"""The 14-day trial rules (pure: no database)."""
from datetime import datetime, timedelta, timezone

from app import access
from app.models import Tenant


def _t(**kw) -> Tenant:
    return Tenant(name="x", plan_id=kw.pop("plan_id", "trial"), **kw)


def test_new_trial_is_14_days():
    now = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    assert access.new_trial_end(now) == now + timedelta(days=14)


def test_days_left_rounds_up_and_expiry():
    now = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    assert access.trial_state(_t(trial_ends_at=now + timedelta(days=14)), now) == {"endsAt": (now + timedelta(days=14)).isoformat(), "daysLeft": 14, "expired": False}
    assert access.trial_state(_t(trial_ends_at=now + timedelta(hours=2)), now)["daysLeft"] == 1
    gone = access.trial_state(_t(trial_ends_at=now - timedelta(minutes=1)), now)
    assert gone and gone["expired"] and gone["daysLeft"] == 0


def test_no_limit_for_old_paid_or_non_trial_workspaces():
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert access.trial_state(_t(trial_ends_at=None)) is None                                   # older workspace
    assert access.trial_state(_t(trial_ends_at=past, subscription_status="active")) is None    # paying
    assert access.trial_state(_t(trial_ends_at=past, plan_id="starter")) is None
    assert not access.trial_expired(None)
