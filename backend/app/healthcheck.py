"""Docker HEALTHCHECK helpers.  python -m app.healthcheck worker  |  python -m app.healthcheck api"""
import sys
import time
import urllib.request
from typing import Any


def worker_ok(max_age: int = 60) -> bool:
    import redis

    from .config_ops import ops

    r: Any = redis.Redis.from_url(ops.redis_url, decode_responses=True)
    v = r.get("ops:worker:heartbeat")
    return bool(v) and time.time() - int(v) <= max_age


def api_ok() -> bool:
    return urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=4).status == 200


if __name__ == "__main__":
    try:
        sys.exit(0 if (worker_ok() if sys.argv[1:] == ["worker"] else api_ok()) else 1)
    except Exception:  # noqa: BLE001
        sys.exit(1)
