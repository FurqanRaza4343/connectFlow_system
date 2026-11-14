"""whatsapp status (stories): profile, campaigns, posts, daily usage

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-14 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'status_profiles',
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('instance_id', sa.String(36), nullable=True),
        sa.Column('auto', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('times', sa.String(40), nullable=False, server_default='11:00,19:00'),
        sa.Column('per_day', sa.Integer(), nullable=False, server_default='2'),
        sa.Column('approval', sa.String(8), nullable=False, server_default='review'),
        sa.Column('image_style', sa.String(8), nullable=False, server_default='card'),
        sa.Column('image_daily_cap', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('image_monthly_cap', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('language', sa.String(12), nullable=False, server_default='English'),
        sa.Column('footer', sa.String(160), nullable=False, server_default=''),
        sa.Column('accent', sa.String(7), nullable=False, server_default=''),
        sa.Column('dark', sa.String(7), nullable=False, server_default=''),
        sa.Column('ink', sa.String(7), nullable=False, server_default=''),
        sa.Column('button_text', sa.String(40), nullable=False, server_default='Message us'),
        sa.Column('button_link', sa.String(300), nullable=False, server_default=''),
        sa.Column('wa_number', sa.String(20), nullable=False, server_default=''),
        sa.Column('wa_text', sa.String(120), nullable=False, server_default='Hi, I saw your status'),
        sa.Column('templates', sa.String(120), nullable=False, server_default='gradient,band,split,badge,minimal,bold,frame,diagonal'),
        sa.Column('offers', sa.Text(), nullable=False, server_default=''),
        sa.Column('recipients', sa.Text(), nullable=False, server_default=''),
        sa.PrimaryKeyConstraint('tenant_id', name='pk_status_profiles'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='fk_status_profiles_tenant', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], name='fk_status_profiles_instance', ondelete='SET NULL'),
    )
    op.create_table(
        'status_campaigns',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(80), nullable=False, server_default=''),
        sa.Column('brief', sa.Text(), nullable=False, server_default=''),
        sa.Column('goal', sa.String(12), nullable=False, server_default='awareness'),
        sa.Column('days', sa.Integer(), nullable=False, server_default='7'),
        sa.Column('interval_hours', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('per_day', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('window_from', sa.String(5), nullable=False, server_default='10:00'),
        sa.Column('window_to', sa.String(5), nullable=False, server_default='21:00'),
        sa.Column('start_date', sa.String(10), nullable=False, server_default=''),
        sa.Column('language', sa.String(12), nullable=False, server_default='English'),
        sa.Column('style', sa.String(8), nullable=False, server_default='card'),
        sa.Column('state', sa.String(12), nullable=False, server_default='planning'),
        sa.Column('questions', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column('answers', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('plan', sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column('summary', sa.Text(), nullable=False, server_default=''),
        sa.Column('error', sa.String(300), nullable=False, server_default=''),
        sa.Column('total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('generated', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('busy_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id', name='pk_status_campaigns'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='fk_status_campaigns_tenant', ondelete='CASCADE'),
    )
    op.create_index('ix_status_campaigns_tenant', 'status_campaigns', ['tenant_id', 'state'])
    op.create_index('ix_status_campaigns_state', 'status_campaigns', ['state'])
    op.create_table(
        'status_posts',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('campaign_id', sa.String(36), nullable=True),
        sa.Column('mode', sa.String(10), nullable=False, server_default='own'),
        sa.Column('kind', sa.String(6), nullable=False, server_default='image'),
        sa.Column('state', sa.String(10), nullable=False, server_default='draft'),
        sa.Column('template', sa.String(12), nullable=False, server_default='gradient'),
        sa.Column('headline', sa.String(120), nullable=False, server_default=''),
        sa.Column('sub', sa.String(160), nullable=False, server_default=''),
        sa.Column('badge', sa.String(14), nullable=False, server_default=''),
        sa.Column('cta', sa.String(40), nullable=False, server_default=''),
        sa.Column('caption', sa.Text(), nullable=False, server_default=''),
        sa.Column('link', sa.String(200), nullable=False, server_default=''),
        sa.Column('btn_link', sa.String(300), nullable=False, server_default=''),
        sa.Column('prompt', sa.String(800), nullable=False, server_default=''),
        sa.Column('image_prompt', sa.String(300), nullable=False, server_default=''),
        sa.Column('accent', sa.String(7), nullable=False, server_default=''),
        sa.Column('dark', sa.String(7), nullable=False, server_default=''),
        sa.Column('ink', sa.String(7), nullable=False, server_default=''),
        sa.Column('overlay', sa.Integer(), nullable=False, server_default='48'),
        sa.Column('pos', sa.String(6), nullable=False, server_default='center'),
        sa.Column('plain', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('bg_key', sa.String(120), nullable=False, server_default=''),
        sa.Column('bg_is_upload', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('image_key', sa.String(120), nullable=False, server_default=''),
        sa.Column('instance_id', sa.String(36), nullable=True),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('posted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('wa_status_id', sa.String(120), nullable=False, server_default=''),
        sa.Column('error', sa.String(300), nullable=False, server_default=''),
        sa.Column('tries', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('auto', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('plan_theme', sa.String(12), nullable=False, server_default=''),
        sa.Column('plan_day', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id', name='pk_status_posts'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='fk_status_posts_tenant', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['campaign_id'], ['status_campaigns.id'], name='fk_status_posts_campaign', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], name='fk_status_posts_instance', ondelete='SET NULL'),
    )
    op.create_index('ix_status_posts_tenant_state', 'status_posts', ['tenant_id', 'state'])
    op.create_index('ix_status_posts_due', 'status_posts', ['state', 'scheduled_at'])
    op.create_index('ix_status_posts_campaign', 'status_posts', ['campaign_id'])
    op.create_table(
        'status_usage_daily',
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('images', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('copies', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('posted', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_micro', sa.BigInteger(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('tenant_id', 'day', name='pk_status_usage_daily'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name='fk_status_usage_daily_tenant', ondelete='CASCADE'),
    )


def downgrade() -> None:
    op.drop_table('status_usage_daily')
    op.drop_index('ix_status_posts_campaign', table_name='status_posts')
    op.drop_index('ix_status_posts_due', table_name='status_posts')
    op.drop_index('ix_status_posts_tenant_state', table_name='status_posts')
    op.drop_table('status_posts')
    op.drop_index('ix_status_campaigns_state', table_name='status_campaigns')
    op.drop_index('ix_status_campaigns_tenant', table_name='status_campaigns')
    op.drop_table('status_campaigns')
    op.drop_table('status_profiles')
