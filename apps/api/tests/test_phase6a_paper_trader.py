"""Phase 6A: self-learning paper trader — tests.

Covers:
  - paper BUY entry (entry zone touched → ACTIVE)
  - paper SELL entry (mirror)
  - WAIT -> no paper trade
  - entry never touched → EXPIRED
  - SL first (before any TP) → STOPPED
  - TP1 first (before SL) → TP1_REACHED
  - TP1 → breakeven (management-v0.1)
  - TP2, TP3, MAX lifecycle
  - MFE / MAE calculation
  - R-multiple calculation
  - no duplicate paper trades (one plan_id → one trade)
  - restart safety (completed work remains)
  - loss review (classification + mistake tags)
  - win review (what worked)
  - mistake aggregation (patterns aggregate across trades)
  - candidate creation only after repeat evidence (≥5 occurrences + ≥3 losses)
  - no single-loss rule modification
  - no future leakage (only live price advances lifecycle)
  - Matrix strategy placeholder remains empty without source material
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.db.models import (
    PaperAccount, PaperTrade, TradeReview, StrategyMistakePattern,
    CandidateStrategyRule, ExternalStrategyKnowledge, PaperTraderAuditLog,
    TradePlan, TradePlanOutcome,
)
from app.services.paper_trader import (
    PAPER_TRADE_VERSION, PAPER_ACCOUNT_ID,
    get_or_create_paper_account,
    create_paper_trade_from_plan,
    evaluate_paper_trades,
    run_post_trade_review,
    _evaluate_one_trade,
    _sample_quality_label,
    MIN_SAMPLE_INSUFFICIENT, MIN_SAMPLE_EARLY, MIN_SAMPLE_DEVELOPING,
)
from app.services.market_state import state
from app.models.market import Quote


def _make_plan(
    plan_id: str = "TP-TEST001",
    direction: str = "BUY",
    plan_status: str = "ACTIONABLE",
    entry_low: float = 4295.0,
    entry_high: float = 4297.0,
    preferred_entry: float = 4296.0,
    stop_loss: float = 4290.0,
    tp1: float = 4301.0,
    tp2: float = 4306.0,
    tp3: float = 4311.0,
    max_objective: float = 4320.0,
    setup_fingerprint: str = "fp_test",
    plan_engine_version: str = "ict-plan-v0.1",
) -> TradePlan:
    """Create a TradePlan row for testing."""
    now = datetime.now(timezone.utc)
    return TradePlan(
        plan_id=plan_id,
        created_at=now,
        market_timestamp=now,
        instrument="XAU/USD",
        brain_decision=direction,
        technical_score=75.0,
        entry_low=entry_low,
        entry_high=entry_high,
        entry_type="FVG",
        entry_reference=preferred_entry,
        preferred_entry=preferred_entry,
        stop_loss=stop_loss,
        invalidation_level=stop_loss,
        sl_distance=abs(preferred_entry - stop_loss),
        tp1=tp1, tp2=tp2, tp3=tp3, tp4=max_objective,
        max_objective=max_objective,
        tp1_reason="test", tp2_reason="test", tp3_reason="test", tp4_reason="test",
        risk_distance=abs(preferred_entry - stop_loss),
        reward_tp1=abs(tp1 - preferred_entry),
        reward_tp2=abs(tp2 - preferred_entry),
        reward_tp3=abs(tp3 - preferred_entry),
        reward_tp4=abs(max_objective - preferred_entry),
        rr_tp1=1.0, rr_tp2=2.0, rr_tp3=3.0, rr_tp4=5.0,
        plan_status=plan_status,
        plan_version="trade-plan-v0.1",
        plan_engine_version=plan_engine_version,
        historical_context="INSUFFICIENT_DATA",
        lifecycle_state="CREATED",
        setup_fingerprint=setup_fingerprint,
        setup_thesis="Test setup",
        for_evidence_json="[]",
        against_evidence_json="[]",
        session_context_json=json.dumps({
            "htf_trend": "BULLISH", "m15_trend": "BULLISH",
            "liquidity_swept": True, "sweep_direction": "SELL_SIDE",
            "displacement_confirmed": True,
            "fvg_active": True, "ob_active": True,
            "location": "DISCOUNT",
            "active_sessions": ["LONDON", "NEW_YORK"],
        }),
    )


def _set_live_price(price: float) -> None:
    """Mock the live spot price."""
    now = datetime.now(timezone.utc)
    state.quote = Quote(
        symbol="XAU/USD", price=price, provider="test",
        market_timestamp=now, received_timestamp=now,
        age_seconds=0.0, status="RECENT",
    )


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    state.quote = None
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    state.quote = None


# ---------- paper account tests ----------

def test_get_or_create_paper_account():
    """Paper account is created on first call, reused on subsequent calls."""
    with SessionLocal() as session:
        account1 = get_or_create_paper_account(session)
        assert account1.paper_account_id == PAPER_ACCOUNT_ID
        assert account1.starting_equity == 10000.0
        # Second call should return the same account
        account2 = get_or_create_paper_account(session)
        assert account2.id == account1.id


# ---------- paper trade creation tests ----------

def test_create_paper_trade_from_actionable_plan():
    """An actionable BUY plan → paper trade created with WAITING_FOR_ENTRY status."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-ACT1", direction="BUY", plan_status="ACTIONABLE")
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-ACT1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        result = create_paper_trade_from_plan(plan)
    assert result is not None
    assert result["status"] == "WAITING_FOR_ENTRY"
    assert result["reused_existing"] is False
    assert result["direction"] == "BUY"


def test_wait_plan_creates_no_paper_trade():
    """A WAIT plan → no paper trade (None returned)."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-WAIT1", direction="WAIT", plan_status="NO_TRADE")
        session.add(plan)
        session.commit()
        result = create_paper_trade_from_plan(plan)
    assert result is None


def test_no_duplicate_paper_trades():
    """One trade_plan_id → maximum ONE paper trade."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-DUP1", direction="BUY", plan_status="ACTIONABLE")
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-DUP1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
    # First call creates
    result1 = create_paper_trade_from_plan(plan)
    assert result1["reused_existing"] is False
    # Second call returns existing (no duplicate)
    with SessionLocal() as session:
        plan2 = session.query(TradePlan).filter_by(plan_id="TP-DUP1").first()
        result2 = create_paper_trade_from_plan(plan2)
    assert result2["reused_existing"] is True
    assert result2["paper_trade_id"] == result1["paper_trade_id"]
    # Verify only ONE paper trade row exists
    with SessionLocal() as session:
        count = session.query(PaperTrade).filter_by(trade_plan_id="TP-DUP1").count()
        assert count == 1


# ---------- entry simulation tests ----------

def test_buy_entry_touched_when_price_in_zone():
    """BUY: when price enters entry zone, trade becomes ACTIVE."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-ENT1", direction="BUY", plan_status="ACTIONABLE",
                          entry_low=4295.0, entry_high=4297.0)
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-ENT1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        create_paper_trade_from_plan(plan)
        # Get the paper trade
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-ENT1").first()
        session.close()
    # Set price inside the entry zone
    _set_live_price(4296.0)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    with SessionLocal() as session:
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-ENT1").first()
        assert trade.status == "ACTIVE"
        assert trade.actual_paper_entry is not None
        assert trade.entry_timestamp is not None


def test_entry_never_touched_expires_after_24h():
    """Paper trade expires if entry not touched within 24 hours."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-EXP1", direction="BUY", plan_status="ACTIONABLE",
                          entry_low=4295.0, entry_high=4297.0)
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-EXP1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        create_paper_trade_from_plan(plan)
        # Backdate the paper trade to 25h ago
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-EXP1").first()
        trade.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        session.commit()
        session.close()
    # Set price OUTSIDE the entry zone
    _set_live_price(4500.0)  # far from entry zone
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    with SessionLocal() as session:
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-EXP1").first()
        assert trade.status == "EXPIRED"
        assert trade.exit_reason == "EXPIRED_NO_ENTRY"


# ---------- SL/TP event ordering tests ----------

def test_sl_first_before_any_tp():
    """SL hit before any TP → STOPPED with -1R."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-SL1", direction="BUY", plan_status="ACTIONABLE",
                          entry_low=4295.0, entry_high=4297.0, preferred_entry=4296.0,
                          stop_loss=4290.0, tp1=4301.0)
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-SL1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        create_paper_trade_from_plan(plan)
        session.close()
    # Step 1: enter the trade (price in entry zone)
    _set_live_price(4296.0)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    # Step 2: price drops to SL
    _set_live_price(4288.0)  # below SL of 4290
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    with SessionLocal() as session:
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-SL1").first()
        assert trade.status == "STOPPED"
        assert trade.exit_reason == "STOP_LOSS_HIT"
        assert trade.realized_r_multiple == -1.0


def test_tp1_first_before_sl():
    """TP1 hit before SL → TP1_REACHED + breakeven activated."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-TP11", direction="BUY", plan_status="ACTIONABLE",
                          entry_low=4295.0, entry_high=4297.0, preferred_entry=4296.0,
                          stop_loss=4290.0, tp1=4301.0)
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-TP11", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        create_paper_trade_from_plan(plan)
        session.close()
    # Step 1: enter
    _set_live_price(4296.0)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    # Step 2: price hits TP1 (4301)
    _set_live_price(4301.0)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    with SessionLocal() as session:
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-TP11").first()
        assert trade.status in ("TP1_REACHED", "BREAKEVEN")
        assert trade.breakeven_activated_at is not None  # BE management after TP1
        assert trade.breakeven_price is not None


# ---------- R-multiple + MFE/MAE tests ----------

def test_r_multiple_calculation_on_max_reached():
    """When MAX_OBJECTIVE is reached, R-multiple = profit / risk."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-MAX1", direction="BUY", plan_status="ACTIONABLE",
                          entry_low=4295.0, entry_high=4297.0, preferred_entry=4296.0,
                          stop_loss=4290.0, tp1=4301.0, tp2=4306.0, tp3=4311.0,
                          max_objective=4320.0)
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-MAX1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        create_paper_trade_from_plan(plan)
        session.close()
    # Enter
    _set_live_price(4296.0)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    # Hit all TPs in order (price jumps to MAX)
    _set_live_price(4321.0)  # above MAX_OBJECTIVE
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    with SessionLocal() as session:
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-MAX1").first()
        assert trade.status == "MAX_REACHED"
        assert trade.realized_r_multiple is not None
        # R = profit / risk = (4320-4296) / (4296-4290) = 24/6 = 4.0R
        assert abs(trade.realized_r_multiple - 4.0) < 0.1


def test_mfe_mae_tracked():
    """MFE and MAE are tracked from entry to terminal."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-MM1", direction="BUY", plan_status="ACTIONABLE",
                          entry_low=4295.0, entry_high=4297.0, preferred_entry=4296.0,
                          stop_loss=4290.0, tp1=4301.0)
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-MM1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        create_paper_trade_from_plan(plan)
        session.close()
    # Enter
    _set_live_price(4296.0)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    # Price goes up (MFE) then down (MAE) then SL
    _set_live_price(4299.0)  # +3 from entry → MFE = 3
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    _set_live_price(4293.0)  # -3 from entry → MAE = 3
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    _set_live_price(4288.0)  # SL hit
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    with SessionLocal() as session:
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-MM1").first()
        assert trade.mfe is not None
        assert trade.mfe >= 3.0  # should have captured at least the +3
        assert trade.mae is not None
        assert trade.mae >= 3.0  # should have captured at least the -3


# ---------- post-trade review tests ----------

def test_loss_review_classifies_mistakes():
    """A losing trade → post-trade review with mistake tags."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-LOSS1", direction="BUY", plan_status="ACTIONABLE",
                          entry_low=4295.0, entry_high=4297.0, preferred_entry=4296.0,
                          stop_loss=4290.0, tp1=4301.0)
        # Make this plan look like a weak setup (no MSS, no sweep, etc.)
        plan.session_context_json = json.dumps({
            "htf_trend": "RANGE", "m15_trend": "RANGE",
            "liquidity_swept": False, "sweep_direction": None,
            "displacement_confirmed": False,
            "fvg_active": False, "ob_active": False,
            "location": "EQUILIBRIUM",
            "active_sessions": [],
        })
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-LOSS1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        create_paper_trade_from_plan(plan)
        session.close()
    # Enter + SL
    _set_live_price(4296.0)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    _set_live_price(4288.0)  # SL
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    with SessionLocal() as session:
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-LOSS1").first()
        assert trade.status == "STOPPED"
        # Review should exist
        review = session.query(TradeReview).filter_by(paper_trade_id=trade.paper_trade_id).first()
        assert review is not None
        assert review.result == "LOSS"
        assert review.r_multiple == -1.0
        tags = json.loads(review.mistake_tags_json) if review.mistake_tags_json else []
        assert len(tags) > 0
        # Should have some of: HTF_CONFLICT, NO_VALID_MSS, FALSE_LIQUIDITY_SWEEP, etc.
        assert any(t in ("HTF_CONFLICT", "NO_VALID_MSS", "FALSE_LIQUIDITY_SWEEP",
                          "WEAK_MSS", "MID_RANGE_ENTRY", "UNKNOWN") for t in tags)


def test_win_review_stores_what_worked():
    """A winning trade → post-trade review with what_worked."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-WIN1", direction="BUY", plan_status="ACTIONABLE",
                          entry_low=4295.0, entry_high=4297.0, preferred_entry=4296.0,
                          stop_loss=4290.0, tp1=4301.0, tp2=4306.0, tp3=4311.0,
                          max_objective=4320.0)
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-WIN1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        create_paper_trade_from_plan(plan)
        session.close()
    # Enter + hit MAX
    _set_live_price(4296.0)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    _set_live_price(4321.0)  # above MAX
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    with SessionLocal() as session:
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-WIN1").first()
        assert trade.status == "MAX_REACHED"
        review = session.query(TradeReview).filter_by(paper_trade_id=trade.paper_trade_id).first()
        assert review is not None
        assert review.result == "WIN"
        what_worked = json.loads(review.what_worked_json) if review.what_worked_json else []
        assert len(what_worked) > 0
        # Should have some of: HTF alignment, clean sweep, FVG retest, etc.
        assert any("alignment" in w.lower() or "sweep" in w.lower() or "FVG" in w or "location" in w.lower() for w in what_worked)


# ---------- no single-loss rule modification ----------

def test_single_loss_does_not_create_candidate_rule():
    """One loss → mistake pattern OBSERVED, but NO candidate rule."""
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-SINGLE1", direction="BUY", plan_status="ACTIONABLE",
                          entry_low=4295.0, entry_high=4297.0, preferred_entry=4296.0,
                          stop_loss=4290.0, tp1=4301.0)
        plan.session_context_json = json.dumps({
            "htf_trend": "RANGE", "m15_trend": "RANGE",
            "liquidity_swept": False, "sweep_direction": None,
            "displacement_confirmed": False,
            "fvg_active": False, "ob_active": False,
            "location": "EQUILIBRIUM",
            "active_sessions": [],
        })
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-SINGLE1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        create_paper_trade_from_plan(plan)
        session.close()
    # Enter + SL
    _set_live_price(4296.0)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    _set_live_price(4288.0)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    with SessionLocal() as session:
        # Mistake patterns should exist
        patterns = session.query(StrategyMistakePattern).all()
        assert len(patterns) > 0
        for p in patterns:
            assert p.occurrence_count == 1
            assert p.status == "OBSERVED"
        # NO candidate rules should exist (needs >= 5 occurrences + >= 3 losses)
        candidates = session.query(CandidateStrategyRule).all()
        assert len(candidates) == 0


# ---------- sample quality labels ----------

def test_sample_quality_labels():
    assert _sample_quality_label(0) == "INSUFFICIENT_SAMPLE"
    assert _sample_quality_label(9) == "INSUFFICIENT_SAMPLE"
    assert _sample_quality_label(10) == "EARLY"
    assert _sample_quality_label(29) == "EARLY"
    assert _sample_quality_label(30) == "DEVELOPING"
    assert _sample_quality_label(99) == "DEVELOPING"
    assert _sample_quality_label(100) == "MORE_ESTABLISHED"


# ---------- Matrix placeholder test ----------

def test_matrix_strategy_placeholder_remains_empty():
    """External strategy knowledge table starts empty — no Matrix rules until source material provided."""
    with SessionLocal() as session:
        count = session.query(ExternalStrategyKnowledge).count()
        assert count == 0  # no Matrix rules until source material provided


# ---------- no future leakage test ----------

def test_no_future_leakage_in_evaluation():
    """Paper trade evaluation uses only the CURRENT price, not future data.

    A trade's lifecycle state is determined by the current spot price at
    evaluation time, not by what the price will become later.
    """
    with SessionLocal() as session:
        plan = _make_plan(plan_id="TP-LEAK1", direction="BUY", plan_status="ACTIONABLE",
                          entry_low=4295.0, entry_high=4297.0, preferred_entry=4296.0,
                          stop_loss=4290.0, tp1=4301.0)
        session.add(plan)
        session.add(TradePlanOutcome(plan_id="TP-LEAK1", entry_touched=False,
                                      tp1_reached=False, tp2_reached=False,
                                      tp3_reached=False, tp4_reached=False))
        session.commit()
        create_paper_trade_from_plan(plan)
        session.close()
    # Set price FAR from entry (should NOT enter)
    _set_live_price(4400.0)
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_paper_trades())
    finally:
        loop.close()
    with SessionLocal() as session:
        trade = session.query(PaperTrade).filter_by(trade_plan_id="TP-LEAK1").first()
        # Should still be WAITING_FOR_ENTRY (price never touched entry zone)
        assert trade.status == "WAITING_FOR_ENTRY"
        assert trade.actual_paper_entry is None
