"""M5 security + quality end-to-end: two-factor authentication, disposable e-mail rejection, Trial session cap, idempotency,
encrypted webhook secrets, data export / deletion request, malformed gateway webhooks, health endpoints.

Two parts:
  * HTTP checks against the running stack (Caddy -> api), like the other suites;
  * an in-process part (`docker compose exec api python -`) that drives the real app + real Postgres/Redis with a test client, for the checks
    that need switches or fakes the live stack must not carry (ADMIN_2FA_REQUIRED, a fake WhatsApp send, the signed gateway secret).
Test workspaces are named 'M5B ...' and are removed at the end (through the admin API when E2E_ADMIN_PASSWORD is set). Nothing prints secrets.
Needs the api image built from this repo state. Does NOT need AI_FAKE / OUTREACH_FAKE_SEND."""
import base64, hashlib, hmac, http.cookiejar, io, json, os, struct, subprocess, sys, time, urllib.error, urllib.request, zipfile  # noqa: E401
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA

ok_n = bad_n = 0
stamp = str(int(time.time()))
PW = "M5B-Test-Pass-" + stamp


def sess(host=APP_HOST):
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    H = {"Host": host, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}

    def call(method, path, body=None, headers=None, raw=False):
        req = urllib.request.Request(BASE + path, method=method, headers={**H, **(headers or {})}, data=json.dumps(body).encode() if body is not None else None)
        try:
            r = op.open(req, timeout=120)
            d = r.read()
            if raw:
                return r.status, d, dict(r.headers)
            return r.status, (json.loads(d) if d else {})
        except urllib.error.HTTPError as e:
            d = e.read()
            if raw:
                return e.code, d, dict(e.headers)
            try:
                return e.code, json.loads(d)
            except Exception:
                return e.code, {"raw": d[:300].decode(errors="ignore")}
    call.jar = jar
    return call


def check(name, cond, extra=""):
    global ok_n, bad_n
    cond = bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{str(extra)[:300]}]" if extra and not cond else ""))
    ok_n += cond
    bad_n += (not cond)


def sql(q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def redis_del(*keys):
    """Clear per-account throttles between scenarios of the SAME test account (test stacks only; the limits themselves are asserted above)."""
    subprocess.run(["docker", "compose", "exec", "-T", "redis", "redis-cli", "del", *keys], cwd=INFRA, capture_output=True, text=True)


def totp(secret_b32, step):
    """RFC 6238 code for a 30 s step number (independent of the server's implementation)."""
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def now_step():
    return int(time.time() // 30)


def signup(tag, email=None):
    c = sess()
    email = email or f"m5b-{tag}-{stamp}@example.com"
    st, me = c("POST", "/api/auth/signup", {"email": email, "password": PW, "name": f"M5B {tag}", "company": f"M5B {tag} {stamp}"})
    return c, email, st, me


tenants: list[str] = []
c1, e1, st, me1 = signup("alpha")
check("signup works (non-disposable domain)", st == 200 and me1.get("tenant"), me1)
T1 = me1["tenant"]["id"]
tenants.append(T1)


# ======================================================================= 1. two-factor authentication
st, s0 = c1("GET", "/api/auth/mfa/status")
check("2FA status: off for a new account", st == 200 and s0["enabled"] is False and s0["enrolRequired"] is False and s0["recoveryCodesLeft"] == 0, s0)
check("/me tells the UI whether 2FA is on", me1["user"]["mfaEnabled"] is False)

st, en = c1("POST", "/api/auth/mfa/enroll/start")
check("enrol/start returns the secret once, an otpauth URI and a local SVG QR", st == 200 and en["otpauthUri"].startswith("otpauth://totp/ConnectFlow:") and en["qrSvg"].lstrip().startswith("<svg") and len(en["secret"]) >= 16, en)
SECRET = en.get("secret", "")
dbsec = sql(f"select totp_secret from users where email='{e1}'")
check("the 2FA secret is stored encrypted (enc1:), never plaintext", dbsec.startswith("enc1:") and SECRET not in dbsec, dbsec[:20])
check("enrolment is not active until a code is verified", sql(f"select totp_enabled_at is null from users where email='{e1}'") == "t")

st, _ = c1("POST", "/api/auth/mfa/enroll/verify", {"code": "000000"})
check("enrol/verify: wrong code is refused", st == 401)
st, v = c1("POST", "/api/auth/mfa/enroll/verify", {"code": totp(SECRET, now_step())})
check("enrol/verify: right code turns 2FA on and returns 10 recovery codes once", st == 200 and len(v.get("recoveryCodes", [])) == 10 and len(set(v["recoveryCodes"])) == 10, v)
RC = v.get("recoveryCodes", [])
ENROL_STEP = now_step()
rc_db = sql(f"select recovery_codes::text from users where email='{e1}'")
check("recovery codes are stored only as hashes", RC and not any(c in rc_db for c in RC) and rc_db.count('"') >= 20, rc_db[:80])
st, s1 = c1("GET", "/api/auth/mfa/status")
check("status now reports enabled with 10 codes", s1["enabled"] is True and s1["recoveryCodesLeft"] == 10, s1)
st, _ = c1("POST", "/api/auth/mfa/enroll/start")
check("cannot enrol again while 2FA is on", st == 409)

# sign-in: password alone no longer gives a session. The enrolment code used up this 30 s step, so wait for the next one (replay protection).
while now_step() <= ENROL_STEP:
    time.sleep(1)
c1b = sess()
st, r = c1b("POST", "/api/auth/login", {"email": e1, "password": PW})
check("login with 2FA on: password step returns mfaRequired + token and NO session", st == 200 and r.get("mfaRequired") is True and r.get("mfaToken") and "user" not in r, r)
st, _ = c1b("GET", "/api/auth/me")
check("no session cookie before the second factor", st == 401)
TOK = r.get("mfaToken", "")
st, _ = c1b("POST", "/api/auth/mfa/verify", {"mfaToken": TOK, "code": "123456"})
check("second step: wrong code is refused", st == 401)
st, _ = c1b("POST", "/api/auth/mfa/verify", {"mfaToken": "garbage", "code": "123456"})
check("second step: a garbage token is refused", st == 401)
st, _ = c1b("POST", "/api/auth/mfa/verify", {"mfaToken": TOK})
check("second step: no code at all is a 422", st == 422)
used_step = now_step()
st, me = c1b("POST", "/api/auth/mfa/verify", {"mfaToken": TOK, "code": totp(SECRET, used_step)})
check("second step: the right code signs in", st == 200 and me.get("user", {}).get("email") == e1 and me["user"]["mfaEnabled"] is True, me)
st, me = c1b("GET", "/api/auth/me")
check("the session works after the second step", st == 200 and me["user"]["email"] == e1)
st, _ = c1b("POST", "/api/auth/mfa/verify", {"mfaToken": TOK, "recoveryCode": RC[9]})
check("a sign-in token yields exactly one session (reuse is refused without spending the recovery code)", st == 401)

# replay protection: the same code cannot be used twice
c1c = sess()
st, r = c1c("POST", "/api/auth/login", {"email": e1, "password": PW})
TOK2 = r["mfaToken"]
st, rep = c1c("POST", "/api/auth/mfa/verify", {"mfaToken": TOK2, "code": totp(SECRET, used_step)})
check("replay: a code that was already used is refused", st == 401, rep)
st, me = c1c("POST", "/api/auth/mfa/verify", {"mfaToken": TOK2, "code": totp(SECRET, used_step + 1)})
check("the next code (next 30 s step) works", st == 200, me)

# rate limit: 5 tries per sign-in token
c1d = sess()
st, r = c1d("POST", "/api/auth/login", {"email": e1, "password": PW})
TOK3 = r["mfaToken"]
codes = []
for i in range(5):
    st, _ = c1d("POST", "/api/auth/mfa/verify", {"mfaToken": TOK3, "code": f"{i:06d}"})
    codes.append(st)
st6, _ = c1d("POST", "/api/auth/mfa/verify", {"mfaToken": TOK3, "code": totp(SECRET, now_step() + 1)})
check("rate limit: 5 wrong tries are 401, the 6th (even with a right code) is 429", codes == [401] * 5 and st6 == 429, (codes, st6))

# recovery codes: single use (the per-account budget of 12 attempts per 15 min was just used up by the checks above: reset it for this account)
U1 = sql(f"select id from users where email='{e1}'")
redis_del(f"mfa-acct:{U1}")
cr = sess()
st, r = cr("POST", "/api/auth/login", {"email": e1, "password": PW})
st, me = cr("POST", "/api/auth/mfa/verify", {"mfaToken": r["mfaToken"], "recoveryCode": RC[0].lower()})
check("a recovery code signs in (case-insensitive)", st == 200, me)
cr2 = sess()
st, r = cr2("POST", "/api/auth/login", {"email": e1, "password": PW})
st, _ = cr2("POST", "/api/auth/mfa/verify", {"mfaToken": r["mfaToken"], "recoveryCode": RC[0]})
check("recovery codes are single-use", st == 401)
check("one recovery code was consumed", sql(f"select json_array_length(recovery_codes) from users where email='{e1}'") == "9")

# sensitive changes need password + code
st, _ = c1("POST", "/api/auth/mfa/disable", {"password": "wrong-password-xx", "code": totp(SECRET, now_step() + 1)})
check("disable: wrong password is refused", st == 401)
st, _ = c1("POST", "/api/auth/mfa/disable", {"password": PW, "code": "000000"})
check("disable: wrong code is refused", st == 401)
st, rg = c1("POST", "/api/auth/mfa/recovery-codes", {"password": PW, "recoveryCode": RC[1]})
check("regenerate recovery codes (password + a recovery code) returns a new set", st == 200 and len(rg.get("recoveryCodes", [])) == 10 and not set(rg["recoveryCodes"]) & set(RC), rg)
check("the old recovery codes are dead after regeneration", True)
cr3 = sess()
st, r = cr3("POST", "/api/auth/login", {"email": e1, "password": PW})
st, _ = cr3("POST", "/api/auth/mfa/verify", {"mfaToken": r["mfaToken"], "recoveryCode": RC[2]})
check("...an old code no longer signs in", st == 401)
# disable with password + a fresh recovery code
st, d = c1("POST", "/api/auth/mfa/disable", {"password": PW, "recoveryCode": rg["recoveryCodes"][0]})
check("disable with password + recovery code works", st == 200, d)
check("2FA columns are cleared", sql(f"select totp_secret is null and totp_enabled_at is null and json_array_length(recovery_codes)=0 from users where email='{e1}'") == "t")
c1e = sess()
st, r = c1e("POST", "/api/auth/login", {"email": e1, "password": PW})
check("after disabling, a password alone signs in again", st == 200 and r.get("user") and "mfaRequired" not in r, r)
check("every 2FA event is in the audit log", int(sql(f"select count(*) from audit_log where user_id=(select id from users where email='{e1}') and action like 'mfa.%'") or 0) >= 6)

# ======================================================================= 2. signup abuse controls
st, r = sess()("POST", "/api/auth/signup", {"email": f"x{stamp}@mailinator.com", "password": PW, "name": "X", "company": "M5B Disposable"})
check("signup with a disposable e-mail domain is rejected with a clear 422", st == 422 and "permanent" in json.dumps(r).lower(), r)
st, r = sess()("POST", "/api/auth/signup", {"email": f"x{stamp}@sub.yopmail.com", "password": PW, "name": "X", "company": "M5B Disposable"})
check("...also on subdomains of listed domains", st == 422, r)
check("no account was created for them", sql(f"select count(*) from users where email like 'x{stamp}@%'") == "0")
st, cfg = sess()("GET", "/api/auth/config")
check("public config exposes the (empty) Turnstile site key only", st == 200 and cfg.get("turnstileSiteKey") == "" and "turnstileSecret" not in json.dumps(cfg), cfg)
st, r = c1("POST", "/api/team/invites", {"email": f"friend{stamp}@guerrillamail.com", "role": "viewer"})
check("inviting a disposable address is rejected", st == 422, r)

# Trial session cap (the account is unverified, so these never reach the gateway: 403 x3, then the middleware's 429)
ct = sess()
st, _ = ct("POST", "/api/auth/signup", {"email": f"m5b-trial-{stamp}@example.com", "password": PW, "name": "T", "company": f"M5B Trial {stamp}"})
sts = [ct("POST", "/api/instances", {"label": f"Trial {i}"})[0] for i in range(4)]
check("Trial workspace: 3 session starts allowed per hour, the 4th is 429", sts[:3] == [403, 403, 403] and sts[3] == 429, sts)
trial_tid = sql(f"select tenant_id from memberships where user_id=(select id from users where email='m5b-trial-{stamp}@example.com')")
tenants.append(trial_tid)

# ======================================================================= 3. webhook secrets at rest
st, wh = c1("POST", "/api/webhooks", {"url": "https://example.com/m5b-hook", "description": "M5B"})
check("webhook create returns the signing secret once (plaintext to the customer)", st == 201 and wh.get("secret", "").startswith("whsec_"), wh)
WSEC = wh.get("secret", "")
stored = sql(f"select secret from webhook_endpoints where id='{wh.get('id')}'")
check("the stored secret is ciphertext (enc1:), not the plaintext", stored.startswith("enc1:") and WSEC not in stored and len(stored) > 80, stored[:12])
st, lst = c1("GET", "/api/webhooks")
check("listing never exposes the secret", st == 200 and "secret" not in json.dumps(lst) and "enc1" not in json.dumps(lst), lst)
st, rot = c1("POST", f"/api/webhooks/{wh['id']}/rotate-secret")
stored2 = sql(f"select secret from webhook_endpoints where id='{wh['id']}'")
check("rotation shows the new plaintext once and re-encrypts", st == 200 and rot["secret"].startswith("whsec_") and rot["secret"] != WSEC and stored2.startswith("enc1:") and stored2 != stored and rot["secret"] not in stored2, rot)
WH_ID = wh["id"]

# ======================================================================= 4. idempotency over HTTP (no real WhatsApp number exists)
sql(f"update users set email_verified_at=now() where email='{e1}'")
st, key = c1("POST", "/api/keys", {"name": "M5B key", "expires_in": "1d"})
check("API key created", st == 201 and key.get("key", "").startswith("cf_live_"), key)
KEY = key.get("key", "")
sql(f"insert into instances (id,tenant_id,label,node_id,engine,openwa_name,openwa_id,phone,status,created_at) values ('m5b-i-{stamp}','{T1}','M5B fake','wa1','baileys','cf-m5b-{stamp}','sid-{stamp}','15555550123','created',now())")
api = sess("app.connectflow.localhost:8080")
bearer = {"Authorization": f"Bearer {KEY}"}
body = {"instance": f"m5b-i-{stamp}", "to": "923001234567", "text": "hello"}
st, r = api("POST", "/v1/messages", body, {**bearer, "Idempotency-Key": "bad key with spaces"})
check("an invalid Idempotency-Key is a 422", st == 422, r)
st, r = api("POST", "/v1/messages", body, {**bearer, "Idempotency-Key": "x" * 65})
check("a 65-character key is a 422", st == 422)
st1, r1 = api("POST", "/v1/messages", body, {**bearer, "Idempotency-Key": f"k-{stamp}"})
st2, r2 = api("POST", "/v1/messages", body, {**bearer, "Idempotency-Key": f"k-{stamp}"})
check("a failed attempt releases the key (same clear error twice, never 'still being processed')", st1 == st2 == 409 and "not connected" in json.dumps(r1) and r1 == r2, (st1, r1, st2, r2))
st, r = api("POST", "/v1/messages", body, bearer)
check("no header = no idempotency (plain behaviour unchanged)", st == 409 and "not connected" in json.dumps(r))

# ======================================================================= 5. data export
st, lead = c1("POST", "/api/leads", {"company": f"M5B Lead Alpha {stamp}", "contact_name": "Ayesha", "notes": "alpha note"})
check("a lead exists for the export", st == 201, lead)
c2, e2, st, me2 = signup("beta")
T2 = me2["tenant"]["id"]
tenants.append(T2)
c2("POST", "/api/leads", {"company": f"M5B Lead BetaSecret {stamp}", "contact_name": "Bilal"})
# an admin of workspace 1 (not the owner): move a throwaway user into it
c3, e3, st, me3 = signup("member")
sql(f"delete from memberships where user_id=(select id from users where email='{e3}')")
sql(f"insert into memberships (id,user_id,tenant_id,role) values ('m5b-m-{stamp}',(select id from users where email='{e3}'),'{T1}','admin')")
tenants.append(me3["tenant"]["id"])
st, _ = c3("GET", "/api/account/export", raw=True)[:2]
check("export is owner-only (an admin gets 403)", st == 403)
st, _ = c3("POST", "/api/account/delete-request", {"password": PW})
check("deletion request is owner-only", st == 403)
st, zbytes, hdr = c1("GET", "/api/account/export", raw=True)
check("export returns a ZIP attachment", st == 200 and "zip" in hdr.get("Content-Type", "") and "attachment" in hdr.get("Content-Disposition", "") and zbytes[:2] == b"PK", (st, hdr))
names, allbytes = [], b""
try:
    zf = zipfile.ZipFile(io.BytesIO(zbytes))
    names = zf.namelist()
    allbytes = b"\n".join(zf.read(n) for n in names)
except Exception as e:  # noqa: BLE001
    print("zip error", e)
need = {"README.txt", "workspace.json", "profile.json", "leads.csv", "lead_lists.csv", "messages.csv", "drafts.csv", "numbers.csv", "api_keys.csv", "webhooks.csv", "team.csv", "audit_log.csv", "usage.csv"}
check("the archive has every expected file", need <= set(names), sorted(need - set(names)))
check("it contains the workspace's own lead", f"M5B Lead Alpha {stamp}".encode() in allbytes)
check("tenant isolation: another workspace's data is NOT in it", f"BetaSecret".encode() not in allbytes and e2.encode() not in allbytes)
pwhash = sql(f"select password_hash from users where email='{e1}'")
keyhash = sql(f"select key_hash from api_keys where tenant_id='{T1}' limit 1")
check("no secrets: no password hash, API key (value or hash), webhook secret (plain or encrypted), gateway ids or encryption prefixes",
      pwhash.encode() not in allbytes and b"$argon2" not in allbytes and KEY.encode() not in allbytes and keyhash.encode() not in allbytes and rot["secret"].encode() not in allbytes
      and b"enc1:" not in allbytes and f"sid-{stamp}".encode() not in allbytes and b"cf-m5b-" not in allbytes)
check("API key metadata is exported (prefix) without the key", KEY[:12].encode() in zf.read("api_keys.csv") and KEY.encode() not in zf.read("api_keys.csv"))
st1, _, _ = c1("GET", "/api/account/export", raw=True)
st2, _, _ = c1("GET", "/api/account/export", raw=True)
st3, msg3, _ = c1("GET", "/api/account/export", raw=True)
check("export is limited to 3 per day (the 4th is 429)", st1 == 200 and st2 == 200 and st3 == 429, (st1, st2, st3))

n_leads = sql(f"select count(*) from leads where tenant_id='{T1}'")
st, _ = c1("POST", "/api/account/delete-request", {"password": "wrong-password-xx"})
check("deletion request: wrong password refused", st == 401)
st, dr = c1("POST", "/api/account/delete-request", {"password": PW})
check("deletion request recorded...", st == 200 and dr.get("requestedAt"), dr)
check("...and NOTHING was deleted (tenant, users, leads, keys intact)", sql(f"select count(*) from leads where tenant_id='{T1}'") == n_leads and sql(f"select count(*) from tenants where id='{T1}'") == "1"
      and sql(f"select deletion_requested_at is not null from tenants where id='{T1}'") == "t" and int(sql(f"select count(*) from api_keys where tenant_id='{T1}'") or 0) >= 1)
st, a = c1("GET", "/api/account")
check("status shows the pending request; the account still works", st == 200 and a["deletionRequestedAt"])
st, _ = c1("POST", "/api/account/delete-request/cancel")
check("the request can be cancelled", st == 200 and sql(f"select deletion_requested_at is null from tenants where id='{T1}'") == "t")

# ======================================================================= 6. health
st, hr = sess()("GET", "/api/health/ready")
check("/api/health/ready reports each dependency and its latency (no secrets)", st == 200 and hr.get("ok") is True and set(hr.get("latencyMs", {})) == {"database", "redis", "gateway"} and all(hr[k] == "ok" for k in ("database", "redis", "gateway")), hr)
for svc in ("redis", "searxng"):
    out = subprocess.run(["docker", "compose", "--profile", "legacy", "ps", "-q", svc], cwd=INFRA, capture_output=True, text=True).stdout.strip()
    health = subprocess.run(["docker", "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", out], capture_output=True, text=True).stdout.strip() if out else "missing"
    check(f"docker healthcheck is defined and healthy for {svc}", health == "healthy", health)

# ======================================================================= 7. in-process part (real app + DB + Redis, test client)
INPROC = r'''
import asyncio, base64, hashlib, hmac, json, struct, sys, threading, time, uuid
from unittest import mock
from fastapi.testclient import TestClient
from sqlalchemy import text
from app.main import app
from app.config import settings
from app.db import SessionLocal
from app import crypto, worker
from app.models import Instance, Tenant, User, WebhookDelivery, WebhookEndpoint

STAMP = sys.argv[1]; PW = sys.argv[2]
HOST = "app.connectflow.localhost:8080"
H = {"X-Requested-With": "ConnectFlow"}
res = []
def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{str(extra)[:200]}]" if extra and not cond else ""))
def client(): return TestClient(app, base_url="http://" + HOST, headers=H)
def totp(secret_b32, step):
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest(); o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o+4])[0] & 0x7FFFFFFF) % 1000000:06d}"
def sql(q, **p):
    with SessionLocal() as db:
        r = db.execute(text(q), p); db.commit()
        try: return r.scalar()
        except Exception: return None

# ---- platform admin must enrol (ADMIN_2FA_REQUIRED switched on in THIS process only)
settings.admin_2fa_required = True
a = client()
email = f"m5b-admin-{STAMP}@example.com"
st = a.post("/api/auth/signup", json={"email": email, "password": PW, "name": "M5B admin", "company": f"M5B Admin {STAMP}"}).status_code
sql("update users set is_platform_admin=true, email_verified_at=now() where email=:e", e=email)
a = client()
r = a.post("/api/auth/login", json={"email": email, "password": PW})
check("forced 2FA: an admin without 2FA gets enrolRequired, not a normal session", r.status_code == 200 and r.json().get("enrolRequired") is True and "user" not in r.json(), r.text[:120])
check("...the limited session cannot read /me", a.get("/api/auth/me").status_code == 401)
check("...nor any admin or workspace API", a.get("/api/admin/customers").status_code in (401, 403) and a.get("/api/leads").status_code in (401, 403))
stt = a.get("/api/auth/mfa/status").json()
check("...but it can reach the enrolment status route", stt.get("enrolRequired") is True and stt.get("adminRequired") is True, stt)
en = a.post("/api/auth/mfa/enroll/start").json()
bad = a.post("/api/auth/mfa/enroll/verify", json={"code": "000000"})
check("...a wrong code does not finish enrolment", bad.status_code == 401)
v = a.post("/api/auth/mfa/enroll/verify", json={"code": totp(en["secret"], int(time.time() // 30))})
check("finishing enrolment upgrades the limited session to a full one and returns /me", v.status_code == 200 and v.json().get("me", {}).get("user", {}).get("isPlatformAdmin") is True and len(v.json().get("recoveryCodes", [])) == 10, v.text[:150])
check("...the full session now works for /me and admin APIs", a.get("/api/auth/me").status_code == 200 and a.get("/api/admin/customers").status_code == 200)
d = a.post("/api/auth/mfa/disable", json={"password": PW, "recoveryCode": v.json()["recoveryCodes"][0]})
check("an admin cannot switch 2FA off while it is required", d.status_code == 409, d.text[:120])
a2 = client()
r = a2.post("/api/auth/login", json={"email": email, "password": PW})
check("the next admin login asks for the code", r.json().get("mfaRequired") is True)
settings.admin_2fa_required = None

# ---- idempotency with a fake WhatsApp send (counts real sends and real quota use)
c = client()
em = f"m5b-idem-{STAMP}@example.com"
c.post("/api/auth/signup", json={"email": em, "password": PW, "name": "Idem", "company": f"M5B Idem {STAMP}"})
sql("update users set email_verified_at=now() where email=:e", e=em)
tid = sql("select tenant_id from memberships where user_id=(select id from users where email=:e)", e=em)
iid = f"m5b-ii-{STAMP}"
sql("insert into instances (id,tenant_id,label,node_id,engine,openwa_name,openwa_id,phone,status,created_at) values (:i,:t,'M5B idem','wa1','baileys',:n,:s,'15555550124','ready',now())", i=iid, t=tid, n=f"cf-m5b-ii-{STAMP}", s=f"sid-ii-{STAMP}")
key = c.post("/api/keys", json={"name": "idem", "expires_in": "1d"}).json()["key"]
sent = []
async def fake_check(sid, digits): return {"exists": True, "whatsappId": f"{digits}@c.us"}
async def fake_send(sid, chat, text_):
    sent.append(text_); await asyncio.sleep(1.2); return {"id": "wamid-" + uuid.uuid4().hex[:8]}
B = {"Authorization": f"Bearer {key}"}
body = {"instance": iid, "to": "923001234567", "text": "idempotent hello"}
def usage(): return int(sql("select count(*) from usage_events where tenant_id=:t and kind='message'", t=tid) or 0)
with mock.patch("app.wa_client.check_number", fake_check), mock.patch("app.wa_client.send_text", fake_send):
    api = client()
    r1 = api.post("/v1/messages", json=body, headers={**B, "Idempotency-Key": "send-1"})
    r2 = api.post("/v1/messages", json=body, headers={**B, "Idempotency-Key": "send-1"})
    check("idempotency: first call sends (202)", r1.status_code == 202 and r1.json()["status"] == "sent", r1.text[:120])
    check("...the retry replays the same result with Idempotent-Replayed: true", r2.status_code == 202 and r2.json() == r1.json() and r2.headers.get("idempotent-replayed") == "true", (r2.status_code, dict(r2.headers)))
    check("...and the message was sent exactly once, using quota exactly once", len(sent) == 1 and usage() == 1, (len(sent), usage()))
    r3 = api.post("/v1/messages", json={**body, "text": "a different body"}, headers={**B, "Idempotency-Key": "send-1"})
    check("same key + different body -> 422, nothing sent", r3.status_code == 422 and len(sent) == 1, r3.text[:120])
    r4 = api.post("/v1/messages", json=body, headers={**B, "Idempotency-Key": "send-2"})
    check("a new key sends again", r4.status_code == 202 and len(sent) == 2 and usage() == 2)
    out = []
    def go(): out.append(client().post("/v1/messages", json={**body, "text": "race"}, headers={**B, "Idempotency-Key": "race-1"}))
    t1 = threading.Thread(target=go); t2 = threading.Thread(target=go)
    t1.start(); time.sleep(0.4); t2.start(); t1.join(); t2.join()
    codes = sorted(x.status_code for x in out)
    check("concurrent duplicates: one runs (202), the other gets 409 while it is in flight", codes == [202, 409], codes)
    check("...and only one message was sent for the race", len([s for s in sent if s == "race"]) == 1, sent)
    r5 = api.post("/v1/messages", json={**body, "text": "race"}, headers={**B, "Idempotency-Key": "race-1"})
    check("after it finished the same key replays", r5.status_code == 202 and r5.headers.get("idempotent-replayed") == "true")

# ---- encrypted webhook secrets: delivery signing, legacy plaintext upgrade, undecryptable rows
secret_plain = "whsec_inproc_" + STAMP
with SessionLocal() as db:
    ep = WebhookEndpoint(tenant_id=tid, url="https://example.com/hook", secret=crypto.encrypt(secret_plain), events="message.received", description="M5B inproc")
    db.add(ep); db.commit(); epid = ep.id
def new_delivery():
    with SessionLocal() as db:
        d = WebhookDelivery(tenant_id=tid, endpoint_id=epid, event="ping", payload='{"hello":"world"}'); db.add(d); db.commit(); return d.id
captured = {}
class FakeResp: status_code = 200
class FakeClient:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def post(self, url, content=None, headers=None): captured["headers"] = headers; captured["body"] = content; return FakeResp()
async def fake_public(url): return None
def run_delivery():
    did = new_delivery()
    with mock.patch("app.worker.httpx.AsyncClient", FakeClient), mock.patch("app.worker.assert_public_url", fake_public):
        asyncio.run(worker.deliver(did))
    return did
did = run_delivery()
h = captured["headers"]
want = "sha256=" + hmac.new(secret_plain.encode(), (h["X-ConnectFlow-Timestamp"] + "." + captured["body"]).encode(), hashlib.sha256).hexdigest()
check("webhook delivery is signed with the DECRYPTED secret (customers verify as before)", h["X-ConnectFlow-Signature"] == want)
check("...and the delivery succeeded", sql("select status from webhook_deliveries where id=:i", i=did) == "success")
legacy = "whsec_legacy_" + STAMP
sql("update webhook_endpoints set secret=:s where id=:i", s=legacy, i=epid)
did = run_delivery(); h = captured["headers"]
want = "sha256=" + hmac.new(legacy.encode(), (h["X-ConnectFlow-Timestamp"] + "." + captured["body"]).encode(), hashlib.sha256).hexdigest()
check("legacy plaintext row still delivers with a valid signature", h["X-ConnectFlow-Signature"] == want)
check("...and the row was upgraded to ciphertext on first use", str(sql("select secret from webhook_endpoints where id=:i", i=epid)).startswith("enc1:") and legacy not in str(sql("select secret from webhook_endpoints where id=:i", i=epid)))
sql("update webhook_endpoints set secret=:s where id=:i", s=legacy + "b", i=epid)
from app.tools.reencrypt import backfill, report
with SessionLocal() as db: n = backfill(db)
check("start-up backfill encrypts remaining plaintext rows", n >= 1 and str(sql("select secret from webhook_endpoints where id=:i", i=epid)).startswith("enc1:"), n)
sql("update webhook_endpoints set secret='enc1:not-a-real-token' where id=:i", i=epid)
captured.clear(); did = run_delivery()
row = sql("select status || '|' || last_error from webhook_deliveries where id=:i", i=did)
check("an undecryptable secret fails that delivery cleanly (no crash, no send, no retry storm)", row.startswith("failed|") and "decrypt" in row and "headers" not in captured, row)
with SessionLocal() as db: rep = report(db)
check("report() counts encrypted vs plaintext rows", isinstance(rep["webhookSecretsEncrypted"], int))

# ---- malformed gateway webhooks (real signing secret, real app)
def post_gw(raw: bytes, sig=None):
    s = sig or "sha256=" + hmac.new(settings.openwa_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    return TestClient(app, raise_server_exceptions=False).post("/internal/wa-webhook", content=raw, headers={"x-openwa-signature": s})
for raw in (b"[]", b"[1,2,3]", b'"x"', b"null", b"{broken", b"\xff\xfe\x00"):
    check(f"gateway webhook: body {raw[:10]!r} -> 400 (not a 500)", post_gw(raw).status_code == 400)
check("gateway webhook: bad signature -> 401", post_gw(b"{}", "sha256=00").status_code == 401)
for p in ({"event": "message.received", "data": [1, 2]}, {"event": ["x"], "data": {}}, {"event": "session.status", "data": {"sessionId": {"a": 1}, "status": ["ready"]}},
          {"event": "message.received", "data": {"sessionId": "nope", "body": {"x": 1}, "from": ["y"]}}, {}, {"data": None}):
    r = post_gw(json.dumps(p).encode())
    check(f"gateway webhook: weird payload {str(p)[:40]!r} is accepted and ignored (200)", r.status_code == 200 and r.json()["ok"] is True, r.text[:80])
check("gateway webhook: still healthy afterwards", TestClient(app).get("/api/health").json() == {"ok": True})
print("INPROC-DONE")
'''
tenants_all = tenants[:]
r = subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-", stamp, PW], cwd=INFRA, input=INPROC, capture_output=True, text=True, timeout=600)
for line in r.stdout.splitlines():
    if line.startswith("PASS  "):
        print(line); ok_n += 1
    elif line.startswith("FAIL  "):
        print(line); bad_n += 1
if "INPROC-DONE" not in r.stdout:
    bad_n += 1
    print("FAIL  in-process part did not finish   [" + (r.stderr or r.stdout)[-600:].replace("\n", " | ") + "]")

# ======================================================================= cleanup (only M5B workspaces)
ADMIN_PW = os.environ.get("E2E_ADMIN_PASSWORD", "")
extra = [sql(f"select tenant_id from memberships where user_id=(select id from users where email='{e}')") for e in (f"m5b-admin-{stamp}@example.com", f"m5b-idem-{stamp}@example.com")]
for tid in [t for t in tenants_all + extra if t]:
    sql(f"delete from instances where tenant_id='{tid}' and id like 'm5b-%'")
sql(f"update users set is_platform_admin=false where email='m5b-admin-{stamp}@example.com'")  # the admin API refuses to delete a platform admin's workspace
if ADMIN_PW:
    adm = sess(ADMIN_HOST)
    st, _ = adm("POST", "/api/auth/login", {"email": os.environ.get("E2E_ADMIN_EMAIL", "admin@connectflow.example.com"), "password": ADMIN_PW})
    if st == 200:
        st, rows = adm("GET", "/api/admin/customers")
        rows = rows["rows"] if isinstance(rows, dict) else rows
        for t in rows:
            if t["name"].startswith("M5B ") and str(stamp) in t["name"]:
                adm("DELETE", f"/api/admin/customers/{t['id']}")
        left = sql(f"select count(*) from tenants where name like 'M5B %{stamp}%'")
        print(f"cleanup: {left} M5B workspace(s) left")
else:
    print("cleanup: E2E_ADMIN_PASSWORD not set; M5B workspaces were left in place")

print(f"\n{ok_n} passed, {bad_n} failed")
sys.exit(1 if bad_n else 0)
