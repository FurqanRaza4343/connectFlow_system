"""Lead-gen engine: batches, research, drafts, paced sender with warm-up, reply handling, follow-ups, scheduler state.
Ported from legacy/orchestrator/src/{outreach,webhook,scheduler}.mjs and made tenant-aware. See docs/M3_CONTRACT.md."""
import random
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import ai, wa_client as wa
from .alerts import preview, send_alert
from .config import settings
from .deps import rds
from .enrich import fetch_site_text
from .models import Batch, Draft, Instance, Lead, Message, Notification, OutreachProfile, Tenant
from .quota import refund, reserve_message

WARMUP = [5, 10, 15, 20, 30, 40]  # first-message cap by number age in days (last value repeats)
STOP_RE = re.compile(r"(?<!\bbus )\b(stop(?!\s+(?:by|at|in|over|near|on)\b)|unsubscribe|remove me|do not contact|don'?t contact|not interested|no thanks|mat bhej|band kar|nahi chahiye|na bhejein|ruk jao)\b", re.I)
OPEN_DRAFT = ("pending", "approved", "sending")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


# ------------------------------------------------------------------ profile / time helpers
def profile_of(db: Session, tenant_id: str) -> OutreachProfile:
    p = db.get(OutreachProfile, tenant_id)
    if not p:
        p = OutreachProfile(tenant_id=tenant_id)
        db.add(p)
        db.commit()
    return p


def tz_of(p: OutreachProfile) -> ZoneInfo:
    try:
        return ZoneInfo(p.timezone or "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def local_day_start(p: OutreachProfile) -> datetime:
    n = utcnow().astimezone(tz_of(p))
    return n.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _hm(s: str) -> int:
    h, m = (s or "00:00").split(":")
    return int(h) * 60 + int(m)


def in_active_hours(p: OutreachProfile, at: datetime | None = None) -> bool:
    loc = (at or utcnow()).astimezone(tz_of(p))
    cur, a, b = loc.hour * 60 + loc.minute, _hm(p.active_from), _hm(p.active_to)
    return a <= cur < b if a <= b else (cur >= a or cur < b)


def next_active_start(p: OutreachProfile, base: datetime) -> datetime:
    t = max(base, utcnow())
    for _ in range(48 * 60 // 5):  # walk forward in 5-minute steps (at most 2 days)
        if in_active_hours(p, t):
            return t
        t += timedelta(minutes=5)
    return t


def notify(db: Session, tenant_id: str, type_: str, title: str, body: str = "", link: str = "", lead_id: str | None = None) -> None:
    db.add(Notification(tenant_id=tenant_id, type=type_, title=title[:200], body=body[:500], link=link, lead_id=lead_id))
    db.commit()


def lead_dict(l: Lead) -> dict:
    return {"company": l.company, "contact_name": l.contact_name, "role": l.role, "business_type": l.business_type, "address": l.address,
            "website": l.website, "linkedin": l.linkedin, "notes": l.notes}


def sent_today(db: Session, tenant_id: str, p: OutreachProfile) -> int:
    return db.query(func.count(Message.id)).filter(Message.tenant_id == tenant_id, Message.direction == "out", Message.kind == "first", Message.created_at >= local_day_start(p)).scalar() or 0


def pending_first(db: Session, tenant_id: str) -> int:
    return db.query(func.count(Draft.id)).filter(Draft.tenant_id == tenant_id, Draft.kind == "first", Draft.status.in_(OPEN_DRAFT)).scalar() or 0


def eligible_count(db: Session, tenant_id: str) -> int:
    return db.query(func.count(Lead.id)).filter(Lead.tenant_id == tenant_id, Lead.status == "new", Lead.whatsapp != "").scalar() or 0


# ------------------------------------------------------------------ numbers / warm-up
def warmup_cap(inst: Instance) -> int:
    days = max(0, (utcnow() - inst.created_at).days)
    return WARMUP[min(days, len(WARMUP) - 1)]


def instance_rows(db: Session, tenant_id: str, p: OutreachProfile) -> list[dict]:
    start = local_day_start(p)
    counts = dict(db.query(Message.instance_id, func.count(Message.id)).filter(Message.tenant_id == tenant_id, Message.direction == "out", Message.kind == "first", Message.created_at >= start).group_by(Message.instance_id).all())
    week = utcnow() - timedelta(days=7)
    sent7 = dict(db.query(Message.instance_id, func.count(Message.id)).filter(Message.tenant_id == tenant_id, Message.direction == "out", Message.created_at >= week).group_by(Message.instance_id).all())
    reps7 = dict(db.query(Message.instance_id, func.count(Message.id)).filter(Message.tenant_id == tenant_id, Message.direction == "in", Message.created_at >= week).group_by(Message.instance_id).all())
    return [{"inst": i, "cap": warmup_cap(i), "sent": counts.get(i.id, 0), "sent7d": sent7.get(i.id, 0), "replies7d": reps7.get(i.id, 0)}
            for i in db.query(Instance).filter(Instance.tenant_id == tenant_id).order_by(Instance.created_at).all()]


def pick_instance(rows: list[dict]) -> Instance | None:
    """Only connected numbers that are switched on for outreach (API-only numbers are never used) and still under their warm-up cap."""
    ok = [r for r in rows if r["inst"].status == "ready" and r["inst"].outreach_enabled and r["sent"] < r["cap"]]
    return min(ok, key=lambda r: r["sent"])["inst"] if ok else None


# ------------------------------------------------------------------ drafting
async def research_lead(db: Session, lead: Lead, p: OutreachProfile, company: str, force: bool = False) -> str:
    if lead.research and not force:
        return lead.research
    site = await fetch_site_text(lead.website)
    lead.research = (await ai.research(lead_dict(lead), p, company, site))[:4000]
    db.commit()
    return lead.research


def _fill(tpl: str, lead: Lead, p: OutreachProfile) -> str:
    return tpl.replace("{{company}}", lead.company).replace("{{sender}}", p.sender_name or "")


async def make_first_draft(db: Session, lead: Lead, p: OutreachProfile, company: str, batch_id: str | None = None, set_queued: bool = True) -> Draft:
    research = await research_lead(db, lead, p, company)
    source, note = "ai", ""
    try:
        body = await ai.write_first(lead_dict(lead), p, company, research)
        if not body:
            raise ai.AIError("empty message")
    except ai.AIError as e:
        if not p.fallback_template.strip():
            raise
        body, source, note = _fill(p.fallback_template, lead, p), "fallback", f"AI unavailable ({str(e)[:80]}): fallback template used. Regenerate before sending."
    status = "approved" if (not p.require_approval and not p.dry_run and source == "ai") else "pending"
    d = Draft(tenant_id=lead.tenant_id, lead_id=lead.id, kind="first", body=body, status=status, source=source, note=note, batch_id=batch_id)
    db.add(d)
    if set_queued:
        lead.status = "queued"
    db.commit()
    return d


async def run_batch(db: Session, tenant_id: str, trigger: str = "manual", limit: int | None = None) -> dict:
    tenant = db.get(Tenant, tenant_id)
    p = profile_of(db, tenant_id)
    mode = "dry" if p.dry_run else ("approval" if p.require_approval else "live")
    b = Batch(tenant_id=tenant_id, trigger=trigger, mode=mode)
    db.add(b)
    db.commit()
    budget = max(0, p.daily_limit - sent_today(db, tenant_id, p) - pending_first(db, tenant_id))
    take = min(limit or p.batch_size, budget)
    leads = db.query(Lead).filter(Lead.tenant_id == tenant_id, Lead.status == "new", Lead.whatsapp != "").order_by(Lead.created_at, Lead.id).limit(take).all() if take > 0 else []
    for lead in leads:
        b.processed += 1
        try:
            d = await make_first_draft(db, lead, p, tenant.name, batch_id=b.id)
            b.queued += 1
            if d.status == "approved":
                pass  # the sender picks it up
        except Exception as e:  # noqa: BLE001 - one bad lead must not stop the batch
            db.rollback()
            b.failed += 1
            b.error = str(e)[:300]
    b.finished_at = utcnow()
    db.commit()
    if b.processed:
        what = f"{b.queued} message(s) drafted" + (" and waiting for your approval" if p.require_approval else "")
        notify(db, tenant_id, "batch", f"Batch complete: {b.processed} lead(s) processed", f"{what}. {b.failed} failed." if b.failed else what + ".", link="approvals")
        if b.failed:
            await send_alert(db, tenant_id, "batch_failed", f"Batch finished with {b.failed} failure(s)",
                             [f"{b.processed} lead(s) processed, {b.queued} message(s) drafted, {b.failed} failed.", f"First error: {preview(b.error) or 'see the batch list in Outreach'}"])
    return {"batchId": b.id, "processed": b.processed, "queued": b.queued, "sent": b.sent, "failed": b.failed}


# ------------------------------------------------------------------ sender
class NoWhatsApp(Exception):
    pass


async def _deliver(inst: Instance, number: str, text: str) -> str:
    """Send one text. Returns the chat id. FAKE mode never touches the gateway."""
    if settings.outreach_fake_send:
        return f"{number}@c.us"
    chk = await wa.check_number(inst.openwa_id, number)
    if not chk.get("exists"):
        raise NoWhatsApp()
    chat_id = chk.get("whatsappId") or f"{number}@c.us"
    await wa.send_text(inst.openwa_id, chat_id, text)
    return chat_id


def _back_to_pending(db: Session, d: Draft, note: str) -> str:
    d.status, d.note, d.sending_at = "pending", note[:300], None
    db.commit()
    return "pending"


def _fail(db: Session, d: Draft, note: str) -> str:
    d.status, d.note, d.sending_at = "failed", note[:300], None
    db.commit()
    return "failed"


async def send_draft(db: Session, d: Draft) -> str:
    """Send one claimed ('sending') draft. Returns sent | pending | failed."""
    lead = db.get(Lead, d.lead_id)
    tenant = db.get(Tenant, d.tenant_id)
    p = profile_of(db, d.tenant_id)
    if not lead or lead.status == "stop":
        return _fail(db, d, "lead opted out or was deleted")
    if d.kind == "first" and lead.status in ("contacted", "replied"):
        return _fail(db, d, f"lead already {lead.status}")
    if not lead.whatsapp:
        return _fail(db, d, "lead has no WhatsApp number")
    rows = instance_rows(db, d.tenant_id, p)

    if d.kind == "first":
        # idempotency: never a second first message to the same lead/number (e.g. a crash between send and DB write)
        if db.query(Message.id).filter(Message.tenant_id == d.tenant_id, Message.lead_id == lead.id, Message.direction == "out", Message.kind == "first").first():
            lead.status = "contacted" if lead.status in ("new", "queued") else lead.status
            return _fail(db, d, "a first message was already sent to this lead")
        if sent_today(db, d.tenant_id, p) >= p.daily_limit:
            await send_alert(db, d.tenant_id, "daily_cap", "Daily sending limit reached",
                             [f"{p.daily_limit} first messages were sent today, which is your daily limit.", "Approved drafts stay queued and go out on the next active day. Raise the limit in Outreach > Setup if you want more."])
            return _back_to_pending(db, d, "Daily limit reached - approve again tomorrow")
        inst = pick_instance(rows)
        if not inst:
            ready = [r for r in rows if r["inst"].status == "ready"]
            enabled = [r for r in ready if r["inst"].outreach_enabled]
            if enabled:
                msg = "No connected number has warm-up capacity left today"
            elif ready:
                msg = "Outreach is switched off for every connected number - turn it on for one under WhatsApp numbers"
            else:
                msg = "No connected WhatsApp number - link one first"
            return _back_to_pending(db, d, msg)
    else:
        # replies and follow-ups stay on the number the conversation started on - never silently switch numbers
        pin_id = lead.instance_id or db.query(Message.instance_id).filter(Message.tenant_id == d.tenant_id, Message.lead_id == lead.id, Message.instance_id.isnot(None)).order_by(Message.created_at.desc()).limit(1).scalar()
        if pin_id:
            pinned = next((r["inst"] for r in rows if r["inst"].id == pin_id), None)
            if pinned is None:
                return _back_to_pending(db, d, "The number this conversation started on was removed - send from a connected number via a new message")
            if pinned.status != "ready":
                return _back_to_pending(db, d, f"Number {pinned.phone or pinned.label} is disconnected - reconnect it to reply")
            inst = pinned
        else:
            inst = next((r["inst"] for r in rows if r["inst"].status == "ready" and r["inst"].outreach_enabled), None)
            if not inst:
                return _back_to_pending(db, d, "No connected WhatsApp number - link one first")

    try:
        ev = reserve_message(db, d.tenant_id, inst.id, None)
    except HTTPException as e:
        return _back_to_pending(db, d, str(e.detail))
    try:
        chat_id = await _deliver(inst, lead.whatsapp, d.body)
    except NoWhatsApp:
        refund(db, ev)
        lead.status = "no_whatsapp"
        return _fail(db, d, "number is not on WhatsApp")
    except wa.WAError as e:
        refund(db, ev)
        notify(db, d.tenant_id, "failed", f"Send failed: {lead.company}", str(e)[:200], link="approvals", lead_id=lead.id)
        return _fail(db, d, str(e))
    db.add(Message(tenant_id=d.tenant_id, lead_id=lead.id, instance_id=inst.id, chat_id=chat_id, direction="out", body=d.body, kind=d.kind))
    d.status, d.sending_at, d.note = "sent", None, ""
    if d.kind == "first":
        lead.status, lead.sent_at, lead.first_message, lead.instance_id, lead.followup_stage = "contacted", utcnow(), d.body, inst.id, 0
    db.commit()
    return "sent"


def claim_next(db: Session, tenant_id: str) -> Draft | None:
    d = db.query(Draft).filter(Draft.tenant_id == tenant_id, Draft.status == "approved", Draft.not_before <= utcnow()).order_by(Draft.created_at).with_for_update(skip_locked=True).first()
    if d:
        d.status, d.sending_at = "sending", utcnow()
        db.commit()
    return d


def recover_interrupted(db: Session) -> int:
    """A draft stuck in 'sending' means a worker died mid-send. We cannot know whether WhatsApp got it: mark failed for a human to check."""
    n = db.query(Draft).filter(Draft.status == "sending").update({Draft.status: "failed", Draft.note: "Interrupted by a restart - check the chat in WhatsApp before sending again", Draft.sending_at: None}, synchronize_session=False)
    db.commit()
    return n


def queue_state(db: Session, tenant_id: str) -> dict:
    rem = db.query(Draft).filter(Draft.tenant_id == tenant_id, Draft.status.in_(("approved", "sending"))).count()
    cur = db.query(Draft, Lead.company).join(Lead, Lead.id == Draft.lead_id).filter(Draft.tenant_id == tenant_id, Draft.status == "sending").first()
    ttl = rds.ttl(f"og:gap:{tenant_id}")
    done = int(rds.get(f"og:done:{tenant_id}") or 0)
    if rem == 0 and done:
        rds.delete(f"og:done:{tenant_id}")
        done = 0
    return {"sending": rem > 0, "remaining": rem, "done": done, "nextAt": iso(utcnow() + timedelta(seconds=ttl)) if ttl and ttl > 0 else None, "current": cur[1] if cur else None}


# ------------------------------------------------------------------ inbound replies
def _history(db: Session, lead_id: str, limit: int = 30) -> list[dict]:
    rows = db.query(Message).filter(Message.lead_id == lead_id).order_by(Message.created_at.desc()).limit(limit).all()
    return [{"direction": m.direction, "body": m.body} for m in reversed(rows)]


async def handle_inbound(db: Session, inst: Instance, number: str, text: str, chat_id: str) -> dict | None:
    tid = inst.tenant_id
    lead = None
    if chat_id:
        m = db.query(Message).filter(Message.tenant_id == tid, Message.chat_id == chat_id, Message.lead_id.isnot(None)).order_by(Message.created_at.desc()).first()
        lead = db.get(Lead, m.lead_id) if m else None
    if not lead and number:
        lead = db.query(Lead).filter(Lead.tenant_id == tid, Lead.whatsapp == number).first()
    if not lead or lead.status not in ("contacted", "replied", "stop", "queued"):
        return None
    p = profile_of(db, tid)
    tenant = db.get(Tenant, tid)
    db.add(Message(tenant_id=tid, lead_id=lead.id, instance_id=inst.id, chat_id=chat_id or f"{number}@c.us", direction="in", body=text, kind="reply"))
    if not lead.instance_id:
        lead.instance_id = inst.id  # the conversation lives on the number the lead wrote to
    db.commit()
    via = inst.phone or inst.label

    if len(text) <= 60 and STOP_RE.search(text):
        lead.status, lead.last_reply, lead.followup_stage = "stop", text[:2000], 99
        db.query(Draft).filter(Draft.lead_id == lead.id, Draft.status.in_(("pending", "approved"))).update({Draft.status: "discarded", Draft.note: "lead opted out"}, synchronize_session=False)
        db.commit()
        notify(db, tid, "reply", f"{lead.company} opted out", text[:200], link="inbox", lead_id=lead.id)
        return {"lead": lead.id, "action": "stop"}

    lead.status, lead.last_reply = "replied", text[:2000]
    db.commit()
    try:
        research = lead.research
        out = await ai.write_reply(lead_dict(lead), p, tenant.name, research, _history(db, lead.id))
    except ai.AIError as e:
        notify(db, tid, "needs_human", f"{lead.company} replied (AI unavailable)", text[:200], link="inbox", lead_id=lead.id)
        await send_alert(db, tid, "needs_human", f"{lead.company} replied and needs you", [f"{lead.company} wrote on {via}: \"{preview(text)}\"", "The AI could not draft an answer - open the Inbox to reply yourself."])
        return {"lead": lead.id, "action": "ai_unavailable", "error": str(e)}
    if out["intent"] == "stop":
        lead.status, lead.followup_stage = "stop", 99
        db.query(Draft).filter(Draft.lead_id == lead.id, Draft.status.in_(("pending", "approved"))).update({Draft.status: "discarded", Draft.note: "lead opted out"}, synchronize_session=False)
        db.commit()
        notify(db, tid, "reply", f"{lead.company} is not interested", text[:200], link="inbox", lead_id=lead.id)
        return {"lead": lead.id, "action": "stop"}
    reply = (out.get("reply") or "").strip()
    needs_human = bool(out["needs_human"])
    note = (f"NEEDS HUMAN: {out['topic']}" if out["topic"] in ("pricing", "complaint") else "NEEDS HUMAN") if needs_human else ""
    if reply:
        auto = p.auto_reply and not needs_human and not p.dry_run
        d = Draft(tenant_id=tid, lead_id=lead.id, kind="reply", body=reply, incoming=text, status="approved" if auto else "pending", source="ai", needs_human=needs_human, note=note,
                  not_before=utcnow() + timedelta(seconds=random.uniform(15, 45)) if auto else utcnow())
        db.add(d)
        db.commit()
    notify(db, tid, "needs_human" if needs_human else "reply", f"{lead.company} replied" + (" - needs you" if needs_human else ""), text[:200], link="inbox", lead_id=lead.id)
    if needs_human:
        await send_alert(db, tid, "needs_human", f"{lead.company} replied and needs you", [f"{lead.company} wrote on {via}: \"{preview(text)}\"", f"Why: {note.replace('NEEDS HUMAN: ', '') or 'a person should answer this one'}. Open the Inbox to reply."])
    return {"lead": lead.id, "action": "reply_drafted", "needsHuman": needs_human}


# ------------------------------------------------------------------ follow-ups
async def queue_followups(db: Session, tenant_id: str, p: OutreachProfile, limit: int = 20) -> int:
    if not p.followups_enabled:
        return 0
    tenant = db.get(Tenant, tenant_id)
    now = utcnow()
    n = 0
    leads = db.query(Lead).filter(Lead.tenant_id == tenant_id, Lead.status == "contacted", Lead.followup_stage.in_((0, 1)), Lead.sent_at.isnot(None)).order_by(Lead.sent_at).all()
    for lead in leads:
        if n >= limit:
            break
        days = p.followup_day1 if lead.followup_stage == 0 else p.followup_day2
        if lead.sent_at > now - timedelta(days=days):
            continue
        if db.query(Message.id).filter(Message.lead_id == lead.id, Message.direction == "in").first():
            continue
        if db.query(Draft.id).filter(Draft.lead_id == lead.id, Draft.kind == "followup", Draft.status.in_(OPEN_DRAFT)).first():
            continue
        try:
            body = await ai.write_followup(lead_dict(lead), p, tenant.name, lead.research, _history(db, lead.id), lead.followup_stage + 1)
        except ai.AIError:
            continue
        if not body:
            continue
        status = "approved" if (not p.require_approval and not p.dry_run) else "pending"
        db.add(Draft(tenant_id=tenant_id, lead_id=lead.id, kind="followup", body=body, status=status, source="ai"))
        lead.followup_stage += 1
        db.commit()
        n += 1
    return n


# ------------------------------------------------------------------ scheduler
def last_timer_batch(db: Session, tenant_id: str) -> Batch | None:
    return db.query(Batch).filter(Batch.tenant_id == tenant_id, Batch.trigger == "timer").order_by(Batch.started_at.desc()).first()


def scheduler_state(db: Session, tenant_id: str) -> dict:
    p = profile_of(db, tenant_id)
    last = db.query(Batch).filter(Batch.tenant_id == tenant_id).order_by(Batch.started_at.desc()).first()
    timer = last_timer_batch(db, tenant_id)
    nxt = None
    if p.auto_run:
        base = (timer.started_at + timedelta(hours=p.interval_hours)) if timer else utcnow()
        nxt = next_active_start(p, base)
    rows = instance_rows(db, tenant_id, p)
    return {
        "autoRun": p.auto_run, "dryRun": p.dry_run, "requireApproval": p.require_approval, "inActiveHours": in_active_hours(p), "nextRunAt": iso(nxt),
        "lastBatch": {"id": last.id, "at": iso(last.started_at), "processed": last.processed, "queued": last.queued, "sent": last.sent, "failed": last.failed} if last else None,
        "sentToday": sent_today(db, tenant_id, p), "dailyLimit": p.daily_limit, "eligibleLeads": eligible_count(db, tenant_id),
        "instances": [{"id": r["inst"].id, "label": r["inst"].label, "status": r["inst"].status, "phone": r["inst"].phone, "warmupCap": r["cap"], "sentToday": r["sent"], "sent7d": r["sent7d"], "replies7d": r["replies7d"], "outreachEnabled": r["inst"].outreach_enabled} for r in rows],
    }


async def scheduler_tick(db: Session) -> int:
    """Run due batches for every workspace with auto_run, and queue follow-ups. Returns number of batches started."""
    started = 0
    for p in db.query(OutreachProfile).filter(OutreachProfile.auto_run.is_(True)).all():
        t = db.get(Tenant, p.tenant_id)
        if not t or t.status != "active" or not in_active_hours(p):
            continue
        last = last_timer_batch(db, p.tenant_id)
        if last and last.started_at + timedelta(hours=p.interval_hours) > utcnow():
            continue
        await run_batch(db, p.tenant_id, "timer")
        started += 1
    for p in db.query(OutreachProfile).filter(OutreachProfile.followups_enabled.is_(True)).all():
        t = db.get(Tenant, p.tenant_id)
        if t and t.status == "active":
            await queue_followups(db, p.tenant_id, p)
    return started


# ------------------------------------------------------------------ enrichment queue (Redis, safe for several workers)
def enqueue_enrich(tenant_id: str, ids: list[str]) -> int:
    if not ids:
        return 0
    rds.rpush(f"og:enrichq:{tenant_id}", *ids)
    rds.hincrby(f"og:enrich:{tenant_id}", "total", len(ids))
    rds.expire(f"og:enrich:{tenant_id}", 86400)
    return len(ids)


def enrich_status(tenant_id: str) -> dict:
    h = rds.hgetall(f"og:enrich:{tenant_id}")
    total, done = int(h.get("total", 0)), int(h.get("done", 0))
    running = rds.llen(f"og:enrichq:{tenant_id}") > 0 or (total > done)
    if total and done >= total and rds.llen(f"og:enrichq:{tenant_id}") == 0:
        rds.delete(f"og:enrich:{tenant_id}")
        return {"running": False, "done": done, "total": total}
    return {"running": running, "done": done, "total": total}
