"""Phase 5.6: target engine — TP1, TP2, TP3, TP4.

Targets are derived from genuine structural levels (swing lows/highs,
prior support/resistance) and ATR-based extensions. Targets are NEVER
arbitrary fixed-R multiples — every target needs a structural reason.

Sources, in priority order:
  1. Prior swing high (for BUY) / low (for SELL) of recent structure
  2. Opposite zone (resistance for BUY, support for SELL)
  3. ATR extension multiple (1.5x, 2.5x, 3.5x, 4.5x of risk distance)
     — only used to fill in if structural levels are exhausted

Constraint: for BUY, TP1 < TP2 < TP3 < TP4 (ascending, all > entry).
          for SELL, TP1 > TP2 > TP3 > TP4 (descending, all < entry).

Targets are rejected if any TP is on the wrong side of entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.market import Candle, Zone


@dataclass
class TargetResult:
    status: Literal["OK", "NO_VALID_TP"]
    tps: list[float] | None = None  # ordered list of [tp1, tp2, tp3, tp4]
    reasons: list[str] | None = None  # reason per TP, parallel to tps
    reason: str | None = None  # high-level failure reason


def _recent_swings(candles: list[Candle], lookback: int = 50) -> tuple[list[float], list[float]]:
    """Return (recent swing highs, recent swing lows) sorted by recency."""
    if not candles:
        return [], []
    recent = candles[-min(lookback, len(candles)):]
    # Simple swing detection: local max/min over a 3-candle window
    highs: list[float] = []
    lows: list[float] = []
    for i in range(1, len(recent) - 1):
        c = recent[i]
        if c.high > recent[i - 1].high and c.high > recent[i + 1].high:
            highs.append(c.high)
        if c.low < recent[i - 1].low and c.low < recent[i + 1].low:
            lows.append(c.low)
    # Always include the absolute recent high/low
    if recent:
        highs.append(max(c.high for c in recent))
        lows.append(min(c.low for c in recent))
    # Reverse so most recent is first
    return list(reversed(highs)), list(reversed(lows))


def derive_buy_targets(
    *,
    entry_reference: float,
    stop_loss: float,
    risk_distance: float,
    resistance: Zone | None,
    candles: list[Candle],
    atr: float | None,
) -> TargetResult:
    """Derive up to 4 BUY targets on the upside.

    Target candidates (most structural first):
      - resistance zone (if exists and above entry)
      - recent swing highs above entry
      - ATR-measured-move extensions (1.5R, 2.5R, 3.5R, 4.5R)
    """
    if entry_reference is None or stop_loss is None or risk_distance is None:
        return TargetResult(status="NO_VALID_TP", reason="missing entry/SL/risk")
    if risk_distance <= 0:
        return TargetResult(status="NO_VALID_TP", reason="non-positive risk distance")

    # Candidate structural levels (price must be > entry)
    structural_candidates: list[tuple[float, str]] = []
    if resistance and resistance.high and resistance.high > entry_reference:
        structural_candidates.append((float(resistance.high), f"resistance zone high ({round(resistance.high, 2)})"))

    swing_highs, _ = _recent_swings(candles)
    for sh in swing_highs:
        if sh > entry_reference + 0.05:  # at least 5 cents above entry
            structural_candidates.append((sh, f"recent swing high ({round(sh, 2)})"))

    # ATR/risk extensions — last resort, only used when structural levels run out
    r_multiples = [1.5, 2.5, 3.5, 4.5]
    atr_extensions: list[tuple[float, str]] = []
    for r in r_multiples:
        level = round(entry_reference + r * risk_distance, 2)
        atr_extensions.append((level, f"ATR extension {r}R"))

    # Combine + deduplicate (keep first occurrence of each level)
    seen: set[float] = set()
    tps: list[float] = []
    reasons: list[str] = []
    for level, reason in structural_candidates + atr_extensions:
        # Round to 2 decimals to dedupe effectively
        rounded = round(level, 2)
        if rounded in seen:
            continue
        seen.add(rounded)
        # Sanity: target must be > entry (BUY side)
        if rounded <= entry_reference:
            continue
        tps.append(rounded)
        reasons.append(reason)
        if len(tps) >= 4:
            break

    if len(tps) < 4:
        return TargetResult(
            status="NO_VALID_TP",
            reason=f"only {len(tps)} targets derivable, need 4",
            tps=tps,
            reasons=reasons,
        )

    # Sort ascending (BUY: TP1 < TP2 < TP3 < TP4)
    paired = sorted(zip(tps, reasons), key=lambda x: x[0])
    sorted_tps = [p[0] for p in paired]
    sorted_reasons = [p[1] for p in paired]

    # Validate strict ordering
    for i in range(len(sorted_tps) - 1):
        if sorted_tps[i] >= sorted_tps[i + 1]:
            return TargetResult(
                status="NO_VALID_TP",
                reason=f"non-ascending targets at index {i}: {sorted_tps[i]} >= {sorted_tps[i + 1]}",
            )

    return TargetResult(status="OK", tps=sorted_tps, reasons=sorted_reasons)


def derive_sell_targets(
    *,
    entry_reference: float,
    stop_loss: float,
    risk_distance: float,
    support: Zone | None,
    candles: list[Candle],
    atr: float | None,
) -> TargetResult:
    """Derive up to 4 SELL targets on the downside."""
    if entry_reference is None or stop_loss is None or risk_distance is None:
        return TargetResult(status="NO_VALID_TP", reason="missing entry/SL/risk")
    if risk_distance <= 0:
        return TargetResult(status="NO_VALID_TP", reason="non-positive risk distance")

    structural_candidates: list[tuple[float, str]] = []
    if support and support.low and support.low < entry_reference:
        structural_candidates.append((float(support.low), f"support zone low ({round(support.low, 2)})"))

    _, swing_lows = _recent_swings(candles)
    for sl_ in swing_lows:
        if sl_ < entry_reference - 0.05:
            structural_candidates.append((sl_, f"recent swing low ({round(sl_, 2)})"))

    r_multiples = [1.5, 2.5, 3.5, 4.5]
    atr_extensions: list[tuple[float, str]] = []
    for r in r_multiples:
        level = round(entry_reference - r * risk_distance, 2)
        atr_extensions.append((level, f"ATR extension {r}R"))

    seen: set[float] = set()
    tps: list[float] = []
    reasons: list[str] = []
    for level, reason in structural_candidates + atr_extensions:
        rounded = round(level, 2)
        if rounded in seen:
            continue
        seen.add(rounded)
        if rounded >= entry_reference:
            continue
        tps.append(rounded)
        reasons.append(reason)
        if len(tps) >= 4:
            break

    if len(tps) < 4:
        return TargetResult(
            status="NO_VALID_TP",
            reason=f"only {len(tps)} targets derivable, need 4",
            tps=tps,
            reasons=reasons,
        )

    # Sort descending (SELL: TP1 > TP2 > TP3 > TP4)
    paired = sorted(zip(tps, reasons), key=lambda x: -x[0])
    sorted_tps = [p[0] for p in paired]
    sorted_reasons = [p[1] for p in paired]

    for i in range(len(sorted_tps) - 1):
        if sorted_tps[i] <= sorted_tps[i + 1]:
            return TargetResult(
                status="NO_VALID_TP",
                reason=f"non-descending targets at index {i}: {sorted_tps[i]} <= {sorted_tps[i + 1]}",
            )

    return TargetResult(status="OK", tps=sorted_tps, reasons=sorted_reasons)
