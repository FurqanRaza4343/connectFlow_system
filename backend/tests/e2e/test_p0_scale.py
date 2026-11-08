"""P0 scale-readiness end-to-end (real Postgres + Redis, the running api/worker images).

 A. function-level checks run INSIDE the api container against the real database and Redis (`docker compose exec api python -`):
    AI counters (admin capacity reads what ai.py writes), recover_interrupted only touches STALE sending drafts, the Redis leader lock
    (exclusive, renewed, released, safe after expiry), the Postgres advisory lock used for start-up, the media byte cache (one storage read
    for many reads, invalidated on delete) and the retention rule for ai_usage_daily (composite key, deleted by ctid).
 B. HTTP checks as the platform admin: /api/admin/capacity ai + storage.cache blocks and the per-customer AI fields.
 C. (needs AI_FAKE=true and OUTREACH_FAKE_SEND=true) two worker replicas at once: every lead gets exactly ONE message, a fresh in-flight 'sending'
    draft is NOT failed by the second worker starting, nothing is marked 'Interrupted'. Skipped (not failed) otherwise.
Test workspaces are named 'P0X ...' and removed at the end. Nothing here prints a secret. Needs E2E_ADMIN_PASSWORD."""
import http.cookiejar, json, os, subprocess, sys, time, urllib.error, urllib.request, uuid  # noqa: E401
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA, api_env, require_admin_password

ADMIN_PW = require_admin_password()
ok_n = bad_n = 0
stamp = str(int(time.time()))
TENANTS: list[str] = []
EMAILS: list[str] = []


def check(name, cond, extra=""):
    global ok_n, bad_n
    cond = bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{str(extra)[:300]}]" if extra and not cond else ""))
    ok_n += cond
    bad_n += (not cond)


def sql(q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def compose(*args, timeout=240):
    """`docker compose` with the same files the stack was started with (E2E_COMPOSE_OVERRIDE = an extra -f file, e.g. the fake-engine override), so a second
    replica gets the same environment as the first."""
    files = ["-f", "docker-compose.yml"] + (["-f", os.environ["E2E_COMPOSE_OVERRIDE"]] if os.environ.get("E2E_COMPOSE_OVERRIDE") else [])
    return subprocess.run(["docker", "compose", *files, *args], cwd=INFRA, capture_output=True, text=True, timeout=timeout)


def wait(fn, timeout=40, every=1.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return fn()


class Client:
    def __init__(self, host=APP_HOST):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.host = host

    def __call__(self, method, path, body=None):
        req = urllib.request.Request(BASE + path, method=method, headers={"Host": self.host, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            r = self.op.open(req, timeout=120)
            d = r.read()
            return r.status, (json.loads(d) if d else {})
        except urllib.error.HTTPError as e:
            d = e.read()
            try:
                return e.code, json.loads(d)
            except ValueError:
                return e.code, {"raw": d[:200].decode(errors="ignore")}


def tenant(label, plan="growth", age_days=None):
    email = f"p0x-{label.lower()}-{stamp}@example.com"
    EMAILS.append(email)
    c = Client()
    st, me = c("POST", "/api/auth/signup", {"email": email, "password": "Sup3rSecretPw!", "company": f"P0X {label} {stamp}"})
    assert st in (200, 201), (st, me)
    sql(f"update users set email_verified_at=now() where email='{email}'")
    tid = me["tenant"]["id"]
    TENANTS.append(tid)
    sql(f"update tenants set plan_id='{plan}' where id='{tid}'")
    iid = sid = None
    if age_days is not None:
        iid, sid, phone = str(uuid.uuid4()), f"sess-p0x-{label.lower()}-{stamp}", "92300" + str(uuid.uuid4().int)[:7]
        sql(f"insert into instances (id,tenant_id,label,node_id,engine,openwa_name,openwa_id,phone,status,created_at) values ('{iid}','{tid}','{label}','wa1','baileys','cf-p0x-{iid[:8]}','{sid}','{phone}','ready',now() - interval '{age_days} days')")
    st, pr = c("GET", "/api/outreach/profile")
    prof = {k: v for k, v in pr.items() if k != "media"}
    prof.update({"sender_name": "Ayesha", "company_name": "P0X Co", "offer": "We test scale", "batch_size": 20, "daily_limit": 100, "delay_min": 0, "delay_max": 0, "timezone": "UTC",
                 "active_from": "00:00", "active_to": "23:59", "dry_run": False, "require_approval": True, "followup_day1": 3, "followup_day2": 7})
    assert c("PUT", "/api/outreach/profile", prof)[0] == 200
    return c, tid, iid, sid


def in_api(script: str) -> dict:
    """Run a Python script inside the api container; it prints one JSON object on its last line."""
    r = subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-"], cwd=INFRA, input=script, capture_output=True, text=True, timeout=300)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return {"_error": (r.stderr or r.stdout)[-600:]}


# ======================================================================== A. in-container checks
SCRIPT = r'''
import asyncio, json, uuid, time
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import text
from app import ai, ai_counters, bytecache, locks, outreach as og, retention, storage, assetlib
from app.db import SessionLocal, engine
from app.deps import rds
from app.models import AiUsageDaily, Draft, Lead

TID = "__TID__"
out = {}

# ---- 1. AI counters: gate() counts, the admin helper reads the same keys
before = ai_counters.read_today(rds)
for _ in range(3):
    asyncio.run(ai.gate(TID))
ai._record_tokens(TID, 1000, 200, 400)
ai._note_retry(TID)
after = ai_counters.read_today(rds)
with SessionLocal() as db:
    row = db.get(AiUsageDaily, (TID, datetime.now(timezone.utc).date()))
    out["usage_row"] = [row.calls, row.retries, row.tokens_in, row.tokens_out, row.tokens_cached] if row else None
out["calls_delta"] = after["callsToday"] - before["callsToday"]
out["tokens_delta"] = after["tokensToday"] - before["tokensToday"]
out["retries_delta"] = after["retriesToday"] - before["retriesToday"]

# ---- 2. recover_interrupted: only stale sending drafts
with SessionLocal() as db:
    lead = Lead(tenant_id=TID, company="P0X recover")
    db.add(lead); db.flush()
    now = datetime.now(timezone.utc)
    ids = {}
    for name, at in (("fresh", now), ("stale", now - timedelta(minutes=20)), ("legacy", None), ("edge", now - timedelta(minutes=5))):
        d = Draft(tenant_id=TID, lead_id=lead.id, kind="first", body="x", status="sending", sending_at=at)
        db.add(d); db.flush(); ids[name] = d.id
    db.commit()
with SessionLocal() as db:
    n = og.recover_interrupted(db)
with SessionLocal() as db:
    out["recover_n_for_tenant"] = db.query(Draft).filter(Draft.tenant_id == TID, Draft.status == "failed").count()
    out["recover_status"] = {k: db.get(Draft, v).status for k, v in ids.items()}
    out["recover_note"] = db.get(Draft, ids["stale"]).note[:40]
    db.query(Draft).filter(Draft.tenant_id == TID).delete(); db.query(Lead).filter(Lead.tenant_id == TID).delete(); db.commit()

# ---- 3. leader lock on the real Redis
async def lock_scenario():
    res = {}
    async with locks.leader("p0x-test", 2) as a:
        async with locks.leader("p0x-test", 2) as b:
            res["inner_denied"] = (b is False)
            await asyncio.sleep(3.2)                       # longer than the TTL: the keep-alive must have renewed it
            res["still_held_after_ttl"] = locks.try_lock("p0x-test", 5) is None
        res["outer_granted"] = a is True
    res["released_on_exit"] = rds.get(locks.PREFIX + "p0x-test") is None
    t = locks.try_lock("p0x-short", 0.4)
    time.sleep(0.6)
    t2 = locks.try_lock("p0x-short", 5)
    res["expired_retaken"] = bool(t and t2 and t != t2)
    res["old_owner_cannot_release"] = locks.release("p0x-short", t) is False
    res["new_owner_releases"] = locks.release("p0x-short", t2) is True
    return res
out["lock"] = asyncio.run(lock_scenario())

# ---- 4. Postgres advisory lock used by start-up
with engine.connect() as c1, engine.connect() as c2:
    locks.advisory_lock(c1, locks.ADV_STARTUP)
    got = c2.execute(text("select pg_try_advisory_lock(:k)"), {"k": locks.ADV_STARTUP}).scalar()
    locks.advisory_unlock(c1, locks.ADV_STARTUP)
    got_after = c2.execute(text("select pg_try_advisory_lock(:k)"), {"k": locks.ADV_STARTUP}).scalar()
    c2.execute(text("select pg_advisory_unlock(:k)"), {"k": locks.ADV_STARTUP})
    out["advisory"] = [bool(got), bool(got_after)]

# ---- 5. media byte cache on the real local storage
async def cache_scenario():
    st = storage.get_storage("local")
    key = await st.put(TID, b"p0x-cache-bytes" * 100, "jpg", "image")
    hour = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    r0 = int(rds.get(f"media:reads:{hour}") or 0); h0 = int(rds.get(f"media:cachehit:{hour}") or 0)
    bytecache.cache.clear()
    datas = [await assetlib.read_file("local", key) for _ in range(5)]
    r1 = int(rds.get(f"media:reads:{hour}") or 0); h1 = int(rds.get(f"media:cachehit:{hour}") or 0)
    res = {"same_bytes": all(d == datas[0] for d in datas), "storage_reads": r1 - r0, "cache_hits": h1 - h0,
           "cached_before_delete": bytecache.cache.get(bytecache.make_key("local", key)) is not None}
    await st.delete(key)
    res["invalidated_on_delete"] = bytecache.cache.get(bytecache.make_key("local", key)) is None
    try:
        await assetlib.read_file("local", key); res["gone_after_delete"] = False
    except Exception:
        res["gone_after_delete"] = True
    return res
out["cache"] = asyncio.run(cache_scenario())

# ---- 6. retention rule for ai_usage_daily (composite primary key -> ctid delete) on real Postgres
old_day = date(2020, 1, 1)
with SessionLocal() as db:
    db.add(AiUsageDaily(tenant_id=TID, day=old_day, calls=1)); db.commit()
rule = [r for r in retention.plan_rules(datetime.now(timezone.utc)) if r["table"] == "ai_usage_daily"][0]
with SessionLocal() as db:
    res = db.execute(text(f"DELETE FROM ai_usage_daily WHERE ctid IN (SELECT ctid FROM ai_usage_daily WHERE {rule['where']} LIMIT :n)"), {"cutoff": rule["cutoff"], "n": 100})
    db.commit()
with SessionLocal() as db:
    out["retention"] = {"old_removed": db.get(AiUsageDaily, (TID, old_day)) is None, "today_kept": db.get(AiUsageDaily, (TID, datetime.now(timezone.utc).date())) is not None}
print(json.dumps(out))
'''

INFLIGHT = r'''
import json
from datetime import datetime, timezone
from app.db import SessionLocal
from app.models import Draft, Lead
with SessionLocal() as db:
    lead = Lead(tenant_id="__TID__", company="P0X inflight", status="queued")
    db.add(lead); db.flush()
    d = Draft(tenant_id="__TID__", lead_id=lead.id, kind="first", body="in flight", status="sending", sending_at=datetime.now(timezone.utc))
    db.add(d); db.commit()
    print(json.dumps({"draft": d.id}))
'''

try:
    c1, tid1, _, _ = tenant("Counters", plan="growth")
    res = in_api(SCRIPT.replace("__TID__", tid1))
    check("in-container script ran", "_error" not in res, res.get("_error", ""))
    if "_error" not in res:
        check("AI gate: calls counted (Redis ai:day) and in the workspace row", res["calls_delta"] >= 3 and res["usage_row"] and res["usage_row"][0] == 3, (res["calls_delta"], res["usage_row"]))
        check("AI tokens + retries land in the Redis counters and the daily row", res["tokens_delta"] >= 1200 and res["retries_delta"] >= 1 and res["usage_row"][1:] == [1, 1000, 200, 400], (res["tokens_delta"], res["retries_delta"], res["usage_row"]))
        check("recover_interrupted: fresh (0 min) and 5-min-old sending drafts are NOT touched",
              res["recover_status"]["fresh"] == "sending" and res["recover_status"]["edge"] == "sending", res["recover_status"])
        check("recover_interrupted: a 20-min-old draft and one with no timestamp are failed for review",
              res["recover_status"]["stale"] == "failed" and res["recover_status"]["legacy"] == "failed" and res["recover_n_for_tenant"] == 2 and "Interrupted" in res["recover_note"], res)
        lk = res["lock"]
        check("leader lock: a second holder is refused while the first is alive", lk["inner_denied"] and lk["outer_granted"], lk)
        check("leader lock: the keep-alive renews it past the TTL, and exit releases it", lk["still_held_after_ttl"] and lk["released_on_exit"], lk)
        check("leader lock: after expiry another replica takes it and the old owner cannot release it", lk["expired_retaken"] and lk["old_owner_cannot_release"] and lk["new_owner_releases"], lk)
        check("Postgres advisory lock: a second session cannot take the start-up lock until it is released", res["advisory"] == [False, True], res["advisory"])
        ch = res["cache"]
        check("media byte cache: 5 reads of one file = ONE storage read + 4 cache hits, same bytes", ch["same_bytes"] and ch["storage_reads"] == 1 and ch["cache_hits"] == 4, ch)
        check("media byte cache: deleting the file invalidates the cached copy", ch["cached_before_delete"] and ch["invalidated_on_delete"] and ch["gone_after_delete"], ch)
        check("retention: ai_usage_daily (composite key) old rows deleted by ctid, today's kept", res["retention"] == {"old_removed": True, "today_kept": True}, res["retention"])

    # ==================================================================== B. admin API
    ad = Client(ADMIN_HOST)
    st, _ = ad("POST", "/api/auth/login", {"email": os.environ.get("E2E_ADMIN_EMAIL", "admin@connectflow.example.com"), "password": ADMIN_PW})
    check("admin login", st == 200, st)
    st, cap = ad("GET", "/api/admin/capacity")
    ai_blk = cap.get("ai", {}) if st == 200 else {}
    check("capacity.ai reports REAL counters (callsToday >= the 3 gated calls, tokens, retries)", st == 200 and ai_blk.get("callsToday", 0) >= 3 and ai_blk.get("tokensToday", 0) >= 1200 and ai_blk.get("retriesToday", 0) >= 1, ai_blk)
    check("capacity.ai has the new keys and a boolean breaker", {"callsToday", "tokensToday", "costTodayUsd", "retriesToday", "breakerOpen", "globalCap"} <= set(ai_blk) and isinstance(ai_blk.get("breakerOpen"), bool), ai_blk)
    sc = cap.get("storage", {}).get("cache", {})
    check("capacity.storage.cache exposes hits / misses / storage reads for the last hour", {"hits1h", "misses1h", "storageReads1h"} <= set(sc) and sc.get("storageReads1h", 0) >= 1 and sc.get("hits1h", 0) >= 4, sc)
    st, det = ad("GET", f"/api/admin/customers/{tid1}")
    u = det.get("usage", {}) if st == 200 else {}
    check("customer detail: aiTokensToday / aiRetriesToday / cost fields (cost 0 = prices not configured)", st == 200 and u.get("aiTokensToday") == 1200 and u.get("aiRetriesToday") == 1 and "aiCostTodayUsd" in u and "aiCostMonthUsd" in u, u)
    st, lst = ad("GET", f"/api/admin/customers?q=P0X%20Counters%20{stamp}")
    row = next((r for r in lst.get("rows", []) if r["id"] == tid1), {}) if st == 200 else {}
    check("customer list rows carry aiTokensToday / aiCostTodayUsd / aiCostMonthUsd", row.get("aiTokensToday") == 1200 and "aiCostTodayUsd" in row and "aiCostMonthUsd" in row, row)
    st, bill = c1("GET", "/api/billing")
    check("customer /api/billing usage has aiTokensToday (tokens only, no cost)", st == 200 and bill["usage"].get("aiTokensToday") == 1200 and "aiCostTodayUsd" not in bill["usage"], bill.get("usage"))
    st, other = Client()("GET", f"/api/admin/customers/{tid1}")
    check("a non-admin cannot read the per-customer AI numbers", st in (401, 403), st)

    # ==================================================================== C. two worker replicas
    eng = api_env("AI_FAKE", "OUTREACH_FAKE_SEND")
    if not (eng.get("AI_FAKE", "").lower() == "true" and eng.get("OUTREACH_FAKE_SEND", "").lower() == "true"):
        print("SKIP  part C (two worker replicas) needs AI_FAKE=true and OUTREACH_FAKE_SEND=true on the stack")
    else:
        c2, tid2, iid2, sid2 = tenant("TwoWorkers", plan="growth", age_days=10)
        rows = ["Company,Phone,Website,Business Type"] + [f"P0X Lead {i},+92301{str(uuid.uuid4().int)[:7]},p0x{i}.example,Clinic" for i in range(8)]
        st, an = c2("POST", "/api/leads/import/analyze", {"csv": "\n".join(rows)})
        st, imp = c2("POST", "/api/leads/import", {"csv": "\n".join(rows), "mapping": an["mapping"], "list_name": "P0X"})
        check("8 leads imported for the two-worker run", imp.get("added") == 8, imp)
        c2("POST", "/api/outreach/run-now", {"limit": 8})
        ids = [d["id"] for d in c2("GET", "/api/drafts?status=pending&kind=first")[1]["rows"]]
        check("8 first-message drafts ready", len(ids) == 8, len(ids))
        # an in-flight send of "another worker": fresh sending_at, must survive the second replica starting
        mk = in_api(INFLIGHT.replace("__TID__", tid2))
        fdraft = mk.get("draft", "")
        check("an in-flight 'sending' draft (sending_at = now) exists", bool(fdraft) and sql(f"select status from drafts where id='{fdraft}'") == "sending", mk)
        r = compose("up", "-d", "--no-recreate", "--scale", "worker=2", "worker")
        names = compose("ps", "worker", "--format", "{{.Name}}|{{.Status}}").stdout
        check("a second worker replica is running", len([l for l in names.splitlines() if l.strip()]) == 2, names + r.stderr[-200:])
        time.sleep(8)  # the new replica runs its recover loop at start-up
        check("the second replica's start-up did NOT fail the first replica's in-flight draft", sql(f"select status from drafts where id='{fdraft}'") == "sending", sql(f"select status,note from drafts where id='{fdraft}'"))
        sql("update instances set status='ready' where openwa_name like 'cf-p0x-%'")
        st, ap = c2("POST", "/api/drafts/approve", {"ids": ids})
        done = wait(lambda: int(sql(f"select count(*) from messages where tenant_id='{tid2}' and direction='out' and kind='first'") or 0) >= 8, 120, 2)
        time.sleep(4)
        n_msgs = int(sql(f"select count(*) from messages where tenant_id='{tid2}' and direction='out' and kind='first'") or 0)
        n_leads = int(sql(f"select count(distinct lead_id) from messages where tenant_id='{tid2}' and direction='out' and kind='first'") or 0)
        check("two replicas: every lead got exactly ONE first message (8 messages, 8 distinct leads)", n_msgs == 8 and n_leads == 8, (n_msgs, n_leads, ap))
        check("two replicas: all 8 drafts sent, none failed or 'Interrupted'", sql(f"select count(*) from drafts where tenant_id='{tid2}' and id <> '{fdraft}' and status='sent'") == "8" and sql(f"select count(*) from drafts where tenant_id='{tid2}' and status='failed'") == "0", sql(f"select status,count(*) from drafts where tenant_id='{tid2}' group by 1"))
        check("two replicas: the plan counted each message once (usage_events == messages)", sql(f"select count(*) from usage_events where tenant_id='{tid2}' and kind='message'") == str(n_msgs), sql(f"select count(*) from usage_events where tenant_id='{tid2}' and kind='message'"))
        compose("up", "-d", "--no-recreate", "--scale", "worker=1", "worker")
        names = compose("ps", "worker", "--format", "{{.Name}}").stdout
        check("scaled back to one worker", len([l for l in names.splitlines() if l.strip()]) == 1, names)
finally:
    compose("up", "-d", "--no-recreate", "--scale", "worker=1", "worker")
    for t in TENANTS:
        sql(f"delete from tenants where id='{t}' and name like 'P0X %'")
    for e in EMAILS:
        sql(f"delete from users where email='{e}'")

print(f"\n{ok_n} passed, {bad_n} failed")
sys.exit(1 if bad_n else 0)
