"""Gateway housekeeping, run by the worker.

* reaper     - every minute: delete sessions that were never linked (QR not scanned) after REAP_UNSCANNED_MINUTES, and stop idle
               TRIAL sessions (no message sent for REAP_TRIAL_IDLE_DAYS). A ready session of a paid plan is never touched.
* reconciler - every 5 minutes: gateway sessions named cf-* that have no database row are deleted (orphans hold RAM), and
               database rows whose gateway session vanished are marked disconnected (or removed if they were never linked).

Selection logic is in pure functions (injected clock) so it is unit-tested; the loops only do I/O.
Safety: REAPER_DRY_RUN, a Redis lock (one worker at a time), row locks (SKIP LOCKED), a grace period for brand-new rows and an
orphan must be seen on two consecutive reconcile runs before it is deleted. The legacy gateway is a different service: not touched.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import redis
from sqlalchemy import func

from . import wa_client as wa
from .config_ops import ops
from .db import SessionLocal
from .models import AuditLog, Instance, Tenant, UsageEvent

log = logging.getLogger("reaper")
UNSCANNED = {"created", "initializing", "qr_ready", "authenticating"}
LOCK_KEY = "ops:reaper:lock"
ORPHANS_KEY = "ops:reconcile:orphans"


@dataclass
class Row:
    """What the selection functions need to know about an instance (plain data, no ORM)."""
    id: str
    name: str
    status: str
    phone: str
    created_at: datetime
    plan_id: str = ""
    last_activity: datetime | None = None  # last message sent through it (usage_events), None = never


# ------------------------------------------------------------------ pure selection logic
def select_unscanned(rows: list[Row], now: datetime, minutes: int) -> list[Row]:
    cutoff = now - timedelta(minutes=minutes)
    return [r for r in rows if r.status in UNSCANNED and not r.phone and r.created_at < cutoff]


def select_idle_trial(rows: list[Row], now: datetime, days: int) -> list[Row]:
    """Idle = a *ready* session on the trial plan whose last activity (or creation) is older than `days`."""
    if days <= 0:
        return []
    cutoff = now - timedelta(days=days)
    return [r for r in rows if r.plan_id == "trial" and r.status == "ready" and (r.last_activity or r.created_at) < cutoff]


def split_reconcile(db_rows: list[Row], gw_sessions: list[dict], now: datetime, grace_seconds: int, seen_before: set[str]) -> dict:
    """Compare DB rows with gateway sessions.
    orphans    : gateway cf-* sessions with no DB row AND already seen as orphan on the previous run (-> delete)
    new_orphans: first-time orphans (remember, do not delete yet)
    gone_linked: DB rows whose session vanished but were linked once (-> mark disconnected)
    gone_unlinked: DB rows whose session vanished and were never linked (-> delete row)"""
    db_names = {r.name for r in db_rows}
    gw_names = {str(s.get("name")) for s in gw_sessions}
    cutoff = now - timedelta(seconds=grace_seconds)
    orphan_names = [str(s.get("name")) for s in gw_sessions if str(s.get("name", "")).startswith("cf-") and str(s.get("name")) not in db_names]
    gone = [r for r in db_rows if r.name not in gw_names and r.created_at < cutoff and r.status != "disconnected"]
    return {
        "orphans": [n for n in orphan_names if n in seen_before],
        "new_orphans": [n for n in orphan_names if n not in seen_before],
        "gone_linked": [r for r in gone if r.phone],
        "gone_unlinked": [r for r in gone if not r.phone],
    }


# ------------------------------------------------------------------ I/O
def _redis() -> redis.Redis:
    return redis.Redis.from_url(ops.redis_url, decode_responses=True)


def _load_rows(db) -> list[Row]:
    last = dict(db.query(UsageEvent.instance_id, func.max(UsageEvent.at)).filter(UsageEvent.kind == "message").group_by(UsageEvent.instance_id).all())
    plan = {t.id: t.plan_id for t in db.query(Tenant).all()}
    return [Row(i.id, i.openwa_name, i.status, i.phone or "", i.created_at, plan.get(i.tenant_id, ""), last.get(i.id)) for i in db.query(Instance).all()]


def _audit(db, inst: Instance, action: str, detail: str) -> None:
    db.add(AuditLog(tenant_id=inst.tenant_id, user_id=None, action=action, detail=detail[:300]))


async def reap_once(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = {"unscanned": 0, "idle_trial": 0, "dry_run": ops.reaper_dry_run}
    with SessionLocal() as db:
        rows = _load_rows(db)
        victims = select_unscanned(rows, now, ops.reap_unscanned_minutes)
        idle = select_idle_trial(rows, now, ops.reap_trial_idle_days)
        for r in victims:
            # lock the row so a second worker (or the user finishing the scan) cannot race us
            inst = db.query(Instance).filter(Instance.id == r.id).with_for_update(skip_locked=True).first()
            if not inst or inst.status not in UNSCANNED or inst.phone:
                continue
            log.info("reap unscanned session %s (age %s)%s", inst.openwa_name, now - inst.created_at, " [dry-run]" if ops.reaper_dry_run else "")
            if ops.reaper_dry_run:
                continue
            try:
                if inst.openwa_id:
                    await wa.delete_session(inst.openwa_id)
            except wa.WAError as e:
                if e.status != 404:
                    log.warning("gateway delete failed for %s: %s", inst.openwa_name, e)
                    continue
            _audit(db, inst, "instance.reaped", f"never linked after {ops.reap_unscanned_minutes} min: {inst.label}")
            db.delete(inst)
            db.commit()
            out["unscanned"] += 1
        for r in idle:
            inst = db.query(Instance).filter(Instance.id == r.id).with_for_update(skip_locked=True).first()
            if not inst or inst.status != "ready":
                continue
            log.info("stop idle trial session %s%s", inst.openwa_name, " [dry-run]" if ops.reaper_dry_run else "")
            if ops.reaper_dry_run:
                continue
            try:
                await wa.stop_session(inst.openwa_id)
            except wa.WAError as e:
                log.warning("gateway stop failed for %s: %s", inst.openwa_name, e)
                continue
            inst.status = "disconnected"
            _audit(db, inst, "instance.idle_stopped", f"trial session idle {ops.reap_trial_idle_days}+ days (press Restart to reconnect): {inst.label}")
            db.commit()
            out["idle_trial"] += 1
    return out


async def reconcile_once(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = {"orphans_deleted": 0, "orphans_new": 0, "rows_disconnected": 0, "rows_removed": 0, "skipped": ""}
    try:
        sessions = await wa.list_sessions()
    except wa.WAError as e:
        out["skipped"] = f"gateway unreachable: {e}"
        return out  # never reconcile against an empty/unknown view
    r = _redis()
    seen = set(r.smembers(ORPHANS_KEY))
    with SessionLocal() as db:
        res = split_reconcile(_load_rows(db), sessions, now, ops.reconcile_grace_seconds, seen)
        by_name = {str(s.get("name")): s for s in sessions}
        for name in res["orphans"]:
            sid = by_name[name].get("id")
            log.warning("orphan gateway session %s (no database row)%s", name, " [dry-run]" if ops.reaper_dry_run else "")
            if ops.reaper_dry_run or not sid:
                continue
            try:
                await wa.delete_session(str(sid))
                out["orphans_deleted"] += 1
            except wa.WAError as e:
                log.warning("could not delete orphan %s: %s", name, e)
        out["orphans_new"] = len(res["new_orphans"])
        for row in res["gone_linked"]:
            inst = db.query(Instance).filter(Instance.id == row.id).with_for_update(skip_locked=True).first()
            if inst and not ops.reaper_dry_run:
                inst.status = "disconnected"
                _audit(db, inst, "instance.reconciled", "gateway session missing: marked disconnected")
                db.commit()
                out["rows_disconnected"] += 1
        for row in res["gone_unlinked"]:
            inst = db.query(Instance).filter(Instance.id == row.id).with_for_update(skip_locked=True).first()
            if inst and not ops.reaper_dry_run:
                _audit(db, inst, "instance.reconciled", "gateway session missing and never linked: row removed")
                db.delete(inst)
                db.commit()
                out["rows_removed"] += 1
    pipe = r.pipeline()
    pipe.delete(ORPHANS_KEY)
    for n in res["new_orphans"]:
        pipe.sadd(ORPHANS_KEY, n)
    pipe.expire(ORPHANS_KEY, ops.reconcile_interval_seconds * 3)
    pipe.execute()
    return out


async def _guarded(name: str, ttl: int, fn) -> None:
    """Run fn() only if we obtain the Redis lock (so two workers never act at once)."""
    r = _redis()
    if not r.set(f"{LOCK_KEY}:{name}", "1", nx=True, ex=ttl):
        return
    try:
        res = await fn()
        if any(v for k, v in res.items() if k not in ("dry_run", "skipped")) or res.get("skipped"):
            log.info("%s: %s", name, res)
    finally:
        r.delete(f"{LOCK_KEY}:{name}")


async def reaper_loop() -> None:
    if not ops.reaper_enabled:
        log.info("reaper disabled")
        return
    log.info("reaper started (unscanned=%s min, trial idle=%s d, dry_run=%s)", ops.reap_unscanned_minutes, ops.reap_trial_idle_days, ops.reaper_dry_run)
    last_reconcile = 0.0
    loop = asyncio.get_event_loop()
    while True:
        try:
            await _guarded("reap", 120, reap_once)
            if loop.time() - last_reconcile >= ops.reconcile_interval_seconds:
                last_reconcile = loop.time()
                await _guarded("reconcile", 240, reconcile_once)
        except Exception:  # noqa: BLE001  (a housekeeping bug must never kill the worker)
            log.exception("reaper loop error")
        await asyncio.sleep(ops.reaper_interval_seconds)
