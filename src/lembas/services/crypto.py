"""Symmetric encryption for secrets stored in the database.

Only upstream API keys use this today. The key is derived from
``LEMBAS_SECRET_KEY`` rather than stored separately, which means rotating that
variable makes every stored API key unreadable -- decrypt() returns "" rather
than raising, so the app degrades to "re-enter your keys" instead of crashing.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from lembas.config import settings

log = logging.getLogger(__name__)

# Rendered in a form in place of a stored secret. If a submitted value still
# equals this, the field was never touched and the stored secret must be kept --
# otherwise saving a name change would silently wipe the credential beside it.
# Lives here rather than in one admin module because every form that edits a
# secret needs the same dance.
UNCHANGED_SENTINEL = "•" * 12


@lru_cache
def _fernet() -> Fernet:
    # Fernet requires a 32-byte urlsafe-base64 key; SECRET_KEY is free-form text.
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    """Encrypt a secret. Empty input stays empty -- keyless endpoints are valid."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Decrypt a secret, returning "" if it cannot be read.

    An unreadable value almost always means LEMBAS_SECRET_KEY changed. Failing
    soft keeps the admin UI usable so the key can simply be re-entered.
    """
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        log.warning("could not decrypt a stored secret; has LEMBAS_SECRET_KEY changed?")
        return ""


def mask(secret: str) -> str:
    """Render a secret for display: never the whole thing, just enough to identify it."""
    if not secret:
        return ""
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:3]}{'*' * 8}{secret[-4:]}"


def keep_or_replace(submitted: str, stored_ciphertext: str) -> str:
    """Resolve a submitted secret field against what is already stored.

    Three cases, and the middle one is the reason this exists: the sentinel
    means "the form rendered a mask and nobody typed over it", which is not the
    same as an empty field. An explicitly emptied field does mean "this endpoint
    needs no key", so it clears the stored value.
    """
    submitted = submitted.strip()
    if submitted == UNCHANGED_SENTINEL:
        return stored_ciphertext
    return encrypt(submitted)
