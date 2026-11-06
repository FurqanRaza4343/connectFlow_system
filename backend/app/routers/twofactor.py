"""Two-factor authentication (TOTP): second sign-in step, enrolment, recovery codes, disabling.

  POST /api/auth/mfa/verify          password was right -> prove the second factor, get the session
  GET  /api/auth/mfa/status          enabled? enrolment forced? recovery codes left?
  POST /api/auth/mfa/enroll/start    new secret + otpauth URI + QR (shown once)
  POST /api/auth/mfa/enroll/verify   first valid code turns 2FA on and returns 10 single-use recovery codes (shown once)
  POST /api/auth/mfa/disable         password + a code (or recovery code); not allowed for platform admins when 2FA is required
  POST /api/auth/mfa/recovery-codes  password + a code -> a fresh set of recovery codes (the old ones stop working)

Codes can be used once (replay-safe), attempts are rate limited per sign-in token, per IP and per account, and every event is audited.
Secrets and codes are never logged.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from .. import crypto, mfa
from ..db import get_db
from ..deps import csrf_guard, enrol_user, rds
from ..mailer import layout, send_email
from ..models import AuditLog, Membership, User
from ..security import verify_password
from .auth import _client_ip, _set_cookie, _throttle, finish_login

log = logging.getLogger("2fa")
router = APIRouter(prefix="/api/auth/mfa", tags=["2fa"])


class VerifyIn(BaseModel):
    mfaToken: str = Field(max_length=2000)
    code: str = Field(default="", max_length=12)
    recoveryCode: str = Field(default="", max_length=32)


class CodeIn(BaseModel):
    code: str = Field(default="", max_length=12)


class SensitiveIn(BaseModel):
    password: str = Field(max_length=128)
    code: str = Field(default="", max_length=12)
    recoveryCode: str = Field(default="", max_length=32)


def _audit(db: Session, user: User, action: str, detail: str = "") -> None:
    m = db.query(Membership).filter(Membership.user_id == user.id).first()
    db.add(AuditLog(tenant_id=m.tenant_id if m else None, user_id=user.id, action=action, detail=detail[:200]))


def _secret(user: User) -> str | None:
    if not user.totp_secret:
        return None
    try:
        return crypto.reveal(user.totp_secret)
    except crypto.DecryptError:
        log.error("2FA secret of user %s cannot be decrypted (wrong ENCRYPTION_KEY?)", user.id[:8])
        return None


def _check_totp(db: Session, user: User, code: str) -> bool:
    """A valid code that has not been used before. The conditional UPDATE makes 'use once' atomic even with parallel requests."""
    secret = _secret(user)
    step = mfa.match_step(secret, code) if secret else None
    if step is None:
        return False
    res = db.execute(update(User).where(User.id == user.id, or_(User.totp_last_step.is_(None), User.totp_last_step < step)).values(totp_last_step=step))
    db.commit()
    if res.rowcount != 1:  # same (or an older) step already accepted: a replay
        return False
    user.totp_last_step = step
    return True


def _check_recovery(db: Session, user: User, code: str) -> bool:
    locked = db.query(User).filter(User.id == user.id).with_for_update().one()  # two parallel requests must not spend one code twice
    rest = mfa.consume_recovery(list(locked.recovery_codes or []), code)
    if rest is None:
        db.rollback()
        return False
    locked.recovery_codes = rest
    db.commit()
    user.recovery_codes = rest
    return True


def check_second_factor(db: Session, user: User, code: str, recovery: str) -> tuple[bool, str]:
    """(ok, method) where method is "totp" or "recovery"."""
    if recovery.strip():
        return _check_recovery(db, user, recovery), "recovery"
    return _check_totp(db, user, code), "totp"


async def _notify(user: User, subject: str, line: str) -> None:
    await send_email(user.email, subject, line, layout(subject, line))


# ---------------------------------------------------------------- sign-in step
@router.post("/verify")
async def verify(body: VerifyIn, request: Request, response: Response, db: Session = Depends(get_db), _: None = Depends(csrf_guard)) -> dict[str, Any]:
    ip = _client_ip(request)
    _throttle(f"mfa-ip:{ip}", 30, 600)
    parsed = mfa.read_mfa_token(body.mfaToken)
    if not parsed:
        raise HTTPException(401, "Your sign-in expired. Enter your email and password again.")
    uid, jti = parsed
    _throttle(f"mfa-tok:{jti}", 5, 300)            # 5 tries per sign-in token
    _throttle(f"mfa-acct:{uid}", 12, 900)          # and per account, so fresh tokens do not reset the budget
    user = db.get(User, uid)
    if not user or not user.totp_enabled_at or rds.exists(f"mfa-done:{jti}"):  # unknown user, or this token already produced a session
        raise HTTPException(401, "Your sign-in expired. Enter your email and password again.")
    if not (body.code.strip() or body.recoveryCode.strip()):
        raise HTTPException(422, "Enter the 6-digit code from your authenticator app, or a recovery code.")
    ok, method = check_second_factor(db, user, body.code, body.recoveryCode)
    if not ok:
        _audit(db, user, "mfa.failed", f"{method} from {ip}")
        db.commit()
        raise HTTPException(401, "That code is not valid or was already used. Wait for the next code and try again." if method == "totp" else "That recovery code is not valid.")
    if not rds.set(f"mfa-done:{jti}", "1", nx=True, ex=300):  # a sign-in token yields one session
        raise HTTPException(401, "Your sign-in expired. Enter your email and password again.")
    if method == "recovery":
        _audit(db, user, "mfa.recovery_used", ip)
        left = len(user.recovery_codes or [])
        await _notify(user, "A recovery code was used on your ConnectFlow account",
                      f"A recovery code was used to sign in to your account. You have {left} recovery code(s) left. If this was not you, change your password now and regenerate your recovery codes.")
    return finish_login(db, user, request, response, second_factor=True)


# ---------------------------------------------------------------- status + enrolment
@router.get("/status")
def status(u: tuple[User, str] = Depends(enrol_user)) -> dict[str, Any]:
    user, scope = u
    return {"enabled": bool(user.totp_enabled_at), "enrolRequired": scope == "enrol", "recoveryCodesLeft": len(user.recovery_codes or []),
            "adminRequired": bool(user.is_platform_admin and mfa.admin_2fa_required()), "pending": bool(user.totp_secret and not user.totp_enabled_at)}


@router.post("/enroll/start")
def enroll_start(u: tuple[User, str] = Depends(enrol_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    user, _scope = u
    if user.totp_enabled_at:
        raise HTTPException(409, "Two-factor authentication is already on. Disable it first to set it up again.")
    _throttle(f"mfa-enrol-start:{user.id}", 10, 3600)
    secret = mfa.new_secret()
    user.totp_secret, user.totp_last_step = crypto.encrypt(secret), None
    _audit(db, user, "mfa.enrol_start")
    db.commit()
    uri = mfa.otpauth_uri(user.email, secret)
    return {"secret": secret, "otpauthUri": uri, "qrSvg": mfa.qr_svg(uri)}  # shown once: the database keeps only the encrypted secret


@router.post("/enroll/verify")
async def enroll_verify(body: CodeIn, request: Request, response: Response, u: tuple[User, str] = Depends(enrol_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    user, scope = u
    if user.totp_enabled_at:
        raise HTTPException(409, "Two-factor authentication is already on.")
    _throttle(f"mfa-enrol:{user.id}", 10, 600)
    secret = _secret(user)
    step = mfa.match_step(secret, body.code) if secret else None
    if step is None:
        raise HTTPException(401, "That code is not valid. Check the code in your authenticator app and try again.")
    codes = mfa.new_recovery_codes()
    user.totp_enabled_at, user.totp_last_step = datetime.now(timezone.utc), step
    user.recovery_codes = [mfa.hash_recovery(c) for c in codes]
    _audit(db, user, "mfa.enabled")
    db.commit()
    await _notify(user, "Two-factor authentication is now on", "Two-factor authentication was turned on for your ConnectFlow account. If this was not you, contact support immediately.")
    me = None
    if scope == "enrol":  # forced enrolment finished: upgrade the limited session to a full one
        me = finish_login(db, user, request, response, second_factor=True)
    return {"recoveryCodes": codes, "me": me}  # the codes are shown once


# ---------------------------------------------------------------- sensitive changes
def _sensitive(u: tuple[User, str], body: SensitiveIn, db: Session, action: str) -> User:
    user, scope = u
    if scope:
        raise HTTPException(403, "Finish setting up two-factor authentication first.")
    _throttle(f"mfa-sens:{user.id}", 5, 600)
    if not verify_password(body.password, user.password_hash):
        _audit(db, user, "mfa.failed", f"{action}: wrong password")
        db.commit()
        raise HTTPException(401, "Wrong password")
    if not user.totp_enabled_at:
        raise HTTPException(409, "Two-factor authentication is not on.")
    ok, _method = check_second_factor(db, user, body.code, body.recoveryCode)
    if not ok:
        _audit(db, user, "mfa.failed", f"{action}: wrong code")
        db.commit()
        raise HTTPException(401, "That code is not valid or was already used.")
    return user


@router.post("/disable")
async def disable(body: SensitiveIn, u: tuple[User, str] = Depends(enrol_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    user = _sensitive(u, body, db, "disable")
    if user.is_platform_admin and mfa.admin_2fa_required():
        raise HTTPException(409, "Two-factor authentication is required for platform administrators and cannot be turned off.")
    user.totp_secret, user.totp_enabled_at, user.totp_last_step, user.recovery_codes = None, None, None, []
    _audit(db, user, "mfa.disabled")
    db.commit()
    await _notify(user, "Two-factor authentication was turned off", "Two-factor authentication was turned off for your ConnectFlow account. If this was not you, change your password and contact support.")
    return {"ok": True}


@router.post("/recovery-codes")
def regenerate(body: SensitiveIn, u: tuple[User, str] = Depends(enrol_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    user = _sensitive(u, body, db, "recovery-codes")
    codes = mfa.new_recovery_codes()
    user.recovery_codes = [mfa.hash_recovery(c) for c in codes]
    _audit(db, user, "mfa.recovery_regenerated")
    db.commit()
    return {"recoveryCodes": codes}


def session_for_google(db: Session, user: User, response: Response) -> str:
    """Used by the Google sign-in callback. Returns "mfa:<token>" when a second step is needed, "enrol" for a limited admin session,
    or "" after setting a normal session cookie."""
    if user.totp_enabled_at:
        token, _jti = mfa.issue_mfa_token(user.id)
        return "mfa:" + token
    if user.is_platform_admin and mfa.admin_2fa_required():
        _set_cookie(response, user.id, scope="enrol")
        return "enrol"
    _set_cookie(response, user.id)
    return ""

