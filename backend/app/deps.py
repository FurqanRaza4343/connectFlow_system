from dataclasses import dataclass
from typing import Any
from datetime import datetime, timezone

import redis
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import Membership, Tenant, User
from .security import COOKIE, read_session, read_token

rds: Any = redis.Redis.from_url(settings.redis_url, decode_responses=True)  # Any: redis-py's stubs type every command as `Awaitable[...] | ...`, which makes the synchronous client unusable under mypy

ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2, "owner": 3}


@dataclass
class Ctx:
    user: User
    tenant: Tenant | None
    role: str  # role inside tenant ("" for a platform admin without tenant)


@dataclass
class TCtx(Ctx):
    """A context that is guaranteed to belong to a workspace: what `need_role(...)` hands to workspace routes."""

    tenant: Tenant  # deliberately narrower than Ctx.tenant (the guard has already rejected a missing workspace)


def origin_allowed(origin: str | None, host: str | None) -> bool:
    """Same-origin check for state-changing calls. Requests without an Origin header (API clients, scripts) pass; a browser
    always sends Origin on cross-site POSTs, and its host must equal the host the request was addressed to."""
    if not origin or origin == "null":
        return origin != "null"
    from urllib.parse import urlsplit
    return bool(host) and urlsplit(origin).netloc.lower() == (host or "").split(",")[0].strip().lower()


def csrf_guard(request: Request) -> None:
    """Cookie auth + a custom header the browser will not add cross-site: blocks CSRF for state-changing calls."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get("x-requested-with") != "ConnectFlow":
        raise HTTPException(403, "missing X-Requested-With header")
    if not origin_allowed(request.headers.get("origin"), request.headers.get("x-forwarded-host") or request.headers.get("host")):
        raise HTTPException(403, "cross-origin request blocked")


def current_ctx(request: Request, db: Session = Depends(get_db), _: None = Depends(csrf_guard)) -> Ctx:
    uid = read_token(request.cookies.get(COOKIE, ""))
    user = db.get(User, uid) if uid else None
    if not user:
        raise HTTPException(401, "login required")
    now = datetime.now(timezone.utc)
    if not user.last_seen_at or (now - user.last_seen_at).total_seconds() > 60:
        user.last_seen_at = now
        db.commit()
    m = db.query(Membership).filter(Membership.user_id == user.id).order_by(Membership.id).first()
    tenant = db.get(Tenant, m.tenant_id) if m else None
    if tenant and tenant.status != "active" and not user.is_platform_admin:
        raise HTTPException(403, "account suspended - contact support")
    from .ops_logging import tenant_id_var, user_id_var  # request-scoped ids for the JSON logs
    user_id_var.set(user.id)
    tenant_id_var.set(tenant.id if tenant else "")
    return Ctx(user=user, tenant=tenant, role=m.role if m else "")


def enrol_user(request: Request, db: Session = Depends(get_db), _: None = Depends(csrf_guard)) -> tuple[User, str]:
    """For the two-factor routes only: a full session OR the limited "enrol" session a platform admin gets until 2FA is set up.
    Returns (user, scope) where scope is "" (full) or "enrol"."""
    got = read_session(request.cookies.get(COOKIE, ""))
    user = db.get(User, got[0]) if got else None
    if not got or not user:
        raise HTTPException(401, "login required")
    return user, got[1]


def need_role(minimum: str) -> Any:
    def dep(ctx: Ctx = Depends(current_ctx)) -> TCtx:
        if not ctx.tenant:
            raise HTTPException(403, "no workspace")
        if ROLE_RANK.get(ctx.role, -1) < ROLE_RANK[minimum]:
            raise HTTPException(403, f"requires role {minimum}")
        return TCtx(user=ctx.user, tenant=ctx.tenant, role=ctx.role)

    return dep


def platform_admin(ctx: Ctx = Depends(current_ctx)) -> Ctx:
    if not ctx.user.is_platform_admin:
        raise HTTPException(403, "platform admin only")
    return ctx


def require_verified(ctx: Ctx) -> None:
    """Adding numbers and creating API keys needs a confirmed email (stops throwaway-account abuse)."""
    if not ctx.user.email_verified_at:
        raise HTTPException(403, "Please confirm your email address first. We sent you a link; you can resend it from the banner at the top of the dashboard.")
