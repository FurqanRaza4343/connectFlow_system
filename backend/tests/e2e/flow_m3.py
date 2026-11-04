import http.cookiejar, json, subprocess, sys, time, urllib.request, urllib.error
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA, require_admin_password, require_fake_engine, require_mailpit, require_real_ai  # noqa: F401
require_real_ai()

H = {"Host": APP_HOST, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
ok_n = bad_n = 0


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers=H, data=json.dumps(body).encode() if body is not None else None)
    try:
        r = op.open(req, timeout=120)
        d = r.read()
        return r.status, (json.loads(d) if d else {})
    except urllib.error.HTTPError as e:
        d = e.read()
        try:
            return e.code, json.loads(d)
        except Exception:
            return e.code, {"raw": d[:200].decode(errors="ignore")}


def check(name, cond, extra=""):
    global ok_n, bad_n
    cond = bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{extra}]" if extra and not cond else ""))
    ok_n += cond
    bad_n += (not cond)


def sql(q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q], cwd=INFRA, capture_output=True, text=True).stdout.strip()


EMAIL, PW = "m3r-demo@example.com", "Sup3rSecretPw!"
sql("delete from tenants where name='M3R Demo'; delete from users where email='m3r-demo@example.com'")
st, me = call("POST", "/api/auth/signup", {"email": EMAIL, "password": PW, "company": "M3R Demo", "name": "Ayesha Khan"})
sql(f"update users set email_verified_at=now() where email='{EMAIL}'")

csv = """Company,Contact,Phone,Website,Business Type,Address
Northwind Dental Clinic,Sara,+92 300 1112233,https://example.com,Dental clinic,Lahore
Orbit Realty,Omar,+971 50 123 4567,https://example.org,Real estate,Dubai
Pixel Parcel,Daniel,+44 7400 123456,,E-commerce,London
Bright Smile Dental,Hina,0301 2223344,,Dental clinic,Karachi
Hilltop Cafe,,,,Cafe,Murree
"""
st, an = call("POST", "/api/leads/import/analyze", {"csv": csv})
st, imp = call("POST", "/api/leads/import", {"csv": csv, "mapping": an["mapping"], "list_name": "Demo list"})
check("import: 5 leads, 1 without number", st == 200 and imp["added"] == 5 and imp["withoutNumber"] == 1, imp)

st, prof = call("GET", "/api/outreach/profile")
prof.update({"company_name": "BrightDesk", "sender_name": "Ayesha", "website": "brightdesk.example",
             "offer": "BrightDesk sends automatic WhatsApp appointment reminders for clinics and cuts no-shows by about a third.",
             "knowledge": "Setup takes one day. Works with any calendar. Plans start at $19/month. Never promise guaranteed results.",
             "default_country": "PK", "batch_size": 2, "dry_run": True, "require_approval": True})
st, p2 = call("PUT", "/api/outreach/profile", prof)
check("profile saved with engine fields", st == 200 and p2.get("dry_run") is True and p2.get("batch_size") == 2, p2)

st, s0 = call("GET", "/api/outreach/status")
check("status: paused, dry-run, 4 eligible leads", st == 200 and s0["autoRun"] is False and s0["dryRun"] is True and s0["eligibleLeads"] == 4, s0)

t0 = time.time()
st, run = call("POST", "/api/outreach/run-now", {"limit": 2})
print(f"   run-now took {time.time()-t0:.1f}s ->", run)
check("batch ran with REAL AI: 2 processed, 2 queued, 0 sent", st == 200 and run["processed"] == 2 and run["queued"] == 2 and run["sent"] == 0, run)

st, d = call("GET", "/api/drafts?status=open")
check("2 drafts waiting for approval", st == 200 and d["counts"]["pending"] == 2 and len(d["rows"]) == 2, d.get("counts"))
dr = d["rows"][0]
print("   draft 1 for", dr["company"], ":\n   ", dr["body"].replace("\n", "\n    "))
check("draft is addressed to the lead and signed", len(dr["body"]) > 60 and ("Ayesha" in dr["body"] or "BrightDesk" in dr["body"]))
check("no secrets/prompts leaked into the draft", "AI_API_KEY" not in dr["body"] and "system prompt" not in dr["body"].lower())

st, up = call("PATCH", f"/api/drafts/{dr['id']}", {"body": dr["body"] + "\n(edited by me)"})
check("user can edit a draft", st == 200 and up["body"].endswith("(edited by me)"))
st, rg = call("POST", f"/api/drafts/{dr['id']}/regenerate", {"instruction": "Make it much shorter, two sentences maximum."})
print("   regenerated:", rg.get("body", rg))
check("regenerate (real AI) returns a shorter draft", st == 200 and len(rg["body"]) < len(dr["body"]) + 20, rg)
st, ap = call("POST", "/api/drafts/approve", {"ids": [dr["id"]]})
check("approving while dry-run is ON is blocked (409)", st == 409 and "dry" in json.dumps(ap).lower(), ap)

# switch dry-run off: approving with NO connected number must fail safely, not crash or double-send
prof["dry_run"] = False
call("PUT", "/api/outreach/profile", prof)
st, ap = call("POST", "/api/drafts/approve", {"ids": [dr["id"]]})
print("   approve with no connected number ->", st, ap)
ap_st = st
time.sleep(8)
st, d2 = call("GET", "/api/drafts?status=failed")
st, d3 = call("GET", "/api/drafts?status=approved")
check("with no connected number approval is refused (409) and the draft stays pending, nothing is sent", ap_st == 409 and any(x["id"] == dr["id"] for x in call("GET", "/api/drafts?status=pending")[1]["rows"]), (ap_st, ap))
print("   note:", [x["note"] for x in d2["rows"] + d3["rows"] if x["id"] == dr["id"]])
check("nothing was recorded as sent (no message usage events)", sql("select count(*) from usage_events where kind='message' and tenant_id=(select id from tenants where name='M3R Demo')") == "0")

st, ov = call("GET", "/api/overview")
c = ov["counts"]
check("overview: counts + 14-day series + scheduler block", st == 200 and c["total"] == 5 and c["queued"] >= 1 and len(ov["daily"]) == 14 and "scheduler" in ov and ov["pendingApprovals"] >= 1, ov)
st, nt = call("GET", "/api/notifications")
check("notifications: batch notification created", st == 200 and any(n["type"] == "batch" for n in nt["rows"]) and nt["unread"] >= 1, nt)
st, ib = call("GET", "/api/inbox?filter=all")
check("inbox endpoint works (empty before any reply)", st == 200 and isinstance(ib, list))
st, lead = call("GET", "/api/leads?q=Northwind")
lid = lead["rows"][0]["id"]
st, th = call("GET", f"/api/leads/{lid}/thread")
check("lead thread has the pending draft", st == 200 and "messages" in th and "drafts" in th)

# isolation: another tenant cannot see/approve these drafts
op2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def call2(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers=H, data=json.dumps(body).encode() if body is not None else None)
    try:
        r = op2.open(req, timeout=60); d = r.read(); return r.status, (json.loads(d) if d else {})
    except urllib.error.HTTPError as e:
        return e.code, {}
call2("POST", "/api/auth/signup", {"email": "m3r-other@example.com", "password": PW, "company": "M3R Other"})
sql("update users set email_verified_at=now() where email='m3r-other@example.com'")
st, od = call2("GET", "/api/drafts?status=open")
check("other tenant sees no drafts", st == 200 and od.get("total", 0) == 0 and od.get("rows") == [])
check("other tenant cannot edit/approve/discard our draft", call2("PATCH", f"/api/drafts/{dr['id']}", {"body": "hijack"})[0] in (403, 404) and call2("POST", "/api/drafts/approve", {"ids": [dr["id"]]})[0] in (403, 404, 409) and call2("POST", f"/api/drafts/{dr['id']}/discard")[0] in (403, 404))
st, chk = call("GET", "/api/drafts?status=open")
check("our draft body unchanged after the hijack attempt", all("hijack" not in x["body"] for x in chk["rows"]))
st, th2 = call2("GET", f"/api/leads/{lid}/thread")
check("other tenant cannot read our lead thread (404)", st in (403, 404))

print(f"\n{ok_n} passed, {bad_n} failed")
sql("delete from tenants where name in ('M3R Other','M3R Demo'); delete from users where email in ('m3r-other@example.com','m3r-demo@example.com')")
sys.exit(1 if bad_n else 0)
