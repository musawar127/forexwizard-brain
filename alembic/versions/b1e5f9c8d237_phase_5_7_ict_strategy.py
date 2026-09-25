"""Phase 5.7: ICT/SMC strategy reasoning brain schema

Revision ID: b1e5f9c8d237
Revises: 9a3c1e7d8b41
Create Date: 2026-09-24 23:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b1e5f9c8d237'
down_revision = '9a3c1e7d8b41'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------- ICT strategy knowledge dictionary ----------
    op.create_table(
        'ict_strategy_knowledge',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('definition', sa.Text(), nullable=False),
        sa.Column('detection_rule_version', sa.String(length=16), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_ict_strategy_knowledge_name'),
    )
    op.create_index('ix_ict_strategy_knowledge_name', 'ict_strategy_knowledge', ['name'])
    op.create_index('ix_ict_strategy_knowledge_category', 'ict_strategy_knowledge', ['category'])

    # ---------- ICT structures (swings + BOS/CHoCH/MSS events) ----------
    op.create_table(
        'ict_structures',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('instrument', sa.String(length=32), nullable=False),
        sa.Column('timeframe', sa.String(length=8), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('structure_type', sa.String(length=16), nullable=False),
        sa.Column('direction', sa.String(length=8), nullable=True),
        sa.Column('quality', sa.Float(), nullable=False),
        sa.Column('broken_level', sa.Float(), nullable=True),
        sa.Column('invalidation', sa.Float(), nullable=True),
        sa.Column('feature_version', sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ict_structures_detected_at', 'ict_structures', ['detected_at'])
    op.create_index('ix_ict_structures_instrument', 'ict_structures', ['instrument'])
    op.create_index('ix_ict_structures_timeframe', 'ict_structures', ['timeframe'])
    op.create_index('ix_ict_structures_timestamp', 'ict_structures', ['timestamp'])
    op.create_index('ix_ict_structures_structure_type', 'ict_structures', ['structure_type'])

    # ---------- ICT liquidity levels ----------
    op.create_table(
        'ict_liquidity_levels',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('instrument', sa.String(length=32), nullable=False),
        sa.Column('price', sa.Float(), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('timeframe', sa.String(length=8), nullable=True),
        sa.Column('session', sa.String(length=16), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('swept', sa.Boolean(), nullable=False),
        sa.Column('swept_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('feature_version', sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ict_liquidity_levels_detected_at', 'ict_liquidity_levels', ['detected_at'])
    op.create_index('ix_ict_liquidity_levels_instrument', 'ict_liquidity_levels', ['instrument'])
    op.create_index('ix_ict_liquidity_levels_price', 'ict_liquidity_levels', ['price'])
    op.create_index('ix_ict_liquidity_levels_kind', 'ict_liquidity_levels', ['kind'])

    # ---------- ICT liquidity sweeps ----------
    op.create_table(
        'ict_liquidity_sweeps',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('instrument', sa.String(length=32), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('level', sa.Float(), nullable=False),
        sa.Column('level_kind', sa.String(length=32), nullable=False),
        sa.Column('direction', sa.String(length=32), nullable=False),
        sa.Column('reaction_magnitude', sa.Float(), nullable=False),
        sa.Column('reaction_atr_multiple', sa.Float(), nullable=True),
        sa.Column('failed', sa.Boolean(), nullable=False),
        sa.Column('feature_version', sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ict_liquidity_sweeps_detected_at', 'ict_liquidity_sweeps', ['detected_at'])
    op.create_index('ix_ict_liquidity_sweeps_instrument', 'ict_liquidity_sweeps', ['instrument'])
    op.create_index('ix_ict_liquidity_sweeps_timestamp', 'ict_liquidity_sweeps', ['timestamp'])
    op.create_index('ix_ict_liquidity_sweeps_direction', 'ict_liquidity_sweeps', ['direction'])

    # ---------- ICT Fair Value Gaps ----------
    op.create_table(
        'ict_fvgs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('instrument', sa.String(length=32), nullable=False),
        sa.Column('timeframe', sa.String(length=8), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('direction', sa.String(length=8), nullable=False),
        sa.Column('upper', sa.Float(), nullable=False),
        sa.Column('lower', sa.Float(), nullable=False),
        sa.Column('midpoint', sa.Float(), nullable=False),
        sa.Column('mitigated', sa.Boolean(), nullable=False),
        sa.Column('fully_filled', sa.Boolean(), nullable=False),
        sa.Column('invalidated', sa.Boolean(), nullable=False),
        sa.Column('mitigated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('filled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('feature_version', sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ict_fvgs_detected_at', 'ict_fvgs', ['detected_at'])
    op.create_index('ix_ict_fvgs_instrument', 'ict_fvgs', ['instrument'])
    op.create_index('ix_ict_fvgs_timeframe', 'ict_fvgs', ['timeframe'])
    op.create_index('ix_ict_fvgs_timestamp', 'ict_fvgs', ['timestamp'])
    op.create_index('ix_ict_fvgs_direction', 'ict_fvgs', ['direction'])

    # ---------- ICT Order Blocks ----------
    op.create_table(
        'ict_order_blocks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('instrument', sa.String(length=32), nullable=False),
        sa.Column('timeframe', sa.String(length=8), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('direction', sa.String(length=8), nullable=False),
        sa.Column('upper', sa.Float(), nullable=False),
        sa.Column('lower', sa.Float(), nullable=False),
        sa.Column('midpoint', sa.Float(), nullable=False),
        sa.Column('quality', sa.Float(), nullable=False),
        sa.Column('bos_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('mitigated', sa.Boolean(), nullable=False),
        sa.Column('mitigated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('invalidated', sa.Boolean(), nullable=False),
        sa.Column('feature_version', sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_ict_order_blocks_detected_at', 'ict_order_blocks', ['detected_at'])
    op.create_index('ix_ict_order_blocks_instrument', 'ict_order_blocks', ['instrument'])
    op.create_index('ix_ict_order_blocks_timeframe', 'ict_order_blocks', ['timeframe'])
    op.create_index('ix_ict_order_blocks_timestamp', 'ict_order_blocks', ['timestamp'])
    op.create_index('ix_ict_order_blocks_direction', 'ict_order_blocks', ['direction'])

    # ---------- ICT Pattern Stats (prospective only) ----------
    op.create_table(
        'ict_pattern_stats',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('pattern_id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('signature_json', sa.Text(), nullable=False),
        sa.Column('friendly_name', sa.String(length=64), nullable=True),
        sa.Column('forward_sample_size', sa.Integer(), nullable=False),
        sa.Column('tp1_reached', sa.Integer(), nullable=False),
        sa.Column('tp2_reached', sa.Integer(), nullable=False),
        sa.Column('tp3_reached', sa.Integer(), nullable=False),
        sa.Column('max_objective_reached', sa.Integer(), nullable=False),
        sa.Column('sl_reached', sa.Integer(), nullable=False),
        sa.Column('avg_mfe', sa.Float(), nullable=True),
        sa.Column('avg_mae', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('pattern_id', name='uq_ict_pattern_stats_pattern_id'),
    )
    op.create_index('ix_ict_pattern_stats_pattern_id', 'ict_pattern_stats', ['pattern_id'])
    op.create_index('ix_ict_pattern_stats_created_at', 'ict_pattern_stats', ['created_at'])
    op.create_index('ix_ict_pattern_stats_status', 'ict_pattern_stats', ['status'])

    # ---------- ICT Candidate Patterns (experimental) ----------
    op.create_table(
        'ict_candidate_patterns',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('candidate_id', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('friendly_name', sa.String(length=64), nullable=True),
        sa.Column('conditions_json', sa.Text(), nullable=False),
        sa.Column('historical_sample_size', sa.Integer(), nullable=False),
        sa.Column('forward_sample_size', sa.Integer(), nullable=False),
        sa.Column('historical_tp1_rate', sa.Float(), nullable=True),
        sa.Column('forward_tp1_rate', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('candidate_id', name='uq_ict_candidate_patterns_candidate_id'),
    )
    op.create_index('ix_ict_candidate_patterns_candidate_id', 'ict_candidate_patterns', ['candidate_id'])
    op.create_index('ix_ict_candidate_patterns_created_at', 'ict_candidate_patterns', ['created_at'])
    op.create_index('ix_ict_candidate_patterns_status', 'ict_candidate_patterns', ['status'])

    # ---------- Extend trade_plans with ICT-specific fields ----------
    # These columns are nullable so old WAIT/NO_TRADE rows from Phase 5.6
    # remain valid. New ICT-driven setups populate them; old-style Phase 5.6
    # plans use the existing tp1-tp4 / risk_distance / etc. fields.
    op.add_column('trade_plans', sa.Column('setup_thesis', sa.Text(), nullable=True))
    op.add_column('trade_plans', sa.Column('for_evidence_json', sa.Text(), nullable=True))
    op.add_column('trade_plans', sa.Column('against_evidence_json', sa.Text(), nullable=True))
    op.add_column('trade_plans', sa.Column('structural_invalidation', sa.Float(), nullable=True))
    op.add_column('trade_plans', sa.Column('max_objective', sa.Float(), nullable=True))
    op.add_column('trade_plans', sa.Column('max_objective_reason', sa.String(length=255), nullable=True))
    op.add_column('trade_plans', sa.Column('preferred_entry', sa.Float(), nullable=True))
    op.add_column('trade_plans', sa.Column('session_context_json', sa.Text(), nullable=True))
    op.add_column('trade_plans', sa.Column('setup_pattern_id', sa.String(length=32), nullable=True))
    op.add_column('trade_plans', sa.Column('plan_engine_version', sa.String(length=16), nullable=True))
    # plan_engine_version distinguishes Phase 5.6 plans ('trade-plan-v0.1')
    # from Phase 5.7 ICT-driven plans ('ict-plan-v0.1').


def downgrade() -> None:
    # Remove trade_plans extensions
    op.drop_column('trade_plans', 'plan_engine_version')
    op.drop_column('trade_plans', 'setup_pattern_id')
    op.drop_column('trade_plans', 'session_context_json')
    op.drop_column('trade_plans', 'preferred_entry')
    op.drop_column('trade_plans', 'max_objective_reason')
    op.drop_column('trade_plans', 'max_objective')
    op.drop_column('trade_plans', 'structural_invalidation')
    op.drop_column('trade_plans', 'against_evidence_json')
    op.drop_column('trade_plans', 'for_evidence_json')
    op.drop_column('trade_plans', 'setup_thesis')

    # Drop ICT tables (reverse order)
    op.drop_index('ix_ict_candidate_patterns_status', table_name='ict_candidate_patterns')
    op.drop_index('ix_ict_candidate_patterns_created_at', table_name='ict_candidate_patterns')
    op.drop_index('ix_ict_candidate_patterns_candidate_id', table_name='ict_candidate_patterns')
    op.drop_table('ict_candidate_patterns')

    op.drop_index('ix_ict_pattern_stats_status', table_name='ict_pattern_stats')
    op.drop_index('ix_ict_pattern_stats_created_at', table_name='ict_pattern_stats')
    op.drop_index('ix_ict_pattern_stats_pattern_id', table_name='ict_pattern_stats')
    op.drop_table('ict_pattern_stats')

    op.drop_index('ix_ict_order_blocks_direction', table_name='ict_order_blocks')
    op.drop_index('ix_ict_order_blocks_timestamp', table_name='ict_order_blocks')
    op.drop_index('ix_ict_order_blocks_timeframe', table_name='ict_order_blocks')
    op.drop_index('ix_ict_order_blocks_instrument', table_name='ict_order_blocks')
    op.drop_index('ix_ict_order_blocks_detected_at', table_name='ict_order_blocks')
    op.drop_table('ict_order_blocks')

    op.drop_index('ix_ict_fvgs_direction', table_name='ict_fvgs')
    op.drop_index('ix_ict_fvgs_timestamp', table_name='ict_fvgs')
    op.drop_index('ix_ict_fvgs_timeframe', table_name='ict_fvgs')
    op.drop_index('ix_ict_fvgs_instrument', table_name='ict_fvgs')
    op.drop_index('ix_ict_fvgs_detected_at', table_name='ict_fvgs')
    op.drop_table('ict_fvgs')

    op.drop_index('ix_ict_liquidity_sweeps_direction', table_name='ict_liquidity_sweeps')
    op.drop_index('ix_ict_liquidity_sweeps_timestamp', table_name='ict_liquidity_sweeps')
    op.drop_index('ix_ict_liquidity_sweeps_instrument', table_name='ict_liquidity_sweeps')
    op.drop_index('ix_ict_liquidity_sweeps_detected_at', table_name='ict_liquidity_sweeps')
    op.drop_table('ict_liquidity_sweeps')

    op.drop_index('ix_ict_liquidity_levels_kind', table_name='ict_liquidity_levels')
    op.drop_index('ix_ict_liquidity_levels_price', table_name='ict_liquidity_levels')
    op.drop_index('ix_ict_liquidity_levels_instrument', table_name='ict_liquidity_levels')
    op.drop_index('ix_ict_liquidity_levels_detected_at', table_name='ict_liquidity_levels')
    op.drop_table('ict_liquidity_levels')

    op.drop_index('ix_ict_structures_structure_type', table_name='ict_structures')
    op.drop_index('ix_ict_structures_timestamp', table_name='ict_structures')
    op.drop_index('ix_ict_structures_timeframe', table_name='ict_structures')
    op.drop_index('ix_ict_structures_instrument', table_name='ict_structures')
    op.drop_index('ix_ict_structures_detected_at', table_name='ict_structures')
    op.drop_table('ict_structures')

    op.drop_index('ix_ict_strategy_knowledge_category', table_name='ict_strategy_knowledge')
    op.drop_index('ix_ict_strategy_knowledge_name', table_name='ict_strategy_knowledge')
    op.drop_table('ict_strategy_knowledge')
