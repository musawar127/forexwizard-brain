"""Phase 5.6: risk/reward calculator.

R:R = reward / risk
where:
  risk   = |entry_reference - stop_loss|
  reward = |TP - entry_reference|

We document that entry_reference is the zone midpoint — display
calculations use that as a single reference point. R:R is NOT
labeled as probability; it is a reward-to-risk distance ratio.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RewardRR:
    reward: float
    rr: float


def compute_rr(
    *,
    entry_reference: float,
    stop_loss: float,
    tps: list[float],
) -> tuple[float, list[RewardRR]]:
    """Compute risk_distance and per-TP reward + R:R.

    Returns:
      (risk_distance, [RewardRR per TP, same order as tps])
    """
    if entry_reference is None or stop_loss is None:
        raise ValueError("entry_reference and stop_loss required")
    risk_distance = abs(entry_reference - stop_loss)
    if risk_distance <= 0:
        raise ValueError("non-positive risk distance — entry == SL")
    rewards: list[RewardRR] = []
    for tp in tps:
        reward = abs(tp - entry_reference)
        rr = reward / risk_distance if risk_distance > 0 else 0.0
        rewards.append(RewardRR(reward=round(reward, 2), rr=round(rr, 2)))
    return round(risk_distance, 2), rewards
