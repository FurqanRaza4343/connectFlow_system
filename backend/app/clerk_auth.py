"""Clerk = who the person is. Everything else (workspace, role, plan, invites) stays in our database.

A Clerk session token (RS256 JWT) is verified against the instance's JWKS, then the user's VERIFIED primary email is read from
Clerk's backend API (the token itself carries no email). The result is exchanged for our normal session cookie (routers/auth.py)."""
import base64
import logging
from typing import Any

import httpx
import jwt
from fastapi import HTTPException

from .config import settings

log = logging.getLogger("clerk")
_jwks: jwt.PyJWKClient | None = None


def enabled() -> bool:
    return bool(settings.clerk_publishable_key and settings.clerk_secret_key)


def frontend_api() -> str:
    """The instance's Frontend API host, encoded inside the publishable key (pk_test_<base64('host$')>)."""
    try:
        raw = settings.clerk_publishable_key.split("_", 2)[2]
        return base64.b64decode(raw + "=" * (-len(raw) % 4)).decode().rstrip("$")
    except Exception:  # noqa: BLE001
        return ""


def issuer() -> str:
    host = frontend_api()
    return f"https://{host}" if host else ""


def _client() -> jwt.PyJWKClient:
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(f"{issuer()}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    return _jwks


def verify_token(token: str) -> dict[str, Any]:
    """Claims of a valid Clerk session token (signature, issuer, expiry, authorised party); 401 otherwise."""
    if not enabled():
        raise HTTPException(503, "Clerk sign-in is not configured")
    try:
        key = _client().get_signing_key_from_jwt(token).key
        claims: dict[str, Any] = jwt.decode(token, key, algorithms=["RS256"], issuer=issuer(), options={"require": ["exp", "iat", "sub", "iss"]}, leeway=10)
    except (jwt.PyJWTError, jwt.PyJWKClientError) as e:
        log.info("clerk token rejected: %s", type(e).__name__)
        raise HTTPException(401, "Sign-in could not be verified. Please try again.") from e
    allowed = [p.strip().rstrip("/") for p in settings.clerk_authorized_parties.split(",") if p.strip()]
    azp = str(claims.get("azp") or "").rstrip("/")
    if allowed and azp and azp not in allowed:
        raise HTTPException(401, "Sign-in could not be verified (wrong origin).")
    return claims


async def fetch_identity(clerk_id: str) -> dict[str, Any]:
    """{email, name, avatar} of the Clerk user, using ONLY an email Clerk has verified."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.clerk.com/v1/users/{clerk_id}", headers={"Authorization": f"Bearer {settings.clerk_secret_key}"})
    except httpx.HTTPError as e:
        raise HTTPException(503, "Could not reach the sign-in service. Please try again.") from e
    if r.status_code != 200:
        log.warning("clerk user lookup failed: %s", r.status_code)
        raise HTTPException(503, "Could not read your account from the sign-in service.")
    u = r.json()
    primary = u.get("primary_email_address_id")
    email = ""
    for e in u.get("email_addresses") or []:
        if e.get("id") == primary and (e.get("verification") or {}).get("status") == "verified":
            email = str(e.get("email_address") or "").strip().lower()
    if not email:
        raise HTTPException(403, "Your email address is not verified yet.")
    name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x).strip()
    return {"email": email, "name": name[:120], "avatar": str(u.get("image_url") or "")[:500]}
