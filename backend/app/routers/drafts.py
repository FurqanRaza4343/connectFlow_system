"""Approval queue for AI drafts: list, edit, regenerate, approve (queues the send), reject. Scoped to the caller's workspace."""
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ai, outreach as og
from ..db import get_db
from ..deps import Ctx, need_role, TCtx
from ..models import Draft, Instance, Lead, Tenant
from ..sqlutil import rows_to_dict

router = APIRouter(prefix="/api/drafts", tags=["drafts"])
STATUSES = ("pending", "approved", "sending", "sent", "discarded", "failed")


def _via(i: Instance | None) -> dict[str, Any] | None:
    return {"id": i.id, "label": i.label, "phone": i.phone, "status": i.status} if i else None


def draft_out(d: Draft, lead: Lead | None, insts: dict | None = None) -> dict:
    # replies / follow-ups are pinned to the number of the conversation; a first message gets its number when it is sent
    pinned = (insts or {}).get(lead.instance_id) if (lead and insts is not None and d.kind != "first") else None
    return {"via": _via(pinned), "id": d.id, "leadId": d.lead_id, "company": lead.company if lead else "", "whatsapp": lead.whatsapp if lead else "", "kind": d.kind, "body": d.body, "incoming": d.incoming,
            "status": d.status, "note": d.note, "source": d.source, "needsHuman": d.needs_human, "createdAt": og.iso(d.created_at)}


def _own(db: Session, ctx: TCtx, draft_id: str) -> Draft:
    d = db.query(Draft).filter(Draft.id == draft_id, Draft.tenant_id == ctx.tenant.id).first()
    if not d:
        raise HTTPException(404, "draft not found")
    return d


@router.get("")
def list_drafts(status: str = "open", kind: str = "", page: int = Query(1, ge=1), ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> dict[str, Any]:
    q = db.query(Draft, Lead).join(Lead, Lead.id == Draft.lead_id).filter(Draft.tenant_id == ctx.tenant.id)
    if status == "open":
        q = q.filter(Draft.status.in_(("pending", "approved", "sending", "failed")))
    elif status in STATUSES:
        q = q.filter(Draft.status == status)
    if kind in ("first", "followup", "reply"):
        q = q.filter(Draft.kind == kind)
    total, size = q.count(), 30
    rows = q.order_by(Draft.needs_human.desc(), Draft.created_at.desc()).offset((page - 1) * size).limit(size).all()
    counts = rows_to_dict(db.query(Draft.status, func.count(Draft.id)).filter(Draft.tenant_id == ctx.tenant.id).group_by(Draft.status).all())
    insts = {i.id: i for i in db.query(Instance).filter(Instance.tenant_id == ctx.tenant.id).all()}
    return {"rows": [draft_out(d, l, insts) for d, l in rows], "total": total, "page": page, "pages": max(1, -(-total // size)), "counts": {s: counts.get(s, 0) for s in STATUSES}}


@router.get("/queue")
def queue(ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> Any:
    return og.queue_state(db, ctx.tenant.id)


class BodyIn(BaseModel):
    body: str = Field(min_length=1, max_length=4096)


@router.patch("/{draft_id}")
def edit(draft_id: str, body: BodyIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict:
    d = _own(db, ctx, draft_id)
    if d.status not in ("pending", "failed"):
        raise HTTPException(409, "Only pending or failed drafts can be edited")
    d.body, d.source, d.note = body.body.strip(), "manual", ""
    if d.status == "failed":
        d.status = "pending"
    db.commit()
    return draft_out(d, db.get(Lead, d.lead_id))


class RegenIn(BaseModel):
    instruction: str = Field(default="", max_length=300)


@router.post("/{draft_id}/regenerate")
async def regenerate(draft_id: str, body: RegenIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict:
    d = _own(db, ctx, draft_id)
    if d.status not in ("pending", "failed"):
        raise HTTPException(409, "Only pending or failed drafts can be regenerated")
    lead = db.get(Lead, d.lead_id)
    if not lead:
        raise HTTPException(404, "lead not found")
    p = og.profile_of(db, ctx.tenant.id)
    try:
        text = await ai.rewrite(og.lead_dict(lead), p, ctx.tenant.name, lead.research, d.body, body.instruction, kind=d.kind, incoming=d.incoming)
    except ai.AIError as e:
        raise HTTPException(getattr(e, "status", 502), str(e) if isinstance(e, ai.AILimitError) else f"AI: {e}")
    if not text:
        raise HTTPException(502, "AI returned an empty message")
    d.body, d.source, d.instruction, d.note = text, "ai", body.instruction[:300], ""
    if d.status == "failed":
        d.status = "pending"
    db.commit()
    return draft_out(d, lead)


class IdsIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)


@router.post("/approve")
def approve(body: IdsIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    if og.profile_of(db, ctx.tenant.id).dry_run:
        raise HTTPException(409, "Dry-run is ON (Outreach > Setup). Turn it off to approve and send messages.")
    from ..config import settings as _cfg
    if not _cfg.outreach_fake_send:
        ready = db.query(Instance).filter(Instance.tenant_id == ctx.tenant.id, Instance.status == "ready").all()
        if not ready:
            raise HTTPException(409, "No WhatsApp number is connected. Link a number under WhatsApp numbers first, then approve again.")
        if not any(i.outreach_enabled for i in ready):
            raise HTTPException(409, "Outreach is switched off for every connected number (they are API-only). Turn it on for a number under WhatsApp numbers, then approve again.")
    n = db.query(Draft).filter(Draft.tenant_id == ctx.tenant.id, Draft.id.in_(body.ids), Draft.status.in_(("pending", "failed"))).update({Draft.status: "approved", Draft.note: ""}, synchronize_session=False)
    db.commit()
    return {"approved": n}


@router.post("/cancel-sending")
def cancel(ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    n = db.query(Draft).filter(Draft.tenant_id == ctx.tenant.id, Draft.status == "approved").update({Draft.status: "pending", Draft.note: "Sending cancelled by you"}, synchronize_session=False)
    db.commit()
    return {"reset": n}


@router.post("/{draft_id}/discard")
def discard(draft_id: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    d = _own(db, ctx, draft_id)
    if d.status == "sending":
        raise HTTPException(409, "This message is being sent right now")
    if d.status == "sent":
        raise HTTPException(409, "Already sent")
    d.status = "discarded"
    lead = db.get(Lead, d.lead_id)
    if d.kind == "first" and lead and lead.status == "queued":
        lead.status = "new"  # a discarded first message puts the lead back in the pool
    db.commit()
    return {"ok": True}
