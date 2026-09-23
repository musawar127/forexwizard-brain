"""Phase 4 + 4.1: Historical pattern-learning engine.

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
  * probability_calibrated is FALSE throughout Phase 4 + 4.1.
  * BUY/SELL/WAIT rules-v0.1 logic is UNCHANGED. Historical statistics
    are informational only — historical_alignment does NOT influence
    the decision.

Phase 4.1 additions:
  * Background build jobs (POST /api/learning/build-states returns immediately
    with job_id; never blocks the API event loop).
  * Immutable SimilarityRun rows (every similarity calc persists a full
    snapshot with run_id; old runs are NEVER updated).
  * Roll-boundary detection (large overnight gaps between consecutive H1
    candles are flagged as possible_contract_roll; crossing outcomes are
    EXCLUDED_FROM_LEARNING by default).
  * Outcome window validity (Friday 20:30 + 4h must NOT silently use
    Sunday/Monday pricing — invalid windows are excluded from stats).
  * Directional MFE/MAE (BUY: MFE=max_up_move, MAE=abs(max_down_move);
    SELL: MFE=abs(max_down_move), MAE=max_up_move).
  * Similarity distribution (highest / median / lowest / 25th / 75th).
  * minimum_similarity_score (sample_size can be LOWER than top_k —
    insufficient data wins).
  * Effective history per horizon (exact, not "5.5d or 2y").
  * State + Run inspectors (GET /api/learning/states/{id}, /runs/{run_id}).
  * Adversarial no-look-ahead test (mutate T+1/T+2 candles, state at T
    must be byte-identical).

Public surface:
  start_build_job       — spawns background build job, returns immediately
  build_states          — legacy entry point (delegates to start_build_job)
  current_similarity    — live matcher, persists immutable SimilarityRun
  learning_status       — /api/learning/status payload
  get_state             — /api/learning/states/{id}
  get_run               — /api/learning/runs/{run_id}
  get_job               — /api/learning/jobs/{job_id}
"""

from __future__ import annotations

from app.services.learning.config import LearningConfig, FeatureWeights, NEUTRAL_X_DEFAULT, HORIZON_MINUTES
from app.services.learning.states import HistoricalStateBuilder, FeatureVector
from app.services.learning.outcomes import OutcomeCalculator
from app.services.learning.resolution_rules import (
    HORIZON_RESOLUTION_RULES,
    OUTCOME_VERSION_V02,
    is_resolution_sufficient,
    select_best_source_timeframe,
)
from app.services.learning.roll_detector import detect_roll_between, outcome_window_valid
from app.services.learning.similarity import SimilarityEngine, SimilarityResult, NeighborMatch
from app.services.learning.statistics import StatisticsAggregator, SampleQuality, StatisticsSummary
from app.services.learning.jobs import (
    create_job,
    detect_orphaned_jobs,
    find_running_job,
    get_job,
    list_active_jobs,
    new_job_id,
    new_run_id,
)
from app.services.learning.orchestrator import (
    build_states,
    current_similarity,
    get_run,
    get_state,
    learning_status,
    start_build_job,
)

__all__ = [
    "LearningConfig",
    "FeatureWeights",
    "NEUTRAL_X_DEFAULT",
    "HORIZON_MINUTES",
    "HistoricalStateBuilder",
    "FeatureVector",
    "OutcomeCalculator",
    "detect_roll_between",
    "outcome_window_valid",
    "SimilarityEngine",
    "SimilarityResult",
    "NeighborMatch",
    "StatisticsAggregator",
    "SampleQuality",
    "StatisticsSummary",
    "create_job",
    "detect_orphaned_jobs",
    "find_running_job",
    "get_job",
    "list_active_jobs",
    "new_job_id",
    "new_run_id",
    "build_states",
    "current_similarity",
    "get_run",
    "get_state",
    "learning_status",
    "start_build_job",
]
