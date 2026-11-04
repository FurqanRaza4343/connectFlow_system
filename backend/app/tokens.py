"""Single-use, expiring tokens. Only a SHA-256 hash is stored, so a database leak does not leak usable links."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import EmailToken


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(db: Session, user_id: str, kind: str, hours: int) -> str:
    # one live token per (user, kind): older ones are invalidated
    now = datetime.now(timezone.utc)
    for old in db.query(EmailToken).filter(EmailToken.user_id == user_id, EmailToken.kind == kind, EmailToken.used_at.is_(None)).all():
        old.used_at = now
    plain = secrets.token_urlsafe(32)
    db.add(EmailToken(user_id=user_id, kind=kind, token_hash=digest(plain), expires_at=now + timedelta(hours=hours)))
    db.commit()
    return plain


def consume(db: Session, token: str, kind: str) -> str | None:
    """Return the user id if the token is valid (and mark it used), else None."""
    t = db.query(EmailToken).filter(EmailToken.token_hash == digest(token or ""), EmailToken.kind == kind).first()
    now = datetime.now(timezone.utc)
    if not t or t.used_at or t.expires_at < now:
        return None
    t.used_at = now
    db.commit()
    return t.user_id
