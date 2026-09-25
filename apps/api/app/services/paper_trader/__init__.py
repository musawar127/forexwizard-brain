"""Phase 6A: paper trader engine — entry/SL/TP/MAX lifecycle with event ordering.

PAPER / SHADOW TRADING ONLY. No real broker orders.

The engine virtually executes actionable ICT trade plans:
  1. Creates a PaperTrade from an actionable TradePlan (one plan → one trade, no duplicates)
  2. Background loop checks waiting entries (entry_low <= price <= entry_high)
  3. Once entered, tracks SL/TP with EVENT ORDERING (which happened first matters)
  4. MFE/MAE tracked from entry to terminal
  5. After TP1: breakeven management (management-v0.1)
  6. Terminal trades → post_trade_review()
  7. Reviews → mistake pattern aggregation → candidate rules (NEVER auto-promoted)

No historical fabrication. Only genuine future market data advances lifecycle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select, func as sa_func
from sqlalchemy.orm import Session

from app.db.models import (
    PaperAccount,
    PaperTrade,
    TradeReview,
    StrategyMistakePattern,
    CandidateStrategyRule,
    PaperTraderAuditLog,
    TradePlan,
    TradePlanOutcome,
)
from app.db.session import SessionLocal
from app.services.market_state import refresh_quote_once, state

log = logging.getLogger("forexwizard")

PAPER_ACCOUNT_ID = "PA-DEFAULT"
STARTING_EQUITY = 10000.0
PAPER_TRADE_VERSION = "paper-trader-v0.1"
MANAGEMENT_VERSION = "management-v0.1"
REVIEW_VERSION = "review-v0.1"

# Lifecycle states
LIVE_STATES = {
    "WAITING_FOR_ENTRY",
    "ACTIVE",
    "TP1_REACHED",
    "TP2_REACHED",
    "TP3_REACHED",
    "BREAKEVEN",
}
TERMINAL_STATES = {
    "MAX_REACHED",
    "STOPPED",
    "EXPIRED",
    "INVALIDATED",
    "CANCELLED",
}

# Minimum sample guards
MIN_SAMPLE_INSUFFICIENT = 10
MIN_SAMPLE_EARLY = 30
MIN_SAMPLE_DEVELOPING = 100


def _to_utc(dt: datetime | None) -> datetime | None:
    """Normalize a datetime to timezone-aware UTC.

    SQLite returns naive datetimes even for DateTime(timezone=True) columns.
    PostgreSQL returns aware datetimes for timestamptz columns.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sample_quality_label(n: int) -> str:
    """Return the evidence-quality label for a sample size."""
    if n < MIN_SAMPLE_INSUFFICIENT:
        return "INSUFFICIENT_SAMPLE"
    if n < MIN_SAMPLE_EARLY:
        return "EARLY"
    if n < MIN_SAMPLE_DEVELOPING:
        return "DEVELOPING"
    return "MORE_ESTABLISHED"


# ---------- paper account ----------

def get_or_create_paper_account(session: Session) -> PaperAccount:
    """Get the default paper account, or create it if it doesn't exist."""
    account = session.scalar(
        select(PaperAccount).where(PaperAccount.paper_account_id == PAPER_ACCOUNT_ID).limit(1)
    )
    if account is not None:
        return account
    now = datetime.now(timezone.utc)
    account = PaperAccount(
        paper_account_id=PAPER_ACCOUNT_ID,
        created_at=now,
        starting_equity=STARTING_EQUITY,
        current_equity=STARTING_EQUITY,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        max_equity=STARTING_EQUITY,
        max_drawdown=0.0,
        paper_trade_count=0,
        win_count=0,
        loss_count=0,
        breakeven_count=0,
    )
    session.add(account)
    session.commit()
    return account


def _audit_log(
    session: Session,
    event_type: str,
    paper_trade_id: str | None = None,
    detail: str | None = None,
    market_price: float | None = None,
) -> None:
    """Append an audit log entry. Does NOT commit."""
    session.add(PaperTraderAuditLog(
        event_type=event_type,
        paper_trade_id=paper_trade_id,
        event_at=datetime.now(timezone.utc),
        detail=(detail or "")[:255],
        market_price=market_price,
    ))


# ---------- paper trade creation ----------

def _build_market_snapshot(plan: TradePlan) -> str:
    """Build an immutable JSON snapshot of market state at trade creation time."""
    snapshot = {
        "brain_decision": plan.brain_decision,
        "technical_score": plan.technical_score,
        "plan_engine_version": plan.plan_engine_version,
        "setup_thesis": plan.setup_thesis,
        "setup_fingerprint": plan.setup_fingerprint,
        "session_context": json.loads(plan.session_context_json) if plan.session_context_json else None,
        "for_evidence": json.loads(plan.for_evidence_json) if plan.for_evidence_json else None,
        "against_evidence": json.loads(plan.against_evidence_json) if plan.against_evidence_json else None,
        "historical_context": plan.historical_context,
        "entry_low": plan.entry_low,
        "entry_high": plan.entry_high,
        "preferred_entry": plan.preferred_entry,
        "stop_loss": plan.stop_loss,
        "tp1": plan.tp1,
        "tp2": plan.tp2,
        "tp3": plan.tp3,
        "max_objective": plan.max_objective,
        "risk_distance": plan.risk_distance,
        "invalidation_level": plan.invalidation_level,
        "structural_invalidation": plan.structural_invalidation,
        "plan_status": plan.plan_status,
        "plan_version": plan.plan_version,
        "market_timestamp": plan.market_timestamp.isoformat() if plan.market_timestamp else None,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }
    return json.dumps(snapshot, default=str)


def create_paper_trade_from_plan(plan: TradePlan) -> dict | None:
    """Create a PaperTrade from an actionable TradePlan.

    Rules:
      - Only ACTIONABLE or WAIT_FOR_ENTRY plans are eligible
      - WAIT / NO_TRADE plans → NO paper trade (return None)
      - One trade_plan_id → maximum ONE paper trade (no duplicates)
      - Market snapshot is frozen at creation (immutable)
    """
    if plan.plan_status not in ("ACTIONABLE", "WAIT_FOR_ENTRY"):
        return None

    with SessionLocal() as session:
        # Check for existing paper trade for this plan_id (no duplicates)
        existing = session.scalar(
            select(PaperTrade).where(PaperTrade.trade_plan_id == plan.plan_id).limit(1)
        )
        if existing is not None:
            return {
                "paper_trade_id": existing.paper_trade_id,
                "status": existing.status,
                "reused_existing": True,
                "reason": "paper trade already exists for this plan_id",
            }

        account = get_or_create_paper_account(session)
        now = datetime.now(timezone.utc)
        paper_trade_id = f"PT-{uuid.uuid4().hex[:8].upper()}"

        snapshot_json = _build_market_snapshot(plan)

        trade = PaperTrade(
            paper_trade_id=paper_trade_id,
            trade_plan_id=plan.plan_id,
            paper_account_id=account.paper_account_id,
            instrument=plan.instrument,
            direction=plan.brain_decision,  # BUY or SELL
            entry_low=plan.entry_low,
            entry_high=plan.entry_high,
            preferred_entry=plan.preferred_entry or plan.entry_reference,
            actual_paper_entry=None,  # filled when entry is touched
            stop_loss=plan.stop_loss,
            tp1=plan.tp1,
            tp2=plan.tp2,
            tp3=plan.tp3,
            max_objective=plan.max_objective or plan.tp4,
            status="WAITING_FOR_ENTRY",
            entry_timestamp=None,
            exit_timestamp=None,
            exit_reason=None,
            realized_pnl_points=None,
            realized_r_multiple=None,
            mfe=None,
            mae=None,
            time_to_entry=None,
            time_to_tp1=None,
            time_to_tp2=None,
            time_to_tp3=None,
            time_to_max=None,
            time_to_stop=None,
            breakeven_activated_at=None,
            breakeven_price=None,
            setup_fingerprint=plan.setup_fingerprint,
            strategy_version=PAPER_TRADE_VERSION,
            plan_engine_version=plan.plan_engine_version or "ict-plan-v0.1",
            market_snapshot_json=snapshot_json,
            created_at=now,
            updated_at=now,
        )
        session.add(trade)
        account.paper_trade_count += 1
        _audit_log(session, "PAPER_TRADE_CREATED", paper_trade_id,
                    detail=f"direction={trade.direction} status=WAITING_FOR_ENTRY plan={plan.plan_id}")
        session.commit()

        return {
            "paper_trade_id": paper_trade_id,
            "status": "WAITING_FOR_ENTRY",
            "reused_existing": False,
            "direction": trade.direction,
            "entry_low": trade.entry_low,
            "entry_high": trade.entry_high,
            "stop_loss": trade.stop_loss,
            "tp1": trade.tp1,
            "tp2": trade.tp2,
            "tp3": trade.tp3,
            "max_objective": trade.max_objective,
            "setup_fingerprint": trade.setup_fingerprint,
        }


# ---------- paper trade evaluation (background loop) ----------

async def evaluate_paper_trades() -> dict:
    """One pass over all live paper trades. Returns a summary dict.

    Designed to be called from the background loop. Uses the live spot
    price from state.quote to check entry/SL/TP lifecycle.

    Event ordering is critical: we determine which event happened FIRST
    by checking SL and TPs in the order they would have been hit based
    on candle high/low. For spot price (no OHLC), we check sequentially.
    """
    now = datetime.now(timezone.utc)
    summary = {
        "evaluated": 0,
        "entries_touched": 0,
        "tps_reached": 0,
        "stopped": 0,
        "breakeven": 0,
        "expired": 0,
        "reviews_completed": 0,
    }

    # Uses whatever quote is currently in state.quote. The background
    # loop (paper_trader_loop) refreshes the quote before calling this.
    # Tests can mock state.quote directly.
    price = None
    if state.quote is not None:
        price = state.quote.price
    if price is None or price <= 0:
        return {**summary, "reason": "no live price available"}

    with SessionLocal() as session:
        # Get all live paper trades
        live_trades = list(
            session.execute(
                select(PaperTrade).where(PaperTrade.status.in_(list(LIVE_STATES)))
                .order_by(PaperTrade.created_at.asc())
            ).scalars()
        )

        for trade in live_trades:
            summary["evaluated"] += 1
            _evaluate_one_trade(session, trade, price, now)
            if trade.status in TERMINAL_STATES:
                # Run post-trade review for terminal trades
                review = run_post_trade_review(session, trade)
                if review is not None:
                    summary["reviews_completed"] += 1
            # Update the trade's updated_at
            trade.updated_at = now

        session.commit()

    return summary


def _evaluate_one_trade(
    session: Session,
    trade: PaperTrade,
    price: float,
    now: datetime,
) -> None:
    """Evaluate one paper trade against the current price.

    Event ordering:
      1. If WAITING_FOR_ENTRY: check if price touched entry zone
      2. If entry touched: mark ACTIVE, set actual_paper_entry
      3. If ACTIVE or post-TP1: check SL and TPs IN ORDER
         - For BUY: SL if price <= stop, TP1 if price >= tp1, etc.
         - For SELL: SL if price >= stop, TP1 if price <= tp1, etc.
      4. After TP1: activate breakeven (management-v0.1)
      5. Track MFE/MAE from entry
      6. Terminal: SL → STOPPED, MAX → MAX_REACHED, expiry → EXPIRED
    """
    direction = trade.direction
    is_buy = direction == "BUY"
    is_sell = direction == "SELL"

    # --- 1. Entry check ---
    if trade.status == "WAITING_FOR_ENTRY":
        if trade.entry_low is not None and trade.entry_high is not None:
            if trade.entry_low <= price <= trade.entry_high:
                # Entry touched!
                trade.actual_paper_entry = trade.preferred_entry or price
                trade.entry_timestamp = now
                trade.status = "ACTIVE"
                if trade.created_at:
                    created = _to_utc(trade.created_at)
                    if created:
                        trade.time_to_entry = round((now - created).total_seconds(), 1)
                _audit_log(session, "PAPER_ENTRY_TOUCHED", trade.paper_trade_id,
                            detail=f"entry at {price}", market_price=price)

    # --- 2. If active (or post-TP), check SL and TPs ---
    if trade.status in ("ACTIVE", "TP1_REACHED", "TP2_REACHED", "TP3_REACHED", "BREAKEVEN"):
        entry = trade.actual_paper_entry
        if entry is None:
            return  # shouldn't happen but defensive

        # MFE/MAE tracking
        if is_buy:
            favorable = max(0.0, price - entry)
            adverse = max(0.0, entry - price)
        else:
            favorable = max(0.0, entry - price)
            adverse = max(0.0, price - entry)
        if trade.mfe is None or favorable > trade.mfe:
            trade.mfe = round(favorable, 2)
        if trade.mae is None or adverse > trade.mae:
            trade.mae = round(adverse, 2)

        # SL check (event ordering: SL before TPs if SL is hit)
        sl_hit = False
        if trade.stop_loss is not None:
            if is_buy and price <= trade.stop_loss:
                sl_hit = True
            elif is_sell and price >= trade.stop_loss:
                sl_hit = True

        # If SL hit AND no TP reached yet → STOPPED (SL before TP1)
        if sl_hit:
            any_tp_reached = (
                trade.status in ("TP1_REACHED", "TP2_REACHED", "TP3_REACHED", "BREAKEVEN")
            )
            trade.exit_timestamp = now
            trade.exit_reason = "STOP_LOSS_HIT"
            trade.time_to_stop = round((now - _to_utc(trade.entry_timestamp)).total_seconds(), 1) if trade.entry_timestamp else None
            risk = abs(entry - trade.stop_loss) if trade.stop_loss else 0
            if risk > 0:
                # SL = -1R (or less if SL after some TP)
                trade.realized_pnl_points = round(-risk, 2)
                trade.realized_r_multiple = -1.0
            trade.status = "STOPPED"
            _audit_log(session, "PAPER_STOP", trade.paper_trade_id,
                        detail=f"SL hit at {price}", market_price=price)
            return

        # TP checks (in order: TP1 → TP2 → TP3 → MAX)
        tp_checks = [
            (1, trade.tp1, "TP1_REACHED", "PAPER_TP1", trade.time_to_tp1),
            (2, trade.tp2, "TP2_REACHED", "PAPER_TP2", trade.time_to_tp2),
            (3, trade.tp3, "TP3_REACHED", "PAPER_TP3", trade.time_to_tp3),
            (4, trade.max_objective, "MAX_REACHED", "PAPER_MAX", trade.time_to_max),
        ]

        for tp_num, tp_level, target_state, audit_event, _existing_time in tp_checks:
            if tp_level is None:
                continue
            # Check if this TP is already reached (for TP1/2/3 states)
            already_at_or_past = False
            if tp_num == 1 and trade.status in ("TP1_REACHED", "TP2_REACHED", "TP3_REACHED", "BREAKEVEN", "MAX_REACHED"):
                already_at_or_past = True
            elif tp_num == 2 and trade.status in ("TP2_REACHED", "TP3_REACHED", "MAX_REACHED"):
                already_at_or_past = True
            elif tp_num == 3 and trade.status in ("TP3_REACHED", "MAX_REACHED"):
                already_at_or_past = True
            elif tp_num == 4 and trade.status == "MAX_REACHED":
                already_at_or_past = True

            if already_at_or_past:
                continue

            tp_hit = False
            if is_buy and price >= tp_level:
                tp_hit = True
            elif is_sell and price <= tp_level:
                tp_hit = True

            if tp_hit:
                # Update time to this TP
                time_attr = f"time_to_tp{tp_num}" if tp_num < 4 else "time_to_max"
                if trade.entry_timestamp:
                    entry_ts = _to_utc(trade.entry_timestamp)
                    if entry_ts:
                        setattr(trade, time_attr, round((now - entry_ts).total_seconds(), 1))

                trade.status = target_state
                _audit_log(session, audit_event, trade.paper_trade_id,
                            detail=f"TP{tp_num} reached at {price} (target {tp_level})",
                            market_price=price)

                # After TP1: activate breakeven (management-v0.1)
                if tp_num == 1:
                    trade.breakeven_activated_at = now
                    trade.breakeven_price = entry  # move SL to entry
                    _audit_log(session, "PAPER_BREAKEVEN", trade.paper_trade_id,
                                detail=f"BE activated at entry {entry} after TP1",
                                market_price=price)

                # MAX = terminal
                if tp_num == 4:
                    trade.exit_timestamp = now
                    trade.exit_reason = "MAX_OBJECTIVE_REACHED"
                    risk = abs(entry - trade.stop_loss) if trade.stop_loss else 0
                    profit = abs(tp_level - entry)
                    if risk > 0:
                        trade.realized_r_multiple = round(profit / risk, 2)
                    trade.realized_pnl_points = round(profit if is_buy else -profit, 2)
                    # Actually for BUY profit = tp - entry (positive); for SELL profit = entry - tp (positive)
                    trade.realized_pnl_points = round(abs(tp_level - entry), 2)
                    return

                # Non-MAX TP: continue to next TP in the same pass
                # (don't break — if price is above all TPs, process them all)
                continue

        # --- 3. Breakeven check (if BE was activated and price returns to entry) ---
        if trade.status == "BREAKEVEN" or (trade.breakeven_price is not None and trade.status in ("TP1_REACHED", "TP2_REACHED", "TP3_REACHED")):
            be_price = trade.breakeven_price
            if be_price is not None:
                be_hit = False
                if is_buy and price <= be_price:
                    be_hit = True
                elif is_sell and price >= be_price:
                    be_hit = True
                if be_hit and trade.status != "BREAKEVEN":
                    trade.exit_timestamp = now
                    trade.exit_reason = "BREAKEVEN_HIT"
                    trade.realized_pnl_points = 0.0
                    trade.realized_r_multiple = 0.0
                    trade.status = "BREAKEVEN"
                    _audit_log(session, "PAPER_BREAKEVEN", trade.paper_trade_id,
                                detail=f"BE hit at {price}", market_price=price)
                    return

    # --- 4. Expiry check (24h without entry touch) ---
    if trade.status == "WAITING_FOR_ENTRY":
        if trade.created_at:
            created = _to_utc(trade.created_at)
            if not created:
                return
            age = (now - created).total_seconds()
            if age > 24 * 3600:
                trade.exit_timestamp = now
                trade.exit_reason = "EXPIRED_NO_ENTRY"
                trade.status = "EXPIRED"
                _audit_log(session, "PAPER_STOP", trade.paper_trade_id,
                            detail="expired (24h no entry touch)", market_price=price)


# ---------- post-trade review ----------

def run_post_trade_review(session: Session, trade: PaperTrade) -> TradeReview | None:
    """Run post-trade review for a terminal paper trade.

    For LOSSES: classify possible failure reasons (HTF_CONFLICT, NO_VALID_MSS, etc.)
    For WINS: store what appeared useful (HTF alignment, clean sweep, etc.)
    For BREAKEVEN: minimal review
    """
    if trade.status not in TERMINAL_STATES and trade.status != "BREAKEVEN":
        return None

    # Check if review already exists (idempotent)
    existing = session.scalar(
        select(TradeReview).where(TradeReview.paper_trade_id == trade.paper_trade_id).limit(1)
    )
    if existing is not None:
        return existing

    # Determine result
    r = trade.realized_r_multiple or 0.0
    if trade.status == "STOPPED":
        result = "LOSS"
    elif trade.status == "BREAKEVEN":
        result = "BREAKEVEN"
    elif trade.status == "EXPIRED":
        result = "BREAKEVEN"  # no loss, no win
    elif trade.status in ("TP1_REACHED", "TP2_REACHED", "TP3_REACHED", "MAX_REACHED"):
        result = "WIN" if r > 0 else "BREAKEVEN"
    else:
        result = "BREAKEVEN"

    # Parse market snapshot
    snapshot = {}
    if trade.market_snapshot_json:
        try:
            snapshot = json.loads(trade.market_snapshot_json)
        except Exception:
            snapshot = {}

    session_ctx = snapshot.get("session_context") or {}
    for_ev = snapshot.get("for_evidence") or []
    against_ev = snapshot.get("against_evidence") or []

    # Build what_worked / what_failed
    what_worked: list[str] = []
    what_failed: list[str] = []
    mistake_tags: list[str] = []

    if result == "LOSS":
        # Classify loss reasons
        htf_trend = session_ctx.get("htf_trend", "RANGE")
        m15_trend = session_ctx.get("m15_trend", "RANGE")
        sweep_dir = session_ctx.get("sweep_direction")
        location = session_ctx.get("location", "EQUILIBRIUM")
        displacement = session_ctx.get("displacement_confirmed", False)
        fvg_active = session_ctx.get("fvg_active", False)
        ob_active = session_ctx.get("ob_active", False)

        # Check for evidence types
        has_mss = any(e.get("kind") in ("CHoCH", "MSS") for e in for_ev)
        has_sweep = session_ctx.get("liquidity_swept", False)

        # Classify
        if htf_trend == "RANGE":
            mistake_tags.append("HTF_CONFLICT")
            what_failed.append("HTF trend was RANGE — no directional bias")
        if not has_mss:
            mistake_tags.append("NO_VALID_MSS")
            what_failed.append("No M15 CHoCH/MSS reversal trigger")
        if not has_sweep:
            mistake_tags.append("FALSE_LIQUIDITY_SWEEP")
            what_failed.append("No genuine liquidity sweep detected")
        if not displacement:
            mistake_tags.append("WEAK_MSS")
            what_failed.append("No displacement confirmation (institutional move missing)")
        if not fvg_active and not ob_active:
            mistake_tags.append("POOR_FVG")
            what_failed.append("No active FVG or OB for entry")
        if location == "EQUILIBRIUM":
            mistake_tags.append("MID_RANGE_ENTRY")
            what_failed.append("Entry at equilibrium (not premium/discount)")
        if trade.mae is not None and trade.mae > 5.0:
            mistake_tags.append("SL_TOO_TIGHT")
            what_failed.append(f"High MAE ({trade.mae}) — SL may have been too tight")
        if not mistake_tags:
            mistake_tags.append("UNKNOWN")
            what_failed.append("Insufficient evidence to classify — primary_failure_reason=UNKNOWN")

        primary_failure = mistake_tags[0] if mistake_tags else "UNKNOWN"
        secondary_failures = mistake_tags[1:] if len(mistake_tags) > 1 else []

    elif result == "WIN":
        # What worked
        if session_ctx.get("htf_trend") in ("BULLISH", "BEARISH"):
            what_worked.append(f"HTF alignment ({session_ctx.get('htf_trend')})")
        if session_ctx.get("liquidity_swept"):
            what_worked.append(f"Clean liquidity sweep ({session_ctx.get('sweep_direction')})")
        if any(e.get("kind") in ("CHoCH", "MSS") for e in for_ev):
            what_worked.append("Confirmed MSS/CHoCH reversal")
        if session_ctx.get("fvg_active"):
            what_worked.append("FVG retest")
        if session_ctx.get("ob_active"):
            what_worked.append("OB confirmation")
        if session_ctx.get("location") in ("PREMIUM", "DISCOUNT"):
            what_worked.append(f"Correct {session_ctx.get('location')} location")
        if session_ctx.get("displacement_confirmed"):
            what_worked.append("Strong displacement")
        if trade.mae is not None and trade.mae < 2.0:
            what_worked.append(f"Low MAE ({trade.mae})")
        if not what_worked:
            what_worked.append("Setup executed as planned")

        primary_failure = None
        secondary_failures = []
    else:  # BREAKEVEN
        what_worked.append("Trade managed to breakeven (no loss)")
        what_failed.append("Did not reach profit targets")
        primary_failure = None
        secondary_failures = []

    review = TradeReview(
        review_id=f"RV-{uuid.uuid4().hex[:8].upper()}",
        paper_trade_id=trade.paper_trade_id,
        result=result,
        r_multiple=r,
        what_worked_json=json.dumps(what_worked, default=str),
        what_failed_json=json.dumps(what_failed, default=str),
        mistake_tags_json=json.dumps(mistake_tags, default=str),
        market_context_json=json.dumps(session_ctx, default=str),
        primary_failure_reason=primary_failure,
        secondary_failure_reasons=json.dumps(secondary_failures, default=str) if secondary_failures else None,
        review_version=REVIEW_VERSION,
        created_at=datetime.now(timezone.utc),
    )
    session.add(review)
    _audit_log(session, "POST_TRADE_REVIEW_COMPLETE", trade.paper_trade_id,
                detail=f"result={result} R={r} tags={mistake_tags}")

    # Update mistake patterns
    _update_mistake_patterns(session, trade, review)

    # Update paper account
    account = session.scalar(
        select(PaperAccount).where(PaperAccount.paper_account_id == trade.paper_account_id).limit(1)
    )
    if account is not None:
        pnl_points = trade.realized_pnl_points or 0.0
        # Convert points to PnL (1 point = $1 for simplicity in paper mode)
        pnl_dollars = pnl_points
        account.realized_pnl += pnl_dollars
        account.current_equity += pnl_dollars
        account.max_equity = max(account.max_equity, account.current_equity)
        drawdown = account.max_equity - account.current_equity
        account.max_drawdown = max(account.max_drawdown, drawdown)
        if result == "WIN":
            account.win_count += 1
        elif result == "LOSS":
            account.loss_count += 1
        else:
            account.breakeven_count += 1

    return review


def _update_mistake_patterns(session: Session, trade: PaperTrade, review: TradeReview) -> None:
    """Aggregate mistake patterns from the review.

    A single loss does NOT create a candidate rule. Only after repeated
    evidence (occurrence_count >= 3) may a pattern become REPEATING,
    and only after more evidence may it become ACTIONABLE_CANDIDATE.

    NEVER auto-promoted into production rules.
    """
    mistake_tags = []
    if review.mistake_tags_json:
        try:
            mistake_tags = json.loads(review.mistake_tags_json)
        except Exception:
            mistake_tags = []

    if not mistake_tags:
        return

    now = datetime.now(timezone.utc)
    r = review.r_multiple or 0.0

    for tag in mistake_tags:
        if tag == "UNKNOWN":
            continue

        # Build conditions JSON from the trade's snapshot
        conditions = {
            "direction": trade.direction,
            "setup_fingerprint": trade.setup_fingerprint,
        }
        conditions_json = json.dumps(conditions, default=str, sort_keys=True)

        # Look up existing pattern by mistake_type + conditions
        existing = session.scalar(
            select(StrategyMistakePattern)
            .where(StrategyMistakePattern.mistake_type == tag)
            .where(StrategyMistakePattern.conditions_json == conditions_json)
            .limit(1)
        )

        if existing is not None:
            existing.occurrence_count += 1
            existing.last_seen = now
            if review.result == "LOSS":
                existing.loss_count += 1
            elif review.result == "WIN":
                existing.win_count += 1
            # Update avg_r (simple running average)
            n = existing.occurrence_count
            existing.avg_r = round(((existing.avg_r or 0.0) * (n - 1) + r) / n, 2) if n > 0 else r
            # Update status based on occurrence count
            if existing.occurrence_count >= 3 and existing.status == "OBSERVED":
                existing.status = "REPEATING"
                _audit_log(session, "MISTAKE_PATTERN_UPDATED", trade.paper_trade_id,
                            detail=f"pattern {existing.pattern_id} -> REPEATING (count={existing.occurrence_count})")
        else:
            # Create new pattern
            pattern = StrategyMistakePattern(
                pattern_id=f"MP-{uuid.uuid4().hex[:8].upper()}",
                mistake_type=tag,
                conditions_json=conditions_json,
                first_seen=now,
                last_seen=now,
                occurrence_count=1,
                loss_count=1 if review.result == "LOSS" else 0,
                win_count=1 if review.result == "WIN" else 0,
                avg_r=r,
                median_mfe=trade.mfe,
                median_mae=trade.mae,
                status="OBSERVED",
            )
            session.add(pattern)

    # Check if any REPEATING patterns should become ACTIONABLE_CANDIDATE
    # (requires >= 5 occurrences AND >= 3 losses — no single-trade overfitting)
    repeating_patterns = list(
        session.execute(
            select(StrategyMistakePattern).where(StrategyMistakePattern.status == "REPEATING")
        ).scalars()
    )
    for pattern in repeating_patterns:
        if pattern.occurrence_count >= 5 and pattern.loss_count >= 3:
            pattern.status = "UNDER_REVIEW"
            _audit_log(session, "MISTAKE_PATTERN_UPDATED", trade.paper_trade_id,
                        detail=f"pattern {pattern.pattern_id} -> UNDER_REVIEW (count={pattern.occurrence_count}, losses={pattern.loss_count})")
            # Create a candidate rule (EXPERIMENTAL — NOT auto-promoted)
            _create_candidate_rule(session, pattern, trade)


def _create_candidate_rule(session: Session, pattern: StrategyMistakePattern, source_trade: PaperTrade) -> None:
    """Create a candidate strategy rule from a repeating mistake pattern.

    Status = EXPERIMENTAL. NEVER auto-promoted into production BUY/SELL engine.
    Actual production integration happens in a later gated phase.
    """
    # Check if a candidate already exists for this pattern
    existing = session.scalar(
        select(CandidateStrategyRule)
        .where(CandidateStrategyRule.source_pattern == pattern.pattern_id)
        .limit(1)
    )
    if existing is not None:
        return

    now = datetime.now(timezone.utc)
    description = f"Avoid {pattern.mistake_type} when conditions match (observed {pattern.occurrence_count} times, {pattern.loss_count} losses, avg R={pattern.avg_r})"
    trigger_conditions = json.loads(pattern.conditions_json)

    candidate = CandidateStrategyRule(
        candidate_id=f"CR-{uuid.uuid4().hex[:8].upper()}",
        created_at=now,
        description=description,
        trigger_conditions_json=json.dumps(trigger_conditions, default=str),
        proposed_change_json=None,  # filled in during candidate testing
        source_pattern=pattern.pattern_id,
        source_trade_count=pattern.occurrence_count,
        historical_sample=pattern.occurrence_count,
        forward_sample=0,  # prospective validation starts from here
        baseline_metrics_json=json.dumps({
            "occurrences": pattern.occurrence_count,
            "losses": pattern.loss_count,
            "wins": pattern.win_count,
            "avg_r": pattern.avg_r,
        }),
        candidate_metrics_json=None,  # filled after candidate testing
        status="EXPERIMENTAL",
    )
    session.add(candidate)
    _audit_log(session, "CANDIDATE_RULE_CREATED", source_trade.paper_trade_id,
                detail=f"candidate {candidate.candidate_id} from pattern {pattern.pattern_id} (EXPERIMENTAL, NOT auto-promoted)")


# ---------- background loop ----------

async def paper_trader_loop(stop_event: asyncio.Event) -> None:
    """Background task: evaluate paper trades every 60 seconds.

    Checks waiting entries, active SL/TP lifecycle, MFE/MAE, completes
    terminal trades, launches post-trade review.
    """
    interval = 60
    last_run = 0.0
    while not stop_event.is_set():
        if last_run >= interval:
            try:
                try:
                    await refresh_quote_once()
                except Exception:
                    pass
                summary = await evaluate_paper_trades()
                if summary.get("evaluated", 0) > 0 and summary.get("reviews_completed", 0) > 0:
                    log.info(
                        "paper_trader — evaluated=%d entries=%d tps=%d stopped=%d be=%d reviews=%d",
                        summary.get("evaluated", 0),
                        summary.get("entries_touched", 0),
                        summary.get("tps_reached", 0),
                        summary.get("stopped", 0),
                        summary.get("breakeven", 0),
                        summary.get("reviews_completed", 0),
                    )
            except Exception as exc:
                log.warning("paper_trader evaluation failed: %s", exc)
            last_run = 0.0
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1)
        except asyncio.TimeoutError:
            last_run += 1


# ---------- API helpers ----------

def get_paper_trader_status() -> dict:
    """Return the paper trader status summary."""
    with SessionLocal() as session:
        account = get_or_create_paper_account(session)
        # Active paper trades
        active_count = session.scalar(
            select(sa_func.count(PaperTrade.id))
            .where(PaperTrade.status.in_(list(LIVE_STATES)))
        ) or 0
        # Completed trades
        completed_count = session.scalar(
            select(sa_func.count(PaperTrade.id))
            .where(PaperTrade.status.in_(list(TERMINAL_STATES) + ("BREAKEVEN",))
        )) or 0
        # Latest review
        latest_review = session.scalar(
            select(TradeReview).order_by(TradeReview.created_at.desc()).limit(1)
        )
        # Latest evaluation timestamp
        latest_audit = session.scalar(
            select(PaperTraderAuditLog)
            .where(PaperTraderAuditLog.event_type == "PAPER_TRADE_CREATED")
            .order_by(PaperTraderAuditLog.event_at.desc())
            .limit(1)
        )
        return {
            "paper_account_id": account.paper_account_id,
            "starting_equity": account.starting_equity,
            "current_equity": round(account.current_equity, 2),
            "realized_pnl": round(account.realized_pnl, 2),
            "unrealized_pnl": round(account.unrealized_pnl, 2),
            "max_equity": round(account.max_equity, 2),
            "max_drawdown": round(account.max_drawdown, 2),
            "paper_trade_count": account.paper_trade_count,
            "win_count": account.win_count,
            "loss_count": account.loss_count,
            "breakeven_count": account.breakeven_count,
            "active_paper_trades": active_count,
            "completed_trades": completed_count,
            "last_paper_evaluation": latest_audit.event_at.isoformat() if latest_audit and latest_audit.event_at else None,
            "last_post_trade_review": latest_review.created_at.isoformat() if latest_review and latest_review.created_at else None,
            "paper_trader_status": "ACTIVE" if active_count > 0 else "WAITING_FOR_ACTIONABLE_SETUP",
            "version": PAPER_TRADE_VERSION,
            "probability_calibrated": False,
        }


def list_paper_trades(limit: int = 20) -> dict:
    """List recent paper trades (newest first)."""
    with SessionLocal() as session:
        trades = list(
            session.execute(
                select(PaperTrade).order_by(PaperTrade.created_at.desc()).limit(limit)
            ).scalars()
        )
        return {
            "trades": [_paper_trade_to_dict(t) for t in trades],
            "count": len(trades),
        }


def get_paper_trade(paper_trade_id: str) -> dict:
    """Get one paper trade + its review (if any)."""
    with SessionLocal() as session:
        trade = session.scalar(
            select(PaperTrade).where(PaperTrade.paper_trade_id == paper_trade_id).limit(1)
        )
        if trade is None:
            return {"trade": None, "reason": f"paper trade {paper_trade_id} not found"}
        review = session.scalar(
            select(TradeReview).where(TradeReview.paper_trade_id == paper_trade_id).limit(1)
        )
        result = {"trade": _paper_trade_to_dict(trade)}
        if review is not None:
            result["review"] = _review_to_dict(review)
        # Also include the original plan snapshot
        if trade.market_snapshot_json:
            try:
                result["market_snapshot"] = json.loads(trade.market_snapshot_json)
            except Exception:
                result["market_snapshot"] = None
        return result


def get_paper_trader_performance() -> dict:
    """Aggregate paper trader performance metrics."""
    with SessionLocal() as session:
        account = get_or_create_paper_account(session)
        all_trades = list(session.execute(select(PaperTrade)).scalars())
        total = len(all_trades)
        wins = sum(1 for t in all_trades if t.status in ("TP1_REACHED", "TP2_REACHED", "TP3_REACHED", "MAX_REACHED") and (t.realized_r_multiple or 0) > 0)
        losses = sum(1 for t in all_trades if t.status == "STOPPED")
        breakevens = sum(1 for t in all_trades if t.status in ("BREAKEVEN", "EXPIRED"))
        # R-multiple stats
        r_values = [t.realized_r_multiple for t in all_trades if t.realized_r_multiple is not None]
        avg_r = sum(r_values) / len(r_values) if r_values else 0.0
        # MFE/MAE
        mfe_values = [t.mfe for t in all_trades if t.mfe is not None]
        mae_values = [t.mae for t in all_trades if t.mae is not None]
        avg_mfe = sum(mfe_values) / len(mfe_values) if mfe_values else 0.0
        avg_mae = sum(mae_values) / len(mae_values) if mae_values else 0.0
        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "breakevens": breakevens,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0.0,
            "average_r": round(avg_r, 2),
            "avg_mfe": round(avg_mfe, 2),
            "avg_mae": round(avg_mae, 2),
            "current_equity": round(account.current_equity, 2),
            "max_drawdown": round(account.max_drawdown, 2),
            "sample_quality": _sample_quality_label(total),
            "probability_calibrated": False,
        }


def list_mistake_patterns() -> dict:
    """List repeating mistake patterns."""
    with SessionLocal() as session:
        patterns = list(
            session.execute(
                select(StrategyMistakePattern)
                .order_by(StrategyMistakePattern.occurrence_count.desc())
                .limit(50)
            ).scalars()
        )
        return {
            "patterns": [
                {
                    "pattern_id": p.pattern_id,
                    "mistake_type": p.mistake_type,
                    "occurrence_count": p.occurrence_count,
                    "loss_count": p.loss_count,
                    "win_count": p.win_count,
                    "avg_r": p.avg_r,
                    "median_mfe": p.median_mfe,
                    "median_mae": p.median_mae,
                    "status": p.status,
                    "sample_quality": _sample_quality_label(p.occurrence_count),
                    "first_seen": p.first_seen.isoformat() if p.first_seen else None,
                    "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                }
                for p in patterns
            ],
            "count": len(patterns),
        }


def list_candidate_rules() -> dict:
    """List candidate strategy rules (EXPERIMENTAL — NOT auto-promoted)."""
    with SessionLocal() as session:
        candidates = list(
            session.execute(
                select(CandidateStrategyRule)
                .order_by(CandidateStrategyRule.created_at.desc())
                .limit(50)
            ).scalars()
        )
        return {
            "candidates": [
                {
                    "candidate_id": c.candidate_id,
                    "description": c.description,
                    "source_pattern": c.source_pattern,
                    "source_trade_count": c.source_trade_count,
                    "historical_sample": c.historical_sample,
                    "forward_sample": c.forward_sample,
                    "status": c.status,
                    "sample_quality": _sample_quality_label(c.source_trade_count),
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
                for c in candidates
            ],
            "count": len(candidates),
            "note": "Candidates are EXPERIMENTAL. NEVER auto-promoted into production BUY/SELL engine.",
        }


def _paper_trade_to_dict(t: PaperTrade) -> dict:
    return {
        "paper_trade_id": t.paper_trade_id,
        "trade_plan_id": t.trade_plan_id,
        "instrument": t.instrument,
        "direction": t.direction,
        "entry_low": t.entry_low,
        "entry_high": t.entry_high,
        "preferred_entry": t.preferred_entry,
        "actual_paper_entry": t.actual_paper_entry,
        "stop_loss": t.stop_loss,
        "tp1": t.tp1, "tp2": t.tp2, "tp3": t.tp3,
        "max_objective": t.max_objective,
        "status": t.status,
        "entry_timestamp": t.entry_timestamp.isoformat() if t.entry_timestamp else None,
        "exit_timestamp": t.exit_timestamp.isoformat() if t.exit_timestamp else None,
        "exit_reason": t.exit_reason,
        "realized_pnl_points": t.realized_pnl_points,
        "realized_r_multiple": t.realized_r_multiple,
        "mfe": t.mfe,
        "mae": t.mae,
        "time_to_entry": t.time_to_entry,
        "time_to_tp1": t.time_to_tp1,
        "time_to_tp2": t.time_to_tp2,
        "time_to_tp3": t.time_to_tp3,
        "time_to_max": t.time_to_max,
        "time_to_stop": t.time_to_stop,
        "breakeven_activated_at": t.breakeven_activated_at.isoformat() if t.breakeven_activated_at else None,
        "breakeven_price": t.breakeven_price,
        "setup_fingerprint": t.setup_fingerprint,
        "strategy_version": t.strategy_version,
        "plan_engine_version": t.plan_engine_version,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _review_to_dict(r: TradeReview) -> dict:
    def _parse_json(s):
        if s:
            try:
                return json.loads(s)
            except Exception:
                return None
        return None
    return {
        "review_id": r.review_id,
        "paper_trade_id": r.paper_trade_id,
        "result": r.result,
        "r_multiple": r.r_multiple,
        "what_worked": _parse_json(r.what_worked_json),
        "what_failed": _parse_json(r.what_failed_json),
        "mistake_tags": _parse_json(r.mistake_tags_json),
        "market_context": _parse_json(r.market_context_json),
        "primary_failure_reason": r.primary_failure_reason,
        "secondary_failure_reasons": _parse_json(r.secondary_failure_reasons),
        "review_version": r.review_version,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
