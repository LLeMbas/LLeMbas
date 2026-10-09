"""Where accounts are checked: the seam an external identity backend
plugs into.

Today there is one provider, `local`: accounts are rows in this instance's own
database and a password is checked against its Argon2id hash. The roadmap's
"accounts in Google Firebase instead of the instance's own database" is a second
provider implementing the same two calls -- check a sign-in, and find or make
the local row that everything else in LLeMbas keys on (chats, settings,
permissions). Sessions, tokens and permissions stay the instance's whichever
provider says who somebody is.

Chosen by `LEMBAS_IDENTITY` (default `local`); an unknown name is `local`, so a
typo cannot lock everybody out.
"""

from __future__ import annotations

import os
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import User
from lembas.security.passwords import verify_password


class Provider(Protocol):
    name: str

    def authenticate(self, db: DBSession, email: str, password: str) -> User | None:
        """The account these credentials sign in to, or None."""
        ...


class LocalProvider:
    """Accounts in this instance's database, passwords as Argon2id hashes."""

    name = "local"

    def authenticate(self, db: DBSession, email: str, password: str) -> User | None:
        user = db.scalar(select(User).where(User.email == email))
        if user is None or not verify_password(password, user.password_hash):
            return None
        return user


PROVIDERS: dict[str, Provider] = {"local": LocalProvider()}


def provider() -> Provider:
    return PROVIDERS.get(os.environ.get("LEMBAS_IDENTITY", "local"), PROVIDERS["local"])


__all__ = ["PROVIDERS", "LocalProvider", "Provider", "provider"]
