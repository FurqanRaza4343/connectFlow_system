from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from .config import settings

_ph = PasswordHasher()
DUMMY_HASH = _ph.hash("not-a-real-password")  # verified against when the email is unknown: same timing either way
COOKIE = "cf_session"


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except VerificationError:
        return False


def make_token(user_id: str, scope: str = "", hours: float | None = None) -> str:
    """Session JWT. scope "" = full session; "enrol" = limited session that may only set up two-factor authentication."""
    exp = datetime.now(timezone.utc) + timedelta(hours=hours or settings.session_hours)
    claims: dict[str, object] = {"sub": user_id, "exp": exp}
    if scope:
        claims["scp"] = scope
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def read_session(token: str) -> tuple[str, str] | None:
    """(user id, scope) of a valid session token, else None. Tokens of other kinds (e.g. the 2FA step token) are never sessions."""
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if "typ" in claims or not isinstance(claims.get("sub"), str):
        return None
    return claims["sub"], str(claims.get("scp") or "")


def read_token(token: str) -> str | None:
    """User id of a FULL session token (limited sessions do not count)."""
    got = read_session(token)
    return got[0] if got and got[1] == "" else None
