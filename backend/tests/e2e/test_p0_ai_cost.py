"""AI cost accounting end-to-end against a FAKE OpenAI-compatible provider started by this script.

Needs the api/worker started with AI_FAKE=false, AI_API_KEY=<anything>, AI_BASE_URL=http://host.docker.internal:<E2E_FAKE_AI_PORT>, AI_MODEL=model-main,
AI_MODEL_FAST=model-fast, AI_PRICE_IN_PER_M=1.0, AI_PRICE_OUT_PER_M=2.0, AI_PRICE_CACHED_PER_M=0.1 (a compose override does this, see docs/BILLING.md);
it skips itself otherwise. Checks: provider usage (tokens, cache hits) is recorded per workspace and per day, the estimated cost uses the configured
prices only, a 429 from the provider is a counted RETRY that does not consume the plan quota, the cheaper model is used for follow-ups, the knowledge
base is clipped before it is sent, and the admin API shows all of it. The fake provider's first request answers 429 on purpose.
Test workspaces are named 'P0A ...' and removed at the end. Needs E2E_ADMIN_PASSWORD."""
import http.cookiejar, json, os, subprocess, sys, threading, time, urllib.error, urllib.request, uuid  # noqa: E401
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA, api_env, require_admin_password, skip

PORT = int(os.environ.get("E2E_FAKE_AI_PORT", "0"))
env = api_env("AI_FAKE", "AI_BASE_URL", "AI_MODEL", "AI_MODEL_FAST", "AI_PRICE_IN_PER_M", "AI_PRICE_OUT_PER_M", "AI_PRICE_CACHED_PER_M")
if not PORT or env.get("AI_FAKE", "").lower() == "true" or f":{PORT}" not in env.get("AI_BASE_URL", "") or env.get("AI_MODEL") != "model-main" or env.get("AI_MODEL_FAST") != "model-fast":
    skip("needs the api started against the fake AI provider (E2E_FAKE_AI_PORT, AI_BASE_URL, AI_MODEL=model-main, AI_MODEL_FAST=model-fast, AI_FAKE=false)")
ADMIN_PW = require_admin_password()

ok_n = bad_n = 0
stamp = str(int(time.time()))
TENANTS: list[str] = []
EMAILS: list[str] = []
SEEN: list[dict] = []
STATE = {"n": 0}


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        STATE["n"] += 1
        sys_msg = (body.get("messages") or [{}])[0].get("content", "")
        SEEN.append({"n": STATE["n"], "model": body.get("model"), "json": bool(body.get("response_format")), "system": sys_msg})
        if STATE["n"] == 1:  # the very first request is rate limited: the client must retry and the retry must be COUNTED
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"slow down"}')
            return
        content = '{"message": "Hi P0A team,\\n[fake-provider] hello.\\nIf this isn\'t relevant, just reply STOP and I won\'t message again.\\nAyesha from P0A Co"}' if body.get("response_format") else "Research text from the fake provider."
        out = {"choices": [{"finish_reason": "stop", "message": {"content": content}}],
               "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_cache_hit_tokens": 400, "prompt_cache_miss_tokens": 600}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


srv = ThreadingHTTPServer(("0.0.0.0", PORT), Provider)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def check(name, cond, extra=""):
    global ok_n, bad_n
    cond = bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{str(extra)[:300]}]" if extra and not cond else ""))
    ok_n += cond
    bad_n += (not cond)


def sql(q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q], cwd=INFRA, capture_output=True, text=True).stdout.strip()


class Client:
    def __init__(self, host=APP_HOST):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.host = host

    def __call__(self, method, path, body=None):
        req = urllib.request.Request(BASE + path, method=method, headers={"Host": self.host, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            r = self.op.open(req, timeout=180)
            d = r.read()
            return r.status, (json.loads(d) if d else {})
        except urllib.error.HTTPError as e:
            d = e.read()
            try:
                return e.code, json.loads(d)
            except ValueError:
                return e.code, {"raw": d[:200].decode(errors="ignore")}


FOLLOWUP = r'''
import asyncio, json
from app import ai, outreach as og
from app.db import SessionLocal
with SessionLocal() as db:
    p = og.profile_of(db, "__TID__")
    msg = asyncio.run(ai.write_followup({"company": "P0A lead"}, p, "P0A Co", "research", [], 1))
print(json.dumps({"msg": msg[:60]}))
'''

try:
    email = f"p0a-{stamp}@example.com"
    EMAILS.append(email)
    c = Client()
    st, me = c("POST", "/api/auth/signup", {"email": email, "password": "Sup3rSecretPw!", "company": f"P0A Cost {stamp}"})
    assert st in (200, 201), (st, me)
    sql(f"update users set email_verified_at=now() where email='{email}'")
    tid = me["tenant"]["id"]
    TENANTS.append(tid)
    sql(f"update tenants set plan_id='growth' where id='{tid}'")
    st, pr = c("GET", "/api/outreach/profile")
    prof = {k: v for k, v in pr.items() if k != "media"}
    knowledge = "Fact number one is true. " * 400  # 10 000 characters: more than AI_KNOWLEDGE_MAX_CHARS (8000)
    prof.update({"sender_name": "Ayesha", "company_name": "P0A Co", "offer": "We test AI cost accounting", "knowledge": knowledge, "batch_size": 5, "daily_limit": 50, "timezone": "UTC",
                 "active_from": "00:00", "active_to": "23:59", "dry_run": False, "require_approval": True})
    st, _ = c("PUT", "/api/outreach/profile", prof)
    check("profile with a 10 000-character knowledge base saved (the profile still stores it all)", st == 200, st)
    check("the stored knowledge base is NOT shortened in the database", int(sql(f"select length(knowledge) from outreach_profiles where tenant_id='{tid}'") or 0) == len(knowledge))

    rows = ["Company,Phone,Website,Business Type", f"P0A Lead,+92301{str(uuid.uuid4().int)[:7]},,Clinic"]
    st, an = c("POST", "/api/leads/import/analyze", {"csv": "\n".join(rows)})
    st, imp = c("POST", "/api/leads/import", {"csv": "\n".join(rows), "mapping": an["mapping"], "list_name": "P0A"})
    check("one lead imported", imp.get("added") == 1, imp)
    t0 = time.time()
    st, run = c("POST", "/api/outreach/run-now", {"limit": 1})
    check("run-now drafts the message through the (fake) provider despite its first 429", st in (200, 201) and run.get("queued") == 1, (st, run))
    check("the 429 was retried (took the back-off) and 3 provider requests were made: 429, research, first message", len(SEEN) == 3 and time.time() - t0 >= 1.5, (len(SEEN), time.time() - t0))
    check("research and the first message use the MAIN model", [s["model"] for s in SEEN[1:3]] == ["model-main", "model-main"], [s["model"] for s in SEEN])
    sysmsg = SEEN[2]["system"] if len(SEEN) > 2 else ""
    kb = sysmsg.split("<knowledge_base>")[-1].split("</knowledge_base>")[0] if "<knowledge_base>" in sysmsg else ""
    check("the knowledge base sent to the provider is clipped (<= ~8000 chars, ends at a sentence, marked as shortened)", 0 < len(kb) <= 8100 and "[...knowledge base shortened...]" in kb and len(kb) < len(knowledge), len(kb))
    d = c("GET", "/api/drafts?status=pending&kind=first")[1].get("rows", [])
    check("the draft text came from the provider", d and "[fake-provider]" in d[0]["body"], d[:1])

    row = sql(f"select calls,retries,tokens_in,tokens_out,tokens_cached,est_cost_micro_usd from ai_usage_daily where tenant_id='{tid}' and day=(now() at time zone 'utc')::date")
    check("ai_usage_daily: 2 calls, 1 retry, 2000 in / 200 out / 800 cached tokens, 1680 micro-USD (600*1.0 + 400*0.1 + 100*2.0, twice)", row == "2|1|2000|200|800|1680", row)
    used = int(sql(f"select count(*) from usage_events where tenant_id='{tid}' and kind='ai'") or 0)
    check("the retry did NOT consume the plan quota (2 gated calls = 2 usage events)", used == 2, used)

    ad = Client(ADMIN_HOST)
    st, _ = ad("POST", "/api/auth/login", {"email": os.environ.get("E2E_ADMIN_EMAIL", "admin@connectflow.example.com"), "password": ADMIN_PW})
    check("admin login", st == 200, st)
    st, det = ad("GET", f"/api/admin/customers/{tid}")
    u = det.get("usage", {}) if st == 200 else {}
    check("admin customer detail: tokens, retries and the estimated cost in USD", u.get("aiTokensToday") == 2200 and u.get("aiRetriesToday") == 1 and abs(u.get("aiCostTodayUsd", 0) - 0.00168) < 1e-9 and abs(u.get("aiCostMonthUsd", 0) - 0.00168) < 1e-9, u)
    st, cap = ad("GET", "/api/admin/capacity")
    a = cap.get("ai", {}) if st == 200 else {}
    check("admin capacity: platform tokens / cost / retries include this workspace", a.get("tokensToday", 0) >= 2200 and a.get("costTodayUsd", 0) >= 0.00168 and a.get("retriesToday", 0) >= 1 and a.get("callsToday", 0) >= 2, a)

    n0 = len(SEEN)
    res = subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-"], cwd=INFRA, input=FOLLOWUP.replace("__TID__", tid), capture_output=True, text=True, timeout=180)
    check("a follow-up goes through the provider", len(SEEN) == n0 + 1 and "[fake-provider]" in res.stdout, (res.stdout[-200:], res.stderr[-200:]))
    check("follow-ups use the cheaper AI_MODEL_FAST", SEEN[-1]["model"] == "model-fast" and SEEN[-1]["json"], SEEN[-1]["model"] if SEEN else "")
    st, det = ad("GET", f"/api/admin/customers/{tid}")
    u = det.get("usage", {}) if st == 200 else {}
    check("after the follow-up the workspace has 3 calls and 3300 tokens", u.get("aiTokensToday") == 3300, u)
    st, lst = ad("GET", f"/api/admin/customers?q=P0A%20Cost%20{stamp}")
    r0 = next((r for r in lst.get("rows", []) if r["id"] == tid), {}) if st == 200 else {}
    check("admin customer list row has the AI numbers", r0.get("aiTokensToday") == 3300 and r0.get("aiCostMonthUsd", 0) > 0.0025, r0)
    st, bill = c("GET", "/api/billing")
    check("the customer sees tokens but never the cost", st == 200 and bill["usage"].get("aiTokensToday") == 3300 and "aiCostMonthUsd" not in json.dumps(bill), bill.get("usage"))
finally:
    srv.shutdown()
    for t in TENANTS:
        sql(f"delete from tenants where id='{t}' and name like 'P0A %'")
    for e in EMAILS:
        sql(f"delete from users where email='{e}'")

print(f"\n{ok_n} passed, {bad_n} failed")
sys.exit(1 if bad_n else 0)
