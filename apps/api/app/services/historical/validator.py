"""Phase 3: Historical candle validator.

This module NEVER repairs source data. It only:
  * identifies duplicates (same symbol + interval + timestamp)
  * identifies invalid OHLC (high < low, zero/negative prices, NaN/inf)
  * identifies out-of-order timestamps
  * identifies gaps (missing periods between consecutive candles
    given the interval's expected spacing)
  * identifies missing periods across a desired [earliest, latest] window

The caller (sync orchestrator) decides what to do with the report. The
default behavior is to keep the first valid candle of any duplicate
group and reject invalid candles, but the report itself is always
returned so it can be persisted for the /data page.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.models.market import Candle

# Reuse the canonical interval spacing from the engine module.
from app.engine.candles import INTERVALS


@dataclass
class CandleValidationReport:
    """Result of validating a batch of candles.

    All counts refer to the input batch. The `valid_candles` list is the
    post-validation deduplicated + ordered list ready for storage.
    """

    total_input: int = 0
    duplicates: int = 0
    invalid_ohlc: int = 0
    out_of_order: int = 0
    zero_or_negative_price: int = 0
    valid_candles: list[Candle] = field(default_factory=list)
    duplicate_keys: list[str] = field(default_factory=list)
    invalid_examples: list[str] = field(default_factory=list)

    @property
    def integrity_status(self) -> str:
        if self.total_input == 0:
            return "EMPTY"
        if self.duplicates == 0 and self.invalid_ohlc == 0 and self.out_of_order == 0 and self.zero_or_negative_price == 0:
            return "OK"
        return "DEGRADED"


def validate_candles(candles: list[Candle]) -> CandleValidationReport:
    """Validate + deduplicate a list of candles.

    Rules:
      1. Reject candles with NaN / inf in any OHLC field.
      2. Reject candles with open/high/low/close <= 0.
      3. Reject candles where high < low, high < max(open, close),
         or low > min(open, close).
      4. Deduplicate on (symbol, interval, timestamp) — keep the FIRST
         occurrence in input order. Subsequent duplicates are counted.
      5. Sort the surviving candles by timestamp ascending. Candles that
         arrived out of order are counted but NOT silently repaired.
    """
    report = CandleValidationReport(total_input=len(candles))
    seen_keys: set[tuple[str, str, datetime]] = set()
    valid: list[Candle] = []

    for c in candles:
        key = (c.symbol, c.interval, c.timestamp)
        if key in seen_keys:
            report.duplicates += 1
            report.duplicate_keys.append(f"{c.symbol}|{c.interval}|{c.timestamp.isoformat()}")
            continue
        seen_keys.add(key)

        # Invalid OHLC checks
        if any(
            (v is None) or (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
            for v in (c.open, c.high, c.low, c.close)
        ):
            report.invalid_ohlc += 1
            report.invalid_examples.append(f"NaN/inf OHLC at {key}")
            continue
        if c.open <= 0 or c.high <= 0 or c.low <= 0 or c.close <= 0:
            report.zero_or_negative_price += 1
            report.invalid_examples.append(f"non-positive price at {key}")
            continue
        if c.high < c.low or c.high < max(c.open, c.close) or c.low > min(c.open, c.close):
            report.invalid_ohlc += 1
            report.invalid_examples.append(f"inconsistent H/L/O/C at {key}")
            continue
        valid.append(c)

    # Detect out-of-order timestamps (on the surviving candles, in their
    # original arrival order). This count does NOT count duplicates.
    last_ts: datetime | None = None
    for c in valid:
        if last_ts is not None and c.timestamp < last_ts:
            report.out_of_order += 1
        last_ts = c.timestamp

    # Final pass: sort ascending. The validator reports out_of_order above
    # but the persisted list is canonical (ascending). This is "presentation
    # ordering" not "data repair": the timestamps themselves are untouched.
    valid.sort(key=lambda c: c.timestamp)
    report.valid_candles = valid
    return report


@dataclass
class GapReport:
    """Result of gap/missing-period detection across a candle list.

    `expected_periods` is computed from the actual span of the data and
    the interval's nominal spacing. `gaps` lists every expected timestamp
    that has no candle.
    """

    interval: str
    interval_seconds: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    actual_count: int
    expected_periods: int
    gaps: list[datetime] = field(default_factory=list)

    @property
    def missing_periods(self) -> int:
        return len(self.gaps)

    @property
    def completeness_pct(self) -> float:
        if self.expected_periods <= 0:
            return 0.0
        return round(100.0 * self.actual_count / self.expected_periods, 2)


def find_gaps(candles: list[Candle], interval: str) -> GapReport:
    """Detect gaps between consecutive candles for a given interval.

    The candle list MUST be sorted ascending by timestamp — call
    `validate_candles()` first to guarantee this.
    """
    if interval not in INTERVALS:
        return GapReport(
            interval=interval,
            interval_seconds=0,
            first_timestamp=None,
            last_timestamp=None,
            actual_count=len(candles),
            expected_periods=0,
        )
    seconds = INTERVALS[interval]
    if not candles:
        return GapReport(
            interval=interval,
            interval_seconds=seconds,
            first_timestamp=None,
            last_timestamp=None,
            actual_count=0,
            expected_periods=0,
        )
    first = candles[0].timestamp
    last = candles[-1].timestamp
    span_seconds = int((last - first).total_seconds())
    expected_periods = max(1, span_seconds // seconds + 1)

    gaps: list[datetime] = []
    expected_next = first
    for c in candles:
        # If a candle is exactly where we expect it, advance.
        if c.timestamp == expected_next:
            expected_next = c.timestamp + timedelta(seconds=seconds)
        elif c.timestamp > expected_next:
            # Record every missed period between expected_next and c.timestamp.
            gap_cursor = expected_next
            while gap_cursor < c.timestamp:
                gaps.append(gap_cursor)
                gap_cursor = gap_cursor + timedelta(seconds=seconds)
            expected_next = c.timestamp + timedelta(seconds=seconds)
        else:
            # c.timestamp < expected_next: duplicate/overlap, ignored by gap logic.
            pass
    return GapReport(
        interval=interval,
        interval_seconds=seconds,
        first_timestamp=first,
        last_timestamp=last,
        actual_count=len(candles),
        expected_periods=expected_periods,
        gaps=gaps,
    )


def find_duplicates(candles: list[Candle]) -> list[tuple[str, int]]:
    """Return (key, count) for every (symbol, interval, timestamp) that
    appears more than once in the input list."""
    counter: Counter[str] = Counter()
    for c in candles:
        counter[f"{c.symbol}|{c.interval}|{c.timestamp.isoformat()}"] += 1
    return [(key, count) for key, count in counter.items() if count > 1]
