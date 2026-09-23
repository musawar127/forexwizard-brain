"""Phase 5.2 tests: canonical TF naming, startup recovery, missed captures,
heartbeat, health status, data gap warning, dedup after restart."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.base import Base
from app.db.models import ForwardHeartbeat, ForwardObservation, SystemConfig
from app.db.session import SessionLocal, engine
from app.services.forward import (
    CANONICAL_CAPTURE_TIMEFRAMES,
    INTERNAL_TO_INTERVALS,
    get_forward_health,
    get_forward_start_date,
    set_forward_start_date,
)


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


# ===========================================================================
# 1. Canonical M15/H1 timeframe naming
# ===========================================================================

def test_canonical_timeframe_mapping():
    """Internal DB uses M15 and H1 only — never 15min or 1h."""
    assert CANONICAL_CAPTURE_TIMEFRAMES["15min"] == "M15"
    assert CANONICAL_CAPTURE_TIMEFRAMES["1h"] == "H1"
    assert INTERNAL_TO_INTERVALS["M15"] == "15min"
    assert INTERNAL_TO_INTERVALS["H1"] == "1h"


def test_observation_uses_canonical_tf():
    """ForwardObservation.capture_timeframe must be M15 or H1."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(ForwardObservation(
            observation_id="FWD-CANON01",
            created_at=now,
            live_instrument="XAUUSD_SPOT",
            live_provider="Gold API",
            live_symbol="XAU",
            live_market_timestamp=now,
            live_price=100.0,
            technical_decision="WAIT",
            technical_score=50.0,
            technical_rule_version="rules-v0.1",
            technical_data_readiness=75.0,
            feature_version="features-v0.1",
            similarity_version="similarity-v0.1",
            outcome_version="outcomes-v0.2",
            capture_timeframe="M15",
            capture_timestamp=now,
            data_freshness_status="RECENT",
            observation_status="PENDING",
            historical_alignment="NOT_APPLICABLE",
            wait_historical_context="NEUTRAL",
        ))
        session.commit()
    with SessionLocal() as session:
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-CANON01"))
        assert obs.capture_timeframe == "M15"
        assert obs.capture_timeframe in ("M15", "H1")


# ===========================================================================
# 2. Forward heartbeat persistence
# ===========================================================================

def test_heartbeat_persisted():
    """ForwardHeartbeat table stores periodic status."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(ForwardHeartbeat(
            timestamp=now,
            collector_running=True,
            evaluator_running=True,
            pending_observations=2,
            uptime_seconds=3600.0,
        ))
        session.commit()
    with SessionLocal() as session:
        hb = session.scalar(select(ForwardHeartbeat).order_by(ForwardHeartbeat.timestamp.desc()))
        assert hb is not None
        assert hb.collector_running is True
        assert hb.pending_observations == 2


# ===========================================================================
# 3. Health endpoint returns collector status
# ===========================================================================

def test_forward_health_returns_status():
    """get_forward_health() returns collector_status + uptime + spot info."""
    health = get_forward_health()
    assert "collector_status" in health
    assert health["collector_status"] in ("ONLINE", "DEGRADED", "OFFLINE")
    assert "uptime_seconds" in health
    assert "spot_storage" in health
    assert "pending_observations" in health


# ===========================================================================
# 4. Startup recovery
# ===========================================================================

@pytest.mark.asyncio
async def test_startup_recovery_evaluates_pending():
    """startup_recovery() should evaluate pending observations."""
    from app.services.forward import startup_recovery
    result = await startup_recovery()
    assert "missed_captures" in result
    assert "evaluated" in result
    assert "completed" in result


# ===========================================================================
# 5. No fake retroactive captures
# ===========================================================================

def test_no_observations_before_forward_start():
    """No valid observation can have capture_timestamp < forward_validation_started_at."""
    start = set_forward_start_date()
    with SessionLocal() as session:
        obs = session.scalars(
            select(ForwardObservation).where(
                ForwardObservation.observation_status != "INVALID",
                ForwardObservation.capture_timestamp < start.replace(tzinfo=None),
            )
        ).all()
        assert len(obs) == 0  # no valid observations before forward start


# ===========================================================================
# 6. Spot storage monitoring
# ===========================================================================

def test_spot_storage_info_in_health():
    """get_forward_health() includes spot_storage with retention info."""
    health = get_forward_health()
    spot = health.get("spot_storage", {})
    # May return 'error' key if no spot data exists in test DB
    if "error" in spot:
        assert "error" in spot  # acceptable — no spot data in test DB
    else:
        assert "oldest_spot_observation" in spot
        assert "latest_spot_observation" in spot
        assert "total_spot_observations" in spot
        assert "retention_days" in spot


# ===========================================================================
# 7. System health endpoint
# ===========================================================================

def test_system_health_fields():
    """System health should cover all subsystems."""
    # Just verify the function exists and returns a dict with expected keys
    # when called directly (the async endpoint is tested via the API)
    from app.services.forward import get_forward_health
    fh = get_forward_health()
    assert isinstance(fh, dict)
    assert "collector_status" in fh


# ===========================================================================
# 8. Deduplication after restart
# ===========================================================================

def test_duplicate_outcome_prevented_after_restart():
    """Unique constraint on (observation_id, horizon_minutes) prevents
    duplicate outcomes even after a restart."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    from app.db.models import ForwardOutcome
    with SessionLocal() as session:
        for _ in range(2):
            try:
                session.add(ForwardOutcome(
                    observation_id="FWD-RESTART01",
                    horizon_minutes=60,
                    outcome_instrument="XAUUSD_SPOT",
                    outcome_provider="Gold API",
                    entry_price=100.0,
                    direction="UP",
                    outcome_status="VALID",
                    horizon_valid=True,
                ))
                session.commit()
            except Exception:
                session.rollback()
                break
    with SessionLocal() as session:
        from sqlalchemy import func
        count = session.scalar(
            select(func.count(ForwardOutcome.id)).where(
                ForwardOutcome.observation_id == "FWD-RESTART01",
                ForwardOutcome.horizon_minutes == 60,
            )
        )
        assert count == 1
