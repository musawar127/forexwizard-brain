"""Phase 3: Historical Brain-feature snapshots.

For each historical candle, compute the Brain's deterministic feature
vector using the EXISTING `app.engine.indicators` and `app.engine.analysis`
helpers. No strategy optimization is performed — these snapshots are
read-only historical context used for measurement, not tuning.

Features computed:
  * EMA fast / slow           (from app.engine.indicators.ema)
  * RSI                        (from app.engine.indicators.rsi)
  * ATR                        (from app.engine.indicators.atr)
  * trend (BULLISH/BEARISH/RANGE/INSUFFICIENT_DATA)
  * regime (TREND_UP/TREND_DOWN/MIXED/RANGE/LEARNING)
  * swing high / swing low    (last 5-candle extreme)
  * support / resistance zones (reused from analysis._zones)
  * volatility                 (rolling std-dev of closes, 20-period)
  * session                    (UTC hour bucket: ASIA/EU/US/OFF)
  * timeframe alignment        (fraction of lower-TF candles whose trend
                                matches the higher-TF trend, 0..1)

Snapshots are persisted to `HistoricalFeatureSnapshot` so future outcome
evaluations can compare predictions against stored state at the prediction
timestamp.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from sqlalchemy import and_, select

from app.db.models import HistoricalFeatureSnapshot
from app.db.session import SessionLocal
from app.engine.candles import get_candles
from app.engine.indicators import atr, ema, rsi, slope
from app.models.market import Candle


# Convenience: map canonical interval name to a comparable "weight"
# (used for timeframe_alignment — higher TFs are weighted more).
TF_ORDER = ["1min", "5min", "15min", "30min", "1h", "4h", "1day"]


def _trend(candles: list[Candle]) -> tuple[str, float | None, float | None]:
    """Local copy of the trend classification so we don't couple to
    `app.engine.analysis._trend` private API. Same deterministic logic."""
    closes = [c.close for c in candles]
    if len(closes) < 6:
        return "INSUFFICIENT_DATA", None, None
    fast = ema(closes, min(8, max(3, len(closes) // 3)))
    slow_period = min(20, max(5, len(closes) // 2))
    slow = ema(closes, slow_period)
    sl = slope(closes, min(6, len(closes)))
    if fast is None or slow is None or sl is None:
        return "INSUFFICIENT_DATA", fast, slow
    av = atr(candles, min(14, max(5, len(candles) - 1))) if len(candles) >= 7 else None
    threshold = max((av or 0) * 0.03, 0.01)
    if fast > slow and sl > threshold:
        return "BULLISH", fast, slow
    if fast < slow and sl < -threshold:
        return "BEARISH", fast, slow
    return "RANGE", fast, slow


def _zones(candles: list[Candle], price: float) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    """Local support/resistance zone calc. Returns (support_low, support_high)
    and (resistance_low, resistance_high), or None for each if unknown."""
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
        support = (anchor - pad, anchor + pad)
    if highs:
        anchor = min(highs)
        resistance = (anchor - pad, anchor + pad)
    return support, resistance


def _swing_high(candles: list[Candle], lookback: int = 5) -> float | None:
    if len(candles) < lookback:
        return None
    window = candles[-lookback:]
    return max(c.high for c in window)


def _swing_low(candles: list[Candle], lookback: int = 5) -> float | None:
    if len(candles) < lookback:
        return None
    window = candles[-lookback:]
    return min(c.low for c in window)


def _volatility(closes: list[float], period: int = 20) -> float | None:
    """Rolling population std-dev of closes over the last `period`."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    var = sum((x - mean) ** 2 for x in window) / period
    return math.sqrt(var)


def _session(timestamp: datetime) -> str:
    """Crude UTC hour bucket. ASIA ~ 00-07 UTC, EU ~ 07-13 UTC, US ~ 13-21 UTC, OFF ~ 21-24."""
    h = timestamp.hour
    if 0 <= h < 7:
        return "ASIA"
    if 7 <= h < 13:
        return "EU"
    if 13 <= h < 21:
        return "US"
    return "OFF"


def _timeframe_alignment(
    higher_trend: str,
    lower_trends: list[str],
) -> float | None:
    """Fraction of lower-TF candles whose trend matches higher-TF trend.

    Returns None if higher_trend is INSUFFICIENT_DATA or no lower trends."""
    if higher_trend == "INSUFFICIENT_DATA" or not lower_trends:
        return None
    matching = sum(1 for t in lower_trends if t == higher_trend)
    return round(matching / len(lower_trends), 3)


async def compute_feature_snapshot(
    symbol: str,
    interval: str,
    timestamp: datetime,
) -> dict | None:
    """Compute + persist the Brain feature snapshot for one historical candle.

    Returns the snapshot dict (also serialized into HistoricalFeatureSnapshot
    row in the database). Returns None if no candle exists at (symbol,
    interval, timestamp).
    """
    # Pull the candle window ending at this timestamp. We want all candles
    # up to and including `timestamp` for this (symbol, interval).
    all_candles = await get_candles(interval, 5000, symbol)
    # Filter to those <= timestamp (UTC-aware comparison).
    ts_utc = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    candles = [c for c in all_candles if c.timestamp <= ts_utc]
    if not candles:
        return None
    # Locate the candle at the requested timestamp (or the immediately preceding one).
    target = candles[-1]

    closes = [c.close for c in candles]
    ema_fast = ema(closes, min(8, max(3, len(closes) // 3))) if len(closes) >= 6 else None
    slow_period = min(20, max(5, len(closes) // 2))
    ema_slow = ema(closes, slow_period) if len(closes) >= slow_period else None
    rsi_val = rsi(closes, min(14, max(5, len(closes) - 1))) if len(closes) >= 7 else None
    atr_val = atr(candles, min(14, max(5, len(candles) - 1))) if len(candles) >= 7 else None
    trend, _, _ = _trend(candles)

    bullish = sum(1 for c in candles[-20:] if c.close > c.open) if len(candles) >= 20 else 0
    bearish = sum(1 for c in candles[-20:] if c.close < c.open) if len(candles) >= 20 else 0
    if bullish >= 15 and bearish == 0:
        regime = "TREND_UP"
    elif bearish >= 15 and bullish == 0:
        regime = "TREND_DOWN"
    elif bullish and bearish:
        regime = "MIXED"
    elif len(candles) >= 20:
        regime = "RANGE"
    else:
        regime = "LEARNING"

    swing_h = _swing_high(candles)
    swing_l = _swing_low(candles)
    support, resistance = _zones(candles, target.close)
    volatility = _volatility(closes)
    session = _session(target.timestamp)

    # Timeframe alignment: if this is M5, look at M1 trends; if M15, M5; etc.
    alignment = None
    try:
        idx = TF_ORDER.index(interval)
        if idx > 0:
            lower_tf = TF_ORDER[idx - 1]
            lower_candles = await get_candles(lower_tf, 200, symbol)
            lower_ts_utc = [c for c in lower_candles if c.timestamp <= ts_utc]
            if lower_ts_utc:
                window = lower_ts_utc[-min(20, len(lower_ts_utc)):]
                lower_trends: list[str] = []
                # Compute the trend at each of the last 20 lower-TF candles.
                for i in range(1, len(window) + 1):
                    slice_candles = lower_ts_utc[: len(lower_ts_utc) - len(window) + i]
                    if len(slice_candles) >= 6:
                        t, _, _ = _trend(slice_candles)
                        lower_trends.append(t)
                alignment = _timeframe_alignment(trend, lower_trends)
    except (ValueError, IndexError):
        pass

    snapshot = {
        "symbol": symbol,
        "interval": interval,
        "timestamp": target.timestamp.isoformat(),
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "rsi": rsi_val,
        "atr": atr_val,
        "trend": trend,
        "regime": regime,
        "swing_high": swing_h,
        "swing_low": swing_l,
        "support_low": support[0] if support else None,
        "support_high": support[1] if support else None,
        "resistance_low": resistance[0] if resistance else None,
        "resistance_high": resistance[1] if resistance else None,
        "volatility": volatility,
        "session": session,
        "timeframe_alignment": alignment,
    }

    _persist_snapshot(snapshot)
    return snapshot


def _persist_snapshot(snapshot: dict) -> None:
    ts = datetime.fromisoformat(snapshot["timestamp"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts_naive = ts.astimezone(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        existing = session.scalar(
            select(HistoricalFeatureSnapshot.id).where(
                and_(
                    HistoricalFeatureSnapshot.symbol == snapshot["symbol"],
                    HistoricalFeatureSnapshot.interval == snapshot["interval"],
                    HistoricalFeatureSnapshot.timestamp == ts_naive,
                )
            )
        )
        if existing is not None:
            return
        session.add(
            HistoricalFeatureSnapshot(
                symbol=snapshot["symbol"],
                interval=snapshot["interval"],
                timestamp=ts_naive,
                ema_fast=snapshot.get("ema_fast"),
                ema_slow=snapshot.get("ema_slow"),
                rsi=snapshot.get("rsi"),
                atr=snapshot.get("atr"),
                trend=snapshot.get("trend"),
                regime=snapshot.get("regime"),
                swing_high=snapshot.get("swing_high"),
                swing_low=snapshot.get("swing_low"),
                support_low=snapshot.get("support_low"),
                support_high=snapshot.get("support_high"),
                resistance_low=snapshot.get("resistance_low"),
                resistance_high=snapshot.get("resistance_high"),
                volatility=snapshot.get("volatility"),
                session=snapshot.get("session"),
                timeframe_alignment=snapshot.get("timeframe_alignment"),
                features_json=json.dumps(snapshot, default=str),
                computed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        session.commit()
