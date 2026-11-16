"""14-day trial and first-run onboarding: tenants.trial_ends_at, tenants.onboarded_at, trial reminder markers

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-16 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tenants', sa.Column('trial_ends_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tenants', sa.Column('onboarded_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tenants', sa.Column('trial_reminder', sa.String(8), nullable=False, server_default=''))
    # every workspace that exists today is already set up and has no trial end date
    op.execute("UPDATE tenants SET onboarded_at = now()")


def downgrade() -> None:
    op.drop_column('tenants', 'trial_reminder')
    op.drop_column('tenants', 'onboarded_at')
    op.drop_column('tenants', 'trial_ends_at')
