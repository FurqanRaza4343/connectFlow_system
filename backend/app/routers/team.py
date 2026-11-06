"""Workspace members and invitations (owner/admin/operator/viewer) with seat limits from the plan."""
from typing import Any
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from .. import abuse, mfa
from ..config import settings
from ..db import get_db
from ..deps import Ctx, ROLE_RANK, csrf_guard, need_role, TCtx
from ..mailer import layout, send_email
from ..models import AuditLog, Invite, Membership, Tenant, User
from ..security import hash_password
from ..tokens import digest
from .auth import _me, _set_cookie, _throttle, _client_ip

router = APIRouter(prefix="/api/team", tags=["team"])
INVITE_ROLES = ("admin", "operator", "viewer")


class InviteIn(BaseModel):
    email: EmailStr
    role: str = "operator"


class RoleIn(BaseModel):
    role: str


class AcceptIn(BaseModel):
    token: str
    name: str = Field(default="", max_length=120)
    password: str = Field(default="", max_length=128)


@router.get("")
def team(ctx: TCtx = Depends(need_role("admin")), db: Session = Depends(get_db)) -> dict[str, Any]:
    members = db.query(Membership, User).join(User, User.id == Membership.user_id).filter(Membership.tenant_id == ctx.tenant.id).order_by(Membership.id).all()
    now = datetime.now(timezone.utc)
    invites = db.query(Invite).filter(Invite.tenant_id == ctx.tenant.id, Invite.accepted_at.is_(None), Invite.expires_at > now).order_by(Invite.created_at.desc()).all()
    return {
        "members": [{"id": m.id, "userId": u.id, "email": u.email, "name": u.name, "role": m.role, "verified": bool(u.email_verified_at), "isYou": u.id == ctx.user.id} for m, u in members],
        "invites": [{"id": i.id, "email": i.email, "role": i.role, "expiresAt": i.expires_at.isoformat()} for i in invites],
        "limit": ctx.tenant.plan.max_members,
    }


@router.post("/invites", status_code=201)
async def invite(body: InviteIn, ctx: TCtx = Depends(need_role("admin")), db: Session = Depends(get_db)) -> dict[str, Any]:
    if body.role not in INVITE_ROLES:
        raise HTTPException(422, "role must be admin, operator or viewer")
    if body.role == "admin" and ctx.role != "owner":
        raise HTTPException(403, "Only the owner can invite admins")
    email = body.email.lower()
    abuse.check_email_allowed(email)
    now = datetime.now(timezone.utc)
    used = db.query(Membership).filter(Membership.tenant_id == ctx.tenant.id).count() + db.query(Invite).filter(
        Invite.tenant_id == ctx.tenant.id, Invite.accepted_at.is_(None), Invite.expires_at > now).count()
    if used >= ctx.tenant.plan.max_members:
        raise HTTPException(402, f"Your {ctx.tenant.plan.name} plan allows {ctx.tenant.plan.max_members} team member(s). Upgrade to invite more.")
    if db.query(User).join(Membership, Membership.user_id == User.id).filter(User.email == email, Membership.tenant_id == ctx.tenant.id).first():
        raise HTTPException(409, "That person is already in your workspace")
    import secrets as _s
    plain = _s.token_urlsafe(32)
    db.add(Invite(tenant_id=ctx.tenant.id, email=email, role=body.role, token_hash=digest(plain), invited_by=ctx.user.id, expires_at=now + timedelta(days=7)))
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="team.invite", detail=f"{email} as {body.role}"))
    db.commit()
    link = f"{settings.public_app_url.rstrip('/')}/?invite={plain}"
    who = escape(ctx.user.name or ctx.user.email)
    await send_email(email, f"{ctx.user.name or 'A teammate'} invited you to {ctx.tenant.name} on ConnectFlow",
                     f"{who} invited you to join {ctx.tenant.name} as {body.role}.\nAccept: {link}\n(valid for 7 days)",
                     layout(f"Join {escape(ctx.tenant.name)}", f"{who} invited you to join <b>{escape(ctx.tenant.name)}</b> as <b>{body.role}</b>. The link is valid for 7 days.", ("Accept invitation", link)))
    return {"ok": True}


@router.delete("/invites/{invite_id}", status_code=204)
def revoke_invite(invite_id: str, ctx: TCtx = Depends(need_role("admin")), db: Session = Depends(get_db)) -> None:
    i = db.query(Invite).filter(Invite.id == invite_id, Invite.tenant_id == ctx.tenant.id).first()
    if not i:
        raise HTTPException(404, "invite not found")
    db.delete(i)
    db.commit()


@router.patch("/members/{membership_id}")
def change_role(membership_id: str, body: RoleIn, ctx: TCtx = Depends(need_role("owner")), db: Session = Depends(get_db)) -> dict[str, Any]:
    m = db.query(Membership).filter(Membership.id == membership_id, Membership.tenant_id == ctx.tenant.id).first()
    if not m:
        raise HTTPException(404, "member not found")
    if m.role == "owner" or body.role not in INVITE_ROLES:
        raise HTTPException(422, "The owner's role cannot be changed, and role must be admin, operator or viewer")
    m.role = body.role
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="team.role", detail=f"{m.user_id} -> {body.role}"))
    db.commit()
    return {"ok": True}


@router.delete("/members/{membership_id}", status_code=204)
def remove_member(membership_id: str, ctx: TCtx = Depends(need_role("admin")), db: Session = Depends(get_db)) -> None:
    m = db.query(Membership).filter(Membership.id == membership_id, Membership.tenant_id == ctx.tenant.id).first()
    if not m:
        raise HTTPException(404, "member not found")
    if m.role == "owner" or m.user_id == ctx.user.id:
        raise HTTPException(422, "The owner and yourself cannot be removed")
    if ROLE_RANK[m.role] >= ROLE_RANK[ctx.role]:
        raise HTTPException(403, "You can only remove people with a lower role than yours")
    db.add(AuditLog(tenant_id=ctx.tenant.id, user_id=ctx.user.id, action="team.remove", detail=m.user_id))
    db.delete(m)
    db.commit()


# ---------- public: the invited person opens the link ----------
def _live_invite(db: Session, token: str) -> Invite:
    i = db.query(Invite).filter(Invite.token_hash == digest(token or "")).first()
    if not i or i.accepted_at or i.expires_at < datetime.now(timezone.utc):
        raise HTTPException(410, "This invitation is invalid or has expired")
    return i


@router.get("/invites/preview")
def preview(token: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    i = _live_invite(db, token)
    t = db.get(Tenant, i.tenant_id)
    exists = db.query(User).filter(User.email == i.email).first() is not None
    return {"email": i.email, "workspace": t.name if t else "", "role": i.role, "hasAccount": exists}


@router.post("/invites/accept")
def accept(body: AcceptIn, response: Response, db: Session = Depends(get_db), _: None = Depends(csrf_guard)) -> Any:
    i = _live_invite(db, body.token)
    user = db.query(User).filter(User.email == i.email).first()
    if user:
        if db.query(Membership).filter(Membership.user_id == user.id).first():
            raise HTTPException(409, "This email already belongs to a workspace. (An account can be in one workspace for now.)")
    else:
        if len(body.password) < 10:
            raise HTTPException(422, "Choose a password with at least 10 characters")
        abuse.check_email_allowed(i.email)
        user = User(email=i.email, name=body.name.strip(), password_hash=hash_password(body.password), email_verified_at=datetime.now(timezone.utc))
        db.add(user)
        db.flush()
    db.add(Membership(user_id=user.id, tenant_id=i.tenant_id, role=i.role))
    user.email_verified_at = user.email_verified_at or datetime.now(timezone.utc)  # the invite email proved ownership
    i.accepted_at = datetime.now(timezone.utc)
    db.add(AuditLog(tenant_id=i.tenant_id, user_id=user.id, action="team.join", detail=i.email))
    db.commit()
    if user.totp_enabled_at:  # an existing account with two-factor on still has to prove the second factor: no session from an invite link alone
        token, _jti = mfa.issue_mfa_token(user.id)
        return {"mfaRequired": True, "mfaToken": token, "joined": True}
    _set_cookie(response, user.id)
    from ..deps import Ctx as _Ctx
    return _me(_Ctx(user=user, tenant=db.get(Tenant, i.tenant_id), role=i.role))
