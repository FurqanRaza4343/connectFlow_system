"""Email alerts for the outreach module (needs-human reply, number down, batch failures, daily cap) + the optional daily digest.
Recipients = owner + admins of the workspace + up to 3 extra addresses from the outreach profile.
Throttled to 1 email / hour per (workspace, type) in Redis. Message previews are cut to 140 chars; nothing secret is ever included."""
import logging
import re
from datetime import timedelta
from html import escape

from sqlalchemy.orm import Session

from .deps import rds
from .mailer import layout, send_email
from .models import Draft, Membership, Message, OutreachProfile, Tenant, User

log = logging.getLogger("alerts")
EMAIL_RE = re.compile(r"^[^@\s,;<>]{1,64}@[^@\s,;<>]{1,255}\.[A-Za-z]{2,}$")
MAX_EXTRA = 3
FOOTER = "You can change these alerts in Outreach > Setup > Notifications."


def parse_extras(raw: str) -> list[str]:
    seen: list[str] = []
    for part in re.split(r"[,;\s]+", raw or ""):
        part = part.strip().lower()
        if part and part not in seen:
            seen.append(part)
    return seen


def validate_extras(raw: str) -> list[str]:
    """Raises ValueError with a customer-facing message."""
    items = parse_extras(raw)
    if len(items) > MAX_EXTRA:
        raise ValueError(f"At most {MAX_EXTRA} extra email addresses")
    for e in items:
        if not EMAIL_RE.match(e):
            raise ValueError(f"'{e[:60]}' is not a valid email address")
    return items


def preview(text: str, n: int = 140) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1] + "…"


def recipients(db: Session, tenant_id: str, p: OutreachProfile) -> list[str]:
    rows = db.query(User.email).join(Membership, Membership.user_id == User.id).filter(Membership.tenant_id == tenant_id, Membership.role.in_(("owner", "admin"))).all()
    out = [e.lower() for (e,) in rows]
    for e in parse_extras(p.notify_extra_emails)[:MAX_EXTRA]:
        if e not in out:
            out.append(e)
    return out


def _body(lines: list[str]) -> tuple[str, str]:
    text = "\n".join(lines + ["", FOOTER])
    html = "".join(f"<p style='margin:0 0 10px'>{escape(l)}</p>" for l in lines) + f"<p style='color:#7a8c82;font-size:12.5px;margin-top:18px'>{escape(FOOTER)}</p>"
    return text, html


async def send_alert(db: Session, tenant_id: str, type_: str, subject: str, lines: list[str], throttle: bool = True) -> int:
    """Returns the number of emails sent (0 when disabled / throttled)."""
    try:
        p = db.get(OutreachProfile, tenant_id)
        if not p or not p.notify_email:
            return 0
        if throttle and not rds.set(f"og:alert:{tenant_id}:{type_}", "1", nx=True, ex=3600):
            return 0
        tenant = db.get(Tenant, tenant_id)
        name = tenant.name if tenant else "ConnectFlow"
        text, html = _body(lines)
        sent = 0
        for to in recipients(db, tenant_id, p):
            if await send_email(to, f"[{name}] {subject}", text, layout(subject, html)):
                sent += 1
        return sent
    except Exception:  # noqa: BLE001  an alert must never break the sender / webhook
        log.exception("alert %s failed", type_)
        return 0


async def digest_tick(db: Session) -> int:
    """Daily 08:00 (workspace time) summary of yesterday. Idempotent per local date via Redis."""
    from . import outreach as og  # local import: outreach imports this module

    n = 0
    for p in db.query(OutreachProfile).filter(OutreachProfile.notify_digest.is_(True), OutreachProfile.notify_email.is_(True)).all():
        t = db.get(Tenant, p.tenant_id)
        if not t or t.status != "active":
            continue
        now = og.utcnow().astimezone(og.tz_of(p))
        if now.hour < 8:
            continue
        key = f"og:digest:{p.tenant_id}:{now.date()}"
        if not rds.set(key, "1", nx=True, ex=172800):
            continue
        end = og.local_day_start(p)
        start = end - timedelta(days=1)
        q = lambda direction: db.query(Message.id).filter(Message.tenant_id == p.tenant_id, Message.direction == direction, Message.created_at >= start, Message.created_at < end).count()  # noqa: E731
        pending = db.query(Draft.id).filter(Draft.tenant_id == p.tenant_id, Draft.status == "pending").count()
        human = db.query(Draft.id).filter(Draft.tenant_id == p.tenant_id, Draft.status == "pending", Draft.needs_human.is_(True)).count()
        lines = [f"Yesterday: {q('out')} message(s) sent, {q('in')} repl{'y' if q('in') == 1 else 'ies'} received.",
                 f"Waiting for you: {pending} draft(s) to approve" + (f", {human} need a human answer." if human else ".")]
        n += await send_alert(db, p.tenant_id, "digest", "Your daily outreach summary", lines, throttle=False)
    return n
