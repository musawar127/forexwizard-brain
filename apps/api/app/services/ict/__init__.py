"""Phase 5.7: ICT/SMC strategy reasoning brain.

This package is DOWNSTREAM of the Brain's BUY/SELL/WAIT decision. It
provides structured, machine-detectable representations of ICT/SMC
market structure concepts:

  - Market structure: HH/HL/LH/LL, BOS, CHoCH, MSS
  - Liquidity: equal highs/lows, PDH/PDL, PWH/PWL, session levels, sweeps
  - ICT/SMC: FVG, OB, breaker, mitigation, premium/discount, equilibrium, OTE
  - Sessions: Asia, London, New York with DST handling

The setup reasoner combines multi-timeframe structure analysis with
liquidity sweeps, FVG/OB detection, and premium/discount location to
build a deterministic BUY/SELL/WAIT thesis with explicit FOR/AGAINST
evidence and a structural invalidation level.

NO auto-trading. Plans are advisory only.
"""
from __future__ import annotations

from .fvg import FVG, detect_fvgs, active_fvgs, nearest_fvg_to_price
from .liquidity import (
    LiquidityAnalysis,
    LiquidityLevel,
    LiquiditySweep,
    analyze_liquidity,
)
from .order_blocks import (
    OrderBlock,
    detect_order_blocks,
    active_order_blocks,
    nearest_ob_to_price,
)
from .premium_discount import DealingRange, compute_dealing_range, compute_range_from_swings
from .sessions import (
    SESSION_NAMES,
    SessionWindow,
    SessionRange,
    get_session_window,
    current_session,
    compute_session_range,
)
from .structures import (
    Swing,
    StructureEvent,
    StructureSequence,
    TimeframeStructure,
    detect_swings,
    classify_structure,
    detect_structure_events,
    analyze_timeframe,
)
from .setup_reasoner import (
    SetupEvidence,
    SetupReasoning,
    MultiTimeframeAnalysis,
    htf_trend,
    reason_setup,
    derive_entry,
    derive_stop_loss,
    derive_targets,
)
from .knowledge import (
    DETECTION_RULE_VERSIONS,
    seed_knowledge,
    list_knowledge,
)
from .engine import (
    ICT_PLAN_VERSION,
    generate_ict_trade_plan,
)

__all__ = [
    # Structures
    "Swing", "StructureEvent", "StructureSequence", "TimeframeStructure",
    "detect_swings", "classify_structure", "detect_structure_events", "analyze_timeframe",
    # Liquidity
    "LiquidityAnalysis", "LiquidityLevel", "LiquiditySweep", "analyze_liquidity",
    # FVG
    "FVG", "detect_fvgs", "active_fvgs", "nearest_fvg_to_price",
    # Order Blocks
    "OrderBlock", "detect_order_blocks", "active_order_blocks", "nearest_ob_to_price",
    # Premium / Discount
    "DealingRange", "compute_dealing_range", "compute_range_from_swings",
    # Sessions
    "SESSION_NAMES", "SessionWindow", "SessionRange",
    "get_session_window", "current_session", "compute_session_range",
    # Reasoner
    "SetupEvidence", "SetupReasoning", "MultiTimeframeAnalysis", "htf_trend",
    "reason_setup", "derive_entry", "derive_stop_loss", "derive_targets",
    # Knowledge
    "DETECTION_RULE_VERSIONS", "seed_knowledge", "list_knowledge",
    # Engine integration
    "ICT_PLAN_VERSION", "generate_ict_trade_plan",
]
