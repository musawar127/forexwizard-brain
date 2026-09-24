"""Phase 5 + 5.1: Forward validation service.

Captures immutable snapshots of the Brain's state at deterministic events
(M15/H1 candle closes), evaluates future outcomes prospectively, and
compares technical decisions against historical evidence.

Phase 5.1 hardening:
  * capture_timestamp >= forward_validation_started_at enforced
  * WAIT observations use wait_historical_context, NOT historical_alignment
  * Stale similarity run rejection (MAX_SIMILARITY_RUN_AGE_SECONDS)
  * Full-window MFE/MAE from spot observations (not just entry+final)
  * Target timestamp tolerance + actual_future_timestamp
  * Market-closure-aware elapsed time (wall vs market)
  * Outcome status: PENDING / VALID / INVALID with invalid_reason
  * Data freshness: quote_age_seconds, brain_analysis_age_seconds, similarity_run_age_seconds
  * Capture only on NEW COMPLETED candle (not wall-clock interval)

CRITICAL INVARIANTS:
  * Observations are IMMUTABLE once created — no retrospective editing.
  * Live XAUUSD_SPOT outcomes use XAUUSD_SPOT data (NOT GC futures).
  * Historical alignment does NOT influence the technical decision.
  * probability_calibrated remains FALSE.
  * Forward validation starts from forward_validation_started_at — no
    backfilled fake observations.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, func, select

from app.db.models import (
    ForwardAuditLog,
    ForwardObservation,
    ForwardOutcome,
    SimilarityRun,
    SystemConfig,
)
from app.db.session import SessionLocal
from app.engine.candles import INTERVALS, get_candles
from app.services.learning.config import DEFAULT_CONFIG

# Phase 5.2: canonical capture_timeframe values.
# Internal DB uses "M15" and "H1" only — never "15min" or "1h".
CANONICAL_CAPTURE_TIMEFRAMES = {"15min": "M15", "1h": "H1"}
INTERNAL_TO_INTERVALS = {"M15": "15min", "H1": "1h"}

FORWARD_HORIZONS = (15, 30, 60, 120, 240, 480, 1440)
FORWARD_SAMPLE_THRESHOLDS = {"INSUFFICIENT": 30, "EARLY": 100, "MODERATE": 300}

# Phase 5.1: max age of similarity run relative to observation capture.
# If the run is older than this, don't attach it — use INSUFFICIENT_DATA.
MAX_SIMILARITY_RUN_AGE_SECONDS = 300  # 5 minutes

# Phase 5.1: target timestamp tolerance for outcome evaluation.
# The closest spot observation to the target end time must be within this tolerance.
TARGET_TOLERANCE_SECONDS = 120  # 2 minutes

# Phase 5.1: outcome expiry threshold — if no spot data found within this
# time after the target, the outcome becomes INVALID.
OUTCOME_EXPIRY_SECONDS = 86400  # 24 hours after target


def new_observation_id() -> str:
    return f"FWD-{uuid.uuid4().hex[:8].upper()}"


def _audit(event_type: str, observation_id: str | None = None, detail: str | None = None) -> None:
    try:
        with SessionLocal() as session:
            session.add(ForwardAuditLog(
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                event_type=event_type,
                observation_id=observation_id,
                detail=detail,
            ))
            session.commit()
    except Exception:
        pass


def get_forward_start_date() -> datetime | None:
    with SessionLocal() as session:
        row = session.get(SystemConfig, "forward_validation_started_at")
        if row is None:
            return None
        try:
            return datetime.fromisoformat(row.value).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None


def set_forward_start_date() -> datetime:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        existing = session.get(SystemConfig, "forward_validation_started_at")
        if existing is None:
            session.add(SystemConfig(
                key="forward_validation_started_at",
                value=now.isoformat(),
                updated_at=now.replace(tzinfo=None),
            ))
            session.commit()
            _audit("forward_start_date_set", detail=now.isoformat())
            return now
        try:
            return datetime.fromisoformat(existing.value).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return now


async def capture_observation(
    *,
    capture_timeframe: str = "H1",
) -> dict:
    """Capture a new forward observation at the current market state.

    Phase 5.1 hardening:
      - Enforces capture_timestamp >= forward_validation_started_at
      - Uses completed candle timestamp (floored to TF boundary)
      - For WAIT: sets historical_alignment=NOT_APPLICABLE + wait_historical_context
      - Rejects stale quotes, stale similarity runs
      - Stores quote_age_seconds, brain_analysis_age_seconds, similarity_run_age_seconds
    """
    start_date = set_forward_start_date()
    now = datetime.now(timezone.utc)

    from app.services.market_state import state
    quote = state.quote_with_freshness()
    if quote is None:
        _audit("capture_skipped", detail="no live quote available")
        return {"error": "no live quote available", "skipped": True}

    if quote.status == "STALE":
        _audit("capture_skipped", detail="feed stale")
        return {"error": "feed stale", "skipped": True, "reason": "STALE"}

    if quote.status == "NO_DATA":
        _audit("capture_skipped", detail="no data")
        return {"error": "no data", "skipped": True, "reason": "NO_DATA"}

    analysis = state.analysis
    if analysis is None:
        _audit("capture_skipped", detail="no analysis available")
        return {"error": "no analysis available", "skipped": True}

    # Phase 5.1: determine completed candle timestamp (floor to TF boundary)
    tf_seconds = INTERVALS.get(INTERNAL_TO_INTERVALS.get(capture_timeframe, capture_timeframe), 3600)
    epoch = int(now.timestamp())
    floored_ts = datetime.fromtimestamp(epoch - (epoch % tf_seconds), tz=timezone.utc)

    # Phase 5.1: enforce capture_timestamp >= forward_validation_started_at
    if start_date and floored_ts < start_date:
        _audit("capture_skipped_before_forward_start",
               detail=f"candle_ts={floored_ts.isoformat()} < start={start_date.isoformat()}")
        return {
            "error": "capture_timestamp before forward_validation_started_at",
            "skipped": True,
            "reason": "SKIPPED_BEFORE_FORWARD_START",
            "candle_timestamp": floored_ts.isoformat(),
            "forward_start": start_date.isoformat(),
        }

    # Phase 5.1: data freshness
    quote_market_ts = quote.market_timestamp or quote.received_timestamp
    if quote_market_ts.tzinfo is None:
        quote_market_ts = quote_market_ts.replace(tzinfo=timezone.utc)
    quote_age = (now - quote_market_ts).total_seconds()

    # Phase 5.1: similarity run age check
    sim_run_id = getattr(analysis, "historical_similarity_run_id", None)
    sim_run_age = None
    sim_run_created = None
    sim_run_market_ts = None
    alignment = getattr(analysis, "historical_alignment", None)
    wait_context = None

    if sim_run_id:
        with SessionLocal() as session:
            sim_run = session.scalar(
                select(SimilarityRun).where(SimilarityRun.run_id == sim_run_id)
            )
            if sim_run:
                sim_run_created = sim_run.created_at
                if sim_run_created.tzinfo is None:
                    sim_run_created = sim_run_created.replace(tzinfo=timezone.utc)
                sim_run_age = (now - sim_run_created).total_seconds()
                sim_run_market_ts = sim_run.current_market_timestamp
                if sim_run_market_ts and sim_run_market_ts.tzinfo is None:
                    sim_run_market_ts = sim_run_market_ts.replace(tzinfo=timezone.utc)

                # Phase 5.1: verify sim_run_market_timestamp <= capture time
                if sim_run_market_ts and sim_run_market_ts > now:
                    _audit("capture_skipped_future_run", detail=f"run_market_ts={sim_run_market_ts} > now")
                    sim_run_id = None
                    alignment = "INSUFFICIENT_DATA"

                # Phase 5.1: stale run check
                if sim_run_age and sim_run_age > MAX_SIMILARITY_RUN_AGE_SECONDS:
                    _audit("capture_skipped_stale_run", detail=f"run_age={sim_run_age:.0f}s > {MAX_SIMILARITY_RUN_AGE_SECONDS}s")
                    sim_run_id = None
                    alignment = "INSUFFICIENT_DATA"
            else:
                sim_run_id = None
                alignment = "INSUFFICIENT_DATA"

    # Phase 5.1: WAIT alignment semantics
    decision = analysis.decision
    if decision == "WAIT":
        # For WAIT, historical_alignment = NOT_APPLICABLE
        # wait_historical_context describes the directional context
        if alignment in ("SUPPORTS", "CONTRADICTS", "NEUTRAL"):
            wait_context = "NEUTRAL"  # WAIT SUPPORTS means dominant was NEUTRAL
        elif alignment == "INSUFFICIENT_DATA":
            wait_context = "INSUFFICIENT_DATA"
        else:
            wait_context = "INSUFFICIENT_DATA"
        alignment = "NOT_APPLICABLE"

    rule_version = analysis.brain_version or "rules-v0.1"
    feature_version = DEFAULT_CONFIG.feature_version
    similarity_version = DEFAULT_CONFIG.similarity_version

    # Dedup check — includes similarity_version per Phase 5.1
    with SessionLocal() as session:
        existing = session.scalar(
            select(ForwardObservation).where(
                ForwardObservation.live_instrument == "XAUUSD_SPOT",
                ForwardObservation.capture_timeframe == capture_timeframe,
                ForwardObservation.capture_timestamp == floored_ts.replace(tzinfo=None),
                ForwardObservation.technical_rule_version == rule_version,
                ForwardObservation.feature_version == feature_version,
                ForwardObservation.similarity_version == similarity_version,
            )
        )
        if existing is not None:
            _audit("capture_skipped_duplicate", detail=f"duplicate for {capture_timeframe} @ {floored_ts.isoformat()}")
            return {"error": "duplicate observation", "skipped": True, "observation_id": existing.observation_id}

    # Create the immutable observation
    obs_id = new_observation_id()
    now_naive = now.replace(tzinfo=None)

    # Determine session
    hour = now.hour
    if 0 <= hour < 7:
        session_val = "ASIA"
    elif 7 <= hour < 13:
        session_val = "EU"
    elif 13 <= hour < 21:
        session_val = "US"
    else:
        session_val = "OFF"

    h1_dir = None
    h4_dir = None
    for tf in analysis.timeframes or []:
        if tf.timeframe == "1h":
            h1_dir = tf.trend
        elif tf.timeframe == "4h":
            h4_dir = tf.trend

    obs = ForwardObservation(
        observation_id=obs_id,
        created_at=now_naive,
        live_instrument="XAUUSD_SPOT",
        live_provider=quote.provider or "Gold API",
        live_symbol="XAU",
        live_market_timestamp=quote.market_timestamp.replace(tzinfo=None) if quote.market_timestamp else now_naive,
        live_price=quote.price,
        technical_decision=decision,
        technical_score=analysis.technical_score or analysis.confidence,
        technical_rule_version=rule_version,
        technical_data_readiness=analysis.technical_data_readiness or analysis.readiness,
        historical_similarity_run_id=sim_run_id,
        historical_analogue_instrument=getattr(analysis, "historical_analogue_instrument", None),
        historical_alignment=alignment,
        historical_sample_size=getattr(analysis, "historical_sample_size", None),
        historical_direction_rate=getattr(analysis, "historical_direction_rate", None),
        historical_probability_calibrated=False,
        historical_median_mfe=getattr(analysis, "historical_mfe", None),
        historical_median_mae=getattr(analysis, "historical_mae", None),
        feature_version=feature_version,
        similarity_version=similarity_version,
        outcome_version="outcomes-v0.2",
        market_regime=analysis.regime,
        session=session_val,
        h1_direction=h1_dir,
        h4_direction=h4_dir,
        d1_direction=None,
        instrument_consistency=analysis.instrument_consistency,
        capture_timeframe=capture_timeframe,
        capture_timestamp=floored_ts.replace(tzinfo=None),
        data_freshness_status=quote.status,
        observation_status="PENDING",
        # Phase 5.1 fields
        wait_historical_context=wait_context if decision == "WAIT" else None,
        quote_age_seconds=round(quote_age, 1),
        brain_analysis_age_seconds=None,  # TODO: track analysis timestamp
        similarity_run_age_seconds=round(sim_run_age, 1) if sim_run_age is not None else None,
        similarity_run_created_at=sim_run_created.replace(tzinfo=None) if sim_run_created else None,
        similarity_run_market_timestamp=sim_run_market_ts.replace(tzinfo=None) if sim_run_market_ts else None,
    )

    with SessionLocal() as session:
        session.add(obs)
        session.commit()

    _audit("observation_captured", observation_id=obs_id,
           detail=f"{decision} @ {quote.price:.2f} score={analysis.technical_score or analysis.confidence}")

    return {
        "observation_id": obs_id,
        "status": "PENDING",
        "live_price": quote.price,
        "technical_decision": decision,
        "technical_score": analysis.technical_score or analysis.confidence,
        "historical_similarity_run_id": sim_run_id,
        "historical_alignment": alignment,
        "wait_historical_context": wait_context if decision == "WAIT" else None,
        "capture_timestamp": floored_ts.isoformat(),
        "quote_age_seconds": round(quote_age, 1),
        "similarity_run_age_seconds": round(sim_run_age, 1) if sim_run_age is not None else None,
    }


async def evaluate_pending_observations() -> dict:
    """Find PENDING/PARTIALLY_EVALUATED observations and evaluate matured horizons.
    Uses XAUUSD_SPOT data only. Full-window MFE/MAE calculation.
    """
    now = datetime.now(timezone.utc)
    evaluated_count = 0
    completed_count = 0

    with SessionLocal() as session:
        pending = session.scalars(
            select(ForwardObservation).where(
                ForwardObservation.observation_status.in_(["PENDING", "PARTIALLY_EVALUATED"])
            ).order_by(ForwardObservation.created_at.asc())
        ).all()

        for obs in pending:
            # Skip invalid observations
            if obs.observation_status == "INVALID":
                continue

            entry_ts = obs.live_market_timestamp.replace(tzinfo=timezone.utc) if obs.live_market_timestamp.tzinfo is None else obs.live_market_timestamp
            entry_price = obs.live_price

            for horizon in FORWARD_HORIZONS:
                # Check if outcome already exists
                existing_outcome = session.scalar(
                    select(ForwardOutcome).where(
                        ForwardOutcome.observation_id == obs.observation_id,
                        ForwardOutcome.horizon_minutes == horizon,
                    )
                )
                if existing_outcome is not None and existing_outcome.outcome_status != "PENDING":
                    continue

                target_ts = entry_ts + timedelta(minutes=horizon)
                if target_ts > now:
                    continue  # horizon hasn't matured

                # Phase 5.1: fetch full window of XAUUSD_SPOT observations
                spot_candles = await get_candles("1min", 5000, "XAU/USD")
                spot_forward = [c for c in spot_candles
                                if getattr(c, "instrument", "") == "XAUUSD_SPOT"
                                and c.timestamp > entry_ts
                                and c.timestamp <= target_ts + timedelta(seconds=TARGET_TOLERANCE_SECONDS)]

                if not spot_forward:
                    # Try H1 sampled candles as fallback
                    h1_candles = await get_candles("1h", 5000, "XAU/USD")
                    spot_forward = [c for c in h1_candles
                                     if getattr(c, "instrument", "") == "XAUUSD_SPOT"
                                     and c.timestamp > entry_ts
                                     and c.timestamp <= target_ts + timedelta(seconds=TARGET_TOLERANCE_SECONDS)]

                if not spot_forward:
                    # Phase 5.1: check if expired
                    expiry_ts = target_ts + timedelta(seconds=OUTCOME_EXPIRY_SECONDS)
                    if now > expiry_ts:
                        if existing_outcome is None:
                            session.add(ForwardOutcome(
                                observation_id=obs.observation_id,
                                horizon_minutes=horizon,
                                outcome_instrument="XAUUSD_SPOT",
                                outcome_provider="Gold API",
                                outcome_source_timeframe="1min",
                                entry_price=entry_price,
                                direction="INVALID",
                                outcome_status="INVALID",
                                invalid_reason="INSUFFICIENT_SPOT_DATA",
                                target_timestamp=target_ts.replace(tzinfo=None),
                                horizon_valid=False,
                            ))
                        else:
                            existing_outcome.outcome_status = "INVALID"
                            existing_outcome.invalid_reason = "INSUFFICIENT_SPOT_DATA"
                            existing_outcome.direction = "INVALID"
                    continue

                # Phase 5.1: find closest spot observation to target_ts
                closest = min(spot_forward, key=lambda c: abs((c.timestamp - target_ts).total_seconds()))
                actual_future_ts = closest.timestamp
                ts_error = abs((actual_future_ts - target_ts).total_seconds())

                if ts_error > TARGET_TOLERANCE_SECONDS:
                    # Too far from target — outcome stays PENDING
                    if existing_outcome is None:
                        session.add(ForwardOutcome(
                            observation_id=obs.observation_id,
                            horizon_minutes=horizon,
                            outcome_instrument="XAUUSD_SPOT",
                            outcome_provider="Gold API",
                            outcome_source_timeframe="1min",
                            entry_price=entry_price,
                            direction="PENDING",
                            outcome_status="PENDING",
                            target_timestamp=target_ts.replace(tzinfo=None),
                            actual_future_timestamp=actual_future_ts.replace(tzinfo=None),
                            timestamp_error_seconds=ts_error,
                            horizon_valid=False,
                        ))
                    continue

                # Phase 5.1: full-window MFE/MAE using ALL spot observations in the window
                window = spot_forward  # all observations from entry to target
                future_price = closest.close
                absolute_change = future_price - entry_price
                percentage_change = (absolute_change / entry_price) * 100.0 if entry_price > 0 else None

                # max_up_move = max(high) - entry_price (over full window)
                # max_down_move = entry_price - min(low) (over full window)
                max_high = max(c.high for c in window) if window else future_price
                min_low = min(c.low for c in window) if window else future_price
                max_up_move = round(max_high - entry_price, 4)
                max_down_move = round(entry_price - min_low, 4)

                # Directional MFE/MAE
                buy_mfe = max_up_move
                buy_mae = abs(max_down_move)
                sell_mfe = abs(max_down_move)
                sell_mae = max_up_move

                # Direction classification (fixed 0.05% threshold for forward obs)
                pct_threshold = 0.0005 * entry_price
                if abs(absolute_change) < pct_threshold:
                    direction = "NEUTRAL"
                else:
                    direction = "UP" if absolute_change > 0 else "DOWN"

                # Phase 5.1: market-aware elapsed time
                elapsed_wall = (actual_future_ts - entry_ts).total_seconds()
                # Market time excludes closures (simplified: subtract weekend hours)
                # For Phase 5.1, use a deterministic rule: if elapsed_wall > 1.5 * horizon * 60,
                # there was likely a closure
                elapsed_market = elapsed_wall  # simplified for Phase 5.1
                is_closure = elapsed_wall > 1.5 * horizon * 60

                if existing_outcome is None:
                    session.add(ForwardOutcome(
                        observation_id=obs.observation_id,
                        horizon_minutes=horizon,
                        outcome_instrument="XAUUSD_SPOT",
                        outcome_provider="Gold API",
                        outcome_source_timeframe="1min",
                        entry_price=entry_price,
                        future_price=round(future_price, 4),
                        absolute_change=round(absolute_change, 4),
                        percentage_change=round(percentage_change, 4) if percentage_change is not None else None,
                        max_up_move=max_up_move,
                        max_down_move=max_down_move,
                        buy_mfe=buy_mfe,
                        buy_mae=buy_mae,
                        sell_mfe=sell_mfe,
                        sell_mae=sell_mae,
                        direction=direction,
                        resolution_sufficient=True,
                        horizon_valid=not is_closure,
                        evaluated_at=now.replace(tzinfo=None),
                        outcome_status="VALID" if not is_closure else "INVALID",
                        invalid_reason="MARKET_CLOSURE" if is_closure else None,
                        target_timestamp=target_ts.replace(tzinfo=None),
                        actual_future_timestamp=actual_future_ts.replace(tzinfo=None),
                        timestamp_error_seconds=ts_error,
                        elapsed_wall_time=round(elapsed_wall, 1),
                        elapsed_market_time=round(elapsed_market, 1),
                    ))
                else:
                    existing_outcome.future_price = round(future_price, 4)
                    existing_outcome.absolute_change = round(absolute_change, 4)
                    existing_outcome.percentage_change = round(percentage_change, 4) if percentage_change is not None else None
                    existing_outcome.max_up_move = max_up_move
                    existing_outcome.max_down_move = max_down_move
                    existing_outcome.buy_mfe = buy_mfe
                    existing_outcome.buy_mae = buy_mae
                    existing_outcome.sell_mfe = sell_mfe
                    existing_outcome.sell_mae = sell_mae
                    existing_outcome.direction = direction
                    existing_outcome.resolution_sufficient = True
                    existing_outcome.horizon_valid = not is_closure
                    existing_outcome.evaluated_at = now.replace(tzinfo=None)
                    existing_outcome.outcome_status = "VALID" if not is_closure else "INVALID"
                    existing_outcome.invalid_reason = "MARKET_CLOSURE" if is_closure else None
                    existing_outcome.target_timestamp = target_ts.replace(tzinfo=None)
                    existing_outcome.actual_future_timestamp = actual_future_ts.replace(tzinfo=None)
                    existing_outcome.timestamp_error_seconds = ts_error
                    existing_outcome.elapsed_wall_time = round(elapsed_wall, 1)
                    existing_outcome.elapsed_market_time = round(elapsed_market, 1)

                evaluated_count += 1
                _audit(f"{horizon}m_evaluated", observation_id=obs.observation_id,
                       detail=f"direction={direction} change={absolute_change:.2f} ts_err={ts_error:.0f}s")

            # Update observation status based on outcome states
            outcomes = session.scalars(
                select(ForwardOutcome).where(ForwardOutcome.observation_id == obs.observation_id)
            ).all()
            valid_count = sum(1 for o in outcomes if o.outcome_status == "VALID")
            invalid_count = sum(1 for o in outcomes if o.outcome_status == "INVALID")
            pending_count = sum(1 for o in outcomes if o.outcome_status == "PENDING")

            if valid_count + invalid_count == len(FORWARD_HORIZONS) and pending_count == 0:
                obs.observation_status = "COMPLETE"
                completed_count += 1
                _audit("observation_complete", observation_id=obs.observation_id)
            elif valid_count > 0 or invalid_count > 0:
                obs.observation_status = "PARTIALLY_EVALUATED"

        session.commit()

    return {
        "evaluated": evaluated_count,
        "completed": completed_count,
        "pending_remaining": len(pending) - completed_count,
    }


def get_forward_status() -> dict:
    """Get forward validation status summary."""
    with SessionLocal() as session:
        total = session.scalar(select(func.count(ForwardObservation.id))) or 0
        pending = session.scalar(select(func.count(ForwardObservation.id)).where(
            ForwardObservation.observation_status == "PENDING"
        )) or 0
        partial = session.scalar(select(func.count(ForwardObservation.id)).where(
            ForwardObservation.observation_status == "PARTIALLY_EVALUATED"
        )) or 0
        complete = session.scalar(select(func.count(ForwardObservation.id)).where(
            ForwardObservation.observation_status == "COMPLETE"
        )) or 0
        invalid = session.scalar(select(func.count(ForwardObservation.id)).where(
            ForwardObservation.observation_status == "INVALID"
        )) or 0
        # Phase 5.1: valid observations (exclude INVALID)
        valid_total = total - invalid

        by_decision = session.execute(
            select(ForwardObservation.technical_decision, func.count(ForwardObservation.id))
            .where(ForwardObservation.observation_status != "INVALID")
            .group_by(ForwardObservation.technical_decision)
        ).all()

        by_alignment = session.execute(
            select(ForwardObservation.historical_alignment, func.count(ForwardObservation.id))
            .where(ForwardObservation.observation_status != "INVALID")
            .group_by(ForwardObservation.historical_alignment)
        ).all()

        by_capture_tf = session.execute(
            select(ForwardObservation.capture_timeframe, func.count(ForwardObservation.id))
            .where(ForwardObservation.observation_status != "INVALID")
            .group_by(ForwardObservation.capture_timeframe)
        ).all()

        by_horizon = session.execute(
            select(
                ForwardOutcome.horizon_minutes,
                func.count(ForwardOutcome.id),
                func.sum(case((ForwardOutcome.outcome_status == "VALID", 1), else_=0)),
                func.sum(case((ForwardOutcome.outcome_status == "INVALID", 1), else_=0)),
            ).group_by(ForwardOutcome.horizon_minutes)
        ).all()

        start_date = get_forward_start_date()

    return {
        "total_observations": total,
        "valid_observations": valid_total,
        "invalid_observations": invalid,
        "pending": pending,
        "partially_evaluated": partial,
        "complete": complete,
        "by_decision": [{"decision": d, "count": c} for d, c in by_decision],
        "by_alignment": [{"alignment": a or "NONE", "count": c} for a, c in by_alignment],
        "by_capture_timeframe": [{"capture_timeframe": t, "count": c} for t, c in by_capture_tf],
        "by_horizon": [
            {"horizon_minutes": h, "total": t, "valid": v or 0, "invalid": i or 0}
            for h, t, v, i in by_horizon
        ],
        "forward_validation_started_at": start_date.isoformat() if start_date else None,
        "probability_calibrated": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_observations(limit: int = 50, offset: int = 0, capture_timeframe: str | None = None) -> list[dict]:
    """Get paginated list of forward observations."""
    with SessionLocal() as session:
        query = select(ForwardObservation).order_by(ForwardObservation.created_at.desc())
        if capture_timeframe and capture_timeframe != "ALL":
            query = query.where(ForwardObservation.capture_timeframe == capture_timeframe)
        rows = session.scalars(query.limit(limit).offset(offset)).all()
        return [
            {
                "observation_id": r.observation_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "live_price": r.live_price,
                "technical_decision": r.technical_decision,
                "technical_score": r.technical_score,
                "technical_rule_version": r.technical_rule_version,
                "historical_similarity_run_id": r.historical_similarity_run_id,
                "historical_alignment": r.historical_alignment,
                "wait_historical_context": getattr(r, "wait_historical_context", None),
                "historical_analogue_instrument": r.historical_analogue_instrument,
                "historical_sample_size": r.historical_sample_size,
                "market_regime": r.market_regime,
                "session": r.session,
                "capture_timeframe": r.capture_timeframe,
                "capture_timestamp": r.capture_timestamp.isoformat() if r.capture_timestamp else None,
                "observation_status": r.observation_status,
                "invalid_reason": getattr(r, "invalid_reason", None),
                "data_freshness_status": r.data_freshness_status,
                "quote_age_seconds": getattr(r, "quote_age_seconds", None),
                "similarity_run_age_seconds": getattr(r, "similarity_run_age_seconds", None),
                "instrument_consistency": r.instrument_consistency,
                "feature_version": r.feature_version,
                "similarity_version": r.similarity_version,
                "outcome_version": r.outcome_version,
            }
            for r in rows
        ]


def get_observation(observation_id: str) -> dict | None:
    """Get a single observation with all its outcomes."""
    with SessionLocal() as session:
        obs = session.scalar(
            select(ForwardObservation).where(ForwardObservation.observation_id == observation_id)
        )
        if obs is None:
            return None
        outcomes = session.scalars(
            select(ForwardOutcome).where(ForwardOutcome.observation_id == observation_id)
            .order_by(ForwardOutcome.horizon_minutes.asc())
        ).all()

        matured_horizons = [o.horizon_minutes for o in outcomes if o.outcome_status == "VALID"]

        return {
            "observation_id": obs.observation_id,
            "created_at": obs.created_at.isoformat() if obs.created_at else None,
            "live_instrument": obs.live_instrument,
            "live_provider": obs.live_provider,
            "live_symbol": obs.live_symbol,
            "live_market_timestamp": obs.live_market_timestamp.isoformat() if obs.live_market_timestamp else None,
            "live_price": obs.live_price,
            "technical_decision": obs.technical_decision,
            "technical_score": obs.technical_score,
            "technical_rule_version": obs.technical_rule_version,
            "technical_data_readiness": obs.technical_data_readiness,
            "historical_similarity_run_id": obs.historical_similarity_run_id,
            "historical_analogue_instrument": obs.historical_analogue_instrument,
            "historical_alignment": obs.historical_alignment,
            "wait_historical_context": getattr(obs, "wait_historical_context", None),
            "historical_sample_size": obs.historical_sample_size,
            "historical_direction_rate": obs.historical_direction_rate,
            "historical_probability_calibrated": obs.historical_probability_calibrated,
            "historical_median_mfe": obs.historical_median_mfe,
            "historical_median_mae": obs.historical_median_mae,
            "feature_version": obs.feature_version,
            "similarity_version": obs.similarity_version,
            "outcome_version": obs.outcome_version,
            "market_regime": obs.market_regime,
            "session": obs.session,
            "h1_direction": obs.h1_direction,
            "h4_direction": obs.h4_direction,
            "d1_direction": obs.d1_direction,
            "instrument_consistency": obs.instrument_consistency,
            "capture_timeframe": obs.capture_timeframe,
            "capture_timestamp": obs.capture_timestamp.isoformat() if obs.capture_timestamp else None,
            "data_freshness_status": obs.data_freshness_status,
            "observation_status": obs.observation_status,
            "invalid_reason": getattr(obs, "invalid_reason", None),
            "quote_age_seconds": getattr(obs, "quote_age_seconds", None),
            "similarity_run_age_seconds": getattr(obs, "similarity_run_age_seconds", None),
            "matured_horizons": matured_horizons,
            "outcomes": [
                {
                    "horizon_minutes": o.horizon_minutes,
                    "outcome_instrument": o.outcome_instrument,
                    "outcome_source_timeframe": o.outcome_source_timeframe,
                    "entry_price": o.entry_price,
                    "future_price": o.future_price,
                    "absolute_change": o.absolute_change,
                    "percentage_change": o.percentage_change,
                    "max_up_move": o.max_up_move,
                    "max_down_move": o.max_down_move,
                    "buy_mfe": o.buy_mfe,
                    "buy_mae": o.buy_mae,
                    "sell_mfe": o.sell_mfe,
                    "sell_mae": o.sell_mae,
                    "direction": o.direction,
                    "resolution_sufficient": o.resolution_sufficient,
                    "horizon_valid": o.horizon_valid,
                    "outcome_status": getattr(o, "outcome_status", "PENDING"),
                    "invalid_reason": getattr(o, "invalid_reason", None),
                    "target_timestamp": getattr(o, "target_timestamp", None).isoformat() if getattr(o, "target_timestamp", None) else None,
                    "actual_future_timestamp": getattr(o, "actual_future_timestamp", None).isoformat() if getattr(o, "actual_future_timestamp", None) else None,
                    "timestamp_error_seconds": getattr(o, "timestamp_error_seconds", None),
                    "elapsed_wall_time": getattr(o, "elapsed_wall_time", None),
                    "elapsed_market_time": getattr(o, "elapsed_market_time", None),
                    "evaluated_at": o.evaluated_at.isoformat() if o.evaluated_at else None,
                }
                for o in outcomes
            ],
        }


def get_forward_performance(horizon_minutes: int = 60, capture_timeframe: str | None = None) -> dict:
    """Get forward performance metrics for one horizon.

    Phase 5.1: Excludes INVALID observations. Separates WAIT from BUY/SELL.
    Shows raw observations vs valid evaluated vs sample used.
    """
    from app.services.learning.statistics import wilson_interval

    with SessionLocal() as session:
        query = (
            select(
                ForwardObservation.technical_decision,
                ForwardObservation.historical_alignment,
                ForwardObservation.observation_status,
                ForwardOutcome.direction,
                ForwardOutcome.buy_mfe,
                ForwardOutcome.buy_mae,
                ForwardOutcome.sell_mfe,
                ForwardOutcome.sell_mae,
                ForwardOutcome.percentage_change,
                ForwardOutcome.max_up_move,
                ForwardOutcome.max_down_move,
                ForwardOutcome.outcome_status,
            )
            .join(ForwardObservation, ForwardObservation.observation_id == ForwardOutcome.observation_id)
            .where(
                ForwardOutcome.horizon_minutes == horizon_minutes,
                ForwardObservation.observation_status != "INVALID",
            )
        )
        if capture_timeframe and capture_timeframe != "ALL":
            query = query.where(ForwardObservation.capture_timeframe == capture_timeframe)

        rows = session.execute(query).all()

    # Count raw vs valid
    raw_count = len(rows)
    valid_rows = [r for r in rows if r[11] == "VALID"]

    # Group by (decision, alignment) — but separate WAIT
    groups: dict[tuple, list] = {}
    for row in valid_rows:
        decision = row[0]
        alignment = row[1] or "NONE"
        if decision == "WAIT":
            alignment = "ALL"  # WAIT uses its own context, not alignment
        key = (decision, alignment)
        groups.setdefault(key, []).append(row)

    result = {
        "horizon_minutes": horizon_minutes,
        "raw_observations": raw_count,
        "valid_evaluated": len(valid_rows),
        "sample_size": len(valid_rows),
        "sample_quality": "INSUFFICIENT" if len(valid_rows) < 30 else ("EARLY" if len(valid_rows) < 100 else ("MODERATE" if len(valid_rows) < 300 else "STRONGER_EVIDENCE")),
        "probability_calibrated": False,
        "groups": [],
    }

    for (decision, alignment), group_rows in sorted(groups.items()):
        n = len(group_rows)
        if n == 0:
            continue

        if decision in ("BUY", "SELL"):
            favorable = "UP" if decision == "BUY" else "DOWN"
            favorable_count = sum(1 for r in group_rows if r[3] == favorable)
            lo, hi = wilson_interval(favorable_count, n)
            result["groups"].append({
                "group": f"Technical {decision} + historical {alignment}",
                "decision": decision,
                "alignment": alignment,
                "sample_size": n,
                "favorable_direction": favorable,
                "favorable_count": favorable_count,
                "favorable_rate": round(favorable_count / n, 4),
                "wilson_lower": round(lo, 4),
                "wilson_upper": round(hi, 4),
                "median_mfe": round(sorted([r[4] for r in group_rows if r[4] is not None])[n // 2], 4) if n > 0 and any(r[4] is not None for r in group_rows) else None,
                "median_mae": round(sorted([r[5] for r in group_rows if r[5] is not None])[n // 2], 4) if n > 0 and any(r[5] is not None for r in group_rows) else None,
            })
        elif decision == "WAIT":
            moves = [abs(r[8] or 0) for r in group_rows]
            ups = [r[9] for r in group_rows if r[9] is not None]
            downs = [r[10] for r in group_rows if r[10] is not None]
            directional_count = sum(1 for r in group_rows if r[3] in ("UP", "DOWN"))
            result["groups"].append({
                "group": f"Technical WAIT",
                "decision": decision,
                "alignment": "ALL",
                "sample_size": n,
                "median_absolute_return": round(sorted(moves)[n // 2], 4) if moves else None,
                "median_max_up_move": round(sorted(ups)[len(ups) // 2], 4) if ups else None,
                "median_max_down_move": round(sorted(downs)[len(downs) // 2], 4) if downs else None,
                "directional_move_rate": round(directional_count / n, 4) if n > 0 else None,
            })

    # Add overall groups for BUY/SELL (all alignments)
    for decision in ("BUY", "SELL", "WAIT"):
        decision_rows = [r for r in valid_rows if r[0] == decision]
        n = len(decision_rows)
        if n == 0:
            result["groups"].append({
                "group": f"Technical {decision} overall",
                "decision": decision,
                "alignment": "ALL",
                "sample_size": 0,
                "note": "no observations yet",
            })
            continue

        if decision in ("BUY", "SELL"):
            favorable = "UP" if decision == "BUY" else "DOWN"
            favorable_count = sum(1 for r in decision_rows if r[3] == favorable)
            lo, hi = wilson_interval(favorable_count, n)
            result["groups"].append({
                "group": f"Technical {decision} overall",
                "decision": decision,
                "alignment": "ALL",
                "sample_size": n,
                "favorable_direction": favorable,
                "favorable_count": favorable_count,
                "favorable_rate": round(favorable_count / n, 4),
                "wilson_lower": round(lo, 4),
                "wilson_upper": round(hi, 4),
                "median_mfe": round(sorted([r[4] for r in decision_rows if r[4] is not None])[n // 2], 4) if any(r[4] is not None for r in decision_rows) else None,
                "median_mae": round(sorted([r[5] for r in decision_rows if r[5] is not None])[n // 2], 4) if any(r[5] is not None for r in decision_rows) else None,
            })
        elif decision == "WAIT":
            moves = [abs(r[8] or 0) for r in decision_rows]
            result["groups"].append({
                "group": f"Technical WAIT overall",
                "decision": decision,
                "alignment": "ALL",
                "sample_size": n,
                "median_absolute_return": round(sorted(moves)[n // 2], 4) if moves else None,
            })

    return result


# ===========================================================================
# Phase 5.2: Heartbeat + Health + Startup recovery
# ===========================================================================

import time as _time_module

_FORWARD_STARTUP_TIME = _time_module.monotonic()
_LAST_HEARTBEAT_TS = None
_LAST_M15_CAPTURE = None
_LAST_H1_CAPTURE = None
_LAST_EVALUATION = None
_LAST_ERROR = None


def _record_heartbeat() -> None:
    """Persist a lightweight heartbeat row."""
    global _LAST_HEARTBEAT_TS, _LAST_M15_CAPTURE, _LAST_H1_CAPTURE, _LAST_EVALUATION, _LAST_ERROR
    try:
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            from app.db.models import ForwardHeartbeat
            session.add(ForwardHeartbeat(
                timestamp=now.replace(tzinfo=None),
                collector_running=True,
                evaluator_running=True,
                last_valid_quote_price=None,
                last_valid_quote_ts=None,
                last_m15_capture_ts=_LAST_M15_CAPTURE.replace(tzinfo=None) if _LAST_M15_CAPTURE else None,
                last_h1_capture_ts=_LAST_H1_CAPTURE.replace(tzinfo=None) if _LAST_H1_CAPTURE else None,
                last_evaluation_ts=_LAST_EVALUATION.replace(tzinfo=None) if _LAST_EVALUATION else None,
                pending_observations=0,
                last_error=_LAST_ERROR,
                uptime_seconds=round(_time_module.monotonic() - _FORWARD_STARTUP_TIME, 1),
            ))
            session.commit()
        _LAST_HEARTBEAT_TS = now
    except Exception:
        pass


def get_forward_health() -> dict:
    """Phase 5.2: GET /api/forward/health — collector + evaluator status."""
    global _LAST_M15_CAPTURE, _LAST_H1_CAPTURE, _LAST_EVALUATION, _LAST_ERROR
    now = datetime.now(timezone.utc)
    uptime = round(_time_module.monotonic() - _FORWARD_STARTUP_TIME, 1)

    # Get latest heartbeat
    latest_hb = None
    try:
        with SessionLocal() as session:
            from app.db.models import ForwardHeartbeat
            from sqlalchemy import select as sa_sel
            hb = session.scalar(sa_sel(ForwardHeartbeat).order_by(ForwardHeartbeat.timestamp.desc()).limit(1))
            if hb:
                latest_hb = {
                    "timestamp": hb.timestamp.isoformat() if hb.timestamp else None,
                    "uptime_seconds": hb.uptime_seconds,
                    "last_m15_capture_ts": hb.last_m15_capture_ts.isoformat() if hb.last_m15_capture_ts else None,
                    "last_h1_capture_ts": hb.last_h1_capture_ts.isoformat() if hb.last_h1_capture_ts else None,
                    "last_evaluation_ts": hb.last_evaluation_ts.isoformat() if hb.last_evaluation_ts else None,
                    "pending_observations": hb.pending_observations,
                    "last_error": hb.last_error,
                }
    except Exception:
        pass

    # Get pending count
    pending_count = 0
    try:
        with SessionLocal() as session:
            from sqlalchemy import func as sa_func
            pending_count = session.scalar(
                sa_func.count(ForwardObservation.id).where(
                    ForwardObservation.observation_status.in_(["PENDING", "PARTIALLY_EVALUATED"])
                )
            ) or 0
    except Exception:
        pass

    # Determine collector status
    heartbeat_age = None
    if latest_hb and latest_hb.get("timestamp"):
        try:
            hb_ts = datetime.fromisoformat(latest_hb["timestamp"])
            if hb_ts.tzinfo is None:
                hb_ts = hb_ts.replace(tzinfo=timezone.utc)
            heartbeat_age = (now - hb_ts).total_seconds()
        except Exception:
            pass

    if heartbeat_age is not None and heartbeat_age < 120:
        collector_status = "ONLINE"
    elif heartbeat_age is not None and heartbeat_age < 600:
        collector_status = "DEGRADED"
    else:
        collector_status = "OFFLINE"

    # Spot storage monitoring
    spot_info = _get_spot_storage_info()

    return {
        "collector_status": collector_status,
        "collector_running": True,
        "evaluator_running": True,
        "uptime_seconds": uptime,
        "last_heartbeat": latest_hb.get("timestamp") if latest_hb else None,
        "last_m15_capture": latest_hb.get("last_m15_capture_ts") if latest_hb else None,
        "last_h1_capture": latest_hb.get("last_h1_capture_ts") if latest_hb else None,
        "last_outcome_evaluation": latest_hb.get("last_evaluation_ts") if latest_hb else None,
        "pending_observations": pending_count,
        "last_error": latest_hb.get("last_error") if latest_hb else None,
        "spot_storage": spot_info,
        "forward_validation_started_at": get_forward_start_date().isoformat() if get_forward_start_date() else None,
    }


def _get_spot_storage_info() -> dict:
    """Phase 5.2: monitor XAUUSD_SPOT data retention."""
    try:
        with SessionLocal() as session:
            from app.db.models import CandleRecord
            from sqlalchemy import func as sa_func
            oldest = session.scalar(sa_func.min(CandleRecord.timestamp).where(
                CandleRecord.instrument == "XAUUSD_SPOT",
                CandleRecord.interval == "1min",
            ))
            latest = session.scalar(sa_func.max(CandleRecord.timestamp).where(
                CandleRecord.instrument == "XAUUSD_SPOT",
                CandleRecord.interval == "1min",
            ))
            total = session.scalar(sa_func.count(CandleRecord.id).where(
                CandleRecord.instrument == "XAUUSD_SPOT",
                CandleRecord.interval == "1min",
            )) or 0
        retention_days = None
        if oldest and latest:
            if oldest.tzinfo is None:
                oldest = oldest.replace(tzinfo=timezone.utc)
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            retention_days = round((latest - oldest).total_seconds() / 86400, 2)
        return {
            "oldest_spot_observation": oldest.isoformat() if oldest else None,
            "latest_spot_observation": latest.isoformat() if latest else None,
            "total_spot_observations": total,
            "retention_days": retention_days,
            "data_gap_warning": retention_days is not None and retention_days < 1.0,
        }
    except Exception as exc:
        import logging
        logging.getLogger("forexwizard").warning("spot_storage query failed: %s", exc, exc_info=True)
        return {"error": "could not query spot storage"}


def detect_missed_captures() -> list[dict]:
    """Phase 5.2: detect M15/H1 completed-candle capture events that were missed
    while the server was offline. Does NOT retroactively create observations —
    just logs MISSED_FORWARD_CAPTURE in the audit log."""
    missed = []
    now = datetime.now(timezone.utc)
    start_date = get_forward_start_date()
    if not start_date:
        return missed

    for canonical_tf, interval_tf in [("M15", "15min"), ("H1", "1h")]:
        tf_seconds = INTERVALS.get(interval_tf, 900)
        # Get last capture for this TF
        last_capture = None
        try:
            with SessionLocal() as session:
                from sqlalchemy import select as sa_sel
                row = session.scalar(
                    sa_sel(ForwardObservation).where(
                        ForwardObservation.capture_timeframe == canonical_tf,
                        ForwardObservation.observation_status != "INVALID",
                    ).order_by(ForwardObservation.capture_timestamp.desc()).limit(1)
                )
                if row:
                    last_capture = row.capture_timestamp
                    if last_capture.tzinfo is None:
                        last_capture = last_capture.replace(tzinfo=timezone.utc)
        except Exception:
            pass

        if last_capture is None:
            last_capture = start_date

        # Walk forward from last_capture to now, detecting missed candle boundaries
        cursor = last_capture
        while cursor < now - timedelta(seconds=tf_seconds):
            next_candle = cursor + timedelta(seconds=tf_seconds)
            # Check if an observation exists for this candle
            exists = False
            try:
                with SessionLocal() as session:
                    from sqlalchemy import select as sa_sel
                    row = session.scalar(
                        sa_sel(ForwardObservation).where(
                            ForwardObservation.capture_timeframe == canonical_tf,
                            ForwardObservation.capture_timestamp == next_candle.replace(tzinfo=None),
                        ).limit(1)
                    )
                    exists = row is not None
            except Exception:
                pass

            if not exists and next_candle > start_date:
                missed.append({
                    "timeframe": canonical_tf,
                    "market_timestamp": next_candle.isoformat(),
                    "reason": "server offline during candle close",
                })
                _audit("MISSED_FORWARD_CAPTURE", detail=f"{canonical_tf} @ {next_candle.isoformat()}: server offline")

            cursor = next_candle

    return missed


async def startup_recovery() -> dict:
    """Phase 5.2: on backend startup, resume forward validation.
    - Detect missed captures (log only — no fake retroactive captures)
    - Evaluate any matured pending observations
    """
    global _FORWARD_STARTUP_TIME
    _FORWARD_STARTUP_TIME = _time_module.monotonic()
    
    _audit("forward_startup_recovery", detail="resuming forward validation")
    
    # Detect missed captures
    missed = detect_missed_captures()
    if missed:
        _audit("missed_captures_detected", detail=f"{len(missed)} missed capture events")
    
    # Evaluate pending observations
    eval_result = await evaluate_pending_observations()
    
    return {
        "missed_captures": len(missed),
        "evaluated": eval_result.get("evaluated", 0),
        "completed": eval_result.get("completed", 0),
    }
