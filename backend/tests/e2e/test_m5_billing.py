"""M5: Trial = exactly one number (atomic), Stripe checkout/portal/webhook, plan mapping, over-limit numbers, isolation.
Needs the api/worker started with STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET and STRIPE_API_BASE=http://host.docker.internal:<E2E_FAKE_STRIPE_PORT>
(run via the wrapper that sets and restores infra/.env) and E2E_ADMIN_PASSWORD for clean-up. Talks to a fake Stripe started here.
Creates real gateway sessions for the 1-number checks and deletes them at the end. Test workspaces are named 'M5A ...'."""
import hashlib, hmac, http.cookiejar, http.server, json, os, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request, uuid  # noqa: E401
from e2e_env import ADMIN_HOST, APP_HOST, BASE, INFRA, api_env, require_admin_password, skip

ADMIN_PW = require_admin_password()
PORT = int(os.environ.get("E2E_FAKE_STRIPE_PORT", "0"))
WH_SECRET = os.environ.get("E2E_STRIPE_WEBHOOK_SECRET", "")
env = api_env("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_API_BASE", "ADMIN_EMAIL")
if not (PORT and WH_SECRET and env.get("STRIPE_SECRET_KEY") and f":{PORT}" in env.get("STRIPE_API_BASE", "")):
    skip("needs the api started with the fake-Stripe settings (see the wrapper: E2E_FAKE_STRIPE_PORT / E2E_STRIPE_WEBHOOK_SECRET)")
SK = env["STRIPE_SECRET_KEY"]

ok_n = bad_n = 0
stamp = str(int(time.time()))
PW = "Sup3rSecretPw!"


# ------------------------------------------------------------------ fake Stripe
CALLS: list[dict] = []
CUSTOMERS = {"n": 0}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _handle(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode())) if n else {}
        CALLS.append({"method": self.command, "path": self.path, "form": form, "auth": self.headers.get("Authorization", "")})
        if self.path == "/v1/customers":
            CUSTOMERS["n"] += 1
            return self._reply({"id": f"cus_fake_{stamp}_{CUSTOMERS['n']}"})
        if self.path == "/v1/checkout/sessions":
            return self._reply({"id": "cs_test_1", "url": "https://checkout.stripe.test/c/pay/cs_test_1"})
        if self.path == "/v1/billing_portal/sessions":
            return self._reply({"url": "https://billing.stripe.test/p/session_1"})
        if self.path.startswith("/v1/subscriptions/"):
            return self._reply({"id": self.path.rsplit("/", 1)[-1], "items": {"data": [{"id": "si_fake_1"}]}})
        self._reply({"error": {"message": "not found"}}, 404)

    do_GET = do_POST = _handle


srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()


# ------------------------------------------------------------------ helpers
def sess(host=APP_HOST):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    H = {"Host": host, "X-Requested-With": "ConnectFlow", "Content-Type": "application/json"}

    def call(method, path, body=None, headers=None, raw=None):
        h = {**H, **(headers or {})}
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(BASE + path, method=method, headers=h, data=data)
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


def sh(*cmd):
    return subprocess.run(["docker", "compose", *cmd], cwd=INFRA, capture_output=True, text=True).stdout.strip()


def sql(q):
    return sh("exec", "-T", "postgres", "psql", "-U", "connectflow", "-d", "connectflow", "-tAc", q)


def clear_signup_throttle():
    sh("exec", "-T", "redis", "sh", "-c", "redis-cli --scan --pattern 'signup:*' | xargs -r redis-cli del")


def signup(company, email):
    clear_signup_throttle()
    c = sess()
    st, me = c("POST", "/api/auth/signup", {"email": email, "password": PW, "company": company})
    assert st in (200, 201), (st, me)
    sql(f"update users set email_verified_at=now() where email='{email}'")
    return c, me["tenant"]["id"]


def login(email):
    c = sess()
    st, _ = c("POST", "/api/auth/login", {"email": email, "password": PW})
    assert st == 200, st
    return c


def event(typ, obj, eid=None):
    return {"id": eid or f"evt_{uuid.uuid4().hex[:20]}", "object": "event", "type": typ, "data": {"object": obj}}


def sign(payload: bytes, secret=WH_SECRET, ts=None):
    ts = int(time.time()) if ts is None else ts
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()


ANON = sess()


def webhook(ev, header="auto", body=None):
    payload = body if body is not None else json.dumps(ev).encode()
    h = {"Stripe-Signature": sign(payload)} if header == "auto" else ({"Stripe-Signature": header} if header else {})
    # no cookie, no CSRF header: a public endpoint authenticated only by the signature
    req = urllib.request.Request(BASE + "/api/stripe/webhook", method="POST", data=payload, headers={"Host": APP_HOST, "Content-Type": "application/json", **h})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def tstate(tid):
    r = sql(f"select plan_id, subscription_status, coalesce(stripe_customer_id,''), coalesce(stripe_subscription_id,''), cancel_at_period_end from tenants where id='{tid}'")
    return r.split("|") if r else []


EA, EB, EC = f"m5a-a-{stamp}@example.com", f"m5a-b-{stamp}@example.com", f"m5a-c-{stamp}@example.com"
A, TA = signup(f"M5A Alpha {stamp}", EA)
B, TB = signup(f"M5A Beta {stamp}", EB)
C0, TC0 = signup(f"M5A Carol {stamp}", EC)
made_instances: list[tuple] = []
try:
    # ------------------------------------------------ 1. Trial = exactly one number
    st, bl = A("GET", "/api/billing")
    check("new workspace is on Trial with 1 number, stripe enabled", st == 200 and bl["plan"]["id"] == "trial" and bl["plan"]["maxInstances"] == 1 and bl["stripe"]["enabled"] is True and bl["stripe"]["status"] == "", bl)
    check("billing usage exposes Trial limits (50 msgs, 20 AI drafts, 1 seat)", bl["plan"]["maxMsgsDay"] == 50 and bl["plan"]["aiCallsDay"] == 20 and bl["plan"]["maxMembers"] == 1, bl["plan"])

    A2 = login(EA)  # a second browser session of the same owner: two parallel "add number" calls
    res = [None, None]
    def add(i, c): res[i] = c("POST", "/api/instances", {"label": f"Race {i}"})
    ts = [threading.Thread(target=add, args=(0, A)), threading.Thread(target=add, args=(1, A2))]
    [t.start() for t in ts]; [t.join() for t in ts]
    codes = sorted(r[0] for r in res)
    check("parallel add: exactly one succeeds (201) and one is refused (402)", codes == [201, 402], [(r[0], str(r[1])[:120]) for r in res])
    win = next(r for r in res if r[0] == 201)[1]
    lose = next(r for r in res if r[0] == 402)[1]
    check("402 carries limit/used/plan/upgradeUrl and a plain message", lose.get("limit") == 1 and lose.get("used") == 1 and lose.get("plan") == "trial" and lose.get("upgradeUrl") == "/#billing" and "1 WhatsApp number" in lose.get("detail", ""), lose)
    made_instances.append((A, win["id"]))
    check("only one instance row exists for the workspace", sql(f"select count(*) from instances where tenant_id='{TA}'") == "1")
    st, r = A("POST", "/api/instances", {"label": "third"})
    check("a later add is refused too (402)", st == 402, r)
    st, r = B("POST", "/api/instances", {"label": "Beta line"})
    check("another Trial workspace is unaffected (can add its own 1)", st == 201, r)
    if st == 201:
        made_instances.append((B, r["id"]))
    st, lst = A("GET", "/api/instances")
    check("instances list: overLimit false, isolated to the tenant", st == 200 and len(lst) == 1 and lst[0]["overLimit"] is False, lst)

    # ------------------------------------------------ 2. checkout / portal (fake Stripe)
    st, r = ANON("POST", "/api/billing/checkout", {"plan_id": "starter"})
    check("checkout needs a login (401)", st == 401, r)
    # a second member of workspace A (operator): move user C's membership into A
    sql(f"update memberships set tenant_id='{TA}', role='operator' where user_id=(select id from users where email='{EC}')")
    OP = login(EC)
    st, me = OP("GET", "/api/auth/me")
    check("test operator belongs to workspace A", st == 200 and me["role"] == "operator" and me["tenant"]["id"] == TA, me)
    st, r = OP("POST", "/api/billing/checkout", {"plan_id": "starter"})
    check("operator cannot start checkout (403)", st == 403, r)
    st, r = OP("POST", "/api/billing/portal")
    check("operator cannot open the portal (403)", st == 403, r)
    st, r = A("POST", "/api/billing/checkout", {"plan_id": "nope"})
    check("unknown plan (404)", st == 404, r)
    st, r = A("POST", "/api/billing/checkout", {"plan_id": "trial"})
    check("the free plan cannot be bought (400)", st == 400, r)
    st, r = A("POST", "/api/billing/portal")
    check("portal before any subscription (409)", st == 409, r)
    st, r = A("POST", "/api/billing/upgrade", {"plan_id": "starter"})
    check("manual request form is closed while online payments are on (409)", st == 409, r)

    n0 = len(CALLS)
    st, r = A("POST", "/api/billing/checkout", {"plan_id": "starter"})
    check("owner gets a Stripe Checkout url", st == 200 and r.get("url", "").startswith("https://checkout.stripe.test/"), r)
    new = CALLS[n0:]
    cust = next((c for c in new if c["path"] == "/v1/customers"), None)
    sess_call = next((c for c in new if c["path"] == "/v1/checkout/sessions"), None)
    check("a Stripe customer was created with the tenant id in metadata", cust and cust["form"].get("metadata[tenant_id]") == TA and cust["form"].get("email") == EA, cust)
    f = (sess_call or {}).get("form", {})
    check("session is a monthly USD subscription for Starter ($19)", f.get("mode") == "subscription" and f.get("line_items[0][price_data][unit_amount]") == "1900" and f.get("line_items[0][price_data][currency]") == "usd" and f.get("line_items[0][price_data][recurring][interval]") == "month", f)
    check("metadata + client_reference_id carry tenant and plan (checkout and subscription)", f.get("client_reference_id") == TA and f.get("metadata[plan_id]") == "starter" and f.get("subscription_data[metadata][tenant_id]") == TA, f)
    check("return urls land back on the billing page", "/?checkout=success#billing" in f.get("success_url", "") and "/?checkout=cancel#billing" in f.get("cancel_url", ""), f)
    check("request was authenticated with the secret key (not logged)", sess_call and sess_call["auth"].startswith("Basic "))
    check("checkout alone does NOT change the plan", tstate(TA)[:2] == ["trial", ""], tstate(TA))
    CA = tstate(TA)[2]
    check("customer id was stored on the tenant", CA.startswith("cus_fake_"), CA)
    st, r = A("POST", "/api/billing/portal")
    check("owner gets a portal url once a customer exists", st == 200 and r.get("url", "").startswith("https://billing.stripe.test/"), r)
    st, r = B("POST", "/api/billing/portal")
    check("another workspace without a customer still gets 409 (no cross-tenant customer)", st == 409, r)

    # ------------------------------------------------ 3. webhook: signature, idempotency, plan mapping
    ev = event("checkout.session.completed", {"mode": "subscription", "customer": CA, "subscription": "sub_A", "client_reference_id": TA, "payment_status": "paid", "metadata": {"tenant_id": TA, "plan_id": "starter"}})
    raw = json.dumps(ev).encode()
    st, r = webhook(ev, header=None)
    check("webhook without a signature is rejected (400)", st == 400, r)
    st, r = webhook(ev, header="t=1,v1=deadbeef")
    check("webhook with a wrong signature is rejected (400)", st == 400, r)
    st, r = webhook(ev, header=sign(raw, ts=int(time.time()) - 1000))
    check("webhook with an old timestamp (replay) is rejected (400)", st == 400, r)
    st, r = webhook(ev, header=sign(raw, secret=WH_SECRET + "x"))
    check("webhook signed with another secret is rejected (400)", st == 400, r)
    st, r = webhook(ev, header=sign(raw), body=raw.replace(b"starter", b"growth"))
    check("tampered body is rejected (400)", st == 400, r)
    check("none of the rejected calls changed the plan", tstate(TA)[:2] == ["trial", ""], tstate(TA))
    st, r = webhook(ev)
    check("valid checkout.session.completed is accepted", st == 200 and r.get("result") == "applied", r)
    check("plan is now Starter / active", tstate(TA)[:2] == ["starter", "active"], tstate(TA))
    ev_replay = json.loads(json.dumps(ev)); ev_replay["data"]["object"]["metadata"]["plan_id"] = "growth"
    st, r = webhook(ev_replay)
    check("replayed event id is acknowledged as duplicate and NOT applied again", st == 200 and r.get("duplicate") is True and tstate(TA)[0] == "starter", (r, tstate(TA)))
    check("event ids are stored once", sql(f"select count(*) from stripe_events where id='{ev['id']}'") == "1")
    st, bl = A("GET", "/api/billing")
    check("billing shows the subscription (active) and the Starter limits", bl["plan"]["id"] == "starter" and bl["stripe"]["status"] == "active" and bl["plan"]["maxInstances"] == 3, bl["stripe"])
    st, r = B("GET", "/api/billing")
    check("tenant isolation: workspace B is still Trial with no subscription", r["plan"]["id"] == "trial" and r["stripe"]["status"] == "" and r["stripe"]["hasCustomer"] is False, r["stripe"])
    st, r = webhook(event("checkout.session.completed", {"mode": "subscription", "customer": "cus_unknown", "client_reference_id": str(uuid.uuid4()), "payment_status": "paid", "metadata": {"plan_id": "growth"}}))
    check("event for an unknown workspace is acknowledged and ignored", st == 200 and r.get("result") == "ignored", r)
    st, r = webhook(event("customer.created", {"id": "cus_x"}))
    check("unhandled event types are acknowledged (200)", st == 200 and r.get("result") == "ignored", r)

    # renewal date + cancel at period end
    soon = int(time.time()) + 20 * 86400
    st, r = webhook(event("customer.subscription.updated", {"id": "sub_A", "customer": CA, "status": "active", "cancel_at_period_end": True, "current_period_end": soon, "metadata": {"tenant_id": TA, "plan_id": "starter"}}))
    st, bl = A("GET", "/api/billing")
    check("cancel-at-period-end + renewal date are reflected, plan unchanged", bl["stripe"]["cancelAtPeriodEnd"] is True and bl["stripe"]["renewsAt"] and bl["plan"]["id"] == "starter", bl["stripe"])

    # switching plan with an existing subscription: in-place change with proration, no second checkout
    n0 = len(CALLS)
    st, r = A("POST", "/api/billing/checkout", {"plan_id": "growth"})
    new = CALLS[n0:]
    upd = next((c for c in new if c["method"] == "POST" and c["path"] == "/v1/subscriptions/sub_A"), None)
    check("active subscriber switching plan updates the subscription (no new checkout)", st == 200 and r.get("changed") is True and upd and not any(c["path"] == "/v1/checkout/sessions" for c in new), (st, r))
    check("proration is requested and the new price is $59", upd and upd["form"].get("proration_behavior") == "create_prorations" and upd["form"].get("items[0][price_data][unit_amount]") == "5900" and upd["form"].get("items[0][id]") == "si_fake_1" and upd["form"].get("metadata[plan_id]") == "growth", upd and upd["form"])
    check("the plan only changes when Stripe confirms (still Starter)", tstate(TA)[0] == "starter")
    st, r = webhook(event("customer.subscription.updated", {"id": "sub_A", "customer": CA, "status": "active", "cancel_at_period_end": False, "current_period_end": soon, "metadata": {"tenant_id": TA, "plan_id": "growth"}}))
    check("subscription.updated moves the workspace to Growth", tstate(TA)[:2] == ["growth", "active"], tstate(TA))
    st, r = A("POST", "/api/billing/checkout", {"plan_id": "growth"})
    check("choosing the plan you are already on is refused (409)", st == 409, r)

    # ------------------------------------------------ 4. downgrade: numbers are kept, extras are paused (over_limit)
    for k in (1, 2):
        sql(f"insert into instances (id,tenant_id,label,node_id,engine,openwa_name,openwa_id,phone,status,created_at) values ('{uuid.uuid4()}','{TA}','Extra {k}','wa1','baileys','cf-m5a-{stamp}-{k}','','92300555000{k}','ready', now() + interval '{k} minute')")
    check("Growth workspace holds 3 numbers", sql(f"select count(*) from instances where tenant_id='{TA}'") == "3")
    st, r = webhook(event("customer.subscription.deleted", {"id": "sub_A", "customer": CA, "status": "canceled", "metadata": {"tenant_id": TA}}))
    check("subscription.deleted returns the workspace to Trial", st == 200 and tstate(TA)[:2] == ["trial", "canceled"], tstate(TA))
    check("no number was deleted", sql(f"select count(*) from instances where tenant_id='{TA}'") == "3")
    over = sql(f"select count(*) from instances where tenant_id='{TA}' and over_limit")
    oldest_ok = sql(f"select count(*) from (select over_limit from instances where tenant_id='{TA}' order by created_at limit 1) x where not over_limit")
    check("the 2 newest numbers are flagged over_limit, the oldest stays usable", over == "2" and oldest_ok == "1", (over, oldest_ok))
    st, bl = A("GET", "/api/billing")
    check("billing reports overLimit=2", bl["overLimit"] == 2 and bl["plan"]["id"] == "trial", bl)
    st, lst = A("GET", "/api/instances")
    check("instances show overLimit flags", sum(1 for i in lst if i["overLimit"]) == 2, lst)
    sh("exec", "-T", "redis", "redis-cli", "del", f"trial-sessions:{TA}")  # the separate Trial 3-sessions-per-hour abuse cap would otherwise answer first
    st, r = A("POST", "/api/instances", {"label": "again"})
    check("adding is blocked while over the limit (402)", st == 402 and r.get("used") == 3 and r.get("limit") == 1, r)
    st, key = A("POST", "/api/keys", {"name": "m5a", "expires_in": "1d"})
    ov_id = sql(f"select id from instances where tenant_id='{TA}' and over_limit order by created_at limit 1")
    ok_id = sql(f"select id from instances where tenant_id='{TA}' and not over_limit limit 1")
    if st in (200, 201) and key.get("key"):
        hk = {"Authorization": f"Bearer {key['key']}"}
        st, r = ANON("POST", "/v1/messages", {"instance": ov_id, "to": "923001234567", "text": "hi"}, headers=hk)
        check("sending from an over-limit number is refused (403, paused)", st == 403 and "over your plan" in str(r), (st, r))
        st, r = ANON("POST", "/v1/messages", {"instance": ok_id, "to": "923001234567", "text": "hi"}, headers=hk)
        check("the number inside the limit is not blocked by the plan check (409 = just not connected)", st == 409, (st, r))
    else:
        check("API key created for the send checks", False, (st, key))
    # re-subscribe: numbers come back automatically
    st, r = webhook(event("customer.subscription.created", {"id": "sub_A2", "customer": CA, "status": "active", "metadata": {"tenant_id": TA, "plan_id": "starter"}}))
    check("re-subscribing (Starter, 3 numbers) un-pauses all numbers", tstate(TA)[:2] == ["starter", "active"] and sql(f"select count(*) from instances where tenant_id='{TA}' and over_limit") == "0", tstate(TA))

    # ------------------------------------------------ 5. past_due grace, recovery, expiry
    st, r = B("POST", "/api/billing/checkout", {"plan_id": "growth"})
    CB = tstate(TB)[2]
    st, r = webhook(event("checkout.session.completed", {"mode": "subscription", "customer": CB, "subscription": "sub_B", "client_reference_id": TB, "payment_status": "paid", "metadata": {"tenant_id": TB, "plan_id": "growth"}}))
    check("workspace B subscribed to Growth", tstate(TB)[:2] == ["growth", "active"], tstate(TB))
    st, r = webhook(event("invoice.payment_failed", {"customer": CB, "subscription": "sub_B"}))
    st, bl = B("GET", "/api/billing")
    check("payment failure: past_due, plan kept during the 7-day grace, grace end shown", tstate(TB)[:2] == ["growth", "past_due"] and bl["stripe"]["graceEndsAt"], bl["stripe"])
    st, r = webhook(event("invoice.paid", {"customer": CB, "subscription": "sub_B"}))
    check("a later successful payment restores active and clears the grace", tstate(TB)[:2] == ["growth", "active"] and B("GET", "/api/billing")[1]["stripe"]["graceEndsAt"] is None, tstate(TB))
    webhook(event("invoice.payment_failed", {"customer": CB, "subscription": "sub_B"}))
    sql(f"update tenants set past_due_since = now() - interval '6 days' where id='{TB}'")
    out = sh("exec", "-T", "api", "python", "-c", "from app.db import SessionLocal;from app import stripe_billing as s\nwith SessionLocal() as db: print('moved',s.expire_grace(db))")
    check("inside the 7-day grace nothing is downgraded", "moved 0" in out and tstate(TB)[0] == "growth", out)
    sql(f"update tenants set past_due_since = now() - interval '8 days' where id='{TB}'")
    out = sh("exec", "-T", "api", "python", "-c", "from app.db import SessionLocal;from app import stripe_billing as s\nwith SessionLocal() as db: print('moved',s.expire_grace(db))")
    check("after 7 days past due the workspace falls back to Trial", "moved 1" in out and tstate(TB)[:2] == ["trial", "canceled"], (out, tstate(TB)))
    st, r = webhook(event("invoice.paid", {"customer": CB, "subscription": "sub_B"}))
    check("a stray invoice.paid after the downgrade does not resurrect the plan", tstate(TB)[0] == "trial", tstate(TB))

    # ------------------------------------------------ 6. admin sees subscription data (no secrets), audit trail
    ad = sess(ADMIN_HOST)
    st, r = ad("POST", "/api/auth/login", {"email": env["ADMIN_EMAIL"], "password": ADMIN_PW})
    st, d = ad("GET", f"/api/admin/customers/{TA}")
    sub = d.get("tenant", {}).get("subscription", {})
    check("admin customer detail exposes subscription status, ids and renewal", st == 200 and sub.get("status") == "active" and sub.get("customerId") == CA, d.get("tenant"))
    check("audit log records the billing changes", int(sql(f"select count(*) from audit_log where tenant_id='{TA}' and action like 'billing.%'") or 0) >= 3)
    st, r = A("GET", f"/api/admin/customers/{TA}")
    check("a normal customer cannot read admin customer data (403)", st == 403, r)
finally:
    for c, iid in made_instances:
        c("DELETE", f"/api/instances/{iid}")
    sql(f"delete from instances where tenant_id in ('{TA}','{TB}')")
    try:
        ad = sess(ADMIN_HOST)
        ad("POST", "/api/auth/login", {"email": env["ADMIN_EMAIL"], "password": ADMIN_PW})
        for tid in (TA, TB, TC0):
            ad("DELETE", f"/api/admin/customers/{tid}")
    except Exception as e:  # noqa: BLE001
        print("cleanup problem:", e)
    srv.shutdown()

print(f"\n{ok_n} passed, {bad_n} failed")
sys.exit(1 if bad_n else 0)
