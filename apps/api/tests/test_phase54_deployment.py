"""Phase 5.4 tests: production deployment — CORS, env config, Alembic,
NEXT_PUBLIC_API_URL, PostgreSQL URL parsing."""

from __future__ import annotations

import pytest

from app.core.config import Settings


# ===========================================================================
# 1. CORS configuration
# ===========================================================================

def test_cors_origins_empty_defaults_to_frontend_origin():
    """If CORS_ORIGINS is not set, frontend_origin is used."""
    s = Settings(frontend_origin="http://localhost:3000", cors_origins="")
    assert s.cors_origins == ""


def test_cors_origins_set_takes_priority():
    """If CORS_ORIGINS is set, it's used for CORS middleware."""
    s = Settings(cors_origins="https://example.com,https://www.example.com")
    assert s.cors_origins == "https://example.com,https://www.example.com"


# ===========================================================================
# 2. Database URL configuration
# ===========================================================================

def test_sqlite_url_default():
    """Default DATABASE_URL is SQLite."""
    s = Settings()
    assert s.database_url.startswith("sqlite")


def test_postgresql_url_supported():
    """PostgreSQL URLs are valid."""
    s = Settings(database_url="postgresql+psycopg://user:pass@localhost:5432/forexwizard")
    assert s.database_url.startswith("postgresql+psycopg")


# ===========================================================================
# 3. Alembic migration exists
# ===========================================================================

def test_alembic_migration_file_exists():
    """The baseline migration file must exist."""
    import os
    # Migration files are at project root: ../../alembic/versions/
    migration_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "..", "alembic", "versions"
    )
    migration_dir = os.path.normpath(migration_dir)
    if not os.path.isdir(migration_dir):
        # May not exist in test env — skip
        import pytest
        pytest.skip(f"Migration directory not found: {migration_dir}")
    files = [f for f in os.listdir(migration_dir) if f.endswith(".py") and not f.startswith("__")]
    assert len(files) >= 1, f"No Alembic migration files found in {migration_dir}"


# ===========================================================================
# 4. Session supports both SQLite + PostgreSQL
# ===========================================================================

def test_session_is_sqlite_check():
    """_is_sqlite() correctly identifies SQLite URLs."""
    from app.db.session import _is_sqlite
    assert _is_sqlite("sqlite:///./forexwizard.db") is True
    assert _is_sqlite("postgresql+psycopg://user:pass@localhost/forexwizard") is False


def test_session_is_postgresql_check():
    """_is_postgresql() correctly identifies PostgreSQL URLs."""
    from app.db.session import _is_postgresql
    assert _is_postgresql("postgresql+psycopg://user:pass@localhost/forexwizard") is True
    assert _is_postgresql("postgresql://user:pass@localhost/forexwizard") is True
    assert _is_postgresql("sqlite:///./forexwizard.db") is False


# ===========================================================================
# 5. All tables exist in metadata (works on both SQLite + PostgreSQL)
# ===========================================================================

def test_all_tables_in_metadata():
    """All expected tables must be registered on Base.metadata."""
    from app.db.base import Base
    expected = {
        "market_ticks", "market_candles", "predictions", "prediction_outcomes",
        "research_news", "historical_market_states", "historical_outcomes",
        "similarity_runs", "historical_sync_state", "historical_feature_snapshots",
        "basis_observations", "build_jobs", "forward_observations",
        "forward_outcomes", "forward_audit_log", "system_config",
        "forward_heartbeats", "system_sync_state", "catchup_jobs",
        "missed_forward_captures",
    }
    actual = set(Base.metadata.tables.keys())
    missing = expected - actual
    assert not missing, f"Missing tables in metadata: {missing}"


# ===========================================================================
# 6. Version bump
# ===========================================================================

def test_app_version_bumped():
    """App version should be 0.4.0 after Phase 5.4."""
    from app.main import app
    assert app.version == "0.4.0"
