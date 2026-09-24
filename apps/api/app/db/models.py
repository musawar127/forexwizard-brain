from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TickRecord(Base):
    __tablename__ = "market_ticks"
    __table_args__ = (UniqueConstraint("symbol", "market_timestamp", name="uq_tick_symbol_time"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    price: Mapped[float] = mapped_column(Float)
    market_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    provider: Mapped[str] = mapped_column(String(64))


class CandleRecord(Base):
    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "timestamp", name="uq_candle_symbol_interval_time"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    interval: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=1)
    provider: Mapped[str] = mapped_column(String(64), default="Local sampled Gold API")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Phase 3: distinguishes locally-sampled candles from genuine historical
    # OHLC candles fetched from an external provider. Defaults to False for
    # backward compatibility with rows created before Phase 3.
    is_historical: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    # Phase 3.1: source lineage. Each candle is now tagged with:
    #   - derivation:      DIRECT (provider gave OHLC at this TF natively),
    #                       AGGREGATED (we built OHLC from a lower-TF candle),
    #                       or SAMPLED (built from raw spot ticks, live feed).
    #   - provider_symbol: the upstream symbol (e.g. "GC=F" for Yahoo gold
    #                       futures, "XAU" for Gold API spot).
    #   - instrument:      canonical bucket — "GC_FRONT_MONTH" for Yahoo
    #                       futures, "XAUUSD_SPOT" for Gold API spot.
    #   - source_timeframe: the TF the candle was sourced from (for DIRECT
    #                       this equals the candle's own interval; for
    #                       AGGREGATED it is the lower TF; for SAMPLED it
    #                       is "TICK").
    #   - target_timeframe: the candle's own interval.
    derivation: Mapped[str] = mapped_column(String(16), default="SAMPLED", index=True)
    provider_symbol: Mapped[str] = mapped_column(String(32), default="XAU")
    instrument: Mapped[str] = mapped_column(String(32), default="XAUUSD_SPOT", index=True)
    source_timeframe: Mapped[str] = mapped_column(String(16), default="TICK")
    target_timeframe: Mapped[str | None] = mapped_column(String(16), nullable=True)


class BasisObservation(Base):
    """Phase 3.1: optional futures-vs-spot basis research record.

    When both a recent GC=F futures price (Yahoo) and a recent XAU/USD
    spot price (Gold API) exist within a small time window, we compute
    basis = futures_price - spot_price and store it for future research.
    This NEVER feeds into BUY/SELL/WAIT logic — Phase 4 may use it for
    statistical learning; Phase 3.1 only stores it.
    """

    __tablename__ = "basis_observations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    futures_price: Mapped[float] = mapped_column(Float)
    spot_price: Mapped[float] = mapped_column(Float)
    basis: Mapped[float] = mapped_column(Float)
    futures_provider: Mapped[str] = mapped_column(String(64), default="Yahoo Finance (GC=F)")
    spot_provider: Mapped[str] = mapped_column(String(64), default="Gold API")
    futures_symbol: Mapped[str] = mapped_column(String(32), default="GC=F")
    spot_symbol: Mapped[str] = mapped_column(String(32), default="XAU")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoricalSyncState(Base):
    """Phase 3: per-(provider, symbol, interval) synchronization state.

    Records the last successful backfill, the earliest/latest candle
    timestamps the provider returned, the total candle count, and the
    last error (if any). Drives the /api/data/* endpoints.

    Phase 3.1: also tracks instrument + derivation + source/target TF
    so the frontend /data page can show source lineage per row.
    """

    __tablename__ = "historical_sync_state"
    __table_args__ = (
        UniqueConstraint("provider", "symbol", "interval", name="uq_historical_sync_p_s_i"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    interval: Mapped[str] = mapped_column(String(16), index=True)
    earliest_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    total_candles: Mapped[int] = mapped_column(Integer, default=0)
    sync_status: Mapped[str] = mapped_column(String(32), default="never_synced")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase 3.1: lineage metadata.
    instrument: Mapped[str] = mapped_column(String(32), default="GC_FRONT_MONTH", index=True)
    provider_symbol: Mapped[str] = mapped_column(String(32), default="GC=F")
    derivation: Mapped[str] = mapped_column(String(16), default="DIRECT")
    source_timeframe: Mapped[str] = mapped_column(String(16), default="")
    target_timeframe: Mapped[str] = mapped_column(String(16), default="")


class HistoricalFeatureSnapshot(Base):
    """Phase 3: deterministic market-feature snapshot for a historical candle.

    Stores the Brain feature vector (EMA/RSI/ATR/regime/trend/swing/S-R/
    volatility/session/alignment) computed at a specific historical timestamp
    so future predictions can be evaluated against stored state.

    No strategy optimization is performed on these snapshots — they are
    read-only historical context, used for measurement rather than tuning.
    """

    __tablename__ = "historical_feature_snapshots"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "timestamp", name="uq_historical_feature_s_i_t"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    interval: Mapped[str] = mapped_column(String(16), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ema_fast: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_slow: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    trend: Mapped[str | None] = mapped_column(String(32), nullable=True)
    regime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    swing_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    swing_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    support_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    support_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    resistance_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    resistance_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    session: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timeframe_alignment: Mapped[float | None] = mapped_column(Float, nullable=True)
    features_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PredictionRecord(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decision: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    regime: Mapped[str] = mapped_column(String(32))
    risk: Mapped[str] = mapped_column(String(16))
    score: Mapped[float] = mapped_column(Float)
    reasons_json: Mapped[str] = mapped_column(Text)
    against_json: Mapped[str] = mapped_column(Text)
    snapshot_json: Mapped[str] = mapped_column(Text)
    brain_version: Mapped[str] = mapped_column(String(32), default="rules-v0.1")


class PredictionOutcome(Base):
    __tablename__ = "prediction_outcomes"
    __table_args__ = (UniqueConstraint("prediction_id", "horizon_minutes", name="uq_prediction_horizon"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(Integer, index=True)
    horizon_minutes: Mapped[int] = mapped_column(Integer)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    future_price: Mapped[float] = mapped_column(Float)
    price_change: Mapped[float] = mapped_column(Float)
    direction_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class NewsRecord(Base):
    __tablename__ = "research_news"
    __table_args__ = (UniqueConstraint("url", name="uq_news_url"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic: Mapped[str] = mapped_column(String(255), default="gold")
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


# ===========================================================================
# Phase 4: Historical pattern-learning engine
#
# Three tables back the learning layer:
#   1. historical_market_states — one row per (instrument, base_tf, ts) with
#      the full feature vector computed from candles <= ts (NO look-ahead).
#   2. historical_outcomes — one row per (state_id, horizon_minutes) with the
#      future_price/MFE/MAE/direction computed from candles >= ts (forward-only).
#   3. similarity_runs — audit log of every live similarity run.
#
# Outcome windows are SEPARATE from state features so we never accidentally
# leak future information back into the feature vector.
# ===========================================================================


class HistoricalMarketState(Base):
    """Phase 4: a single historical market-state snapshot at time T.

    All features (EMA, RSI, ATR, trend, regime, support/resistance,
    swing structure, volatility percentile, multi-TF direction,
    alignment, session) are computed from candles with timestamp <= T.
    Absolutely no look-ahead — features describe the world AS IT WAS at
    time T, before any future candle existed.

    feature_version + similarity_version persist the versioning so we
    can rebuild cleanly when feature engineering changes.

    Phase 4.1: possible_contract_roll flags states near a detected
    contract-roll discontinuity. Roll-boundary states are EXCLUDED
    from learning by default (not deleted — preserved for audit).
    """

    __tablename__ = "historical_market_states"
    __table_args__ = (
        UniqueConstraint("instrument", "base_timeframe", "timestamp", "feature_version", name="uq_hist_state_inst_tf_ts_fv"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    instrument: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(64))
    provider_symbol: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    base_timeframe: Mapped[str] = mapped_column(String(16), index=True)
    price: Mapped[float] = mapped_column(Float)

    # Normalized continuous features
    ema_fast: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_slow: Mapped[float | None] = mapped_column(Float, nullable=True)
    ema_distance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    rsi: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Categorical features (stored as strings for inspection)
    trend: Mapped[str | None] = mapped_column(String(32), nullable=True)
    market_regime: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ATR-normalized distances (continuous, scale-free)
    distance_to_support_atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_to_resistance_atr: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Swing structure classification
    swing_structure: Mapped[str | None] = mapped_column(String(32), nullable=True)
    higher_high: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    higher_low: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lower_high: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lower_low: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Volatility context (0-100 percentile rank within recent history)
    volatility_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    session: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Multi-timeframe direction (computed from each TF's trend at T)
    h1_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    h4_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    d1_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timeframe_alignment_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Source quality: HEALTHY / DEGRADED / INVALID based on data around T
    source_quality: Mapped[str] = mapped_column(String(16), default="HEALTHY")

    # Phase 4.1: roll-boundary detection metadata
    possible_contract_roll: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    roll_gap_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    roll_detection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Versioning — mandatory so we can rebuild when feature engineering changes
    feature_version: Mapped[str] = mapped_column(String(16), default="features-v0.1", index=True)
    similarity_version: Mapped[str] = mapped_column(String(16), default="similarity-v0.1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoricalOutcome(Base):
    """Phase 4: outcome window for a historical state at one horizon.

    Phase 4.1 changes:
      - max_up_move and max_down_move are now the CANONICAL stored fields
        (neutral market excursions — the state itself does NOT represent
        BUY or SELL). Directional MFE/MAE is computed at query time:
          BUY  query: MFE = max_up_move,        MAE = abs(max_down_move)
          SELL query: MFE = abs(max_down_move), MAE = max_up_move
      - horizon_valid: True only if the forward window contains sufficient
        valid market observations (no excessive market closures). Friday
        20:30 + 4h must NOT silently use Sunday/Monday pricing.
      - possible_contract_roll + excluded_from_learning: outcomes crossing
        a suspected roll boundary are flagged and excluded from stats by
        default (preserved for audit).
    """

    __tablename__ = "historical_outcomes"
    __table_args__ = (
        UniqueConstraint("state_id", "horizon_minutes", name="uq_hist_outcome_state_h"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    state_id: Mapped[int] = mapped_column(Integer, index=True)
    horizon_minutes: Mapped[int] = mapped_column(Integer, index=True)
    future_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    absolute_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentage_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Canonical neutral excursions (Phase 4.1)
    max_up_move: Mapped[float | None] = mapped_column(Float, nullable=True)  # max(high) - entry_price (>= 0)
    max_down_move: Mapped[float | None] = mapped_column(Float, nullable=True)  # entry_price - min(low) (>= 0)
    # Legacy MFE/MAE fields — kept for backward compat but recomputed
    # from max_up_move/max_down_move at query time based on direction.
    mfe: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Phase 4.1: window validity + roll-boundary + exclusion metadata
    horizon_valid: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    actual_elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Phase 5.5: widened from String(64) -> String(255). The roll_detector
    # produces a diagnostic string like "actual elapsed 17394.4h exceeds
    # expected 0.2h by >2.0x — likely weekend/holiday/maintenance closure"
    # which is ~95 chars. SQLite silently accepted this; PostgreSQL rejects
    # it with StringDataRightTruncation. 255 chars gives comfortable headroom.
    invalid_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    possible_contract_roll: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    roll_gap_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    excluded_from_learning: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    exclusion_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Phase 4.3: outcome resolution metadata — explicitly record which
    # source timeframe/provider/instrument was used to compute this outcome.
    # This prevents the 15m/30m issue where H1 candles were incorrectly
    # used for sub-hour horizons.
    outcome_source_timeframe: Mapped[str | None] = mapped_column(String(16), nullable=True)
    outcome_source_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome_source_instrument: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_sufficient: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    outcome_version: Mapped[str] = mapped_column(String(16), default="outcomes-v0.1")


class SimilarityRun(Base):
    """Phase 4 + 4.1: immutable audit record of every live similarity run.

    Phase 4.1: every similarity calculation now persists a full immutable
    snapshot. Given a run_id, all stats (sample / counts / rates / Wilson
    intervals / median return / MFE / MAE / top analogues) MUST remain
    byte-for-byte identical. New market state → new run, never update old.
    """

    __tablename__ = "similarity_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # "SIM-<uuid8>"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    current_market_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_market_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    instrument: Mapped[str] = mapped_column(String(32), index=True)             # live instrument
    analogue_instrument: Mapped[str] = mapped_column(String(32), index=True)    # instrument stats came from
    feature_version: Mapped[str] = mapped_column(String(16))
    similarity_version: Mapped[str] = mapped_column(String(16))
    horizon_minutes: Mapped[int] = mapped_column(Integer, index=True)
    technical_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    technical_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    candidate_count: Mapped[int] = mapped_column(Integer)                       # total candidates considered
    raw_neighbor_count: Mapped[int] = mapped_column(Integer)                    # top-K before temporal dedup
    independent_neighbor_count: Mapped[int] = mapped_column(Integer)            # after temporal dedup (= sample_size)
    minimum_spacing_seconds: Mapped[int] = mapped_column(Integer)               # dedup spacing used
    similarity_threshold: Mapped[float] = mapped_column(Float)                   # minimum_similarity_score config

    highest_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_selected_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    lowest_selected_similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    similarity_25th_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    similarity_75th_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)

    top_match_ids: Mapped[str] = mapped_column(Text)                            # JSON list of state_ids
    statistics_json: Mapped[str] = mapped_column(Text)                          # full immutable stats snapshot
    historical_alignment: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Effective history per horizon (exact — not "5.5d or 2y")
    feature_history_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    feature_history_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_history_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_history_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_history_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_history_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_days: Mapped[float | None] = mapped_column(Float, nullable=True)

    probability_calibrated: Mapped[bool] = mapped_column(Boolean, default=False)  # ALWAYS False in Phase 4.1
    # Phase 4.3: outcome_version — tracks which outcome computation version
    # the statistics were based on. Old runs keep outcomes-v0.1 (H1 for all
    # horizons); new runs use outcomes-v0.2 (correct source TF per horizon).
    outcome_version: Mapped[str] = mapped_column(String(16), default="outcomes-v0.1")


class BuildJob(Base):
    """Phase 4.1: background build job tracker.

    POST /api/learning/build-states creates a BuildJob row + spawns an
    asyncio task. The task periodically updates built/remaining/
    percent_complete/updated_at. Resumable: on backend restart, jobs
    left in "running" state are marked "interrupted" and can be resumed
    by POSTing the same job_id with resume=true.
    """

    __tablename__ = "build_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # "JOB-<uuid8>"
    instrument: Mapped[str] = mapped_column(String(32), index=True)
    base_timeframe: Mapped[str] = mapped_column(String(16))
    feature_version: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued/running/paused/completed/failed/interrupted
    eligible_total: Mapped[int] = mapped_column(Integer, default=0)
    built: Mapped[int] = mapped_column(Integer, default=0)
    skipped_existing: Mapped[int] = mapped_column(Integer, default=0)
    excluded_roll: Mapped[int] = mapped_column(Integer, default=0)
    excluded_gaps: Mapped[int] = mapped_column(Integer, default=0)
    excluded_insufficient_future: Mapped[int] = mapped_column(Integer, default=0)
    remaining: Mapped[int] = mapped_column(Integer, default=0)
    percent_complete: Mapped[float] = mapped_column(Float, default=0.0)
    last_checkpoint_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    earliest_state: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latest_state: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    states_per_second: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Phase 4.3: DB-as-source-of-truth reconciliation fields.
    # At each checkpoint + completion, query the DB for the actual state
    # count and persist it alongside the in-memory counter. If they
    # mismatch, mark the job COMPLETED_WITH_RECONCILIATION_WARNING.
    db_state_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    counter_state_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    counter_db_difference: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reconciliation_warning: Mapped[str | None] = mapped_column(Text, nullable=True)


# ===========================================================================
# Phase 5: Forward validation and live learning audit
#
# Three tables:
#   1. forward_observations — immutable snapshot of Brain state at capture T
#   2. forward_outcomes — evaluated outcomes per horizon (XAUUSD_SPOT data)
#   3. forward_audit_log — system events for debugging
# ===========================================================================


class ForwardObservation(Base):
    """Phase 5: immutable snapshot of the Brain's state at capture time.

    Once created, NEVER modified — even if the Brain changes one minute
    later. This preserves exactly what was known at T for true out-of-
    sample evaluation.
    """

    __tablename__ = "forward_observations"
    __table_args__ = (
        UniqueConstraint(
            "live_instrument", "capture_timeframe", "capture_timestamp",
            "technical_rule_version", "feature_version",
            name="uq_fwd_obs_inst_tf_ts_rule_feat",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    observation_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # "FWD-<uuid8>"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # Live market context
    live_instrument: Mapped[str] = mapped_column(String(32), index=True)
    live_provider: Mapped[str] = mapped_column(String(64))
    live_symbol: Mapped[str] = mapped_column(String(32))
    live_market_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    live_price: Mapped[float] = mapped_column(Float)

    # Technical engine snapshot (immutable)
    technical_decision: Mapped[str] = mapped_column(String(16))
    technical_score: Mapped[float] = mapped_column(Float)
    technical_rule_version: Mapped[str] = mapped_column(String(16))
    technical_data_readiness: Mapped[float] = mapped_column(Float)

    # Historical similarity snapshot (immutable)
    historical_similarity_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    historical_analogue_instrument: Mapped[str | None] = mapped_column(String(32), nullable=True)
    historical_alignment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    historical_sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    historical_direction_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_probability_calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    historical_median_mfe: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_median_mae: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Version tracking
    feature_version: Mapped[str] = mapped_column(String(16))
    similarity_version: Mapped[str] = mapped_column(String(16))
    outcome_version: Mapped[str] = mapped_column(String(16))

    # Market context
    market_regime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session: Mapped[str | None] = mapped_column(String(16), nullable=True)
    h1_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    h4_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    d1_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    instrument_consistency: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Capture metadata
    capture_timeframe: Mapped[str] = mapped_column(String(16))  # "15min" or "1h"
    capture_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_freshness_status: Mapped[str] = mapped_column(String(16))  # "RECENT" / "STALE" / "NO_DATA"

    # Lifecycle
    observation_status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    # PENDING → PARTIALLY_EVALUATED → COMPLETE / INVALID
    # Phase 5.1: invalid_reason for observations that violate rules
    # Phase 5.5: widened from String(64) -> String(255) for parity with
    # historical_outcomes.invalid_reason (see Phase 5.5 migration).
    invalid_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Phase 5.1: WAIT alignment semantics — for WAIT observations,
    # historical_alignment = NOT_APPLICABLE and wait_historical_context is used instead.
    wait_historical_context: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # DIRECTIONAL_UP / DIRECTIONAL_DOWN / NEUTRAL / INSUFFICIENT_DATA

    # Phase 5.1: data freshness at capture (stored separately)
    quote_age_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    brain_analysis_age_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    similarity_run_age_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Phase 5.1: historical run immutability link
    similarity_run_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    similarity_run_market_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ForwardOutcome(Base):
    """Phase 5: evaluated outcome for a forward observation at one horizon.

    Uses XAUUSD_SPOT live data (NOT GC futures) for outcome evaluation.
    This is mandatory — a live XAU spot prediction is evaluated against
    XAU spot prices, not futures prices.
    """

    __tablename__ = "forward_outcomes"
    __table_args__ = (
        UniqueConstraint("observation_id", "horizon_minutes", name="uq_fwd_outcome_obs_horizon"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    observation_id: Mapped[str] = mapped_column(String(32), index=True)
    horizon_minutes: Mapped[int] = mapped_column(Integer)

    # Outcome instrument MUST be XAUUSD_SPOT (not GC_FRONT_MONTH)
    outcome_instrument: Mapped[str] = mapped_column(String(32))  # "XAUUSD_SPOT"
    outcome_provider: Mapped[str] = mapped_column(String(64))    # "Gold API"
    outcome_source_timeframe: Mapped[str | None] = mapped_column(String(16), nullable=True)

    entry_price: Mapped[float] = mapped_column(Float)
    future_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    absolute_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentage_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_up_move: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_down_move: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Directional MFE/MAE (computed from max_up_move + max_down_move)
    buy_mfe: Mapped[float | None] = mapped_column(Float, nullable=True)
    buy_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell_mfe: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell_mae: Mapped[float | None] = mapped_column(Float, nullable=True)

    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)  # UP/DOWN/NEUTRAL/PENDING
    resolution_sufficient: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    horizon_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Phase 5.1: outcome status + invalid reason + target timestamp metadata
    outcome_status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING/VALID/INVALID
    # Phase 5.5: widened from String(64) -> String(255) for parity with
    # historical_outcomes.invalid_reason.
    invalid_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # INSUFFICIENT_SPOT_DATA / TARGET_TIMESTAMP_MISSING / MARKET_CLOSURE / BAD_SOURCE_DATA / OBSERVATION_INVALID

    target_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_future_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timestamp_error_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Phase 5.1: market-aware elapsed time
    elapsed_wall_time: Mapped[float | None] = mapped_column(Float, nullable=True)  # seconds
    elapsed_market_time: Mapped[float | None] = mapped_column(Float, nullable=True)  # seconds (excludes closures)


class ForwardAuditLog(Base):
    """Phase 5: system events for debugging forward validation."""

    __tablename__ = "forward_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    observation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class SystemConfig(Base):
    """Phase 5: system-level configuration values (key-value store)."""

    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ForwardHeartbeat(Base):
    """Phase 5.2: lightweight periodic heartbeat from the forward collector."""

    __tablename__ = "forward_heartbeats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    collector_running: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluator_running: Mapped[bool] = mapped_column(Boolean, default=False)
    last_valid_quote_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_valid_quote_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_m15_capture_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_h1_capture_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_evaluation_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_observations: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uptime_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)


# ===========================================================================
# Phase 5.3: Wake and catch-up recovery mode
# ===========================================================================


class SystemSyncState(Base):
    """Phase 5.3: per-component sync state for the catch-up coordinator."""

    __tablename__ = "system_sync_state"
    __table_args__ = (
        UniqueConstraint("component", name="uq_sync_state_component"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    component: Mapped[str] = mapped_column(String(64), index=True)
    # market_history / spot_history / historical_states / forward_outcomes /
    # research / economic_events / brain_memory
    last_successful_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempted_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="IDLE")
    # IDLE / SYNCING / COMPLETE / DEGRADED / FAILED
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CatchupJob(Base):
    """Phase 5.3: catch-up job tracker for startup recovery."""

    __tablename__ = "catchup_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    # QUEUED / RUNNING / COMPLETED / COMPLETED_WITH_WARNINGS / FAILED / INTERRUPTED

    offline_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    offline_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    market_sync_status: Mapped[str] = mapped_column(String(16), default="IDLE")
    research_sync_status: Mapped[str] = mapped_column(String(16), default="IDLE")
    forward_outcome_status: Mapped[str] = mapped_column(String(16), default="IDLE")
    historical_state_status: Mapped[str] = mapped_column(String(16), default="IDLE")

    recovered_market_candles: Mapped[int] = mapped_column(Integer, default=0)
    recovered_spot_observations: Mapped[int] = mapped_column(Integer, default=0)
    research_items_added: Mapped[int] = mapped_column(Integer, default=0)
    forward_outcomes_evaluated: Mapped[int] = mapped_column(Integer, default=0)
    missed_forward_captures: Mapped[int] = mapped_column(Integer, default=0)
    historical_states_added: Mapped[int] = mapped_column(Integer, default=0)

    progress_percent: Mapped[float] = mapped_column(Float, default=0.0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class MissedForwardCapture(Base):
    """Phase 5.3: record of forward capture events missed while offline.

    These do NOT count as forward observations — they are audit records
    showing what was missed. Never fabricate retrospective predictions.
    """

    __tablename__ = "missed_forward_captures"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    capture_timeframe: Mapped[str] = mapped_column(String(16))
    # M15 / H1
    expected_capture_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    offline_reason: Mapped[str] = mapped_column(String(64), default="SERVER_OFFLINE")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ============================================================
# Phase 5.6: Trade Plan Engine — advisory XAU/USD trade plans
# ============================================================
# These tables are SEPARATE from forward_observations / forward_outcomes.
# forward_* tracks prospective prediction outcomes (post-decision, pre-target).
# trade_plan_* tracks the lifecycle of structured trade plans generated
# downstream of the Brain's BUY/SELL/WAIT decision.
# ============================================================


class TradePlan(Base):
    """Phase 5.6: immutable advisory trade plan.

    Generated by the Trade Plan Engine when the Brain emits BUY or SELL.
    WAIT decisions produce a row with plan_status=NO_TRADE so the user
    can see the Brain explicitly chose to stand aside.

    Rows are NEVER rewritten after market movement. Lifecycle transitions
    are stored in trade_plan_lifecycle_events. Forward validation outcomes
    (entry touched? SL hit? TP1-4 reached? MFE/MAE?) are stored in
    trade_plan_outcomes.
    """

    __tablename__ = "trade_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # "TP-<uuid8>"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    market_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    instrument: Mapped[str] = mapped_column(String(32), index=True, default="XAU/USD")
    brain_decision: Mapped[str] = mapped_column(String(16), index=True)  # BUY / SELL / WAIT / NO_DECISION
    technical_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Entry zone (NULL when plan_status=NO_TRADE / NO_VALID_ENTRY)
    entry_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # SUPPORT_RETEST / RESISTANCE_RETEST
    entry_reference: Mapped[float | None] = mapped_column(Float, nullable=True)  # midpoint

    # Stop / invalidation
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalidation_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    invalidation_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sl_distance: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Targets (TP1 < TP2 < TP3 < TP4 for BUY; reverse for SELL)
    tp1: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp2: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp3: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp4: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp1_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tp2_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tp3_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tp4_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # R:R per target
    risk_distance: Mapped[float | None] = mapped_column(Float, nullable=True)
    reward_tp1: Mapped[float | None] = mapped_column(Float, nullable=True)
    reward_tp2: Mapped[float | None] = mapped_column(Float, nullable=True)
    reward_tp3: Mapped[float | None] = mapped_column(Float, nullable=True)
    reward_tp4: Mapped[float | None] = mapped_column(Float, nullable=True)
    rr_tp1: Mapped[float | None] = mapped_column(Float, nullable=True)
    rr_tp2: Mapped[float | None] = mapped_column(Float, nullable=True)
    rr_tp3: Mapped[float | None] = mapped_column(Float, nullable=True)
    rr_tp4: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Management
    management_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Plan generation status
    # ACTIONABLE / WAIT_FOR_ENTRY / NO_VALID_ENTRY / INSUFFICIENT_DATA / STALE / NO_TRADE
    plan_status: Mapped[str] = mapped_column(String(24), index=True, default="NO_TRADE")
    plan_version: Mapped[str] = mapped_column(String(16), default="trade-plan-v0.1")

    # Historical context (informational only — does NOT change BUY/SELL/WAIT)
    historical_similarity_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    historical_context: Mapped[str | None] = mapped_column(String(32), nullable=True)  # INSUFFICIENT_DATA / SUPPORTS / CONTRADICTS / NEUTRAL

    # Live lifecycle state (transitions stored in trade_plan_lifecycle_events)
    # CREATED / WAITING_FOR_ENTRY / ENTRY_TOUCHED / ACTIVE / TP1_REACHED /
    # TP2_REACHED / TP3_REACHED / TP4_REACHED / STOPPED / BREAKEVEN / EXPIRED / INVALIDATED
    lifecycle_state: Mapped[str] = mapped_column(String(24), default="CREATED", index=True)

    # Final outcome status (forward validation)
    # NULL while plan is live; once finalized, one of:
    # COMPLETED_TP1 / COMPLETED_TP2 / COMPLETED_TP3 / COMPLETED_TP4 / STOPPED / EXPIRED / INVALIDATED
    final_status: Mapped[str | None] = mapped_column(String(24), nullable=True)


class TradePlanLifecycleEvent(Base):
    """Phase 5.6: immutable record of every lifecycle transition for a plan.

    Stored SEPARATELY from trade_plans so the plan row is never rewritten.
    """

    __tablename__ = "trade_plan_lifecycle_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(32), index=True)
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    from_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_state: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(String(255))
    market_price: Mapped[float | None] = mapped_column(Float, nullable=True)


class TradePlanOutcome(Base):
    """Phase 5.6: forward validation of a trade plan.

    One row per plan, updated as the market evolves. Records what actually
    happened AFTER plan creation — entry touched? SL hit before target?
    TP1-4 reached? MFE/MAE? Times? Final outcome?

    PROSPECTIVE ONLY. We never backfill historical plan outcomes.
    """

    __tablename__ = "trade_plan_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    entry_touched: Mapped[bool] = mapped_column(Boolean, default=False)
    entry_touched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entry_touch_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    sl_before_target: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sl_hit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sl_hit_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    tp1_reached: Mapped[bool] = mapped_column(Boolean, default=False)
    tp2_reached: Mapped[bool] = mapped_column(Boolean, default=False)
    tp3_reached: Mapped[bool] = mapped_column(Boolean, default=False)
    tp4_reached: Mapped[bool] = mapped_column(Boolean, default=False)
    tp1_reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tp2_reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tp3_reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tp4_reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    max_favorable_excursion: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_adverse_excursion: Mapped[float | None] = mapped_column(Float, nullable=True)

    time_to_entry_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_tp1_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_tp2_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_tp3_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_to_tp4_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    final_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
