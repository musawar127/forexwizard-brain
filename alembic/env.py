"""Alembic environment for ForexWizard Brain.

Uses the application's SQLAlchemy engine and Base metadata for autogenerate.
The database URL is taken from the app settings (DATABASE_URL env var).
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add the app directory to sys.path so we can import app modules
# alembic/ is at project root, apps/api/ is two levels up
import pathlib
_here = pathlib.Path(__file__).resolve().parent  # alembic/
_project_root = _here.parent  # project root
_apps_api = _project_root / "apps" / "api"
sys.path.insert(0, str(_apps_api))

from app.core.config import settings
from app.db.base import Base

# Import all models so they are registered on Base.metadata
import app.db.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the URL from settings (supports both SQLite and PostgreSQL)
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
