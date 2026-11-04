"""Receives events from OpenWA. Not exposed publicly (the frontend proxy only forwards /api)."""
import hashlib
import hmac
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from .. import outreach as og
from ..deps import rds
from ..events import emit
from ..alerts import send_alert
from ..models import Instance

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/wa-webhook")
async def wa_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    sig = request.headers.get("x-openwa-signature", "")
    expected = "sha256=" + hmac.new(settings.openwa_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(401, "bad signature")
    idem = request.headers.get("x-openwa-idempotency-key")
    if idem and not rds.set(f"wh:{idem}", "1", nx=True, ex=86400):
        return {"ok": True, "duplicate": True}  # the gateway retried a delivery we already processed
    try:
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
    except ValueError:
        raise HTTPException(400, "invalid JSON body")
    event = payload.get("event") or request.headers.get("x-openwa-event")
    data = payload.get("data", payload)
    sid = data.get("sessionId") or payload.get("sessionId")
    if event == "session.status" and sid:
        inst = db.query(Instance).filter(Instance.openwa_id == str(sid)).first()
        if inst:  # session -> tenant mapping happens here: unknown sessions are ignored
            old_status = inst.status
            inst.status = str(data.get("status") or inst.status)
            if data.get("phone"):
                inst.phone = str(data["phone"])
            db.commit()
            if inst.status != old_status:
                emit(db, inst.tenant_id, "instance.status", inst, {"status": inst.status, "phone": inst.phone})
                if inst.status in ("disconnected", "failed"):
                    og.notify(db, inst.tenant_id, "session", f"WhatsApp number disconnected: {inst.label}", "Open WhatsApp numbers and scan the QR again if it does not reconnect.", link="numbers")
                if old_status == "ready" and inst.status != "ready":  # a linked number dropped (disconnect or a fresh QR is needed)
                    await send_alert(db, inst.tenant_id, "session", f"WhatsApp number {inst.phone or inst.label} needs attention",
                                     [f"Your number {inst.label} ({inst.phone or 'not linked'}) is now '{inst.status}'.", "Open WhatsApp numbers and scan the QR code again if it does not reconnect on its own. Replies and follow-ups on this number wait until it is back."])
    elif event == "message.received" and sid:
        inst = db.query(Instance).filter(Instance.openwa_id == str(sid)).first()
        frm = str(data.get("from") or "")
        text = (data.get("body") or data.get("text") or "").strip()
        if inst and text and not data.get("fromMe") and not data.get("isGroup") and not re.search(r"@(g\.us|newsletter|broadcast)$", frm):
            number = re.sub(r"\D", "", str(data.get("senderPhone") or frm.split("@")[0]))
            emit(db, inst.tenant_id, "message.received", inst, {"from": number, "text": text, "messageId": str(data.get("id") or data.get("messageId") or "")})
            try:
                await og.handle_inbound(db, inst, number, text, frm)
            except Exception:  # noqa: BLE001  never fail the gateway webhook because lead handling failed
                db.rollback()
    # message.received: routed to the tenant's webhooks / lead-gen module in later phases
    return {"ok": True}
