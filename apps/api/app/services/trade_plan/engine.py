"""Phase 5.6: trade plan engine — main entry point.

generate_trade_plan(analysis: BrainAnalysis) -> dict
    Produces a plan dict (suitable for JSON serialization) and persists
    an immutable TradePlan row + initial lifecycle event.

The Brain's BUY/SELL/WAIT decision is the INPUT — this module never
overrides it. WAIT -> plan_status=NO_TRADE (no invented entries).

Quality gates:
  STALE              - market_timestamp too old (default > 90s)
  NO_VALID_ENTRY     - insufficient structure for entry zone
  NO_VALID_SL        - cannot derive technical SL
  NO_VALID_TP        - cannot derive 4 valid targets
  INSUFFICIENT_DATA  - missing price / timeframes
  NO_TRADE           - WAIT or NO_DECISION
  ACTIONABLE         - all gates passed, entry zone covers current price
  WAIT_FOR_ENTRY     - all gates passed, waiting for pullback to entry
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    TradePlan,
    TradePlanLifecycleEvent,
    TradePlanOutcome,
)
from app.db.session import SessionLocal
from app.engine.candles import get_candles
from app.models.market import BrainAnalysis, Candle
from .entry import derive_buy_entry, derive_sell_entry
from .instrument_meta import get_instrument_meta
from .lifecycle import record_event, transition
from .position_sizing import calculate_position_size, PositionSizeResult
from .risk_reward import compute_rr
from .stops import derive_buy_stop, derive_sell_stop
from .targets import derive_buy_targets, derive_sell_targets


PLAN_VERSION: str = "trade-plan-v0.1"
STALE_THRESHOLD_SECONDS: float = 90.0  # market_timestamp older than this is stale


def _gen_plan_id() -> str:
    return f"TP-{uuid.uuid4().hex[:8].upper()}"


def _is_stale(analysis: BrainAnalysis) -> bool:
    """A plan is stale if the analysis market_timestamp is too old."""
    if analysis.timestamp is None:
        return True
    now = datetime.now(timezone.utc)
    ts = analysis.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() > STALE_THRESHOLD_SECONDS


def _pick_candles_for_structure(analysis: BrainAnalysis) -> tuple[list[Candle], float | None]:
    """Pick the richest candle list for swing-level detection.

    Prefers 15min (good swing structure with reasonable recency) and
    falls back to 5min then 1h. Returns the candles + the ATR.

    Returns ([], atr) if candles cannot be fetched — entry/SL/TP
    functions handle this gracefully by falling back to ATR-based
    levels (no swing history).
    """
    # Prefer 15min for swing detection — rich structure without too much noise
    for tf in ("15min", "5min", "1h"):
        tf_state = next((s for s in analysis.timeframes if s.timeframe == tf), None)
        if tf_state and tf_state.candles >= 5:
            try:
                candles = _sync_get_candles(tf, 120)
                if candles and len(candles) >= 5:
                    return candles, tf_state.atr
            except Exception:
                pass
            # Couldn't fetch candles — at least return the ATR so
            # entry/SL/TP functions can use ATR-based fallback levels.
            return [], tf_state.atr
    return [], None


def _sync_get_candles(tf: str, count: int) -> list[Candle]:
    """Best-effort sync wrapper around get_candles (which is async).

    engine.candles.get_candles reads from the in-memory candle cache, so
    a sync call is OK — we just need to run the coroutine to completion.
    """
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(get_candles(tf, count))
        finally:
            loop.close()
    except Exception:
        return []


def _wait_plan(plan_id: str, analysis: BrainAnalysis, reason: str) -> dict:
    """Build a NO_TRADE plan dict for WAIT / NO_DECISION / insufficient data."""
    return {
        "plan_id": plan_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "market_timestamp": analysis.timestamp.isoformat() if analysis.timestamp else None,
        "instrument": "XAU/USD",
        "brain_decision": analysis.decision,
        "technical_score": analysis.technical_score,
        "entry_low": None,
        "entry_high": None,
        "entry_type": None,
        "entry_reference": None,
        "stop_loss": None,
        "invalidation_level": None,
        "invalidation_reason": None,
        "sl_distance": None,
        "tp1": None, "tp2": None, "tp3": None, "tp4": None,
        "tp1_reason": None, "tp2_reason": None, "tp3_reason": None, "tp4_reason": None,
        "risk_distance": None,
        "reward_tp1": None, "reward_tp2": None, "reward_tp3": None, "reward_tp4": None,
        "rr_tp1": None, "rr_tp2": None, "rr_tp3": None, "rr_tp4": None,
        "management_instructions": None,
        "plan_status": "NO_TRADE",
        "plan_version": PLAN_VERSION,
        "historical_similarity_run_id": getattr(analysis, "historical_similarity_run_id", None),
        "historical_context": getattr(analysis, "historical_alignment", None) or "INSUFFICIENT_DATA",
        "lifecycle_state": "CREATED",
        "reason": reason,
    }


def _persist_plan(plan_dict: dict) -> None:
    """Persist the plan dict as an immutable TradePlan row + initial event.

    Also creates an empty TradePlanOutcome row for forward validation.
    """
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        plan = TradePlan(
            plan_id=plan_dict["plan_id"],
            created_at=now,
            market_timestamp=datetime.fromisoformat(plan_dict["market_timestamp"].replace("Z", "+00:00")) if plan_dict.get("market_timestamp") else now,
            instrument=plan_dict.get("instrument", "XAU/USD"),
            brain_decision=plan_dict["brain_decision"],
            technical_score=plan_dict.get("technical_score"),
            entry_low=plan_dict.get("entry_low"),
            entry_high=plan_dict.get("entry_high"),
            entry_type=plan_dict.get("entry_type"),
            entry_reference=plan_dict.get("entry_reference"),
            stop_loss=plan_dict.get("stop_loss"),
            invalidation_level=plan_dict.get("invalidation_level"),
            invalidation_reason=plan_dict.get("invalidation_reason"),
            sl_distance=plan_dict.get("sl_distance"),
            tp1=plan_dict.get("tp1"),
            tp2=plan_dict.get("tp2"),
            tp3=plan_dict.get("tp3"),
            tp4=plan_dict.get("tp4"),
            tp1_reason=plan_dict.get("tp1_reason"),
            tp2_reason=plan_dict.get("tp2_reason"),
            tp3_reason=plan_dict.get("tp3_reason"),
            tp4_reason=plan_dict.get("tp4_reason"),
            risk_distance=plan_dict.get("risk_distance"),
            reward_tp1=plan_dict.get("reward_tp1"),
            reward_tp2=plan_dict.get("reward_tp2"),
            reward_tp3=plan_dict.get("reward_tp3"),
            reward_tp4=plan_dict.get("reward_tp4"),
            rr_tp1=plan_dict.get("rr_tp1"),
            rr_tp2=plan_dict.get("rr_tp2"),
            rr_tp3=plan_dict.get("rr_tp3"),
            rr_tp4=plan_dict.get("rr_tp4"),
            management_instructions=plan_dict.get("management_instructions"),
            plan_status=plan_dict["plan_status"],
            plan_version=PLAN_VERSION,
            historical_similarity_run_id=plan_dict.get("historical_similarity_run_id"),
            historical_context=plan_dict.get("historical_context"),
            lifecycle_state="CREATED",
        )
        session.add(plan)
        # Initial CREATED event
        record_event(
            session,
            plan_id=plan.plan_id,
            from_state=None,
            to_state="CREATED",
            reason=f"plan generated with status={plan.plan_status}",
            market_price=plan_dict.get("entry_reference"),
        )
        # Empty outcome row (will be filled by forward-validation loop)
        outcome = TradePlanOutcome(
            plan_id=plan.plan_id,
            entry_touched=False,
            tp1_reached=False,
            tp2_reached=False,
            tp3_reached=False,
            tp4_reached=False,
        )
        session.add(outcome)
        session.commit()


def generate_trade_plan(analysis: BrainAnalysis) -> dict:
    """Main entry point — generate a trade plan from the Brain's analysis.

    The Brain's BUY/SELL/WAIT decision is INPUT and is NEVER modified.
    """
    plan_id = _gen_plan_id()

    # Quality gate 1: stale market data
    if _is_stale(analysis):
        plan = _wait_plan(plan_id, analysis, reason=f"analysis market_timestamp is stale (>{STALE_THRESHOLD_SECONDS:.0f}s old)")
        plan["plan_status"] = "STALE"
        _persist_plan(plan)
        return plan

    # Quality gate 2: WAIT or NO_DECISION -> NO_TRADE
    if analysis.decision in ("WAIT", "NO_DECISION"):
        plan = _wait_plan(plan_id, analysis, reason=f"brain decision is {analysis.decision} — no trade plan emitted")
        _persist_plan(plan)
        return plan

    # Quality gate 3: missing price
    if analysis.price is None or analysis.price <= 0:
        plan = _wait_plan(plan_id, analysis, reason="no live price available")
        plan["plan_status"] = "INSUFFICIENT_DATA"
        _persist_plan(plan)
        return plan

    # Gather candles + ATR for structural levels.
    # We tolerate empty candles — entry/SL/TP functions fall back to ATR-based
    # levels derived from the analysis's support/resistance + ATR.
    candles, atr = _pick_candles_for_structure(analysis)
    if atr is None:
        plan = _wait_plan(plan_id, analysis, reason="no ATR available from any timeframe")
        plan["plan_status"] = "INSUFFICIENT_DATA"
        _persist_plan(plan)
        return plan

    # ---- BUY / SELL planning ----
    if analysis.decision == "BUY":
        entry = derive_buy_entry(
            price=analysis.price,
            support=analysis.support,
            candles=candles,
            atr=atr,
        )
    elif analysis.decision == "SELL":
        entry = derive_sell_entry(
            price=analysis.price,
            resistance=analysis.resistance,
            candles=candles,
            atr=atr,
        )
    else:
        # Should not reach here — covered above
        plan = _wait_plan(plan_id, analysis, reason=f"unsupported decision {analysis.decision}")
        _persist_plan(plan)
        return plan

    # Gate 4: no valid entry
    if entry.status == "NO_VALID_ENTRY":
        plan = _wait_plan(plan_id, analysis, reason=entry.reason or "no valid entry zone derivable")
        plan["plan_status"] = "NO_VALID_ENTRY"
        _persist_plan(plan)
        return plan

    # ---- Stop / invalidation ----
    if analysis.decision == "BUY":
        stop = derive_buy_stop(
            entry_reference=entry.entry_reference,
            support=analysis.support,
            candles=candles,
            atr=atr,
        )
    else:
        stop = derive_sell_stop(
            entry_reference=entry.entry_reference,
            resistance=analysis.resistance,
            candles=candles,
            atr=atr,
        )

    if stop.status != "OK":
        plan = _wait_plan(plan_id, analysis, reason=stop.reason or "no valid SL derivable")
        plan["plan_status"] = "NO_VALID_SL"
        _persist_plan(plan)
        return plan

    # ---- Targets ----
    risk_distance = stop.sl_distance
    if analysis.decision == "BUY":
        targets = derive_buy_targets(
            entry_reference=entry.entry_reference,
            stop_loss=stop.stop_loss,
            risk_distance=risk_distance,
            resistance=analysis.resistance,
            candles=candles,
            atr=atr,
        )
    else:
        targets = derive_sell_targets(
            entry_reference=entry.entry_reference,
            stop_loss=stop.stop_loss,
            risk_distance=risk_distance,
            support=analysis.support,
            candles=candles,
            atr=atr,
        )

    if targets.status != "OK" or not targets.tps or len(targets.tps) < 4:
        plan = _wait_plan(plan_id, analysis, reason=targets.reason or "no valid TP set derivable")
        plan["plan_status"] = "NO_VALID_TP"
        _persist_plan(plan)
        return plan

    # ---- R:R ----
    risk_distance_computed, rewards = compute_rr(
        entry_reference=entry.entry_reference,
        stop_loss=stop.stop_loss,
        tps=targets.tps,
    )

    # ---- Management instructions ----
    mgmt = (
        "After TP1 reached, consider moving SL to entry (breakeven). "
        "Advisory only — no automatic execution."
    )

    plan = {
        "plan_id": plan_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "market_timestamp": analysis.timestamp.isoformat() if analysis.timestamp else None,
        "instrument": "XAU/USD",
        "brain_decision": analysis.decision,
        "technical_score": analysis.technical_score,
        "entry_low": entry.entry_low,
        "entry_high": entry.entry_high,
        "entry_type": entry.entry_type,
        "entry_reference": entry.entry_reference,
        "stop_loss": stop.stop_loss,
        "invalidation_level": stop.invalidation_level,
        "invalidation_reason": stop.invalidation_reason,
        "sl_distance": stop.sl_distance,
        "tp1": targets.tps[0],
        "tp2": targets.tps[1],
        "tp3": targets.tps[2],
        "tp4": targets.tps[3],
        "tp1_reason": targets.reasons[0] if targets.reasons else None,
        "tp2_reason": targets.reasons[1] if targets.reasons else None,
        "tp3_reason": targets.reasons[2] if targets.reasons else None,
        "tp4_reason": targets.reasons[3] if targets.reasons else None,
        "risk_distance": risk_distance_computed,
        "reward_tp1": rewards[0].reward,
        "reward_tp2": rewards[1].reward,
        "reward_tp3": rewards[2].reward,
        "reward_tp4": rewards[3].reward,
        "rr_tp1": rewards[0].rr,
        "rr_tp2": rewards[1].rr,
        "rr_tp3": rewards[2].rr,
        "rr_tp4": rewards[3].rr,
        "management_instructions": mgmt,
        "plan_status": "ACTIONABLE" if entry.status == "ACTIONABLE" else "WAIT_FOR_ENTRY",
        "plan_version": PLAN_VERSION,
        "historical_similarity_run_id": getattr(analysis, "historical_similarity_run_id", None),
        "historical_context": getattr(analysis, "historical_alignment", None) or "INSUFFICIENT_DATA",
        "lifecycle_state": "CREATED",
    }

    _persist_plan(plan)
    return plan


def get_current_plan() -> dict:
    """Return the most recent plan (any status)."""
    with SessionLocal() as session:
        plan = session.scalar(
            select(TradePlan).order_by(TradePlan.created_at.desc()).limit(1)
        )
        if plan is None:
            return {"plan": None, "reason": "no plan generated yet"}
        return _plan_row_to_dict(plan)


def list_plans(limit: int = 20) -> dict:
    """List recent plans (newest first)."""
    with SessionLocal() as session:
        result = session.execute(
            select(TradePlan).order_by(TradePlan.created_at.desc()).limit(limit)
        )
        plans = list(result.scalars())
        return {"plans": [_plan_row_to_dict(p)["plan"] for p in plans], "count": len(plans)}


def get_plan(plan_id: str) -> dict:
    """Get one plan + its lifecycle events + its outcome."""
    with SessionLocal() as session:
        plan = session.scalar(
            select(TradePlan).where(TradePlan.plan_id == plan_id).limit(1)
        )
        if plan is None:
            return {"plan": None, "reason": f"plan {plan_id} not found"}
        out = _plan_row_to_dict(plan)
        # Lifecycle events
        events = session.execute(
            select(TradePlanLifecycleEvent)
            .where(TradePlanLifecycleEvent.plan_id == plan_id)
            .order_by(TradePlanLifecycleEvent.event_at.asc())
        ).scalars().all()
        out["lifecycle_events"] = [
            {
                "event_at": e.event_at.isoformat() if e.event_at else None,
                "from_state": e.from_state,
                "to_state": e.to_state,
                "reason": e.reason,
                "market_price": e.market_price,
            }
            for e in events
        ]
        # Outcome row
        outcome = session.scalar(
            select(TradePlanOutcome).where(TradePlanOutcome.plan_id == plan_id).limit(1)
        )
        out["outcome"] = _outcome_row_to_dict(outcome) if outcome else None
        return out


def get_performance() -> dict:
    """Aggregate performance metrics across all plans.

    Phase 5.7.1: WAIT decisions are reported SEPARATELY from actionable
    BUY/SELL plans. Repeated WAIT snapshots are NOT treated as trading
    performance — they're informational market-state records.
    """
    with SessionLocal() as session:
        # Total counts
        all_plans = list(session.execute(select(TradePlan)).scalars())
        total = len(all_plans)
        by_decision = {}
        by_status = {}
        by_lifecycle = {}
        by_engine_version = {}
        # Phase 5.7.1: separate WAIT from actionable
        wait_count = 0
        actionable_buy_count = 0
        actionable_sell_count = 0
        no_trade_count = 0
        for p in all_plans:
            by_decision[p.brain_decision] = by_decision.get(p.brain_decision, 0) + 1
            by_status[p.plan_status] = by_status.get(p.plan_status, 0) + 1
            by_lifecycle[p.lifecycle_state] = by_lifecycle.get(p.lifecycle_state, 0) + 1
            ev = p.plan_engine_version or "trade-plan-v0.1"
            by_engine_version[ev] = by_engine_version.get(ev, 0) + 1

            if p.brain_decision == "WAIT" or p.plan_status == "NO_TRADE":
                wait_count += 1
            elif p.brain_decision == "BUY" and p.plan_status in ("ACTIONABLE", "WAIT_FOR_ENTRY"):
                actionable_buy_count += 1
            elif p.brain_decision == "SELL" and p.plan_status in ("ACTIONABLE", "WAIT_FOR_ENTRY"):
                actionable_sell_count += 1
            if p.plan_status == "NO_TRADE":
                no_trade_count += 1

        # Determine version display: MIXED if multiple engine versions exist
        if len(by_engine_version) > 1:
            version_display = "MIXED"
        elif by_engine_version:
            version_display = list(by_engine_version.keys())[0]
        else:
            version_display = PLAN_VERSION

        # Outcome counts — only for ACTIONABLE plans (not WAIT)
        actionable_plan_ids = [
            p.plan_id for p in all_plans
            if p.plan_status in ("ACTIONABLE", "WAIT_FOR_ENTRY")
        ]
        if actionable_plan_ids:
            outcomes = list(
                session.execute(
                    select(TradePlanOutcome).where(TradePlanOutcome.plan_id.in_(actionable_plan_ids))
                ).scalars()
            )
        else:
            outcomes = []
        outcome_summary = {
            "total_outcomes_tracked": len(outcomes),
            "entry_touched_count": sum(1 for o in outcomes if o.entry_touched),
            "sl_before_target_count": sum(1 for o in outcomes if o.sl_before_target),
            "tp1_reached": sum(1 for o in outcomes if o.tp1_reached),
            "tp2_reached": sum(1 for o in outcomes if o.tp2_reached),
            "tp3_reached": sum(1 for o in outcomes if o.tp3_reached),
            "tp4_reached": sum(1 for o in outcomes if o.tp4_reached),
        }
        return {
            "total_plans": total,
            "by_decision": by_decision,
            "by_plan_status": by_status,
            "by_lifecycle_state": by_lifecycle,
            "by_engine_version": by_engine_version,
            "version_display": version_display,
            # Phase 5.7.1: WAIT separated from actionable
            "wait_count": wait_count,
            "actionable_buy_count": actionable_buy_count,
            "actionable_sell_count": actionable_sell_count,
            "no_trade_count": no_trade_count,
            "actionable_total": actionable_buy_count + actionable_sell_count,
            "outcome_summary": outcome_summary,
            "plan_version": PLAN_VERSION,
        }


def _plan_row_to_dict(plan: TradePlan) -> dict:
    import json as _json
    # Parse Phase 5.7 ICT extension fields if present
    for_ev = None
    against_ev = None
    session_ctx = None
    if plan.for_evidence_json:
        try:
            for_ev = _json.loads(plan.for_evidence_json)
        except Exception:
            for_ev = None
    if plan.against_evidence_json:
        try:
            against_ev = _json.loads(plan.against_evidence_json)
        except Exception:
            against_ev = None
    if plan.session_context_json:
        try:
            session_ctx = _json.loads(plan.session_context_json)
        except Exception:
            session_ctx = None

    return {
        "plan": {
            "plan_id": plan.plan_id,
            "created_at": plan.created_at.isoformat() if plan.created_at else None,
            "market_timestamp": plan.market_timestamp.isoformat() if plan.market_timestamp else None,
            "instrument": plan.instrument,
            "brain_decision": plan.brain_decision,
            "technical_score": plan.technical_score,
            "entry_low": plan.entry_low,
            "entry_high": plan.entry_high,
            "entry_type": plan.entry_type,
            "entry_reference": plan.entry_reference,
            "preferred_entry": plan.preferred_entry,  # Phase 5.7
            "entry_reason": getattr(plan, "entry_reason", None),  # may not exist as a column; safe via getattr
            "stop_loss": plan.stop_loss,
            "invalidation_level": plan.invalidation_level,
            "invalidation_reason": plan.invalidation_reason,
            "structural_invalidation": plan.structural_invalidation,  # Phase 5.7
            "sl_distance": plan.sl_distance,
            "tp1": plan.tp1, "tp2": plan.tp2, "tp3": plan.tp3, "tp4": plan.tp4,
            "max_objective": plan.max_objective,  # Phase 5.7
            "max_objective_reason": plan.max_objective_reason,  # Phase 5.7
            "tp1_reason": plan.tp1_reason, "tp2_reason": plan.tp2_reason,
            "tp3_reason": plan.tp3_reason, "tp4_reason": plan.tp4_reason,
            "risk_distance": plan.risk_distance,
            "reward_tp1": plan.reward_tp1, "reward_tp2": plan.reward_tp2,
            "reward_tp3": plan.reward_tp3, "reward_tp4": plan.reward_tp4,
            "rr_tp1": plan.rr_tp1, "rr_tp2": plan.rr_tp2,
            "rr_tp3": plan.rr_tp3, "rr_tp4": plan.rr_tp4,
            "management_instructions": plan.management_instructions,
            "plan_status": plan.plan_status,
            "plan_version": plan.plan_version,
            "plan_engine_version": plan.plan_engine_version,  # Phase 5.7
            "historical_similarity_run_id": plan.historical_similarity_run_id,
            "historical_context": plan.historical_context,
            "lifecycle_state": plan.lifecycle_state,
            "final_status": plan.final_status,
            # Phase 5.7 ICT extensions
            "setup_thesis": plan.setup_thesis,
            "for_evidence": for_ev,
            "against_evidence": against_ev,
            "session_context": session_ctx,
            "setup_pattern_id": plan.setup_pattern_id,
            # Phase 5.7.1: dedup + quality hardening
            "setup_fingerprint": plan.setup_fingerprint,
            "short_reason": plan.short_reason,
            "reused_existing_plan": False,  # default; set to True when dedup returns existing
        }
    }


def _outcome_row_to_dict(o: TradePlanOutcome) -> dict:
    return {
        "last_evaluated_at": o.last_evaluated_at.isoformat() if o.last_evaluated_at else None,
        "entry_touched": o.entry_touched,
        "entry_touched_at": o.entry_touched_at.isoformat() if o.entry_touched_at else None,
        "entry_touch_price": o.entry_touch_price,
        "sl_before_target": o.sl_before_target,
        "sl_hit_at": o.sl_hit_at.isoformat() if o.sl_hit_at else None,
        "sl_hit_price": o.sl_hit_price,
        "tp1_reached": o.tp1_reached,
        "tp2_reached": o.tp2_reached,
        "tp3_reached": o.tp3_reached,
        "tp4_reached": o.tp4_reached,
        "tp1_reached_at": o.tp1_reached_at.isoformat() if o.tp1_reached_at else None,
        "tp2_reached_at": o.tp2_reached_at.isoformat() if o.tp2_reached_at else None,
        "tp3_reached_at": o.tp3_reached_at.isoformat() if o.tp3_reached_at else None,
        "tp4_reached_at": o.tp4_reached_at.isoformat() if o.tp4_reached_at else None,
        "max_favorable_excursion": o.max_favorable_excursion,
        "max_adverse_excursion": o.max_adverse_excursion,
        "time_to_entry_seconds": o.time_to_entry_seconds,
        "time_to_tp1_seconds": o.time_to_tp1_seconds,
        "time_to_tp2_seconds": o.time_to_tp2_seconds,
        "time_to_tp3_seconds": o.time_to_tp3_seconds,
        "time_to_tp4_seconds": o.time_to_tp4_seconds,
        "final_status": o.final_status,
    }


__all__ = [
    "PLAN_VERSION",
    "generate_trade_plan",
    "get_current_plan",
    "list_plans",
    "get_plan",
    "get_performance",
    "calculate_position_size",
]
