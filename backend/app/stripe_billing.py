"""Stripe billing without the SDK: signature check, form-encoded REST calls, webhook event handling and plan limits.

Flow: the owner clicks Subscribe -> POST /api/billing/checkout creates a Checkout Session (mode=subscription) -> Stripe
redirects back; the plan only changes when Stripe's signed webhook says so (never from the browser redirect).
Plan mapping: active|trialing -> the purchased plan; past_due -> same plan for a 7-day grace, then back to Trial;
canceled / deleted / unpaid -> Trial. Numbers are never deleted on a downgrade: extras are flagged `over_limit`.
"""
import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .models import AuditLog, Instance, Plan, StripeEvent, Tenant

log = logging.getLogger("connectflow.stripe")

TOLERANCE_SECONDS = 300
GRACE_DAYS = 7
PAID_PLANS = ("starter", "growth")
LIVE_STATUSES = ("active", "trialing", "past_due")


def enabled() -> bool:
    return bool(settings.stripe_secret_key)


# ---------------------------------------------------------------- signature
def verify_signature(payload: bytes, header: str, secret: str, now: float | None = None, tolerance: int = TOLERANCE_SECONDS) -> bool:
    """`Stripe-Signature: t=<unix>,v1=<hex>[,v1=...]`; v1 = HMAC-SHA256(secret, f"{t}.{payload}"). Constant-time compare, 5-minute window."""
    if not secret or not header:
        return False
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int(parts.get("t", [""])[0])
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", []))


def sign(payload: bytes, secret: str, ts: int | None = None) -> str:
    """Build a valid header (used by tests and by `stripe listen`-style local simulations)."""
    ts = ts if ts is not None else int(time.time())
    return f"t={ts},v1={hmac.new(secret.encode(), f'{ts}.'.encode() + payload, hashlib.sha256).hexdigest()}"


# ---------------------------------------------------------------- REST helpers
def form_encode(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Stripe's nested form encoding: {"a": {"b": [1]}} -> a[b][0]=1."""
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += form_encode(v, f"{prefix}[{k}]" if prefix else str(k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out += form_encode(v, f"{prefix}[{i}]")
    elif obj is not None:
        out.append((prefix, "true" if obj is True else "false" if obj is False else str(obj)))
    return out


class StripeError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


async def _call(method: str, path: str, data: dict | None = None) -> dict:
    if not enabled():
        raise StripeError(503, "Online payments are not enabled yet")
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.request(method, settings.stripe_api_base.rstrip("/") + path, auth=(settings.stripe_secret_key, ""),
                                data=dict(form_encode(data)) if data else None, headers={"Stripe-Version": "2024-06-20"})
    except httpx.HTTPError as e:
        raise StripeError(502, f"Could not reach Stripe: {e.__class__.__name__}") from e
    body = r.json() if r.content else {}
    if r.status_code >= 400:
        msg = (body.get("error") or {}).get("message") or f"Stripe error {r.status_code}"
        log.warning("stripe %s %s -> %s", method, path, r.status_code)
        raise StripeError(502 if r.status_code >= 500 else 400, str(msg)[:300])
    return body


def _price_items(plan: Plan) -> dict:
    """A recurring monthly USD price: a configured price id if present, otherwise an inline price built from the plans table."""
    pid = {"starter": settings.stripe_price_starter, "growth": settings.stripe_price_growth}.get(plan.id, "")
    if pid:
        return {"price": pid, "quantity": 1}
    return {"price_data": {"currency": "usd", "unit_amount": plan.price_usd * 100, "recurring": {"interval": "month"},
                           "product_data": {"name": f"ConnectFlow {plan.name}"}}, "quantity": 1}


async def ensure_customer(db: Session, tenant: Tenant, email: str) -> str:
    if tenant.stripe_customer_id:
        return tenant.stripe_customer_id
    c = await _call("POST", "/v1/customers", {"email": email, "name": tenant.name, "metadata": {"tenant_id": tenant.id}})
    tenant.stripe_customer_id = str(c["id"])
    db.commit()
    return tenant.stripe_customer_id


async def create_checkout(db: Session, tenant: Tenant, plan: Plan, email: str, base_url: str) -> dict:
    """Returns {"url": ...} to redirect to, or {"changed": True} when an existing subscription was switched in place (proration)."""
    if plan.id not in PAID_PLANS or plan.price_usd <= 0:
        raise StripeError(400, "That plan cannot be bought online")
    if tenant.stripe_subscription_id and tenant.subscription_status in LIVE_STATUSES:
        sub = await _call("GET", f"/v1/subscriptions/{tenant.stripe_subscription_id}")
        item = ((sub.get("items") or {}).get("data") or [{}])[0]
        await _call("POST", f"/v1/subscriptions/{tenant.stripe_subscription_id}", {
            "items": [{"id": item.get("id"), **_price_items(plan)}], "proration_behavior": "create_prorations",
            "metadata": {"tenant_id": tenant.id, "plan_id": plan.id}, "cancel_at_period_end": False})
        return {"changed": True}
    customer = await ensure_customer(db, tenant, email)
    meta = {"tenant_id": tenant.id, "plan_id": plan.id}
    s = await _call("POST", "/v1/checkout/sessions", {
        "mode": "subscription", "customer": customer, "client_reference_id": tenant.id, "allow_promotion_codes": True,
        "line_items": [_price_items(plan)], "metadata": meta, "subscription_data": {"metadata": meta},
        "success_url": f"{base_url}/?checkout=success#billing", "cancel_url": f"{base_url}/?checkout=cancel#billing"})
    return {"url": s.get("url")}


async def create_portal(db: Session, tenant: Tenant, email: str, base_url: str) -> str:
    customer = await ensure_customer(db, tenant, email)
    s = await _call("POST", "/v1/billing_portal/sessions", {"customer": customer, "return_url": f"{base_url}/#billing"})
    return str(s.get("url"))


# ---------------------------------------------------------------- plan limits
def enforce_plan_limits(db: Session, tenant: Tenant) -> int:
    """Keep the oldest N numbers (N = plan limit) usable and flag the rest `over_limit` (kept, never deleted, cannot send or be picked
    for outreach). Returns how many numbers are over the limit. Call after every plan change."""
    rows = db.query(Instance).filter(Instance.tenant_id == tenant.id).order_by(Instance.created_at, Instance.id).all()
    limit = tenant.plan.max_instances
    over = 0
    for i, inst in enumerate(rows):
        flag = i >= limit
        over += int(flag)
        if inst.over_limit != flag:
            inst.over_limit = flag
    return over


def set_plan(db: Session, tenant: Tenant, plan_id: str) -> None:
    tenant.plan_id = plan_id
    db.flush()
    db.refresh(tenant)
    enforce_plan_limits(db, tenant)


def _ts(v: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(v), tz=timezone.utc) if v else None
    except (TypeError, ValueError, OSError):
        return None


def _find_tenant(db: Session, obj: dict) -> Tenant | None:
    cid = obj.get("customer")
    if isinstance(cid, str) and cid:
        t = db.query(Tenant).filter(Tenant.stripe_customer_id == cid).first()
        if t:
            return t
    tid = (obj.get("metadata") or {}).get("tenant_id") or obj.get("client_reference_id")
    return db.get(Tenant, tid) if tid else None


def _audit(db: Session, tenant: Tenant, action: str, detail: str) -> None:
    db.add(AuditLog(tenant_id=tenant.id, user_id=None, action=action, detail=detail[:300]))


def _period_end(sub: dict) -> datetime | None:
    pe = sub.get("current_period_end")
    if not pe:  # newer API versions keep it on the subscription item
        items = (sub.get("items") or {}).get("data") or []
        pe = items[0].get("current_period_end") if items else None
    return _ts(pe)


def _apply_subscription(db: Session, t: Tenant, sub: dict) -> None:
    status = str(sub.get("status") or "")
    plan_id = (sub.get("metadata") or {}).get("plan_id") or ""
    if plan_id not in PAID_PLANS or not db.get(Plan, plan_id):
        plan_id = t.plan_id if t.plan_id in PAID_PLANS else ""
    t.stripe_subscription_id = str(sub.get("id") or t.stripe_subscription_id or "") or None
    t.cancel_at_period_end = bool(sub.get("cancel_at_period_end"))
    pe = _period_end(sub)
    if pe:
        t.current_period_end = pe
    if status in ("active", "trialing"):
        t.subscription_status, t.past_due_since = status, None
        if plan_id:
            set_plan(db, t, plan_id)
    elif status == "past_due":
        t.subscription_status = "past_due"
        t.past_due_since = t.past_due_since or datetime.now(timezone.utc)
        if plan_id and t.plan_id == "trial":
            set_plan(db, t, plan_id)  # first payment failed on a brand-new subscription: keep what they chose during the grace
    elif status in ("canceled", "unpaid", "incomplete_expired"):
        _downgrade(db, t, status)
    # "incomplete" (first payment still pending): change nothing


def _downgrade(db: Session, t: Tenant, why: str) -> None:
    t.subscription_status, t.past_due_since, t.cancel_at_period_end = "canceled", None, False
    t.stripe_subscription_id = None
    if t.plan_id != "trial":
        set_plan(db, t, "trial")
    else:
        enforce_plan_limits(db, t)
    _audit(db, t, "billing.downgrade", why)


def apply_event(db: Session, event: dict) -> str:
    """Apply one verified event. Returns 'applied' | 'ignored'. Raises on unexpected data so Stripe retries."""
    typ, obj = str(event.get("type") or ""), (event.get("data") or {}).get("object") or {}
    if typ == "checkout.session.completed":
        if obj.get("mode") != "subscription":
            return "ignored"
        t = _find_tenant(db, obj)
        if not t:
            return "ignored"
        if obj.get("customer"):
            t.stripe_customer_id = str(obj["customer"])
        pid = (obj.get("metadata") or {}).get("plan_id") or ""
        t.stripe_subscription_id = str(obj.get("subscription") or "") or t.stripe_subscription_id
        if pid in PAID_PLANS and db.get(Plan, pid) is not None and obj.get("payment_status") in ("paid", "no_payment_required"):
            t.subscription_status, t.past_due_since = "active", None
            set_plan(db, t, pid)
            _audit(db, t, "billing.subscribed", pid)
        return "applied"
    if typ in ("customer.subscription.created", "customer.subscription.updated"):
        t = _find_tenant(db, obj)
        if not t:
            return "ignored"
        _apply_subscription(db, t, obj)
        return "applied"
    if typ == "customer.subscription.deleted":
        t = _find_tenant(db, obj)
        if not t:
            return "ignored"
        _downgrade(db, t, "subscription deleted")
        return "applied"
    if typ == "invoice.payment_failed":
        t = _find_tenant(db, obj)
        if not t or not t.stripe_subscription_id:
            return "ignored"
        t.subscription_status = "past_due"
        t.past_due_since = t.past_due_since or datetime.now(timezone.utc)
        _audit(db, t, "billing.payment_failed", "grace period started")
        return "applied"
    if typ == "invoice.paid":
        t = _find_tenant(db, obj)
        if not t or not t.stripe_subscription_id:
            return "ignored"
        if t.subscription_status == "past_due":
            t.subscription_status, t.past_due_since = "active", None
            _audit(db, t, "billing.payment_recovered", "")
        return "applied"
    return "ignored"


def process_webhook(db: Session, payload: bytes, header: str) -> tuple[int, dict]:
    """Verify, de-duplicate and apply. Returns (http_status, body)."""
    import json

    if not verify_signature(payload, header, settings.stripe_webhook_secret):
        return 400, {"error": "invalid signature"}
    try:
        event = json.loads(payload)
        eid = str(event["id"])
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "invalid payload"}
    try:
        db.add(StripeEvent(id=eid[:80], type=str(event.get("type", ""))[:80]))
        db.flush()
    except IntegrityError:
        db.rollback()
        return 200, {"ok": True, "duplicate": True}
    try:
        result = apply_event(db, event)
        db.commit()
    except Exception:  # noqa: BLE001  roll back the event id too, so Stripe's retry is processed
        db.rollback()
        log.exception("stripe event %s failed", eid)
        return 500, {"error": "processing failed"}
    return 200, {"ok": True, "result": result}


def expire_grace(db: Session, now: datetime | None = None) -> int:
    """Past-due workspaces fall back to Trial once the grace period ends. Called hourly by the worker."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=GRACE_DAYS)
    rows = db.query(Tenant).filter(Tenant.subscription_status == "past_due", Tenant.past_due_since.isnot(None), Tenant.past_due_since < cutoff).all()
    for t in rows:
        _downgrade(db, t, "payment overdue beyond grace period")
    if rows:
        db.commit()
    return len(rows)


def billing_state(t: Tenant) -> dict:
    grace_ends = (t.past_due_since + timedelta(days=GRACE_DAYS)).isoformat() if t.past_due_since else None
    return {"enabled": enabled(), "status": t.subscription_status or "", "renewsAt": t.current_period_end.isoformat() if t.current_period_end else None,
            "cancelAtPeriodEnd": bool(t.cancel_at_period_end), "graceEndsAt": grace_ends, "hasCustomer": bool(t.stripe_customer_id)}
