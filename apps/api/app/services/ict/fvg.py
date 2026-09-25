"""Phase 5.7: ICT/SMC Fair Value Gap (FVG) engine.

Detects bullish and bearish FVGs using the 3-candle pattern:
  - Bullish FVG: candle[i-1].high < candle[i+1].low
    (gap up between candle 1's high and candle 3's low)
  - Bearish FVG: candle[i-1].low > candle[i+1].high
    (gap down between candle 1's low and candle 3's high)

Tracks mitigation (partial fill) and full fill / invalidation.
Never uses an FVG after it has become structurally invalid.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models.market import Candle


@dataclass
class FVG:
    """A detected Fair Value Gap."""
    timestamp: datetime
    timeframe: str
    direction: Literal["BULLISH", "BEARISH"]
    upper: float       # upper bound of the gap
    lower: float       # lower bound of the gap
    midpoint: float    # (upper + lower) / 2
    # Mitigation state
    mitigated: bool = False
    fully_filled: bool = False
    invalidated: bool = False
    # The candle index where the FVG was created (the middle candle)
    origin_candle_index: int = 0
    # Mitigation tracking
    mitigated_at: datetime | None = None
    filled_at: datetime | None = None


def detect_fvgs(candles: list[Candle], timeframe: str) -> list[FVG]:
    """Detect all FVGs in the candle history.

    Walks forward through candles. For each triplet (i-1, i, i+1), check
    if a gap exists. Then walk forward to check for mitigation/fill.
    """
    fvgs: list[FVG] = []
    n = len(candles)
    if n < 3:
        return fvgs

    for i in range(1, n - 1):
        c1 = candles[i - 1]
        c3 = candles[i + 1]

        # Bullish FVG: c1.high < c3.low (gap up)
        if c1.high < c3.low:
            lower = float(c1.high)
            upper = float(c3.low)
            fvg = FVG(
                timestamp=candles[i].timestamp,
                timeframe=timeframe,
                direction="BULLISH",
                upper=upper,
                lower=lower,
                midpoint=round((upper + lower) / 2.0, 4),
                origin_candle_index=i,
            )
            # Check forward for mitigation
            _check_mitigation(fvg, candles[i + 2:i + 100])  # look ahead up to 100 candles
            fvgs.append(fvg)

        # Bearish FVG: c1.low > c3.high (gap down)
        if c1.low > c3.high:
            upper = float(c1.low)
            lower = float(c3.high)
            fvg = FVG(
                timestamp=candles[i].timestamp,
                timeframe=timeframe,
                direction="BEARISH",
                upper=upper,
                lower=lower,
                midpoint=round((upper + lower) / 2.0, 4),
                origin_candle_index=i,
            )
            _check_mitigation(fvg, candles[i + 2:i + 100])
            fvgs.append(fvg)

    return fvgs


def _check_mitigation(fvg: FVG, forward_candles: list[Candle]) -> None:
    """Walk forward through candles to check if the FVG was mitigated/filled.

    For a BULLISH FVG (gap up between c1.high and c3.low):
      - Mitigated when price retraces into the gap (closes <= upper)
      - Fully filled when price reaches the lower bound
      - Invalidated when price closes below the lower bound (structural break)
    For a BEARISH FVG: symmetric.
    """
    if not forward_candles:
        return

    for c in forward_candles:
        if fvg.direction == "BULLISH":
            if c.low <= fvg.lower:
                fvg.fully_filled = True
                fvg.invalidated = True
                fvg.filled_at = c.timestamp
                fvg.mitigated_at = fvg.mitigated_at or c.timestamp
                break  # fully filled = terminal
            if c.low <= fvg.upper and not fvg.mitigated:
                fvg.mitigated = True
                fvg.mitigated_at = c.timestamp
            # If price closes well below the lower bound, invalidate the FVG
            if c.close < fvg.lower - (fvg.upper - fvg.lower):
                fvg.invalidated = True
                break
        else:  # BEARISH
            if c.high >= fvg.upper:
                fvg.fully_filled = True
                fvg.invalidated = True
                fvg.filled_at = c.timestamp
                fvg.mitigated_at = fvg.mitigated_at or c.timestamp
                break
            if c.high >= fvg.lower and not fvg.mitigated:
                fvg.mitigated = True
                fvg.mitigated_at = c.timestamp
            if c.close > fvg.upper + (fvg.upper - fvg.lower):
                fvg.invalidated = True
                break


def active_fvgs(fvgs: list[FVG], direction: Literal["BULLISH", "BEARISH"] | None = None) -> list[FVG]:
    """Return FVGs that are still actionable (not fully filled, not invalidated)."""
    out = [f for f in fvgs if not f.invalidated and not f.fully_filled]
    if direction is not None:
        out = [f for f in out if f.direction == direction]
    return out


def nearest_fvg_to_price(
    fvgs: list[FVG],
    price: float,
    direction: Literal["BULLISH", "BEARISH"] | None = None,
) -> FVG | None:
    """Return the active FVG nearest to the current price."""
    active = active_fvgs(fvgs, direction=direction)
    if not active:
        return None
    return min(active, key=lambda f: abs(f.midpoint - price))
