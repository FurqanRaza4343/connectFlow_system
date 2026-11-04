"""Platform-admin API used by the separate Admin dashboard. Admins see everything about customers EXCEPT message content."""
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from .. import wa_client as wa
from ..config import settings
from ..db import get_db
from ..deps import Ctx, platform_admin, rds
from ..models import (ApiKey, ApiKeyUsage, AuditLog, Instance, Lead, Membership, Message, Plan, Tenant, UpgradeRequest, UsageEvent, User, WebhookDelivery, WebhookEndpoint)
from ..quota import ai_calls_today, day_start, messages_today

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _series(rows, days: int) -> list[dict]:
    start = day_start() - timedelta(days=days - 1)
    m = {str(d): c for d, c in rows}
    return [{"day": str((start + timedelta(days=i)).date()), "count": m.get(str((start + timedelta(days=i)).date()), 0)} for i in range(days)]


def _provider(u: User) -> str:
    return "google" if u.google_sub else "password"


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(_: Ctx = Depends(platform_admin), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    week = now - timedelta(days=7)
    tenants = db.query(Tenant).all()
    active_ids = {m.tenant_id for m, u in db.query(Membership, User).join(User, User.id == Membership.user_id).filter(User.last_seen_at >= week).all()}
    plans = {p.id: p for p in db.query(Plan).all()}
    since30 = day_start() - timedelta(days=29)
    return {
        "kpis": {
            "customers": len(tenants), "activeCustomers7d": len(active_ids), "users": db.query(User).count(),
            "verifiedUsers": db.query(User).filter(User.email_verified_at.isnot(None)).count(),
            "numbers": db.query(Instance).count(), "numbersConnected": db.query(Instance).filter(Instance.status == "ready").count(),
            "messagesToday": db.query(UsageEvent).filter(UsageEvent.at >= day_start(), UsageEvent.kind == "message").count(),
            "messages7d": db.query(UsageEvent).filter(UsageEvent.at >= week, UsageEvent.kind == "message").count(),
            "leads": db.query(Lead).count(), "pendingRequests": db.query(UpgradeRequest).filter(UpgradeRequest.status == "pending").count(),
            "mrrUsd": sum(plans[t.plan_id].price_usd for t in tenants if t.status == "active" and t.plan_id in plans),
        },
        "signups": _series(db.query(func.date(Tenant.created_at), func.count(Tenant.id)).filter(Tenant.created_at >= since30).group_by(func.date(Tenant.created_at)).all(), 30),
        "messages": _series(db.query(func.date(UsageEvent.at), func.count(UsageEvent.id)).filter(UsageEvent.at >= since30, UsageEvent.kind == "message").group_by(func.date(UsageEvent.at)).all(), 30),
        "planBreakdown": [{"plan": p, "count": c} for p, c in db.query(Tenant.plan_id, func.count(Tenant.id)).group_by(Tenant.plan_id).all()],
    }


# ------------------------------------------------------------------ customers
@router.get("/customers")
def customers(q: str = "", status: str = "", plan: str = "", page: int = Query(1, ge=1), db: Session = Depends(get_db), _: Ctx = Depends(platform_admin)):
    base = db.query(Tenant)
    if status:
        base = base.filter(Tenant.status == status)
    if plan:
        base = base.filter(Tenant.plan_id == plan)
    if q.strip():
        like = f"%{q.strip()}%"
        owner_ids = [m.tenant_id for m, u in db.query(Membership, User).join(User, User.id == Membership.user_id).filter(or_(User.email.ilike(like), User.name.ilike(like))).all()]
        base = base.filter(or_(Tenant.name.ilike(like), Tenant.id.in_(owner_ids or ["-"])))
    total = base.count()
    size = 25
    rows = base.order_by(Tenant.created_at.desc()).offset((page - 1) * size).limit(size).all()
    ids = [t.id for t in rows]
    owners = {m.tenant_id: u for m, u in db.query(Membership, User).join(User, User.id == Membership.user_id).filter(Membership.tenant_id.in_(ids), Membership.role == "owner").all()}
    seen = dict(db.query(Membership.tenant_id, func.max(User.last_seen_at)).join(User, User.id == Membership.user_id).filter(Membership.tenant_id.in_(ids)).group_by(Membership.tenant_id).all())
    nums = dict(db.query(Instance.tenant_id, func.count(Instance.id)).filter(Instance.tenant_id.in_(ids)).group_by(Instance.tenant_id).all())
    mems = dict(db.query(Membership.tenant_id, func.count(Membership.id)).filter(Membership.tenant_id.in_(ids)).group_by(Membership.tenant_id).all())
    leads = dict(db.query(Lead.tenant_id, func.count(Lead.id)).filter(Lead.tenant_id.in_(ids)).group_by(Lead.tenant_id).all())
    today = dict(db.query(UsageEvent.tenant_id, func.count(UsageEvent.id)).filter(UsageEvent.tenant_id.in_(ids), UsageEvent.at >= day_start(), UsageEvent.kind == "message").group_by(UsageEvent.tenant_id).all())
    allm = dict(db.query(UsageEvent.tenant_id, func.count(UsageEvent.id)).filter(UsageEvent.tenant_id.in_(ids), UsageEvent.kind == "message").group_by(UsageEvent.tenant_id).all())
    return {
        "rows": [{"id": t.id, "name": t.name, "ownerEmail": owners[t.id].email if t.id in owners else "", "ownerName": owners[t.id].name if t.id in owners else "", "plan": t.plan_id, "status": t.status,
                  "createdAt": _iso(t.created_at), "lastActiveAt": _iso(seen.get(t.id)), "numbers": nums.get(t.id, 0), "members": mems.get(t.id, 0), "leads": leads.get(t.id, 0),
                  "messagesToday": today.get(t.id, 0), "messagesTotal": allm.get(t.id, 0)} for t in rows],
        "total": total, "page": page, "pages": max(1, -(-total // size)),
    }


@router.get("/customers/{tenant_id}")
def customer(tenant_id: str, db: Session = Depends(get_db), _: Ctx = Depends(platform_admin)):
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "customer not found")
    members = db.query(Membership, User).join(User, User.id == Membership.user_id).filter(Membership.tenant_id == t.id).order_by(Membership.id).all()
    instances = db.query(Instance).filter(Instance.tenant_id == t.id).order_by(Instance.created_at).all()
    keys = db.query(ApiKey).filter(ApiKey.tenant_id == t.id).order_by(ApiKey.created_at.desc()).all()
    eps = db.query(WebhookEndpoint).filter(WebhookEndpoint.tenant_id == t.id).all()
    dl = {}
    for ep_id, st, c in db.query(WebhookDelivery.endpoint_id, WebhookDelivery.status, func.count(WebhookDelivery.id)).filter(WebhookDelivery.tenant_id == t.id).group_by(WebhookDelivery.endpoint_id, WebhookDelivery.status).all():
        dl.setdefault(ep_id, {})[st] = c
    by_status = dict(db.query(Lead.status, func.count(Lead.id)).filter(Lead.tenant_id == t.id).group_by(Lead.status).all())
    start = day_start() - timedelta(days=13)
    usage = db.query(func.date(UsageEvent.at), func.count(UsageEvent.id)).filter(UsageEvent.tenant_id == t.id, UsageEvent.kind == "message", UsageEvent.at >= start).group_by(func.date(UsageEvent.at)).all()
    acts = db.query(AuditLog, User.email).outerjoin(User, User.id == AuditLog.user_id).filter(AuditLog.tenant_id == t.id).order_by(AuditLog.id.desc()).limit(150).all()
    reqs = db.query(UpgradeRequest).filter(UpgradeRequest.tenant_id == t.id).order_by(UpgradeRequest.created_at.desc()).all()
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).date()
    kstats: dict = {}
    for u in db.query(ApiKeyUsage).filter(ApiKeyUsage.tenant_id == t.id, ApiKeyUsage.day >= week_ago).order_by(ApiKeyUsage.day.asc()).all():
        d = kstats.setdefault(u.key_id, {"requests7d": 0, "errors7d": 0, "ips": set(), "lastIp": "", "lastUserAgent": "", "lastAt": None, "requestsToday": 0})
        d["requests7d"] += u.requests
        d["errors7d"] += u.errors
        d["ips"].update((u.ips or {}).keys())
        d["lastIp"], d["lastUserAgent"], d["lastAt"] = u.last_ip, u.last_user_agent, _iso(u.last_at)
        if u.day == now.date():
            d["requestsToday"] = u.requests
    def _kstat(k):
        d = kstats.get(k.id, {})
        return {"requests7d": d.get("requests7d", 0), "errors7d": d.get("errors7d", 0), "distinctIps7d": len(d.get("ips", ())), "lastIp": d.get("lastIp", ""),
                "lastUserAgent": d.get("lastUserAgent", ""), "lastSeenAt": d.get("lastAt"), "requestsToday": d.get("requestsToday", 0),
                "status": "revoked" if k.revoked_at else "suspended" if k.suspended_at else "expired" if (k.expires_at and k.expires_at < now) else "active",
                "suspendedReason": k.suspended_reason or "", "suspendedBy": k.suspended_by or "", "suspendedAt": _iso(k.suspended_at), "allowedIps": k.allowed_ips or ""}
    week = now - timedelta(days=7)
    sent_m = dict(db.query(Message.instance_id, func.count(Message.id)).filter(Message.tenant_id == t.id, Message.direction == "out", Message.created_at >= week).group_by(Message.instance_id).all())
    in_m = dict(db.query(Message.instance_id, func.count(Message.id)).filter(Message.tenant_id == t.id, Message.direction == "in", Message.created_at >= week).group_by(Message.instance_id).all())
    sent_t = dict(db.query(Message.instance_id, func.count(Message.id)).filter(Message.tenant_id == t.id, Message.direction == "out", Message.created_at >= day_start()).group_by(Message.instance_id).all())

    def _icount(iid):  # counters only - admins never see message content
        return {"sentToday": sent_t.get(iid, 0), "sent7d": sent_m.get(iid, 0), "replies7d": in_m.get(iid, 0)}
    return {
        "tenant": {"id": t.id, "name": t.name, "plan": t.plan_id, "status": t.status, "createdAt": _iso(t.created_at), "website": t.website_url, "brandColor": t.brand_color},
        "members": [{"userId": u.id, "email": u.email, "name": u.name, "role": m.role, "verified": bool(u.email_verified_at), "lastLoginAt": _iso(u.last_login_at), "lastSeenAt": _iso(u.last_seen_at), "provider": _provider(u)} for m, u in members],
        "instances": [{"id": i.id, "label": i.label, "phone": i.phone, "status": i.status, "createdAt": _iso(i.created_at), "outreachEnabled": i.outreach_enabled, **_icount(i.id)} for i in instances],
        "keys": [{"id": k.id, "name": k.name, "prefix": k.prefix, "lastUsedAt": _iso(k.last_used_at), "expiresAt": _iso(k.expires_at), "revoked": bool(k.revoked_at or (k.expires_at and k.expires_at < now)), **_kstat(k)} for k in keys],
        "webhooks": [{"id": e.id, "url": e.url, "active": e.active, "events": e.events.split(","), "deliveries": {"success": dl.get(e.id, {}).get("success", 0), "failed": dl.get(e.id, {}).get("failed", 0), "pending": dl.get(e.id, {}).get("pending", 0) + dl.get(e.id, {}).get("retrying", 0)}} for e in eps],
        "leads": {"total": sum(by_status.values()), "byStatus": by_status},
        "usage": {"today": messages_today(db, t.id), "limit": t.plan.max_msgs_day, "days": _series(usage, 14), "aiToday": ai_calls_today(db, t.id), "aiLimit": t.plan.ai_calls_day},
        "requests": [{"id": r.id, "planId": r.plan_id, "status": r.status, "createdAt": _iso(r.created_at), "note": r.note} for r in reqs],
        "activity": [{"at": _iso(a.at), "action": a.action, "detail": a.detail, "userEmail": email} for a, email in acts],
    }


@router.delete("/customers/{tenant_id}", status_code=204)
async def delete_customer(tenant_id: str, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)):
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "customer not found")
    if any(u.is_platform_admin for _, u in db.query(Membership, User).join(User, User.id == Membership.user_id).filter(Membership.tenant_id == t.id).all()):
        raise HTTPException(409, "This workspace belongs to a platform admin and cannot be deleted here")
    for i in db.query(Instance).filter(Instance.tenant_id == t.id).all():
        try:
            await wa.delete_session(i.openwa_id)
        except wa.WAError:
            pass  # the session may already be gone
    only_here = [u for m, u in db.query(Membership, User).join(User, User.id == Membership.user_id).filter(Membership.tenant_id == t.id).all()
                 if db.query(Membership).filter(Membership.user_id == u.id, Membership.tenant_id != t.id).count() == 0]
    name = t.name
    db.delete(t)
    for u in only_here:
        db.delete(u)
    db.add(AuditLog(tenant_id=None, user_id=ctx.user.id, action="admin.customer.delete", detail=f"{name} ({len(only_here)} user(s))"))
    db.commit()


@router.post("/users/{user_id}/verify-email")
def verify_email(user_id: str, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    u.email_verified_at = u.email_verified_at or datetime.now(timezone.utc)
    db.add(AuditLog(user_id=ctx.user.id, action="admin.user.verify", detail=u.email))
    db.commit()
    return {"ok": True}


def _support_throttle(kind: str, user_id: str) -> None:
    if not rds.set(f"adm:{kind}:{user_id}", "1", nx=True, ex=120):
        raise HTTPException(429, "An email was just sent to this user - wait two minutes")


@router.post("/users/{user_id}/resend-verification")
async def admin_resend_verification(user_id: str, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)):
    """Support action: e-mails the user a fresh verification link. The link/token is never returned to the admin."""
    from .auth import _send_verification
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    if u.email_verified_at:
        raise HTTPException(409, "This email is already verified")
    _support_throttle("verify", u.id)
    await _send_verification(db, u)
    db.add(AuditLog(user_id=ctx.user.id, action="admin.user.resend_verification", detail=u.email))
    db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/send-password-reset")
async def admin_send_password_reset(user_id: str, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)):
    """Support action: e-mails the user a reset link (valid 1 hour). The admin never sees the token or sets a password."""
    from .. import tokens
    from ..mailer import layout, send_email
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    _support_throttle("reset", u.id)
    link = f"{settings.public_app_url.rstrip('/')}/?reset={tokens.issue(db, u.id, 'reset', 1)}"
    await send_email(u.email, "Reset your ConnectFlow password", f"Reset your password (valid 1 hour): {link}",
                     layout("Reset your password", "Our support team sent you this link to reset your password. It is valid for 1 hour.", ("Choose a new password", link)))
    db.add(AuditLog(user_id=ctx.user.id, action="admin.user.send_reset", detail=u.email))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ users + activity
@router.get("/users")
def users(q: str = "", page: int = Query(1, ge=1), db: Session = Depends(get_db), _: Ctx = Depends(platform_admin)):
    base = db.query(User)
    if q.strip():
        like = f"%{q.strip()}%"
        base = base.filter(or_(User.email.ilike(like), User.name.ilike(like)))
    total, size = base.count(), 30
    rows = base.order_by(User.created_at.desc()).offset((page - 1) * size).limit(size).all()
    mem = {m.user_id: (m, t) for m, t in db.query(Membership, Tenant).join(Tenant, Tenant.id == Membership.tenant_id).filter(Membership.user_id.in_([u.id for u in rows])).all()}
    return {"rows": [{"id": u.id, "email": u.email, "name": u.name, "tenant": mem[u.id][1].name if u.id in mem else "", "tenantId": mem[u.id][1].id if u.id in mem else None,
                      "role": mem[u.id][0].role if u.id in mem else "", "verified": bool(u.email_verified_at), "provider": _provider(u), "isPlatformAdmin": u.is_platform_admin,
                      "createdAt": _iso(u.created_at), "lastLoginAt": _iso(u.last_login_at), "lastSeenAt": _iso(u.last_seen_at)} for u in rows],
            "total": total, "page": page, "pages": max(1, -(-total // size))}


@router.get("/activity")
def activity(tenant_id: str = "", action: str = "", q: str = "", page: int = Query(1, ge=1), db: Session = Depends(get_db), _: Ctx = Depends(platform_admin)):
    base = db.query(AuditLog, Tenant.name, User.email).outerjoin(Tenant, Tenant.id == AuditLog.tenant_id).outerjoin(User, User.id == AuditLog.user_id)
    if tenant_id:
        base = base.filter(AuditLog.tenant_id == tenant_id)
    if action:
        base = base.filter(AuditLog.action == action)
    if q.strip():
        like = f"%{q.strip()}%"
        base = base.filter(or_(AuditLog.detail.ilike(like), User.email.ilike(like), Tenant.name.ilike(like)))
    total, size = base.count(), 50
    rows = base.order_by(AuditLog.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"rows": [{"id": a.id, "at": _iso(a.at), "tenant": tn, "tenantId": a.tenant_id, "userEmail": ue, "action": a.action, "detail": a.detail} for a, tn, ue in rows],
            "total": total, "page": page, "pages": max(1, -(-total // size)),
            "actions": [a for (a,) in db.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]}


# ------------------------------------------------------------------ plans + health
class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    priceUsd: int = Field(ge=0)
    pricePkr: int = Field(ge=0)
    maxInstances: int = Field(ge=1, le=1000)
    maxMsgsDay: int = Field(ge=1, le=1_000_000)
    maxMembers: int = Field(ge=1, le=1000)
    aiCallsDay: int = Field(default=200, ge=0, le=1_000_000)


def _plan(p: Plan) -> dict:
    return {"id": p.id, "name": p.name, "priceUsd": p.price_usd, "pricePkr": p.price_pkr, "maxInstances": p.max_instances, "maxMsgsDay": p.max_msgs_day, "maxMembers": p.max_members, "aiCallsDay": p.ai_calls_day}


@router.get("/plans")
def plans(db: Session = Depends(get_db), _: Ctx = Depends(platform_admin)):
    return [_plan(p) for p in db.query(Plan).order_by(Plan.price_usd).all()]


@router.put("/plans/{plan_id}")
def put_plan(plan_id: str, body: PlanIn, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)):
    p = db.get(Plan, plan_id)
    if not p:
        raise HTTPException(404, "plan not found")
    p.name, p.price_usd, p.price_pkr = body.name.strip(), body.priceUsd, body.pricePkr
    p.max_instances, p.max_msgs_day, p.max_members = body.maxInstances, body.maxMsgsDay, body.maxMembers
    p.ai_calls_day = body.aiCallsDay
    db.add(AuditLog(user_id=ctx.user.id, action="admin.plan.update", detail=f"{plan_id}: ${body.priceUsd}, {body.maxInstances} numbers, {body.maxMsgsDay}/day, {body.maxMembers} seats, {body.aiCallsDay} AI calls/day"))
    db.commit()
    return _plan(p)


@router.get("/health")
async def health(db: Session = Depends(get_db), _: Ctx = Depends(platform_admin)):
    out: dict = {"time": datetime.now(timezone.utc).isoformat()}
    try:
        db.execute(text("select 1"))
        out["database"] = "ok"
    except Exception:  # noqa: BLE001
        out["database"] = "error"
    try:
        out["redis"] = "ok" if rds.ping() else "error"
    except Exception:  # noqa: BLE001
        out["redis"] = "error"
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{settings.openwa_url}/api/health/ready")
        out["gateway"] = {"ok": r.status_code == 200, **({} if r.status_code == 200 else {"error": f"HTTP {r.status_code}"})}
    except Exception as e:  # noqa: BLE001
        out["gateway"] = {"ok": False, "error": e.__class__.__name__}
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    cnt = dict(db.query(WebhookDelivery.status, func.count(WebhookDelivery.id)).filter(WebhookDelivery.created_at >= since).group_by(WebhookDelivery.status).all())
    out["worker"] = {"pending": db.query(WebhookDelivery).filter(WebhookDelivery.status == "pending").count(), "retrying": db.query(WebhookDelivery).filter(WebhookDelivery.status == "retrying").count(), "failed24h": cnt.get("failed", 0)}
    return out
