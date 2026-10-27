"""Who may do what to a path in an organisation's file tree.

**The one resolver.** `access(db, user, chat, path)` answers ``none``, ``ro``,
``stage`` or ``commit``, and every tool, search hit, `show`, Pyodide hand-in,
UI tree, API route and forge verb asks it -- through `services/fs.py`, which
also turns a refusal into the words the harness spec fixes
(`harness_spec/filesystem.md`).

A level is the lowest of three:

1. **Base** -- the person's own right to the node: the owner of the node or of
   any directory above it has ``commit``; otherwise the highest share on the
   nearest shared node at or above it, for them, any of their groups, or their
   organisation; otherwise ``none``. A directory nobody owns (``/home``,
   ``/skills``) is the organisation's, and its owners and admins have
   ``commit`` there -- never over a directory that has an owner below it, so a
   home stays private from them. An **instance** admin gets nothing for being
   one.
2. **Session** (a chat only) -- the chat's grants: its own rows once
   customised, otherwise its data group's preset (a group with no preset rules
   grants ``/`` at ``commit``: a session gets everything its person has unless
   restricted). The most specific grant wins and covers everything below; a
   path with no grant on it or above it is ``none``.
3. **Floor** (a chat only) -- the data group's ceilings, the most specific
   applying; no rule caps nothing.

A path at ``none`` with something visible below it is a **passage**: denied
for reading and writing alike, but how a session reaches a granted directory
under an ungranted one by its full path. Listing a passage is refused, and a
listing never shows one.

Paths here are absolute and normalised (`normalise`); the tree's root ``/`` has
no row.
"""

from __future__ import annotations

import posixpath
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    DEFAULT_GROUP,
    GRANTEE_GROUP,
    GRANTEE_ORG,
    GRANTEE_USER,
    LEVEL_COMMIT,
    LEVEL_NONE,
    LEVEL_RO,
    LEVELS,
    RULE_FLOOR,
    RULE_PRESET,
    Chat,
    DataGroupRule,
    Node,
    NodeShare,
    SessionGrant,
    User,
)

RANK = {level: n for n, level in enumerate(LEVELS)}


def rank(level: str) -> int:
    return RANK.get(level, 0)


def lowest(*levels: str) -> str:
    return min(levels, key=rank)


def highest(*levels: str) -> str:
    return max(levels, key=rank)


def at_least(level: str, wanted: str) -> bool:
    return rank(level) >= rank(wanted)


# --- Paths --------------------------------------------------------------------------
class BadPath(ValueError):
    """A name that cannot be in the tree: empty, `.`, `..` or with a NUL."""


def normalise(path: str, cwd: str = "/") -> str:
    """The absolute, resolved form of `path`.

    Relative paths resolve against `cwd`; `.` and `..` by their text, `..`
    above `/` staying at `/`; repeated `/` count as one and a trailing one
    changes nothing. Each name is put in NFC, so the same name typed two ways
    is one path.
    """
    if "\x00" in path:
        raise BadPath(path)
    joined = path if path.startswith("/") else f"{cwd.rstrip('/')}/{path}"
    resolved = posixpath.normpath("/" + joined.lstrip("/"))
    if resolved.startswith("//"):
        resolved = "/" + resolved.lstrip("/")
    return "/".join(unicodedata.normalize("NFC", part) for part in resolved.split("/")) or "/"


def check_name(name: str) -> str:
    """One path segment, NFC, or `BadPath`."""
    name = unicodedata.normalize("NFC", name)
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        raise BadPath(name)
    return name


def parent_of(path: str) -> str:
    return posixpath.dirname(path) or "/"


def chain(path: str) -> list[str]:
    """`path` and every directory above it, nearest first, `/` last."""
    out = [path]
    while path != "/":
        path = parent_of(path)
        out.append(path)
    return out


def is_within(path: str, top: str) -> bool:
    return top == "/" or path == top or path.startswith(top.rstrip("/") + "/")


def nearest(rules: Mapping[str, str], path: str) -> str | None:
    """The level of the most specific rule at or above `path`, or None."""
    for candidate in chain(path):
        if candidate in rules:
            return rules[candidate]
    return None


def like_prefix(path: str) -> str:
    """A LIKE pattern for everything strictly below `path` (ESCAPE '\\')."""
    escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return ("" if path == "/" else escaped) + "/%"


# --- The resolver ------------------------------------------------------------------
_MISSING = object()


@dataclass
class Resolver:
    """One person's view of their organisation's tree, optionally in a chat.

    Built once per request or tool call; caches what it reads, so a listing of
    a directory costs one query for the directory's chain, not one per entry.
    """

    db: DBSession
    user: User
    chat: Chat | None = None
    grants: dict[str, str] | None = None  # None outside a chat: no session layer
    floor: dict[str, str] = field(default_factory=dict)
    boundary: str = "/"
    _shares: dict[str, str] = field(default_factory=dict)
    _owners: dict[str, Any] = field(default_factory=dict)
    _levels: dict[str, str] = field(default_factory=dict)

    @property
    def org_id(self) -> str:
        return self.user.org_id

    # Loading ---------------------------------------------------------------------
    def load(self) -> Resolver:
        group_ids = [group.id for group in self.user.groups]
        grantees = [
            (NodeShare.grantee_kind == GRANTEE_USER) & (NodeShare.grantee_id == self.user.id),
            (NodeShare.grantee_kind == GRANTEE_ORG) & (NodeShare.grantee_id == self.org_id),
        ]
        if group_ids:
            grantees.append(
                (NodeShare.grantee_kind == GRANTEE_GROUP) & NodeShare.grantee_id.in_(group_ids)
            )
        rows = self.db.execute(
            select(Node.path, NodeShare.level)
            .join(NodeShare, NodeShare.node_id == Node.id)
            .where(Node.org_id == self.org_id, Node.trashed_at.is_(None), or_(*grantees))
        )
        for path, level in rows:
            # On one node, the highest share that reaches this person wins.
            self._shares[path] = highest(self._shares.get(path, LEVEL_NONE), level)

        if self.chat is not None:
            self.boundary = normalise(self.chat.boundary or "/")
            group = self.chat.data_group_id or DEFAULT_GROUP
            if self.chat.grants_customised:
                self.grants = {
                    normalise(row.path): row.level
                    for row in self.db.scalars(
                        select(SessionGrant).where(SessionGrant.chat_id == self.chat.id)
                    )
                }
            else:
                self.grants = rules_of(self.db, group, RULE_PRESET) or {"/": LEVEL_COMMIT}
            self.floor = rules_of(self.db, group, RULE_FLOOR)
        return self

    def remember(self, nodes: Iterable[Node]) -> None:
        """Owners of nodes already in hand (a listing's children)."""
        for node in nodes:
            self._owners[node.path] = node.owner_id

    def _owners_of(self, paths: list[str]) -> dict[str, Any]:
        wanted = [p for p in paths if p != "/" and p not in self._owners]
        if wanted:
            found = dict(
                self.db.execute(
                    select(Node.path, Node.owner_id).where(
                        Node.org_id == self.org_id,
                        Node.path.in_(wanted),
                        Node.trashed_at.is_(None),
                    )
                ).all()
            )
            for path in wanted:
                self._owners[path] = found.get(path, _MISSING)
        return {p: self._owners.get(p, _MISSING) for p in paths if p != "/"}

    # The three layers ------------------------------------------------------------
    def base(self, path: str) -> str:
        owners = self._owners_of(chain(path))
        if any(owner == self.user.id for owner in owners.values()):
            return LEVEL_COMMIT
        shared = nearest(self._shares, path) or LEVEL_NONE
        if self.user.manages_org and all(
            owner is None or owner is _MISSING for owner in owners.values()
        ):
            # The organisation's own directories, where nothing above or at the
            # path has an owner: its owners and admins run them.
            return LEVEL_COMMIT
        return shared

    def session(self, path: str) -> str:
        if self.grants is None:
            return LEVEL_COMMIT
        if not is_within(path, self.boundary):
            return LEVEL_NONE
        return nearest(self.grants, path) or LEVEL_NONE

    def ceiling(self, path: str) -> str:
        return nearest(self.floor, path) or LEVEL_COMMIT

    def level(self, path: str) -> str:
        if path not in self._levels:
            session = self.session(path)
            if session == LEVEL_NONE:
                self._levels[path] = LEVEL_NONE
            else:
                self._levels[path] = lowest(self.base(path), session, self.ceiling(path))
        return self._levels[path]

    def visible(self, path: str) -> bool:
        return at_least(self.level(path), LEVEL_RO)

    def passage(self, path: str) -> bool:
        """At none, with something visible somewhere below.

        The highest level anywhere below a path is reached at one of the
        places a layer changes -- a grant, a share, an owned node, the
        boundary -- so only those are tried, not every node in the subtree.
        """
        if self.visible(path):
            return False
        return any(self.visible(candidate) for candidate in self._openings_below(path))

    def _openings_below(self, path: str) -> Iterable[str]:
        below = [
            p
            for p in (
                *(self.grants or {}),
                *self._shares,
                self.boundary,
            )
            if p != path and is_within(p, path)
        ]
        yield from below
        pattern = like_prefix(path)
        owned = select(Node.path).where(
            Node.org_id == self.org_id,
            Node.trashed_at.is_(None),
            Node.path.like(pattern, escape="\\"),
        )
        mine = owned.where(Node.owner_id == self.user.id)
        yield from self.db.scalars(mine.order_by(Node.path).limit(50))
        if self.user.manages_org:
            yield from self.db.scalars(
                owned.where(Node.owner_id.is_(None)).order_by(Node.path).limit(50)
            )


def rules_of(db: DBSession, data_group_id: str, mode: str) -> dict[str, str]:
    return {
        normalise(rule.path): rule.level
        for rule in db.scalars(
            select(DataGroupRule).where(
                DataGroupRule.data_group_id == data_group_id, DataGroupRule.mode == mode
            )
        )
        if rule.level in RANK
    }


def resolver(db: DBSession, user: User, chat: Chat | None = None) -> Resolver:
    return Resolver(db=db, user=user, chat=chat).load()


def access(db: DBSession, user: User, chat: Chat | None, path: str) -> str:
    """The level `user` has on `path`, in `chat` when given."""
    return resolver(db, user, chat).level(normalise(path))


__all__ = [
    "RANK",
    "BadPath",
    "Resolver",
    "access",
    "at_least",
    "chain",
    "check_name",
    "highest",
    "is_within",
    "like_prefix",
    "lowest",
    "nearest",
    "normalise",
    "parent_of",
    "rank",
    "resolver",
    "rules_of",
]
