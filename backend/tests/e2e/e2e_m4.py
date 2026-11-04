import http.cookiejar, json, re, subprocess, sys, time, urllib.request, urllib.error
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA, require_admin_password, require_fake_engine, require_mailpit  # noqa: F401
require_admin_password()

ok_n = bad_n = 0


def sess(host=APP_HOST):
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


def sql(q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q], cwd=INFRA, capture_output=True, text=True).stdout.strip()


ADM_PW = require_admin_password()
stamp = str(int(time.time()))

# ---- admin host serves the admin SPA, app host serves the customer SPA
st, html = sess(ADMIN_HOST)("GET", "/", raw=True)
check("admin host serves the admin app", st == 200 and "admin" in html.lower() and "ConnectFlow Admin" in html, html[:150])
st, html2 = sess(ADMIN_HOST)("GET", "/customers/deep/link", raw=True)
check("admin SPA fallback works on deep links", st == 200 and "ConnectFlow Admin" in html2)
check("customer host does not serve the admin app", "ConnectFlow Admin" not in sess()("GET", "/", raw=True)[1])

# ---- customers
A = sess()
A("POST", "/api/auth/signup", {"email": f"m4a-{stamp}@example.com", "password": "Sup3rSecretPw!", "company": "M4 Alpha"})
mail = sql("select 1")
sql(f"update users set email_verified_at=now() where email='m4a-{stamp}@example.com'")

# ---- API key expiry
st, k1 = A("POST", "/api/keys", {"name": "short", "expires_in": "1h"})
check("key created with 1 hour expiry", st == 201 and k1["expiresAt"], k1)
st, k2 = A("POST", "/api/keys", {"name": "forever", "expires_in": "never"})
check("key with no expiry", st == 201 and k2["expiresAt"] is None)
check("bad expiry rejected (422)", A("POST", "/api/keys", {"name": "x", "expires_in": "2 weeks"})[0] == 422)
sql(f"update api_keys set expires_at = now() - interval '1 minute' where id='{k1['id']}'")
anon = sess()
st, r = anon("GET", "/v1/instances", headers={"Authorization": f"Bearer {k1['key']}"})
check("expired key is rejected (401, says expired)", st == 401 and "expired" in json.dumps(r).lower(), r)
st, r = anon("GET", "/v1/instances", headers={"Authorization": f"Bearer {k2['key']}"})
check("non-expiring key still works", st == 200)
st, ks = A("GET", "/api/keys")
check("key list shows expired flag", [k for k in ks if k["id"] == k1["id"]][0]["expired"] is True)

# ---- theme + website
st, r = A("PUT", "/api/branding/theme", {"accent": "#2563eb", "sidebar": "#0b1020", "page": "#111729"})
check("3-colour theme saved", st == 200 and r["sidebar"] == "#0b1020" and r["page"] == "#111729", r)
check("bad colour rejected (422)", A("PUT", "/api/branding/theme", {"accent": "red"})[0] == 422)
st, me = A("GET", "/api/auth/me")
check("/me returns accent+sidebar+page", me["tenant"]["brand"]["sidebar"] == "#0b1020" and me["tenant"]["brand"]["page"] == "#111729")
st, r = A("PUT", "/api/workspace/website", {"website": "m4alpha.example.com"})
check("website saved and normalised to https", st == 200 and r["website"] == "https://m4alpha.example.com", r)
check("bad website rejected (422)", A("PUT", "/api/workspace/website", {"website": "not a site"})[0] == 422)
st, me = A("GET", "/api/auth/me")
check("/me carries the website", me["tenant"]["website"] == "https://m4alpha.example.com")
check("theme reset", A("PUT", "/api/branding/theme", {})[0] == 200)

# ---- login tracking
B = sess()
B("POST", "/api/auth/login", {"email": f"m4a-{stamp}@example.com", "password": "Sup3rSecretPw!"})
check("login writes last_login_at", sql(f"select last_login_at is not null from users where email='m4a-{stamp}@example.com'") == "t")
check("login is recorded in the audit trail", int(sql("select count(*) from audit_log where action='login' and detail <> ''") or 0) >= 1)

# ---- admin API: only platform admins
check("customer cannot reach admin API (403)", A("GET", "/api/admin/overview")[0] == 403)
AD = sess(ADMIN_HOST)
st, me = AD("POST", "/api/auth/login", {"email": "admin@connectflow.example.com", "password": ADM_PW})
check("admin logs in on the admin host", st == 200 and me["user"]["isPlatformAdmin"] is True)
st, o = AD("GET", "/api/admin/overview")
k = o.get("kpis", {})
check("overview KPIs present", st == 200 and all(x in k for x in ("customers", "users", "numbers", "messagesToday", "leads", "mrrUsd", "activeCustomers7d")), o)
check("overview series have 30 days", len(o["signups"]) == 30 and len(o["messages"]) == 30)
st, c = AD("GET", "/api/admin/customers?q=M4%20Alpha")
row = c["rows"][0] if c.get("rows") else {}
check("customer list finds by company and shows owner email", st == 200 and row.get("ownerEmail") == f"m4a-{stamp}@example.com", c)
check("customer list by owner email search", AD("GET", f"/api/admin/customers?q=m4a-{stamp}")[1]["total"] == 1)
st, d = AD("GET", f"/api/admin/customers/{row['id']}")
check("detail: members with last login + provider", st == 200 and d["members"][0]["lastLoginAt"] and d["members"][0]["provider"] == "password", d.get("members"))
check("detail: keys incl. expiry, webhooks, leads, usage, activity present", all(x in d for x in ("keys", "webhooks", "leads", "usage", "activity", "instances", "requests")) and any(k["expiresAt"] is None for k in d["keys"]) and len(d["usage"]["days"]) == 14)
check("detail: activity trail includes our actions", {"signup", "apikey.create", "login"} <= {a["action"] for a in d["activity"]}, [a["action"] for a in d["activity"]])
st, u = AD("GET", f"/api/admin/users?q=m4a-{stamp}")
check("users list shows verified + last seen", st == 200 and u["rows"][0]["verified"] is True and u["rows"][0]["lastSeenAt"])
st, act = AD("GET", "/api/admin/activity?action=login")
check("activity feed filters by action", st == 200 and act["total"] >= 1 and all(r["action"] == "login" for r in act["rows"]) and "login" in act["actions"])
st, act = AD("GET", f"/api/admin/activity?tenant_id={row['id']}")
check("activity feed filters by customer", act["total"] >= 3)
st, pl = AD("GET", "/api/admin/plans")
check("plans listed", st == 200 and len(pl) == 3)
st, h = AD("GET", "/api/admin/health")
check("health reports db/redis/gateway/worker", h.get("database") == "ok" and h.get("redis") == "ok" and "gateway" in h and "pending" in h["worker"], h)
check("admin changes plan + suspends", AD("PATCH", f"/api/admin/tenants/{row['id']}", {"plan_id": "starter", "status": "suspended"})[0] == 200)
check("suspended customer is locked out (403)", A("GET", "/api/instances")[0] == 403)
check("admin re-activates", AD("PATCH", f"/api/admin/tenants/{row['id']}", {"status": "active"})[0] == 200)
check("admin cannot delete a platform-admin workspace (409)", AD("DELETE", f"/api/admin/customers/{[t for t in AD('GET','/api/admin/customers?q=ConnectFlow')[1]['rows'] if t['ownerEmail'].startswith('admin@')][0]['id']}")[0] == 409)
check("admin deletes the test customer", AD("DELETE", f"/api/admin/customers/{row['id']}")[0] == 204)
check("deleting a customer removes its users too", sql(f"select count(*) from users where email='m4a-{stamp}@example.com'") == "0")

print(f"\n{ok_n} passed, {bad_n} failed")
sql("delete from tenants where name like 'M4 %'")
sql("delete from users where email like 'm4%@example.com'")
sys.exit(1 if bad_n else 0)
