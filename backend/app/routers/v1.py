"""Public API (Bearer cf_live_...). Server-to-server: no cookies, no CSRF, scoped keys, same limits as the dashboard.
Every request is checked by app/telemetry.py (per-IP limit before auth, suspension, IP allow-list, usage telemetry, leak rules)."""
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from .. import assetlib, idempotency, storage
from ..db import get_db
from ..deps import rds
from ..messaging import fetch_remote_media, send_media, send_text
from ..models import ApiKey, Asset, Instance, Tenant
from ..telemetry import check_active_hours, client_ip, ip_allowed, record_event, record_use
from .keys import hash_key

router = APIRouter(prefix="/v1", tags=["public api"])


class KeyCtx:
    def __init__(self, key: ApiKey, tenant: Tenant) -> None:
        self.key, self.tenant = key, tenant


def api_key_ctx(request: Request, authorization: str = Header(default=""), user_agent: str = Header(default=""), db: Session = Depends(get_db)) -> KeyCtx:
    ip = client_ip(request)
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Send your key as: Authorization: Bearer cf_live_...")
    k = db.query(ApiKey).filter(ApiKey.key_hash == hash_key(authorization[7:].strip())).first()
    if not k:
        raise HTTPException(401, "Invalid or revoked API key")
    request.state.key_id = k.id
    now = datetime.now(timezone.utc)
    expired = bool(k.expires_at and k.expires_at < now)
    if k.revoked_at or expired:  # somebody still holds a dead key: worth a note (it may be a leaked copy)
        record_event("warn", "dead_key_used", f"{'Expired' if expired else 'Revoked'} key '{k.name}' ({k.prefix}…) was used from {ip}", k.tenant_id, k.id, dedupe=f"dead:{k.id}:{ip}", ttl=3600, db=db)
        raise HTTPException(401, "This API key has expired. Create a new one in the dashboard." if expired and not k.revoked_at else "Invalid or revoked API key")
    if k.suspended_at:
        raise HTTPException(401, "This API key is suspended. Contact the workspace owner to review and unsuspend it.")
    if not ip_allowed(k.allowed_ips, ip):
        record_event("warn", "ip_blocked", f"Key '{k.name}' ({k.prefix}…) was refused from {ip}: not on its IP allow-list", k.tenant_id, k.id, dedupe=f"ipb:{k.id}:{ip}", ttl=3600, db=db)
        raise HTTPException(403, "This API key is not allowed from this IP address.")
    t = db.get(Tenant, k.tenant_id)
    if not t or t.status != "active":
        raise HTTPException(403, "Account suspended")
    try:
        n = rds.incr(f"v1rate:{k.id}")
        if n == 1:
            rds.expire(f"v1rate:{k.id}", 60)
        if n > 120:
            raise HTTPException(429, "Rate limit: 120 requests per minute per key")
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Redis down: keep serving, just without this limit
        pass
    reason = record_use(db, k, ip, user_agent)
    if reason:
        raise HTTPException(401, "This API key was just suspended because of unusual activity. Contact the workspace owner.")
    k.last_used_at = now
    db.commit()
    return KeyCtx(k, t)


class MediaIn(BaseModel):
    type: Literal["image", "document", "video"]
    assetId: str | None = Field(default=None, description="A file from your media library (Outreach > Assets)")
    url: str | None = Field(default=None, max_length=500, description="OR a public https URL of the file (we download it, validate it and send it)")
    caption: str = Field(default="", max_length=1024)
    filename: str = Field(default="", max_length=255, description="Shown for documents")

    @model_validator(mode="after")
    def _one_source(self) -> "MediaIn":
        if bool(self.assetId) == bool(self.url):
            raise ValueError("send exactly one of media.assetId or media.url")
        return self


class MessageIn(BaseModel):
    instance: str = Field(description="Instance id (from GET /v1/instances)")
    to: str = Field(description="International number with country code, e.g. 923001234567")
    text: str = Field(default="", max_length=4096, description="The message. With `media` it is the caption (up to 1024 characters) unless media.caption is set")
    media: MediaIn | None = None

    @model_validator(mode="after")
    def _text_or_media(self) -> "MessageIn":
        if self.media is None and not self.text.strip():
            raise ValueError("`text` must be 1-4096 characters")
        return self


@router.get("/instances")
def instances(c: KeyCtx = Depends(api_key_ctx), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    q = db.query(Instance).filter(Instance.tenant_id == c.tenant.id)
    if c.key.instance_id:
        q = q.filter(Instance.id == c.key.instance_id)
    return [{"id": i.id, "label": i.label, "phone": i.phone, "status": i.status} for i in q.all()]


@router.post("/messages", status_code=202)
async def messages(body: MessageIn, response: Response, c: KeyCtx = Depends(api_key_ctx), db: Session = Depends(get_db),
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> Any:
    """Send a text message, or an image / PDF / video with an optional caption (`media`). Optional `Idempotency-Key` header (<= 64 chars): a retry with the same key and body returns the first result
    (header `Idempotent-Replayed: true`) without sending again."""
    idem = idempotency.validate_key(idempotency_key)
    state = idempotency.Begin(tracked=False)
    h = idempotency.body_hash(body.model_dump())
    if idem:
        state = idempotency.begin(rds, c.tenant.id, idem, h)
        if state.replay:
            return JSONResponse(state.body or {}, status_code=state.status, headers={"Idempotent-Replayed": "true", "Idempotency-Key": idem})
    try:
        if c.key.instance_id and c.key.instance_id != body.instance:
            raise HTTPException(403, "This key is scoped to a different number")
        inst = db.query(Instance).filter(Instance.id == body.instance, Instance.tenant_id == c.tenant.id).first()
        if not inst:
            raise HTTPException(404, "instance not found")
        check_active_hours(db, c.key)
        if body.media is None:
            result = await send_text(db, c.tenant, inst, body.to, body.text, api_key_id=c.key.id)
        else:
            data, mime, fname = b"", "", body.media.filename
            if body.media.assetId:
                a = db.query(Asset).filter(Asset.id == body.media.assetId, Asset.tenant_id == c.tenant.id).first()
                if not a:
                    raise HTTPException(404, "media asset not found")
                if a.kind != body.media.type:
                    raise HTTPException(422, f"That asset is a {a.kind}, not a {body.media.type}")
                _, backend, key, mime = assetlib.choose_variant(db, a)
                try:
                    data = await assetlib.read_file(backend, key)
                except storage.StorageError as e:
                    raise HTTPException(502, f"The stored file could not be read: {e}")
                fname = fname or f"{(a.label or 'document')[:60]}.pdf"
            else:
                data, mime = await fetch_remote_media(body.media.url or "", body.media.type)
            result = await send_media(db, c.tenant, inst, body.to, body.media.type, data, mime, body.media.caption or body.text, fname, api_key_id=c.key.id)
    except BaseException:
        if idem and state.tracked:
            idempotency.abort(rds, c.tenant.id, idem)  # a failed attempt must not block the client's retry
        raise
    if idem and state.tracked:
        idempotency.finish(rds, c.tenant.id, idem, h, 202, result)
        response.headers["Idempotency-Key"] = idem
    return result
