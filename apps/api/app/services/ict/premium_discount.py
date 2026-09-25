"""Phase 5.7: ICT/SMC premium/discount engine.

For a dealing range, calculate:
  range_high, range_low, equilibrium
  premium = price > equilibrium (above 50%)
  discount = price < equilibrium (below 50%)

For BUY setups prefer structurally justified DISCOUNT locations.
For SELL setups prefer structurally justified PREMIUM locations.

This is CONTEXTUAL evidence, not an absolute rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class DealingRange:
    """A dealing range with equilibrium and premium/discount zones."""
    range_high: float
    range_low: float
    equilibrium: float  # 50% of the range
    # The price being evaluated
    price: float

    @property
    def is_premium(self) -> bool:
        """True if price is above the equilibrium (upper half of range)."""
        return self.price > self.equilibrium

    @property
    def is_discount(self) -> bool:
        """True if price is below the equilibrium (lower half of range)."""
        return self.price < self.equilibrium

    @property
    def location_label(self) -> Literal["PREMIUM", "DISCOUNT", "EQUILIBRIUM"]:
        if self.price > self.equilibrium:
            return "PREMIUM"
        if self.price < self.equilibrium:
            return "DISCOUNT"
        return "EQUILIBRIUM"

    @property
    def range_pct(self) -> float:
        """Price position in the range (0.0 = range_low, 1.0 = range_high)."""
        if self.range_high == self.range_low:
            return 0.5
        return (self.price - self.range_low) / (self.range_high - self.range_low)


def compute_dealing_range(
    range_high: float,
    range_low: float,
    price: float,
) -> DealingRange:
    """Compute the dealing range for the given high/low + current price.

    `range_high` and `range_low` should come from HTF swing highs/lows over
    a meaningful lookback (e.g. last 50-100 candles on H1 or D1).
    """
    if range_high <= range_low:
        # Defensive: if range is inverted, treat as a single price
        equilibrium = range_high
    else:
        equilibrium = (range_high + range_low) / 2.0
    return DealingRange(
        range_high=range_high,
        range_low=range_low,
        equilibrium=equilibrium,
        price=price,
    )


def compute_range_from_swings(
    swing_highs: list[float],
    swing_lows: list[float],
    price: float,
    lookback: int = 20,
) -> DealingRange | None:
    """Compute dealing range from the last N swing highs/lows.

    Uses the most recent swing high and most recent swing low as range bounds.
    Returns None if no swings available.
    """
    if not swing_highs or not swing_lows:
        return None
    recent_highs = swing_highs[-lookback:]
    recent_lows = swing_lows[-lookback:]
    range_high = max(recent_highs)
    range_low = min(recent_lows)
    return compute_dealing_range(range_high, range_low, price)
