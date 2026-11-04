import http.cookiejar, json, re, subprocess, sys, time, urllib.request, urllib.error, uuid
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA, require_admin_password, require_fake_engine, require_mailpit  # noqa: F401
require_fake_engine()

ok_n = bad_n = 0
stamp = str(int(time.time()))


def sess():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    H = {"Host": APP_HOST, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}

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


def inbound(sid, frm, body):
    """Deliver a signed gateway webhook inside the docker network (the route is not exposed publicly)."""
    payload = json.dumps({"event": "message.received", "data": {"sessionId": sid, "from": frm, "body": body, "id": str(uuid.uuid4())}})
    code = "import hmac,hashlib,sys,httpx,os;b=sys.argv[1].encode();s='sha256='+hmac.new(os.environ['OPENWA_WEBHOOK_SECRET'].encode(),b,hashlib.sha256).hexdigest();r=httpx.post('http://localhost:8000/internal/wa-webhook',content=b,headers={'x-openwa-signature':s,'content-type':'application/json'});print(r.status_code)"
    r = subprocess.run(["docker", "compose", "exec", "-T", "api", "python", "-c", code, payload], cwd=INFRA, capture_output=True, text=True)
    return r.stdout.strip()


def setup(name, email):
    c = sess()
    st, me = c("POST", "/api/auth/signup", {"email": email, "password": "Sup3rSecretPw!", "company": name})
    sql(f"update users set email_verified_at=now() where email='{email}'")
    tid = me["tenant"]["id"]
    sql(f"update tenants set plan_id='growth' where id='{tid}'")
    return c, tid


A, TA = setup(f"M3E Alpha {stamp}", f"m3e-a-{stamp}@example.com")
B, TB = setup(f"M3E Beta {stamp}", f"m3e-b-{stamp}@example.com")

# a connected number, created "now" so the warm-up cap of day 0 (5) applies
IID, SID = str(uuid.uuid4()), "sess-" + stamp
sql(f"insert into instances (id,tenant_id,label,node_id,engine,openwa_name,openwa_id,phone,status,created_at) values ('{IID}','{TA}','Sales','wa1','baileys','cf-e2e-{stamp}','{SID}','923009990000','ready',now())")

rows = ["Company,Phone,Website,Business Type"] + [f"Acme {i},+92300123{4500 + i},acme{i}.example,Clinic" for i in range(14)]
st, an = A("POST", "/api/leads/import/analyze", {"csv": "\n".join(rows)})
st, imp = A("POST", "/api/leads/import", {"csv": "\n".join(rows), "mapping": an["mapping"], "list_name": "e2e"})
check("setup: 14 leads imported", imp["added"] == 14, imp)

# ---------------- profile validation
st, pr = A("GET", "/api/outreach/profile")
check("profile has all M3 fields with the contract defaults", all(k in pr for k in ("auto_run", "batch_size", "interval_hours", "delay_min", "delay_max", "dry_run", "auto_reply", "hold_pricing", "followups_enabled", "followup_day1", "followup_day2", "fallback_template")) and pr["dry_run"] is True and pr["auto_run"] is False and pr["batch_size"] == 5 and pr["delay_min"] == 60, pr)
check("delay_min > delay_max rejected (422)", A("PUT", "/api/outreach/profile", {**pr, "delay_min": 10, "delay_max": 5})[0] == 422)
check("bad timezone rejected (422)", A("PUT", "/api/outreach/profile", {**pr, "timezone": "Mars/Base"})[0] == 422)
prof = {**pr, "sender_name": "Ayesha", "company_name": "E2E Co", "offer": "We test things", "batch_size": 3, "daily_limit": 4, "delay_min": 1, "delay_max": 1, "timezone": "UTC", "active_from": "00:00", "active_to": "23:59", "timezone": "UTC"}
check("profile saved", A("PUT", "/api/outreach/profile", prof)[0] == 200)

# ---------------- batch -> drafts (dry-run)
st, r = A("POST", "/api/outreach/run-now", {})
check("run-now processes batch_size leads and queues drafts", st == 200 and r["processed"] == 3 and r["queued"] == 3 and r["failed"] == 0, r)
st, dr = A("GET", "/api/drafts?status=open")
check("drafts list: contract shape + counts", st == 200 and dr["total"] == 3 and set(dr["counts"]) == {"pending", "approved", "sending", "sent", "discarded", "failed"} and all(k in dr["rows"][0] for k in ("id", "leadId", "company", "whatsapp", "kind", "body", "incoming", "status", "note", "source", "needsHuman", "createdAt")), dr)
check("drafts are pending first messages written by the (fake) AI with the opt-out line and sender signature", all(d["status"] == "pending" and d["kind"] == "first" and "STOP" in d["body"] and "Ayesha from E2E Co" in d["body"] for d in dr["rows"]))
ids = [d["id"] for d in dr["rows"]]
st, lst = A("GET", "/api/leads?status=queued")
check("leads moved to queued", lst["total"] == 3)
check("approve is blocked while dry-run is ON (409)", A("POST", "/api/drafts/approve", {"ids": ids})[0] == 409)
st, stt = A("GET", "/api/outreach/status")
check("status: contract keys + last batch + instances with warm-up cap 5", all(k in stt for k in ("autoRun", "dryRun", "requireApproval", "inActiveHours", "nextRunAt", "lastBatch", "sentToday", "dailyLimit", "eligibleLeads", "instances")) and stt["lastBatch"]["processed"] == 3 and stt["instances"][0]["warmupCap"] == 5 and stt["eligibleLeads"] == 11, stt)

# edit + regenerate
d0 = dr["rows"][0]
st, e = A("PATCH", f"/api/drafts/{d0['id']}", {"body": "Hi custom\nIf this isn't relevant, reply STOP.\nAyesha from E2E Co"})
check("edit pending draft", st == 200 and e["source"] == "manual")
st, rg = A("POST", f"/api/drafts/{d0['id']}/regenerate", {"instruction": "shorter"})
check("regenerate with instruction", st == 200 and "fake-ai rewrite: shorter" in rg["body"], rg)

# ---------------- live: approve -> sent with gaps
A("PUT", "/api/outreach/profile", {**prof, "dry_run": False})
st, ap = A("POST", "/api/drafts/approve", {"ids": ids})
check("approve works once dry-run is OFF", st == 200 and ap["approved"] == 3, ap)
t0 = time.time()
done = wait(lambda: len([d for d in A("GET", "/api/drafts?status=sent")[1]["rows"]]) == 3, 60, 1)
check("all 3 sent by the worker", done)
dur = time.time() - t0
check("sends were paced by the gap (>= 2 s for 3 messages with a 1 s gap)", dur >= 2.0, f"{dur:.1f}s")
st, lst = A("GET", "/api/leads?status=contacted")
check("leads are contacted with instance + sentAt", lst["total"] == 3 and all(l["instanceId"] == IID and l["sentAt"] for l in lst["rows"]), lst["rows"][:1])
st, th = A("GET", f"/api/leads/{lst['rows'][0]['id']}/thread")
check("thread has the outbound message + the sent draft", len(th["messages"]) == 1 and th["messages"][0]["direction"] == "out" and th["messages"][0]["kind"] == "first" and len(th["drafts"]) == 1, th)
st, q = A("GET", "/api/drafts/queue")
check("queue shape", set(q) == {"sending", "remaining", "done", "nextAt", "current"}, q)
check("usage events recorded for the plan quota", int(sql(f"select count(*) from usage_events where tenant_id='{TA}' and kind='message'") or 0) == 3, sql(f"select kind,count(*) from usage_events where tenant_id='{TA}' group by 1"))

# ---------------- caps: daily limit (4), warm-up (5)
st, r = A("POST", "/api/outreach/run-now", {"limit": 10})
check("daily cap limits the next batch (budget 4 - 3 sent = 1)", r["processed"] == 1, r)
st, r = A("POST", "/api/outreach/run-now", {"limit": 10})
check("no budget left -> nothing processed", r["processed"] == 0, r)
st, dr = A("GET", "/api/drafts?status=pending")
dd = dr["rows"][0]
A("POST", "/api/drafts/approve", {"ids": [dd["id"]]})
wait(lambda: A("GET", "/api/drafts?status=sent")[1]["counts"]["sent"] == 4, 30, 1)
st, stt = A("GET", "/api/outreach/status")
check("4th message sent (sentToday == dailyLimit)", stt["sentToday"] == 4, stt)
# a manual draft for another lead now hits the daily cap and goes back to pending with a note
other = A("GET", "/api/leads?status=new&page_size=10")[1]["rows"][0]
st, md = A("POST", f"/api/leads/{other['id']}/draft")
check("manual draft endpoint creates a pending draft", st == 201 and md["status"] == "pending", md)
A("POST", "/api/drafts/approve", {"ids": [md["id"]]})
note = wait(lambda: [d for d in A("GET", "/api/drafts?status=pending")[1]["rows"] if d["id"] == md["id"] and d["note"]], 20, 1)
check("daily limit reached -> back to pending with a note", note and "Daily limit" in note[0]["note"], note)
# raise the daily cap: warm-up (5 on day 0) must still hold
A("PUT", "/api/outreach/profile", {**prof, "dry_run": False, "daily_limit": 100})
st, md2 = A("POST", f"/api/leads/{A('GET', '/api/leads?status=new&page_size=10')[1]['rows'][1]['id']}/draft")
A("POST", "/api/drafts/approve", {"ids": [md["id"], md2["id"]]})
wait(lambda: A("GET", "/api/drafts?status=sent")[1]["counts"]["sent"] >= 5, 30, 1)
time.sleep(8)
st, dr = A("GET", "/api/drafts?status=pending")
held = [d for d in dr["rows"] if "warm-up" in d["note"].lower()]
st, stt = A("GET", "/api/outreach/status")
check("warm-up cap (5 on day 0) stops the 6th first message even though the daily cap allows it", stt["sentToday"] == 5 and held, (stt["sentToday"], [d["note"] for d in dr["rows"]]))

# ---------------- inbound replies
leads_c = A("GET", "/api/leads?status=contacted&page_size=20")[1]["rows"]
L1, L2, L3 = leads_c[0], leads_c[1], leads_c[2]
frm = lambda l: f"{l['whatsapp']}@c.us"
check("gateway webhook accepted", inbound(SID, frm(L1), "How much does your service cost? Please send pricing.") == "200")
st, th = A("GET", f"/api/leads/{L1['id']}/thread")
rep = [d for d in th["drafts"] if d["kind"] == "reply"]
check("pricing question -> lead replied + reply draft needsHuman with note", rep and rep[0]["needsHuman"] and rep[0]["status"] == "pending" and rep[0]["note"] == "NEEDS HUMAN: pricing" and rep[0]["incoming"].startswith("How much"), th["drafts"])
st, ib = A("GET", "/api/inbox?filter=needs_human")
check("inbox needs_human filter lists it with the contract keys", st == 200 and any(r["leadId"] == L1["id"] and r["needsHuman"] and {"leadId", "company", "whatsapp", "status", "lastMessage", "lastDirection", "lastAt", "needsHuman"} <= set(r) and {"instanceId", "via"} <= set(r) for r in ib), ib)
st, nt = A("GET", "/api/notifications")
check("notification created (needs_human) with the contract keys", nt["unread"] >= 1 and any(n["type"] == "needs_human" and set(n) == {"id", "type", "title", "body", "link", "leadId", "read", "createdAt"} for n in nt["rows"]), nt["rows"][:2])
# long sentence containing 'stop' is NOT an opt-out
inbound(SID, frm(L2), "We could never stop thinking about your offer, please send more information about services today")
st, l2 = A("GET", f"/api/leads/{L2['id']}/thread")
check("long message containing 'stop' is not treated as opt-out (lead replied)", A("GET", "/api/leads?status=replied&page_size=50")[1]["rows"] and any(x["id"] == L2["id"] for x in A("GET", "/api/leads?status=replied&page_size=50")[1]["rows"]))
# strict STOP
st, pend_before = A("POST", f"/api/leads/{L3['id']}/reply", {"text": "hello"})
inbound(SID, frm(L3), "STOP")
stopped = A("GET", "/api/leads?status=stop")[1]
check("strict STOP -> lead stop", any(x["id"] == L3["id"] for x in stopped["rows"]))
check("STOP discards the lead's pending/approved drafts and refuses manual replies (409)", A("POST", f"/api/leads/{L3['id']}/reply", {"text": "again"})[0] == 409 and not [d for d in A("GET", f"/api/leads/{L3['id']}/thread")[1]["drafts"] if d["status"] in ("pending", "approved")])
# auto-reply
A("PUT", "/api/outreach/profile", {**prof, "dry_run": False, "daily_limit": 100, "auto_reply": True})
leads_c2 = [l for l in A("GET", "/api/leads?status=contacted&page_size=20")[1]["rows"]]
L4 = leads_c2[0]
inbound(SID, frm(L4), "Sounds interesting, tell me more please")
st, th4 = A("GET", f"/api/leads/{L4['id']}/thread")
ar = [d for d in th4["drafts"] if d["kind"] == "reply"]
check("auto_reply: non-sensitive reply is approved automatically with a human delay", ar and ar[0]["status"] == "approved" and not ar[0]["needsHuman"], th4["drafts"])
nb = sql(f"select (not_before > now() + interval '10 seconds') from drafts where id='{ar[0]['id']}'")
check("auto reply waits 15-45 s before sending", nb == "t", nb)
sql(f"update drafts set not_before=now() where id='{ar[0]['id']}'")
wait(lambda: [d for d in A("GET", f"/api/leads/{L4['id']}/thread")[1]["drafts"] if d["kind"] == "reply" and d["status"] == "sent"], 30, 1)
check("auto reply was sent by the worker (outbound reply message stored)", [m for m in A("GET", f"/api/leads/{L4['id']}/thread")[1]["messages"] if m["kind"] == "reply" and m["direction"] == "out"])

# ---------------- manual reply (sent through the sender)
L5 = [l for l in A("GET", "/api/leads?status=contacted&page_size=20")[1]["rows"]][0]
st, mr = A("POST", f"/api/leads/{L5['id']}/reply", {"text": "Thanks, here is the info you asked for"})
wait(lambda: [d for d in A("GET", f"/api/leads/{L5['id']}/thread")[1]["drafts"] if d["id"] == mr["id"] and d["status"] == "sent"], 30, 1)
check("manual reply goes out via the sender", st == 201 and [d for d in A("GET", f"/api/leads/{L5['id']}/thread")[1]["drafts"] if d["id"] == mr["id"] and d["status"] == "sent"])

# ---------------- follow-ups
A("PUT", "/api/outreach/profile", {**prof, "dry_run": False, "daily_limit": 100, "followups_enabled": True, "followup_day1": 3, "followup_day2": 7})
cand = [l for l in A("GET", "/api/leads?status=contacted&page_size=50")[1]["rows"] if l["id"] not in (L1["id"], L2["id"], L4["id"], L5["id"])]
FU = cand[0] if cand else L5
sql(f"delete from messages where lead_id='{FU['id']}' and direction='in'")
sql(f"update leads set sent_at = now() - interval '4 days', followup_stage=0 where id='{FU['id']}'")
fu = wait(lambda: [d for d in A("GET", f"/api/leads/{FU['id']}/thread")[1]["drafts"] if d["kind"] == "followup"], 70, 2)
check("follow-up #1 drafted after 3 days with no reply (stage -> 1)", fu and fu[0]["status"] == "pending" and sql(f"select followup_stage from leads where id='{FU['id']}'") == "1", fu)
A("POST", f"/api/drafts/{fu[0]['id']}/discard")
sql(f"update leads set sent_at = now() - interval '8 days' where id='{FU['id']}'")
fu2 = wait(lambda: [d for d in A("GET", f"/api/leads/{FU['id']}/thread")[1]["drafts"] if d["kind"] == "followup" and d["status"] == "pending"], 70, 2)
check("follow-up #2 drafted after 7 days (stage -> 2)", fu2 and sql(f"select followup_stage from leads where id='{FU['id']}'") == "2", fu2)
# a lead that replied never gets a follow-up
RP = L1
sql(f"update leads set sent_at = now() - interval '9 days', followup_stage=0, status='contacted' where id='{RP['id']}'")
time.sleep(35)
check("a lead that already replied is never followed up", not [d for d in A("GET", f"/api/leads/{RP['id']}/thread")[1]["drafts"] if d["kind"] == "followup"])

# ---------------- restart recovery
rid = str(uuid.uuid4())
sql(f"insert into drafts (id,tenant_id,lead_id,kind,body,status,note,incoming,source,needs_human,instruction,not_before,created_at,sending_at) values ('{rid}','{TA}','{FU['id']}','followup','x','sending','','','ai',false,'',now(),now(),now())")
subprocess.run(["docker", "compose", "restart", "worker"], cwd=INFRA, capture_output=True)
rec = wait(lambda: sql(f"select status from drafts where id='{rid}'") == "failed", 40, 2)
check("restart recovery: a draft stuck in 'sending' becomes failed with a 'check WhatsApp' note", rec and "check the chat" in sql(f"select note from drafts where id='{rid}'"))

# ---------------- scheduler
A("PUT", "/api/outreach/profile", {**prof, "dry_run": True, "auto_run": True, "interval_hours": 1, "daily_limit": 100, "batch_size": 2})
tb = wait(lambda: int(sql(f"select count(*) from batches where tenant_id='{TA}' and trigger='timer'") or 0) >= 1, 75, 3)
check("scheduler runs a timer batch for auto_run workspaces inside active hours", tb)
st, stt = A("GET", "/api/outreach/status")
check("status shows the next scheduled run (>= interval after the last timer batch)", stt["autoRun"] and stt["nextRunAt"], stt)
A("PUT", "/api/outreach/profile", {**prof, "auto_run": False})
st, bl = A("GET", "/api/outreach/batches")
check("batches list shape", st == 200 and bl and set(bl[0]) == {"id", "startedAt", "finishedAt", "mode", "processed", "queued", "sent", "failed", "error"}, bl[:1])

# ---------------- enrichment queue
st, en = A("POST", "/api/leads/enrich", {"all": True})
check("enrich queues leads", st == 202 and en["queued"] >= 1, en)
fin = wait(lambda: not A("GET", "/api/leads/enrich/status")[1]["running"] or A("GET", "/api/leads/enrich/status")[1]["done"] >= en["queued"], 60, 2)
check("enrich status reaches done == total", fin)
check("enriched flag set on leads", int(sql(f"select count(*) from leads where tenant_id='{TA}' and enriched_at is not null") or 0) >= en["queued"])
st, rl = A("POST", f"/api/leads/{L5['id']}/research")
check("research endpoint returns the lead (research text filled)", st == 200 and rl["research"] and rl["enriched"], rl)

# ---------------- overview
st, ov = A("GET", "/api/overview")
check("overview: contract keys", st == 200 and set(ov) == {"counts", "sentToday", "dailyLimit", "replyRate", "pendingApprovals", "needsHuman", "unreadNotifications", "daily", "scheduler", "recent"}, list(ov))
check("overview counts keys", set(ov["counts"]) == {"total", "new", "queued", "contacted", "replied", "stop", "no_whatsapp", "failed", "hold", "verify", "withNumber", "enriched"}, ov["counts"])
check("overview: 14 daily points with sent/replies, recent feed", len(ov["daily"]) == 14 and set(ov["daily"][0]) == {"day", "sent", "replies"} and ov["recent"] and set(ov["recent"][0]) == {"at", "type", "title", "body", "leadId"} and sum(x["sent"] for x in ov["daily"]) >= 5, ov["daily"][-1])
check("overview: reply rate + needsHuman numbers", ov["replyRate"] > 0 and ov["needsHuman"] >= 1)
A("POST", "/api/notifications/read", {})
check("mark all notifications read", A("GET", "/api/notifications")[1]["unread"] == 0)

# ---------------- tenant isolation
check("tenant B sees no drafts / leads / overview data of A", A("GET", "/api/drafts?status=open")[1]["total"] >= 0 and B("GET", "/api/drafts?status=open")[1]["total"] == 0 and B("GET", "/api/leads")[1]["total"] == 0 and B("GET", "/api/overview")[1]["counts"]["total"] == 0 and B("GET", "/api/inbox")[1] == [] and B("GET", "/api/notifications")[1]["rows"] == [])
check("tenant B cannot read A's thread / draft / reply / research (404)", all(B(m, p, bd)[0] == 404 for m, p, bd in [("GET", f"/api/leads/{L1['id']}/thread", None), ("POST", f"/api/leads/{L1['id']}/draft", None), ("POST", f"/api/leads/{L1['id']}/reply", {"text": "x"}), ("POST", f"/api/leads/{L1['id']}/research", None), ("PATCH", f"/api/drafts/{mr['id']}", {"body": "x"}), ("POST", f"/api/drafts/{mr['id']}/discard", None)]))
check("B's approve of A's draft id changes nothing", B("POST", "/api/drafts/approve", {"ids": [md["id"]]})[0] in (200, 409) and sql(f"select status from drafts where id='{md['id']}'") != "sent_by_b")

print(f"\n{ok_n} passed, {bad_n} failed")
sql(f"delete from tenants where id in ('{TA}','{TB}')")
sql("delete from users where email like 'm3e-%@example.com'")
sys.exit(1 if bad_n else 0)
