from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.config import settings
from app.db.base import Base
from app.db.migrations import run_startup_migrations
from app.db.session import engine
from app.engine.candles import INTERVALS, candle_counts, get_candles
from app.engine.predictions import performance_summary, recent_predictions
from app.models.market import AskRequest, AskResponse
from app.services.brain_chat import answer_question
from app.services.historical import (
    data_quality_summary,
    sync_historical_candles,
    timeframe_breakdown,
)
from app.services.market_state import collector_loop, refresh_quote_once, state
from app.services.redis_health import redis_status
from app.services.research import recent_research


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 3: run idempotent startup migrations (new tables + new columns)
    # before the live market-data collector starts so the schema is consistent.
    try:
        run_startup_migrations()
    except Exception as exc:
        state.last_error = f"Database migration failed: {exc}"

    stop_event = asyncio.Event()
    task = asyncio.create_task(collector_loop(stop_event))
    yield
    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=5)
    except asyncio.TimeoutError:
        task.cancel()
    engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.3.0",
    description="No-broker-login XAU/USD market intelligence starter with persistent local memory and Phase 3 historical market-memory layer.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def snapshot_payload() -> dict:
    quote = state.quote_with_freshness()
    return {
        "quote": quote.model_dump(mode="json") if quote else None,
        "source_status": state.source_status,
        "last_error": state.last_error,
        "research_status": state.research_status,
        "research_error": state.research_error,
        "last_research_at": state.last_research_at.isoformat() if state.last_research_at else None,
        "brain": state.analysis.model_dump(mode="json") if state.analysis else None,
    }


@app.get("/")
async def root():
    return {"name": settings.app_name, "version": "0.2.0", "docs": "/docs"}


@app.get("/health")
async def health():
    quote = state.quote_with_freshness()
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc),
        "market_provider": state.source_status,
        "research": state.research_status,
        "redis": await redis_status(),
        "quote_status": quote.status if quote else "NO_DATA",
        "last_error": state.last_error,
    }


@app.get("/api/market/xauusd")
async def market_snapshot():
    return snapshot_payload()


@app.post("/api/market/refresh")
async def force_market_refresh():
    await refresh_quote_once()
    return snapshot_payload()


@app.get("/api/market/candles")
async def candles(interval: str = Query("1min"), limit: int = Query(120, ge=10, le=1000)):
    if interval not in INTERVALS:
        raise HTTPException(status_code=400, detail=f"Unsupported interval. Use one of: {', '.join(INTERVALS)}")
    items = await get_candles(interval, limit)
    return {
        "interval": interval,
        "count": len(items),
        "candles": [x.model_dump(mode="json") for x in items],
        "note": "Local sampled candles are aggregated from periodic spot-price observations unless an optional historical provider seeded them.",
    }


@app.get("/api/market/readiness")
async def readiness():
    return {"counts": await candle_counts(), "analysis_min_candles": settings.analysis_min_candles}


@app.get("/api/brain")
async def brain():
    return state.analysis.model_dump(mode="json") if state.analysis else None


@app.post("/api/brain/ask", response_model=AskResponse)
async def ask_brain(payload: AskRequest):
    return await answer_question(payload.question, state.analysis)


@app.get("/api/research")
async def research(limit: int = Query(30, ge=1, le=100)):
    return {
        "status": state.research_status,
        "last_research_at": state.last_research_at,
        "error": state.research_error,
        "items": await recent_research(limit),
    }


@app.get("/api/memory/predictions")
async def prediction_memory(limit: int = Query(50, ge=1, le=500)):
    return {"items": await recent_predictions(limit)}


@app.get("/api/performance")
async def performance():
    return await performance_summary()


@app.get("/api/system/status")
async def system_status():
    quote = state.quote_with_freshness()
    return {
        "brain": "ONLINE" if state.analysis else "STARTING",
        "market_feed": quote.status if quote else "NO_DATA",
        "provider": state.source_status,
        "research": state.research_status,
        "database": "CONNECTED" if state.last_error is None or "Database" not in state.last_error else "ERROR",
        "redis": await redis_status(),
        "version": "0.3.0",
        "brain_version": state.analysis.brain_version if state.analysis else "rules-v0.1",
    }


# ---------------------------------------------------------------------------
# Phase 3: Historical market-memory endpoints
#
# These endpoints expose the genuine historical OHLC backfill layer that
# runs over the HistoricalMarketDataProvider abstraction (Yahoo Finance as
# default, Twelve Data when a key is supplied). The Brain BUY/SELL/WAIT
# engine is NOT modified to consume these statistics yet — Phase 3 only
# builds the clean historical market-memory layer.
# ---------------------------------------------------------------------------

class DataSyncRequest(BaseModel):
    """POST /api/data/sync body.

    The body is intentionally restrictive: callers cannot pick an
    arbitrary symbol or arbitrary time range. The server-side
    `historical_symbol` config (default "XAU/USD") is the only symbol
    allowed. Timeframes default to the canonical set; callers may
    restrict to a subset but cannot extend beyond it.
    """

    symbol: str | None = None
    timeframes: list[str] | None = None


@app.get("/api/data/status")
async def data_status():
    """Top-level historical data quality summary.

    Returns: active provider + health, overall earliest/latest historical
    timestamps, total historical candle count, per-interval breakdown
    (candle_count, first/last timestamp, missing_intervals,
    duplicate_count, integrity_status), and all HistoricalSyncState rows.
    """
    return await data_quality_summary(settings.historical_symbol)


@app.get("/api/data/timeframes")
async def data_timeframes():
    """Per-interval breakdown of historical candles stored in the DB."""
    return {
        "intervals": timeframe_breakdown(settings.historical_symbol),
        "supported_intervals": list(INTERVALS.keys()),
    }


@app.get("/api/data/gaps")
async def data_gaps(interval: str = Query("1day")):
    """Gap report for a single interval. Returns missing-period list +
    completeness_pct based on the actually-persisted historical candles."""
    if interval not in INTERVALS:
        raise HTTPException(status_code=400, detail=f"Unsupported interval: {interval}")
    from app.services.historical.validator import find_gaps
    candles = await get_candles(interval, 5000, settings.historical_symbol)
    historical_only = [c for c in candles if "aggregated" in c.provider or c.provider.startswith(("Yahoo", "Twelve"))]
    report = find_gaps(historical_only, interval)
    return {
        "interval": interval,
        "interval_seconds": report.interval_seconds,
        "first_timestamp": report.first_timestamp.isoformat() if report.first_timestamp else None,
        "last_timestamp": report.last_timestamp.isoformat() if report.last_timestamp else None,
        "actual_count": report.actual_count,
        "expected_periods": report.expected_periods,
        "missing_periods": report.missing_periods,
        "gaps": [t.isoformat() for t in report.gaps[:200]],  # cap payload size
        "completeness_pct": report.completeness_pct,
    }


@app.post("/api/data/sync")
async def data_sync(payload: DataSyncRequest | None = None):
    """Trigger a single-shot historical backfill.

    Safe to call repeatedly — already-present candles are skipped (dedup
    on symbol+interval+timestamp). The body is optional; without it, the
    server uses the canonical timeframe set M1, M5, M15, M30, H1, H4, D1.

    No arbitrary uncontrolled downloads: the symbol is pinned to
    `settings.historical_symbol` (default "XAU/USD"). Callers cannot
    override the symbol.
    """
    if not settings.historical_sync_enabled:
        raise HTTPException(status_code=403, detail="Historical sync disabled in backend config.")
    symbol = settings.historical_symbol  # ignore payload.symbol — never trust client
    tfs = payload.timeframes if (payload and payload.timeframes) else None
    if tfs:
        # Restrict to the canonical set; ignore anything else.
        tfs = [t for t in tfs if t in INTERVALS]
    summary = await sync_historical_candles(symbol=symbol, timeframes=tfs)
    return summary


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(snapshot_payload())
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
