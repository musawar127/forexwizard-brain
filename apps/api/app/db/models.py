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


class HistoricalSyncState(Base):
    """Phase 3: per-(provider, symbol, interval) synchronization state.

    Records the last successful backfill, the earliest/latest candle
    timestamps the provider returned, the total candle count, and the
    last error (if any). Drives the /api/data/* endpoints.
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
