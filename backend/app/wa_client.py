"""The ONLY place that talks to OpenWA. Customers never see OpenWA or its admin key.

One pooled httpx client per event loop (keep-alive connections are reused instead of opening a new client for every request)."""
import asyncio
import base64
from typing import Any
import httpx
from pathlib import Path

from .config import settings

DEFAULT_TIMEOUT = 60.0
MEDIA_TIMEOUT = 120.0
MAX_MEDIA_BYTES = 18 * 1024 * 1024   # raw bytes; base64 inflates by a third and the gateway's body limit is 25 MB
MEDIA_ENDPOINT = {"image": "send-image", "video": "send-video", "document": "send-document"}


class WAError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _key() -> str:
    p = Path(settings.openwa_key_file)
    if not p.exists():
        raise WAError(503, "WhatsApp gateway key not available yet (gateway still starting)")
    return p.read_text().strip()


_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


def _get_client() -> httpx.AsyncClient:
    """The shared client of the running event loop (re-created if the loop changed or the client was closed)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=10.0), limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30.0))
        _client_loop = loop
    return _client


async def close() -> None:
    """Close the pooled client (tests / shutdown)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _call(method: str, path: str, body: dict | None = None, timeout: float | None = None) -> Any:
    try:
        r = await _get_client().request(method, f"{settings.openwa_url}/api{path}", json=body, headers={"X-API-Key": _key()}, timeout=timeout or DEFAULT_TIMEOUT)
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


async def send_text(sid: str, chat_id: str, text: str, link_preview: dict[str, str] | None = None) -> dict:
    """link_preview = {"url", "title", "description"?}: asks WhatsApp to render a preview card for that link in the text."""
    body: dict[str, Any] = {"chatId": chat_id, "text": text}
    if link_preview and link_preview.get("url") and link_preview.get("title"):
        body["linkPreview"] = True
        body["customLinkPreview"] = {k: v[:256 if k == "title" else 1024] for k, v in link_preview.items() if v}
    return await _call("POST", f"/sessions/{sid}/messages/send-text", body)


async def send_media(sid: str, chat_id: str, kind: str, *, data: bytes, mimetype: str, filename: str = "", caption: str = "") -> dict:
    """One image / video / document message (with an optional caption of at most 1024 characters). The file travels as base64 read from our
    own storage, so the gateway never fetches a URL on our behalf (no SSRF surface, nothing publicly hosted)."""
    if kind not in MEDIA_ENDPOINT:
        raise WAError(422, f"unsupported media kind {kind}")
    if len(data) > MAX_MEDIA_BYTES:
        raise WAError(413, "The file is too large to send")
    body: dict[str, Any] = {"chatId": chat_id, "base64": base64.b64encode(data).decode(), "mimetype": mimetype}
    if caption:
        body["caption"] = caption[:1024]
    if kind == "document":
        body["filename"] = (filename or "document.pdf")[:255]
    return await _call("POST", f"/sessions/{sid}/messages/{MEDIA_ENDPOINT[kind]}", body, timeout=MEDIA_TIMEOUT)


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
        r = await _get_client().get(f"{settings.openwa_url}/api/health/ready", timeout=5)
    except httpx.HTTPError as e:
        raise WAError(503, f"gateway unreachable: {e.__class__.__name__}")
    if r.status_code >= 400:
        raise WAError(r.status_code, "gateway not ready")
    try:
        return r.json() if r.text else {}
    except ValueError:
        return {}
