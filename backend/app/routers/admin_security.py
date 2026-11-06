"""Platform-admin Security API: events raised by the detection rules (app/telemetry.py), flagged keys, kill switches.
Admins see key names, prefixes, IPs and counters. They never see message content or full keys (only a hash is stored)."""
from typing import Any
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Ctx, platform_admin, rds
from ..models import ApiKey, ApiKeyUsage, AuditLog, Membership, SecurityEvent, Tenant, User
from ..telemetry import reset_known_ips, suspend_key
from ..sqlutil import rows_to_dict

router = APIRouter(prefix="/api/admin/security", tags=["admin-security"])


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _event(e: SecurityEvent, tenant: str | None, key: ApiKey | None) -> dict:
    return {"id": e.id, "at": _iso(e.at), "severity": e.severity, "kind": e.kind, "detail": e.detail, "resolved": e.resolved,
            "tenantId": e.tenant_id, "tenant": tenant, "keyId": e.key_id, "keyName": key.name if key else None, "keyPrefix": key.prefix if key else None,
            "keySuspended": bool(key and key.suspended_at)}


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: Ctx = Depends(platform_admin)) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    sev = rows_to_dict(db.query(SecurityEvent.severity, func.count(SecurityEvent.id)).filter(SecurityEvent.at >= since).group_by(SecurityEvent.severity).all())
    open_ = rows_to_dict(db.query(SecurityEvent.severity, func.count(SecurityEvent.id)).filter(SecurityEvent.resolved.is_(False)).group_by(SecurityEvent.severity).all())
    flagged_ids = {k for (k,) in db.query(SecurityEvent.key_id).filter(SecurityEvent.resolved.is_(False), SecurityEvent.key_id.isnot(None), SecurityEvent.severity.in_(("warn", "critical"))).distinct().all()}
    flagged_ids |= {k for (k,) in db.query(ApiKey.id).filter(ApiKey.suspended_at.isnot(None), ApiKey.revoked_at.is_(None)).all()}
    keys = db.query(ApiKey, Tenant.name).join(Tenant, Tenant.id == ApiKey.tenant_id).filter(ApiKey.id.in_(flagged_ids or ["-"])).all()
    use = {u.key_id: u for u in db.query(ApiKeyUsage).filter(ApiKeyUsage.key_id.in_(flagged_ids or ["-"]), ApiKeyUsage.day == now.date()).all()}
    flagged = [{"id": k.id, "name": k.name, "prefix": k.prefix, "tenantId": k.tenant_id, "tenant": tn, "suspended": bool(k.suspended_at), "suspendedReason": k.suspended_reason or "",
                "suspendedBy": k.suspended_by or "", "lastIp": use[k.id].last_ip if k.id in use else "", "requestsToday": use[k.id].requests if k.id in use else 0,
                "distinctIpsToday": len(use[k.id].ips or {}) if k.id in use else 0} for k, tn in keys]
    top = (db.query(ApiKeyUsage, ApiKey.name, ApiKey.prefix, Tenant.name).join(ApiKey, ApiKey.id == ApiKeyUsage.key_id).join(Tenant, Tenant.id == ApiKeyUsage.tenant_id)
           .filter(ApiKeyUsage.day == now.date()).order_by(ApiKeyUsage.requests.desc()).limit(10).all())
    storms = []
    try:  # live failed-auth counters per IP (Redis); empty when Redis is down
        for k in rds.scan_iter("v1fail:*", count=200):
            n = int(rds.get(k) or 0)
            if n >= 5:
                storms.append({"ip": k.split(":", 1)[1], "failures": n, "retryIn": max(0, rds.ttl(k) or 0)})
        storms.sort(key=lambda x: -x["failures"])
    except Exception:  # noqa: BLE001
        pass
    return {
        "events24h": {"critical": sev.get("critical", 0), "warn": sev.get("warn", 0), "info": sev.get("info", 0)},
        "open": {"critical": open_.get("critical", 0), "warn": open_.get("warn", 0), "info": open_.get("info", 0)},
        "suspendedKeys": db.query(ApiKey).filter(ApiKey.suspended_at.isnot(None), ApiKey.revoked_at.is_(None)).count(),
        "flaggedKeys": flagged,
        "topKeys": [{"keyId": u.key_id, "name": n, "prefix": pf, "tenant": tn, "requests": u.requests, "errors": u.errors, "distinctIps": len(u.ips or {}), "lastIp": u.last_ip} for u, n, pf, tn in top],
        "authStorms": storms[:20],
    }


@router.get("/events")
def events(severity: str = "", kind: str = "", resolved: str = "", tenant_id: str = "", key_id: str = "", page: int = Query(1, ge=1),
           db: Session = Depends(get_db), _: Ctx = Depends(platform_admin)) -> dict[str, Any]:
    base = db.query(SecurityEvent)
    if severity:
        base = base.filter(SecurityEvent.severity == severity)
    if kind:
        base = base.filter(SecurityEvent.kind == kind)
    if resolved in ("true", "false"):
        base = base.filter(SecurityEvent.resolved.is_(resolved == "true"))
    if tenant_id:
        base = base.filter(SecurityEvent.tenant_id == tenant_id)
    if key_id:
        base = base.filter(SecurityEvent.key_id == key_id)
    total, size = base.count(), 30
    rows = base.order_by(SecurityEvent.id.desc()).offset((page - 1) * size).limit(size).all()
    tn = {t.id: t.name for t in db.query(Tenant).filter(Tenant.id.in_({e.tenant_id for e in rows if e.tenant_id} or ["-"])).all()}
    ks = {k.id: k for k in db.query(ApiKey).filter(ApiKey.id.in_({e.key_id for e in rows if e.key_id} or ["-"])).all()}
    return {"rows": [_event(e, tn.get(e.tenant_id or ""), ks.get(e.key_id or "")) for e in rows], "total": total, "page": page, "pages": max(1, -(-total // size)),
            "kinds": [k for (k,) in db.query(SecurityEvent.kind).distinct().order_by(SecurityEvent.kind).all()]}


@router.post("/events/{event_id}/resolve")
def resolve(event_id: int, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    e = db.get(SecurityEvent, event_id)
    if not e:
        raise HTTPException(404, "event not found")
    if not e.resolved:
        e.resolved = True
        db.add(AuditLog(tenant_id=e.tenant_id, user_id=ctx.user.id, action="admin.security.resolve", detail=f"{e.kind} #{e.id}"))
        db.commit()
    return {"ok": True}


class SuspendIn(BaseModel):
    reason: str = Field(default="Suspended by the platform team", max_length=200)


@router.post("/keys/{key_id}/suspend")
def suspend(key_id: str, body: SuspendIn | None = None, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    k = db.get(ApiKey, key_id)
    if not k:
        raise HTTPException(404, "key not found")
    reason = (body.reason if body else "Suspended by the platform team").strip() or "Suspended by the platform team"
    changed = suspend_key(db, k, reason, "admin_suspend", by="admin", user_id=ctx.user.id)
    return {"ok": True, "changed": changed}


@router.post("/keys/{key_id}/unsuspend")
def unsuspend(key_id: str, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    k = db.get(ApiKey, key_id)
    if not k:
        raise HTTPException(404, "key not found")
    if k.suspended_at:
        k.suspended_at, k.suspended_reason, k.suspended_by = None, "", ""
        db.add(AuditLog(tenant_id=k.tenant_id, user_id=ctx.user.id, action="admin.apikey.unsuspend", detail=k.name))
        db.add(SecurityEvent(tenant_id=k.tenant_id, key_id=k.id, severity="info", kind="admin_unsuspend", detail=f"Key '{k.name}' ({k.prefix}…) was unsuspended by the platform team", resolved=True))
        db.commit()
        reset_known_ips(k.id)
    return {"ok": True}


@router.post("/tenants/{tenant_id}/lock")
def lock_tenant(tenant_id: str, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Kill switch for a workspace: suspend it (API + app access stop) and suspend all its live keys. Re-activate it from the Customers page."""
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "customer not found")
    if db.query(Membership).join(User, User.id == Membership.user_id).filter(Membership.tenant_id == t.id, User.is_platform_admin.is_(True)).first():
        raise HTTPException(409, "This workspace belongs to a platform admin and cannot be locked")
    n = 0
    for k in db.query(ApiKey).filter(ApiKey.tenant_id == t.id, ApiKey.revoked_at.is_(None)).all():
        n += 1 if suspend_key(db, k, "Workspace locked by the platform team", "tenant_lock", by="admin", user_id=ctx.user.id) else 0
    t.status = "suspended"
    db.add(AuditLog(tenant_id=t.id, user_id=ctx.user.id, action="admin.tenant.lock", detail=f"{n} key(s) suspended"))
    db.add(SecurityEvent(tenant_id=t.id, severity="critical", kind="tenant_lock", detail=f"Workspace '{t.name}' locked by the platform team ({n} key(s) suspended)", resolved=True))
    db.commit()
    return {"ok": True, "keysSuspended": n}
