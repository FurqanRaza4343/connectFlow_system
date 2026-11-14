"""WhatsApp Status CAMPAIGNS: a planned series of posts (e.g. 14 days, one every 12 hours).

Flow: brief -> slots (computed here from days, frequency and the posting window) -> the AI plans the themes, or asks at most 3 questions when it
truly cannot -> the AI writes every post in small batches -> each post is rendered as a card and saved as a DRAFT with its planned time -> the
operator reviews the calendar and presses "Approve all" (drafts become 'scheduled'; the worker's status_tick then posts them with all the usual
safety checks). Nothing is posted without that approval.

The slow AI steps (planning, writing) run in the WORKER: the API only records what the operator asked for (state 'planning' / 'generating'),
so a restart or a second replica never loses or duplicates the work. `campaign_step()` claims one campaign with FOR UPDATE SKIP LOCKED."""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import ai, status_engine as eng
from .db import SessionLocal
from .models import OutreachProfile, StatusCampaign, StatusPost, StatusProfile, Tenant

log = logging.getLogger("status")
MAX_SLOTS, MAX_DAYS, BATCH = 150, 45, 5
CLAIM_STALE = timedelta(minutes=5)
_TIME = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


# ---------------------------------------------------------------- slots (pure)
def _hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def day_times(*, interval_hours: int = 0, per_day: int = 1, window_from: str = "10:00", window_to: str = "21:00") -> list[str]:
    """The posting times of one day: every `interval_hours` from the window start, or `per_day` evenly spaced inside the window."""
    a, b = eng.to_min(window_from), eng.to_min(window_to)
    if not b > a:
        raise eng.StatusError(422, "The posting window must end after it starts")
    times: list[int] = []
    if interval_hours > 0:
        m = a
        while m <= b and len(times) < 5:
            times.append(m)
            m += interval_hours * 60
    else:
        n = min(5, max(1, per_day))
        step = (b - a) / (n - 1) if n > 1 else 0
        times = [round(a + step * i) for i in range(n)]
    return [_hm(x) for x in times]


def compute_slots(*, days: int, interval_hours: int, per_day: int, window_from: str, window_to: str, start: date, tz: ZoneInfo, now: datetime | None = None) -> list[dict[str, Any]]:
    """Every planned post time of a campaign, in order. Times that are already in the past (day 1 = today) are skipped."""
    now = now or datetime.now(timezone.utc)
    times = day_times(interval_hours=interval_hours, per_day=per_day, window_from=window_from, window_to=window_to)
    slots: list[dict[str, Any]] = []
    for d in range(days):
        day = start + timedelta(days=d)
        for t in times:
            at = eng.zoned_to_utc(day, t, tz)
            if at < now + timedelta(minutes=5):
                continue
            slots.append({"n": len(slots) + 1, "day": d + 1, "date": day.isoformat(), "time": t, "at": at.isoformat()})
    if not slots:
        raise eng.StatusError(422, "No post time is left in the future: choose a later start date")
    if len(slots) > MAX_SLOTS:
        raise eng.StatusError(422, f"That is {len(slots)} posts: the limit is {MAX_SLOTS} per campaign (fewer days or fewer posts a day)")
    return slots


def offers_allowed(offers: str, brief: str) -> bool:
    return bool(offers.strip()) or bool(re.search(r"\b(off|discount|sale|offer|promo|deal)\b|%", brief, re.I))


# ---------------------------------------------------------------- rows
def get_campaign(db: Session, tenant_id: str, cid: str) -> StatusCampaign:
    c = db.query(StatusCampaign).filter(StatusCampaign.id == cid, StatusCampaign.tenant_id == tenant_id).first()
    if not c:
        raise eng.StatusError(404, "campaign not found")
    return c


def campaign_out(db: Session, c: StatusCampaign) -> dict[str, Any]:
    slots = (c.plan or {}).get("slots", [])
    per_day: dict[str, int] = {}
    for s in slots:
        per_day[s["date"]] = per_day.get(s["date"], 0) + 1
    counts: dict[str, int] = {str(k): int(v) for k, v in db.query(StatusPost.state, func.count(StatusPost.id)).filter(StatusPost.campaign_id == c.id).group_by(StatusPost.state).all()}
    return {
        "id": c.id, "name": c.name, "brief": c.brief, "goal": c.goal, "days": c.days, "intervalHours": c.interval_hours, "perDay": c.per_day, "windowFrom": c.window_from,
        "windowTo": c.window_to, "startDate": c.start_date, "language": c.language, "style": c.style, "state": c.state, "questions": c.questions or [], "summary": c.summary,
        "error": c.error, "busySince": eng.iso(c.busy_since), "total": c.total, "generated": c.generated, "counts": counts, "slots": slots,
        "maxPerDay": max(per_day.values(), default=0), "createdAt": eng.iso(c.created_at),
    }


def list_campaigns(db: Session, tenant_id: str) -> list[dict[str, Any]]:
    return [campaign_out(db, c) for c in db.query(StatusCampaign).filter(StatusCampaign.tenant_id == tenant_id).order_by(StatusCampaign.created_at.desc()).limit(30).all()]


# ---------------------------------------------------------------- create / answer / replan / generate (API side: only records the request)
def create_campaign(db: Session, tenant: Tenant, data: dict[str, Any]) -> StatusCampaign:
    lim = eng.plan_limits(tenant.plan_id)
    if not lim["campaigns"]:
        raise eng.StatusError(402, "Campaigns are not included in your plan: upgrade to plan a campaign")
    brief = str(data.get("brief") or "").strip()
    if len(brief) < 8:
        raise eng.StatusError(422, "Tell the AI what to promote (at least a short sentence)")
    days = int(data.get("days") or 0)
    if not 1 <= days <= MAX_DAYS:
        raise eng.StatusError(422, f"Days must be between 1 and {MAX_DAYS}")
    interval = int(data.get("interval_hours") or 0)
    interval = interval if interval in (6, 8, 12, 24) else 0
    per_day = min(5, max(1, int(data.get("per_day") or 1)))
    wf = str(data.get("window_from") or "10:00")
    wt = str(data.get("window_to") or "21:00")
    wf, wt = (wf if _TIME.match(wf) else "10:00").zfill(5), (wt if _TIME.match(wt) else "21:00").zfill(5)
    tz = eng.tz_of(db, tenant.id)
    today = eng.local_date(datetime.now(timezone.utc), tz)
    sd = str(data.get("start_date") or "")
    start = date.fromisoformat(sd) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", sd) else today + timedelta(days=1)
    if start < today:
        raise eng.StatusError(422, "The start date cannot be in the past")
    slots = compute_slots(days=days, interval_hours=interval, per_day=per_day, window_from=wf, window_to=wt, start=start, tz=tz)
    perday: dict[str, int] = {}
    for s in slots:
        perday[s["date"]] = perday.get(s["date"], 0) + 1
    if max(perday.values()) > lim["postsPerDay"]:
        raise eng.StatusError(422, f"Your plan allows {lim['postsPerDay']} Status post(s) a day: choose a lower frequency")
    p = eng.get_profile(db, tenant.id)
    language = data.get("language") if data.get("language") in ("English", "Roman Urdu", "Urdu") else p.language
    c = StatusCampaign(tenant_id=tenant.id, name=(str(data.get("name") or "").strip() or brief[:40])[:80], brief=brief[:1200],
                       goal=data.get("goal") if data.get("goal") in ("awareness", "leads", "offer", "trust") else "awareness", days=days, interval_hours=interval, per_day=per_day,
                       window_from=wf, window_to=wt, start_date=start.isoformat(), language=language, style="ai" if data.get("style") == "ai" else "card",
                       state="planning", plan={"slots": slots}, total=len(slots), busy_since=datetime.now(timezone.utc))
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def answer_questions(db: Session, tenant_id: str, cid: str, answers: dict[str, Any]) -> StatusCampaign:
    c = get_campaign(db, tenant_id, cid)
    if c.state != "asking":
        raise eng.StatusError(409, "this campaign is not waiting for answers")
    clean = {q["question"]: (str((answers or {}).get(q["id"], "")).strip()[:300] or "(no preference: you decide)") for q in (c.questions or [])}
    c.answers, c.state, c.error, c.busy_since, c.claimed_at = clean, "planning", "", datetime.now(timezone.utc), None
    db.commit()
    return c


def replan(db: Session, tenant_id: str, cid: str, instruction: str) -> StatusCampaign:
    c = get_campaign(db, tenant_id, cid)
    if c.state not in ("planned", "review", "failed"):
        raise eng.StatusError(409, "this campaign cannot be re-planned now")
    if db.query(func.count(StatusPost.id)).filter(StatusPost.campaign_id == c.id, StatusPost.state.in_(("draft", "scheduled", "posted"))).scalar():
        raise eng.StatusError(409, "Posts were already written: cancel this campaign and start a new one to change the plan")
    extra = (instruction or "").strip()[:300]
    if extra:
        c.brief = f"{c.brief}\nChange requested by the operator: {extra}"[:1500]
    c.answers = c.answers or {"Operator instruction": extra or "(re-plan)"}
    c.state, c.error, c.busy_since, c.claimed_at = "planning", "", datetime.now(timezone.utc), None
    db.commit()
    return c


def request_generate(db: Session, tenant_id: str, cid: str) -> StatusCampaign:
    c = get_campaign(db, tenant_id, cid)
    if c.state not in ("planned", "failed", "generating"):
        raise eng.StatusError(409, f'a "{c.state}" campaign cannot be generated')
    if c.state != "generating":
        c.state, c.error, c.busy_since, c.claimed_at = "generating", "", datetime.now(timezone.utc), None
        db.commit()
    return c


# ---------------------------------------------------------------- approve / pause / resume / cancel / remove
def approve(db: Session, tenant_id: str, cid: str) -> dict[str, Any]:
    c = get_campaign(db, tenant_id, cid)
    if c.state != "review":
        raise eng.StatusError(409, "only a campaign in review can be approved")
    now = datetime.now(timezone.utc)
    scheduled = late = 0
    for s in db.query(StatusPost).filter(StatusPost.campaign_id == c.id, StatusPost.state == "draft").all():
        if s.scheduled_at and s.scheduled_at > now + timedelta(minutes=1):
            s.state, s.error = "scheduled", ""
            scheduled += 1
        else:
            s.state, s.error = "cancelled", "Its planned time had already passed when the campaign was approved"
            late += 1
    if not scheduled:
        db.rollback()
        raise eng.StatusError(409, "Every planned time has passed: create the campaign again with a later start date")
    c.state = "running"
    db.commit()
    op = db.get(OutreachProfile, tenant_id)
    return {"scheduled": scheduled, "skippedLate": late, "dryRun": bool(op.dry_run) if op else True}


def pause(db: Session, tenant_id: str, cid: str) -> None:
    c = get_campaign(db, tenant_id, cid)
    if c.state != "running":
        raise eng.StatusError(409, "only a running campaign can be paused")
    db.query(StatusPost).filter(StatusPost.campaign_id == c.id, StatusPost.state == "scheduled").update({"state": "draft"}, synchronize_session=False)
    c.state = "paused"
    db.commit()


def resume(db: Session, tenant_id: str, cid: str) -> None:
    c = get_campaign(db, tenant_id, cid)
    if c.state != "paused":
        raise eng.StatusError(409, "only a paused campaign can be resumed")
    now = datetime.now(timezone.utc)
    for s in db.query(StatusPost).filter(StatusPost.campaign_id == c.id, StatusPost.state == "draft").all():
        if s.scheduled_at and s.scheduled_at > now + timedelta(minutes=1):
            s.state = "scheduled"
        else:
            s.state, s.error = "cancelled", "Its planned time passed while the campaign was paused"
    c.state = "running"
    db.commit()


def cancel(db: Session, tenant_id: str, cid: str) -> None:
    c = get_campaign(db, tenant_id, cid)
    if c.state in ("done", "cancelled"):
        raise eng.StatusError(409, "this campaign is already finished")
    db.query(StatusPost).filter(StatusPost.campaign_id == c.id, StatusPost.state.in_(("draft", "scheduled"))).update({"state": "cancelled"}, synchronize_session=False)
    c.state = "cancelled"
    db.commit()


async def remove_campaign(db: Session, tenant_id: str, cid: str) -> None:
    c = db.query(StatusCampaign).filter(StatusCampaign.id == cid, StatusCampaign.tenant_id == tenant_id).first()
    if not c:
        return
    if c.state in ("running", "generating"):
        raise eng.StatusError(409, "cancel or pause it first")
    for s in db.query(StatusPost).filter(StatusPost.campaign_id == c.id, StatusPost.state.in_(("draft", "cancelled", "failed"))).all():
        await eng.remove_post(db, tenant_id, s.id)
    db.query(StatusPost).filter(StatusPost.campaign_id == c.id).update({"campaign_id": None}, synchronize_session=False)   # posted ones stay in the list
    db.delete(c)
    db.commit()


async def rewrite_post(db: Session, tenant: Tenant, post_id: str, instruction: str = "") -> StatusPost:
    """Rewrite one post (same theme and angle, new words)."""
    s = eng.get_post(db, tenant.id, post_id)
    if s.state not in ("draft", "scheduled", "failed"):
        raise eng.StatusError(409, "this post cannot be rewritten now")
    prompt = (f"Theme: {s.plan_theme}. " if s.plan_theme else "") + (f"Idea: {s.prompt}. " if s.prompt else "") + f'Write it again in different words than "{s.headline}".' \
        + (f" Operator instruction: {instruction[:200]}." if instruction else "")
    [idea] = (await eng.make_ideas(db, tenant, prompt, 1))[:1]
    return await eng.update_post(db, tenant, post_id, {"headline": idea["headline"], "sub": idea["sub"], "badge": idea["badge"], "caption": idea["caption"], "cta": idea["cta"] or s.cta,
                                                    "image_prompt": idea.get("imagePrompt", "")})


# ---------------------------------------------------------------- worker side: the AI steps
def _claim(db: Session) -> StatusCampaign | None:
    now = datetime.now(timezone.utc)
    c = (db.query(StatusCampaign).filter(StatusCampaign.state.in_(("planning", "generating")), (StatusCampaign.claimed_at.is_(None)) | (StatusCampaign.claimed_at < now - CLAIM_STALE))
         .order_by(StatusCampaign.busy_since).with_for_update(skip_locked=True).first())
    if c:
        c.claimed_at = now
        db.commit()
    return c


async def campaign_step() -> bool:
    """Do one unit of AI work for one campaign (plan it, or write all its posts). Returns True when it found work."""
    with SessionLocal() as db:
        c = _claim(db)
        if not c:
            return False
        cid, tid, state = c.id, c.tenant_id, c.state
    with SessionLocal() as db:
        try:
            if state == "planning":
                await _plan(db, cid)
            else:
                await _write_all(db, cid)
        except Exception as e:  # noqa: BLE001 - a campaign problem must never stop the loop
            log.exception("campaign %s failed", cid)
            db.rollback()
            c2 = db.get(StatusCampaign, cid)
            if c2 and c2.state in ("planning", "generating"):
                c2.state, c2.error, c2.claimed_at = "failed", str(e)[:300], None
                db.commit()
                eng._notify(db, tid, "failed", "Campaign step failed", str(e)[:200])
    return True


async def _context(db: Session, c: StatusCampaign) -> tuple[Tenant, OutreachProfile, StatusProfile]:
    tenant = db.get(Tenant, c.tenant_id)
    assert tenant is not None
    return tenant, db.get(OutreachProfile, c.tenant_id) or OutreachProfile(tenant_id=c.tenant_id), eng.get_profile(db, c.tenant_id)


async def _plan(db: Session, cid: str) -> None:
    c = db.get(StatusCampaign, cid)
    assert c is not None
    tenant, op, sp = await _context(db, c)
    slots = (c.plan or {}).get("slots", [])
    out = await ai.plan_campaign(op, op.company_name or tenant.name, brief=c.brief, goal=c.goal, slots=slots, language=c.language, offers=sp.offers,
                                 recent=eng.recent_headlines(db, c.tenant_id), answers=c.answers or None, offers_allowed=offers_allowed(sp.offers, c.brief))
    eng.bump_usage(db, c.tenant_id, eng.tz_of(db, c.tenant_id), copies=1)
    db.refresh(c)
    if c.state != "planning":     # cancelled / changed meanwhile
        return
    if out.get("questions"):
        c.state, c.questions, c.error = "asking", out["questions"], ""
    else:
        by_n = {r["n"]: r for r in out["roadmap"]}
        c.plan = {"slots": [{**s, "theme": by_n[s["n"]]["theme"], "angle": by_n[s["n"]]["angle"]} for s in slots]}
        c.state, c.summary, c.questions, c.error = "planned", out["summary"], [], ""
    c.claimed_at = None
    db.commit()


async def _write_all(db: Session, cid: str) -> None:
    c = db.get(StatusCampaign, cid)
    assert c is not None
    tenant, op, sp = await _context(db, c)
    slots = (c.plan or {}).get("slots", [])
    have = {s.scheduled_at.isoformat() for s in db.query(StatusPost).filter(StatusPost.campaign_id == c.id, StatusPost.state != "cancelled").all() if s.scheduled_at}
    todo = [s for s in slots if datetime.fromisoformat(s["at"]).isoformat() not in have]
    c.generated = len(slots) - len(todo)
    db.commit()
    allow = offers_allowed(sp.offers, c.brief)
    for i in range(0, len(todo), BATCH):
        db.refresh(c)
        if c.state != "generating":      # cancelled meanwhile
            return
        c.claimed_at = datetime.now(timezone.utc)   # heartbeat: another worker must not take over while we are alive
        db.commit()
        batch = todo[i:i + BATCH]
        items = await ai.write_campaign_items(op, op.company_name or tenant.name, items=[{"n": s["n"], "day": s["day"], "theme": s["theme"], "angle": s["angle"]} for s in batch],
                                              brief=c.brief, language=c.language, offers=sp.offers, offers_allowed=allow, avoid=eng.recent_headlines(db, c.tenant_id))
        eng.bump_usage(db, c.tenant_id, eng.tz_of(db, c.tenant_id), copies=1)
        for it in items:
            sl = next((s for s in batch if s["n"] == it["n"]), None)
            if not sl or not it["headline"]:
                continue
            post = await eng.create_post(db, tenant, {**it, "mode": "campaign", "kind": "image", "template": eng.pick_template(it, sp), "prompt": sl["angle"], "image_prompt": it.get("imagePrompt", ""),
                                                      "cta": it["cta"] or (sp.button_text if sp.button_link else ""), "btn_link": sp.button_link},
                                         style="ai" if c.style == "ai" else "card", campaign_id=c.id, plan_theme=sl["theme"], plan_day=sl["day"])
            post.scheduled_at = datetime.fromisoformat(sl["at"])
            c.generated += 1
            db.commit()
    done = db.query(func.count(StatusPost.id)).filter(StatusPost.campaign_id == c.id, StatusPost.state != "cancelled").scalar() or 0
    db.refresh(c)
    if c.state != "generating":
        return
    if done < len(slots):
        raise RuntimeError(f"Only {done} of {len(slots)} posts could be written: press Write posts again to finish the rest")
    c.state, c.claimed_at = "review", None
    db.commit()
    eng._notify(db, c.tenant_id, "info", "Campaign is ready to review", f'"{c.name}": {len(slots)} posts are written. Open Status, then Planner, and press Approve all.')


def campaign_housekeeping() -> None:
    """A running campaign with nothing left to post is done (called once a minute by the worker)."""
    with SessionLocal() as db:
        for c in db.query(StatusCampaign).filter(StatusCampaign.state == "running").all():
            open_n = db.query(func.count(StatusPost.id)).filter(StatusPost.campaign_id == c.id, StatusPost.state.in_(("draft", "scheduled", "posting"))).scalar() or 0
            if not open_n:
                c.state = "done"
                db.commit()
                posted = db.query(func.count(StatusPost.id)).filter(StatusPost.campaign_id == c.id, StatusPost.state == "posted").scalar() or 0
                eng._notify(db, c.tenant_id, "info", "Campaign finished", f'"{c.name}": {posted} post(s) went out.')


# ---------------------------------------------------------------- calendar + stats (for the planner page)
def _when(s: StatusPost) -> datetime:
    return s.posted_at or s.scheduled_at or s.created_at


def calendar(db: Session, tenant_id: str, month: str) -> dict[str, Any]:
    tz = eng.tz_of(db, tenant_id)
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        month = eng.local_date(datetime.now(timezone.utc), tz).isoformat()[:7]
    y, m = int(month[:4]), int(month[5:7])
    lo = datetime(y, m, 1, tzinfo=timezone.utc) - timedelta(hours=36)
    hi = (datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc)) + timedelta(hours=36)
    rows = db.query(StatusPost).filter(StatusPost.tenant_id == tenant_id, StatusPost.state != "cancelled",
                                       ((StatusPost.scheduled_at >= lo) & (StatusPost.scheduled_at <= hi)) | ((StatusPost.posted_at >= lo) & (StatusPost.posted_at <= hi))).all()
    items = [{"id": s.id, "state": s.state, "at": eng.iso(_when(s)), "date": eng.local_date(_when(s), tz).isoformat(), "headline": s.headline,
              "theme": s.plan_theme or ("auto" if s.auto else "manual"), "campaignId": s.campaign_id, "hasImage": bool(s.image_key), "error": s.error} for s in rows]
    return {"month": month, "items": sorted([i for i in items if str(i["date"]).startswith(month)], key=lambda i: str(i["at"] or ""))}


def stats(db: Session, tenant_id: str, back: int = 10, ahead: int = 14) -> dict[str, Any]:
    tz = eng.tz_of(db, tenant_id)
    today = eng.local_date(datetime.now(timezone.utc), tz)
    days = [(today + timedelta(days=i)).isoformat() for i in range(-back, ahead + 1)]
    series: dict[str, dict[str, Any]] = {d: {"day": d, "posted": 0, "planned": 0, "failed": 0} for d in days}
    themes: dict[str, int] = {}
    for s in db.query(StatusPost).filter(StatusPost.tenant_id == tenant_id, StatusPost.state != "cancelled").all():
        d = eng.local_date(_when(s), tz).isoformat()
        if d in series:
            series[d]["posted" if s.state == "posted" else "failed" if s.state == "failed" else "planned"] += 1
        th = s.plan_theme or ("auto" if s.auto else "manual")
        themes[th] = themes.get(th, 0) + 1
    states: dict[str, int] = {str(k): int(v) for k, v in db.query(StatusPost.state, func.count(StatusPost.id)).filter(StatusPost.tenant_id == tenant_id).group_by(StatusPost.state).all()}
    nxt = db.query(func.min(StatusPost.scheduled_at)).filter(StatusPost.tenant_id == tenant_id, StatusPost.state == "scheduled", StatusPost.scheduled_at > datetime.now(timezone.utc)).scalar()
    return {"today": today.isoformat(), "series": [series[d] for d in days], "themes": themes, "states": states, "nextAt": eng.iso(nxt), "usage": eng.usage_out(db, tenant_id)}
