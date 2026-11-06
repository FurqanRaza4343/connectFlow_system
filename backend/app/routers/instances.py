"""WhatsApp numbers: create a session, fetch the QR, status, per-number settings, disconnect and delete. Plan limits and the gateway capacity guard apply."""
from typing import Any
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import wa_client as wa
from ..config_ops import ops
from ..db import get_db
from ..deps import Ctx, need_role, require_verified, TCtx
from ..models import AuditLog, Instance, Tenant

router = APIRouter(prefix="/api/instances", tags=["instances"])


LIVE = ("created", "initializing", "qr_ready", "authenticating", "ready")  # states that hold a gateway slot (RAM)


def platform_capacity() -> int:
    """Max live sessions the platform will accept (never more than the gateway itself allows)."""
    return max(0, min(ops.platform_max_instances, ops.gateway_max_sessions or ops.platform_max_instances))


def add_to_waitlist(tenant_id: str, email: str) -> int:
    """Remember who wanted a number while we were full. Redis list `waitlist:instances`; one row per tenant."""
    try:
        r: Any = redis.Redis.from_url(ops.redis_url, decode_responses=True)  # Any: redis-py stubs type sync commands as Awaitable|...
        if r.sadd("waitlist:instances:tenants", tenant_id):
            r.rpush("waitlist:instances", json.dumps({"tenantId": tenant_id, "email": email, "at": datetime.now(timezone.utc).isoformat()}))
        return int(r.llen("waitlist:instances"))
    except redis.RedisError:
        return 0


class CreateIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)


def _out(i: Instance) -> dict:
    return {"id": i.id, "label": i.label, "phone": i.phone, "status": i.status, "engine": i.engine, "outreachEnabled": i.outreach_enabled, "overLimit": i.over_limit, "createdAt": i.created_at.isoformat()}


def _own(db: Session, ctx: TCtx, instance_id: str) -> Instance:
    """Every instance lookup goes through here: a row of another tenant is indistinguishable from a missing one."""
    inst = db.query(Instance).filter(Instance.id == instance_id, Instance.tenant_id == ctx.tenant.id).first()
    if not inst:
        raise HTTPException(404, "instance not found")
    return inst


def _gw(e: wa.WAError) -> Any:
    return HTTPException(502 if e.status < 500 else e.status, f"WhatsApp gateway: {e}")


async def _refresh(db: Session, inst: Instance) -> None:
    if not inst.openwa_id:
        return
    try:
        s = await wa.get_session(inst.openwa_id)
    except wa.WAError:
        return  # keep last known status; the webhook / next poll will fix it
    inst.status = str(s.get("status") or inst.status)
    inst.phone = str(s.get("phone") or inst.phone or "")
    db.commit()


@router.get("")
async def list_instances(ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> list[Any]:
    rows = db.query(Instance).filter(Instance.tenant_id == ctx.tenant.id).order_by(Instance.created_at).all()
    return [_out(i) for i in rows]


@router.post("", status_code=201)
async def create_instance(body: CreateIn, ctx: TCtx = Depends(need_role("admin")), db: Session = Depends(get_db)) -> Any:
    require_verified(ctx)
    # Plan limit, atomically. This handler is `async` but the DB session is synchronous: waiting for a row lock blocks the whole
    # event loop, so NO lock may be held across an `await`. Therefore: lock the tenant row, count, insert the number as a reservation
    # and COMMIT (all synchronous, microseconds), and only then talk to the gateway. A parallel call that was waiting on the lock
    # then sees the reservation in its count. Numbers in ANY state count; deleted numbers are gone. A failed gateway call removes
    # the reservation again.
    t = db.query(Tenant).filter(Tenant.id == ctx.tenant.id).with_for_update(of=Tenant).populate_existing().first()
    if t is None:
        raise HTTPException(404, "workspace not found")
    used = db.query(Instance).filter(Instance.tenant_id == t.id).count()
    if used >= t.plan.max_instances:
        db.rollback()
        return JSONResponse(status_code=402, content={
            "detail": f"Your {t.plan.name} plan allows {t.plan.max_instances} WhatsApp number{'s' if t.plan.max_instances != 1 else ''}. Upgrade to add more.",
            "limit": t.plan.max_instances, "used": used, "plan": t.plan_id, "upgradeUrl": "/#billing"})
    # Capacity guard: serialise creations (advisory lock held until the commit below) so two signups cannot both take the last slot.
    db.execute(text("select pg_advisory_xact_lock(7001)"))
    live = db.query(Instance).filter(Instance.status.in_(LIVE)).count()
    if live >= platform_capacity():
        db.rollback()
        add_to_waitlist(ctx.tenant.id, ctx.user.email)
        raise HTTPException(503, "We are at capacity right now. We added you to the waiting list and will email you as soon as a slot opens.")
    name = "cf-" + uuid.uuid4().hex[:12]
    inst = Instance(tenant_id=ctx.tenant.id, label=body.label.strip(), openwa_name=name)
    db.add(inst)
    db.commit()  # releases both locks; the reservation now counts against the plan limit and the platform capacity
    inst_id = inst.id
    try:
        s = await wa.create_session(name)
        inst.openwa_id = str(s.get("id"))
        await wa.add_webhook(inst.openwa_id)
        await wa.start_session(inst.openwa_id)
        inst.status = "initializing"
        db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="instance.create", detail=inst.label))
        db.commit()
    except BaseException as e:  # noqa: BLE001  gateway error, client disconnect, anything: give the reserved slot back
        db.rollback()
        gone = db.get(Instance, inst_id)
        if gone is not None:
            if gone.openwa_id:
                try:
                    await wa.delete_session(gone.openwa_id)
                except wa.WAError:
                    pass
            db.delete(gone)
            db.commit()
        if isinstance(e, wa.WAError):
            raise _gw(e)
        raise
    return _out(inst)


class OutreachToggleIn(BaseModel):
    outreach_enabled: bool


@router.patch("/{instance_id}")
def set_outreach(instance_id: str, body: OutreachToggleIn, ctx: TCtx = Depends(need_role("admin")), db: Session = Depends(get_db)) -> dict:
    """Per-number control: off = API-only number, the outreach sender never picks it (replies already pinned to it still go out)."""
    inst = _own(db, ctx, instance_id)
    inst.outreach_enabled = body.outreach_enabled
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="instance.outreach", detail=f"{inst.label}: {'on' if body.outreach_enabled else 'off'}"))
    db.commit()
    return _out(inst)


@router.get("/{instance_id}")
async def get_instance(instance_id: str, ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> dict:
    inst = _own(db, ctx, instance_id)
    await _refresh(db, inst)
    return _out(inst)


@router.get("/{instance_id}/qr")
async def get_qr(instance_id: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    inst = _own(db, ctx, instance_id)
    await _refresh(db, inst)
    if inst.status in ("created", "disconnected", "failed"):
        try:
            await wa.start_session(inst.openwa_id)
        except wa.WAError as e:
            raise _gw(e)
        return {"status": "initializing", "qr": None}
    qr = None
    if inst.status == "qr_ready":
        try:
            qr = (await wa.get_qr(inst.openwa_id)).get("qrCode")
        except wa.WAError:
            qr = None
    return {"status": inst.status, "qr": qr, "phone": inst.phone}


@router.post("/{instance_id}/restart")
async def restart(instance_id: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    inst = _own(db, ctx, instance_id)
    try:
        await wa.start_session(inst.openwa_id)
    except wa.WAError as e:
        raise _gw(e)
    return {"ok": True}


@router.delete("/{instance_id}", status_code=204)
async def delete_instance(instance_id: str, ctx: TCtx = Depends(need_role("admin")), db: Session = Depends(get_db)) -> None:
    inst = _own(db, ctx, instance_id)
    try:
        await wa.delete_session(inst.openwa_id)
    except wa.WAError as e:
        if e.status != 404:
            raise _gw(e)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="instance.delete", detail=inst.label))
    db.delete(inst)
    db.commit()
