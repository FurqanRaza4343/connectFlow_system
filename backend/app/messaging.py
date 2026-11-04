"""The single send path used by BOTH the dashboard and the public API, so limits and checks can never differ."""
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import wa_client as wa
from .models import Instance, Tenant
from .quota import refund, reserve_message


def normalize_to(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not re.fullmatch(r"[1-9]\d{9,14}", digits):
        raise HTTPException(422, "`to` must be an international number with country code, e.g. 923001234567")
    return digits


async def send_text(db: Session, tenant: Tenant, inst: Instance, to: str, text: str, api_key_id: str | None = None) -> dict:
    text = (text or "").strip()
    if not text or len(text) > 4096:
        raise HTTPException(422, "`text` must be 1-4096 characters")
    digits = normalize_to(to)
    if inst.status != "ready":
        raise HTTPException(409, f"This number is not connected (status: {inst.status}). Link it from the dashboard first.")
    ev = reserve_message(db, tenant.id, inst.id, api_key_id)  # raises 429 when the plan limit is reached
    try:
        chk = await wa.check_number(inst.openwa_id, digits)
        if not chk.get("exists"):
            refund(db, ev)
            raise HTTPException(422, "That number is not on WhatsApp")
        chat_id = chk.get("whatsappId") or f"{digits}@c.us"
        res = await wa.send_text(inst.openwa_id, chat_id, text)
    except wa.WAError as e:
        refund(db, ev)
        raise HTTPException(429 if e.status == 429 else 502, f"WhatsApp gateway: {e}")
    return {"id": str(res.get("id") or res.get("messageId") or ev.id), "status": "sent", "to": digits, "instance": inst.id}
