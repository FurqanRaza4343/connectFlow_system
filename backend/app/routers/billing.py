"""Plans and payments. With STRIPE_SECRET_KEY set, owners subscribe through Stripe Checkout and manage the card/invoices in the
Stripe customer portal; the plan only changes from Stripe's signed webhook (POST /api/stripe/webhook). Without the key the old
manual flow remains: the customer requests an upgrade, a platform admin confirms payment and approves it."""
from typing import Any
from datetime import datetime, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import stripe_billing as sb
from ..config import settings
from ..db import get_db
from ..deps import Ctx, need_role, platform_admin, TCtx
from ..mailer import layout, send_email
from ..models import AuditLog, Instance, Membership, Plan, Tenant, UpgradeRequest, User
from ..quota import ai_calls_today, messages_today

router = APIRouter(prefix="/api", tags=["billing"])


class CheckoutIn(BaseModel):
    plan_id: str


class UpgradeIn(BaseModel):
    plan_id: str
    note: str = Field(default="", max_length=300)


def _plan(p: Plan) -> dict:
    return {"id": p.id, "name": p.name, "priceUsd": p.price_usd, "pricePkr": p.price_pkr, "maxInstances": p.max_instances, "maxMsgsDay": p.max_msgs_day, "maxMembers": p.max_members, "aiCallsDay": p.ai_calls_day}


@router.get("/billing")
def billing(ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> dict[str, Any]:
    t = ctx.tenant
    pending = db.query(UpgradeRequest).filter(UpgradeRequest.tenant_id == t.id, UpgradeRequest.status == "pending").first()
    history = db.query(UpgradeRequest).filter(UpgradeRequest.tenant_id == t.id).order_by(UpgradeRequest.created_at.desc()).limit(10).all()
    return {
        "plan": _plan(t.plan),
        "usage": {"messagesToday": messages_today(db, t.id), "aiCallsToday": ai_calls_today(db, t.id), "numbers": db.query(func.count(Instance.id)).filter(Instance.tenant_id == t.id).scalar(), "members": db.query(func.count(Membership.id)).filter(Membership.tenant_id == t.id).scalar()},
        "plans": [_plan(p) for p in db.query(Plan).order_by(Plan.price_usd).all()],
        "stripe": sb.billing_state(t),
        "overLimit": db.query(func.count(Instance.id)).filter(Instance.tenant_id == t.id, Instance.over_limit.is_(True)).scalar(),
        "pending": {"id": pending.id, "planId": pending.plan_id, "createdAt": pending.created_at.isoformat()} if pending else None,
        "history": [{"id": r.id, "planId": r.plan_id, "status": r.status, "createdAt": r.created_at.isoformat()} for r in history],
    }


def _base_url(request: Request) -> str:
    """Where Stripe sends the browser back to: the host the owner is on (cookies are per host), falling back to PUBLIC_APP_URL."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    proto = request.headers.get("x-forwarded-proto") or ("https" if settings.cookie_secure else "http")
    return f"{proto}://{host}" if host else settings.public_app_url.rstrip("/")


@router.post("/billing/checkout")
async def checkout(body: CheckoutIn, request: Request, ctx: TCtx = Depends(need_role("owner")), db: Session = Depends(get_db)) -> Any:
    """Start a Stripe Checkout (or switch the existing subscription's plan with proration). Owner only."""
    if not sb.enabled():
        raise HTTPException(503, "Online payments are not enabled yet - use the request form instead")
    plan = db.get(Plan, body.plan_id)
    if not plan:
        raise HTTPException(404, "unknown plan")
    if plan.id == ctx.tenant.plan_id and ctx.tenant.subscription_status in sb.LIVE_STATUSES:
        raise HTTPException(409, "You are already on this plan")
    try:
        res = await sb.create_checkout(db, ctx.tenant, plan, ctx.user.email, _base_url(request))
    except sb.StripeError as e:
        raise HTTPException(e.status, str(e))
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="billing.checkout", detail=plan.id))
    db.commit()
    return res


@router.post("/billing/portal")
async def portal(request: Request, ctx: TCtx = Depends(need_role("owner")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Stripe customer portal: update the card, download invoices, cancel. Owner only."""
    if not sb.enabled():
        raise HTTPException(503, "Online payments are not enabled yet")
    if not ctx.tenant.stripe_customer_id:
        raise HTTPException(409, "There is no billing account yet - subscribe to a plan first")
    try:
        return {"url": await sb.create_portal(db, ctx.tenant, ctx.user.email, _base_url(request))}
    except sb.StripeError as e:
        raise HTTPException(e.status, str(e))


@router.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Public endpoint for Stripe. Trust comes only from the HMAC signature; there is no cookie or CSRF header here."""

    status, content = sb.process_webhook(db, await request.body(), request.headers.get("stripe-signature", ""))
    return JSONResponse(content, status_code=status)


@router.post("/billing/upgrade", status_code=201)
async def request_upgrade(body: UpgradeIn, ctx: TCtx = Depends(need_role("owner")), db: Session = Depends(get_db)) -> dict[str, Any]:
    target = db.get(Plan, body.plan_id)
    if not target:
        raise HTTPException(404, "unknown plan")
    if sb.enabled() and target.price_usd > 0:
        raise HTTPException(409, "Online payments are on - use Subscribe instead of the request form")
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
def list_requests(_: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.query(UpgradeRequest, Tenant, Plan).join(Tenant, Tenant.id == UpgradeRequest.tenant_id).join(Plan, Plan.id == UpgradeRequest.plan_id).order_by(UpgradeRequest.created_at.desc()).limit(100).all()
    return [{"id": r.id, "tenant": t.name, "tenantId": t.id, "currentPlan": t.plan_id, "requestedPlan": p.id, "priceUsd": p.price_usd, "pricePkr": p.price_pkr, "note": r.note, "status": r.status, "createdAt": r.created_at.isoformat()} for r, t, p in rows]


async def _resolve(request_id: str, approve: bool, ctx: Ctx, db: Session) -> dict[str, Any]:
    r = db.get(UpgradeRequest, request_id)
    if not r or r.status != "pending":
        raise HTTPException(404, "pending request not found")
    t = db.get(Tenant, r.tenant_id)
    if not t:
        raise HTTPException(404, "workspace not found")
    r.status, r.resolved_at = ("approved" if approve else "rejected"), datetime.now(timezone.utc)
    if approve:
        sb.set_plan(db, t, r.plan_id)
    db.add(AuditLog(tenant_id=t.id, user_id=ctx.user.id, action=f"billing.{r.status}", detail=r.plan_id))
    db.commit()
    owner = db.query(User).join(Membership, Membership.user_id == User.id).filter(Membership.tenant_id == t.id, Membership.role == "owner").first()
    if owner:
        msg = f"Your workspace is now on the {r.plan_id} plan." if approve else "We could not complete your upgrade request. Please contact us."
        await send_email(owner.email, "Your plan request was " + r.status, msg, layout("Plan " + r.status, escape(msg)))
    return {"ok": True}


@router.post("/admin/upgrade-requests/{request_id}/approve")
async def approve(request_id: str, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> Any:
    return await _resolve(request_id, True, ctx, db)


@router.post("/admin/upgrade-requests/{request_id}/reject")
async def reject(request_id: str, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> Any:
    return await _resolve(request_id, False, ctx, db)
