"""Phase 4.2: Database session configuration with WAL mode + busy_timeout.

SQLite development mode: WAL (Write-Ahead Logging) journal mode allows
concurrent readers during writes — eliminates the read-blocking-during-build
contention that caused /api/learning/status to time out in Phase 4.1.

PostgreSQL production mode: natively supports concurrent reads + writes.
No special configuration needed.

Both modes use the same SQLAlchemy session interface — no business-logic
branching.
"""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_postgresql(url: str) -> bool:
    return url.startswith("postgresql")


engine = create_engine(settings.database_url, future=True, echo=False)

# Phase 4.2: SQLite optimizations for concurrent read/write during builds.
# WAL mode allows readers to see a consistent snapshot while writers append
# to the WAL file — no more read-blocking-during-write.
# busy_timeout = 5000ms: if a write lock is held, wait up to 5s before
# raising "database is locked" — gives the writer time to commit.
if _is_sqlite(settings.database_url):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        import sqlite3
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
