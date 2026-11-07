"""The single send path used by BOTH the dashboard and the public API, so limits and checks can never differ."""
import re

import httpx
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
    if inst.over_limit:
        raise HTTPException(403, "This number is over your plan's limit and is paused. Upgrade your plan (or remove other numbers) to use it again.")
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


MEDIA_KINDS = ("image", "document", "video")


async def fetch_remote_media(url: str, kind: str) -> tuple[bytes, str]:
    """Download a customer-supplied https URL for a public-API media send (we send the bytes ourselves, so the gateway never fetches URLs).
    SSRF-guarded (public hosts only), no redirects, size-capped, and the BYTES must really be the declared kind. Returns (bytes, mime)."""
    from . import media
    from .netguard import UnsafeUrl, assert_crawlable

    if not url.lower().startswith("https://"):
        raise HTTPException(422, "`media.url` must start with https://")
    try:
        await assert_crawlable(url)
    except UnsafeUrl as e:
        raise HTTPException(422, f"`media.url` cannot be used: {e}")
    cap = media.MAX_BYTES[kind]
    buf = bytearray()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False) as c:
            async with c.stream("GET", url, headers={"User-Agent": "ConnectFlow/1.0"}) as r:
                if r.status_code != 200:
                    raise HTTPException(422, f"`media.url` answered {r.status_code}")
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > cap:
                        raise HTTPException(413, f"`media.url` is larger than {cap // media.MB} MB (limit for {kind} files)")
    except httpx.HTTPError:
        raise HTTPException(422, "`media.url` could not be downloaded")
    try:
        found = media.validate_upload(bytes(buf), "")
    except media.MediaError as e:
        raise HTTPException(e.status, f"`media.url`: {e}")
    if found.kind != kind:
        raise HTTPException(422, f"`media.url` is a {found.kind}, not a {kind}")
    return bytes(buf), found.mime


async def send_media(db: Session, tenant: Tenant, inst: Instance, to: str, kind: str, data: bytes, mime: str, caption: str = "", filename: str = "", api_key_id: str | None = None) -> dict:
    """One image / PDF / video message through the same limits as text (plan quota, number state, over-limit pause)."""
    if kind not in MEDIA_KINDS:
        raise HTTPException(422, "`media.type` must be image, document or video")
    caption = (caption or "").strip()
    if len(caption) > 1024:
        raise HTTPException(422, "`caption` can be at most 1024 characters")
    digits = normalize_to(to)
    if inst.over_limit:
        raise HTTPException(403, "This number is over your plan's limit and is paused. Upgrade your plan (or remove other numbers) to use it again.")
    if inst.status != "ready":
        raise HTTPException(409, f"This number is not connected (status: {inst.status}). Link it from the dashboard first.")
    ev = reserve_message(db, tenant.id, inst.id, api_key_id)  # raises 429 when the plan limit is reached
    try:
        chk = await wa.check_number(inst.openwa_id, digits)
        if not chk.get("exists"):
            refund(db, ev)
            raise HTTPException(422, "That number is not on WhatsApp")
        chat_id = chk.get("whatsappId") or f"{digits}@c.us"
        res = await wa.send_media(inst.openwa_id, chat_id, kind, data=data, mimetype=mime, filename=filename, caption=caption)
    except wa.WAError as e:
        refund(db, ev)
        raise HTTPException(429 if e.status == 429 else 502, f"WhatsApp gateway: {e}")
    return {"id": str(res.get("id") or res.get("messageId") or ev.id), "status": "sent", "to": digits, "instance": inst.id, "media": kind}
