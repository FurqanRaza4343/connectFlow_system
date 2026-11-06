"""Signup, login, logout, email verification, password reset and the current-user endpoint. Sessions are an HttpOnly cookie; sensitive routes are rate limited in Redis.
Two-factor sign-in (second step and enrolment) lives in routers/twofactor.py."""
import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import Ctx, csrf_guard, current_ctx, rds
from datetime import datetime, timezone

from ..mailer import layout, send_email
from ..models import AuditLog, Membership, Tenant, User
from .. import abuse, mfa, tokens
from ..security import COOKIE, DUMMY_HASH, hash_password, make_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    name: str = Field(default="", max_length=120)
    company: str = Field(min_length=2, max_length=120)
    captcha: str = Field(default="", max_length=4096)  # Cloudflare Turnstile token (only checked when Turnstile is configured)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)
    captcha: str = Field(default="", max_length=4096)


def _client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip()) or (request.client.host if request.client else "?")


def _throttle(key: str, limit: int, window: int) -> None:
    n = rds.incr(key)
    if n == 1:
        rds.expire(key, window)
    if n > limit:
        raise HTTPException(429, "Too many attempts, try again later")


def _set_cookie(resp: Response, user_id: str, scope: str = "") -> None:
    """scope "" = normal session; "enrol" = 30-minute session that can only set up two-factor authentication."""
    hours = 0.5 if scope else settings.session_hours
    resp.set_cookie(COOKIE, make_token(user_id, scope, hours=hours), httponly=True, samesite="strict", secure=settings.cookie_secure,
                    max_age=int(hours * 3600), path="/")


def _me(ctx: Ctx) -> dict:
    t = ctx.tenant
    return {
        "user": {"id": ctx.user.id, "email": ctx.user.email, "name": ctx.user.name, "isPlatformAdmin": ctx.user.is_platform_admin, "avatar": ctx.user.avatar_url, "emailVerified": bool(ctx.user.email_verified_at), "mfaEnabled": bool(ctx.user.totp_enabled_at)},
        "tenant": ({"id": t.id, "name": t.name, "status": t.status,
                    "plan": {"id": t.plan.id, "name": t.plan.name, "maxInstances": t.plan.max_instances, "maxMsgsDay": t.plan.max_msgs_day, "maxMembers": t.plan.max_members},
                    "brand": {"color": t.brand_color, "sidebar": t.sidebar_color, "page": t.page_color, "logo": t.logo_data},
                    "website": t.website_url} if t else None),
        "role": ctx.role,
    }


@router.get("/config")
def auth_config() -> dict[str, Any]:
    """Public: which sign-in methods the login page may offer."""
    return {"google": bool(settings.google_client_id and settings.google_client_secret), "turnstileSiteKey": settings.turnstile_site_key if abuse.turnstile_enabled() else ""}


@router.post("/signup")
async def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_db), _: None = Depends(csrf_guard)) -> dict[str, Any]:
    ip = _client_ip(request)
    _throttle(f"signup:{ip}", 10, 3600)
    await asyncio.to_thread(abuse.verify_turnstile, body.captcha, ip)
    email = body.email.lower()
    abuse.check_email_allowed(email)
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "An account with this email already exists")
    tenant = Tenant(name=body.company.strip(), plan_id="trial")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    db.add_all([tenant, user])
    db.flush()
    db.add(Membership(user_id=user.id, tenant_id=tenant.id, role="owner"))
    db.add(AuditLog(tenant_id=tenant.id, user_id=user.id, action="signup", detail=email))
    db.commit()
    await _send_verification(db, user)
    _set_cookie(response, user.id)
    return _me(Ctx(user=user, tenant=db.get(Tenant, tenant.id), role="owner"))


def membership_ctx(db: Session, user: User) -> Ctx:
    m = db.query(Membership).filter(Membership.user_id == user.id).order_by(Membership.id).first()
    return Ctx(user=user, tenant=db.get(Tenant, m.tenant_id) if m else None, role=m.role if m else "")


def finish_login(db: Session, user: User, request: Request, response: Response, second_factor: bool = False) -> dict[str, Any]:
    """Everything that happens once a user is fully authenticated: session cookie, timestamps, audit entry, the /me payload."""
    _set_cookie(response, user.id)
    user.last_login_at = user.last_seen_at = datetime.now(timezone.utc)
    ctx = membership_ctx(db, user)
    db.add(AuditLog(tenant_id=ctx.tenant.id if ctx.tenant else None, user_id=user.id, action="login",
                    detail=f"{_client_ip(request)} · {(request.headers.get('user-agent') or '')[:120]}{' · 2fa' if second_factor else ''}"))
    db.commit()
    abuse.clear_login_failures(user.email)
    return _me(ctx)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db), _: None = Depends(csrf_guard)) -> dict[str, Any]:
    email = body.email.lower()
    ip = _client_ip(request)
    # limit per IP and per account, so neither one attacker nor a botnet can brute-force a password
    _throttle(f"login-ip:{ip}", 20, 600)
    _throttle(f"login-acct:{email}", 8, 600)
    if abuse.turnstile_enabled() and abuse.login_failures(email) >= 3:  # captcha only after repeated failures
        abuse.verify_turnstile(body.captcha, ip)
    user = db.query(User).filter(User.email == email).first()
    ok = verify_password(body.password, user.password_hash) if user else verify_password(body.password, DUMMY_HASH)
    if not user or not ok:
        abuse.note_login_failure(email)
        raise HTTPException(401, "Wrong email or password")
    if user.totp_enabled_at:  # password is right, but no session until the second factor is proven (POST /api/auth/mfa/verify)
        token, _jti = mfa.issue_mfa_token(user.id)
        return {"mfaRequired": True, "mfaToken": token}
    if user.is_platform_admin and mfa.admin_2fa_required():  # admins must enrol: the limited session can only reach the enrolment routes
        _set_cookie(response, user.id, scope="enrol")
        m0 = db.query(Membership).filter(Membership.user_id == user.id).first()
        db.add(AuditLog(tenant_id=m0.tenant_id if m0 else None, user_id=user.id, action="login.enrol_required", detail=ip))
        db.commit()
        return {"enrolRequired": True}
    return finish_login(db, user, request, response)


@router.post("/logout")
def logout(response: Response, _: None = Depends(csrf_guard)) -> dict[str, Any]:
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(ctx: Ctx = Depends(current_ctx)) -> dict:
    return _me(ctx)


# ---------- email verification & password reset ----------
def _app() -> str:
    return settings.public_app_url.rstrip("/")


async def _send_verification(db: Session, user: User) -> None:
    link = f"{_app()}/?verify={tokens.issue(db, user.id, 'verify', 48)}"
    await send_email(user.email, "Confirm your email for ConnectFlow", f"Confirm your email: {link}",
                     layout("Confirm your email", "Welcome to ConnectFlow! Confirm your email address to unlock sending and API keys.", ("Confirm email", link)))


class TokenIn(BaseModel):
    token: str


class EmailIn(BaseModel):
    email: EmailStr
    captcha: str = Field(default="", max_length=4096)


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=10, max_length=128)


@router.post("/verify")
def verify(body: TokenIn, db: Session = Depends(get_db), _: None = Depends(csrf_guard)) -> dict[str, Any]:
    uid = tokens.consume(db, body.token, "verify")
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(410, "This confirmation link is invalid or has expired. Request a new one from the dashboard.")
    user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)
    db.add(AuditLog(user_id=user.id, action="email.verified", detail=user.email))
    db.commit()
    return {"ok": True}


@router.post("/resend-verification")
async def resend(ctx: Ctx = Depends(current_ctx), db: Session = Depends(get_db)) -> dict[str, Any]:
    if ctx.user.email_verified_at:
        return {"ok": True, "alreadyVerified": True}
    _throttle(f"resend:{ctx.user.id}", 3, 3600)
    await _send_verification(db, ctx.user)
    return {"ok": True}


@router.post("/forgot")
async def forgot(body: EmailIn, request: Request, db: Session = Depends(get_db), _: None = Depends(csrf_guard)) -> dict[str, Any]:
    ip = _client_ip(request)
    _throttle(f"forgot-ip:{ip}", 10, 3600)
    await asyncio.to_thread(abuse.verify_turnstile, body.captcha, ip)
    email = body.email.lower()
    _throttle(f"forgot-acct:{email}", 3, 3600)
    user = db.query(User).filter(User.email == email).first()
    if user:  # same response either way: never reveal which emails have accounts
        link = f"{_app()}/?reset={tokens.issue(db, user.id, 'reset', 1)}"
        await send_email(user.email, "Reset your ConnectFlow password", f"Reset your password (valid 1 hour): {link}",
                         layout("Reset your password", "Someone asked to reset the password for this account. The link is valid for 1 hour.", ("Choose a new password", link)))
    return {"ok": True}


@router.post("/reset")
def reset(body: ResetIn, db: Session = Depends(get_db), _: None = Depends(csrf_guard)) -> dict[str, Any]:
    uid = tokens.consume(db, body.token, "reset")
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(410, "This reset link is invalid or has expired. Request a new one.")
    user.password_hash = hash_password(body.password)
    user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)  # reaching the inbox proves ownership
    db.add(AuditLog(user_id=user.id, action="password.reset", detail=user.email))
    db.commit()
    return {"ok": True}
