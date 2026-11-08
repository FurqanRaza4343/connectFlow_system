import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # trial | starter | growth
    name: Mapped[str] = mapped_column(String(64))
    max_instances: Mapped[int] = mapped_column(Integer)
    max_msgs_day: Mapped[int] = mapped_column(Integer)
    max_members: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    price_usd: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    price_pkr: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    ai_calls_day: Mapped[int] = mapped_column(Integer, default=200, server_default="200")  # AI calls per workspace per UTC day
    asset_bytes_limit: Mapped[int] = mapped_column(BigInteger, default=20 * 1024 * 1024, server_default=str(20 * 1024 * 1024))  # media library storage per workspace
    asset_count_limit: Mapped[int] = mapped_column(Integer, default=5, server_default="5")  # media library items (files and links) per workspace


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(120))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), default="trial")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | suspended
    brand_color: Mapped[str | None] = mapped_column(String(7), nullable=True)  # #rrggbb, null = ConnectFlow green
    logo_data: Mapped[str | None] = mapped_column(Text, nullable=True)  # data URL (png/jpeg/webp/svg), <= 256 KB
    sidebar_color: Mapped[str | None] = mapped_column(String(7), nullable=True)  # #rrggbb, null = default
    page_color: Mapped[str | None] = mapped_column(String(7), nullable=True)  # #rrggbb, null = default
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # owner asked to delete the workspace; an admin acts manually
    website_url: Mapped[str] = mapped_column(String(300), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Stripe subscription state (all empty while the workspace has never subscribed)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscription_status: Mapped[str] = mapped_column(String(24), default="", server_default="")  # active | trialing | past_due | canceled | ""
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    past_due_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # start of the 7-day grace after a failed payment
    plan: Mapped[Plan] = relationship(lazy="joined")


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    avatar_url: Mapped[str] = mapped_column(String(500), default="")
    totp_secret: Mapped[str | None] = mapped_column(Text, nullable=True)  # enc1:... (app/crypto.py); pending enrolment while totp_enabled_at is null
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    totp_last_step: Mapped[int | None] = mapped_column(Integer, nullable=True)  # last accepted 30 s step: a code can never be used twice
    recovery_codes: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'::json"))  # keyed hashes of the unused single-use recovery codes
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="owner")  # owner | admin | operator | viewer


class Instance(Base):
    """One WhatsApp number of a tenant. Maps 1:1 to an OpenWA session on the Baileys node."""

    __tablename__ = "instances"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(80))
    node_id: Mapped[str] = mapped_column(String(32), default="wa1")  # which OpenWA node hosts it (multi-node later)
    engine: Mapped[str] = mapped_column(String(24), default="baileys")
    openwa_name: Mapped[str] = mapped_column(String(64), unique=True)
    openwa_id: Mapped[str] = mapped_column(String(64), default="")
    phone: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(24), default="created")
    outreach_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")  # False = API-only number, never used by the outreach sender
    over_limit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # plan was downgraded below the number count: kept, but cannot send
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class StripeEvent(Base):
    """Every processed Stripe webhook event id, so a retried delivery is acknowledged but never applied twice."""

    __tablename__ = "stripe_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    type: Mapped[str] = mapped_column(String(80))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")


class ApiKey(Base):
    """Programmatic access. Only a SHA-256 hash is stored; the plaintext is shown once at creation."""

    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    instance_id: Mapped[str | None] = mapped_column(ForeignKey("instances.id", ondelete="CASCADE"), nullable=True)  # null = all numbers
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))  # shown in the UI, e.g. cf_live_ab12
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # null = never expires
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_reason: Mapped[str] = mapped_column(String(200), default="", server_default="")
    suspended_by: Mapped[str] = mapped_column(String(10), default="", server_default="")  # system | admin
    allowed_ips: Mapped[str] = mapped_column(Text, default="", server_default="")  # comma separated IPs/CIDRs; empty = any


class UsageEvent(Base):
    """One row per billable/limited action (currently: a message accepted for sending)."""

    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_tenant_kind_at", "tenant_id", "kind", "at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    api_key_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(24), default="message")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[str] = mapped_column(Text)  # signs every delivery; stored encrypted (enc1:...), shown to the customer once
    events: Mapped[str] = mapped_column(String(200), default="message.received,instance.status")
    description: Mapped[str] = mapped_column(String(120), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(40))
    payload: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending | retrying | success | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    last_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invited_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailToken(Base):
    """Single-use tokens for email verification and password reset (only the hash is stored)."""

    __tablename__ = "email_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # verify | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UpgradeRequest(Base):
    __tablename__ = "upgrade_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    note: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LeadList(Base):
    __tablename__ = "lead_lists"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(20), default="csv")  # csv | manual
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (Index("uq_lead_whatsapp", "tenant_id", "whatsapp", unique=True, postgresql_where=text("whatsapp <> ''")),
                      Index("ix_leads_followup", "tenant_id", "status", "followup_stage", "sent_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    list_id: Mapped[str | None] = mapped_column(ForeignKey("lead_lists.id", ondelete="SET NULL"), nullable=True, index=True)
    company: Mapped[str] = mapped_column(String(200))
    contact_name: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(200), default="")
    business_type: Mapped[str] = mapped_column(String(120), default="")
    address: Mapped[str] = mapped_column(String(300), default="")
    phone_raw: Mapped[str] = mapped_column(String(200), default="")
    whatsapp: Mapped[str] = mapped_column(String(20), default="")  # E.164 digits, no plus; '' = no valid number
    country: Mapped[str] = mapped_column(String(2), default="")
    website: Mapped[str] = mapped_column(String(300), default="")
    email: Mapped[str] = mapped_column(String(254), default="")
    linkedin: Mapped[str] = mapped_column(String(300), default="")
    facebook: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)  # new|queued|contacted|replied|stop|no_whatsapp|failed|hold|verify
    research: Mapped[str] = mapped_column(Text, default="")
    first_message: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reply: Mapped[str] = mapped_column(Text, default="")
    followup_stage: Mapped[int] = mapped_column(Integer, default=0)
    instance_id: Mapped[str | None] = mapped_column(ForeignKey("instances.id", ondelete="SET NULL"), nullable=True)
    enrichment: Mapped[str] = mapped_column(Text, default="", server_default="")
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class OutreachProfile(Base):
    """Per-workspace settings that replace the hard-coded One Group values of the legacy tool."""

    __tablename__ = "outreach_profiles"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    company_name: Mapped[str] = mapped_column(String(120), default="")
    sender_name: Mapped[str] = mapped_column(String(80), default="")
    website: Mapped[str] = mapped_column(String(300), default="")
    offer: Mapped[str] = mapped_column(Text, default="")  # what you sell, in a few sentences
    tone: Mapped[str] = mapped_column(String(20), default="friendly")  # friendly | formal | short
    knowledge: Mapped[str] = mapped_column(Text, default="")  # facts the AI may use: services, pricing rules, proof points
    default_country: Mapped[str] = mapped_column(String(2), default="PK")
    timezone: Mapped[str] = mapped_column(String(40), default="UTC")
    active_from: Mapped[str] = mapped_column(String(5), default="09:00")
    active_to: Mapped[str] = mapped_column(String(5), default="20:00")
    daily_limit: Mapped[int] = mapped_column(Integer, default=15)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_run: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    batch_size: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    interval_hours: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    delay_min: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    delay_max: Mapped[int] = mapped_column(Integer, default=180, server_default="180")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    auto_reply: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    hold_pricing: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    followups_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    followup_day1: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    followup_day2: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    fallback_template: Mapped[str] = mapped_column(Text, default="", server_default="")
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notify_extra_emails: Mapped[str] = mapped_column(String(600), default="", server_default="")  # comma separated, max 3
    notify_digest: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    media: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'::json"))  # what to attach: firstMessage / followup1 / followup2 / replies (see app/assetlib.py)


class Draft(Base):
    """A message waiting for approval / sending (kind first | followup | reply)."""

    __tablename__ = "drafts"
    __table_args__ = (Index("ix_drafts_tenant_status", "tenant_id", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="first")
    body: Mapped[str] = mapped_column(Text)
    incoming: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|approved|sending|sent|discarded|failed
    note: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai | manual | fallback
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False)
    instruction: Mapped[str] = mapped_column(String(300), default="")
    batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    sending_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Asset(Base):
    """A file (image, PDF, short video) or a link in the workspace's media library. Bytes live in the storage backend, never in Postgres."""

    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(12))  # image | document | video | link
    label: Mapped[str] = mapped_column(String(120), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'::json"))  # services | pricing | portfolio | trust | booking | ...
    mime: Mapped[str] = mapped_column(String(80), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    storage_backend: Mapped[str] = mapped_column(String(12), default="")  # local | cloudinary ('' for links)
    storage_key: Mapped[str] = mapped_column(String(300), default="")
    thumb_key: Mapped[str] = mapped_column(String(300), default="")
    link_url: Mapped[str] = mapped_column(String(500), default="")
    link_title: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AssetVariant(Base):
    """Up to 5 alternative files of one asset; the sender rotates through them so identical bytes are not sent to hundreds of strangers."""

    __tablename__ = "asset_variants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    storage_backend: Mapped[str] = mapped_column(String(12), default="")
    storage_key: Mapped[str] = mapped_column(String(300), default="")
    mime: Mapped[str] = mapped_column(String(80), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DraftAsset(Base):
    """What a draft will carry when it is sent (files and links, in order)."""

    __tablename__ = "draft_assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("drafts.id", ondelete="CASCADE"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)


class Message(Base):
    """The conversation log. Content is visible to the customer only (admins get counts)."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_tenant_lead", "tenant_id", "lead_id"), Index("ix_messages_tenant_chat", "tenant_id", "chat_id"),
                      Index("ix_messages_tenant_dir_kind_at", "tenant_id", "direction", "kind", "created_at"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    lead_id: Mapped[str | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), nullable=True)
    instance_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chat_id: Mapped[str] = mapped_column(String(80), default="")
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(10), default="first")  # first | followup | reply
    media_kind: Mapped[str | None] = mapped_column(String(12), nullable=True)  # image | video | document | voice | sticker | media (null = plain text)
    asset_ids: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'::json"))  # [{"assetId", "variantId"}] actually sent with this message
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger: Mapped[str] = mapped_column(String(10), default="manual")  # timer | manual
    mode: Mapped[str] = mapped_column(String(12), default="approval")  # dry | approval | live
    processed: Mapped[int] = mapped_column(Integer, default=0)
    queued: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(300), default="")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(14))  # batch|reply|needs_human|failed|session|info
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(500), default="")
    link: Mapped[str] = mapped_column(String(60), default="")
    lead_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ApiKeyUsage(Base):
    """Per key per UTC day: request counters and where the key was used from (flushed from Redis by app/telemetry.py)."""

    __tablename__ = "api_key_usage"
    __table_args__ = (UniqueConstraint("key_id", "day"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    ips: Mapped[dict] = mapped_column(JSON, default=dict)  # ip -> first seen (ISO), capped at 50
    last_ip: Mapped[str] = mapped_column(String(64), default="")
    last_user_agent: Mapped[str] = mapped_column(String(200), default="")
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecurityEvent(Base):
    """Suspicious activity found by the detection rules, or raised by an admin. Shown on the admin Security page."""

    __tablename__ = "security_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    key_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String(8), default="info", index=True)  # info | warn | critical
    kind: Mapped[str] = mapped_column(String(32), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

class AiUsageDaily(Base):
    """Per workspace per UTC day: AI calls, provider retries, tokens and an ESTIMATED cost (price from AI_PRICE_* env; 0 = unknown).
    Never holds prompts or message text. Upserted atomically by app/ai.py."""

    __tablename__ = "ai_usage_daily"
    __table_args__ = (Index("ix_ai_usage_daily_day", "day"),)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens_cached: Mapped[int] = mapped_column(BigInteger, default=0)
    est_cost_micro_usd: Mapped[int] = mapped_column(BigInteger, default=0)
