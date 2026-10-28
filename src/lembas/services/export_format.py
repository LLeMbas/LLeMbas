"""The `lembas-export` file, version 1: the one reader every import goes through.

The format is the one the 1.7 LTS line writes (`LLeMbas.wiki/Export-format.md`,
1.7's `services/export.py`). Two kinds read it here: a person's memories
(`services/memory_io.py`) and an administrator's configuration
(`services/import_admin.py`). Both start with `load`, which checks the header
and nothing else -- unknown item types and fields are kept, because deciding
what to drop, and naming it, is the importer's job.

**Secrets** are Fernet tokens under a key derived from a passphrase by scrypt,
with the salt and parameters in the file's `encryption` header. The parameters
are read from the file, never assumed, so a later writer may raise them. The
header's `check` token decrypts to `lembas-export` under the right passphrase,
so a wrong one is known before anything is written (`opener`).

The same derivation protects the secret key inside a backup
(`services/backup.py`), which is why `derive_key` and `new_encryption` live
here rather than in either importer.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from lembas.web.i18n import t

FORMAT = "lembas-export"
VERSION = 1

KIND_ADMIN = "admin"
KIND_MEMORY = "memory"

SECRET_MARK = "$secret"
OMITTED_MARK = "$omitted"
CHECK_TEXT = "lembas-export"

# What a new file (or a backup's key) is encrypted with: 2^15 x 8, 32 MiB and
# a fraction of a second -- the same numbers 1.7 writes.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

# An administrator's export is configuration and a few workflow graphs; a
# memory export is at most a few hundred short records.
MAX_FILE = {KIND_MEMORY: 5 * 1024 * 1024, KIND_ADMIN: 20 * 1024 * 1024}


class Refused(ValueError):
    """The file as a whole is not one to import, in the reader's language."""


def stamp(moment: datetime | None) -> str | None:
    """UTC, ISO 8601 with `Z`, as the format writes every time."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(raw: bytes, kind: str) -> dict[str, Any]:
    """The document, its header checked for `kind`, or `Refused` saying why not."""
    if len(raw) > MAX_FILE.get(kind, MAX_FILE[KIND_ADMIN]):
        if kind == KIND_MEMORY:
            raise Refused(t("That file is too large to be a memory export."))
        raise Refused(t("That file is too large to be an administrator's export."))
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as problem:
        raise Refused(t("That file is not an export: it is not JSON.")) from problem
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise Refused(t("That file is not a LLeMbas export."))
    version = document.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version > VERSION:
        raise Refused(
            t(
                "That export is format version %(theirs)s; this server reads version %(ours)s.",
                theirs=version,
                ours=VERSION,
            )
        )
    found = document.get("kind")
    if found != kind:
        if kind == KIND_MEMORY:
            raise Refused(
                t("That is not a memory export; an administrator's export is imported elsewhere.")
            )
        raise Refused(
            t("That is not an administrator's export; memories are imported under Settings.")
        )
    if not isinstance(document.get("items"), list):
        raise Refused(t("That export has no items."))
    return document


# --- Secrets -------------------------------------------------------------------------
def derive_key(passphrase: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    """The Fernet key a passphrase makes with this salt and these parameters."""
    raw = Scrypt(salt=salt, length=KEY_BYTES, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def new_encryption(passphrase: str) -> tuple[Fernet, dict[str, Any]]:
    """A fresh salt, its Fernet and the header that lets a reader rebuild it."""
    salt = os.urandom(SALT_BYTES)
    fernet = Fernet(derive_key(passphrase, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P))
    header = {
        "scheme": "scrypt+fernet",
        "kdf": "scrypt",
        "salt": base64.b64encode(salt).decode("ascii"),
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
        "length": KEY_BYTES,
        "check": fernet.encrypt(CHECK_TEXT.encode()).decode("ascii"),
    }
    return fernet, header


def opener(params: Any, passphrase: str) -> Fernet:
    """The Fernet for an `encryption` header, the passphrase tested on `check`.
    Raises `Refused` for a wrong passphrase or a damaged header."""
    if not isinstance(params, dict):
        raise Refused(t("That file carries no encrypted keys."))
    try:
        salt = base64.b64decode(str(params["salt"]))
        n, r, p = int(params["n"]), int(params["r"]), int(params["p"])
        # A header is data from a file: refuse parameters that would take
        # minutes or gigabytes before the check could even fail.
        if not (2**10 <= n <= 2**20 and 1 <= r <= 16 and 1 <= p <= 4):
            raise ValueError("scrypt parameters out of range")
        fernet = Fernet(derive_key(passphrase, salt, n=n, r=r, p=p))
        if fernet.decrypt(str(params["check"]).encode("ascii")) != CHECK_TEXT.encode():
            raise ValueError("check text")
    except (KeyError, TypeError, ValueError, InvalidToken) as problem:
        raise Refused(t("Wrong passphrase, or the file's encryption is damaged.")) from problem
    return fernet


def is_secret(marker: Any) -> bool:
    return isinstance(marker, dict) and SECRET_MARK in marker


def omitted(marker: Any) -> str:
    """Why a secret was left out of the file, or "" when it was not."""
    if isinstance(marker, dict) and OMITTED_MARK in marker:
        return str(marker[OMITTED_MARK] or "omitted")
    return ""


def open_secret(fernet: Fernet | None, marker: Any) -> str:
    """The clear text of a `{"$secret": …}` marker; "" for anything else, or
    for a token the passphrase does not open."""
    if fernet is None or not is_secret(marker):
        return ""
    try:
        return fernet.decrypt(str(marker[SECRET_MARK]).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


__all__ = [
    "CHECK_TEXT",
    "FORMAT",
    "KIND_ADMIN",
    "KIND_MEMORY",
    "MAX_FILE",
    "OMITTED_MARK",
    "SECRET_MARK",
    "VERSION",
    "Refused",
    "derive_key",
    "is_secret",
    "load",
    "new_encryption",
    "omitted",
    "open_secret",
    "opener",
    "stamp",
]
