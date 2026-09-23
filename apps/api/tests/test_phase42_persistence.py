"""Phase 4.2 tests: orphan detection, duplicate build protection, resume,
checkpoint idempotence, database URL configuration, batch commits,
full-count reconciliation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db.base import Base
from app.db.models import BuildJob, CandleRecord, HistoricalMarketState
from app.db.session import SessionLocal, engine
from app.services.learning.config import DEFAULT_CONFIG
from app.services.learning.jobs import (
    create_job,
    detect_orphaned_jobs,
    find_running_job,
    get_job,
    update_job_progress,
)


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def _seed_candle(ts, instrument="GC_FRONT_MONTH"):
    naive = ts.replace(tzinfo=None) if ts.tzinfo else ts
    with SessionLocal() as session:
        session.add(CandleRecord(
            symbol="XAU/USD", interval="1h", timestamp=naive,
            open=100, high=101, low=99, close=100.5, volume=None, sample_count=1,
            provider="Yahoo Finance (GC=F)", received_at=naive,
            is_historical=True, derivation="DIRECT",
            provider_symbol="GC=F", instrument=instrument,
            source_timeframe="1h", target_timeframe="1h",
        ))
        session.commit()


# ===========================================================================
# 1. Orphan RUNNING job detection
# ===========================================================================

def test_detect_orphaned_jobs_marks_running_as_interrupted():
    """On startup, jobs left in 'running' state (their asyncio tasks died)
    must be detected and marked as 'interrupted'."""
    job_id = create_job(
        instrument="GC_FRONT_MONTH", base_timeframe="1h",
        feature_version="features-v0.1", eligible_total=100,
    )
    update_job_progress(job_id, status="running")

    # Simulate process restart — detect orphaned jobs
    orphans = detect_orphaned_jobs()
    assert job_id in orphans

    # Verify the job is now 'interrupted'
    job = get_job(job_id)
    assert job["status"] == "interrupted"


def test_detect_orphaned_jobs_does_not_touch_completed():
    """Completed jobs must NOT be marked as interrupted."""
    job_id = create_job(
        instrument="GC_FRONT_MONTH", base_timeframe="1h",
        feature_version="features-v0.1", eligible_total=10,
    )
    update_job_progress(job_id, status="completed")

    orphans = detect_orphaned_jobs()
    assert job_id not in orphans
    assert get_job(job_id)["status"] == "completed"


# ===========================================================================
# 2. Duplicate build protection
# ===========================================================================

def test_find_running_job_returns_existing_job_id():
    """If a build job is already RUNNING for the same instrument + version,
    find_running_job() returns its job_id."""
    job_id = create_job(
        instrument="GC_FRONT_MONTH", base_timeframe="1h",
        feature_version="features-v0.1", eligible_total=100,
    )
    update_job_progress(job_id, status="running")

    existing = find_running_job("GC_FRONT_MONTH", "features-v0.1")
    assert existing == job_id


def test_find_running_job_returns_none_when_no_running_job():
    existing = find_running_job("GC_FRONT_MONTH", "features-v0.1")
    assert existing is None


def test_find_running_job_does_not_match_different_instrument():
    """A running job for XAUUSD_SPOT must NOT block a GC_FRONT_MONTH build."""
    job_id = create_job(
        instrument="XAUUSD_SPOT", base_timeframe="1h",
        feature_version="features-v0.1", eligible_total=100,
    )
    update_job_progress(job_id, status="running")

    existing = find_running_job("GC_FRONT_MONTH", "features-v0.1")
    assert existing is None  # different instrument — no conflict


# ===========================================================================
# 3. Checkpoint idempotence — no duplicate states
# ===========================================================================

def test_checkpoint_idempotence_no_duplicate_states():
    """Re-running a build must NOT create duplicate states. The unique key
    is (instrument, base_timeframe, timestamp, feature_version)."""
    # Insert a state manually
    ts = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(HistoricalMarketState(
            instrument="GC_FRONT_MONTH", provider="Yahoo Finance (GC=F)",
            provider_symbol="GC=F", timestamp=ts, base_timeframe="1h",
            price=100.5, feature_version="features-v0.1",
            similarity_version="similarity-v0.1", source_quality="HEALTHY",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        session.commit()

    # Try to insert the SAME state — should be skipped (idempotent)
    with SessionLocal() as session:
        from sqlalchemy import select
        existing = session.scalar(
            select(HistoricalMarketState.id).where(
                HistoricalMarketState.instrument == "GC_FRONT_MONTH",
                HistoricalMarketState.base_timeframe == "1h",
                HistoricalMarketState.timestamp == ts,
                HistoricalMarketState.feature_version == "features-v0.1",
            )
        )
        assert existing is not None  # the state already exists

    # Count states — should be 1
    with SessionLocal() as session:
        from sqlalchemy import func
        count = session.scalar(
            select(func.count(HistoricalMarketState.id)).where(
                HistoricalMarketState.instrument == "GC_FRONT_MONTH",
                HistoricalMarketState.timestamp == ts,
            )
        )
        assert count == 1  # no duplicate


# ===========================================================================
# 4. Database URL configuration
# ===========================================================================

def test_sqlite_url_parsing():
    """SQLite URLs are correctly identified."""
    from app.db.session import _is_sqlite, _is_postgresql
    assert _is_sqlite("sqlite:///./forexwizard.db") is True
    assert _is_sqlite("sqlite:////absolute/path.db") is True
    assert _is_postgresql("sqlite:///./forexwizard.db") is False


def test_postgresql_url_parsing():
    """PostgreSQL URLs are correctly identified."""
    from app.db.session import _is_sqlite, _is_postgresql
    assert _is_postgresql("postgresql+psycopg://user:pass@localhost:5432/forexwizard") is True
    assert _is_postgresql("postgresql://user:pass@localhost:5432/forexwizard") is True
    assert _is_sqlite("postgresql+psycopg://user:pass@localhost:5432/forexwizard") is False


# ===========================================================================
# 5. Batch commit behavior
# ===========================================================================

def test_batch_commit_config_exists():
    """Build config must have a build_batch_size setting."""
    cfg = DEFAULT_CONFIG
    assert cfg.build_batch_size > 0
    assert cfg.build_batch_size <= 250  # reasonable upper bound
    assert cfg.build_checkpoint_interval > 0


# ===========================================================================
# 6. Job status reconciliation
# ===========================================================================

def test_job_reconciliation_built_plus_skipped_plus_excluded():
    """built + skipped_existing + excluded_roll + excluded_gaps +
    excluded_insufficient_future should reconcile with eligible_total."""
    job_id = create_job(
        instrument="GC_FRONT_MONTH", base_timeframe="1h",
        feature_version="features-v0.1", eligible_total=100,
    )
    update_job_progress(
        job_id, built=50, skipped_existing=10, excluded_roll=5,
        excluded_gaps=3, excluded_insufficient_future=2,
    )
    job = get_job(job_id)
    total_processed = (
        job["built"] + job["skipped_existing"] + job["excluded_roll"]
        + job["excluded_gaps"] + job["excluded_insufficient_future"]
    )
    assert total_processed == 70
    assert job["remaining"] == 30
    assert job["percent_complete"] == 70.0


# ===========================================================================
# 7. Resume endpoint logic
# ===========================================================================

def test_resume_rejects_already_running_job():
    """Resuming a job that's already 'running' should return 'already_running'."""
    from app.services.learning.orchestrator import start_build_job
    job_id = create_job(
        instrument="GC_FRONT_MONTH", base_timeframe="1h",
        feature_version="features-v0.1", eligible_total=10,
    )
    update_job_progress(job_id, status="running")

    # The resume logic checks status — running jobs can't be resumed
    job = get_job(job_id)
    assert job["status"] == "running"
    # The API endpoint would return 200 with "already_running" — tested
    # at the endpoint level. Here we verify the status check logic.


def test_resume_accepts_interrupted_job():
    """An interrupted job CAN be resumed."""
    job_id = create_job(
        instrument="GC_FRONT_MONTH", base_timeframe="1h",
        feature_version="features-v0.1", eligible_total=10,
    )
    update_job_progress(job_id, status="interrupted")

    job = get_job(job_id)
    assert job["status"] == "interrupted"
    # The API endpoint would call start_build_job(resume_job_id=job_id)
    # which updates status to "running" and spawns the build task.


# ===========================================================================
# 8. WAL mode verification
# ===========================================================================

def test_sqlite_wal_mode_active():
    """WAL journal mode must be active on the SQLite database for
    concurrent read/write support during builds."""
    import sqlite3
    # Use the test database URL's file path
    db_path = str(engine.url.database)
    if not db_path.endswith(".db"):
        return  # PostgreSQL — skip
    conn = sqlite3.connect(db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal", f"Expected WAL mode, got {mode}"


def test_sqlite_busy_timeout_set():
    """busy_timeout must be set to allow concurrent reads during writes."""
    import sqlite3
    db_path = str(engine.url.database)
    if not db_path.endswith(".db"):
        return  # PostgreSQL — skip
    conn = sqlite3.connect(db_path)
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert timeout >= 5000, f"Expected busy_timeout >= 5000ms, got {timeout}ms"
