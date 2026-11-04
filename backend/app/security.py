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


def make_token(user_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    return jwt.encode({"sub": user_id, "exp": exp}, settings.jwt_secret, algorithm="HS256")


def read_token(token: str) -> str | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        return None
