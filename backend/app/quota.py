"""Plan limits. One place decides whether a tenant may send another message today."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Tenant, UsageEvent


def day_start() -> datetime:
    n = datetime.now(timezone.utc)
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


def messages_today(db: Session, tenant_id: str) -> int:
    return (
        db.query(func.count(UsageEvent.id))
        .filter(UsageEvent.tenant_id == tenant_id, UsageEvent.kind == "message", UsageEvent.at >= day_start())
        .scalar()
        or 0
    )


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
    return ev


def refund(db: Session, ev: UsageEvent) -> None:
    db.delete(ev)
    db.commit()


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


def reserve_ai(db: Session, tenant_id: str) -> None:
    """Count one AI call against the workspace's plan (row lock => parallel calls cannot overshoot). Raises HTTPException(429)."""
    t = db.query(Tenant).filter(Tenant.id == tenant_id).with_for_update(of=Tenant).one()
    used = ai_calls_today(db, tenant_id)
    if used >= t.plan.ai_calls_day:
        db.rollback()
        raise HTTPException(429, f"Daily AI limit reached ({t.plan.ai_calls_day} on the {t.plan.name} plan). It resets at 00:00 UTC.")
    db.add(UsageEvent(tenant_id=tenant_id, kind="ai"))
    db.commit()
