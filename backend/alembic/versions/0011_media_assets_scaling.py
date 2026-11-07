"""media_assets_scaling

Media library (assets, variants, draft attachments), per-plan storage limits, the outreach media settings, media columns on messages,
and the composite indexes the hot paths need (per-send quota counts, per-number counts, follow-up selection).

Plain CREATE INDEX (not CONCURRENTLY) is used on purpose: Alembic runs inside a transaction and today's tables are small (a few thousand
rows). When messages/usage_events reach millions, create these indexes by hand with CREATE INDEX CONCURRENTLY BEFORE upgrading; the
IF NOT EXISTS below then makes this step a no-op (see docs/MEDIA.md).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-06 20:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

MB = 1024 * 1024


def upgrade() -> None:
    op.add_column('plans', sa.Column('asset_bytes_limit', sa.BigInteger(), server_default=str(20 * MB), nullable=False))
    op.add_column('plans', sa.Column('asset_count_limit', sa.Integer(), server_default='5', nullable=False))
    op.execute(f"UPDATE plans SET asset_bytes_limit = {20 * MB}, asset_count_limit = 5 WHERE id = 'trial'")
    op.execute(f"UPDATE plans SET asset_bytes_limit = {200 * MB}, asset_count_limit = 30 WHERE id = 'starter'")
    op.execute(f"UPDATE plans SET asset_bytes_limit = {2048 * MB}, asset_count_limit = 200 WHERE id = 'growth'")

    op.create_table(
        'assets',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('label', sa.String(120), nullable=False, server_default=''),
        sa.Column('tags', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('mime', sa.String(80), nullable=False, server_default=''),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('sha256', sa.String(64), nullable=False, server_default=''),
        sa.Column('storage_backend', sa.String(12), nullable=False, server_default=''),
        sa.Column('storage_key', sa.String(300), nullable=False, server_default=''),
        sa.Column('thumb_key', sa.String(300), nullable=False, server_default=''),
        sa.Column('link_url', sa.String(500), nullable=False, server_default=''),
        sa.Column('link_title', sa.String(200), nullable=False, server_default=''),
        sa.Column('created_by', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_assets_tenant_id', 'assets', ['tenant_id'])
    op.create_table(
        'asset_variants',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('asset_id', sa.String(36), sa.ForeignKey('assets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('storage_backend', sa.String(12), nullable=False, server_default=''),
        sa.Column('storage_key', sa.String(300), nullable=False, server_default=''),
        sa.Column('mime', sa.String(80), nullable=False, server_default=''),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('sha256', sa.String(64), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_asset_variants_asset_id', 'asset_variants', ['asset_id'])
    op.create_index('ix_asset_variants_tenant_id', 'asset_variants', ['tenant_id'])
    op.create_table(
        'draft_assets',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('draft_id', sa.String(36), sa.ForeignKey('drafts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('asset_id', sa.String(36), sa.ForeignKey('assets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_draft_assets_draft_id', 'draft_assets', ['draft_id'])
    op.create_index('ix_draft_assets_asset_id', 'draft_assets', ['asset_id'])

    op.add_column('outreach_profiles', sa.Column('media', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column('messages', sa.Column('media_kind', sa.String(12), nullable=True))
    op.add_column('messages', sa.Column('asset_ids', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))

    op.execute("CREATE INDEX IF NOT EXISTS ix_messages_tenant_dir_kind_at ON messages (tenant_id, direction, kind, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_usage_events_tenant_kind_at ON usage_events (tenant_id, kind, at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_leads_followup ON leads (tenant_id, status, followup_stage, sent_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_leads_followup")
    op.execute("DROP INDEX IF EXISTS ix_usage_events_tenant_kind_at")
    op.execute("DROP INDEX IF EXISTS ix_messages_tenant_dir_kind_at")
    op.drop_column('messages', 'asset_ids')
    op.drop_column('messages', 'media_kind')
    op.drop_column('outreach_profiles', 'media')
    op.drop_index('ix_draft_assets_asset_id', table_name='draft_assets')
    op.drop_index('ix_draft_assets_draft_id', table_name='draft_assets')
    op.drop_table('draft_assets')
    op.drop_index('ix_asset_variants_tenant_id', table_name='asset_variants')
    op.drop_index('ix_asset_variants_asset_id', table_name='asset_variants')
    op.drop_table('asset_variants')
    op.drop_index('ix_assets_tenant_id', table_name='assets')
    op.drop_table('assets')
    op.drop_column('plans', 'asset_count_limit')
    op.drop_column('plans', 'asset_bytes_limit')
