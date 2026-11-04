"""Outreach setup (profile, active hours, caps), batches, scheduler status and the inbox for the workspace."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import outreach as og
from ..db import get_db
from ..deps import Ctx, need_role
from ..models import Batch, Draft, Instance, Lead, Message, Notification
from ..quota import day_start

router = APIRouter(prefix="/api", tags=["outreach"])


@router.get("/outreach/status")
def status(ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    return og.scheduler_state(db, ctx.tenant.id)


class RunIn(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=100)


@router.post("/outreach/run-now")
async def run_now(body: RunIn | None = None, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    return await og.run_batch(db, ctx.tenant.id, "manual", (body.limit if body else None))


@router.get("/outreach/batches")
def batches(ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    rows = db.query(Batch).filter(Batch.tenant_id == ctx.tenant.id).order_by(Batch.started_at.desc()).limit(20).all()
    return [{"id": b.id, "startedAt": og.iso(b.started_at), "finishedAt": og.iso(b.finished_at), "mode": b.mode, "processed": b.processed, "queued": b.queued, "sent": b.sent, "failed": b.failed, "error": b.error} for b in rows]


# ------------------------------------------------------------------ overview
@router.get("/overview")
def overview(ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    tid = ctx.tenant.id
    c = dict(db.query(Lead.status, func.count(Lead.id)).filter(Lead.tenant_id == tid).group_by(Lead.status).all())
    names = ("new", "queued", "contacted", "replied", "stop", "no_whatsapp", "failed", "hold", "verify")
    counts = {s: c.get(s, 0) for s in names}
    counts["total"] = sum(c.values())
    counts["withNumber"] = db.query(func.count(Lead.id)).filter(Lead.tenant_id == tid, Lead.whatsapp != "").scalar() or 0
    counts["enriched"] = db.query(func.count(Lead.id)).filter(Lead.tenant_id == tid, Lead.enriched_at.isnot(None)).scalar() or 0
    reached = counts["contacted"] + counts["replied"] + counts["stop"]
    reply_rate = round((counts["replied"] + counts["stop"]) / reached * 100) if reached else 0

    start = day_start() - timedelta(days=13)
    sent = dict(db.query(func.date(Message.created_at), func.count(Message.id)).filter(Message.tenant_id == tid, Message.direction == "out", Message.created_at >= start).group_by(func.date(Message.created_at)).all())
    reps = dict(db.query(func.date(Message.created_at), func.count(Message.id)).filter(Message.tenant_id == tid, Message.direction == "in", Message.created_at >= start).group_by(func.date(Message.created_at)).all())
    daily = []
    for i in range(14):
        d = (start + timedelta(days=i)).date()
        daily.append({"day": str(d), "sent": sent.get(d, 0), "replies": reps.get(d, 0)})
    sched = og.scheduler_state(db, tid)
    recent = db.query(Notification).filter(Notification.tenant_id == tid).order_by(Notification.created_at.desc()).limit(20).all()
    return {
        "counts": counts, "sentToday": sched["sentToday"], "dailyLimit": sched["dailyLimit"], "replyRate": reply_rate,
        "pendingApprovals": db.query(Draft).filter(Draft.tenant_id == tid, Draft.status.in_(("pending", "approved", "failed"))).count(),
        "needsHuman": db.query(Draft).filter(Draft.tenant_id == tid, Draft.status == "pending", Draft.needs_human.is_(True)).count(),
        "unreadNotifications": db.query(Notification).filter(Notification.tenant_id == tid, Notification.read.is_(False)).count(),
        "daily": daily, "scheduler": sched,
        "recent": [{"at": og.iso(n.created_at), "type": n.type, "title": n.title, "body": n.body, "leadId": n.lead_id} for n in recent],
    }


# ------------------------------------------------------------------ notifications
@router.get("/notifications")
def notifications(ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    rows = db.query(Notification).filter(Notification.tenant_id == ctx.tenant.id).order_by(Notification.created_at.desc()).limit(50).all()
    unread = db.query(Notification).filter(Notification.tenant_id == ctx.tenant.id, Notification.read.is_(False)).count()
    return {"rows": [{"id": n.id, "type": n.type, "title": n.title, "body": n.body, "link": n.link, "leadId": n.lead_id, "read": n.read, "createdAt": og.iso(n.created_at)} for n in rows], "unread": unread}


class ReadIn(BaseModel):
    ids: list[str] | None = None


@router.post("/notifications/read")
def mark_read(body: ReadIn | None = None, ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    q = db.query(Notification).filter(Notification.tenant_id == ctx.tenant.id, Notification.read.is_(False))
    if body and body.ids:
        q = q.filter(Notification.id.in_(body.ids))
    q.update({Notification.read: True}, synchronize_session=False)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ inbox
@router.get("/inbox")
def inbox(filter: str = Query("all", pattern="^(all|needs_human|replied)$"), instance_id: str = "", ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    tid = ctx.tenant.id
    # a conversation = a lead that has at least one inbound message
    has_in = {lid for (lid,) in db.query(Message.lead_id).filter(Message.tenant_id == tid, Message.direction == "in").distinct().all()}
    nh = {lid for (lid,) in db.query(Draft.lead_id).filter(Draft.tenant_id == tid, Draft.status == "pending", Draft.needs_human.is_(True)).distinct().all()}
    tail = db.query(Message).filter(Message.tenant_id == tid, Message.lead_id.in_(has_in or ["-"])).distinct(Message.lead_id).order_by(Message.lead_id, Message.created_at.desc()).all()
    leads = {l.id: l for l in db.query(Lead).filter(Lead.tenant_id == tid, Lead.id.in_(has_in or ["-"])).all()}
    insts = {i.id: i for i in db.query(Instance).filter(Instance.tenant_id == tid).all()}
    rows = []
    for m in tail:
        l = leads.get(m.lead_id)
        if not l:
            continue
        conv_inst = l.instance_id or m.instance_id
        if instance_id and conv_inst != instance_id:
            continue
        i = insts.get(conv_inst)
        if filter == "needs_human" and l.id not in nh:
            continue
        if filter == "replied" and l.status != "replied":
            continue
        rows.append({"leadId": l.id, "company": l.company, "whatsapp": l.whatsapp, "status": l.status, "lastMessage": m.body[:200], "lastDirection": m.direction, "lastAt": og.iso(m.created_at), "needsHuman": l.id in nh,
                     "instanceId": conv_inst, "via": {"id": i.id, "label": i.label, "phone": i.phone, "status": i.status} if i else None})
    rows.sort(key=lambda r: r["lastAt"] or "", reverse=True)
    return rows[:100]
