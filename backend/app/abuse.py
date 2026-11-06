"""Signup abuse controls: disposable e-mail blocking, optional Cloudflare Turnstile captcha, and the Trial-session cap.

Everything here is optional or has a safe default: with no Turnstile keys the captcha check is skipped, and the disposable
list can be overridden per deployment with DISPOSABLE_EMAIL_ALLOW.
"""
import logging
import pathlib
from collections.abc import Awaitable, Callable
from functools import lru_cache

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from .config import settings
from .db import SessionLocal
from .deps import rds
from .models import Membership, Tenant
from .security import read_token

log = logging.getLogger("abuse")
DATA = pathlib.Path(__file__).parent / "data" / "disposable_domains.txt"
TURNSTILE_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TRIAL_SESSIONS_PER_HOUR = 3
DISPOSABLE_MESSAGE = "Please use a permanent work or personal email address. Disposable email services are not accepted."


# ---------------- disposable e-mail ----------------
@lru_cache(maxsize=1)
def _blocklist() -> frozenset[str]:
    try:
        words = [w.lower() for line in DATA.read_text(encoding="utf8").splitlines() if not line.lstrip().startswith("#") for w in line.split()]
    except OSError:
        log.warning("disposable domain list is missing: %s", DATA)
        words = []
    return frozenset(words)


def _allowed() -> frozenset[str]:
    return frozenset(d.strip().lower() for d in settings.disposable_email_allow.split(",") if d.strip())


def is_disposable(email: str) -> bool:
    """True when the address's domain (or a parent domain, e.g. x.mailinator.com) is on the list and not explicitly allowed."""
    domain = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not domain or "." not in domain:
        return False
    parts = domain.split(".")
    candidates = {".".join(parts[i:]) for i in range(len(parts) - 1)}
    if candidates & _allowed():
        return False
    return bool(candidates & _blocklist())


def check_email_allowed(email: str) -> None:
    if is_disposable(email):
        raise HTTPException(422, DISPOSABLE_MESSAGE)


# ---------------- Turnstile (optional) ----------------
def turnstile_enabled() -> bool:
    return bool(settings.turnstile_site_key and settings.turnstile_secret_key)


def verify_turnstile(token: str, ip: str = "") -> None:
    """Server-side check of a Turnstile response token. No-op when Turnstile is not configured; fails closed when it is."""
    if not turnstile_enabled():
        return
    if not token:
        raise HTTPException(400, "captcha_required")
    try:
        r = httpx.post(TURNSTILE_URL, data={"secret": settings.turnstile_secret_key, "response": token, "remoteip": ip}, timeout=6)
        ok = r.status_code == 200 and bool(r.json().get("success"))
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Could not verify the captcha right now. Please try again in a moment.")
    if not ok:
        raise HTTPException(400, "captcha_failed")


def note_login_failure(email: str) -> int:
    k = f"login-fail:{email}"
    n = int(rds.incr(k))
    if n == 1:
        rds.expire(k, 600)
    return n


def login_failures(email: str) -> int:
    try:
        return int(rds.get(f"login-fail:{email}") or 0)
    except Exception:  # noqa: BLE001
        return 0


def clear_login_failures(email: str) -> None:
    try:
        rds.delete(f"login-fail:{email}")
    except Exception:  # noqa: BLE001
        pass


# ---------------- Trial-session cap ----------------
async def trial_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Middleware: a workspace on the Trial plan may start at most TRIAL_SESSIONS_PER_HOUR new WhatsApp sessions per hour.
    Each `POST /api/instances` creates an unscanned session on the gateway; this stops a throwaway trial from churning them."""
    if request.method == "POST" and request.url.path == "/api/instances":
        uid = read_token(request.cookies.get("cf_session", ""))
        if uid:
            try:
                with SessionLocal() as db:
                    m = db.query(Membership).filter(Membership.user_id == uid).order_by(Membership.id).first()
                    t = db.get(Tenant, m.tenant_id) if m else None
                    if t and t.plan_id == "trial":
                        k = f"trial-sessions:{t.id}"
                        n = int(rds.incr(k))
                        if n == 1:
                            rds.expire(k, 3600)
                        if n > TRIAL_SESSIONS_PER_HOUR:
                            return JSONResponse({"detail": f"Trial workspaces can start {TRIAL_SESSIONS_PER_HOUR} WhatsApp sessions per hour. Finish linking your number or try again later."}, status_code=429)
            except Exception:  # noqa: BLE001 - never block a request because the guard itself failed
                log.exception("trial guard failed")
    return await call_next(request)
