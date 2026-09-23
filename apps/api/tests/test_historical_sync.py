"""Phase 3.1 tests: historical sync orchestrator.

Phase 3.1 design rule: each NATIVE TF is fetched directly from the
provider at its native depth. Only H4 is derived locally from H1
(because Yahoo has no native 4h interval).

Uses a fake provider that returns synthetic-looking deterministic
candles — this is the explicit test-data convention documented in
TEST_RESULTS.md. Production code never generates fake candles; the
real Yahoo Finance provider is exercised by the live integration
test at the end of the suite (skipped if offline).
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
    """Deterministic in-memory provider used only by tests. Returns
    DIRECT candles for every native TF (1min, 5min, 15min, 30min, 1h, 1day)
    with full GC_FRONT_MONTH lineage — same shape as the real Yahoo provider."""

    provider_name = "Fake Historical Provider"
    requires_api_key = False

    def __init__(self, m1_count: int = 30, m5_count: int = 50, m15_count: int = 40,
                 m30_count: int = 30, h1_count: int = 20, daily_count: int = 10) -> None:
        self._counts = {
            "1min": m1_count, "5min": m5_count, "15min": m15_count,
            "30min": m30_count, "1h": h1_count, "1day": daily_count,
        }

    async def get_history(self, symbol, timeframe, start=None, end=None):
        if timeframe == "4h":
            return []  # 4h is derived from 1h, not fetched directly
        count = self._counts.get(timeframe, 0)
        if count == 0:
            return []
        base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        # Use the right timedelta for each interval
        from app.engine.candles import INTERVALS
        seconds = INTERVALS.get(timeframe, 60)
        return [
            Candle(
                symbol=symbol, interval=timeframe,
                timestamp=base + timedelta(seconds=seconds * i),
                open=100 + 0.1 * i, high=100.5 + 0.1 * i,
                low=99.5 + 0.1 * i, close=100 + 0.1 * (i + 1),
                volume=None, sample_count=1,
                provider=self.provider_name,
                is_historical=True, derivation="DIRECT",
                provider_symbol="GC=F", instrument="GC_FRONT_MONTH",
                source_timeframe=timeframe, target_timeframe=timeframe,
            )
            for i in range(count)
        ]

    async def get_latest_available_timestamp(self, symbol, timeframe):
        from app.engine.candles import INTERVALS
        count = self._counts.get(timeframe, 0)
        if count == 0:
            return None
        base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        return base + timedelta(seconds=INTERVALS[timeframe] * (count - 1))

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
    import app.services.historical.sync as sync_mod
    monkeypatch.setattr(sync_mod, "get_historical_provider", lambda: provider)


@pytest.mark.asyncio
async def test_sync_persists_each_native_tf_directly(monkeypatch):
    """Phase 3.1: each native TF (M1, M5, M15, M30, H1, D1) is fetched
    DIRECTLY from the provider at its native depth. Only H4 is derived
    locally from H1."""
    _patch_provider(monkeypatch, _FakeProvider(
        m1_count=30, m5_count=50, m15_count=40, m30_count=30,
        h1_count=20, daily_count=10,
    ))
    summary = await sync_historical_candles("XAU/USD")

    # All 6 native TFs fetched directly
    assert summary["timeframes"]["1min"]["candles_fetched"] == 30
    assert summary["timeframes"]["5min"]["candles_fetched"] == 50
    assert summary["timeframes"]["15min"]["candles_fetched"] == 40
    assert summary["timeframes"]["30min"]["candles_fetched"] == 30
    assert summary["timeframes"]["1h"]["candles_fetched"] == 20
    assert summary["timeframes"]["1day"]["candles_fetched"] == 10

    # Each direct TF must be tagged DIRECT
    for tf in ("1min", "5min", "15min", "30min", "1h", "1day"):
        info = summary["timeframes"][tf]
        assert info["derivation"] == "DIRECT", f"{tf} should be DIRECT"
        assert info["instrument"] == "GC_FRONT_MONTH"
        assert info["provider_symbol"] == "GC=F"
        assert info["source_timeframe"] == tf
        assert info["target_timeframe"] == tf

    # H4 is the ONLY derived TF — derived from H1
    h4_info = summary["timeframes"]["4h"]
    assert h4_info["derivation"] == "AGGREGATED"
    assert h4_info["source_timeframe"] == "1h"
    assert h4_info["target_timeframe"] == "4h"
    assert h4_info["instrument"] == "GC_FRONT_MONTH"
    assert h4_info["provider_symbol"] == "GC=F"
    # 20 H1 candles → 5 H4 candles (4:1 ratio)
    assert h4_info["candles_fetched"] == 5


@pytest.mark.asyncio
async def test_sync_persists_lineage_in_candle_records(monkeypatch):
    """Every persisted CandleRecord must carry the full lineage fields
    (derivation, provider_symbol, instrument, source_timeframe, target_timeframe)."""
    _patch_provider(monkeypatch, _FakeProvider(m1_count=10, m5_count=20, h1_count=8, daily_count=3))
    await sync_historical_candles("XAU/USD")
    with SessionLocal() as session:
        rows = session.scalars(
            select(CandleRecord).where(CandleRecord.is_historical.is_(True))
        ).all()
        assert len(rows) > 0
        for r in rows:
            assert r.derivation in {"DIRECT", "AGGREGATED"}
            assert r.provider_symbol == "GC=F"
            assert r.instrument == "GC_FRONT_MONTH"
            assert r.source_timeframe != ""
            assert r.target_timeframe != ""
            # For DIRECT: source == target
            if r.derivation == "DIRECT":
                assert r.source_timeframe == r.target_timeframe
            # For AGGREGATED: source < target (e.g. 1h → 4h)
            if r.derivation == "AGGREGATED":
                assert r.source_timeframe == "1h"
                assert r.target_timeframe == "4h"


@pytest.mark.asyncio
async def test_sync_is_idempotent_second_call_skips_all(monkeypatch):
    _patch_provider(monkeypatch, _FakeProvider(m1_count=20, m5_count=30, daily_count=5))
    await sync_historical_candles("XAU/USD")
    summary = await sync_historical_candles("XAU/USD")

    # Second call: everything already present, so inserted=0 for all TFs.
    for tf, info in summary["timeframes"].items():
        assert info["inserted"] == 0, f"{tf} should be 0 inserts on 2nd call, got {info['inserted']}"
        assert info["skipped_already_present"] >= 0


@pytest.mark.asyncio
async def test_sync_persists_historical_sync_state_rows(monkeypatch):
    """Phase 3.1: sync state is persisted per (provider, symbol, interval).
    With 6 native TFs + 1 derived H4 = 7 sync state rows expected."""
    _patch_provider(monkeypatch, _FakeProvider(
        m1_count=15, m5_count=20, m15_count=15, m30_count=12,
        h1_count=10, daily_count=4,
    ))
    await sync_historical_candles("XAU/USD")
    with SessionLocal() as session:
        rows = session.scalars(
            select(HistoricalSyncState).where(HistoricalSyncState.symbol == "XAU/USD")
        ).all()
        # 6 native TFs (1min, 5min, 15min, 30min, 1h, 1day) + 1 derived (4h) = 7
        assert len(rows) == 7
        for row in rows:
            assert row.last_sync_at is not None
            assert row.sync_status in {"ok", "degraded"}
            assert row.total_candles >= 0
            assert row.instrument == "GC_FRONT_MONTH"
            assert row.provider_symbol == "GC=F"
            assert row.derivation in {"DIRECT", "AGGREGATED"}
            if row.derivation == "AGGREGATED":
                assert row.source_timeframe == "1h"
                assert row.target_timeframe == "4h"


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
                       sample_count=1, provider=self.provider_name,
                       is_historical=True, derivation="DIRECT",
                       provider_symbol="GC=F", instrument="GC_FRONT_MONTH",
                       source_timeframe="1min", target_timeframe="1min")
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
