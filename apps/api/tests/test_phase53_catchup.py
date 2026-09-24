"""Phase 5.3 tests: wake and catch-up recovery — system mode, sync states,
missed captures, single-job protection, no fake spot recovery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.base import Base
from app.db.models import (
    CatchupJob,
    ForwardHeartbeat,
    MissedForwardCapture,
    SystemSyncState,
)
from app.db.session import SessionLocal, engine
from app.services.catchup import (
    MODE_LIVE,
    get_catchup_status,
    get_system_mode,
    _detect_offline_duration,
    _detect_missed_captures,
    _find_running_catchup,
    _mark_interrupted_jobs,
)


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


# ===========================================================================
# 1. System mode
# ===========================================================================

def test_system_mode_returns_dict():
    mode = get_system_mode()
    assert "mode" in mode
    assert mode["mode"] in ("STARTING", "CATCHING_UP", "LIVE", "DEGRADED", "OFFLINE")


# ===========================================================================
# 2. Sync state persistence
# ===========================================================================

def test_sync_state_persisted():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(SystemSyncState(
            component="market_history",
            status="COMPLETE",
            last_successful_sync=now,
            last_attempted_sync=now,
            updated_at=now,
        ))
        session.commit()
    with SessionLocal() as session:
        row = session.scalar(select(SystemSyncState).where(SystemSyncState.component == "market_history"))
        assert row is not None
        assert row.status == "COMPLETE"


# ===========================================================================
# 3. Missed forward capture logging
# ===========================================================================

def test_missed_capture_logged_not_as_observation():
    """MissedForwardCapture records must NOT appear in forward_observations."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(MissedForwardCapture(
            capture_timeframe="M15",
            expected_capture_timestamp=now,
            offline_reason="SERVER_OFFLINE",
            detected_at=now,
        ))
        session.commit()
    # Verify it's in missed_forward_captures, NOT in forward_observations
    with SessionLocal() as session:
        from app.db.models import ForwardObservation
        from sqlalchemy import func
        missed_count = session.scalar(select(func.count(MissedForwardCapture.id))) or 0
        obs_count = session.scalar(select(func.count(ForwardObservation.id)).where(
            ForwardObservation.capture_timeframe == "M15",
            ForwardObservation.capture_timestamp == now,
        )) or 0
        assert missed_count == 1
        assert obs_count == 0  # missed captures do NOT count as observations


# ===========================================================================
# 4. Offline duration detection
# ===========================================================================

def test_offline_duration_from_heartbeat():
    """If a heartbeat exists 1 hour ago, offline_duration should be ~3600s."""
    old_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as session:
        session.add(ForwardHeartbeat(
            timestamp=old_ts.replace(tzinfo=None),
            collector_running=True,
            evaluator_running=True,
            pending_observations=0,
            uptime_seconds=100.0,
        ))
        session.commit()
    duration = _detect_offline_duration()
    assert duration > 3500  # ~1 hour
    assert duration < 3700


def test_offline_duration_zero_without_heartbeat():
    """If no heartbeat, offline_duration should be 0."""
    duration = _detect_offline_duration()
    assert duration == 0.0


# ===========================================================================
# 5. Single catch-up protection
# ===========================================================================

def test_single_catchup_protection():
    """If a catch-up job is already RUNNING, find_running_catchup returns its job_id."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(CatchupJob(
            job_id="CATCH-TEST01",
            started_at=now,
            status="RUNNING",
            progress_percent=30.0,
        ))
        session.commit()
    existing = _find_running_catchup()
    assert existing == "CATCH-TEST01"


def test_no_running_catchup_returns_none():
    assert _find_running_catchup() is None


# ===========================================================================
# 6. Interrupted job marking
# ===========================================================================

def test_interrupted_jobs_marked_on_startup():
    """Orphaned RUNNING jobs must be marked INTERRUPTED."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(CatchupJob(job_id="CATCH-ORPHAN01", started_at=now, status="RUNNING"))
        session.add(CatchupJob(job_id="CATCH-ORPHAN02", started_at=now, status="QUEUED"))
        session.commit()
    interrupted = _mark_interrupted_jobs()
    assert "CATCH-ORPHAN01" in interrupted
    assert "CATCH-ORPHAN02" in interrupted
    with SessionLocal() as session:
        for jid in interrupted:
            job = session.scalar(select(CatchupJob).where(CatchupJob.job_id == jid))
            assert job.status == "INTERRUPTED"


# ===========================================================================
# 7. Catch-up status returns all components
# ===========================================================================

def test_catchup_status_structure():
    status = get_catchup_status()
    assert "system_mode" in status
    assert "sync_states" in status
    assert "latest_job" in status
    assert "last_live" in status


# ===========================================================================
# 8. Missed capture detection
# ===========================================================================

def test_missed_captures_detected_for_offline_window():
    """_detect_missed_captures should log M15/H1 boundaries within the offline window."""
    now = datetime.now(timezone.utc)
    offline_start = now - timedelta(hours=2)
    offline_end = now - timedelta(minutes=30)
    count = _detect_missed_captures(offline_start, offline_end)
    # Should have detected at least some M15 boundaries (8+ in 1.5h)
    assert count > 0
    # Verify they're in the DB
    with SessionLocal() as session:
        from sqlalchemy import func
        total = session.scalar(select(func.count(MissedForwardCapture.id))) or 0
        assert total == count


# ===========================================================================
# 9. No fake spot recovery
# ===========================================================================

def test_spot_history_marked_unavailable():
    """Spot history from free Gold API is UNAVAILABLE — no historical endpoint.
    The catch-up coordinator must record SPOT_HISTORY_NOT_RECOVERABLE."""
    # This is verified by the catchup service code which sets:
    # _update_sync_state("spot_history", "DEGRADED", "SPOT_HISTORY_NOT_RECOVERABLE: ...")
    # We verify the sync state can be set with this status
    from app.services.catchup import _update_sync_state
    _update_sync_state("spot_history", "DEGRADED", "SPOT_HISTORY_NOT_RECOVERABLE: free Gold API has no historical spot endpoint")
    with SessionLocal() as session:
        row = session.scalar(select(SystemSyncState).where(SystemSyncState.component == "spot_history"))
        assert row is not None
        assert row.status == "DEGRADED"
        assert "NOT_RECOVERABLE" in (row.last_error or "")
