"""At-rest encryption maintenance for webhook signing secrets and 2FA secrets.

  python -m app.tools.reencrypt            # encrypt legacy plaintext rows AND re-encrypt everything with the current ENCRYPTION_KEY
  python -m app.tools.reencrypt --check    # only report how many rows are plaintext / encrypted (changes nothing)

Key rotation (see docs/SECRETS.md): set ENCRYPTION_KEY=<new>, ENCRYPTION_KEY_PREVIOUS=<old>, restart, run this tool,
then remove ENCRYPTION_KEY_PREVIOUS. The api also calls `backfill()` at start-up so plaintext rows never survive a restart.
"""
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import crypto
from ..db import SessionLocal
from ..models import User, WebhookEndpoint


def backfill(db: Session) -> int:
    """Encrypt every legacy plaintext webhook secret. Returns how many rows were upgraded."""
    n = 0
    for ep in db.execute(select(WebhookEndpoint)).scalars():
        up = crypto.upgrade_if_plain(ep.secret)
        if up:
            ep.secret = up
            n += 1
    if n:
        db.commit()
    return n


def reencrypt_all(db: Session) -> dict[str, int]:
    """Re-encrypt every stored secret with the current key (plaintext rows are encrypted). Rows that cannot be opened are skipped and counted."""
    done = failed = 0
    for ep in db.execute(select(WebhookEndpoint)).scalars():
        try:
            ep.secret = crypto.reencrypt(ep.secret) if crypto.is_encrypted(ep.secret) else crypto.encrypt(ep.secret)
            done += 1
        except crypto.DecryptError:
            failed += 1
    for u in db.execute(select(User).where(User.totp_secret.is_not(None))).scalars():
        try:
            u.totp_secret = crypto.reencrypt(str(u.totp_secret))
            done += 1
        except crypto.DecryptError:
            failed += 1
    db.commit()
    return {"reencrypted": done, "failed": failed}


def report(db: Session) -> dict[str, int]:
    eps = list(db.execute(select(WebhookEndpoint.secret)).scalars())
    return {"webhookSecretsEncrypted": sum(1 for s in eps if crypto.is_encrypted(s)), "webhookSecretsPlaintext": sum(1 for s in eps if not crypto.is_encrypted(s))}


if __name__ == "__main__":
    with SessionLocal() as session:
        print(report(session) if "--check" in sys.argv else reencrypt_all(session))
