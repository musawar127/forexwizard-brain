"""Phase 4: orchestrator (optimized).

build_states() — batch builder. Fetches H1/H4/D1 candles ONCE upfront
(O(1) DB calls) and slices them per-state, avoiding the catastrophic
N+1 query pattern. Iterates over historical H1 candles, builds a state
per candle (with no-look-ahead), computes outcomes at each horizon,
and persists everything to historical_market_states + historical_outcomes.

current_similarity() — live matcher. Builds a feature vector for the
current market state, finds the nearest K historical neighbors (with
temporal de-duplication), aggregates their outcome statistics, and
returns the full result.

learning_status() — overall status for /api/learning/status.
"""

from __future__ import annotations

import asyncio
import bisect
import json
from datetime import datetime, timezone

from sqlalchemy import delete, func, select

from app.db.models import (
    HistoricalMarketState,
    HistoricalOutcome,
    SimilarityRun,
)
from app.db.session import SessionLocal
from app.engine.candles import INTERVALS, get_candles
from app.engine.indicators import atr as calc_atr
from app.services.learning.config import DEFAULT_CONFIG, HORIZON_MINUTES, LearningConfig
from app.services.learning.outcomes import OutcomeCalculator
from app.services.learning.similarity import NeighborMatch, SimilarityEngine
from app.services.learning.states import FeatureVector, HistoricalStateBuilder
from app.services.learning.statistics import StatisticsAggregator

# Cache for current_similarity results — short TTL to avoid recomputing
# on every frontend refresh tick.
_SIMILARITY_CACHE: dict[tuple[str, int], tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 30.0


async def build_states(
    *,
    instrument: str = "GC_FRONT_MONTH",
    base_timeframe: str = "1h",
    config: LearningConfig | None = None,
    batch_limit: int = 5000,
    clear_existing: bool = False,
) -> dict:
    """Batch-build historical market states + outcomes.

    Phase 4 optimized: fetches H1/H4/D1 candle lists ONCE upfront and
    slices per-state, avoiding the N+1 query pattern that made the
    naive version hang on 11k candles.
    """
    cfg = config or DEFAULT_CONFIG
    builder = HistoricalStateBuilder(base_timeframe=base_timeframe, min_history=cfg.min_history_candles)
    outcome_calc = OutcomeCalculator(neutral_x=cfg.neutral_x)

    if clear_existing:
        with SessionLocal() as session:
            session.execute(
                delete(HistoricalOutcome).where(
                    HistoricalOutcome.state_id.in_(
                        select(HistoricalMarketState.id).where(
                            HistoricalMarketState.instrument == instrument
                        )
                    )
                )
            )
            session.execute(
                delete(HistoricalMarketState).where(
                    HistoricalMarketState.instrument == instrument
                )
            )
            session.commit()

    # ----- PHASE 4 PERFORMANCE OPTIMIZATION -----
    # Fetch H1/H4/D1 candles ONCE upfront — slice per-state to avoid N+1.
    all_h1 = await get_candles("1h", 5000, "XAU/USD")
    all_h4 = await get_candles("4h", 5000, "XAU/USD")
    all_d1 = await get_candles("1day", 5000, "XAU/USD")

    h1_instrument = [c for c in all_h1 if getattr(c, "instrument", "") == instrument]
    h4_instrument = [c for c in all_h4 if getattr(c, "instrument", "") == instrument]
    d1_instrument = [c for c in all_d1 if getattr(c, "instrument", "") == instrument]

    # Pre-compute sorted timestamp arrays for bisect-based slicing (O(log N) per slice).
    h1_ts_sorted = [c.timestamp for c in h1_instrument]  # already sorted asc by get_candles
    h1_forward_ts_sorted = h1_ts_sorted  # alias

    # Only build states for candles AFTER min_history_candles (need enough
    # lookback for indicators).
    start_idx = max(cfg.min_history_candles, 0)
    target_candles = h1_instrument[start_idx:][:batch_limit]

    states_built = 0
    outcomes_built = 0
    states_with_insufficient_forward_data: dict[int, int] = {h: 0 for h in HORIZON_MINUTES}
    earliest_ts: datetime | None = None
    latest_ts: datetime | None = None
    now_utc = datetime.now(timezone.utc)
    skipped_recent = 0  # candles too recent to have 24h forward data

    # Single DB session for the whole build (massive speedup vs per-state session).
    session = SessionLocal()
    try:
        for c in target_candles:
            ts_utc = c.timestamp if c.timestamp.tzinfo else c.timestamp.replace(tzinfo=timezone.utc)
            # Skip the last 24h of candles — they won't have full outcome windows
            # at the 24h horizon.
            if (now_utc - ts_utc).total_seconds() < 24 * 3600 + 3600:
                skipped_recent += 1
                continue

            # Bisect-based slice for candles <= T (O(log N), not O(N))
            idx = bisect.bisect_right(h1_ts_sorted, ts_utc)
            candle_window = h1_instrument[:idx]
            if len(candle_window) < cfg.min_history_candles:
                continue
            fv = await builder.build_at(
                instrument, ts_utc,
                candles=h1_instrument,
                h1_candles=h1_instrument,
                h4_candles=h4_instrument,
                d1_candles=d1_instrument,
            )
            if fv is None:
                continue

            # Compute ATR for outcome direction classification
            atr_val = calc_atr(candle_window[-15:], 14) if len(candle_window) >= 15 else None

            # Idempotency check + insert in the same session
            existing = session.scalar(
                select(HistoricalMarketState.id).where(
                    HistoricalMarketState.instrument == instrument,
                    HistoricalMarketState.base_timeframe == base_timeframe,
                    HistoricalMarketState.timestamp == ts_utc.replace(tzinfo=None),
                )
            )
            if existing is not None:
                continue  # idempotent — skip already-built states

            state_row = HistoricalMarketState(
                instrument=instrument,
                provider=c.provider,
                provider_symbol=c.provider_symbol,
                timestamp=ts_utc.replace(tzinfo=None),
                base_timeframe=base_timeframe,
                price=c.close,
                ema_fast=None, ema_slow=None,
                ema_distance_pct=None,
                rsi=fv.rsi_normalized * 100.0 if fv.rsi_normalized is not None else None,
                atr=atr_val,
                atr_pct=fv.atr_pct,
                trend=fv.trend,
                market_regime=fv.market_regime,
                distance_to_support_atr=fv.distance_to_support_atr,
                distance_to_resistance_atr=fv.distance_to_resistance_atr,
                swing_structure=fv.swing_structure,
                higher_high=None, higher_low=None, lower_high=None, lower_low=None,
                volatility_percentile=fv.volatility_percentile,
                session=fv.session,
                h1_direction=fv.h1_direction,
                h4_direction=fv.h4_direction,
                d1_direction=fv.d1_direction,
                timeframe_alignment_score=fv.timeframe_alignment_score,
                source_quality="HEALTHY",
                feature_version=cfg.feature_version,
                similarity_version=cfg.similarity_version,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            session.add(state_row)
            session.flush()  # flush to get state_row.id without committing
            state_id = state_row.id

            states_built += 1
            if earliest_ts is None or ts_utc < earliest_ts:
                earliest_ts = ts_utc
            if latest_ts is None or ts_utc > latest_ts:
                latest_ts = ts_utc

            # Compute outcomes at each horizon (forward-only, no look-ahead)
            # Bisect slice for forward candles
            forward_idx = bisect.bisect_right(h1_ts_sorted, ts_utc)
            h1_forward = h1_instrument[forward_idx:]
            outcomes = await _compute_outcomes_inline(
                ts_utc=ts_utc,
                state_price=c.close,
                state_atr=atr_val,
                h1_forward=h1_forward,
                horizons=HORIZON_MINUTES,
                neutral_x=cfg.neutral_x,
            )
            for h, w in outcomes.items():
                if w.direction is None or w.direction == "NULL":
                    states_with_insufficient_forward_data[h] += 1
                session.add(HistoricalOutcome(
                    state_id=state_id,
                    horizon_minutes=h,
                    future_price=w.future_price,
                    absolute_change=w.absolute_change,
                    percentage_change=w.percentage_change,
                    mfe=w.mfe,
                    mae=w.mae,
                    maximum_up_move=w.maximum_up_move,
                    maximum_down_move=w.maximum_down_move,
                    direction=w.direction,
                    computed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                ))
                outcomes_built += 1

            # Commit periodically (every 25 states) to avoid massive
            # transaction overhead but also avoid per-row commits.
            if states_built % 25 == 0:
                session.commit()
                await asyncio.sleep(0)  # yield to event loop
        # Final commit
        session.commit()
    finally:
        session.close()

    # Effective historical range per horizon — based on what data was actually used.
    effective_range = {
        "15m": "5.5d (M1) or 2y (H1-derived)",
        "30m": "5.5d (M1) or 2y (H1-derived)",
        "1h": "2y (H1)",
        "2h": "2y (H1)",
        "4h": "2y (H1)",
        "8h": "2y (H1)",
        "24h": "2y (H1) or 10y (D1)",
    }

    return {
        "instrument": instrument,
        "base_timeframe": base_timeframe,
        "feature_version": cfg.feature_version,
        "similarity_version": cfg.similarity_version,
        "states_built": states_built,
        "outcomes_built": outcomes_built,
        "skipped_recent": skipped_recent,
        "earliest_state": earliest_ts.isoformat() if earliest_ts else None,
        "latest_state": latest_ts.isoformat() if latest_ts else None,
        "states_with_insufficient_forward_data": states_with_insufficient_forward_data,
        "effective_range_per_horizon": effective_range,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


def _override_multi_tf_directions(
    fv: FeatureVector,
    h1_instrument: list,
    h4_instrument: list,
    d1_instrument: list,
    ts_utc: datetime,
) -> FeatureVector:
    """Re-compute multi-TF directions at T using pre-fetched candle lists
    (avoiding the builder's per-state DB fetch)."""
    builder = HistoricalStateBuilder()
    h1_at_t = [c for c in h1_instrument if c.timestamp <= ts_utc]
    h4_at_t = [c for c in h4_instrument if c.timestamp <= ts_utc]
    d1_at_t = [c for c in d1_instrument if c.timestamp <= ts_utc]
    h1_dir = builder._trend(h1_at_t) if len(h1_at_t) >= 6 else None
    h4_dir = builder._trend(h4_at_t) if len(h4_at_t) >= 6 else None
    d1_dir = builder._trend(d1_at_t) if len(d1_at_t) >= 6 else None
    return FeatureVector(
        rsi_normalized=fv.rsi_normalized,
        ema_distance_atr=fv.ema_distance_atr,
        atr_pct=fv.atr_pct,
        distance_to_support_atr=fv.distance_to_support_atr,
        distance_to_resistance_atr=fv.distance_to_resistance_atr,
        volatility_percentile=fv.volatility_percentile,
        timeframe_alignment_score=fv.timeframe_alignment_score,
        trend=fv.trend,
        market_regime=fv.market_regime,
        swing_structure=fv.swing_structure,
        h1_direction=h1_dir,
        h4_direction=h4_dir,
        d1_direction=d1_dir,
        session=fv.session,
    )


async def _compute_outcomes_inline(
    *,
    ts_utc: datetime,
    state_price: float,
    state_atr: float | None,
    h1_forward: list,
    horizons: tuple[int, ...],
    neutral_x: float,
) -> dict[int, "OutcomeWindow"]:
    """Inline outcome calculator using pre-fetched forward H1 candles
    (avoids per-state DB fetch in OutcomeCalculator)."""
    from app.services.learning.outcomes import OutcomeWindow
    h1_seconds = INTERVALS["1h"]
    results: dict[int, OutcomeWindow] = {}
    for horizon in horizons:
        candles_needed = max(1, horizon * 60 // h1_seconds)
        if len(h1_forward) < candles_needed:
            results[horizon] = OutcomeWindow(
                horizon_minutes=horizon,
                future_price=None, absolute_change=None, percentage_change=None,
                mfe=None, mae=None, maximum_up_move=None, maximum_down_move=None,
                direction="NULL",
            )
            continue
        window = h1_forward[:candles_needed]
        future_price = window[-1].close
        absolute_change = future_price - state_price
        percentage_change = (absolute_change / state_price) * 100.0 if state_price > 0 else None
        max_high = max(c.high for c in window)
        min_low = min(c.low for c in window)
        mfe = round(max_high - state_price, 4)
        mae = round(state_price - min_low, 4)
        # Direction classification — volatility-aware
        if state_atr is not None and state_atr > 0:
            normalized = abs(absolute_change) / state_atr
            if normalized < neutral_x:
                direction = "NEUTRAL"
            else:
                direction = "UP" if absolute_change > 0 else "DOWN"
        else:
            pct_threshold = 0.0005 * state_price
            if abs(absolute_change) < pct_threshold:
                direction = "NEUTRAL"
            else:
                direction = "UP" if absolute_change > 0 else "DOWN"
        results[horizon] = OutcomeWindow(
            horizon_minutes=horizon,
            future_price=round(future_price, 4),
            absolute_change=round(absolute_change, 4),
            percentage_change=round(percentage_change, 4) if percentage_change is not None else None,
            mfe=mfe, mae=mae,
            maximum_up_move=mfe, maximum_down_move=mae,
            direction=direction,
        )
    return results


async def current_similarity(
    *,
    instrument: str = "GC_FRONT_MONTH",
    horizon_minutes: int = 60,
    config: LearningConfig | None = None,
    technical_decision: str | None = None,
) -> dict:
    """Find historical neighbors of the current market state + report stats."""
    cfg = config or DEFAULT_CONFIG
    cache_key = (instrument, horizon_minutes)
    now_ts = asyncio.get_event_loop().time()
    if cache_key in _SIMILARITY_CACHE:
        cached_at, cached_payload = _SIMILARITY_CACHE[cache_key]
        if now_ts - cached_at < _CACHE_TTL_SECONDS:
            return cached_payload

    builder = HistoricalStateBuilder(base_timeframe=cfg.base_timeframe, min_history=cfg.min_history_candles)
    engine = SimilarityEngine(config=cfg)
    stats_agg = StatisticsAggregator()

    # Build current feature vector — pre-fetch H1/H4/D1 ONCE.
    all_h1 = await get_candles(cfg.base_timeframe, 5000, "XAU/USD")
    all_h4 = await get_candles("4h", 5000, "XAU/USD")
    all_d1 = await get_candles("1day", 5000, "XAU/USD")
    h1_instrument = [c for c in all_h1 if getattr(c, "instrument", "") == instrument]
    h4_instrument = [c for c in all_h4 if getattr(c, "instrument", "") == instrument]
    d1_instrument = [c for c in all_d1 if getattr(c, "instrument", "") == instrument]
    if not h1_instrument:
        return {
            "instrument": instrument,
            "error": "no historical candles for this instrument — run /api/learning/build-states first",
            "horizon_minutes": horizon_minutes,
        }
    latest_candle = h1_instrument[-1]
    current_ts = latest_candle.timestamp if latest_candle.timestamp.tzinfo else latest_candle.timestamp.replace(tzinfo=timezone.utc)
    current_fv = await builder.build_at(
        instrument, current_ts,
        candles=h1_instrument,
        h1_candles=h1_instrument,
        h4_candles=h4_instrument,
        d1_candles=d1_instrument,
    )
    if current_fv is None:
        return {
            "instrument": instrument,
            "error": "insufficient history to build current state feature vector",
            "horizon_minutes": horizon_minutes,
        }

    # Load candidate states for this instrument only.
    with SessionLocal() as session:
        candidate_rows = session.scalars(
            select(HistoricalMarketState).where(
                HistoricalMarketState.instrument == instrument,
                HistoricalMarketState.base_timeframe == cfg.base_timeframe,
            ).order_by(HistoricalMarketState.timestamp.asc())
        ).all()

    candidates: list[tuple[int, datetime, FeatureVector]] = []
    for row in candidate_rows:
        # Reconstruct FeatureVector from the stored row.
        fv = FeatureVector(
            rsi_normalized=row.rsi / 100.0 if row.rsi is not None else None,
            ema_distance_atr=None,
            atr_pct=row.atr_pct,
            distance_to_support_atr=row.distance_to_support_atr,
            distance_to_resistance_atr=row.distance_to_resistance_atr,
            volatility_percentile=row.volatility_percentile,
            timeframe_alignment_score=row.timeframe_alignment_score,
            trend=row.trend,
            market_regime=row.market_regime,
            swing_structure=row.swing_structure,
            h1_direction=row.h1_direction,
            h4_direction=row.h4_direction,
            d1_direction=row.d1_direction,
            session=row.session,
        )
        ts = row.timestamp.replace(tzinfo=timezone.utc) if row.timestamp.tzinfo is None else row.timestamp
        candidates.append((row.id, ts, fv))

    # Rank + dedup + top-K
    min_spacing_seconds = cfg.min_spacing_candles * INTERVALS[cfg.base_timeframe]
    ranked = engine.rank_and_dedup(
        current_fv, candidates,
        min_spacing_seconds=min_spacing_seconds,
        top_k=cfg.top_k,
    )

    # Fetch outcomes at the requested horizon for each kept neighbor
    neighbor_state_ids = [r[0] for r in ranked]
    outcomes_by_state_id: dict[int, dict] = {}
    if neighbor_state_ids:
        with SessionLocal() as session:
            outcome_rows = session.scalars(
                select(HistoricalOutcome).where(
                    HistoricalOutcome.state_id.in_(neighbor_state_ids),
                    HistoricalOutcome.horizon_minutes == horizon_minutes,
                )
            ).all()
            for o in outcome_rows:
                outcomes_by_state_id.setdefault(o.state_id, {})[horizon_minutes] = o

    # Build NeighborMatch list with outcome attached
    neighbors: list[NeighborMatch] = []
    outcomes_for_stats: list[dict] = []
    from app.services.learning.outcomes import OutcomeWindow
    for state_id, score, ts, fv in ranked:
        outcome_row = outcomes_by_state_id.get(state_id, {}).get(horizon_minutes)
        if outcome_row is None:
            continue
        neighbors.append(NeighborMatch(
            state_id=state_id,
            timestamp=ts,
            instrument=instrument,
            similarity_score=score,
            feature_vector=fv,
            outcome_direction=outcome_row.direction,
            outcome_future_price=outcome_row.future_price,
            outcome_mfe=outcome_row.mfe,
            outcome_mae=outcome_row.mae,
            outcome_percentage_change=outcome_row.percentage_change,
        ))
        if outcome_row.direction and outcome_row.direction != "NULL":
            outcomes_for_stats.append({
                horizon_minutes: OutcomeWindow(
                    horizon_minutes=horizon_minutes,
                    future_price=outcome_row.future_price,
                    absolute_change=outcome_row.absolute_change,
                    percentage_change=outcome_row.percentage_change,
                    mfe=outcome_row.mfe,
                    mae=outcome_row.mae,
                    maximum_up_move=outcome_row.maximum_up_move,
                    maximum_down_move=outcome_row.maximum_down_move,
                    direction=outcome_row.direction,
                )
            })

    stats_summary = stats_agg.aggregate(outcomes_for_stats, horizons=(horizon_minutes,))
    horizon_stats = stats_summary.by_horizon.get(horizon_minutes)

    alignment = None
    if horizon_stats and technical_decision:
        alignment = stats_agg.alignment_for_decision(technical_decision, horizon_stats)

    result = {
        "instrument": instrument,
        "feature_version": cfg.feature_version,
        "similarity_version": cfg.similarity_version,
        "horizon_minutes": horizon_minutes,
        "candidate_count": len(candidates),
        "sample_size": horizon_stats.sample_size if horizon_stats else 0,
        "current_state_timestamp": current_ts.isoformat(),
        "current_state_price": latest_candle.close,
        "neighbors": [
            {
                "state_id": n.state_id,
                "timestamp": n.timestamp.isoformat(),
                "instrument": n.instrument,
                "similarity_score": n.similarity_score,
                "outcome_direction": n.outcome_direction,
                "outcome_future_price": n.outcome_future_price,
                "outcome_mfe": n.outcome_mfe,
                "outcome_mae": n.outcome_mae,
                "outcome_percentage_change": n.outcome_percentage_change,
                "feature_snapshot": {
                    "trend": n.feature_vector.trend,
                    "market_regime": n.feature_vector.market_regime,
                    "rsi": n.feature_vector.rsi_normalized,
                    "atr_pct": n.feature_vector.atr_pct,
                    "swing_structure": n.feature_vector.swing_structure,
                    "h1_direction": n.feature_vector.h1_direction,
                    "h4_direction": n.feature_vector.h4_direction,
                    "d1_direction": n.feature_vector.d1_direction,
                    "session": n.feature_vector.session,
                    "distance_to_support_atr": n.feature_vector.distance_to_support_atr,
                    "distance_to_resistance_atr": n.feature_vector.distance_to_resistance_atr,
                    "volatility_percentile": n.feature_vector.volatility_percentile,
                    "timeframe_alignment_score": n.feature_vector.timeframe_alignment_score,
                },
            }
            for n in neighbors[:10]
        ],
        "statistics": _horizon_stats_to_dict(horizon_stats) if horizon_stats else None,
        "historical_alignment": alignment,
        "technical_decision": technical_decision,
        "probability_calibrated": False,  # Phase 4 invariant — always False
        "interpretation_note": (
            f"Among {horizon_stats.sample_size if horizon_stats else 0} similar {instrument} "
            f"historical states, the observed directional frequencies are descriptive statistics — "
            "they are NOT calibrated probabilities of future outcomes."
        ),
    }

    # Audit-log the run (best-effort)
    try:
        with SessionLocal() as session:
            session.add(SimilarityRun(
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
                instrument=instrument,
                feature_version=cfg.feature_version,
                similarity_version=cfg.similarity_version,
                horizon_minutes=horizon_minutes,
                candidate_count=len(candidates),
                sample_size=horizon_stats.sample_size if horizon_stats else 0,
                result_statistics_json=json.dumps(result.get("statistics") or {}),
                technical_decision=technical_decision,
                historical_alignment=alignment,
            ))
            session.commit()
    except Exception:
        pass

    _SIMILARITY_CACHE[cache_key] = (now_ts, result)
    return result


def _horizon_stats_to_dict(stats) -> dict:
    return {
        "horizon_minutes": stats.horizon_minutes,
        "sample_size": stats.sample_size,
        "up_count": stats.up_count,
        "down_count": stats.down_count,
        "neutral_count": stats.neutral_count,
        "up_rate": {
            "count": stats.up_rate.count,
            "rate": round(stats.up_rate.rate, 4),
            "wilson_lower": round(stats.up_rate.wilson_lower, 4),
            "wilson_upper": round(stats.up_rate.wilson_upper, 4),
        },
        "down_rate": {
            "count": stats.down_rate.count,
            "rate": round(stats.down_rate.rate, 4),
            "wilson_lower": round(stats.down_rate.wilson_lower, 4),
            "wilson_upper": round(stats.down_rate.wilson_upper, 4),
        },
        "neutral_rate": {
            "count": stats.neutral_rate.count,
            "rate": round(stats.neutral_rate.rate, 4),
            "wilson_lower": round(stats.neutral_rate.wilson_lower, 4),
            "wilson_upper": round(stats.neutral_rate.wilson_upper, 4),
        },
        "median_return": stats.median_return,
        "mean_return": stats.mean_return,
        "median_mfe": stats.median_mfe,
        "median_mae": stats.median_mae,
        "mean_mfe": stats.mean_mfe,
        "mean_mae": stats.mean_mae,
        "return_25th": stats.return_25th,
        "return_50th": stats.return_50th,
        "return_75th": stats.return_75th,
        "sample_quality": stats.sample_quality,
    }


async def learning_status() -> dict:
    """Top-level /api/learning/status payload."""
    with SessionLocal() as session:
        total_states = session.scalar(
            select(func.count(HistoricalMarketState.id))
        ) or 0
        total_outcomes = session.scalar(
            select(func.count(HistoricalOutcome.id))
        ) or 0
        by_instrument = session.execute(
            select(
                HistoricalMarketState.instrument,
                func.count(HistoricalMarketState.id),
                func.min(HistoricalMarketState.timestamp),
                func.max(HistoricalMarketState.timestamp),
            ).group_by(HistoricalMarketState.instrument)
        ).all()
        by_base_tf = session.execute(
            select(
                HistoricalMarketState.base_timeframe,
                func.count(HistoricalMarketState.id),
            ).group_by(HistoricalMarketState.base_timeframe)
        ).all()
        by_horizon = session.execute(
            select(
                HistoricalOutcome.horizon_minutes,
                func.count(HistoricalOutcome.id),
                func.sum(func.iif(HistoricalOutcome.direction != "NULL", 1, 0)),
            ).group_by(HistoricalOutcome.horizon_minutes)
        ).all()
        recent_runs = session.scalars(
            select(SimilarityRun).order_by(SimilarityRun.timestamp.desc()).limit(10)
        ).all()

    return {
        "total_states": total_states,
        "total_outcomes": total_outcomes,
        "by_instrument": [
            {"instrument": i, "count": c, "earliest": e.isoformat() if e else None, "latest": l.isoformat() if l else None}
            for i, c, e, l in by_instrument
        ],
        "by_base_timeframe": [
            {"base_timeframe": b, "count": c} for b, c in by_base_tf
        ],
        "by_horizon": [
            {
                "horizon_minutes": h,
                "total_outcomes": total,
                "valid_outcomes": valid if valid is not None else 0,
            }
            for h, total, valid in by_horizon
        ],
        "recent_runs": [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "instrument": r.instrument,
                "feature_version": r.feature_version,
                "similarity_version": r.similarity_version,
                "horizon_minutes": r.horizon_minutes,
                "candidate_count": r.candidate_count,
                "sample_size": r.sample_size,
                "technical_decision": r.technical_decision,
                "historical_alignment": r.historical_alignment,
            }
            for r in recent_runs
        ],
        "feature_version": DEFAULT_CONFIG.feature_version,
        "similarity_version": DEFAULT_CONFIG.similarity_version,
        "probability_calibrated": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
