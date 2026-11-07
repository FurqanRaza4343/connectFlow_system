"""Outreach loops (run inside the `worker` service next to the webhook delivery loop):
  scheduler  every 30 s : due batches (auto_run) + follow-ups
  sender     every 2 s  : one approved draft per workspace, paced by a random gap (Redis lock, safe with several workers)
  enrichment every 3 s  : "Improve data" jobs queued in Redis
  digest     every 5 min: daily summary e-mail at 08:00 workspace time (opt-in)
State lives in Postgres/Redis, so a restart loses nothing ('sending' drafts become 'failed' for a human to check)."""
import asyncio
import logging
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import outreach as og
from .db import SessionLocal
from .deps import rds
from .enrich import enrich_lead
from .models import Draft, Lead

log = logging.getLogger("outreach")
SEND_CONCURRENCY = 8  # workspaces sent to at the same time per worker (each uses its own DB session)


async def _send_one(db: Session, tid: str) -> None:
    """One paced send for one workspace. Raises on unexpected errors; the caller isolates them per workspace."""
    p = og.profile_of(db, tid)
    if p.dry_run:
        return  # dry-run: nothing leaves, even if something was approved earlier
    key = f"og:gap:{tid}"
    if not rds.set(key, "1", nx=True, ex=600):  # lock + gap in one key: busy or still waiting for the human-like pause
        return
    d = og.claim_next(db, tid)
    if not d:
        rds.delete(key)
        return
    try:
        result = await og.send_draft(db, d)
    except Exception:  # noqa: BLE001
        rds.delete(key)
        raise
    if result == "sent":
        rds.incr(f"og:done:{tid}")
        gap = random.uniform(p.delay_min, max(p.delay_min, p.delay_max))
        rds.set(key, "1", ex=max(1, int(gap)))
        log.info("sent draft %s (%s) -> next in %ss", d.id[:8], d.kind, int(gap))
    else:
        rds.delete(key)
        log.info("draft %s -> %s: %s", d.id[:8], result, d.note)


_inflight: dict[str, "asyncio.Task[None]"] = {}


async def _send_isolated(tid: str) -> None:
    """One workspace's send in its own task and DB session: a slow media upload of one workspace never delays the others, and a failure stays local."""
    try:
        with SessionLocal() as db:
            await _send_one(db, tid)
    except Exception:  # noqa: BLE001
        log.exception("sender failed for workspace %s", tid[:8])
    finally:
        _inflight.pop(tid, None)


async def sender_loop() -> None:
    while True:
        try:
            with SessionLocal() as db:
                tids = [t for (t,) in db.execute(select(Draft.tenant_id).where(Draft.status == "approved", Draft.not_before <= og.utcnow()).distinct()).all()]
            for tid in tids:
                if tid in _inflight or len(_inflight) >= SEND_CONCURRENCY:
                    continue  # that workspace is still sending (e.g. a slow media upload), or the worker is at capacity: try again next round
                _inflight[tid] = asyncio.create_task(_send_isolated(tid))
        except Exception:  # noqa: BLE001
            log.exception("sender loop error")
        await asyncio.sleep(2)


async def scheduler_loop() -> None:
    while True:
        try:
            with SessionLocal() as db:
                n = await og.scheduler_tick(db)
                if n:
                    log.info("scheduler started %s batch(es)", n)
        except Exception:  # noqa: BLE001
            log.exception("scheduler loop error")
        await asyncio.sleep(30)


async def digest_loop() -> None:
    """Optional daily summary e-mail (08:00 workspace time, once per local day thanks to a Redis key)."""
    from .alerts import digest_tick
    while True:
        try:
            with SessionLocal() as db:
                n = await digest_tick(db)
                if n:
                    log.info("sent %s digest e-mail(s)", n)
        except Exception:  # noqa: BLE001
            log.exception("digest loop error")
        await asyncio.sleep(300)


async def enrich_loop() -> None:
    while True:
        try:
            for key in rds.scan_iter("og:enrichq:*"):
                tid = key.split(":", 2)[2]
                lead_id = rds.lpop(key)
                if not lead_id:
                    continue
                with SessionLocal() as db:
                    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.tenant_id == tid).first()
                    if lead:
                        try:
                            await enrich_lead(db, lead, og.profile_of(db, tid))
                        except Exception:  # noqa: BLE001
                            db.rollback()
                            log.exception("enrich failed for %s", lead_id)
                rds.hincrby(f"og:enrich:{tid}", "done", 1)
        except Exception:  # noqa: BLE001
            log.exception("enrich loop error")
        await asyncio.sleep(3)


async def run() -> None:
    for _ in range(30):  # the api container applies migrations at start-up; wait for the tables
        try:
            with SessionLocal() as db:
                n = og.recover_interrupted(db)
            if n:
                log.warning("%s draft(s) were mid-send at shutdown -> marked failed for review", n)
            break
        except Exception:  # noqa: BLE001
            await asyncio.sleep(2)
    log.info("outreach loops started")
    await asyncio.gather(sender_loop(), scheduler_loop(), enrich_loop(), digest_loop())
