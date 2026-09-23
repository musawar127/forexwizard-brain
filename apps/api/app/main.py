from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
from app.engine.candles import INTERVALS, candle_counts, get_candles
from app.engine.predictions import performance_summary, recent_predictions
from app.models.market import AskRequest, AskResponse
from app.services.brain_chat import answer_question
from app.services.market_state import collector_loop, refresh_quote_once, state
from app.services.redis_health import redis_status
from app.services.research import recent_research


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(engine)
    except Exception as exc:
        state.last_error = f"Database initialization failed: {exc}"

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
    version="0.2.0",
    description="No-broker-login XAU/USD market intelligence starter with persistent local memory.",
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
        "version": "0.2.0",
        "brain_version": state.analysis.brain_version if state.analysis else "rules-v0.1",
    }


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(snapshot_payload())
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
