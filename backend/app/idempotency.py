"""Idempotency-Key support for POST /v1/messages (so a client may safely retry after a timeout without sending the message twice).

Rules (per workspace, 24 hours):
  * first request with a key   -> runs; its result is stored under the key
  * same key + same body       -> the stored result is replayed with `Idempotent-Replayed: true`; no second send, no quota use
  * same key + different body  -> 422
  * same key while the first is still running -> 409 (Retry-After: 2)
  * a request that fails (any error) releases the key, so the client may retry it
If Redis is unavailable the header is ignored (fail open): the message still goes out exactly as before.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import redis
from fastapi import HTTPException

log = logging.getLogger("idempotency")
KEY_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
TTL_DONE = 24 * 3600
TTL_INFLIGHT = 120


@dataclass
class Begin:
    replay: bool = False
    status: int = 202
    body: dict[str, Any] | None = None
    tracked: bool = True  # False when Redis was unavailable: nothing to finish/abort


def validate_key(key: str | None) -> str | None:
    if key is None:
        return None
    key = key.strip()
    if not KEY_RE.fullmatch(key):
        raise HTTPException(422, "Idempotency-Key must be 1-64 characters: letters, digits and _ . : -")
    return key


def body_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _rk(tenant_id: str, key: str) -> str:
    return f"idem:{tenant_id}:{key}"


def begin(r: Any, tenant_id: str, key: str, h: str) -> Begin:
    rk = _rk(tenant_id, key)
    try:
        for _ in range(2):
            if r.set(rk, json.dumps({"h": h, "s": "inflight"}), nx=True, ex=TTL_INFLIGHT):
                return Begin()
            raw = r.get(rk)
            if raw is None:  # expired between our SET and GET: try to claim it again
                continue
            rec = json.loads(raw)
            if rec.get("h") != h:
                raise HTTPException(422, "This Idempotency-Key was already used with a different request body.")
            if rec.get("s") == "inflight":
                raise HTTPException(409, "A request with this Idempotency-Key is still being processed. Retry in a moment.", headers={"Retry-After": "2"})
            return Begin(replay=True, status=int(rec.get("status", 202)), body=rec.get("body"))
    except redis.RedisError:
        log.warning("redis unavailable: Idempotency-Key ignored")
        return Begin(tracked=False)
    raise HTTPException(409, "A request with this Idempotency-Key is still being processed. Retry in a moment.", headers={"Retry-After": "2"})


def finish(r: Any, tenant_id: str, key: str, h: str, status: int, body: dict[str, Any]) -> None:
    try:
        r.set(_rk(tenant_id, key), json.dumps({"h": h, "s": "done", "status": status, "body": body}), ex=TTL_DONE)
    except redis.RedisError:
        log.warning("could not store the idempotent result")


def abort(r: Any, tenant_id: str, key: str) -> None:
    try:
        r.delete(_rk(tenant_id, key))
    except redis.RedisError:
        pass
