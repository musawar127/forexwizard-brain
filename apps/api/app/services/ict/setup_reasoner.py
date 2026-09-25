"""Phase 5.7: ICT/SMC setup reasoner — deterministic BUY/SELL/WAIT thesis.

Combines multi-timeframe structure, liquidity, FVG, OB, premium/discount,
and session evidence into an actionable setup with:
  - direction (BUY/SELL/WAIT)
  - entry zone (from FVG/OB/breaker)
  - stop loss (structural invalidation)
  - TP1 / TP2 / TP3 / MAX_OBJECTIVE
  - FOR evidence (supporting setup)
  - AGAINST evidence (opposing setup)
  - thesis (concise summary)
  - invalidation (what proves thesis wrong)

No single ICT/SMC signal automatically generates a trade. The reasoner
requires multiple confirmations:
  - HTF structure alignment (H1/H4 trend direction)
  - Liquidity sweep at HTF (counter-trend sweep)
  - M15 CHoCH/MSS (reversal trigger)
  - Displacement confirmation
  - FVG or OB retest for entry
  - Acceptable premium/discount location
  - Acceptable R:R (>= 2.0 to TP1)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.models.market import Candle
from .fvg import FVG, active_fvgs, nearest_fvg_to_price
from .liquidity import LiquidityAnalysis, LiquiditySweep, LiquidityLevel
from .order_blocks import OrderBlock, active_order_blocks, nearest_ob_to_price
from .premium_discount import DealingRange
from .sessions import SessionRange, current_session
from .structures import TimeframeStructure, StructureEvent


# ---------- data structures ----------

@dataclass
class SetupEvidence:
    """A single piece of evidence supporting or opposing a setup."""
    kind: str          # HTF_STRUCTURE / LIQUIDITY_SWEEP / MSS / FVG / OB / SESSION / LOCATION / RR
    timeframe: str | None
    description: str
    bullish_or_bearish: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    confidence: float = 50.0


@dataclass
class SetupReasoning:
    """The full reasoning output for a setup."""
    direction: Literal["BUY", "SELL", "WAIT"]
    plan_status: Literal[
        "ACTIONABLE",
        "WAIT_FOR_ENTRY",
        "NO_TRADE",
        "NO_VALID_ENTRY",
        "NO_VALID_SL",
        "NO_VALID_TP",
        "INSUFFICIENT_DATA",
        "STALE",
    ]
    for_evidence: list[SetupEvidence] = field(default_factory=list)
    against_evidence: list[SetupEvidence] = field(default_factory=list)
    thesis: str = ""
    invalidation: str = ""

    # Per-TF structure summary
    htf_trend: str = "RANGE"            # H1/H4/D1 combined trend
    m15_trend: str = "RANGE"            # M15 trend (setup TF)
    liquidity_swept: bool = False
    sweep_direction: str | None = None  # BUY_SIDE / SELL_SIDE
    displacement_confirmed: bool = False
    fvg_active: bool = False
    ob_active: bool = False
    location: str = "EQUILIBRIUM"       # PREMIUM / DISCOUNT / EQUILIBRIUM
    session_active: list[str] = field(default_factory=list)


# ---------- multi-timeframe structure ----------

@dataclass
class MultiTimeframeAnalysis:
    """Holds structure analysis for every relevant timeframe."""
    # HTF = D1, H4, H1 — macro directional structure
    d1: TimeframeStructure | None = None
    h4: TimeframeStructure | None = None
    h1: TimeframeStructure | None = None
    # M15 = setup structure
    m15: TimeframeStructure | None = None
    # M5/M1 = entry confirmation
    m5: TimeframeStructure | None = None
    m1: TimeframeStructure | None = None
    # M30 — sometimes used as transition
    m30: TimeframeStructure | None = None


def htf_trend(analysis: MultiTimeframeAnalysis) -> tuple[str, float]:
    """Combine H1/H4/D1 trend into a single HTF trend with confidence.

    Returns ("BULLISH"/"BEARISH"/"RANGE", confidence_0..100).
    Confidence = fraction of HTFs agreeing.
    """
    votes: list[str] = []
    for tf in (analysis.d1, analysis.h4, analysis.h1):
        if tf is not None:
            votes.append(tf.trend)
    if not votes:
        return "RANGE", 0.0
    bull = sum(1 for v in votes if v == "BULLISH")
    bear = sum(1 for v in votes if v == "BEARISH")
    if bull > bear:
        return "BULLISH", (bull / len(votes)) * 100.0
    if bear > bull:
        return "BEARISH", (bear / len(votes)) * 100.0
    return "RANGE", 0.0


# ---------- setup reasoner ----------

def reason_setup(
    *,
    mtf: MultiTimeframeAnalysis,
    liquidity: LiquidityAnalysis,
    fvgs_m15: list[FVG],
    obs_m15: list[OrderBlock],
    dealing_range: DealingRange | None,
    current_price: float,
    atr_value: float | None,
    now_utc,
    candles_h1: list[Candle],
) -> SetupReasoning:
    """Build a deterministic BUY/SELL/WAIT thesis from the evidence.

    BUY thesis requires:
      - HTF bullish (H1/H4/D1 majority BULLISH)
      - sell-side liquidity sweep (price took sell-side liquidity then reversed up)
      - M15 bullish CHoCH/MSS (reversal trigger)
      - displacement confirmed (>= 1 ATR)
      - active bullish FVG or bullish OB in discount (price location)
      - acceptable R:R (will be checked downstream)

    SELL thesis: mirror.

    WAIT: any of the above missing.
    """
    result = SetupReasoning(direction="WAIT", plan_status="NO_TRADE")

    # HTF trend
    trend, trend_conf = htf_trend(mtf)
    result.htf_trend = trend

    # M15 trend (setup TF)
    if mtf.m15 is not None:
        result.m15_trend = mtf.m15.trend

    # Active sessions
    result.session_active = current_session(now_utc)

    # Liquidity sweep detection — look for most recent sweep
    recent_sweep: LiquiditySweep | None = None
    if liquidity.sweeps:
        # Pick the most recent sweep
        recent_sweep = max(liquidity.sweeps, key=lambda s: s.timestamp)
        result.liquidity_swept = True
        result.sweep_direction = "BUY_SIDE" if recent_sweep.direction == "BUY_SIDE_SWEEP" else "SELL_SIDE"

    # M15 structure event (last event)
    last_m15_event: StructureEvent | None = None
    if mtf.m15 is not None and mtf.m15.events:
        last_m15_event = mtf.m15.events[-1]

    # Displacement check on M15
    if last_m15_event is not None and atr_value and atr_value > 0:
        displacement = abs(last_m15_event.price - last_m15_event.broken_level)
        if displacement >= 1.0 * atr_value:
            result.displacement_confirmed = True

    # Active FVG / OB on M15
    active_fvg_list = active_fvgs(fvgs_m15)
    active_ob_list = active_order_blocks(obs_m15)
    if active_fvg_list:
        result.fvg_active = True
    if active_ob_list:
        result.ob_active = True

    # Premium/discount
    if dealing_range is not None:
        result.location = dealing_range.location_label

    # ---------- assemble evidence ----------

    # HTF structure evidence
    if trend != "RANGE":
        result.for_evidence.append(SetupEvidence(
            kind="HTF_STRUCTURE",
            timeframe="H1/H4/D1",
            description=f"HTF trend is {trend} ({trend_conf:.0f}% agreement across H1/H4/D1)",
            bullish_or_bearish="BULLISH" if trend == "BULLISH" else "BEARISH",
            confidence=trend_conf,
        ))
    else:
        result.against_evidence.append(SetupEvidence(
            kind="HTF_STRUCTURE",
            timeframe="H1/H4/D1",
            description="HTF trend is RANGE — no directional bias",
            bullish_or_bearish="NEUTRAL",
            confidence=30.0,
        ))

    # Liquidity sweep evidence
    if recent_sweep is not None:
        direction = "BULLISH" if recent_sweep.direction == "SELL_SIDE_SWEEP" else "BEARISH"
        # SELL_SIDE_SWEEP = sell-side liquidity taken = price dropped below a low then reversed up
        # This is BULLISH (smart money grabbed sell-side liquidity then reversed up)
        result.for_evidence.append(SetupEvidence(
            kind="LIQUIDITY_SWEEP",
            timeframe="M15/H1",
            description=f"{recent_sweep.direction} ({recent_sweep.level_kind}) at {recent_sweep.level:.2f} — reaction {recent_sweep.reaction_magnitude:.2f}",
            bullish_or_bearish=direction,  # type: ignore[arg-type]
            confidence=70.0,
        ))
    else:
        result.against_evidence.append(SetupEvidence(
            kind="LIQUIDITY_SWEEP",
            timeframe="M15/H1",
            description="No recent liquidity sweep detected",
            bullish_or_bearish="NEUTRAL",
            confidence=20.0,
        ))

    # M15 MSS/CHoCH evidence
    if last_m15_event is not None and last_m15_event.event_type in ("CHoCH", "MSS"):
        result.for_evidence.append(SetupEvidence(
            kind=last_m15_event.event_type,
            timeframe="M15",
            description=f"M15 {last_m15_event.event_type} {last_m15_event.direction} — broke {last_m15_event.broken_kind} at {last_m15_event.broken_level:.2f}",
            bullish_or_bearish=last_m15_event.direction,  # type: ignore[arg-type]
            confidence=75.0 if last_m15_event.event_type == "MSS" else 60.0,
        ))
    elif last_m15_event is not None:
        # Last event is BOS (continuation) — not a reversal signal
        result.against_evidence.append(SetupEvidence(
            kind="MSS",
            timeframe="M15",
            description=f"No M15 CHoCH/MSS — last event is {last_m15_event.event_type} (continuation, not reversal)",
            bullish_or_bearish="NEUTRAL",
            confidence=30.0,
        ))

    # Displacement evidence
    if result.displacement_confirmed:
        result.for_evidence.append(SetupEvidence(
            kind="DISPLACEMENT",
            timeframe="M15",
            description=f"M15 displacement >= 1.0 * ATR (institutional move confirmed)",
            bullish_or_bearish=last_m15_event.direction if last_m15_event else "NEUTRAL",  # type: ignore[arg-type]
            confidence=70.0,
        ))

    # FVG evidence
    if result.fvg_active:
        # Find nearest active FVG
        nearest_fvg = nearest_fvg_to_price(active_fvg_list, current_price)
        if nearest_fvg is not None:
            result.for_evidence.append(SetupEvidence(
                kind="FVG",
                timeframe="M15",
                description=f"Active {nearest_fvg.direction} FVG at {nearest_fvg.lower:.2f}-{nearest_fvg.upper:.2f} (mid {nearest_fvg.midpoint:.2f})",
                bullish_or_bearish=nearest_fvg.direction,  # type: ignore[arg-type]
                confidence=60.0,
            ))

    # OB evidence
    if result.ob_active:
        nearest_ob = nearest_ob_to_price(active_ob_list, current_price)
        if nearest_ob is not None:
            result.for_evidence.append(SetupEvidence(
                kind="ORDER_BLOCK",
                timeframe="M15",
                description=f"Active {nearest_ob.direction} OB at {nearest_ob.lower:.2f}-{nearest_ob.upper:.2f} (quality {nearest_ob.quality:.0f})",
                bullish_or_bearish=nearest_ob.direction,  # type: ignore[arg-type]
                confidence=60.0,
            ))

    # Premium/discount evidence
    if dealing_range is not None:
        result.for_evidence.append(SetupEvidence(
            kind="LOCATION",
            timeframe="H1",
            description=f"Price in {dealing_range.location_label} (range_pct={dealing_range.range_pct:.2f})",
            bullish_or_bearish="BULLISH" if dealing_range.is_discount else "BEARISH" if dealing_range.is_premium else "NEUTRAL",  # type: ignore[arg-type]
            confidence=50.0,
        ))

    # Session evidence
    if result.session_active:
        result.for_evidence.append(SetupEvidence(
            kind="SESSION",
            timeframe=None,
            description=f"Active session(s): {', '.join(result.session_active)}",
            bullish_or_bearish="NEUTRAL",
            confidence=40.0,
        ))

    # ---------- decide direction ----------

    # Required for BUY:
    #   1. HTF bullish
    #   2. sell-side liquidity swept (SELL_SIDE_SWEEP)
    #   3. M15 bullish CHoCH/MSS
    #   4. displacement confirmed
    #   5. active bullish FVG OR bullish OB
    #   6. discount location (price in discount)

    buy_conditions_met = (
        trend == "BULLISH"
        and recent_sweep is not None
        and recent_sweep.direction == "SELL_SIDE_SWEEP"
        and last_m15_event is not None
        and last_m15_event.direction == "BULLISH"
        and last_m15_event.event_type in ("CHoCH", "MSS")
        and result.displacement_confirmed
        and (
            any(f.direction == "BULLISH" for f in active_fvg_list)
            or any(o.direction == "BULLISH" for o in active_ob_list)
        )
        and (dealing_range is None or dealing_range.is_discount)
    )

    sell_conditions_met = (
        trend == "BEARISH"
        and recent_sweep is not None
        and recent_sweep.direction == "BUY_SIDE_SWEEP"
        and last_m15_event is not None
        and last_m15_event.direction == "BEARISH"
        and last_m15_event.event_type in ("CHoCH", "MSS")
        and result.displacement_confirmed
        and (
            any(f.direction == "BEARISH" for f in active_fvg_list)
            or any(o.direction == "BEARISH" for o in active_ob_list)
        )
        and (dealing_range is None or dealing_range.is_premium)
    )

    if buy_conditions_met:
        result.direction = "BUY"
        result.plan_status = "ACTIONABLE"
        result.thesis = (
            f"BUY setup: HTF bullish ({trend_conf:.0f}%) + sell-side liquidity swept + "
            f"M15 {last_m15_event.event_type if last_m15_event else '?'} + displacement + "
            f"FVG/OB retest + discount location"
        )
        result.invalidation = (
            f"Thesis invalid if price closes below the structural low that hosted the sweep "
            f"(typically the swept {result.sweep_direction} liquidity level or the M15 swing low)."
        )
    elif sell_conditions_met:
        result.direction = "SELL"
        result.plan_status = "ACTIONABLE"
        result.thesis = (
            f"SELL setup: HTF bearish ({trend_conf:.0f}%) + buy-side liquidity swept + "
            f"M15 {last_m15_event.event_type if last_m15_event else '?'} + displacement + "
            f"FVG/OB retest + premium location"
        )
        result.invalidation = (
            f"Thesis invalid if price closes above the structural high that hosted the sweep."
        )
    else:
        # WAIT — build reasons
        reasons: list[str] = []
        if trend == "RANGE":
            reasons.append("HTF trend is RANGE — no directional bias")
        elif trend == "BULLISH":
            reasons.append("HTF bullish but no sell-side sweep / M15 bullish CHoCH/MSS combo")
        elif trend == "BEARISH":
            reasons.append("HTF bearish but no buy-side sweep / M15 bearish CHoCH/MSS combo")
        if recent_sweep is None:
            reasons.append("no liquidity sweep detected")
        if last_m15_event is None or last_m15_event.event_type not in ("CHoCH", "MSS"):
            reasons.append("no M15 CHoCH/MSS reversal trigger")
        if not result.displacement_confirmed:
            reasons.append("no displacement confirmation (institutional move missing)")
        if not result.fvg_active and not result.ob_active:
            reasons.append("no active FVG or OB for entry")
        if dealing_range is not None and dealing_range.location_label == "EQUILIBRIUM":
            reasons.append("price at equilibrium (not premium/discount)")

        result.thesis = f"WAIT — {', '.join(reasons)}"
        result.invalidation = "N/A — no active setup to invalidate"

    return result


# ---------- entry / SL / target derivation ----------

def derive_entry(
    *,
    reasoning: SetupReasoning,
    fvgs_m15: list[FVG],
    obs_m15: list[OrderBlock],
    current_price: float,
    atr_value: float | None,
) -> dict:
    """Derive the entry zone from the nearest actionable FVG or OB.

    Returns:
      {
        "entry_low": float,
        "entry_high": float,
        "preferred_entry": float,  # midpoint or OB midpoint
        "entry_type": str,        # "FVG" or "ORDER_BLOCK" or "RETEST"
        "entry_reason": str,
        "status": "ACTIONABLE" | "WAIT_FOR_ENTRY" | "NO_VALID_ENTRY"
      }
    """
    direction = "BULLISH" if reasoning.direction == "BUY" else "BEARISH"

    # Find nearest active FVG in the setup direction
    nearest_fvg = nearest_fvg_to_price(fvgs_m15, current_price, direction=direction)
    nearest_ob = nearest_ob_to_price(obs_m15, current_price, direction=direction)

    # Pick the one nearest to the current price (prefer FVG for tight entries)
    candidates: list[tuple[float, str, float, float, float, str]] = []  # (distance, type, low, high, midpoint, reason)
    if nearest_fvg is not None:
        candidates.append((
            abs(nearest_fvg.midpoint - current_price),
            "FVG",
            nearest_fvg.lower,
            nearest_fvg.upper,
            nearest_fvg.midpoint,
            f"{nearest_fvg.direction} FVG at {nearest_fvg.lower:.2f}-{nearest_fvg.upper:.2f}",
        ))
    if nearest_ob is not None:
        candidates.append((
            abs(nearest_ob.midpoint - current_price),
            "ORDER_BLOCK",
            nearest_ob.lower,
            nearest_ob.upper,
            nearest_ob.midpoint,
            f"{nearest_ob.direction} OB at {nearest_ob.lower:.2f}-{nearest_ob.upper:.2f} (quality {nearest_ob.quality:.0f})",
        ))

    if not candidates:
        return {
            "entry_low": None, "entry_high": None, "preferred_entry": None,
            "entry_type": None, "entry_reason": "no actionable FVG or OB in setup direction",
            "status": "NO_VALID_ENTRY",
        }

    # Sort by distance to current price (nearest first)
    candidates.sort(key=lambda x: x[0])
    _dist, etype, low, high, midpoint, reason = candidates[0]

    # Add a small ATR buffer around the zone (entry zone is the level + small buffer)
    buf = (atr_value * 0.1) if atr_value and atr_value > 0 else (midpoint * 0.0005)
    entry_low = round(low - buf, 2)
    entry_high = round(high + buf, 2)
    preferred = round(midpoint, 2)

    # Determine if entry is ACTIONABLE (price already in zone) or WAIT_FOR_ENTRY (awaiting retracement)
    if entry_low <= current_price <= entry_high:
        status = "ACTIONABLE"
    else:
        status = "WAIT_FOR_ENTRY"

    return {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "preferred_entry": preferred,
        "entry_type": etype,
        "entry_reason": reason,
        "status": status,
    }


def derive_stop_loss(
    *,
    reasoning: SetupReasoning,
    entry_reference: float,
    liquidity: LiquidityAnalysis,
    m15: TimeframeStructure | None,
    obs_m15: list[OrderBlock],
    atr_value: float | None,
) -> dict:
    """Derive stop-loss from structural invalidation.

    For BUY: below the swept sell-side liquidity level OR below M15 swing low OR below OB lower
    For SELL: above the swept buy-side liquidity level OR above M15 swing high OR above OB upper

    Adds a volatility-aware buffer (0.5 * ATR or 0.10% of price).
    """
    direction = reasoning.direction
    buf = (atr_value * 0.5) if atr_value and atr_value > 0 else (entry_reference * 0.001)

    # Candidate structural levels for SL
    candidates: list[tuple[float, str]] = []

    if direction == "BUY":
        # Below entry, find the lowest structural level below entry
        # 1. Swept liquidity level (sell-side)
        for sweep in liquidity.sweeps:
            if sweep.direction == "SELL_SIDE_SWEEP" and sweep.level < entry_reference:
                candidates.append((sweep.level, f"swept sell-side liquidity ({sweep.level_kind})"))
        # 2. Recent M15 swing low
        if m15 is not None:
            for sw in m15.swings:
                if sw.kind == "SWING_LOW" and sw.price < entry_reference:
                    candidates.append((sw.price, "M15 swing low"))
        # 3. OB lower edge
        for ob in obs_m15:
            if ob.direction == "BULLISH" and ob.lower < entry_reference:
                candidates.append((ob.lower, "bullish OB lower edge"))
    elif direction == "SELL":
        for sweep in liquidity.sweeps:
            if sweep.direction == "BUY_SIDE_SWEEP" and sweep.level > entry_reference:
                candidates.append((sweep.level, f"swept buy-side liquidity ({sweep.level_kind})"))
        if m15 is not None:
            for sw in m15.swings:
                if sw.kind == "SWING_HIGH" and sw.price > entry_reference:
                    candidates.append((sw.price, "M15 swing high"))
        for ob in obs_m15:
            if ob.direction == "BEARISH" and ob.upper > entry_reference:
                candidates.append((ob.upper, "bearish OB upper edge"))

    if not candidates:
        return {
            "stop_loss": None,
            "structural_invalidation": None,
            "buffer": None,
            "reason": "no structural level below/above entry to invalidate against",
            "status": "NO_VALID_SL",
        }

    # Choose the closest valid structural level to entry (tightest valid SL)
    if direction == "BUY":
        # Highest level below entry = closest = tightest SL
        valid = [(lvl, src) for lvl, src in candidates if lvl < entry_reference]
        if not valid:
            return {
                "stop_loss": None, "structural_invalidation": None,
                "buffer": None, "reason": "no structural level below entry",
                "status": "NO_VALID_SL",
            }
        structural_level, source = max(valid, key=lambda x: x[0])
        stop_loss = round(structural_level - buf, 2)
    else:
        valid = [(lvl, src) for lvl, src in candidates if lvl > entry_reference]
        if not valid:
            return {
                "stop_loss": None, "structural_invalidation": None,
                "buffer": None, "reason": "no structural level above entry",
                "status": "NO_VALID_SL",
            }
        structural_level, source = min(valid, key=lambda x: x[0])
        stop_loss = round(structural_level + buf, 2)

    return {
        "stop_loss": stop_loss,
        "structural_invalidation": round(structural_level, 2),
        "buffer": round(buf, 2),
        "reason": f"{direction} SL: {source} at {structural_level:.2f} ± buffer {buf:.2f}",
        "status": "OK",
    }


def derive_targets(
    *,
    direction: Literal["BUY", "SELL"],
    entry_reference: float,
    stop_loss: float,
    risk_distance: float,
    liquidity: LiquidityAnalysis,
    m15: TimeframeStructure | None,
    h1: TimeframeStructure | None,
    sessions_ranges: list,  # list of SessionRange
    current_price: float,
) -> dict:
    """Derive TP1, TP2, TP3, MAX_OBJECTIVE.

    Targets use structural / liquidity objectives in priority:
      1. internal liquidity (recent opposing swing on M15)
      2. session high/low (Asia/London/NY)
      3. PDH/PDL or PWH/PWL
      4. external liquidity (HTF swing high/low on H1)
      5. ATR-measured extension (only for MAX_OBJECTIVE if structural levels exhausted)
    """
    # Collect target candidates on the profitable side
    candidates: list[tuple[float, str]] = []

    if direction == "BUY":
        # 1. Internal liquidity — recent M15 swing high
        if m15 is not None:
            for sw in m15.swings:
                if sw.kind == "SWING_HIGH" and sw.price > entry_reference + 0.05:
                    candidates.append((sw.price, f"M15 swing high ({sw.price:.2f})"))
        # 2. Session highs
        for sr in sessions_ranges:
            if sr.high > entry_reference + 0.05:
                candidates.append((sr.high, f"{sr.name} session high ({sr.high:.2f})"))
        # 3. PDH / PWH
        for level in liquidity.levels:
            if level.kind in ("PDH", "PWH") and level.price > entry_reference + 0.05:
                candidates.append((level.price, f"{level.kind} ({level.price:.2f})"))
        # 4. External liquidity — H1 swing high
        if h1 is not None:
            for sw in h1.swings:
                if sw.kind == "SWING_HIGH" and sw.price > entry_reference + 0.05:
                    candidates.append((sw.price, f"H1 swing high ({sw.price:.2f})"))
    else:  # SELL
        if m15 is not None:
            for sw in m15.swings:
                if sw.kind == "SWING_LOW" and sw.price < entry_reference - 0.05:
                    candidates.append((sw.price, f"M15 swing low ({sw.price:.2f})"))
        for sr in sessions_ranges:
            if sr.low < entry_reference - 0.05:
                candidates.append((sr.low, f"{sr.name} session low ({sr.low:.2f})"))
        for level in liquidity.levels:
            if level.kind in ("PDL", "PWL") and level.price < entry_reference - 0.05:
                candidates.append((level.price, f"{level.kind} ({level.price:.2f})"))
        if h1 is not None:
            for sw in h1.swings:
                if sw.kind == "SWING_LOW" and sw.price < entry_reference - 0.05:
                    candidates.append((sw.price, f"H1 swing low ({sw.price:.2f})"))

    # Deduplicate + sort
    seen: set[float] = set()
    unique: list[tuple[float, str]] = []
    for lvl, src in candidates:
        rounded = round(lvl, 2)
        if rounded in seen:
            continue
        seen.add(rounded)
        unique.append((rounded, src))

    if direction == "BUY":
        unique.sort(key=lambda x: x[0])  # ascending — TP1 < TP2 < TP3 < MAX
    else:
        unique.sort(key=lambda x: -x[0])  # descending — TP1 > TP2 > TP3 > MAX

    # Need at least 3 distinct targets + 1 max = 4
    if len(unique) < 4:
        # Add ATR extensions as last-resort for MAX_OBJECTIVE
        r_multiples = [1.5, 2.5, 3.5, 4.5]
        for r in r_multiples:
            if direction == "BUY":
                ext = round(entry_reference + r * risk_distance, 2)
                if ext not in seen:
                    unique.append((ext, f"ATR extension {r}R"))
                    seen.add(ext)
            else:
                ext = round(entry_reference - r * risk_distance, 2)
                if ext not in seen:
                    unique.append((ext, f"ATR extension {r}R"))
                    seen.add(ext)

    if len(unique) < 4:
        return {
            "tp1": None, "tp2": None, "tp3": None, "max_objective": None,
            "tp1_reason": None, "tp2_reason": None, "tp3_reason": None, "max_objective_reason": None,
            "status": "NO_VALID_TP",
            "reason": f"only {len(unique)} structural targets derivable, need 4",
        }

    # Re-sort after ATR additions
    if direction == "BUY":
        unique.sort(key=lambda x: x[0])
    else:
        unique.sort(key=lambda x: -x[0])

    # Validate ordering — every target must be on the profitable side of entry
    valid: list[tuple[float, str]] = []
    for lvl, src in unique:
        if direction == "BUY" and lvl > entry_reference:
            valid.append((lvl, src))
        elif direction == "SELL" and lvl < entry_reference:
            valid.append((lvl, src))

    if len(valid) < 4:
        return {
            "tp1": None, "tp2": None, "tp3": None, "max_objective": None,
            "tp1_reason": None, "tp2_reason": None, "tp3_reason": None, "max_objective_reason": None,
            "status": "NO_VALID_TP",
            "reason": "fewer than 4 valid targets on profitable side of entry",
        }

    # TP1, TP2, TP3 = closest 3 levels; MAX_OBJECTIVE = furthest
    tp1, tp1_reason = valid[0]
    tp2, tp2_reason = valid[1]
    tp3, tp3_reason = valid[2]
    max_obj, max_reason = valid[-1]  # furthest structural objective

    return {
        "tp1": round(tp1, 2), "tp2": round(tp2, 2), "tp3": round(tp3, 2),
        "max_objective": round(max_obj, 2),
        "tp1_reason": tp1_reason, "tp2_reason": tp2_reason,
        "tp3_reason": tp3_reason, "max_objective_reason": max_reason,
        "status": "OK",
    }
