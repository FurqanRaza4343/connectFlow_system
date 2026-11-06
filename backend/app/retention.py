"""Retention: keeps operational tables from growing forever. Never deletes customer data (leads, messages, lead lists).

Runs daily at RETENTION_HOUR_UTC in the worker (and on demand: `python -m app.retention`). Deletes are batched
(RETENTION_BATCH_SIZE) so they never lock a table for long. usage_events are rolled up into per-day counters in Redis
(`usage:rollup:<tenant_id>` hash, field YYYY-MM-DD) BEFORE deletion so billing history survives.
Result of the last run is stored in Redis `ops:retention:last` = {"at": iso, "deleted": {table: n}}.
"""
import asyncio
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import redis
from sqlalchemy import text
from typing import Any

from .config_ops import ops
from .db import SessionLocal

log = logging.getLogger("retention")
LAST_KEY = "ops:retention:last"


def months_ago(now: datetime, months: int) -> datetime:
    """Calendar-agnostic 'N months ago' (30.44-day months are plenty for a retention cut-off)."""
    return now - timedelta(days=round(months * 30.44))


def plan_rules(now: datetime) -> list[dict]:
    """The retention rules as data (pure function => unit-testable). `where` uses :cutoff; every table is operational data."""
    d = lambda days: now - timedelta(days=days)  # noqa: E731
    return [
        {"table": "webhook_deliveries", "where": "status IN ('success','failed') AND created_at < :cutoff", "cutoff": d(ops.retention_webhook_days)},
        {"table": "notifications", "where": '"read" = true AND created_at < :cutoff', "cutoff": d(ops.retention_notification_days)},
        {"table": "audit_log", "where": "at < :cutoff", "cutoff": months_ago(now, ops.retention_audit_months)},
        {"table": "usage_events", "where": "at < :cutoff", "cutoff": months_ago(now, ops.retention_usage_months), "rollup": True, "ts": "at"},
        {"table": "drafts", "where": "status IN ('sent','discarded') AND created_at < :cutoff", "cutoff": d(ops.retention_draft_days)},
        {"table": "batches", "where": "started_at < :cutoff", "cutoff": months_ago(now, ops.retention_batch_months)},
        # shrink big JSON bodies early; the delivery row itself (status, attempts, response) stays until the 30-day rule
        {"table": "webhook_deliveries", "kind": "blank_payload", "where": "status IN ('success','failed') AND payload <> '' AND created_at < :cutoff", "cutoff": d(ops.retention_webhook_payload_days)},
    ]


def rollup_counts(rows: list[tuple[str, datetime]]) -> Counter:
    """(tenant_id, timestamp) rows -> Counter[(tenant_id, 'YYYY-MM-DD')]  (pure)."""
    c: Counter = Counter()
    for tenant_id, at in rows:
        c[(tenant_id, at.astimezone(timezone.utc).strftime("%Y-%m-%d"))] += 1
    return c


def _redis() -> Any:
    return redis.Redis.from_url(ops.redis_url, decode_responses=True)


def run_once(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    deleted: dict[str, int] = {}
    r = _redis()
    batch = max(100, ops.retention_batch_size)
    with SessionLocal() as db:
        for rule in plan_rules(now):
            t, w, params = rule["table"], rule["where"], {"cutoff": rule["cutoff"], "n": batch}
            key = t if rule.get("kind") != "blank_payload" else t + ".payload"
            total = 0
            while True:
                if rule.get("kind") == "blank_payload":
                    res = db.execute(text(f"UPDATE {t} SET payload = '' WHERE id IN (SELECT id FROM {t} WHERE {w} LIMIT :n)"), params)
                elif rule.get("rollup"):
                    rows = db.execute(text(f"SELECT id, tenant_id, {rule['ts']} FROM {t} WHERE {w} ORDER BY id LIMIT :n"), params).all()
                    if not rows:
                        break
                    for (tenant_id, day), n in rollup_counts([(x[1], x[2]) for x in rows]).items():
                        r.hincrby(f"usage:rollup:{tenant_id}", day, n)
                    res = db.execute(text(f"DELETE FROM {t} WHERE id = ANY(:ids)"), {"ids": [x[0] for x in rows]})
                else:
                    res = db.execute(text(f"DELETE FROM {t} WHERE id IN (SELECT id FROM {t} WHERE {w} LIMIT :n)"), params)
                db.commit()
                changed = int(getattr(res, "rowcount", 0) or 0)
                total += changed
                if changed < batch:
                    break
            deleted[key] = total
    result = {"at": now.isoformat(), "deleted": deleted}
    r.set(LAST_KEY, json.dumps(result))
    log.info("retention run: %s", deleted)
    return result


async def retention_loop() -> None:
    if not ops.retention_enabled:
        log.info("retention disabled")
        return
    log.info("retention scheduled daily at %02d:00 UTC", ops.retention_hour_utc)
    last_day = None
    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.hour == ops.retention_hour_utc and last_day != now.date():
                last_day = now.date()
                if _redis().set("ops:retention:lock", "1", nx=True, ex=3600):  # one worker per day
                    await asyncio.to_thread(run_once, now)
        except Exception:  # noqa: BLE001
            log.exception("retention error")
        await asyncio.sleep(300)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_once(), indent=2))
