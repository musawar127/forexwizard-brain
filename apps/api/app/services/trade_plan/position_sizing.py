"""Phase 5.6: position sizing.

Position sizing is OPT-IN — we never assume a user's preferred
risk percentage or a broker's contract spec. The user provides
account_equity and risk_percent; we read the contract spec from
the instrument_meta module. If the spec is not configured, we
return POSITION_SIZE_UNAVAILABLE rather than guessing.

Formulas:
  risk_amount   = account_equity * risk_percent / 100
  ticks_at_risk = sl_distance / tick_size
  loss_per_lot  = ticks_at_risk * tick_value
  lot_size      = risk_amount / loss_per_lot
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .instrument_meta import get_instrument_meta


@dataclass
class PositionSizeResult:
    status: Literal["OK", "POSITION_SIZE_UNAVAILABLE"]
    risk_amount: float | None = None
    sl_distance: float | None = None
    lot_size: float | None = None
    contract_size: float | None = None
    tick_size: float | None = None
    tick_value: float | None = None
    instrument: str | None = None
    reason: str | None = None


def calculate_position_size(
    *,
    account_equity: float,
    risk_percent: float,
    sl_distance: float,
    instrument: str = "XAU/USD",
) -> PositionSizeResult:
    """Compute lot size from account equity + risk % + SL distance.

    Returns POSITION_SIZE_UNAVAILABLE when:
      - account_equity or risk_percent or sl_distance non-positive
      - instrument spec not configured (no env vars)
      - tick_value <= 0 (would divide by zero)
      - sl_distance < tick_size (would round to 0 ticks)
    """
    if account_equity is None or account_equity <= 0:
        return PositionSizeResult(status="POSITION_SIZE_UNAVAILABLE", reason="non-positive account equity")
    if risk_percent is None or risk_percent <= 0 or risk_percent > 100:
        return PositionSizeResult(status="POSITION_SIZE_UNAVAILABLE", reason="invalid risk_percent (must be 0..100)")
    if sl_distance is None or sl_distance <= 0:
        return PositionSizeResult(status="POSITION_SIZE_UNAVAILABLE", reason="non-positive SL distance")

    meta = get_instrument_meta(instrument)
    if meta is None:
        return PositionSizeResult(
            status="POSITION_SIZE_UNAVAILABLE",
            reason=f"no broker spec configured for {instrument} — set XAUUSD_CONTRACT_SIZE / XAUUSD_TICK_SIZE / XAUUSD_TICK_VALUE env vars",
            instrument=instrument,
        )

    risk_amount = account_equity * risk_percent / 100.0
    ticks_at_risk = sl_distance / meta["tick_size"]
    if ticks_at_risk < 1:
        return PositionSizeResult(
            status="POSITION_SIZE_UNAVAILABLE",
            reason=f"SL distance {sl_distance} smaller than tick_size {meta['tick_size']} — rounds to 0 ticks",
            risk_amount=round(risk_amount, 2),
            sl_distance=round(sl_distance, 2),
            instrument=instrument,
        )

    loss_per_lot = ticks_at_risk * meta["tick_value"]
    if loss_per_lot <= 0:
        return PositionSizeResult(
            status="POSITION_SIZE_UNAVAILABLE",
            reason="non-positive loss per lot",
            risk_amount=round(risk_amount, 2),
            sl_distance=round(sl_distance, 2),
            instrument=instrument,
        )

    lot_size = risk_amount / loss_per_lot

    return PositionSizeResult(
        status="OK",
        risk_amount=round(risk_amount, 2),
        sl_distance=round(sl_distance, 2),
        lot_size=round(lot_size, 4),
        contract_size=meta["contract_size"],
        tick_size=meta["tick_size"],
        tick_value=meta["tick_value"],
        instrument=instrument,
    )
