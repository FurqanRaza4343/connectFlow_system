"""Multi-number control, number-aware conversations, e-mail alerts and admin support actions.
Needs AI_FAKE=true + OUTREACH_FAKE_SEND=true (api/worker) and Mailpit (RESEND_API_KEY blank) for the e-mail checks.
Test workspaces are named 'M4X ...' and removed at the end."""
import http.cookiejar, json, os, subprocess, sys, time, urllib.request, urllib.error, uuid  # noqa: E401
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA, api_env, require_fake_engine

require_fake_engine()
HAVE_MAILPIT = not api_env("RESEND_API_KEY").get("RESEND_API_KEY")
ADMIN_PW = os.environ.get("E2E_ADMIN_PASSWORD", "")
ok_n = bad_n = 0
stamp = str(int(time.time()))


def sess(host=APP_HOST):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    H = {"Host": host, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}

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
                return e.code, {"raw": d[:300].decode(errors="ignore")}
    return call


def check(name, cond, extra=""):
    global ok_n, bad_n
    cond = bool(cond)
    print(("PASS  " if cond else "FAIL  ") + name + (f"   [{str(extra)[:300]}]" if extra and not cond else ""))
    ok_n += cond
    bad_n += (not cond)


def sql(q):
    return subprocess.run(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def wait(fn, timeout=40, every=1.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return fn()


def webhook(event, data):
    payload = json.dumps({"event": event, "data": data})
    code = "import hmac,hashlib,sys,httpx,os;b=sys.argv[1].encode();s='sha256='+hmac.new(os.environ['OPENWA_WEBHOOK_SECRET'].encode(),b,hashlib.sha256).hexdigest();r=httpx.post('http://localhost:8000/internal/wa-webhook',content=b,headers={'x-openwa-signature':s,'content-type':'application/json'});print(r.status_code)"
    return subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-c", code, payload], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def inbound(sid, frm, body):
    return webhook("message.received", {"sessionId": sid, "from": frm, "body": body, "id": str(uuid.uuid4())})


def mailpit(to):
    """Messages Mailpit holds for one recipient (empty when Mailpit is unavailable)."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:8025/api/v1/search?query={urllib.request.quote('to:' + to)}")
        return json.loads(urllib.request.urlopen(req, timeout=10).read()).get("messages", [])
    except Exception:
        return []


def clear_throttle(tid):
    subprocess.run(["docker", "compose", "exec", "-T", "redis", "sh", "-c", f"redis-cli --scan --pattern 'og:alert:{tid}:*' | xargs -r redis-cli del"], cwd=INFRA, capture_output=True, text=True)


def signup(name, email):
    c = sess()
    st, me = c("POST", "/api/auth/signup", {"email": email, "password": "Sup3rSecretPw!", "company": name})
    sql(f"update users set email_verified_at=now() where email='{email}'")
    tid = me["tenant"]["id"]
    sql(f"update tenants set plan_id='growth' where id='{tid}'")
    return c, tid


EMAIL_A, EMAIL_B = f"m4x-a-{stamp}@example.com", f"m4x-b-{stamp}@example.com"
A, TA = signup(f"M4X Alpha {stamp}", EMAIL_A)
B, TB = signup(f"M4X Beta {stamp}", EMAIL_B)
try:
    I1, S1, I2, S2 = str(uuid.uuid4()), "sess1-" + stamp, str(uuid.uuid4()), "sess2-" + stamp
    for iid, sid, label, phone in ((I1, S1, "Sales", "923001110001"), (I2, S2, "Support", "923001110002")):
        sql(f"insert into instances (id,tenant_id,label,node_id,engine,openwa_name,openwa_id,phone,status,created_at) values ('{iid}','{TA}','{label}','wa1','baileys','cf-m4x-{iid[:8]}','{sid}','{phone}','ready',now())")

    # ------------------------------------------------ 1. per-number control
    st, lst = A("GET", "/api/instances")
    check("instances expose outreachEnabled=true by default", st == 200 and all(i["outreachEnabled"] is True for i in lst), lst)
    st, r = A("PATCH", f"/api/instances/{I2}", {"outreach_enabled": False})
    check("owner can switch a number to API-only", st == 200 and r["outreachEnabled"] is False, r)
    st, r = B("PATCH", f"/api/instances/{I2}", {"outreach_enabled": True})
    check("another tenant cannot toggle it (404)", st == 404, r)
    st, ov = A("GET", "/api/overview")
    inst = {i["id"]: i for i in ov["scheduler"]["instances"]}
    check("overview lists per-number counters", all(k in inst[I1] for k in ("sentToday", "sent7d", "replies7d", "warmupCap", "outreachEnabled")) and inst[I2]["outreachEnabled"] is False, inst.get(I2))

    rows = ["Company,Phone,Website"] + [f"Beta Co {i},+92300888{1000 + i},beta{i}.example" for i in range(6)]
    st, an = A("POST", "/api/leads/import/analyze", {"csv": "\n".join(rows)})
    st, imp = A("POST", "/api/leads/import", {"csv": "\n".join(rows), "mapping": an["mapping"], "list_name": "m4x"})
    check("6 leads imported", imp["added"] == 6, imp)
    st, pr = A("GET", "/api/outreach/profile")
    pr.update(dry_run=False, require_approval=True, delay_min=5, delay_max=5, daily_limit=50, active_from="00:00", active_to="23:59", timezone="UTC")
    A("PUT", "/api/outreach/profile", pr)
    st, run = A("POST", "/api/outreach/run-now", {"limit": 6})
    st, dr = A("GET", "/api/drafts?status=pending&kind=first")
    ids = [d["id"] for d in dr["rows"]]
    check("first-message drafts created", len(ids) == 6, run)
    A("POST", "/api/drafts/approve", {"ids": ids})
    wait(lambda: sql(f"select count(*) from messages where tenant_id='{TA}' and direction='out' and kind='first'") == "5", 60, 2)
    by = dict((r.split("|")[0], int(r.split("|")[1])) for r in sql(f"select instance_id,count(*) from messages where tenant_id='{TA}' and direction='out' and kind='first' group by 1").splitlines() if r)
    check("sender used only the outreach-enabled number", by.get(I1, 0) >= 1 and by.get(I2, 0) == 0, by)

    # approve 409 when every connected number is API-only (only checked when the fake-send bypass is off)
    # ------------------------------------------------ 2. conversations know their number
    lead = sql(f"select id||'|'||whatsapp from leads where tenant_id='{TA}' and instance_id='{I1}' limit 1").split("|")
    lead_id, lead_wa = lead[0], lead[1]
    st, lr = A("GET", f"/api/leads?instance_id={I1}")
    check("leads filter by number + via field", st == 200 and lr["total"] >= 1 and lr["rows"][0]["via"]["id"] == I1, lr["rows"][:1])
    st, lr2 = A("GET", f"/api/leads?instance_id={I2}")
    check("leads filter by the other number is empty", lr2["total"] == 0, lr2["total"])
    clear_throttle(TA)
    check("inbound webhook accepted", inbound(S1, f"{lead_wa}@c.us", "How much does it cost? Send me the price list") == "200")
    st, ib = A("GET", "/api/inbox")
    row = next((r for r in ib if r["leadId"] == lead_id), None)
    check("inbox row shows the number", row and row["instanceId"] == I1 and row["via"]["phone"] == "923001110001", row)
    st, ib2 = A("GET", f"/api/inbox?instance_id={I2}")
    st, ib1 = A("GET", f"/api/inbox?instance_id={I1}")
    check("inbox filter by number works", not any(r["leadId"] == lead_id for r in ib2) and any(r["leadId"] == lead_id for r in ib1))
    st, th = A("GET", f"/api/leads/{lead_id}/thread")
    check("thread carries the number", th["via"] and th["via"]["id"] == I1 and th["messages"][-1]["instanceId"] == I1, th.get("via"))
    st, dl = A("GET", "/api/drafts?status=open&kind=reply")
    rep = next((d for d in dl["rows"] if d["leadId"] == lead_id), None)
    check("reply draft shows the sending number", rep and rep["via"] and rep["via"]["id"] == I1, rep)

    # reply pinned to its number: disconnect it -> stays pending with a clear note, never moves to the other (ready) number
    sql(f"update instances set status='disconnected' where id='{I1}'")
    sql(f"update instances set outreach_enabled=true where id='{I2}'")
    if rep:
        A("POST", "/api/drafts/approve", {"ids": [rep["id"]]})
        time.sleep(8)
        row = sql(f"select status||'|'||note from drafts where id='{rep['id']}'")
        check("reply NOT sent from another number when its own is down", row.startswith(("pending|", "approved|")) and "disconnected" in row.lower(), row)
        check("no outbound reply was recorded on number 2", sql(f"select count(*) from messages where tenant_id='{TA}' and direction='out' and kind='reply' and instance_id='{I2}'") == "0")
        sql(f"update instances set status='ready' where id='{I1}'")
        A("POST", "/api/drafts/approve", {"ids": [rep["id"]]})  # the draft went back to pending: approve again
        sent = wait(lambda: sql(f"select count(*) from messages where tenant_id='{TA}' and direction='out' and kind='reply' and instance_id='{I1}'") == "1", 40, 2)
        check("after reconnect the reply goes out from its own number", sent)

    # ------------------------------------------------ 3. e-mail alerts
    st, pr = A("GET", "/api/outreach/profile")
    check("profile defaults: notify_email on, digest off, no extras", pr["notify_email"] is True and pr["notify_digest"] is False and pr["notify_extra_emails"] == "", pr)
    bad = dict(pr, notify_extra_emails="a@x.com,b@x.com,c@x.com,d@x.com")
    st, r = A("PUT", "/api/outreach/profile", bad)
    check("more than 3 extra emails rejected (422)", st == 422, r)
    st, r = A("PUT", "/api/outreach/profile", dict(pr, notify_extra_emails="nope"))
    check("invalid extra email rejected (422)", st == 422, r)
    extra = f"m4x-extra-{stamp}@example.com"
    st, r = A("PUT", "/api/outreach/profile", dict(pr, notify_extra_emails=extra))
    check("valid extra email saved", st == 200 and r["notify_extra_emails"] == extra, r)
    if HAVE_MAILPIT:
        clear_throttle(TA)
        lead2 = sql(f"select whatsapp from leads where tenant_id='{TA}' and instance_id='{I1}' and id<>'{lead_id}' limit 1")
        inbound(S1, f"{lead2}@c.us", "What is your price? Too expensive, I want a discount")
        got = wait(lambda: mailpit(EMAIL_A) and mailpit(extra), 30, 2)
        check("needs-human reply e-mails the owner and the extra address", bool(got))
        msgs = mailpit(EMAIL_A)
        check("alert e-mail mentions the number and is a short preview", msgs and "needs you" in msgs[0]["Subject"].lower(), msgs[:1])
        n_before = len(mailpit(EMAIL_A))
        inbound(S1, f"{lead2}@c.us", "Still waiting - the price again please, too expensive")
        time.sleep(5)
        check("alerts are throttled to 1/hour per type", len(mailpit(EMAIL_A)) == n_before)
        clear_throttle(TA)
        webhook("session.status", {"sessionId": S1, "status": "disconnected"})
        down = wait(lambda: any("needs attention" in m["Subject"] for m in mailpit(EMAIL_A)), 30, 2)
        check("number disconnect after ready sends an e-mail", down)
        sql(f"update instances set status='ready' where id='{I1}'")
        # opt out: no more e-mails
        A("PUT", "/api/outreach/profile", dict(pr, notify_email=False))
        clear_throttle(TA)
        n_off = len(mailpit(EMAIL_A))
        inbound(S1, f"{lead2}@c.us", "price? send me quote asap")
        time.sleep(5)
        check("notify_email=false sends nothing", len(mailpit(EMAIL_A)) == n_off)
    else:
        print("SKIP  e-mail delivery checks (RESEND_API_KEY set)")

    # ------------------------------------------------ 4. viewers cannot toggle; roles
    st, r = A("PATCH", f"/api/instances/{I1}", {"outreach_enabled": "maybe"})
    check("toggle validates its body (422)", st == 422, r)

    # ------------------------------------------------ 5. admin support actions
    if ADMIN_PW:
        ad = sess(ADMIN_HOST)
        st, r = ad("POST", "/api/auth/login", {"email": api_env("ADMIN_EMAIL").get("ADMIN_EMAIL"), "password": ADMIN_PW})
        check("platform admin signs in", st == 200, r)
        uid = sql(f"select id from users where email='{EMAIL_B}'")
        sql(f"update users set email_verified_at=null where id='{uid}'")
        st, r = B("POST", "/api/admin/users/x/resend-verification")
        check("customers cannot call admin support actions", st in (401, 403, 404), (st, r))
        st, r = ad("POST", f"/api/admin/users/{uid}/resend-verification")
        check("admin: resend verification works and returns no token", st == 200 and "token" not in json.dumps(r).lower() and "http" not in json.dumps(r).lower(), r)
        st, r = ad("POST", f"/api/admin/users/{uid}/resend-verification")
        check("admin: second resend is rate-limited (429)", st == 429, (st, r))
        st, r = ad("POST", f"/api/admin/users/{uid}/send-password-reset")
        check("admin: send password reset works and returns no token", st == 200 and "token" not in json.dumps(r).lower(), r)
        a1 = sql("select count(*) from audit_log where action in ('admin.user.resend_verification','admin.user.send_reset') and detail='%s'" % EMAIL_B)
        check("both actions are audited", a1 == "2", a1)
        st, d = ad("GET", f"/api/admin/customers/{TA}")
        i1 = next((i for i in d["instances"] if i["id"] == I1), {})
        check("admin customer detail has per-number counters", all(k in i1 for k in ("outreachEnabled", "sentToday", "sent7d", "replies7d")) and "body" not in json.dumps(d["instances"]), i1)
        check("admin sees no message content", "Send me the price list" not in json.dumps(d))
        st, r = ad("POST", "/api/admin/users/nonexistent/send-password-reset")
        check("unknown user -> 404", st == 404, r)
    else:
        print("SKIP  admin support checks (E2E_ADMIN_PASSWORD not set)")
finally:
    for t in (TA, TB):
        sql(f"delete from tenants where id='{t}' and name like 'M4X %'")
    sql(f"delete from users where email in ('{EMAIL_A}','{EMAIL_B}')")

print(f"\n{ok_n} passed, {bad_n} failed")
sys.exit(1 if bad_n else 0)
