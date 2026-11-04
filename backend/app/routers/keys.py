"""Developer API keys: create (shown once), list, revoke, permanently delete, IP allow-list. Only a SHA-256 hash and a prefix are stored."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Ctx, need_role, require_verified
from ..models import ApiKey, ApiKeyUsage, AuditLog, Instance
from ..telemetry import parse_allowed, reset_known_ips
from ..quota import last_days, messages_today

router = APIRouter(prefix="/api", tags=["keys"])


EXPIRY = {"1h": timedelta(hours=1), "1d": timedelta(days=1), "7d": timedelta(days=7), "30d": timedelta(days=30), "90d": timedelta(days=90), "1y": timedelta(days=365), "never": None}


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    instance_id: str | None = None
    expires_in: str = "90d"  # 1h | 1d | 7d | 30d | 90d | 1y | never
    allowed_ips: str = ""  # comma separated IPv4/IPv6/CIDR; empty = any


class KeyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    allowed_ips: str | None = None


def _clean_ips(raw: str) -> str:
    try:
        nets = parse_allowed(raw)
    except ValueError as e:
        raise HTTPException(422, f"Invalid IP or CIDR in allow-list: {e}")
    if len(nets) > 50:
        raise HTTPException(422, "At most 50 allow-list entries.")
    return ",".join(str(n) for n in nets)


def hash_key(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


def _status(k: ApiKey) -> str:
    if k.revoked_at:
        return "revoked"
    if k.suspended_at:
        return "suspended"
    if k.expires_at and k.expires_at < datetime.now(timezone.utc):
        return "expired"
    return "active"


def _out(k: ApiKey, u: ApiKeyUsage | None = None) -> dict:
    return {
        "status": _status(k), "allowedIps": k.allowed_ips or "",
        "suspendedAt": k.suspended_at.isoformat() if k.suspended_at else None,
        "suspendedReason": k.suspended_reason or "", "suspendedBy": k.suspended_by or "",
        "lastIp": u.last_ip if u else "", "lastUserAgent": u.last_user_agent if u else "",
        "requestsToday": u.requests if u else 0,
        "id": k.id, "name": k.name, "prefix": k.prefix, "instanceId": k.instance_id,
        "createdAt": k.created_at.isoformat(),
        "lastUsedAt": k.last_used_at.isoformat() if k.last_used_at else None,
        "revoked": bool(k.revoked_at),
        "expiresAt": k.expires_at.isoformat() if k.expires_at else None,
        "expired": bool(k.expires_at and k.expires_at < datetime.now(timezone.utc)),
    }


@router.get("/keys")
def list_keys(ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    rows = db.query(ApiKey).filter(ApiKey.tenant_id == ctx.tenant.id).order_by(ApiKey.created_at.desc()).all()
    today = {u.key_id: u for u in db.query(ApiKeyUsage).filter(ApiKeyUsage.tenant_id == ctx.tenant.id, ApiKeyUsage.day == datetime.now(timezone.utc).date()).all()}
    # last IP: today's row, else the latest earlier one
    last = {}
    for u in db.query(ApiKeyUsage).filter(ApiKeyUsage.tenant_id == ctx.tenant.id).order_by(ApiKeyUsage.day.asc()).all():
        last[u.key_id] = u
    out = []
    for k in rows:
        o = _out(k, last.get(k.id))
        o["requestsToday"] = today[k.id].requests if k.id in today else 0
        out.append(o)
    return out


@router.post("/keys", status_code=201)
def create_key(body: KeyIn, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    require_verified(ctx)
    if body.instance_id and not db.query(Instance).filter(Instance.id == body.instance_id, Instance.tenant_id == ctx.tenant.id).first():
        raise HTTPException(404, "instance not found")
    if db.query(ApiKey).filter(ApiKey.tenant_id == ctx.tenant.id, ApiKey.revoked_at.is_(None)).count() >= 20:
        raise HTTPException(409, "You already have 20 active keys. Revoke one first.")
    if body.expires_in not in EXPIRY:
        raise HTTPException(422, "expires_in must be one of: " + ", ".join(EXPIRY))
    delta = EXPIRY[body.expires_in]
    ips = _clean_ips(body.allowed_ips)
    plain = "cf_live_" + secrets.token_urlsafe(32)
    k = ApiKey(tenant_id=ctx.tenant.id, instance_id=body.instance_id, name=body.name.strip(), prefix=plain[:12], key_hash=hash_key(plain), created_by=ctx.user.id,
               expires_at=(datetime.now(timezone.utc) + delta) if delta else None, allowed_ips=ips)
    db.add(k)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="apikey.create", detail=k.name))
    db.commit()
    return {**_out(k), "key": plain}  # the only time the plaintext is ever returned


def _own(db: Session, ctx: Ctx, key_id: str) -> ApiKey:
    k = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.tenant_id == ctx.tenant.id).first()
    if not k:
        raise HTTPException(404, "key not found")
    return k


@router.patch("/keys/{key_id}")
def patch_key(key_id: str, body: KeyPatch, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    k = _own(db, ctx, key_id)
    if body.name is not None:
        k.name = body.name.strip()
    if body.allowed_ips is not None:
        k.allowed_ips = _clean_ips(body.allowed_ips)
        db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="apikey.allowlist", detail=f"{k.name}: {k.allowed_ips or 'any IP'}"[:500]))
    db.commit()
    return _out(k)


@router.post("/keys/{key_id}/unsuspend")
def unsuspend_key(key_id: str, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    k = _own(db, ctx, key_id)
    if not k.suspended_at:
        raise HTTPException(409, "This key is not suspended.")
    if k.suspended_by == "admin":
        raise HTTPException(403, "This key was suspended by the platform team. Contact support to restore it.")
    k.suspended_at, k.suspended_reason, k.suspended_by = None, "", ""
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="apikey.unsuspend", detail=k.name))
    db.commit()
    reset_known_ips(k.id)
    return _out(k)


@router.post("/keys/{key_id}/rotate", status_code=201)
def rotate_key(key_id: str, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    """New key with the same name/instance/allow-list/lifetime; the old one is revoked at once. Plaintext is shown once."""
    require_verified(ctx)
    old = _own(db, ctx, key_id)
    if old.revoked_at:
        raise HTTPException(409, "This key is already revoked.")
    life = (old.expires_at - old.created_at) if old.expires_at else None
    plain = "cf_live_" + secrets.token_urlsafe(32)
    new = ApiKey(tenant_id=ctx.tenant.id, instance_id=old.instance_id, name=old.name, prefix=plain[:12], key_hash=hash_key(plain), created_by=ctx.user.id,
                 expires_at=(datetime.now(timezone.utc) + life) if life else None, allowed_ips=old.allowed_ips)
    old.revoked_at = datetime.now(timezone.utc)
    db.add(new)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="apikey.rotate", detail=old.name))
    db.commit()
    return {**_out(new), "key": plain}


@router.delete("/keys/{key_id}", status_code=204)
def revoke_key(key_id: str, permanent: bool = False, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    """Default: revoke (the key stops working but stays in the list). permanent=true: remove it for good."""
    k = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.tenant_id == ctx.tenant.id).first()
    if not k:
        raise HTTPException(404, "key not found")
    if permanent:
        db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="apikey.delete", detail=k.name))
        db.delete(k)
    else:
        k.revoked_at = datetime.now(timezone.utc)
        db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="apikey.revoke", detail=k.name))
    db.commit()


@router.get("/usage")
def usage(ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    return {"today": messages_today(db, ctx.tenant.id), "limit": ctx.tenant.plan.max_msgs_day, "plan": ctx.tenant.plan.name, "days": last_days(db, ctx.tenant.id)}
