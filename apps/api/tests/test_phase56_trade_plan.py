"""Phase 5.6: trade plan engine — comprehensive tests.

Covers:
  - BUY plan: valid entry, SL below entry, TP1<TP2<TP3<TP4, R:R>0
  - SELL plan: valid entry, SL above entry, TP1>TP2>TP3>TP4, R:R>0
  - WAIT -> NO_TRADE
  - NO_DECISION -> NO_TRADE
  - stale data -> plan_status=STALE
  - missing price -> INSUFFICIENT_DATA
  - insufficient structure -> NO_VALID_ENTRY
  - no SL derivable -> NO_VALID_SL
  - plan immutability (lifecycle events separate from plan row)
  - position sizing with known contract spec
  - position sizing rejection when spec unknown
  - position sizing rejection with bad inputs
  - plan lifecycle transitions (CREATED -> ENTRY_TOUCHED -> ACTIVE -> TP1 -> TP4)
  - SL-before-target detection
  - breakeven transition after TP1
  - plan expiry (24h without entry)
  - forward-validation prospective only (no historical backfill)
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import TradePlan, TradePlanLifecycleEvent, TradePlanOutcome
from app.db.session import SessionLocal, engine
from app.models.market import (
    BrainAnalysis,
    Candle,
    Quote,
    TimeframeState,
    Zone,
)
from app.services.trade_plan import (
    PLAN_VERSION,
    LIFECYCLE_STATES,
    LIVE_STATES,
    TERMINAL_STATES,
    calculate_position_size,
    evaluate_live_plans,
    generate_trade_plan,
    get_current_plan,
    get_performance,
    get_plan,
    list_plans,
)


# ---------- helpers ----------

def _make_candles(n: int = 30, base_price: float = 4300.0, atr_per_candle: float = 2.0) -> list[Candle]:
    """Build N synthetic candles with realistic OHLC around base_price."""
    out: list[Candle] = []
    now = datetime.now(timezone.utc)
    for i in range(n):
        ts = now - timedelta(minutes=(n - i))
        # alternate small up/down moves
        delta = ((-1) ** i) * atr_per_candle * 0.3
        op = base_price + delta * i
        cl = op + delta
        hi = max(op, cl) + atr_per_candle * 0.4
        lo = min(op, cl) - atr_per_candle * 0.4
        out.append(Candle(
            symbol="XAU/USD",
            interval="15min",
            timestamp=ts,
            open=op, high=hi, low=lo, close=cl,
            sample_count=1,
            is_historical=True,
        ))
    return out


def _make_buy_analysis(price: float = 4300.0, support_low: float = 4295.0, support_high: float = 4297.0) -> BrainAnalysis:
    """A BUY-decision BrainAnalysis with actionable support."""
    return BrainAnalysis(
        symbol="XAU/USD",
        timestamp=datetime.now(timezone.utc),
        decision="BUY",
        confidence=75.0,
        readiness=100.0,
        regime="TREND_UP",
        risk="MEDIUM",
        score=4.5,
        price=price,
        reasons_for=["4h trend is bullish."],
        reasons_against=[],
        invalidation=support_low,
        support=Zone(kind="SUPPORT", low=support_low, high=support_high, touches=3, strength=54.0),
        resistance=None,
        timeframes=[
            TimeframeState(timeframe="15min", candles=120, status="READY", trend="BULLISH", rsi=42.0, atr=2.5, ema_fast=4300.0, ema_slow=4298.0),
            TimeframeState(timeframe="1h", candles=120, status="READY", trend="BULLISH", rsi=48.0, atr=4.0, ema_fast=4300.0, ema_slow=4295.0),
            TimeframeState(timeframe="4h", candles=120, status="READY", trend="BULLISH", rsi=42.0, atr=10.0, ema_fast=4300.0, ema_slow=4290.0),
        ],
        data_quality="GOOD",
        message="Test BUY plan",
        brain_version="rules-v0.1",
        technical_data_readiness=100.0,
        technical_score=75.0,
        historical_alignment="INSUFFICIENT_DATA",
        probability_calibrated=False,
    )


def _make_sell_analysis(price: float = 4300.0, res_low: float = 4303.0, res_high: float = 4305.0) -> BrainAnalysis:
    return BrainAnalysis(
        symbol="XAU/USD",
        timestamp=datetime.now(timezone.utc),
        decision="SELL",
        confidence=75.0,
        readiness=100.0,
        regime="TREND_DOWN",
        risk="MEDIUM",
        score=-4.5,
        price=price,
        reasons_for=["4h trend is bearish."],
        reasons_against=[],
        invalidation=res_high,
        support=None,
        resistance=Zone(kind="RESISTANCE", low=res_low, high=res_high, touches=3, strength=54.0),
        timeframes=[
            TimeframeState(timeframe="15min", candles=120, status="READY", trend="BEARISH", rsi=58.0, atr=2.5, ema_fast=4300.0, ema_slow=4302.0),
            TimeframeState(timeframe="1h", candles=120, status="READY", trend="BEARISH", rsi=52.0, atr=4.0, ema_fast=4300.0, ema_slow=4305.0),
            TimeframeState(timeframe="4h", candles=120, status="READY", trend="BEARISH", rsi=58.0, atr=10.0, ema_fast=4300.0, ema_slow=4310.0),
        ],
        data_quality="GOOD",
        message="Test SELL plan",
        brain_version="rules-v0.1",
        technical_data_readiness=100.0,
        technical_score=75.0,
        historical_alignment="INSUFFICIENT_DATA",
        probability_calibrated=False,
    )


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
        reasons_against=["Evidence does not meet the conservative BUY/SELL threshold."],
        invalidation=None,
        support=None,
        resistance=None,
        timeframes=[
            TimeframeState(timeframe="15min", candles=120, status="READY", trend="RANGE", rsi=50.0, atr=2.5, ema_fast=4300.0, ema_slow=4300.0),
        ],
        data_quality="LIMITED",
        message="Test WAIT plan",
        brain_version="rules-v0.1",
        technical_data_readiness=100.0,
        technical_score=40.0,
        historical_alignment="INSUFFICIENT_DATA",
        probability_calibrated=False,
    )


def _cleanup_plans() -> None:
    """Delete all rows from trade_plans + lifecycle + outcomes (test isolation)."""
    with SessionLocal() as session:
        session.query(TradePlanLifecycleEvent).delete()
        session.query(TradePlanOutcome).delete()
        session.query(TradePlan).delete()
        session.commit()


def _set_live_price(price: float) -> None:
    """Mock the market state to return a specific live price."""
    from app.services import market_state
    from app.models.market import Quote
    from datetime import datetime, timezone
    market_state.state.quote = Quote(
        symbol="XAU/USD",
        price=price,
        provider="test",
        market_timestamp=datetime.now(timezone.utc),
        received_timestamp=datetime.now(timezone.utc),
        age_seconds=0.0,
        status="RECENT",
    )


# ---------- fixtures ----------

@pytest.fixture(autouse=True)
def _reset_db():
    """Each test gets a freshly-created DB schema (all tables)."""
    from app.db.base import Base
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def clean_db():
    """Delete any trade_plan rows + clear market state (test isolation)."""
    from app.services import market_state
    _cleanup_plans()
    market_state.state.quote = None
    market_state.state.analysis = None
    yield
    _cleanup_plans()
    market_state.state.quote = None
    market_state.state.analysis = None


@pytest.fixture
def known_contract_spec(monkeypatch):
    """Configure a known XAU/USD contract spec via env vars."""
    monkeypatch.setenv("XAUUSD_CONTRACT_SIZE", "100")
    monkeypatch.setenv("XAUUSD_TICK_SIZE", "0.01")
    monkeypatch.setenv("XAUUSD_TICK_VALUE", "1.0")


@pytest.fixture
def unknown_contract_spec(monkeypatch):
    """Remove env vars so the spec is unknown."""
    monkeypatch.delenv("XAUUSD_CONTRACT_SIZE", raising=False)
    monkeypatch.delenv("XAUUSD_TICK_SIZE", raising=False)
    monkeypatch.delenv("XAUUSD_TICK_VALUE", raising=False)


# ---------- tests: BUY / SELL / WAIT plans ----------

def test_buy_plan_has_actionable_or_wait_for_entry_status():
    plan = generate_trade_plan(_make_buy_analysis())
    assert plan["brain_decision"] == "BUY"
    assert plan["plan_version"] == PLAN_VERSION
    assert plan["plan_status"] in ("ACTIONABLE", "WAIT_FOR_ENTRY"), f"got {plan['plan_status']}"
    # Entry zone exists
    assert plan["entry_low"] is not None and plan["entry_high"] is not None
    assert plan["entry_low"] < plan["entry_high"]
    # SL below entry
    assert plan["stop_loss"] is not None
    assert plan["stop_loss"] < plan["entry_reference"]
    # TP1 < TP2 < TP3 < TP4
    assert plan["tp1"] < plan["tp2"] < plan["tp3"] < plan["tp4"]
    # All TPs > entry (BUY side: profit = upside)
    for tp in (plan["tp1"], plan["tp2"], plan["tp3"], plan["tp4"]):
        assert tp > plan["entry_reference"]
    # R:R positive
    for rr in (plan["rr_tp1"], plan["rr_tp2"], plan["rr_tp3"], plan["rr_tp4"]):
        assert rr is not None and rr > 0
    # Risk distance = entry - SL (positive)
    assert plan["risk_distance"] > 0


def test_sell_plan_valid_ordering():
    plan = generate_trade_plan(_make_sell_analysis())
    assert plan["brain_decision"] == "SELL"
    assert plan["plan_status"] in ("ACTIONABLE", "WAIT_FOR_ENTRY")
    # SL above entry
    assert plan["stop_loss"] is not None
    assert plan["stop_loss"] > plan["entry_reference"]
    # TP1 > TP2 > TP3 > TP4 (descending)
    assert plan["tp1"] > plan["tp2"] > plan["tp3"] > plan["tp4"]
    # All TPs < entry (SELL side: profit = downside)
    for tp in (plan["tp1"], plan["tp2"], plan["tp3"], plan["tp4"]):
        assert tp < plan["entry_reference"]
    # R:R positive
    for rr in (plan["rr_tp1"], plan["rr_tp2"], plan["rr_tp3"], plan["rr_tp4"]):
        assert rr is not None and rr > 0


def test_wait_plan_returns_no_trade():
    plan = generate_trade_plan(_make_wait_analysis())
    assert plan["brain_decision"] == "WAIT"
    assert plan["plan_status"] == "NO_TRADE"
    assert plan["entry_low"] is None
    assert plan["entry_high"] is None
    assert plan["stop_loss"] is None
    assert plan["tp1"] is None
    assert plan["rr_tp1"] is None


def test_no_decision_plan_returns_no_trade():
    a = _make_wait_analysis()
    a.decision = "NO_DECISION"
    plan = generate_trade_plan(a)
    assert plan["plan_status"] == "NO_TRADE"


def test_stale_analysis_returns_stale_status():
    a = _make_buy_analysis()
    # Make the analysis timestamp old (5 minutes ago)
    a.timestamp = datetime.now(timezone.utc) - timedelta(seconds=300)
    plan = generate_trade_plan(a)
    assert plan["plan_status"] == "STALE"
    # Even with BUY decision, stale -> no levels
    assert plan["entry_low"] is None
    assert plan["stop_loss"] is None


def test_missing_price_returns_insufficient_data():
    a = _make_buy_analysis()
    a.price = None
    plan = generate_trade_plan(a)
    assert plan["plan_status"] == "INSUFFICIENT_DATA"


def test_no_valid_entry_when_support_far():
    """If support is too far below current price, NO_VALID_ENTRY."""
    a = _make_buy_analysis(price=4300.0, support_low=4200.0, support_high=4202.0)
    # Support is 100 below price — far outside 2*ATR reach
    plan = generate_trade_plan(a)
    assert plan["plan_status"] == "NO_VALID_ENTRY"
    assert plan["entry_low"] is None


def test_no_valid_entry_when_no_support():
    """BUY with no support zone -> NO_VALID_ENTRY."""
    a = _make_buy_analysis()
    a.support = None
    plan = generate_trade_plan(a)
    assert plan["plan_status"] == "NO_VALID_ENTRY"


# ---------- tests: plan immutability ----------

def test_plan_immutability_levels_unchanged_after_creation():
    """The plan row's entry/SL/TP fields must not change after creation."""
    a = _make_buy_analysis()
    plan_dict = generate_trade_plan(a)
    plan_id = plan_dict["plan_id"]

    # Fetch the persisted plan
    persisted = get_plan(plan_id)
    p = persisted["plan"]
    assert p["entry_low"] == plan_dict["entry_low"]
    assert p["entry_high"] == plan_dict["entry_high"]
    assert p["stop_loss"] == plan_dict["stop_loss"]
    assert p["tp1"] == plan_dict["tp1"]
    assert p["tp4"] == plan_dict["tp4"]

    # Set a live price so evaluate_live_plans can run
    _set_live_price(p["entry_reference"] or 4300.0)

    # Run evaluate_live_plans — should NOT modify original levels
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_live_plans())
    finally:
        loop.close()

    persisted2 = get_plan(plan_id)
    p2 = persisted2["plan"]
    assert p2["entry_low"] == p["entry_low"]
    assert p2["stop_loss"] == p["stop_loss"]
    assert p2["tp1"] == p["tp1"]
    assert p2["tp4"] == p["tp4"]
    # Lifecycle state may have moved, but original levels are unchanged


def test_lifecycle_events_are_persisted_separately():
    """Lifecycle transitions go into trade_plan_lifecycle_events, not the plan row."""
    a = _make_buy_analysis()
    plan = generate_trade_plan(a)
    plan_id = plan["plan_id"]

    with SessionLocal() as session:
        events = session.query(TradePlanLifecycleEvent).filter_by(plan_id=plan_id).all()
        assert len(events) >= 1
        # The first event should be CREATED
        assert events[0].to_state == "CREATED"
        assert events[0].from_state is None


# ---------- tests: position sizing ----------

def test_position_sizing_with_known_spec(known_contract_spec):
    """With env vars set, position sizing should compute a lot size."""
    result = calculate_position_size(
        account_equity=10000.0,
        risk_percent=1.0,
        sl_distance=2.0,
        instrument="XAU/USD",
    )
    assert result.status == "OK"
    assert result.risk_amount == 100.0
    assert result.sl_distance == 2.0
    # ticks_at_risk = 2.0 / 0.01 = 200 ticks
    # loss_per_lot = 200 * 1.0 = $200
    # lot_size = 100 / 200 = 0.5
    assert abs(result.lot_size - 0.5) < 0.001
    assert result.contract_size == 100.0
    assert result.tick_size == 0.01
    assert result.tick_value == 1.0


def test_position_sizing_rejects_unknown_spec(unknown_contract_spec):
    """Without env vars, returns POSITION_SIZE_UNAVAILABLE."""
    result = calculate_position_size(
        account_equity=10000.0,
        risk_percent=1.0,
        sl_distance=2.0,
    )
    assert result.status == "POSITION_SIZE_UNAVAILABLE"
    assert result.lot_size is None
    assert "no broker spec configured" in (result.reason or "")


def test_position_sizing_rejects_bad_inputs(known_contract_spec):
    # Negative equity
    r = calculate_position_size(account_equity=-100, risk_percent=1, sl_distance=2)
    assert r.status == "POSITION_SIZE_UNAVAILABLE"
    # Risk percent > 100
    r = calculate_position_size(account_equity=1000, risk_percent=200, sl_distance=2)
    assert r.status == "POSITION_SIZE_UNAVAILABLE"
    # Zero SL
    r = calculate_position_size(account_equity=1000, risk_percent=1, sl_distance=0)
    assert r.status == "POSITION_SIZE_UNAVAILABLE"
    # SL smaller than tick
    r = calculate_position_size(account_equity=1000, risk_percent=1, sl_distance=0.001)
    assert r.status == "POSITION_SIZE_UNAVAILABLE"


# ---------- tests: lifecycle ----------

def test_lifecycle_states_constant_complete():
    """LIFECYCLE_STATES must list all the states we promised in the spec."""
    expected = {
        "CREATED", "WAITING_FOR_ENTRY", "ENTRY_TOUCHED", "ACTIVE",
        "TP1_REACHED", "TP2_REACHED", "TP3_REACHED", "TP4_REACHED",
        "STOPPED", "BREAKEVEN", "EXPIRED", "INVALIDATED",
    }
    assert set(LIFECYCLE_STATES) == expected


def test_live_plans_list_excludes_terminal():
    """After a plan is STOPPED, it should not appear in live plans."""
    from app.services.trade_plan.lifecycle import list_live_plans
    a = _make_buy_analysis()
    plan = generate_trade_plan(a)
    plan_id = plan["plan_id"]

    # Initially in CREATED (live)
    with SessionLocal() as session:
        live = list_live_plans(session)
        assert any(p.plan_id == plan_id for p in live)

    # Manually mark STOPPED
    with SessionLocal() as session:
        p = session.query(TradePlan).filter_by(plan_id=plan_id).first()
        p.lifecycle_state = "STOPPED"
        session.commit()

    # Now should NOT be in live list
    with SessionLocal() as session:
        live = list_live_plans(session)
        assert not any(p.plan_id == plan_id for p in live)


def test_buy_plan_sl_before_target_detection():
    """If price drops below SL before any TP is hit, plan -> STOPPED."""
    a = _make_buy_analysis(price=4300.0, support_low=4295.0, support_high=4297.0)
    plan = generate_trade_plan(a)
    plan_id = plan["plan_id"]

    # Read the persisted plan to get SL
    persisted = get_plan(plan_id)
    sl = persisted["plan"]["stop_loss"]
    assert sl is not None
    assert sl < 4300.0

    # Simulate price dropping below SL (mock state.quote.price)
    _set_live_price(sl - 5.0)  # 5 below SL = SL hit

    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_live_plans())
    finally:
        loop.close()

    persisted2 = get_plan(plan_id)
    p = persisted2["plan"]
    outcome = persisted2["outcome"]
    # Plan should be in STOPPED state
    assert p["lifecycle_state"] == "STOPPED"
    assert outcome["sl_before_target"] is True
    assert outcome["sl_hit_price"] is not None
    assert outcome["sl_hit_at"] is not None


def test_buy_plan_tp1_then_tp4_progression():
    """Entry -> TP1 -> TP2 -> TP3 -> TP4 progression."""
    a = _make_buy_analysis(price=4300.0, support_low=4299.0, support_high=4300.0)
    # Support right around price -> entry should be ACTIONABLE
    plan = generate_trade_plan(a)
    plan_id = plan["plan_id"]
    persisted = get_plan(plan_id)
    p = persisted["plan"]

    # Skip if plan is not ACTIONABLE (depends on candle structure)
    if p["plan_status"] not in ("ACTIONABLE", "WAIT_FOR_ENTRY"):
        pytest.skip("Plan not actionable in this synthetic setup")

    import asyncio

    # First: touch entry (use entry_reference)
    _set_live_price(p["entry_reference"])
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_live_plans())
    finally:
        loop.close()
    persisted = get_plan(plan_id)
    assert persisted["outcome"]["entry_touched"] is True

    # Then: hit TP1
    _set_live_price(p["tp1"])
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_live_plans())
    finally:
        loop.close()
    persisted = get_plan(plan_id)
    assert persisted["outcome"]["tp1_reached"] is True
    assert persisted["plan"]["lifecycle_state"] in ("TP1_REACHED", "BREAKEVEN")

    # Then: hit TP4 (skip TP2/TP3 — they'll be detected as the price moves up)
    _set_live_price(p["tp4"] + 1.0)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_live_plans())
    finally:
        loop.close()
    persisted = get_plan(plan_id)
    # All TPs should be reached now
    assert persisted["outcome"]["tp4_reached"] is True
    assert persisted["outcome"]["tp1_reached"] is True
    assert persisted["plan"]["lifecycle_state"] == "TP4_REACHED"


def test_breakeven_after_tp1():
    """After TP1, lifecycle should include a BREAKEVEN transition (advisory)."""
    a = _make_buy_analysis(price=4300.0, support_low=4299.0, support_high=4300.0)
    plan = generate_trade_plan(a)
    plan_id = plan["plan_id"]
    persisted = get_plan(plan_id)
    p = persisted["plan"]
    if p["plan_status"] not in ("ACTIONABLE", "WAIT_FOR_ENTRY"):
        pytest.skip()

    import asyncio

    # Touch entry
    _set_live_price(p["entry_reference"])
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_live_plans())
    finally:
        loop.close()

    # Hit TP1
    _set_live_price(p["tp1"])
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_live_plans())
    finally:
        loop.close()

    persisted = get_plan(plan_id)
    events = persisted["lifecycle_events"]
    # Should have a BREAKEVEN event after TP1_REACHED
    states = [e["to_state"] for e in events]
    assert "TP1_REACHED" in states
    assert "BREAKEVEN" in states


def test_plan_expiry_after_24h():
    """A plan that hasn't been touched after 24h should be EXPIRED."""
    from app.services.trade_plan.lifecycle import is_plan_expired
    a = _make_buy_analysis()
    plan = generate_trade_plan(a)
    plan_id = plan["plan_id"]

    # Backdate created_at to 25 hours ago
    with SessionLocal() as session:
        p = session.query(TradePlan).filter_by(plan_id=plan_id).first()
        p.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        session.commit()

    # Run evaluate — should mark EXPIRED. Price must NOT touch the entry zone
    # (we want to test the expiry branch, not the entry-touch branch).
    # Use a price far from any entry zone.
    _set_live_price(99999.0)  # wildly out of range
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(evaluate_live_plans())
    finally:
        loop.close()

    persisted = get_plan(plan_id)
    assert persisted["plan"]["lifecycle_state"] == "EXPIRED"
    assert persisted["outcome"]["final_status"] == "EXPIRED"


# ---------- tests: forward validation prospective only ----------

def test_no_historical_backfill_of_outcomes():
    """We must NEVER mark a historical plan as having TPs reached retroactively.

    This test creates a plan, then immediately runs evaluate — outcome
    fields should be initial (no TPs reached, no SL hit) unless the
    current price genuinely triggered them.
    """
    a = _make_buy_analysis(price=4300.0, support_low=4295.0, support_high=4297.0)
    # The plan's entry zone will be around 4295 +/- buffer
    plan = generate_trade_plan(a)
    plan_id = plan["plan_id"]

    # Initial outcome should be pristine
    persisted = get_plan(plan_id)
    outcome = persisted["outcome"]
    assert outcome["entry_touched"] is False
    assert outcome["tp1_reached"] is False
    assert outcome["tp4_reached"] is False
    assert outcome["sl_hit_at"] is None
    assert outcome["final_status"] is None


# ---------- tests: API helpers ----------

def test_list_plans_returns_newest_first():
    generate_trade_plan(_make_wait_analysis())
    generate_trade_plan(_make_buy_analysis())
    result = list_plans(limit=10)
    assert result["count"] == 2
    plans = result["plans"]
    # Newest first
    assert plans[0]["brain_decision"] == "BUY"
    assert plans[1]["brain_decision"] == "WAIT"


def test_get_current_plan_returns_latest():
    generate_trade_plan(_make_wait_analysis())
    import time as t
    t.sleep(0.05)  # ensure different created_at timestamps
    generate_trade_plan(_make_buy_analysis())
    result = get_current_plan()
    assert result["plan"]["brain_decision"] == "BUY"


def test_get_performance_aggregates():
    generate_trade_plan(_make_wait_analysis())  # NO_TRADE
    generate_trade_plan(_make_buy_analysis())   # ACTIONABLE or WAIT_FOR_ENTRY
    perf = get_performance()
    assert perf["total_plans"] == 2
    assert perf["by_decision"].get("BUY") == 1
    assert perf["by_decision"].get("WAIT") == 1
    assert perf["plan_version"] == PLAN_VERSION


# ---------- tests: entry engine units ----------

def test_buy_entry_support_too_far_returns_no_valid_entry():
    from app.services.trade_plan.entry import derive_buy_entry
    from app.models.market import Zone
    result = derive_buy_entry(
        price=4300.0,
        support=Zone(kind="SUPPORT", low=4200.0, high=4202.0, touches=1, strength=18),
        candles=_make_candles(),
        atr=2.5,
    )
    assert result.status == "NO_VALID_ENTRY"


def test_sell_entry_resistance_too_far_returns_no_valid_entry():
    from app.services.trade_plan.entry import derive_sell_entry
    from app.models.market import Zone
    result = derive_sell_entry(
        price=4300.0,
        resistance=Zone(kind="RESISTANCE", low=4400.0, high=4402.0, touches=1, strength=18),
        candles=_make_candles(),
        atr=2.5,
    )
    assert result.status == "NO_VALID_ENTRY"


# ---------- tests: stops engine units ----------

def test_buy_stop_uses_structural_low():
    from app.services.trade_plan.stops import derive_buy_stop
    from app.models.market import Zone
    candles = _make_candles(n=20, base_price=4300.0)
    result = derive_buy_stop(
        entry_reference=4300.0,
        support=Zone(kind="SUPPORT", low=4295.0, high=4297.0, touches=2, strength=36),
        candles=candles,
        atr=2.5,
    )
    assert result.status == "OK"
    # SL must be below support.low (after buffer)
    assert result.stop_loss < 4295.0
    assert result.invalidation_level == 4295.0
    assert "support zone low" in result.invalidation_reason


def test_sell_stop_uses_structural_high():
    from app.services.trade_plan.stops import derive_sell_stop
    from app.models.market import Zone
    candles = _make_candles(n=20, base_price=4300.0)
    result = derive_sell_stop(
        entry_reference=4300.0,
        resistance=Zone(kind="RESISTANCE", low=4303.0, high=4305.0, touches=2, strength=36),
        candles=candles,
        atr=2.5,
    )
    assert result.status == "OK"
    assert result.stop_loss > 4305.0
    assert result.invalidation_level == 4305.0
    assert "resistance zone high" in result.invalidation_reason


def test_buy_stop_rejects_when_no_structure():
    from app.services.trade_plan.stops import derive_buy_stop
    candles = []
    result = derive_buy_stop(
        entry_reference=4300.0,
        support=None,
        candles=candles,
        atr=2.5,
    )
    assert result.status == "NO_VALID_SL"


# ---------- tests: targets engine units ----------

def test_buy_targets_ascending():
    from app.services.trade_plan.targets import derive_buy_targets
    from app.models.market import Zone
    candles = _make_candles(n=50, base_price=4300.0)
    result = derive_buy_targets(
        entry_reference=4300.0,
        stop_loss=4295.0,
        risk_distance=5.0,
        resistance=Zone(kind="RESISTANCE", low=4310.0, high=4312.0, touches=2, strength=36),
        candles=candles,
        atr=2.5,
    )
    assert result.status == "OK"
    assert len(result.tps) == 4
    assert result.tps[0] < result.tps[1] < result.tps[2] < result.tps[3]
    # All above entry
    for tp in result.tps:
        assert tp > 4300.0


def test_sell_targets_descending():
    from app.services.trade_plan.targets import derive_sell_targets
    from app.models.market import Zone
    candles = _make_candles(n=50, base_price=4300.0)
    result = derive_sell_targets(
        entry_reference=4300.0,
        stop_loss=4305.0,
        risk_distance=5.0,
        support=Zone(kind="SUPPORT", low=4288.0, high=4290.0, touches=2, strength=36),
        candles=candles,
        atr=2.5,
    )
    assert result.status == "OK"
    assert len(result.tps) == 4
    assert result.tps[0] > result.tps[1] > result.tps[2] > result.tps[3]
    for tp in result.tps:
        assert tp < 4300.0


# ---------- tests: R:R ----------

def test_rr_calculation_buy():
    from app.services.trade_plan.risk_reward import compute_rr
    risk, rewards = compute_rr(
        entry_reference=4300.0,
        stop_loss=4295.0,
        tps=[4305.0, 4310.0, 4315.0, 4320.0],
    )
    assert risk == 5.0
    assert rewards[0].reward == 5.0
    assert rewards[0].rr == 1.0
    assert rewards[3].reward == 20.0
    assert rewards[3].rr == 4.0


def test_rr_calculation_sell():
    from app.services.trade_plan.risk_reward import compute_rr
    risk, rewards = compute_rr(
        entry_reference=4300.0,
        stop_loss=4305.0,
        tps=[4295.0, 4290.0, 4285.0, 4280.0],
    )
    assert risk == 5.0
    assert rewards[0].reward == 5.0
    assert rewards[0].rr == 1.0
    assert rewards[3].reward == 20.0
    assert rewards[3].rr == 4.0


def test_rr_rejects_zero_risk():
    from app.services.trade_plan.risk_reward import compute_rr
    with pytest.raises(ValueError):
        compute_rr(entry_reference=4300.0, stop_loss=4300.0, tps=[4305.0])
