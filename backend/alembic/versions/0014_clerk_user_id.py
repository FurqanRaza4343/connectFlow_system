"""users.clerk_user_id: link to the Clerk identity (identity only; workspaces and roles stay here)

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-15 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('clerk_user_id', sa.String(64), nullable=True))
    op.create_unique_constraint('uq_users_clerk_user_id', 'users', ['clerk_user_id'])


def downgrade() -> None:
    op.drop_constraint('uq_users_clerk_user_id', 'users', type_='unique')
    op.drop_column('users', 'clerk_user_id')
