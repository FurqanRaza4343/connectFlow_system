"""WhatsApp Status (24-hour stories) engine: create / render / post / schedule / delete, plan caps, the autopilot tick.

Ported from legacy/orchestrator/src/status.mjs (see docs/STATUS.md). The rules that matter:
  * a post is moved to 'posting' (with a timestamp) BEFORE the gateway call; if the process dies or the answer is uncertain it is never re-sent
    by itself (a duplicate story is worse than a missing one): the row becomes 'failed' and the operator decides;
  * the workspace's Outreach "dry run" switch is honoured; the gateway number must be ready; a 429 from the gateway only reschedules;
  * a Status picture has no tappable area: the link lives in the CAPTION (that is the part WhatsApp makes tappable);
  * text on the picture is drawn by us (status_render.py), never by an AI image model;
  * the Baileys engine posts to exactly the `recipients` list (<= 256 jids), built from the workspace's contacted/replied leads + a manual list.
Images live in the storage backend (local volume or Cloudinary), never in the database."""
import asyncio
import base64
import logging
import random
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import ai, status_render as render, wa_client
from .config import settings
from .db import SessionLocal
from .leadtools import normalize_phone
from .deps import rds
from .models import Instance, Lead, OutreachProfile, StatusPost, StatusProfile, StatusUsageDaily, Tenant
from .storage import StorageError, get_storage

log = logging.getLogger("status")

MAX_CAPTION = 1024
MAX_UPLOAD = 3 * 1024 * 1024
MAX_AUDIENCE = 256
STALE_POSTING = timedelta(minutes=10)
# what a plan may do (workspace plans are trial | starter | growth): posts a day, autopilot, campaigns
PLAN_POSTS_PER_DAY = {"trial": 1, "starter": 2, "growth": 5}
PLAN_AUTOPILOT = {"trial": False, "starter": True, "growth": True}
PLAN_CAMPAIGNS = {"trial": False, "starter": True, "growth": True}
HARD_MAX_PER_DAY = 5
_LINK = re.compile(r"^https?://[^\s]{3,}$", re.I)


class StatusError(Exception):
    """A problem the operator can read (the router turns it into an HTTP error)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def plan_limits(plan_id: str) -> dict[str, Any]:
    return {"postsPerDay": PLAN_POSTS_PER_DAY.get(plan_id, 2), "autopilot": PLAN_AUTOPILOT.get(plan_id, True), "campaigns": PLAN_CAMPAIGNS.get(plan_id, True)}


# ---------------------------------------------------------------- time
def tz_of(db: Session, tenant_id: str) -> ZoneInfo:
    p = db.get(OutreachProfile, tenant_id)
    try:
        return ZoneInfo(p.timezone if p and p.timezone else "UTC")
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def local_date(now: datetime, tz: ZoneInfo) -> date:
    return now.astimezone(tz).date()


def local_minutes(now: datetime, tz: ZoneInfo) -> int:
    n = now.astimezone(tz)
    return n.hour * 60 + n.minute


def to_min(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def zoned_to_utc(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    h, m = hhmm.split(":")
    return datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=tz).astimezone(timezone.utc)


def iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


# ---------------------------------------------------------------- profile (brand kit + autopilot)
def get_profile(db: Session, tenant_id: str) -> StatusProfile:
    p = db.get(StatusProfile, tenant_id)
    if not p:
        p = StatusProfile(tenant_id=tenant_id)
        db.add(p)
        db.commit()
        db.refresh(p)
    return p


_TEMPLATES = set(render.TEMPLATES)


def _digits(v: str) -> str:
    return re.sub(r"\D", "", v or "")


def update_profile(db: Session, tenant_id: str, plan_id: str, data: dict[str, Any]) -> StatusProfile:
    """Validate and save the brand kit / autopilot settings (only keys that are present)."""
    p = get_profile(db, tenant_id)
    lim = plan_limits(plan_id)
    op = db.get(OutreachProfile, tenant_id)
    region = op.default_country if op and op.default_country else "PK"     # "0300 1234567" -> 923001234567
    for k, v in data.items():
        if v is None:
            continue
        if k in ("accent", "dark", "ink"):
            if v and not render.is_hex(v):
                raise StatusError(422, f"{k} must look like #1a73e8")
            setattr(p, k, str(v).lower())
        elif k == "auto":
            if bool(v) and not lim["autopilot"]:
                raise StatusError(402, "Autopilot is not included in your plan: upgrade to turn it on")
            p.auto = bool(v)
        elif k == "times":
            t = [x.strip() for x in str(v).split(",") if x.strip()]
            if len(t) > 5 or any(not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", x) for x in t):
                raise StatusError(422, "times must look like 11:00,19:00 (up to 5 times)")
            p.times = ",".join(x.zfill(5) for x in t) or "11:00"
        elif k == "per_day":
            p.per_day = min(HARD_MAX_PER_DAY, lim["postsPerDay"], max(1, int(v)))
        elif k == "approval":
            if v not in ("review", "auto"):
                raise StatusError(422, "approval must be review or auto")
            p.approval = v
        elif k == "image_style":
            if v not in ("card", "ai", "mixed"):
                raise StatusError(422, "image_style must be card, ai or mixed")
            p.image_style = v
        elif k in ("image_daily_cap", "image_monthly_cap"):
            setattr(p, k, min(20 if k == "image_daily_cap" else 600, max(0, int(v))))
        elif k == "language":
            if v not in ("English", "Roman Urdu", "Urdu"):
                raise StatusError(422, "language must be English, Roman Urdu or Urdu")
            p.language = v
        elif k == "footer":
            p.footer = str(v)[:160]
        elif k == "button_text":
            p.button_text = str(v).strip()[:40]
        elif k == "button_link":
            if v and v != "wa" and not _LINK.match(str(v)):
                raise StatusError(422, 'button_link must be "wa" or start with https://')
            p.button_link = str(v)[:300]
        elif k == "wa_number":
            d = normalize_phone(str(v), region)[0] if str(v).strip() else ""
            if str(v).strip() and not d:
                raise StatusError(422, "wa_number must be a valid phone number")
            p.wa_number = d
        elif k == "wa_text":
            p.wa_text = str(v).strip()[:120]
        elif k == "templates":
            lst = [x.strip() for x in str(v).split(",") if x.strip()]
            if any(x not in _TEMPLATES for x in lst):
                raise StatusError(422, "templates has an unknown design")
            p.templates = ",".join(dict.fromkeys(lst)) or ",".join(render.TEMPLATES)
        elif k == "offers":
            p.offers = str(v)[:2000]
        elif k == "recipients":
            nums = [normalize_phone(x, region)[0] for x in str(v).split(",") if x.strip()]
            if any(not n for n in nums) or len(nums) > MAX_AUDIENCE:
                raise StatusError(422, f"recipients: every entry must be a valid phone number (max {MAX_AUDIENCE})")
            p.recipients = ",".join(dict.fromkeys(nums))
        elif k == "instance_id":
            if v:
                inst = db.query(Instance).filter(Instance.id == v, Instance.tenant_id == tenant_id).first()
                if not inst:
                    raise StatusError(404, "number not found")
            p.instance_id = v or None
    db.commit()
    db.refresh(p)
    return p


# ---------------------------------------------------------------- links and captions (pure)
def resolve_link(link: str, p: StatusProfile, session_phone: str = "") -> str:
    """'' = none, 'wa' = a wa.me chat link (number + opening text from the brand kit), otherwise a plain https:// URL; anything else is dropped."""
    v = (link or "").strip()
    if not v:
        return ""
    if v == "wa":
        num = _digits(p.wa_number or session_phone)
        return f"https://wa.me/{num}" + (f"?text={_quote(p.wa_text)}" if p.wa_text else "") if num else ""
    return v[:300] if _LINK.match(v) else ""


def _quote(s: str) -> str:
    from urllib.parse import quote
    return quote(s, safe="")


def full_caption(post: Any, p: StatusProfile, session_phone: str = "") -> str:
    """Caption = text + contact line + "👉 button text: link", cut so that the LINK always survives the 1024-character limit."""
    url = resolve_link(post.btn_link, p, session_phone)
    label = (post.cta or p.button_text or "Open").strip()
    tail = "\n".join(x for x in ((post.link or "").strip(), f"👉 {label}: {url}" if url else "") if x)
    room = max(0, MAX_CAPTION - (len(tail) + 2 if tail else 0))
    head = (post.caption or "").strip()
    if len(head) > room:
        head = re.sub(r"\s+\S*$", "", head[: max(0, room - 1)]) + "…"
    return "\n\n".join(x for x in (head, tail) if x)[:MAX_CAPTION]


def clean_fields(f: dict[str, Any]) -> dict[str, Any]:
    """Validate / trim the editable fields of a post (raises StatusError 422 for a bad link)."""
    def c(x: Any, n: int) -> str:
        return re.sub(r"[ \t]+", " ", str("" if x is None else x)).strip()[:n]

    def col(x: Any) -> str:
        return str(x).lower() if render.is_hex(x) else ""

    link = c(f.get("btn_link"), 300)
    if link and link != "wa" and not _LINK.match(link):
        raise StatusError(422, 'The button link must start with https:// (or choose "Chat on WhatsApp")')
    try:
        ov = min(85, max(0, round(float(f.get("overlay", 48)))))
    except (TypeError, ValueError):
        ov = 48
    return {
        "mode": f.get("mode") if f.get("mode") in ("ai", "prompt", "own", "auto", "campaign") else "own", "kind": "text" if f.get("kind") == "text" else "image",
        "template": f.get("template") if f.get("template") in _TEMPLATES else "gradient",
        "headline": c(f.get("headline"), 120), "sub": c(f.get("sub"), 160), "badge": c(f.get("badge"), 14), "cta": c(f.get("cta"), 40),
        "caption": str(f.get("caption") or "").strip()[:900], "link": c(f.get("link"), 200), "prompt": c(f.get("prompt"), 800), "image_prompt": c(f.get("image_prompt"), 300),
        "accent": col(f.get("accent")), "dark": col(f.get("dark")), "ink": col(f.get("ink")), "btn_link": link, "overlay": ov,
        "pos": f.get("pos") if f.get("pos") in render.POSITIONS else "center", "plain": bool(f.get("plain")),
    }


# ---------------------------------------------------------------- rendering
def card_options(f: dict[str, Any], p: StatusProfile, tenant: Tenant, bg_data_url: str = "") -> dict[str, Any]:
    has_link = bool(f.get("btn_link"))
    logo = tenant.logo_data if tenant.logo_data and re.match(r"^data:image/(png|jpeg|webp);base64,", tenant.logo_data) else ""
    return {
        "template": f["template"], "headline": f["headline"], "sub": f["sub"], "badge": f["badge"],
        "cta": f["cta"] or (p.button_text if has_link else ""), "hint": "Tap the link below" if has_link else "",
        "footer": "\n".join(x for x in (p.footer, f["link"]) if x),
        "accent": f["accent"] or p.accent or tenant.brand_color or "", "dark": f["dark"] or p.dark or tenant.sidebar_color or "", "ink": f["ink"] or p.ink,
        "logo": logo or "", "bg": bg_data_url, "overlay": f["overlay"], "pos": f["pos"], "plain": f["plain"],
    }


def _sniff(data: bytes) -> str:
    return "image/png" if data[:4] == b"\x89PNG" else "image/jpeg" if data[:3] == b"\xff\xd8\xff" else "image/webp" if data[:4] == b"RIFF" else ""


async def _bg_data_url(key: str) -> str:
    if not key:
        return ""
    try:
        data = await get_storage().get(key)
    except StorageError:
        return ""
    mt = _sniff(data)
    return f"data:{mt};base64,{base64.b64encode(data).decode()}" if mt else ""


async def preview_png(db: Session, tenant: Tenant, f: dict[str, Any], bg_key: str = "") -> bytes:
    if not render.renderer_available():
        raise StatusError(503, "The image renderer is not installed on this server (a text status is used instead)")
    p = get_profile(db, tenant.id)
    opts = card_options(clean_fields(f), p, tenant, await _bg_data_url(bg_key))
    return await asyncio.to_thread(render.render_card, opts)


async def save_upload(tenant_id: str, data_url: str) -> str:
    """Store a picture the operator uploaded (PNG / JPEG / WebP, <= 3 MB, magic bytes checked). Returns its storage key."""
    m = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", data_url or "")
    if not m:
        raise StatusError(422, "The picture must be a PNG, JPEG or WebP image")
    raw = base64.b64decode(m.group(2))
    if len(raw) > MAX_UPLOAD:
        raise StatusError(413, "The picture is too large (max 3 MB)")
    if _sniff(raw) != f"image/{m.group(1)}":
        raise StatusError(422, "The file content does not match its image type")
    key = await get_storage().put(tenant_id, raw, "jpg" if m.group(1) == "jpeg" else m.group(1), "image")
    rds.set(f"status:upload:{tenant_id}:{key}", "1", ex=6 * 3600)   # only a key this workspace uploaded can be used as a background
    return key


async def _build_media(tenant: Tenant, p: StatusProfile, f: dict[str, Any], *, style: str, bg_upload: str, reuse_bg: str, reuse_is_upload: bool) -> tuple[str, bool, str, str]:
    """-> (bg_key, bg_is_upload, image_key, note). Picture problems degrade to a plain card; they never raise (except a bad upload key)."""
    bg_key, is_up, note = reuse_bg, reuse_is_upload, ""
    if bg_upload:
        if not rds.exists(f"status:upload:{tenant.id}:{bg_upload}"):
            raise StatusError(422, "The uploaded picture is missing or expired: upload it again")
        bg_key, is_up = bg_upload, True
    elif not bg_key and style in ("ai", "mixed"):
        note = "AI pictures are not set up on this server yet. A designed card was used."
    image_key = ""
    if f["kind"] == "image":
        if render.renderer_available():
            png = await asyncio.to_thread(render.render_card, card_options(f, p, tenant, await _bg_data_url(bg_key)))
            image_key = await get_storage().put(tenant.id, png, "png", "image")
        else:
            note = (note + " " if note else "") + "Image renderer not installed: it will be posted as a text status."
    return bg_key, is_up, image_key, note


async def _drop_files(*keys: str, keep: str = "") -> None:
    for k in keys:
        if k and k != keep:
            try:
                await get_storage().delete(k)
            except StorageError:
                pass


# ---------------------------------------------------------------- usage
def bump_usage(db: Session, tenant_id: str, tz: ZoneInfo, **inc: int) -> None:
    day = local_date(datetime.now(timezone.utc), tz)
    stmt = pg_insert(StatusUsageDaily).values(tenant_id=tenant_id, day=day, **inc)
    stmt = stmt.on_conflict_do_update(index_elements=["tenant_id", "day"], set_={k: getattr(StatusUsageDaily, k) + v for k, v in inc.items()})
    db.execute(stmt)
    db.commit()


def usage_out(db: Session, tenant_id: str) -> dict[str, Any]:
    tz = tz_of(db, tenant_id)
    today = local_date(datetime.now(timezone.utc), tz)
    t = db.get(StatusUsageDaily, (tenant_id, today))
    first = today.replace(day=1)
    m = db.query(func.coalesce(func.sum(StatusUsageDaily.images), 0), func.coalesce(func.sum(StatusUsageDaily.copies), 0), func.coalesce(func.sum(StatusUsageDaily.posted), 0),
                 func.coalesce(func.sum(StatusUsageDaily.cost_micro), 0)).filter(StatusUsageDaily.tenant_id == tenant_id, StatusUsageDaily.day >= first).one()
    p = get_profile(db, tenant_id)
    return {"today": {"images": t.images if t else 0, "copies": t.copies if t else 0, "posted": t.posted if t else 0},
            "month": {"images": int(m[0]), "copies": int(m[1]), "posted": int(m[2]), "costUsd": int(m[3]) / 1e6},
            "caps": {"imagesPerDay": p.image_daily_cap, "imagesPerMonth": p.image_monthly_cap}}


def posted_today(db: Session, tenant_id: str, tz: ZoneInfo) -> int:
    """Posts that went out today (local day) plus the ones being sent right now: what counts against the plan's daily limit."""
    today = local_date(datetime.now(timezone.utc), tz)
    start = datetime(today.year, today.month, today.day, tzinfo=tz).astimezone(timezone.utc)
    done = db.query(func.count(StatusPost.id)).filter(StatusPost.tenant_id == tenant_id, StatusPost.state == "posted", StatusPost.posted_at >= start).scalar() or 0
    sending = db.query(func.count(StatusPost.id)).filter(StatusPost.tenant_id == tenant_id, StatusPost.state == "posting").scalar() or 0
    return int(done) + int(sending)


# ---------------------------------------------------------------- rows
def post_out(s: StatusPost) -> dict[str, Any]:
    return {
        "id": s.id, "mode": s.mode, "kind": s.kind, "state": s.state, "template": s.template, "headline": s.headline, "sub": s.sub, "badge": s.badge, "cta": s.cta,
        "caption": s.caption, "link": s.link, "prompt": s.prompt, "imagePrompt": s.image_prompt, "hasImage": bool(s.image_key), "hasBg": bool(s.bg_key),
        "bgUpload": bool(s.bg_is_upload and s.bg_key), "accent": s.accent, "dark": s.dark, "ink": s.ink, "btnLink": s.btn_link, "overlay": s.overlay, "pos": s.pos, "plain": s.plain,
        "campaignId": s.campaign_id, "planTheme": s.plan_theme, "planDay": s.plan_day, "scheduledAt": iso(s.scheduled_at), "postedAt": iso(s.posted_at),
        "expiresAt": iso(s.expires_at), "error": s.error, "tries": s.tries, "auto": s.auto, "waId": bool(s.wa_status_id), "createdAt": iso(s.created_at),
    }


def get_post(db: Session, tenant_id: str, post_id: str) -> StatusPost:
    s = db.query(StatusPost).filter(StatusPost.id == post_id, StatusPost.tenant_id == tenant_id).first()
    if not s:
        raise StatusError(404, "post not found")
    return s


_EDITABLE = ("mode", "kind", "template", "headline", "sub", "badge", "cta", "caption", "link", "prompt", "image_prompt", "accent", "dark", "ink", "btn_link", "overlay", "pos", "plain")


async def create_post(db: Session, tenant: Tenant, data: dict[str, Any], *, action: str = "draft", scheduled_at: datetime | None = None, style: str = "card",
                      bg_upload: str = "", auto: bool = False, campaign_id: str | None = None, plan_theme: str = "", plan_day: int = 0) -> StatusPost:
    """action: draft | schedule (needs scheduled_at) | post (posts right now)."""
    f = clean_fields(data)
    if f["kind"] == "image" and not f["headline"] and not (f["plain"] and bg_upload):
        raise StatusError(422, "Write a headline for the picture")
    if f["kind"] == "text" and not f["headline"] and not f["caption"]:
        raise StatusError(422, "Write some text")
    if action == "schedule":
        if not scheduled_at or scheduled_at < datetime.now(timezone.utc) - timedelta(minutes=1):
            raise StatusError(422, "Choose a time in the future")
        if scheduled_at > datetime.now(timezone.utc) + timedelta(days=45):
            raise StatusError(422, "You can schedule at most 45 days ahead")
    p = get_profile(db, tenant.id)
    bg_key, is_up, image_key, note = await _build_media(tenant, p, f, style=style, bg_upload=bg_upload, reuse_bg="", reuse_is_upload=False)
    s = StatusPost(tenant_id=tenant.id, state="draft", auto=auto, campaign_id=campaign_id, plan_theme=plan_theme[:12], plan_day=plan_day, bg_key=bg_key, bg_is_upload=is_up,
                   image_key=image_key, error=note, **f)
    db.add(s)
    db.commit()
    db.refresh(s)
    if action == "schedule":
        s.state, s.scheduled_at = "scheduled", scheduled_at
        db.commit()
    if action == "post":
        await post_now(db, tenant.id, s.id, manual=True)
        db.refresh(s)
    return s


async def update_post(db: Session, tenant: Tenant, post_id: str, data: dict[str, Any], *, style: str = "card", bg_upload: str = "") -> StatusPost:
    s = get_post(db, tenant.id, post_id)
    if s.state not in ("draft", "scheduled", "failed", "cancelled"):
        raise StatusError(409, f'a "{s.state}" post cannot be edited')
    f = clean_fields({**{k: getattr(s, k) for k in _EDITABLE}, **data})
    p = get_profile(db, tenant.id)
    old_img = s.image_key
    bg_key, is_up, image_key, note = await _build_media(tenant, p, f, style=style, bg_upload=bg_upload, reuse_bg=s.bg_key, reuse_is_upload=s.bg_is_upload)
    for k, v in f.items():
        setattr(s, k, v)
    s.bg_key, s.bg_is_upload, s.image_key, s.error = bg_key, is_up, image_key, note
    if s.state in ("failed", "cancelled"):
        s.state = "draft"
    db.commit()
    await _drop_files(old_img, keep=image_key)
    db.refresh(s)
    return s


def schedule_post(db: Session, tenant_id: str, post_id: str, when: datetime) -> StatusPost:
    s = get_post(db, tenant_id, post_id)
    if s.state not in ("draft", "scheduled", "failed"):
        raise StatusError(409, f'a "{s.state}" post cannot be scheduled')
    if when < datetime.now(timezone.utc) - timedelta(minutes=1):
        raise StatusError(422, "Choose a time in the future")
    s.state, s.scheduled_at, s.error, s.tries = "scheduled", when, "", 0
    db.commit()
    return s


def cancel_post(db: Session, tenant_id: str, post_id: str) -> None:
    s = get_post(db, tenant_id, post_id)
    if s.state not in ("draft", "scheduled"):
        raise StatusError(409, "only a draft or scheduled post can be cancelled")
    s.state = "cancelled"
    db.commit()


async def remove_post(db: Session, tenant_id: str, post_id: str) -> None:
    s = db.query(StatusPost).filter(StatusPost.id == post_id, StatusPost.tenant_id == tenant_id).first()
    if not s:
        return
    if s.state == "posting":
        raise StatusError(409, "this post is being sent right now")
    keys = (s.image_key, s.bg_key if not s.bg_is_upload else "")   # an uploaded picture may be reused by another post: the sweeper clears orphans
    db.delete(s)
    db.commit()
    await _drop_files(*keys)


# ---------------------------------------------------------------- audience and number
def audience(db: Session, tenant_id: str, p: StatusProfile) -> list[str]:
    """jids that may see the story: contacted / replied leads + the manual list, at most 256 (Baileys posts to exactly this list)."""
    nums: dict[str, None] = {}
    for (wa,) in db.query(Lead.whatsapp).filter(Lead.tenant_id == tenant_id, Lead.whatsapp != "", Lead.status.in_(("contacted", "replied"))).order_by(Lead.created_at.desc()).limit(MAX_AUDIENCE).all():
        nums[_digits(wa)] = None
    for n in p.recipients.split(","):
        if n.strip():
            nums[_digits(n)] = None
    return [f"{n}@c.us" for n in list(nums)[:MAX_AUDIENCE] if n]


def pick_instance(db: Session, tenant_id: str, p: StatusProfile) -> Instance | None:
    q = db.query(Instance).filter(Instance.tenant_id == tenant_id, Instance.status == "ready", Instance.over_limit.is_(False))
    if p.instance_id:
        inst = q.filter(Instance.id == p.instance_id).first()
        if inst:
            return inst
    return q.order_by(Instance.created_at).first()


# ---------------------------------------------------------------- posting
def _notify(db: Session, tenant_id: str, type_: str, title: str, body: str = "") -> None:
    try:
        from .outreach import notify
        notify(db, tenant_id, type_, title, body, link="")
        db.commit()
    except Exception:  # noqa: BLE001
        log.exception("status notification failed")


def _reschedule(db: Session, s: StatusPost, why: str, minutes: int, *, inc: int = 1) -> dict[str, Any]:
    s.tries += inc
    if s.tries >= 6:
        s.state, s.error = "failed", f"Gave up after {s.tries} tries: {why}"[:300]
        db.commit()
        _notify(db, s.tenant_id, "failed", "Status was not posted", s.error)
        return {"posted": False, "error": why}
    s.state, s.scheduled_at, s.error = "scheduled", datetime.now(timezone.utc) + timedelta(minutes=minutes), f"Waiting: {why}"[:300]
    db.commit()
    return {"posted": False, "rescheduled": True, "why": why}


async def post_now(db: Session, tenant_id: str, post_id: str, *, manual: bool) -> dict[str, Any]:
    """Post one status. manual=True (a person pressed the button) raises readable errors; the worker (manual=False) reschedules or records the failure.
    Returns {posted, dryRun?, rescheduled?, statusId?}."""
    s = get_post(db, tenant_id, post_id)
    if s.state not in ("draft", "scheduled", "failed"):
        raise StatusError(409, f'a "{s.state}" post cannot be posted')
    tenant = db.get(Tenant, tenant_id)
    assert tenant is not None
    op = db.get(OutreachProfile, tenant_id)
    p = get_profile(db, tenant_id)
    if op is None or op.dry_run:   # no Outreach profile yet = the default, which is dry-run ON
        s.state, s.scheduled_at, s.error = "draft", None, "Dry-run is ON (Outreach settings): nothing was posted."
        db.commit()
        return {"posted": False, "dryRun": True}
    tz = tz_of(db, tenant_id)
    cap = min(HARD_MAX_PER_DAY, plan_limits(tenant.plan_id)["postsPerDay"])
    if posted_today(db, tenant_id, tz) >= cap:
        why = f"your plan allows {cap} Status post(s) a day"
        if manual:
            raise StatusError(429, f"Daily Status limit reached: {why}")
        return _reschedule(db, s, f"Daily limit reached ({why})", 120, inc=0 if s.tries < 4 else 1)
    inst = pick_instance(db, tenant_id, p)
    fake = settings.outreach_fake_send
    if not inst and not fake:
        why = "No WhatsApp number is connected and ready"
        if manual:
            raise StatusError(409, why)
        return _reschedule(db, s, why, 15)
    recipients = audience(db, tenant_id, p)
    if not recipients and not fake:
        why = "No audience yet: add numbers in Status settings, or contact some leads first (a Status is seen by your contacts)"
        if manual:
            raise StatusError(409, why)
        s.state, s.error = "failed", why
        db.commit()
        _notify(db, tenant_id, "failed", "Status was not posted", why)
        return {"posted": False, "error": why}
    claimed = db.query(StatusPost).filter(StatusPost.id == s.id, StatusPost.state.in_(("draft", "scheduled", "failed"))).update(
        {"state": "posting", "tries": StatusPost.tries + 1, "error": "", "claimed_at": datetime.now(timezone.utc), "instance_id": inst.id if inst else None}, synchronize_session=False)
    db.commit()
    if not claimed:
        raise StatusError(409, "this post is already being sent")
    db.refresh(s)
    session_phone = inst.phone if inst else ""
    caption = full_caption(s, p, session_phone)
    try:
        if fake:
            data: dict[str, Any] = {"statusId": f"fake-{uuid.uuid4().hex[:12]}"}
        elif s.kind == "image" and s.image_key:
            png = await get_storage().get(s.image_key)
            data = await wa_client.send_status_image(inst.openwa_id, png, caption=caption, mimetype="image/png", recipients=recipients)  # type: ignore[union-attr]
        else:
            text = "\n\n".join(x for x in (s.headline, s.sub, caption, s.cta if s.cta and s.cta not in caption else "") if x)[:4000]
            data = await wa_client.send_status_text(inst.openwa_id, text, background_color=s.accent or p.accent or tenant.brand_color or "#004ECB", recipients=recipients)  # type: ignore[union-attr]
        d = data.get("data") if isinstance(data.get("data"), dict) else data
        now = datetime.now(timezone.utc)
        s.state, s.posted_at, s.error = "posted", now, ""
        s.wa_status_id = str(d.get("statusId") or d.get("id") or "")[:120]
        try:
            s.expires_at = datetime.fromisoformat(str(d["expiresAt"]).replace("Z", "+00:00")) if d.get("expiresAt") else now + timedelta(hours=24)
        except ValueError:
            s.expires_at = now + timedelta(hours=24)
        db.commit()
        bump_usage(db, tenant_id, tz, posted=1)
        if s.auto or s.campaign_id:
            _notify(db, tenant_id, "info", "Status posted", f'"{s.headline or s.caption[:50]}" is live for 24 hours.')
        return {"posted": True, "statusId": s.wa_status_id}
    except wa_client.WAError as e:
        if e.status == 429:
            return _reschedule(db, s, f"WhatsApp gateway pacing: {str(e)[:120]}", 20, inc=0)
        uncertain = e.status == 503   # no real answer (timeout / unreachable): it may or may not have been posted
        msg = (f"Unsure whether it was posted ({str(e)[:140]}). Check WhatsApp Status before posting again." if uncertain else str(e)[:300])
        s.state, s.error = "failed", msg
        db.commit()
        _notify(db, tenant_id, "failed", "Status was not posted", msg)
        if manual:
            raise StatusError(502, msg)
        return {"posted": False, "error": msg}
    except StorageError as e:
        s.state, s.error = "failed", f"The picture could not be read: {e}"[:300]
        db.commit()
        if manual:
            raise StatusError(502, s.error)
        return {"posted": False, "error": s.error}


async def delete_from_whatsapp(db: Session, tenant_id: str, post_id: str) -> None:
    s = get_post(db, tenant_id, post_id)
    if s.state != "posted" or not s.wa_status_id:
        raise StatusError(409, "only a posted status can be deleted from WhatsApp")
    p = get_profile(db, tenant_id)
    inst = db.get(Instance, s.instance_id) if s.instance_id else pick_instance(db, tenant_id, p)
    if not settings.outreach_fake_send:
        if not inst or inst.status != "ready":
            raise StatusError(409, "The number that posted it is not connected: delete it on the phone instead")
        try:
            await wa_client.delete_status(inst.openwa_id, s.wa_status_id)
        except wa_client.WAError as e:
            raise StatusError(502, f"WhatsApp refused to delete it ({str(e)[:140]}). Delete it on the phone instead.")
    s.state, s.wa_status_id, s.error = "cancelled", "", "Deleted from WhatsApp"
    db.commit()


# ---------------------------------------------------------------- idea helpers
def recent_headlines(db: Session, tenant_id: str, days: int = 14) -> list[str]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return [h for (h,) in db.query(StatusPost.headline).filter(StatusPost.tenant_id == tenant_id, StatusPost.headline != "", StatusPost.created_at >= since).order_by(StatusPost.created_at.desc()).limit(30).all()]


async def make_ideas(db: Session, tenant: Tenant, prompt: str = "", count: int = 1) -> list[dict]:
    op = db.get(OutreachProfile, tenant.id) or OutreachProfile(tenant_id=tenant.id)
    p = get_profile(db, tenant.id)
    try:
        ideas = await ai.status_ideas(op, op.company_name or tenant.name, offers=p.offers, prompt=prompt[:800], recent=recent_headlines(db, tenant.id), language=p.language, count=count)
    except ai.AILimitError:
        raise
    except ai.AIError as e:
        raise StatusError(502, str(e))
    bump_usage(db, tenant.id, tz_of(db, tenant.id), copies=1)
    if not ideas:
        raise StatusError(502, "The AI returned no usable idea; try again or write your own text")
    return ideas


def pick_template(idea: dict, p: StatusProfile) -> str:
    allowed = [t for t in p.templates.split(",") if t in _TEMPLATES] or list(render.TEMPLATES)
    withbadge = [t for t in allowed if t in ("badge", "gradient", "split", "bold")]
    pool = withbadge if idea.get("badge") and withbadge else [t for t in allowed if t != "badge"]
    return random.choice(pool or allowed)


# ---------------------------------------------------------------- autopilot + due posts (called by the worker under a leader lock)
async def _autopilot_make(db: Session, tenant: Tenant, p: StatusProfile, now: datetime) -> None:
    [idea] = (await make_ideas(db, tenant, "", 1))[:1]
    wants_ai = p.image_style in ("ai", "mixed")
    s = await create_post(db, tenant, {**idea, "mode": "auto", "kind": "image", "template": pick_template(idea, p), "image_prompt": idea.get("imagePrompt", ""),
                                       "cta": idea.get("cta") or (p.button_text if p.button_link else ""), "btn_link": p.button_link}, style="ai" if wants_ai else "card", auto=True)
    if p.approval == "auto":
        s.state, s.scheduled_at = "scheduled", now + timedelta(seconds=random.randint(0, 600))
        db.commit()
    else:
        _notify(db, tenant.id, "info", "Status draft is ready", f'"{s.headline}": open Status to review and post it.')


async def status_tick() -> None:
    """One pass: recover stale 'posting' rows, post what is due, make autopilot posts for the slots that have started."""
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        stale = db.query(StatusPost).filter(StatusPost.state == "posting", StatusPost.claimed_at < now - STALE_POSTING).all()
        for s in stale:   # the sender stopped mid-post: we cannot know the outcome, so never re-send by ourselves
            s.state, s.error = "failed", "Interrupted while sending. Check WhatsApp Status, then post again if it is not there."
        if stale:
            db.commit()
            log.warning("%s interrupted status post(s) marked failed (not re-sent)", len(stale))
        due = db.query(StatusPost.id, StatusPost.tenant_id).filter(StatusPost.state == "scheduled", StatusPost.scheduled_at <= now).order_by(StatusPost.scheduled_at).limit(20).all()
    for pid, tid in due:
        with SessionLocal() as db:
            try:
                await post_now(db, tid, pid, manual=False)
            except StatusError as e:
                log.info("status post %s: %s", pid, e)
            except Exception:  # noqa: BLE001
                log.exception("status post %s failed", pid)
    with SessionLocal() as db:
        for p in db.query(StatusProfile).filter(StatusProfile.auto.is_(True)).all():
            tenant = db.get(Tenant, p.tenant_id)
            if not tenant or tenant.status != "active" or not plan_limits(tenant.plan_id)["autopilot"]:
                continue
            tz = tz_of(db, tenant.id)
            d, mins = local_date(now, tz), local_minutes(now, tz)
            for t in [x.strip() for x in p.times.split(",") if x.strip()]:
                key = f"status:slot:{tenant.id}:{d.isoformat()}:{t}"
                if mins < to_min(t) or rds.get(key):
                    continue
                rds.set(key, "1", ex=3 * 86400)          # marker FIRST: a crash never makes two posts for one slot
                if mins > to_min(t) + 90:                 # the worker was down for a long time: skip the stale slot
                    continue
                made = db.query(func.count(StatusPost.id)).filter(StatusPost.tenant_id == tenant.id, StatusPost.auto.is_(True), StatusPost.state != "cancelled",
                                                                 StatusPost.created_at >= datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc)).scalar() or 0
                if made >= min(HARD_MAX_PER_DAY, p.per_day, plan_limits(tenant.plan_id)["postsPerDay"]):
                    continue
                try:
                    await _autopilot_make(db, tenant, p, now)
                except Exception as e:  # noqa: BLE001
                    log.warning("autopilot idea failed for a workspace: %s", e)
                    _notify(db, tenant.id, "failed", "Status autopilot could not make a post", str(e)[:200])
    # housekeeping once a day: old finished posts and their pictures
    if rds.set("status:clean", "1", ex=86400, nx=True):
        with SessionLocal() as db:
            old = db.query(StatusPost).filter(StatusPost.state.in_(("posted", "cancelled", "failed")), StatusPost.created_at < now - timedelta(days=45)).limit(200).all()
            for s in old:
                keys = (s.image_key, s.bg_key if not s.bg_is_upload else "")
                db.delete(s)
                db.commit()
                await _drop_files(*keys)
