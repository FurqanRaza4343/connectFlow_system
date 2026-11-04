import os, base64, hashlib, hmac, http.cookiejar, json, re, subprocess, sys, time, urllib.request, urllib.error
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA, require_admin_password, require_fake_engine, require_mailpit  # noqa: F401
MAILPIT = os.environ.get("E2E_MAILPIT_URL", "http://127.0.0.1:8025")
require_mailpit()
require_admin_password()

HOST = {"Host": APP_HOST, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}
ok_n = bad_n = 0


def sess():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(method, path, body=None, headers=None):
        req = urllib.request.Request(BASE + path, method=method, headers={**HOST, **(headers or {})}, data=json.dumps(body).encode() if body is not None else None)
        try:
            r = op.open(req, timeout=60)
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw[:200].decode(errors="ignore")}
    return call


def check(name, cond, extra=""):
    global ok_n, bad_n
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{extra}]" if extra and not cond else ""))
    cond = bool(cond)
    ok_n += cond
    bad_n += (not cond)


def sql(q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def mail_link(to, key):
    """Newest Mailpit message to `to`, return the value of ?key=... from its text body."""
    for _ in range(20):
        r = urllib.request.urlopen(MAILPIT + "/api/v1/messages?limit=50").read()
        for m in json.loads(r)["messages"]:
            if any(t["Address"] == to for t in m["To"]):
                body = json.loads(urllib.request.urlopen(MAILPIT + f"/api/v1/message/{m['ID']}").read())["Text"]
                mt = re.search(rf"[?&]{key}=([\w-]+)", body)
                if mt:
                    return mt.group(1)
        time.sleep(0.5)
    return None


def admin_login():
    c = sess()
    pw = require_admin_password()
    st, _ = c("POST", "/api/auth/login", {"email": "admin@connectflow.example.com", "password": pw})
    assert st == 200, st
    return c


stamp = str(int(time.time()))
EA, EB = f"m2a-{stamp}@example.com", f"m2b-{stamp}@example.com"
PW = "Sup3rSecretPw!"

# ---------------- email verification ----------------
A = sess()
st, me = A("POST", "/api/auth/signup", {"email": EA, "password": PW, "name": "Aisha", "company": "M2 Alpha"})
check("signup ok, starts unverified", st == 200 and me["user"]["emailVerified"] is False)
st, r = A("POST", "/api/keys", {"name": "x"})
check("API key blocked until email confirmed (403)", st == 403, r)
tok = mail_link(EA, "verify")
check("verification email delivered (Mailpit)", bool(tok))
st, _ = A("POST", "/api/auth/verify", {"token": tok})
check("verify link works", st == 200)
st, _ = A("POST", "/api/auth/verify", {"token": tok})
check("verify link is single-use (410)", st == 410)
st, me = A("GET", "/api/auth/me")
check("/me shows verified", me["user"]["emailVerified"] is True)

# ---------------- password reset ----------------
anon = sess()
st, _ = anon("POST", "/api/auth/forgot", {"email": "nobody-here@example.com"})
check("forgot: unknown email answers 200 (no account enumeration)", st == 200)
st, _ = anon("POST", "/api/auth/forgot", {"email": EA})
rt = mail_link(EA, "reset")
check("reset email delivered", bool(rt))
st, r = anon("POST", "/api/auth/reset", {"token": rt, "password": "short"})
check("weak password rejected (422)", st == 422)
st, _ = anon("POST", "/api/auth/reset", {"token": rt, "password": "N3wPassw0rd!!"})
check("password reset works", st == 200)
st, _ = anon("POST", "/api/auth/reset", {"token": rt, "password": "An0therPassw0rd!"})
check("reset link is single-use (410)", st == 410)
c2 = sess()
check("old password no longer works", c2("POST", "/api/auth/login", {"email": EA, "password": PW})[0] == 401)
check("new password works", c2("POST", "/api/auth/login", {"email": EA, "password": "N3wPassw0rd!!"})[0] == 200)
A = c2

# ---------------- branding ----------------
check("invalid colour rejected", A("PUT", "/api/branding/color", {"color": "blue"})[0] == 422)
check("valid colour saved", A("PUT", "/api/branding/color", {"color": "#2563EB"})[0] == 200)
png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()
check("PNG logo accepted", A("PUT", "/api/branding/logo", {"dataUrl": "data:image/png;base64," + png})[0] == 200)
fake = base64.b64encode(b"this is not an image").decode()
check("text disguised as PNG rejected", A("PUT", "/api/branding/logo", {"dataUrl": "data:image/png;base64," + fake})[0] == 422)
svg = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>").decode()
check("SVG with <script> rejected", A("PUT", "/api/branding/logo", {"dataUrl": "data:image/svg+xml;base64," + svg})[0] == 422)
big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 300000).decode()
check("oversized logo rejected (413)", A("PUT", "/api/branding/logo", {"dataUrl": "data:image/png;base64," + big})[0] == 413)
st, me = A("GET", "/api/auth/me")
check("/me returns saved brand", me["tenant"]["brand"]["color"] == "#2563eb" and (me["tenant"]["brand"]["logo"] or "").startswith("data:image/png"))
check("reset to default colour", A("PUT", "/api/branding/color", {"color": None})[0] == 200)

# ---------------- billing / upgrade request ----------------
st, b = A("GET", "/api/billing")
check("billing shows trial + 3 plans with prices", b["plan"]["id"] == "trial" and len(b["plans"]) == 3 and b["plans"][1]["priceUsd"] == 19)
st, _ = A("POST", "/api/billing/upgrade", {"plan_id": "growth", "note": "need team seats"})
check("upgrade request created", st == 201)
check("second pending request refused (409)", A("POST", "/api/billing/upgrade", {"plan_id": "starter"})[0] == 409)
AD = admin_login()
st, reqs = AD("GET", "/api/admin/upgrade-requests")
mine = [r for r in reqs if r["tenant"] == "M2 Alpha" and r["status"] == "pending"]
check("admin sees the request", len(mine) == 1)
check("non-admin cannot list requests (403)", A("GET", "/api/admin/upgrade-requests")[0] == 403)
check("admin approves", AD("POST", f"/api/admin/upgrade-requests/{mine[0]['id']}/approve")[0] == 200)
st, b = A("GET", "/api/billing")
check("plan is now growth", b["plan"]["id"] == "growth")

# ---------------- team invites ----------------
st, _ = A("POST", "/api/team/invites", {"email": EB, "role": "operator"})
check("invite sent (growth plan has seats)", st == 201)
it = mail_link(EB, "invite")
check("invite email delivered", bool(it))
anon = sess()
st, pv = anon("GET", f"/api/team/invites/preview?token={it}")
check("invite preview shows workspace + role", st == 200 and pv["workspace"] == "M2 Alpha" and pv["role"] == "operator")
st, r = anon("POST", "/api/team/invites/accept", {"token": it, "name": "Bilal", "password": "short"})
check("accepting with weak password refused (422)", st == 422)
B = sess()
st, me = B("POST", "/api/team/invites/accept", {"token": it, "name": "Bilal", "password": PW})
check("invite accepted, joins as operator, already verified", st == 200 and me["role"] == "operator" and me["user"]["emailVerified"] is True, me)
check("invite is single-use (410)", sess()("POST", "/api/team/invites/accept", {"token": it, "name": "x", "password": PW})[0] == 410)
check("operator cannot create API keys (403)", B("POST", "/api/keys", {"name": "x"})[0] == 403)
check("operator cannot open the team list (403)", B("GET", "/api/team")[0] == 403)
st, t = A("GET", "/api/team")
bil = [m for m in t["members"] if m["email"] == EB][0]
check("owner sees 2 members", len(t["members"]) == 2)
check("owner changes role to viewer", A("PATCH", f"/api/team/members/{bil['id']}", {"role": "viewer"})[0] == 200)
check("viewer cannot add numbers (403)", B("POST", "/api/instances", {"label": "x"})[0] == 403)
check("owner removes member", A("DELETE", f"/api/team/members/{bil['id']}")[0] == 204)
check("removed member loses access (403/401)", B("GET", "/api/instances")[0] in (401, 403))

# ---------------- webhooks ----------------
st, ep = A("POST", "/api/webhooks", {"url": "http://cf-recv:9000/ok", "description": "ok receiver", "events": ["message.received", "instance.status"]})
check("endpoint created, secret returned once", st == 201 and ep["secret"].startswith("whsec_"), ep)
st, ep2 = A("POST", "/api/webhooks", {"url": "http://cf-recv:9000/flaky", "events": ["instance.status"]})
st, lst = A("GET", "/api/webhooks")
check("list does not leak secrets", all("secret" not in e for e in lst["endpoints"]))
check("unknown events rejected (422)", A("POST", "/api/webhooks", {"url": "http://cf-recv:9000/x", "events": ["nope"]})[0] == 422)
A("POST", f"/api/webhooks/{ep['id']}/test")
A("POST", f"/api/webhooks/{ep2['id']}/test")
time.sleep(6)
st, dels = A("GET", "/api/webhooks/deliveries")
d_ok = [d for d in dels if d["endpointId"] == ep["id"]]
d_fl = [d for d in dels if d["endpointId"] == ep2["id"]]
check("test event delivered (success)", bool(d_ok) and d_ok[0]["status"] == "success", d_ok)
check("flaky endpoint: first attempt 500 -> retrying with a next attempt time", bool(d_fl) and d_fl[0]["status"] == "retrying" and d_fl[0]["lastStatus"] == 500 and d_fl[0]["nextAttemptAt"], d_fl)
# signature verification exactly as documented
logs = subprocess.run(["docker", "logs", "cf-recv"], capture_output=True, text=True).stdout.splitlines()
rec = [json.loads(l) for l in logs if l.startswith("{")]
good = False
for r in rec:
    if r["path"] == "/ok":
        h = {k.lower(): v for k, v in r["headers"].items()}
        calc = "sha256=" + hmac.new(ep["secret"].encode(), f"{h['x-connectflow-timestamp']}.{r['body']}".encode(), hashlib.sha256).hexdigest()
        good = hmac.compare_digest(calc, h["x-connectflow-signature"]) and json.loads(r["body"])["event"] == "ping"
check("signature verifies with the documented formula (HMAC of timestamp.body)", good)
# retry-now
A("POST", f"/api/webhooks/deliveries/{d_fl[0]['id']}/retry")
time.sleep(5)
st, dels = A("GET", "/api/webhooks/deliveries")
check("manual retry then succeeds", [d for d in dels if d["id"] == d_fl[0]["id"]][0]["status"] == "success")
# other tenant cannot see these
other = sess(); other("POST", "/api/auth/signup", {"email": f"m2c-{stamp}@example.com", "password": PW, "company": "M2 Other"})
check("other tenant sees no webhooks", other("GET", "/api/webhooks")[0] == 403 or other("GET", "/api/webhooks")[1].get("endpoints") == [])
check("other tenant cannot test our endpoint (404/403)", other("POST", f"/api/webhooks/{ep['id']}/test")[0] in (403, 404))

# ---------------- instance.status event via the gateway ----------------
sql(f"update tenants set status='active' where name='M2 Alpha'")
st, inst = A("POST", "/api/instances", {"label": "evt"})
check("number added (verified account)", st == 201, inst)
time.sleep(12)
ev = sql(f"select count(*) from webhook_deliveries d join webhook_endpoints e on e.id=d.endpoint_id where d.event='instance.status' and e.url like '%/ok'")
check("instance.status events were emitted from the gateway", int(ev or 0) >= 1, ev)
A("DELETE", f"/api/instances/{inst['id']}")

print(f"\n{ok_n} passed, {bad_n} failed")
sys.exit(1 if bad_n else 0)
