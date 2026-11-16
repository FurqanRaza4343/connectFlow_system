"""The 14-day free trial.

A NEW trial workspace gets `trial_ends_at = now + 14 days`. Older workspaces keep NULL = no expiry (no surprise lock-out).
After the end date a workspace without a live subscription is read-only: it keeps its data, but cannot send WhatsApp messages,
call the AI, post Statuses or add numbers until it chooses a plan."""
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from .models import Tenant

TRIAL_DAYS = 14
LIVE_STATUSES = ("active", "trialing", "past_due")
TRIAL_ENDED_MSG = "Your 14-day free trial has ended. Your data is safe - choose a plan to keep sending messages."


def new_trial_end(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) + timedelta(days=TRIAL_DAYS)


def trial_state(t: Tenant | None, now: datetime | None = None) -> dict[str, Any] | None:
    """None = no trial limit applies (paid, or a workspace from before trials expired). Otherwise {endsAt, daysLeft, expired}."""
    if not t or t.plan_id != "trial" or not t.trial_ends_at or t.subscription_status in LIVE_STATUSES:
        return None
    now = now or datetime.now(timezone.utc)
    secs = (t.trial_ends_at - now).total_seconds()
    return {"endsAt": t.trial_ends_at.isoformat(), "daysLeft": max(0, int(-(-secs // 86400))), "expired": secs <= 0}


def trial_expired(t: Tenant | None) -> bool:
    s = trial_state(t)
    return bool(s and s["expired"])


def require_active(t: Tenant | None) -> None:
    """Raise 402 for an expired trial (HTTP handlers). Engines that have their own error type check `trial_expired` instead."""
    if trial_expired(t):
        raise HTTPException(402, TRIAL_ENDED_MSG)


async def trial_reminders() -> int:
    """Worker (hourly, one replica): one e-mail to the owner when the trial has <= 2 days left, one when it has ended."""
    from html import escape

    from .config import settings
    from .db import SessionLocal
    from .mailer import layout, send_email
    from .models import Membership, User

    sent = 0
    with SessionLocal() as db:
        for t in db.query(Tenant).filter(Tenant.plan_id == "trial", Tenant.trial_ends_at.isnot(None), Tenant.trial_reminder != "ended").all():
            st = trial_state(t)
            if not st:
                continue
            kind = "ended" if st["expired"] else ("soon" if st["daysLeft"] <= 2 and t.trial_reminder == "" else "")
            if not kind:
                continue
            owner = db.query(User).join(Membership, Membership.user_id == User.id).filter(Membership.tenant_id == t.id, Membership.role == "owner").first()
            if owner:
                link = f"{settings.public_app_url.rstrip('/')}/#billing"
                msg = (f"Your free trial of ConnectFlow ends in {st['daysLeft']} day(s)." if kind == "soon" else TRIAL_ENDED_MSG)
                await send_email(owner.email, "Your ConnectFlow trial " + ("is ending soon" if kind == "soon" else "has ended"), f"{msg}\nChoose a plan: {link}",
                                 layout("Your free trial", escape(msg), ("Choose a plan", link)))
                sent += 1
            t.trial_reminder = kind
            db.commit()
    return sent
