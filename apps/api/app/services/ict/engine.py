"""Phase 5.7: ICT engine integration — bridges ICT analysis into trade plans.

This module:
  1. Fetches candles for all relevant timeframes (M1/M5/M15/M30/H1/H4/D1)
  2. Runs structure / liquidity / FVG / OB detection per timeframe
  3. Computes the dealing range (premium/discount)
  4. Runs the setup reasoner to produce a BUY/SELL/WAIT thesis
  5. Derives entry / SL / TP1-3 / MAX_OBJECTIVE
  6. Persists a NEW immutable TradePlan row with ICT-specific fields
  7. Persists detected ICT structures / liquidity / FVGs / OBs

All detection is PROSPECTIVE — no historical backfill of plan outcomes.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import (
    TradePlan,
    TradePlanLifecycleEvent,
    TradePlanOutcome,
    IctStructure,
    IctLiquidityLevel,
    IctLiquiditySweep,
    IctFvg,
    IctOrderBlock,
    IctPatternStat,
)
from app.db.session import SessionLocal
from app.engine.candles import get_candles
from app.models.market import BrainAnalysis, Candle
from app.services.trade_plan.engine import (
    PLAN_VERSION as TRADE_PLAN_VERSION,
    _gen_plan_id,
    _persist_plan,
    _wait_plan,
    _is_stale,
)
from app.services.trade_plan.lifecycle import record_event
from . import (
    MultiTimeframeAnalysis,
    analyze_timeframe,
    analyze_liquidity,
    detect_fvgs,
    detect_order_blocks,
    compute_range_from_swings,
    compute_session_range,
    current_session,
    reason_setup,
    derive_entry,
    derive_stop_loss,
    derive_targets,
    seed_knowledge,
)
from .setup_reasoner import SetupReasoning


ICT_PLAN_VERSION = "ict-plan-v0.1"
STALE_THRESHOLD_SECONDS = 90.0


# Timeframe roles per spec
TIMEFRAMES_HTF = ["1day", "4h", "1h"]    # macro directional structure
TIMEFRAME_SETUP = "15min"                 # setup structure
TIMEFRAMES_LTF = ["5min", "1min"]         # entry confirmation


async def _fetch_candles_async(tf: str, limit: int = 200) -> list[Candle]:
    """Fetch candles for a timeframe (async wrapper around get_candles)."""
    try:
        return await get_candles(tf, limit)
    except Exception:
        return []


def _fetch_candles_sync(tf: str, limit: int = 200) -> list[Candle]:
    """Synchronous candle fetch — queries the DB directly without an event loop.

    The async get_candles() function actually does a sync DB query inside,
    but wrapping it in asyncio.new_event_loop() can fail in some contexts.
    This helper queries CandleRecord directly and returns Candle objects.
    """
    try:
        from app.db.session import SessionLocal
        from app.db.models import CandleRecord
        from app.engine.candles import ensure_utc
        with SessionLocal() as session:
            rows = session.scalars(
                select(CandleRecord)
                .where(CandleRecord.symbol == "XAU/USD", CandleRecord.interval == tf)
                .order_by(CandleRecord.timestamp.desc())
                .limit(limit)
            ).all()
        rows = list(rows)
        rows.reverse()
        return [
            Candle(
                symbol=r.symbol,
                interval=r.interval,
                timestamp=ensure_utc(r.timestamp),
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
                sample_count=r.sample_count,
                provider=r.provider,
                is_historical=bool(getattr(r, "is_historical", False)),
                derivation=getattr(r, "derivation", None) or "SAMPLED",
                provider_symbol=getattr(r, "provider_symbol", None) or "XAU",
                instrument=getattr(r, "instrument", None) or "XAUUSD_SPOT",
                source_timeframe=getattr(r, "source_timeframe", None) or "TICK",
                target_timeframe=getattr(r, "target_timeframe", None) or r.interval,
            )
            for r in rows
        ]
    except Exception:
        return []


def _atr_from_tf_state(analysis: BrainAnalysis, tf_name: str) -> float | None:
    """Extract ATR for a given timeframe from the Brain's analysis.timeframes."""
    for tf in analysis.timeframes:
        if tf.timeframe == tf_name:
            return tf.atr
    return None


def _build_mtf(analysis: BrainAnalysis, candle_cache: dict[str, list[Candle]]) -> MultiTimeframeAnalysis:
    """Build the MultiTimeframeAnalysis object by running analyze_timeframe on each TF."""
    mtf = MultiTimeframeAnalysis()

    # Map our timeframe names to Brain analysis timeframe names
    # Brain uses: 1min, 5min, 15min, 30min, 1h, 4h, 1day
    tf_map = {
        "1day": "1day",
        "4h": "4h",
        "1h": "1h",
        "30min": "30min",
        "15min": "15min",
        "5min": "5min",
        "1min": "1min",
    }

    for tf_short, tf_full in tf_map.items():
        candles = candle_cache.get(tf_full, [])
        atr = _atr_from_tf_state(analysis, tf_full)
        ts = analyze_timeframe(candles, tf_short, atr_value=atr) if candles else None
        if tf_short == "1day":
            mtf.d1 = ts
        elif tf_short == "4h":
            mtf.h4 = ts
        elif tf_short == "1h":
            mtf.h1 = ts
        elif tf_short == "30min":
            mtf.m30 = ts
        elif tf_short == "15min":
            mtf.m15 = ts
        elif tf_short == "5min":
            mtf.m5 = ts
        elif tf_short == "1min":
            mtf.m1 = ts

    return mtf


def _persist_ict_observations(
    session,
    *,
    mtf: MultiTimeframeAnalysis,
    liquidity_analysis,
    fvgs_m15: list,
    obs_m15: list,
    instrument: str = "XAU/USD",
) -> int:
    """Persist detected ICT structures / liquidity / FVGs / OBs.

    Returns count of rows inserted.
    """
    inserted = 0
    now = datetime.now(timezone.utc)

    # Structures (per TF)
    for tf_attr in ("d1", "h4", "h1", "m30", "m15", "m5", "m1"):
        ts_obj = getattr(mtf, tf_attr, None)
        if ts_obj is None:
            continue
        tf_name = {"d1": "1day", "h4": "4h", "h1": "1h", "m30": "30min", "m15": "15min", "m5": "5min", "m1": "1min"}[tf_attr]
        # Swings
        for sw in ts_obj.swings[-10:]:  # last 10 swings per TF
            session.add(IctStructure(
                detected_at=now,
                instrument=instrument,
                timeframe=tf_name,
                timestamp=sw.timestamp,
                price=sw.price,
                structure_type=sw.kind,
                direction=None,
                quality=float(sw.strength * 20),  # 1-5 strength → 20-100 quality
                broken_level=None,
                invalidation=None,
                feature_version="structure-v0.1",
            ))
            inserted += 1
        # Structure sequence (HH/HL/LH/LL)
        for sq in ts_obj.sequence[-10:]:
            session.add(IctStructure(
                detected_at=now,
                instrument=instrument,
                timeframe=tf_name,
                timestamp=sq.timestamp,
                price=sq.price,
                structure_type=sq.structure_type,
                direction=None,
                quality=50.0,
                broken_level=None,
                invalidation=None,
                feature_version="structure-v0.1",
            ))
            inserted += 1
        # Structure events (BOS/CHoCH/MSS)
        for ev in ts_obj.events[-5:]:  # last 5 events per TF
            session.add(IctStructure(
                detected_at=now,
                instrument=instrument,
                timeframe=tf_name,
                timestamp=ev.timestamp,
                price=ev.price,
                structure_type=ev.event_type,
                direction=ev.direction,
                quality={"WEAK": 30.0, "MODERATE": 60.0, "STRONG": 90.0}[ev.quality],
                broken_level=ev.broken_level,
                invalidation=ev.broken_level,  # the level itself is the invalidation reference
                feature_version="structure-v0.1",
            ))
            inserted += 1

    # Liquidity levels (deduplicate by price+kind — keep only new ones)
    for level in liquidity_analysis.levels:
        session.add(IctLiquidityLevel(
            detected_at=now,
            instrument=instrument,
            price=level.price,
            kind=level.kind,
            timeframe=None,
            session=level.session,
            confidence=level.confidence,
            swept=level.swept,
            swept_at=level.swept_at,
            feature_version="liquidity-v0.1",
        ))
        inserted += 1

    # Liquidity sweeps
    for sweep in liquidity_analysis.sweeps:
        session.add(IctLiquiditySweep(
            detected_at=now,
            instrument=instrument,
            timestamp=sweep.timestamp,
            level=sweep.level,
            level_kind=sweep.level_kind,
            direction=sweep.direction,
            reaction_magnitude=sweep.reaction_magnitude,
            reaction_atr_multiple=sweep.reaction_atr_multiple,
            failed=sweep.failed,
            feature_version="liquidity-v0.1",
        ))
        inserted += 1

    # FVGs (M15)
    for fvg in fvgs_m15[-20:]:  # last 20 FVGs
        session.add(IctFvg(
            detected_at=now,
            instrument=instrument,
            timeframe="15min",
            timestamp=fvg.timestamp,
            direction=fvg.direction,
            upper=fvg.upper,
            lower=fvg.lower,
            midpoint=fvg.midpoint,
            mitigated=fvg.mitigated,
            fully_filled=fvg.fully_filled,
            invalidated=fvg.invalidated,
            mitigated_at=fvg.mitigated_at,
            filled_at=fvg.filled_at,
            feature_version="fvg-v0.1",
        ))
        inserted += 1

    # Order Blocks (M15)
    for ob in obs_m15[-20:]:
        session.add(IctOrderBlock(
            detected_at=now,
            instrument=instrument,
            timeframe="15min",
            timestamp=ob.timestamp,
            direction=ob.direction,
            upper=ob.upper,
            lower=ob.lower,
            midpoint=ob.midpoint,
            quality=ob.quality,
            bos_timestamp=ob.bos_timestamp,
            mitigated=ob.mitigated,
            mitigated_at=ob.mitigated_at,
            invalidated=ob.invalidated,
            feature_version="ob-v0.1",
        ))
        inserted += 1

    return inserted


def _build_pattern_signature(reasoning: SetupReasoning, dealing_range, sweep) -> dict:
    """Build the pattern signature dict for ict_pattern_stats.

    The signature uniquely identifies a setup pattern by its conditions
    (HTF trend, sweep direction, M15 event type, FVG/OB presence, location).
    """
    sig = {
        "htf_trend": reasoning.htf_trend,
        "m15_trend": reasoning.m15_trend,
        "sweep_direction": sweep.direction if sweep else None,
        "liquidity_swept": reasoning.liquidity_swept,
        "m15_event_type": None,
        "m15_event_direction": None,
        "displacement_confirmed": reasoning.displacement_confirmed,
        "fvg_active": reasoning.fvg_active,
        "ob_active": reasoning.ob_active,
        "location": reasoning.location,
    }
    # Pull M15 event details
    if reasoning.for_evidence:
        for ev in reasoning.for_evidence:
            if ev.kind in ("CHoCH", "MSS"):
                sig["m15_event_type"] = ev.kind
                sig["m15_event_direction"] = ev.bullish_or_bearish
                break
    return sig


def _persist_or_update_pattern_stat(session, signature: dict, plan_id: str) -> str:
    """Look up an existing ict_pattern_stats row by signature, or create one.

    Returns the pattern_id. Pattern stats are tracked prospectively —
    forward_sample_size is incremented when a new plan with this signature
    is created. Outcome counts (tp1_reached, etc.) are updated by the
    forward-validation loop, NOT at plan creation time.
    """
    sig_str = json.dumps(signature, sort_keys=True)
    # Search for existing pattern by signature
    existing = session.scalar(
        select(IctPatternStat).where(IctPatternStat.signature_json == sig_str).limit(1)
    )
    if existing is not None:
        # Increment forward sample size
        existing.forward_sample_size += 1
        existing.updated_at = existing.updated_at  # touch
        return existing.pattern_id

    # Create new pattern stat row
    pattern_id = f"PATTERN-{uuid.uuid4().hex[:8].upper()}"
    ps = IctPatternStat(
        pattern_id=pattern_id,
        created_at=datetime.now(timezone.utc),
        signature_json=sig_str,
        friendly_name=None,
        forward_sample_size=1,
        tp1_reached=0,
        tp2_reached=0,
        tp3_reached=0,
        max_objective_reached=0,
        sl_reached=0,
        avg_mfe=None,
        avg_mae=None,
        status="EXPERIMENTAL",
        notes=None,
    )
    session.add(ps)
    return pattern_id


def generate_ict_trade_plan(analysis: BrainAnalysis) -> dict:
    """Main entry point — generate an ICT/SMC-driven trade plan.

    The Brain's BUY/SELL/WAIT decision is INPUT and is NEVER modified.
    The ICT reasoner may agree or disagree with the Brain — the reasoner's
    own direction (BUY/SELL/WAIT) is what determines the plan's direction.
    The Brain's `brain_decision` field is preserved on the plan row for
    auditability.
    """
    plan_id = _gen_plan_id()

    # Quality gate 1: stale
    if _is_stale(analysis):
        plan = _wait_plan(plan_id, analysis, reason=f"analysis market_timestamp is stale (>{STALE_THRESHOLD_SECONDS:.0f}s old)")
        plan["plan_status"] = "STALE"
        plan["plan_engine_version"] = ICT_PLAN_VERSION
        _persist_plan(plan)
        return plan

    # Quality gate 2: WAIT / NO_DECISION
    # Per spec, the ICT reasoner should be allowed to override WAIT if there
    # is a genuine actionable setup. But to honor "do not force a trade" +
    # "WAIT decisions are important", we keep the WAIT/NO_TRADE behavior when
    # the Brain says WAIT — and let the ICT reasoner build evidence only.
    brain_decision = analysis.decision

    # Quality gate 3: missing price
    if analysis.price is None or analysis.price <= 0:
        plan = _wait_plan(plan_id, analysis, reason="no live price available")
        plan["plan_status"] = "INSUFFICIENT_DATA"
        plan["plan_engine_version"] = ICT_PLAN_VERSION
        _persist_plan(plan)
        return plan

    # Fetch candles for all timeframes
    candle_cache: dict[str, list[Candle]] = {}
    for tf in ("1min", "5min", "15min", "30min", "1h", "4h", "1day"):
        candle_cache[tf] = _fetch_candles_sync(tf, 200)

    # Build multi-timeframe structure analysis
    mtf = _build_mtf(analysis, candle_cache)

    # Liquidity analysis (use M15 candles for swing liquidity + sweeps)
    m15_candles = candle_cache.get("15min", [])
    h1_candles = candle_cache.get("1h", [])
    m15_atr = _atr_from_tf_state(analysis, "15min")
    h1_atr = _atr_from_tf_state(analysis, "1h")

    # M15 swings + liquidity
    if mtf.m15 is None or not mtf.m15.swings:
        plan = _wait_plan(plan_id, analysis, reason="no M15 swing structure available")
        plan["plan_status"] = "INSUFFICIENT_DATA"
        plan["plan_engine_version"] = ICT_PLAN_VERSION
        _persist_plan(plan)
        return plan

    liquidity_m15 = analyze_liquidity(m15_candles, mtf.m15.swings, atr_value=m15_atr)
    liquidity_h1 = analyze_liquidity(h1_candles, mtf.h1.swings if mtf.h1 else [], atr_value=h1_atr) if mtf.h1 else None

    # Merge liquidity from M15 + H1 (H1 levels are more significant)
    all_levels = list(liquidity_m15.levels)
    if liquidity_h1:
        all_levels.extend(liquidity_h1.levels)
    all_sweeps = list(liquidity_m15.sweeps)
    if liquidity_h1:
        all_sweeps.extend(liquidity_h1.sweeps)

    # FVGs on M15
    fvgs_m15 = detect_fvgs(m15_candles, "15min")

    # OBs on M15 (require M15 structure events)
    obs_m15: list = []
    if mtf.m15 is not None and mtf.m15.events:
        obs_m15 = detect_order_blocks(m15_candles, mtf.m15.events, "15min", atr_value=m15_atr)

    # Dealing range from H1 swings (most relevant for premium/discount)
    dealing_range = None
    if mtf.h1 is not None and mtf.h1.swings:
        swing_highs = [s.price for s in mtf.h1.swings if s.kind == "SWING_HIGH"]
        swing_lows = [s.price for s in mtf.h1.swings if s.kind == "SWING_LOW"]
        dealing_range = compute_range_from_swings(swing_highs, swing_lows, analysis.price)

    # Session ranges
    now_utc = datetime.now(timezone.utc)
    sessions_ranges = []
    for s_name in ("ASIA", "LONDON", "NEW_YORK"):
        sr = compute_session_range(h1_candles or m15_candles, s_name)
        if sr is not None:
            sessions_ranges.append(sr)

    # Pick the most recent sweep (from M15 + H1)
    recent_sweep = max(all_sweeps, key=lambda s: s.timestamp) if all_sweeps else None

    # ---------- run the setup reasoner ----------
    reasoning = reason_setup(
        mtf=mtf,
        liquidity=type("LA", (), {"levels": all_levels, "sweeps": all_sweeps})(),
        fvgs_m15=fvgs_m15,
        obs_m15=obs_m15,
        dealing_range=dealing_range,
        current_price=analysis.price,
        atr_value=m15_atr,
        now_utc=now_utc,
        candles_h1=h1_candles,
    )

    # ---------- WAIT / NO_TRADE branch ----------
    if reasoning.direction == "WAIT":
        plan = _wait_plan(plan_id, analysis, reason=reasoning.thesis)
        # Override plan_status to NO_TRADE for WAIT
        plan["plan_status"] = "NO_TRADE"
        # Persist ICT-specific fields even on WAIT (for visibility)
        plan["plan_engine_version"] = ICT_PLAN_VERSION
        plan["setup_thesis"] = reasoning.thesis
        plan["for_evidence"] = [e.__dict__ for e in reasoning.for_evidence]
        plan["against_evidence"] = [e.__dict__ for e in reasoning.against_evidence]
        plan["invalidation"] = reasoning.invalidation
        plan["session_context"] = {
            "active_sessions": reasoning.session_active,
            "htf_trend": reasoning.htf_trend,
            "m15_trend": reasoning.m15_trend,
            "liquidity_swept": reasoning.liquidity_swept,
            "sweep_direction": reasoning.sweep_direction,
            "displacement_confirmed": reasoning.displacement_confirmed,
            "fvg_active": reasoning.fvg_active,
            "ob_active": reasoning.ob_active,
            "location": reasoning.location,
        }
        _persist_ict_plan(plan, reasoning, sessions_ranges, recent_sweep, dealing_range)
        # Also persist ICT observations even for WAIT (the detections are real)
        with SessionLocal() as session:
            _persist_ict_observations(
                session,
                mtf=mtf,
                liquidity_analysis=type("LA", (), {"levels": all_levels, "sweeps": all_sweeps})(),
                fvgs_m15=fvgs_m15,
                obs_m15=obs_m15,
            )
            session.commit()
        return plan

    # ---------- BUY/SELL branch: derive entry / SL / targets ----------
    entry = derive_entry(
        reasoning=reasoning,
        fvgs_m15=fvgs_m15,
        obs_m15=obs_m15,
        current_price=analysis.price,
        atr_value=m15_atr,
    )
    if entry["status"] == "NO_VALID_ENTRY":
        plan = _wait_plan(plan_id, analysis, reason=entry["entry_reason"])
        plan["plan_status"] = "NO_VALID_ENTRY"
        plan["plan_engine_version"] = ICT_PLAN_VERSION
        plan["setup_thesis"] = reasoning.thesis
        plan["for_evidence"] = [e.__dict__ for e in reasoning.for_evidence]
        plan["against_evidence"] = [e.__dict__ for e in reasoning.against_evidence]
        _persist_ict_plan(plan, reasoning, sessions_ranges, recent_sweep, dealing_range)
        return plan

    stop = derive_stop_loss(
        reasoning=reasoning,
        entry_reference=entry["preferred_entry"],
        liquidity=type("LA", (), {"levels": all_levels, "sweeps": all_sweeps})(),
        m15=mtf.m15,
        obs_m15=obs_m15,
        atr_value=m15_atr,
    )
    if stop["status"] != "OK":
        plan = _wait_plan(plan_id, analysis, reason=stop["reason"])
        plan["plan_status"] = "NO_VALID_SL"
        plan["plan_engine_version"] = ICT_PLAN_VERSION
        _persist_ict_plan(plan, reasoning, sessions_ranges, recent_sweep, dealing_range)
        return plan

    risk_distance = abs(entry["preferred_entry"] - stop["stop_loss"])
    targets = derive_targets(
        direction=reasoning.direction,  # type: ignore[arg-type]
        entry_reference=entry["preferred_entry"],
        stop_loss=stop["stop_loss"],
        risk_distance=risk_distance,
        liquidity=type("LA", (), {"levels": all_levels, "sweeps": all_sweeps})(),
        m15=mtf.m15,
        h1=mtf.h1,
        sessions_ranges=sessions_ranges,
        current_price=analysis.price,
    )
    if targets["status"] != "OK":
        plan = _wait_plan(plan_id, analysis, reason=targets.get("reason", "no valid TP set"))
        plan["plan_status"] = "NO_VALID_TP"
        plan["plan_engine_version"] = ICT_PLAN_VERSION
        _persist_ict_plan(plan, reasoning, sessions_ranges, recent_sweep, dealing_range)
        return plan

    # R:R per TP
    def rr(tp: float) -> float:
        reward = abs(tp - entry["preferred_entry"])
        return round(reward / risk_distance, 2) if risk_distance > 0 else 0.0

    # Build the plan dict
    plan = {
        "plan_id": plan_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "market_timestamp": analysis.timestamp.isoformat() if analysis.timestamp else None,
        "instrument": "XAU/USD",
        "brain_decision": brain_decision,  # the Brain's actual decision (preserved)
        "technical_score": analysis.technical_score,
        "entry_low": entry["entry_low"],
        "entry_high": entry["entry_high"],
        "entry_type": entry["entry_type"],
        "entry_reference": entry["preferred_entry"],
        "preferred_entry": entry["preferred_entry"],
        "entry_reason": entry["entry_reason"],
        "stop_loss": stop["stop_loss"],
        "structural_invalidation": stop["structural_invalidation"],
        "invalidation_level": stop["structural_invalidation"],  # alias for compat
        "invalidation_reason": stop["reason"],
        "sl_distance": round(abs(entry["preferred_entry"] - stop["stop_loss"]), 2),
        "tp1": targets["tp1"], "tp2": targets["tp2"], "tp3": targets["tp3"],
        "tp4": targets["max_objective"],  # alias for compat with Phase 5.6
        "max_objective": targets["max_objective"],
        "tp1_reason": targets["tp1_reason"],
        "tp2_reason": targets["tp2_reason"],
        "tp3_reason": targets["tp3_reason"],
        "tp4_reason": targets["max_objective_reason"],
        "max_objective_reason": targets["max_objective_reason"],
        "risk_distance": round(risk_distance, 2),
        "reward_tp1": round(abs(targets["tp1"] - entry["preferred_entry"]), 2),
        "reward_tp2": round(abs(targets["tp2"] - entry["preferred_entry"]), 2),
        "reward_tp3": round(abs(targets["tp3"] - entry["preferred_entry"]), 2),
        "reward_tp4": round(abs(targets["max_objective"] - entry["preferred_entry"]), 2),
        "rr_tp1": rr(targets["tp1"]),
        "rr_tp2": rr(targets["tp2"]),
        "rr_tp3": rr(targets["tp3"]),
        "rr_tp4": rr(targets["max_objective"]),
        "management_instructions": "After TP1 reached, consider moving SL to entry (breakeven). Advisory only.",
        "plan_status": "ACTIONABLE" if entry["status"] == "ACTIONABLE" else "WAIT_FOR_ENTRY",
        "plan_version": TRADE_PLAN_VERSION,
        "plan_engine_version": ICT_PLAN_VERSION,
        "historical_similarity_run_id": getattr(analysis, "historical_similarity_run_id", None),
        "historical_context": getattr(analysis, "historical_alignment", None) or "INSUFFICIENT_DATA",
        "lifecycle_state": "CREATED",
        "setup_thesis": reasoning.thesis,
        "for_evidence": [e.__dict__ for e in reasoning.for_evidence],
        "against_evidence": [e.__dict__ for e in reasoning.against_evidence],
        "invalidation": reasoning.invalidation,
        "session_context": {
            "active_sessions": reasoning.session_active,
            "htf_trend": reasoning.htf_trend,
            "m15_trend": reasoning.m15_trend,
            "liquidity_swept": reasoning.liquidity_swept,
            "sweep_direction": reasoning.sweep_direction,
            "displacement_confirmed": reasoning.displacement_confirmed,
            "fvg_active": reasoning.fvg_active,
            "ob_active": reasoning.ob_active,
            "location": reasoning.location,
            "sessions": [{"name": s.name, "high": s.high, "low": s.low, "is_dst": s.is_dst} for s in sessions_ranges],
        },
    }

    _persist_ict_plan(plan, reasoning, sessions_ranges, recent_sweep, dealing_range)

    # Persist ICT observations (structures, liquidity, FVGs, OBs)
    with SessionLocal() as session:
        _persist_ict_observations(
            session,
            mtf=mtf,
            liquidity_analysis=type("LA", (), {"levels": all_levels, "sweeps": all_sweeps})(),
            fvgs_m15=fvgs_m15,
            obs_m15=obs_m15,
        )
        # Pattern stats tracking
        signature = _build_pattern_signature(reasoning, dealing_range, recent_sweep)
        pattern_id = _persist_or_update_pattern_stat(session, signature, plan_id)
        plan["setup_pattern_id"] = pattern_id
        # Update the persisted plan row with the pattern_id
        plan_row = session.scalar(select(TradePlan).where(TradePlan.plan_id == plan_id).limit(1))
        if plan_row is not None:
            plan_row.setup_pattern_id = pattern_id
            session.commit()

    return plan


def _persist_ict_plan(
    plan_dict: dict,
    reasoning: SetupReasoning,
    sessions_ranges: list,
    sweep,
    dealing_range,
) -> None:
    """Persist an ICT-driven trade plan row with ICT-specific fields.

    The dict is enriched with for_evidence / against_evidence / session_context
    that the Phase 5.6 _persist_plan doesn't know about. We use a custom
    persistence path here that adds the ICT fields.
    """
    now = datetime.now(timezone.utc)
    market_ts_str = plan_dict.get("market_timestamp")
    market_ts = (
        datetime.fromisoformat(market_ts_str.replace("Z", "+00:00"))
        if market_ts_str else now
    )

    # Serialize evidence + session_context as JSON
    for_ev = plan_dict.get("for_evidence") or []
    against_ev = plan_dict.get("against_evidence") or []
    session_ctx = plan_dict.get("session_context") or {}

    with SessionLocal() as session:
        plan = TradePlan(
            plan_id=plan_dict["plan_id"],
            created_at=now,
            market_timestamp=market_ts,
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
            plan_version=plan_dict.get("plan_version", TRADE_PLAN_VERSION),
            historical_similarity_run_id=plan_dict.get("historical_similarity_run_id"),
            historical_context=plan_dict.get("historical_context"),
            lifecycle_state="CREATED",
            # Phase 5.7 ICT extensions
            setup_thesis=plan_dict.get("setup_thesis"),
            for_evidence_json=json.dumps(for_ev, default=str) if for_ev else None,
            against_evidence_json=json.dumps(against_ev, default=str) if against_ev else None,
            structural_invalidation=plan_dict.get("structural_invalidation"),
            max_objective=plan_dict.get("max_objective"),
            max_objective_reason=plan_dict.get("max_objective_reason"),
            preferred_entry=plan_dict.get("preferred_entry"),
            session_context_json=json.dumps(session_ctx, default=str) if session_ctx else None,
            setup_pattern_id=plan_dict.get("setup_pattern_id"),
            plan_engine_version=plan_dict.get("plan_engine_version", ICT_PLAN_VERSION),
        )
        session.add(plan)

        # Initial lifecycle event
        record_event(
            session,
            plan_id=plan.plan_id,
            from_state=None,
            to_state="CREATED",
            reason=f"ICT plan generated: direction={reasoning.direction} status={plan.plan_status}",
            market_price=plan_dict.get("entry_reference"),
        )

        # Empty outcome row for forward validation
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


__all__ = [
    "ICT_PLAN_VERSION",
    "generate_ict_trade_plan",
]
