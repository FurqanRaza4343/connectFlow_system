"""Pure-logic unit tests: phone/CSV helpers, quota math, STOP detection, API-key hashing/expiry, SSRF guard,
retention rules and the session-reaper selection logic (all with an injected clock; nothing touches a database)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import leadtools, netguard, outreach, quota, reaper, retention
from app.routers import keys

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- phone numbers
@pytest.mark.parametrize("raw,expected", [
    ("+92 331 2781199", "923312781199"),
    ("0331-2781199", "923312781199"),
    ("03312781199", "923312781199"),
    ("+92 331 2781199 (Google); +92 313 1777563", "923312781199"),   # first valid number wins, notes in brackets ignored
    ("n/a; 0313 1777563", "923131777563"),                           # junk chunk skipped
])
def test_normalize_phone_valid(raw, expected):
    assert leadtools.normalize_phone(raw)[0] == expected


@pytest.mark.parametrize("raw", ["", "abc", "123", "042 35761234", None])
def test_normalize_phone_rejects(raw):
    assert leadtools.normalize_phone(raw) == ("", "")           # landlines and garbage never become WhatsApp targets


def test_same_number_in_different_formats_dedupes():
    forms = ["+92 331 2781199", "0331 2781199", "00923312781199", "+92-331-2781199"]
    assert len({leadtools.normalize_phone(f)[0] for f in forms}) == 1


def test_international_number_keeps_its_country():
    digits, country = leadtools.normalize_phone("+971 50 123 4567")
    assert digits == "971501234567" and country == "AE"


def test_csv_safe_blocks_formula_injection():
    assert leadtools.csv_safe('=HYPERLINK("http://x")').startswith("'")
    assert leadtools.csv_safe("@SUM(A1)").startswith("'")
    assert leadtools.csv_safe("+15555550123") == "+15555550123"    # a phone number is not a formula
    assert leadtools.csv_safe("Acme") == "Acme" and leadtools.csv_safe(None) == ""


# ---------------------------------------------------------------- CSV mapping
def test_suggest_mapping_maps_each_field_once():
    m = leadtools.suggest_mapping(["Business Name", "Contact Person", "WhatsApp Number", "Phone (raw)", "Website", "Email", "Foo"])
    assert m["Business Name"] == "company" and m["Contact Person"] == "contact_name"
    assert m["WhatsApp Number"] == "phone" and m["Phone (raw)"] == "phone"   # second phone column is a fallback source
    assert m["Website"] == "website" and m["Email"] == "email" and m["Foo"] is None


def test_parse_csv_variants():
    h, rows = leadtools.parse_csv("﻿Name;Phone\nAcme;0331 2781199\n;\nBeta;0313 1777563\n")
    assert h == ["Name", "Phone"] and [r["Name"] for r in rows] == ["Acme", "Beta"]   # BOM stripped, ; sniffed, blank row dropped


@pytest.mark.parametrize("text", ["", "   \n", "OnlyHeader\n"])
def test_parse_csv_rejects_unusable_files(text):
    with pytest.raises(ValueError):
        leadtools.parse_csv(text)


def test_parse_csv_row_limit():
    big = "a,b\n" + "\n".join(f"{i},x" for i in range(11))
    with pytest.raises(ValueError):
        leadtools.parse_csv(big, max_rows=10)


# ---------------------------------------------------------------- quota math
class FakeQuery:
    def __init__(self, tenant, used):
        self.tenant, self.used = tenant, used

    def filter(self, *a, **k):
        return self

    def with_for_update(self, **k):
        return self

    def one(self):
        return self.tenant

    def scalar(self):
        return self.used


class FakeDb:
    def __init__(self, tenant, used):
        self.tenant, self.used, self.added, self.rolled_back, self.committed = tenant, used, [], False, False

    def query(self, *a):
        return FakeQuery(self.tenant, self.used)

    def add(self, x):
        self.added.append(x)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _tenant(limit=100, status="active"):
    return SimpleNamespace(status=status, plan=SimpleNamespace(max_msgs_day=limit, ai_calls_day=limit, name="Test"))


def test_quota_allows_below_limit_and_records_usage():
    db = FakeDb(_tenant(100), used=99)
    quota.reserve_message(db, "t1", None, None)
    assert len(db.added) == 1 and db.committed


def test_quota_blocks_at_limit_with_429():
    db = FakeDb(_tenant(100), used=100)
    with pytest.raises(HTTPException) as e:
        quota.reserve_message(db, "t1", None, None)
    assert e.value.status_code == 429 and db.rolled_back and not db.added


def test_quota_blocks_suspended_tenant():
    with pytest.raises(HTTPException) as e:
        quota.reserve_message(FakeDb(_tenant(status="suspended"), used=0), "t1", None, None)
    assert e.value.status_code == 403


def test_day_start_is_utc_midnight():
    d = quota.day_start()
    assert (d.hour, d.minute, d.second) == (0, 0, 0) and d.utcoffset() == timedelta(0)


@pytest.mark.parametrize("age_days,cap", [(0, 5), (1, 10), (2, 15), (3, 20), (4, 30), (5, 40), (90, 40)])
def test_warmup_cap_grows_with_number_age(age_days, cap):
    inst = SimpleNamespace(created_at=datetime.now(timezone.utc) - timedelta(days=age_days, hours=1))
    assert outreach.warmup_cap(inst) == cap


# ---------------------------------------------------------------- STOP / opt-out detection
@pytest.mark.parametrize("text", ["STOP", "stop.", "Please unsubscribe me", "Remove me from your list", "not interested", "Mat bhej", "band kar do", "Do not contact me", "nahi chahiye"])
def test_stop_detected(text):
    assert outreach.STOP_RE.search(text)


@pytest.mark.parametrize("text", ["Sounds good, tell me more", "nonstop service?", "stopover in Dubai", "What is the price?", "Yes interested"])
def test_stop_not_detected_in_normal_replies(text):
    assert not outreach.STOP_RE.search(text)


# ---------------------------------------------------------------- API keys
def test_key_hash_is_stable_sha256_hex():
    h = keys.hash_key("cf_live_abc")
    assert h == keys.hash_key("cf_live_abc") and len(h) == 64 and h != keys.hash_key("cf_live_abd")
    assert "cf_live_abc" not in h


def _key(**kw):
    base = dict(revoked_at=None, suspended_at=None, expires_at=None)
    return SimpleNamespace(**{**base, **kw})


def test_key_status_and_expiry():
    past, future = NOW - timedelta(hours=1), datetime.now(timezone.utc) + timedelta(days=1)
    assert keys._status(_key()) == "active"
    assert keys._status(_key(expires_at=future)) == "active"
    assert keys._status(_key(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))) == "expired"
    assert keys._status(_key(suspended_at=past)) == "suspended"
    assert keys._status(_key(revoked_at=past, suspended_at=past, expires_at=past)) == "revoked"   # revoked beats everything


# ---------------------------------------------------------------- SSRF guard
@pytest.mark.parametrize("url", [
    "http://8.8.8.8/hook",                  # webhooks must be https
    "https://user:pw@8.8.8.8/hook",         # credentials in URL
    "https://localhost/hook", "https://internal/hook",
    "https://127.0.0.1/hook", "https://10.0.0.5/hook", "https://192.168.1.10/hook",
    "https://169.254.169.254/latest/meta-data",   # cloud metadata
    "ftp://8.8.8.8/x", "https:///nohost",
])
def test_webhook_url_rejected(url, monkeypatch):
    monkeypatch.setattr(netguard.settings, "webhook_allow_private", False)
    with pytest.raises(netguard.UnsafeUrl):
        netguard._check_sync(url)


def test_webhook_url_public_ip_allowed(monkeypatch):
    monkeypatch.setattr(netguard.settings, "webhook_allow_private", False)
    netguard._check_sync("https://8.8.8.8/hook")


@pytest.mark.parametrize("url", ["http://192.168.0.1/", "http://127.0.0.1:8000/", "http://[::1]/", "http://metadata/", "file:///etc/passwd"])
def test_crawler_never_reaches_private_hosts(url):
    with pytest.raises(netguard.UnsafeUrl):
        netguard._check_crawl_sync(url)


def test_crawler_allows_public_http():
    netguard._check_crawl_sync("http://8.8.8.8/")


# ---------------------------------------------------------------- retention rules
def test_retention_rules_cutoffs_and_scope():
    rules = retention.plan_rules(NOW)
    tables = {r["table"] for r in rules}
    assert tables == {"webhook_deliveries", "notifications", "audit_log", "usage_events", "drafts", "batches", "ai_usage_daily", "security_events"}
    assert not tables & {"messages", "leads", "instances", "tenants", "users"}      # customer data is never pruned
    for r in rules:
        assert r["cutoff"] < NOW and ":cutoff" in r["where"]
    by = {(r["table"], r.get("kind")): r for r in rules}
    assert by[("webhook_deliveries", None)]["cutoff"] == NOW - timedelta(days=retention.ops.retention_webhook_days)
    assert by[("webhook_deliveries", "blank_payload")]["cutoff"] > by[("webhook_deliveries", None)]["cutoff"]   # payloads shrink before the row goes
    assert by[("usage_events", None)].get("rollup") is True                                                    # usage is rolled up before deletion
    assert "'success','failed'" in by[("webhook_deliveries", None)]["where"].replace(" ", "")                  # pending/retrying deliveries are kept
    assert '"read" = true' in by[("notifications", None)]["where"]                                             # unread notifications are kept


def test_months_ago_is_monotonic():
    assert retention.months_ago(NOW, 13) < retention.months_ago(NOW, 12) < NOW


def test_rollup_counts_groups_by_tenant_and_utc_day():
    rows = [("a", NOW), ("a", NOW + timedelta(hours=1)), ("a", NOW - timedelta(days=1)), ("b", NOW)]
    c = retention.rollup_counts(rows)
    assert c[("a", "2026-11-04")] == 2 and c[("a", "2026-11-03")] == 1 and c[("b", "2026-11-04")] == 1


# ---------------------------------------------------------------- reaper selection (injected clock)
def R(name, status="qr_ready", phone="", age_min=60, plan="trial", last=None):
    return reaper.Row(id=name, name=name, status=status, phone=phone, created_at=NOW - timedelta(minutes=age_min), plan_id=plan, last_activity=last)


def test_unscanned_sessions_are_selected_after_the_window():
    rows = [R("old-qr", age_min=30), R("fresh-qr", age_min=3), R("linked", status="ready", phone="92300", age_min=999), R("init-old", status="initializing", age_min=45)]
    assert {r.name for r in reaper.select_unscanned(rows, NOW, 10)} == {"old-qr", "init-old"}


def test_unscanned_never_touches_a_linked_number():
    assert reaper.select_unscanned([R("x", status="qr_ready", phone="923001112222", age_min=500)], NOW, 10) == []


def test_idle_trial_only_ready_trial_sessions():
    day = 24 * 60
    rows = [
        R("idle-trial", status="ready", phone="1", age_min=10 * day, plan="trial"),
        R("active-trial", status="ready", phone="1", age_min=10 * day, plan="trial", last=NOW - timedelta(days=1)),
        R("idle-paid", status="ready", phone="1", age_min=10 * day, plan="growth"),
        R("new-trial", status="ready", phone="1", age_min=2 * day, plan="trial"),
        R("disc-trial", status="disconnected", phone="1", age_min=10 * day, plan="trial"),
    ]
    assert {r.name for r in reaper.select_idle_trial(rows, NOW, 7)} == {"idle-trial"}
    assert reaper.select_idle_trial(rows, NOW, 0) == []        # 0 disables the rule


def test_reconcile_split():
    db = [R("cf-linked-gone", status="ready", phone="1", age_min=60), R("cf-unlinked-gone", age_min=60), R("cf-brand-new", age_min=1), R("cf-ok", status="ready", phone="1", age_min=60), R("cf-off", status="disconnected", phone="1", age_min=60)]
    gw = [{"name": "cf-ok"}, {"name": "cf-orphan-seen"}, {"name": "cf-orphan-new"}, {"name": "someone-elses"}]
    r = reaper.split_reconcile(db, gw, NOW, 180, {"cf-orphan-seen"})
    assert r["orphans"] == ["cf-orphan-seen"] and r["new_orphans"] == ["cf-orphan-new"]      # two sightings before deletion; foreign names ignored
    assert [x.name for x in r["gone_linked"]] == ["cf-linked-gone"]
    assert [x.name for x in r["gone_unlinked"]] == ["cf-unlinked-gone"]                     # brand-new rows get a grace period, disconnected ones are left alone


# ---------------------------------------------------------------- M4: multi-number + alerts
def _row(status="ready", enabled=True, sent=0, cap=5, label="n"):
    return {"inst": SimpleNamespace(status=status, outreach_enabled=enabled, label=label, over_limit=False), "sent": sent, "cap": cap}


def test_pick_instance_skips_api_only_numbers():
    api_only, outreach_num = _row(enabled=False, sent=0), _row(enabled=True, sent=3)
    assert outreach.pick_instance([api_only, outreach_num]) is outreach_num["inst"]
    assert outreach.pick_instance([api_only]) is None


def test_pick_instance_needs_ready_and_capacity():
    assert outreach.pick_instance([_row(status="disconnected")]) is None
    assert outreach.pick_instance([_row(sent=5, cap=5)]) is None
    a, b = _row(sent=2), _row(sent=1)
    assert outreach.pick_instance([a, b]) is b["inst"]  # least used first


def test_alert_extra_emails_validation():
    from app import alerts
    assert alerts.validate_extras("A@x.com, b@y.org;a@x.com") == ["a@x.com", "b@y.org"]
    assert alerts.validate_extras("") == []
    with pytest.raises(ValueError):
        alerts.validate_extras("a@x.com,b@x.com,c@x.com,d@x.com")
    with pytest.raises(ValueError):
        alerts.validate_extras("not-an-email")


def test_alert_preview_is_cut_to_140():
    from app import alerts
    assert len(alerts.preview("x" * 500)) == 140
    assert alerts.preview("  hello \n world ") == "hello world"
    assert alerts.preview(None) == ""


# ---------------------------------------------------------------- hardening: CSRF origin, STOP edge cases, production config
@pytest.mark.parametrize("origin,host,ok", [
    (None, "app.connectflow.localhost:8080", True),                                   # API client / script: no Origin header
    ("http://app.connectflow.localhost:8080", "app.connectflow.localhost:8080", True),  # same-origin browser call
    ("http://evil.example", "app.connectflow.localhost:8080", False),                  # cross-site browser call
    ("null", "app.connectflow.localhost:8080", False),                                 # sandboxed iframe / file://
    ("http://app.connectflow.localhost:8080", None, False),
])
def test_origin_allowed(origin, host, ok):
    from app.deps import origin_allowed
    assert origin_allowed(origin, host) is ok


@pytest.mark.parametrize("text", ["STOP", "  stop  ", "Stop!", "UNSUBSCRIBE", "please stop messaging me", "Don't contact me again"])
def test_stop_more_variants(text):
    assert outreach.STOP_RE.search(text)


@pytest.mark.parametrize("text", ["Bus stop is near my office", "Can you stop by tomorrow?", "stopwatch price?", "Let's stop at 5pm for a call"])
def test_stop_not_triggered_by_ordinary_sentences(text):
    assert not outreach.STOP_RE.search(text)
