"""Redis daily counters that replace the per-send COUNT(*) queries on the hot path.

The database stays the audit source of truth. A counter is only an accelerator, so it can never be wrong for long:
  * a missing key (expired, Redis restarted, first message of the day) is rebuilt once from the database (`loader`);
  * an increment on a missing key is skipped, because the next read rebuilds it from the database, which already holds the row;
  * if Redis is unreachable the loader answers directly (slower, still correct).
Per-instance counters are Redis hashes with a sentinel field marking "this hash was initialised"."""
from collections.abc import Callable
from typing import Any

import redis

TTL = 3 * 86400
SENTINEL = "_"
_ERRORS = (redis.RedisError, OSError)


def get_int(r: Any, key: str, loader: Callable[[], int]) -> int:
    """Value of a plain counter; rebuilt from the database when absent."""
    try:
        v = r.get(key)
        if v is None:
            r.set(key, int(loader()), ex=TTL, nx=True)  # nx: a concurrent rebuild/increment wins, never overwritten
            v = r.get(key)
        return int(v or 0)
    except _ERRORS:
        return int(loader())


def incr_int(r: Any, key: str, n: int = 1) -> None:
    """Count one more. Skipped when the key is absent (the next get_int rebuilds it from the database, which already includes this event)."""
    try:
        if r.exists(key):
            r.incrby(key, n)
    except _ERRORS:
        pass


def decr_int(r: Any, key: str, n: int = 1) -> None:
    try:
        if r.exists(key) and r.decrby(key, n) < 0:
            r.set(key, 0, keepttl=True)
    except _ERRORS:
        pass


def hash_counts(r: Any, key: str, loader: Callable[[], dict[str, int]]) -> dict[str, int]:
    """{field: count} of a per-instance daily hash; rebuilt from the database when absent."""
    try:
        data = r.hgetall(key)
        if SENTINEL not in data:
            loaded = loader()
            pipe = r.pipeline()
            for field, n in loaded.items():
                pipe.hsetnx(key, field, int(n))
            pipe.hsetnx(key, SENTINEL, 1)
            pipe.expire(key, TTL)
            pipe.execute()
            data = r.hgetall(key)
        return {k: int(v) for k, v in data.items() if k != SENTINEL}
    except _ERRORS:
        return {k: int(v) for k, v in loader().items()}


def hash_incr(r: Any, key: str, field: str, n: int = 1) -> None:
    try:
        if r.hexists(key, SENTINEL):
            r.hincrby(key, field, n)
    except _ERRORS:
        pass
