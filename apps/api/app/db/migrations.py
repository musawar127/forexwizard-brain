"""Phase 3 + 3.1: Lightweight runtime column migration for SQLite.

SQLite supports `ALTER TABLE ... ADD COLUMN` but SQLAlchemy's
`Base.metadata.create_all` only creates missing tables — it does not
add columns to existing tables. This helper inspects the existing
schema and adds the Phase 3 + 3.1 columns at startup without losing
any sampled-candle history.

It also backfills lineage metadata (derivation, provider_symbol,
instrument, source_timeframe, target_timeframe) on existing rows so
they can be cleanly reported on the /data page after the upgrade.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.db.base import Base
from app.db.session import engine

log = logging.getLogger("forexwizard.migrations")


def run_startup_migrations() -> None:
    """Idempotent startup migrations. Safe to call on every boot."""
    # 1. create_all handles missing tables (HistoricalSyncState,
    #    HistoricalFeatureSnapshot, BasisObservation) without touching
    #    existing ones.
    Base.metadata.create_all(engine)

    # 2. Add Phase 3 + 3.1 columns to CandleRecord if missing.
    try:
        inspector = inspect(engine)
        if "market_candles" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("market_candles")}
            needed = [
                ("is_historical", "BOOLEAN DEFAULT 0 NOT NULL"),
                ("derivation", "VARCHAR(16) DEFAULT 'SAMPLED' NOT NULL"),
                ("provider_symbol", "VARCHAR(32) DEFAULT 'XAU' NOT NULL"),
                ("instrument", "VARCHAR(32) DEFAULT 'XAUUSD_SPOT' NOT NULL"),
                ("source_timeframe", "VARCHAR(16) DEFAULT 'TICK' NOT NULL"),
                ("target_timeframe", "VARCHAR(16)"),
            ]
            for col_name, col_type in needed:
                if col_name not in columns:
                    with engine.begin() as conn:
                        conn.execute(
                            text(f"ALTER TABLE market_candles ADD COLUMN {col_name} {col_type}")
                        )
                    log.info("migration: added %s column to market_candles", col_name)
    except Exception as exc:
        log.warning("migration: CandleRecord column check failed: %s", exc)

    # 3. Add Phase 3.1 lineage columns to HistoricalSyncState if missing.
    try:
        inspector = inspect(engine)
        if "historical_sync_state" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("historical_sync_state")}
            needed = [
                ("instrument", "VARCHAR(32) DEFAULT 'GC_FRONT_MONTH' NOT NULL"),
                ("provider_symbol", "VARCHAR(32) DEFAULT 'GC=F' NOT NULL"),
                ("derivation", "VARCHAR(16) DEFAULT 'DIRECT' NOT NULL"),
                ("source_timeframe", "VARCHAR(16) DEFAULT '' NOT NULL"),
                ("target_timeframe", "VARCHAR(16) DEFAULT '' NOT NULL"),
            ]
            for col_name, col_type in needed:
                if col_name not in columns:
                    with engine.begin() as conn:
                        conn.execute(
                            text(f"ALTER TABLE historical_sync_state ADD COLUMN {col_name} {col_type}")
                        )
                    log.info("migration: added %s column to historical_sync_state", col_name)
    except Exception as exc:
        log.warning("migration: HistoricalSyncState column check failed: %s", exc)

    # 4. Backfill lineage on existing rows (idempotent — only touches rows
    # whose derivation is NULL or unchanged from the SQL DEFAULT). We run
    # UPDATE statements that set the lineage based on the `provider` string.
    try:
        with engine.begin() as conn:
            # Sampled candles (Gold API spot ticks → candles)
            conn.execute(text(
                "UPDATE market_candles "
                "SET derivation='SAMPLED', provider_symbol='XAU', instrument='XAUUSD_SPOT', "
                "    source_timeframe='TICK', target_timeframe=interval "
                "WHERE provider='Local sampled Gold API' "
                "  AND (target_timeframe IS NULL OR target_timeframe = '')"
            ))
            # Direct-fetch historical candles (Yahoo native interval)
            conn.execute(text(
                "UPDATE market_candles "
                "SET derivation='DIRECT', provider_symbol='GC=F', instrument='GC_FRONT_MONTH', "
                "    source_timeframe=interval, target_timeframe=interval "
                "WHERE provider='Yahoo Finance (GC=F)' "
                "  AND (target_timeframe IS NULL OR target_timeframe = '')"
            ))
            # Direct-fetch historical candles (Twelve Data native interval)
            conn.execute(text(
                "UPDATE market_candles "
                "SET derivation='DIRECT', provider_symbol='XAU/USD', instrument='XAUUSD_SPOT', "
                "    source_timeframe=interval, target_timeframe=interval "
                "WHERE provider='Twelve Data (XAU/USD spot)' "
                "  AND (target_timeframe IS NULL OR target_timeframe = '')"
            ))
            # Aggregated candles — Phase 3 stored these with provider strings
            # like "Yahoo Finance (GC=F) (aggregated to 5min)". Extract the
            # target TF from the provider string with a simple LIKE pattern.
            for tf in ("1min", "5min", "15min", "30min", "1h", "4h", "1day"):
                conn.execute(text(
                    f"UPDATE market_candles "
                    f"SET derivation='AGGREGATED', provider_symbol='GC=F', instrument='GC_FRONT_MONTH', "
                    f"    source_timeframe=interval, target_timeframe='{tf}' "
                    f"WHERE provider LIKE '%aggregated to {tf}%' "
                    f"  AND (target_timeframe IS NULL OR target_timeframe = '')"
                ))
    except Exception as exc:
        log.warning("migration: lineage backfill failed (non-fatal): %s", exc)
