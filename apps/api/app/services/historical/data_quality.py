"""Phase 3.1: Data-quality summary builder for /api/data/* endpoints.

Reads existing CandleRecord rows (historical + sampled) and
HistoricalSyncState rows, returns the structured payload the frontend
/data page renders.

Phase 3.1 changes:
  * Group per-interval by instrument + derivation so the frontend can
    show DIRECT vs AGGREGATED lineage.
  * Expose provider_symbol, source_timeframe, target_timeframe, days_covered.
  * Provide historical_depth per TF for the Brain analysis endpoint.
  * Provide instrument_consistency classification per TF.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db.models import CandleRecord, HistoricalSyncState
from app.db.session import SessionLocal
from app.engine.candles import INTERVALS, get_candles
from app.services.historical.factory import list_available_providers
from app.services.historical.validator import find_gaps


def _to_iso_naive(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        # Treat naive datetimes as UTC for serialization consistency.
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).isoformat()
        return value.astimezone(timezone.utc).isoformat()
    return None


def _days_between(first, last) -> float | None:
    """Return (last - first) in days (float). Inputs are naive UTC datetimes."""
    if first is None or last is None:
        return None
    delta = last - first
    return round(delta.total_seconds() / 86400.0, 2)


def _instrument_consistency(candles: list) -> str:
    """Classify the instrument mix in a candle list.

    Returns:
      - "NONE"          no candles
      - "PURE_GC"        all candles are GC_FRONT_MONTH (Yahoo futures)
      - "PURE_SPOT"      all candles are XAUUSD_SPOT (Gold API spot)
      - "MIXED"         both instruments are present (DO NOT merge —
                         historical statistical learning must know which
                         instrument generated each observation)
    """
    if not candles:
        return "NONE"
    instruments = {getattr(c, "instrument", None) for c in candles}
    instruments.discard(None)
    if not instruments:
        return "NONE"
    if len(instruments) > 1:
        return "MIXED"
    only = next(iter(instruments))
    if only == "GC_FRONT_MONTH":
        return "PURE_GC"
    if only == "XAUUSD_SPOT":
        return "PURE_SPOT"
    return f"PURE_{only}"


def _historical_depth_for_tf(symbol: str, interval: str) -> float:
    """Days of genuine historical depth at this TF — pulled from
    HistoricalSyncState so it reflects the FULL DB range, not just
    the most recent candles. Returns 0 if no sync state exists."""
    from app.db.models import HistoricalSyncState
    from sqlalchemy import select as sa_select
    with SessionLocal() as session:
        rows = session.scalars(
            sa_select(HistoricalSyncState).where(
                HistoricalSyncState.symbol == symbol,
                HistoricalSyncState.interval == interval,
            )
        ).all()
        if not rows:
            return 0.0
        earliest = None
        latest = None
        for r in rows:
            if r.earliest_timestamp and (earliest is None or r.earliest_timestamp < earliest):
                earliest = r.earliest_timestamp
            if r.latest_timestamp and (latest is None or r.latest_timestamp > latest):
                latest = r.latest_timestamp
        if not earliest or not latest:
            return 0.0
        if earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=timezone.utc)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return round((latest - earliest).total_seconds() / 86400.0, 2)


async def data_quality_summary(symbol: str = "XAU/USD") -> dict:
    """Build the top-level /api/data/status payload."""
    with SessionLocal() as session:
        # Overall historical candle counts per (interval, instrument, derivation).
        rows = session.execute(
            select(
                CandleRecord.interval,
                CandleRecord.provider,
                CandleRecord.derivation,
                CandleRecord.instrument,
                CandleRecord.provider_symbol,
                CandleRecord.source_timeframe,
                CandleRecord.target_timeframe,
                func.count(CandleRecord.id),
                func.min(CandleRecord.timestamp),
                func.max(CandleRecord.timestamp),
            )
            .where(CandleRecord.symbol == symbol, CandleRecord.is_historical.is_(True))
            .group_by(
                CandleRecord.interval,
                CandleRecord.provider,
                CandleRecord.derivation,
                CandleRecord.instrument,
                CandleRecord.provider_symbol,
                CandleRecord.source_timeframe,
                CandleRecord.target_timeframe,
            )
        ).all()

        sync_states = session.scalars(
            select(HistoricalSyncState).where(HistoricalSyncState.symbol == symbol)
        ).all()

        total_candles_db = session.scalar(
            select(func.count(CandleRecord.id)).where(CandleRecord.symbol == symbol)
        ) or 0
        total_historical = session.scalar(
            select(func.count(CandleRecord.id)).where(
                CandleRecord.symbol == symbol,
                CandleRecord.is_historical.is_(True),
            )
        ) or 0

    providers_health = await list_available_providers()

    # Build per-(interval, instrument, derivation) summary.
    by_interval: dict[str, list[dict]] = defaultdict(list)
    earliest = None
    latest = None
    total_candles = 0
    for interval, provider, derivation, instrument, provider_symbol, source_tf, target_tf, count, first, last in rows:
        days_covered = _days_between(first, last)
        entry = {
            "interval": interval,
            "provider": provider,
            "derivation": derivation,
            "instrument": instrument,
            "provider_symbol": provider_symbol,
            "source_timeframe": source_tf,
            "target_timeframe": target_tf or interval,
            "candle_count": count,
            "first_timestamp": _to_iso_naive(first),
            "last_timestamp": _to_iso_naive(last),
            "days_covered": days_covered,
        }
        by_interval[interval].append(entry)
        if first and (earliest is None or first < earliest):
            earliest = first
        if last and (latest is None or last > latest):
            latest = last
        total_candles += count

    # For each interval, ALSO compute gaps + duplicates + integrity from
    # the actually-persisted candle list (grouped by derivation).
    interval_quality: dict[str, dict] = {}
    for interval in list(INTERVALS.keys()):
        candles = await get_candles(interval, 5000, symbol)
        historical_only = [c for c in candles if getattr(c, "is_historical", False)]
        gap_report = find_gaps(historical_only, interval)
        consistency = _instrument_consistency(historical_only)
        depth = _historical_depth_for_tf(symbol, interval)
        dup_count = _count_db_duplicates(interval, symbol)
        # Phase 3.2: classify gaps into EXPECTED_MARKET_CLOSURE,
        # EXPECTED_SESSION_BREAK, UNEXPECTED_GAP. Integrity is HEALTHY
        # when only expected closures exist; DEGRADED when unexpected
        # gaps exist; INVALID when invalid OHLC / out-of-order / dup
        # corruption exists.
        if dup_count > 0:
            integrity = "DEGRADED"
        else:
            integrity = gap_report.integrity_status  # HEALTHY or DEGRADED
        interval_quality[interval] = {
            "interval": interval,
            # Phase 3.2: classified gap counts (separate)
            "expected_gap_count": gap_report.expected_gap_count,
            "unexpected_gap_count": gap_report.unexpected_gap_count,
            "invalid_candle_count": _count_invalid_candles(symbol, interval),
            # Backward-compat aggregate (still useful as a total)
            "missing_intervals": gap_report.missing_periods,
            "expected_periods": gap_report.expected_periods,
            "completeness_pct": gap_report.completeness_pct,
            "duplicate_count": dup_count,
            "integrity_status": integrity,
            "instrument_consistency": consistency,
            "historical_depth_days": depth,
        }

    # Latest sync state per (provider, interval).
    sync_summary = []
    for s in sync_states:
        sync_summary.append({
            "provider": s.provider,
            "interval": s.interval,
            "earliest_timestamp": _to_iso_naive(s.earliest_timestamp),
            "latest_timestamp": _to_iso_naive(s.latest_timestamp),
            "last_sync_at": _to_iso_naive(s.last_sync_at),
            "total_candles": s.total_candles,
            "sync_status": s.sync_status,
            "last_error": s.last_error,
            "instrument": s.instrument,
            "provider_symbol": s.provider_symbol,
            "derivation": s.derivation,
            "source_timeframe": s.source_timeframe,
            "target_timeframe": s.target_timeframe,
        })

    active_provider = None
    if sync_states:
        latest_state = max(sync_states, key=lambda s: s.last_sync_at or datetime.min.replace(tzinfo=None))
        active_provider = latest_state.provider

    return {
        "symbol": symbol,
        "active_provider": active_provider,
        "providers": [
            {
                "provider_name": p.provider_name,
                "reachable": p.reachable,
                "requires_api_key": p.requires_api_key,
                "has_api_key": p.has_api_key,
                "last_error": p.last_error,
                "extra": p.extra,
            }
            for p in providers_health
        ],
        "historical": {
            "earliest_timestamp": _to_iso_naive(earliest),
            "latest_timestamp": _to_iso_naive(latest),
            "total_candles": total_candles,
        },
        "by_interval": by_interval,
        "interval_quality": interval_quality,
        "sync_states": sync_summary,
        "database_health": {
            "total_candle_rows": total_candles_db,
            "historical_candle_rows": total_historical,
            "storage_engine": "sqlite",
            "ok": total_candles_db >= 0,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def timeframe_breakdown(symbol: str = "XAU/USD") -> list[dict]:
    """Per-interval breakdown for /api/data/timeframes.

    Phase 3.1: each row now includes instrument, provider_symbol,
    derivation, source_timeframe, days_covered. The list is grouped by
    (interval, instrument, derivation) so DIRECT and AGGREGATED candles
    at the same TF appear as separate rows.
    """
    out: list[dict] = []
    with SessionLocal() as session:
        for interval in INTERVALS:
            rows = session.execute(
                select(
                    CandleRecord.provider,
                    CandleRecord.derivation,
                    CandleRecord.instrument,
                    CandleRecord.provider_symbol,
                    CandleRecord.source_timeframe,
                    CandleRecord.target_timeframe,
                    func.count(CandleRecord.id),
                    func.min(CandleRecord.timestamp),
                    func.max(CandleRecord.timestamp),
                ).where(
                    CandleRecord.symbol == symbol,
                    CandleRecord.interval == interval,
                    CandleRecord.is_historical.is_(True),
                ).group_by(
                    CandleRecord.provider,
                    CandleRecord.derivation,
                    CandleRecord.instrument,
                    CandleRecord.provider_symbol,
                    CandleRecord.source_timeframe,
                    CandleRecord.target_timeframe,
                )
            ).all()
            for provider, derivation, instrument, provider_symbol, source_tf, target_tf, count, first, last in rows:
                dup = _count_db_duplicates(interval, symbol)
                invalid = _count_invalid_candles(symbol, interval)
                # Phase 3.2: HEALTHY / DEGRADED / INVALID semantics.
                if invalid > 0:
                    integrity = "INVALID"
                elif dup > 0:
                    integrity = "DEGRADED"
                else:
                    integrity = "HEALTHY"
                out.append({
                    "interval": interval,
                    "provider": provider,
                    "derivation": derivation,
                    "instrument": instrument,
                    "provider_symbol": provider_symbol,
                    "source_timeframe": source_tf,
                    "target_timeframe": target_tf or interval,
                    "candle_count": count or 0,
                    "first_timestamp": _to_iso_naive(first),
                    "last_timestamp": _to_iso_naive(last),
                    "days_covered": _days_between(first, last),
                    "duplicate_count": dup,
                    "invalid_candle_count": invalid,
                    "integrity_status": integrity,
                })
    return out


def _count_invalid_candles(symbol: str, interval: str) -> int:
    """Phase 3.2: count candles with invalid OHLC persisted in the DB.

    A candle is "invalid" if:
      - any OHLC field is null
      - any OHLC field is zero or negative
      - high < low, or high < max(open, close), or low > min(open, close)
    The validator should have rejected these on insert, but this query
    is a defense-in-depth check for the /data page integrity badge.
    """
    with SessionLocal() as session:
        from sqlalchemy import text
        result = session.execute(
            text(
                """
                SELECT COUNT(*) FROM market_candles
                WHERE symbol = :sym AND interval = :iv AND is_historical = TRUE
                  AND (open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
                       OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
                       OR high < low
                       OR high < open OR high < close
                       OR low > open OR low > close)
                """
            ),
            {"sym": symbol, "iv": interval},
        )
        return int(result.scalar() or 0)


def _count_db_duplicates(interval: str, symbol: str) -> int:
    """Count duplicate (symbol, interval, timestamp) groups in DB.

    Should always be 0 because the unique constraint prevents inserts;
    this is a defense-in-depth check.
    """
    with SessionLocal() as session:
        from sqlalchemy import text
        result = session.execute(
            text(
                """
                SELECT COUNT(*) FROM (
                  SELECT symbol, interval, timestamp, COUNT(*) AS c
                  FROM market_candles
                  WHERE symbol = :sym AND interval = :iv AND is_historical = TRUE
                  GROUP BY symbol, interval, timestamp
                  HAVING COUNT(*) > 1
                )
                """
            ),
            {"sym": symbol, "iv": interval},
        )
        return int(result.scalar() or 0)
