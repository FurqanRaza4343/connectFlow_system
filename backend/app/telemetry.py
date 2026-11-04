"""API-key telemetry, leak detection and the pre-auth limiter for the public API.

Design (all of it fails OPEN: if Redis is down the public API keeps working, we just lose telemetry/limits):
- Per authenticated /v1 request we update Redis counters (cheap, no DB round trip): requests/errors per key per day, a rolling
  7-day ZSET of the IPs a key was used from (score = first seen), an hourly request counter, last IP / user agent.
- Detection rules run on every request using those counters; a 60 s task flushes the counters to `api_key_usage`.
- Every hit becomes a `security_events` row (deduplicated). Critical hits auto-suspend the key, e-mail the workspace owner and
  write an audit row. The same incident is never reported twice inside its dedupe window.
- `v1_guard` is a middleware that runs BEFORE the key lookup: per-IP request limit and a stricter bucket for repeated 401s.
"""
import asyncio
import ipaddress
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from .config import settings
from .db import SessionLocal
from .deps import rds
from .mailer import layout, send_email
from .models import ApiKey, ApiKeyUsage, AuditLog, Membership, OutreachProfile, SecurityEvent, Tenant, User

log = logging.getLogger("telemetry")
IP_SET_MAX = 50
KNOWN_WINDOW = 7 * 86400
DAY = 86400


def client_ip(request: Request) -> str:
    """The proxy (Caddy) overwrites X-Forwarded-For with the real peer, so the left-most value is trustworthy."""
    xff = request.headers.get("x-forwarded-for", "")
    return (xff.split(",")[0].strip() if xff else "") or (request.client.host if request.client else "?")


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - Redis down must never break the API
        return default


# ------------------------------------------------------------------ security events
def record_event(severity: str, kind: str, detail: str, tenant_id: str | None = None, key_id: str | None = None, dedupe: str | None = None, ttl: int = 3600, db: Session | None = None) -> SecurityEvent | None:
    """Store a security event once per dedupe window. Uses its own DB session unless one is passed."""
    if dedupe and _safe(lambda: rds.set(f"secd:{dedupe}", 1, nx=True, ex=ttl), "redis-down") is None:
        return None  # SET NX answered None: already reported inside the window (if Redis is down we report anyway)
    own = db is None
    s = db or SessionLocal()
    try:
        ev = SecurityEvent(tenant_id=tenant_id, key_id=key_id, severity=severity, kind=kind, detail=detail[:2000])
        s.add(ev)
        s.commit()
        return ev
    finally:
        if own:
            s.close()


# ------------------------------------------------------------------ owner e-mail
def _notify_owner(email: str, subject: str, body_html: str, text: str) -> None:
    def run():
        try:
            asyncio.run(send_email(email, subject, text, layout(subject, body_html)))
        except Exception:  # noqa: BLE001
            log.warning("owner notification failed", exc_info=True)

    threading.Thread(target=run, daemon=True).start()


def owner_email(db: Session, tenant_id: str) -> str | None:
    row = db.query(User.email).join(Membership, Membership.user_id == User.id).filter(Membership.tenant_id == tenant_id, Membership.role == "owner").first()
    return row[0] if row else None


# ------------------------------------------------------------------ suspension
def suspend_key(db: Session, key: ApiKey, reason: str, kind: str, by: str = "system", user_id: str | None = None) -> bool:
    """Idempotent: returns True only for the call that actually suspended the key (so the e-mail is sent once)."""
    if key.suspended_at:
        return False
    key.suspended_at = datetime.now(timezone.utc)
    key.suspended_reason, key.suspended_by = reason[:200], by
    db.add(SecurityEvent(tenant_id=key.tenant_id, key_id=key.id, severity="critical", kind=kind, detail=f"Key '{key.name}' ({key.prefix}…) suspended: {reason}"))
    db.add(AuditLog(tenant_id=key.tenant_id, user_id=user_id, action="apikey.suspended", detail=f"{key.name}: {reason}"[:500]))
    db.commit()
    mail = owner_email(db, key.tenant_id)
    if mail and by == "system":
        _notify_owner(
            mail, "An API key was suspended automatically",
            f"We suspended the API key <b>{key.name}</b> ({key.prefix}…) because we saw unusual activity: {reason}.<br><br>"
            "If this was you, open <b>API &amp; usage</b> in your dashboard, check where the key is used, and unsuspend it. "
            "If you do not recognise the activity, delete the key and create a new one: it may have leaked.",
            f"API key '{key.name}' ({key.prefix}...) was suspended: {reason}. Open API & usage to review it.",
        )
    return True


def reset_known_ips(key_id: str) -> None:
    """After an owner/admin unsuspends a key the 'IPs seen' window starts again, otherwise the same key would be suspended on its next call."""
    _safe(lambda: rds.delete(f"ki:{key_id}"))


# ------------------------------------------------------------------ allow-list
def parse_allowed(raw: str) -> list:
    out = []
    for part in (raw or "").replace("\n", ",").split(","):
        part = part.strip()
        if part:
            out.append(ipaddress.ip_network(part, strict=False))
    return out


def ip_allowed(raw: str, ip: str) -> bool:
    nets = parse_allowed(raw)
    if not nets:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in nets)


# ------------------------------------------------------------------ per-request recording + rules
def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def baseline_daily(key_id: str) -> float:
    """Average requests/day over the previous 7 days (cached 10 min). New keys have no history -> 0."""
    cached = _safe(lambda: rds.get(f"kb:{key_id}"))
    if cached is not None:
        return float(cached)
    with SessionLocal() as db:
        since = date.today() - timedelta(days=7)
        total = db.query(func.coalesce(func.sum(ApiKeyUsage.requests), 0)).filter(ApiKeyUsage.key_id == key_id, ApiKeyUsage.day >= since, ApiKeyUsage.day < date.today()).scalar() or 0
    avg = total / 7
    _safe(lambda: rds.set(f"kb:{key_id}", avg, ex=600))
    return avg


def record_use(db: Session, key: ApiKey, ip: str, user_agent: str) -> str | None:
    """Update counters and evaluate the rules for ONE successful authentication. Returns a reason string when the key must be
    refused now (it has just been auto-suspended), else None. Never raises."""
    try:
        now = time.time()
        day, hour = _today(), datetime.now(timezone.utc).strftime("%Y%m%d%H")
        kt, ki, kh = f"kt:{key.id}:{day}", f"ki:{key.id}", f"kh:{key.id}:{hour}"
        known_before = rds.zcard(ki)
        pipe = rds.pipeline()
        pipe.hincrby(kt, "req", 1)
        pipe.hset(kt, mapping={"ip": ip, "ua": (user_agent or "")[:200], "at": int(now), "tenant": key.tenant_id})
        pipe.expire(kt, 3 * DAY)
        pipe.sadd("kt:active", f"{key.id}:{day}")
        pipe.zadd(ki, {ip: now}, nx=True)
        pipe.zremrangebyscore(ki, "-inf", now - KNOWN_WINDOW)
        pipe.zremrangebyrank(ki, 0, -(IP_SET_MAX + 1))
        pipe.expire(ki, KNOWN_WINDOW + DAY)
        pipe.zcount(ki, now - DAY, "+inf")
        pipe.incr(kh)
        pipe.expire(kh, 7200)
        pipe.hsetnx(f"kdi:{key.id}:{day}", ip, int(now))  # distinct IPs per day: survives an unsuspend (which resets the 24h window)
        pipe.expire(f"kdi:{key.id}:{day}", 3 * DAY)
        res = pipe.execute()
        added, ips24, hour_count = res[4], res[8], res[9]

        if ips24 > settings.sec_many_ips_24h:
            reason = f"used from {ips24} different IP addresses in 24 hours"
            if suspend_key(db, key, reason, "many_ips"):
                return reason
            return reason
        if added and known_before >= settings.sec_known_ips_before_alert:
            record_event("warn", "new_ip", f"Key '{key.name}' ({key.prefix}…) was used from a new IP {ip} (it already had {known_before} known IPs)", key.tenant_id, key.id, dedupe=f"newip:{key.id}:{ip}", ttl=DAY, db=db)
        if hour_count > settings.sec_volume_floor:
            base = baseline_daily(key.id)
            warn_at = max(settings.sec_volume_floor, settings.sec_volume_mult * base)
            crit_at = max(settings.sec_volume_critical_floor, settings.sec_volume_mult * base)
            if hour_count > crit_at:
                reason = f"{hour_count} requests in one hour (normally ~{int(base)} per day)"
                suspend_key(db, key, reason, "volume_spike")
                return reason
            if hour_count > warn_at:
                record_event("warn", "volume_spike", f"Key '{key.name}' made {hour_count} requests this hour (normal: ~{int(base)} per day)", key.tenant_id, key.id, dedupe=f"vol:{key.id}:{hour}", ttl=3600, db=db)
    except Exception:  # noqa: BLE001
        log.debug("telemetry skipped", exc_info=True)
    return None


def count_error(key_id: str) -> None:
    _safe(lambda: rds.hincrby(f"kt:{key_id}:{_today()}", "err", 1))


def check_active_hours(db: Session, key: ApiKey) -> None:
    """Informational: a send through the API outside the workspace's configured active hours."""
    try:
        p = db.get(OutreachProfile, key.tenant_id)
        if not p:
            return
        local = datetime.now(ZoneInfo(p.timezone or "UTC")).strftime("%H:%M")
        inside = p.active_from <= local <= p.active_to if p.active_from <= p.active_to else (local >= p.active_from or local <= p.active_to)
        if not inside:
            record_event("info", "off_hours_send", f"Key '{key.name}' sent a message at {local} ({p.timezone}), outside the active hours {p.active_from}-{p.active_to}", key.tenant_id, key.id, dedupe=f"offh:{key.id}", ttl=6 * 3600, db=db)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ pre-auth limiter (middleware)
def _pre_auth(ip: str) -> int:
    """0 = allowed, else seconds until the caller may retry."""
    fails = _safe(lambda: int(rds.get(f"v1fail:{ip}") or 0), 0)
    if fails >= settings.sec_fail_limit_5min:
        return max(1, _safe(lambda: rds.ttl(f"v1fail:{ip}"), 60) or 60)
    n = _safe(lambda: rds.incr(f"v1ip:{ip}"), 0)
    if n == 1:
        _safe(lambda: rds.expire(f"v1ip:{ip}", 60))
    if n > settings.sec_ip_rate_per_min:
        return max(1, _safe(lambda: rds.ttl(f"v1ip:{ip}"), 60) or 60)
    return 0


def _count_fail(ip: str) -> None:
    n = _safe(lambda: rds.incr(f"v1fail:{ip}"), 0)
    if n == 1:
        _safe(lambda: rds.expire(f"v1fail:{ip}", 300))
    if n == settings.sec_fail_limit_5min:
        record_event("warn", "auth_storm", f"{n} failed API-key attempts from {ip} within 5 minutes; this address is blocked for a few minutes", None, None, dedupe=f"storm:{ip}", ttl=900)


async def v1_guard(request: Request, call_next):
    if not request.url.path.startswith("/v1/"):
        return await call_next(request)
    ip = client_ip(request)
    wait = _pre_auth(ip)
    if wait:
        return JSONResponse({"detail": "Too many requests from this address. Slow down and retry later."}, status_code=429, headers={"Retry-After": str(wait)})
    resp = await call_next(request)
    if resp.status_code == 401:
        _count_fail(ip)
    kid = getattr(request.state, "key_id", None)
    if kid and resp.status_code >= 400:
        count_error(kid)
    return resp


# ------------------------------------------------------------------ flush Redis counters -> api_key_usage
def flush_to_db() -> int:
    n = 0
    members = _safe(lambda: list(rds.smembers("kt:active")), []) or []
    if not members:
        return 0
    today = date.today()
    with SessionLocal() as db:
        for m in members:
            key_id, _, day = m.partition(":")
            try:
                d = datetime.strptime(day, "%Y%m%d").date()
            except ValueError:
                _safe(lambda: rds.srem("kt:active", m))
                continue
            h = _safe(lambda: rds.hgetall(f"kt:{key_id}:{day}"), {}) or {}
            if not h:
                _safe(lambda: rds.srem("kt:active", m))
                continue
            seen = _safe(lambda: rds.hgetall(f"kdi:{key_id}:{day}"), {}) or {}
            ips = {ip: datetime.fromtimestamp(int(sc), timezone.utc).isoformat() for ip, sc in list(seen.items())[:IP_SET_MAX]}
            row = db.query(ApiKeyUsage).filter(ApiKeyUsage.key_id == key_id, ApiKeyUsage.day == d).first()
            if not row:
                tenant = h.get("tenant") or (db.query(ApiKey.tenant_id).filter(ApiKey.id == key_id).scalar())
                if not tenant or not db.get(ApiKey, key_id):
                    _safe(lambda: rds.srem("kt:active", m))  # key was deleted
                    continue
                row = ApiKeyUsage(key_id=key_id, tenant_id=tenant, day=d)
                db.add(row)
            row.requests, row.errors = int(h.get("req", 0)), int(h.get("err", 0))
            row.ips, row.last_ip, row.last_user_agent = ips, h.get("ip", ""), h.get("ua", "")
            row.last_at = datetime.fromtimestamp(int(h.get("at", time.time())), timezone.utc)
            n += 1
            if d < today - timedelta(days=1):
                _safe(lambda: rds.srem("kt:active", m))
        db.commit()
    return n


async def sweeper() -> None:
    """Runs in the api process: flush counters every 60 s (a Redis lock keeps several replicas from doing it twice)."""
    while True:
        await asyncio.sleep(60)
        try:
            if _safe(lambda: rds.set("sec:sweep", 1, nx=True, ex=55)):
                await asyncio.to_thread(flush_to_db)
        except Exception:  # noqa: BLE001
            log.warning("telemetry flush failed", exc_info=True)
