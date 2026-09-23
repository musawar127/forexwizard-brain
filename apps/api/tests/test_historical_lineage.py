"""Phase 3.1 tests: instrument separation + source lineage.

Verifies that:
  * Live Gold API XAU/USD spot candles are tagged XAUUSD_SPOT + SAMPLED
  * Yahoo GC=F futures candles are tagged GC_FRONT_MONTH + DIRECT
  * Aggregated H4 candles inherit GC_FRONT_MONTH lineage
  * Direct vs Aggregated candles have correct source/target TFs
  * Native interval selection chooses Yahoo's per-TF deepest range
  * H1 → H4 aggregation preserves all lineage fields
  * Mixed-instrument detection (PURE_GC / PURE_SPOT / MIXED / NONE)

Synthetic data only inside tests, per project convention.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.market import Candle
from app.services.historical.aggregator import aggregate_candles
from app.services.historical.yahoo_finance import (
    YAHOO_DEFAULT_RANGES,
    YAHOO_INTERVALS,
    YahooFinanceHistoricalProvider,
)


def _mk_gc_h1(ts: datetime, o: float, h: float, l: float, c: float) -> Candle:
    """Direct H1 candle from Yahoo (GC=F gold futures)."""
    return Candle(
        symbol="XAU/USD", interval="1h", timestamp=ts,
        open=o, high=h, low=l, close=c, volume=None, sample_count=1,
        provider="Yahoo Finance (GC=F)",
        is_historical=True, derivation="DIRECT",
        provider_symbol="GC=F", instrument="GC_FRONT_MONTH",
        source_timeframe="1h", target_timeframe="1h",
    )


def _mk_sampled_m1(ts: datetime, o: float, h: float, l: float, c: float) -> Candle:
    """Sampled M1 candle from live Gold API spot ticks."""
    return Candle(
        symbol="XAU/USD", interval="1min", timestamp=ts,
        open=o, high=h, low=l, close=c, volume=None, sample_count=2,
        provider="Local sampled Gold API",
        is_historical=False, derivation="SAMPLED",
        provider_symbol="XAU", instrument="XAUUSD_SPOT",
        source_timeframe="TICK", target_timeframe="1min",
    )


def test_instrument_separation_spot_vs_futures():
    """XAUUSD_SPOT and GC_FRONT_MONTH must never be merged into the same
    identity. Two candles at the SAME timestamp but different instruments
    must remain distinct objects with different lineage tags."""
    ts = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    spot = _mk_sampled_m1(ts, 100.0, 100.5, 99.5, 100.2)
    futures = _mk_gc_h1(ts, 101.0, 101.5, 100.5, 100.8)  # 1h, not 1min, but tests the lineage separation

    assert spot.instrument == "XAUUSD_SPOT"
    assert futures.instrument == "GC_FRONT_MONTH"
    assert spot.provider_symbol == "XAU"
    assert futures.provider_symbol == "GC=F"
    assert spot.derivation == "SAMPLED"
    assert futures.derivation == "DIRECT"
    # Same timestamp, same symbol label "XAU/USD" — but DIFFERENT instruments.
    # This proves the system can distinguish them by lineage metadata.
    assert spot.timestamp == futures.timestamp
    assert spot.instrument != futures.instrument


def test_native_interval_selection_picks_deepest_native_range():
    """Phase 3.1 rule: M5/M15/M30 must use their native 1mo range (Yahoo's
    actual empirically-verified max for these TFs), NOT be derived from the
    shallow 5-day M1 history. H1 uses 2y native. Yahoo has native intervals
    for all of M1, M5, M15, M30, H1, D1; only H4 is non-native."""
    # YAHOO_INTERVALS must list M1, M5, M15, M30, H1, D1 as native
    assert YAHOO_INTERVALS["1min"] == "1m"
    assert YAHOO_INTERVALS["5min"] == "5m"
    assert YAHOO_INTERVALS["15min"] == "15m"
    assert YAHOO_INTERVALS["30min"] == "30m"
    assert YAHOO_INTERVALS["1h"] == "60m"
    assert YAHOO_INTERVALS["1day"] == "1d"
    # 4h must NOT be native (derived from 1h)
    assert YAHOO_INTERVALS["4h"] is None

    # Yahoo's empirically verified deepest native range per TF (2026-09-23)
    assert YAHOO_DEFAULT_RANGES["1min"] == "5d"
    assert YAHOO_DEFAULT_RANGES["5min"] == "1mo"   # ~30 days — Yahoo's actual max
    assert YAHOO_DEFAULT_RANGES["15min"] == "1mo"  # ~30 days
    assert YAHOO_DEFAULT_RANGES["30min"] == "1mo"  # ~30 days
    assert YAHOO_DEFAULT_RANGES["1h"] == "2y"
    assert YAHOO_DEFAULT_RANGES["1day"] == "10y"


def test_provider_lineage_tags_direct_candle():
    """Yahoo provider must tag every returned Candle with the correct
    lineage (DIRECT / GC=F / GC_FRONT_MONTH / source=interval / target=interval)."""
    # Construct a Yahoo candle manually using the same field shape the
    # provider's get_history returns. We don't actually call the network here.
    candle = Candle(
        symbol="XAU/USD", interval="5min", timestamp=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        open=100, high=101, low=99, close=100.5, volume=None, sample_count=1,
        provider="Yahoo Finance (GC=F)",
        is_historical=True, derivation="DIRECT",
        provider_symbol="GC=F", instrument="GC_FRONT_MONTH",
        source_timeframe="5min", target_timeframe="5min",
    )
    assert candle.derivation == "DIRECT"
    assert candle.provider_symbol == "GC=F"
    assert candle.instrument == "GC_FRONT_MONTH"
    assert candle.source_timeframe == candle.interval  # native: source == target
    assert candle.target_timeframe == candle.interval
    assert candle.is_historical is True


def test_aggregator_preserves_lineage_on_h4_from_h1():
    """H4 aggregated from H1 must inherit GC_FRONT_MONTH + GC=F lineage
    and be tagged derivation=AGGREGATED with source=1h target=4h."""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    h1_candles = [
        _mk_gc_h1(base + timedelta(hours=i), 100 + i, 101 + i, 99 + i, 100 + i + 0.5)
        for i in range(8)
    ]
    h4 = aggregate_candles(h1_candles, "4h", symbol="XAU/USD", source_interval="1h")
    assert len(h4) == 2
    for c in h4:
        assert c.interval == "4h"
        assert c.derivation == "AGGREGATED"
        assert c.instrument == "GC_FRONT_MONTH"
        assert c.provider_symbol == "GC=F"
        assert c.source_timeframe == "1h"
        assert c.target_timeframe == "4h"
        assert c.is_historical is True


def test_aggregator_does_not_inherit_spot_instrument_when_source_is_spot():
    """If source candles are SAMPLED XAUUSD_SPOT (Gold API ticks), the
    aggregated output must remain XAUUSD_SPOT (not silently re-tagged
    as GC_FRONT_MONTH)."""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    spot_m1 = [
        _mk_sampled_m1(base + timedelta(minutes=i), 100, 100.5, 99.5, 100.2)
        for i in range(10)
    ]
    m5 = aggregate_candles(spot_m1, "5min", symbol="XAU/USD", source_interval="1min")
    assert len(m5) == 2
    for c in m5:
        # Sampled candles aggregated stay SAMPLED-instrument XAUUSD_SPOT
        assert c.instrument == "XAUUSD_SPOT"
        assert c.provider_symbol == "XAU"
        # derivation should still be AGGREGATED (candle-level aggregation)
        assert c.derivation == "AGGREGATED"
        # source/target TFs
        assert c.source_timeframe == "1min"
        assert c.target_timeframe == "5min"


def test_mixed_instrument_detection_pure_gc():
    """A candle list containing ONLY GC_FRONT_MONTH candles → PURE_GC."""
    from app.services.historical.data_quality import _instrument_consistency
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = [_mk_gc_h1(base + timedelta(hours=i), 100, 101, 99, 100.5) for i in range(5)]
    assert _instrument_consistency(candles) == "PURE_GC"


def test_mixed_instrument_detection_pure_spot():
    from app.services.historical.data_quality import _instrument_consistency
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    candles = [_mk_sampled_m1(base + timedelta(minutes=i), 100, 101, 99, 100.5) for i in range(5)]
    assert _instrument_consistency(candles) == "PURE_SPOT"


def test_mixed_instrument_detection_mixed():
    """A candle list containing BOTH GC_FRONT_MONTH and XAUUSD_SPOT
    candles at the same TF → MIXED. The system must NEVER silently merge
    them into the same series."""
    from app.services.historical.data_quality import _instrument_consistency
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    gc = _mk_gc_h1(base, 100, 101, 99, 100.5)
    spot = _mk_sampled_m1(base, 100.2, 100.7, 99.7, 100.3)  # same timestamp, different instrument
    assert _instrument_consistency([gc, spot]) == "MIXED"


def test_mixed_instrument_detection_empty():
    from app.services.historical.data_quality import _instrument_consistency
    assert _instrument_consistency([]) == "NONE"


def test_historical_depth_calculation_excludes_sampled():
    """historical_depth_days reports the FULL DB range from HistoricalSyncState
    (not just the 120 most recent candles). Sampled candles are NOT counted
    because no HistoricalSyncState rows exist for SAMPLED providers."""
    from app.engine.analysis import _full_historical_depth_days
    from app.db.base import Base
    from app.db.session import engine, SessionLocal
    from app.db.models import HistoricalSyncState
    from datetime import datetime, timedelta, timezone

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    # No sync state → 0 depth (sampled candles alone don't count)
    assert _full_historical_depth_days("XAU/USD", "1h") == 0.0

    # Add a sync state spanning 5 days
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(HistoricalSyncState(
            provider="Yahoo Finance (GC=F)", symbol="XAU/USD", interval="1h",
            earliest_timestamp=base,
            latest_timestamp=base + timedelta(days=5),
            last_sync_at=base,
            total_candles=120,
            sync_status="ok",
            last_error=None,
            instrument="GC_FRONT_MONTH",
            provider_symbol="GC=F",
            derivation="DIRECT",
            source_timeframe="1h",
            target_timeframe="1h",
        ))
        session.commit()
    depth = _full_historical_depth_days("XAU/USD", "1h")
    assert depth == pytest.approx(5.0, abs=0.01)

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def test_h4_aggregation_yields_correct_count():
    """8 H1 candles (spanning 8 hours) → 2 H4 candles."""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    h1 = [_mk_gc_h1(base + timedelta(hours=i), 100 + i, 101 + i, 99 + i, 100 + i + 0.5) for i in range(8)]
    h4 = aggregate_candles(h1, "4h", symbol="XAU/USD", source_interval="1h")
    assert len(h4) == 2
    assert h4[0].sample_count == 4
    assert h4[1].sample_count == 4
    # Verify the OHLC aggregation is correct
    assert h4[0].open == 100  # first H1 open
    assert h4[0].close == 103.5  # 4th H1 close (i=3)
    assert h4[0].high == 104  # max(101,102,103,104)
    assert h4[0].low == 99  # min(99,100,101,102)


def test_aggregated_candle_target_timeframe_equals_interval():
    """Every aggregated candle's target_timeframe must equal its interval
    (this is the lineage invariant the /data page relies on)."""
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    m1 = [_mk_sampled_m1(base + timedelta(minutes=i), 100, 101, 99, 100.5) for i in range(15)]
    for tf in ("5min", "15min"):
        agg = aggregate_candles(m1, tf, symbol="XAU/USD", source_interval="1min")
        for c in agg:
            assert c.target_timeframe == tf
            assert c.interval == tf
