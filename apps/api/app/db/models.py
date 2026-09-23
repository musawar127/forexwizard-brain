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
    """

    __tablename__ = "historical_market_states"
    __table_args__ = (
        UniqueConstraint("instrument", "base_timeframe", "timestamp", name="uq_hist_state_inst_tf_ts"),
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
    ema_distance_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # (fast-slow)/slow * 100
    rsi: Mapped[float | None] = mapped_column(Float, nullable=True)               # 0..100
    atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr_pct: Mapped[float | None] = mapped_column(Float, nullable=True)            # atr / price * 100

    # Categorical features (stored as strings for inspection)
    trend: Mapped[str | None] = mapped_column(String(32), nullable=True)
    market_regime: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # ATR-normalized distances (continuous, scale-free)
    distance_to_support_atr: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_to_resistance_atr: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Swing structure classification
    swing_structure: Mapped[str | None] = mapped_column(String(32), nullable=True)  # HH_HL / LH_LL / etc.
    higher_high: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    higher_low: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lower_high: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lower_low: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Volatility context (0-100 percentile rank within recent history)
    volatility_percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    session: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ASIA / EU / US / OFF

    # Multi-timeframe direction (computed from each TF's trend at T)
    h1_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    h4_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    d1_direction: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timeframe_alignment_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0..1

    # Source quality: HEALTHY / DEGRADED / INVALID based on data around T
    source_quality: Mapped[str] = mapped_column(String(16), default="HEALTHY")

    # Versioning — mandatory so we can rebuild when feature engineering changes
    feature_version: Mapped[str] = mapped_column(String(16), default="features-v0.1")
    similarity_version: Mapped[str] = mapped_column(String(16), default="similarity-v0.1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoricalOutcome(Base):
    """Phase 4: outcome window for a historical state at one horizon.

    Computed strictly from candles with timestamp >= state.timestamp.
    NEVER used to influence the feature vector at the state's own time
    (would be look-ahead leakage).

    direction is UP / DOWN / NEUTRAL based on a volatility-aware
    threshold (X * ATR). X is configurable; default X = 0.5 (conservative).
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
    mfe: Mapped[float | None] = mapped_column(Float, nullable=True)  # max favorable excursion
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)  # max adverse excursion
    maximum_up_move: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum_down_move: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction: Mapped[str | None] = mapped_column(String(16), nullable=True)  # UP / DOWN / NEUTRAL / NULL
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SimilarityRun(Base):
    """Phase 4: audit log of every live similarity run.

    Lets us later evaluate whether historical similarity actually helps —
    by comparing technical decisions against historical evidence over time.
    """

    __tablename__ = "similarity_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    instrument: Mapped[str] = mapped_column(String(32), index=True)
    feature_version: Mapped[str] = mapped_column(String(16))
    similarity_version: Mapped[str] = mapped_column(String(16))
    horizon_minutes: Mapped[int] = mapped_column(Integer)
    candidate_count: Mapped[int] = mapped_column(Integer)
    sample_size: Mapped[int] = mapped_column(Integer)
    result_statistics_json: Mapped[str] = mapped_column(Text)        # JSON: up_rate, down_rate, neutral_rate, etc.
    technical_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    historical_alignment: Mapped[str | None] = mapped_column(String(32), nullable=True)  # SUPPORTS/CONTRADICTS/NEUTRAL/INSUFFICIENT_DATA
