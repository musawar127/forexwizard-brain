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
    detect_orphaned_jobs,
    get_job,
    get_run,
    get_state,
    learning_status,
    start_build_job,
)
from app.services.forward import (
    capture_observation,
    evaluate_pending_observations,
    get_forward_health,
    get_forward_performance,
    get_forward_status,
    get_observation as get_forward_observation,
    get_observations as get_forward_observations,
    _record_heartbeat,
    startup_recovery,
)
from app.services.catchup import (
    get_catchup_status,
    get_catchup_job,
    get_system_mode,
    list_catchup_jobs,
    run_catchup,
    startup_catchup,
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

    # Phase 4.2: detect orphaned RUNNING build jobs from a previous process.
    # Mark them as INTERRUPTED so they can be resumed via the resume endpoint.
    try:
        orphans = detect_orphaned_jobs()
        if orphans:
            import logging
            logging.getLogger("forexwizard").info(
                "startup: detected %d orphaned build jobs: %s — marked as interrupted",
                len(orphans), orphans,
            )
    except Exception:
        pass  # orphan detection is best-effort

    stop_event = asyncio.Event()
    task = asyncio.create_task(collector_loop(stop_event))

    # Phase 5: forward validation capture + evaluation schedulers.
    # Capture runs every 15 minutes (aligned to M15 candle close).
    # Evaluation runs every 5 minutes (evaluates matured horizons).
    forward_task = asyncio.create_task(_forward_validation_loop(stop_event))

    # Phase 5.2: startup recovery for forward validation
    try:
        recovery_result = await startup_recovery()
        import logging
        logging.getLogger("forexwizard").info(
            "startup: forward recovery — missed_captures=%d evaluated=%d completed=%d",
            recovery_result.get("missed_captures", 0),
            recovery_result.get("evaluated", 0),
            recovery_result.get("completed", 0),
        )
    except Exception:
        pass  # startup recovery is best-effort

    # Phase 5.3: run startup catch-up (market recovery + missed captures + research)
    try:
        catchup_result = await startup_catchup()
        import logging
        logging.getLogger("forexwizard").info(
            "startup: catch-up — mode=%s offline=%ss recovered=%d missed=%d",
            catchup_result.get("status", "?"),
            catchup_result.get("offline_duration_seconds", 0),
            catchup_result.get("recovered_candles", 0),
            catchup_result.get("missed_captures", 0),
        )
    except Exception as exc:
        import logging
        logging.getLogger("forexwizard").warning("startup: catch-up failed: %s", exc)

    yield
    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=5)
    except asyncio.TimeoutError:
        task.cancel()
    try:
        await asyncio.wait_for(forward_task, timeout=5)
    except asyncio.TimeoutError:
        forward_task.cancel()
    engine.dispose()


async def _forward_validation_loop(stop_event: asyncio.Event) -> None:
    """Phase 5.2: periodic capture + evaluation of forward observations.

    Capture: every 15 minutes (aligned to M15 candle close).
    Evaluation: every 5 minutes (evaluates matured horizons).
    Heartbeat: every 60 seconds (lightweight status persistence).

    Restart-safe: pending observations remain in DB and resume automatically.
    Missed captures are logged (MISSED_FORWARD_CAPTURE) — never backfilled.
    """
    capture_interval = 900  # 15 minutes
    eval_interval = 300  # 5 minutes
    heartbeat_interval = 60  # 1 minute
    last_capture = 0.0
    last_eval = 0.0
    last_heartbeat = 0.0

    from app.services.forward import (
        capture_observation,
        evaluate_pending_observations,
        _record_heartbeat,
    )

    while not stop_event.is_set():
        try:
            now = asyncio.get_event_loop().time()
            if now - last_capture >= capture_interval:
                # Alternate between M15 and H1 captures
                await capture_observation(capture_timeframe="M15")
                await capture_observation(capture_timeframe="H1")
                last_capture = now
            if now - last_eval >= eval_interval:
                await evaluate_pending_observations()
                last_eval = now
            if now - last_heartbeat >= heartbeat_interval:
                _record_heartbeat()
                last_heartbeat = now
        except Exception:
            pass  # forward validation is best-effort — never break the live feed

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=15)
        except asyncio.TimeoutError:
            pass


app = FastAPI(
    title=settings.app_name,
    version="0.4.0",
    description="No-broker-login XAU/USD market intelligence terminal with persistent local memory, historical market-memory layer, and forward validation.",
    lifespan=lifespan,
)

# Phase 5.4: production CORS — uses CORS_ORIGINS env if set, otherwise
# falls back to frontend_origin for local development. Never uses
# unrestricted CORS in production unless explicitly configured.
_allowed_origins: list[str]
if settings.cors_origins:
    _allowed_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
else:
    _allowed_origins = [settings.frontend_origin]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
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

    Phase 4.1: this endpoint now spawns a BACKGROUND JOB and returns
    immediately with job_id + status. The HTTP request NEVER blocks.
    Poll GET /api/learning/jobs/{job_id} for progress.

    Restrictive: callers cannot pick an arbitrary instrument other than
    the canonical XAU/USD futures (GC_FRONT_MONTH) or spot (XAUUSD_SPOT).
    The base timeframe is pinned to H1 (deepest intraday native history).
    """

    instrument: str = "GC_FRONT_MONTH"
    batch_limit: int = 5000  # ignored in Phase 4.1 — full build runs in background
    clear_existing: bool = False
    resume_job_id: str | None = None


@app.post("/api/learning/build-states")
async def api_learning_build_states(payload: BuildStatesRequest | None = None):
    """Phase 4.1: trigger a background build of historical market states +
    outcomes. Returns IMMEDIATELY with job_id + status — never blocks the
    API event loop. Poll GET /api/learning/jobs/{job_id} for progress.

    Single-shot per job_id — resumable via `resume_job_id` in the body.
    Safe to call repeatedly with the same job_id (already-built states
    are skipped idempotently). Clears existing states first only if
    `clear_existing=true` is passed.

    No arbitrary uncontrolled downloads: instrument is pinned to
    GC_FRONT_MONTH or XAUUSD_SPOT.
    """
    p = payload or BuildStatesRequest()
    if p.instrument not in {"GC_FRONT_MONTH", "XAUUSD_SPOT"}:
        raise HTTPException(status_code=400, detail="instrument must be GC_FRONT_MONTH or XAUUSD_SPOT")
    result = start_build_job(
        instrument=p.instrument,
        clear_existing=p.clear_existing,
        resume_job_id=p.resume_job_id,
    )
    return result


@app.get("/api/learning/jobs/{job_id}")
async def api_learning_job_status(job_id: str):
    """Phase 4.1: poll a background build job's progress. Returns
    {job_id, status, eligible_total, built, remaining, percent_complete,
    started_at, updated_at, earliest_state, latest_state, elapsed_seconds,
    states_per_second}."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@app.post("/api/learning/jobs/{job_id}/resume")
async def api_learning_resume_job(job_id: str):
    """Phase 4.2: resume an interrupted build job. The job continues from
    its last checkpoint without duplicating existing states (idempotent
    dedup on instrument + base_timeframe + timestamp + feature_version)."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job["status"] == "running":
        return {"job_id": job_id, "status": "already_running", "message": "Job is already running."}
    if job["status"] == "completed":
        return {"job_id": job_id, "status": "completed", "message": "Job already completed."}
    if job["status"] not in ("interrupted", "failed", "paused"):
        raise HTTPException(status_code=409, detail=f"Job {job_id} is in state '{job['status']}' — cannot resume.")
    # Resume the job
    result = start_build_job(
        instrument=job["instrument"],
        resume_job_id=job_id,
    )
    return result


@app.get("/api/learning/states/{state_id}")
async def api_learning_state_inspector(state_id: int):
    """Phase 4.1: full feature snapshot + outcome availability for one
    historical state. Lets you inspect WHY an analogue matched."""
    state = get_state(state_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"State {state_id} not found")
    return state


@app.get("/api/learning/runs/{run_id}")
async def api_learning_run_inspector(run_id: str):
    """Phase 4.1: full immutable run snapshot. Given a run_id, returns
    the complete statistics that were computed at that point in time.
    Old runs are NEVER updated — new market state → new run."""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return run


# ---------------------------------------------------------------------------
# Phase 5: Forward validation and live learning audit endpoints
#
# These endpoints expose the forward validation system. Observations are
# immutable snapshots of the Brain's state. Outcomes use XAUUSD_SPOT live
# data. probability_calibrated remains FALSE. No signal modification.
# ---------------------------------------------------------------------------

@app.get("/api/forward/status")
async def api_forward_status():
    """Forward validation status: total observations, pending/partial/complete/
    invalid counts, by decision, by alignment, by horizon."""
    return get_forward_status()


@app.get("/api/forward/observations")
async def api_forward_observations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Paginated list of forward observations (most recent first)."""
    return {
        "observations": get_forward_observations(limit=limit, offset=offset),
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/forward/observations/{observation_id}")
async def api_forward_observation_detail(observation_id: str):
    """Single observation with all its outcomes (or PENDING where not yet
    evaluated). Immutable — the observation itself is never updated."""
    obs = get_forward_observation(observation_id)
    if obs is None:
        raise HTTPException(status_code=404, detail=f"Observation {observation_id} not found")
    return obs


@app.post("/api/forward/capture")
async def api_forward_capture(
    payload: dict | None = None,
):
    """Manually trigger a forward observation capture at the current market
    state. Also called automatically by the capture scheduler at M15/H1
    candle closes.

    Body: {"capture_timeframe": "15min" or "1h"} (default: "15min")
    """
    tf = "15min"
    if payload and payload.get("capture_timeframe"):
        tf = payload["capture_timeframe"]
    result = await capture_observation(capture_timeframe=tf)
    if result.get("skipped"):
        return result
    return result


@app.get("/api/forward/performance")
async def api_forward_performance(
    horizon: int = Query(60, ge=15, le=1440),
):
    """Forward performance metrics for one horizon. Groups by technical
    decision + historical alignment. Reports directional rates with
    Wilson intervals. Sample quality labeled INSUFFICIENT/EARLY/MODERATE/
    STRONGER_EVIDENCE — these describe sample size only, NOT prediction
    quality."""
    return get_forward_performance(horizon_minutes=horizon)


@app.post("/api/forward/evaluate")
async def api_forward_evaluate():
    """Manually trigger evaluation of pending forward observations. Also
    runs automatically via the evaluation scheduler."""
    return await evaluate_pending_observations()


@app.get("/api/forward/health")
async def api_forward_health():
    """Phase 5.2: forward collector + evaluator health status.
    Returns collector status (ONLINE/DEGRADED/OFFLINE), uptime, last
    captures, last evaluation, pending count, spot storage info."""
    return get_forward_health()


@app.get("/api/system/health")
async def api_system_health():
    """Phase 5.2: system-wide health summary across all subsystems.
    Does NOT pretend optional services are healthy when unavailable."""
    from app.services.market_state import state
    quote = state.quote_with_freshness()
    return {
        "market_feed": {
            "status": state.source_status,
            "quote_status": quote.status if quote else "NO_DATA",
            "last_error": state.last_error,
        },
        "database": {
            "engine": "sqlite" if settings.database_url.startswith("sqlite") else "postgresql",
            "status": "CONNECTED" if state.last_error is None or "Database" not in (state.last_error or "") else "ERROR",
        },
        "forward_collector": get_forward_health(),
        "historical_engine": {
            "feature_version": "features-v0.1",
            "states_built": None,  # query on demand via /api/learning/status
        },
        "research_service": {
            "status": state.research_status,
            "error": state.research_error,
        },
        "redis": await redis_status(),
        "brain": {
            "status": "ONLINE" if state.analysis else "STARTING",
            "decision": state.analysis.decision if state.analysis else None,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Phase 5.3: Wake and catch-up recovery endpoints
# ---------------------------------------------------------------------------

@app.get("/api/system/status")
async def api_system_status():
    """Phase 5.3: system mode + catch-up progress.
    Returns mode (STARTING/CATCHING_UP/LIVE/DEGRADED/OFFLINE),
    offline_duration_seconds, started_at, progress_percent."""
    return get_system_mode()


@app.get("/api/catchup/status")
async def api_catchup_status():
    """Phase 5.3: catch-up coordinator status with sync states."""
    return get_catchup_status()


@app.get("/api/catchup/jobs")
async def api_catchup_jobs(limit: int = Query(20, ge=1, le=100)):
    """Phase 5.3: list recent catch-up jobs."""
    return {"jobs": list_catchup_jobs(limit=limit)}


@app.get("/api/catchup/jobs/{job_id}")
async def api_catchup_job_detail(job_id: str):
    """Phase 5.3: single catch-up job detail."""
    job = get_catchup_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Catch-up job {job_id} not found")
    return job


@app.post("/api/catchup/run")
async def api_catchup_run():
    """Phase 5.3: manually trigger a catch-up run. Returns immediately
    with job_id. Single catch-up protection prevents duplicates."""
    result = await run_catchup()
    return result


@app.post("/api/catchup/jobs/{job_id}/resume")
async def api_catchup_resume(job_id: str):
    """Phase 5.3: resume an interrupted catch-up job."""
    result = await run_catchup(resume_job_id=job_id)
    return result


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(snapshot_payload())
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
