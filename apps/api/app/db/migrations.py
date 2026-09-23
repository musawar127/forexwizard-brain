"""Phase 3 + 3.1 + 3.2 + 4 + 4.1: Runtime column migration for SQLite.

SQLite supports `ALTER TABLE ... ADD COLUMN` but SQLAlchemy's
`Base.metadata.create_all` only creates missing tables — it does not
add columns to existing tables. This helper inspects the existing
schema and adds the columns at startup without losing any data.

It also backfills lineage metadata + handles Phase 4.1 outcome-schema
migration (max_up_move/max_down_move populated from old mfe/mae fields
where the historical direction was DOWN/UP respectively — best-effort
since Phase 4 didn't store direction context).
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.db.base import Base
from app.db.session import engine

log = logging.getLogger("forexwizard.migrations")


def run_startup_migrations() -> None:
    """Idempotent startup migrations. Safe to call on every boot."""
    # 1. create_all handles missing tables.
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

    # 4. Phase 4.1: add columns to HistoricalMarketState + HistoricalOutcome
    #    if missing (feature_version in the unique key, roll metadata, etc.)
    try:
        inspector = inspect(engine)
        if "historical_market_states" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("historical_market_states")}
            needed = [
                ("possible_contract_roll", "BOOLEAN DEFAULT 0 NOT NULL"),
                ("roll_gap_size", "FLOAT"),
                ("roll_detection_reason", "VARCHAR(255)"),
            ]
            for col_name, col_type in needed:
                if col_name not in columns:
                    with engine.begin() as conn:
                        conn.execute(
                            text(f"ALTER TABLE historical_market_states ADD COLUMN {col_name} {col_type}")
                        )
                    log.info("migration: added %s column to historical_market_states", col_name)
    except Exception as exc:
        log.warning("migration: HistoricalMarketState column check failed: %s", exc)

    try:
        inspector = inspect(engine)
        if "historical_outcomes" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("historical_outcomes")}
            needed = [
                ("max_up_move", "FLOAT"),
                ("max_down_move", "FLOAT"),
                ("horizon_valid", "BOOLEAN DEFAULT 1 NOT NULL"),
                ("actual_elapsed_seconds", "FLOAT"),
                ("invalid_reason", "VARCHAR(64)"),
                ("possible_contract_roll", "BOOLEAN DEFAULT 0 NOT NULL"),
                ("roll_gap_size", "FLOAT"),
                ("excluded_from_learning", "BOOLEAN DEFAULT 0 NOT NULL"),
                ("exclusion_reason", "VARCHAR(255)"),
            ]
            for col_name, col_type in needed:
                if col_name not in columns:
                    with engine.begin() as conn:
                        conn.execute(
                            text(f"ALTER TABLE historical_outcomes ADD COLUMN {col_name} {col_type}")
                        )
                    log.info("migration: added %s column to historical_outcomes", col_name)
            # Phase 4.1 best-effort backfill: if max_up_move is NULL but mfe
            # is populated (from Phase 4), copy mfe → max_up_move and mae →
            # max_down_move. Phase 4 stored direction-agnostic mfe/mae
            # (just max favorable / max adverse excursion in price terms),
            # which is exactly max_up_move / max_down_move respectively.
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        "UPDATE historical_outcomes SET max_up_move = mfe "
                        "WHERE max_up_move IS NULL AND mfe IS NOT NULL"
                    ))
                    conn.execute(text(
                        "UPDATE historical_outcomes SET max_down_move = mae "
                        "WHERE max_down_move IS NULL AND mae IS NOT NULL"
                    ))
                    # Mark all existing Phase 4 outcomes as horizon_valid=True
                    # (Phase 4 didn't track validity — assume valid by default
                    # for backward compat. New outcomes get explicit checks.)
                    conn.execute(text(
                        "UPDATE historical_outcomes SET horizon_valid = 1 "
                        "WHERE horizon_valid IS NULL OR horizon_valid = 0"
                    ))
            except Exception as exc:
                log.warning("migration: outcome backfill failed (non-fatal): %s", exc)
    except Exception as exc:
        log.warning("migration: HistoricalOutcome column check failed: %s", exc)

    # 5. Backfill lineage on existing CandleRecord rows (idempotent).
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE market_candles "
                "SET derivation='SAMPLED', provider_symbol='XAU', instrument='XAUUSD_SPOT', "
                "    source_timeframe='TICK', target_timeframe=interval "
                "WHERE provider='Local sampled Gold API' "
                "  AND (target_timeframe IS NULL OR target_timeframe = '')"
            ))
            conn.execute(text(
                "UPDATE market_candles "
                "SET derivation='DIRECT', provider_symbol='GC=F', instrument='GC_FRONT_MONTH', "
                "    source_timeframe=interval, target_timeframe=interval "
                "WHERE provider='Yahoo Finance (GC=F)' "
                "  AND (target_timeframe IS NULL OR target_timeframe = '')"
            ))
            conn.execute(text(
                "UPDATE market_candles "
                "SET derivation='DIRECT', provider_symbol='XAU/USD', instrument='XAUUSD_SPOT', "
                "    source_timeframe=interval, target_timeframe=interval "
                "WHERE provider='Twelve Data (XAU/USD spot)' "
                "  AND (target_timeframe IS NULL OR target_timeframe = '')"
            ))
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

    # 6. Phase 4.1: mark any in-flight BuildJobs as "interrupted" on startup.
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE build_jobs SET status='interrupted', "
                "updated_at=CURRENT_TIMESTAMP "
                "WHERE status IN ('queued', 'running')"
            ))
    except Exception as exc:
        log.warning("migration: BuildJob cleanup failed (non-fatal): %s", exc)

    # 7. Phase 4.3: add outcome resolution metadata columns to HistoricalOutcome.
    try:
        inspector = inspect(engine)
        if "historical_outcomes" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("historical_outcomes")}
            needed = [
                ("outcome_source_timeframe", "VARCHAR(16)"),
                ("outcome_source_provider", "VARCHAR(64)"),
                ("outcome_source_instrument", "VARCHAR(32)"),
                ("resolution_sufficient", "BOOLEAN"),
                ("outcome_version", "VARCHAR(16) DEFAULT 'outcomes-v0.1' NOT NULL"),
            ]
            for col_name, col_type in needed:
                if col_name not in columns:
                    with engine.begin() as conn:
                        conn.execute(
                            text(f"ALTER TABLE historical_outcomes ADD COLUMN {col_name} {col_type}")
                        )
                    log.info("migration: added %s column to historical_outcomes", col_name)
            # Backfill: existing outcomes used H1 for all horizons (outcomes-v0.1)
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE historical_outcomes SET outcome_version='outcomes-v0.1' "
                    "WHERE outcome_version IS NULL OR outcome_version='outcomes-v0.1'"
                ))
    except Exception as exc:
        log.warning("migration: HistoricalOutcome Phase 4.3 column check failed: %s", exc)

    # 8. Phase 4.3: add outcome_version to SimilarityRun.
    try:
        inspector = inspect(engine)
        if "similarity_runs" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("similarity_runs")}
            if "outcome_version" not in columns:
                with engine.begin() as conn:
                    conn.execute(text(
                        "ALTER TABLE similarity_runs ADD COLUMN outcome_version VARCHAR(16) DEFAULT 'outcomes-v0.1' NOT NULL"
                    ))
                log.info("migration: added outcome_version column to similarity_runs")
    except Exception as exc:
        log.warning("migration: SimilarityRun outcome_version check failed: %s", exc)

    # 9. Phase 4.3: add reconciliation fields to BuildJob.
    try:
        inspector = inspect(engine)
        if "build_jobs" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("build_jobs")}
            needed = [
                ("db_state_count", "INTEGER"),
                ("counter_state_count", "INTEGER"),
                ("counter_db_difference", "INTEGER"),
                ("reconciliation_warning", "TEXT"),
            ]
            for col_name, col_type in needed:
                if col_name not in columns:
                    with engine.begin() as conn:
                        conn.execute(
                            text(f"ALTER TABLE build_jobs ADD COLUMN {col_name} {col_type}")
                        )
                    log.info("migration: added %s column to build_jobs", col_name)
    except Exception as exc:
        log.warning("migration: BuildJob reconciliation column check failed: %s", exc)
