"""E2E for the legacy importer (python -m app.tools.migrate_legacy) against the running stack.

Builds a tiny legacy-format SQLite file, copies it into the api container, runs the importer (dry run, then --apply, then again)
and checks the result through the public API and the database. Nothing here needs the fake engine or the AI.
"""
import http.cookiejar, json, os, sqlite3, subprocess, sys, tempfile, time, urllib.request, urllib.error
from e2e_env import APP_HOST, BASE, INFRA

HOST = {"Host": APP_HOST, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}
ok_n = bad_n = 0


def sess():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(method, path, body=None):
        req = urllib.request.Request(BASE + path, method=method, headers=HOST, data=json.dumps(body).encode() if body is not None else None)
        try:
            r = op.open(req, timeout=60)
            data = r.read()
            return r.status, (json.loads(data) if data else {})
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


def dc(*args):
    return subprocess.run(["docker", "compose", *args], cwd=INFRA, capture_output=True, text=True)


def sql(q):
    return dc("exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q).stdout.strip()


stamp = str(int(time.time()))
NAME = f"Legacy Mig {stamp}"
A = sess()
st, _ = A("POST", "/api/auth/signup", {"email": f"migleg-a-{stamp}@example.com", "password": "Sup3rSecretPw!", "company": NAME})
assert st in (200, 201), "signup failed"
B = sess()
B("POST", "/api/auth/signup", {"email": f"migleg-b-{stamp}@example.com", "password": "Sup3rSecretPw!", "company": f"Legacy Mig other {stamp}"})

# ---------------------------------------------------------------- a legacy-format database
tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, "state.sqlite")
md_path = os.path.join(tmp, "company_knowledge.md")
con = sqlite3.connect(db_path)
con.executescript("""
create table lead_lists (id integer primary key, name text, source text, imported_at text, count integer);
create table leads (id integer primary key, company text, role text, business_type text, address text, phone_raw text, whatsapp text, website text, email text,
  linkedin text, facebook text, leadership text, rating text, source text, notes text, status text, research text, first_message text, sent_at text, last_reply text,
  followup_stage text, created_at text, list_id integer, enrichment text, enriched_at text, updated_at text);
create table messages (id integer primary key, lead_key text, chat_id text, direction text, body text, kind text, created_at text);
create table drafts (id integer primary key, lead_key text, chat_id text, company text, incoming text, body text, status text, created_at text, kind text, lead_id integer, note text, source text, instruction text, batch_id integer);
create table settings (key text primary key, value text);
create table meta (key text primary key, value text);
""")
con.execute("insert into lead_lists values (1,'Import 1','csv','2026-10-28 08:00:00',3)")
L = [
    (1, "Legacy Alpha", "923044300555", "contacted", "4.7", "2026-10-28 09:00:00", '{"steps":["x"]}'),
    (2, "Legacy Beta", "923054300556", "replied", "", "2026-10-28 09:05:00", ""),
    (3, "Legacy Gamma", "", "new", "", "2026-10-28 09:10:00", ""),                 # no number
    (4, "Legacy Delta", "971501234567", "queued", "", "2026-10-28 09:15:00", ""),  # last lead: the old importer attached EVERY draft to this one
]
for i, co, wa, status, rating, created, enr in L:
    con.execute("insert into leads (id, company, whatsapp, status, rating, source, notes, created_at, list_id, enrichment, enriched_at, followup_stage, research, first_message, last_reply) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (i, co, wa, status, rating, "maps" if rating else "", "", created, 1, enr, created if enr else "", "0", "r", "hi", ""))
con.execute("insert into messages values (1,'923044300555','923044300555@c.us','out','Hello Alpha','first','2026-10-28 09:00:00')")
con.execute("insert into messages values (2,'923044300555','923044300555@c.us','in','Thanks!','reply','2026-10-28 10:00:00')")
con.execute("insert into drafts values (1,'923054300556','923054300556@c.us','Legacy Beta','Can you share pricing?','Our team will send details.','pending','2026-10-28 11:00:00','reply',2,'NEEDS HUMAN: pricing','ai','',null)")
for k, v in {"sender": "Abdullah", "defaultCountry": "United Arab Emirates", "timezone": "Asia/Karachi", "activeFrom": "9:05", "activeTo": "18:30", "dailyLimit": 12,
             "paused": False, "dryRun": False, "autoReply": True, "extraRules": "Keep it short.", "fallbackTemplate": "Hi {{company}} team,\n{{sender}}",
             "adminWhatsapp": "923001112233"}.items():
    con.execute("insert into settings values (?,?)", (k, json.dumps(v)))
con.execute("insert into meta values ('brand_color','#c2410c')")
con.commit()
con.close()
open(md_path, "w", encoding="utf-8").write("# One Group\nWe build AI agents.\n")

for f, dest in ((db_path, f"/tmp/legacy-{stamp}.sqlite"), (md_path, f"/tmp/legacy-{stamp}.md")):
    r = dc("cp", f, f"api:{dest}")
    assert r.returncode == 0, r.stderr


def run_importer(*extra):
    r = dc("exec", "-T", "api", "python", "-m", "app.tools.migrate_legacy", "--state", f"/tmp/legacy-{stamp}.sqlite", "--knowledge", f"/tmp/legacy-{stamp}.md",
           "--tenant", NAME, "--company-name", "Legacy Co", *extra)
    return r.returncode, r.stdout + r.stderr


# ---------------------------------------------------------------- dry run writes nothing
rc, out = run_importer()
check("dry run reports what it would import", rc == 0 and "leads to import: 4" in out and "DRY RUN" in out, out[-300:])
check("dry run says the run switches stay off", "auto_run=false, dry_run=true" in out)
st, lst = A("GET", "/api/leads?page_size=200")
check("dry run wrote no leads", st == 200 and lst["total"] == 0, lst.get("total"))

# ---------------------------------------------------------------- apply
rc, out = run_importer("--apply")
check("apply succeeds", rc == 0 and "APPLIED." in out, out[-300:])
st, lst = A("GET", "/api/leads?page_size=200")
rows = {l["company"]: l for l in lst["rows"]}
check("4 leads imported (one without a number)", lst["total"] == 4 and set(rows) == {"Legacy Alpha", "Legacy Beta", "Legacy Gamma", "Legacy Delta"}, list(rows))
check("country is detected from each number (PK / AE), blank without a number", rows["Legacy Alpha"]["country"] == "PK" and rows["Legacy Delta"]["country"] == "AE" and rows["Legacy Gamma"]["country"] == "")
check("statuses kept; a 'queued' lead without a draft goes back to new", rows["Legacy Beta"]["status"] == "replied" and rows["Legacy Alpha"]["status"] == "contacted" and rows["Legacy Delta"]["status"] == "new", {k: v["status"] for k, v in rows.items()})
check("leads were grouped into the (legacy) list", rows["Legacy Alpha"]["listId"] is not None)
check("rating and source were kept as a note", "Google rating: 4.7" in rows["Legacy Alpha"]["notes"] and "Source: maps" in rows["Legacy Alpha"]["notes"], rows["Legacy Alpha"]["notes"])
check("enrichment flag carried over", rows["Legacy Alpha"]["enriched"] is True and rows["Legacy Beta"]["enriched"] is False)

st, th = A("GET", f"/api/leads/{rows['Legacy Alpha']['id']}/thread")
check("the conversation log came with the lead (out + in)", st == 200 and [m["direction"] for m in th["messages"]] == ["out", "in"], th)

st, dl = A("GET", "/api/drafts?status=open&kind=reply")
dr = dl["rows"][0] if st == 200 and dl.get("rows") else None
check("the open draft is attached to ITS lead, not to the last lead of the file", dr and dr["leadId"] == rows["Legacy Beta"]["id"] and dr["leadId"] != rows["Legacy Delta"]["id"], dr)
check("a 'NEEDS HUMAN' note keeps the draft flagged", dr and dr.get("needsHuman") in (True, 1), dr)

st, pr = A("GET", "/api/outreach/profile")
check("settings reached the outreach profile", st == 200 and pr["sender_name"] == "Abdullah" and pr["default_country"] == "AE" and pr["daily_limit"] == 12 and pr["active_from"] == "09:05" and pr["active_to"] == "18:30", pr)
check("the legacy 'live' switches were NOT copied: auto_run off, dry_run on", pr["auto_run"] is False and pr["dry_run"] is True, {k: pr.get(k) for k in ("auto_run", "dry_run")})
check("knowledge + operator rules are in the profile", "We build AI agents." in pr["knowledge"] and "Keep it short." in pr["knowledge"])
check("the fallback template keeps its placeholders", "{{company}}" in pr["fallback_template"])
check("company name from the option", pr["company_name"] == "Legacy Co")
check("branding colour moved to the workspace", sql(f"select brand_color from tenants where name='{NAME}'") == "#c2410c")

# ---------------------------------------------------------------- re-run adds nothing
rc, out = run_importer("--apply", "--backfill")
check("a second run imports nothing new", rc == 0 and "leads to import: 0   already present (skipped): 4" in out, out[-300:])
st, lst2 = A("GET", "/api/leads?page_size=200")
check("no duplicate leads after the second run", lst2["total"] == 4)
st, th2 = A("GET", f"/api/leads/{rows['Legacy Alpha']['id']}/thread")
check("no duplicate messages after the second run", len(th2["messages"]) == 2)

# ---------------------------------------------------------------- isolation
st, lb = B("GET", "/api/leads?page_size=200")
check("another workspace sees none of the imported leads", st == 200 and lb["total"] == 0, lb.get("total"))
st, rb = B("GET", f"/api/leads/{rows['Legacy Alpha']['id']}/thread")
check("...and cannot read their threads (404)", st == 404, st)

# a wrong workspace name fails loudly instead of importing somewhere else
rc, out = dc("exec", "-T", "api", "python", "-m", "app.tools.migrate_legacy", "--state", f"/tmp/legacy-{stamp}.sqlite", "--tenant", "No Such Workspace").returncode, ""
check("an unknown workspace name is refused (non-zero exit)", rc != 0)

sql("delete from users where email like 'migleg-%@example.com'")
sql("delete from tenants where name like 'Legacy Mig%'")
print(f"\n{ok_n} passed, {bad_n} failed")
sys.exit(1 if bad_n else 0)
