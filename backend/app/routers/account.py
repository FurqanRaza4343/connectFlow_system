"""Account data rights (owner only): download everything the workspace holds, and ask for the workspace to be deleted.

  GET  /api/account                      deletion-request status
  GET  /api/account/export               ZIP of CSV/JSON files with the workspace's own data (3 per day)
  POST /api/account/delete-request       {password}: records the request and emails the owner + the platform admin. NOTHING is deleted.
  POST /api/account/delete-request/cancel

The export never contains secrets: no password hashes, API key hashes or plaintext, webhook secrets, gateway ids, or other workspaces' data.
Deleting a workspace is a manual, audited admin action (docs/RUNBOOK.md, "Deleting a workspace on request").
"""
import csv
import io
import json
import tempfile
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import Ctx, need_role, rds, TCtx
from ..leadtools import csv_safe
from ..mailer import layout, send_email
from ..models import ApiKey, AuditLog, Draft, Instance, Lead, LeadList, Membership, Message, OutreachProfile, UsageEvent, User, WebhookEndpoint
from ..security import verify_password

router = APIRouter(prefix="/api/account", tags=["account"])
EXPORTS_PER_DAY = 3
README = """ConnectFlow data export
=======================
Everything your workspace holds, as CSV (open in Excel / Google Sheets) and JSON.

leads.csv, lead_lists.csv, messages.csv, drafts.csv   your leads and conversations (message text included: it is your data)
numbers.csv                                           your WhatsApp numbers (no gateway identifiers)
api_keys.csv                                          key metadata only (name, prefix, dates). Key values and hashes are never exported.
webhooks.csv                                          endpoint URLs and events. Signing secrets are never exported.
team.csv, audit_log.csv, usage.csv                    members, activity log, message usage
profile.json, workspace.json                          outreach settings and workspace details
"""


class DeleteIn(BaseModel):
    password: str = Field(max_length=128)


def _iso(v: Any) -> str:
    return v.isoformat() if isinstance(v, datetime) else ("" if v is None else str(v))


def _write_csv(zf: zipfile.ZipFile, name: str, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    with zf.open(name, "w") as raw:
        out = io.TextIOWrapper(raw, encoding="utf-8", newline="")
        w = csv.writer(out)
        w.writerow(header)
        for r in rows:
            w.writerow([csv_safe(_iso(x)) for x in r])
        out.flush()
        out.detach()  # leave the zip member open for zipfile to close


def _json(zf: zipfile.ZipFile, name: str, obj: dict[str, Any]) -> None:
    zf.writestr(name, json.dumps(obj, indent=2, ensure_ascii=False, default=_iso))


def _profile_dict(p: OutreachProfile | None) -> dict[str, Any]:
    if not p:
        return {}
    return {c.name: getattr(p, c.name) for c in OutreachProfile.__table__.columns if c.name != "tenant_id"}


def build_export(db: Session, ctx: Ctx) -> tempfile.SpooledTemporaryFile[bytes]:
    t = ctx.tenant
    assert t is not None
    tid = t.id
    numbers = {i.id: (f"{i.label} (+{i.phone})" if i.phone else i.label) for i in db.query(Instance).filter(Instance.tenant_id == tid)}
    lists = {lst.id: lst.name for lst in db.query(LeadList).filter(LeadList.tenant_id == tid)}
    users = {u.id: u.email for u in db.query(User).join(Membership, Membership.user_id == User.id).filter(Membership.tenant_id == tid)}
    buf: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", README)
        _json(zf, "workspace.json", {"name": t.name, "plan": t.plan_id, "createdAt": t.created_at, "website": t.website_url,
                                      "theme": {"accent": t.brand_color, "sidebar": t.sidebar_color, "page": t.page_color}, "exportedAt": datetime.now(timezone.utc)})
        _json(zf, "profile.json", _profile_dict(db.get(OutreachProfile, tid)))
        _write_csv(zf, "lead_lists.csv", ["id", "name", "source", "created_at"], ((x.id, x.name, x.source, x.created_at) for x in db.query(LeadList).filter(LeadList.tenant_id == tid)))
        _write_csv(zf, "leads.csv",
                   ["id", "list", "company", "contact_name", "role", "business_type", "address", "phone_raw", "whatsapp", "country", "website", "email", "linkedin", "facebook", "notes", "status",
                    "research", "first_message", "sent_at", "last_reply", "followup_stage", "number", "enrichment", "created_at", "updated_at"],
                   ((x.id, lists.get(x.list_id or "", ""), x.company, x.contact_name, x.role, x.business_type, x.address, x.phone_raw, x.whatsapp, x.country, x.website, x.email, x.linkedin, x.facebook, x.notes,
                     x.status, x.research, x.first_message, x.sent_at, x.last_reply, x.followup_stage, numbers.get(x.instance_id or "", ""), x.enrichment, x.created_at, x.updated_at)
                    for x in db.query(Lead).filter(Lead.tenant_id == tid).order_by(Lead.created_at).yield_per(500)))
        _write_csv(zf, "messages.csv", ["id", "lead_id", "number", "chat_id", "direction", "kind", "body", "created_at"],
                   ((x.id, x.lead_id, numbers.get(x.instance_id or "", ""), x.chat_id, x.direction, x.kind, x.body, x.created_at)
                    for x in db.query(Message).filter(Message.tenant_id == tid).order_by(Message.created_at).yield_per(500)))
        _write_csv(zf, "drafts.csv", ["id", "lead_id", "kind", "status", "body", "incoming", "note", "source", "needs_human", "instruction", "created_at"],
                   ((x.id, x.lead_id, x.kind, x.status, x.body, x.incoming, x.note, x.source, x.needs_human, x.instruction, x.created_at)
                    for x in db.query(Draft).filter(Draft.tenant_id == tid).order_by(Draft.created_at).yield_per(500)))
        _write_csv(zf, "numbers.csv", ["id", "label", "phone", "status", "engine", "outreach_enabled", "created_at"],
                   ((x.id, x.label, x.phone, x.status, x.engine, x.outreach_enabled, x.created_at) for x in db.query(Instance).filter(Instance.tenant_id == tid)))
        _write_csv(zf, "api_keys.csv", ["id", "name", "prefix", "number", "created_at", "last_used_at", "expires_at", "revoked_at", "suspended_at", "suspended_reason", "allowed_ips"],
                   ((x.id, x.name, x.prefix, numbers.get(x.instance_id or "", "all numbers"), x.created_at, x.last_used_at, x.expires_at, x.revoked_at, x.suspended_at, x.suspended_reason, x.allowed_ips)
                    for x in db.query(ApiKey).filter(ApiKey.tenant_id == tid)))
        _write_csv(zf, "webhooks.csv", ["id", "url", "events", "description", "active", "created_at"],
                   ((x.id, x.url, x.events, x.description, x.active, x.created_at) for x in db.query(WebhookEndpoint).filter(WebhookEndpoint.tenant_id == tid)))
        members = db.query(User, Membership).join(Membership, Membership.user_id == User.id).filter(Membership.tenant_id == tid).all()
        _write_csv(zf, "team.csv", ["email", "name", "role", "email_verified", "two_factor", "account_created"],
                   ((u.email, u.name, m.role, bool(u.email_verified_at), bool(u.totp_enabled_at), u.created_at) for u, m in members))
        _write_csv(zf, "audit_log.csv", ["at", "user", "action", "detail"],
                   ((x.at, users.get(x.user_id or "", ""), x.action, x.detail) for x in db.query(AuditLog).filter(AuditLog.tenant_id == tid).order_by(AuditLog.at).yield_per(500)))
        _write_csv(zf, "usage.csv", ["at", "kind", "number", "api_key_id"],
                   ((x.at, x.kind, numbers.get(x.instance_id or "", ""), x.api_key_id) for x in db.query(UsageEvent).filter(UsageEvent.tenant_id == tid).order_by(UsageEvent.at).yield_per(1000)))
    buf.seek(0)
    return buf


def _stream(buf: tempfile.SpooledTemporaryFile[bytes]) -> Iterator[bytes]:
    try:
        while chunk := buf.read(64 * 1024):
            yield chunk
    finally:
        buf.close()


@router.get("")
def account(ctx: TCtx = Depends(need_role("owner"))) -> dict[str, Any]:
    assert ctx.tenant is not None
    return {"deletionRequestedAt": ctx.tenant.deletion_requested_at.isoformat() if ctx.tenant.deletion_requested_at else None, "exportsPerDay": EXPORTS_PER_DAY}


@router.get("/export")
def export(ctx: TCtx = Depends(need_role("owner")), db: Session = Depends(get_db)) -> StreamingResponse:
    assert ctx.tenant is not None
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    key = f"export:{ctx.tenant.id}:{day}"
    n = int(rds.incr(key))
    if n == 1:
        rds.expire(key, 86400)
    if n > EXPORTS_PER_DAY:
        raise HTTPException(429, f"You can download your data up to {EXPORTS_PER_DAY} times per day. Try again tomorrow.")
    buf = build_export(db, ctx)
    size = buf.seek(0, 2)
    buf.seek(0)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="account.export", detail=f"{size} bytes"))
    db.commit()
    return StreamingResponse(_stream(buf), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="connectflow-export-{day}.zip"', "Content-Length": str(size), "Cache-Control": "no-store"})


@router.post("/delete-request")
async def delete_request(body: DeleteIn, ctx: TCtx = Depends(need_role("owner")), db: Session = Depends(get_db)) -> dict[str, Any]:
    t = ctx.tenant
    assert t is not None
    if not verify_password(body.password, ctx.user.password_hash):
        raise HTTPException(401, "Wrong password")
    if t.deletion_requested_at:
        return {"requestedAt": t.deletion_requested_at.isoformat(), "alreadyRequested": True}
    t.deletion_requested_at = datetime.now(timezone.utc)
    db.add(AuditLog(tenant_id=t.id, user_id=ctx.user.id, action="account.delete_requested", detail=ctx.user.email))
    db.commit()
    line = f"A request to delete the ConnectFlow workspace '{t.name}' was recorded. Nothing has been deleted yet: our team will confirm with you and complete it within 30 days. You can cancel the request in Settings."
    await send_email(ctx.user.email, "We received your deletion request", line, layout("Deletion request received", line))
    if settings.admin_email:
        await send_email(settings.admin_email, f"Deletion request: {t.name}", f"{t.name} ({ctx.user.email}) asked to delete their workspace.",
                         layout("Deletion request", f"Workspace <b>{t.name}</b> (owner {ctx.user.email}) requested deletion. Follow the runbook."))
    return {"requestedAt": t.deletion_requested_at.isoformat(), "alreadyRequested": False}


@router.post("/delete-request/cancel")
def cancel_delete(ctx: TCtx = Depends(need_role("owner")), db: Session = Depends(get_db)) -> dict[str, Any]:
    t = ctx.tenant
    assert t is not None
    if t.deletion_requested_at:
        t.deletion_requested_at = None
        db.add(AuditLog(tenant_id=t.id, user_id=ctx.user.id, action="account.delete_cancelled", detail=""))
        db.commit()
    return {"ok": True}
