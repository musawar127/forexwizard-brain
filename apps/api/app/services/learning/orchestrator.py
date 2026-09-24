"""Phase 4.1: orchestrator (rewritten for immutability + background jobs).

build_states_async() — background task. Iterates over ALL eligible H1
candles (full 2-year history, no artificial cap). Uses bisect-slicing
+ single-session DB writes + periodic checkpoints. Updates the
BuildJob row with progress. NEVER blocks the API event loop.

current_similarity() — live matcher. Persists a NEW immutable
SimilarityRun row with full snapshot (run_id, statistics, top_match_ids,
similarity distribution, effective history). BrainAnalysis.historical_
similarity_run_id points to the latest run. Old runs are NEVER updated
— new market state → new run.

learning_status() — overall status for /api/learning/status.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import uuid
from datetime import datetime, timezone
from statistics import median as py_median
from statistics import quantiles as py_quantiles

from sqlalchemy import case, delete, func, select

from app.db.models import (
    BuildJob,
    HistoricalMarketState,
    HistoricalOutcome,
    SimilarityRun,
)
from app.db.session import SessionLocal
from app.engine.candles import INTERVALS, get_candles
from app.engine.indicators import atr as calc_atr
from app.models.market import Candle
from app.services.learning.config import DEFAULT_CONFIG, HORIZON_MINUTES, LearningConfig
from app.services.learning.jobs import (
    create_job,
    detect_orphaned_jobs,
    find_running_job,
    get_job,
    mark_job_completed,
    mark_job_failed,
    new_run_id,
    update_job_progress,
)
from app.services.learning.outcomes import OutcomeCalculator
from app.services.learning.resolution_rules import (
    OUTCOME_VERSION_V02,
    HORIZON_RESOLUTION_RULES,
    is_resolution_sufficient,
    select_best_source_timeframe,
)
from app.services.learning.roll_detector import detect_roll_between, outcome_window_valid
from app.services.learning.similarity import NeighborMatch, SimilarityEngine
from app.services.learning.states import FeatureVector, HistoricalStateBuilder
from app.services.learning.statistics import (
    StatisticsAggregator,
    percentile,
    wilson_interval,
)

# In-memory registry of running asyncio tasks so we can cancel on shutdown.
_BUILD_TASKS: dict[str, asyncio.Task] = {}
# Cache for current_similarity results — short TTL.
_SIMILARITY_CACHE: dict[tuple[str, int], tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 30.0


# ===========================================================================
# Background state-builder
# ===========================================================================

def start_build_job(
    *,
    instrument: str = "GC_FRONT_MONTH",
    base_timeframe: str = "1h",
    config: LearningConfig | None = None,
    clear_existing: bool = False,
    resume_job_id: str | None = None,
) -> dict:
    """Start a background build job. Returns immediately with job_id + status.

    Phase 4.2: Single-build protection — if an equivalent build (same instrument
    + feature_version) is already RUNNING, return its job_id instead of starting
    a duplicate.

    The actual work runs in an asyncio.create_task — never blocks the
    FastAPI event loop. The dashboard and live market feed continue working.
    """
    cfg = config or DEFAULT_CONFIG

    # Phase 4.2: single-build protection
    if not resume_job_id:
        existing_job_id = find_running_job(instrument, cfg.feature_version)
        if existing_job_id:
            return {
                "job_id": existing_job_id,
                "status": "already_running",
                "eligible_total": 0,
                "message": f"A build job is already running for {instrument}/{cfg.feature_version} (job {existing_job_id}). Poll GET /api/learning/jobs/{existing_job_id} for progress.",
            }

    # Pre-compute eligible_total so the API response is accurate immediately.
    eligible_total = _count_eligible_candles(instrument, base_timeframe, cfg.min_history_candles)

    if resume_job_id:
        job_id = resume_job_id
        update_job_progress(job_id, status="running")
    else:
        job_id = create_job(
            instrument=instrument,
            base_timeframe=base_timeframe,
            feature_version=cfg.feature_version,
            eligible_total=eligible_total,
        )
        if clear_existing:
            _clear_existing_states(instrument)

    # Phase 4.2: spawn the build in a DEDICATED daemon thread so its
    # synchronous DB operations don't block the asyncio event loop AND
    # don't compete with uvicorn's shared thread pool. WAL mode +
    # busy_timeout (configured in session.py) keep reads responsive.
    import threading
    thread = threading.Thread(
        target=_run_build_job_sync,
        args=(job_id, instrument, base_timeframe, cfg),
        daemon=True,
        name=f"build-{job_id}",
    )
    thread.start()
    _BUILD_TASKS[job_id] = thread  # type: ignore
    return {"job_id": job_id, "status": "running", "eligible_total": eligible_total}


def _count_eligible_candles(instrument: str, base_timeframe: str, min_history: int) -> int:
    """Count how many eligible H1 candles exist (need >= min_history lookback)."""
    # Synchronous DB query — acceptable since this is called once at job start.
    from app.db.models import CandleRecord
    with SessionLocal() as session:
        total = session.scalar(
            select(func.count(CandleRecord.id)).where(
                CandleRecord.instrument == instrument,
                CandleRecord.interval == base_timeframe,
                CandleRecord.is_historical.is_(True),
            )
        ) or 0
    # Subtract min_history because the first min_history candles can't build states.
    return max(0, total - min_history)


def _clear_existing_states(instrument: str) -> None:
    """Delete all historical states + outcomes for this instrument.
    Use carefully — preserves audit runs."""
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


def _run_build_job_sync(job_id: str, instrument: str, base_timeframe: str, cfg: LearningConfig) -> None:
    """Phase 4.2: SYNCHRONOUS build worker that runs in a thread pool.

    This is the same logic as the async _run_build_job but fully synchronous —
    no `await` calls. Runs in a thread via loop.run_in_executor() so its DB
    operations don't block the asyncio event loop. The API stays responsive.

    Uses synchronous DB sessions + synchronous candle fetching.
    """
    import time
    started_at = time.monotonic()
    try:
        builder = HistoricalStateBuilder(base_timeframe=base_timeframe, min_history=cfg.min_history_candles)

        # Fetch H1/H4/D1 ONCE upfront (synchronous via SQLAlchemy)
        # Phase 5.5: ALSO pre-load M1/M5/M15/M30 upfront. The previous code
        # opened a new SessionLocal + ran 4 SELECT queries PER state, which
        # on PostgreSQL limited the build to ~1.7 states/sec. Pre-loading
        # upfront + bisect slicing brings it back to the SQLite-era ~50/s.
        from app.db.models import CandleRecord
        from sqlalchemy import select as sa_select
        all_h1_raw = []
        all_h4_raw = []
        all_d1_raw = []
        all_m1_raw = []
        all_m5_raw = []
        all_m15_raw = []
        all_m30_raw = []
        with SessionLocal() as session:
            for interval, target_list in [
                ("1h", all_h1_raw), ("4h", all_h4_raw), ("1day", all_d1_raw),
                ("1min", all_m1_raw), ("5min", all_m5_raw),
                ("15min", all_m15_raw), ("30min", all_m30_raw),
            ]:
                rows = session.scalars(
                    sa_select(CandleRecord).where(
                        CandleRecord.symbol == "XAU/USD",
                        CandleRecord.interval == interval,
                    ).order_by(CandleRecord.timestamp.asc())
                ).all()
                for r in rows:
                    target_list.append(Candle(
                        symbol=r.symbol, interval=r.interval,
                        timestamp=_ensure_utc(r.timestamp),
                        open=r.open, high=r.high, low=r.low, close=r.close,
                        volume=r.volume, sample_count=r.sample_count,
                        provider=r.provider,
                        is_historical=r.is_historical,
                        derivation=getattr(r, "derivation", "DIRECT") or "DIRECT",
                        provider_symbol=getattr(r, "provider_symbol", "GC=F") or "GC=F",
                        instrument=getattr(r, "instrument", "GC_FRONT_MONTH") or "GC_FRONT_MONTH",
                        source_timeframe=getattr(r, "source_timeframe", interval) or interval,
                        target_timeframe=getattr(r, "target_timeframe", interval) or interval,
                    ))

        h1_instrument = [c for c in all_h1_raw if getattr(c, "instrument", "") == instrument]
        h4_instrument = [c for c in all_h4_raw if getattr(c, "instrument", "") == instrument]
        d1_instrument = [c for c in all_d1_raw if getattr(c, "instrument", "") == instrument]
        m1_instrument = [c for c in all_m1_raw if getattr(c, "instrument", "") == instrument]
        m5_instrument = [c for c in all_m5_raw if getattr(c, "instrument", "") == instrument]
        m15_instrument = [c for c in all_m15_raw if getattr(c, "instrument", "") == instrument]
        m30_instrument = [c for c in all_m30_raw if getattr(c, "instrument", "") == instrument]
        # Pre-compute sorted timestamp lists for O(log N) bisect forward slicing
        h1_ts_sorted = [c.timestamp for c in h1_instrument]
        m1_ts_sorted = [c.timestamp for c in m1_instrument]
        m5_ts_sorted = [c.timestamp for c in m5_instrument]
        m15_ts_sorted = [c.timestamp for c in m15_instrument]
        m30_ts_sorted = [c.timestamp for c in m30_instrument]

        # Phase 4.2: Pre-load ALL existing state timestamps into a set.
        # This replaces the per-candle DB idempotency check (O(N) DB queries
        # that get slower as the session accumulates state objects) with a
        # single DB query + O(1) in-memory set lookups.
        existing_timestamps: set = set()
        with SessionLocal() as session:
            existing_rows = session.scalars(
                select(HistoricalMarketState.timestamp).where(
                    HistoricalMarketState.instrument == instrument,
                    HistoricalMarketState.base_timeframe == base_timeframe,
                    HistoricalMarketState.feature_version == cfg.feature_version,
                )
            ).all()
            for ts in existing_rows:
                existing_timestamps.add(ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc))
        print(f"[build-{job_id}] Pre-loaded {len(existing_timestamps)} existing state timestamps", flush=True)

        start_idx = max(cfg.min_history_candles, 0)
        target_candles = h1_instrument[start_idx:]
        now_utc = datetime.now(timezone.utc)

        states_built = 0
        outcomes_built = 0
        skipped_existing = 0
        excluded_roll = 0
        excluded_gaps = 0
        excluded_insufficient_future = 0
        earliest_ts: datetime | None = None
        latest_ts: datetime | None = None

        update_job_progress(job_id, status="running")

        session = SessionLocal()
        try:
            for c in target_candles:
                ts_utc = c.timestamp if c.timestamp.tzinfo else c.timestamp.replace(tzinfo=timezone.utc)
                if (now_utc - ts_utc).total_seconds() < 25 * 3600:
                    continue

                # Phase 4.2: O(1) idempotency check FIRST — skip all expensive
                # feature computation for existing states. This was the
                # critical bottleneck: the old code computed features for
                # ALL 11,432 candles (O(N²) total) before checking if the
                # state already existed.
                if ts_utc in existing_timestamps:
                    skipped_existing += 1
                    # Update checkpoint periodically even for skipped states
                    if (states_built + skipped_existing) % cfg.build_checkpoint_interval == 0:
                        elapsed = time.monotonic() - started_at
                        sps = round((states_built + skipped_existing) / elapsed, 2) if elapsed > 0 else None
                        update_job_progress(
                            job_id,
                            built=states_built,
                            skipped_existing=skipped_existing,
                            excluded_roll=excluded_roll,
                            excluded_gaps=excluded_gaps,
                            excluded_insufficient_future=excluded_insufficient_future,
                            last_checkpoint_ts=ts_utc,
                            earliest_state=earliest_ts,
                            latest_state=latest_ts,
                            elapsed_seconds=elapsed,
                            states_per_second=sps,
                        )
                    continue

                idx = bisect.bisect_right(h1_ts_sorted, ts_utc)
                candle_window = h1_instrument[:idx]
                if len(candle_window) < cfg.min_history_candles:
                    excluded_gaps += 1
                    continue

                prev_candle = h1_instrument[idx - 2] if idx >= 2 else None
                atr_val = calc_atr(candle_window[-15:], 14) if len(candle_window) >= 15 else None
                possible_roll, roll_gap, roll_reason = detect_roll_between(
                    prev_candle, c, atr_val, roll_atr_multiple=cfg.roll_atr_multiple,
                )

                # Build feature vector synchronously using the builder's helpers
                # (the builder's async build_at is replaced with inline sync logic here)
                closes = [ck.close for ck in candle_window]
                last_c = candle_window[-1]
                price = last_c.close

                from app.engine.indicators import ema, rsi, slope
                ema_fast = ema(closes, 8) if len(closes) >= 8 else None
                ema_slow = ema(closes, min(20, max(5, len(closes) // 2))) if len(closes) >= 6 else None
                rsi_val = rsi(closes, 14) if len(closes) >= 15 else None
                atr_val = calc_atr(candle_window, 14) if len(candle_window) >= 15 else None

                # Reuse the builder's helper methods (they're all sync)
                trend = builder._trend(candle_window)
                regime = builder._regime(candle_window)
                support_low, support_high = builder._support_zone(candle_window, price)
                resistance_low, resistance_high = builder._resistance_zone(candle_window, price)
                dist_support = round((price - support_high) / atr_val, 3) if support_high and atr_val and atr_val > 0 else None
                dist_resist = round((resistance_low - price) / atr_val, 3) if resistance_low and atr_val and atr_val > 0 else None
                swing_struct, _, _, _, _ = builder._swing_structure(candle_window[-20:])
                vol_pct = builder._volatility_percentile(candle_window)
                session_val = builder._session(last_c.timestamp)

                # Multi-TF direction (synchronous — using cached lists)
                h1_dir = builder._trend([ck for ck in h1_instrument if ck.timestamp <= ts_utc]) if len([ck for ck in h1_instrument if ck.timestamp <= ts_utc]) >= 6 else None
                h4_dir = builder._trend([ck for ck in h4_instrument if ck.timestamp <= ts_utc]) if len([ck for ck in h4_instrument if ck.timestamp <= ts_utc]) >= 6 else None
                d1_dir = builder._trend([ck for ck in d1_instrument if ck.timestamp <= ts_utc]) if len([ck for ck in d1_instrument if ck.timestamp <= ts_utc]) >= 6 else None

                directions = [d for d in [h1_dir, h4_dir, d1_dir] if d and d != "INSUFFICIENT_DATA"]
                alignment = round(sum(1 for d in directions if d == trend) / len(directions), 3) if directions and trend and trend != "INSUFFICIENT_DATA" else None

                # Phase 4.2: state is new (already checked above) — build it
                state_row = HistoricalMarketState(
                    instrument=instrument,
                    provider=c.provider,
                    provider_symbol=c.provider_symbol,
                    timestamp=ts_utc.replace(tzinfo=None),
                    base_timeframe=base_timeframe,
                    price=c.close,
                    ema_fast=None, ema_slow=None,
                    ema_distance_pct=None,
                    rsi=rsi_val,
                    atr=atr_val,
                    atr_pct=round((atr_val / price) * 100.0, 4) if atr_val and price > 0 else None,
                    trend=trend,
                    market_regime=regime,
                    distance_to_support_atr=dist_support,
                    distance_to_resistance_atr=dist_resist,
                    swing_structure=swing_struct,
                    higher_high=None, higher_low=None, lower_high=None, lower_low=None,
                    volatility_percentile=vol_pct,
                    session=session_val,
                    h1_direction=h1_dir,
                    h4_direction=h4_dir,
                    d1_direction=d1_dir,
                    timeframe_alignment_score=alignment,
                    source_quality="HEALTHY",
                    possible_contract_roll=possible_roll,
                    roll_gap_size=roll_gap,
                    roll_detection_reason=roll_reason,
                    feature_version=cfg.feature_version,
                    similarity_version=cfg.similarity_version,
                    created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                session.add(state_row)
                session.flush()
                state_id = state_row.id

                states_built += 1
                if earliest_ts is None or ts_utc < earliest_ts:
                    earliest_ts = ts_utc
                if latest_ts is None or ts_utc > latest_ts:
                    latest_ts = ts_utc

                if possible_roll:
                    excluded_roll += 1

                # Compute outcomes at each horizon (synchronous)
                # Phase 5.5: replaced per-state SessionLocal + 4 DB queries
                # with bisect slicing on the upfront-pre-loaded M1/M5/M15/M30
                # lists. Same algorithm, same data, no DB roundtrips per state.
                forward_idx = bisect.bisect_right(h1_ts_sorted, ts_utc)
                h1_forward = h1_instrument[forward_idx:]
                all_forward_by_tf = {"1h": h1_forward}
                # Sub-hour TFs — bisect slice on pre-loaded lists (no DB hit)
                m1_idx = bisect.bisect_right(m1_ts_sorted, ts_utc)
                if m1_idx < len(m1_instrument):
                    all_forward_by_tf["1min"] = m1_instrument[m1_idx:m1_idx + 5000]
                m5_idx = bisect.bisect_right(m5_ts_sorted, ts_utc)
                if m5_idx < len(m5_instrument):
                    all_forward_by_tf["5min"] = m5_instrument[m5_idx:m5_idx + 5000]
                m15_idx = bisect.bisect_right(m15_ts_sorted, ts_utc)
                if m15_idx < len(m15_instrument):
                    all_forward_by_tf["15min"] = m15_instrument[m15_idx:m15_idx + 5000]
                m30_idx = bisect.bisect_right(m30_ts_sorted, ts_utc)
                if m30_idx < len(m30_instrument):
                    all_forward_by_tf["30min"] = m30_instrument[m30_idx:m30_idx + 5000]
                # Also add H4 and D1 forward
                h4_forward = [ck for ck in h4_instrument if ck.timestamp > ts_utc]
                if h4_forward:
                    all_forward_by_tf["4h"] = h4_forward
                d1_forward = [ck for ck in d1_instrument if ck.timestamp > ts_utc]
                if d1_forward:
                    all_forward_by_tf["1day"] = d1_forward
                outcomes = _compute_outcomes_inline(
                    ts_utc=ts_utc,
                    state_price=c.close,
                    state_atr=atr_val,
                    h1_forward=h1_forward,
                    horizons=HORIZON_MINUTES,
                    neutral_x=cfg.neutral_x,
                    max_elapsed_multiple=cfg.max_elapsed_multiple,
                    all_forward_by_tf=all_forward_by_tf,
                )
                for h, w in outcomes.items():
                    session.add(HistoricalOutcome(
                        state_id=state_id,
                        horizon_minutes=h,
                        future_price=w.get("future_price"),
                        absolute_change=w.get("absolute_change"),
                        percentage_change=w.get("percentage_change"),
                        max_up_move=w.get("max_up_move"),
                        max_down_move=w.get("max_down_move"),
                        mfe=w.get("mfe"),
                        mae=w.get("mae"),
                        direction=w.get("direction"),
                        computed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        horizon_valid=w.get("horizon_valid", True),
                        actual_elapsed_seconds=w.get("actual_elapsed_seconds"),
                        invalid_reason=w.get("invalid_reason"),
                        possible_contract_roll=possible_roll,
                        roll_gap_size=roll_gap,
                        excluded_from_learning=possible_roll or not w.get("horizon_valid", True) or not w.get("resolution_sufficient", True),
                        exclusion_reason=(
                            "CONTRACT_ROLL_BOUNDARY" if possible_roll
                            else ("INVALID_HORIZON_WINDOW" if not w.get("horizon_valid", True) else None)
                            if w.get("resolution_sufficient", True)
                            else "INSUFFICIENT_RESOLUTION"
                        ),
                        outcome_source_timeframe=w.get("outcome_source_timeframe"),
                        outcome_source_provider=w.get("outcome_source_provider"),
                        outcome_source_instrument=w.get("outcome_source_instrument"),
                        resolution_sufficient=w.get("resolution_sufficient"),
                        outcome_version=w.get("outcome_version", "outcomes-v0.1"),
                    ))
                    outcomes_built += 1

                # Commit periodically
                if states_built % cfg.build_batch_size == 0:
                    session.commit()

                # Checkpoint periodically
                if states_built % cfg.build_checkpoint_interval == 0:
                    elapsed = time.monotonic() - started_at
                    sps = round(states_built / elapsed, 2) if elapsed > 0 and states_built > 0 else None
                    update_job_progress(
                        job_id,
                        built=states_built,
                        skipped_existing=skipped_existing,
                        excluded_roll=excluded_roll,
                        excluded_gaps=excluded_gaps,
                        excluded_insufficient_future=excluded_insufficient_future,
                        last_checkpoint_ts=ts_utc,
                        earliest_state=earliest_ts,
                        latest_state=latest_ts,
                        elapsed_seconds=elapsed,
                        states_per_second=sps,
                    )

            session.commit()
        finally:
            session.close()

        elapsed = time.monotonic() - started_at
        sps = round(states_built / elapsed, 2) if elapsed > 0 and states_built > 0 else None
        # Phase 4.3: DB-as-source-of-truth reconciliation
        with SessionLocal() as recon_session:
            from sqlalchemy import func as sa_func, select as sa_select
            db_count = recon_session.scalar(
                sa_select(sa_func.count(HistoricalMarketState.id)).where(
                    HistoricalMarketState.instrument == instrument,
                    HistoricalMarketState.base_timeframe == base_timeframe,
                    HistoricalMarketState.feature_version == cfg.feature_version,
                )
            ) or 0
        counter_count = states_built + skipped_existing
        diff = db_count - counter_count
        recon_warning = None
        final_status = "completed"
        if diff != 0:
            final_status = "completed_with_reconciliation_warning"
            recon_warning = f"DB has {db_count} states, counter says {counter_count} (diff={diff})"
        update_job_progress(
            job_id,
            built=states_built,
            skipped_existing=skipped_existing,
            excluded_roll=excluded_roll,
            excluded_gaps=excluded_gaps,
            excluded_insufficient_future=excluded_insufficient_future,
            status=final_status,
            last_checkpoint_ts=latest_ts,
            earliest_state=earliest_ts,
            latest_state=latest_ts,
            elapsed_seconds=elapsed,
            states_per_second=sps,
        )
        # Persist reconciliation fields
        try:
            with SessionLocal() as rs:
                from app.db.models import BuildJob as BJ
                job_row = rs.scalar(sa_sel(BJ).where(BJ.job_id == job_id))
                if job_row:
                    job_row.db_state_count = db_count
                    job_row.counter_state_count = counter_count
                    job_row.counter_db_difference = diff
                    job_row.reconciliation_warning = recon_warning
                    rs.commit()
        except Exception:
            pass
        mark_job_completed(job_id, elapsed_seconds=elapsed)

    except Exception as exc:
        mark_job_failed(job_id, str(exc))
    finally:
        _BUILD_TASKS.pop(job_id, None)


def _ensure_utc(dt):
    from datetime import timezone as _tz
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_tz.utc)
    return dt.astimezone(_tz.utc)


async def _run_build_job(
    job_id: str,
    instrument: str,
    base_timeframe: str,
    cfg: LearningConfig,
) -> None:
    """Background build worker. Updates BuildJob progress periodically.

    NEVER raises — catches all exceptions and marks the job as failed so
    the API event loop stays healthy.
    """
    started_at = datetime.now(timezone.utc)
    try:
        builder = HistoricalStateBuilder(base_timeframe=base_timeframe, min_history=cfg.min_history_candles)

        # Fetch H1/H4/D1 ONCE upfront
        all_h1 = await get_candles("1h", 5000, "XAU/USD")
        all_h4 = await get_candles("4h", 5000, "XAU/USD")
        all_d1 = await get_candles("1day", 5000, "XAU/USD")
        h1_instrument = [c for c in all_h1 if getattr(c, "instrument", "") == instrument]
        h4_instrument = [c for c in all_h4 if getattr(c, "instrument", "") == instrument]
        d1_instrument = [c for c in all_d1 if getattr(c, "instrument", "") == instrument]
        h1_ts_sorted = [c.timestamp for c in h1_instrument]  # already sorted asc

        start_idx = max(cfg.min_history_candles, 0)
        target_candles = h1_instrument[start_idx:]
        now_utc = datetime.now(timezone.utc)

        states_built = 0
        outcomes_built = 0
        skipped_existing = 0
        excluded_roll = 0
        excluded_gaps = 0
        excluded_insufficient_future = 0
        earliest_ts: datetime | None = None
        latest_ts: datetime | None = None

        update_job_progress(job_id, status="running")

        session = SessionLocal()
        try:
            for c in target_candles:
                ts_utc = c.timestamp if c.timestamp.tzinfo else c.timestamp.replace(tzinfo=timezone.utc)
                # Skip the last 25h of candles (need full 24h forward data)
                if (now_utc - ts_utc).total_seconds() < 25 * 3600:
                    continue

                # Bisect slice for candles <= T
                idx = bisect.bisect_right(h1_ts_sorted, ts_utc)
                candle_window = h1_instrument[:idx]
                if len(candle_window) < cfg.min_history_candles:
                    excluded_gaps += 1
                    continue

                # Roll detection: check the gap between this candle's open and
                # the previous candle's close. If > roll_atr_multiple * ATR,
                # mark as possible_contract_roll and exclude from learning.
                prev_candle = h1_instrument[idx - 2] if idx >= 2 else None
                atr_val = calc_atr(candle_window[-15:], 14) if len(candle_window) >= 15 else None
                possible_roll, roll_gap, roll_reason = detect_roll_between(
                    prev_candle, c, atr_val, roll_atr_multiple=cfg.roll_atr_multiple,
                )

                # Build feature vector (no look-ahead)
                fv = await builder.build_at(
                    instrument, ts_utc,
                    candles=h1_instrument,
                    h1_candles=h1_instrument,
                    h4_candles=h4_instrument,
                    d1_candles=d1_instrument,
                )
                if fv is None:
                    excluded_gaps += 1
                    continue

                # Idempotency check + insert
                existing = session.scalar(
                    select(HistoricalMarketState.id).where(
                        HistoricalMarketState.instrument == instrument,
                        HistoricalMarketState.base_timeframe == base_timeframe,
                        HistoricalMarketState.timestamp == ts_utc.replace(tzinfo=None),
                        HistoricalMarketState.feature_version == cfg.feature_version,
                    )
                )
                if existing is not None:
                    skipped_existing += 1
                    continue

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
                    possible_contract_roll=possible_roll,
                    roll_gap_size=roll_gap,
                    roll_detection_reason=roll_reason,
                    feature_version=cfg.feature_version,
                    similarity_version=cfg.similarity_version,
                    created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                session.add(state_row)
                session.flush()
                state_id = state_row.id

                states_built += 1
                if earliest_ts is None or ts_utc < earliest_ts:
                    earliest_ts = ts_utc
                if latest_ts is None or ts_utc > latest_ts:
                    latest_ts = ts_utc

                if possible_roll:
                    excluded_roll += 1

                # Compute outcomes at each horizon — forward-only, no look-ahead
                forward_idx = bisect.bisect_right(h1_ts_sorted, ts_utc)
                h1_forward = h1_instrument[forward_idx:]
                outcomes = _compute_outcomes_inline(
                    ts_utc=ts_utc,
                    state_price=c.close,
                    state_atr=atr_val,
                    h1_forward=h1_forward,
                    horizons=HORIZON_MINUTES,
                    neutral_x=cfg.neutral_x,
                    max_elapsed_multiple=cfg.max_elapsed_multiple,
                )
                for h, w in outcomes.items():
                    session.add(HistoricalOutcome(
                        state_id=state_id,
                        horizon_minutes=h,
                        future_price=w.get("future_price"),
                        absolute_change=w.get("absolute_change"),
                        percentage_change=w.get("percentage_change"),
                        max_up_move=w.get("max_up_move"),
                        max_down_move=w.get("max_down_move"),
                        mfe=w.get("mfe"),         # legacy field — direction-agnostic
                        mae=w.get("mae"),          # legacy field — direction-agnostic
                        direction=w.get("direction"),
                        computed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                        horizon_valid=w.get("horizon_valid", True),
                        actual_elapsed_seconds=w.get("actual_elapsed_seconds"),
                        invalid_reason=w.get("invalid_reason"),
                        possible_contract_roll=possible_roll,
                        roll_gap_size=roll_gap,
                        excluded_from_learning=possible_roll or not w.get("horizon_valid", True),
                        exclusion_reason=(
                            "CONTRACT_ROLL_BOUNDARY" if possible_roll
                            else ("INVALID_HORIZON_WINDOW" if not w.get("horizon_valid", True) else None)
                        ),
                    ))
                    outcomes_built += 1

                # Commit + checkpoint periodically
                if states_built % cfg.build_batch_size == 0:
                    session.commit()
                    await asyncio.sleep(0)  # yield to event loop

                if states_built % cfg.build_checkpoint_interval == 0:
                    update_job_progress(
                        job_id,
                        built=states_built,
                        skipped_existing=skipped_existing,
                        excluded_roll=excluded_roll,
                        excluded_gaps=excluded_gaps,
                        excluded_insufficient_future=excluded_insufficient_future,
                        last_checkpoint_ts=ts_utc,
                        earliest_state=earliest_ts,
                        latest_state=latest_ts,
                        elapsed_seconds=(datetime.now(timezone.utc) - started_at).total_seconds(),
                    )

            # Final commit
            session.commit()
        finally:
            session.close()

        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        sps = round(states_built / elapsed, 2) if elapsed > 0 and states_built > 0 else None
        update_job_progress(
            job_id,
            built=states_built,
            skipped_existing=skipped_existing,
            excluded_roll=excluded_roll,
            excluded_gaps=excluded_gaps,
            excluded_insufficient_future=excluded_insufficient_future,
            status="completed",
            last_checkpoint_ts=latest_ts,
            earliest_state=earliest_ts,
            latest_state=latest_ts,
            elapsed_seconds=elapsed,
            states_per_second=sps,
        )
        mark_job_completed(job_id, elapsed_seconds=elapsed)

    except Exception as exc:
        mark_job_failed(job_id, str(exc))
    finally:
        _BUILD_TASKS.pop(job_id, None)


def _compute_outcomes_inline(
    *,
    ts_utc,
    state_price,
    state_atr,
    h1_forward,
    horizons,
    neutral_x,
    max_elapsed_multiple,
    all_forward_by_tf=None,
):
    """Phase 4.3: Multi-resolution outcome lookup. For each horizon, selects
    the best available source timeframe (M1 preferred for 15m, M5 for 30m,
    H1 for 60m+). Falls back to H1 if no finer TF is available."""
    h1_seconds = INTERVALS["1h"]
    results = {}
    available_tfs = list(all_forward_by_tf.keys()) if all_forward_by_tf else ["1h"]

    for horizon in horizons:
        source_tf, res_sufficient = select_best_source_timeframe(horizon, available_tfs)
        if source_tf is None or not res_sufficient:
            source_tf = "1h"
            res_sufficient = False

        if all_forward_by_tf and source_tf in all_forward_by_tf:
            forward_candles = all_forward_by_tf[source_tf]
        else:
            forward_candles = h1_forward

        source_seconds = INTERVALS.get(source_tf, h1_seconds)
        candles_needed = max(1, horizon * 60 // source_seconds)

        if len(forward_candles) < candles_needed:
            results[horizon] = {
                "horizon_valid": False,
                "invalid_reason": f"insufficient {source_tf} forward data",
                "direction": "NULL",
                "outcome_source_timeframe": source_tf,
                "resolution_sufficient": res_sufficient,
                "outcome_version": OUTCOME_VERSION_V02 if all_forward_by_tf else "outcomes-v0.1",
            }
            continue

        window = forward_candles[:candles_needed]
        future_price = window[-1].close
        absolute_change = future_price - state_price
        percentage_change = (absolute_change / state_price) * 100.0 if state_price > 0 else None
        max_high = max(c.high for c in window)
        min_low = min(c.low for c in window)
        max_up_move = round(max_high - state_price, 4)
        max_down_move = round(state_price - min_low, 4)

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

        valid, actual_elapsed, invalid_reason = outcome_window_valid(
            ts_utc, window, horizon_minutes=horizon,
            max_elapsed_multiple=max_elapsed_multiple,
        )

        legacy_mfe = max(max_up_move, max_down_move)
        legacy_mae = min(max_up_move, max_down_move)

        results[horizon] = {
            "future_price": round(future_price, 4),
            "absolute_change": round(absolute_change, 4),
            "percentage_change": round(percentage_change, 4) if percentage_change is not None else None,
            "max_up_move": max_up_move,
            "max_down_move": max_down_move,
            "mfe": legacy_mfe,
            "mae": legacy_mae,
            "direction": direction,
            "horizon_valid": valid,
            "actual_elapsed_seconds": actual_elapsed,
            "invalid_reason": invalid_reason if not valid else None,
            "outcome_source_timeframe": source_tf,
            "outcome_source_provider": "Yahoo Finance (GC=F)",
            "outcome_source_instrument": "GC_FRONT_MONTH",
            "resolution_sufficient": res_sufficient,
            "outcome_version": OUTCOME_VERSION_V02 if all_forward_by_tf else "outcomes-v0.1",
        }
    return results


# ===========================================================================
# Current similarity matcher (immutable run persistence)
# ===========================================================================

async def current_similarity(
    *,
    instrument: str = "GC_FRONT_MONTH",
    horizon_minutes: int = 60,
    config: LearningConfig | None = None,
    technical_decision: str | None = None,
    technical_score: float | None = None,
) -> dict:
    """Find historical neighbors of the current market state + persist a NEW
    immutable SimilarityRun row. Returns the full result including run_id.

    Phase 4.1: every call creates a NEW run_id + persists a NEW SimilarityRun
    row. The 30-second computation cache reuses the MATCHING RESULTS (candidates,
    ranking, stats) for speed, but the run_id is always fresh.
    """
    cfg = config or DEFAULT_CONFIG
    cache_key = (instrument, horizon_minutes)
    now_ts_time = asyncio.get_event_loop().time()

    # Check computation cache (not run_id — always generate a fresh run_id)
    cached_computation = None
    if cache_key in _SIMILARITY_CACHE:
        cached_at, cached_payload = _SIMILARITY_CACHE[cache_key]
        if now_ts_time - cached_at < _CACHE_TTL_SECONDS:
            cached_computation = cached_payload

    if cached_computation and "error" not in cached_computation:
        # Reuse the cached computation, but create a NEW run_id + persist
        # a NEW immutable SimilarityRun row.
        result = dict(cached_computation)  # shallow copy
        run_id = new_run_id()
        result["similarity_run_id"] = run_id
        result["technical_decision"] = technical_decision
        result["technical_score"] = technical_score
        # Recompute historical_alignment if the technical_decision changed
        if technical_decision and result.get("statistics"):
            stats_agg = StatisticsAggregator()
            from app.services.learning.statistics import HorizonStatistics, DirectionRate
            # alignment from the cached stats
            stats_dict = result["statistics"]
            if stats_dict and stats_dict.get("sample_size", 0) > 0:
                # Reconstruct HorizonStatistics for alignment computation
                hs = HorizonStatistics(
                    horizon_minutes=stats_dict["horizon_minutes"],
                    sample_size=stats_dict["sample_size"],
                    up_count=stats_dict["up_count"],
                    down_count=stats_dict["down_count"],
                    neutral_count=stats_dict["neutral_count"],
                    up_rate=DirectionRate(stats_dict["up_rate"]["count"], stats_dict["up_rate"]["rate"],
                                          stats_dict["up_rate"]["wilson_lower"], stats_dict["up_rate"]["wilson_upper"]),
                    down_rate=DirectionRate(stats_dict["down_rate"]["count"], stats_dict["down_rate"]["rate"],
                                            stats_dict["down_rate"]["wilson_lower"], stats_dict["down_rate"]["wilson_upper"]),
                    neutral_rate=DirectionRate(stats_dict["neutral_rate"]["count"], stats_dict["neutral_rate"]["rate"],
                                               stats_dict["neutral_rate"]["wilson_lower"], stats_dict["neutral_rate"]["wilson_upper"]),
                    median_return=stats_dict.get("median_return"),
                    mean_return=stats_dict.get("mean_return"),
                    median_mfe=stats_dict.get("median_mfe"),
                    median_mae=stats_dict.get("median_mae"),
                    mean_mfe=stats_dict.get("mean_mfe"),
                    mean_mae=stats_dict.get("mean_mae"),
                    return_25th=stats_dict.get("return_25th"),
                    return_50th=stats_dict.get("return_50th"),
                    return_75th=stats_dict.get("return_75th"),
                    sample_quality=stats_dict.get("sample_quality", "INSUFFICIENT"),
                )
                result["historical_alignment"] = stats_agg.alignment_for_decision(technical_decision, hs)
        # Persist a NEW immutable SimilarityRun row with the fresh run_id
        _persist_similarity_run(result, cfg, run_id)
        return result

    # Full computation path (cache miss)

    builder = HistoricalStateBuilder(base_timeframe=cfg.base_timeframe, min_history=cfg.min_history_candles)
    engine = SimilarityEngine(config=cfg)
    stats_agg = StatisticsAggregator()

    # Pre-fetch H1/H4/D1 ONCE
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
                HistoricalMarketState.feature_version == cfg.feature_version,
                HistoricalMarketState.possible_contract_roll.is_(False),  # exclude rolls from learning
            ).order_by(HistoricalMarketState.timestamp.asc())
        ).all()

    candidates: list[tuple[int, datetime, FeatureVector]] = []
    for row in candidate_rows:
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
    raw_neighbor_count = len(ranked)

    # Phase 4.1: minimum_similarity_score — drop matches below threshold
    # (sample_size can be LOWER than top_k — insufficient data wins).
    ranked = [r for r in ranked if r[1] >= cfg.minimum_similarity_score]

    # Fetch outcomes at the requested horizon for each kept neighbor
    # (only VALID + NON-EXCLUDED outcomes contribute to stats).
    neighbor_state_ids = [r[0] for r in ranked]
    outcomes_by_state_id: dict[int, dict] = {}
    if neighbor_state_ids:
        with SessionLocal() as session:
            outcome_rows = session.scalars(
                select(HistoricalOutcome).where(
                    HistoricalOutcome.state_id.in_(neighbor_state_ids),
                    HistoricalOutcome.horizon_minutes == horizon_minutes,
                    HistoricalOutcome.excluded_from_learning.is_(False),
                    HistoricalOutcome.horizon_valid.is_(True),
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
        # Phase 4.1: directional MFE/MAE — depends on technical_decision
        directional_mfe = None
        directional_mae = None
        if outcome_row.max_up_move is not None and outcome_row.max_down_move is not None:
            if technical_decision == "BUY":
                directional_mfe = outcome_row.max_up_move
                directional_mae = abs(outcome_row.max_down_move)
            elif technical_decision == "SELL":
                directional_mfe = abs(outcome_row.max_down_move)
                directional_mae = outcome_row.max_up_move
            else:
                # WAIT / NO_DECISION — direction-neutral
                directional_mfe = outcome_row.max_up_move
                directional_mae = outcome_row.max_down_move

        neighbors.append(NeighborMatch(
            state_id=state_id,
            timestamp=ts,
            instrument=instrument,
            similarity_score=score,
            feature_vector=fv,
            outcome_direction=outcome_row.direction,
            outcome_future_price=outcome_row.future_price,
            outcome_mfe=directional_mfe,        # Phase 4.1: directional
            outcome_mae=directional_mae,         # Phase 4.1: directional
            outcome_percentage_change=outcome_row.percentage_change,
        ))
        if outcome_row.direction and outcome_row.direction != "NULL":
            outcomes_for_stats.append({
                horizon_minutes: OutcomeWindow(
                    horizon_minutes=horizon_minutes,
                    future_price=outcome_row.future_price,
                    absolute_change=outcome_row.absolute_change,
                    percentage_change=outcome_row.percentage_change,
                    mfe=directional_mfe,
                    mae=directional_mae,
                    maximum_up_move=outcome_row.max_up_move,
                    maximum_down_move=outcome_row.max_down_move,
                    direction=outcome_row.direction,
                )
            })

    stats_summary = stats_agg.aggregate(outcomes_for_stats, horizons=(horizon_minutes,))
    horizon_stats = stats_summary.by_horizon.get(horizon_minutes)

    alignment = None
    if horizon_stats and technical_decision:
        alignment = stats_agg.alignment_for_decision(technical_decision, horizon_stats)

    # Phase 4.1: similarity distribution (highest / median / lowest / 25 / 75)
    similarities = [n.similarity_score for n in neighbors] if neighbors else []
    highest_sim = max(similarities) if similarities else None
    lowest_sim = min(similarities) if similarities else None
    median_sim = round(py_median(similarities), 4) if similarities else None
    sim_25th = round(percentile(similarities, 25), 4) if similarities else None
    sim_75th = round(percentile(similarities, 75), 4) if similarities else None

    # Phase 4.1: effective history (exact, not "5.5d or 2y")
    feature_history_start = min((r[2] for r in ranked), default=None) if ranked else None
    feature_history_end = max((r[2] for r in ranked), default=None) if ranked else None
    outcome_history_start: datetime | None = None
    outcome_history_end: datetime | None = None
    if neighbors:
        outcome_history_start = min(n.timestamp for n in neighbors)
        outcome_history_end = max(n.timestamp for n in neighbors)
    if feature_history_start and outcome_history_start:
        effective_start = max(feature_history_start, outcome_history_start)
    else:
        effective_start = feature_history_start or outcome_history_start
    if feature_history_end and outcome_history_end:
        effective_end = min(feature_history_end, outcome_history_end)
    else:
        effective_end = feature_history_end or outcome_history_end
    effective_days = None
    if effective_start and effective_end:
        effective_days = round((effective_end - effective_start).total_seconds() / 86400.0, 2)

    # Top-match IDs (first 50 for the snapshot — full list queryable via the API)
    top_match_ids = [n.state_id for n in neighbors[:50]]

    # Phase 4.1: persist a NEW immutable SimilarityRun row
    run_id = new_run_id()
    run_created_at = datetime.now(timezone.utc)
    stats_dict = _horizon_stats_to_dict(horizon_stats) if horizon_stats else None
    result = {
        "similarity_run_id": run_id,
        "instrument": instrument,
        "analogue_instrument": instrument,  # for now, same as live; future phases may differ
        "feature_version": cfg.feature_version,
        "similarity_version": cfg.similarity_version,
        "horizon_minutes": horizon_minutes,
        "candidate_count": len(candidates),
        "raw_neighbor_count": raw_neighbor_count,
        "independent_neighbor_count": len(neighbors),  # after temporal dedup + threshold filter
        "sample_size": len(neighbors),
        "minimum_spacing_seconds": min_spacing_seconds,
        "similarity_threshold": cfg.minimum_similarity_score,
        "current_market_timestamp": current_ts.isoformat(),
        "current_market_price": latest_candle.close,
        "technical_decision": technical_decision,
        "technical_score": technical_score,
        "highest_similarity": highest_sim,
        "median_selected_similarity": median_sim,
        "lowest_selected_similarity": lowest_sim,
        "similarity_25th_percentile": sim_25th,
        "similarity_75th_percentile": sim_75th,
        "neighbors": [
            {
                "state_id": n.state_id,
                "timestamp": n.timestamp.isoformat(),
                "instrument": n.instrument,
                "similarity_score": n.similarity_score,
                "outcome_direction": n.outcome_direction,
                "outcome_future_price": n.outcome_future_price,
                "outcome_mfe": n.outcome_mfe,           # Phase 4.1: directional
                "outcome_mae": n.outcome_mae,           # Phase 4.1: directional
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
        "statistics": stats_dict,
        "historical_alignment": alignment,
        "probability_calibrated": False,  # Phase 4.1 invariant — always False
        "effective_history": {
            "feature_history_start": feature_history_start.isoformat() if feature_history_start else None,
            "feature_history_end": feature_history_end.isoformat() if feature_history_end else None,
            "outcome_history_start": outcome_history_start.isoformat() if outcome_history_start else None,
            "outcome_history_end": outcome_history_end.isoformat() if outcome_history_end else None,
            "effective_history_start": effective_start.isoformat() if effective_start else None,
            "effective_history_end": effective_end.isoformat() if effective_end else None,
            "effective_days": effective_days,
        },
        "interpretation_note": (
            f"Among {len(neighbors)} similar {instrument} historical states, "
            "the observed directional frequencies are descriptive statistics — "
            "they are NOT calibrated probabilities of future outcomes."
        ),
    }

    # Persist immutable SimilarityRun row
    _persist_similarity_run(result, cfg, run_id)

    # Cache the computation (without the run_id — each call generates fresh)
    cached_result = dict(result)
    cached_result.pop("similarity_run_id", None)
    _SIMILARITY_CACHE[cache_key] = (now_ts_time, cached_result)
    return result


def _persist_similarity_run(result: dict, cfg: LearningConfig, run_id: str) -> None:
    """Persist a NEW immutable SimilarityRun row. Best-effort — never breaks the API."""
    try:
        eff = result.get("effective_history", {})
        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc).replace(tzinfo=None)

        def _to_naive(ts_str):
            if not ts_str:
                return None
            try:
                return _dt.fromisoformat(ts_str).replace(tzinfo=None)
            except (ValueError, TypeError):
                return None

        stats_dict = result.get("statistics")
        top_ids = [n["state_id"] for n in result.get("neighbors", [])][:50]

        with SessionLocal() as session:
            session.add(SimilarityRun(
                run_id=run_id,
                created_at=now,
                current_market_timestamp=_to_naive(result.get("current_market_timestamp")),
                current_market_price=result.get("current_market_price"),
                instrument=result.get("instrument", "GC_FRONT_MONTH"),
                analogue_instrument=result.get("analogue_instrument", "GC_FRONT_MONTH"),
                feature_version=cfg.feature_version,
                similarity_version=cfg.similarity_version,
                horizon_minutes=result.get("horizon_minutes", 60),
                technical_decision=result.get("technical_decision"),
                technical_score=result.get("technical_score"),
                candidate_count=result.get("candidate_count", 0),
                raw_neighbor_count=result.get("raw_neighbor_count", 0),
                independent_neighbor_count=result.get("independent_neighbor_count", 0),
                minimum_spacing_seconds=result.get("minimum_spacing_seconds", 0),
                similarity_threshold=result.get("similarity_threshold", 0.5),
                highest_similarity=result.get("highest_similarity"),
                median_selected_similarity=result.get("median_selected_similarity"),
                lowest_selected_similarity=result.get("lowest_selected_similarity"),
                similarity_25th_percentile=result.get("similarity_25th_percentile"),
                similarity_75th_percentile=result.get("similarity_75th_percentile"),
                top_match_ids=json.dumps(top_ids),
                statistics_json=json.dumps(stats_dict or {}),
                historical_alignment=result.get("historical_alignment"),
                feature_history_start=_to_naive(eff.get("feature_history_start")),
                feature_history_end=_to_naive(eff.get("feature_history_end")),
                outcome_history_start=_to_naive(eff.get("outcome_history_start")),
                outcome_history_end=_to_naive(eff.get("outcome_history_end")),
                effective_history_start=_to_naive(eff.get("effective_history_start")),
                effective_history_end=_to_naive(eff.get("effective_history_end")),
                effective_days=eff.get("effective_days"),
                probability_calibrated=False,
                outcome_version=OUTCOME_VERSION_V02,
            ))
            session.commit()
    except Exception:
        pass  # audit log is best-effort — never break the API


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


# ===========================================================================
# State + Run inspectors
# ===========================================================================

def get_state(state_id: int) -> dict | None:
    """GET /api/learning/states/{id} — full feature snapshot + outcome availability."""
    with SessionLocal() as session:
        state = session.scalar(
            select(HistoricalMarketState).where(HistoricalMarketState.id == state_id)
        )
        if state is None:
            return None
        outcomes = session.scalars(
            select(HistoricalOutcome).where(HistoricalOutcome.state_id == state_id)
        ).all()
    return {
        "state_id": state.id,
        "instrument": state.instrument,
        "provider": state.provider,
        "provider_symbol": state.provider_symbol,
        "timestamp": state.timestamp.isoformat() if state.timestamp else None,
        "base_timeframe": state.base_timeframe,
        "price": state.price,
        "raw_features": {
            "ema_fast": state.ema_fast, "ema_slow": state.ema_slow,
            "ema_distance_pct": state.ema_distance_pct,
            "rsi": state.rsi, "atr": state.atr, "atr_pct": state.atr_pct,
            "trend": state.trend, "market_regime": state.market_regime,
            "distance_to_support_atr": state.distance_to_support_atr,
            "distance_to_resistance_atr": state.distance_to_resistance_atr,
            "swing_structure": state.swing_structure,
            "volatility_percentile": state.volatility_percentile,
            "session": state.session,
            "h1_direction": state.h1_direction, "h4_direction": state.h4_direction, "d1_direction": state.d1_direction,
            "timeframe_alignment_score": state.timeframe_alignment_score,
        },
        "normalized_vector": {
            "rsi_normalized": state.rsi / 100.0 if state.rsi is not None else None,
            "atr_pct": state.atr_pct,
            "distance_to_support_atr": state.distance_to_support_atr,
            "distance_to_resistance_atr": state.distance_to_resistance_atr,
            "volatility_percentile": state.volatility_percentile,
            "timeframe_alignment_score": state.timeframe_alignment_score,
        },
        "source_quality": state.source_quality,
        "possible_contract_roll": state.possible_contract_roll,
        "roll_gap_size": state.roll_gap_size,
        "roll_detection_reason": state.roll_detection_reason,
        "feature_version": state.feature_version,
        "similarity_version": state.similarity_version,
        "created_at": state.created_at.isoformat() if state.created_at else None,
        "outcome_availability": [
            {
                "horizon_minutes": o.horizon_minutes,
                "direction": o.direction,
                "future_price": o.future_price,
                "max_up_move": o.max_up_move,
                "max_down_move": o.max_down_move,
                "horizon_valid": o.horizon_valid,
                "actual_elapsed_seconds": o.actual_elapsed_seconds,
                "excluded_from_learning": o.excluded_from_learning,
                "exclusion_reason": o.exclusion_reason,
                "possible_contract_roll": o.possible_contract_roll,
            }
            for o in outcomes
        ],
    }


def get_run(run_id: str) -> dict | None:
    """GET /api/learning/runs/{run_id} — full immutable run snapshot."""
    with SessionLocal() as session:
        run = session.scalar(
            select(SimilarityRun).where(SimilarityRun.run_id == run_id)
        )
        if run is None:
            return None
    import json as _json
    return {
        "run_id": run.run_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "current_market_timestamp": run.current_market_timestamp.isoformat() if run.current_market_timestamp else None,
        "current_market_price": run.current_market_price,
        "instrument": run.instrument,
        "analogue_instrument": run.analogue_instrument,
        "feature_version": run.feature_version,
        "similarity_version": run.similarity_version,
        "horizon_minutes": run.horizon_minutes,
        "technical_decision": run.technical_decision,
        "technical_score": run.technical_score,
        "candidate_count": run.candidate_count,
        "raw_neighbor_count": run.raw_neighbor_count,
        "independent_neighbor_count": run.independent_neighbor_count,
        "minimum_spacing_seconds": run.minimum_spacing_seconds,
        "similarity_threshold": run.similarity_threshold,
        "highest_similarity": run.highest_similarity,
        "median_selected_similarity": run.median_selected_similarity,
        "lowest_selected_similarity": run.lowest_selected_similarity,
        "similarity_25th_percentile": run.similarity_25th_percentile,
        "similarity_75th_percentile": run.similarity_75th_percentile,
        "top_match_ids": _json.loads(run.top_match_ids) if run.top_match_ids else [],
        "statistics": _json.loads(run.statistics_json) if run.statistics_json else {},
        "historical_alignment": run.historical_alignment,
        "effective_history": {
            "feature_history_start": run.feature_history_start.isoformat() if run.feature_history_start else None,
            "feature_history_end": run.feature_history_end.isoformat() if run.feature_history_end else None,
            "outcome_history_start": run.outcome_history_start.isoformat() if run.outcome_history_start else None,
            "outcome_history_end": run.outcome_history_end.isoformat() if run.outcome_history_end else None,
            "effective_history_start": run.effective_history_start.isoformat() if run.effective_history_start else None,
            "effective_history_end": run.effective_history_end.isoformat() if run.effective_history_end else None,
            "effective_days": run.effective_days,
        },
        "probability_calibrated": run.probability_calibrated,
        "outcome_version": getattr(run, "outcome_version", "outcomes-v0.1"),
    }


# ===========================================================================
# Status endpoint
# ===========================================================================

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
                func.sum(case((HistoricalOutcome.direction != "NULL", 1), else_=0)),
                func.sum(case((HistoricalOutcome.excluded_from_learning, 1), else_=0)),
            ).group_by(HistoricalOutcome.horizon_minutes)
        ).all()
        recent_runs = session.scalars(
            select(SimilarityRun).order_by(SimilarityRun.created_at.desc()).limit(10)
        ).all()
        active_jobs_rows = session.scalars(
            select(BuildJob).where(
                BuildJob.status.in_(["queued", "running", "interrupted", "paused"])
            ).order_by(BuildJob.started_at.desc())
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
                "excluded_outcomes": excluded if excluded is not None else 0,
                "resolution_rules": HORIZON_RESOLUTION_RULES.get(h, []),
            }
            for h, total, valid, excluded in by_horizon
        ],
        "recent_runs": [
            {
                "run_id": r.run_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "instrument": r.instrument,
                "analogue_instrument": r.analogue_instrument,
                "feature_version": r.feature_version,
                "similarity_version": r.similarity_version,
                "horizon_minutes": r.horizon_minutes,
                "candidate_count": r.candidate_count,
                "independent_neighbor_count": r.independent_neighbor_count,
                "raw_neighbor_count": r.raw_neighbor_count,
                "technical_decision": r.technical_decision,
                "technical_score": r.technical_score,
                "historical_alignment": r.historical_alignment,
                "highest_similarity": r.highest_similarity,
                "median_selected_similarity": r.median_selected_similarity,
                "lowest_similarity": r.lowest_selected_similarity,
                "effective_days": r.effective_days,
                "outcome_version": getattr(r, "outcome_version", "outcomes-v0.1"),
            }
            for r in recent_runs
        ],
        "active_jobs": [get_job(r.job_id) or {} for r in active_jobs_rows],
        "feature_version": DEFAULT_CONFIG.feature_version,
        "similarity_version": DEFAULT_CONFIG.similarity_version,
        "probability_calibrated": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ===========================================================================
# Legacy sync build_states (still callable, but spawns background job)
# ===========================================================================

async def build_states(
    *,
    instrument: str = "GC_FRONT_MONTH",
    base_timeframe: str = "1h",
    config: LearningConfig | None = None,
    batch_limit: int = 5000,
    clear_existing: bool = False,
) -> dict:
    """Phase 4.1: legacy entry point. Spawns a background build job and
    returns immediately with job_id. The HTTP request NEVER blocks.

    Kept for backward compat with Phase 4 callers; new code should call
    start_build_job() directly.
    """
    return start_build_job(
        instrument=instrument,
        base_timeframe=base_timeframe,
        config=config,
        clear_existing=clear_existing,
    )
