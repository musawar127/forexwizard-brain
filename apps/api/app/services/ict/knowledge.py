"""Phase 5.7: ICT/SMC strategy knowledge layer.

Stores machine-detectable representations of strategy concepts:
  - Market structure: HH, HL, LH, LL, BOS, CHoCH, MSS
  - Liquidity: buy-side, sell-side, equal highs/lows, session levels, sweeps
  - ICT/SMC: FVG, OB, breaker, mitigation block, liquidity void,
    displacement, premium/discount, equilibrium, OTE, inducement
  - Sessions: Asia, London, New York

Knowledge is SEPARATE from live market observations. The knowledge
table is the "concept dictionary" with detection rule versions; the
ict_structures / ict_liquidity_levels / ict_fvgs / ict_order_blocks
tables hold LIVE observations derived from market data.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import StrategyKnowledge


# Detection rule versions — bump when detection logic changes
DETECTION_RULE_VERSIONS: dict[str, str] = {
    # Market structure
    "HH": "structure-v0.1",
    "HL": "structure-v0.1",
    "LH": "structure-v0.1",
    "LL": "structure-v0.1",
    "BOS": "structure-v0.1",
    "CHoCH": "structure-v0.1",
    "MSS": "structure-v0.1",
    # Liquidity
    "EQUAL_HIGHS": "liquidity-v0.1",
    "EQUAL_LOWS": "liquidity-v0.1",
    "PDH": "liquidity-v0.1",
    "PDL": "liquidity-v0.1",
    "PWH": "liquidity-v0.1",
    "PWL": "liquidity-v0.1",
    "SESSION_HIGH": "liquidity-v0.1",
    "SESSION_LOW": "liquidity-v0.1",
    "BUY_SIDE_LIQUIDITY": "liquidity-v0.1",
    "SELL_SIDE_LIQUIDITY": "liquidity-v0.1",
    "LIQUIDITY_SWEEP": "liquidity-v0.1",
    # ICT/SMC
    "FAIR_VALUE_GAP": "fvg-v0.1",
    "ORDER_BLOCK": "ob-v0.1",
    "BREAKER_BLOCK": "breaker-v0.1",
    "MITIGATION_BLOCK": "mitigation-v0.1",
    "LIQUIDITY_VOID": "void-v0.1",
    "DISPLACEMENT": "displacement-v0.1",
    "PREMIUM": "premium-discount-v0.1",
    "DISCOUNT": "premium-discount-v0.1",
    "EQUILIBRIUM": "premium-discount-v0.1",
    "OTE": "ote-v0.1",
    "INDUCEMENT": "inducement-v0.1",
    # Sessions
    "ASIA_SESSION": "sessions-v0.1",
    "LONDON_SESSION": "sessions-v0.1",
    "NEW_YORK_SESSION": "sessions-v0.1",
}


# Concept dictionary — definitions + detection rules
_CONCEPT_DEFINITIONS: list[dict] = [
    # Market structure
    {"name": "HH", "category": "MARKET_STRUCTURE", "definition": "Higher High — current swing high exceeds the previous swing high, indicating bullish continuation.", "source": "ICT/SMC standard"},
    {"name": "HL", "category": "MARKET_STRUCTURE", "definition": "Higher Low — current swing low exceeds the previous swing low, indicating bullish continuation.", "source": "ICT/SMC standard"},
    {"name": "LH", "category": "MARKET_STRUCTURE", "definition": "Lower High — current swing high is below the previous swing high, indicating bearish continuation.", "source": "ICT/SMC standard"},
    {"name": "LL", "category": "MARKET_STRUCTURE", "definition": "Lower Low — current swing low is below the previous swing low, indicating bearish continuation.", "source": "ICT/SMC standard"},
    {"name": "BOS", "category": "MARKET_STRUCTURE", "definition": "Break of Structure — price breaks the most recent swing high (bullish BOS) or swing low (bearish BOS) in the direction of the prevailing trend. Continuation signal.", "source": "ICT/SMC standard"},
    {"name": "CHoCH", "category": "MARKET_STRUCTURE", "definition": "Change of Character — price breaks a swing in the OPPOSITE direction of the prevailing trend. First reversal signal.", "source": "ICT/SMC standard"},
    {"name": "MSS", "category": "MARKET_STRUCTURE", "definition": "Market Structure Shift — strong counter-trend break with displacement (>= 1 ATR). Higher-confidence reversal signal than CHoCH.", "source": "ICT/SMC standard"},
    # Liquidity
    {"name": "EQUAL_HIGHS", "category": "LIQUIDITY", "definition": "Two or more swing highs clustered within a small tolerance (default 0.08% of price). Buy-side liquidity resting above.", "source": "ICT/SMC standard"},
    {"name": "EQUAL_LOWS", "category": "LIQUIDITY", "definition": "Two or more swing lows clustered within a small tolerance. Sell-side liquidity resting below.", "source": "ICT/SMC standard"},
    {"name": "PDH", "category": "LIQUIDITY", "definition": "Previous Day High — buy-side liquidity resting above the previous trading day's high.", "source": "ICT/SMC standard"},
    {"name": "PDL", "category": "LIQUIDITY", "definition": "Previous Day Low — sell-side liquidity resting below the previous trading day's low.", "source": "ICT/SMC standard"},
    {"name": "PWH", "category": "LIQUIDITY", "definition": "Previous Week High — buy-side liquidity above the previous week's high.", "source": "ICT/SMC standard"},
    {"name": "PWL", "category": "LIQUIDITY", "definition": "Previous Week Low — sell-side liquidity below the previous week's low.", "source": "ICT/SMC standard"},
    {"name": "SESSION_HIGH", "category": "LIQUIDITY", "definition": "Session high — buy-side liquidity above the highest price reached during a named trading session (Asia/London/NY).", "source": "ICT/SMC standard"},
    {"name": "SESSION_LOW", "category": "LIQUIDITY", "definition": "Session low — sell-side liquidity below the lowest price reached during a named trading session.", "source": "ICT/SMC standard"},
    {"name": "BUY_SIDE_LIQUIDITY", "category": "LIQUIDITY", "definition": "Buy-side liquidity — resting stop-buy orders above swing highs / equal highs / PDH / PWH / session highs. Often targeted by smart money before a reversal.", "source": "ICT/SMC standard"},
    {"name": "SELL_SIDE_LIQUIDITY", "category": "LIQUIDITY", "definition": "Sell-side liquidity — resting stop-sell orders below swing lows / equal lows / PDL / PWL / session lows.", "source": "ICT/SMC standard"},
    {"name": "LIQUIDITY_SWEEP", "category": "LIQUIDITY", "definition": "Liquidity sweep — price briefly penetrates a liquidity level then reverses. Indicates smart money filled resting orders then reversed.", "source": "ICT/SMC standard"},
    # ICT/SMC
    {"name": "FAIR_VALUE_GAP", "category": "ICT_SMC", "definition": "Fair Value Gap — 3-candle imbalance where candle1.high < candle3.low (bullish) or candle1.low > candle3.high (bearish). Represents inefficiency the market may revisit.", "source": "ICT/SMC standard"},
    {"name": "ORDER_BLOCK", "category": "ICT_SMC", "definition": "Order Block — last opposing candle before a displacement move that breaks structure (BOS). Bullish OB = last bearish candle before bullish BOS. Bearish OB = last bullish candle before bearish BOS.", "source": "ICT/SMC standard"},
    {"name": "BREAKER_BLOCK", "category": "ICT_SMC", "definition": "Breaker Block — an order block that failed and then was retested from the other side. Stronger reversal signal.", "source": "ICT/SMC standard"},
    {"name": "MITIGATION_BLOCK", "category": "ICT_SMC", "definition": "Mitigation Block — a retest of a prior swing that was not filled; price returns to mitigate the unfilled orders.", "source": "ICT/SMC standard"},
    {"name": "LIQUIDITY_VOID", "category": "ICT_SMC", "definition": "Liquidity Void — a price region with little volume / inefficiency. Similar to FVG but more general; price tends to retrace through voids.", "source": "ICT/SMC standard"},
    {"name": "DISPLACEMENT", "category": "ICT_SMC", "definition": "Displacement — a strong directional move (>= 1 ATR) indicating institutional participation. Required for valid OB and MSS detection.", "source": "ICT/SMC standard"},
    {"name": "PREMIUM", "category": "ICT_SMC", "definition": "Premium — price above the dealing range equilibrium (upper 50%). Prefer SELL setups from premium.", "source": "ICT/SMC standard"},
    {"name": "DISCOUNT", "category": "ICT_SMC", "definition": "Discount — price below the dealing range equilibrium (lower 50%). Prefer BUY setups from discount.", "source": "ICT/SMC standard"},
    {"name": "EQUILIBRIUM", "category": "ICT_SMC", "definition": "Equilibrium — 50% of the dealing range. Not a favorable entry location for either BUY or SELL.", "source": "ICT/SMC standard"},
    {"name": "OTE", "category": "ICT_SMC", "definition": "Optimal Trade Entry — 70-88% retracement of the most recent impulse leg. High-probability entry zone when combined with HTF alignment.", "source": "ICT/SMC standard"},
    {"name": "INDUCEMENT", "category": "ICT_SMC", "definition": "Inducement — a minor liquidity level that price is likely to sweep before the real move. Used to anticipate entry timing.", "source": "ICT/SMC standard"},
    # Sessions
    {"name": "ASIA_SESSION", "category": "SESSIONS", "definition": "Asia session — 00:00 to 09:00 UTC. Often a range-forming session; liquidity built during Asia is often swept at London open.", "source": "ICT/SMC standard"},
    {"name": "LONDON_SESSION", "category": "SESSIONS", "definition": "London session — 07:00 to 16:00 UTC (06:00-15:00 BST in summer). Primaryliquidity injection session for XAU/USD.", "source": "ICT/SMC standard"},
    {"name": "NEW_YORK_SESSION", "category": "SESSIONS", "definition": "New York session — 12:00 to 21:00 UTC (11:00-20:00 EDT in summer). Continuation of London moves; often the session where liquidity is taken.", "source": "ICT/SMC standard"},
]


def seed_knowledge(session: Session) -> int:
    """Seed the strategy_knowledge table with the concept dictionary.

    Idempotent — only inserts concepts that don't already exist.
    Returns the number of concepts inserted.
    """
    inserted = 0
    now = datetime.now(timezone.utc)
    for concept in _CONCEPT_DEFINITIONS:
        # Check if exists
        existing = session.scalar(
            select(StrategyKnowledge).where(StrategyKnowledge.name == concept["name"]).limit(1)
        )
        if existing is not None:
            # Update updated_at + definition if changed
            if existing.definition != concept["definition"]:
                existing.definition = concept["definition"]
                existing.updated_at = now
            continue

        # Insert
        sk = StrategyKnowledge(
            name=concept["name"],
            category=concept["category"],
            definition=concept["definition"],
            detection_rule_version=DETECTION_RULE_VERSIONS.get(concept["name"], "v0.1"),
            source=concept["source"],
            created_at=now,
            updated_at=now,
        )
        session.add(sk)
        inserted += 1
    session.commit()
    return inserted


def list_knowledge(session: Session) -> list[dict]:
    """List all knowledge concepts."""
    result = session.execute(select(StrategyKnowledge).order_by(StrategyKnowledge.category, StrategyKnowledge.name))
    out: list[dict] = []
    for sk in result.scalars():
        out.append({
            "name": sk.name,
            "category": sk.category,
            "definition": sk.definition,
            "detection_rule_version": sk.detection_rule_version,
            "source": sk.source,
            "created_at": sk.created_at.isoformat() if sk.created_at else None,
            "updated_at": sk.updated_at.isoformat() if sk.updated_at else None,
        })
    return out
