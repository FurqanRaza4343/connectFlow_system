"""Plans and upgrades. Payments are handled manually for now (invoice, JazzCash/Easypaisa/bank): the customer requests an
upgrade, a platform admin confirms payment and approves it. Stripe Checkout can replace the manual step later without
changing this API's shape."""
from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import Ctx, need_role, platform_admin
from ..mailer import layout, send_email
from ..models import AuditLog, Instance, Membership, Plan, Tenant, UpgradeRequest, User
from ..quota import ai_calls_today, messages_today

router = APIRouter(prefix="/api", tags=["billing"])


class UpgradeIn(BaseModel):
    plan_id: str
    note: str = Field(default="", max_length=300)


def _plan(p: Plan) -> dict:
    return {"id": p.id, "name": p.name, "priceUsd": p.price_usd, "pricePkr": p.price_pkr, "maxInstances": p.max_instances, "maxMsgsDay": p.max_msgs_day, "maxMembers": p.max_members, "aiCallsDay": p.ai_calls_day}


@router.get("/billing")
def billing(ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    t = ctx.tenant
    pending = db.query(UpgradeRequest).filter(UpgradeRequest.tenant_id == t.id, UpgradeRequest.status == "pending").first()
    history = db.query(UpgradeRequest).filter(UpgradeRequest.tenant_id == t.id).order_by(UpgradeRequest.created_at.desc()).limit(10).all()
    return {
        "plan": _plan(t.plan),
        "usage": {"messagesToday": messages_today(db, t.id), "aiCallsToday": ai_calls_today(db, t.id), "numbers": db.query(func.count(Instance.id)).filter(Instance.tenant_id == t.id).scalar(), "members": db.query(func.count(Membership.id)).filter(Membership.tenant_id == t.id).scalar()},
        "plans": [_plan(p) for p in db.query(Plan).order_by(Plan.price_usd).all()],
        "pending": {"id": pending.id, "planId": pending.plan_id, "createdAt": pending.created_at.isoformat()} if pending else None,
        "history": [{"id": r.id, "planId": r.plan_id, "status": r.status, "createdAt": r.created_at.isoformat()} for r in history],
    }


@router.post("/billing/upgrade", status_code=201)
async def request_upgrade(body: UpgradeIn, ctx: Ctx = Depends(need_role("owner")), db: Session = Depends(get_db)):
    target = db.get(Plan, body.plan_id)
    if not target:
        raise HTTPException(404, "unknown plan")
    if target.id == ctx.tenant.plan_id:
        raise HTTPException(409, "You are already on this plan")
    if db.query(UpgradeRequest).filter(UpgradeRequest.tenant_id == ctx.tenant.id, UpgradeRequest.status == "pending").first():
        raise HTTPException(409, "You already have a pending request")
    r = UpgradeRequest(tenant_id=ctx.tenant.id, user_id=ctx.user.id, plan_id=target.id, note=body.note.strip())
    db.add(r)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="billing.upgrade_request", detail=target.id))
    db.commit()
    if settings.admin_email:
        await send_email(settings.admin_email, f"Upgrade request: {ctx.tenant.name} → {target.name}",
                         f"{ctx.tenant.name} ({ctx.user.email}) asked to move to {target.name}. Note: {r.note or '-'}",
                         layout("Upgrade request", f"<b>{escape(ctx.tenant.name)}</b> ({escape(ctx.user.email)}) asked to move to <b>{escape(target.name)}</b>.<br>Note: {escape(r.note or '-')}"))
    return {"ok": True}


# ---------- platform admin ----------
@router.get("/admin/upgrade-requests")
def list_requests(_: Ctx = Depends(platform_admin), db: Session = Depends(get_db)):
    rows = db.query(UpgradeRequest, Tenant, Plan).join(Tenant, Tenant.id == UpgradeRequest.tenant_id).join(Plan, Plan.id == UpgradeRequest.plan_id).order_by(UpgradeRequest.created_at.desc()).limit(100).all()
    return [{"id": r.id, "tenant": t.name, "tenantId": t.id, "currentPlan": t.plan_id, "requestedPlan": p.id, "priceUsd": p.price_usd, "pricePkr": p.price_pkr, "note": r.note, "status": r.status, "createdAt": r.created_at.isoformat()} for r, t, p in rows]


async def _resolve(request_id: str, approve: bool, ctx: Ctx, db: Session):
    r = db.get(UpgradeRequest, request_id)
    if not r or r.status != "pending":
        raise HTTPException(404, "pending request not found")
    t = db.get(Tenant, r.tenant_id)
    r.status, r.resolved_at = ("approved" if approve else "rejected"), datetime.now(timezone.utc)
    if approve:
        t.plan_id = r.plan_id
    db.add(AuditLog(tenant_id=t.id, user_id=ctx.user.id, action=f"billing.{r.status}", detail=r.plan_id))
    db.commit()
    owner = db.query(User).join(Membership, Membership.user_id == User.id).filter(Membership.tenant_id == t.id, Membership.role == "owner").first()
    if owner:
        msg = f"Your workspace is now on the {r.plan_id} plan." if approve else "We could not complete your upgrade request. Please contact us."
        await send_email(owner.email, "Your plan request was " + r.status, msg, layout("Plan " + r.status, escape(msg)))
    return {"ok": True}


@router.post("/admin/upgrade-requests/{request_id}/approve")
async def approve(request_id: str, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)):
    return await _resolve(request_id, True, ctx, db)


@router.post("/admin/upgrade-requests/{request_id}/reject")
async def reject(request_id: str, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)):
    return await _resolve(request_id, False, ctx, db)
