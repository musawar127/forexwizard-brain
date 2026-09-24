"""Phase 5.6: stop-loss / invalidation engine.

SL is derived from technical invalidation:
  - BUY:  below the relevant swing low / structural support low,
          minus a volatility buffer (0.5 * ATR or 0.10% of price)
  - SELL: above the relevant swing high / structural resistance high,
          plus a volatility buffer

Never an arbitrary constant dollar offset. The structural level +
reason are persisted so the user can audit WHY this SL was chosen.

If the structural level cannot be computed (insufficient candles,
no clear swing) the plan is rejected with NO_VALID_SL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.market import Candle, Zone


@dataclass
class StopResult:
    status: Literal["OK", "NO_VALID_SL"]
    stop_loss: float | None = None
    invalidation_level: float | None = None  # the structural level itself
    invalidation_reason: str | None = None
    sl_distance: float | None = None  # |entry_reference - SL| in price units
    reason: str | None = None


def _volatility_buffer(atr: float | None, price: float) -> float:
    """Volatility buffer added below/above the structural level.

    0.5 * ATR when ATR is available; otherwise 0.10% of price (about
    $4.30 at $4300). This is NOT the SL itself — it's the cushion we
    add so the SL sits cleanly below the structural low, not exactly
    on it (which would get stopped out by noise).
    """
    if atr and atr > 0:
        return max(atr * 0.5, price * 0.001)
    return price * 0.001


def derive_buy_stop(
    *,
    entry_reference: float,
    support: Zone | None,
    candles: list[Candle],
    atr: float | None,
    lookback: int = 20,
) -> StopResult:
    """Derive a BUY plan stop-loss from swing-low invalidation.

    Priority of structural levels:
      1. support.low (the actionable support zone itself)
      2. recent swing low (min(low) over last N candles)
    """
    if entry_reference is None or entry_reference <= 0:
        return StopResult(status="NO_VALID_SL", reason="missing entry reference")

    # Candidate 1: support zone low
    candidates: list[tuple[float, str]] = []
    if support and support.low and support.low > 0:
        candidates.append((float(support.low), "support zone low"))

    # Candidate 2: recent swing low
    if candles and len(candles) >= 5:
        recent = candles[-min(lookback, len(candles)):]
        swing_low = min(c.low for c in recent)
        if swing_low > 0:
            candidates.append((swing_low, f"recent swing low (lookback {len(recent)})"))

    if not candidates:
        return StopResult(status="NO_VALID_SL", reason="no swing low or support available")

    # Choose the highest valid level below entry (closest valid SL).
    # A valid SL must be strictly below entry_reference.
    valid = [(lvl, src) for lvl, src in candidates if lvl < entry_reference]
    if not valid:
        return StopResult(
            status="NO_VALID_SL",
            reason=f"all structural levels are at/above entry {entry_reference}",
        )

    # Pick the highest valid level (closest to entry — tightest valid SL)
    structural_level, source = max(valid, key=lambda x: x[0])

    # Add volatility buffer below
    buf = _volatility_buffer(atr, entry_reference)
    stop_loss = round(structural_level - buf, 2)
    sl_distance = round(entry_reference - stop_loss, 2)

    # Sanity: SL must be at least 0.05% below entry (avoid trivial stops)
    min_distance = entry_reference * 0.0005
    if sl_distance < min_distance:
        return StopResult(
            status="NO_VALID_SL",
            reason=f"computed SL distance {sl_distance} < minimum {min_distance:.2f}",
        )

    return StopResult(
        status="OK",
        stop_loss=stop_loss,
        invalidation_level=round(structural_level, 2),
        invalidation_reason=f"BUY invalidation: break below {source} ({round(structural_level, 2)})",
        sl_distance=sl_distance,
    )


def derive_sell_stop(
    *,
    entry_reference: float,
    resistance: Zone | None,
    candles: list[Candle],
    atr: float | None,
    lookback: int = 20,
) -> StopResult:
    """Derive a SELL plan stop-loss from swing-high invalidation."""
    if entry_reference is None or entry_reference <= 0:
        return StopResult(status="NO_VALID_SL", reason="missing entry reference")

    candidates: list[tuple[float, str]] = []
    if resistance and resistance.high and resistance.high > 0:
        candidates.append((float(resistance.high), "resistance zone high"))

    if candles and len(candles) >= 5:
        recent = candles[-min(lookback, len(candles)):]
        swing_high = max(c.high for c in recent)
        if swing_high > 0:
            candidates.append((swing_high, f"recent swing high (lookback {len(recent)})"))

    if not candidates:
        return StopResult(status="NO_VALID_SL", reason="no swing high or resistance available")

    # Valid SL must be strictly above entry
    valid = [(lvl, src) for lvl, src in candidates if lvl > entry_reference]
    if not valid:
        return StopResult(
            status="NO_VALID_SL",
            reason=f"all structural levels are at/below entry {entry_reference}",
        )

    # Pick the lowest valid level (closest to entry — tightest valid SL)
    structural_level, source = min(valid, key=lambda x: x[0])

    buf = _volatility_buffer(atr, entry_reference)
    stop_loss = round(structural_level + buf, 2)
    sl_distance = round(stop_loss - entry_reference, 2)

    min_distance = entry_reference * 0.0005
    if sl_distance < min_distance:
        return StopResult(
            status="NO_VALID_SL",
            reason=f"computed SL distance {sl_distance} < minimum {min_distance:.2f}",
        )

    return StopResult(
        status="OK",
        stop_loss=stop_loss,
        invalidation_level=round(structural_level, 2),
        invalidation_reason=f"SELL invalidation: break above {source} ({round(structural_level, 2)})",
        sl_distance=sl_distance,
    )
