"""Two-factor authentication helpers (TOTP, RFC 6238): secrets, codes, replay-safe verification, recovery codes, the step token.

Pure logic lives here so it can be unit tested; the HTTP routes are in routers/twofactor.py.
Secrets are stored encrypted (app/crypto.py); recovery codes are stored only as keyed hashes.
"""
import hashlib
import hmac
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pyotp
import segno

from .config import settings

STEP_SECONDS = 30
WINDOW = 1  # accept the previous and the next 30 s step too (clock drift)
RECOVERY_COUNT = 10
_RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
MFA_TOKEN_MINUTES = 5


def admin_2fa_required() -> bool:
    """Platform admins must use 2FA: explicit ADMIN_2FA_REQUIRED, otherwise on exactly when COOKIE_SECURE=true (production)."""
    return settings.cookie_secure if settings.admin_2fa_required is None else settings.admin_2fa_required


def new_secret() -> str:
    return pyotp.random_base32()


def otpauth_uri(email: str, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="ConnectFlow")


def qr_svg(uri: str) -> str:
    """The otpauth URI as an inline SVG QR code, generated locally (no external service ever sees the secret)."""
    return str(segno.make(uri, error="m").svg_inline(scale=6, border=2, dark="#000000", light="#ffffff", omitsize=False))


def normalize_code(code: str) -> str:
    return re.sub(r"\D", "", code or "")


def match_step(secret: str, code: str, now: float | None = None) -> int | None:
    """The 30 s step number the code belongs to (within WINDOW of now), or None. Constant-time compare."""
    c = normalize_code(code)
    if len(c) != 6:
        return None
    t = int((now if now is not None else time.time()) // STEP_SECONDS)
    totp = pyotp.TOTP(secret)
    found: int | None = None
    for step in range(t - WINDOW, t + WINDOW + 1):
        if hmac.compare_digest(totp.generate_otp(step), c):
            found = step
    return found


# ---------------- recovery codes ----------------
def new_recovery_codes(n: int = RECOVERY_COUNT) -> list[str]:
    """Human friendly single-use codes like 7QK2M-X9RPA (50 bits each)."""
    out = []
    for _ in range(n):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        out.append(f"{raw[:5]}-{raw[5:]}")
    return out


def normalize_recovery(code: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def hash_recovery(code: str) -> str:
    """Keyed hash (HMAC-SHA256 with a server secret): a leaked database alone cannot be brute-forced offline."""
    return hmac.new(settings.jwt_secret.encode(), b"recovery:" + normalize_recovery(code).encode(), hashlib.sha256).hexdigest()


def consume_recovery(stored: list[str], code: str) -> list[str] | None:
    """The remaining hashes when `code` matches one of the stored hashes (and removes it), else None."""
    h = hash_recovery(code)
    if len(normalize_recovery(code)) != 10:
        return None
    hit = [x for x in stored if hmac.compare_digest(x, h)]
    if not hit:
        return None
    rest = list(stored)
    rest.remove(hit[0])
    return rest


# ---------------- step token (password OK, second factor pending) ----------------
def issue_mfa_token(user_id: str) -> tuple[str, str]:
    jti = uuid.uuid4().hex
    exp = datetime.now(timezone.utc) + timedelta(minutes=MFA_TOKEN_MINUTES)
    return jwt.encode({"sub": user_id, "typ": "mfa", "jti": jti, "exp": exp}, settings.jwt_secret, algorithm="HS256"), jti


def read_mfa_token(token: str) -> tuple[str, str] | None:
    try:
        c = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if c.get("typ") != "mfa" or not isinstance(c.get("sub"), str) or not isinstance(c.get("jti"), str):
        return None
    return c["sub"], c["jti"]
