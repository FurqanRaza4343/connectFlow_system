"""stripe_billing_over_limit

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-05 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('stripe_customer_id', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_tenants_stripe_customer_id'), 'tenants', ['stripe_customer_id'], unique=False)
    op.add_column('tenants', sa.Column('stripe_subscription_id', sa.String(length=64), nullable=True))
    op.add_column('tenants', sa.Column('subscription_status', sa.String(length=24), server_default='', nullable=False))
    op.add_column('tenants', sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tenants', sa.Column('cancel_at_period_end', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('tenants', sa.Column('past_due_since', sa.DateTime(timezone=True), nullable=True))
    op.add_column('instances', sa.Column('over_limit', sa.Boolean(), server_default='false', nullable=False))
    # Databases upgraded from before the AI caps existed still carry the migration default (200) on every plan: apply the intended
    # Trial/Growth caps, but only where nobody has edited the value.
    op.execute("update plans set ai_calls_day = 20 where id = 'trial' and ai_calls_day = 200")
    op.execute("update plans set ai_calls_day = 1000 where id = 'growth' and ai_calls_day = 200")
    op.create_table(
        'stripe_events',
        sa.Column('id', sa.String(length=80), nullable=False),
        sa.Column('type', sa.String(length=80), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('stripe_events')
    op.drop_column('instances', 'over_limit')
    op.drop_column('tenants', 'past_due_since')
    op.drop_column('tenants', 'cancel_at_period_end')
    op.drop_column('tenants', 'current_period_end')
    op.drop_column('tenants', 'subscription_status')
    op.drop_column('tenants', 'stripe_subscription_id')
    op.drop_index(op.f('ix_tenants_stripe_customer_id'), table_name='tenants')
    op.drop_column('tenants', 'stripe_customer_id')
