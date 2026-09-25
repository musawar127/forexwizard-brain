"""Phase 5.7.1: trade plan dedup + quality hardening

Revision ID: d3a7b2e1f459
Revises: c2f6a1d9e348
Create Date: 2026-09-25 13:30:00.000000

Adds:
  - setup_fingerprint (VARCHAR(64), indexed, unique-per-active-plan)
    — SHA-256 hash of (decision + M15_completed_candle_ts + HTF_trend +
    M15_trend + latest_MSS/CHoCH + liquidity_sweep + FVG + OB +
    premium/discount_location). Used for dedup: if a plan with the same
    fingerprint already exists, generate returns it with
    reused_existing_plan=true instead of inserting a new row.

  - short_reason (VARCHAR(255))
    — concise reason label for the previous-plans table, e.g.
    "WAIT — no M15 MSS", "SELL — buy-side sweep + bearish MSS".
"""
from alembic import op
import sqlalchemy as sa


revision = 'd3a7b2e1f459'
down_revision = 'c2f6a1d9e348'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('trade_plans', sa.Column('setup_fingerprint', sa.String(length=64), nullable=True))
    op.add_column('trade_plans', sa.Column('short_reason', sa.String(length=255), nullable=True))
    op.create_index('ix_trade_plans_setup_fingerprint', 'trade_plans', ['setup_fingerprint'])


def downgrade() -> None:
    op.drop_index('ix_trade_plans_setup_fingerprint', table_name='trade_plans')
    op.drop_column('trade_plans', 'short_reason')
    op.drop_column('trade_plans', 'setup_fingerprint')
