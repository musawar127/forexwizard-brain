"""Phase 5.7 fix: widen ict_strategy_knowledge.detection_rule_version column

Revision ID: c2f6a1d9e348
Revises: b1e5f9c8d237
Create Date: 2026-09-25 13:05:00.000000

The original Phase 5.7 migration created detection_rule_version as
VARCHAR(16), but the longest version string ("premium-discount-v0.1")
is 22 chars and "displacement-v0.1" is 17 chars. This caused the ICT
knowledge seed to fail with StringDataRightTruncation.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c2f6a1d9e348'
down_revision = 'b1e5f9c8d237'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Widen detection_rule_version from VARCHAR(16) to VARCHAR(32)
    op.alter_column(
        'ict_strategy_knowledge',
        'detection_rule_version',
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'ict_strategy_knowledge',
        'detection_rule_version',
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
