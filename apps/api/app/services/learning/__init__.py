"""Phase 4: Historical pattern-learning engine.

Builds historical market-state snapshots with NO look-ahead bias,
finds genuinely similar past setups via a deterministic weighted
similarity algorithm, measures subsequent price behavior at multiple
horizons (15m / 30m / 1h / 2h / 4h / 8h / 24h), and reports sample
statistics with Wilson confidence intervals.

CRITICAL INVARIANTS (enforced in tests):
  * No-look-ahead: features at time T use ONLY candles with ts <= T.
  * Same-instrument-only: GC_FRONT_MONTH states match GC_FRONT_MONTH
    candidates only; never combine with XAUUSD_SPOT statistics.
  * Deterministic: same DB + same current state + same feature version +
    same weights + same config => identical similarity results.
  * Temporal de-duplication: closely-clustered neighbors are spaced so
    they don't count as multiple independent samples.
  * probability_calibrated is FALSE throughout Phase 4. Calibration is
    a future phase.
  * BUY/SELL/WAIT rules-v0.1 logic is UNCHANGED. Historical statistics
    are informational only — historical_alignment does NOT influence
    the decision.

Public surface:
  build_states                — batch builder, populates historical_market_states + outcomes
  current_similarity           — live matcher, returns neighbors + statistics for current state
  FeatureVector                — normalized feature representation
  HistoricalStateBuilder       — feature computation with no-look-ahead
  OutcomeCalculator             — MFE/MAE/direction at each horizon
  SimilarityEngine              — weighted distance + temporal dedup + samples
  StatisticsAggregator          — Wilson interval + sample quality + distribution
"""

from __future__ import annotations

from app.services.learning.config import LearningConfig, FeatureWeights, NEUTRAL_X_DEFAULT, HORIZON_MINUTES
from app.services.learning.states import HistoricalStateBuilder, FeatureVector
from app.services.learning.outcomes import OutcomeCalculator
from app.services.learning.similarity import SimilarityEngine, SimilarityResult, NeighborMatch
from app.services.learning.statistics import StatisticsAggregator, SampleQuality, StatisticsSummary
from app.services.learning.orchestrator import build_states, current_similarity, learning_status

__all__ = [
    "LearningConfig",
    "FeatureWeights",
    "NEUTRAL_X_DEFAULT",
    "HORIZON_MINUTES",
    "HistoricalStateBuilder",
    "FeatureVector",
    "OutcomeCalculator",
    "SimilarityEngine",
    "SimilarityResult",
    "NeighborMatch",
    "StatisticsAggregator",
    "SampleQuality",
    "StatisticsSummary",
    "build_states",
    "current_similarity",
    "learning_status",
]
