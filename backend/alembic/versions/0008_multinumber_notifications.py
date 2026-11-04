"""multinumber_notifications

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-05 09:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('instances', sa.Column('outreach_enabled', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('outreach_profiles', sa.Column('notify_email', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('outreach_profiles', sa.Column('notify_extra_emails', sa.String(length=600), server_default='', nullable=False))
    op.add_column('outreach_profiles', sa.Column('notify_digest', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('outreach_profiles', 'notify_digest')
    op.drop_column('outreach_profiles', 'notify_extra_emails')
    op.drop_column('outreach_profiles', 'notify_email')
    op.drop_column('instances', 'outreach_enabled')
