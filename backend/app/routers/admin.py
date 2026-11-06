"""Platform-admin API: tenants, users, plans overview. Every route requires the platform_admin dependency; message content is never exposed."""
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import stripe_billing as sb
from ..db import get_db
from ..deps import Ctx, platform_admin
from ..models import AuditLog, Instance, Membership, Plan, Tenant, User
from ..sqlutil import rows_to_dict

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats")
def stats(_: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    by_status = rows_to_dict(db.query(Instance.status, func.count()).group_by(Instance.status).all())
    return {
        "tenants": db.query(Tenant).count(),
        "users": db.query(User).count(),
        "instances": db.query(Instance).count(),
        "instancesByStatus": by_status,
    }


@router.get("/tenants")
def tenants(_: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    inst_counts = rows_to_dict(db.query(Instance.tenant_id, func.count()).group_by(Instance.tenant_id).all())
    owners = {m.tenant_id: u.email for m, u in db.query(Membership, User).join(User, User.id == Membership.user_id).filter(Membership.role == "owner").all()}
    return [
        {"id": t.id, "name": t.name, "owner": owners.get(t.id, ""), "plan": t.plan_id, "status": t.status,
         "instances": inst_counts.get(t.id, 0), "createdAt": t.created_at.isoformat()}
        for t in db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    ]


class TenantPatch(BaseModel):
    status: str | None = None  # active | suspended
    plan_id: str | None = None


@router.patch("/tenants/{tenant_id}")
def patch_tenant(tenant_id: str, body: TenantPatch, ctx: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> dict[str, Any]:
    t = db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "tenant not found")
    if body.status:
        if body.status not in ("active", "suspended"):
            raise HTTPException(400, "bad status")
        t.status = body.status
    if body.plan_id:
        if not db.get(Plan, body.plan_id):
            raise HTTPException(400, "unknown plan")
        sb.set_plan(db, t, body.plan_id)  # also flags numbers over the new limit
    db.add(AuditLog(tenant_id=t.id, user_id=ctx.user.id, action="admin.tenant.patch", detail=body.model_dump_json(exclude_none=True)))
    db.commit()
    return {"ok": True}


@router.get("/instances")
def all_instances(_: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    names = {t.id: t.name for t in db.query(Tenant).all()}
    return [
        {"id": i.id, "tenant": names.get(i.tenant_id, "?"), "label": i.label, "phone": i.phone, "status": i.status,
         "engine": i.engine, "node": i.node_id, "createdAt": i.created_at.isoformat()}
        for i in db.query(Instance).order_by(Instance.created_at.desc()).limit(500).all()
    ]


@router.get("/audit")
def audit(_: Ctx = Depends(platform_admin), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [{"at": a.at.isoformat(), "tenantId": a.tenant_id, "userId": a.user_id, "action": a.action, "detail": a.detail}
            for a in db.query(AuditLog).order_by(AuditLog.id.desc()).limit(200).all()]
