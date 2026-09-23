"""Phase 3 tests: OHLC validation + deduplication.

Synthetic test data is used ONLY inside these tests, mirroring the
project convention documented in TEST_RESULTS.md. Production code never
generates fake candles.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market import Candle
from app.services.historical.validator import (
    find_duplicates,
    find_gaps,
    validate_candles,
)


def _mk(ts: datetime, o: float, h: float, l: float, c: float, *, interval: str = "1min") -> Candle:
    return Candle(
        symbol="XAU/USD",
        interval=interval,
        timestamp=ts,
        open=o, high=h, low=l, close=c,
        volume=None, sample_count=1,
        provider="Yahoo Finance (GC=F)",
    )


def test_validate_clean_batch_passes():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = [_mk(base + timedelta(minutes=i), 100 + i, 100.5 + i, 99.5 + i, 100 + i) for i in range(10)]
    report = validate_candles(candles)
    assert report.total_input == 10
    assert report.duplicates == 0
    assert report.invalid_ohlc == 0
    assert report.out_of_order == 0
    assert report.zero_or_negative_price == 0
    assert len(report.valid_candles) == 10
    # Phase 3.2: integrity_status renamed OK → HEALTHY
    assert report.integrity_status == "HEALTHY"


def test_validate_rejects_high_below_low():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    bad = _mk(base, 100.0, 99.0, 100.5, 100.0)  # high=99 < low=100.5
    report = validate_candles([bad])
    assert report.invalid_ohlc == 1
    assert len(report.valid_candles) == 0
    # Phase 3.2: invalid OHLC → integrity_status = INVALID (was DEGRADED)
    assert report.integrity_status == "INVALID"


def test_validate_rejects_zero_and_negative_prices():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    zero_price = _mk(base, 0.0, 0.0, 0.0, 0.0)
    negative_price = _mk(base + timedelta(minutes=1), -1.0, -1.0, -1.0, -1.0)
    report = validate_candles([zero_price, negative_price])
    assert report.zero_or_negative_price == 2
    assert len(report.valid_candles) == 0


def test_validate_dedupes_on_symbol_interval_timestamp():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    a = _mk(base, 100.0, 101.0, 99.0, 100.5)
    b = _mk(base, 200.0, 201.0, 199.0, 200.5)  # same timestamp — duplicate
    c = _mk(base + timedelta(minutes=1), 105.0, 106.0, 104.0, 105.5)
    report = validate_candles([a, b, c])
    assert report.duplicates == 1
    assert len(report.valid_candles) == 2
    # First occurrence wins
    assert report.valid_candles[0].open == 100.0


def test_validate_reports_out_of_order():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    a = _mk(base, 100.0, 101.0, 99.0, 100.5)
    c = _mk(base + timedelta(minutes=2), 110.0, 111.0, 109.0, 110.5)
    b = _mk(base + timedelta(minutes=1), 105.0, 106.0, 104.0, 105.5)
    report = validate_candles([a, c, b])  # out of order on input
    # Two transitions where the next ts < previous ts:
    # a -> c (forward), c -> b (backward, +1 out-of-order)
    assert report.out_of_order == 1
    # Surviving candles are sorted ascending
    ts_list = [c.timestamp for c in report.valid_candles]
    assert ts_list == sorted(ts_list)


def test_find_duplicates_returns_groups():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    a = _mk(base, 100.0, 101.0, 99.0, 100.5)
    b = _mk(base, 200.0, 201.0, 199.0, 200.5)
    c = _mk(base + timedelta(minutes=1), 105.0, 106.0, 104.0, 105.5)
    d = _mk(base, 300.0, 301.0, 299.0, 300.5)  # 3rd duplicate of base
    groups = find_duplicates([a, b, c, d])
    assert len(groups) == 1
    key, count = groups[0]
    assert count == 3
    assert "XAU/USD|1min|" in key


def test_find_gaps_no_gaps_in_continuous_series():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = [_mk(base + timedelta(minutes=i), 100, 101, 99, 100) for i in range(10)]
    report = find_gaps(candles, "1min")
    assert report.actual_count == 10
    assert report.missing_periods == 0
    assert report.completeness_pct == 100.0


def test_find_gaps_reports_missing_periods():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    # 5 candles at minutes 0,1,2,3,4 then jump to 7 (missing 5,6)
    candles = (
        [_mk(base + timedelta(minutes=i), 100, 101, 99, 100) for i in range(5)]
        + [_mk(base + timedelta(minutes=7), 100, 101, 99, 100)]
    )
    report = find_gaps(candles, "1min")
    assert report.actual_count == 6
    assert report.missing_periods == 2
    assert report.completeness_pct < 100.0


def test_find_gaps_empty_returns_zero():
    report = find_gaps([], "1min")
    assert report.actual_count == 0
    assert report.expected_periods == 0
    assert report.missing_periods == 0


def test_integrity_status_empty():
    report = validate_candles([])
    assert report.integrity_status == "EMPTY"
    assert len(report.valid_candles) == 0
