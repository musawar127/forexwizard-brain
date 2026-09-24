"""Phase 5.5: widen invalid_reason columns to varchar(255)

Revision ID: 7f4a8e1c2b39
Revises: 4e729d826d67
Create Date: 2026-09-24 20:25:00.000000

Background
----------
Three tables stored `invalid_reason` as VARCHAR(64):

    historical_outcomes.invalid_reason
    forward_observations.invalid_reason
    forward_outcomes.invalid_reason

On SQLite, VARCHAR(N) is a hint — longer strings were silently accepted.
On PostgreSQL, VARCHAR(N) is strict — longer strings raise
StringDataRightTruncation.

The roll_detector produces a diagnostic string like:
    "actual elapsed 17394.4h exceeds expected 0.2h by >2.0x — likely
     weekend/holiday/maintenance closure"
which is ~95 chars. Phase 5.5 historical bootstrap hit this when the
very first H1 state had a forward window that spanned a weekend
(yielding ~17400h actual vs 0.2h expected).

Fix: widen all three `invalid_reason` columns to VARCHAR(255). The
sister column `exclusion_reason` (in historical_outcomes) is already
VARCHAR(255) — this brings the same headroom to invalid_reason.

This migration is safe to run on production:
  * ALTER COLUMN ... TYPE VARCHAR(255) on Postgres is metadata-only
    when widening a VARCHAR to a larger VARCHAR (no table rewrite).
  * No data is transformed.
  * Forward-compatible: future diagnostic strings up to 255 chars work
    on both SQLite and Postgres.
"""
from alembic import op
import sqlalchemy as sa


# Alembic revision identifiers
revision = '7f4a8e1c2b39'
down_revision = '4e729d826d67'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Widen three invalid_reason columns from varchar(64) to varchar(255).

    SQLite does not support `ALTER COLUMN TYPE` and silently accepts any
    length already (VARCHAR(N) is just a hint). So on SQLite this
    migration is a no-op — the model definition change alone is enough.

    On PostgreSQL, ALTER COLUMN ... TYPE VARCHAR(255) is metadata-only
    when widening a varchar (no table rewrite, no data rewrite).
    """
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    if dialect_name != "postgresql":
        # SQLite, other lenient dialects: skip — column length isn't enforced
        # and the model-level change (String(255)) is sufficient.
        return
    # historical_outcomes.invalid_reason
    op.alter_column(
        'historical_outcomes',
        'invalid_reason',
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    # forward_observations.invalid_reason
    op.alter_column(
        'forward_observations',
        'invalid_reason',
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
    # forward_outcomes.invalid_reason
    op.alter_column(
        'forward_outcomes',
        'invalid_reason',
        existing_type=sa.String(length=64),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Shrink invalid_reason back to varchar(64).

    WARNING: any rows with invalid_reason longer than 64 chars will be
    truncated or fail to migrate. Only run downgrade if you have verified
    no row would be affected.
    """
    for tbl in ('historical_outcomes', 'forward_observations', 'forward_outcomes'):
        op.alter_column(
            tbl,
            'invalid_reason',
            existing_type=sa.String(length=255),
            type_=sa.String(length=64),
            existing_nullable=True,
        )
