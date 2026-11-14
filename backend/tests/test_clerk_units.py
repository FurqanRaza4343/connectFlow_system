"""Clerk token verification (no network: a throw-away RSA key stands in for the instance's JWKS)."""
import base64
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app import clerk_auth
from app.config import settings

HOST = "example-app-1.clerk.accounts.dev"


class _Key:
    def __init__(self, key: object) -> None:
        self.key = key


class _Jwks:
    def __init__(self, pub: object) -> None:
        self.pub = pub

    def get_signing_key_from_jwt(self, _t: str) -> _Key:
        return _Key(self.pub)


@pytest.fixture()
def keys(monkeypatch: pytest.MonkeyPatch):
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pk = "pk_test_" + base64.b64encode(f"{HOST}$".encode()).decode().rstrip("=")
    monkeypatch.setattr(settings, "clerk_publishable_key", pk)
    monkeypatch.setattr(settings, "clerk_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "clerk_authorized_parties", "https://app.example.com")
    monkeypatch.setattr(clerk_auth, "_client", lambda: _Jwks(priv.public_key()))
    return priv


def _tok(priv: object, **over: object) -> str:
    now = int(time.time())
    claims = {"sub": "user_1", "iss": f"https://{HOST}", "iat": now, "exp": now + 60, "azp": "https://app.example.com", **over}
    return jwt.encode(claims, priv, algorithm="RS256")  # type: ignore[arg-type]


def test_issuer_comes_from_the_publishable_key(keys: object) -> None:
    assert clerk_auth.frontend_api() == HOST and clerk_auth.issuer() == f"https://{HOST}"


def test_good_token_passes(keys: object) -> None:
    assert clerk_auth.verify_token(_tok(keys))["sub"] == "user_1"


@pytest.mark.parametrize("over", [{"exp": 1}, {"iss": "https://evil.example"}, {"azp": "https://evil.example"}])
def test_bad_tokens_are_refused(keys: object, over: dict[str, object]) -> None:
    with pytest.raises(HTTPException) as e:
        clerk_auth.verify_token(_tok(keys, **over))
    assert e.value.status_code == 401


def test_token_signed_by_another_key_is_refused(keys: object) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException):
        clerk_auth.verify_token(_tok(other))


def test_not_configured_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "clerk_secret_key", "")
    with pytest.raises(HTTPException) as e:
        clerk_auth.verify_token("x")
    assert e.value.status_code == 503
