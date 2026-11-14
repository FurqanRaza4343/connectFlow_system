"""WhatsApp Status (24-hour stories): posts, brand kit + autopilot, AI ideas, campaigns (planner), calendar and charts. Scoped to the caller's workspace.

Everything slow runs in the worker (campaign planning / writing) or is a single short call (ideas, preview). See docs/STATUS.md."""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import ai, status_campaigns as camp, status_engine as eng, status_render as render
from ..db import get_db
from ..deps import TCtx, need_role
from ..models import AuditLog, OutreachProfile, StatusPost
from ..storage import StorageError, get_storage

router = APIRouter(prefix="/api/status", tags=["status"])


class PostIn(BaseModel):
    mode: str = "own"
    kind: str = "image"
    template: str = "gradient"
    headline: str = Field(default="", max_length=120)
    sub: str = Field(default="", max_length=160)
    badge: str = Field(default="", max_length=14)
    cta: str = Field(default="", max_length=40)
    caption: str = Field(default="", max_length=900)
    link: str = Field(default="", max_length=200)
    btn_link: str = Field(default="", max_length=300)
    prompt: str = Field(default="", max_length=800)
    image_prompt: str = Field(default="", max_length=300)
    accent: str = ""
    dark: str = ""
    ink: str = ""
    overlay: int = 48
    pos: str = "center"
    plain: bool = False
    style: str = "card"             # card | ai | own (own = bg_upload from /upload)
    bg_upload: str = Field(default="", max_length=200)
    action: str = "draft"            # draft | schedule | post
    scheduled_at: datetime | None = None
    new_background: bool = False


class IdeasIn(BaseModel):
    prompt: str = Field(default="", max_length=800)
    count: int = Field(default=3, ge=1, le=3)


class UploadIn(BaseModel):
    data_url: str = Field(max_length=5_000_000)


class WhenIn(BaseModel):
    scheduled_at: datetime


class ProfileIn(BaseModel):
    instance_id: str | None = None
    auto: bool | None = None
    times: str | None = None
    per_day: int | None = None
    approval: str | None = None
    image_style: str | None = None
    image_daily_cap: int | None = None
    image_monthly_cap: int | None = None
    language: str | None = None
    footer: str | None = None
    accent: str | None = None
    dark: str | None = None
    ink: str | None = None
    button_text: str | None = None
    button_link: str | None = None
    wa_number: str | None = None
    wa_text: str | None = None
    templates: str | None = None
    offers: str | None = None
    recipients: str | None = None


class CampaignIn(BaseModel):
    brief: str = Field(max_length=1200)
    name: str = Field(default="", max_length=80)
    goal: str = "awareness"
    days: int = Field(default=7, ge=1, le=45)
    interval_hours: int = 0
    per_day: int = Field(default=1, ge=1, le=5)
    window_from: str = "10:00"
    window_to: str = "21:00"
    start_date: str = ""
    language: str = ""
    style: str = "card"


class AnswersIn(BaseModel):
    answers: dict[str, str] = {}


class TextIn(BaseModel):
    instruction: str = Field(default="", max_length=300)


def _profile_out(p: Any) -> dict[str, Any]:
    return {k: getattr(p, k) for k in ("instance_id", "auto", "times", "per_day", "approval", "image_style", "image_daily_cap", "image_monthly_cap", "language", "footer", "accent", "dark", "ink",
                                       "button_text", "button_link", "wa_number", "wa_text", "templates", "offers", "recipients")}


def _audit(db: Session, ctx: TCtx, action: str, detail: str = "") -> None:
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action=action, detail=detail[:300]))
    db.commit()


# ---------------------------------------------------------------- overview + settings
@router.get("")
def overview(ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> dict[str, Any]:
    p = eng.get_profile(db, ctx.tenant.id)
    op = db.get(OutreachProfile, ctx.tenant.id)
    posts = db.query(StatusPost).filter(StatusPost.tenant_id == ctx.tenant.id).order_by(StatusPost.created_at.desc()).limit(60).all()
    inst = eng.pick_instance(db, ctx.tenant.id, p)
    return {
        "posts": [eng.post_out(s) for s in posts], "usage": eng.usage_out(db, ctx.tenant.id), "profile": _profile_out(p),
        "capabilities": {"renderer": render.renderer_available(), "aiImages": False, "templates": render.TEMPLATES, "plan": eng.plan_limits(ctx.tenant.plan_id),
                         "dryRun": bool(op.dry_run) if op else True, "timezone": (op.timezone if op else "UTC"), "numberReady": bool(inst), "audience": len(eng.audience(db, ctx.tenant.id, p)),
                         "fake": False},
    }


@router.put("/profile")
def update_profile(body: ProfileIn, ctx: TCtx = Depends(need_role("admin")), db: Session = Depends(get_db)) -> dict[str, Any]:
    p = eng.update_profile(db, ctx.tenant.id, ctx.tenant.plan_id, body.model_dump(exclude_unset=True))
    _audit(db, ctx, "status.profile")
    return _profile_out(p)


# ---------------------------------------------------------------- ideas / preview / upload
@router.post("/ideas")
async def ideas(body: IdeasIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return {"ideas": await eng.make_ideas(db, ctx.tenant, body.prompt, body.count)}
    except ai.AILimitError as e:
        raise eng.StatusError(e.status, str(e))


@router.post("/preview")
async def preview(body: PostIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    import base64
    bg = body.bg_upload if body.style == "own" and body.bg_upload else ""
    if bg and not eng.rds.exists(f"status:upload:{ctx.tenant.id}:{bg}"):
        raise eng.StatusError(422, "The uploaded picture is missing or expired: upload it again")
    png = await eng.preview_png(db, ctx.tenant, body.model_dump(), bg)
    return {"png": "data:image/png;base64," + base64.b64encode(png).decode()}


@router.post("/upload")
async def upload(body: UploadIn, ctx: TCtx = Depends(need_role("operator"))) -> dict[str, str]:
    return {"file": await eng.save_upload(ctx.tenant.id, body.data_url)}


# ---------------------------------------------------------------- posts
@router.post("/posts", status_code=201)
async def create_post(body: PostIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.action not in ("draft", "schedule", "post"):
        raise eng.StatusError(422, "action must be draft, schedule or post")
    s = await eng.create_post(db, ctx.tenant, body.model_dump(), action=body.action, scheduled_at=body.scheduled_at, style=body.style, bg_upload=body.bg_upload if body.style == "own" else "")
    _audit(db, ctx, "status.create", s.id)
    return {"post": eng.post_out(s)}


@router.put("/posts/{post_id}")
async def update_post(post_id: str, body: PostIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    s = await eng.update_post(db, ctx.tenant, post_id, body.model_dump(), style=body.style, bg_upload=body.bg_upload if body.style == "own" else "")
    return {"post": eng.post_out(s)}


@router.get("/posts/{post_id}/image")
async def image(post_id: str, ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> Response:
    s = eng.get_post(db, ctx.tenant.id, post_id)
    if not s.image_key:
        raise eng.StatusError(404, "no image")
    try:
        data = await get_storage().get(s.image_key)
    except StorageError as e:
        raise eng.StatusError(404, str(e))
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/posts/{post_id}/post")
async def post_now(post_id: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    r = await eng.post_now(db, ctx.tenant.id, post_id, manual=True)
    _audit(db, ctx, "status.post", post_id)
    return r


@router.post("/posts/{post_id}/schedule")
def schedule(post_id: str, body: WhenIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"post": eng.post_out(eng.schedule_post(db, ctx.tenant.id, post_id, body.scheduled_at))}


@router.post("/posts/{post_id}/cancel")
def cancel(post_id: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, bool]:
    eng.cancel_post(db, ctx.tenant.id, post_id)
    return {"ok": True}


@router.post("/posts/{post_id}/delete-wa")
async def delete_wa(post_id: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, bool]:
    await eng.delete_from_whatsapp(db, ctx.tenant.id, post_id)
    _audit(db, ctx, "status.delete_wa", post_id)
    return {"ok": True}


@router.post("/posts/{post_id}/rewrite")
async def rewrite(post_id: str, body: TextIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return {"post": eng.post_out(await camp.rewrite_post(db, ctx.tenant, post_id, body.instruction))}
    except ai.AILimitError as e:
        raise eng.StatusError(e.status, str(e))


@router.delete("/posts/{post_id}")
async def remove(post_id: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, bool]:
    await eng.remove_post(db, ctx.tenant.id, post_id)
    return {"ok": True}


# ---------------------------------------------------------------- calendar + charts
@router.get("/calendar")
def calendar(month: str = Query(default=""), ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return camp.calendar(db, ctx.tenant.id, month)


@router.get("/stats")
def stats(ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return camp.stats(db, ctx.tenant.id)


# ---------------------------------------------------------------- campaigns (the AI steps run in the worker; poll GET /campaigns/{id})
@router.get("/campaigns")
def list_campaigns(ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"campaigns": camp.list_campaigns(db, ctx.tenant.id)}


@router.post("/campaigns", status_code=201)
def create_campaign(body: CampaignIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    c = camp.create_campaign(db, ctx.tenant, body.model_dump())
    _audit(db, ctx, "status.campaign.create", c.id)
    return {"campaign": camp.campaign_out(db, c)}


@router.get("/campaigns/{cid}")
def get_campaign(cid: str, ctx: TCtx = Depends(need_role("viewer")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"campaign": camp.campaign_out(db, camp.get_campaign(db, ctx.tenant.id, cid))}


@router.delete("/campaigns/{cid}")
async def delete_campaign(cid: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, bool]:
    await camp.remove_campaign(db, ctx.tenant.id, cid)
    return {"ok": True}


@router.post("/campaigns/{cid}/answer")
def answer(cid: str, body: AnswersIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"campaign": camp.campaign_out(db, camp.answer_questions(db, ctx.tenant.id, cid, body.answers))}


@router.post("/campaigns/{cid}/replan")
def replan(cid: str, body: TextIn, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"campaign": camp.campaign_out(db, camp.replan(db, ctx.tenant.id, cid, body.instruction))}


@router.post("/campaigns/{cid}/generate")
def generate(cid: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"campaign": camp.campaign_out(db, camp.request_generate(db, ctx.tenant.id, cid))}


@router.post("/campaigns/{cid}/approve")
def approve(cid: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, Any]:
    r = camp.approve(db, ctx.tenant.id, cid)
    _audit(db, ctx, "status.campaign.approve", cid)
    return r


@router.post("/campaigns/{cid}/pause")
def pause(cid: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, bool]:
    camp.pause(db, ctx.tenant.id, cid)
    return {"ok": True}


@router.post("/campaigns/{cid}/resume")
def resume(cid: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, bool]:
    camp.resume(db, ctx.tenant.id, cid)
    return {"ok": True}


@router.post("/campaigns/{cid}/cancel")
def cancel_campaign(cid: str, ctx: TCtx = Depends(need_role("operator")), db: Session = Depends(get_db)) -> dict[str, bool]:
    camp.cancel(db, ctx.tenant.id, cid)
    return {"ok": True}
