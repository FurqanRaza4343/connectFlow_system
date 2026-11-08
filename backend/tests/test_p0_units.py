"""P0 scale-readiness units: AI token/cost accounting, knowledge clipping, the media byte cache, the leader lock helper
(with an in-memory fake Redis), the AI counter keys and the production guard for AI_GLOBAL_DAILY_CAP. No database, Redis or network."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app import ai, ai_counters, bytecache, locks, outreach, retention, worker
from app.config import Settings

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- cost math
def test_cost_micro_usd_splits_cached_and_fresh_input():
    # 1000 prompt tokens of which 400 were cache hits, 500 completion tokens; USD per million tokens: in 1.0, out 2.0, cached 0.1
    assert ai_counters.cost_micro_usd(1000, 500, 400, 1.0, 2.0, 0.1) == 600 * 1 + 400 * 0.1 + 500 * 2 == 1640


def test_cost_is_zero_without_prices_and_never_negative():
    assert ai_counters.cost_micro_usd(1000, 500, 400, 0.0, 0.0, 0.0) == 0
    assert ai_counters.cost_micro_usd(100, 0, 500, 1.0, 1.0, 0.5) == 50      # cached larger than input: fresh part clamps to 0


def test_counter_keys_are_per_utc_day():
    assert ai_counters.calls_key("20261108") == "ai:day:20261108"
    assert ai_counters.breaker_key("20261108") == "ai:breaker:20261108"
    assert len(ai_counters.today()) == 8


class _Redis:
    """Just enough of redis-py for ai_counters.read_today."""

    def __init__(self, kv: dict[str, Any] | None = None, hashes: dict[str, dict[str, str]] | None = None) -> None:
        self.kv, self.hashes = kv or {}, hashes or {}

    def get(self, k: str) -> Any:
        return self.kv.get(k)

    def hgetall(self, k: str) -> dict[str, str]:
        return self.hashes.get(k, {})

    def exists(self, k: str) -> int:
        return 1 if k in self.kv else 0


def test_read_today_reports_what_ai_py_writes():
    d = "20261108"
    r = _Redis({ai_counters.calls_key(d): "7", ai_counters.retries_key(d): "2", ai_counters.cost_key(d): "1500000"}, {ai_counters.tokens_key(d): {"in": "4000", "out": "300", "cached": "100"}})
    out = ai_counters.read_today(r, d)
    assert out == {"callsToday": 7, "retriesToday": 2, "tokensToday": 4300, "costTodayUsd": 1.5, "breakerOpen": False}
    r.kv[ai_counters.breaker_key(d)] = "1"
    assert ai_counters.read_today(r, d)["breakerOpen"] is True


def test_read_today_empty_redis_is_all_zero():
    assert ai_counters.read_today(_Redis(), "20261108") == {"callsToday": 0, "retriesToday": 0, "tokensToday": 0, "costTodayUsd": 0.0, "breakerOpen": False}


# ---------------------------------------------------------------- provider usage parsing
def test_parse_usage_deepseek_cache_fields():
    body = {"usage": {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200}}
    assert ai.parse_usage(body) == (1000, 50, 800)


def test_parse_usage_openai_style_details():
    assert ai.parse_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 64}}}) == (100, 5, 64)


def test_parse_usage_without_prompt_tokens_adds_hit_and_miss():
    assert ai.parse_usage({"usage": {"completion_tokens": 3, "prompt_cache_hit_tokens": 10, "prompt_cache_miss_tokens": 5}}) == (15, 3, 10)


@pytest.mark.parametrize("body", [{}, {"usage": None}, {"usage": "x"}, {"usage": {"prompt_tokens": -5, "completion_tokens": "7", "prompt_cache_hit_tokens": True}}])
def test_parse_usage_tolerates_garbage(body):
    assert ai.parse_usage(body) == (0, 0, 0)


def test_parse_usage_cached_never_exceeds_prompt():
    assert ai.parse_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 1, "prompt_cache_hit_tokens": 99}}) == (10, 1, 10)


# ---------------------------------------------------------------- knowledge clipping
def test_clip_knowledge_short_text_is_unchanged():
    assert ai.clip_knowledge("We make websites.", 8000) == "We make websites."
    assert ai.clip_knowledge("abc", 0) == "abc"           # 0 = no cap


def test_clip_knowledge_cuts_at_a_sentence_boundary():
    text = "We build sites. " * 400                          # 6400 chars
    out = ai.clip_knowledge(text, 1000)
    body = out.split("\n[...")[0]
    assert len(body) <= 1000 and body.endswith("sites.")     # never a half sentence
    assert out.endswith("[...knowledge base shortened...]")


def test_clip_knowledge_hard_cuts_when_no_boundary_in_the_last_half():
    out = ai.clip_knowledge("a" * 5000, 100)
    assert out.split("\n[...")[0] == "a" * 100


def test_clip_knowledge_prefers_a_line_break():
    text = "line one is here\n" * 100
    body = ai.clip_knowledge(text, 120).split("\n[...")[0]
    assert body.endswith("here") and len(body) <= 120


# ---------------------------------------------------------------- byte cache
def test_cache_hit_miss_and_stats():
    c = bytecache.ByteCache(max_bytes=100, max_entries=4)
    assert c.get("a") is None
    assert c.put("a", b"x" * 10)
    assert c.get("a") == b"x" * 10
    s = c.stats()
    assert (s["hits"], s["misses"], s["entries"], s["bytes"]) == (1, 1, 1, 10)


def test_cache_evicts_least_recently_used_by_bytes():
    c = bytecache.ByteCache(max_bytes=30, max_entries=10)
    c.put("a", b"x" * 10)
    c.put("b", b"x" * 10)
    c.put("c", b"x" * 10)
    assert c.get("a") is not None                         # touch a: b is now the oldest
    c.put("d", b"x" * 10)                                 # 40 bytes > 30: evict b
    assert c.get("b") is None and c.get("a") is not None and c.get("d") is not None
    assert c.stats()["bytes"] <= 30


def test_cache_entry_cap():
    c = bytecache.ByteCache(max_bytes=10_000, max_entries=3)
    for k in "abcd":
        c.put(k, b"1")
    assert c.stats()["entries"] == 3 and c.get("a") is None


def test_cache_refuses_a_file_bigger_than_the_whole_cache():
    c = bytecache.ByteCache(max_bytes=10)
    assert c.put("big", b"x" * 11) is False and c.get("big") is None


def test_cache_replace_keeps_byte_count_exact():
    c = bytecache.ByteCache(max_bytes=100)
    c.put("a", b"x" * 40)
    c.put("a", b"y" * 10)
    assert c.stats()["bytes"] == 10 and c.get("a") == b"y" * 10


def test_cache_invalidate_and_clear():
    c = bytecache.ByteCache()
    c.put("a", b"1")
    assert c.invalidate("a") is True and c.invalidate("a") is False and c.get("a") is None
    c.put("b", b"1")
    c.clear()
    assert c.stats()["entries"] == 0 and c.stats()["bytes"] == 0


def test_cache_entries_expire():
    c = bytecache.ByteCache(ttl_s=0.05)
    c.put("a", b"1")
    assert c.get("a") == b"1"
    time.sleep(0.08)
    assert c.get("a") is None and c.stats()["bytes"] == 0


def test_cache_key_includes_backend():
    assert bytecache.make_key("local", "t/abc.jpg") != bytecache.make_key("cloudinary", "t/abc.jpg")


# ---------------------------------------------------------------- leader lock (fake redis with expiry)
class FakeRedis:
    def __init__(self) -> None:
        self.d: dict[str, tuple[str, float]] = {}

    def _live(self, k: str) -> str | None:
        v = self.d.get(k)
        if v and v[1] > time.monotonic():
            return v[0]
        self.d.pop(k, None)
        return None

    def set(self, k: str, v: str, nx: bool = False, px: int | None = None) -> bool | None:
        if nx and self._live(k) is not None:
            return None
        self.d[k] = (v, time.monotonic() + (px or 10_000_000) / 1000)
        return True

    def eval(self, script: str, n: int, key: str, *args: Any) -> int:
        cur = self._live(key)
        if cur != args[0]:
            return 0
        if "'del'" in script:
            self.d.pop(key, None)
            return 1
        self.d[key] = (cur, time.monotonic() + int(args[1]) / 1000)  # pexpire
        return 1


class DeadRedis:
    def set(self, *a: Any, **k: Any) -> None:
        raise ConnectionError("down")


def test_try_lock_is_exclusive_and_release_checks_the_owner():
    r = FakeRedis()
    t1 = locks.try_lock("job", 5, r)
    assert t1 and locks.try_lock("job", 5, r) is None            # second replica is refused
    assert locks.release("job", "someone-else", r) is False      # a stranger cannot release it
    assert locks.release("job", t1, r) is True
    assert locks.try_lock("job", 5, r)                           # free again


def test_expired_lock_can_be_taken_and_old_owner_cannot_release_it():
    r = FakeRedis()
    old = locks.try_lock("job", 0.05, r)
    time.sleep(0.08)
    new = locks.try_lock("job", 5, r)
    assert new and new != old
    assert locks.release("job", old or "", r) is False           # the late old owner must not delete the new owner's lock
    assert locks.renew("job", new, 5, r) is True and locks.renew("job", "nope", 5, r) is False


def test_redis_down_means_no_lock():
    assert locks.try_lock("job", 5, DeadRedis()) is None


def test_leader_context_manager_exclusive_and_renewed():
    r = FakeRedis()

    async def scenario() -> tuple[bool, bool, bool, bool]:
        async with locks.leader("sched", 0.3, r) as first:
            async with locks.leader("sched", 0.3, r) as second:
                await asyncio.sleep(0.6)                           # longer than the TTL: the keep-alive must have renewed it
                third = locks.try_lock("sched", 5, r) is None      # still held by `first`
            held_after_inner = locks.try_lock("sched", 5, r) is None
        free_after = locks.try_lock("sched", 5, r) is not None     # released on exit
        return first, second, third and held_after_inner, free_after

    first, second, still_held, free_after = asyncio.run(scenario())
    assert first is True and second is False and still_held is True and free_after is True


def test_leader_yields_false_when_redis_is_down():
    async def scenario() -> bool:
        async with locks.leader("x", 1, DeadRedis()) as ok:
            return ok

    assert asyncio.run(scenario()) is False


# ---------------------------------------------------------------- recovery threshold, retention rules, production guard
def test_stale_sending_threshold_is_ten_minutes():
    assert outreach.STALE_SENDING_S == 600


def test_retention_covers_ai_usage_and_security_events():
    rules = {(r["table"], r.get("kind")): r for r in retention.plan_rules(NOW)}
    ai_rule = rules[("ai_usage_daily", None)]
    assert ai_rule.get("ctid") is True and "day <" in ai_rule["where"]      # composite key: deleted by ctid, not by id
    assert ai_rule["cutoff"] < NOW - timedelta(days=380)                    # ~13 months
    assert rules[("security_events", None)]["cutoff"] < NOW - timedelta(days=380)


def _prod(**over: Any) -> Settings:
    from app import crypto

    base: dict[str, Any] = dict(_env_file=None, jwt_secret="j" * 40, openwa_webhook_secret="w" * 40, cookie_secure=True, encryption_key=crypto.derive_key("prod"), ai_global_daily_cap=5000)
    base.update(over)
    return Settings(**base)


def test_production_requires_a_global_ai_cap():
    assert _prod().ai_global_daily_cap == 5000
    with pytest.raises(ValueError, match="AI_GLOBAL_DAILY_CAP"):
        _prod(ai_global_daily_cap=0)


def test_development_does_not_need_an_ai_cap():
    s = Settings(_env_file=None, jwt_secret="dev", openwa_webhook_secret="dev")
    assert s.ai_global_daily_cap == 0 and s.ai_model_fast == "" and s.ai_knowledge_max_chars == 8000
    assert s.ai_price_in_per_m == 0.0                                      # prices are never invented


# ---------------------------------------------------------------- worker supervisor
def test_supervise_does_not_restart_a_loop_that_finished_on_purpose():
    calls: list[int] = []

    async def finished() -> None:        # e.g. reaper_loop with REAPER_ENABLED=false
        calls.append(1)

    async def run() -> None:
        await asyncio.wait_for(worker._supervise("reaper", finished), timeout=2)   # must return, not spin forever

    asyncio.run(run())
    assert calls == [1]


def test_supervise_restarts_a_loop_that_crashed():
    calls: list[int] = []
    old = worker.RESTART_DELAY_S

    async def flaky() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    worker.RESTART_DELAY_S = 0
    try:
        asyncio.run(asyncio.wait_for(worker._supervise("x", flaky), timeout=2))
    finally:
        worker.RESTART_DELAY_S = old
    assert calls == [1, 1]
