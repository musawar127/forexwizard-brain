"""Phase 3 tests: deterministic higher-timeframe aggregation.

Synthetic data only inside tests, per project convention.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market import Candle
from app.services.historical.aggregator import aggregate_candles


def _mk_m1(ts: datetime, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol="XAU/USD", interval="1min", timestamp=ts,
        open=o, high=h, low=l, close=c,
        volume=None, sample_count=1, provider="Yahoo Finance (GC=F)",
    )


def test_aggregate_empty_returns_empty():
    assert aggregate_candles([], "5min") == []


def test_aggregate_returns_empty_for_non_multiple_target():
    # Cannot aggregate 1min -> 4h directly (240 is a multiple of 60, so this
    # actually IS supported; test the unsupported case with a fake interval).
    from app.services.historical.aggregator import _can_aggregate
    # We rely on INTERVALS only knowing canonical TFs, so anything else fails.
    assert not _can_aggregate("1min", "7min")


def test_aggregate_m1_to_m5_basic():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    # 5 M1 candles: open=100, highs up to 104, lows down to 99, closes drift up
    m1 = [
        _mk_m1(base + timedelta(minutes=0), 100.0, 101.0, 99.0, 100.5),
        _mk_m1(base + timedelta(minutes=1), 100.5, 102.0, 100.0, 101.5),
        _mk_m1(base + timedelta(minutes=2), 101.5, 103.0, 101.0, 102.0),
        _mk_m1(base + timedelta(minutes=3), 102.0, 104.0, 101.5, 103.0),
        _mk_m1(base + timedelta(minutes=4), 103.0, 103.5, 102.5, 103.5),
    ]
    m5 = aggregate_candles(m1, "5min", symbol="XAU/USD", source_interval="1min")
    assert len(m5) == 1
    candle = m5[0]
    assert candle.interval == "5min"
    assert candle.timestamp == base  # floored to the 5min bucket start
    assert candle.open == 100.0  # first M1 open
    assert candle.high == 104.0  # max of all highs
    assert candle.low == 99.0  # min of all lows
    assert candle.close == 103.5  # last M1 close
    assert candle.sample_count == 5
    assert "aggregated to 5min" in candle.provider


def test_aggregate_m1_to_m5_two_buckets():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    # 10 M1 candles spanning two 5min buckets (0-4 and 5-9)
    m1 = [
        _mk_m1(base + timedelta(minutes=i), 100.0 + i, 100.5 + i, 99.5 + i, 100.0 + i)
        for i in range(10)
    ]
    m5 = aggregate_candles(m1, "5min", symbol="XAU/USD", source_interval="1min")
    assert len(m5) == 2
    # First bucket: open=100, high=104.5, low=99.5, close=104
    assert m5[0].open == 100.0
    assert m5[0].high == 104.5
    assert m5[0].low == 99.5
    assert m5[0].close == 104.0
    # Second bucket: open=105, high=109.5, low=104.5, close=109
    assert m5[1].open == 105.0
    assert m5[1].high == 109.5
    assert m5[1].low == 104.5
    assert m5[1].close == 109.0


def test_aggregate_m1_to_h1_one_hour_bucket():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    # 60 M1 candles in one hour, all rising
    m1 = [
        _mk_m1(base + timedelta(minutes=i), 100.0 + 0.1 * i, 100.5 + 0.1 * i, 99.5 + 0.1 * i, 100.0 + 0.1 * (i + 1))
        for i in range(60)
    ]
    h1 = aggregate_candles(m1, "1h", symbol="XAU/USD", source_interval="1min")
    assert len(h1) == 1
    assert h1[0].open == 100.0
    assert h1[0].close == pytest.approx(106.0, rel=1e-3)
    assert h1[0].sample_count == 60


def test_aggregate_volume_summed_when_present():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    m1 = [
        Candle(symbol="XAU/USD", interval="1min", timestamp=base + timedelta(minutes=i),
               open=100, high=101, low=99, close=100.5, volume=10 + i, sample_count=1,
               provider="Yahoo Finance (GC=F)")
        for i in range(5)
    ]
    m5 = aggregate_candles(m1, "5min", symbol="XAU/USD", source_interval="1min")
    assert len(m5) == 1
    assert m5[0].volume == 10 + 11 + 12 + 13 + 14  # = 60


def test_aggregate_deterministic_same_input_same_output():
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    m1 = [
        _mk_m1(base + timedelta(minutes=i), 100 + i, 101 + i, 99 + i, 100 + i + 0.5)
        for i in range(10)
    ]
    out1 = aggregate_candles(m1, "5min", symbol="XAU/USD", source_interval="1min")
    out2 = aggregate_candles(m1, "5min", symbol="XAU/USD", source_interval="1min")
    assert len(out1) == len(out2)
    for a, b in zip(out1, out2):
        assert a.timestamp == b.timestamp
        assert a.open == b.open
        assert a.high == b.high
        assert a.low == b.low
        assert a.close == b.close
        assert a.sample_count == b.sample_count


def test_aggregate_chain_m1_m5_m15_h1():
    """Verify chained aggregation: M1 -> M5 -> M15 -> H1 produces consistent
    results vs direct M1 -> H1 aggregation."""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    # 60 M1 candles in one hour
    m1 = [
        _mk_m1(base + timedelta(minutes=i), 100 + 0.1 * i, 100.5 + 0.1 * i, 99.5 + 0.1 * i, 100 + 0.1 * (i + 1))
        for i in range(60)
    ]
    # Direct: M1 -> H1
    direct_h1 = aggregate_candles(m1, "1h", symbol="XAU/USD", source_interval="1min")
    # Chain: M1 -> M5 -> M15 -> H1
    m5 = aggregate_candles(m1, "5min", symbol="XAU/USD", source_interval="1min")
    m15 = aggregate_candles(m5, "15min", symbol="XAU/USD", source_interval="5min",
                           provider_label="Yahoo Finance (GC=F) (aggregated to 5min) (aggregated to 15min)")
    chained_h1 = aggregate_candles(m15, "1h", symbol="XAU/USD", source_interval="15min",
                                   provider_label="chained")
    assert len(direct_h1) == 1
    assert len(chained_h1) == 1
    # OHLC must match regardless of aggregation path
    assert direct_h1[0].open == chained_h1[0].open
    assert direct_h1[0].high == chained_h1[0].high
    assert direct_h1[0].low == chained_h1[0].low
    assert direct_h1[0].close == chained_h1[0].close


def test_aggregate_h1_to_h4_yahoo_compatible():
    """Yahoo doesn't serve H4; H4 is derived locally from H1. Verify the
    aggregation produces 1 H4 candle per 4 H1 candles."""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    h1 = [
        Candle(symbol="XAU/USD", interval="1h", timestamp=base + timedelta(hours=i),
               open=100 + i, high=101 + i, low=99 + i, close=100 + i + 0.5,
               volume=None, sample_count=1, provider="Yahoo Finance (GC=F)")
        for i in range(8)
    ]
    h4 = aggregate_candles(h1, "4h", symbol="XAU/USD", source_interval="1h")
    assert len(h4) == 2
    assert h4[0].open == 100  # first H1 open
    assert h4[0].close == 103.5  # 4th H1 close
    assert h4[0].sample_count == 4
    assert h4[1].open == 104
    assert h4[1].close == 107.5
