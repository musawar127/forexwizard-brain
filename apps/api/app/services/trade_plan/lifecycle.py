"""Phase 5.6: plan lifecycle state machine + transitions.

States (a plan transitions through these in order):
  CREATED              - plan persisted, waiting to see if entry zone gets touched
  WAITING_FOR_ENTRY    - plan is live, entry zone not yet touched
  ENTRY_TOUCHED        - price reached entry zone (intermediate)
  ACTIVE               - position conceptually open (between entry and TP1/SL)
  TP1_REACHED          - first target hit (management: consider BE)
  TP2_REACHED / TP3_REACHED / TP4_REACHED  - subsequent targets
  BREAKEVEN            - SL moved to entry (advisory only)
  STOPPED              - SL hit before any TP (or after entry but before any TP)
  EXPIRED              - plan aged out without entry touch (default 24h)
  INVALIDATED          - market structure changed so the plan is no longer valid
                         (e.g. price broke through the invalidation level before
                         entry was touched, OR a newer plan supersedes this one)

State transitions are persisted as TradePlanLifecycleEvent rows. The
plan row's lifecycle_state field is updated to reflect the LATEST
state — but the original entry/SL/TP levels are NEVER rewritten.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TradePlan, TradePlanLifecycleEvent


# Allowed state values
LIFECYCLE_STATES: list[str] = [
    "CREATED",
    "WAITING_FOR_ENTRY",
    "ENTRY_TOUCHED",
    "ACTIVE",
    "TP1_REACHED",
    "TP2_REACHED",
    "TP3_REACHED",
    "TP4_REACHED",
    "BREAKEVEN",
    "STOPPED",
    "EXPIRED",
    "INVALIDATED",
]

# Terminal states (no further transitions)
TERMINAL_STATES: set[str] = {
    "TP4_REACHED",
    "STOPPED",
    "EXPIRED",
    "INVALIDATED",
}

# Live states (forward-validation loop should evaluate these)
LIVE_STATES: set[str] = {
    "CREATED",
    "WAITING_FOR_ENTRY",
    "ENTRY_TOUCHED",
    "ACTIVE",
    "TP1_REACHED",
    "TP2_REACHED",
    "TP3_REACHED",
    "BREAKEVEN",
}


def record_event(
    session: Session,
    plan_id: str,
    from_state: str | None,
    to_state: str,
    reason: str,
    market_price: float | None = None,
) -> None:
    """Append a lifecycle event. Does NOT commit — caller commits."""
    event = TradePlanLifecycleEvent(
        plan_id=plan_id,
        event_at=datetime.now(timezone.utc),
        from_state=from_state,
        to_state=to_state,
        reason=reason[:255],
        market_price=market_price,
    )
    session.add(event)


def transition(
    session: Session,
    plan: TradePlan,
    to_state: str,
    reason: str,
    market_price: float | None = None,
) -> None:
    """Transition a plan to a new lifecycle state.

    Persists a TradePlanLifecycleEvent AND updates plan.lifecycle_state.
    The plan's original entry/SL/TP levels are NEVER modified.

    Refuses to transition from a terminal state.
    """
    if plan.lifecycle_state in TERMINAL_STATES:
        return  # terminal — no further transitions allowed

    if plan.lifecycle_state == to_state:
        return  # no-op (already in target state)

    record_event(
        session,
        plan_id=plan.plan_id,
        from_state=plan.lifecycle_state,
        to_state=to_state,
        reason=reason,
        market_price=market_price,
    )
    plan.lifecycle_state = to_state


def list_live_plans(session: Session) -> list[TradePlan]:
    """Return all plans that are still in a non-terminal state."""
    result = session.execute(
        select(TradePlan).where(
            TradePlan.lifecycle_state.in_(list(LIVE_STATES))
        ).order_by(TradePlan.created_at.asc())
    )
    return list(result.scalars())


PLAN_EXPIRY_SECONDS: int = 24 * 3600  # 24h default


def is_plan_expired(plan: TradePlan, now: datetime | None = None) -> bool:
    """A plan is considered expired if it's older than PLAN_EXPIRY_SECONDS
    AND has not entered (entry_touched=False).
    """
    now = now or datetime.now(timezone.utc)
    if plan.created_at is None:
        return False
    # Make sure both timestamps are timezone-aware for comparison
    created = plan.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = (now - created).total_seconds()
    return age > PLAN_EXPIRY_SECONDS
