"""The ONLY place that talks to OpenWA. Customers never see OpenWA or its admin key."""
from typing import Any
import httpx
from pathlib import Path

from .config import settings


class WAError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _key() -> str:
    p = Path(settings.openwa_key_file)
    if not p.exists():
        raise WAError(503, "WhatsApp gateway key not available yet (gateway still starting)")
    return p.read_text().strip()


async def _call(method: str, path: str, body: dict | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.request(method, f"{settings.openwa_url}/api{path}", json=body, headers={"X-API-Key": _key()})
    except httpx.HTTPError as e:
        raise WAError(503, f"WhatsApp gateway unreachable: {e.__class__.__name__}")
    if r.status_code >= 400:
        raise WAError(r.status_code, r.text[:300])
    try:
        j = r.json() if r.text else {}
    except ValueError:
        return {}
    return j.get("data", j) if isinstance(j, dict) else j


async def create_session(name: str) -> dict:
    return await _call("POST", "/sessions", {"name": name})


async def start_session(sid: str) -> dict:
    try:
        return await _call("POST", f"/sessions/{sid}/start")
    except WAError as e:
        if e.status == 400 and "already started" in str(e).lower():
            return {"alreadyStarted": True}
        raise


async def get_session(sid: str) -> dict:
    return await _call("GET", f"/sessions/{sid}")


async def get_qr(sid: str) -> dict:
    return await _call("GET", f"/sessions/{sid}/qr")


async def delete_session(sid: str) -> None:
    await _call("DELETE", f"/sessions/{sid}")


async def add_webhook(sid: str) -> dict:
    return await _call(
        "POST",
        f"/sessions/{sid}/webhooks",
        {"url": settings.internal_webhook_url, "events": ["session.status", "message.received"], "secret": settings.openwa_webhook_secret},
    )


async def check_number(sid: str, digits: str) -> dict:
    return await _call("GET", f"/sessions/{sid}/contacts/check/{digits}")


async def send_text(sid: str, chat_id: str, text: str) -> dict:
    return await _call("POST", f"/sessions/{sid}/messages/send-text", {"chatId": chat_id, "text": text})


async def list_sessions() -> list[dict]:
    """All sessions on the gateway node: [{id, name, status, ...}]."""
    res = await _call("GET", "/sessions")
    if isinstance(res, dict):
        res = res.get("items") or res.get("sessions") or res.get("data") or []
    return res if isinstance(res, list) else []


async def stop_session(sid: str) -> None:
    """Stop the engine but keep the linked credentials (the customer can press 'Restart' later)."""
    await _call("POST", f"/sessions/{sid}/stop")


async def health() -> dict:
    """Gateway readiness (+ memory figures when the gateway reports them). Raises WAError when unreachable."""
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{settings.openwa_url}/api/health/ready")
    except httpx.HTTPError as e:
        raise WAError(503, f"gateway unreachable: {e.__class__.__name__}")
    if r.status_code >= 400:
        raise WAError(r.status_code, "gateway not ready")
    try:
        return r.json() if r.text else {}
    except ValueError:
        return {}
