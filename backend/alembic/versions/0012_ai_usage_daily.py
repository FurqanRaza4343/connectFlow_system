"""ai_usage_daily

Per workspace per UTC day: AI calls, provider retries, tokens in/out/cached and an ESTIMATED cost in micro-USD (the price comes from the
AI_PRICE_* settings; 0 means 'unknown'). Written with an atomic upsert by app/ai.py; never holds prompts or message text.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-08 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'ai_usage_daily',
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('calls', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('retries', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tokens_in', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('tokens_out', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('tokens_cached', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('est_cost_micro_usd', sa.BigInteger(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('tenant_id', 'day', name='pk_ai_usage_daily'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='fk_ai_usage_daily_tenant', ondelete='CASCADE'),
    )
    op.create_index('ix_ai_usage_daily_day', 'ai_usage_daily', ['day'])


def downgrade() -> None:
    op.drop_index('ix_ai_usage_daily_day', table_name='ai_usage_daily')
    op.drop_table('ai_usage_daily')
