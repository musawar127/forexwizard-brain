"""Phase 4.1: roll-detection + outcome-window validity helpers.

GC_FRONT_MONTH is a futures series — the front-month contract changes
every ~1-2 months. The transition appears as a large overnight gap
between consecutive H1 candles that does NOT represent ordinary market
movement. These "rolls" must be detected and excluded from learning
by default (preserved for audit — never silently deleted).

Outcome window validity: Friday 20:30 + 4h must NOT silently use
Sunday/Monday pricing. We use active-market elapsed time as the
check — if the actual elapsed time between state timestamp and the
last candle in the forward window exceeds expected * max_elapsed_multiple,
the outcome is marked horizon_valid=False.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.market import Candle
from app.services.historical.validator import _classify_gap  # reuse for market-closure detection


def detect_roll_between(
    prev_candle: Candle | None,
    next_candle: Candle | None,
    atr_val: float | None,
    *,
    roll_atr_multiple: float = 5.0,
) -> tuple[bool, float | None, str | None]:
    """Detect a possible contract roll between two consecutive candles.

    Args:
        prev_candle: the candle BEFORE the suspected roll.
        next_candle: the candle AFTER the suspected roll.
        atr_val: ATR at prev_candle's timestamp (for normalization).
        roll_atr_multiple: gap must exceed this multiple of ATR.

    Returns:
        (possible_roll, roll_gap_size, roll_detection_reason).
    """
    if prev_candle is None or next_candle is None:
        return False, None, None
    # Gap = |next_candle.open - prev_candle.close| (the overnight gap)
    gap = abs(next_candle.open - prev_candle.close)
    if atr_val is None or atr_val <= 0:
        # No ATR — use a fixed threshold of 1% of price.
        threshold = 0.01 * prev_candle.close
        if gap > threshold:
            return True, round(gap, 4), f"gap {gap:.2f} > 1% of price (no ATR available)"
        return False, None, None
    # Normalized gap = gap / ATR
    normalized = gap / atr_val
    if normalized > roll_atr_multiple:
        return True, round(gap, 4), (
            f"gap {gap:.2f} = {normalized:.1f}x ATR (>{roll_atr_multiple}x threshold) — "
            "likely front-month contract roll, NOT ordinary market movement"
        )
    return False, None, None


def outcome_window_valid(
    state_ts: datetime,
    forward_window: list[Candle],
    *,
    horizon_minutes: int,
    max_elapsed_multiple: float = 2.0,
) -> tuple[bool, float | None, str | None]:
    """Validate that the forward outcome window contains sufficient
    valid market observations.

    A 4-hour horizon starting Friday 20:30 UTC must NOT silently use
    Sunday/Monday pricing — that's a 60+ hour elapsed time vs the
    expected 4 hours.

    Args:
        state_ts: state timestamp T (UTC).
        forward_window: list of forward candles covering the horizon.
        horizon_minutes: expected horizon in minutes.
        max_elapsed_multiple: actual_elapsed / expected_elapsed must be
            below this multiple for the window to be valid.

    Returns:
        (valid, actual_elapsed_seconds, invalid_reason).
    """
    if not forward_window:
        return False, None, "no forward data"
    ts_utc = state_ts if state_ts.tzinfo else state_ts.replace(tzinfo=timezone.utc)
    last_candle_ts = forward_window[-1].timestamp
    if last_candle_ts.tzinfo is None:
        last_candle_ts = last_candle_ts.replace(tzinfo=timezone.utc)
    actual_elapsed = (last_candle_ts - ts_utc).total_seconds()
    expected_elapsed = horizon_minutes * 60
    if actual_elapsed > expected_elapsed * max_elapsed_multiple:
        # Identify the closure reason
        # Walk the forward window + detect the closure type at the first
        # significant gap (>= 2x expected_interval).
        # For simplicity, just describe the closure as "market closure"
        # (more detailed classification would need the validator's
        # gap classifier — which expects full candle lists).
        invalid_reason = (
            f"actual elapsed {actual_elapsed/3600:.1f}h exceeds expected "
            f"{expected_elapsed/3600:.1f}h by >{max_elapsed_multiple}x — "
            "likely weekend/holiday/maintenance closure"
        )
        return False, round(actual_elapsed, 2), invalid_reason
    return True, round(actual_elapsed, 2), None
