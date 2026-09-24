"""Phase 5.6: trade plan engine schema

Revision ID: 9a3c1e7d8b41
Revises: 7f4a8e1c2b39
Create Date: 2026-09-24 22:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '9a3c1e7d8b41'
down_revision = '7f4a8e1c2b39'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'trade_plans',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('plan_id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('market_timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('instrument', sa.String(length=32), nullable=False),
        sa.Column('brain_decision', sa.String(length=16), nullable=False),
        sa.Column('technical_score', sa.Float(), nullable=True),
        sa.Column('entry_low', sa.Float(), nullable=True),
        sa.Column('entry_high', sa.Float(), nullable=True),
        sa.Column('entry_type', sa.String(length=32), nullable=True),
        sa.Column('entry_reference', sa.Float(), nullable=True),
        sa.Column('stop_loss', sa.Float(), nullable=True),
        sa.Column('invalidation_level', sa.Float(), nullable=True),
        sa.Column('invalidation_reason', sa.String(length=255), nullable=True),
        sa.Column('sl_distance', sa.Float(), nullable=True),
        sa.Column('tp1', sa.Float(), nullable=True),
        sa.Column('tp2', sa.Float(), nullable=True),
        sa.Column('tp3', sa.Float(), nullable=True),
        sa.Column('tp4', sa.Float(), nullable=True),
        sa.Column('tp1_reason', sa.String(length=255), nullable=True),
        sa.Column('tp2_reason', sa.String(length=255), nullable=True),
        sa.Column('tp3_reason', sa.String(length=255), nullable=True),
        sa.Column('tp4_reason', sa.String(length=255), nullable=True),
        sa.Column('risk_distance', sa.Float(), nullable=True),
        sa.Column('reward_tp1', sa.Float(), nullable=True),
        sa.Column('reward_tp2', sa.Float(), nullable=True),
        sa.Column('reward_tp3', sa.Float(), nullable=True),
        sa.Column('reward_tp4', sa.Float(), nullable=True),
        sa.Column('rr_tp1', sa.Float(), nullable=True),
        sa.Column('rr_tp2', sa.Float(), nullable=True),
        sa.Column('rr_tp3', sa.Float(), nullable=True),
        sa.Column('rr_tp4', sa.Float(), nullable=True),
        sa.Column('management_instructions', sa.Text(), nullable=True),
        sa.Column('plan_status', sa.String(length=24), nullable=False),
        sa.Column('plan_version', sa.String(length=16), nullable=False),
        sa.Column('historical_similarity_run_id', sa.String(length=32), nullable=True),
        sa.Column('historical_context', sa.String(length=32), nullable=True),
        sa.Column('lifecycle_state', sa.String(length=24), nullable=False),
        sa.Column('final_status', sa.String(length=24), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_id', name='uq_trade_plans_plan_id'),
    )
    op.create_index('ix_trade_plans_plan_id', 'trade_plans', ['plan_id'])
    op.create_index('ix_trade_plans_created_at', 'trade_plans', ['created_at'])
    op.create_index('ix_trade_plans_market_timestamp', 'trade_plans', ['market_timestamp'])
    op.create_index('ix_trade_plans_instrument', 'trade_plans', ['instrument'])
    op.create_index('ix_trade_plans_brain_decision', 'trade_plans', ['brain_decision'])
    op.create_index('ix_trade_plans_plan_status', 'trade_plans', ['plan_status'])
    op.create_index('ix_trade_plans_lifecycle_state', 'trade_plans', ['lifecycle_state'])
    op.create_index('ix_trade_plans_historical_similarity_run_id', 'trade_plans', ['historical_similarity_run_id'])

    op.create_table(
        'trade_plan_lifecycle_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('plan_id', sa.String(length=32), nullable=False),
        sa.Column('event_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('from_state', sa.String(length=24), nullable=True),
        sa.Column('to_state', sa.String(length=24), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=False),
        sa.Column('market_price', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_trade_plan_lifecycle_events_plan_id', 'trade_plan_lifecycle_events', ['plan_id'])
    op.create_index('ix_trade_plan_lifecycle_events_event_at', 'trade_plan_lifecycle_events', ['event_at'])

    op.create_table(
        'trade_plan_outcomes',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('plan_id', sa.String(length=32), nullable=False),
        sa.Column('last_evaluated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('entry_touched', sa.Boolean(), nullable=False),
        sa.Column('entry_touched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('entry_touch_price', sa.Float(), nullable=True),
        sa.Column('sl_before_target', sa.Boolean(), nullable=True),
        sa.Column('sl_hit_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sl_hit_price', sa.Float(), nullable=True),
        sa.Column('tp1_reached', sa.Boolean(), nullable=False),
        sa.Column('tp2_reached', sa.Boolean(), nullable=False),
        sa.Column('tp3_reached', sa.Boolean(), nullable=False),
        sa.Column('tp4_reached', sa.Boolean(), nullable=False),
        sa.Column('tp1_reached_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tp2_reached_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tp3_reached_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tp4_reached_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('max_favorable_excursion', sa.Float(), nullable=True),
        sa.Column('max_adverse_excursion', sa.Float(), nullable=True),
        sa.Column('time_to_entry_seconds', sa.Float(), nullable=True),
        sa.Column('time_to_tp1_seconds', sa.Float(), nullable=True),
        sa.Column('time_to_tp2_seconds', sa.Float(), nullable=True),
        sa.Column('time_to_tp3_seconds', sa.Float(), nullable=True),
        sa.Column('time_to_tp4_seconds', sa.Float(), nullable=True),
        sa.Column('final_status', sa.String(length=24), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_id', name='uq_trade_plan_outcomes_plan_id'),
    )
    op.create_index('ix_trade_plan_outcomes_plan_id', 'trade_plan_outcomes', ['plan_id'])


def downgrade() -> None:
    op.drop_index('ix_trade_plan_outcomes_plan_id', table_name='trade_plan_outcomes')
    op.drop_table('trade_plan_outcomes')
    op.drop_index('ix_trade_plan_lifecycle_events_event_at', table_name='trade_plan_lifecycle_events')
    op.drop_index('ix_trade_plan_lifecycle_events_plan_id', table_name='trade_plan_lifecycle_events')
    op.drop_table('trade_plan_lifecycle_events')
    op.drop_index('ix_trade_plans_historical_similarity_run_id', table_name='trade_plans')
    op.drop_index('ix_trade_plans_lifecycle_state', table_name='trade_plans')
    op.drop_index('ix_trade_plans_plan_status', table_name='trade_plans')
    op.drop_index('ix_trade_plans_brain_decision', table_name='trade_plans')
    op.drop_index('ix_trade_plans_instrument', table_name='trade_plans')
    op.drop_index('ix_trade_plans_market_timestamp', table_name='trade_plans')
    op.drop_index('ix_trade_plans_created_at', table_name='trade_plans')
    op.drop_index('ix_trade_plans_plan_id', table_name='trade_plans')
    op.drop_table('trade_plans')
