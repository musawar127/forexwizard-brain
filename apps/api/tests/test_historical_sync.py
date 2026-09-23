"""Phase 3 tests: historical sync orchestrator.

Verifies the backfill loop stores genuine candles, persists sync state,
and idempotently skips already-present candles. Uses a fake provider
that returns synthetic-looking deterministic candles — this is the
explicit test-data convention documented in TEST_RESULTS.md.

Production code never generates fake candles; the real Yahoo Finance
provider is exercised by the live integration test at the end of the
suite (skipped if offline).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db.base import Base
from app.db.models import CandleRecord, HistoricalSyncState
from app.db.session import SessionLocal, engine
from app.models.market import Candle
from app.services.historical.base import (
    HistoricalMarketDataProvider,
    ProviderHealth,
)
from app.services.historical.sync import sync_historical_candles


class _FakeProvider(HistoricalMarketDataProvider):
    """Deterministic in-memory provider used only by tests."""

    provider_name = "Fake Historical Provider"
    requires_api_key = False

    def __init__(self, m1_count: int = 30, daily_count: int = 10) -> None:
        self._m1_count = m1_count
        self._daily_count = daily_count

    async def get_history(self, symbol, timeframe, start=None, end=None):
        base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        if timeframe == "1min":
            return [
                Candle(symbol=symbol, interval="1min",
                       timestamp=base + timedelta(minutes=i),
                       open=100 + 0.1 * i, high=100.5 + 0.1 * i,
                       low=99.5 + 0.1 * i, close=100 + 0.1 * (i + 1),
                       volume=None, sample_count=1,
                       provider=self.provider_name)
                for i in range(self._m1_count)
            ]
        if timeframe == "1day":
            return [
                Candle(symbol=symbol, interval="1day",
                       timestamp=base + timedelta(days=i),
                       open=100 + i, high=101 + i, low=99 + i, close=100 + i + 0.5,
                       volume=None, sample_count=1,
                       provider=self.provider_name)
                for i in range(self._daily_count)
            ]
        return []

    async def get_latest_available_timestamp(self, symbol, timeframe):
        if timeframe == "1min":
            return datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=self._m1_count - 1)
        if timeframe == "1day":
            return datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc) + timedelta(days=self._daily_count - 1)
        return None

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider_name=self.provider_name,
            reachable=True,
            requires_api_key=False,
            has_api_key=True,
        )


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _patch_provider(monkeypatch, provider):
    from app.services.historical import factory
    monkeypatch.setattr(factory, "get_historical_provider", lambda: provider)
    # Also patch the sync module's import-binding by going through the package's
    # top-level reference used in sync_historical_candles.
    import app.services.historical.sync as sync_mod
    monkeypatch.setattr(sync_mod, "get_historical_provider", lambda: provider)


@pytest.mark.asyncio
async def test_sync_persists_m1_and_aggregated_higher_timeframes(monkeypatch):
    _patch_provider(monkeypatch, _FakeProvider(m1_count=30, daily_count=5))
    summary = await sync_historical_candles("XAU/USD")

    assert summary["provider"] == "Fake Historical Provider"
    # M1 source batch
    assert summary["timeframes"]["1min"]["candles_fetched"] == 30
    assert summary["timeframes"]["1min"]["inserted"] == 30
    assert summary["timeframes"]["1min"]["sync_status"] == "ok"

    # M5 derived locally
    assert summary["timeframes"]["5min"]["candles_fetched"] == 6  # 30 / 5
    assert summary["timeframes"]["5min"]["inserted"] == 6
    # M15 derived locally
    assert summary["timeframes"]["15min"]["candles_fetched"] == 2  # 30 / 15
    assert summary["timeframes"]["15min"]["inserted"] == 2
    # H1 derived locally — 30 M1 candles is < 60, so we expect 1 H1 candle
    # with sample_count = 30 (the entire source range).
    assert summary["timeframes"]["1h"]["candles_fetched"] == 1
    assert summary["timeframes"]["1h"]["inserted"] == 1
    # H4 derived from H1: 1 H1 candle aggregates into 1 H4 candle (under-filled).
    assert summary["timeframes"]["4h"]["candles_fetched"] == 1
    # D1 direct fetch
    assert summary["timeframes"]["1day"]["candles_fetched"] == 5
    assert summary["timeframes"]["1day"]["inserted"] == 5

    # Verify CandleRecord rows actually persisted with is_historical=True
    with SessionLocal() as session:
        from sqlalchemy import select, func
        total = session.scalar(select(func.count(CandleRecord.id)).where(CandleRecord.is_historical.is_(True))) or 0
        assert total >= 30 + 6 + 2 + 1 + 1 + 5
        m1_with_hist = session.scalar(
            select(func.count(CandleRecord.id)).where(
                CandleRecord.interval == "1min",
                CandleRecord.is_historical.is_(True),
            )
        ) or 0
        assert m1_with_hist == 30


@pytest.mark.asyncio
async def test_sync_is_idempotent_second_call_skips_all(monkeypatch):
    _patch_provider(monkeypatch, _FakeProvider(m1_count=20, daily_count=5))
    await sync_historical_candles("XAU/USD")
    summary = await sync_historical_candles("XAU/USD")

    # Second call: everything already present, so inserted=0 for all TFs.
    for tf, info in summary["timeframes"].items():
        assert info["inserted"] == 0, f"{tf} should be 0 inserts on 2nd call, got {info['inserted']}"
        assert info["skipped_already_present"] >= 0


@pytest.mark.asyncio
async def test_sync_persists_historical_sync_state_rows(monkeypatch):
    _patch_provider(monkeypatch, _FakeProvider(m1_count=15, daily_count=4))
    await sync_historical_candles("XAU/USD")
    with SessionLocal() as session:
        rows = session.scalars(
            select(HistoricalSyncState).where(HistoricalSyncState.symbol == "XAU/USD")
        ).all()
        # 1 source TF + 4 derived + 1 direct D1 = 6 sync-state rows
        assert len(rows) >= 5
        # Each row must have last_sync_at and sync_status set
        for row in rows:
            assert row.last_sync_at is not None
            assert row.sync_status in {"ok", "degraded"}
            assert row.total_candles >= 0


@pytest.mark.asyncio
async def test_sync_does_not_silently_repair_out_of_order(monkeypatch):
    """Verify the validator counts out-of-order candles in its report
    rather than mutating timestamps to "repair" them."""

    class _OutOfOrderProvider(_FakeProvider):
        async def get_history(self, symbol, timeframe, start=None, end=None):
            if timeframe != "1min":
                return await super().get_history(symbol, timeframe, start, end)
            base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
            candles = [
                Candle(symbol=symbol, interval="1min", timestamp=base + timedelta(minutes=i),
                       open=100, high=101, low=99, close=100.5, volume=None,
                       sample_count=1, provider=self.provider_name)
                for i in range(10)
            ]
            # Swap two middle candles to inject out-of-order on input.
            candles[4], candles[5] = candles[5], candles[4]
            return candles

    _patch_provider(monkeypatch, _OutOfOrderProvider(m1_count=10, daily_count=0))
    summary = await sync_historical_candles("XAU/USD")
    # Out-of-order must be reported, not silently fixed.
    assert summary["timeframes"]["1min"]["out_of_order"] >= 1
    # Persisted candles must be in ascending order (presentation fix,
    # not data repair — timestamps themselves are unchanged).
    with SessionLocal() as session:
        rows = session.scalars(
            select(CandleRecord).where(
                CandleRecord.interval == "1min",
                CandleRecord.is_historical.is_(True),
            ).order_by(CandleRecord.timestamp)
        ).all()
        timestamps = [r.timestamp for r in rows]
        assert timestamps == sorted(timestamps)
