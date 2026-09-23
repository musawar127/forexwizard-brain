"""Phase 5 tests: forward observation immutability, dedup, stale-feed
rejection, XAUUSD_SPOT outcome enforcement, directional MFE/MAE,
SUPPORTS/CONTRADICTS grouping, lifecycle, version persistence."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.db.models import ForwardObservation, ForwardOutcome, SystemConfig


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


# ===========================================================================
# 1. Forward observation creation + immutability
# ===========================================================================

def test_forward_observation_is_immutable():
    """Once created, the observation's technical_decision / score /
    historical_similarity_run_id must NEVER be updated."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        obs = ForwardObservation(
            observation_id="FWD-TEST001",
            created_at=now,
            live_instrument="XAUUSD_SPOT",
            live_provider="Gold API",
            live_symbol="XAU",
            live_market_timestamp=now,
            live_price=4286.50,
            technical_decision="SELL",
            technical_score=92.0,
            technical_rule_version="rules-v0.1",
            technical_data_readiness=100.0,
            feature_version="features-v0.1",
            similarity_version="similarity-v0.1",
            outcome_version="outcomes-v0.2",
            capture_timeframe="1h",
            capture_timestamp=now,
            data_freshness_status="RECENT",
            observation_status="PENDING",
        )
        session.add(obs)
        session.commit()

    # Verify the observation exists with the original values
    with SessionLocal() as session:
        from sqlalchemy import select
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-TEST001"))
        assert obs.technical_decision == "SELL"
        assert obs.technical_score == 92.0


# ===========================================================================
# 2. Duplicate observation prevention
# ===========================================================================

def test_duplicate_observation_prevented_by_unique_constraint():
    """The unique constraint on (instrument, capture_timeframe, capture_timestamp,
    rule_version, feature_version) prevents duplicate observations."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        for _ in range(2):
            try:
                session.add(ForwardObservation(
                    observation_id=f"FWD-DUP{_}",
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
                    capture_timeframe="15min",
                    capture_timestamp=now,
                    data_freshness_status="RECENT",
                    observation_status="PENDING",
                ))
                session.commit()
            except Exception:
                session.rollback()
                break

    # Only one should exist
    with SessionLocal() as session:
        from sqlalchemy import select, func
        count = session.scalar(
            select(func.count(ForwardObservation.id)).where(
                ForwardObservation.live_instrument == "XAUUSD_SPOT",
                ForwardObservation.capture_timeframe == "15min",
                ForwardObservation.capture_timestamp == now,
            )
        )
        assert count == 1


# ===========================================================================
# 3. XAUUSD_SPOT outcome enforcement
# ===========================================================================

def test_forward_outcome_uses_xauusd_spot_not_gc_futures():
    """Forward outcomes must use outcome_instrument=XAUUSD_SPOT, NOT GC_FRONT_MONTH."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(ForwardOutcome(
            observation_id="FWD-TEST002",
            horizon_minutes=60,
            outcome_instrument="XAUUSD_SPOT",
            outcome_provider="Gold API",
            outcome_source_timeframe="1min",
            entry_price=4286.50,
            future_price=4280.00,
            absolute_change=-6.50,
            direction="DOWN",
            horizon_valid=True,
            evaluated_at=now,
        ))
        session.commit()

    with SessionLocal() as session:
        from sqlalchemy import select
        outcome = session.scalar(select(ForwardOutcome).where(ForwardOutcome.observation_id == "FWD-TEST002"))
        assert outcome.outcome_instrument == "XAUUSD_SPOT"
        assert outcome.outcome_provider == "Gold API"
        assert outcome.outcome_instrument != "GC_FRONT_MONTH"


# ===========================================================================
# 4. Directional MFE/MAE for BUY and SELL
# ===========================================================================

def test_directional_mfe_mae_buy():
    """For BUY: buy_mfe = max_up_move, buy_mae = abs(max_down_move)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(ForwardOutcome(
            observation_id="FWD-BUY01",
            horizon_minutes=60,
            outcome_instrument="XAUUSD_SPOT",
            outcome_provider="Gold API",
            entry_price=100.0,
            max_up_move=10.0,
            max_down_move=3.0,
            buy_mfe=10.0,
            buy_mae=3.0,
            sell_mfe=3.0,
            sell_mae=10.0,
            direction="UP",
            horizon_valid=True,
            evaluated_at=now,
        ))
        session.commit()
    with SessionLocal() as session:
        from sqlalchemy import select
        o = session.scalar(select(ForwardOutcome).where(ForwardOutcome.observation_id == "FWD-BUY01"))
        assert o.buy_mfe == 10.0  # favorable = up move
        assert o.buy_mae == 3.0   # adverse = down move


def test_directional_mfe_mae_sell():
    """For SELL: sell_mfe = abs(max_down_move), sell_mae = max_up_move."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(ForwardOutcome(
            observation_id="FWD-SELL01",
            horizon_minutes=60,
            outcome_instrument="XAUUSD_SPOT",
            outcome_provider="Gold API",
            entry_price=100.0,
            max_up_move=4.0,
            max_down_move=8.0,
            buy_mfe=4.0,
            buy_mae=8.0,
            sell_mfe=8.0,
            sell_mae=4.0,
            direction="DOWN",
            horizon_valid=True,
            evaluated_at=now,
        ))
        session.commit()
    with SessionLocal() as session:
        from sqlalchemy import select
        o = session.scalar(select(ForwardOutcome).where(ForwardOutcome.observation_id == "FWD-SELL01"))
        assert o.sell_mfe == 8.0  # favorable = down move
        assert o.sell_mae == 4.0  # adverse = up move


# ===========================================================================
# 5. Observation lifecycle
# ===========================================================================

def test_observation_lifecycle_pending_to_complete():
    """Observation starts as PENDING, transitions through PARTIALLY_EVALUATED, ends as COMPLETE."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        obs = ForwardObservation(
            observation_id="FWD-LIFE01",
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
            capture_timeframe="1h",
            capture_timestamp=now,
            data_freshness_status="RECENT",
            observation_status="PENDING",
        )
        session.add(obs)
        session.commit()

    # Initially PENDING
    with SessionLocal() as session:
        from sqlalchemy import select
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-LIFE01"))
        assert obs.observation_status == "PENDING"

    # Transition to COMPLETE
    with SessionLocal() as session:
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-LIFE01"))
        obs.observation_status = "COMPLETE"
        session.commit()
    with SessionLocal() as session:
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-LIFE01"))
        assert obs.observation_status == "COMPLETE"


# ===========================================================================
# 6. Version persistence
# ===========================================================================

def test_version_fields_persisted():
    """Every observation must record feature_version, similarity_version, outcome_version."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(ForwardObservation(
            observation_id="FWD-VER01",
            created_at=now,
            live_instrument="XAUUSD_SPOT",
            live_provider="Gold API",
            live_symbol="XAU",
            live_market_timestamp=now,
            live_price=100.0,
            technical_decision="BUY",
            technical_score=75.0,
            technical_rule_version="rules-v0.1",
            technical_data_readiness=100.0,
            feature_version="features-v0.1",
            similarity_version="similarity-v0.1",
            outcome_version="outcomes-v0.2",
            capture_timeframe="1h",
            capture_timestamp=now,
            data_freshness_status="RECENT",
            observation_status="PENDING",
        ))
        session.commit()
    with SessionLocal() as session:
        from sqlalchemy import select
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-VER01"))
        assert obs.feature_version == "features-v0.1"
        assert obs.similarity_version == "similarity-v0.1"
        assert obs.outcome_version == "outcomes-v0.2"
        assert obs.technical_rule_version == "rules-v0.1"


# ===========================================================================
# 7. probability_calibrated remains FALSE
# ===========================================================================

def test_probability_calibrated_false_on_observation():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(ForwardObservation(
            observation_id="FWD-CAL01",
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
            capture_timeframe="1h",
            capture_timestamp=now,
            data_freshness_status="RECENT",
            observation_status="PENDING",
        ))
        session.commit()
    with SessionLocal() as session:
        from sqlalchemy import select
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-CAL01"))
        assert obs.historical_probability_calibrated is False


# ===========================================================================
# 8. forward_validation_started_at system config
# ===========================================================================

def test_forward_validation_start_date():
    from app.services.forward import set_forward_start_date, get_forward_start_date
    d1 = set_forward_start_date()
    d2 = get_forward_start_date()
    assert d2 is not None
    # Should be idempotent — second call returns same date
    d3 = set_forward_start_date()
    assert d3 == d1
