"""Media library: images, PDF brochures, short videos and links the workspace can attach to first messages, follow-ups and AI replies.

Reads need role viewer, writes need operator. Every query is filtered by tenant_id (another workspace's id is a 404). Files are validated by
their bytes (not their name), images are re-encoded without metadata, limits come from the plan, and file bytes are only ever served to a
signed-in member of the owning workspace (there is no public URL)."""
import hashlib
import html
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import assetlib, media, storage
from ..db import get_db
from ..deps import TCtx, need_role, rds
from ..models import Asset, AssetVariant, AuditLog, Tenant
from ..netguard import UnsafeUrl, assert_crawlable

router = APIRouter(prefix="/api/assets", tags=["media library"])
log = logging.getLogger("assets")
MAX_REQUEST = 26 * 1024 * 1024


def _hour() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H")


def _fail(reason: str) -> None:
    """Count an upload failure for the admin capacity view (hourly Redis counter)."""
    try:
        k = f"asset:fail:{_hour()}"
        rds.incr(k)
        rds.expire(k, 7200)
    except Exception:  # noqa: BLE001
        pass


def _rate_limit(tenant_id: str) -> None:
    try:
        k = f"asset:up:{tenant_id}:{_hour()}"
        n = rds.incr(k)
        if n == 1:
            rds.expire(k, 3700)
        if n > media.UPLOADS_PER_HOUR:
            raise HTTPException(429, f"Upload limit reached ({media.UPLOADS_PER_HOUR} per hour). Try again later.")
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Redis down: no limit rather than no uploads
        pass


def _own(db: Session, ctx: TCtx, asset_id: str) -> Asset:
    a = db.query(Asset).filter(Asset.id == asset_id, Asset.tenant_id == ctx.tenant.id).first()
    if not a:
        raise HTTPException(404, "asset not found")
    return a


def _http(e: Exception) -> HTTPException:
    if isinstance(e, (media.MediaError, storage.StorageError)):
        return HTTPException(e.status, str(e))
    return HTTPException(500, "unexpected error")


def _reserve(db: Session, ctx: TCtx, size: int, new_asset: bool) -> JSONResponse | None:
    """Plan limits, atomically: lock the tenant row, count, and let the caller insert its reservation row before the lock is released.
    Returns a 402 response when a limit is hit (the lock is released by the rollback)."""
    t = db.query(Tenant).filter(Tenant.id == ctx.tenant.id).with_for_update(of=Tenant).populate_existing().first()
    if t is None:
        raise HTTPException(404, "workspace not found")
    u = assetlib.usage(db, t.id, t.plan)
    if new_asset and u["count"] >= u["limitCount"]:
        db.rollback()
        return JSONResponse(status_code=402, content=assetlib.limit_payload(t, u["count"], u["limitCount"], "media items"))
    if u["bytes"] + size > u["limitBytes"]:
        db.rollback()
        mb = u["limitBytes"] // media.MB
        return JSONResponse(status_code=402, content={**assetlib.limit_payload(t, u["bytes"], u["limitBytes"], f"{mb} MB of media storage"), "detail": f"Not enough media storage: your {t.plan.name} plan includes {mb} MB ({u['bytes'] / media.MB:.1f} MB used). Delete something or upgrade."})
    return None


async def _read_upload(request: Request, file: UploadFile) -> bytes:
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_REQUEST:
        raise HTTPException(413, "That upload is too large (maximum 16 MB for a video, 10 MB for a PDF, 5 MB for an image)")
    return await file.read(max(media.MAX_BYTES.values()) + 1)


async def _prepare(data: bytes, declared: str) -> tuple[media.Detected, bytes, bytes | None, str]:
    """(detected, bytes to store, thumbnail or None, mime to store). Images are re-encoded (EXIF/GPS dropped, max 1600 px)."""
    found = media.validate_upload(data, declared)
    if found.kind == "image":
        import asyncio
        jpeg, thumb, _, _ = await asyncio.to_thread(media.process_image, data)
        if len(jpeg) > media.MAX_BYTES["image"]:
            raise media.MediaError(413, f"Images can be at most {media.MAX_BYTES['image'] // media.MB} MB")
        return found, jpeg, thumb, "image/jpeg"
    return found, data, None, found.mime


# ------------------------------------------------------------------ list
@router.get("")
def list_assets(ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(Asset).filter(Asset.tenant_id == ctx.tenant.id).order_by(Asset.created_at.desc()).all()
    variants: dict[str, list[AssetVariant]] = {}
    for v in db.query(AssetVariant).filter(AssetVariant.tenant_id == ctx.tenant.id).order_by(AssetVariant.created_at).all():
        variants.setdefault(v.asset_id, []).append(v)
    used = assetlib.used_counts(db, ctx.tenant.id)
    return {"items": [assetlib.asset_out(a, variants.get(a.id), used.get(a.id, 0)) for a in rows if a.storage_key or a.kind == "link"], "usage": assetlib.usage(db, ctx.tenant.id, ctx.tenant.plan)}


# ------------------------------------------------------------------ upload a file
@router.post("", status_code=201, response_model=None)
async def upload(request: Request, file: UploadFile = File(...), label: str = Form(default=""), tags: str = Form(default=""), ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> Any:
    _rate_limit(ctx.tenant.id)
    try:
        data = await _read_upload(request, file)
        found, stored, thumb, mime = await _prepare(data, file.content_type or "")
    except (media.MediaError, storage.StorageError) as e:
        _fail(str(e))
        raise _http(e)
    size = len(stored)
    limited = _reserve(db, ctx, size, new_asset=True)
    if limited is not None:
        return limited
    a = Asset(tenant_id=ctx.tenant.id, kind=found.kind, label=(label.strip() or "Untitled")[:120], tags=media.clean_tags(tags), mime=mime, size_bytes=size,
              sha256=hashlib.sha256(stored).hexdigest(), storage_backend=storage.get_storage().name, created_by=ctx.user.id)
    db.add(a)
    db.commit()  # the reservation now counts against the plan limits; the lock is released before we wait for the storage provider
    aid = a.id
    put_keys: list[str] = []
    try:
        st = storage.get_storage(a.storage_backend)
        ext = "jpg" if found.kind == "image" else found.ext
        a.storage_key = await st.put(ctx.tenant.id, stored, ext, found.kind)
        put_keys.append(a.storage_key)
        if thumb is not None:
            a.thumb_key = await st.put(ctx.tenant.id, thumb, "jpg", "image")
            put_keys.append(a.thumb_key)
        db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="asset.upload", detail=f"{found.kind} {size}B"))
        db.commit()
    except BaseException as e:  # noqa: BLE001 - provider error, client disconnect, anything: release the reservation and any stored object
        db.rollback()
        for k in put_keys:
            try:
                await storage.get_storage(a.storage_backend).delete(k)
            except Exception:  # noqa: BLE001
                pass
        gone = db.get(Asset, aid)
        if gone is not None:
            db.delete(gone)
            db.commit()
        _fail("storage")
        if isinstance(e, storage.StorageError):
            raise _http(e)
        raise
    return assetlib.asset_out(a)


# ------------------------------------------------------------------ links
class LinkIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    label: str = Field(default="", max_length=120)
    tags: list[str] | str = Field(default_factory=list)


_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


async def fetch_title(url: str) -> str:
    """Best effort, never fatal: the page <title> (no redirects, 5 s, first 200 KB)."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=False) as c:
            async with c.stream("GET", url, headers={"User-Agent": "ConnectFlowBot/1.0"}) as r:
                if r.status_code >= 400:
                    return ""
                buf = b""
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > 200_000 or b"</title>" in buf.lower():
                        break
        m = _TITLE.search(buf.decode("utf-8", "ignore"))
        return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()[:200] if m else ""
    except Exception:  # noqa: BLE001
        return ""


@router.post("/link", status_code=201, response_model=None)
async def add_link(body: LinkIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> Any:
    _rate_limit(ctx.tenant.id)
    url = body.url.strip()
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname:
        raise HTTPException(422, "Links must start with https://")
    try:
        await assert_crawlable(url)
    except UnsafeUrl as e:
        raise HTTPException(422, f"That link cannot be used: {e}")
    limited = _reserve(db, ctx, 0, new_asset=True)
    if limited is not None:
        return limited
    title = await fetch_title(url)
    a = Asset(tenant_id=ctx.tenant.id, kind="link", label=(body.label.strip() or title or u.hostname or "Link")[:120], tags=media.clean_tags(body.tags), link_url=url, link_title=title,
              created_by=ctx.user.id)
    db.add(a)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="asset.link", detail=(u.hostname or "")[:80]))
    db.commit()
    return assetlib.asset_out(a)


# ------------------------------------------------------------------ edit / delete
class PatchIn(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    tags: list[str] | str | None = None


@router.patch("/{asset_id}")
def patch(asset_id: str, body: PatchIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    a = _own(db, ctx, asset_id)
    if body.label is not None:
        a.label = body.label.strip()[:120] or a.label
    if body.tags is not None:
        a.tags = media.clean_tags(body.tags)
    db.commit()
    variants = db.query(AssetVariant).filter(AssetVariant.asset_id == a.id).order_by(AssetVariant.created_at).all()
    return assetlib.asset_out(a, variants, assetlib.used_counts(db, ctx.tenant.id).get(a.id, 0))


@router.delete("/{asset_id}")
async def delete(asset_id: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    a = _own(db, ctx, asset_id)
    if assetlib.attached_to_open_draft(db, a.id):
        raise HTTPException(409, "This item is attached to a message that is waiting for approval or being sent. Send or discard that message first.")
    assetlib.forget_in_profile(db, ctx.tenant.id, a.id)
    files = await assetlib.delete_files(db, a)  # stored objects first; the rows (variants, draft links) go with the asset
    db.delete(a)
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="asset.delete", detail=a.kind))
    db.commit()
    return {"ok": True, "filesRemoved": files}


# ------------------------------------------------------------------ variants
@router.post("/{asset_id}/variants", status_code=201, response_model=None)
async def add_variant(asset_id: str, request: Request, file: UploadFile = File(...), ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> Any:
    a = _own(db, ctx, asset_id)
    if a.kind == "link":
        raise HTTPException(422, "Links have no variants")
    if db.query(AssetVariant).filter(AssetVariant.asset_id == a.id).count() >= media.MAX_VARIANTS:
        raise HTTPException(422, f"An item can have at most {media.MAX_VARIANTS} variants")
    _rate_limit(ctx.tenant.id)
    try:
        data = await _read_upload(request, file)
        found, stored, _, mime = await _prepare(data, file.content_type or "")
    except (media.MediaError, storage.StorageError) as e:
        _fail(str(e))
        raise _http(e)
    if found.kind != a.kind:
        raise HTTPException(422, f"A variant must be the same kind as the item ({a.kind})")
    limited = _reserve(db, ctx, len(stored), new_asset=False)
    if limited is not None:
        return limited
    v = AssetVariant(asset_id=a.id, tenant_id=ctx.tenant.id, storage_backend=storage.get_storage().name, mime=mime, size_bytes=len(stored), sha256=hashlib.sha256(stored).hexdigest())
    db.add(v)
    db.commit()
    vid = v.id
    try:
        v.storage_key = await storage.get_storage(v.storage_backend).put(ctx.tenant.id, stored, "jpg" if found.kind == "image" else found.ext, found.kind)
        db.commit()
    except BaseException as e:  # noqa: BLE001
        db.rollback()
        gone = db.get(AssetVariant, vid)
        if gone is not None:
            db.delete(gone)
            db.commit()
        _fail("storage")
        if isinstance(e, storage.StorageError):
            raise _http(e)
        raise
    variants = db.query(AssetVariant).filter(AssetVariant.asset_id == a.id).order_by(AssetVariant.created_at).all()
    return assetlib.asset_out(a, variants)


@router.delete("/{asset_id}/variants/{variant_id}")
async def delete_variant(asset_id: str, variant_id: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    a = _own(db, ctx, asset_id)
    v = db.query(AssetVariant).filter(AssetVariant.id == variant_id, AssetVariant.asset_id == a.id, AssetVariant.tenant_id == ctx.tenant.id).first()
    if not v:
        raise HTTPException(404, "variant not found")
    if v.storage_key:
        try:
            await storage.get_storage(v.storage_backend).delete(v.storage_key)
        except Exception as e:  # noqa: BLE001
            log.warning("could not delete a variant file: %s", e.__class__.__name__)
    db.delete(v)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ file bytes (authenticated, never public)
@router.get("/{asset_id}/file", response_model=None)
async def file_bytes(asset_id: str, thumb: int = Query(0), variant: str = Query(""), ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> Any:
    a = _own(db, ctx, asset_id)
    if a.kind == "link":
        raise HTTPException(404, "links have no file")
    backend, key, mime = a.storage_backend, a.storage_key, a.mime
    if thumb:
        if not a.thumb_key:
            raise HTTPException(404, "no thumbnail")
        key, mime = a.thumb_key, "image/jpeg"
    elif variant:
        v = db.query(AssetVariant).filter(AssetVariant.id == variant, AssetVariant.asset_id == a.id, AssetVariant.tenant_id == ctx.tenant.id).first()
        if not v:
            raise HTTPException(404, "variant not found")
        backend, key, mime = v.storage_backend, v.storage_key, v.mime
    if not key:
        raise HTTPException(404, "file not available")
    try:
        st = storage.get_storage(backend)
        it: AsyncIterator[bytes] = st.stream(key)
        first = await it.__anext__()  # surfaces a missing file / provider error as a proper status code before streaming starts
    except StopAsyncIteration:
        first = b""
        it = _empty()
    except storage.StorageError as e:
        raise HTTPException(e.status if e.status in (404, 507) else 502, str(e))

    async def body() -> AsyncIterator[bytes]:
        if first:
            yield first
        async for chunk in it:
            yield chunk

    return StreamingResponse(body(), media_type=mime or "application/octet-stream", headers={
        "Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'",
        "Content-Disposition": 'inline; filename="asset"'})


async def _empty() -> AsyncIterator[bytes]:
    if False:  # an async generator that yields nothing
        yield b""

