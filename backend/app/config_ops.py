"""Operational settings (capacity, reaper, retention, observability). All values come from the environment."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class OpsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://redis:6379/0"

    # ---- capacity guard (platform-wide cap on live WhatsApp sessions; keep <= the gateway's own GATEWAY_MAX_SESSIONS)
    platform_max_instances: int = 20
    gateway_max_sessions: int = 20  # mirrors MAX_CONCURRENT_SESSIONS on the gateway; shown in /api/admin/capacity

    # ---- session reaper + reconciler
    reaper_enabled: bool = True
    reaper_dry_run: bool = False
    reap_unscanned_minutes: int = 10      # a session that was never linked (QR not scanned) is deleted after this long
    reap_trial_idle_days: int = 7         # trial-plan sessions with no message sent for this long are stopped (0 = off)
    reaper_interval_seconds: int = 60
    reconcile_interval_seconds: int = 300
    reconcile_grace_seconds: int = 180    # never touch rows/sessions younger than this (creation is not atomic)

    # ---- retention (days / months)
    retention_enabled: bool = True
    retention_hour_utc: int = 3           # quiet hour for the daily run
    retention_webhook_days: int = 30
    retention_webhook_payload_days: int = 7
    retention_notification_days: int = 90
    retention_audit_months: int = 24
    retention_usage_months: int = 13
    retention_ai_usage_months: int = 13
    retention_security_months: int = 13
    retention_draft_days: int = 180
    retention_batch_months: int = 12
    retention_batch_size: int = 5000

    # ---- observability
    sentry_dsn: str = ""
    log_json: bool = True
    log_level: str = "INFO"
    environment: str = "local"


ops = OpsSettings()
