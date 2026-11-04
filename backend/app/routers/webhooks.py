"""Customer webhook endpoints: register (public https only), rotate secret, test ping, delivery log. Deliveries are signed with HMAC-SHA256."""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Ctx, need_role
from ..events import EVENTS, make_delivery
from ..models import AuditLog, WebhookDelivery, WebhookEndpoint
from ..netguard import UnsafeUrl, assert_public_url

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class EndpointIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    description: str = Field(default="", max_length=120)
    events: list[str] = Field(default_factory=lambda: list(EVENTS))


class EndpointPatch(BaseModel):
    active: bool | None = None
    description: str | None = Field(default=None, max_length=120)
    events: list[str] | None = None


def _ep(e: WebhookEndpoint) -> dict:
    return {"id": e.id, "url": e.url, "description": e.description, "events": e.events.split(","), "active": e.active, "createdAt": e.created_at.isoformat()}


def _events(evs: list[str]) -> str:
    clean = [x for x in dict.fromkeys(evs) if x in EVENTS]
    if not clean:
        raise HTTPException(422, f"Choose at least one event: {', '.join(EVENTS)}")
    return ",".join(clean)


def _own(db: Session, ctx: Ctx, endpoint_id: str) -> WebhookEndpoint:
    e = db.query(WebhookEndpoint).filter(WebhookEndpoint.id == endpoint_id, WebhookEndpoint.tenant_id == ctx.tenant.id).first()
    if not e:
        raise HTTPException(404, "webhook not found")
    return e


@router.get("")
def list_endpoints(ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    rows = db.query(WebhookEndpoint).filter(WebhookEndpoint.tenant_id == ctx.tenant.id).order_by(WebhookEndpoint.created_at.desc()).all()
    return {"endpoints": [_ep(e) for e in rows], "events": list(EVENTS)}


@router.post("", status_code=201)
async def create_endpoint(body: EndpointIn, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    try:
        await assert_public_url(body.url)
    except UnsafeUrl as e:
        raise HTTPException(422, str(e))
    if db.query(WebhookEndpoint).filter(WebhookEndpoint.tenant_id == ctx.tenant.id).count() >= 10:
        raise HTTPException(409, "You can have up to 10 webhook endpoints.")
    secret = "whsec_" + secrets.token_urlsafe(24)
    e = WebhookEndpoint(tenant_id=ctx.tenant.id, url=body.url.strip(), secret=secret, events=_events(body.events), description=body.description.strip())
    db.add(e)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="webhook.create", detail=e.url))
    db.commit()
    return {**_ep(e), "secret": secret}  # shown once


@router.patch("/{endpoint_id}")
def patch_endpoint(endpoint_id: str, body: EndpointPatch, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    e = _own(db, ctx, endpoint_id)
    if body.active is not None:
        e.active = body.active
    if body.description is not None:
        e.description = body.description.strip()
    if body.events is not None:
        e.events = _events(body.events)
    db.commit()
    return _ep(e)


@router.post("/{endpoint_id}/rotate-secret")
def rotate_secret(endpoint_id: str, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    e = _own(db, ctx, endpoint_id)
    e.secret = "whsec_" + secrets.token_urlsafe(24)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="webhook.rotate", detail=e.url))
    db.commit()
    return {"secret": e.secret}  # shown once


@router.delete("/{endpoint_id}", status_code=204)
def delete_endpoint(endpoint_id: str, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    e = _own(db, ctx, endpoint_id)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="webhook.delete", detail=e.url))
    db.delete(e)
    db.commit()


@router.post("/{endpoint_id}/test", status_code=202)
def send_test(endpoint_id: str, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    e = _own(db, ctx, endpoint_id)
    d = make_delivery(db, e, "ping", None, {"message": "This is a test event from ConnectFlow."})
    db.commit()
    return {"deliveryId": d.id}


@router.get("/deliveries")
def deliveries(endpoint_id: str | None = None, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    q = db.query(WebhookDelivery).filter(WebhookDelivery.tenant_id == ctx.tenant.id)
    if endpoint_id:
        q = q.filter(WebhookDelivery.endpoint_id == endpoint_id)
    return [
        {"id": d.id, "endpointId": d.endpoint_id, "event": d.event, "status": d.status, "attempts": d.attempts, "lastStatus": d.last_status,
         "error": d.last_error, "createdAt": d.created_at.isoformat(), "nextAttemptAt": d.next_attempt_at.isoformat() if d.status == "retrying" else None}
        for d in q.order_by(WebhookDelivery.created_at.desc()).limit(50).all()
    ]


@router.post("/deliveries/{delivery_id}/retry", status_code=202)
def retry(delivery_id: str, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    from datetime import datetime, timezone

    d = db.query(WebhookDelivery).filter(WebhookDelivery.id == delivery_id, WebhookDelivery.tenant_id == ctx.tenant.id).first()
    if not d:
        raise HTTPException(404, "delivery not found")
    d.status, d.attempts, d.next_attempt_at, d.last_error = "pending", 0, datetime.now(timezone.utc), ""
    db.commit()
    return {"ok": True}
