"""Phase 5.6: Trade Plan Engine — advisory XAU/USD trade plan generator.

The Trade Plan Engine sits DOWNSTREAM of the Brain's BUY/SELL/WAIT
decision (rules-v0.1, unchanged). It receives the Brain's decision and
either:
  - WAIT  -> produces plan_status=NO_TRADE (no invented entries/SL/TP)
  - BUY   -> derives entry zone, SL, TP1-4, R:R from genuine structure
  - SELL  -> same
  - NO_DECISION -> plan_status=NO_TRADE

Plans are IMMUTABLE once persisted. Lifecycle transitions and forward
validation outcomes are stored in separate tables — the original plan
row is never rewritten after market movement.

trade_plan_version = "trade-plan-v0.1"
"""
from __future__ import annotations

from .engine import (
    generate_trade_plan,
    get_current_plan,
    list_plans,
    get_plan,
    get_performance,
    calculate_position_size,
    PLAN_VERSION,
)
from .forward_validation import evaluate_live_plans, trade_plan_validation_loop
from .lifecycle import LIFECYCLE_STATES, LIVE_STATES, TERMINAL_STATES

__all__ = [
    "generate_trade_plan",
    "get_current_plan",
    "list_plans",
    "get_plan",
    "get_performance",
    "calculate_position_size",
    "evaluate_live_plans",
    "trade_plan_validation_loop",
    "PLAN_VERSION",
    "LIFECYCLE_STATES",
    "LIVE_STATES",
    "TERMINAL_STATES",
]
