"""The file system a chat works in: an organisation's tree, kept in the database.

Every operation takes a `View` -- a person, optionally in a chat -- and goes
through `security/access.py` before it touches a node. A refusal raises
`FsError` carrying the exact sentence the harness spec fixes
(`harness_spec/filesystem.md`, *Errors*), so a tool returns it to the model as
it is, and the CLI and the server say the same thing about the same case.

**What a session cannot see is answered by what it already knows.** A path at
``none`` is reported by its nearest ancestor that is visible ("No such file or
directory") or a passage ("Access denied"), so a hidden entry and a missing one
read the same. A name taken by an entry at ``none`` cannot be created, and
saying so ("Access denied") gives away nothing more.

**Every write is a version.** A file's content is a blob (`services/blobs.py`);
writing appends a `NodeVersion` with the chat and message that made it, which
is what undoing an agent's changes in one session reads. `rm` moves to the
trash, `restore` brings it back; nothing here deletes a node outright.

**A rename rewrites the subtree's paths** in one statement, with `LIKE`
escaping, inside the caller's transaction.

Functions flush, never commit: the caller (a route, a tool) commits once.
"""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    GRANTEE_ORG,
    KIND_DIR,
    KIND_FILE,
    KIND_MOUNT,
    LEVEL_COMMIT,
    LEVEL_NONE,
    LEVEL_RO,
    LEVEL_STAGE,
    Chat,
    Node,
    NodeShare,
    NodeVersion,
    User,
)
from lembas.security import access
from lembas.security.access import BadPath, at_least, normalise
from lembas.services import blobs

HOME = "/home"
SKILLS = "/skills"


# --- Errors, in the spec's words ------------------------------------------------
class FsError(Exception):
    """A refusal, worded exactly as `filesystem.md` *Errors* gives it."""

    def __init__(self, message: str, path: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.path = path


def no_such(path: str) -> FsError:
    return FsError(f"No such file or directory: {path}", path)


def denied(path: str) -> FsError:
    return FsError(f"Access denied: {path} is not open to this session.", path)


def read_only(path: str) -> FsError:
    return FsError(f"Read-only: {path} can be read in this session but not changed.", path)


def not_committed(path: str) -> FsError:
    return FsError(
        f"Not committed: this session may change {path} but not commit it; "
        "the change stays staged.",
        path,
    )


def nothing_to_commit(path: str) -> FsError:
    return FsError(f"Nothing to commit: {path} is not in a repository.", path)


def is_a_directory(path: str) -> FsError:
    return FsError(f"Is a directory: {path}", path)


def not_a_directory(path: str) -> FsError:
    return FsError(f"Not a directory: {path}", path)


def already_exists(path: str) -> FsError:
    return FsError(f"Already exists: {path}", path)


# --- The view ------------------------------------------------------------------------
@dataclass
class View:
    """A person's view of their organisation's tree, in a chat when given."""

    db: DBSession
    user: User
    chat: Chat | None = None
    message_id: str | None = None
    resolver: access.Resolver = field(init=False)

    def __post_init__(self) -> None:
        self.resolver = access.resolver(self.db, self.user, self.chat)

    @property
    def org_id(self) -> str:
        return self.user.org_id

    @property
    def cwd(self) -> str:
        if self.chat is None:
            return "/"
        start = (self.chat.start_dir or "").strip() or (self.chat.boundary or "/")
        start = normalise(start)
        boundary = normalise(self.chat.boundary or "/")
        # A start directory outside the boundary is read as the boundary.
        return start if access.is_within(start, boundary) else boundary

    def path(self, raw: str) -> str:
        try:
            return normalise(raw, self.cwd)
        except BadPath as problem:
            raise no_such(str(raw)) from problem

    def level(self, path: str) -> str:
        return self.resolver.level(path)

    def refresh(self) -> None:
        """Forget what the resolver cached, after the tree changed under it."""
        self.resolver = access.resolver(self.db, self.user, self.chat)


def view(db: DBSession, user: User, chat: Chat | None = None, **kwargs) -> View:
    return View(db=db, user=user, chat=chat, **kwargs)


# --- Nodes, below access ------------------------------------------------------------
def lookup(db: DBSession, org_id: str, path: str) -> Node | None:
    if path == "/":
        return None
    return db.scalar(
        select(Node).where(Node.org_id == org_id, Node.path == path, Node.trashed_at.is_(None))
    )


def children(db: DBSession, org_id: str, parent: Node | None) -> list[Node]:
    where = Node.parent_id == parent.id if parent is not None else Node.parent_id.is_(None)
    return list(
        db.scalars(
            select(Node).where(Node.org_id == org_id, where, Node.trashed_at.is_(None))
        )
    )


def _nearest_existing(db: DBSession, org_id: str, path: str) -> tuple[str, Node | None]:
    """The nearest existing node at or above `path` (None for `/`)."""
    for candidate in access.chain(path):
        if candidate == "/":
            return "/", None
        node = lookup(db, org_id, candidate)
        if node is not None:
            return candidate, node
    return "/", None


def _join(parent: str, name: str) -> str:
    return f"{parent.rstrip('/')}/{name}"


def _make_node(
    db: DBSession,
    org_id: str,
    path: str,
    kind: str,
    *,
    owner_id: str | None,
    by: str | None,
    mount: dict | None = None,
) -> Node:
    """One node at `path`, making missing directories above it (mkdir -p)."""
    parent_path = access.parent_of(path)
    parent: Node | None = None
    if parent_path != "/":
        parent = lookup(db, org_id, parent_path)
        if parent is None:
            parent = _make_node(db, org_id, parent_path, KIND_DIR, owner_id=owner_id, by=by)
        elif parent.kind == KIND_FILE:
            raise not_a_directory(parent_path)
    name = access.check_name(path.rsplit("/", 1)[-1])
    node = Node(
        org_id=org_id,
        parent_id=parent.id if parent else None,
        name=name,
        path=path,
        kind=kind,
        owner_id=owner_id,
        updated_by=by,
        mount_json=mount,
    )
    db.add(node)
    db.flush()
    return node


def _set_content(
    db: DBSession,
    node: Node,
    digest: str,
    size: int,
    *,
    by: str | None,
    chat_id: str | None,
    message_id: str | None,
) -> NodeVersion:
    """Point a file at a blob and record the version, keeping refcounts true."""
    old = node.blob_hash
    blobs.incref(db, digest, 2)  # the node's current content, and its version
    blobs.decref(db, old)
    node.blob_hash = digest
    node.size = size
    node.updated_by = by
    node.updated_at = datetime.now(UTC)
    version = NodeVersion(
        node_id=node.id,
        blob_hash=digest,
        size=size,
        by_user_id=by,
        chat_id=chat_id,
        message_id=message_id,
    )
    db.add(version)
    db.flush()
    return version


# --- The hidden-path rule -----------------------------------------------------------
def _unseen(v: View, path: str) -> FsError:
    """What to say about a path the session cannot see (filesystem.md)."""
    if v.resolver.passage(path):
        return denied(path)
    for ancestor in access.chain(path)[1:]:
        if v.resolver.visible(ancestor):
            return no_such(path)
        if v.resolver.passage(ancestor):
            return denied(path)
    return denied(path)


def _visible_node(v: View, path: str) -> Node | None:
    """The node at a visible path, None for `/`; `FsError` when unseen/missing."""
    if not v.resolver.visible(path):
        raise _unseen(v, path)
    if path == "/":
        return None
    node = lookup(v.db, v.org_id, path)
    if node is None:
        at, above = _nearest_existing(v.db, v.org_id, access.parent_of(path))
        if above is not None and above.kind == KIND_FILE:
            raise not_a_directory(at)
        raise no_such(path)
    return node


def _writable(v: View, path: str) -> str:
    """The level at `path` when it may be changed, or the refusal."""
    level = v.level(path)
    if level == LEVEL_NONE:
        # A passage, a name taken by an entry at none, or simply not granted:
        # creating or changing it is denied, and nothing more is said.
        raise denied(path)
    if not at_least(level, LEVEL_STAGE):
        raise read_only(path)
    return level


def _in_mount(db: DBSession, org_id: str, path: str) -> Node | None:
    for candidate in access.chain(path):
        if candidate == "/":
            return None
        node = lookup(db, org_id, candidate)
        if node is not None and node.kind == KIND_MOUNT:
            return node
    return None


# --- Looking -------------------------------------------------------------------------
@dataclass(frozen=True)
class Entry:
    name: str
    path: str
    kind: str
    size: int
    level: str
    read_only: bool  # at ro inside a directory the session may change

    def line(self) -> str:
        """As a listing shows it (filesystem.md, *A listing*)."""
        out = self.name + ("/" if self.kind in (KIND_DIR, KIND_MOUNT) else "")
        if self.kind == KIND_MOUNT:
            out += " [mount]"
        if self.read_only:
            out += " [read-only]"
        return out


def stat(v: View, raw: str) -> Node | None:
    """The node at a path the session can read (None for `/`)."""
    return _visible_node(v, v.path(raw))


def list_dir(v: View, raw: str) -> list[Entry]:
    """The entries of a directory the session sees, at ro or above.

    Directories (and mounts) first, then by name. A passage cannot be listed,
    and a listing never shows one.
    """
    path = v.path(raw)
    node = _visible_node(v, path)
    if node is not None and node.kind == KIND_FILE:
        raise not_a_directory(path)
    found = children(v.db, v.org_id, node)
    v.resolver.remember(found)
    writable = at_least(v.level(path), LEVEL_STAGE)
    out: list[Entry] = []
    for child in found:
        level = v.level(child.path)
        if not at_least(level, LEVEL_RO):
            continue
        out.append(
            Entry(
                name=child.name,
                path=child.path,
                kind=child.kind,
                size=child.size,
                level=level,
                read_only=writable and level == LEVEL_RO,
            )
        )
    out.sort(key=lambda e: (e.kind == KIND_FILE, e.name))
    return out


def below(v: View, raw: str) -> list[Node]:
    """Everything under a directory the session can read that it can see,
    by path -- however it is reached: a node under a passage below the
    directory is found, the passage itself is not (filesystem.md, *A search*).
    What `glob`, `grep` and a search walk."""
    path = v.path(raw)
    node = _visible_node(v, path)
    if node is not None and node.kind == KIND_FILE:
        raise not_a_directory(path)
    query = select(Node).where(
        Node.org_id == v.org_id,
        Node.trashed_at.is_(None),
        Node.path.like(access.like_prefix(path), escape="\\"),
    )
    found = list(v.db.scalars(query.order_by(Node.path)))
    v.resolver.remember(found)
    return [n for n in found if v.resolver.visible(n.path)]


def check_writable(v: View, raw: str) -> str:
    """The level at a path the session may change, or the refusal in the spec's words."""
    return _writable(v, v.path(raw))


def read_stream(v: View, raw: str) -> Iterator[bytes]:
    path = v.path(raw)
    node = _visible_node(v, path)
    if node is None or node.kind != KIND_FILE:
        raise is_a_directory(path)
    if not node.blob_hash:
        return iter(())
    return blobs.iter_parts(v.db, node.blob_hash)


def read(v: View, raw: str) -> bytes:
    return b"".join(read_stream(v, raw))


def versions(v: View, raw: str) -> list[NodeVersion]:
    """A file's versions, newest first."""
    path = v.path(raw)
    node = _visible_node(v, path)
    if node is None or node.kind != KIND_FILE:
        raise is_a_directory(path)
    return list(
        v.db.scalars(
            select(NodeVersion)
            .where(NodeVersion.node_id == node.id)
            .order_by(NodeVersion.at.desc(), NodeVersion.id)
        )
    )


# --- Changing ------------------------------------------------------------------------
def write(
    v: View, raw: str, data: bytes | Iterable[bytes], *, mime: str = ""
) -> Node:
    """Write a file (made, with any missing directories, if it is not there)."""
    path = v.path(raw)
    if path == "/":
        raise is_a_directory(path)
    _writable(v, path)
    node = lookup(v.db, v.org_id, path)
    if node is not None and node.kind != KIND_FILE:
        raise is_a_directory(path)
    if node is None:
        at, above = _nearest_existing(v.db, v.org_id, access.parent_of(path))
        if above is not None and above.kind == KIND_FILE:
            raise not_a_directory(at)
        node = _make_node(v.db, v.org_id, path, KIND_FILE, owner_id=v.user.id, by=v.user.id)
        node.mime = mime or mimetypes.guess_type(node.name)[0] or "application/octet-stream"
    elif mime:
        node.mime = mime
    chunks = [data] if isinstance(data, (bytes, bytearray)) else data
    digest, size = blobs.put_stream(v.db, (bytes(c) for c in chunks))
    _set_content(
        v.db,
        node,
        digest,
        size,
        by=v.user.id,
        chat_id=v.chat.id if v.chat else None,
        message_id=v.message_id,
    )
    return node


def mkdir(v: View, raw: str) -> Node | None:
    """Make a directory, and any missing above it. An existing one is fine."""
    path = v.path(raw)
    if v.level(path) == LEVEL_NONE:
        # Making a name at none is denied, taken or not (filesystem.md).
        raise denied(path)
    existing = lookup(v.db, v.org_id, path) if path != "/" else None
    if path == "/" or (existing is not None and existing.kind != KIND_FILE):
        return existing  # already a directory the session sees: nothing to make
    _writable(v, path)
    if existing is not None:
        raise already_exists(path)
    at, above = _nearest_existing(v.db, v.org_id, access.parent_of(path))
    if above is not None and above.kind == KIND_FILE:
        raise not_a_directory(at)
    return _make_node(v.db, v.org_id, path, KIND_DIR, owner_id=v.user.id, by=v.user.id)


def _subtree(db: DBSession, org_id: str, path: str, *, live: bool = True) -> list[Node]:
    query = select(Node).where(
        Node.org_id == org_id,
        (Node.path == path) | Node.path.like(access.like_prefix(path), escape="\\"),
    )
    if live:
        query = query.where(Node.trashed_at.is_(None))
    return list(db.scalars(query))


def rm(v: View, raw: str) -> Node:
    """Move a node and everything below it to the trash."""
    path = v.path(raw)
    node = _visible_node(v, path)
    if node is None:
        raise denied(path)
    _writable(v, path)
    if node.kind == KIND_MOUNT:
        # Removed by whoever mounted it, never by a session.
        raise denied(path)
    subtree = _subtree(v.db, v.org_id, path)
    v.resolver.remember(subtree)
    if any(not at_least(v.level(n.path), LEVEL_STAGE) for n in subtree):
        # Something below is not this session's to remove.
        raise denied(path)
    if any(n.kind == KIND_MOUNT for n in subtree if n.id != node.id):
        raise denied(path)
    now = datetime.now(UTC)
    for each in subtree:
        each.trashed_at = now
        each.updated_by = v.user.id
    v.db.flush()
    v.refresh()
    return node


def _same_moment(a: datetime | None, b: datetime | None) -> bool:
    """SQLite drops the offset: compare a loaded stamp with one set in Python."""
    if a is None or b is None:
        return a is b
    return a.replace(tzinfo=None) == b.replace(tzinfo=None)


def restore(v: View, node_id: str) -> Node:
    """Bring a trashed node, and what went to the trash with it, back."""
    node = v.db.get(Node, node_id)
    if node is None or node.org_id != v.org_id or node.trashed_at is None:
        raise no_such(node_id)
    _writable(v, node.path)
    if lookup(v.db, v.org_id, node.path) is not None:
        raise already_exists(node.path)
    if node.parent_id is not None:
        parent = v.db.get(Node, node.parent_id)
        if parent is None or parent.trashed_at is not None:
            raise no_such(access.parent_of(node.path))
    when = node.trashed_at
    for each in _subtree(v.db, v.org_id, node.path, live=False):
        if _same_moment(each.trashed_at, when):
            each.trashed_at = None
    v.db.flush()
    v.refresh()
    return node


def trash(v: View) -> list[Node]:
    """What this person may restore: trashed nodes at the top of what went."""
    rows = list(
        v.db.scalars(
            select(Node)
            .where(Node.org_id == v.org_id, Node.trashed_at.is_not(None))
            .order_by(Node.trashed_at.desc())
        )
    )
    out = []
    for node in rows:
        parent = v.db.get(Node, node.parent_id) if node.parent_id else None
        if parent is not None and _same_moment(parent.trashed_at, node.trashed_at):
            continue  # went with its parent; restored with it
        if at_least(v.level(node.path), LEVEL_STAGE):
            out.append(node)
    return out


def mv(v: View, raw_from: str, raw_to: str) -> Node:
    """Rename or move a node; the subtree's paths follow in one statement."""
    source = v.path(raw_from)
    target = v.path(raw_to)
    node = _visible_node(v, source)
    if node is None:
        raise denied(source)
    _writable(v, source)
    target_parent = access.parent_of(target)
    _writable(v, target)
    if target == source:
        return node
    if access.is_within(target, source):
        raise denied(target)
    existing = lookup(v.db, v.org_id, target)
    if existing is not None:
        raise already_exists(target)
    if node.kind == KIND_MOUNT and v.chat is not None:
        raise denied(source)
    mount_from = _in_mount(v.db, v.org_id, source)
    mount_to = _in_mount(v.db, v.org_id, target_parent)
    if (mount_from.id if mount_from else None) != (mount_to.id if mount_to else None):
        # Between a mount and the rest of the tree, or between two mounts.
        raise denied(target)
    parent: Node | None = None
    if target_parent != "/":
        at, above = _nearest_existing(v.db, v.org_id, target_parent)
        if above is not None and above.kind == KIND_FILE:
            raise not_a_directory(at)
        parent = lookup(v.db, v.org_id, target_parent)
        if parent is None:
            parent = _make_node(
                v.db, v.org_id, target_parent, KIND_DIR, owner_id=v.user.id, by=v.user.id
            )
    name = access.check_name(target.rsplit("/", 1)[-1])
    rename_subtree(v.db, v.org_id, source, target)
    node.parent_id = parent.id if parent else None
    node.name = name
    node.updated_by = v.user.id
    v.db.flush()
    v.refresh()
    return node


def rename_subtree(db: DBSession, org_id: str, old: str, new: str) -> int:
    """Rewrite `path` for `old` and everything below it, trashed nodes too.

    One UPDATE: `path = :new || substr(path, len(:old) + 1)` where the path is
    `old` or starts with `old/` -- the prefix matched with `LIKE`, `%`, `_` and
    the escape character escaped, so a directory called `50%_off` moves only
    itself. Session expired after, since the ORM's copies are stale.
    """
    db.flush()
    result = db.execute(
        text(
            "UPDATE nodes SET path = :new || substr(path, :cut) "
            "WHERE org_id = :org AND (path = :old OR path LIKE :pattern ESCAPE '\\')"
        ),
        {
            "new": new,
            "cut": len(old) + 1,
            "org": org_id,
            "old": old,
            "pattern": access.like_prefix(old),
        },
    )
    db.expire_all()
    return result.rowcount


def check_commit(v: View, raw: str) -> None:
    """Whether the session may send a change at `path` to its forge."""
    path = v.path(raw)
    level = v.level(path)
    if level == LEVEL_NONE:
        raise _unseen(v, path)
    if not at_least(level, LEVEL_STAGE):
        raise read_only(path)
    if _in_mount(v.db, v.org_id, path) is None:
        raise nothing_to_commit(path)
    if not at_least(level, LEVEL_COMMIT):
        raise not_committed(path)


# --- The organisation's layout ------------------------------------------------------
_HOME_NAME = re.compile(r"[^a-z0-9._-]+")


def seed_org(db: DBSession, org_id: str) -> None:
    """`/home/` and `/skills/`, the organisation's own; `/skills` readable by all."""
    for path in (HOME, SKILLS):
        if lookup(db, org_id, path) is None:
            _make_node(db, org_id, path, KIND_DIR, owner_id=None, by=None)
    skills = lookup(db, org_id, SKILLS)
    shared = db.scalar(
        select(NodeShare.id).where(
            NodeShare.node_id == skills.id,
            NodeShare.grantee_kind == GRANTEE_ORG,
            NodeShare.grantee_id == org_id,
        )
    )
    if shared is None:
        db.add(
            NodeShare(
                node_id=skills.id, grantee_kind=GRANTEE_ORG, grantee_id=org_id, level=LEVEL_RO
            )
        )
        db.flush()


def ensure_layout(db: DBSession) -> int:
    """Seed every organisation and give every account its home. Returns how
    many homes were made. Idempotent; run at startup."""
    from lembas.db.models import Organisation

    for org_id in db.scalars(select(Organisation.id)):
        seed_org(db, org_id)
    made = 0
    for user in db.scalars(select(User)):
        if home_of(db, user) is None:
            ensure_home(db, user)
            made += 1
    return made


def home_of(db: DBSession, user: User) -> Node | None:
    home = lookup(db, user.org_id, HOME)
    if home is None:
        return None
    return db.scalar(
        select(Node).where(
            Node.parent_id == home.id, Node.owner_id == user.id, Node.trashed_at.is_(None)
        )
    )


def ensure_home(db: DBSession, user: User) -> Node:
    """`/home/<name>/`, owned by `user`; made the first time it is asked for."""
    seed_org(db, user.org_id)
    found = home_of(db, user)
    if found is not None:
        return found
    base = _HOME_NAME.sub("-", (user.email.split("@")[0] or user.name).lower()).strip("-.")
    base = base or "user"
    name, n = base, 1
    while lookup(db, user.org_id, _join(HOME, name)) is not None:
        n += 1
        name = f"{base}-{n}"
    home = _make_node(db, user.org_id, _join(HOME, name), KIND_DIR, owner_id=user.id, by=user.id)
    _make_node(db, user.org_id, _join(home.path, "skills"), KIND_DIR, owner_id=user.id, by=user.id)
    return home


def share(
    db: DBSession, node: Node, grantee_kind: str, grantee_id: str, level: str, *, by: User | None
) -> NodeShare:
    """Share a node (and all below it) at `level`; replaces an earlier share."""
    if level not in (LEVEL_RO, LEVEL_STAGE, LEVEL_COMMIT):
        raise ValueError(level)
    row = db.scalar(
        select(NodeShare).where(
            NodeShare.node_id == node.id,
            NodeShare.grantee_kind == grantee_kind,
            NodeShare.grantee_id == grantee_id,
        )
    )
    if row is None:
        row = NodeShare(node_id=node.id, grantee_kind=grantee_kind, grantee_id=grantee_id)
        db.add(row)
    row.level = level
    row.created_by = by.id if by else None
    db.flush()
    return row


def ensure_dir(db: DBSession, org_id: str, path: str, *, owner: User) -> Node:
    """A directory at `path` owned by `owner`, made (with any missing above it)
    when it is not there. Below access, for an administrator's action that puts
    something in a person's hands -- the import (`services/import_old.py`)
    makes `~/Imported` this way, and writes below it as that person."""
    path = normalise(path)
    node = lookup(db, org_id, path)
    if node is not None:
        if node.kind == KIND_FILE:
            raise not_a_directory(path)
        return node
    return _make_node(db, org_id, path, KIND_DIR, owner_id=owner.id, by=owner.id)


def count(db: DBSession, org_id: str) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(Node).where(
                Node.org_id == org_id, Node.trashed_at.is_(None)
            )
        )
        or 0
    )


__all__ = [
    "HOME",
    "SKILLS",
    "Entry",
    "FsError",
    "View",
    "below",
    "check_commit",
    "check_writable",
    "children",
    "count",
    "ensure_dir",
    "ensure_home",
    "ensure_layout",
    "home_of",
    "list_dir",
    "lookup",
    "mkdir",
    "mv",
    "read",
    "read_stream",
    "rename_subtree",
    "restore",
    "rm",
    "seed_org",
    "share",
    "stat",
    "trash",
    "versions",
    "view",
    "write",
]
