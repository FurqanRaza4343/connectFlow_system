"""E2E: API-key telemetry, leak detection, IP allow-list, limiters, AI cost control, admin Security API.

Standalone (python + urllib, no pytest). Run from anywhere with the stack up:
    python backend/tests/e2e/test_security.py
Needs docker compose (for SQL, Redis and calling the api container directly, because Caddy overwrites X-Forwarded-For).
Owner e-mails are checked in Mailpit; if RESEND_API_KEY is set the mail goes to Resend instead and that check is reported as SKIP.
The circuit-breaker check runs only when AI_GLOBAL_DAILY_CAP > 0 in the api container (otherwise SKIP).
"""
import http.cookiejar, json, os, re, subprocess, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
INFRA = os.path.abspath(os.path.join(HERE, "..", "..", "..", "infra"))
BASE = "http://127.0.0.1:8080"
ok_n = bad_n = skip_n = 0
STAMP = str(int(time.time()))


def sess(host="app.connectflow.localhost:8080"):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    H = {"Host": host, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}

    def call(method, path, body=None, raw=False, headers=None):
        req = urllib.request.Request(BASE + path, method=method, headers={**H, **(headers or {})}, data=json.dumps(body).encode() if body is not None else None)
        try:
            r = op.open(req, timeout=60)
            d = r.read()
            return r.status, (d.decode() if raw else (json.loads(d) if d else {}))
        except urllib.error.HTTPError as e:
            d = e.read()
            try:
                return e.code, json.loads(d)
            except Exception:
                return e.code, {"raw": d[:300].decode(errors="ignore")}
    return call


def check(name, cond, extra=""):
    global ok_n, bad_n
    cond = bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{extra}]" if extra and not cond else ""))
    ok_n += cond
    bad_n += (not cond)


def skip(name, why):
    global skip_n
    skip_n += 1
    print(f"SKIP  {name}   [{why}]")


def dc(*args, stdin=None):
    return subprocess.run(["docker", "compose", *args], cwd=INFRA, capture_output=True, text=True, input=stdin).stdout.strip()


def sql(q):
    return dc("exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q)


def redis(*a):
    return dc("exec", "-T", "redis", "redis-cli", *a)


DIRECT = '''
import sys, json, urllib.request, urllib.error
out = []
for m, p, h in json.loads(sys.stdin.read()):
    r = urllib.request.Request("http://127.0.0.1:8000" + p, method=m, headers=h)
    try:
        x = urllib.request.urlopen(r, timeout=20); out.append([x.status, x.read().decode()[:400], dict(x.headers)])
    except urllib.error.HTTPError as e:
        out.append([e.code, e.read().decode()[:400], dict(e.headers)])
print(json.dumps(out))
'''


def direct(reqs):
    """Call the api container itself (so we choose the client IP through X-Forwarded-For). reqs = [(method, path, headers)]"""
    return json.loads(dc("exec", "-T", "api", "python", "-c", DIRECT, stdin=json.dumps(reqs)).splitlines()[-1])


def use(key, ip, path="/v1/instances", ua="sec-test/1.0"):
    return direct([("GET", path, {"Authorization": f"Bearer {key}", "X-Forwarded-For": ip, "User-Agent": ua})])[0]


def flush():
    return dc("exec", "-T", "api", "python", "-c", "from app.telemetry import flush_to_db; print(flush_to_db())")


env = open(os.path.join(INFRA, ".env"), encoding="utf-8").read()
ADM_PW = re.search(r"ADMIN_PASSWORD=(.*)", env).group(1).strip()
ADM_EMAIL = (re.search(r"ADMIN_EMAIL=(.*)", env) or [None, "admin@connectflow.example.com"])[1].strip() if re.search(r"ADMIN_EMAIL=(.*)", env) else "admin@connectflow.example.com"
RESEND_ON = bool(re.search(r"^RESEND_API_KEY=.+", env, re.M))


def setup(name, email, plan="trial"):
    c = sess()
    st, me = c("POST", "/api/auth/signup", {"email": email, "password": "Sup3rSecretPw!", "company": name})
    sql(f"update users set email_verified_at=now() where email='{email}'")
    tid = me["tenant"]["id"]
    sql(f"update tenants set plan_id='{plan}' where id='{tid}'")
    return c, tid


def mkkey(c, name, **kw):
    st, k = c("POST", "/api/keys", {"name": name, "expires_in": "1d", **kw})
    assert st == 201, (st, k)
    return k


def mail_for(email, needle):
    try:
        r = json.loads(urllib.request.urlopen("http://127.0.0.1:8025/api/v1/messages?limit=60").read())
        return any(any(t.get("Address") == email for t in m.get("To", [])) and needle in m.get("Subject", "") for m in r.get("messages", []))
    except Exception:
        return False


created = []
AD = sess("admin.connectflow.localhost:8080")
st, _ = AD("POST", "/api/auth/login", {"email": ADM_EMAIL, "password": ADM_PW})
assert st == 200, "admin login failed"

# ---------------------------------------------------------------- tenants
A, TA = setup(f"SEC Alpha {STAMP}", f"sec-a-{STAMP}@example.com")
B, TB = setup(f"SEC Beta {STAMP}", f"sec-b-{STAMP}@example.com")
created += [TA, TB]
EA = f"sec-a-{STAMP}@example.com"

# ---------------------------------------------------------------- 1. leak simulation: one key, 6 IPs
kA = mkkey(A, "leaky")
ips = [f"203.0.113.{i}" for i in range(1, 7)]
res = [use(kA["key"], ip)[0] for ip in ips]
check("key works from 5 IPs, the 6th distinct IP in 24h is refused", res[:5] == [200] * 5 and res[5] == 401, res)
st, ks = A("GET", "/api/keys")
row = [k for k in ks if k["id"] == kA["id"]][0]
check("key is now suspended with a reason, status shown to the owner", row["status"] == "suspended" and "6 different IP" in row["suspendedReason"] and row["suspendedBy"] == "system", row)
st, r = use(kA["key"], ips[0])[:2]
check("suspended key is refused even from a known IP (401, says suspended)", st == 401 and "suspended" in r.lower(), r)
st, ev = AD("GET", f"/api/admin/security/events?tenant_id={TA}&kind=many_ips")
check("admin sees a critical many_ips event for that key", st == 200 and ev["total"] >= 1 and ev["rows"][0]["severity"] == "critical" and ev["rows"][0]["keyId"] == kA["id"], ev)
check("audit trail has apikey.suspended", int(sql(f"select count(*) from audit_log where tenant_id='{TA}' and action='apikey.suspended'") or 0) == 1)
if RESEND_ON:
    skip("owner e-mail on auto-suspend", "RESEND_API_KEY set: mail goes to Resend, not Mailpit")
else:
    time.sleep(2)
    check("owner received the 'API key was suspended' e-mail", mail_for(EA, "suspended"))
st, ev2 = AD("GET", f"/api/admin/security/events?tenant_id={TA}&kind=many_ips")
check("suspension is idempotent: still exactly one event after more attempts", ev2["total"] == 1, ev2["total"])

# customer unsuspend resets the IP window and the key works again
check("owner can unsuspend a system-suspended key", A("POST", f"/api/keys/{kA['id']}/unsuspend")[0] == 200)
check("unsuspended key works again", use(kA["key"], ips[0])[0] == 200)
check("unsuspending a key that is not suspended is 409", A("POST", f"/api/keys/{kA['id']}/unsuspend")[0] == 409)

# ---------------------------------------------------------------- 2. new-IP warning
kC = mkkey(A, "newip")
for ip in ("203.0.113.21", "203.0.113.22", "203.0.113.23"):
    use(kC["key"], ip)
n0 = int(sql(f"select count(*) from security_events where key_id='{kC['id']}' and kind='new_ip'") or 0)
use(kC["key"], "203.0.113.24")
n1 = int(sql(f"select count(*) from security_events where key_id='{kC['id']}' and kind='new_ip'") or 0)
check("a 4th (new) IP raises a warn event, the first three do not", n0 == 0 and n1 == 1, (n0, n1))
check("the key is NOT suspended by a single new IP", [k for k in A("GET", "/api/keys")[1] if k["id"] == kC["id"]][0]["status"] == "active")

# ---------------------------------------------------------------- 3. allow-list
st, kB = A("POST", "/api/keys", {"name": "locked", "expires_in": "1d", "allowed_ips": "198.51.100.0/24, 2001:db8::/32, 192.0.2.99"})
check("key created with IPv4/IPv6/CIDR allow-list", st == 201 and kB["allowedIps"].count(",") == 2, kB)
check("invalid allow-list entry rejected (422)", A("POST", "/api/keys", {"name": "bad", "allowed_ips": "not-an-ip"})[0] == 422)
check("inside CIDR -> 200", use(kB["key"], "198.51.100.7")[0] == 200)
check("IPv6 range -> 200", use(kB["key"], "2001:db8::5")[0] == 200)
check("exact IP -> 200", use(kB["key"], "192.0.2.99")[0] == 200)
st, body, _h = use(kB["key"], "203.0.113.99")
check("outside the list -> 403 with a clear message", st == 403 and "allow" in body.lower(), (st, body))
check("blocked attempt is logged as ip_blocked", int(sql(f"select count(*) from security_events where key_id='{kB['id']}' and kind='ip_blocked'") or 0) >= 1)
st, p = A("PATCH", f"/api/keys/{kB['id']}", {"allowed_ips": ""})
check("allow-list can be cleared with PATCH", st == 200 and p["allowedIps"] == "" and use(kB["key"], "203.0.113.99")[0] == 200, p)
check("PATCH with a bad entry -> 422", A("PATCH", f"/api/keys/{kB['id']}", {"allowed_ips": "300.1.1.1"})[0] == 422)

# ---------------------------------------------------------------- 4. dead keys (revoked / expired) are logged
kD = mkkey(A, "to-revoke")
A("DELETE", f"/api/keys/{kD['id']}")
st, body, _h = use(kD["key"], "192.0.2.70")
check("revoked key -> 401", st == 401)
kE = mkkey(A, "to-expire")
sql(f"update api_keys set expires_at = now() - interval '1 minute' where id='{kE['id']}'")
check("expired key -> 401", use(kE["key"], "192.0.2.71")[0] == 401)
check("both attempts logged as dead_key_used", int(sql(f"select count(*) from security_events where tenant_id='{TA}' and kind='dead_key_used'") or 0) >= 2)

# ---------------------------------------------------------------- 5. 401 storm -> IP blocked
redis("DEL", "secd:storm:192.0.2.50", "v1fail:192.0.2.50", "v1ip:192.0.2.50")  # earlier runs
bad = "cf_live_" + "x" * 43
good = mkkey(B, "bystander")
rs = direct([("GET", "/v1/instances", {"Authorization": f"Bearer {bad}{i}", "X-Forwarded-For": "192.0.2.50"}) for i in range(22)])
codes = [r[0] for r in rs]
check("first 20 wrong keys are 401, then the IP is blocked with 429", codes[:20] == [401] * 20 and 429 in codes[20:], codes)
blocked = [r for r in rs if r[0] == 429][-1]
check("429 carries Retry-After", any(k.lower() == "retry-after" for k in blocked[2]), blocked[2])
st = use(good["key"], "192.0.2.50")[0]
check("even a valid key is refused from the stormy IP", st == 429, st)
check("a different IP is unaffected", use(good["key"], "192.0.2.51")[0] == 200)
check("storm is visible to admins as an auth_storm event", int(sql("select count(*) from security_events where kind='auth_storm' and detail like '%192.0.2.50%'") or 0) == 1)
st, ov = AD("GET", "/api/admin/security/overview")
check("overview lists the storm IP", st == 200 and any(s["ip"] == "192.0.2.50" for s in ov["authStorms"]), ov.get("authStorms"))

# ---------------------------------------------------------------- 6. per-IP limiter 60/min, volume warning
kV = mkkey(B, "busy")
rs = direct([("GET", "/v1/instances", {"Authorization": f"Bearer {kV['key']}", "X-Forwarded-For": "192.0.2.60"}) for _ in range(66)])
codes = [r[0] for r in rs]
check("60 requests/min per IP: the 61st+ get 429 + Retry-After", codes[:60] == [200] * 60 and codes[60:] == [429] * 6 and "Retry-After" in {k.title(): v for k, v in rs[-1][2].items()}, codes[55:])
check("over 50 requests in an hour raised a volume_spike WARN but did not suspend", int(sql(f"select count(*) from security_events where key_id='{kV['id']}' and kind='volume_spike' and severity='warn'") or 0) == 1 and [k for k in B("GET", "/api/keys")[1] if k["id"] == kV["id"]][0]["status"] == "active")
hour = time.strftime("%Y%m%d%H", time.gmtime())
redis("SET", f"kh:{kV['id']}:{hour}", "5000")
st = use(kV["key"], "192.0.2.61")[0]
check("a huge hourly spike (>max(200, 10x baseline)) auto-suspends the key", st == 401 and [k for k in B("GET", "/api/keys")[1] if k["id"] == kV["id"]][0]["status"] == "suspended")

# ---------------------------------------------------------------- 7. telemetry flush + per-key fields
n = flush()
check("flush wrote api_key_usage rows", n.isdigit() and int(n) >= 1, n)
row = sql(f"select requests, last_ip, last_user_agent from api_key_usage where key_id='{kA['id']}' and day = current_date")
check("api_key_usage has requests, last_ip and user agent for the leaky key", row.startswith("8|") or (row and row.split("|")[0].isdigit() and "203.0.113" in row and "sec-test" in row), row)
st, ks = A("GET", "/api/keys")
r1 = [k for k in ks if k["id"] == kA["id"]][0]
check("owner key list: lastIp, lastUserAgent, requestsToday, allowedIps present", r1["lastIp"].startswith("203.0.113.") and r1["requestsToday"] >= 6 and "sec-test" in r1["lastUserAgent"] and "allowedIps" in r1, r1)
st, d = AD("GET", f"/api/admin/customers/{TA}")
k1 = [k for k in d["keys"] if k["id"] == kA["id"]][0]
check("admin customer detail: per-key requests7d, distinctIps7d, lastIp, status", k1["requests7d"] >= 6 and k1["distinctIps7d"] >= 6 and k1["lastIp"] and k1["status"] == "active", k1)
check("admin customer detail: AI usage today/limit", "aiToday" in d["usage"] and "aiLimit" in d["usage"], d["usage"])

# ---------------------------------------------------------------- 8. admin suspend / unsuspend / lock
st, _r = AD("POST", f"/api/admin/security/keys/{kC['id']}/suspend", {"reason": "manual review"})
check("admin can suspend a key", st == 200 and use(kC["key"], "203.0.113.21")[0] == 401)
st, r = A("POST", f"/api/keys/{kC['id']}/unsuspend")
check("owner cannot unsuspend an admin-suspended key (403)", st == 403, r)
st, _r = AD("POST", f"/api/admin/security/keys/{kC['id']}/unsuspend")
check("admin unsuspend restores it", st == 200 and use(kC["key"], "203.0.113.21")[0] == 200)
st, evl = AD("GET", f"/api/admin/security/events?tenant_id={TA}&resolved=false&page=1")
eid = evl["rows"][0]["id"]
check("admin resolves an event", AD("POST", f"/api/admin/security/events/{eid}/resolve")[0] == 200 and AD("GET", f"/api/admin/security/events?tenant_id={TA}&resolved=true")[1]["total"] >= 1)
check("lock on a platform-admin workspace is refused (409)", AD("POST", "/api/admin/security/tenants/" + sql("select tenant_id from memberships m join users u on u.id=m.user_id where u.is_platform_admin limit 1") + "/lock")[0] == 409)

# ---------------------------------------------------------------- 9. rotate
kR = mkkey(A, "rotating", allowed_ips="198.51.100.0/24")
st, nk = A("POST", f"/api/keys/{kR['id']}/rotate")
check("rotate returns a new plaintext key with the same name and allow-list", st == 201 and nk["key"].startswith("cf_live_") and nk["key"] != kR["key"] and nk["name"] == "rotating" and nk["allowedIps"] == "198.51.100.0/24", nk)
check("old key is dead, new key works", use(kR["key"], "198.51.100.5")[0] == 401 and use(nk["key"], "198.51.100.5")[0] == 200)

# ---------------------------------------------------------------- 10. isolation + admin-only
check("tenant B cannot patch / unsuspend / rotate tenant A's key (404)", all(B(m, f"/api/keys/{kA['id']}" + s_, {} if m == "PATCH" else None)[0] == 404 for m, s_ in (("PATCH", ""), ("POST", "/unsuspend"), ("POST", "/rotate"))))
check("tenant B's key list has none of A's keys", all(k["id"] != kA["id"] for k in B("GET", "/api/keys")[1]))
check("customers get 403 on every /api/admin/security endpoint", all(B(m, p)[0] == 403 for m, p in (("GET", "/api/admin/security/overview"), ("GET", "/api/admin/security/events"), ("POST", f"/api/admin/security/keys/{kA['id']}/suspend"), ("POST", f"/api/admin/security/tenants/{TA}/lock"))))
check("anonymous gets 401 on the security API", sess("admin.connectflow.localhost:8080")("GET", "/api/admin/security/overview")[0] == 401)
st, lk = AD("POST", f"/api/admin/security/tenants/{TB}/lock")
check("admin can lock a workspace (suspends keys + workspace)", st == 200 and lk["keysSuspended"] >= 1 and sql(f"select status from tenants where id='{TB}'") == "suspended", lk)

# ---------------------------------------------------------------- 11. fail-open when Redis is down
code = ("import app.telemetry as t\nclass Dead:\n    def __getattr__(self, n):\n        raise ConnectionError('redis down')\n"
        "t.rds = Dead()\nfrom app.db import SessionLocal\nfrom app.models import ApiKey\n"
        "with SessionLocal() as db:\n    k = db.query(ApiKey).first()\n    print('RES', t.record_use(db, k, '1.2.3.4', 'x'), t._pre_auth('1.2.3.4'), t.record_event('info','x','y',dedupe='d'))\n")
out = dc("exec", "-T", "api", "python", "-c", code)
check("telemetry fails OPEN when Redis is unreachable (no exception, not blocked)", "RES None 0" in out, out[-300:])

# ---------------------------------------------------------------- 12. AI cost control
C, TC = setup(f"SEC Gamma {STAMP}", f"sec-c-{STAMP}@example.com", plan="trial")
created.append(TC)
st, plans = AD("GET", "/api/admin/plans")
trial = [p for p in plans if p["id"] == "trial"][0]
check("trial plan has aiCallsDay (default 20) in the admin plans API", trial["aiCallsDay"] >= 1, trial)
st, bil = C("GET", "/api/billing")
check("customer billing shows AI usage today and the plan's AI limit", st == 200 and "aiCallsToday" in bil["usage"] and "aiCallsDay" in bil["plan"], bil)

def set_cap(n):
    body = {k: trial[k] for k in ("name", "priceUsd", "pricePkr", "maxInstances", "maxMsgsDay", "maxMembers")}
    return AD("PUT", "/api/admin/plans/trial", {**body, "aiCallsDay": n})

set_cap(500)
IID, SID = "sec-" + STAMP + "-i", "sess-sec-" + STAMP
sql(f"insert into instances (id,tenant_id,label,node_id,engine,openwa_name,openwa_id,phone,status,created_at) values ('{IID}','{TC}','Sales','wa1','baileys','cf-sec-{STAMP}','{SID}','923009990001','ready',now())")
rows = ["Company,Phone,Website,Business Type"] + [f"Sec {i},+92300555{1000 + i},sec{i}.example,Clinic" for i in range(6)]
st, an = C("POST", "/api/leads/import/analyze", {"csv": "\n".join(rows)})
C("POST", "/api/leads/import", {"csv": "\n".join(rows), "mapping": an["mapping"], "list_name": "sec"})
st, pr = C("GET", "/api/outreach/profile")
C("PUT", "/api/outreach/profile", {**pr, "sender_name": "Sam", "company_name": "Sec Co", "offer": "We test", "batch_size": 1, "daily_limit": 5, "delay_min": 1, "delay_max": 1, "timezone": "UTC", "active_from": "00:00", "active_to": "23:59"})
st, r = C("POST", "/api/outreach/run-now", {})
check("setup: one draft written with the fake AI", st == 200 and r["queued"] == 1, r)
used = int(sql(f"select count(*) from usage_events where tenant_id='{TC}' and kind='ai'") or 0)
check("every AI call was counted as a usage event (kind='ai')", used >= 1, used)
st, dr = C("GET", "/api/drafts?status=open")
did = dr["rows"][0]["id"]
set_cap(used + 1)
st1, _r = C("POST", f"/api/drafts/{did}/regenerate", {"instruction": ""})
st2, r2 = C("POST", f"/api/drafts/{did}/regenerate", {"instruction": ""})
check("with a cap of N, call N succeeds and call N+1 is blocked with 429 and a clear message", st1 == 200 and st2 == 429 and "Daily AI limit reached" in json.dumps(r2) and "trial" in json.dumps(r2).lower(), (st1, st2, r2))
st, r = C("POST", "/api/outreach/run-now", {})
check("a batch over the cap does not crash: leads fail softly and stay untouched", st == 200 and r["queued"] == 0, r)
check("blocked calls are not counted (usage stays at the cap)", int(sql(f"select count(*) from usage_events where tenant_id='{TC}' and kind='ai'") or 0) == used + 1)
st, bil = C("GET", "/api/billing")
check("billing reflects AI used = limit", bil["usage"]["aiCallsToday"] == bil["plan"]["aiCallsDay"] == used + 1, bil)
set_cap(0)
check("cap 0 turns AI off for the plan (429)", C("POST", f"/api/drafts/{did}/regenerate", {"instruction": ""})[0] == 429)
set_cap(trial["aiCallsDay"])

# circuit breaker
glob = dc("exec", "-T", "api", "printenv", "AI_GLOBAL_DAILY_CAP").strip()
if glob.isdigit() and int(glob) > 0:
    set_cap(500)
    day = time.strftime("%Y%m%d", time.gmtime())
    redis("SET", f"ai:day:{day}", str(int(glob)))
    st, r = C("POST", f"/api/drafts/{did}/regenerate", {"instruction": ""})
    check("global circuit breaker: over the platform cap -> 503 'AI is temporarily unavailable'", st == 503 and "temporarily unavailable" in json.dumps(r), (st, r))
    C("POST", f"/api/drafts/{did}/regenerate", {"instruction": ""})
    check("exactly one admin warn event for the breaker", int(sql("select count(*) from security_events where kind='ai_breaker' and at > now() - interval '5 minutes'") or 0) == 1)
    redis("DEL", f"ai:breaker:{day}", f"ai:day:{day}")
    set_cap(trial["aiCallsDay"])
else:
    skip("AI circuit breaker", "set AI_GLOBAL_DAILY_CAP>0 for the api container to run it")

# ---------------------------------------------------------------- cleanup
for t in created:
    AD("DELETE", f"/api/admin/customers/{t}")
sql("delete from security_events where tenant_id is null and (detail like '%192.0.2.%' or kind in ('ai_breaker'))")
redis("DEL", "v1fail:192.0.2.50", "v1ip:192.0.2.50", "v1ip:192.0.2.60", "secd:storm:192.0.2.50")
print(f"\n{ok_n} passed, {bad_n} failed, {skip_n} skipped")
sys.exit(1 if bad_n else 0)
