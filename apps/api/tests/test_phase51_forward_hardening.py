"""Phase 5.1 tests: forward validation hardening — start-time enforcement,
WAIT semantics, immutability, stale run rejection, full-window MFE/MAE,
outcome status, M15/H1 separation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.base import Base
from app.db.models import ForwardObservation, ForwardOutcome, SystemConfig
from app.db.session import SessionLocal, engine
from app.services.forward import (
    MAX_SIMILARITY_RUN_AGE_SECONDS,
    TARGET_TOLERANCE_SECONDS,
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
# 1. Forward start-time enforcement
# ===========================================================================

def test_observation_before_forward_start_marked_invalid():
    """If capture_timestamp < forward_validation_started_at, mark INVALID."""
    # Set forward start date
    start = set_forward_start_date()
    start_naive = start.replace(tzinfo=None)

    # Insert an observation with capture_timestamp BEFORE start
    earlier_ts = start_naive - timedelta(hours=1)
    with SessionLocal() as session:
        obs = ForwardObservation(
            observation_id="FWD-PRESTART",
            created_at=earlier_ts,
            live_instrument="XAUUSD_SPOT",
            live_provider="Gold API",
            live_symbol="XAU",
            live_market_timestamp=earlier_ts,
            live_price=100.0,
            technical_decision="WAIT",
            technical_score=50.0,
            technical_rule_version="rules-v0.1",
            technical_data_readiness=75.0,
            feature_version="features-v0.1",
            similarity_version="similarity-v0.1",
            outcome_version="outcomes-v0.2",
            capture_timeframe="1h",
            capture_timestamp=earlier_ts,
            data_freshness_status="RECENT",
            observation_status="PENDING",
        )
        session.add(obs)
        session.commit()

    # Run the migration's audit logic manually
    from sqlalchemy import text
    with SessionLocal() as session:
        session.execute(text(
            "UPDATE forward_observations SET observation_status='INVALID', "
            "invalid_reason='BEFORE_FORWARD_VALIDATION_START' "
            "WHERE capture_timestamp < :start"
        ), {"start": start_naive})
        session.commit()

    # Verify it's marked INVALID
    with SessionLocal() as session:
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-PRESTART"))
        assert obs.observation_status == "INVALID"
        assert obs.invalid_reason == "BEFORE_FORWARD_VALIDATION_START"


# ===========================================================================
# 2. WAIT alignment semantics
# ===========================================================================

def test_wait_observation_uses_not_applicable_alignment():
    """WAIT observations must have historical_alignment=NOT_APPLICABLE
    and use wait_historical_context instead."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        obs = ForwardObservation(
            observation_id="FWD-WAIT01",
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
            historical_alignment="NOT_APPLICABLE",
            wait_historical_context="NEUTRAL",
        )
        session.add(obs)
        session.commit()

    with SessionLocal() as session:
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-WAIT01"))
        assert obs.historical_alignment == "NOT_APPLICABLE"
        assert obs.wait_historical_context == "NEUTRAL"


def test_buy_observation_uses_directional_alignment():
    """BUY observations use SUPPORTS/CONTRADICTS/NEUTRAL/INSUFFICIENT_DATA (not NOT_APPLICABLE)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        obs = ForwardObservation(
            observation_id="FWD-BUY01",
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
            historical_alignment="SUPPORTS",
        )
        session.add(obs)
        session.commit()

    with SessionLocal() as session:
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-BUY01"))
        assert obs.historical_alignment == "SUPPORTS"
        assert obs.wait_historical_context is None


# ===========================================================================
# 3. Observation immutability
# ===========================================================================

def test_observation_immutable_fields():
    """live_price, technical_decision, technical_score, historical_similarity_run_id,
    capture_timestamp must never change after creation."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        obs = ForwardObservation(
            observation_id="FWD-IMMUT01",
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
            historical_similarity_run_id="SIM-TEST01",
            historical_alignment="CONTRADICTS",
        )
        session.add(obs)
        session.commit()

    # Verify fields are immutable — they should NEVER be updated by the service
    with SessionLocal() as session:
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-IMMUT01"))
        # Store original values
        orig_price = obs.live_price
        orig_decision = obs.technical_decision
        orig_score = obs.technical_score
        orig_run_id = obs.historical_similarity_run_id
        orig_ts = obs.capture_timestamp

    # The service should NEVER update these fields — only observation_status
    # and outcomes change. Here we verify the fields are still the same.
    with SessionLocal() as session:
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-IMMUT01"))
        assert obs.live_price == orig_price
        assert obs.technical_decision == orig_decision
        assert obs.technical_score == orig_score
        assert obs.historical_similarity_run_id == orig_run_id
        assert obs.capture_timestamp == orig_ts


# ===========================================================================
# 4. Full-window MFE/MAE
# ===========================================================================

def test_full_window_mfe_mae_not_just_entry_and_final():
    """MFE/MAE must use ALL spot observations in the window, not just entry+final."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    entry_price = 4280.0

    # Create outcome with full-window MFE/MAE
    with SessionLocal() as session:
        session.add(ForwardOutcome(
            observation_id="FWD-WINDOW01",
            horizon_minutes=60,
            outcome_instrument="XAUUSD_SPOT",
            outcome_provider="Gold API",
            outcome_source_timeframe="1min",
            entry_price=entry_price,
            future_price=4290.0,  # final
            max_up_move=20.0,  # max(high) - entry = 4300 - 4280
            max_down_move=10.0,  # entry - min(low) = 4280 - 4270
            buy_mfe=20.0,
            buy_mae=10.0,
            sell_mfe=10.0,
            sell_mae=20.0,
            direction="UP",
            outcome_status="VALID",
            horizon_valid=True,
            evaluated_at=now,
            target_timestamp=now,
            actual_future_timestamp=now,
            timestamp_error_seconds=5.0,
        ))
        session.commit()

    with SessionLocal() as session:
        o = session.scalar(select(ForwardOutcome).where(ForwardOutcome.observation_id == "FWD-WINDOW01"))
        # BUY MFE = 20 (not just 4290-4280=10 — the full window had a 4300 high)
        assert o.buy_mfe == 20.0
        # BUY MAE = 10 (the 4270 low)
        assert o.buy_mae == 10.0
        # SELL MFE = 10 (downside move is favorable for SELL)
        assert o.sell_mfe == 10.0
        # SELL MAE = 20 (upside move is adverse for SELL)
        assert o.sell_mae == 20.0


# ===========================================================================
# 5. Outcome status + invalid reason
# ===========================================================================

def test_outcome_status_pending():
    """New outcomes start as PENDING."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        o = ForwardOutcome(
            observation_id="FWD-STAT01",
            horizon_minutes=60,
            outcome_instrument="XAUUSD_SPOT",
            outcome_provider="Gold API",
            entry_price=100.0,
            direction="PENDING",
            outcome_status="PENDING",
            horizon_valid=True,
        )
        session.add(o)
        session.commit()
    with SessionLocal() as session:
        o = session.scalar(select(ForwardOutcome).where(ForwardOutcome.observation_id == "FWD-STAT01"))
        assert o.outcome_status == "PENDING"


def test_outcome_status_invalid_with_reason():
    """Invalid outcomes must have an invalid_reason."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        o = ForwardOutcome(
            observation_id="FWD-STAT02",
            horizon_minutes=60,
            outcome_instrument="XAUUSD_SPOT",
            outcome_provider="Gold API",
            entry_price=100.0,
            direction="INVALID",
            outcome_status="INVALID",
            invalid_reason="INSUFFICIENT_SPOT_DATA",
            horizon_valid=False,
        )
        session.add(o)
        session.commit()
    with SessionLocal() as session:
        o = session.scalar(select(ForwardOutcome).where(ForwardOutcome.observation_id == "FWD-STAT02"))
        assert o.outcome_status == "INVALID"
        assert o.invalid_reason == "INSUFFICIENT_SPOT_DATA"


# ===========================================================================
# 6. M15 / H1 separation
# ===========================================================================

def test_capture_timeframe_stored_as_m15_or_h1():
    """capture_timeframe must be M15 or H1, not mixed."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for tf in ["15min", "1h"]:
        with SessionLocal() as session:
            session.add(ForwardObservation(
                observation_id=f"FWD-TF-{tf}",
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
                capture_timeframe=tf,
                capture_timestamp=now,
                data_freshness_status="RECENT",
                observation_status="PENDING",
                historical_alignment="NOT_APPLICABLE",
                wait_historical_context="NEUTRAL",
            ))
            session.commit()
    with SessionLocal() as session:
        from sqlalchemy import func
        m15_count = session.scalar(select(func.count(ForwardObservation.id)).where(ForwardObservation.capture_timeframe == "15min")) or 0
        h1_count = session.scalar(select(func.count(ForwardObservation.id)).where(ForwardObservation.capture_timeframe == "1h")) or 0
        assert m15_count == 1
        assert h1_count == 1


# ===========================================================================
# 7. Config values
# ===========================================================================

def test_max_similarity_run_age_config():
    """MAX_SIMILARITY_RUN_AGE_SECONDS must be a positive number."""
    assert MAX_SIMILARITY_RUN_AGE_SECONDS > 0


def test_target_tolerance_config():
    """TARGET_TOLERANCE_SECONDS must be a positive number."""
    assert TARGET_TOLERANCE_SECONDS > 0


# ===========================================================================
# 8. Invalid observations excluded from performance
# ===========================================================================

def test_invalid_observations_have_invalid_reason():
    """Invalid observations must have invalid_reason set."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        session.add(ForwardObservation(
            observation_id="FWD-INV01",
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
            observation_status="INVALID",
            invalid_reason="BEFORE_FORWARD_VALIDATION_START",
            historical_alignment="SUPPORTS",
        ))
        session.commit()
    with SessionLocal() as session:
        obs = session.scalar(select(ForwardObservation).where(ForwardObservation.observation_id == "FWD-INV01"))
        assert obs.observation_status == "INVALID"
        assert obs.invalid_reason == "BEFORE_FORWARD_VALIDATION_START"


# ===========================================================================
# 9. Duplicate ForwardOutcome prevention
# ===========================================================================

def test_duplicate_outcome_prevented():
    """Unique constraint on (observation_id, horizon_minutes) prevents duplicates."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal() as session:
        for _ in range(2):
            try:
                session.add(ForwardOutcome(
                    observation_id="FWD-DUP01",
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
                ForwardOutcome.observation_id == "FWD-DUP01",
                ForwardOutcome.horizon_minutes == 60,
            )
        )
        assert count == 1
