from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Quote(BaseModel):
    symbol: str
    price: float
    provider: str
    market_timestamp: datetime | None = None
    received_timestamp: datetime
    age_seconds: float | None = None
    status: Literal["RECENT", "STALE", "NO_DATA"] = "RECENT"


class Candle(BaseModel):
    symbol: str
    interval: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    sample_count: int = 1
    provider: str = "Local sampled Gold API"
    # Phase 3.1: source lineage. Defaults preserve backward compatibility
    # for the live Gold API sampler (SAMPLED / XAUUSD_SPOT / TICK source).
    is_historical: bool = False
    derivation: str = "SAMPLED"  # DIRECT | AGGREGATED | SAMPLED
    provider_symbol: str = "XAU"
    instrument: str = "XAUUSD_SPOT"
    source_timeframe: str = "TICK"
    target_timeframe: str | None = None  # None means "same as interval"


class Zone(BaseModel):
    kind: Literal["SUPPORT", "RESISTANCE"]
    low: float
    high: float
    touches: int = 1
    strength: float = 0.0


class TimeframeState(BaseModel):
    timeframe: str
    candles: int
    status: str
    trend: str = "INSUFFICIENT_DATA"
    rsi: float | None = None
    atr: float | None = None
    ema_fast: float | None = None
    ema_slow: float | None = None


class BrainAnalysis(BaseModel):
    symbol: str = "XAU/USD"
    timestamp: datetime
    decision: Literal["BUY", "SELL", "WAIT", "NO_DECISION"]
    confidence: float = Field(ge=0, le=100)
    readiness: float = Field(ge=0, le=100)
    regime: str
    risk: str
    score: float
    price: float | None
    reasons_for: list[str]
    reasons_against: list[str]
    invalidation: float | None = None
    support: Zone | None = None
    resistance: Zone | None = None
    timeframes: list[TimeframeState]
    data_quality: str
    message: str
    brain_version: str = "rules-v0.1"
    # Phase 3.1: separate "technical readiness" (rule agreement) from
    # "historical depth" (how many days of genuine history back each TF)
    # and "instrument consistency" (PURE_GC / PURE_SPOT / MIXED / NONE).
    # These are read-only context fields. They do NOT influence the
    # BUY/SELL/WAIT decision — only clarify what the confidence number
    # is actually backed by.
    historical_depth: dict[str, float] = Field(default_factory=dict)
    instrument_consistency: str = "NONE"  # PURE_GC | PURE_SPOT | MIXED | NONE
    technical_data_readiness: float = 0.0


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class AskResponse(BaseModel):
    answer: str
    analysis: BrainAnalysis | None = None
    sources: list[dict] = []
