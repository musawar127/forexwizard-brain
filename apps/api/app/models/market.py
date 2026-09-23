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


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class AskResponse(BaseModel):
    answer: str
    analysis: BrainAnalysis | None = None
    sources: list[dict] = []
