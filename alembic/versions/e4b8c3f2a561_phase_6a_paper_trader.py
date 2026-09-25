"""Phase 6A: self-learning paper trader and loss review engine

Revision ID: e4b8c3f2a561
Revises: d3a7b2e1f459
Create Date: 2026-09-25 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e4b8c3f2a561'
down_revision = 'd3a7b2e1f459'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Paper accounts
    op.create_table(
        'paper_accounts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('paper_account_id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('starting_equity', sa.Float(), nullable=False),
        sa.Column('current_equity', sa.Float(), nullable=False),
        sa.Column('realized_pnl', sa.Float(), nullable=False),
        sa.Column('unrealized_pnl', sa.Float(), nullable=False),
        sa.Column('max_equity', sa.Float(), nullable=False),
        sa.Column('max_drawdown', sa.Float(), nullable=False),
        sa.Column('paper_trade_count', sa.Integer(), nullable=False),
        sa.Column('win_count', sa.Integer(), nullable=False),
        sa.Column('loss_count', sa.Integer(), nullable=False),
        sa.Column('breakeven_count', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('paper_account_id', name='uq_paper_accounts_id'),
    )
    op.create_index('ix_paper_accounts_paper_account_id', 'paper_accounts', ['paper_account_id'])

    # Paper trades
    op.create_table(
        'paper_trades',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('paper_trade_id', sa.String(length=32), nullable=False),
        sa.Column('trade_plan_id', sa.String(length=32), nullable=False),
        sa.Column('paper_account_id', sa.String(length=32), nullable=False),
        sa.Column('instrument', sa.String(length=32), nullable=False),
        sa.Column('direction', sa.String(length=8), nullable=False),
        sa.Column('entry_low', sa.Float(), nullable=True),
        sa.Column('entry_high', sa.Float(), nullable=True),
        sa.Column('preferred_entry', sa.Float(), nullable=True),
        sa.Column('actual_paper_entry', sa.Float(), nullable=True),
        sa.Column('stop_loss', sa.Float(), nullable=True),
        sa.Column('tp1', sa.Float(), nullable=True),
        sa.Column('tp2', sa.Float(), nullable=True),
        sa.Column('tp3', sa.Float(), nullable=True),
        sa.Column('max_objective', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('entry_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('exit_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('exit_reason', sa.String(length=64), nullable=True),
        sa.Column('realized_pnl_points', sa.Float(), nullable=True),
        sa.Column('realized_r_multiple', sa.Float(), nullable=True),
        sa.Column('mfe', sa.Float(), nullable=True),
        sa.Column('mae', sa.Float(), nullable=True),
        sa.Column('time_to_entry', sa.Float(), nullable=True),
        sa.Column('time_to_tp1', sa.Float(), nullable=True),
        sa.Column('time_to_tp2', sa.Float(), nullable=True),
        sa.Column('time_to_tp3', sa.Float(), nullable=True),
        sa.Column('time_to_max', sa.Float(), nullable=True),
        sa.Column('time_to_stop', sa.Float(), nullable=True),
        sa.Column('breakeven_activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('breakeven_price', sa.Float(), nullable=True),
        sa.Column('setup_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('strategy_version', sa.String(length=16), nullable=False),
        sa.Column('plan_engine_version', sa.String(length=16), nullable=True),
        sa.Column('market_snapshot_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('paper_trade_id', name='uq_paper_trades_id'),
    )
    op.create_index('ix_paper_trades_paper_trade_id', 'paper_trades', ['paper_trade_id'])
    op.create_index('ix_paper_trades_trade_plan_id', 'paper_trades', ['trade_plan_id'])
    op.create_index('ix_paper_trades_paper_account_id', 'paper_trades', ['paper_account_id'])
    op.create_index('ix_paper_trades_status', 'paper_trades', ['status'])
    op.create_index('ix_paper_trades_setup_fingerprint', 'paper_trades', ['setup_fingerprint'])
    op.create_index('ix_paper_trades_created_at', 'paper_trades', ['created_at'])

    # Trade reviews
    op.create_table(
        'trade_reviews',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('review_id', sa.String(length=32), nullable=False),
        sa.Column('paper_trade_id', sa.String(length=32), nullable=False),
        sa.Column('result', sa.String(length=16), nullable=False),
        sa.Column('r_multiple', sa.Float(), nullable=True),
        sa.Column('what_worked_json', sa.Text(), nullable=True),
        sa.Column('what_failed_json', sa.Text(), nullable=True),
        sa.Column('mistake_tags_json', sa.Text(), nullable=True),
        sa.Column('market_context_json', sa.Text(), nullable=True),
        sa.Column('primary_failure_reason', sa.String(length=64), nullable=True),
        sa.Column('secondary_failure_reasons', sa.Text(), nullable=True),
        sa.Column('review_version', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('review_id', name='uq_trade_reviews_id'),
    )
    op.create_index('ix_trade_reviews_review_id', 'trade_reviews', ['review_id'])
    op.create_index('ix_trade_reviews_paper_trade_id', 'trade_reviews', ['paper_trade_id'])

    # Strategy mistake patterns
    op.create_table(
        'strategy_mistake_patterns',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('pattern_id', sa.String(length=32), nullable=False),
        sa.Column('mistake_type', sa.String(length=64), nullable=False),
        sa.Column('conditions_json', sa.Text(), nullable=False),
        sa.Column('first_seen', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen', sa.DateTime(timezone=True), nullable=False),
        sa.Column('occurrence_count', sa.Integer(), nullable=False),
        sa.Column('loss_count', sa.Integer(), nullable=False),
        sa.Column('win_count', sa.Integer(), nullable=False),
        sa.Column('avg_r', sa.Float(), nullable=True),
        sa.Column('median_mfe', sa.Float(), nullable=True),
        sa.Column('median_mae', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pattern_id', name='uq_strategy_mistake_patterns_id'),
    )
    op.create_index('ix_strategy_mistake_patterns_pattern_id', 'strategy_mistake_patterns', ['pattern_id'])
    op.create_index('ix_strategy_mistake_patterns_mistake_type', 'strategy_mistake_patterns', ['mistake_type'])
    op.create_index('ix_strategy_mistake_patterns_status', 'strategy_mistake_patterns', ['status'])

    # Candidate strategy rules
    op.create_table(
        'candidate_strategy_rules',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('candidate_id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('trigger_conditions_json', sa.Text(), nullable=False),
        sa.Column('proposed_change_json', sa.Text(), nullable=True),
        sa.Column('source_pattern', sa.String(length=32), nullable=True),
        sa.Column('source_trade_count', sa.Integer(), nullable=False),
        sa.Column('historical_sample', sa.Integer(), nullable=False),
        sa.Column('forward_sample', sa.Integer(), nullable=False),
        sa.Column('baseline_metrics_json', sa.Text(), nullable=True),
        sa.Column('candidate_metrics_json', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('candidate_id', name='uq_candidate_strategy_rules_id'),
    )
    op.create_index('ix_candidate_strategy_rules_candidate_id', 'candidate_strategy_rules', ['candidate_id'])
    op.create_index('ix_candidate_strategy_rules_status', 'candidate_strategy_rules', ['status'])

    # External strategy knowledge (Matrix ingestion framework)
    op.create_table(
        'external_strategy_knowledge',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('strategy_id', sa.String(length=32), nullable=False),
        sa.Column('strategy_name', sa.String(length=64), nullable=False),
        sa.Column('source_type', sa.String(length=32), nullable=False),
        sa.Column('source_reference', sa.String(length=255), nullable=True),
        sa.Column('concepts_json', sa.Text(), nullable=True),
        sa.Column('entry_rules_json', sa.Text(), nullable=True),
        sa.Column('exit_rules_json', sa.Text(), nullable=True),
        sa.Column('invalidation_rules_json', sa.Text(), nullable=True),
        sa.Column('timeframe_rules_json', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('strategy_id', name='uq_external_strategy_knowledge_id'),
    )
    op.create_index('ix_external_strategy_knowledge_strategy_id', 'external_strategy_knowledge', ['strategy_id'])
    op.create_index('ix_external_strategy_knowledge_status', 'external_strategy_knowledge', ['status'])

    # Paper trader audit log
    op.create_table(
        'paper_trader_audit_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_type', sa.String(length=48), nullable=False),
        sa.Column('paper_trade_id', sa.String(length=32), nullable=True),
        sa.Column('event_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('detail', sa.String(length=255), nullable=True),
        sa.Column('market_price', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_paper_trader_audit_log_event_type', 'paper_trader_audit_log', ['event_type'])
    op.create_index('ix_paper_trader_audit_log_paper_trade_id', 'paper_trader_audit_log', ['paper_trade_id'])
    op.create_index('ix_paper_trader_audit_log_event_at', 'paper_trader_audit_log', ['event_at'])


def downgrade() -> None:
    op.drop_index('ix_paper_trader_audit_log_event_at', table_name='paper_trader_audit_log')
    op.drop_index('ix_paper_trader_audit_log_paper_trade_id', table_name='paper_trader_audit_log')
    op.drop_index('ix_paper_trader_audit_log_event_type', table_name='paper_trader_audit_log')
    op.drop_table('paper_trader_audit_log')

    op.drop_index('ix_external_strategy_knowledge_status', table_name='external_strategy_knowledge')
    op.drop_index('ix_external_strategy_knowledge_strategy_id', table_name='external_strategy_knowledge')
    op.drop_table('external_strategy_knowledge')

    op.drop_index('ix_candidate_strategy_rules_status', table_name='candidate_strategy_rules')
    op.drop_index('ix_candidate_strategy_rules_candidate_id', table_name='candidate_strategy_rules')
    op.drop_table('candidate_strategy_rules')

    op.drop_index('ix_strategy_mistake_patterns_status', table_name='strategy_mistake_patterns')
    op.drop_index('ix_strategy_mistake_patterns_mistake_type', table_name='strategy_mistake_patterns')
    op.drop_index('ix_strategy_mistake_patterns_pattern_id', table_name='strategy_mistake_patterns')
    op.drop_table('strategy_mistake_patterns')

    op.drop_index('ix_trade_reviews_paper_trade_id', table_name='trade_reviews')
    op.drop_index('ix_trade_reviews_review_id', table_name='trade_reviews')
    op.drop_table('trade_reviews')

    op.drop_index('ix_paper_trades_created_at', table_name='paper_trades')
    op.drop_index('ix_paper_trades_setup_fingerprint', table_name='paper_trades')
    op.drop_index('ix_paper_trades_status', table_name='paper_trades')
    op.drop_index('ix_paper_trades_paper_account_id', table_name='paper_trades')
    op.drop_index('ix_paper_trades_trade_plan_id', table_name='paper_trades')
    op.drop_index('ix_paper_trades_paper_trade_id', table_name='paper_trades')
    op.drop_table('paper_trades')

    op.drop_index('ix_paper_accounts_paper_account_id', table_name='paper_accounts')
    op.drop_table('paper_accounts')
