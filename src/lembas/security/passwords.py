"""Password hashing.

Argon2id via argon2-cffi, using the library's current recommended parameters.
``needs_rehash`` lets stored hashes be upgraded transparently when those
defaults tighten in a future release.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from lembas.web.i18n import t

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def validate_password(password: str) -> str | None:
    """Return a human-readable problem with the password, or None if it is fine."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return t("Password must be at least %(n)s characters.", n=MIN_PASSWORD_LENGTH)
    return None
