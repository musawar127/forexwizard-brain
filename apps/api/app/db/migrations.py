"""Phase 3: Lightweight runtime column migration for SQLite.

SQLite supports `ALTER TABLE ... ADD COLUMN` but SQLAlchemy's
`Base.metadata.create_all` only creates missing tables — it does not
add columns to existing tables. This helper inspects the existing
schema and adds the Phase 3 columns (`is_historical` on CandleRecord;
new tables HistoricalSyncState + HistoricalFeatureSnapshot) at startup
without losing any sampled-candle history.
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
    #    HistoricalFeatureSnapshot) without touching existing ones.
    Base.metadata.create_all(engine)

    # 2. Add the is_historical column to CandleRecord if missing.
    try:
        inspector = inspect(engine)
        if "market_candles" in inspector.get_table_names():
            columns = {c["name"] for c in inspector.get_columns("market_candles")}
            if "is_historical" not in columns:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            "ALTER TABLE market_candles ADD COLUMN is_historical BOOLEAN DEFAULT 0 NOT NULL"
                        )
                    )
                log.info("migration: added is_historical column to market_candles")
    except Exception as exc:
        # Migrations are best-effort; never block startup.
        log.warning("migration: CandleRecord is_historical column check failed: %s", exc)
