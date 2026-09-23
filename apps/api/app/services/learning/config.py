"""Phase 4: configuration for the historical learning engine.

All weights and thresholds are EXPLICIT and CONFIGURABLE.
Phase 4 does NOT optimize any of these against outcome results —
they are documented sensible defaults only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Outcome horizons in minutes (15m / 30m / 1h / 2h / 4h / 8h / 24h).
HORIZON_MINUTES: tuple[int, ...] = (15, 30, 60, 120, 240, 480, 1440)

# Neutral-threshold multiplier. If absolute movement > X * ATR => UP/DOWN,
# else NEUTRAL. Conservative default = 0.5 (half an ATR in either direction
# is "noise"). Tuned only in a future calibration phase — not Phase 4.
NEUTRAL_X_DEFAULT: float = 0.5

# Sample-quality thresholds (configurable). These are NOT optimized —
# they are documented sensible defaults for "do we have enough evidence".
SAMPLE_QUALITY_THRESHOLDS: dict[str, int] = {
    "INSUFFICIENT": 30,
    "LOW": 100,
    "MODERATE": 300,
}

# Temporal de-duplication minimum spacing (in base_timeframe candles).
# Prevents 5 consecutive similar candles from counting as 5 independent
# examples. 4 = "keep one match per 4 candles minimum".
MIN_SPACING_CANDLES_DEFAULT: int = 4

# Top-K nearest neighbors to return from a similarity query.
TOP_K_DEFAULT: int = 200

# Similarity cache TTL (seconds). Live similarity results are cached briefly
# to avoid recomputing on every frontend refresh tick.
SIMILARITY_CACHE_TTL_SECONDS: int = 30


@dataclass(frozen=True)
class FeatureWeights:
    """Explicit, configurable weights for the similarity distance metric.

    Phase 4 defaults (NOT optimized — documented sensible choices):
      trend/regime/alignment:   HIGH (2.0)  — directional context matters most
      S-R distance / ATR:       MEDIUM_HIGH (1.5) — structure context
      RSI / volatility / ATR-normalized EMA distance / session: MEDIUM (1.0)
      swing_structure:          MEDIUM (1.0)
      multi-TF direction:       HIGH (2.0) per direction (H1/H4/D1)
    """

    trend: float = 2.0
    market_regime: float = 2.0
    timeframe_alignment_score: float = 2.0
    h1_direction: float = 2.0
    h4_direction: float = 2.0
    d1_direction: float = 2.0
    distance_to_support_atr: float = 1.5
    distance_to_resistance_atr: float = 1.5
    rsi: float = 1.0
    atr_pct: float = 1.0
    ema_distance_pct: float = 1.0
    volatility_percentile: float = 1.0
    session: float = 1.0
    swing_structure: float = 1.0


@dataclass(frozen=True)
class LearningConfig:
    """Top-level config for the learning engine."""

    feature_version: str = "features-v0.1"
    similarity_version: str = "similarity-v0.1"
    neutral_x: float = NEUTRAL_X_DEFAULT
    min_spacing_candles: int = MIN_SPACING_CANDLES_DEFAULT
    top_k: int = TOP_K_DEFAULT
    sample_quality_thresholds: dict[str, int] = field(
        default_factory=lambda: dict(SAMPLE_QUALITY_THRESHOLDS)
    )
    weights: FeatureWeights = field(default_factory=FeatureWeights)
    # State-building only considers H1 as the base timeframe for now —
    # it has the deepest intraday history (2 years). M1 has only 5d and
    # would limit states to recent history; D1 lacks intraday granularity.
    base_timeframe: str = "1h"
    # How many candles of history must exist before T to compute a state.
    # Needs >=20 for slow EMA + RSI + ATR + percentile rank.
    min_history_candles: int = 30
    # Phase 4.1: minimum_similarity_score — matches below this score are
    # NOT included merely to reach top-K. So sample_size can be LOWER
    # than top_k. Insufficient data must win.
    minimum_similarity_score: float = 0.50
    # Phase 4.1: roll-boundary detection — if the absolute gap between
    # consecutive H1 candles (|close[i] - open[i+1]| / ATR) exceeds this
    # multiple of ATR, mark the state as possible_contract_roll.
    roll_atr_multiple: float = 5.0  # 5x ATR — conservative (real rolls are 5-30x ATR)
    # Phase 4.1: outcome window validity — if the actual elapsed time
    # between state timestamp and the last candle in the forward window
    # exceeds expected_elapsed * max_elapsed_multiple, mark horizon_valid=False.
    max_elapsed_multiple: float = 2.0  # 2x expected — covers small closures, rejects weekends
    # Phase 4.2: build batching — commit every N states.
    # Set to 1 so the SQLite write lock is held for only ~16ms per state,
    # well under the 5s busy_timeout. This allows the main event loop's
    # collector_loop writes to interleave with the build's writes.
    build_batch_size: int = 1
    # Phase 4.1: build checkpoint interval — update job progress every N states.
    build_checkpoint_interval: int = 50


# Singleton default config — use LearningConfig() to override per-call.
DEFAULT_CONFIG = LearningConfig()
