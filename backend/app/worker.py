"""Webhook delivery worker:  python -m app.worker

Claims due deliveries (visibility timeout, so several workers never send the same one twice), POSTs them signed,
and retries failures with exponential backoff. Nothing is lost on restart: state lives in Postgres.
"""
import asyncio
import hashlib
import hmac
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

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
        ts = str(int(time.time()))
        headers = {
            "Content-Type": "application/json", "User-Agent": "ConnectFlow-Webhooks/1.0",
            "X-ConnectFlow-Event": d.event, "X-ConnectFlow-Delivery": d.id, "X-ConnectFlow-Timestamp": ts,
            "X-ConnectFlow-Signature": sign(ep.secret, ts, d.payload),
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


async def webhook_loop() -> None:
    log.info("webhook worker started")
    while True:
        try:
            ids = await asyncio.to_thread(claim)
            if ids:
                await asyncio.gather(*(deliver(i) for i in ids))
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


async def main() -> None:
    from .outreach_worker import run as outreach_run  # imported here: the API image does not need it at import time
    from .reaper import reaper_loop
    from .retention import retention_loop

    await asyncio.gather(webhook_loop(), outreach_run(), reaper_loop(), retention_loop(), heartbeat_loop())


if __name__ == "__main__":
    asyncio.run(main())
