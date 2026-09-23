"""Phase 3: Historical backfill orchestrator.

Fetches genuine historical OHLC from the configured provider for the
lowest available timeframe, validates + dedupes, persists to the
existing CandleRecord table (with `is_historical=True`), derives higher
timeframes locally via `aggregate_candles()`, persists those too, and
updates HistoricalSyncState per (provider, symbol, interval).

This function NEVER silently repairs source data. If the provider
returns out-of-order candles or duplicates, the validator's report is
recorded in the HistoricalSyncState.last_error field — but the candles
themselves are not modified. Invalid candles are rejected; only valid
ones are persisted.

Per the user spec, sync is single-shot — there is no automatic resync
loop. The frontend POST /api/data/sync endpoint triggers a fresh run.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.dialects import sqlite as sqlite_dialect

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

# Timeframes the orchestrator will sync end-to-end. The lowest
# available source timeframe (M1) is fetched first; higher TFs are
# derived locally via aggregation. D1 is fetched directly because Yahoo's
# M1 depth (5 days) is too short to derive a useful D1 series.
SYNC_TIMEFRAMES = ["1min", "5min", "15min", "30min", "1h", "4h", "1day"]

# Yahoo's M1 only goes back 5 days, so deriving D1 from M1 yields ~5
# daily candles — not enough. We fetch D1 directly. The provider knows
# this and skips timeframes it cannot serve (e.g. Yahoo skips 4h).
DIRECT_FETCH_TIMEFRAMES = {"1day"}


async def _upsert_candles(
    session,
    candles: list[Candle],
    *,
    provider: str,
) -> tuple[int, int]:
    """Insert new historical candles, skip existing (dedupe by unique key).

    Returns (inserted, skipped).
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
                provider=provider,
                received_at=datetime.now(timezone.utc).replace(tzinfo=None),
                is_historical=True,
            )
        )
        inserted += 1
    return inserted, skipped


def _update_sync_state(
    session,
    *,
    provider: str,
    symbol: str,
    interval: str,
    report: CandleValidationReport,
    gap_report: GapReport,
    inserted: int,
    skipped: int,
    last_error: str | None,
    sync_status: str,
) -> HistoricalSyncState:
    """Insert or update the HistoricalSyncState row for this (p, s, i)."""
    state = session.scalar(
        select(HistoricalSyncState).where(
            HistoricalSyncState.provider == provider,
            HistoricalSyncState.symbol == symbol,
            HistoricalSyncState.interval == interval,
        )
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if state is None:
        state = HistoricalSyncState(
            provider=provider,
            symbol=symbol,
            interval=interval,
            earliest_timestamp=gap_report.first_timestamp.astimezone(timezone.utc).replace(tzinfo=None) if gap_report.first_timestamp else None,
            latest_timestamp=gap_report.last_timestamp.astimezone(timezone.utc).replace(tzinfo=None) if gap_report.last_timestamp else None,
            last_sync_at=now,
            total_candles=gap_report.actual_count,
            sync_status=sync_status,
            last_error=last_error,
        )
        session.add(state)
    else:
        state.earliest_timestamp = gap_report.first_timestamp.astimezone(timezone.utc).replace(tzinfo=None) if gap_report.first_timestamp else state.earliest_timestamp
        state.latest_timestamp = gap_report.last_timestamp.astimezone(timezone.utc).replace(tzinfo=None) if gap_report.last_timestamp else state.latest_timestamp
        state.last_sync_at = now
        state.total_candles = gap_report.actual_count
        state.sync_status = sync_status
        state.last_error = last_error
    return state


async def sync_historical_candles(
    symbol: str = "XAU/USD",
    *,
    timeframes: list[str] | None = None,
) -> dict:
    """Single-shot backfill. Returns a structured summary for /api/data/sync.

    Per (provider, symbol, interval), the summary contains:
      * candles_fetched  — count from provider
      * duplicates_in_batch
      * invalid_ohlc
      * out_of_order
      * zero_or_negative_price
      * gaps_detected
      * inserted
      * skipped_already_present
      * first_timestamp / last_timestamp
      * sync_status
      * last_error
    """
    tfs = list(timeframes) if timeframes else list(SYNC_TIMEFRAMES)
    provider = get_historical_provider()
    summary: dict = {
        "provider": provider.provider_name,
        "symbol": symbol,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "timeframes": {},
        "notes": [],
    }

    # Step 1: fetch + persist M1 (lowest source timeframe).
    source_tf = "1min"
    if source_tf in tfs:
        try:
            source_candles = await provider.get_history(symbol=symbol, timeframe=source_tf)
            summary["notes"].append(
                f"Fetched {len(source_candles)} {source_tf} candles from {provider.provider_name}."
            )
        except Exception as exc:
            err = f"{source_tf} fetch failed: {exc}"
            summary["notes"].append(err)
            summary["timeframes"][source_tf] = {
                "candles_fetched": 0, "duplicates_in_batch": 0,
                "invalid_ohlc": 0, "out_of_order": 0,
                "zero_or_negative_price": 0, "gaps_detected": 0,
                "inserted": 0, "skipped_already_present": 0,
                "first_timestamp": None, "last_timestamp": None,
                "sync_status": "error", "last_error": err,
            }
            source_candles = []
    else:
        source_candles = []

    # Validate the source batch once.
    report = validate_candles(source_candles)
    gap_report = find_gaps(report.valid_candles, source_tf)

    # Persist source candles.
    with SessionLocal() as session:
        inserted, skipped = await _upsert_candles(
            session, report.valid_candles, provider=provider.provider_name,
        )
        _update_sync_state(
            session,
            provider=provider.provider_name, symbol=symbol, interval=source_tf,
            report=report, gap_report=gap_report,
            inserted=inserted, skipped=skipped,
            last_error=(
                None
                if report.integrity_status == "OK"
                else f"validation issues: dup={report.duplicates} invalid={report.invalid_ohlc} ooo={report.out_of_order} zero_neg={report.zero_or_negative_price}"
            ),
            sync_status=("ok" if report.integrity_status == "OK" else "degraded"),
        )
        session.commit()

    summary["timeframes"][source_tf] = {
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
    }

    # Step 2: derive higher timeframes from M1 (deterministic local aggregation).
    # We do NOT re-fetch from the provider for these — only aggregate.
    # Yahoo has no native 4h, so H4 is always derived from H1 here.
    derived_chain = ["5min", "15min", "30min", "1h", "4h"]
    derived_chain = [tf for tf in derived_chain if tf in tfs]
    source_for_aggregation = report.valid_candles
    current_tf = source_tf
    for tf in derived_chain:
        if not _can_chain(current_tf, tf):
            continue
        agg = aggregate_candles(source_for_aggregation, tf, symbol=symbol, source_interval=current_tf)
        agg_report = validate_candles(agg)
        agg_gap_report = find_gaps(agg_report.valid_candles, tf)
        with SessionLocal() as session:
            inserted, skipped = await _upsert_candles(
                session, agg_report.valid_candles,
                provider=f"{provider.provider_name} (aggregated to {tf})",
            )
            _update_sync_state(
                session,
                provider=f"{provider.provider_name} (aggregated to {tf})",
                symbol=symbol, interval=tf,
                report=agg_report, gap_report=agg_gap_report,
                inserted=inserted, skipped=skipped,
                last_error=None,
                sync_status="ok",
            )
            session.commit()
        summary["timeframes"][tf] = {
            "candles_fetched": len(agg),
            "duplicates_in_batch": agg_report.duplicates,
            "invalid_ohlc": agg_report.invalid_ohlc,
            "out_of_order": agg_report.out_of_order,
            "zero_or_negative_price": agg_report.zero_or_negative_price,
            "gaps_detected": agg_gap_report.missing_periods,
            "inserted": inserted,
            "skipped_already_present": skipped,
            "first_timestamp": agg_gap_report.first_timestamp.isoformat() if agg_gap_report.first_timestamp else None,
            "last_timestamp": agg_gap_report.last_timestamp.isoformat() if agg_gap_report.last_timestamp else None,
            "sync_status": "ok",
            "last_error": None,
        }
        # Use the aggregated output as the source for the next level.
        # If aggregation produced no candles (e.g. M1 had < 5 candles),
        # stop deriving higher TFs — there's not enough source data.
        if not agg_report.valid_candles:
            break
        source_for_aggregation = agg_report.valid_candles
        current_tf = tf

    # Step 3: D1 direct fetch (Yahoo's M1 depth is only 5d, too short for D1 derivation).
    if "1day" in tfs:
        try:
            daily_candles = await provider.get_history(symbol=symbol, timeframe="1day")
        except Exception as exc:
            err = f"1day fetch failed: {exc}"
            summary["notes"].append(err)
            daily_candles = []
        daily_report = validate_candles(daily_candles)
        daily_gap_report = find_gaps(daily_report.valid_candles, "1day")
        with SessionLocal() as session:
            inserted, skipped = await _upsert_candles(
                session, daily_report.valid_candles, provider=provider.provider_name,
            )
            _update_sync_state(
                session,
                provider=provider.provider_name, symbol=symbol, interval="1day",
                report=daily_report, gap_report=daily_gap_report,
                inserted=inserted, skipped=skipped,
                last_error=(
                    None
                    if daily_report.integrity_status == "OK"
                    else f"validation issues: dup={daily_report.duplicates} invalid={daily_report.invalid_ohlc} ooo={daily_report.out_of_order} zero_neg={daily_report.zero_or_negative_price}"
                ),
                sync_status=("ok" if daily_report.integrity_status == "OK" else "degraded"),
            )
            session.commit()
        summary["timeframes"]["1day"] = {
            "candles_fetched": daily_report.total_input,
            "duplicates_in_batch": daily_report.duplicates,
            "invalid_ohlc": daily_report.invalid_ohlc,
            "out_of_order": daily_report.out_of_order,
            "zero_or_negative_price": daily_report.zero_or_negative_price,
            "gaps_detected": daily_gap_report.missing_periods,
            "inserted": inserted,
            "skipped_already_present": skipped,
            "first_timestamp": daily_gap_report.first_timestamp.isoformat() if daily_gap_report.first_timestamp else None,
            "last_timestamp": daily_gap_report.last_timestamp.isoformat() if daily_gap_report.last_timestamp else None,
            "sync_status": ("ok" if daily_report.integrity_status == "OK" else "degraded"),
            "last_error": (
                None if daily_report.integrity_status == "OK"
                else f"validation issues: dup={daily_report.duplicates} invalid={daily_report.invalid_ohlc} ooo={daily_report.out_of_order} zero_neg={daily_report.zero_or_negative_price}"
            ),
        }

    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def _can_chain(source_interval: str, target_interval: str) -> bool:
    """True if target_interval is a clean integer multiple of source_interval."""
    if source_interval not in INTERVALS or target_interval not in INTERVALS:
        return False
    src = INTERVALS[source_interval]
    tgt = INTERVALS[target_interval]
    return tgt > src and tgt % src == 0
