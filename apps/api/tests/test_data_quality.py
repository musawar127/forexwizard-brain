"""Phase 3 tests: data-quality summary builder.

Seeds the DB with synthetic historical candles + sync states, then
verifies the data_quality_summary / timeframe_breakdown payloads match
the persisted state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

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


def _seed_m1(n: int = 100) -> None:
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        for i in range(n):
            ts = base + timedelta(minutes=i)
            session.add(CandleRecord(
                symbol="XAU/USD", interval="1min", timestamp=ts,
                open=100.0 + 0.01 * i, high=100.5 + 0.01 * i,
                low=99.5 + 0.01 * i, close=100.0 + 0.01 * (i + 1),
                volume=None, sample_count=1,
                provider="Yahoo Finance (GC=F)",
                received_at=now_naive,
                is_historical=True,
            ))
        session.commit()


def _seed_sync_state(provider: str, interval: str, count: int) -> None:
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(HistoricalSyncState(
            provider=provider, symbol="XAU/USD", interval=interval,
            earliest_timestamp=base,
            latest_timestamp=base + timedelta(minutes=count - 1) if interval == "1min" else base + timedelta(days=count - 1),
            last_sync_at=now_naive,
            total_candles=count,
            sync_status="ok",
            last_error=None,
        ))
        session.commit()


@pytest.mark.asyncio
async def test_data_quality_summary_returns_zero_state_when_empty():
    summary = await data_quality_summary("XAU/USD")
    assert summary["symbol"] == "XAU/USD"
    assert summary["historical"]["total_candles"] == 0
    assert summary["historical"]["earliest_timestamp"] is None
    assert summary["historical"]["latest_timestamp"] is None
    # by_interval is always populated for all canonical intervals
    assert len(summary["by_interval"]) >= 7
    # Database health check is always returned
    assert "database_health" in summary
    assert summary["database_health"]["ok"] is True


@pytest.mark.asyncio
async def test_data_quality_summary_reflects_seeded_candles():
    _seed_m1(100)
    _seed_sync_state("Yahoo Finance (GC=F)", "1min", 100)

    summary = await data_quality_summary("XAU/USD")
    assert summary["historical"]["total_candles"] >= 100
    assert summary["historical"]["earliest_timestamp"] is not None
    assert summary["historical"]["latest_timestamp"] is not None

    # by_interval must contain the 1min row with candle_count == 100
    m1_entry = next((d for d in summary["by_interval"] if d["interval"] == "1min"), None)
    assert m1_entry is not None
    assert m1_entry["candle_count"] == 100
    assert "Yahoo Finance (GC=F)" in m1_entry["providers"]
    assert m1_entry["integrity_status"] in {"OK", "DEGRADED"}


@pytest.mark.asyncio
async def test_timeframe_breakdown_lists_all_canonical_intervals():
    _seed_m1(50)
    _seed_sync_state("Yahoo Finance (GC=F)", "1min", 50)
    rows = timeframe_breakdown("XAU/USD")
    intervals = {r["interval"] for r in rows}
    assert "1min" in intervals
    assert "5min" in intervals
    assert "1day" in intervals
    # 1min row must have 50 candles, 0 duplicates, OK status
    m1 = next(r for r in rows if r["interval"] == "1min")
    assert m1["candle_count"] == 50
    assert m1["duplicate_count"] == 0  # unique constraint prevents dupes
    assert m1["integrity_status"] == "OK"


@pytest.mark.asyncio
async def test_data_quality_summary_reports_provider_health():
    summary = await data_quality_summary("XAU/USD")
    assert "providers" in summary
    # Yahoo is always listed (no key needed) — health_check should report reachable
    yahoo = next((p for p in summary["providers"] if "Yahoo" in p["provider_name"]), None)
    assert yahoo is not None
    assert yahoo["requires_api_key"] is False
    # Twelve Data should be listed too; has_api_key reflects env config (False here)
    twelve = next((p for p in summary["providers"] if "Twelve" in p["provider_name"]), None)
    assert twelve is not None
    assert twelve["requires_api_key"] is True
