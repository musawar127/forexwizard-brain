"""Phase 3.1 tests: data-quality summary builder.

Phase 3.1: `data_quality_summary` now returns:
  - by_interval: dict keyed by interval, each value is a list of
    (instrument, derivation)-grouped entries
  - interval_quality: dict keyed by interval with gap/consistency/depth
  - sync_states: list with lineage fields (instrument, provider_symbol,
    derivation, source_timeframe, target_timeframe)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.base import Base
from app.db.models import CandleRecord, HistoricalSyncState
from app.db.session import SessionLocal, engine
from app.services.historical.data_quality import (
    data_quality_summary,
    timeframe_breakdown,
)


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _seed_historical(n: int = 100, interval: str = "1min") -> None:
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        for i in range(n):
            ts = base + timedelta(minutes=i) if interval == "1min" else base + timedelta(days=i)
            session.add(CandleRecord(
                symbol="XAU/USD", interval=interval, timestamp=ts,
                open=100.0 + 0.01 * i, high=100.5 + 0.01 * i,
                low=99.5 + 0.01 * i, close=100.0 + 0.01 * (i + 1),
                volume=None, sample_count=1,
                provider="Yahoo Finance (GC=F)",
                received_at=now_naive,
                is_historical=True,
                derivation="DIRECT",
                provider_symbol="GC=F",
                instrument="GC_FRONT_MONTH",
                source_timeframe=interval,
                target_timeframe=interval,
            ))
        session.commit()


def _seed_sync_state(provider: str, interval: str, count: int) -> None:
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(HistoricalSyncState(
            provider=provider, symbol="XAU/USD", interval=interval,
            earliest_timestamp=base,
            latest_timestamp=base + timedelta(minutes=count - 1),
            last_sync_at=now_naive,
            total_candles=count,
            sync_status="ok",
            last_error=None,
            instrument="GC_FRONT_MONTH",
            provider_symbol="GC=F",
            derivation="DIRECT",
            source_timeframe=interval,
            target_timeframe=interval,
        ))
        session.commit()


@pytest.mark.asyncio
async def test_data_quality_summary_returns_zero_state_when_empty():
    summary = await data_quality_summary("XAU/USD")
    assert summary["symbol"] == "XAU/USD"
    assert summary["historical"]["total_candles"] == 0
    assert summary["historical"]["earliest_timestamp"] is None
    assert summary["historical"]["latest_timestamp"] is None
    # by_interval is now a DICT keyed by interval (Phase 3.1)
    assert isinstance(summary["by_interval"], dict)
    # interval_quality is always populated for all canonical intervals
    assert "interval_quality" in summary
    assert "1min" in summary["interval_quality"]
    # Database health check is always returned
    assert "database_health" in summary
    assert summary["database_health"]["ok"] is True


@pytest.mark.asyncio
async def test_data_quality_summary_reflects_seeded_candles():
    _seed_historical(100, "1min")
    _seed_sync_state("Yahoo Finance (GC=F)", "1min", 100)

    summary = await data_quality_summary("XAU/USD")
    assert summary["historical"]["total_candles"] >= 100
    assert summary["historical"]["earliest_timestamp"] is not None
    assert summary["historical"]["latest_timestamp"] is not None

    # by_interval must contain the 1min row
    assert "1min" in summary["by_interval"]
    m1_entries = summary["by_interval"]["1min"]
    assert len(m1_entries) == 1  # one (instrument, derivation) group
    entry = m1_entries[0]
    assert entry["candle_count"] == 100
    assert entry["instrument"] == "GC_FRONT_MONTH"
    assert entry["derivation"] == "DIRECT"
    assert entry["provider_symbol"] == "GC=F"
    assert entry["source_timeframe"] == "1min"
    assert entry["target_timeframe"] == "1min"

    # interval_quality for 1min
    m1_q = summary["interval_quality"]["1min"]
    assert "instrument_consistency" in m1_q
    assert "historical_depth_days" in m1_q


@pytest.mark.asyncio
async def test_timeframe_breakdown_lists_per_instrument_derivation():
    """Phase 3.1: timeframe_breakdown returns one row per
    (interval, instrument, derivation) group — DIRECT and AGGREGATED
    candles at the same TF appear as separate rows."""
    _seed_historical(50, "1min")
    _seed_sync_state("Yahoo Finance (GC=F)", "1min", 50)
    rows = timeframe_breakdown("XAU/USD")
    intervals = {r["interval"] for r in rows}
    assert "1min" in intervals
    # 1min row must have DIRECT lineage
    m1 = next(r for r in rows if r["interval"] == "1min")
    assert m1["candle_count"] == 50
    assert m1["derivation"] == "DIRECT"
    assert m1["instrument"] == "GC_FRONT_MONTH"
    assert m1["provider_symbol"] == "GC=F"
    assert m1["source_timeframe"] == "1min"
    assert m1["target_timeframe"] == "1min"
    assert m1["days_covered"] is not None
    assert m1["duplicate_count"] == 0  # unique constraint prevents dupes


@pytest.mark.asyncio
async def test_data_quality_summary_reports_provider_health():
    summary = await data_quality_summary("XAU/USD")
    assert "providers" in summary
    # Yahoo is always listed (no key needed)
    yahoo = next((p for p in summary["providers"] if "Yahoo" in p["provider_name"]), None)
    assert yahoo is not None
    assert yahoo["requires_api_key"] is False
    # Twelve Data should be listed too
    twelve = next((p for p in summary["providers"] if "Twelve" in p["provider_name"]), None)
    assert twelve is not None
    assert twelve["requires_api_key"] is True
