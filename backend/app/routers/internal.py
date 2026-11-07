"""Receives events from OpenWA. Not exposed publicly (the frontend proxy only forwards /api)."""
import hashlib
import hmac
import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from .. import outreach as og
from ..deps import rds
from ..events import emit
from ..alerts import send_alert
from ..models import Instance

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger("internal")


def _text(v: object, limit: int = 4096) -> str:
    """Gateway payload fields are untrusted: only strings/numbers are accepted, everything else becomes empty."""
    return str(v).strip()[:limit] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


MEDIA_TYPES = {"image": "image", "video": "video", "document": "document", "audio": "voice", "ptt": "voice", "voice": "voice", "sticker": "sticker"}


def media_kind(msg_type: str, has_media: bool) -> str | None:
    """Gateway message type -> the placeholder kind we store ('image', 'document', ...); None for ordinary text."""
    if msg_type in MEDIA_TYPES:
        return MEDIA_TYPES[msg_type]
    return "media" if has_media else None


@router.post("/wa-webhook", response_model=None)
async def wa_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, Any] | JSONResponse:
    raw = await request.body()
    sig = request.headers.get("x-openwa-signature", "")
    expected = "sha256=" + hmac.new(settings.openwa_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(401, "bad signature")
    try:
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
    except ValueError:
        raise HTTPException(400, "invalid JSON body")
    idem = request.headers.get("x-openwa-idempotency-key")
    idem_key = f"wh:{idem[:100]}" if idem else ""
    if idem_key and not rds.set(idem_key, "1", nx=True, ex=86400):
        return {"ok": True, "duplicate": True}  # the gateway retried a delivery we already processed
    try:
        await _handle(db, request, payload)
    except Exception:  # noqa: BLE001 - a bad event must not become a crash loop: log (no payload), release the dedupe key so a retry can succeed
        db.rollback()
        if idem_key:
            rds.delete(idem_key)
        log.exception("could not process a gateway event")
        return JSONResponse({"ok": False}, status_code=500)
    return {"ok": True}


async def _handle(db: Session, request: Request, payload: dict[str, Any]) -> None:
    event = _text(payload.get("event")) or _text(request.headers.get("x-openwa-event"))
    raw_data = payload.get("data", payload)
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    sid = _text(data.get("sessionId")) or _text(payload.get("sessionId"))
    if event == "session.status" and sid:
        inst = db.query(Instance).filter(Instance.openwa_id == sid).first()
        if inst:  # session -> tenant mapping happens here: unknown sessions are ignored
            old_status = inst.status
            inst.status = _text(data.get("status"), 24) or inst.status
            if data.get("phone"):
                inst.phone = _text(data.get("phone"), 32)
            db.commit()
            if inst.status != old_status:
                emit(db, inst.tenant_id, "instance.status", inst, {"status": inst.status, "phone": inst.phone})
                if inst.status in ("disconnected", "failed"):
                    og.notify(db, inst.tenant_id, "session", f"WhatsApp number disconnected: {inst.label}", "Open WhatsApp numbers and scan the QR again if it does not reconnect.", link="numbers")
                if old_status == "ready" and inst.status != "ready":  # a linked number dropped (disconnect or a fresh QR is needed)
                    await send_alert(db, inst.tenant_id, "session", f"WhatsApp number {inst.phone or inst.label} needs attention",
                                     [f"Your number {inst.label} ({inst.phone or 'not linked'}) is now '{inst.status}'.", "Open WhatsApp numbers and scan the QR code again if it does not reconnect on its own. Replies and follow-ups on this number wait until it is back."])
    elif event == "message.received" and sid:
        inst = db.query(Instance).filter(Instance.openwa_id == sid).first()
        frm = _text(data.get("from"))
        text = _text(data.get("body")) or _text(data.get("text"))
        mkind = media_kind(_text(data.get("type"), 24).lower(), bool(data.get("hasMedia")))  # an image/PDF/voice note arrives with an empty or caption-only body
        if inst and (text or mkind) and not data.get("fromMe") and not data.get("isGroup") and not re.search(r"@(g\.us|newsletter|broadcast)$", frm):
            number = re.sub(r"\D", "", _text(data.get("senderPhone")) or frm.split("@")[0])
            emit(db, inst.tenant_id, "message.received", inst, {"from": number, "text": text or f"[{mkind}]", "mediaKind": mkind, "messageId": _text(data.get("id")) or _text(data.get("messageId"))})
            try:
                await og.handle_inbound(db, inst, number, text, frm, mkind)
            except Exception:  # noqa: BLE001  never fail the gateway webhook because lead handling failed
                db.rollback()
                log.exception("lead handling failed for an inbound message")
