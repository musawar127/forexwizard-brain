"""Phase 4.3: Outcome resolution rules per horizon.

Defines which source timeframes are valid for each outcome horizon.
Prevents the 15m/30m issue where H1 candles (spanning 1 hour) were
incorrectly used for sub-hour horizons.

Rules (deterministic, not configurable — these are mathematical facts):
  - Source TF interval must be <= horizon interval
  - Among valid TFs, prefer the LOWEST available (finest resolution)
  - If no valid TF has data, resolution_sufficient=False

Example:
  15m horizon: M1 ✓, M5 ✓, M15 ✓, M30 ✗ (30m > 15m), H1 ✗, H4 ✗, D1 ✗
  30m horizon: M1 ✓, M5 ✓, M15 ✓, M30 ✓, H1 ✗ (1h > 30m), H4 ✗, D1 ✗
  60m horizon: M1 ✓, M5 ✓, M15 ✓, M30 ✓, H1 ✓ (1h = 1h), H4 ✗, D1 ✗
  120m horizon: any TF <= 1h ✓, H4 ✗
  240m horizon: any TF <= 1h ✓, H4 ✗ (4h = 4h, but we prefer lower)
  480m horizon: any TF <= 1h ✓, H4 ✓ (4h < 8h)
  1440m horizon: any TF <= 4h ✓ (H1 ✓, H4 ✓, D1 ✗ because D1 > 24h? No, 1d = 24h = 1440m, so D1 ✓)
"""

from __future__ import annotations

from app.engine.candles import INTERVALS


# Required resolution per horizon: list of valid source TFs, ordered by
# preference (lowest interval first = finest resolution first).
HORIZON_RESOLUTION_RULES: dict[int, list[str]] = {
    15: ["1min", "5min", "15min"],
    30: ["1min", "5min", "15min", "30min"],
    60: ["1min", "5min", "15min", "30min", "1h"],
    120: ["1min", "5min", "15min", "30min", "1h"],
    240: ["1min", "5min", "15min", "30min", "1h"],
    480: ["1min", "5min", "15min", "30min", "1h", "4h"],
    1440: ["1min", "5min", "15min", "30min", "1h", "4h", "1day"],
}

# Outcome computation version. v0.1 = H1 for all horizons (incorrect for
# sub-hour). v0.2 = correct source TF per horizon.
OUTCOME_VERSION_V02 = "outcomes-v0.2"


def is_resolution_sufficient(horizon_minutes: int, source_timeframe: str) -> bool:
    """Check if the source timeframe is valid for the given horizon.

    A source TF is valid if its interval is <= the horizon interval.
    """
    rules = HORIZON_RESOLUTION_RULES.get(horizon_minutes, [])
    return source_timeframe in rules


def select_best_source_timeframe(
    horizon_minutes: int,
    available_tfs: list[str],
) -> tuple[str | None, bool]:
    """Select the best available source timeframe for a given horizon.

    Returns (selected_tf, resolution_sufficient).
    - selected_tf: the lowest-interval TF from `available_tfs` that is valid
      for this horizon. None if no valid TF is available.
    - resolution_sufficient: True if a valid TF was found, False otherwise.
    """
    rules = HORIZON_RESOLUTION_RULES.get(horizon_minutes, [])
    available_set = set(available_tfs)
    for tf in rules:  # ordered by preference (finest first)
        if tf in available_set:
            return tf, True
    return None, False


def get_resolution_rule_for_horizon(horizon_minutes: int) -> list[str]:
    """Return the list of valid source TFs for a horizon (ordered by preference)."""
    return HORIZON_RESOLUTION_RULES.get(horizon_minutes, [])
