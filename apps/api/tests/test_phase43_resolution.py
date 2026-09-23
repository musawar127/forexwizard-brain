"""Phase 4.3 tests: outcome resolution rules, job reconciliation,
outcome_version persistence, multi-resolution selection.
"""

from __future__ import annotations

import pytest

from app.services.learning.resolution_rules import (
    HORIZON_RESOLUTION_RULES,
    OUTCOME_VERSION_V02,
    is_resolution_sufficient,
    select_best_source_timeframe,
)


# ===========================================================================
# 1. Resolution rules — 15m cannot use H1
# ===========================================================================

def test_15m_cannot_use_h1():
    """15m horizon: H1 is NOT a valid source TF (1h > 15m)."""
    assert not is_resolution_sufficient(15, "1h")
    assert not is_resolution_sufficient(15, "4h")
    assert not is_resolution_sufficient(15, "1day")


def test_15m_can_use_m1_m5_m15():
    """15m horizon: M1, M5, M15 are valid source TFs."""
    assert is_resolution_sufficient(15, "1min")
    assert is_resolution_sufficient(15, "5min")
    assert is_resolution_sufficient(15, "15min")


def test_30m_cannot_use_h1():
    """30m horizon: H1 is NOT a valid source TF (1h > 30m)."""
    assert not is_resolution_sufficient(30, "1h")


def test_30m_can_use_m5_m15_m30():
    assert is_resolution_sufficient(30, "5min")
    assert is_resolution_sufficient(30, "15min")
    assert is_resolution_sufficient(30, "30min")


def test_1h_can_use_h1():
    """1h horizon: H1 IS valid (1h = 1h)."""
    assert is_resolution_sufficient(60, "1h")
    assert is_resolution_sufficient(120, "1h")
    assert is_resolution_sufficient(240, "1h")
    assert is_resolution_sufficient(480, "1h")


def test_24h_can_use_h1_and_h4_and_d1():
    """24h horizon: H1, H4, D1 are all valid."""
    assert is_resolution_sufficient(1440, "1h")
    assert is_resolution_sufficient(1440, "4h")
    assert is_resolution_sufficient(1440, "1day")


# ===========================================================================
# 2. Best available source TF selection
# ===========================================================================

def test_best_source_tf_prefers_finest():
    """For 15m, if M1 is available, it should be selected (finest resolution)."""
    selected, sufficient = select_best_source_timeframe(15, ["1min", "5min", "15min", "1h"])
    assert selected == "1min"
    assert sufficient is True


def test_best_source_tf_falls_back_to_m5():
    """For 15m, if M1 is NOT available but M5 is, select M5."""
    selected, sufficient = select_best_source_timeframe(15, ["5min", "15min", "1h"])
    assert selected == "5min"
    assert sufficient is True


def test_best_source_tf_returns_insufficient_when_only_h1():
    """For 15m, if only H1 is available, no valid TF is found → (None, False)."""
    selected, sufficient = select_best_source_timeframe(15, ["1h", "4h"])
    assert selected is None  # no valid TF for 15m from H1/H4
    assert sufficient is False


def test_best_source_tf_for_1h_with_h1():
    """For 1h, H1 IS valid."""
    selected, sufficient = select_best_source_timeframe(60, ["1h", "4h"])
    assert selected == "1h"
    assert sufficient is True


def test_best_source_tf_for_30m_with_m5():
    """For 30m, M5 is valid."""
    selected, sufficient = select_best_source_timeframe(30, ["5min", "1h"])
    assert selected == "5min"
    assert sufficient is True


# ===========================================================================
# 3. Outcome version persistence
# ===========================================================================

def test_outcome_version_v02_exists():
    assert OUTCOME_VERSION_V02 == "outcomes-v0.2"


def test_old_runs_keep_v01_default():
    """SimilarityRun.outcome_version defaults to outcomes-v0.1."""
    from app.db.models import SimilarityRun
    col = SimilarityRun.__table__.c.outcome_version
    assert col.default.arg == "outcomes-v0.1"  # type: ignore


def test_outcome_default_version_on_historical_outcome():
    """HistoricalOutcome.outcome_version defaults to outcomes-v0.1."""
    from app.db.models import HistoricalOutcome
    col = HistoricalOutcome.__table__.c.outcome_version
    assert col.default.arg == "outcomes-v0.1"  # type: ignore


# ===========================================================================
# 4. Job reconciliation fields exist
# ===========================================================================

def test_buildjob_has_reconciliation_fields():
    """BuildJob must have db_state_count, counter_state_count, counter_db_difference."""
    from app.db.models import BuildJob
    cols = {c.name for c in BuildJob.__table__.columns}
    assert "db_state_count" in cols
    assert "counter_state_count" in cols
    assert "counter_db_difference" in cols
    assert "reconciliation_warning" in cols


# ===========================================================================
# 5. No fake interpolation
# ===========================================================================

def test_no_fake_interpolation_in_resolution_rules():
    """Resolution rules must NOT include any TF that doesn't exist in INTERVALS."""
    from app.engine.candles import INTERVALS
    for horizon, tfs in HORIZON_RESOLUTION_RULES.items():
        for tf in tfs:
            assert tf in INTERVALS, f"Unknown TF {tf} in resolution rules for {horizon}m"


# ===========================================================================
# 6. Resolution rules cover all horizons
# ===========================================================================

def test_all_horizons_have_resolution_rules():
    """Every HORIZON_MINUTES value must have a resolution rule."""
    from app.services.learning.config import HORIZON_MINUTES
    for h in HORIZON_MINUTES:
        assert h in HORIZON_RESOLUTION_RULES, f"Missing resolution rule for {h}m"
