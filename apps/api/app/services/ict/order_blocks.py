"""Phase 5.7: ICT/SMC Order Block engine.

Order Blocks are the last opposing candle before a strong displacement move
that breaks structure (BOS):
  - Bullish OB = last BEARISH candle before a bullish displacement that
    breaks above a swing high (BOS up).
  - Bearish OB = last BULLISH candle before a bearish displacement that
    breaks below a swing low (BOS down).

Quality gates (avoid identifying every opposite candle as an OB):
  - Must have a BOS confirmation (the displacement must break structure)
  - Displacement must be >= 1.0 * ATR (strong move)
  - The OB candle must be the most recent opposite candle before the break
  - Mark as mitigated when price returns to the zone
  - Mark as invalidated when price closes through the zone's far edge
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.models.market import Candle
from .structures import StructureEvent


@dataclass
class OrderBlock:
    """A detected Order Block zone."""
    timestamp: datetime          # timestamp of the OB candle (origin)
    timeframe: str
    direction: Literal["BULLISH", "BEARISH"]
    # Zone bounds
    upper: float                # max(high, close) of the OB candle
    lower: float                # min(low, close) of the OB candle
    midpoint: float
    # The BOS event that confirmed this OB
    confirmed_by_bos: StructureEvent | None = None
    bos_timestamp: datetime | None = None
    # Quality score 0..100 — higher = stronger OB
    quality: float = 0.0
    # Mitigation / invalidation
    mitigated: bool = False
    mitigated_at: datetime | None = None
    invalidated: bool = False


def detect_order_blocks(
    candles: list[Candle],
    events: list[StructureEvent],
    timeframe: str,
    atr_value: float | None = None,
    lookback: int = 10,
) -> list[OrderBlock]:
    """Detect order blocks from BOS events.

    For each BOS event, walk back to find the last opposite candle (the OB).
    Apply quality gates: skip if displacement is weak or no BOS exists.
    """
    obs: list[OrderBlock] = []
    if not candles or not events:
        return obs

    # Index events by candle_index (approximate — use timestamp matching)
    candle_by_ts = {c.timestamp: i for i, c in enumerate(candles)}

    for event in events:
        # Only BOS events create OBs (CHoCH/MSS may also — they're all "displacement + structure break")
        if event.event_type not in ("BOS", "CHoCH", "MSS"):
            continue

        # Find the candle index of the BOS event
        bos_idx = candle_by_ts.get(event.timestamp)
        if bos_idx is None:
            # Find closest by timestamp
            closest_idx = _find_closest_candle_index(candles, event.timestamp)
            if closest_idx is None:
                continue
            bos_idx = closest_idx

        # Walk back to find the last opposite candle
        if event.direction == "BULLISH":
            # Bullish OB = last BEARISH candle (close < open) before the bullish BOS
            ob_idx = _find_last_opposite_candle(candles, bos_idx, "BEARISH", lookback)
            if ob_idx is None:
                continue
            ob_candle = candles[ob_idx]
            # Check that close < open (bearish candle)
            if ob_candle.close >= ob_candle.open:
                continue
            upper = max(float(ob_candle.high), float(ob_candle.close))
            lower = min(float(ob_candle.low), float(ob_candle.open))
        else:
            # Bearish OB = last BULLISH candle (close > open) before the bearish BOS
            ob_idx = _find_last_opposite_candle(candles, bos_idx, "BULLISH", lookback)
            if ob_idx is None:
                continue
            ob_candle = candles[ob_idx]
            if ob_candle.close <= ob_candle.open:
                continue
            upper = max(float(ob_candle.high), float(ob_candle.open))
            lower = min(float(ob_candle.low), float(ob_candle.close))

        # Quality gate: displacement must be >= 1.0 * ATR
        displacement = abs(event.price - event.broken_level)
        if atr_value and atr_value > 0:
            if displacement < 1.0 * atr_value:
                continue

        # Compute quality score (0..100)
        # - Base: 40 (passes minimum gates)
        # - +20 if BOS (vs CHoCH/MSS)
        # - +20 if displacement >= 1.5 * ATR
        # - +20 if displacement >= 2.0 * ATR
        quality = 40.0
        if event.event_type == "BOS":
            quality += 20.0
        if atr_value and atr_value > 0:
            if displacement >= 1.5 * atr_value:
                quality += 20.0
            if displacement >= 2.0 * atr_value:
                quality += 20.0

        ob = OrderBlock(
            timestamp=ob_candle.timestamp,
            timeframe=timeframe,
            direction=event.direction,  # type: ignore[arg-type]
            upper=upper,
            lower=lower,
            midpoint=round((upper + lower) / 2.0, 4),
            confirmed_by_bos=event,
            bos_timestamp=event.timestamp,
            quality=min(100.0, quality),
        )

        # Check mitigation forward from the BOS event
        _check_ob_mitigation(ob, candles[bos_idx + 1:bos_idx + 100])

        obs.append(ob)

    return obs


def _find_last_opposite_candle(
    candles: list[Candle],
    bos_idx: int,
    opposite_direction: Literal["BULLISH", "BEARISH"],
    lookback: int,
) -> int | None:
    """Find the index of the last opposite candle before bos_idx."""
    start = max(0, bos_idx - lookback)
    for i in range(bos_idx - 1, start - 1, -1):
        if i < 0:
            break
        c = candles[i]
        if opposite_direction == "BULLISH" and c.close > c.open:
            return i
        if opposite_direction == "BEARISH" and c.close < c.open:
            return i
    return None


def _find_closest_candle_index(candles: list[Candle], ts: datetime) -> int | None:
    """Find the candle index with timestamp closest to ts."""
    if not candles:
        return None
    closest_i = 0
    closest_diff = abs((candles[0].timestamp - ts).total_seconds())
    for i, c in enumerate(candles[1:], 1):
        diff = abs((c.timestamp - ts).total_seconds())
        if diff < closest_diff:
            closest_diff = diff
            closest_i = i
    return closest_i


def _check_ob_mitigation(ob: OrderBlock, forward_candles: list[Candle]) -> None:
    """Walk forward through candles to check mitigation/invalidation."""
    if not forward_candles:
        return
    for c in forward_candles:
        if ob.direction == "BULLISH":
            # Mitigated when price retraces back into the OB zone
            if c.low <= ob.upper and not ob.mitigated:
                ob.mitigated = True
                ob.mitigated_at = c.timestamp
            # Invalidated when price closes below the OB lower edge
            if c.close < ob.lower:
                ob.invalidated = True
                break
        else:  # BEARISH
            if c.high >= ob.lower and not ob.mitigated:
                ob.mitigated = True
                ob.mitigated_at = c.timestamp
            if c.close > ob.upper:
                ob.invalidated = True
                break


def active_order_blocks(obs: list[OrderBlock], direction: Literal["BULLISH", "BEARISH"] | None = None) -> list[OrderBlock]:
    """Return OBs that are still actionable (not invalidated)."""
    out = [o for o in obs if not o.invalidated]
    if direction is not None:
        out = [o for o in out if o.direction == direction]
    # Sort by quality (highest first)
    return sorted(out, key=lambda o: -o.quality)


def nearest_ob_to_price(
    obs: list[OrderBlock],
    price: float,
    direction: Literal["BULLISH", "BEARISH"] | None = None,
) -> OrderBlock | None:
    """Return the active OB nearest to the current price (by midpoint)."""
    active = active_order_blocks(obs, direction=direction)
    if not active:
        return None
    return min(active, key=lambda o: abs(o.midpoint - price))
