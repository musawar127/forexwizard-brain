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
from app.services.learning import (
    build_states as build_learning_states,
    current_similarity,
    learning_status,
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
    # Phase 3.1: filter by is_historical flag rather than by provider-string
    # matching, so mixed-instrument batches (GC_FRONT_MONTH + XAUUSD_SPOT)
    # are correctly included in the gap analysis.
    historical_only = [c for c in candles if getattr(c, "is_historical", False)]
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


@app.get("/api/data/basis")
async def data_basis(limit: int = Query(100, ge=1, le=1000)):
    """Phase 3.1: research-only futures-vs-spot basis history.

    Returns the most recent BasisObservation rows (futures_price,
    spot_price, basis = futures - spot). When both a recent GC=F futures
    price (Yahoo) and a recent XAU/USD spot price (Gold API) exist
    within a small time window, the system stores a basis row.

    This endpoint is research-only — it does NOT feed into the Brain
    BUY/SELL/WAIT logic. Phase 4 may consume it for statistical learning.
    """
    from app.db.models import BasisObservation
    from app.db.session import SessionLocal
    from sqlalchemy import select
    with SessionLocal() as session:
        rows = session.scalars(
            select(BasisObservation).order_by(BasisObservation.timestamp.desc()).limit(limit)
        ).all()
    return {
        "count": len(rows),
        "rows": [
            {
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "futures_price": r.futures_price,
                "spot_price": r.spot_price,
                "basis": r.basis,
                "futures_provider": r.futures_provider,
                "spot_provider": r.spot_provider,
                "futures_symbol": r.futures_symbol,
                "spot_symbol": r.spot_symbol,
                "captured_at": r.captured_at.isoformat() if r.captured_at else None,
            }
            for r in rows
        ],
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


# ---------------------------------------------------------------------------
# Phase 4: Historical pattern-learning endpoints
#
# These endpoints expose the historical similarity + outcome learning engine.
# The Brain BUY/SELL/WAIT rules-v0.1 logic is UNCHANGED — historical statistics
# are informational only. probability_calibrated is always False in Phase 4.
# ---------------------------------------------------------------------------

@app.get("/api/learning/status")
async def api_learning_status():
    """Top-level learning engine status.

    Returns: total historical states built, breakdowns by instrument / base
    timeframe / horizon, recent similarity runs (audit log), and the
    feature_version + similarity_version + probability_calibrated flags.
    """
    return await learning_status()


@app.get("/api/learning/horizons")
async def api_learning_horizons():
    """Supported outcome horizons (15m / 30m / 1h / 2h / 4h / 8h / 24h)."""
    from app.services.learning.config import HORIZON_MINUTES, NEUTRAL_X_DEFAULT
    return {
        "horizons_minutes": list(HORIZON_MINUTES),
        "labels": {15: "15m", 30: "30m", 60: "1h", 120: "2h", 240: "4h", 480: "8h", 1440: "24h"},
        "neutral_x_default": NEUTRAL_X_DEFAULT,
        "probability_calibrated": False,
    }


@app.get("/api/learning/current-similarity")
async def api_learning_current_similarity(
    instrument: str = Query("GC_FRONT_MONTH"),
    horizon: int = Query(60, ge=15, le=1440),
    technical_decision: str | None = Query(None),
):
    """Find historical neighbors of the current market state for one
    instrument + horizon. Returns top-10 analogs with outcome windows,
    aggregated statistics with Wilson confidence intervals, and the
    historical_alignment classification (SUPPORTS / CONTRADICTS / NEUTRAL
    / INSUFFICIENT_DATA) — informational only, does NOT influence the
    technical decision.

    Instrument is REQUIRED to match exactly — GC_FRONT_MONTH states only
    match against GC_FRONT_MONTH candidates. Never combine with
    XAUUSD_SPOT statistics.
    """
    if instrument not in {"GC_FRONT_MONTH", "XAUUSD_SPOT"}:
        raise HTTPException(status_code=400, detail="instrument must be GC_FRONT_MONTH or XAUUSD_SPOT")
    valid_horizons = {15, 30, 60, 120, 240, 480, 1440}
    if horizon not in valid_horizons:
        raise HTTPException(status_code=400, detail=f"horizon must be one of {sorted(valid_horizons)}")
    return await current_similarity(
        instrument=instrument,
        horizon_minutes=horizon,
        technical_decision=technical_decision,
    )


@app.get("/api/learning/analogs")
async def api_learning_analogs(
    instrument: str = Query("GC_FRONT_MONTH"),
    horizon: int = Query(60, ge=15, le=1440),
    limit: int = Query(10, ge=1, le=50),
):
    """Top-N closest historical analogs. Capped at 50 to prevent
    uncontrolled large queries."""
    result = await current_similarity(instrument=instrument, horizon_minutes=horizon)
    return {
        "instrument": instrument,
        "horizon_minutes": horizon,
        "analogs": (result.get("neighbors") or [])[:limit],
        "candidate_count": result.get("candidate_count", 0),
        "sample_size": result.get("sample_size", 0),
    }


class BuildStatesRequest(BaseModel):
    """POST /api/learning/build-states body.

    Restrictive: callers cannot pick an arbitrary instrument other than
    the canonical XAU/USD futures (GC_FRONT_MONTH) or spot (XAUUSD_SPOT).
    The base timeframe is pinned to H1 (deepest intraday native history).
    """

    instrument: str = "GC_FRONT_MONTH"
    batch_limit: int = 5000
    clear_existing: bool = False


@app.post("/api/learning/build-states")
async def api_learning_build_states(payload: BuildStatesRequest | None = None):
    """Trigger a batch build of historical market states + outcomes.

    Single-shot — safe to call repeatedly (already-built states are
    skipped idempotently). Clears existing states first only if
    `clear_existing=true` is passed in the body.

    No arbitrary uncontrolled downloads: instrument is pinned to
    GC_FRONT_MONTH or XAUUSD_SPOT. batch_limit caps the build size.
    """
    p = payload or BuildStatesRequest()
    if p.instrument not in {"GC_FRONT_MONTH", "XAUUSD_SPOT"}:
        raise HTTPException(status_code=400, detail="instrument must be GC_FRONT_MONTH or XAUUSD_SPOT")
    if not (1 <= p.batch_limit <= 10000):
        raise HTTPException(status_code=400, detail="batch_limit must be 1-10000")
    return await build_learning_states(
        instrument=p.instrument,
        batch_limit=p.batch_limit,
        clear_existing=p.clear_existing,
    )


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(snapshot_payload())
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
