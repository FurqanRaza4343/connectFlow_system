"""E2E for WhatsApp Status on the SaaS (needs AI_FAKE + OUTREACH_FAKE_SEND; the worker does the due posts and the campaign AI steps).

Posts (draft, preview, picture, upload, post now, dry-run, plan limit, delete, scheduled post picked up by the worker), the brand kit,
and campaigns (plan, questions, write, review, approve, pause, resume, cancel, calendar, stats) with two workspaces for isolation."""
import base64, http.cookiejar, json, subprocess, sys, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from e2e_env import APP_HOST, BASE, INFRA, require_fake_engine

require_fake_engine()
HOST = {"Host": APP_HOST, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}
ok_n = bad_n = 0
PNG_1PX = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def sess():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(method, path, body=None, raw=False):
        req = urllib.request.Request(BASE + path, method=method, headers=HOST, data=json.dumps(body).encode() if body is not None else None)
        try:
            r = op.open(req, timeout=120)
            data = r.read()
            return r.status, (data if raw else (json.loads(data) if data else {}))
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


def wait(fn, timeout=60, step=2):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


stamp = str(int(time.time()))
NA, NB, NC = f"Status E2E A {stamp}", f"Status E2E B {stamp}", f"Status E2E C {stamp}"
A, B, C = sess(), sess(), sess()
for c, name, tag in ((A, NA, "a"), (B, NB, "b"), (C, NC, "c")):
    st, _ = c("POST", "/api/auth/signup", {"email": f"stat-{tag}-{stamp}@example.com", "password": "Sup3rSecretPw!", "company": name})
    assert st in (200, 201), "signup failed"
sql(f"update tenants set plan_id='growth' where name='{NA}'")    # A: growth (5 a day, campaigns); B and C stay on the trial plan


def live(c):   # the workspace's Outreach "dry run" switch is on by default: turn it off so posts really "go out" (fake send here)
    st, pr = c("GET", "/api/outreach/profile")
    c("PUT", "/api/outreach/profile", {**pr, "dry_run": False})


# ---------------------------------------------------------------- overview + brand kit
st, ov = A("GET", "/api/status")
check("overview: renderer is installed, plan limits shown, no posts yet", st == 200 and ov["capabilities"]["renderer"] is True and ov["capabilities"]["plan"]["postsPerDay"] == 5 and ov["posts"] == [], ov.get("capabilities"))
check("the 8 designs are offered", len(ov["capabilities"]["templates"]) == 8)
st, p = A("PUT", "/api/status/profile", {"accent": "#e11d48", "dark": "#3a0b17", "button_text": "Chat now", "button_link": "wa", "wa_number": "0300 1234567", "wa_text": "Hi there",
                                         "footer": "deonegroup.com", "templates": "bold,frame", "offers": "30% off chatbot setup", "language": "Roman Urdu"})
check("brand kit saves", st == 200 and p["accent"] == "#e11d48" and p["button_link"] == "wa" and p["wa_number"] == "923001234567" and p["templates"] == "bold,frame" and p["language"] == "Roman Urdu", p)
for bad in ({"accent": "red"}, {"button_link": "javascript:alert(1)"}, {"times": "25:00"}, {"templates": "gradient,nope"}, {"approval": "yolo"}, {"wa_number": "abc"}):
    check(f"brand kit refuses {list(bad)[0]}={list(bad.values())[0]}", A("PUT", "/api/status/profile", bad)[0] == 422)
check("a trial workspace cannot switch the autopilot on (402)", B("PUT", "/api/status/profile", {"auto": True})[0] == 402)

# ---------------------------------------------------------------- ideas, preview, create
st, ide = A("POST", "/api/status/ideas", {"prompt": "30% off offer", "count": 3})
check("3 ideas from the AI", st == 200 and len(ide["ideas"]) == 3 and all(i["headline"] for i in ide["ideas"]), ide)
st, pv = A("POST", "/api/status/preview", {"headline": "Preview check", "template": "badge", "badge": "30% OFF", "cta": "Message us", "btn_link": "wa"})
png = base64.b64decode(pv["png"].split(",")[1]) if st == 200 else b""
check("preview returns a 1080x1920 PNG", st == 200 and png[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(png[16:20], "big") == 1080 and int.from_bytes(png[20:24], "big") == 1920)
st, _ = A("POST", "/api/status/preview", {"headline": "x", "btn_link": "javascript:alert(1)"})
check("preview refuses a bad link", st == 422)

st, cr = A("POST", "/api/status/posts", {"headline": "Automate your chats", "sub": "AI agents 24/7", "cta": "Message us", "caption": "We build AI agents.", "link": "deonegroup.com", "btn_link": "wa", "action": "draft"})
post1 = cr["post"] if st == 201 else {}
check("create a draft with a picture and a wa.me link", st == 201 and post1["state"] == "draft" and post1["hasImage"] and post1["btnLink"] == "wa", cr)
st, img = A("GET", f"/api/status/posts/{post1['id']}/image", raw=True)
check("the picture is served to its own workspace", st == 200 and img[:4] == b"\x89PNG")
check("another workspace cannot read the post or its picture (404)", B("GET", f"/api/status/posts/{post1['id']}/image", raw=True)[0] == 404 and B("POST", f"/api/status/posts/{post1['id']}/post")[0] == 404)
st, _ = A("POST", "/api/status/posts", {"headline": "x", "btn_link": "javascript:alert(1)"})
check("a bad button link is refused when creating (422)", st == 422)

# uploads
check("an upload whose bytes are not a PNG is refused", A("POST", "/api/status/upload", {"data_url": "data:image/png;base64,AAAA"})[0] == 422)
st, up = A("POST", "/api/status/upload", {"data_url": f"data:image/png;base64,{PNG_1PX}"})
key = up.get("file", "")
check("a valid picture is stored", st == 200 and key != "")
st, ps = A("POST", "/api/status/posts", {"headline": "On my picture", "style": "own", "bg_upload": key, "overlay": 60, "action": "draft"})
check("a post can use the uploaded picture", st == 201 and ps["post"]["hasBg"] and ps["post"]["bgUpload"], ps)
check("another workspace cannot use that upload key", B("POST", "/api/status/posts", {"headline": "steal", "style": "own", "bg_upload": key})[0] == 422)

# ---------------------------------------------------------------- posting
st, r = A("POST", f"/api/status/posts/{post1['id']}/post")
check("dry run (the default): nothing is posted, the draft stays", st == 200 and r.get("dryRun") is True)
live(A)
st, r = A("POST", f"/api/status/posts/{post1['id']}/post")
st, ov = A("GET", "/api/status")
row = next((x for x in ov["posts"] if x["id"] == post1["id"]), {})
check("post now: posted, with a status id and a 24 h expiry", st == 200 and row.get("state") == "posted" and row.get("waId") and row.get("expiresAt"), row)
st, r = A("POST", f"/api/status/posts/{post1['id']}/post")
check("a posted status cannot be posted twice (409)", st == 409)
st, r = A("POST", f"/api/status/posts/{post1['id']}/delete-wa")
st, ov = A("GET", "/api/status")
row = next((x for x in ov["posts"] if x["id"] == post1["id"]), {})
check("delete from WhatsApp works and the row is cancelled", st == 200 and row.get("state") == "cancelled" and not row.get("waId"), row)

# edit + cancel + schedule rules
st, up2 = A("PUT", f"/api/status/posts/{ps['post']['id']}", {"headline": "Edited headline", "style": "own", "bg_upload": "", "overlay": 20, "pos": "top"})
check("edit a draft: new headline, the picture is redrawn", st == 200 and up2["post"]["headline"] == "Edited headline" and up2["post"]["overlay"] == 20 and up2["post"]["pos"] == "top", up2)
check("scheduling in the past is refused (422)", A("POST", f"/api/status/posts/{ps['post']['id']}/schedule", {"scheduled_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})[0] == 422)
st, rw = A("POST", f"/api/status/posts/{ps['post']['id']}/rewrite", {"instruction": "shorter"})
check("rewrite makes new words with the AI", st == 200 and rw["post"]["headline"] != "Edited headline", rw)

# the worker posts what is due
st, sc = A("POST", "/api/status/posts", {"headline": "Scheduled by the worker", "action": "schedule", "scheduled_at": (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()})
sid = sc["post"]["id"] if st == 201 else ""
got = wait(lambda: next((x for x in A("GET", "/api/status")[1]["posts"] if x["id"] == sid and x["state"] == "posted"), None), timeout=110, step=5)
check("a scheduled post is posted by the worker when it is due", got is not None, A("GET", "/api/status")[1]["posts"][:2])

# trial plan limit: 1 post a day
live(C)
st, c1 = C("POST", "/api/status/posts", {"headline": "Trial one", "action": "post"})
st2, c2 = C("POST", "/api/status/posts", {"headline": "Trial two", "action": "post"})
check("trial plan: the first post goes out, the second is refused (429, plan limit)", st == 201 and c1["post"]["state"] == "posted" and st2 == 429 and "limit" in json.dumps(c2).lower(), (st, st2, c2))
check("trial plan: campaigns are not included (402)", C("POST", "/api/status/campaigns", {"brief": "Promote our chatbot", "days": 3, "interval_hours": 24})[0] == 402)

# ---------------------------------------------------------------- campaigns (growth)
tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
body = {"brief": "Promote our AI chatbot service to small businesses", "days": 3, "interval_hours": 12, "window_from": "08:00", "window_to": "22:00", "start_date": tomorrow, "goal": "leads"}
st, cc = A("POST", "/api/status/campaigns", body)
cid = cc["campaign"]["id"] if st == 201 else ""
check("create a campaign: returns at once in state planning", st == 201 and cc["campaign"]["state"] == "planning" and cc["campaign"]["total"] == 6, cc)
camp = wait(lambda: (lambda c: c if c["state"] != "planning" else None)(A("GET", f"/api/status/campaigns/{cid}")[1]["campaign"]), timeout=60, step=2)
check("the worker plans it: a roadmap with a theme for every slot, ending with a call to action", camp and camp["state"] == "planned" and len(camp["slots"]) == 6 and all(s["theme"] for s in camp["slots"]) and camp["slots"][-1]["theme"] == "cta", camp and camp["state"])
check("generate starts the writing in the worker", A("POST", f"/api/status/campaigns/{cid}/generate")[0] == 200)
camp = wait(lambda: (lambda c: c if c["state"] in ("review", "failed") else None)(A("GET", f"/api/status/campaigns/{cid}")[1]["campaign"]), timeout=180, step=3)
check("all 6 posts are written as drafts with pictures and planned times", camp and camp["state"] == "review" and camp["counts"].get("draft") == 6, camp and (camp["state"], camp["counts"], camp["error"]))
st, ov = A("GET", "/api/status")
cposts = [x for x in ov["posts"] if x["campaignId"] == cid]
check("each campaign post keeps its day, theme and time", len(cposts) == 6 and all(x["planTheme"] and x["planDay"] and x["scheduledAt"] and x["hasImage"] for x in cposts))
check("another workspace cannot see the campaign (404)", B("GET", f"/api/status/campaigns/{cid}")[0] == 404 and B("POST", f"/api/status/campaigns/{cid}/approve")[0] == 404)
check("a campaign in review cannot be generated again (409)", A("POST", f"/api/status/campaigns/{cid}/generate")[0] == 409)
st, ap = A("POST", f"/api/status/campaigns/{cid}/approve")
check("approve all schedules every post", st == 200 and ap["scheduled"] == 6, ap)
check("pause turns them back into drafts, resume schedules them again",
      A("POST", f"/api/status/campaigns/{cid}/pause")[0] == 200 and A("GET", f"/api/status/campaigns/{cid}")[1]["campaign"]["counts"].get("draft") == 6
      and A("POST", f"/api/status/campaigns/{cid}/resume")[0] == 200 and A("GET", f"/api/status/campaigns/{cid}")[1]["campaign"]["counts"].get("scheduled") == 6)
st, cal = A("GET", f"/api/status/calendar?month={tomorrow[:7]}")
check("the calendar lists the campaign posts", st == 200 and sum(1 for i in cal["items"] if i["campaignId"] == cid) >= 1, cal.get("month"))
st, sts = A("GET", "/api/status/stats")
check("stats: a 25-day series, theme counts, next post time", st == 200 and len(sts["series"]) == 25 and len(sts["themes"]) >= 3 and sts["nextAt"], sts.get("themes"))
check("cancel stops the campaign", A("POST", f"/api/status/campaigns/{cid}/cancel")[0] == 200 and A("GET", f"/api/status/campaigns/{cid}")[1]["campaign"]["state"] == "cancelled")
check("a cancelled campaign can be removed", A("DELETE", f"/api/status/campaigns/{cid}")[0] == 200 and A("GET", f"/api/status/campaigns/{cid}")[0] == 404)

# unclear brief: the AI asks, then plans
st, q = A("POST", "/api/status/campaigns", {**body, "brief": "ASKME promote something"})
qid = q["campaign"]["id"]
camp = wait(lambda: (lambda c: c if c["state"] != "planning" else None)(A("GET", f"/api/status/campaigns/{qid}")[1]["campaign"]), timeout=60, step=2)
check("an unclear brief makes the AI ask (at most 3 questions)", camp and camp["state"] == "asking" and 1 <= len(camp["questions"]) <= 3, camp and camp["state"])
A("POST", f"/api/status/campaigns/{qid}/answer", {"answers": {camp["questions"][0]["id"]: "Chatbots"}})
camp = wait(lambda: (lambda c: c if c["state"] != "planning" else None)(A("GET", f"/api/status/campaigns/{qid}")[1]["campaign"]), timeout=60, step=2)
check("after the answers it plans and never asks again", camp and camp["state"] == "planned")
check("too many posts for the plan is refused (422)", A("POST", "/api/status/campaigns", {**body, "interval_hours": 0, "per_day": 5, "days": 45})[0] == 422)
A("DELETE", f"/api/status/campaigns/{qid}")

st, ovb = B("GET", "/api/status")
check("workspace B sees none of A's posts", st == 200 and ovb["posts"] == [])

sql("delete from users where email like 'stat-%@example.com'")
sql("delete from tenants where name like 'Status E2E%'")
print(f"\n{ok_n} passed, {bad_n} failed")
sys.exit(1 if bad_n else 0)
