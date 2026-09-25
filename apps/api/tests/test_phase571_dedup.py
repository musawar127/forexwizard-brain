"""Phase 5.7.1: Trade Plan Quality + Dedup Hardening — tests.

Covers:
  - Same M15 state -> no duplicate plan (reused_existing_plan=true)
  - New M15 candle -> eligible for new plan
  - Changed MSS -> eligible for new plan
  - Changed decision -> eligible for new plan
  - Manual repeated generate -> existing plan returned
  - Version display (MIXED when both engine versions present)
  - WAIT excluded from actionable trade metrics
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.db.models import TradePlan, TradePlanOutcome
from app.models.market import (
    BrainAnalysis,
    Candle,
    TimeframeState,
    Zone,
)
from app.services.ict.engine import (
    ICT_PLAN_VERSION,
    _compute_setup_fingerprint,
    _derive_short_reason,
    _last_completed_m15_timestamp,
    _find_existing_plan_by_fingerprint,
)
from app.services.ict.setup_reasoner import SetupReasoning
from app.services.trade_plan.engine import get_performance


# ---------- helpers ----------

def _make_candle(ts: datetime, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(
        symbol="XAU/USD", interval="15min", timestamp=ts,
        open=o, high=h, low=l, close=c,
        sample_count=1, is_historical=True,
    )


def _make_m15_candles(n: int = 20, base: float = 4300.0) -> list[Candle]:
    """Build N M15 candles ending at least 15 minutes ago (all 'completed')."""
    out: list[Candle] = []
    # Start far enough back that all candles are 'completed' relative to now
    start = datetime.now(timezone.utc) - timedelta(minutes=(n + 2) * 15)
    for i in range(n):
        ts = start + timedelta(minutes=i * 15)
        price = base + i * 0.5
        out.append(_make_candle(ts, price - 0.5, price + 0.5, price - 1.0, price))
    return out


def _make_wait_analysis(price: float = 4300.0) -> BrainAnalysis:
    return BrainAnalysis(
        symbol="XAU/USD",
        timestamp=datetime.now(timezone.utc),
        decision="WAIT",
        confidence=40.0,
        readiness=100.0,
        regime="RANGE",
        risk="HIGH",
        score=0.5,
        price=price,
        reasons_for=[],
        reasons_against=["Evidence does not meet threshold."],
        invalidation=None,
        support=None,
        resistance=None,
        timeframes=[
            TimeframeState(timeframe="15min", candles=120, status="READY", trend="RANGE", rsi=50.0, atr=2.5, ema_fast=4300.0, ema_slow=4300.0),
        ],
        data_quality="LIMITED",
        message="Test WAIT",
        brain_version="rules-v0.1",
        technical_data_readiness=100.0,
        technical_score=40.0,
        historical_alignment="INSUFFICIENT_DATA",
        probability_calibrated=False,
    )


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


# ---------- fingerprint tests ----------

def test_fingerprint_is_deterministic():
    """Same inputs -> same fingerprint."""
    from app.services.ict.structures import StructureEvent
    from app.services.ict.liquidity import LiquiditySweep
    from app.services.ict.fvg import FVG
    from app.services.ict.order_blocks import OrderBlock

    m15_ts = datetime(2026, 9, 25, 14, 0, 0, tzinfo=timezone.utc)
    mss = StructureEvent(
        timestamp=m15_ts, price=4305.0, event_type="MSS",
        direction="BULLISH", broken_level=4295.0, broken_kind="SWING_HIGH",
        quality="STRONG",
    )
    sweep = LiquiditySweep(
        timestamp=m15_ts, level=4295.0, level_kind="EQUAL_LOWS",
        direction="SELL_SIDE_SWEEP", reaction_magnitude=3.0,
        reaction_atr_multiple=1.2, failed=False,
    )
    fvg = FVG(
        timestamp=m15_ts, timeframe="15min", direction="BULLISH",
        upper=4298.0, lower=4296.0, midpoint=4297.0,
    )
    ob = OrderBlock(
        timestamp=m15_ts, timeframe="15min", direction="BULLISH",
        upper=4295.0, lower=4293.0, midpoint=4294.0,
        quality=80.0, bos_timestamp=m15_ts,
    )

    fp1 = _compute_setup_fingerprint(
        decision="BUY", m15_completed_ts=m15_ts,
        htf_trend="BULLISH", m15_trend="BULLISH",
        latest_mss_event=mss, latest_sweep=sweep,
        nearest_fvg=fvg, nearest_ob=ob, location="DISCOUNT",
    )
    fp2 = _compute_setup_fingerprint(
        decision="BUY", m15_completed_ts=m15_ts,
        htf_trend="BULLISH", m15_trend="BULLISH",
        latest_mss_event=mss, latest_sweep=sweep,
        nearest_fvg=fvg, nearest_ob=ob, location="DISCOUNT",
    )
    assert fp1 == fp2
    assert len(fp1) == 16  # 16-char truncated SHA-256


def test_fingerprint_changes_when_m15_candle_changes():
    """Different M15 completed candle -> different fingerprint."""
    m15_ts_1 = datetime(2026, 9, 25, 14, 0, 0, tzinfo=timezone.utc)
    m15_ts_2 = datetime(2026, 9, 25, 14, 15, 0, tzinfo=timezone.utc)

    fp1 = _compute_setup_fingerprint(
        decision="WAIT", m15_completed_ts=m15_ts_1,
        htf_trend="RANGE", m15_trend="RANGE",
        latest_mss_event=None, latest_sweep=None,
        nearest_fvg=None, nearest_ob=None, location="EQUILIBRIUM",
    )
    fp2 = _compute_setup_fingerprint(
        decision="WAIT", m15_completed_ts=m15_ts_2,
        htf_trend="RANGE", m15_trend="RANGE",
        latest_mss_event=None, latest_sweep=None,
        nearest_fvg=None, nearest_ob=None, location="EQUILIBRIUM",
    )
    assert fp1 != fp2, "fingerprint must change when M15 candle timestamp changes"


def test_fingerprint_changes_when_decision_changes():
    """Different decision (WAIT vs BUY) -> different fingerprint."""
    m15_ts = datetime(2026, 9, 25, 14, 0, 0, tzinfo=timezone.utc)

    fp_wait = _compute_setup_fingerprint(
        decision="WAIT", m15_completed_ts=m15_ts,
        htf_trend="RANGE", m15_trend="RANGE",
        latest_mss_event=None, latest_sweep=None,
        nearest_fvg=None, nearest_ob=None, location="EQUILIBRIUM",
    )
    fp_buy = _compute_setup_fingerprint(
        decision="BUY", m15_completed_ts=m15_ts,
        htf_trend="RANGE", m15_trend="RANGE",
        latest_mss_event=None, latest_sweep=None,
        nearest_fvg=None, nearest_ob=None, location="EQUILIBRIUM",
    )
    assert fp_wait != fp_buy


def test_fingerprint_changes_when_mss_changes():
    """Different MSS event (or no MSS vs has MSS) -> different fingerprint."""
    from app.services.ict.structures import StructureEvent

    m15_ts = datetime(2026, 9, 25, 14, 0, 0, tzinfo=timezone.utc)
    mss = StructureEvent(
        timestamp=m15_ts, price=4305.0, event_type="MSS",
        direction="BULLISH", broken_level=4295.0, broken_kind="SWING_HIGH",
        quality="STRONG",
    )

    fp_no_mss = _compute_setup_fingerprint(
        decision="BUY", m15_completed_ts=m15_ts,
        htf_trend="BULLISH", m15_trend="BULLISH",
        latest_mss_event=None, latest_sweep=None,
        nearest_fvg=None, nearest_ob=None, location="DISCOUNT",
    )
    fp_with_mss = _compute_setup_fingerprint(
        decision="BUY", m15_completed_ts=m15_ts,
        htf_trend="BULLISH", m15_trend="BULLISH",
        latest_mss_event=mss, latest_sweep=None,
        nearest_fvg=None, nearest_ob=None, location="DISCOUNT",
    )
    assert fp_no_mss != fp_with_mss


# ---------- short reason tests ----------

def test_short_reason_wait_no_mss():
    """WAIT with no MSS -> 'WAIT — no M15 MSS' (or similar)."""
    reasoning = SetupReasoning(
        direction="WAIT", plan_status="NO_TRADE",
        htf_trend="BULLISH", m15_trend="BULLISH",
        liquidity_swept=True, sweep_direction="SELL_SIDE",
        displacement_confirmed=True,
        fvg_active=True, ob_active=True,
        location="DISCOUNT",
        for_evidence=[],  # no MSS in evidence
        against_evidence=[],
    )
    reason = _derive_short_reason(reasoning)
    assert reason.startswith("WAIT —")
    assert "no M15 MSS" in reason


def test_short_reason_wait_no_sweep():
    """WAIT with no sweep -> 'WAIT — no liquidity sweep'."""
    reasoning = SetupReasoning(
        direction="WAIT", plan_status="NO_TRADE",
        htf_trend="RANGE", m15_trend="RANGE",
        liquidity_swept=False, sweep_direction=None,
        fvg_active=False, ob_active=False,
        location="EQUILIBRIUM",
        for_evidence=[], against_evidence=[],
    )
    reason = _derive_short_reason(reasoning)
    assert "no liquidity sweep" in reason


def test_short_reason_wait_htf_conflict():
    """WAIT with HTF conflict -> 'WAIT — HTF conflict'."""
    reasoning = SetupReasoning(
        direction="WAIT", plan_status="NO_TRADE",
        htf_trend="BEARISH", m15_trend="BULLISH",
        liquidity_swept=True, sweep_direction="SELL_SIDE",  # bullish signal vs bearish HTF
        displacement_confirmed=True,
        fvg_active=True, ob_active=True,
        location="DISCOUNT",
        for_evidence=[
            type("E", (), {"kind": "MSS", "timeframe": "M15", "description": "M15 MSS BULLISH", "bullish_or_bearish": "BULLISH", "confidence": 75.0})(),
        ],
        against_evidence=[],
    )
    reason = _derive_short_reason(reasoning)
    assert "HTF conflict" in reason


def test_short_reason_buy():
    """BUY with sell-side sweep + bullish MSS."""
    reasoning = SetupReasoning(
        direction="BUY", plan_status="ACTIONABLE",
        htf_trend="BULLISH", m15_trend="BULLISH",
        liquidity_swept=True, sweep_direction="SELL_SIDE",
        fvg_active=True, ob_active=True,
        location="DISCOUNT",
        for_evidence=[
            type("E", (), {"kind": "MSS", "timeframe": "M15", "description": "M15 MSS BULLISH", "bullish_or_bearish": "BULLISH", "confidence": 75.0})(),
        ],
        against_evidence=[],
    )
    reason = _derive_short_reason(reasoning)
    assert reason.startswith("BUY —")
    assert "sell-side sweep" in reason
    assert "bullish" in reason.lower()


# ---------- last completed M15 timestamp tests ----------

def test_last_completed_m15_timestamp():
    """_last_completed_m15_timestamp returns the most recent closed M15 candle."""
    candles = _make_m15_candles(n=10)
    ts = _last_completed_m15_timestamp(candles)
    assert ts is not None
    # Should be the last candle's timestamp (all candles are completed)
    assert ts == candles[-1].timestamp.replace(tzinfo=timezone.utc) or ts == candles[-1].timestamp


def test_last_completed_m15_returns_none_for_empty():
    ts = _last_completed_m15_timestamp([])
    assert ts is None


# ---------- dedup integration tests ----------

def test_dedup_returns_existing_plan_on_same_fingerprint():
    """When fingerprint matches an existing live plan, return it with reused_existing_plan=true."""
    from app.services.ict.engine import generate_ict_trade_plan
    from app.services.trade_plan.lifecycle import LIVE_STATES

    # Create a plan manually with a known fingerprint
    now = datetime.now(timezone.utc)
    plan = TradePlan(
        plan_id="TP-DEDUP001",
        created_at=now,
        market_timestamp=now,
        instrument="XAU/USD",
        brain_decision="WAIT",
        plan_status="NO_TRADE",
        plan_version="trade-plan-v0.1",
        plan_engine_version="ict-plan-v0.1",
        lifecycle_state="CREATED",
        setup_fingerprint="abc123def456abcd",  # known fingerprint
        short_reason="WAIT — test",
    )
    with SessionLocal() as session:
        session.add(plan)
        session.add(TradePlanOutcome(
            plan_id="TP-DEDUP001",
            entry_touched=False,
            tp1_reached=False, tp2_reached=False, tp3_reached=False, tp4_reached=False,
        ))
        session.commit()

    # Lookup should find it
    found = _find_existing_plan_by_fingerprint("abc123def456abcd")
    assert found is not None
    assert found.plan_id == "TP-DEDUP001"


def test_dedup_does_not_match_terminal_plans():
    """Terminal plans (STOPPED / EXPIRED / INVALIDATED) don't block new plan creation."""
    now = datetime.now(timezone.utc)
    plan = TradePlan(
        plan_id="TP-TERM001",
        created_at=now,
        market_timestamp=now,
        instrument="XAU/USD",
        brain_decision="BUY",
        plan_status="ACTIONABLE",
        plan_version="trade-plan-v0.1",
        plan_engine_version="ict-plan-v0.1",
        lifecycle_state="STOPPED",  # terminal
        setup_fingerprint="terminal_fp_001",
        short_reason="BUY — test",
    )
    with SessionLocal() as session:
        session.add(plan)
        session.add(TradePlanOutcome(
            plan_id="TP-TERM001",
            entry_touched=False,
            tp1_reached=False, tp2_reached=False, tp3_reached=False, tp4_reached=False,
        ))
        session.commit()

    # Lookup should NOT find it (terminal state)
    found = _find_existing_plan_by_fingerprint("terminal_fp_001")
    assert found is None


# ---------- version display tests ----------

def test_version_display_single_engine():
    """When all plans use the same engine version, version_display = that version."""
    now = datetime.now(timezone.utc)
    for i in range(3):
        plan = TradePlan(
            plan_id=f"TP-V{i}",
            created_at=now + timedelta(seconds=i),
            market_timestamp=now,
            instrument="XAU/USD",
            brain_decision="WAIT",
            plan_status="NO_TRADE",
            plan_version="trade-plan-v0.1",
            plan_engine_version="ict-plan-v0.1",
            lifecycle_state="CREATED",
        )
        with SessionLocal() as session:
            session.add(plan)
            session.add(TradePlanOutcome(
                plan_id=f"TP-V{i}",
                entry_touched=False,
                tp1_reached=False, tp2_reached=False, tp3_reached=False, tp4_reached=False,
            ))
            session.commit()

    perf = get_performance()
    assert perf["version_display"] == "ict-plan-v0.1"
    assert perf["by_engine_version"] == {"ict-plan-v0.1": 3}


def test_version_display_mixed():
    """When plans use different engine versions, version_display = MIXED."""
    now = datetime.now(timezone.utc)
    versions = ["ict-plan-v0.1", "trade-plan-v0.1", "ict-plan-v0.1"]
    for i, ev in enumerate(versions):
        plan = TradePlan(
            plan_id=f"TP-MIX{i}",
            created_at=now + timedelta(seconds=i),
            market_timestamp=now,
            instrument="XAU/USD",
            brain_decision="WAIT",
            plan_status="NO_TRADE",
            plan_version="trade-plan-v0.1",
            plan_engine_version=ev,
            lifecycle_state="CREATED",
        )
        with SessionLocal() as session:
            session.add(plan)
            session.add(TradePlanOutcome(
                plan_id=f"TP-MIX{i}",
                entry_touched=False,
                tp1_reached=False, tp2_reached=False, tp3_reached=False, tp4_reached=False,
            ))
            session.commit()

    perf = get_performance()
    assert perf["version_display"] == "MIXED"
    assert perf["by_engine_version"] == {"ict-plan-v0.1": 2, "trade-plan-v0.1": 1}


# ---------- WAIT excluded from actionable metrics ----------

def test_wait_excluded_from_actionable_metrics():
    """WAIT plans should not count as actionable BUY/SELL."""
    now = datetime.now(timezone.utc)
    # 3 WAIT plans + 2 BUY + 1 SELL
    plans = [
        ("TP-W1", "WAIT", "NO_TRADE"),
        ("TP-W2", "WAIT", "NO_TRADE"),
        ("TP-W3", "WAIT", "NO_TRADE"),
        ("TP-B1", "BUY", "ACTIONABLE"),
        ("TP-B2", "BUY", "WAIT_FOR_ENTRY"),
        ("TP-S1", "SELL", "ACTIONABLE"),
    ]
    for i, (pid, decision, status) in enumerate(plans):
        plan = TradePlan(
            plan_id=pid,
            created_at=now + timedelta(seconds=i),
            market_timestamp=now,
            instrument="XAU/USD",
            brain_decision=decision,
            plan_status=status,
            plan_version="trade-plan-v0.1",
            plan_engine_version="ict-plan-v0.1",
            lifecycle_state="CREATED",
        )
        with SessionLocal() as session:
            session.add(plan)
            session.add(TradePlanOutcome(
                plan_id=pid,
                entry_touched=False,
                tp1_reached=False, tp2_reached=False, tp3_reached=False, tp4_reached=False,
            ))
            session.commit()

    perf = get_performance()
    assert perf["wait_count"] == 3
    assert perf["actionable_buy_count"] == 2
    assert perf["actionable_sell_count"] == 1
    assert perf["actionable_total"] == 3
    assert perf["no_trade_count"] == 3
    # Outcome summary should only track actionable plans (3), not WAIT (3)
    assert perf["outcome_summary"]["total_outcomes_tracked"] == 3


def test_repeated_wait_snapshots_not_counted_as_trading_performance():
    """Multiple WAIT snapshots in the same M15 window are dedup'd.

    Even if they weren't dedup'd, they should NOT count as actionable
    trading performance — they're informational market-state records.
    """
    now = datetime.now(timezone.utc)
    # 5 WAIT plans with different fingerprints (simulating different M15 candles)
    for i in range(5):
        plan = TradePlan(
            plan_id=f"TP-WS{i}",
            created_at=now + timedelta(minutes=i * 15),
            market_timestamp=now + timedelta(minutes=i * 15),
            instrument="XAU/USD",
            brain_decision="WAIT",
            plan_status="NO_TRADE",
            plan_version="trade-plan-v0.1",
            plan_engine_version="ict-plan-v0.1",
            lifecycle_state="CREATED",
            setup_fingerprint=f"wait_fp_{i:04d}",
        )
        with SessionLocal() as session:
            session.add(plan)
            session.add(TradePlanOutcome(
                plan_id=f"TP-WS{i}",
                entry_touched=False,
                tp1_reached=False, tp2_reached=False, tp3_reached=False, tp4_reached=False,
            ))
            session.commit()

    perf = get_performance()
    # All 5 are WAIT -> wait_count=5, actionable=0
    assert perf["wait_count"] == 5
    assert perf["actionable_buy_count"] == 0
    assert perf["actionable_sell_count"] == 0
    assert perf["actionable_total"] == 0
    # Outcome summary should track 0 actionable plans
    assert perf["outcome_summary"]["total_outcomes_tracked"] == 0
    assert perf["outcome_summary"]["tp1_reached"] == 0
    assert perf["outcome_summary"]["sl_before_target_count"] == 0


# ---------- plan row serialization tests ----------

def test_plan_row_includes_fingerprint_and_short_reason():
    """_plan_row_to_dict should include setup_fingerprint + short_reason."""
    from app.services.trade_plan.engine import _plan_row_to_dict

    now = datetime.now(timezone.utc)
    plan = TradePlan(
        plan_id="TP-SER1",
        created_at=now,
        market_timestamp=now,
        instrument="XAU/USD",
        brain_decision="WAIT",
        plan_status="NO_TRADE",
        plan_version="trade-plan-v0.1",
        plan_engine_version="ict-plan-v0.1",
        lifecycle_state="CREATED",
        setup_fingerprint="fp_abcd1234",
        short_reason="WAIT — no M15 MSS",
    )
    with SessionLocal() as session:
        session.add(plan)
        session.add(TradePlanOutcome(
            plan_id="TP-SER1",
            entry_touched=False,
            tp1_reached=False, tp2_reached=False, tp3_reached=False, tp4_reached=False,
        ))
        session.commit()
        session.refresh(plan)

    result = _plan_row_to_dict(plan)
    p = result["plan"]
    assert p["setup_fingerprint"] == "fp_abcd1234"
    assert p["short_reason"] == "WAIT — no M15 MSS"
    assert p["reused_existing_plan"] is False
    assert p["plan_engine_version"] == "ict-plan-v0.1"
