"""Phase 3.1: Historical backfill orchestrator.

Fetches genuine historical OHLC from the configured provider for the
DEEPEST NATIVE interval each TF supports — NOT by deriving short-window
TFs from shallow M1 history.

Native interval selection (Yahoo Finance as the active provider):
    M1:  native M1   (5d depth)
    M5:  native M5   (3mo depth)     ← was derived from M1 in Phase 3
    M15: native M15  (3mo depth)     ← was derived from M1 in Phase 3
    M30: native M30  (3mo depth)     ← was derived from M1 in Phase 3
    H1:  native H1   (2y depth)      ← was derived from M1 in Phase 3
    H4:  derived from H1  (Yahoo has no native 4h)
    D1:  native D1   (10y depth)

Each persisted candle carries full source lineage:
    provider         = "Yahoo Finance (GC=F)" (or "... (aggregated to 4h)")
    provider_symbol  = "GC=F"
    instrument       = "GC_FRONT_MONTH"
    derivation       = DIRECT | AGGREGATED
    source_timeframe = the TF the candle was sourced from (e.g. "1h" for an
                       H4 aggregated from H1; equals target_timeframe for DIRECT)
    target_timeframe = the candle's own interval

This function NEVER silently repairs source data. If the provider
returns out-of-order candles or duplicates, the validator's report is
recorded in the HistoricalSyncState.last_error field — but the candles
themselves are not modified. Invalid candles are rejected; only valid
ones are persisted.

Per the user spec, sync is single-shot — there is no automatic resync
loop. The frontend POST /api/data/sync endpoint triggers a fresh run.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import and_, select

from app.db.models import CandleRecord, HistoricalSyncState
from app.db.session import SessionLocal
from app.engine.candles import INTERVALS
from app.models.market import Candle
from app.services.historical.aggregator import aggregate_candles
from app.services.historical.factory import get_historical_provider
from app.services.historical.validator import (
    CandleValidationReport,
    GapReport,
    find_gaps,
    validate_candles,
)

# All canonical ForexWizard timeframes, in order from lowest to highest.
SYNC_TIMEFRAMES = ["1min", "5min", "15min", "30min", "1h", "4h", "1day"]

# Yahoo (and most providers) skip 4h natively. Phase 3.1 design rule:
# only H4 is derived — and it is derived from H1, not from M1.
DERIVED_FROM = {"4h": "1h"}


async def _upsert_candles(
    session,
    candles: list[Candle],
) -> tuple[int, int]:
    """Insert new historical candles, skip existing (dedup by unique key).

    All lineage metadata (derivation, provider_symbol, instrument,
    source_timeframe, target_timeframe) is copied from the Candle object
    to the CandleRecord row.
    """
    inserted = 0
    skipped = 0
    for c in candles:
        ts_naive = c.timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        existing = session.scalar(
            select(CandleRecord.id).where(
                and_(
                    CandleRecord.symbol == c.symbol,
                    CandleRecord.interval == c.interval,
                    CandleRecord.timestamp == ts_naive,
                )
            )
        )
        if existing is not None:
            skipped += 1
            continue
        session.add(
            CandleRecord(
                symbol=c.symbol,
                interval=c.interval,
                timestamp=ts_naive,
                open=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                sample_count=c.sample_count,
                provider=c.provider,
                received_at=datetime.now(timezone.utc).replace(tzinfo=None),
                is_historical=True,
                derivation=c.derivation,
                provider_symbol=c.provider_symbol,
                instrument=c.instrument,
                source_timeframe=c.source_timeframe,
                target_timeframe=c.target_timeframe or c.interval,
            )
        )
        inserted += 1
    return inserted, skipped


def _update_sync_state(
    session,
    *,
    candles: list[Candle],
    report: CandleValidationReport,
    gap_report: GapReport,
    inserted: int,
    skipped: int,
    last_error: str | None,
    sync_status: str,
) -> HistoricalSyncState:
    """Insert or update the HistoricalSyncState row for this (p, s, i).

    Phase 3.1: lineage metadata (instrument, provider_symbol, derivation,
    source_timeframe, target_timeframe) is propagated from the candles
    so the /data page can show full source provenance per row.
    """
    if candles:
        sample = candles[0]
        provider = sample.provider
        provider_symbol = sample.provider_symbol
        instrument = sample.instrument
        derivation = sample.derivation
        source_tf = sample.source_timeframe
        target_tf = sample.target_timeframe or sample.interval
    else:
        provider = "unknown"
        provider_symbol = ""
        instrument = "UNKNOWN"
        derivation = "DIRECT"
        source_tf = ""
        target_tf = ""

    state = session.scalar(
        select(HistoricalSyncState).where(
            HistoricalSyncState.provider == provider,
            HistoricalSyncState.symbol == "XAU/USD",
            HistoricalSyncState.interval == (candles[0].interval if candles else ""),
        )
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if state is None:
        state = HistoricalSyncState(
            provider=provider,
            symbol="XAU/USD",
            interval=candles[0].interval if candles else "",
            earliest_timestamp=gap_report.first_timestamp.astimezone(timezone.utc).replace(tzinfo=None) if gap_report.first_timestamp else None,
            latest_timestamp=gap_report.last_timestamp.astimezone(timezone.utc).replace(tzinfo=None) if gap_report.last_timestamp else None,
            last_sync_at=now,
            total_candles=gap_report.actual_count,
            sync_status=sync_status,
            last_error=last_error,
            instrument=instrument,
            provider_symbol=provider_symbol,
            derivation=derivation,
            source_timeframe=source_tf,
            target_timeframe=target_tf,
        )
        session.add(state)
    else:
        state.earliest_timestamp = gap_report.first_timestamp.astimezone(timezone.utc).replace(tzinfo=None) if gap_report.first_timestamp else state.earliest_timestamp
        state.latest_timestamp = gap_report.last_timestamp.astimezone(timezone.utc).replace(tzinfo=None) if gap_report.last_timestamp else state.latest_timestamp
        state.last_sync_at = now
        state.total_candles = gap_report.actual_count
        state.sync_status = sync_status
        state.last_error = last_error
        state.instrument = instrument
        state.provider_symbol = provider_symbol
        state.derivation = derivation
        state.source_timeframe = source_tf
        state.target_timeframe = target_tf
    return state


async def _sync_direct_tf(provider, symbol: str, tf: str) -> dict:
    """Fetch + persist a single native timeframe directly from the provider.

    All candles are tagged DIRECT with source_timeframe == target_timeframe == tf.
    """
    try:
        candles = await provider.get_history(symbol=symbol, timeframe=tf)
    except Exception as exc:
        err = f"{tf} fetch failed: {exc}"
        return {
            "candles_fetched": 0, "duplicates_in_batch": 0,
            "invalid_ohlc": 0, "out_of_order": 0,
            "zero_or_negative_price": 0, "gaps_detected": 0,
            "inserted": 0, "skipped_already_present": 0,
            "first_timestamp": None, "last_timestamp": None,
            "sync_status": "error", "last_error": err,
            "instrument": None, "provider_symbol": None,
            "derivation": "DIRECT", "source_timeframe": None,
            "target_timeframe": tf,
        }
    report = validate_candles(candles)
    gap_report = find_gaps(report.valid_candles, tf)
    with SessionLocal() as session:
        inserted, skipped = await _upsert_candles(session, report.valid_candles)
        _update_sync_state(
            session,
            candles=report.valid_candles,
            report=report, gap_report=gap_report,
            inserted=inserted, skipped=skipped,
            last_error=(
                None if report.integrity_status == "OK"
                else f"validation issues: dup={report.duplicates} invalid={report.invalid_ohlc} ooo={report.out_of_order} zero_neg={report.zero_or_negative_price}"
            ),
            sync_status=("ok" if report.integrity_status == "OK" else "degraded"),
        )
        session.commit()
    sample = report.valid_candles[0] if report.valid_candles else None
    return {
        "candles_fetched": report.total_input,
        "duplicates_in_batch": report.duplicates,
        "invalid_ohlc": report.invalid_ohlc,
        "out_of_order": report.out_of_order,
        "zero_or_negative_price": report.zero_or_negative_price,
        "gaps_detected": gap_report.missing_periods,
        "inserted": inserted,
        "skipped_already_present": skipped,
        "first_timestamp": gap_report.first_timestamp.isoformat() if gap_report.first_timestamp else None,
        "last_timestamp": gap_report.last_timestamp.isoformat() if gap_report.last_timestamp else None,
        "sync_status": ("ok" if report.integrity_status == "OK" else "degraded"),
        "last_error": (
            None if report.integrity_status == "OK"
            else f"validation issues: dup={report.duplicates} invalid={report.invalid_ohlc} ooo={report.out_of_order} zero_neg={report.zero_or_negative_price}"
        ),
        "instrument": sample.instrument if sample else None,
        "provider_symbol": sample.provider_symbol if sample else None,
        "derivation": "DIRECT",
        "source_timeframe": sample.source_timeframe if sample else None,
        "target_timeframe": tf,
    }


async def _sync_derived_h4(provider, symbol: str) -> dict:
    """Fetch H1 natively, aggregate to H4 locally, persist.

    Yahoo has no native 4h interval — H4 is the ONLY derived timeframe
    in the Phase 3.1 design. The aggregator already preserves lineage:
    source_timeframe="1h", target_timeframe="4h", derivation="AGGREGATED".
    """
    try:
        h1_candles = await provider.get_history(symbol=symbol, timeframe="1h")
    except Exception as exc:
        err = f"H4 source (1h) fetch failed: {exc}"
        return {
            "candles_fetched": 0, "duplicates_in_batch": 0,
            "invalid_ohlc": 0, "out_of_order": 0,
            "zero_or_negative_price": 0, "gaps_detected": 0,
            "inserted": 0, "skipped_already_present": 0,
            "first_timestamp": None, "last_timestamp": None,
            "sync_status": "error", "last_error": err,
            "instrument": None, "provider_symbol": None,
            "derivation": "AGGREGATED", "source_timeframe": "1h",
            "target_timeframe": "4h",
        }
    if not h1_candles:
        return {
            "candles_fetched": 0, "duplicates_in_batch": 0,
            "invalid_ohlc": 0, "out_of_order": 0,
            "zero_or_negative_price": 0, "gaps_detected": 0,
            "inserted": 0, "skipped_already_present": 0,
            "first_timestamp": None, "last_timestamp": None,
            "sync_status": "ok", "last_error": None,
            "instrument": "GC_FRONT_MONTH", "provider_symbol": "GC=F",
            "derivation": "AGGREGATED", "source_timeframe": "1h",
            "target_timeframe": "4h",
        }
    # Tag the H1 source candles with their full lineage (the provider already
    # set derivation=DIRECT etc., but we re-confirm here for safety).
    h1_candles = [
        c.model_copy(update={
            "is_historical": True,
            "derivation": "DIRECT",
            "provider_symbol": c.provider_symbol or "GC=F",
            "instrument": c.instrument or "GC_FRONT_MONTH",
            "source_timeframe": "1h",
            "target_timeframe": "1h",
        })
        for c in h1_candles
    ]
    # Aggregate to H4 — the aggregator preserves lineage via the
    # provider_label parameter; we explicitly set the derivation flag here.
    h4_raw = aggregate_candles(
        h1_candles, "4h", symbol=symbol, source_interval="1h",
        provider_label=f"{provider.provider_name} (aggregated to 4h)",
    )
    # Re-tag every H4 candle as AGGREGATED with full lineage.
    h4_candles = [
        c.model_copy(update={
            "is_historical": True,
            "derivation": "AGGREGATED",
            "provider_symbol": "GC=F",
            "instrument": "GC_FRONT_MONTH",
            "source_timeframe": "1h",
            "target_timeframe": "4h",
        })
        for c in h4_raw
    ]
    report = validate_candles(h4_candles)
    gap_report = find_gaps(report.valid_candles, "4h")
    with SessionLocal() as session:
        inserted, skipped = await _upsert_candles(session, report.valid_candles)
        _update_sync_state(
            session,
            candles=report.valid_candles,
            report=report, gap_report=gap_report,
            inserted=inserted, skipped=skipped,
            last_error=None,
            sync_status="ok",
        )
        session.commit()
    sample = report.valid_candles[0] if report.valid_candles else None
    return {
        "candles_fetched": len(h4_candles),
        "duplicates_in_batch": report.duplicates,
        "invalid_ohlc": report.invalid_ohlc,
        "out_of_order": report.out_of_order,
        "zero_or_negative_price": report.zero_or_negative_price,
        "gaps_detected": gap_report.missing_periods,
        "inserted": inserted,
        "skipped_already_present": skipped,
        "first_timestamp": gap_report.first_timestamp.isoformat() if gap_report.first_timestamp else None,
        "last_timestamp": gap_report.last_timestamp.isoformat() if gap_report.last_timestamp else None,
        "sync_status": ("ok" if report.integrity_status == "OK" else "degraded"),
        "last_error": (
            None if report.integrity_status == "OK"
            else f"validation issues: dup={report.duplicates} invalid={report.invalid_ohlc} ooo={report.out_of_order} zero_neg={report.zero_or_negative_price}"
        ),
        "instrument": sample.instrument if sample else "GC_FRONT_MONTH",
        "provider_symbol": sample.provider_symbol if sample else "GC=F",
        "derivation": "AGGREGATED",
        "source_timeframe": "1h",
        "target_timeframe": "4h",
    }


async def sync_historical_candles(
    symbol: str = "XAU/USD",
    *,
    timeframes: list[str] | None = None,
) -> dict:
    """Single-shot backfill. Returns a structured summary for /api/data/sync.

    Phase 3.1 design rule: each native TF is fetched DIRECTLY at its
    native depth (M5/M15/M30/H1 from Yahoo at 3mo/3mo/3mo/2y — NOT
    derived from the shallow 5-day M1 history). Only H4 is derived
    locally from H1 because Yahoo has no native 4h interval.
    """
    tfs = list(timeframes) if timeframes else list(SYNC_TIMEFRAMES)
    provider = get_historical_provider()
    summary: dict = {
        "provider": provider.provider_name,
        "symbol": symbol,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "timeframes": {},
        "notes": [
            "Phase 3.1 design: each native TF fetched directly at Yahoo's "
            "deepest native range (M5/M15/M30=3mo, H1=2y, D1=10y). Only H4 "
            "is derived locally from H1 because Yahoo has no native 4h.",
        ],
    }

    # 1. Fetch each NATIVE timeframe directly. H4 is skipped here and
    #    handled separately (derived from H1) below.
    for tf in tfs:
        if tf in DERIVED_FROM:
            continue
        summary["timeframes"][tf] = await _sync_direct_tf(provider, symbol, tf)

    # 2. Derive H4 from H1 (Yahoo has no native 4h).
    if "4h" in tfs:
        summary["timeframes"]["4h"] = await _sync_derived_h4(provider, symbol)

    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    return summary
