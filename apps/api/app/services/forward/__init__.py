"""Phase 5: Forward validation service.

Captures immutable snapshots of the Brain's state at deterministic events
(M15/H1 candle closes), evaluates future outcomes prospectively, and
compares technical decisions against historical evidence.

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

from sqlalchemy import and_, func, select

from app.db.models import (
    ForwardAuditLog,
    ForwardObservation,
    ForwardOutcome,
    SystemConfig,
)
from app.db.session import SessionLocal
from app.engine.candles import INTERVALS, get_candles
from app.services.learning.config import DEFAULT_CONFIG

FORWARD_HORIZONS = (15, 30, 60, 120, 240, 480, 1440)
FORWARD_SAMPLE_THRESHOLDS = {"INSUFFICIENT": 30, "EARLY": 100, "MODERATE": 300}


def new_observation_id() -> str:
    return f"FWD-{uuid.uuid4().hex[:8].upper()}"


def _audit(event_type: str, observation_id: str | None = None, detail: str | None = None) -> None:
    """Best-effort audit log."""
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
    """Get the system's forward_validation_started_at timestamp."""
    with SessionLocal() as session:
        row = session.get(SystemConfig, "forward_validation_started_at")
        if row is None:
            return None
        try:
            return datetime.fromisoformat(row.value).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None


def set_forward_start_date() -> datetime:
    """Set forward_validation_started_at to now (if not already set)."""
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
    capture_timeframe: str = "1h",
) -> dict:
    """Capture a new forward observation at the current market state.

    The observation is an immutable snapshot of:
      - Live XAUUSD_SPOT price (from Gold API)
      - Technical decision + score (from rules-v0.1, unchanged)
      - Historical similarity run_id + alignment (informational only)
      - All version info

    Returns the observation dict, or an error dict if capture was skipped.
    """
    # Ensure forward validation has started
    start_date = set_forward_start_date()

    # Get current market state from the live collector
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

    # Determine the capture timestamp (floor to the capture timeframe)
    now = datetime.now(timezone.utc)
    tf_seconds = INTERVALS.get(capture_timeframe, 3600)
    epoch = int(now.timestamp())
    floored_ts = datetime.fromtimestamp(epoch - (epoch % tf_seconds), tz=timezone.utc)

    # Dedup check — prevent duplicate observations for the same
    # (instrument, capture_timeframe, capture_timestamp, rule_version, feature_version)
    rule_version = analysis.brain_version or "rules-v0.1"
    feature_version = DEFAULT_CONFIG.feature_version
    with SessionLocal() as session:
        existing = session.scalar(
            select(ForwardObservation).where(
                ForwardObservation.live_instrument == "XAUUSD_SPOT",
                ForwardObservation.capture_timeframe == capture_timeframe,
                ForwardObservation.capture_timestamp == floored_ts.replace(tzinfo=None),
                ForwardObservation.technical_rule_version == rule_version,
                ForwardObservation.feature_version == feature_version,
            )
        )
        if existing is not None:
            _audit("capture_skipped_duplicate", detail=f"duplicate for {capture_timeframe} @ {floored_ts.isoformat()}")
            return {"error": "duplicate observation", "skipped": True, "observation_id": existing.observation_id}

    # Create the immutable observation
    obs_id = new_observation_id()
    now_naive = now.replace(tzinfo=None)

    # Extract historical similarity info from the analysis
    sim_run_id = getattr(analysis, "historical_similarity_run_id", None)
    analogue_inst = getattr(analysis, "historical_analogue_instrument", None)
    alignment = getattr(analysis, "historical_alignment", None)
    sample_size = getattr(analysis, "historical_sample_size", None)
    direction_rate = getattr(analysis, "historical_direction_rate", None)
    median_mfe = getattr(analysis, "historical_mfe", None)
    median_mae = getattr(analysis, "historical_mae", None)

    # Extract market context
    regime = analysis.regime
    # Determine session from UTC hour
    hour = now.hour
    if 0 <= hour < 7:
        session_val = "ASIA"
    elif 7 <= hour < 13:
        session_val = "EU"
    elif 13 <= hour < 21:
        session_val = "US"
    else:
        session_val = "OFF"

    # Extract TF directions
    h1_dir = None
    h4_dir = None
    d1_dir = None
    for tf in analysis.timeframes or []:
        if tf.timeframe == "1h":
            h1_dir = tf.trend
        elif tf.timeframe == "4h":
            h4_dir = tf.trend
    # D1 direction is not in timeframes list, try from analysis
    d1_dir = getattr(analysis, "historical_depth", {}).get("1day") and "available" or None

    obs = ForwardObservation(
        observation_id=obs_id,
        created_at=now_naive,
        live_instrument="XAUUSD_SPOT",
        live_provider=quote.provider or "Gold API",
        live_symbol="XAU",
        live_market_timestamp=quote.market_timestamp.replace(tzinfo=None) if quote.market_timestamp else now_naive,
        live_price=quote.price,
        technical_decision=analysis.decision,
        technical_score=analysis.technical_score or analysis.confidence,
        technical_rule_version=rule_version,
        technical_data_readiness=analysis.technical_data_readiness or analysis.readiness,
        historical_similarity_run_id=sim_run_id,
        historical_analogue_instrument=analogue_inst,
        historical_alignment=alignment,
        historical_sample_size=sample_size,
        historical_direction_rate=direction_rate,
        historical_probability_calibrated=False,
        historical_median_mfe=median_mfe,
        historical_median_mae=median_mae,
        feature_version=feature_version,
        similarity_version=DEFAULT_CONFIG.similarity_version,
        outcome_version="outcomes-v0.2",
        market_regime=regime,
        session=session_val,
        h1_direction=h1_dir,
        h4_direction=h4_dir,
        d1_direction=d1_dir,
        instrument_consistency=analysis.instrument_consistency,
        capture_timeframe=capture_timeframe,
        capture_timestamp=floored_ts.replace(tzinfo=None),
        data_freshness_status=quote.status,
        observation_status="PENDING",
    )

    with SessionLocal() as session:
        session.add(obs)
        session.commit()

    _audit("observation_captured", observation_id=obs_id,
           detail=f"{analysis.decision} @ {quote.price:.2f} score={analysis.technical_score or analysis.confidence}")

    return {
        "observation_id": obs_id,
        "status": "PENDING",
        "live_price": quote.price,
        "technical_decision": analysis.decision,
        "technical_score": analysis.technical_score or analysis.confidence,
        "historical_similarity_run_id": sim_run_id,
        "historical_alignment": alignment,
        "capture_timestamp": floored_ts.isoformat(),
    }


async def evaluate_pending_observations() -> dict:
    """Find PENDING/PARTIALLY_EVALUATED observations and evaluate any horizon
    whose required market time has elapsed. Uses XAUUSD_SPOT data only.

    Returns a summary of evaluations performed.
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
            entry_ts = obs.live_market_timestamp.replace(tzinfo=timezone.utc) if obs.live_market_timestamp.tzinfo is None else obs.live_market_timestamp
            entry_price = obs.live_price

            for horizon in FORWARD_HORIZONS:
                # Check if this horizon already has an outcome
                existing_outcome = session.scalar(
                    select(ForwardOutcome).where(
                        ForwardOutcome.observation_id == obs.observation_id,
                        ForwardOutcome.horizon_minutes == horizon,
                    )
                )
                if existing_outcome is not None and existing_outcome.direction != "PENDING":
                    continue  # already evaluated

                target_ts = entry_ts + timedelta(minutes=horizon)
                if target_ts > now:
                    continue  # horizon hasn't matured yet

                # Fetch XAUUSD_SPOT candles for the forward window
                # Use the best available TF from the sampled candles
                spot_candles = await get_candles("1min", 5000, "XAU/USD")
                spot_forward = [c for c in spot_candles
                                if getattr(c, "instrument", "") == "XAUUSD_SPOT"
                                and c.timestamp > entry_ts]

                if not spot_forward:
                    # No spot data — try H1 sampled candles
                    h1_candles = await get_candles("1h", 5000, "XAU/USD")
                    spot_forward = [c for c in h1_candles
                                     if getattr(c, "instrument", "") == "XAUUSD_SPOT"
                                     and c.timestamp > entry_ts]

                if not spot_forward:
                    if existing_outcome is None:
                        session.add(ForwardOutcome(
                            observation_id=obs.observation_id,
                            horizon_minutes=horizon,
                            outcome_instrument="XAUUSD_SPOT",
                            outcome_provider="Gold API",
                            outcome_source_timeframe="1min",
                            entry_price=entry_price,
                            direction="PENDING",
                            horizon_valid=False,
                        ))
                    continue

                # Select best candles for this horizon
                tf_seconds = INTERVALS.get("1min", 60)
                candles_needed = max(1, horizon * 60 // tf_seconds)

                if len(spot_forward) < candles_needed:
                    # Not enough forward data yet
                    if existing_outcome is None:
                        session.add(ForwardOutcome(
                            observation_id=obs.observation_id,
                            horizon_minutes=horizon,
                            outcome_instrument="XAUUSD_SPOT",
                            outcome_provider="Gold API",
                            outcome_source_timeframe="1min",
                            entry_price=entry_price,
                            direction="PENDING",
                            horizon_valid=False,
                        ))
                    continue

                window = spot_forward[:candles_needed]
                future_price = window[-1].close
                absolute_change = future_price - entry_price
                percentage_change = (absolute_change / entry_price) * 100.0 if entry_price > 0 else None
                max_high = max(c.high for c in window)
                min_low = min(c.low for c in window)
                max_up_move = round(max_high - entry_price, 4)
                max_down_move = round(entry_price - min_low, 4)

                # Direction classification (using entry ATR if available)
                # For forward obs, use a fixed 0.5% threshold as fallback
                pct_threshold = 0.0005 * entry_price
                if abs(absolute_change) < pct_threshold:
                    direction = "NEUTRAL"
                else:
                    direction = "UP" if absolute_change > 0 else "DOWN"

                # Directional MFE/MAE
                buy_mfe = max_up_move
                buy_mae = abs(max_down_move)
                sell_mfe = abs(max_down_move)
                sell_mae = max_up_move

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
                        horizon_valid=True,
                        evaluated_at=now.replace(tzinfo=None),
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
                    existing_outcome.horizon_valid = True
                    existing_outcome.evaluated_at = now.replace(tzinfo=None)

                evaluated_count += 1
                _audit(f"{horizon}m_evaluated", observation_id=obs.observation_id,
                       detail=f"direction={direction} change={absolute_change:.2f}")

            # Check if all horizons are resolved
            outcomes = session.scalars(
                select(ForwardOutcome).where(
                    ForwardOutcome.observation_id == obs.observation_id
                )
            ).all()
            all_resolved = all(o.direction and o.direction != "PENDING" for o in outcomes)
            if all_resolved and len(outcomes) == len(FORWARD_HORIZONS):
                obs.observation_status = "COMPLETE"
                completed_count += 1
                _audit("observation_complete", observation_id=obs.observation_id)
            elif evaluated_count > 0:
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

        by_decision = session.execute(
            select(
                ForwardObservation.technical_decision,
                func.count(ForwardObservation.id),
            ).group_by(ForwardObservation.technical_decision)
        ).all()

        by_alignment = session.execute(
            select(
                ForwardObservation.historical_alignment,
                func.count(ForwardObservation.id),
            ).group_by(ForwardObservation.historical_alignment)
        ).all()

        # Per-horizon outcome counts
        by_horizon = session.execute(
            select(
                ForwardOutcome.horizon_minutes,
                func.count(ForwardOutcome.id),
                func.sum(func.iif(ForwardOutcome.direction != "PENDING", 1, 0)),
            ).group_by(ForwardOutcome.horizon_minutes)
        ).all()

        start_date = get_forward_start_date()

    return {
        "total_observations": total,
        "pending": pending,
        "partially_evaluated": partial,
        "complete": complete,
        "invalid": invalid,
        "by_decision": [{"decision": d, "count": c} for d, c in by_decision],
        "by_alignment": [{"alignment": a or "NONE", "count": c} for a, c in by_alignment],
        "by_horizon": [
            {"horizon_minutes": h, "total": t, "evaluated": e or 0}
            for h, t, e in by_horizon
        ],
        "forward_validation_started_at": start_date.isoformat() if start_date else None,
        "probability_calibrated": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_observations(limit: int = 50, offset: int = 0) -> list[dict]:
    """Get paginated list of forward observations."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(ForwardObservation)
            .order_by(ForwardObservation.created_at.desc())
            .limit(limit).offset(offset)
        ).all()
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
                "historical_analogue_instrument": r.historical_analogue_instrument,
                "historical_sample_size": r.historical_sample_size,
                "historical_direction_rate": r.historical_direction_rate,
                "historical_median_mfe": r.historical_median_mfe,
                "historical_median_mae": r.historical_median_mae,
                "market_regime": r.market_regime,
                "session": r.session,
                "capture_timeframe": r.capture_timeframe,
                "capture_timestamp": r.capture_timestamp.isoformat() if r.capture_timestamp else None,
                "observation_status": r.observation_status,
                "data_freshness_status": r.data_freshness_status,
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
                    "evaluated_at": o.evaluated_at.isoformat() if o.evaluated_at else None,
                }
                for o in outcomes
            ],
        }


def get_forward_performance(horizon_minutes: int = 60) -> dict:
    """Get forward performance metrics for one horizon.

    Groups by technical decision + historical alignment and reports
    directional rates with Wilson intervals.
    """
    from app.services.learning.statistics import wilson_interval

    with SessionLocal() as session:
        # Get all evaluated outcomes at this horizon
        rows = session.execute(
            select(
                ForwardObservation.technical_decision,
                ForwardObservation.historical_alignment,
                ForwardOutcome.direction,
                ForwardOutcome.buy_mfe,
                ForwardOutcome.buy_mae,
                ForwardOutcome.sell_mfe,
                ForwardOutcome.sell_mae,
                ForwardOutcome.percentage_change,
                ForwardOutcome.max_up_move,
                ForwardOutcome.max_down_move,
            ).join(
                ForwardObservation,
                ForwardObservation.observation_id == ForwardOutcome.observation_id,
            ).where(
                ForwardOutcome.horizon_minutes == horizon_minutes,
                ForwardOutcome.direction != "PENDING",
                ForwardOutcome.horizon_valid.is_(True),
            )
        ).all()

    # Group by (decision, alignment)
    groups: dict[tuple, list] = {}
    for row in rows:
        key = (row[0], row[1] or "NONE")
        groups.setdefault(key, []).append(row)

    result = {
        "horizon_minutes": horizon_minutes,
        "total_evaluated": len(rows),
        "sample_quality": "INSUFFICIENT" if len(rows) < 30 else ("EARLY" if len(rows) < 100 else ("MODERATE" if len(rows) < 300 else "STRONGER_EVIDENCE")),
        "probability_calibrated": False,
        "groups": [],
    }

    for (decision, alignment), group_rows in sorted(groups.items()):
        n = len(group_rows)
        if n == 0:
            continue

        # Directional rates
        if decision in ("BUY", "SELL"):
            favorable = "UP" if decision == "BUY" else "DOWN"
            favorable_count = sum(1 for r in group_rows if r[2] == favorable)
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
                "median_mfe": round(sorted([r[3] for r in group_rows if r[3] is not None])[n // 2], 4) if n > 0 and any(r[3] is not None for r in group_rows) else None,
                "median_mae": round(sorted([r[4] for r in group_rows if r[4] is not None])[n // 2], 4) if n > 0 and any(r[4] is not None for r in group_rows) else None,
            })
        elif decision == "WAIT":
            moves = [abs(r[7] or 0) for r in group_rows]
            ups = [r[8] for r in group_rows if r[8] is not None]
            downs = [r[9] for r in group_rows if r[9] is not None]
            result["groups"].append({
                "group": f"Technical WAIT + historical {alignment}",
                "decision": decision,
                "alignment": alignment,
                "sample_size": n,
                "median_absolute_move": round(sorted(moves)[n // 2], 4) if moves else None,
                "median_max_up": round(sorted(ups)[len(ups) // 2], 4) if ups else None,
                "median_max_down": round(sorted(downs)[len(downs) // 2], 4) if downs else None,
            })

    # Also add overall groups (all alignments combined)
    for decision in ("BUY", "SELL", "WAIT"):
        decision_rows = [r for r in rows if r[0] == decision]
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
            favorable_count = sum(1 for r in decision_rows if r[2] == favorable)
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
                "median_mfe": round(sorted([r[3] for r in decision_rows if r[3] is not None])[n // 2], 4) if any(r[3] is not None for r in decision_rows) else None,
                "median_mae": round(sorted([r[4] for r in decision_rows if r[4] is not None])[n // 2], 4) if any(r[4] is not None for r in decision_rows) else None,
            })
        elif decision == "WAIT":
            moves = [abs(r[7] or 0) for r in decision_rows]
            result["groups"].append({
                "group": f"Technical WAIT overall",
                "decision": decision,
                "alignment": "ALL",
                "sample_size": n,
                "median_absolute_move": round(sorted(moves)[n // 2], 4) if moves else None,
            })

    return result
