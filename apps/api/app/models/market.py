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
    # Phase 3.2: rename-display semantics. The existing rules-v0.1
    # `confidence` value is NOT a calibrated probability — it's a
    # deterministic rule-agreement score. We expose the SAME value as
    # `technical_score` so the frontend can display "Technical score:
    # 91.9 / 100" instead of "91.9% probability of success". The
    # underlying calculation is unchanged. Backward compat: `confidence`
    # is preserved in API responses.
    technical_score: float | None = None
    # Phase 3.2: future statistical-probability fields. These remain
    # NULL until Phase 4 implements historical pattern learning. They
    # are exposed in the API so the frontend can render placeholders
    # ("Not yet calculated — Phase 4") today and real numbers later
    # without breaking the response schema.
    # Phase 4: these fields are now POPULATED by the historical similarity
    # engine when there is sufficient same-instrument historical data.
    # probability_calibrated is ALWAYS False in Phase 4 — calibration
    # is a future phase.
    historical_sample_size: int | None = None
    historical_direction_rate: float | None = None
    historical_mfe: float | None = None
    historical_mae: float | None = None
    historical_probability: float | None = None
    probability_calibrated: bool | None = None
    # Phase 4: historical_alignment — informational only. SUPPORTS /
    # CONTRADICTS / NEUTRAL / INSUFFICIENT_DATA. Does NOT influence the
    # BUY/SELL/WAIT decision (rules-v0.1 unchanged).
    historical_alignment: str | None = None
    # Phase 4: which instrument the historical statistics came from.
    # May differ from the live instrument (e.g. live=XAUUSD_SPOT,
    # historical_analogue_instrument=GC_FRONT_MONTH).
    historical_analogue_instrument: str | None = None
    historical_analogue_horizon_minutes: int | None = None
    historical_analogue_note: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class AskResponse(BaseModel):
    answer: str
    analysis: BrainAnalysis | None = None
    sources: list[dict] = []
