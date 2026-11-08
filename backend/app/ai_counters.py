"""Redis key names and readers for the platform-wide AI counters, in ONE place: app/ai.py writes them, routers/admin_ops.py reads them
(they used to disagree, so the admin page always showed 0 calls)."""
from datetime import datetime, timezone
from typing import Any


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def calls_key(day: str) -> str:
    return f"ai:day:{day}"


def breaker_key(day: str) -> str:
    return f"ai:breaker:{day}"


def retries_key(day: str) -> str:
    return f"ai:retries:{day}"


def tokens_key(day: str) -> str:
    return f"ai:tokens:{day}"          # hash: in / out / cached


def cost_key(day: str) -> str:
    return f"ai:cost:{day}"            # integer micro-USD


def read_today(r: Any, day: str | None = None) -> dict[str, Any]:
    """What the admin capacity page shows. Every field degrades to 0/False when Redis has no data."""
    d = day or today()
    tok = r.hgetall(tokens_key(d)) or {}
    return {
        "callsToday": int(r.get(calls_key(d)) or 0),
        "retriesToday": int(r.get(retries_key(d)) or 0),
        "tokensToday": int(tok.get("in", 0) or 0) + int(tok.get("out", 0) or 0),
        "costTodayUsd": round(int(r.get(cost_key(d)) or 0) / 1_000_000, 6),
        "breakerOpen": bool(r.exists(breaker_key(d))),
    }


def cost_micro_usd(tokens_in: int, tokens_out: int, tokens_cached: int, price_in: float, price_out: float, price_cached: float) -> int:
    """Estimated cost in micro-USD from per-million-token prices (0 prices => 0 = 'unknown'). `tokens_in` includes the cached part:
    cached tokens are billed at the (cheaper) cached price, the remainder at the normal input price."""
    cached = min(max(0, tokens_cached), max(0, tokens_in))  # cached tokens are part of the input: never more than it
    fresh = max(0, tokens_in) - cached
    return int(round(fresh * price_in + cached * price_cached + max(0, tokens_out) * price_out))  # (tokens * USD/M) = micro-USD
