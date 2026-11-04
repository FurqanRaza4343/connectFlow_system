"""Lead lists and leads: CSV import with dedupe, filters, status changes, thread view, export. All queries are filtered by tenant_id."""
import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import ai, outreach as og
from ..db import get_db
from ..deps import Ctx, need_role
from ..leadtools import FIELDS, csv_safe, normalize_phone, parse_csv, suggest_mapping
from ..alerts import validate_extras
from ..models import AuditLog, Draft, Instance, Lead, LeadList, Message, OutreachProfile
from .drafts import draft_out

router = APIRouter(prefix="/api", tags=["leads"])
STATUSES = ("new", "queued", "contacted", "replied", "stop", "no_whatsapp", "failed", "hold", "verify")
EDITABLE = ("company", "contact_name", "role", "business_type", "address", "phone_raw", "website", "email", "linkedin", "facebook", "notes", "status")
MAX_CSV = 5 * 1024 * 1024


def _profile(db: Session, tenant_id: str) -> OutreachProfile:
    p = db.get(OutreachProfile, tenant_id)
    if not p:
        p = OutreachProfile(tenant_id=tenant_id)
        db.add(p)
        db.commit()
    return p


def _out(l: Lead, insts: dict | None = None) -> dict:
    i = (insts or {}).get(l.instance_id)
    return {"via": {"id": i.id, "label": i.label, "phone": i.phone, "status": i.status} if i else None, 
        "id": l.id, "listId": l.list_id, "company": l.company, "contactName": l.contact_name, "role": l.role, "businessType": l.business_type,
        "address": l.address, "phoneRaw": l.phone_raw, "whatsapp": l.whatsapp, "country": l.country, "website": l.website, "email": l.email,
        "linkedin": l.linkedin, "facebook": l.facebook, "notes": l.notes, "status": l.status, "research": l.research,
        "firstMessage": l.first_message, "lastReply": l.last_reply, "createdAt": l.created_at.isoformat(),
        "enriched": bool(l.enriched_at), "instanceId": l.instance_id, "sentAt": l.sent_at.isoformat() if l.sent_at else None, "followupStage": l.followup_stage,
    }


def _own(db: Session, ctx: Ctx, lead_id: str) -> Lead:
    l = db.query(Lead).filter(Lead.id == lead_id, Lead.tenant_id == ctx.tenant.id).first()
    if not l:
        raise HTTPException(404, "lead not found")
    return l


# ------------------------------------------------------------------ lists
@router.get("/lead-lists")
def lists(ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    counts = dict(db.query(Lead.list_id, func.count(Lead.id)).filter(Lead.tenant_id == ctx.tenant.id).group_by(Lead.list_id).all())
    rows = db.query(LeadList).filter(LeadList.tenant_id == ctx.tenant.id).order_by(LeadList.created_at.desc()).all()
    return [{"id": x.id, "name": x.name, "source": x.source, "count": counts.get(x.id, 0), "createdAt": x.created_at.isoformat()} for x in rows]


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.patch("/lead-lists/{list_id}")
def rename_list(list_id: str, body: NameIn, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    x = db.query(LeadList).filter(LeadList.id == list_id, LeadList.tenant_id == ctx.tenant.id).first()
    if not x:
        raise HTTPException(404, "list not found")
    x.name = body.name.strip()
    db.commit()
    return {"ok": True}


@router.delete("/lead-lists/{list_id}", status_code=204)
def delete_list(list_id: str, delete_leads: bool = False, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    x = db.query(LeadList).filter(LeadList.id == list_id, LeadList.tenant_id == ctx.tenant.id).first()
    if not x:
        raise HTTPException(404, "list not found")
    if delete_leads:
        db.query(Lead).filter(Lead.list_id == x.id, Lead.tenant_id == ctx.tenant.id, Lead.status.in_(("new", "hold", "verify", "no_whatsapp", "failed"))).delete(synchronize_session=False)
    db.delete(x)
    db.commit()


# ------------------------------------------------------------------ leads CRUD
@router.get("/leads")
def list_leads(
    q: str = "", status: str = "", list_id: str = "", has_number: str = "", instance_id: str = "", page: int = Query(1, ge=1), page_size: int = Query(50, ge=10, le=200),
    ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db),
):
    base = db.query(Lead).filter(Lead.tenant_id == ctx.tenant.id)
    if status:
        base = base.filter(Lead.status == status)
    if list_id:
        base = base.filter(Lead.list_id == (None if list_id == "none" else list_id))
    if instance_id:
        base = base.filter(Lead.instance_id == instance_id)
    if has_number == "yes":
        base = base.filter(Lead.whatsapp != "")
    elif has_number == "no":
        base = base.filter(Lead.whatsapp == "")
    if q.strip():
        like = f"%{q.strip()}%"
        base = base.filter(or_(Lead.company.ilike(like), Lead.contact_name.ilike(like), Lead.whatsapp.ilike(like), Lead.website.ilike(like), Lead.email.ilike(like), Lead.address.ilike(like)))
    total = base.count()
    rows = base.order_by(Lead.created_at.desc(), Lead.id).offset((page - 1) * page_size).limit(page_size).all()
    counts = dict(db.query(Lead.status, func.count(Lead.id)).filter(Lead.tenant_id == ctx.tenant.id).group_by(Lead.status).all())
    insts = {i.id: i for i in db.query(Instance).filter(Instance.tenant_id == ctx.tenant.id).all()}
    return {"rows": [_out(l, insts) for l in rows], "total": total, "page": page, "pages": max(1, -(-total // page_size)), "counts": counts, "withNumber": db.query(func.count(Lead.id)).filter(Lead.tenant_id == ctx.tenant.id, Lead.whatsapp != "").scalar()}


class LeadIn(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    contact_name: str = Field(default="", max_length=120)
    role: str = Field(default="", max_length=200)
    business_type: str = Field(default="", max_length=120)
    address: str = Field(default="", max_length=300)
    phone_raw: str = Field(default="", max_length=200)
    website: str = Field(default="", max_length=300)
    email: str = Field(default="", max_length=254)
    linkedin: str = Field(default="", max_length=300)
    facebook: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=4000)


class LeadPatch(BaseModel):
    company: str | None = Field(default=None, min_length=1, max_length=200)
    contact_name: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=200)
    business_type: str | None = Field(default=None, max_length=120)
    address: str | None = Field(default=None, max_length=300)
    phone_raw: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=300)
    email: str | None = Field(default=None, max_length=254)
    linkedin: str | None = Field(default=None, max_length=300)
    facebook: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4000)
    status: str | None = None


def _duplicate(db: Session, tenant_id: str, whatsapp: str, company: str, address: str, exclude: str | None = None) -> bool:
    q = db.query(Lead.id).filter(Lead.tenant_id == tenant_id)
    if exclude:
        q = q.filter(Lead.id != exclude)
    if whatsapp and q.filter(Lead.whatsapp == whatsapp).first():
        return True
    if not company:
        return False
    same = q.filter(func.lower(Lead.company) == company.lower(), func.lower(Lead.address) == address.lower())
    if address:  # same company at the same address is the same business, whatever the number
        return bool(same.first())
    return (not whatsapp) and bool(same.first())  # no number and no address: only the company name can identify it


@router.post("/leads", status_code=201)
def create_lead(body: LeadIn, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    region = _profile(db, ctx.tenant.id).default_country
    wa, country = normalize_phone(body.phone_raw, region)
    if _duplicate(db, ctx.tenant.id, wa, body.company.strip(), body.address.strip()):
        raise HTTPException(409, "A lead with this WhatsApp number (or the same company and address) already exists")
    l = Lead(tenant_id=ctx.tenant.id, whatsapp=wa, country=country, **body.model_dump())
    l.status = "new" if wa else "verify"
    db.add(l)
    db.commit()
    return _out(l)


@router.patch("/leads/{lead_id}")
def patch_lead(lead_id: str, body: LeadPatch, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    l = _own(db, ctx, lead_id)
    data = body.model_dump(exclude_unset=True)
    if "status" in data:
        if data["status"] not in STATUSES:
            raise HTTPException(422, "unknown status")
        if data["status"] in ("contacted", "replied", "queued") and l.status in ("new", "hold", "verify"):
            raise HTTPException(422, "That status is set automatically when a message is sent")
    if "phone_raw" in data:
        wa, country = normalize_phone(data["phone_raw"] or "", _profile(db, ctx.tenant.id).default_country)
        if wa and _duplicate(db, ctx.tenant.id, wa, "", "", exclude=l.id):
            raise HTTPException(409, "Another lead already has this WhatsApp number")
        l.whatsapp, l.country = wa, country
    for k, v in data.items():
        if k in EDITABLE and v is not None:
            setattr(l, k, v)
    db.commit()
    return _out(l)


@router.delete("/leads/{lead_id}", status_code=204)
def delete_lead(lead_id: str, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    db.delete(_own(db, ctx, lead_id))
    db.commit()


class BulkIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    action: str  # hold | release | delete


@router.post("/leads/bulk")
def bulk(body: BulkIn, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    q = db.query(Lead).filter(Lead.tenant_id == ctx.tenant.id, Lead.id.in_(body.ids))
    n = 0
    if body.action == "delete":
        n = q.filter(Lead.status.in_(("new", "hold", "verify", "no_whatsapp", "failed", "stop"))).delete(synchronize_session=False)
    elif body.action == "hold":
        n = q.filter(Lead.status.in_(("new", "verify"))).update({Lead.status: "hold"}, synchronize_session=False)
    elif body.action == "release":
        n = q.filter(Lead.status == "hold").update({Lead.status: "new"}, synchronize_session=False)
    else:
        raise HTTPException(422, "action must be hold, release or delete")
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action=f"leads.{body.action}", detail=str(n)))
    db.commit()
    return {"changed": n}


# ------------------------------------------------------------------ CSV import / export
class CsvIn(BaseModel):
    csv: str = Field(max_length=MAX_CSV)


class ImportIn(CsvIn):
    mapping: dict[str, str | None]  # CSV header -> canonical field
    list_name: str = Field(default="", max_length=120)


@router.post("/leads/import/analyze")
def analyze(body: CsvIn, ctx: Ctx = Depends(need_role("operator"))):
    try:
        headers, rows = parse_csv(body.csv)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"headers": headers, "rowCount": len(rows), "sample": rows[:3], "mapping": suggest_mapping(headers), "fields": FIELDS}


@router.post("/leads/import")
def do_import(body: ImportIn, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    try:
        headers, rows = parse_csv(body.csv)
    except ValueError as e:
        raise HTTPException(422, str(e))
    mp = {h: f for h, f in body.mapping.items() if f in FIELDS and h in headers}
    if "company" not in mp.values() and "phone" not in mp.values():
        raise HTTPException(422, "Map at least a Company or a Phone column")
    region = _profile(db, ctx.tenant.id).default_country
    lst = LeadList(tenant_id=ctx.tenant.id, name=(body.list_name.strip() or f"Import {datetime.now(timezone.utc):%Y-%m-%d %H:%M}"), source="csv")
    db.add(lst)
    db.flush()

    existing_wa = {w for (w,) in db.query(Lead.whatsapp).filter(Lead.tenant_id == ctx.tenant.id, Lead.whatsapp != "").all()}
    existing_ca = {(c.lower(), (a or '').lower()) for c, a in db.query(Lead.company, Lead.address).filter(Lead.tenant_id == ctx.tenant.id).all()}
    added = dup = skipped = no_number = 0
    for r in rows:
        v = {f: "" for f in FIELDS}
        for h, f in mp.items():
            if r.get(h):
                v[f] = (v[f] + "; " + r[h]) if v[f] and f in ("notes",) else (v[f] or r[h])
        company = (v["company"] or "").strip()
        wa, country = normalize_phone(v["phone"], region)
        if not company and not wa:
            skipped += 1
            continue
        company = company or f"Lead {wa}"
        addr = (v["address"] or "").strip()
        key = (company.lower(), addr.lower())
        if (wa and wa in existing_wa) or (key in existing_ca and (bool(addr) or not wa)):
            dup += 1
            continue
        if wa:
            existing_wa.add(wa)
        else:
            no_number += 1
        existing_ca.add(key)
        db.add(Lead(
            tenant_id=ctx.tenant.id, list_id=lst.id, company=company[:200], contact_name=v["contact_name"][:120], role=v["role"][:200], business_type=v["business_type"][:120],
            address=addr[:300], phone_raw=v["phone"][:200], whatsapp=wa, country=country, website=v["website"][:300], email=v["email"][:254],
            linkedin=v["linkedin"][:300], facebook=v["facebook"][:300], notes=v["notes"][:4000], research=v["research"][:8000],
            status="new" if wa else "verify",
        ))
        added += 1
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="leads.import", detail=f"{added} added, {dup} duplicates, list \"{lst.name}\""))
    db.commit()
    return {"listId": lst.id, "added": added, "duplicates": dup, "skipped": skipped, "withoutNumber": no_number}


@router.get("/leads-export.csv")
def export(status: str = "", list_id: str = "", ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    q = db.query(Lead).filter(Lead.tenant_id == ctx.tenant.id)
    if status:
        q = q.filter(Lead.status == status)
    if list_id:
        q = q.filter(Lead.list_id == list_id)
    cols = ["company", "contact_name", "role", "business_type", "address", "phone_raw", "whatsapp", "country", "website", "email", "linkedin", "facebook", "notes", "status", "first_message", "last_reply"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for l in q.order_by(Lead.created_at).limit(50000):
        w.writerow([csv_safe(getattr(l, c)) for c in cols])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="leads-{datetime.now(timezone.utc):%Y%m%d}.csv"'})


# ------------------------------------------------------------------ outreach profile (replaces the hard-coded One Group values)
class ProfileIn(BaseModel):
    company_name: str = Field(default="", max_length=120)
    sender_name: str = Field(default="", max_length=80)
    website: str = Field(default="", max_length=300)
    offer: str = Field(default="", max_length=3000)
    tone: str = "friendly"
    knowledge: str = Field(default="", max_length=20000)
    default_country: str = Field(default="PK", min_length=2, max_length=2)
    timezone: str = Field(default="UTC", max_length=40)
    active_from: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    active_to: str = Field(default="20:00", pattern=r"^\d{2}:\d{2}$")
    daily_limit: int = Field(default=15, ge=1, le=500)
    require_approval: bool = True
    auto_run: bool = False
    batch_size: int = Field(default=5, ge=1, le=100)
    interval_hours: int = Field(default=5, ge=1, le=72)
    delay_min: int = Field(default=60, ge=0, le=3600)
    delay_max: int = Field(default=180, ge=0, le=7200)
    dry_run: bool = True
    auto_reply: bool = False
    hold_pricing: bool = True
    followups_enabled: bool = False
    followup_day1: int = Field(default=3, ge=1, le=60)
    followup_day2: int = Field(default=7, ge=1, le=90)
    fallback_template: str = Field(default="", max_length=3000)
    notify_email: bool = True
    notify_extra_emails: str = Field(default="", max_length=600)
    notify_digest: bool = False


def _prof_out(p: OutreachProfile) -> dict:
    return {k: getattr(p, k) for k in ("company_name", "sender_name", "website", "offer", "tone", "knowledge", "default_country", "timezone", "active_from", "active_to", "daily_limit", "require_approval",
                                    "auto_run", "batch_size", "interval_hours", "delay_min", "delay_max", "dry_run", "auto_reply", "hold_pricing", "followups_enabled", "followup_day1", "followup_day2", "fallback_template", "notify_email", "notify_extra_emails", "notify_digest")}


@router.get("/outreach/profile")
def get_profile(ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    p = _profile(db, ctx.tenant.id)
    out = _prof_out(p)
    out["company_name"] = out["company_name"] or ctx.tenant.name
    return out


@router.put("/outreach/profile")
def put_profile(body: ProfileIn, ctx: Ctx = Depends(need_role("admin")), db: Session = Depends(get_db)):
    if body.tone not in ("friendly", "formal", "short"):
        raise HTTPException(422, "tone must be friendly, formal or short")
    if body.delay_min > body.delay_max:
        raise HTTPException(422, "delay_min must be <= delay_max")
    if body.followup_day1 >= body.followup_day2:
        raise HTTPException(422, "followup_day2 must be later than followup_day1")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(body.timezone)
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "Unknown timezone")
    import phonenumbers
    if body.default_country.upper() not in phonenumbers.SUPPORTED_REGIONS:
        raise HTTPException(422, "Unknown country code")
    try:
        extras = validate_extras(body.notify_extra_emails)
    except ValueError as e:
        raise HTTPException(422, str(e))
    p = _profile(db, ctx.tenant.id)
    for k, v in body.model_dump().items():
        setattr(p, k, v.upper() if k == "default_country" else ", ".join(extras) if k == "notify_extra_emails" else v)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="outreach.profile", detail="updated"))
    db.commit()
    return _prof_out(p)


# ------------------------------------------------------------------ M3: research / draft / thread / reply / enrich
@router.post("/leads/enrich", status_code=202)
def enrich_many(body: dict | None = None, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    body = body or {}
    q = db.query(Lead.id).filter(Lead.tenant_id == ctx.tenant.id)
    if body.get("ids"):
        q = q.filter(Lead.id.in_([str(i) for i in body["ids"]][:2000]))
    elif body.get("all"):
        q = q.filter(Lead.status.in_(("new", "verify", "hold", "no_whatsapp")), Lead.enriched_at.is_(None))
    else:
        raise HTTPException(422, "send ids or all=true")
    return {"queued": og.enqueue_enrich(ctx.tenant.id, [i for (i,) in q.limit(2000).all()])}


@router.get("/leads/enrich/status")
def enrich_status(ctx: Ctx = Depends(need_role("viewer"))):
    return og.enrich_status(ctx.tenant.id)


@router.post("/leads/{lead_id}/research")
async def research_now(lead_id: str, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    from ..enrich import enrich_lead
    l = _own(db, ctx, lead_id)
    p = _profile(db, ctx.tenant.id)
    await enrich_lead(db, l, p)
    await og.research_lead(db, l, p, ctx.tenant.name, force=True)
    return _out(l)


@router.post("/leads/{lead_id}/draft", status_code=201)
async def draft_now(lead_id: str, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    l = _own(db, ctx, lead_id)
    if not l.whatsapp:
        raise HTTPException(409, "This lead has no valid WhatsApp number")
    if l.status in ("contacted", "replied", "stop"):
        raise HTTPException(409, f"This lead is already {l.status}")
    if db.query(Draft.id).filter(Draft.lead_id == l.id, Draft.kind == "first", Draft.status.in_(("pending", "approved", "sending"))).first():
        raise HTTPException(409, "This lead already has a message waiting")
    try:
        d = await og.make_first_draft(db, l, _profile(db, ctx.tenant.id), ctx.tenant.name)
    except ai.AIError as e:
        raise HTTPException(getattr(e, "status", 502), str(e) if isinstance(e, ai.AILimitError) else f"AI: {e}")
    return draft_out(d, l)


@router.get("/leads/{lead_id}/thread")
def thread(lead_id: str, ctx: Ctx = Depends(need_role("viewer")), db: Session = Depends(get_db)):
    l = _own(db, ctx, lead_id)
    msgs = db.query(Message).filter(Message.lead_id == l.id, Message.tenant_id == ctx.tenant.id).order_by(Message.created_at).all()
    drafts = db.query(Draft).filter(Draft.lead_id == l.id, Draft.tenant_id == ctx.tenant.id).order_by(Draft.created_at).all()
    insts = {i.id: i for i in db.query(Instance).filter(Instance.tenant_id == ctx.tenant.id).all()}
    i = insts.get(l.instance_id)
    return {"messages": [{"id": m.id, "direction": m.direction, "body": m.body, "kind": m.kind, "at": m.created_at.isoformat(), "instanceId": m.instance_id} for m in msgs],
            "drafts": [draft_out(d, l, insts) for d in drafts], "via": {"id": i.id, "label": i.label, "phone": i.phone, "status": i.status} if i else None}


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4096)


@router.post("/leads/{lead_id}/reply", status_code=201)
def manual_reply(lead_id: str, body: ReplyIn, ctx: Ctx = Depends(need_role("operator")), db: Session = Depends(get_db)):
    l = _own(db, ctx, lead_id)
    if _profile(db, ctx.tenant.id).dry_run:
        raise HTTPException(409, "Dry-run is ON (Outreach > Setup). Turn it off to send messages.")
    if l.status == "stop":
        raise HTTPException(409, "This lead opted out and must not be contacted")
    if not l.whatsapp:
        raise HTTPException(409, "This lead has no WhatsApp number")
    d = Draft(tenant_id=ctx.tenant.id, lead_id=l.id, kind="reply", body=body.text.strip(), status="approved", source="manual")
    db.add(d)
    db.commit()
    return draft_out(d, l)
