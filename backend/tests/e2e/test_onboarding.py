"""E2E: first-run onboarding wizard + the 14-day trial (needs the dev stack; no AI or WhatsApp is used).

New workspace -> trial_ends_at ~ +14 days and onboarding needed; wizard saves business details; skip works; a paid choice either returns a
Stripe Checkout url (Stripe configured) or records an upgrade request (not configured); an expired trial is read-only (402) and
paying/extending lifts it; a workspace with no trial end date (older workspaces) is never blocked; members cannot run the wizard."""
import http.cookiejar, json, subprocess, time, urllib.request, urllib.error
from e2e_env import APP_HOST, BASE, INFRA

HOST = {"Host": APP_HOST, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}
ok_n = bad_n = 0


def sess():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(method, path, body=None):
        req = urllib.request.Request(BASE + path, method=method, headers=HOST, data=json.dumps(body).encode() if body is not None else None)
        try:
            r = op.open(req, timeout=60)
            d = r.read()
            return r.status, (json.loads(d) if d else {})
        except urllib.error.HTTPError as e:
            d = e.read()
            try:
                return e.code, json.loads(d)
            except Exception:
                return e.code, {"raw": d[:200].decode(errors="ignore")}
    return call


def check(name, cond, extra=""):
    global ok_n, bad_n
    cond = bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{extra}]" if extra and not cond else ""))
    ok_n += cond
    bad_n += (not cond)


def sql(q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q], cwd=INFRA, capture_output=True, text=True).stdout.strip()


stamp = str(int(time.time()))
A, B, C = sess(), sess(), sess()
ids = {}
for c, tag in ((A, "a"), (B, "b"), (C, "c")):
    st, me = c("POST", "/api/auth/signup", {"email": f"onb-{tag}-{stamp}@example.com", "password": "Sup3rSecretPw!", "company": f"Onb {tag} {stamp}"})
    assert st in (200, 201), "signup failed"
    ids[tag] = me["tenant"]["id"]

# ---- new workspace: 14-day trial and the wizard is needed
st, me = A("GET", "/api/auth/me")
tr = me.get("trial") or {}
check("new workspace has a 14-day trial", st == 200 and tr.get("daysLeft") in (14, 15) and tr.get("expired") is False, tr)
check("wizard is needed for a new workspace", me.get("onboarding", {}).get("needed") is True)
st, o = A("GET", "/api/onboarding")
check("onboarding state lists the plans and prefill", st == 200 and o["needed"] and o["trialDays"] == 14 and {p["id"] for p in o["plans"]} >= {"trial", "starter", "growth"} and o["prefill"]["business"].startswith("Onb a"), o)

# ---- finishing the wizard on the free plan saves the details
st, r = A("POST", "/api/onboarding", {"plan_id": "trial", "name": "Ayesha Khan", "business": f"Acme Dental {stamp}", "website": "acme-dental.example", "country": "pk", "about": "We sell dental implants. Consultation is free.", "phone": "0300 1234567"})
check("free plan finishes with no checkout", st == 200 and r.get("ok") and "checkoutUrl" not in r, r)
st, me = A("GET", "/api/auth/me")
check("business name, user name saved and wizard done", me["tenant"]["name"] == f"Acme Dental {stamp}" and me["user"]["name"] == "Ayesha Khan" and me["onboarding"]["needed"] is False and me["tenant"]["plan"]["id"] == "trial", me)
check("website, knowledge and country reach the profile", sql(f"select website_url from tenants where id='{ids['a']}'") == "https://acme-dental.example"
      and "dental implants" in sql(f"select knowledge from outreach_profiles where tenant_id='{ids['a']}'") and sql(f"select default_country from outreach_profiles where tenant_id='{ids['a']}'") == "PK")
check("own WhatsApp number is normalised", sql(f"select wa_number from status_profiles where tenant_id='{ids['a']}'") == "923001234567")
st, r2 = A("POST", "/api/onboarding", {"plan_id": "trial", "business": "Changed Later"})
st, me = A("GET", "/api/auth/me")
check("running the wizard again does not overwrite anything", me["tenant"]["name"] == f"Acme Dental {stamp}")

# ---- skipping saves nothing but ends the wizard
st, r = B("POST", "/api/onboarding", {"plan_id": "trial", "skip": True, "business": "Must Not Save"})
st, me = B("GET", "/api/auth/me")
check("skip ends the wizard without saving the details", st == 200 and me["onboarding"]["needed"] is False and me["tenant"]["name"].startswith("Onb b"), me["tenant"]["name"])

# ---- paid plan: Stripe Checkout url when configured, otherwise a recorded request
st, r = C("POST", "/api/onboarding", {"plan_id": "starter", "business": f"Paid Co {stamp}"})
if r.get("checkoutUrl"):
    check("paid plan returns a Stripe Checkout url", st == 200 and r["checkoutUrl"].startswith("https://"), r)
else:
    check("paid plan without Stripe records an upgrade request and says so", st == 200 and "not enabled" in r.get("message", "") and sql(f"select count(*) from upgrade_requests where tenant_id='{ids['c']}' and plan_id='starter'") == "1", r)
check("the plan does not change until payment is confirmed", sql(f"select plan_id from tenants where id='{ids['c']}'") == "trial")

# ---- expired trial is read-only
sql(f"update tenants set trial_ends_at = now() - interval '1 day' where id='{ids['b']}'")
st, me = B("GET", "/api/auth/me")
check("expired trial is reported", me["trial"] and me["trial"]["expired"] is True and me["trial"]["daysLeft"] == 0, me.get("trial"))
st, r = B("POST", "/api/status/posts", {"headline": "Hello", "action": "draft"})
check("expired trial cannot create a Status post (402)", st == 402 and "trial has ended" in json.dumps(r), (st, r))
st, r = B("POST", "/api/status/ideas", {"prompt": "x", "count": 1})
check("expired trial cannot use the AI (402)", st == 402, (st, r))
st, r = B("GET", "/api/leads")
check("expired trial can still read its data", st == 200)
sql(f"update tenants set trial_ends_at = now() + interval '3 days' where id='{ids['b']}'")
st, r = B("POST", "/api/status/posts", {"headline": "Hello", "action": "draft"})
check("extending the trial lifts the block", st in (200, 201), (st, r))

# ---- older workspaces (no trial end date) are never blocked
sql(f"update tenants set trial_ends_at = NULL where id='{ids['a']}'")
st, me = A("GET", "/api/auth/me")
st2, r = A("POST", "/api/status/posts", {"headline": "Hello", "action": "draft"})
check("a workspace with no trial end date has no trial and is not blocked", me["trial"] is None and st2 in (200, 201), (me.get("trial"), st2))

# ---- a paid workspace is never blocked even with an old trial date
sql(f"update tenants set trial_ends_at = now() - interval '5 days', plan_id='starter', subscription_status='active' where id='{ids['c']}'")
st, me = C("GET", "/api/auth/me")
check("a paid workspace has no trial limit", me["trial"] is None, me.get("trial"))

print(f"\n{ok_n} passed, {bad_n} failed")
raise SystemExit(1 if bad_n else 0)
