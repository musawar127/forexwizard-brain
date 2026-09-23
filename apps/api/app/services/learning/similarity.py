"""Phase 4: deterministic weighted-similarity engine.

Algorithm (similarity-v0.1):
  For each candidate historical state, compute a weighted normalized
  distance from the live query feature vector.

  Continuous features use |a - b| distance (already normalized to ~[0,1]).
  Categorical features use 0 (exact match) or 1 (mismatch).

  distance = sum(weight * feature_distance) / sum(weight)
  similarity_score = 1 - distance   (in [0, 1])

Temporal de-duplication:
  After ranking candidates by similarity, group by timestamp bucket
  (min_spacing_candles * interval_seconds) and keep only the BEST
  match per bucket. Prevents 5 consecutive similar candles from
  counting as 5 independent neighbors.

Determinism (tested):
  Same DB + same query vector + same weights + same config => identical
  ranking + identical sample. No randomness, no tie-break by id.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.services.learning.config import LearningConfig
from app.services.learning.states import FeatureVector


@dataclass(frozen=True)
class NeighborMatch:
    """A single similar historical state."""

    state_id: int
    timestamp: datetime
    instrument: str
    similarity_score: float                    # 0..1 (1 = identical)
    feature_vector: FeatureVector
    # Outcome at the queried horizon (filled in by SimilarityEngine.query)
    outcome_direction: str | None = None
    outcome_future_price: float | None = None
    outcome_mfe: float | None = None
    outcome_mae: float | None = None
    outcome_percentage_change: float | None = None


@dataclass(frozen=True)
class SimilarityResult:
    """Full result of a similarity query."""

    instrument: str
    feature_version: str
    similarity_version: str
    horizon_minutes: int
    candidate_count: int                       # total candidates considered
    sample_size: int                            # independent neighbors after dedup
    neighbors: list[NeighborMatch]              # top-K after dedup
    min_similarity: float | None                # lowest similarity in the kept set


class SimilarityEngine:
    """Deterministic weighted-distance similarity engine."""

    def __init__(self, config: LearningConfig | None = None) -> None:
        self.config = config or LearningConfig()
        self.weights = self.config.weights

    def distance(self, a: FeatureVector, b: FeatureVector) -> float:
        """Weighted normalized distance in [0, 1]."""
        total_weight = 0.0
        total_distance = 0.0

        # Continuous features: |a - b| distance (already normalized to ~[0,1])
        for attr, weight in [
            ("rsi_normalized", self.weights.rsi),
            ("ema_distance_atr", self.weights.ema_distance_pct),
            ("atr_pct", self.weights.atr_pct),
            ("distance_to_support_atr", self.weights.distance_to_support_atr),
            ("distance_to_resistance_atr", self.weights.distance_to_resistance_atr),
            ("volatility_percentile", self.weights.volatility_percentile),
            ("timeframe_alignment_score", self.weights.timeframe_alignment_score),
        ]:
            av = getattr(a, attr)
            bv = getattr(b, attr)
            if av is None or bv is None:
                # Missing data: contribute 0 to numerator AND denominator so
                # the distance is not unfairly penalized.
                continue
            # Normalize continuous distances to [0, 1] conservatively.
            # Most of our continuous features are already in roughly [0, 1]
            # or [-1, 1]; clipping handles outliers.
            d = min(1.0, abs(float(av) - float(bv)))
            total_distance += weight * d
            total_weight += weight

        # Categorical features: 0 (exact match) or 1 (mismatch)
        for attr, weight in [
            ("trend", self.weights.trend),
            ("market_regime", self.weights.market_regime),
            ("swing_structure", self.weights.swing_structure),
            ("h1_direction", self.weights.h1_direction),
            ("h4_direction", self.weights.h4_direction),
            ("d1_direction", self.weights.d1_direction),
            ("session", self.weights.session),
        ]:
            av = getattr(a, attr)
            bv = getattr(b, attr)
            if av is None or bv is None:
                continue
            d = 0.0 if av == bv else 1.0
            total_distance += weight * d
            total_weight += weight

        if total_weight == 0:
            return 1.0  # no comparable features → maximum distance
        return total_distance / total_weight

    def similarity_score(self, a: FeatureVector, b: FeatureVector) -> float:
        """Similarity in [0, 1] (1 = identical, 0 = maximally different)."""
        return round(1.0 - self.distance(a, b), 4)

    def dedup_neighbors(
        self,
        ranked: list[tuple[int, float, datetime, FeatureVector]],
        *,
        min_spacing_seconds: int,
    ) -> list[tuple[int, float, datetime, FeatureVector]]:
        """Temporal de-duplication: keep only the BEST match per time bucket.

        Prevents 5 consecutive similar candles from counting as 5
        independent neighbors.
        """
        # Sort by similarity DESC (already done by caller), then walk forward.
        # Greedily accept neighbors, rejecting any whose timestamp is within
        # min_spacing_seconds of an already-accepted neighbor.
        accepted: list[tuple[int, float, datetime, FeatureVector]] = []
        accepted_timestamps: list[datetime] = []
        for state_id, score, ts, fv in ranked:
            too_close = any(
                abs((ts - other).total_seconds()) < min_spacing_seconds
                for other in accepted_timestamps
            )
            if too_close:
                continue
            accepted.append((state_id, score, ts, fv))
            accepted_timestamps.append(ts)
        return accepted

    def rank_and_dedup(
        self,
        query: FeatureVector,
        candidates: list[tuple[int, datetime, FeatureVector]],
        *,
        min_spacing_seconds: int,
        top_k: int,
    ) -> list[tuple[int, float, datetime, FeatureVector]]:
        """Compute similarity for every candidate, rank, dedup, return top-K."""
        scored: list[tuple[int, float, datetime, FeatureVector]] = []
        for state_id, ts, fv in candidates:
            score = self.similarity_score(query, fv)
            scored.append((state_id, score, ts, fv))
        # Rank by similarity DESC. Tie-break by timestamp DESC (later states
        # preferred when identical similarity) — deterministic, no id tie-break.
        scored.sort(key=lambda x: (-x[1], -x[2].timestamp()))
        deduped = self.dedup_neighbors(scored, min_spacing_seconds=min_spacing_seconds)
        return deduped[:top_k]
