"""Sign in / sign up with Google (OAuth 2.0 authorization-code flow, server side: the client secret never reaches the browser).

Setup (Google Cloud Console -> APIs & Services -> Credentials -> OAuth client ID, type "Web application"):
  Authorized redirect URI:  <PUBLIC_APP_URL>/api/auth/google/callback
Then put GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in infra/.env.
"""
from typing import Any
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AuditLog, Membership, Tenant, User
from ..security import hash_password
from .auth import _client_ip, _throttle
from .twofactor import session_for_google

router = APIRouter(prefix="/api/auth/google", tags=["auth"])
STATE_COOKIE = "cf_oauth"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


def _redirect_uri() -> str:
    return f"{settings.public_app_url.rstrip('/')}/api/auth/google/callback"


def _back(error: str | None = None, mode: str = "login") -> RedirectResponse:
    target = f"{settings.public_app_url.rstrip('/')}/"
    if error:
        target += "?" + urlencode({"auth_error": error}) + f"#{mode}"
    r = RedirectResponse(target, status_code=302)
    r.delete_cookie(STATE_COOKIE, path="/api/auth/google")
    return r


@router.get("/start")
def start(request: Request, mode: str = "login") -> Any:
    if not (settings.google_client_id and settings.google_client_secret):
        return _back("Google sign-in is not configured yet.", mode)
    _throttle(f"gstart:{_client_ip(request)}", 30, 600)
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    cookie = jwt.encode({"s": state, "n": nonce, "m": mode, "exp": _exp(600)}, settings.jwt_secret, algorithm="HS256")
    url = AUTH_URL + "?" + urlencode({
        "client_id": settings.google_client_id, "redirect_uri": _redirect_uri(), "response_type": "code",
        "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account",
    })
    r = RedirectResponse(url, status_code=302)
    # Lax (not Strict): Google sends the browser back to us from another site, and the cookie must come along.
    r.set_cookie(STATE_COOKIE, cookie, max_age=600, httponly=True, samesite="lax", secure=settings.cookie_secure, path="/api/auth/google")
    return r


def _exp(seconds: int) -> int:
    import time
    return int(time.time()) + seconds


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)) -> Any:
    try:
        saved = jwt.decode(request.cookies.get(STATE_COOKIE, ""), settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return _back("Your sign-in session expired. Please try again.")
    mode = saved.get("m", "login")
    if error:
        return _back("Google sign-in was cancelled.", mode)
    if not code or not secrets.compare_digest(saved["s"], state):
        return _back("Sign-in could not be verified. Please try again.", mode)

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            tok = await c.post(TOKEN_URL, data={
                "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
                "redirect_uri": _redirect_uri(), "grant_type": "authorization_code"})
            if tok.status_code != 200:
                return _back("Google rejected the sign-in. Please try again.", mode)
            info = await c.get(TOKENINFO_URL, params={"id_token": tok.json()["id_token"]})  # Google validates signature + expiry
            if info.status_code != 200:
                return _back("Could not verify your Google account.", mode)
    except (httpx.HTTPError, KeyError):
        return _back("Could not reach Google. Please try again.", mode)

    g = info.json()
    if g.get("aud") != settings.google_client_id or g.get("iss") not in ("https://accounts.google.com", "accounts.google.com") \
            or g.get("nonce") != saved["n"] or str(g.get("email_verified")).lower() != "true" or not g.get("email"):
        return _back("Your Google account could not be verified.", mode)

    email, sub = g["email"].lower(), g["sub"]
    user = db.query(User).filter(User.google_sub == sub).first() or db.query(User).filter(User.email == email).first()
    if user:
        if not user.google_sub:
            user.google_sub = sub  # email is verified by Google, so linking to the existing account is safe
        if g.get("picture") and not user.avatar_url:
            user.avatar_url = g["picture"][:500]
        if not user.name and g.get("name"):
            user.name = g["name"][:120]
        user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
        db.add(AuditLog(user_id=user.id, action="login.google", detail=email))
    else:
        name = (g.get("name") or email.split("@")[0])[:120]
        tenant = Tenant(name=f"{name}'s workspace"[:120], plan_id="trial")
        user = User(email=email, name=name, google_sub=sub, email_verified_at=datetime.now(timezone.utc), avatar_url=(g.get("picture") or "")[:500],
                    password_hash=hash_password(secrets.token_urlsafe(32)))  # random: password login stays impossible until a reset
        db.add_all([tenant, user])
        db.flush()
        db.add(Membership(user_id=user.id, tenant_id=tenant.id, role="owner"))
        db.add(AuditLog(tenant_id=tenant.id, user_id=user.id, action="signup.google", detail=email))
    db.commit()

    resp = _back()
    step = session_for_google(db, user, resp)  # "" = signed in; "mfa:<token>" = the user must still enter a 2FA code; "enrol" = admin must set up 2FA
    if step.startswith("mfa:"):
        resp = RedirectResponse(f"{settings.public_app_url.rstrip('/')}/?" + urlencode({"mfa_token": step[4:]}), status_code=302)
        resp.delete_cookie(STATE_COOKIE, path="/api/auth/google")
    return resp
