"""Webhook delivery worker:  python -m app.worker

Claims due deliveries (visibility timeout, so several workers never send the same one twice), POSTs them signed,
and retries failures with exponential backoff. Nothing is lost on restart: state lives in Postgres.
"""
import asyncio
import hashlib
import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from . import crypto
from .db import SessionLocal
from .models import WebhookDelivery, WebhookEndpoint
from .netguard import UnsafeUrl, assert_public_url
from .ops_logging import setup_logging

log = logging.getLogger("worker")
setup_logging()

BACKOFF = [30, 120, 600, 3600, 21600]  # seconds before attempt 2..6
MAX_ATTEMPTS = len(BACKOFF) + 1
CLAIM_SECONDS = 90


def sign(secret: str, timestamp: str, body: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()


def claim(limit: int = 20) -> list[str]:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        rows = db.execute(
            select(WebhookDelivery).where(WebhookDelivery.status.in_(("pending", "retrying")), WebhookDelivery.next_attempt_at <= now)
            .order_by(WebhookDelivery.next_attempt_at).limit(limit).with_for_update(skip_locked=True)
        ).scalars().all()
        for d in rows:
            d.next_attempt_at = now + timedelta(seconds=CLAIM_SECONDS)  # if we crash, it becomes due again
        db.commit()
        return [d.id for d in rows]


async def deliver(delivery_id: str) -> None:
    with SessionLocal() as db:
        d = db.get(WebhookDelivery, delivery_id)
        if not d or d.status not in ("pending", "retrying"):
            return
        ep = db.get(WebhookEndpoint, d.endpoint_id)
        now = datetime.now(timezone.utc)
        if not ep or not ep.active:
            d.status, d.last_error = "failed", "endpoint disabled or deleted"
            db.commit()
            return
        try:
            secret = crypto.reveal(ep.secret)
        except crypto.DecryptError:
            d.status, d.last_error = "failed", "signing secret cannot be decrypted (check ENCRYPTION_KEY); rotate the endpoint secret"
            db.commit()
            log.error("delivery %s: endpoint %s secret cannot be decrypted", d.id[:8], ep.id[:8])
            return
        upgraded = crypto.upgrade_if_plain(ep.secret)  # legacy plaintext row: encrypt it now (the start-up backfill normally got it already)
        if upgraded:
            ep.secret = upgraded
        ts = str(int(time.time()))
        headers = {
            "Content-Type": "application/json", "User-Agent": "ConnectFlow-Webhooks/1.0",
            "X-ConnectFlow-Event": d.event, "X-ConnectFlow-Delivery": d.id, "X-ConnectFlow-Timestamp": ts,
            "X-ConnectFlow-Signature": sign(secret, ts, d.payload),
        }
        status_code, error = None, ""
        try:
            await assert_public_url(ep.url)
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as c:
                r = await c.post(ep.url, content=d.payload, headers=headers)
            status_code = r.status_code
            if not 200 <= r.status_code < 300:
                error = f"HTTP {r.status_code}"
        except UnsafeUrl as e:
            error = f"blocked: {e}"
        except httpx.HTTPError as e:
            error = f"{e.__class__.__name__}: {str(e)[:200]}"

        d.attempts += 1
        d.last_status, d.last_error = status_code, error[:300]
        if not error:
            d.status, d.delivered_at = "success", now
        elif d.attempts >= MAX_ATTEMPTS or error.startswith("blocked"):
            d.status = "failed"
        else:
            d.status = "retrying"
            d.next_attempt_at = now + timedelta(seconds=BACKOFF[d.attempts - 1])
        db.commit()
        log.info("delivery %s %s attempt %s -> %s %s", d.id[:8], d.event, d.attempts, d.status, error or status_code)


def _record_crash(delivery_id: str) -> None:
    """A delivery that crashed the handler (malformed payload, bad row ...) must not be retried forever: count it as an attempt."""
    try:
        with SessionLocal() as db:
            d = db.get(WebhookDelivery, delivery_id)
            if not d or d.status not in ("pending", "retrying"):
                return
            d.attempts += 1
            d.last_error = "internal error while delivering"
            if d.attempts >= MAX_ATTEMPTS:
                d.status = "failed"
            else:
                d.status = "retrying"
                d.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=BACKOFF[d.attempts - 1])
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not record the crash of delivery %s", delivery_id[:8])


async def safe_deliver(delivery_id: str) -> None:
    """One bad delivery can never take down the loop or its siblings."""
    try:
        await deliver(delivery_id)
    except Exception:  # noqa: BLE001
        log.exception("delivery %s crashed", delivery_id[:8])
        await asyncio.to_thread(_record_crash, delivery_id)


async def webhook_loop() -> None:
    log.info("webhook worker started")
    while True:
        try:
            ids = await asyncio.to_thread(claim)
            if ids:
                await asyncio.gather(*(safe_deliver(i) for i in ids))
                continue
        except Exception:  # noqa: BLE001  (never die: log and keep polling)
            log.exception("worker loop error")
        await asyncio.sleep(2)


async def heartbeat_loop() -> None:
    """Liveness signal for the docker healthcheck and the admin System page: Redis key ops:worker:heartbeat (unix seconds)."""
    import redis as _redis
    from .config_ops import ops

    r = _redis.Redis.from_url(ops.redis_url, decode_responses=True)
    while True:
        try:
            r.set("ops:worker:heartbeat", str(int(time.time())), ex=300)
        except Exception:  # noqa: BLE001
            log.warning("heartbeat failed (redis unreachable?)")
        await asyncio.sleep(15)


async def billing_loop() -> None:
    """Hourly: past-due subscriptions whose 7-day grace has ended fall back to the Trial plan."""
    from . import stripe_billing

    while True:
        try:
            with SessionLocal() as db:
                n = stripe_billing.expire_grace(db)
            if n:
                log.info("billing: %d workspace(s) moved to Trial after the grace period", n)
        except Exception:  # noqa: BLE001
            log.exception("billing loop failed")
        await asyncio.sleep(3600)


async def _supervise(name: str, fn: Callable[[], Awaitable[None]]) -> None:
    """Restart a background loop if it ever crashes out (each loop already guards its own iterations)."""
    while True:
        try:
            await fn()
        except Exception:  # noqa: BLE001
            log.exception("%s loop crashed, restarting in 5 s", name)
            await asyncio.sleep(5)


async def main() -> None:
    from .outreach_worker import run as outreach_run  # imported here: the API image does not need it at import time
    from .reaper import reaper_loop
    from .retention import retention_loop

    loops = [("webhooks", webhook_loop), ("outreach", outreach_run), ("reaper", reaper_loop), ("retention", retention_loop), ("heartbeat", heartbeat_loop), ("billing", billing_loop)]
    await asyncio.gather(*(_supervise(name, fn) for name, fn in loops))


if __name__ == "__main__":
    asyncio.run(main())
