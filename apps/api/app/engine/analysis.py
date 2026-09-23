from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.models import HistoricalSyncState
from app.db.session import SessionLocal
from app.engine.candles import get_candles
from app.engine.indicators import atr, ema, rsi, slope
from app.models.market import BrainAnalysis, Candle, TimeframeState, Zone


TIMEFRAMES = ["4h", "1h", "30min", "15min", "5min", "1min"]

# Phase 3.2: also compute historical_depth for D1 — the dashboard's
# context-strip shows H1 + D1 depth so the user knows what genuine
# history backs the technical_score at a glance.
DEPTH_TIMEFRAMES = TIMEFRAMES + ["1day"]


def _full_historical_depth_days(symbol: str, interval: str) -> float:
    """Query HistoricalSyncState for the FULL earliest/latest historical
    range at this TF — not just the 120 most recent candles the Brain
    reads for its trend analysis. This gives an honest depth number that
    reflects the genuine history available in the DB."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(HistoricalSyncState).where(
                HistoricalSyncState.symbol == symbol,
                HistoricalSyncState.interval == interval,
            )
        ).all()
        if not rows:
            return 0.0
        earliest = None
        latest = None
        for r in rows:
            if r.earliest_timestamp:
                if earliest is None or r.earliest_timestamp < earliest:
                    earliest = r.earliest_timestamp
            if r.latest_timestamp:
                if latest is None or r.latest_timestamp > latest:
                    latest = r.latest_timestamp
        if not earliest or not latest:
            return 0.0
        # Treat naive datetimes as UTC.
        if earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=timezone.utc)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return round((latest - earliest).total_seconds() / 86400.0, 2)


def _trend(candles: list[Candle]) -> tuple[str, float | None, float | None, float | None, float | None]:
    closes = [c.close for c in candles]
    fast = ema(closes, min(8, max(3, len(closes) // 3))) if len(closes) >= 6 else None
    slow_period = min(20, max(5, len(closes) // 2))
    slow = ema(closes, slow_period) if len(closes) >= slow_period else None
    rv = rsi(closes, min(14, max(5, len(closes) - 1))) if len(closes) >= 7 else None
    av = atr(candles, min(14, max(5, len(candles) - 1))) if len(candles) >= 7 else None
    sl = slope(closes, min(6, len(closes))) if len(closes) >= 3 else None
    if fast is None or slow is None or sl is None:
        return "INSUFFICIENT_DATA", fast, slow, rv, av
    threshold = max((av or 0) * 0.03, 0.01)
    if fast > slow and sl > threshold:
        trend = "BULLISH"
    elif fast < slow and sl < -threshold:
        trend = "BEARISH"
    else:
        trend = "RANGE"
    return trend, fast, slow, rv, av


def _zones(candles: list[Candle], price: float) -> tuple[Zone | None, Zone | None]:
    if len(candles) < 5:
        return None, None
    recent = candles[-min(50, len(candles)):]
    lows = sorted(c.low for c in recent if c.low <= price)
    highs = sorted((c.high for c in recent if c.high >= price), reverse=True)
    avg_range = sum(max(c.high - c.low, 0.01) for c in recent) / len(recent)
    pad = max(avg_range * 0.25, price * 0.00015)
    support = None
    resistance = None
    if lows:
        anchor = max(lows)
        touches = sum(1 for c in recent if abs(c.low - anchor) <= pad)
        support = Zone(kind="SUPPORT", low=anchor - pad, high=anchor + pad, touches=touches, strength=min(100, touches * 18))
    if highs:
        anchor = min(highs)
        touches = sum(1 for c in recent if abs(c.high - anchor) <= pad)
        resistance = Zone(kind="RESISTANCE", low=anchor - pad, high=anchor + pad, touches=touches, strength=min(100, touches * 18))
    return support, resistance


def _historical_depth_days(candles: list[Candle]) -> float:
    """Days of genuine historical depth at this TF — counts ONLY candles
    with is_historical=True so sampled live history doesn't masquerade
    as multi-day historical depth."""
    hist = [c for c in candles if getattr(c, "is_historical", False)]
    if not hist:
        return 0.0
    span = (max(c.timestamp for c in hist) - min(c.timestamp for c in hist)).total_seconds()
    return round(span / 86400.0, 2)


def _instrument_consistency(candles: list[Candle]) -> str:
    """PURE_GC / PURE_SPOT / MIXED / NONE for a candle list."""
    if not candles:
        return "NONE"
    instruments = {getattr(c, "instrument", None) for c in candles}
    instruments.discard(None)
    if not instruments:
        return "NONE"
    if len(instruments) > 1:
        return "MIXED"
    only = next(iter(instruments))
    if only == "GC_FRONT_MONTH":
        return "PURE_GC"
    if only == "XAUUSD_SPOT":
        return "PURE_SPOT"
    return f"PURE_{only}"


async def analyze_market(price: float | None, quote_status: str, source_status: str) -> BrainAnalysis:
    now = datetime.now(timezone.utc)
    timeframe_states: list[TimeframeState] = []
    by_tf: dict[str, tuple[list[Candle], TimeframeState]] = {}

    # Phase 3.1: track per-TF historical depth + instrument consistency
    # so the Brain can report them WITHOUT feeding them into the score.
    historical_depth: dict[str, float] = {}
    instrument_per_tf: dict[str, set[str]] = {}

    for tf in TIMEFRAMES:
        candles = await get_candles(tf, 120)
        trend, fast, slow, rv, av = _trend(candles)
        status = "READY" if len(candles) >= settings.analysis_min_candles else "LEARNING"
        state = TimeframeState(
            timeframe=tf,
            candles=len(candles),
            status=status,
            trend=trend,
            rsi=round(rv, 1) if rv is not None else None,
            atr=round(av, 4) if av is not None else None,
            ema_fast=round(fast, 4) if fast is not None else None,
            ema_slow=round(slow, 4) if slow is not None else None,
        )
        timeframe_states.append(state)
        by_tf[tf] = (candles, state)
        # Phase 3.1: historical_depth reports the FULL DB range from
        # HistoricalSyncState, not just the 120 most recent candles. This
        # is honest about what genuine history is available even if the
        # Brain's trend analysis only uses the recent 120.
        historical_depth[tf] = _full_historical_depth_days("XAU/USD", tf)
        instrument_per_tf[tf] = {getattr(c, "instrument", "XAUUSD_SPOT") for c in candles}

    # Phase 3.2: also compute D1 depth (the dashboard context-strip uses it).
    # D1 is NOT in TIMEFRAMES because the Brain's trend logic doesn't read
    # D1 for its scoring — but the depth info is still useful read-only context.
    historical_depth["1day"] = _full_historical_depth_days("XAU/USD", "1day")

    ready = [x for x in timeframe_states if x.status == "READY" and x.trend != "INSUFFICIENT_DATA"]
    readiness = min(100.0, round(len(ready) / 4 * 100, 1))

    # Phase 3.1: instrument consistency across ALL TFs combined.
    all_instruments: set[str] = set()
    for s in instrument_per_tf.values():
        all_instruments |= s
    all_instruments.discard(None)
    if not all_instruments:
        instrument_consistency = "NONE"
    elif len(all_instruments) > 1:
        instrument_consistency = "MIXED"
    elif "GC_FRONT_MONTH" in all_instruments:
        instrument_consistency = "PURE_GC"
    elif "XAUUSD_SPOT" in all_instruments:
        instrument_consistency = "PURE_SPOT"
    else:
        instrument_consistency = f"PURE_{next(iter(all_instruments))}"

    if price is None or quote_status == "STALE" or source_status != "CONNECTED":
        return BrainAnalysis(
            timestamp=now,
            decision="NO_DECISION",
            confidence=0,
            readiness=readiness,
            regime="UNKNOWN",
            risk="HIGH",
            score=0,
            price=price,
            reasons_for=[],
            reasons_against=["Live price feed is unavailable or stale."],
            timeframes=timeframe_states,
            data_quality="BAD",
            message="Analysis paused because current market data is not reliable enough.",
            historical_depth=historical_depth,
            instrument_consistency=instrument_consistency,
            technical_data_readiness=readiness,
            technical_score=0.0,
            # Phase 4: NO_DECISION → no directional statistics to populate.
            # All historical fields remain None until the Brain can produce
            # a directional decision worth measuring.
            historical_sample_size=None,
            historical_direction_rate=None,
            historical_mfe=None,
            historical_mae=None,
            historical_probability=None,
            probability_calibrated=False,
            historical_alignment="INSUFFICIENT_DATA",
            historical_analogue_instrument=None,
            historical_analogue_horizon_minutes=None,
            historical_analogue_note=None,
        )

    # Select the richest locally accumulated timeframe for nearby zones.
    zone_candles: list[Candle] = []
    for tf in ("15min", "5min", "1min", "1h"):
        if len(by_tf[tf][0]) >= 5:
            zone_candles = by_tf[tf][0]
            break
    support, resistance = _zones(zone_candles, price)

    # ---- BUY/SELL/WAIT SCORING LOGIC — UNCHANGED FROM rules-v0.1 ----
    score = 0.0
    reasons: list[str] = []
    against: list[str] = []
    weights = {"4h": 2.0, "1h": 1.7, "30min": 1.3, "15min": 1.1, "5min": 0.8, "1min": 0.4}
    bullish = bearish = 0

    for tf, (_, state) in by_tf.items():
        if state.status != "READY":
            continue
        weight = weights[tf]
        if state.trend == "BULLISH":
            score += weight
            bullish += 1
            reasons.append(f"{tf} trend is bullish.")
        elif state.trend == "BEARISH":
            score -= weight
            bearish += 1
            reasons.append(f"{tf} trend is bearish.")
        else:
            against.append(f"{tf} is ranging rather than trending.")

        if state.rsi is not None:
            if state.rsi >= 72:
                score -= 0.35
                against.append(f"{tf} RSI is stretched high ({state.rsi:.0f}).")
            elif state.rsi <= 28:
                score += 0.35
                against.append(f"{tf} RSI is stretched low ({state.rsi:.0f}).")

    if support and price <= support.high * 1.00035:
        score += 0.5
        reasons.append("Price is trading close to a locally observed support zone.")
    if resistance and price >= resistance.low * 0.99965:
        score -= 0.5
        reasons.append("Price is trading close to a locally observed resistance zone.")

    if readiness < 50:
        decision = "WAIT"
        confidence = min(49.0, readiness)
        against.append("The Brain is still collecting enough local candle history for multi-timeframe confirmation.")
    else:
        magnitude = abs(score)
        agreement = max(bullish, bearish) / max(1, bullish + bearish)
        confidence = min(92.0, 42 + magnitude * 7 + agreement * 18)
        if score >= 3.4 and confidence >= 65:
            decision = "BUY"
        elif score <= -3.4 and confidence >= 65:
            decision = "SELL"
        else:
            decision = "WAIT"
            against.append("Evidence does not meet the conservative BUY/SELL threshold.")
    # ---- END UNCHANGED SCORING LOGIC ----

    if bullish >= 3 and bearish == 0:
        regime = "TREND_UP"
    elif bearish >= 3 and bullish == 0:
        regime = "TREND_DOWN"
    elif bullish and bearish:
        regime = "MIXED"
    elif ready:
        regime = "RANGE"
    else:
        regime = "LEARNING"

    risk = "HIGH" if readiness < 75 or regime in {"MIXED", "LEARNING"} else "MEDIUM"
    invalidation = None
    if decision == "BUY" and support:
        invalidation = round(support.low, 2)
    elif decision == "SELL" and resistance:
        invalidation = round(resistance.high, 2)

    data_quality = "GOOD" if readiness >= 75 else "LIMITED"
    if decision == "WAIT" and readiness < 50:
        message = "Collecting market history. WAIT is intentional until enough sampled candles exist."
    elif decision == "WAIT":
        message = "No high-quality directional setup currently passes the Brain's conservative threshold."
    else:
        message = "Experimental rules-based setup detected. This is evidence-based analysis, not a guarantee of future price direction."

    # Phase 4: populate the nullable statistical fields from the historical
    # similarity engine — INFORMATIONAL ONLY. The BUY/SELL/WAIT decision is
    # unchanged (rules-v0.1). historical_alignment does NOT influence the
    # decision. probability_calibrated stays False throughout Phase 4.
    stats_payload = await _build_phase4_stats_overlay(decision)

    return BrainAnalysis(
        timestamp=now,
        decision=decision,
        confidence=round(confidence, 1),
        readiness=readiness,
        regime=regime,
        risk=risk,
        score=round(score, 2),
        price=round(price, 4),
        reasons_for=reasons[:8],
        reasons_against=against[:8],
        invalidation=invalidation,
        support=support,
        resistance=resistance,
        timeframes=timeframe_states,
        data_quality=data_quality,
        message=message,
        historical_depth=historical_depth,
        instrument_consistency=instrument_consistency,
        technical_data_readiness=readiness,
        # Phase 3.2: technical_score is the SAME value as confidence —
        # renamed for display so it's not mistaken for a probability.
        technical_score=round(confidence, 1),
        # Phase 4: populated historical statistics (or None if insufficient data).
        historical_sample_size=stats_payload.get("historical_sample_size"),
        historical_direction_rate=stats_payload.get("historical_direction_rate"),
        historical_mfe=stats_payload.get("historical_mfe"),
        historical_mae=stats_payload.get("historical_mae"),
        historical_probability=stats_payload.get("historical_probability"),
        probability_calibrated=False,  # ALWAYS False in Phase 4 — calibration is later
        historical_alignment=stats_payload.get("historical_alignment"),
        historical_analogue_instrument=stats_payload.get("historical_analogue_instrument"),
        historical_analogue_horizon_minutes=stats_payload.get("historical_analogue_horizon_minutes"),
        historical_analogue_note=stats_payload.get("historical_analogue_note"),
    )


async def _build_phase4_stats_overlay(technical_decision: str) -> dict:
    """Phase 4: informational overlay — pull historical similarity stats
    for the GC_FRONT_MONTH instrument at the 1h horizon. Does NOT
    influence the BUY/SELL/WAIT decision. Returns empty dict on any
    error so the Brain never breaks if the learning layer is offline.
    """
    try:
        from app.services.learning import current_similarity
        result = await current_similarity(
            instrument="GC_FRONT_MONTH",
            horizon_minutes=60,
            technical_decision=technical_decision,
        )
        stats = result.get("statistics") or {}
        # historical_direction_rate = observed rate of the Brain's current
        # technical direction (BUY→UP rate, SELL→DOWN rate, WAIT→NEUTRAL rate).
        direction_rate = None
        if technical_decision == "BUY":
            direction_rate = stats.get("up_rate", {}).get("rate")
        elif technical_decision == "SELL":
            direction_rate = stats.get("down_rate", {}).get("rate")
        elif technical_decision == "WAIT":
            direction_rate = stats.get("neutral_rate", {}).get("rate")
        return {
            "historical_sample_size": result.get("sample_size"),
            "historical_direction_rate": direction_rate,
            "historical_mfe": stats.get("median_mfe"),
            "historical_mae": stats.get("median_mae"),
            # historical_probability = the observed directional rate (NOT a
            # calibrated probability). The frontend MUST phrase this as
            # "Among N similar GC futures historical states, X% produced a
            # <direction> outcome" — NOT "X% probability of success".
            "historical_probability": direction_rate,
            "historical_alignment": result.get("historical_alignment"),
            "historical_analogue_instrument": "GC_FRONT_MONTH",
            "historical_analogue_horizon_minutes": 60,
            "historical_analogue_note": (
                "Historical analogue instrument: GC_FRONT_MONTH (Yahoo gold futures). "
                "Live instrument may be XAUUSD_SPOT — these are SEPARATE instruments. "
                "Statistics are descriptive observations of similar past states, not "
                "calibrated probabilities of future outcomes."
            ),
        }
    except Exception:
        return {}
