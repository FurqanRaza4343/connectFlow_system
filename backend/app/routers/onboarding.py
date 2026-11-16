"""First-run wizard after sign-up: choose a plan, tell the system about the business, then (paid plans) go to Stripe's card page.
Every field is optional and the whole wizard can be skipped; the 14-day trial clock starts when the workspace is created."""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import access, status_engine, stripe_billing as sb
from ..db import get_db
from ..deps import Ctx, TCtx, need_role, platform_admin
from ..leadtools import normalize_phone
from ..models import AuditLog, OutreachProfile, Plan, Tenant, UpgradeRequest
from .billing import _base_url, _plan

router = APIRouter(prefix="/api", tags=["onboarding"])


class OnboardingIn(BaseModel):
    plan_id: str = Field(default="trial", max_length=32)
    name: str = Field(default="", max_length=120)
    business: str = Field(default="", max_length=120)
    website: str = Field(default="", max_length=300)
    country: str = Field(default="", max_length=2)
    about: str = Field(default="", max_length=4000)
    phone: str = Field(default="", max_length=60)
    skip: bool = False


def _prefill(db: Session, ctx: TCtx) -> dict[str, str]:
    op = db.get(OutreachProfile, ctx.tenant.id)
    return {"name": ctx.user.name or "", "business": ctx.tenant.name or "", "website": ctx.tenant.website_url or (op.website if op else ""),
            "country": op.default_country if op else "", "about": (op.knowledge if op else ""), "phone": "", "email": ctx.user.email}


@router.get("/onboarding")
def state(ctx: TCtx = Depends(need_role("owner")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"needed": ctx.tenant.onboarded_at is None, "stripe": sb.enabled(), "trialDays": access.TRIAL_DAYS,
            "plans": [_plan(p) for p in db.query(Plan).order_by(Plan.price_usd).all()], "prefill": _prefill(db, ctx)}


def _save_details(db: Session, ctx: TCtx, body: OnboardingIn) -> None:
    t = ctx.tenant
    op = db.get(OutreachProfile, t.id)
    if not op:
        op = OutreachProfile(tenant_id=t.id)
        db.add(op)
    if body.name.strip():
        ctx.user.name = body.name.strip()
        op.sender_name = op.sender_name or body.name.strip()[:80]
    if len(body.business.strip()) >= 2:
        t.name = body.business.strip()
        op.company_name = body.business.strip()
    if body.website.strip():
        site = body.website.strip()
        if not site.lower().startswith(("http://", "https://")):
            site = "https://" + site
        t.website_url = site[:300]
        op.website = site[:300]
    if len(body.country.strip()) == 2 and body.country.strip().isalpha():
        op.default_country = body.country.strip().upper()
    if body.about.strip():
        op.knowledge = body.about.strip()
        op.offer = op.offer or body.about.strip()[:500]
    if body.phone.strip():
        digits, _cc = normalize_phone(body.phone, op.default_country or "PK")
        if digits:
            status_engine.get_profile(db, t.id).wa_number = digits  # used for the "chat on WhatsApp" button and as the owner's contact number


@router.post("/onboarding")
async def finish(body: OnboardingIn, request: Request, ctx: TCtx = Depends(need_role("owner")), db: Session = Depends(get_db)) -> dict[str, Any]:
    t = ctx.tenant
    plan = db.get(Plan, body.plan_id or "trial")
    if not plan:
        raise HTTPException(404, "unknown plan")
    first = t.onboarded_at is None
    if first and not body.skip:
        _save_details(db, ctx, body)
    if first:
        t.onboarded_at = datetime.now(timezone.utc)
        db.add(AuditLog(tenant_id=t.id, user_id=ctx.user.id, action="onboarding.skip" if body.skip else "onboarding.done", detail=plan.id))
    db.commit()
    out: dict[str, Any] = {"ok": True}
    if plan.price_usd > 0 and plan.id != t.plan_id:  # a paid plan: card details are entered on Stripe's own page, never here
        if sb.enabled():
            try:
                res = await sb.create_checkout(db, t, plan, ctx.user.email, _base_url(request))
                db.add(AuditLog(tenant_id=t.id, user_id=ctx.user.id, action="billing.checkout", detail=plan.id))
                db.commit()
                if res.get("url"):
                    out["checkoutUrl"] = res["url"]
            except sb.StripeError as e:
                out["message"] = f"We could not open the payment page ({e}). You can subscribe any time from Billing."
        else:
            if not db.query(UpgradeRequest).filter(UpgradeRequest.tenant_id == t.id, UpgradeRequest.status == "pending").first():
                db.add(UpgradeRequest(tenant_id=t.id, user_id=ctx.user.id, plan_id=plan.id, note="from sign-up"))
                db.commit()
            out["message"] = "Online payments are not enabled yet. We sent your request to the team and will contact you. Your free trial is running meanwhile."
    return out


@router.post("/admin/tenants/{tenant_id}/extend-trial")
def extend_trial(tenant_id: str, days: int = 7, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "workspace not found")
    if not 1 <= days <= 90:
        raise HTTPException(422, "days must be 1-90")
    base = max(t.trial_ends_at or datetime.now(timezone.utc), datetime.now(timezone.utc))
    t.trial_ends_at = base + timedelta(days=days)
    t.trial_reminder = ""
    db.add(AuditLog(tenant_id=t.id, user_id=ctx.user.id, action="admin.extend_trial", detail=f"+{days}d"))
    db.commit()
    return {"ok": True, "trialEndsAt": t.trial_ends_at.isoformat()}
