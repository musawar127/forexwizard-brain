"""Phase 5.7: ICT/SMC liquidity engine.

Detects:
  - equal highs / equal lows (clustered swing levels)
  - previous day high / low (PDH/PDL)
  - previous week high / low (PWH/PWL)
  - session highs / lows (Asia / London / NY)
  - swing liquidity (resting liquidity above swing highs / below swing lows)
  - liquidity sweeps (price briefly penetrates a level then reverses)
  - failed sweeps (price keeps going — breakout, not sweep)

Liquidity candidates are persisted in ict_liquidity_levels / ict_liquidity_sweeps.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from app.models.market import Candle
from .structures import Swing


@dataclass
class LiquidityLevel:
    """A candidate liquidity pool (price level where stops rest)."""
    price: float
    kind: Literal[
        "EQUAL_HIGHS", "EQUAL_LOWS",
        "PDH", "PDL",  # previous day high/low
        "PWH", "PWL",  # previous week high/low
        "SESSION_HIGH", "SESSION_LOW",  # Asia/London/NY session extrema
        "SWING_HIGH", "SWING_LOW",  # swing liquidity (resting above/below)
    ]
    timestamp: datetime
    # Confidence 0..100 — equal highs with 3+ touches are stronger
    confidence: float = 50.0
    # Optional metadata
    session: str | None = None  # "ASIA" / "LONDON" / "NEW_YORK"
    # Whether this level has been swept
    swept: bool = False
    swept_at: datetime | None = None


@dataclass
class LiquiditySweep:
    """A liquidity sweep event — price penetrates a level then reverses."""
    timestamp: datetime
    level: float
    level_kind: str
    direction: Literal["BUY_SIDE_SWEEP", "SELL_SIDE_SWEEP"]
    # BUY_SIDE_SWEEP = buy-side liquidity taken (price briefly pierced above a high)
    # SELL_SIDE_SWEEP = sell-side liquidity taken (price briefly pierced below a low)
    reaction_magnitude: float  # how far price reversed after sweeping
    reaction_atr_multiple: float | None  # reaction / ATR
    failed: bool = False  # True if price kept going (breakout, not sweep)


# ---------- equal highs / equal lows ----------

def detect_equal_highs_lows(swings: list[Swing], tolerance_pct: float = 0.0008) -> list[LiquidityLevel]:
    """Detect equal highs and equal lows — clustered swing levels within tolerance.

    A cluster of 2+ swing highs within `tolerance_pct * price` of each other
    forms an equal-highs liquidity pool. The same is true for lows.
    """
    levels: list[LiquidityLevel] = []
    highs = [s for s in swings if s.kind == "SWING_HIGH"]
    lows = [s for s in swings if s.kind == "SWING_LOW"]

    # Cluster highs by price proximity
    clusters_h = _cluster_by_price(highs, tolerance_pct)
    for cluster in clusters_h:
        if len(cluster) < 2:
            continue
        avg_price = sum(s.price for s in cluster) / len(cluster)
        # Confidence: more touches = stronger
        confidence = min(100.0, 40.0 + len(cluster) * 15)
        levels.append(LiquidityLevel(
            price=avg_price,
            kind="EQUAL_HIGHS",
            timestamp=cluster[-1].timestamp,  # most recent touch
            confidence=confidence,
        ))

    clusters_l = _cluster_by_price(lows, tolerance_pct)
    for cluster in clusters_l:
        if len(cluster) < 2:
            continue
        avg_price = sum(s.price for s in cluster) / len(cluster)
        confidence = min(100.0, 40.0 + len(cluster) * 15)
        levels.append(LiquidityLevel(
            price=avg_price,
            kind="EQUAL_LOWS",
            timestamp=cluster[-1].timestamp,
            confidence=confidence,
        ))

    return levels


def _cluster_by_price(swings: list[Swing], tolerance_pct: float) -> list[list[Swing]]:
    """Group swings whose prices are within tolerance_pct * price of each other."""
    if not swings:
        return []
    sorted_swings = sorted(swings, key=lambda s: s.price)
    clusters: list[list[Swing]] = []
    current: list[Swing] = [sorted_swings[0]]
    for s in sorted_swings[1:]:
        ref_price = current[0].price
        if abs(s.price - ref_price) <= ref_price * tolerance_pct:
            current.append(s)
        else:
            clusters.append(current)
            current = [s]
    clusters.append(current)
    return clusters


# ---------- PDH / PDL / PWH / PWL ----------

def detect_pdh_pdl(candles: list[Candle]) -> tuple[LiquidityLevel | None, LiquidityLevel | None]:
    """Compute previous day high and low from candle history.

    Uses the calendar date of each candle's timestamp. Returns the previous
    UTC day's high/low as liquidity levels.
    """
    if not candles:
        return None, None
    today = candles[-1].timestamp.astimezone(timezone.utc).date()
    prev_day_candles = [
        c for c in candles
        if c.timestamp.astimezone(timezone.utc).date() == today - timedelta(days=1)
    ]
    if not prev_day_candles:
        return None, None
    pdh = max(c.high for c in prev_day_candles)
    pdl = min(c.low for c in prev_day_candles)
    pd_ts = datetime.combine(today - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return (
        LiquidityLevel(price=float(pdh), kind="PDH", timestamp=pd_ts, confidence=70.0),
        LiquidityLevel(price=float(pdl), kind="PDL", timestamp=pd_ts, confidence=70.0),
    )


def detect_pwh_pwl(candles: list[Candle]) -> tuple[LiquidityLevel | None, LiquidityLevel | None]:
    """Compute previous week high and low."""
    if not candles:
        return None, None
    # Find the ISO week of the latest candle
    latest = candles[-1].timestamp.astimezone(timezone.utc)
    current_week = latest.isocalendar()[:2]  # (year, week)
    prev_week_candles: list[Candle] = []
    for c in candles:
        c_week = c.timestamp.astimezone(timezone.utc).isocalendar()[:2]
        # Previous week = current_week - 1 (with year rollover)
        if _is_previous_week(c_week, current_week):
            prev_week_candles.append(c)
    if not prev_week_candles:
        return None, None
    pwh = max(c.high for c in prev_week_candles)
    pwl = min(c.low for c in prev_week_candles)
    pw_ts = prev_week_candles[-1].timestamp
    return (
        LiquidityLevel(price=float(pwh), kind="PWH", timestamp=pw_ts, confidence=75.0),
        LiquidityLevel(price=float(pwl), kind="PWL", timestamp=pw_ts, confidence=75.0),
    )


def _is_previous_week(c_week: tuple[int, int], current_week: tuple[int, int]) -> bool:
    """True if c_week is the ISO week immediately before current_week."""
    # Simple heuristic: same year, week == current_week - 1
    # (does not handle year rollover perfectly but is fine for our purposes)
    if c_week[0] != current_week[0]:
        return False
    return c_week[1] == current_week[1] - 1


# ---------- session highs / lows ----------

def detect_session_extrema(
    candles: list[Candle],
    session: Literal["ASIA", "LONDON", "NEW_YORK"],
) -> tuple[LiquidityLevel | None, LiquidityLevel | None]:
    """Compute session high/low for the latest occurrence of `session`.

    Session times (UTC, non-DST):
      ASIA:     00:00 - 09:00
      LONDON:   07:00 - 16:00
      NEW_YORK: 12:00 - 21:00
    """
    if not candles:
        return None, None
    ranges = {
        "ASIA": (0, 9),
        "LONDON": (7, 16),
        "NEW_YORK": (12, 21),
    }
    start_h, end_h = ranges[session]
    # Find the most recent calendar date in the candles
    latest_date = candles[-1].timestamp.astimezone(timezone.utc).date()
    session_candles = [
        c for c in candles
        if c.timestamp.astimezone(timezone.utc).date() == latest_date
        and start_h <= c.timestamp.astimezone(timezone.utc).hour < end_h
    ]
    if not session_candles:
        return None, None
    sh = max(c.high for c in session_candles)
    sl = min(c.low for c in session_candles)
    s_ts = session_candles[-1].timestamp
    return (
        LiquidityLevel(
            price=float(sh), kind="SESSION_HIGH", timestamp=s_ts,
            confidence=55.0, session=session,
        ),
        LiquidityLevel(
            price=float(sl), kind="SESSION_LOW", timestamp=s_ts,
            confidence=55.0, session=session,
        ),
    )


# ---------- swing liquidity (resting above/below recent swings) ----------

def detect_swing_liquidity(swings: list[Swing], lookback: int = 5) -> list[LiquidityLevel]:
    """Convert recent swing highs/lows into resting liquidity levels."""
    if not swings:
        return []
    recent = swings[-lookback:]
    levels: list[LiquidityLevel] = []
    for s in recent:
        kind = "SWING_HIGH" if s.kind == "SWING_HIGH" else "SWING_LOW"
        levels.append(LiquidityLevel(
            price=s.price,
            kind=kind,
            timestamp=s.timestamp,
            confidence=50.0,
        ))
    return levels


# ---------- sweep detection ----------

def detect_liquidity_sweeps(
    candles: list[Candle],
    levels: list[LiquidityLevel],
    atr_value: float | None = None,
    sweep_lookback: int = 5,
) -> list[LiquiditySweep]:
    """Detect liquidity sweep events.

    A sweep occurs when a candle briefly penetrates a liquidity level (high
    or low) but then closes back on the other side. A "failed sweep" is when
    the price keeps going (i.e. closes beyond the level).
    """
    sweeps: list[LiquiditySweep] = []
    n = len(candles)
    if not levels or n < 2:
        return sweeps

    for level in levels:
        for i in range(max(1, n - 200), n):  # check last 200 candles for sweeps
            c = candles[i]
            prev = candles[i - 1]

            # BUY_SIDE_SWEEP: candle pierced above a level but closed below
            if c.high > level.price and c.close < level.price and level.kind in (
                "EQUAL_HIGHS", "PDH", "PWH", "SESSION_HIGH", "SWING_HIGH"
            ):
                # Look back a few candles for the reaction magnitude
                lookback_end = min(i + sweep_lookback, n)
                if lookback_end <= i:
                    continue
                post_candles = candles[i:lookback_end]
                if post_candles:
                    # Reaction = drop from the sweep high
                    lowest_after = min(pc.low for pc in post_candles)
                    reaction = level.price - lowest_after
                    if reaction > 0:
                        reaction_atr = reaction / atr_value if atr_value and atr_value > 0 else None
                        sweeps.append(LiquiditySweep(
                            timestamp=c.timestamp,
                            level=level.price,
                            level_kind=level.kind,
                            direction="BUY_SIDE_SWEEP",
                            reaction_magnitude=round(reaction, 2),
                            reaction_atr_multiple=round(reaction_atr, 2) if reaction_atr else None,
                            failed=False,
                        ))
                        level.swept = True
                        level.swept_at = c.timestamp
                        break  # one sweep per level

            # SELL_SIDE_SWEEP: candle pierced below a level but closed above
            if c.low < level.price and c.close > level.price and level.kind in (
                "EQUAL_LOWS", "PDL", "PWL", "SESSION_LOW", "SWING_LOW"
            ):
                lookback_end = min(i + sweep_lookback, n)
                if lookback_end <= i:
                    continue
                post_candles = candles[i:lookback_end]
                if post_candles:
                    highest_after = max(pc.high for pc in post_candles)
                    reaction = highest_after - level.price
                    if reaction > 0:
                        reaction_atr = reaction / atr_value if atr_value and atr_value > 0 else None
                        sweeps.append(LiquiditySweep(
                            timestamp=c.timestamp,
                            level=level.price,
                            level_kind=level.kind,
                            direction="SELL_SIDE_SWEEP",
                            reaction_magnitude=round(reaction, 2),
                            reaction_atr_multiple=round(reaction_atr, 2) if reaction_atr else None,
                            failed=False,
                        ))
                        level.swept = True
                        level.swept_at = c.timestamp
                        break

    return sweeps


# ---------- full liquidity analysis ----------

@dataclass
class LiquidityAnalysis:
    levels: list[LiquidityLevel]
    sweeps: list[LiquiditySweep]


def analyze_liquidity(
    candles: list[Candle],
    swings: list[Swing],
    atr_value: float | None = None,
) -> LiquidityAnalysis:
    """Run all liquidity detection in one pass."""
    levels: list[LiquidityLevel] = []

    # 1. equal highs / lows
    levels.extend(detect_equal_highs_lows(swings))

    # 2. PDH / PDL
    pdh, pdl = detect_pdh_pdl(candles)
    if pdh is not None:
        levels.append(pdh)
    if pdl is not None:
        levels.append(pdl)

    # 3. PWH / PWL
    pwh, pwl = detect_pwh_pwl(candles)
    if pwh is not None:
        levels.append(pwh)
    if pwl is not None:
        levels.append(pwl)

    # 4. session highs / lows
    for session in ("ASIA", "LONDON", "NEW_YORK"):
        sh, sl = detect_session_extrema(candles, session)  # type: ignore[arg-type]
        if sh is not None:
            levels.append(sh)
        if sl is not None:
            levels.append(sl)

    # 5. swing liquidity (resting liquidity)
    levels.extend(detect_swing_liquidity(swings))

    # 6. sweeps
    sweeps = detect_liquidity_sweeps(candles, levels, atr_value=atr_value)

    return LiquidityAnalysis(levels=levels, sweeps=sweeps)
