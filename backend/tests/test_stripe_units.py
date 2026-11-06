"""Pure-logic tests for the Stripe module: webhook signature, form encoding, price items, plan limit flagging. No network or DB."""
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import stripe_billing as sb

SECRET = "whsec_unit_test_secret"
BODY = json.dumps({"id": "evt_1", "type": "invoice.paid"}).encode()


def test_good_signature_is_accepted():
    assert sb.verify_signature(BODY, sb.sign(BODY, SECRET), SECRET)


def test_tampered_body_is_rejected():
    assert not sb.verify_signature(BODY + b" ", sb.sign(BODY, SECRET), SECRET)


def test_wrong_secret_is_rejected():
    assert not sb.verify_signature(BODY, sb.sign(BODY, "other"), SECRET)


def test_old_timestamp_is_rejected_but_edge_of_window_passes():
    now = 1_800_000_000
    assert not sb.verify_signature(BODY, sb.sign(BODY, SECRET, ts=now - 301), SECRET, now=now)
    assert sb.verify_signature(BODY, sb.sign(BODY, SECRET, ts=now - 299), SECRET, now=now)
    assert not sb.verify_signature(BODY, sb.sign(BODY, SECRET, ts=now + 400), SECRET, now=now)  # future timestamps too


@pytest.mark.parametrize("header", ["", "garbage", "t=abc,v1=00", "v1=00", "t=1", "t=,v1="])
def test_malformed_headers_are_rejected(header):
    assert not sb.verify_signature(BODY, header, SECRET)


def test_empty_secret_never_verifies():
    assert not sb.verify_signature(BODY, sb.sign(BODY, ""), "")


def test_multiple_v1_signatures_any_match():
    ts = int(time.time())
    good = sb.sign(BODY, SECRET, ts=ts).split("v1=")[1]
    assert sb.verify_signature(BODY, f"t={ts},v1={'0' * 64},v1={good}", SECRET)


def test_form_encode_nested():
    got = dict(sb.form_encode({"mode": "subscription", "line_items": [{"price_data": {"unit_amount": 1900, "recurring": {"interval": "month"}}, "quantity": 1}],
                               "allow_promotion_codes": True, "skip": None}))
    assert got["mode"] == "subscription"
    assert got["line_items[0][price_data][unit_amount]"] == "1900"
    assert got["line_items[0][price_data][recurring][interval]"] == "month"
    assert got["allow_promotion_codes"] == "true"
    assert "skip" not in got


def test_price_items_inline_in_cents(monkeypatch):
    monkeypatch.setattr(sb.settings, "stripe_price_starter", "")
    items = sb._price_items(SimpleNamespace(id="starter", name="Starter", price_usd=19))
    assert items["price_data"]["unit_amount"] == 1900 and items["price_data"]["currency"] == "usd" and items["quantity"] == 1


def test_price_items_prefers_configured_price_id(monkeypatch):
    monkeypatch.setattr(sb.settings, "stripe_price_growth", "price_123")
    assert sb._price_items(SimpleNamespace(id="growth", name="Growth", price_usd=59)) == {"price": "price_123", "quantity": 1}


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a):
        return self

    def order_by(self, *a):
        return self

    def all(self):
        return self.rows


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, *a):
        return _Q(self.rows)


def _inst(n):
    return SimpleNamespace(over_limit=False, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=n), id=str(n))


def test_enforce_plan_limits_keeps_oldest_and_flags_the_rest():
    rows = [_inst(i) for i in range(4)]
    over = sb.enforce_plan_limits(_DB(rows), SimpleNamespace(id="t", plan=SimpleNamespace(max_instances=1)))
    assert over == 3 and [r.over_limit for r in rows] == [False, True, True, True]


def test_enforce_plan_limits_clears_flags_after_an_upgrade():
    rows = [_inst(i) for i in range(3)]
    for r in rows:
        r.over_limit = True
    assert sb.enforce_plan_limits(_DB(rows), SimpleNamespace(id="t", plan=SimpleNamespace(max_instances=3))) == 0
    assert not any(r.over_limit for r in rows)


def test_billing_state_reports_grace_end():
    t = SimpleNamespace(subscription_status="past_due", current_period_end=None, cancel_at_period_end=False, stripe_customer_id="cus_1",
                        past_due_since=datetime(2026, 1, 1, tzinfo=timezone.utc))
    s = sb.billing_state(t)
    assert s["status"] == "past_due" and s["graceEndsAt"].startswith("2026-01-08") and s["hasCustomer"] is True


def test_pick_instance_skips_over_limit_numbers():
    from app.outreach import pick_instance

    def row(over):
        return {"inst": SimpleNamespace(status="ready", outreach_enabled=True, over_limit=over), "sent": 0, "cap": 5}

    assert pick_instance([row(True)]) is None
    ok = row(False)
    assert pick_instance([row(True), ok]) is ok["inst"]
