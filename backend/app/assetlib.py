"""Database-level helpers for the media library: usage/limits, API shapes, what a draft carries, variant rotation, purge.

File bytes are handled by app/storage.py, the validation/safety rules by app/media.py; routers/assets.py is the HTTP layer."""
import logging
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import bytecache, media, storage
from .deps import rds
from .models import Asset, AssetVariant, Draft, DraftAsset, Message, OutreachProfile, Plan, Tenant

log = logging.getLogger("assets")

FILE_KINDS = ("image", "document", "video")
OPEN_DRAFT = ("pending", "approved", "sending")
DEFAULT_MEDIA: dict[str, Any] = {
    "firstMessage": {"mode": "off", "assetIds": []},
    "followup1": {"assetIds": []},
    "followup2": {"assetIds": []},
    "replies": {"enabled": False, "allowTags": []},
}


# ------------------------------------------------------------------ usage and limits
def usage(db: Session, tenant_id: str, plan: Plan) -> dict[str, Any]:
    nbytes = (db.query(func.coalesce(func.sum(Asset.size_bytes), 0)).filter(Asset.tenant_id == tenant_id).scalar() or 0) \
        + (db.query(func.coalesce(func.sum(AssetVariant.size_bytes), 0)).filter(AssetVariant.tenant_id == tenant_id).scalar() or 0)
    count = db.query(func.count(Asset.id)).filter(Asset.tenant_id == tenant_id).scalar() or 0
    return {"bytes": int(nbytes), "limitBytes": int(plan.asset_bytes_limit), "count": int(count), "limitCount": int(plan.asset_count_limit), "plan": plan.id}


def limit_payload(t: Tenant, used: int, limit: int, what: str) -> dict[str, Any]:
    return {"detail": f"Your {t.plan.name} plan allows {limit} {what}. Upgrade to add more.", "limit": limit, "used": used, "plan": t.plan_id, "upgradeUrl": "/#billing"}


# ------------------------------------------------------------------ API shapes
def file_url(asset_id: str, thumb: bool = False, variant_id: str = "") -> str:
    q = []
    if thumb:
        q.append("thumb=1")
    if variant_id:
        q.append(f"variant={variant_id}")
    return f"/api/assets/{asset_id}/file" + (("?" + "&".join(q)) if q else "")


def asset_out(a: Asset, variants: list[AssetVariant] | None = None, used: int = 0) -> dict[str, Any]:
    is_link = a.kind == "link"
    return {
        "id": a.id, "kind": a.kind, "label": a.label, "tags": list(a.tags or []), "mime": a.mime, "sizeBytes": int(a.size_bytes or 0),
        "previewUrl": None if is_link else file_url(a.id), "thumbUrl": file_url(a.id, thumb=True) if (a.kind == "image" and a.thumb_key) else None,
        "linkUrl": a.link_url if is_link else None, "title": a.link_title if is_link else "", "usedCount": used, "createdAt": a.created_at.isoformat(),
        "variants": [{"id": v.id, "mime": v.mime, "sizeBytes": int(v.size_bytes or 0), "previewUrl": file_url(a.id, variant_id=v.id)} for v in (variants or [])],
    }


def attachment_out(a: Asset) -> dict[str, Any]:
    return {"assetId": a.id, "kind": a.kind, "label": a.label, "thumbUrl": file_url(a.id, thumb=True) if (a.kind == "image" and a.thumb_key) else None, "linkUrl": a.link_url if a.kind == "link" else None}


def draft_attachments(db: Session, draft_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """{draft_id: [attachment, ...]} for many drafts in ONE query (no N+1)."""
    out: dict[str, list[dict[str, Any]]] = {}
    if not draft_ids:
        return out
    rows = db.query(DraftAsset.draft_id, Asset).join(Asset, Asset.id == DraftAsset.asset_id).filter(DraftAsset.draft_id.in_(draft_ids)).order_by(DraftAsset.draft_id, DraftAsset.position).all()
    for did, a in rows:
        out.setdefault(did, []).append(attachment_out(a))
    return out


def link_of(attachments: list[dict[str, Any]]) -> str | None:
    return next((x["linkUrl"] for x in attachments if x.get("linkUrl")), None)


def message_attachments(db: Session, messages: list[Message]) -> dict[str, list[dict[str, Any]]]:
    """{message_id: [attachment, ...]} from Message.asset_ids (assets deleted since then are shown by kind only)."""
    ids = {x["assetId"] for m in messages for x in (m.asset_ids or []) if isinstance(x, dict) and x.get("assetId")}
    found = {a.id: a for a in db.query(Asset).filter(Asset.id.in_(ids or {"-"})).all()} if ids else {}
    out: dict[str, list[dict[str, Any]]] = {}
    for m in messages:
        items: list[dict[str, Any]] = []
        for x in (m.asset_ids or []):
            a = found.get(x.get("assetId", "")) if isinstance(x, dict) else None
            items.append(attachment_out(a) if a else {"assetId": x.get("assetId") if isinstance(x, dict) else None, "kind": m.media_kind or "media", "label": "(deleted)", "thumbUrl": None, "linkUrl": None})
        if items:
            out[m.id] = items
    return out


# ------------------------------------------------------------------ the outreach "media" settings
def normalize_media(raw: Any) -> dict[str, Any]:
    """Whatever is stored (maybe {} from before the feature) -> the full documented shape."""
    raw = raw if isinstance(raw, dict) else {}
    fm = raw.get("firstMessage") if isinstance(raw.get("firstMessage"), dict) else {}
    rp = raw.get("replies") if isinstance(raw.get("replies"), dict) else {}

    def ids(x: Any) -> list[str]:
        return [str(i) for i in (x.get("assetIds") if isinstance(x, dict) else []) or [] if isinstance(i, str)]

    mode = fm.get("mode") if fm.get("mode") in ("off", "image", "link") else "off"
    return {
        "firstMessage": {"mode": mode, "assetIds": ids(fm)},
        "followup1": {"assetIds": ids(raw.get("followup1"))},
        "followup2": {"assetIds": ids(raw.get("followup2"))},
        "replies": {"enabled": bool(rp.get("enabled")), "allowTags": media.clean_tags(rp.get("allowTags") if isinstance(rp.get("allowTags"), list) else [])},
    }


def validate_media_config(db: Session, tenant_id: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Check a submitted `media` block: every id is one of THIS workspace's assets, kinds fit the mode, counts are within limits."""
    cfg = normalize_media(cfg)
    wanted = {i for key in ("firstMessage", "followup1", "followup2") for i in cfg[key]["assetIds"]}
    found = {a.id: a for a in db.query(Asset).filter(Asset.tenant_id == tenant_id, Asset.id.in_(wanted or {"-"})).all()} if wanted else {}
    missing = wanted - set(found)
    if missing:
        raise HTTPException(422, "Some selected media items do not exist in this workspace")
    fm = cfg["firstMessage"]
    if len(fm["assetIds"]) > media.FIRST_MESSAGE_MAX_ASSETS:
        raise HTTPException(422, f"Choose at most {media.FIRST_MESSAGE_MAX_ASSETS} items for the first message (one of them is sent, rotating)")
    if fm["mode"] == "image":
        if not fm["assetIds"] or any(found[i].kind not in FILE_KINDS for i in fm["assetIds"]):
            raise HTTPException(422, "First message mode 'image' needs at least one image, PDF or video (and no links)")
    elif fm["mode"] == "link":
        if not fm["assetIds"] or any(found[i].kind != "link" for i in fm["assetIds"]):
            raise HTTPException(422, "First message mode 'link' needs at least one link (and no files)")
    else:
        fm["assetIds"] = []
    for key in ("followup1", "followup2"):
        if len(cfg[key]["assetIds"]) > media.FOLLOWUP_MAX_ASSETS:
            raise HTTPException(422, f"Choose at most {media.FOLLOWUP_MAX_ASSETS} items per follow-up")
    return cfg


# ------------------------------------------------------------------ what a draft carries
def assets_by_ids(db: Session, tenant_id: str, ids: list[str]) -> list[Asset]:
    """The tenant's assets in the order of `ids` (unknown ids silently skipped)."""
    if not ids:
        return []
    found = {a.id: a for a in db.query(Asset).filter(Asset.tenant_id == tenant_id, Asset.id.in_(ids)).all()}
    return [found[i] for i in ids if i in found]


def rr_next(name: str) -> int:
    """Stable round-robin counter in Redis (never raises: Redis trouble means 'first item')."""
    try:
        return int(rds.incr(f"asset:rr:{name}")) - 1
    except Exception:  # noqa: BLE001
        return 0


def pick_first(db: Session, tenant_id: str, cfg: dict[str, Any]) -> list[Asset]:
    """The ONE asset a first message carries: rotate through the configured candidates."""
    fm = cfg["firstMessage"]
    if fm["mode"] == "off" or not fm["assetIds"]:
        return []
    items = assets_by_ids(db, tenant_id, fm["assetIds"])
    items = [a for a in items if (a.kind == "link") == (fm["mode"] == "link")]
    return [items[media.pick_round_robin(len(items), rr_next(f"first:{tenant_id}"))]] if items else []


def attach(db: Session, draft: Draft, assets: list[Asset]) -> None:
    """Record the attachments of a draft; links are written into the text itself (once) so the approver sees exactly what will go out."""
    if not assets:
        return
    for pos, a in enumerate(assets):
        db.add(DraftAsset(draft_id=draft.id, asset_id=a.id, position=pos))
    draft.body = media.append_links(draft.body, [a.link_url for a in assets if a.kind == "link"])


def choose_variant(db: Session, a: Asset) -> tuple[str | None, str, str, str]:
    """(variant_id | None, backend, storage_key, mime) for this send: the asset's own file and its variants take turns."""
    variants = db.query(AssetVariant).filter(AssetVariant.asset_id == a.id).order_by(AssetVariant.created_at, AssetVariant.id).all()
    n = len(variants) + 1
    i = media.pick_round_robin(n, rr_next(f"v:{a.id}"))
    if i == 0:
        return None, a.storage_backend, a.storage_key, a.mime
    v = variants[i - 1]
    return v.id, v.storage_backend, v.storage_key, v.mime


def _count(name: str) -> None:
    """Hourly Redis counters shown on the admin capacity page (best effort)."""
    from datetime import datetime, timezone

    from .deps import rds

    try:
        k = f"media:{name}:{datetime.now(timezone.utc):%Y%m%d%H}"
        if rds.incr(k) == 1:
            rds.expire(k, 3 * 3600)
    except Exception:  # noqa: BLE001
        pass


async def read_file(backend: str, key: str) -> bytes:
    """The bytes of a stored file. Recently used files come from an in-process cache (see bytecache.py), so a campaign that sends the same
    image 300 times reads storage once instead of 300 times."""
    ck = bytecache.make_key(backend, key)
    data = bytecache.cache.get(ck)
    if data is not None:
        _count("cachehit")
        return data
    _count("cachemiss")
    data = await storage.get_storage(backend).get(key)  # the one real storage read
    _count("reads")
    bytecache.cache.put(ck, data)
    return data


# ------------------------------------------------------------------ removing files
async def delete_files(db: Session, a: Asset) -> int:
    """Delete every stored object of an asset (main file, thumbnail, variants). Best effort: a failure is logged and the sweep/ops can retry."""
    keys: list[tuple[str, str]] = []
    if a.storage_key:
        keys.append((a.storage_backend, a.storage_key))
    if a.thumb_key:
        keys.append((a.storage_backend, a.thumb_key))
    keys += [(v.storage_backend, v.storage_key) for v in db.query(AssetVariant).filter(AssetVariant.asset_id == a.id).all() if v.storage_key]
    n = 0
    for backend, key in keys:
        try:
            await storage.get_storage(backend).delete(key)
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("could not delete a stored file (%s): %s", backend, e.__class__.__name__)
    return n


async def purge_tenant(db: Session, tenant_id: str) -> int:
    """Remove all stored files of a workspace (admin deleting a customer). Rows go with the tenant (ON DELETE CASCADE)."""
    n = 0
    for a in db.query(Asset).filter(Asset.tenant_id == tenant_id).all():
        n += await delete_files(db, a)
    return n


def forget_in_profile(db: Session, tenant_id: str, asset_id: str) -> None:
    """An asset was deleted: drop it from the workspace's media settings so nothing keeps pointing at it."""
    p = db.get(OutreachProfile, tenant_id)
    if not p:
        return
    cfg = normalize_media(p.media)
    changed = False
    for key in ("firstMessage", "followup1", "followup2"):
        if asset_id in cfg[key]["assetIds"]:
            cfg[key]["assetIds"] = [i for i in cfg[key]["assetIds"] if i != asset_id]
            changed = True
    if cfg["firstMessage"]["mode"] != "off" and not cfg["firstMessage"]["assetIds"]:
        cfg["firstMessage"]["mode"] = "off"
    if changed:
        p.media = cfg


def used_counts(db: Session, tenant_id: str) -> dict[str, int]:
    rows = db.query(DraftAsset.asset_id, func.count(DraftAsset.id)).join(Asset, Asset.id == DraftAsset.asset_id).filter(Asset.tenant_id == tenant_id).group_by(DraftAsset.asset_id).all()
    return {r[0]: int(r[1]) for r in rows}


def attached_to_open_draft(db: Session, asset_id: str) -> bool:
    return db.query(DraftAsset.id).join(Draft, Draft.id == DraftAsset.draft_id).filter(DraftAsset.asset_id == asset_id, Draft.status.in_(OPEN_DRAFT)).first() is not None
