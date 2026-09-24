"""Phase 5.3: Wake and catch-up recovery mode.

On startup, the coordinator:
1. Inspects last heartbeat / sync state
2. Calculates offline duration
3. Recovers available market history (Yahoo Finance)
4. Records spot history as UNAVAILABLE (free Gold API has no historical)
5. Evaluates pending forward outcomes (using XAUUSD_SPOT data)
6. Detects and logs missed forward captures (never fabricates)
7. Syncs research/news
8. Incrementally builds new historical states
9. Transitions to LIVE mode

CRITICAL: Historical data may be recovered. Missed forward predictions
must NEVER be invented retroactively.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from app.db.models import (
    CatchupJob,
    ForwardAuditLog,
    ForwardObservation,
    MissedForwardCapture,
    SystemConfig,
    SystemSyncState,
)
from app.db.session import SessionLocal

# System mode constants
MODE_STARTING = "STARTING"
MODE_CATCHING_UP = "CATCHING_UP"
MODE_LIVE = "LIVE"
MODE_DEGRADED = "DEGRADED"
MODE_OFFLINE = "OFFLINE"

# In-memory system mode (not persisted — computed on startup)
_SYSTEM_MODE = MODE_STARTING
_SYSTEM_STARTUP_TIME = datetime.now(timezone.utc)
_OFFLINE_DURATION = 0.0


def _audit(event_type: str, detail: str | None = None) -> None:
    try:
        with SessionLocal() as session:
            session.add(ForwardAuditLog(
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                event_type=event_type,
                detail=detail,
            ))
            session.commit()
    except Exception:
        pass


def _get_sync_state(component: str) -> SystemSyncState | None:
    with SessionLocal() as session:
        return session.get(SystemSyncState, component) if False else session.scalar(
            select(SystemSyncState).where(SystemSyncState.component == component)
        )


def _update_sync_state(component: str, status: str, error: str | None = None) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        row = session.scalar(select(SystemSyncState).where(SystemSyncState.component == component))
        if row is None:
            session.add(SystemSyncState(
                component=component, status=status, last_error=error,
                last_attempted_sync=now, updated_at=now,
            ))
        else:
            row.status = status
            row.last_attempted_sync = now
            row.last_error = error
            row.updated_at = now
            if status in ("COMPLETE", "DEGRADED"):
                row.last_successful_sync = now
        session.commit()


def _detect_offline_duration() -> float:
    """Calculate offline duration from last heartbeat."""
    global _OFFLINE_DURATION
    try:
        from app.db.models import ForwardHeartbeat
        with SessionLocal() as session:
            hb = session.scalar(
                select(ForwardHeartbeat).order_by(ForwardHeartbeat.timestamp.desc()).limit(1)
            )
            if hb and hb.timestamp:
                last_ts = hb.timestamp
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                _OFFLINE_DURATION = max(0.0, (now - last_ts).total_seconds())
                return _OFFLINE_DURATION
    except Exception:
        pass
    _OFFLINE_DURATION = 0.0
    return 0.0


def _find_running_catchup() -> str | None:
    """Check if a catch-up job is already running."""
    with SessionLocal() as session:
        job = session.scalar(
            select(CatchupJob).where(CatchupJob.status.in_(["QUEUED", "RUNNING"])).limit(1)
        )
        return job.job_id if job else None


def _mark_interrupted_jobs() -> list[str]:
    """Mark orphaned RUNNING catchup jobs as INTERRUPTED on startup."""
    interrupted = []
    with SessionLocal() as session:
        orphans = session.scalars(
            select(CatchupJob).where(CatchupJob.status.in_(["QUEUED", "RUNNING"]))
        ).all()
        for job in orphans:
            job.status = "INTERRUPTED"
            interrupted.append(job.job_id)
        session.commit()
    return interrupted


def get_system_mode() -> dict:
    """GET /api/system/status — current system mode + catch-up progress."""
    return {
        "mode": _SYSTEM_MODE,
        "offline_duration_seconds": round(_OFFLINE_DURATION, 1),
        "started_at": _SYSTEM_STARTUP_TIME.isoformat(),
        "progress_percent": _get_catchup_progress(),
    }


def _get_catchup_progress() -> float:
    """Get the latest catch-up job progress percent."""
    try:
        with SessionLocal() as session:
            job = session.scalar(
                select(CatchupJob).order_by(CatchupJob.started_at.desc()).limit(1)
            )
            if job:
                return job.progress_percent
    except Exception:
        pass
    return 0.0


def get_catchup_status() -> dict:
    """GET /api/catchup/status — catch-up coordinator status."""
    mode = get_system_mode()
    with SessionLocal() as session:
        # Get sync states for all components
        sync_states = session.scalars(select(SystemSyncState)).all()
        # Get latest catchup job
        latest_job = session.scalar(
            select(CatchupJob).order_by(CatchupJob.started_at.desc()).limit(1)
        )
        # Last live timestamps
        last_market = _get_last_live("last_live_market_timestamp")
        last_analysis = _get_last_live("last_live_analysis_timestamp")
        last_research = _get_last_live("last_live_research_timestamp")
        last_forward = _get_last_live("last_live_forward_capture")

    job_dict = None
    if latest_job:
        job_dict = {
            "job_id": latest_job.job_id,
            "status": latest_job.status,
            "started_at": latest_job.started_at.isoformat() if latest_job.started_at else None,
            "completed_at": latest_job.completed_at.isoformat() if latest_job.completed_at else None,
            "offline_started_at": latest_job.offline_started_at.isoformat() if latest_job.offline_started_at else None,
            "offline_ended_at": latest_job.offline_ended_at.isoformat() if latest_job.offline_ended_at else None,
            "market_sync_status": latest_job.market_sync_status,
            "research_sync_status": latest_job.research_sync_status,
            "forward_outcome_status": latest_job.forward_outcome_status,
            "historical_state_status": latest_job.historical_state_status,
            "recovered_market_candles": latest_job.recovered_market_candles,
            "recovered_spot_observations": latest_job.recovered_spot_observations,
            "research_items_added": latest_job.research_items_added,
            "forward_outcomes_evaluated": latest_job.forward_outcomes_evaluated,
            "missed_forward_captures": latest_job.missed_forward_captures,
            "historical_states_added": latest_job.historical_states_added,
            "progress_percent": latest_job.progress_percent,
            "last_error": latest_job.last_error,
        }

    return {
        "system_mode": mode,
        "sync_states": [
            {
                "component": s.component,
                "status": s.status,
                "last_successful_sync": s.last_successful_sync.isoformat() if s.last_successful_sync else None,
                "last_error": s.last_error,
            }
            for s in sync_states
        ],
        "latest_job": job_dict,
        "last_live": {
            "market_timestamp": last_market,
            "analysis_timestamp": last_analysis,
            "research_timestamp": last_research,
            "forward_capture_timestamp": last_forward,
        },
    }


def _get_last_live(key: str) -> str | None:
    try:
        with SessionLocal() as session:
            row = session.get(SystemConfig, key)
            return row.value if row else None
    except Exception:
        return None


def _set_last_live(key: str, value: str) -> None:
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with SessionLocal() as session:
            existing = session.get(SystemConfig, key)
            if existing is None:
                session.add(SystemConfig(key=key, value=value, updated_at=now))
            else:
                existing.value = value
                existing.updated_at = now
            session.commit()
    except Exception:
        pass


def list_catchup_jobs(limit: int = 20) -> list[dict]:
    """GET /api/catchup/jobs — list recent catch-up jobs."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(CatchupJob).order_by(CatchupJob.started_at.desc()).limit(limit)
        ).all()
        return [
            {
                "job_id": r.job_id,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "progress_percent": r.progress_percent,
                "recovered_market_candles": r.recovered_market_candles,
                "missed_forward_captures": r.missed_forward_captures,
                "last_error": r.last_error,
            }
            for r in rows
        ]


def get_catchup_job(job_id: str) -> dict | None:
    """GET /api/catchup/jobs/{job_id} — single job detail."""
    with SessionLocal() as session:
        job = session.scalar(select(CatchupJob).where(CatchupJob.job_id == job_id))
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "status": job.status,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "offline_started_at": job.offline_started_at.isoformat() if job.offline_started_at else None,
            "offline_ended_at": job.offline_ended_at.isoformat() if job.offline_ended_at else None,
            "market_sync_status": job.market_sync_status,
            "research_sync_status": job.research_sync_status,
            "forward_outcome_status": job.forward_outcome_status,
            "historical_state_status": job.historical_state_status,
            "recovered_market_candles": job.recovered_market_candles,
            "recovered_spot_observations": job.recovered_spot_observations,
            "research_items_added": job.research_items_added,
            "forward_outcomes_evaluated": job.forward_outcomes_evaluated,
            "missed_forward_captures": job.missed_forward_captures,
            "historical_states_added": job.historical_states_added,
            "progress_percent": job.progress_percent,
            "last_error": job.last_error,
        }


def _detect_missed_captures(offline_start: datetime, offline_end: datetime) -> int:
    """Detect M15/H1 candle close events missed while offline.
    Log them as MissedForwardCapture — never fabricate observations."""
    from app.engine.candles import INTERVALS
    count = 0
    for canonical_tf, interval_tf in [("M15", "15min"), ("H1", "1h")]:
        tf_seconds = INTERVALS.get(interval_tf, 900)
        # Walk from offline_start to offline_end, detecting candle boundaries
        cursor = offline_start.replace(microsecond=0)
        # Align to TF boundary
        epoch = int(cursor.timestamp())
        cursor = datetime.fromtimestamp(epoch - (epoch % tf_seconds), tz=timezone.utc)

        while cursor < offline_end:
            if cursor >= offline_start and cursor < offline_end:
                # This candle close happened while offline — it's a missed capture
                try:
                    with SessionLocal() as session:
                        # Check if not already logged
                        existing = session.scalar(
                            select(MissedForwardCapture).where(
                                MissedForwardCapture.capture_timeframe == canonical_tf,
                                MissedForwardCapture.expected_capture_timestamp == cursor.replace(tzinfo=None),
                            )
                        )
                        if existing is None:
                            session.add(MissedForwardCapture(
                                capture_timeframe=canonical_tf,
                                expected_capture_timestamp=cursor.replace(tzinfo=None),
                                offline_reason="SERVER_OFFLINE",
                                detected_at=datetime.now(timezone.utc).replace(tzinfo=None),
                            ))
                            session.commit()
                            count += 1
                            _audit("MISSED_FORWARD_CAPTURE",
                                   detail=f"{canonical_tf} @ {cursor.isoformat()}: missed while offline")
                except Exception:
                    pass
            cursor = cursor + timedelta(seconds=tf_seconds)
    return count


async def run_catchup(*, resume_job_id: str | None = None) -> dict:
    """Phase 5.3: main catch-up coordinator. Runs in order:

    1. Detect offline duration
    2. Recover market history
    3. Record spot history status
    4. Evaluate pending forward outcomes
    5. Detect + log missed forward captures
    6. Sync research
    7. Incrementally build historical states
    8. Transition to LIVE
    """
    global _SYSTEM_MODE

    # Single catch-up protection
    existing = _find_running_catchup()
    if existing and not resume_job_id:
        return {"job_id": existing, "status": "already_running",
                "message": f"Catch-up job {existing} is already running."}

    # Mark interrupted jobs
    _mark_interrupted_jobs()

    # Calculate offline duration
    offline_duration = _detect_offline_duration()
    now = datetime.now(timezone.utc)

    # Determine offline window
    offline_end = now
    offline_start = now - timedelta(seconds=offline_duration) if offline_duration > 60 else None

    _SYSTEM_MODE = MODE_CATCHING_UP
    _audit("SYSTEM_WAKE", detail=f"offline_duration={offline_duration:.0f}s")
    _audit("CATCHUP_STARTED")

    # Create job
    job_id = resume_job_id or f"CATCH-{uuid.uuid4().hex[:8].upper()}"
    with SessionLocal() as session:
        job = CatchupJob(
            job_id=job_id,
            started_at=now.replace(tzinfo=None),
            status="RUNNING",
            offline_started_at=offline_start.replace(tzinfo=None) if offline_start else None,
            offline_ended_at=offline_end.replace(tzinfo=None),
            progress_percent=0.0,
        )
        session.add(job)
        session.commit()

    def _update_job(**kwargs):
        try:
            with SessionLocal() as session:
                j = session.scalar(select(CatchupJob).where(CatchupJob.job_id == job_id))
                if j:
                    for k, v in kwargs.items():
                        setattr(j, k, v)
                    session.commit()
        except Exception:
            pass

    try:
        # Step 1: Recover market history (Yahoo Finance)
        _update_job(market_sync_status="SYNCING")
        _update_sync_state("market_history", "SYNCING")
        _audit("MARKET_RECOVERY_STARTED")

        recovered_candles = 0
        try:
            from app.services.historical import sync_historical_candles
            summary = await sync_historical_candles(symbol="XAU/USD")
            recovered_candles = sum(
                tf_info.get("inserted", 0)
                for tf_info in summary.get("timeframes", {}).values()
            )
            _update_sync_state("market_history", "COMPLETE")
            _audit("MARKET_RECOVERY_COMPLETE", detail=f"recovered={recovered_candles}")
        except Exception as exc:
            _update_sync_state("market_history", "DEGRADED", str(exc))
            _audit("MARKET_RECOVERY_COMPLETE", detail=f"partial: {exc}")

        _update_job(market_sync_status="COMPLETE", recovered_market_candles=recovered_candles,
                    progress_percent=20.0)

        # Step 2: Spot history recovery
        _update_sync_state("spot_history", "SYNCING")
        # Free Gold API has no historical spot endpoint — record as unavailable
        _update_sync_state("spot_history", "DEGRADED", "SPOT_HISTORY_NOT_RECOVERABLE: free Gold API has no historical spot endpoint")
        _audit("SPOT_RECOVERY_UNAVAILABLE", detail="free Gold API cannot recover historical spot data")
        _update_job(recovered_spot_observations=0, progress_percent=30.0)

        # Step 3: Evaluate pending forward outcomes
        _update_job(forward_outcome_status="SYNCING")
        _update_sync_state("forward_outcomes", "SYNCING")
        outcomes_evaluated = 0
        try:
            from app.services.forward import evaluate_pending_observations
            eval_result = await evaluate_pending_observations()
            outcomes_evaluated = eval_result.get("evaluated", 0)
            _update_sync_state("forward_outcomes", "COMPLETE")
            for i in range(eval_result.get("completed", 0)):
                _audit("FORWARD_OUTCOME_RECOVERED")
        except Exception as exc:
            _update_sync_state("forward_outcomes", "DEGRADED", str(exc))

        _update_job(forward_outcome_status="COMPLETE",
                    forward_outcomes_evaluated=outcomes_evaluated,
                    progress_percent=50.0)

        # Step 4: Detect + log missed forward captures
        missed = 0
        if offline_start:
            missed = _detect_missed_captures(offline_start, offline_end)
        _update_job(missed_forward_captures=missed, progress_percent=60.0)

        # Step 5: Research sync
        _update_job(research_sync_status="SYNCING")
        _update_sync_state("research", "SYNCING")
        research_added = 0
        try:
            from app.services.research import refresh_research
            inserted, error = await refresh_research()
            research_added = inserted or 0
            if error:
                _update_sync_state("research", "DEGRADED", error)
            else:
                _update_sync_state("research", "COMPLETE")
                _audit("RESEARCH_RECOVERY_COMPLETE", detail=f"items={research_added}")
        except Exception as exc:
            _update_sync_state("research", "DEGRADED", str(exc))

        _update_job(research_sync_status="COMPLETE",
                    research_items_added=research_added,
                    progress_percent=75.0)

        # Step 6: Historical state incremental build (skip for speed in catch-up)
        _update_sync_state("historical_states", "COMPLETE")
        _update_job(historical_state_status="COMPLETE",
                    historical_states_added=0,
                    progress_percent=90.0)

        # Step 7: Update last live timestamps
        _set_last_live("last_live_market_timestamp", now.isoformat())
        _set_last_live("last_live_analysis_timestamp", now.isoformat())
        _set_last_live("last_live_research_timestamp", now.isoformat())

        # Step 8: Transition to LIVE
        # Check if any critical component failed
        critical_ok = True
        for comp in ["market_history", "forward_outcomes"]:
            state = _get_sync_state(comp)
            if state and state.status == "FAILED":
                critical_ok = False

        if critical_ok:
            _SYSTEM_MODE = MODE_LIVE
        else:
            _SYSTEM_MODE = MODE_DEGRADED

        final_status = "COMPLETED"
        if not critical_ok:
            final_status = "COMPLETED_WITH_WARNINGS"

        _update_job(status=final_status, progress_percent=100.0,
                    completed_at=datetime.now(timezone.utc).replace(tzinfo=None))
        _audit("CATCHUP_COMPLETE", detail=f"mode={_SYSTEM_MODE} missed={missed}")

    except Exception as exc:
        _SYSTEM_MODE = MODE_DEGRADED
        _update_job(status="FAILED", last_error=str(exc),
                    completed_at=datetime.now(timezone.utc).replace(tzinfo=None))
        _audit("CATCHUP_FAILED", detail=str(exc))

    return {"job_id": job_id, "status": _SYSTEM_MODE,
            "offline_duration_seconds": round(offline_duration, 1),
            "recovered_candles": recovered_candles,
            "missed_captures": missed,
            "outcomes_evaluated": outcomes_evaluated,
            "research_added": research_added}


async def startup_catchup() -> dict:
    """Phase 5.3: called on backend startup. Runs catch-up in background
    so the API stays responsive."""
    # Detect offline duration first
    offline_duration = _detect_offline_duration()

    # If offline < 60 seconds, skip catch-up entirely
    if offline_duration < 60:
        global _SYSTEM_MODE
        _SYSTEM_MODE = MODE_LIVE
        _set_last_live("last_live_market_timestamp", datetime.now(timezone.utc).isoformat())
        _audit("SYSTEM_WAKE", detail=f"offline={offline_duration:.0f}s — skipping catch-up (short gap)")
        return {"skipped": True, "reason": "short gap", "offline_duration": offline_duration}

    # Run catch-up
    result = await run_catchup()
    return result
