"""Encryption at rest for small secrets (webhook signing secrets, 2FA secrets).

Values are stored as ``enc1:<fernet token>``. A value without that prefix is legacy plaintext: ``reveal`` returns it unchanged
and callers upgrade the row with ``encrypt`` the next time they touch it (see ``upgrade_if_plain``).

Keys: ``ENCRYPTION_KEY`` (current) and ``ENCRYPTION_KEY_PREVIOUS`` (decrypt-only while rotating). With no ENCRYPTION_KEY a key is
derived from JWT_SECRET with HKDF so development works out of the box; production refuses to start without a dedicated key.
"""
import base64
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .config import settings

log = logging.getLogger("crypto")
PREFIX = "enc1:"


class DecryptError(Exception):
    """The stored value is encrypted but none of the configured keys can open it."""


def valid_key(key: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(key.encode())) == 32
    except Exception:  # noqa: BLE001
        return False


def derive_key(secret: str) -> str:
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"connectflow-at-rest-v1", info=b"fernet").derive(secret.encode())
    return base64.urlsafe_b64encode(raw).decode()


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    keys: list[str] = []
    if settings.encryption_key:
        keys.append(settings.encryption_key)
        keys.append(derive_key(settings.jwt_secret))  # rows written before a dedicated key was introduced still open
    else:
        log.warning("ENCRYPTION_KEY is not set: deriving the at-rest key from JWT_SECRET (fine for development, set a dedicated key in production)")
        keys.append(derive_key(settings.jwt_secret))
    if settings.encryption_key_previous:
        keys.append(settings.encryption_key_previous)
    return MultiFernet([Fernet(k.encode()) for k in dict.fromkeys(keys)])


def is_encrypted(value: str | None) -> bool:
    return bool(value) and str(value).startswith(PREFIX)


def encrypt(plain: str) -> str:
    return PREFIX + _fernet().encrypt(plain.encode()).decode()


def reveal(value: str) -> str:
    """Plaintext of a stored value (legacy plaintext passes through). Raises DecryptError if no key opens it."""
    if not is_encrypted(value):
        return value
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        raise DecryptError("stored secret cannot be decrypted with the configured keys") from e


def upgrade_if_plain(value: str) -> str | None:
    """The encrypted form of a legacy plaintext value, or None when it is already encrypted / empty."""
    return None if not value or is_encrypted(value) else encrypt(value)


def reencrypt(value: str) -> str:
    """Re-encrypt with the current (first) key; used by `python -m app.tools.reencrypt` after rotating ENCRYPTION_KEY."""
    return encrypt(reveal(value))


def reset_cache() -> None:  # tests
    _fernet.cache_clear()
