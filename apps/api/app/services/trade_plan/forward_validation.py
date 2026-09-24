"""Phase 5.6: forward-validation loop for trade plans.

PROSPECTIVE ONLY. For every live plan, evaluate what happens AFTER
plan creation:
  - Was the entry zone touched? At what price? When?
  - Did SL hit before any TP? At what price? When?
  - Did TP1 / TP2 / TP3 / TP4 get reached? When?
  - Maximum favorable / adverse excursion observed?
  - Time-to-entry / time-to-TP1 / time-to-TP2 / time-to-TP3 / time-to-TP4?

The plan row's ORIGINAL entry/SL/TP levels are NEVER rewritten.
Lifecycle state transitions are stored in trade_plan_lifecycle_events
and the outcome row is updated in-place.

NO HISTORICAL BACKFILL — we only evaluate plans that were genuinely
generated and persisted at the moment the Brain emitted its decision.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import TradePlan, TradePlanOutcome
from app.db.session import SessionLocal
from app.services.market_state import refresh_quote_once, state
from .lifecycle import is_plan_expired, list_live_plans, transition


log = logging.getLogger("forexwizard")


async def evaluate_live_plans() -> dict:
    """One pass over all live plans. Returns a summary dict.

    Designed to be called every 60 seconds from the lifespan startup.
    Uses whatever quote is currently in state.quote — the background
    validation loop (trade_plan_validation_loop) refreshes the quote
    before calling this, so tests can mock state.quote directly.
    """
    now = datetime.now(timezone.utc)
    summary = {
        "evaluated": 0,
        "transitions": 0,
        "entries_touched": 0,
        "targets_reached": 0,
        "stopped": 0,
        "expired": 0,
    }

    # Pull price from state.quote (Quote object) — defensive against None
    price = None
    if state.quote is not None:
        price = state.quote.price
    if price is None or price <= 0:
        return {**summary, "reason": "no live price available"}

    with SessionLocal() as session:
        plans = list_live_plans(session)
        for plan in plans:
            summary["evaluated"] += 1
            changed = _evaluate_plan(session, plan, price, now)
            if changed:
                summary["transitions"] += 1
        session.commit()

    return summary


def _evaluate_plan(
    session,
    plan: TradePlan,
    price: float,
    now: datetime,
) -> bool:
    """Evaluate one plan. Returns True if state changed."""
    # Skip NO_TRADE / STALE / NO_VALID_* plans — nothing to track
    if plan.plan_status in ("NO_TRADE", "STALE", "NO_VALID_ENTRY", "NO_VALID_SL", "NO_VALID_TP", "INSUFFICIENT_DATA"):
        return False

    # Skip plans without entry/SL/TP (shouldn't happen but defensive)
    if plan.entry_low is None or plan.entry_high is None or plan.stop_loss is None:
        return False
    if plan.tp1 is None or plan.tp2 is None or plan.tp3 is None or plan.tp4 is None:
        return False

    # Get or create outcome row
    outcome = session.scalar(
        select(TradePlanOutcome).where(TradePlanOutcome.plan_id == plan.plan_id).limit(1)
    )
    if outcome is None:
        outcome = TradePlanOutcome(plan_id=plan.plan_id, entry_touched=False)
        session.add(outcome)

    outcome.last_evaluated_at = now

    decision = plan.brain_decision
    entry_low = plan.entry_low
    entry_high = plan.entry_high
    stop = plan.stop_loss
    tp1, tp2, tp3, tp4 = plan.tp1, plan.tp2, plan.tp3, plan.tp4
    entry_ref = plan.entry_reference

    changed = False

    # --- 1. Entry touch detection ---
    if not outcome.entry_touched:
        if entry_low <= price <= entry_high:
            outcome.entry_touched = True
            outcome.entry_touched_at = now
            outcome.entry_touch_price = price
            summary_key = "entries_touched"
            _update_inner(summary_key)  # noop summary counter (kept on outer)
            transition(session, plan, "ENTRY_TOUCHED",
                       reason=f"entry zone touched at {price}",
                       market_price=price)
            transition(session, plan, "ACTIVE",
                       reason="plan is now active — between entry and SL/TP1",
                       market_price=price)
            changed = True

    # --- 2. After entry: MFE/MAE tracking ---
    if outcome.entry_touched:
        # Favorable: distance in the direction of profit
        if decision == "BUY":
            favorable = (price - entry_ref) if price > entry_ref else 0.0
            adverse = (entry_ref - price) if price < entry_ref else 0.0
            tp_hit_target = max(tp1, tp2, tp3, tp4)  # max profit target
            sl_hit_target = min(stop, stop)
        else:  # SELL
            favorable = (entry_ref - price) if price < entry_ref else 0.0
            adverse = (price - entry_ref) if price > entry_ref else 0.0
            tp_hit_target = min(tp1, tp2, tp3, tp4)
            sl_hit_target = max(stop, stop)

        # MFE/MAE
        if outcome.max_favorable_excursion is None or favorable > outcome.max_favorable_excursion:
            outcome.max_favorable_excursion = round(favorable, 2)
        if outcome.max_adverse_excursion is None or adverse > outcome.max_adverse_excursion:
            outcome.max_adverse_excursion = round(adverse, 2)

        # Time-to-entry
        if outcome.time_to_entry_seconds is None and outcome.entry_touched_at and plan.created_at:
            created = plan.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            touched = outcome.entry_touched_at
            if touched.tzinfo is None:
                touched = touched.replace(tzinfo=timezone.utc)
            outcome.time_to_entry_seconds = round((touched - created).total_seconds(), 1)

    # --- 3. SL before any TP detection ---
    sl_hit = False
    if decision == "BUY" and price <= stop:
        sl_hit = True
    elif decision == "SELL" and price >= stop:
        sl_hit = True

    if sl_hit and outcome.sl_hit_at is None:
        outcome.sl_hit_at = now
        outcome.sl_hit_price = price
        # Was SL hit before any TP?
        any_tp_reached = outcome.tp1_reached or outcome.tp2_reached or outcome.tp3_reached or outcome.tp4_reached
        outcome.sl_before_target = not any_tp_reached
        if outcome.sl_before_target:
            transition(session, plan, "STOPPED",
                       reason=f"SL hit at {price} before any TP",
                       market_price=price)
            outcome.final_status = "STOPPED"
            plan.final_status = "STOPPED"
        else:
            # SL after some TP — terminal but with TP-completed note
            transition(session, plan, "STOPPED",
                       reason=f"SL hit at {price} after TPs",
                       market_price=price)
            outcome.final_status = "STOPPED"
            plan.final_status = "STOPPED"
        changed = True

    # --- 4. TP detection (in TP1 -> TP2 -> TP3 -> TP4 order) ---
    if outcome.entry_touched and not sl_hit:
        # For BUY: TP1 < TP2 < TP3 < TP4 — price must cross each in order
        # For SELL: TP1 > TP2 > TP3 > TP4 — price must cross each in order (down)
        tps_in_order = [(1, tp1), (2, tp2), (3, tp3), (4, tp4)]
        for tp_num, tp_level in tps_in_order:
            reached = False
            if decision == "BUY" and price >= tp_level:
                reached = True
            elif decision == "SELL" and price <= tp_level:
                reached = True

            reached_attr = f"tp{tp_num}_reached"
            reached_at_attr = f"tp{tp_num}_reached_at"
            time_attr = f"time_to_tp{tp_num}_seconds"

            if reached and not getattr(outcome, reached_attr):
                setattr(outcome, reached_attr, True)
                setattr(outcome, reached_at_attr, now)
                # Time to TP
                if plan.created_at and getattr(outcome, time_attr) is None:
                    created = plan.created_at
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    setattr(outcome, time_attr, round((now - created).total_seconds(), 1))
                # Lifecycle transition
                to_state = f"TP{tp_num}_REACHED"
                transition(session, plan, to_state,
                           reason=f"TP{tp_num} reached at {price} (target {tp_level})",
                           market_price=price)
                # After TP1: breakeven consideration (advisory)
                if tp_num == 1:
                    transition(session, plan, "BREAKEVEN",
                               reason="TP1 reached — consider SL to entry (advisory, not executed)",
                               market_price=price)
                if tp_num == 4:
                    outcome.final_status = "COMPLETED_TP4"
                    plan.final_status = "COMPLETED_TP4"
                changed = True

    # --- 5. Expiry check ---
    if not outcome.entry_touched and is_plan_expired(plan, now):
        transition(session, plan, "EXPIRED",
                   reason=f"plan aged out (>24h) without entry touch",
                   market_price=price)
        outcome.final_status = "EXPIRED"
        plan.final_status = "EXPIRED"
        changed = True

    return changed


# Tiny mutable counter for the inner function (kept for log diagnostics only)
_summary_inner = {"entries_touched": 0}


def _update_inner(key: str) -> None:
    _summary_inner[key] = _summary_inner.get(key, 0) + 1


async def trade_plan_validation_loop(stop_event: asyncio.Event) -> None:
    """Background task: evaluate live plans every 60 seconds.

    Each iteration refreshes the live quote (via refresh_quote_once) then
    calls evaluate_live_plans to walk every live plan through its next
    lifecycle transition if applicable.
    """
    interval = 60  # 1 minute
    last_run = 0.0
    while not stop_event.is_set():
        if last_run >= interval:
            try:
                # Refresh the quote first so evaluate_live_plans has a
                # current price to check entry/SL/TP touches.
                try:
                    await refresh_quote_once()
                except Exception:
                    pass
                summary = await evaluate_live_plans()
                if summary.get("transitions", 0) > 0:
                    log.info(
                        "trade_plan_forward_validation — evaluated=%d transitions=%d entries=%d targets=%d stopped=%d expired=%d",
                        summary.get("evaluated", 0),
                        summary.get("transitions", 0),
                        summary.get("entries_touched", 0),
                        summary.get("targets_reached", 0),
                        summary.get("stopped", 0),
                        summary.get("expired", 0),
                    )
            except Exception as exc:
                log.warning("trade_plan_forward_validation failed: %s", exc)
            last_run = 0.0
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1)
        except asyncio.TimeoutError:
            last_run += 1
