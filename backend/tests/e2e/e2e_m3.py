import http.cookiejar, json, subprocess, sys, time, urllib.request, urllib.error
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA, require_admin_password, require_fake_engine, require_mailpit  # noqa: F401

HOST = {"Host": APP_HOST, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}
ok_n = bad_n = 0


def sess():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(method, path, body=None, raw=False):
        req = urllib.request.Request(BASE + path, method=method, headers=HOST, data=json.dumps(body).encode() if body is not None else None)
        try:
            r = op.open(req, timeout=60)
            data = r.read()
            return r.status, (data.decode() if raw else (json.loads(data) if data else {}))
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
A = sess()
A("POST", "/api/auth/signup", {"email": f"m3a-{stamp}@example.com", "password": "Sup3rSecretPw!", "company": "M3 Alpha"})
B = sess()
B("POST", "/api/auth/signup", {"email": f"m3b-{stamp}@example.com", "password": "Sup3rSecretPw!", "company": "M3 Beta"})

legacy = open(r"d:\For sir\lead_gen_whatsapp\legacy\data\leads.csv", encoding="utf-8").read()
st, an = A("POST", "/api/leads/import/analyze", {"csv": legacy})
check("analyze reads the real legacy CSV", st == 200 and an["rowCount"] >= 25, an if st != 200 else "")
m = an["mapping"]
check("headers auto-mapped (Company, WhatsApp Number, Website, Email, Role Hiring For)", m.get("Company") == "company" and m.get("WhatsApp Number") == "phone" and m.get("Website") == "website" and m.get("Email") == "email" and m.get("Role Hiring For") == "role", m)
check("blank/unknown columns left unmapped, not guessed wrongly", m.get("Google Rating") is None and m.get("ID") is None, m)

st, r = A("POST", "/api/leads/import", {"csv": legacy, "mapping": m, "list_name": "Legacy One Group"})
check("import ok", st == 200 and r["added"] > 20, r)
print("   ->", r)
st, lst = A("GET", "/api/leads?page_size=200")
check("leads listed with totals", lst["total"] == r["added"])
with_num = [l for l in lst["rows"] if l["whatsapp"]]
check("valid numbers normalised to E.164 digits", with_num and all(l["whatsapp"].isdigit() and 10 <= len(l["whatsapp"]) <= 15 for l in with_num))
check("country detected from the number", any(l["country"] == "PK" for l in with_num))
multi = [l for l in lst["rows"] if ";" in l["phoneRaw"]]
check("a field with several numbers still yields a valid one", (not multi) or all(l["whatsapp"] for l in multi), [l["phoneRaw"] for l in multi if not l["whatsapp"]])
no_num = [l for l in lst["rows"] if not l["whatsapp"]]
check("rows without a valid number are marked 'verify', not lost", all(l["status"] == "verify" for l in no_num))
st, r2 = A("POST", "/api/leads/import", {"csv": legacy, "mapping": m, "list_name": "again"})
check("re-importing the same file adds nothing (duplicates detected)", st == 200 and r2["added"] == 0 and r2["duplicates"] >= r["added"] - 1, r2)

st, one = A("POST", "/api/leads", {"company": "Manual Co", "phone_raw": "0300 1112233", "website": "manual.example"})
check("manual lead: local number gets the workspace default country (PK)", st == 201 and one["whatsapp"] == "923001112233", one)
check("duplicate manual lead refused (409)", A("POST", "/api/leads", {"company": "Other", "phone_raw": "+92 300 1112233"})[0] == 409)
st, p = A("PATCH", f"/api/leads/{one['id']}", {"phone_raw": "+44 7400 123456", "notes": "met at expo"})
check("editing the phone re-normalises (UK)", st == 200 and p["whatsapp"] == "447400123456" and p["country"] == "GB", p)
check("cannot jump to contacted by hand (422)", A("PATCH", f"/api/leads/{one['id']}", {"status": "contacted"})[0] == 422)
check("hold via bulk", A("POST", "/api/leads/bulk", {"ids": [one["id"]], "action": "hold"})[1].get("changed") == 1)

st, f = A("GET", "/api/leads?q=manual")
check("search finds by company", f["total"] == 1)
st, f = A("GET", "/api/leads?status=verify")
check("status filter works", f["total"] == len(no_num))

# formula injection in exports
A("POST", "/api/leads", {"company": "=HYPERLINK(\"http://evil\")", "phone_raw": "+971 50 123 4567"})
st, csvtxt = A("GET", "/api/leads-export.csv", raw=True)
check("export neutralises formula injection", st == 200 and "'=HYPERLINK" in csvtxt and "\n=HYPERLINK" not in csvtxt)

# tenant isolation
st, bl = B("GET", "/api/leads")
check("other tenant sees none of these leads", st == 200 and bl["total"] == 0)
check("other tenant cannot read/edit/delete our lead (404)", B("PATCH", f"/api/leads/{one['id']}", {"notes": "x"})[0] == 404 and B("DELETE", f"/api/leads/{one['id']}")[0] == 404)
check("other tenant cannot see our lists", B("GET", "/api/lead-lists")[1] == [])
check("other tenant's export has no data from us", "Manual Co" not in B("GET", "/api/leads-export.csv", raw=True)[1])

# profile
st, pr = A("GET", "/api/outreach/profile")
check("profile defaults to the workspace name", pr["company_name"] == "M3 Alpha" and pr["require_approval"] is True)
check("profile saves", A("PUT", "/api/outreach/profile", {**pr, "sender_name": "Ayesha", "default_country": "ae", "daily_limit": 20, "offer": "We do X"})[0] == 200)
st, pr = A("GET", "/api/outreach/profile")
check("saved values persist (country upper-cased)", pr["sender_name"] == "Ayesha" and pr["default_country"] == "AE")
check("bad country rejected (422)", A("PUT", "/api/outreach/profile", {**pr, "default_country": "ZZ"})[0] == 422)
check("bad tone rejected (422)", A("PUT", "/api/outreach/profile", {**pr, "tone": "angry"})[0] == 422)

# list delete keeps contacted leads
st, ls = A("GET", "/api/lead-lists")
check("list shows its lead count", ls and ls[-1]["count"] > 20 if len(ls) else False, ls)
big = [x for x in ls if x["name"] == "Legacy One Group"][0]
sql(f"update leads set status='contacted' where list_id='{big['id']}' and id in (select id from leads where list_id='{big['id']}' and status='new' limit 2)")
A("DELETE", f"/api/lead-lists/{big['id']}?delete_leads=true")
left = sql(f"select count(*) from leads where status='contacted' and tenant_id=(select tenant_id from leads where company='Manual Co' limit 1)")
check("deleting a list keeps already-contacted leads", int(left or 0) == 2, left)

print(f"\n{ok_n} passed, {bad_n} failed")
# clean up only our test data
sql("delete from tenants where name in ('M3 Alpha','M3 Beta')")
sql("delete from users where email like 'm3%@example.com'")
sys.exit(1 if bad_n else 0)
