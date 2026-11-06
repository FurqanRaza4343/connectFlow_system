"""M5 unit tests: at-rest encryption, TOTP + recovery codes, signup abuse controls, idempotency, malformed-input boundaries.
No database, network or real Redis (a tiny in-memory fake stands in)."""
import asyncio
import hashlib
import hmac
import json
import time

import jwt
import pyotp
import pytest
from fastapi import HTTPException

from app import abuse, crypto, idempotency, mfa
from app.config import Settings, settings
from app.security import make_token, read_session, read_token


class FakeRedis:
    """The few Redis calls the code under test makes."""

    def __init__(self) -> None:
        self.d: dict[str, str] = {}

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.d:
            return None
        self.d[k] = v
        return True

    def get(self, k):
        return self.d.get(k)

    def delete(self, k):
        return int(self.d.pop(k, None) is not None)

    def incr(self, k):
        self.d[k] = str(int(self.d.get(k, "0")) + 1)
        return int(self.d[k])

    def expire(self, k, s):
        return True


# ---------------------------------------------------------------- at-rest encryption
def test_encrypt_roundtrip_and_prefix():
    c = crypto.encrypt("whsec_abc123")
    assert c.startswith("enc1:") and "whsec_abc123" not in c
    assert crypto.reveal(c) == "whsec_abc123"
    assert crypto.encrypt("whsec_abc123") != c  # random IV: ciphertexts differ


def test_legacy_plaintext_passes_through_and_is_upgraded():
    assert crypto.reveal("whsec_plain") == "whsec_plain"
    up = crypto.upgrade_if_plain("whsec_plain")
    assert up and crypto.is_encrypted(up) and crypto.reveal(up) == "whsec_plain"
    assert crypto.upgrade_if_plain(up) is None and crypto.upgrade_if_plain("") is None


def test_wrong_key_cannot_decrypt(monkeypatch):
    c = crypto.encrypt("secret")
    monkeypatch.setattr(settings, "encryption_key", crypto.derive_key("a completely different secret"))
    monkeypatch.setattr(settings, "jwt_secret", "another-jwt-secret-entirely")
    crypto.reset_cache()
    try:
        with pytest.raises(crypto.DecryptError):
            crypto.reveal(c)
    finally:
        monkeypatch.undo()
        crypto.reset_cache()


def test_rotation_with_previous_key(monkeypatch):
    old = crypto.derive_key("old-key-material")
    new = crypto.derive_key("new-key-material")
    monkeypatch.setattr(settings, "encryption_key", old)
    crypto.reset_cache()
    ciphertext = crypto.encrypt("rotating")
    monkeypatch.setattr(settings, "encryption_key", new)
    monkeypatch.setattr(settings, "encryption_key_previous", old)
    crypto.reset_cache()
    try:
        assert crypto.reveal(ciphertext) == "rotating"          # still opens with the previous key
        fresh = crypto.reencrypt(ciphertext)
        monkeypatch.setattr(settings, "encryption_key_previous", "")
        crypto.reset_cache()
        assert crypto.reveal(fresh) == "rotating"                # opens with the new key alone after re-encryption
        with pytest.raises(crypto.DecryptError):
            crypto.reveal(ciphertext)                            # the old ciphertext is dead once the previous key is removed
    finally:
        monkeypatch.undo()
        crypto.reset_cache()


def test_key_validation():
    assert crypto.valid_key(crypto.derive_key("x"))
    assert not crypto.valid_key("not-a-key") and not crypto.valid_key("")
    assert crypto.derive_key("same") == crypto.derive_key("same") != crypto.derive_key("other")


def _prod(**over):
    base = dict(_env_file=None, jwt_secret="j" * 40, openwa_webhook_secret="w" * 40, cookie_secure=True)
    base.update(over)
    return Settings(**base)


def test_production_requires_dedicated_encryption_key():
    with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
        _prod()
    assert _prod(encryption_key=crypto.derive_key("prod")).cookie_secure is True
    with pytest.raises(ValueError, match="not a valid Fernet key"):
        _prod(encryption_key="short")
    Settings(_env_file=None, jwt_secret="dev", openwa_webhook_secret="dev")  # development needs none


def test_turnstile_keys_must_come_as_a_pair():
    with pytest.raises(ValueError, match="TURNSTILE"):
        Settings(_env_file=None, jwt_secret="x", openwa_webhook_secret="y", turnstile_site_key="site")


# ---------------------------------------------------------------- TOTP
def test_totp_accepts_current_and_adjacent_steps_only():
    secret = mfa.new_secret()
    now = 1_800_000_000.0
    t = pyotp.TOTP(secret)
    step = int(now // 30)
    assert mfa.match_step(secret, t.generate_otp(step), now) == step
    assert mfa.match_step(secret, t.generate_otp(step - 1), now) == step - 1
    assert mfa.match_step(secret, t.generate_otp(step + 1), now) == step + 1
    assert mfa.match_step(secret, t.generate_otp(step - 2), now) is None
    assert mfa.match_step(secret, t.generate_otp(step + 5), now) is None


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "000000 0", None])
def test_totp_rejects_malformed_codes(bad):
    assert mfa.match_step(mfa.new_secret(), bad or "", 1_800_000_000.0) is None


def test_totp_formatting_is_tolerated():
    secret = mfa.new_secret()
    now = time.time()
    code = pyotp.TOTP(secret).now()
    assert mfa.match_step(secret, f" {code[:3]} {code[3:]} ", now) is not None


def test_qr_is_local_inline_svg_and_uri_has_issuer():
    uri = mfa.otpauth_uri("ana@example.com", mfa.new_secret())
    assert uri.startswith("otpauth://totp/ConnectFlow:") and "issuer=ConnectFlow" in uri
    svg = mfa.qr_svg(uri)
    assert svg.lstrip().startswith("<svg") and "http" not in svg.replace("http://www.w3.org/2000/svg", "")


def test_admin_2fa_default_follows_cookie_secure(monkeypatch):
    monkeypatch.setattr(settings, "admin_2fa_required", None)
    monkeypatch.setattr(settings, "cookie_secure", False)
    assert mfa.admin_2fa_required() is False
    monkeypatch.setattr(settings, "cookie_secure", True)
    assert mfa.admin_2fa_required() is True
    monkeypatch.setattr(settings, "admin_2fa_required", False)
    assert mfa.admin_2fa_required() is False


# ---------------------------------------------------------------- recovery codes
def test_recovery_codes_format_uniqueness_and_single_use():
    codes = mfa.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(len(c) == 11 and c[5] == "-" for c in codes)
    stored = [mfa.hash_recovery(c) for c in codes]
    assert codes[0] not in "".join(stored)
    rest = mfa.consume_recovery(stored, codes[3].lower().replace("-", " "))  # case / separators do not matter
    assert rest is not None and len(rest) == 9
    assert mfa.consume_recovery(rest, codes[3]) is None                      # already used
    assert mfa.consume_recovery(stored, "AAAAA-AAAAA") is None
    assert mfa.consume_recovery(stored, "short") is None


def test_recovery_hash_is_keyed(monkeypatch):
    h1 = mfa.hash_recovery("ABCDE-FGHJK")
    monkeypatch.setattr(settings, "jwt_secret", "a different server secret")
    assert mfa.hash_recovery("ABCDE-FGHJK") != h1                           # a leaked database alone is not enough to test guesses


# ---------------------------------------------------------------- tokens
def test_mfa_token_and_session_token_are_not_interchangeable():
    token, jti = mfa.issue_mfa_token("user-1")
    assert mfa.read_mfa_token(token) == ("user-1", jti)
    assert read_session(token) is None and read_token(token) is None         # a step token is never a session
    session = make_token("user-1")
    assert mfa.read_mfa_token(session) is None                               # and a session is never a step token
    assert read_token(session) == "user-1"


def test_limited_enrol_session_is_not_a_full_session():
    limited = make_token("user-1", scope="enrol", hours=0.5)
    assert read_session(limited) == ("user-1", "enrol")
    assert read_token(limited) is None


def test_expired_and_tampered_tokens_are_rejected():
    expired = jwt.encode({"sub": "u", "typ": "mfa", "jti": "j", "exp": int(time.time()) - 5}, settings.jwt_secret, algorithm="HS256")
    assert mfa.read_mfa_token(expired) is None
    token, _ = mfa.issue_mfa_token("u")
    assert mfa.read_mfa_token(token[:-3] + "AAA") is None
    assert mfa.read_mfa_token("garbage") is None and read_session("garbage") is None


# ---------------------------------------------------------------- abuse controls
@pytest.mark.parametrize("email,expected", [
    ("a@mailinator.com", True), ("a@sub.mailinator.com", True), ("a@YOPMAIL.com", True), ("a@guerrillamail.com", True),
    ("a@gmail.com", False), ("a@company.io", False), ("nodomain", False), ("a@localhost", False), ("", False),
])
def test_disposable_detection(email, expected):
    assert abuse.is_disposable(email) is expected


def test_disposable_allow_override(monkeypatch):
    monkeypatch.setattr(settings, "disposable_email_allow", "mailinator.com, other.org")
    assert abuse.is_disposable("a@mailinator.com") is False and abuse.is_disposable("a@sub.mailinator.com") is False
    assert abuse.is_disposable("a@yopmail.com") is True


def test_disposable_rejection_is_a_clear_422():
    with pytest.raises(HTTPException) as e:
        abuse.check_email_allowed("x@10minutemail.com")
    assert e.value.status_code == 422 and "permanent" in e.value.detail
    abuse.check_email_allowed("x@gmail.com")


def test_blocklist_file_is_big_enough():
    assert len(abuse._blocklist()) >= 300


def test_turnstile_off_is_a_noop_and_on_requires_a_token(monkeypatch):
    abuse.verify_turnstile("")  # not configured: skipped
    monkeypatch.setattr(settings, "turnstile_site_key", "site")
    monkeypatch.setattr(settings, "turnstile_secret_key", "secret")
    with pytest.raises(HTTPException) as e:
        abuse.verify_turnstile("")
    assert e.value.status_code == 400 and e.value.detail == "captcha_required"


def test_turnstile_server_side_verdicts(monkeypatch):
    monkeypatch.setattr(settings, "turnstile_site_key", "site")
    monkeypatch.setattr(settings, "turnstile_secret_key", "secret")

    class R:
        def __init__(self, ok): self.status_code, self._ok = 200, ok
        def json(self): return {"success": self._ok}

    monkeypatch.setattr(abuse.httpx, "post", lambda *a, **k: R(True))
    abuse.verify_turnstile("tok")
    monkeypatch.setattr(abuse.httpx, "post", lambda *a, **k: R(False))
    with pytest.raises(HTTPException) as e:
        abuse.verify_turnstile("tok")
    assert e.value.detail == "captcha_failed"

    def boom(*a, **k): raise abuse.httpx.ConnectError("down")
    monkeypatch.setattr(abuse.httpx, "post", boom)
    with pytest.raises(HTTPException) as e2:
        abuse.verify_turnstile("tok")
    assert e2.value.status_code == 503                                       # fails closed, with a friendly message


# ---------------------------------------------------------------- idempotency
def test_idempotency_key_validation():
    assert idempotency.validate_key(None) is None
    assert idempotency.validate_key(" order-42_a:b.c ") == "order-42_a:b.c"
    for bad in ("", "x" * 65, "has space", "semi;colon", "ünïcode"):
        with pytest.raises(HTTPException):
            idempotency.validate_key(bad)


def test_body_hash_ignores_key_order_but_not_content():
    a = idempotency.body_hash({"instance": "i", "to": "923001234567", "text": "hi"})
    assert a == idempotency.body_hash({"text": "hi", "to": "923001234567", "instance": "i"})
    assert a != idempotency.body_hash({"instance": "i", "to": "923001234567", "text": "hi!"})


def test_idempotency_flow():
    r = FakeRedis()
    h = idempotency.body_hash({"x": 1})
    first = idempotency.begin(r, "t1", "k1", h)
    assert first.replay is False and first.tracked
    with pytest.raises(HTTPException) as e:                                   # concurrent duplicate while the first runs
        idempotency.begin(r, "t1", "k1", h)
    assert e.value.status_code == 409
    idempotency.finish(r, "t1", "k1", h, 202, {"id": "m1", "status": "sent"})
    again = idempotency.begin(r, "t1", "k1", h)
    assert again.replay and again.status == 202 and again.body == {"id": "m1", "status": "sent"}
    with pytest.raises(HTTPException) as e2:                                  # same key, other body
        idempotency.begin(r, "t1", "k1", idempotency.body_hash({"x": 2}))
    assert e2.value.status_code == 422
    assert idempotency.begin(r, "t2", "k1", h).replay is False               # keys are per workspace


def test_failed_first_attempt_releases_the_key():
    r = FakeRedis()
    h = idempotency.body_hash({"x": 1})
    idempotency.begin(r, "t", "k", h)
    idempotency.abort(r, "t", "k")
    assert idempotency.begin(r, "t", "k", h).replay is False


def test_idempotency_fails_open_when_redis_is_down():
    import redis

    class Down:
        def set(self, *a, **k): raise redis.ConnectionError("down")
        get = delete = set

    st = idempotency.begin(Down(), "t", "k", "h")
    assert st.tracked is False and st.replay is False
    idempotency.finish(Down(), "t", "k", "h", 202, {})                        # must not raise
    idempotency.abort(Down(), "t", "k")


# ---------------------------------------------------------------- malformed input never crashes the receivers
def test_internal_text_helper_only_accepts_scalars():
    from app.routers.internal import _text
    assert _text(" hi ") == "hi" and _text(42) == "42" and _text(True) == ""
    assert _text(None) == "" and _text(["a"]) == "" and _text({"a": 1}) == ""
    assert len(_text("x" * 10_000)) == 4096


def _signed(body: bytes) -> str:
    return "sha256=" + hmac.new(settings.openwa_webhook_secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture()
def client(monkeypatch):
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app
    from app.routers import internal

    monkeypatch.setattr(internal, "rds", FakeRedis())
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None  # unknown session -> ignored
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


@pytest.mark.parametrize("body", [b"[]", b"[1,2,3]", b'"text"', b"123", b"null", b"{not json", b"\xff\xfe"])
def test_gateway_webhook_rejects_non_object_bodies_with_400(client, body):
    r = client.post("/internal/wa-webhook", content=body, headers={"x-openwa-signature": _signed(body)})
    assert r.status_code == 400


def test_gateway_webhook_bad_signature_is_401(client):
    assert client.post("/internal/wa-webhook", content=b"{}", headers={"x-openwa-signature": "sha256=00"}).status_code == 401


@pytest.mark.parametrize("payload", [
    {"event": "message.received", "data": [1, 2]}, {"event": "message.received", "data": "str"}, {"event": ["x"], "data": {}},
    {"event": "session.status", "data": {"sessionId": {"a": 1}, "status": ["ready"]}}, {"event": "message.received", "sessionId": 7, "data": {"body": {"x": 1}}},
    {}, {"data": None}, {"event": "message.received", "data": {"sessionId": "s", "body": "hi", "from": ["x"]}},
])
def test_gateway_webhook_survives_weird_payloads(client, payload):
    body = json.dumps(payload).encode()
    r = client.post("/internal/wa-webhook", content=body, headers={"x-openwa-signature": _signed(body)})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_webhook_worker_isolates_a_crashing_delivery(monkeypatch):
    from app import worker

    crashed: list[str] = []

    async def bad(delivery_id: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "deliver", bad)
    monkeypatch.setattr(worker, "_record_crash", lambda i: crashed.append(i))
    asyncio.run(worker.safe_deliver("d-1"))  # must not raise
    assert crashed == ["d-1"]


def test_supervisor_restarts_a_crashing_loop(monkeypatch):
    from app import worker

    calls = {"n": 0}

    async def flaky() -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("crash")
        raise asyncio.CancelledError  # stop the test after the third start

    async def fast_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(worker.asyncio, "sleep", fast_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker._supervise("t", flaky))
    assert calls["n"] == 3


def test_sender_round_continues_after_one_workspace_fails(monkeypatch):
    from app import outreach_worker as ow

    seen: list[str] = []

    async def fake_send_one(db, tid: str) -> None:
        seen.append(tid)
        if tid == "bad":
            raise RuntimeError("workspace problem")

    class Sess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k):
            class R:
                def all(self_inner): return [("bad",), ("good",)]
            return R()

    n = {"sleeps": 0}

    async def stop_after_one_round(_s: float) -> None:
        n["sleeps"] += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(ow, "_send_one", fake_send_one)
    monkeypatch.setattr(ow, "SessionLocal", lambda: Sess())
    monkeypatch.setattr(ow.asyncio, "sleep", stop_after_one_round)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ow.sender_loop())
    assert seen == ["bad", "good"]                                          # the failing workspace did not block the next one


def test_blank_admin_2fa_env_means_default_not_an_error():
    s = Settings(_env_file=None, jwt_secret="x", openwa_webhook_secret="y", admin_2fa_required="")
    assert s.admin_2fa_required is None
    assert Settings(_env_file=None, jwt_secret="x", openwa_webhook_secret="y", admin_2fa_required="true").admin_2fa_required is True
