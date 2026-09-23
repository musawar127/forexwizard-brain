"""Phase 3: Data-quality summary builder for /api/data/* endpoints.

Reads existing CandleRecord rows (historical + sampled) and
HistoricalSyncState rows, returns the structured payload the frontend
/data page renders.
"""

from __future__ import annotations

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


async def data_quality_summary(symbol: str = "XAU/USD") -> dict:
    """Build the top-level /api/data/status payload."""
    with SessionLocal() as session:
        # Overall historical candle counts per interval.
        rows = session.execute(
            select(
                CandleRecord.interval,
                CandleRecord.provider,
                func.count(CandleRecord.id),
                func.min(CandleRecord.timestamp),
                func.max(CandleRecord.timestamp),
            )
            .where(CandleRecord.symbol == symbol, CandleRecord.is_historical.is_(True))
            .group_by(CandleRecord.interval, CandleRecord.provider)
        ).all()

        # Sync states.
        sync_states = session.scalars(
            select(HistoricalSyncState).where(HistoricalSyncState.symbol == symbol)
        ).all()

        # Database health: total candle rows.
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

    # Build per-interval summary keyed by interval.
    by_interval: dict[str, dict] = {}
    for interval, provider, count, first, last in rows:
        d = by_interval.setdefault(interval, {
            "interval": interval,
            "providers": [],
            "candle_count": 0,
            "first_timestamp": None,
            "last_timestamp": None,
        })
        d["providers"].append(provider)
        d["candle_count"] += count
        # Track min/max across providers for this interval.
        if d["first_timestamp"] is None or (first is not None and first < d["first_timestamp"]):
            d["first_timestamp"] = first
        if d["last_timestamp"] is None or (last is not None and last > d["last_timestamp"]):
            d["last_timestamp"] = last

    # For each interval, compute gaps + duplicates + integrity from the
    # actually-persisted candle list (sorted ascending by timestamp).
    for interval in list(INTERVALS.keys()):
        candles = await get_candles(interval, 5000, symbol)
        historical_only = [c for c in candles if c.provider.startswith(("Yahoo", "Twelve")) or "aggregated" in c.provider]
        gap_report = find_gaps(historical_only, interval)
        d = by_interval.setdefault(interval, {
            "interval": interval,
            "providers": [],
            "candle_count": 0,
            "first_timestamp": None,
            "last_timestamp": None,
        })
        d["missing_intervals"] = gap_report.missing_periods
        d["expected_periods"] = gap_report.expected_periods
        d["completeness_pct"] = gap_report.completeness_pct
        d["duplicate_count"] = _count_db_duplicates(interval, symbol)
        d["integrity_status"] = "OK" if (d.get("missing_intervals", 0) == 0 and d.get("duplicate_count", 0) == 0) else "DEGRADED"

    # Find active provider (the one whose sync state has the most recent last_sync_at).
    active_provider = None
    if sync_states:
        latest = max(sync_states, key=lambda s: s.last_sync_at or datetime.min.replace(tzinfo=None))
        active_provider = latest.provider

    earliest = None
    latest = None
    total_candles = 0
    for d in by_interval.values():
        if d.get("first_timestamp") and (earliest is None or d["first_timestamp"] < earliest):
            earliest = d["first_timestamp"]
        if d.get("last_timestamp") and (latest is None or d["last_timestamp"] > latest):
            latest = d["last_timestamp"]
        total_candles += d.get("candle_count", 0)

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
        })

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
        "by_interval": list(by_interval.values()),
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
    """Per-interval breakdown for /api/data/timeframes."""
    out: list[dict] = []
    for interval in INTERVALS:
        with SessionLocal() as session:
            row = session.execute(
                select(
                    func.count(CandleRecord.id),
                    func.min(CandleRecord.timestamp),
                    func.max(CandleRecord.timestamp),
                ).where(
                    CandleRecord.symbol == symbol,
                    CandleRecord.interval == interval,
                    CandleRecord.is_historical.is_(True),
                )
            ).one()
            count, first, last = row
            duplicate_count = _count_db_duplicates(interval, symbol)
        out.append({
            "interval": interval,
            "candle_count": count or 0,
            "first_timestamp": _to_iso_naive(first),
            "last_timestamp": _to_iso_naive(last),
            "duplicate_count": duplicate_count,
            "integrity_status": "OK" if duplicate_count == 0 else "DEGRADED",
        })
    return out


def _count_db_duplicates(interval: str, symbol: str) -> int:
    """Count duplicate (symbol, interval, timestamp) groups in DB.

    Should always be 0 because the unique constraint prevents inserts;
    this is a defense-in-depth check.
    """
    with SessionLocal() as session:
        # SQLite-compatible count of groups with >1 row.
        from sqlalchemy import text
        result = session.execute(
            text(
                """
                SELECT COUNT(*) FROM (
                  SELECT symbol, interval, timestamp, COUNT(*) AS c
                  FROM market_candles
                  WHERE symbol = :sym AND interval = :iv AND is_historical = 1
                  GROUP BY symbol, interval, timestamp
                  HAVING COUNT(*) > 1
                )
                """
            ),
            {"sym": symbol, "iv": interval},
        )
        return int(result.scalar() or 0)
