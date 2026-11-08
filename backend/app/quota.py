"""Plan limits. One place decides whether a tenant may send another message today.

The per-day message count is a Redis counter (app/counters.py) rebuilt from `usage_events` when missing; the table stays the audit trail
and the source of truth, and the tenant row lock still serialises parallel reservations so the limit can never be overshot."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import counters
from .deps import rds
from .models import AiUsageDaily, Tenant, UsageEvent


def day_start() -> datetime:
    n = datetime.now(timezone.utc)
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


def _key(tenant_id: str, at: datetime | None = None) -> str:
    return f"q:msg:{tenant_id}:{(at or day_start()).astimezone(timezone.utc):%Y%m%d}"


def _db_messages_today(db: Session, tenant_id: str) -> int:
    return (
        db.query(func.count(UsageEvent.id))
        .filter(UsageEvent.tenant_id == tenant_id, UsageEvent.kind == "message", UsageEvent.at >= day_start())
        .scalar()
        or 0
    )


def messages_today(db: Session, tenant_id: str) -> int:
    return counters.get_int(rds, _key(tenant_id), lambda: _db_messages_today(db, tenant_id))


def reserve_message(db: Session, tenant_id: str, instance_id: str | None, api_key_id: str | None) -> UsageEvent:
    """Count the message against today's limit atomically (row lock on the tenant, so parallel requests cannot both pass)."""
    t = db.query(Tenant).filter(Tenant.id == tenant_id).with_for_update(of=Tenant).one()
    if t.status != "active":
        raise HTTPException(403, "account suspended")
    if messages_today(db, tenant_id) >= t.plan.max_msgs_day:
        db.rollback()
        raise HTTPException(429, f"Daily message limit reached ({t.plan.max_msgs_day} on the {t.plan.name} plan). Resets at 00:00 UTC.")
    ev = UsageEvent(tenant_id=tenant_id, instance_id=instance_id, api_key_id=api_key_id, kind="message")
    db.add(ev)
    db.commit()
    counters.incr_int(rds, _key(tenant_id, ev.at))
    return ev


def refund(db: Session, ev: UsageEvent) -> None:
    tenant_id, at = ev.tenant_id, ev.at
    db.delete(ev)
    db.commit()
    counters.decr_int(rds, _key(tenant_id, at))


def last_days(db: Session, tenant_id: str, days: int = 7) -> list[dict]:
    start = day_start() - timedelta(days=days - 1)
    rows = (
        db.query(func.date(UsageEvent.at), func.count(UsageEvent.id))
        .filter(UsageEvent.tenant_id == tenant_id, UsageEvent.kind == "message", UsageEvent.at >= start)
        .group_by(func.date(UsageEvent.at))
        .all()
    )
    counts = {str(d): c for d, c in rows}
    out = []
    for i in range(days):
        d = str((start + timedelta(days=i)).date())
        out.append({"day": d, "count": counts.get(d, 0)})
    return out


def ai_calls_today(db: Session, tenant_id: str) -> int:
    return db.query(func.count(UsageEvent.id)).filter(UsageEvent.tenant_id == tenant_id, UsageEvent.kind == "ai", UsageEvent.at >= day_start()).scalar() or 0


def ai_tokens_today(db: Session, tenant_id: str) -> int:
    """Provider tokens (prompt + completion) this workspace used today (UTC); 0 until the first real AI call."""
    row = db.query(AiUsageDaily.tokens_in, AiUsageDaily.tokens_out).filter(AiUsageDaily.tenant_id == tenant_id, AiUsageDaily.day == datetime.now(timezone.utc).date()).first()
    return int(row[0] + row[1]) if row else 0


def ai_cost_usd(db: Session, tenant_ids: list[str], month: bool = False) -> dict[str, float]:
    """tenant_id -> estimated cost in USD for today (or the current UTC month). 0 when no price is configured (AI_PRICE_*)."""
    now = datetime.now(timezone.utc)
    q = db.query(AiUsageDaily.tenant_id, func.coalesce(func.sum(AiUsageDaily.est_cost_micro_usd), 0)).filter(AiUsageDaily.tenant_id.in_(tenant_ids))
    q = q.filter(AiUsageDaily.day >= now.date().replace(day=1)) if month else q.filter(AiUsageDaily.day == now.date())
    return {t: round(int(v) / 1_000_000, 6) for t, v in q.group_by(AiUsageDaily.tenant_id).all()}


def ai_tokens_by_tenant(db: Session, tenant_ids: list[str]) -> dict[str, int]:
    rows = db.query(AiUsageDaily.tenant_id, AiUsageDaily.tokens_in + AiUsageDaily.tokens_out).filter(AiUsageDaily.tenant_id.in_(tenant_ids), AiUsageDaily.day == datetime.now(timezone.utc).date()).all()
    return {t: int(n) for t, n in rows}


def reserve_ai(db: Session, tenant_id: str) -> None:
    """Count one AI call against the workspace's plan (row lock => parallel calls cannot overshoot). Raises HTTPException(429)."""
    t = db.query(Tenant).filter(Tenant.id == tenant_id).with_for_update(of=Tenant).one()
    used = ai_calls_today(db, tenant_id)
    if used >= t.plan.ai_calls_day:
        db.rollback()
        raise HTTPException(429, f"Daily AI limit reached ({t.plan.ai_calls_day} on the {t.plan.name} plan). It resets at 00:00 UTC.")
    db.add(UsageEvent(tenant_id=tenant_id, kind="ai"))
    db.commit()
