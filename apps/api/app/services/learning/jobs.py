"""Phase 4.1: background build-job infrastructure.

BuildJob rows are persisted to the DB; the actual work runs as a
fire-and-forget asyncio task. The task periodically updates the
BuildJob row with built/remaining/percent_complete/updated_at so
the GET /api/learning/jobs/{job_id} endpoint can report progress.

On backend restart, the startup migration marks any in-flight BuildJobs
as "interrupted". They can be resumed by POST /api/learning/build-states
with the same job_id + resume=true.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import BuildJob
from app.db.session import SessionLocal


def new_job_id() -> str:
    return f"JOB-{uuid.uuid4().hex[:8].upper()}"


def new_run_id() -> str:
    return f"SIM-{uuid.uuid4().hex[:8].upper()}"


def create_job(
    *,
    instrument: str,
    base_timeframe: str,
    feature_version: str,
    eligible_total: int = 0,
) -> str:
    """Insert a new BuildJob row + return its job_id."""
    job_id = new_job_id()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(BuildJob(
            job_id=job_id,
            instrument=instrument,
            base_timeframe=base_timeframe,
            feature_version=feature_version,
            started_at=now,
            updated_at=now,
            status="queued",
            eligible_total=eligible_total,
            built=0,
            skipped_existing=0,
            excluded_roll=0,
            excluded_gaps=0,
            excluded_insufficient_future=0,
            remaining=eligible_total,
            percent_complete=0.0,
        ))
        session.commit()
    return job_id


def update_job_progress(
    job_id: str,
    *,
    built: int | None = None,
    skipped_existing: int | None = None,
    excluded_roll: int | None = None,
    excluded_gaps: int | None = None,
    excluded_insufficient_future: int | None = None,
    status: str | None = None,
    last_checkpoint_ts: datetime | None = None,
    earliest_state: datetime | None = None,
    latest_state: datetime | None = None,
    error_message: str | None = None,
    elapsed_seconds: float | None = None,
    states_per_second: float | None = None,
) -> None:
    """Best-effort progress update — never blocks the caller."""
    try:
        with SessionLocal() as session:
            job = session.scalar(select(BuildJob).where(BuildJob.job_id == job_id))
            if job is None:
                return
            if built is not None:
                job.built = built
            if skipped_existing is not None:
                job.skipped_existing = skipped_existing
            if excluded_roll is not None:
                job.excluded_roll = excluded_roll
            if excluded_gaps is not None:
                job.excluded_gaps = excluded_gaps
            if excluded_insufficient_future is not None:
                job.excluded_insufficient_future = excluded_insufficient_future
            if status is not None:
                job.status = status
            if last_checkpoint_ts is not None:
                job.last_checkpoint_ts = last_checkpoint_ts
            if earliest_state is not None:
                if job.earliest_state is None or earliest_state < job.earliest_state:
                    job.earliest_state = earliest_state
            if latest_state is not None:
                if job.latest_state is None or latest_state > job.latest_state:
                    job.latest_state = latest_state
            if error_message is not None:
                job.error_message = error_message
            if elapsed_seconds is not None:
                job.elapsed_seconds = elapsed_seconds
            if states_per_second is not None:
                job.states_per_second = states_per_second
            # Compute remaining + percent_complete
            total_processed = (
                (job.built or 0)
                + (job.skipped_existing or 0)
                + (job.excluded_roll or 0)
                + (job.excluded_gaps or 0)
                + (job.excluded_insufficient_future or 0)
            )
            job.remaining = max(0, (job.eligible_total or 0) - total_processed)
            if job.eligible_total and job.eligible_total > 0:
                job.percent_complete = round(100.0 * total_processed / job.eligible_total, 2)
            job.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
    except Exception:
        pass  # progress updates are best-effort


def mark_job_completed(job_id: str, *, elapsed_seconds: float | None = None) -> None:
    try:
        with SessionLocal() as session:
            job = session.scalar(select(BuildJob).where(BuildJob.job_id == job_id))
            if job is None:
                return
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            job.updated_at = job.completed_at
            if elapsed_seconds is not None:
                job.elapsed_seconds = elapsed_seconds
                if elapsed_seconds > 0 and job.built:
                    job.states_per_second = round(job.built / elapsed_seconds, 2)
            session.commit()
    except Exception:
        pass


def mark_job_failed(job_id: str, error: str) -> None:
    try:
        with SessionLocal() as session:
            job = session.scalar(select(BuildJob).where(BuildJob.job_id == job_id))
            if job is None:
                return
            job.status = "failed"
            job.error_message = error
            job.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
    except Exception:
        pass


def get_job(job_id: str) -> dict | None:
    """Return the job's full state as a dict for the API endpoint."""
    with SessionLocal() as session:
        job = session.scalar(select(BuildJob).where(BuildJob.job_id == job_id))
        if job is None:
            return None
        return {
            "job_id": job.job_id,
            "instrument": job.instrument,
            "base_timeframe": job.base_timeframe,
            "feature_version": job.feature_version,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "status": job.status,
            "eligible_total": job.eligible_total,
            "built": job.built,
            "skipped_existing": job.skipped_existing,
            "excluded_roll": job.excluded_roll,
            "excluded_gaps": job.excluded_gaps,
            "excluded_insufficient_future": job.excluded_insufficient_future,
            "remaining": job.remaining,
            "percent_complete": job.percent_complete,
            "last_checkpoint_ts": job.last_checkpoint_ts.isoformat() if job.last_checkpoint_ts else None,
            "earliest_state": job.earliest_state.isoformat() if job.earliest_state else None,
            "latest_state": job.latest_state.isoformat() if job.latest_state else None,
            "error_message": job.error_message,
            "elapsed_seconds": job.elapsed_seconds,
            "states_per_second": job.states_per_second,
        }


def list_active_jobs() -> list[dict]:
    """List all jobs not in a terminal state."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(BuildJob).where(
                BuildJob.status.in_(["queued", "running", "interrupted", "paused"])
            ).order_by(BuildJob.started_at.desc())
        ).all()
        return [get_job(r.job_id) or {} for r in rows]  # type: ignore


# ===========================================================================
# Phase 4.2: Single-build protection + orphan detection + resume
# ===========================================================================

def find_running_job(instrument: str, feature_version: str) -> str | None:
    """Check if an equivalent build job is already RUNNING for the same
    instrument + feature_version. If so, return its job_id (caller should
    return that job_id rather than starting a duplicate build).

    Prevents accidentally starting five identical 11k-state builds.
    """
    with SessionLocal() as session:
        job = session.scalar(
            select(BuildJob).where(
                BuildJob.instrument == instrument,
                BuildJob.feature_version == feature_version,
                BuildJob.status.in_(["queued", "running"]),
            ).order_by(BuildJob.started_at.desc()).limit(1)
        )
        return job.job_id if job else None


def detect_orphaned_jobs() -> list[str]:
    """Phase 4.2: on startup, find all jobs left in 'queued' or 'running'
    state (their asyncio tasks died when the process restarted). Mark
    them as 'interrupted' and return their job_ids for potential resume.

    The startup migration also does this via a SQL UPDATE, but this Python
    function is callable on-demand for testing.
    """
    orphan_ids: list[str] = []
    with SessionLocal() as session:
        orphans = session.scalars(
            select(BuildJob).where(
                BuildJob.status.in_(["queued", "running"])
            )
        ).all()
        for job in orphans:
            job.status = "interrupted"
            job.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            orphan_ids.append(job.job_id)
        session.commit()
    return orphan_ids
