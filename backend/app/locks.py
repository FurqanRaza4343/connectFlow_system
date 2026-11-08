"""Locks that make several API/worker replicas safe.

* `leader(name, ttl)`: a Redis lock (SET NX PX with a random token, compare-and-delete release, automatic renewal while held). Used so loops
  that must not run twice at the same moment (scheduler tick, digest, billing grace, assets sweep) run on ONE replica at a time:

      async with leader("scheduler", 600) as is_leader:
          if is_leader:
              ...do the work...

  Redis unreachable => the lock is NOT granted (a skipped tick is harmless; a duplicated one is not).
* `advisory(db, key)`: a Postgres advisory lock (transaction-scoped or session-scoped) used to serialise start-up work (migrations, seeding).
"""
import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .deps import rds

log = logging.getLogger("locks")

PREFIX = "lock:"
# compare-and-delete / compare-and-extend: only the holder (matching token) can release or renew
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"
_RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('pexpire', KEYS[1], ARGV[2]) else return 0 end"


def try_lock(name: str, ttl_s: float, client: Any = None) -> str | None:
    """Take the lock `name` for `ttl_s` seconds. Returns the owner token, or None when somebody else holds it (or Redis is down)."""
    r = client or rds
    token = secrets.token_hex(12)
    try:
        return token if r.set(PREFIX + name, token, nx=True, px=max(1, int(ttl_s * 1000))) else None
    except Exception:  # noqa: BLE001
        log.warning("lock %s: redis unreachable, not taking the lock", name)
        return None


def release(name: str, token: str, client: Any = None) -> bool:
    """Release only if we still own it (an expired lock that somebody else took is never deleted)."""
    r = client or rds
    try:
        return bool(r.eval(_RELEASE, 1, PREFIX + name, token))
    except Exception:  # noqa: BLE001
        return False


def renew(name: str, token: str, ttl_s: float, client: Any = None) -> bool:
    r = client or rds
    try:
        return bool(r.eval(_RENEW, 1, PREFIX + name, token, max(1, int(ttl_s * 1000))))
    except Exception:  # noqa: BLE001
        return False


async def _keep_alive(name: str, token: str, ttl_s: float, client: Any) -> None:
    """Extend the TTL every third of the TTL while the holder is still working (a long batch must not lose its lock)."""
    while True:
        await asyncio.sleep(max(0.05, ttl_s / 3))
        if not await asyncio.to_thread(renew, name, token, ttl_s, client):
            log.warning("lock %s was lost while held", name)
            return


@asynccontextmanager
async def leader(name: str, ttl_s: float, client: Any = None) -> AsyncIterator[bool]:
    """Yields True when this replica holds the lock (and keeps it alive), False when another replica does."""
    token = await asyncio.to_thread(try_lock, name, ttl_s, client)
    if token is None:
        yield False
        return
    keeper = asyncio.create_task(_keep_alive(name, token, ttl_s, client))
    try:
        yield True
    finally:
        keeper.cancel()
        await asyncio.to_thread(release, name, token, client)


# ---------------------------------------------------------------- Postgres advisory locks
ADV_STARTUP = 7002  # (7001 is the instance-capacity guard in routers/instances.py)


def advisory_lock(conn: Connection, key: int) -> None:
    """Block until the session-level advisory lock `key` is ours. Keep `conn` open for as long as the work lasts, then call advisory_unlock."""
    conn.execute(text("select pg_advisory_lock(:k)"), {"k": key})


def advisory_unlock(conn: Connection, key: int) -> None:
    conn.execute(text("select pg_advisory_unlock(:k)"), {"k": key})
