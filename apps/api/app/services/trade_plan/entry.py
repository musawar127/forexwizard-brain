"""Phase 5.6: entry zone engine — derive entry zones from technical structure.

For BUY:
  - Look for a pullback to actionable support (Zone.low ≤ price ≤ Zone.high)
  - Or a retest of broken resistance (now support) — optional, requires trend
    confirmation
  - Entry zone is the technical level +/- a small ATR-based buffer

For SELL:
  - Look for a pullback to actionable resistance
  - Or a retest of broken support (now resistance)

If structure quality is insufficient (no zone, weak touches, no ATR,
price too far from any actionable level) -> return NO_VALID_ENTRY.

Entry zones are ALWAYS expressed as a [low, high] band, never as a
single price level, because real entries rarely happen at one tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.market import Candle, Zone


@dataclass
class EntryResult:
    """Result of an entry-zone derivation attempt."""
    status: Literal[
        "ACTIONABLE",
        "WAIT_FOR_ENTRY",
        "NO_VALID_ENTRY",
    ]
    entry_low: float | None = None
    entry_high: float | None = None
    entry_type: str | None = None  # SUPPORT_RETEST / RESISTANCE_RETEST / etc.
    entry_reference: float | None = None  # midpoint of [low, high]
    reason: str | None = None


def _atr_buffer(atr: float | None, price: float) -> float:
    """Small volatility-aware buffer around the entry anchor.

    Uses 0.25 * ATR when ATR is available; falls back to 0.015% of
    price (about $0.64 at $4300) as a minimal sanity band.
    """
    if atr and atr > 0:
        return max(atr * 0.25, price * 0.0005)
    return price * 0.0005


def derive_buy_entry(
    *,
    price: float,
    support: Zone | None,
    candles: list[Candle],
    atr: float | None,
) -> EntryResult:
    """Derive a BUY entry zone anchored at actionable support.

    Rules:
      1. If support zone exists AND its top is within (1.0 * ATR) below
         current price, treat it as the entry anchor.
      2. If price is already inside the support zone, entry is ACTIONABLE
         around the zone midpoint.
      3. If no support or support is too far away (more than 2.0 * ATR),
         return NO_VALID_ENTRY — do not invent arbitrary pullback levels.
    """
    if not support or price is None or price <= 0:
        return EntryResult(status="NO_VALID_ENTRY", reason="no actionable support zone")

    # Distance from current price to the support zone
    zone_top = float(support.high)
    zone_low = float(support.low)
    buffer = _atr_buffer(atr, price)

    # If price is currently above the zone (normal pullback setup)
    if price >= zone_low:
        distance = price - zone_top
        # Within reach? (<= 2 * ATR)
        max_reach = (atr * 2.0) if atr and atr > 0 else price * 0.005
        if distance > max_reach:
            return EntryResult(
                status="NO_VALID_ENTRY",
                reason=f"support zone {zone_low}-{zone_top} too far below price ({distance:.2f} > 2*ATR={max_reach:.2f})",
            )
        # Within reach -> plan a pullback entry
        entry_low = zone_low - buffer
        entry_high = zone_top + buffer
        return EntryResult(
            status="WAIT_FOR_ENTRY",
            entry_low=round(entry_low, 2),
            entry_high=round(entry_high, 2),
            entry_type="SUPPORT_RETEST",
            entry_reference=round((entry_low + entry_high) / 2.0, 2),
            reason=f"entry on pullback to support zone ({zone_low}-{zone_top})",
        )

    # Price is below the zone — already inside or below. This is unusual
    # but may still be a valid entry (price is in the support zone now).
    if price >= zone_low - buffer and price <= zone_top + buffer:
        entry_low = zone_low - buffer
        entry_high = zone_top + buffer
        return EntryResult(
            status="ACTIONABLE",
            entry_low=round(entry_low, 2),
            entry_high=round(entry_high, 2),
            entry_type="SUPPORT_RETEST",
            entry_reference=round((entry_low + entry_high) / 2.0, 2),
            reason="price is inside support zone — entry actionable",
        )

    return EntryResult(
        status="NO_VALID_ENTRY",
        reason=f"price {price} below support zone {zone_low}-{zone_top} (potential breakdown, no BUY)",
    )


def derive_sell_entry(
    *,
    price: float,
    resistance: Zone | None,
    candles: list[Candle],
    atr: float | None,
) -> EntryResult:
    """Derive a SELL entry zone anchored at actionable resistance.

    Symmetric to derive_buy_entry.
    """
    if not resistance or price is None or price <= 0:
        return EntryResult(status="NO_VALID_ENTRY", reason="no actionable resistance zone")

    zone_top = float(resistance.high)
    zone_low = float(resistance.low)
    buffer = _atr_buffer(atr, price)

    # If price is currently below the resistance zone (normal retest setup)
    if price <= zone_top:
        distance = zone_low - price
        max_reach = (atr * 2.0) if atr and atr > 0 else price * 0.005
        if distance > max_reach:
            return EntryResult(
                status="NO_VALID_ENTRY",
                reason=f"resistance zone {zone_low}-{zone_top} too far above price ({distance:.2f} > 2*ATR={max_reach:.2f})",
            )
        entry_low = zone_low - buffer
        entry_high = zone_top + buffer
        return EntryResult(
            status="WAIT_FOR_ENTRY",
            entry_low=round(entry_low, 2),
            entry_high=round(entry_high, 2),
            entry_type="RESISTANCE_RETEST",
            entry_reference=round((entry_low + entry_high) / 2.0, 2),
            reason=f"entry on retest to resistance zone ({zone_low}-{zone_top})",
        )

    # Price is inside the resistance zone
    if price >= zone_low - buffer and price <= zone_top + buffer:
        entry_low = zone_low - buffer
        entry_high = zone_top + buffer
        return EntryResult(
            status="ACTIONABLE",
            entry_low=round(entry_low, 2),
            entry_high=round(entry_high, 2),
            entry_type="RESISTANCE_RETEST",
            entry_reference=round((entry_low + entry_high) / 2.0, 2),
            reason="price is inside resistance zone — entry actionable",
        )

    return EntryResult(
        status="NO_VALID_ENTRY",
        reason=f"price {price} above resistance zone {zone_low}-{zone_top} (potential breakout, no SELL)",
    )
