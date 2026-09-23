"""Phase 4: statistics aggregator with Wilson confidence intervals.

For each horizon, computes:
  sample_size
  up_count / down_count / neutral_count
  up_rate / down_rate / neutral_rate (each with 95% Wilson interval)
  median_return, mean_return
  median_MFE, median_MAE, mean_MFE, mean_MAE
  return_distribution: 25th / 50th / 75th percentiles

Sample quality classification (configurable, NOT optimized):
  < 30   → INSUFFICIENT
  30-99  → LOW
  100-299 → MODERATE
  300+   → GOOD
"""

from __future__ import annotations

import math
import statistics as py_stats
from dataclasses import dataclass, field

from app.services.learning.config import SAMPLE_QUALITY_THRESHOLDS
from app.services.learning.outcomes import OutcomeWindow


@dataclass(frozen=True)
class DirectionRate:
    """One direction's rate with 95% Wilson confidence interval."""

    count: int
    rate: float                                   # 0..1
    wilson_lower: float                            # 0..1
    wilson_upper: float                            # 0..1


@dataclass(frozen=True)
class HorizonStatistics:
    """Per-horizon sample statistics."""

    horizon_minutes: int
    sample_size: int
    up_count: int
    down_count: int
    neutral_count: int
    up_rate: DirectionRate
    down_rate: DirectionRate
    neutral_rate: DirectionRate
    median_return: float | None
    mean_return: float | None
    median_mfe: float | None
    median_mae: float | None
    mean_mfe: float | None
    mean_mae: float | None
    return_25th: float | None
    return_50th: float | None
    return_75th: float | None
    sample_quality: str                            # INSUFFICIENT / LOW / MODERATE / GOOD


@dataclass(frozen=True)
class StatisticsSummary:
    """Full statistics summary across all horizons."""

    by_horizon: dict[int, HorizonStatistics]
    overall_sample_size: int                       # min sample across horizons
    sample_quality: str                            # worst-case quality across horizons


@dataclass
class SampleQuality:
    """Sample-quality classification helper."""

    INSUFFICIENT: int = SAMPLE_QUALITY_THRESHOLDS["INSUFFICIENT"]
    LOW: int = SAMPLE_QUALITY_THRESHOLDS["LOW"]
    MODERATE: int = SAMPLE_QUALITY_THRESHOLDS["MODERATE"]

    def classify(self, n: int) -> str:
        if n < self.INSUFFICIENT:
            return "INSUFFICIENT"
        if n < self.LOW:
            return "LOW"
        if n < self.MODERATE:
            return "MODERATE"
        return "GOOD"


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score confidence interval for a binomial proportion.

    Returns (lower, upper) in [0, 1]. If total == 0, returns (0, 0).
    """
    if total <= 0:
        return 0.0, 0.0
    if successes < 0:
        successes = 0
    if successes > total:
        successes = total
    p = successes / total
    n = total
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def percentile(values: list[float], p: float) -> float | None:
    """Linear-interpolation percentile. p in [0, 100]."""
    if not values:
        return None
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


class StatisticsAggregator:
    """Aggregates per-horizon sample statistics from outcome windows."""

    def __init__(self) -> None:
        self.quality_classifier = SampleQuality()

    def aggregate(
        self,
        outcomes_by_state: list[dict[int, OutcomeWindow]],
        horizons: tuple[int, ...] = (15, 30, 60, 120, 240, 480, 1440),
    ) -> StatisticsSummary:
        """Aggregate outcome windows across all neighbor states.

        outcomes_by_state: list of {horizon_minutes: OutcomeWindow} dicts
                           (one dict per neighbor state).
        """
        by_horizon: dict[int, HorizonStatistics] = {}
        worst_quality = "GOOD"
        worst_sample = math.inf

        for h in horizons:
            windows = [ow[h] for ow in outcomes_by_state if h in ow and ow[h].direction is not None and ow[h].direction != "NULL"]
            sample_size = len(windows)
            if sample_size == 0:
                up_rate = down_rate = neutral_rate = DirectionRate(0, 0.0, 0.0, 0.0)
                stats = HorizonStatistics(
                    horizon_minutes=h, sample_size=0,
                    up_count=0, down_count=0, neutral_count=0,
                    up_rate=up_rate, down_rate=down_rate, neutral_rate=neutral_rate,
                    median_return=None, mean_return=None,
                    median_mfe=None, median_mae=None,
                    mean_mfe=None, mean_mae=None,
                    return_25th=None, return_50th=None, return_75th=None,
                    sample_quality="INSUFFICIENT",
                )
                by_horizon[h] = stats
                if "INSUFFICIENT" < worst_quality or worst_quality == "GOOD":
                    worst_quality = "INSUFFICIENT"
                worst_sample = min(worst_sample, 0)
                continue

            up = sum(1 for w in windows if w.direction == "UP")
            down = sum(1 for w in windows if w.direction == "DOWN")
            neutral = sum(1 for w in windows if w.direction == "NEUTRAL")

            up_lo, up_hi = wilson_interval(up, sample_size)
            down_lo, down_hi = wilson_interval(down, sample_size)
            neu_lo, neu_hi = wilson_interval(neutral, sample_size)

            pct_changes = [w.percentage_change for w in windows if w.percentage_change is not None]
            mfes = [w.mfe for w in windows if w.mfe is not None]
            maes = [w.mae for w in windows if w.mae is not None]

            stats = HorizonStatistics(
                horizon_minutes=h,
                sample_size=sample_size,
                up_count=up, down_count=down, neutral_count=neutral,
                up_rate=DirectionRate(up, up / sample_size, up_lo, up_hi),
                down_rate=DirectionRate(down, down / sample_size, down_lo, down_hi),
                neutral_rate=DirectionRate(neutral, neutral / sample_size, neu_lo, neu_hi),
                median_return=round(py_stats.median(pct_changes), 4) if pct_changes else None,
                mean_return=round(py_stats.mean(pct_changes), 4) if pct_changes else None,
                median_mfe=round(py_stats.median(mfes), 4) if mfes else None,
                median_mae=round(py_stats.median(maes), 4) if maes else None,
                mean_mfe=round(py_stats.mean(mfes), 4) if mfes else None,
                mean_mae=round(py_stats.mean(maes), 4) if maes else None,
                return_25th=round(percentile(pct_changes, 25), 4) if pct_changes else None,
                return_50th=round(percentile(pct_changes, 50), 4) if pct_changes else None,
                return_75th=round(percentile(pct_changes, 75), 4) if pct_changes else None,
                sample_quality=self.quality_classifier.classify(sample_size),
            )
            by_horizon[h] = stats
            # Worst-case quality across horizons
            q = stats.sample_quality
            q_rank = {"INSUFFICIENT": 0, "LOW": 1, "MODERATE": 2, "GOOD": 3}
            if q_rank.get(q, 0) < q_rank.get(worst_quality, 3):
                worst_quality = q
            worst_sample = min(worst_sample, sample_size)

        return StatisticsSummary(
            by_horizon=by_horizon,
            overall_sample_size=int(worst_sample) if worst_sample != math.inf else 0,
            sample_quality=worst_quality,
        )

    def alignment_for_decision(
        self,
        technical_decision: str,
        stats: HorizonStatistics,
    ) -> str:
        """Compute historical_alignment (SUPPORTS / CONTRADICTS / NEUTRAL / INSUFFICIENT_DATA).

        Rule:
          - INSUFFICIENT_DATA if sample_size < INSUFFICIENT threshold (30)
          - SUPPORTS if the dominant direction matches the technical decision:
              BUY + UP dominant => SUPPORTS
              SELL + DOWN dominant => SUPPORTS
              WAIT + NEUTRAL dominant => SUPPORTS
          - CONTRADICTS if the OPPOSITE direction is dominant:
              BUY + DOWN dominant => CONTRADICTS
              SELL + UP dominant => CONTRADICTS
          - NEUTRAL otherwise (no clear dominant direction or WAIT + UP/DOWN dominant)
        """
        if stats.sample_size < self.quality_classifier.INSUFFICIENT:
            return "INSUFFICIENT_DATA"

        # Determine dominant direction
        if stats.up_count > stats.down_count and stats.up_count > stats.neutral_count:
            dominant = "UP"
        elif stats.down_count > stats.up_count and stats.down_count > stats.neutral_count:
            dominant = "DOWN"
        elif stats.neutral_count > stats.up_count and stats.neutral_count > stats.down_count:
            dominant = "NEUTRAL"
        else:
            # Tie — no clear dominant
            return "NEUTRAL"

        if technical_decision == "BUY":
            return "SUPPORTS" if dominant == "UP" else ("CONTRADICTS" if dominant == "DOWN" else "NEUTRAL")
        if technical_decision == "SELL":
            return "SUPPORTS" if dominant == "DOWN" else ("CONTRADICTS" if dominant == "UP" else "NEUTRAL")
        if technical_decision == "WAIT":
            return "SUPPORTS" if dominant == "NEUTRAL" else "NEUTRAL"
        return "NEUTRAL"
