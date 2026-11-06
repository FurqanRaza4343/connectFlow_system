"""two_factor_encryption_export

Two-factor authentication columns on users, the account-deletion request marker on tenants, and room for encrypted webhook secrets.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-05 17:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('totp_secret', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('totp_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('totp_last_step', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('recovery_codes', sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False))
    op.add_column('tenants', sa.Column('deletion_requested_at', sa.DateTime(timezone=True), nullable=True))
    # enc1:<Fernet token> does not fit in 80 characters; existing plaintext secrets are encrypted by the application at start-up
    op.alter_column('webhook_endpoints', 'secret', existing_type=sa.String(length=80), type_=sa.Text(), existing_nullable=False)


def downgrade() -> None:
    # NOTE: encrypted secrets (enc1:...) are longer than 80 characters and cannot be restored to the old column; regenerate them after a downgrade.
    op.execute("UPDATE webhook_endpoints SET secret = 'whsec_reset_' || substr(md5(random()::text), 1, 24) WHERE length(secret) > 80")
    op.alter_column('webhook_endpoints', 'secret', existing_type=sa.Text(), type_=sa.String(length=80), existing_nullable=False)
    op.drop_column('tenants', 'deletion_requested_at')
    op.drop_column('users', 'recovery_codes')
    op.drop_column('users', 'totp_last_step')
    op.drop_column('users', 'totp_enabled_at')
    op.drop_column('users', 'totp_secret')
