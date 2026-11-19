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

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
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
from lembas.services import blobs, file_kinds

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


class AlreadyExists(FsError):
    """A creation that found something at its path (`write(create=True)`)."""


class Changed(FsError):
    """The file holds something else than the content a write was based on."""


def changed(path: str) -> FsError:
    return Changed(f"Changed since it was read: {path}", path)


class TooMany(FsError):
    """More nodes than one copy makes."""


def already_exists(path: str) -> FsError:
    return AlreadyExists(f"Already exists: {path}", path)


# --- The view ------------------------------------------------------------------------
@dataclass
class View:
    """A person's view of their organisation's tree, in a chat when given."""

    db: DBSession
    user: User
    chat: Chat | None = None
    message_id: str | None = None
    # A write made *for* a chat but with the person's own access and not the
    # chat's (a generated file put into their home, whatever the chat is
    # bounded to): the chat its version records. Read only when `chat` is None.
    stamp_chat_id: str | None = None
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
        boundary = normalise(self.chat.boundary or "/")
        start = (self.chat.start_dir or "").strip()
        if not start:
            # Nothing chosen: a chat bounded by the whole tree starts in its
            # person's home, not at the organisation's root -- a model writing
            # `notes.md` should not put it where everybody sees it (and where a
            # member may not write at all).
            start = self._home() if boundary == "/" else boundary
        start = normalise(start)
        # A start directory outside the boundary is read as the boundary.
        return start if access.is_within(start, boundary) else boundary

    def _home(self) -> str:
        if not hasattr(self, "_home_path"):
            home = home_of(self.db, self.user)
            self._home_path = home.path if home is not None else "/"
        return self._home_path

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


def path_of(db: DBSession, org_id: str, node_id: str) -> str | None:
    """Where a live node of this organisation is now, by its id: what lets a
    link made when a file was written (a generated video's player) follow the
    file through a rename or a move. Says nothing about who may read it --
    the path is then asked for like any other."""
    node = db.get(Node, node_id) if node_id else None
    if node is None or node.org_id != org_id or node.trashed_at is not None:
        return None
    return node.path


def located(v: View, node_id: str) -> tuple[Node, bool]:
    """Where a node is **now**, for somebody who may read it: the node, and
    whether it is in the trash. The same answer as asking for its path would
    get -- `no_such`, naming the id and never the path, when the view cannot
    see it. A trashed node is judged by its own shares (`_held`)."""
    node = v.db.get(Node, node_id) if node_id else None
    if node is None or node.org_id != v.org_id:
        raise no_such(node_id)
    if node.trashed_at is not None:
        level, _ = _held(v, node)
        if not at_least(level, LEVEL_RO):
            raise no_such(node_id)
        return node, True
    try:
        seen = stat(v, node.path)
    except FsError as refused:
        raise no_such(node_id) from refused
    if seen is None or seen.id != node.id:
        raise no_such(node_id)
    return node, False


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
    v: View,
    raw: str,
    data: bytes | Iterable[bytes],
    *,
    mime: str = "",
    create: bool = False,
    base: str | None = None,
) -> Node:
    """Write a file (made, with any missing directories, if it is not there).

    `create`: make a new file and never write over one -- anything already at
    the path is `already_exists`. Decided by the tree's own rule (one live
    node per path, a unique index), not by looking first: of two creations of
    one name at the same moment, one is refused.

    `base`: the content hash this write was made from. The file is written
    only if it still holds exactly that (`Changed` otherwise) -- compared
    **in the statement that takes the write**, not by looking first: of two
    saves based on one content, the second is refused however close they
    come. None compares nothing.

    What the file is -- its kind and its type -- is read from its name and its
    first bytes (`services/file_kinds.py`) at every write. `mime` is what the
    sender called it and is not believed: it is kept as a parameter so a
    caller need not change, and decides nothing."""
    del mime
    path = v.path(raw)
    if path == "/":
        raise is_a_directory(path)
    _writable(v, path)
    node = lookup(v.db, v.org_id, path)
    if node is not None and create:
        raise already_exists(path)
    if node is not None and node.kind != KIND_FILE:
        raise is_a_directory(path)
    if node is None:
        at, above = _nearest_existing(v.db, v.org_id, access.parent_of(path))
        if above is not None and above.kind == KIND_FILE:
            raise not_a_directory(at)
        try:
            with v.db.begin_nested():
                node = _make_node(
                    v.db, v.org_id, path, KIND_FILE, owner_id=v.user.id, by=v.user.id
                )
        except IntegrityError as clash:
            # Somebody made it between the look and the insert.
            if lookup(v.db, v.org_id, path) is None:
                raise
            raise already_exists(path) from clash
    elif base is not None:
        # Compare and take the write in one statement. A file with no
        # content yet holds NULL.
        held = v.db.execute(
            text(
                "UPDATE nodes SET updated_by = updated_by "
                "WHERE id = :id AND trashed_at IS NULL AND COALESCE(blob_hash, '') = :base"
            ),
            {"id": node.id, "base": base},
        ).rowcount
        if held != 1:
            raise changed(path)
    chunks = [data] if isinstance(data, (bytes, bytearray)) else data
    head = bytearray()

    def seen() -> Iterator[bytes]:
        for chunk in chunks:
            chunk = bytes(chunk)
            if len(head) < file_kinds.HEAD_BYTES:
                head.extend(chunk[: file_kinds.HEAD_BYTES - len(head)])
            yield chunk

    digest, size = blobs.put_stream(v.db, seen())
    found = file_kinds.detect(node.name, bytes(head))
    node.mime = found.mime
    node.content_kind = found.kind
    _set_content(
        v.db,
        node,
        digest,
        size,
        by=v.user.id,
        chat_id=v.chat.id if v.chat else v.stamp_chat_id,
        message_id=v.message_id,
    )
    return node


def kind_of(db: DBSession, node: Node, digest: str | None = None) -> file_kinds.Kind:
    """What a file is (`services/file_kinds.py`): the kind settled when it was
    written, or -- for a file from before that was kept, and for an older
    version, whose bytes are others -- read from the first bytes now."""
    current = digest is None or digest == (node.blob_hash or "")
    if current and node.content_kind:
        by_name = file_kinds.detect(node.name, None)
        language = by_name.language if by_name.kind == node.content_kind else ""
        if node.content_kind == file_kinds.SVG:
            language = "xml"
        return file_kinds.Kind(node.content_kind, node.mime or "application/octet-stream", language)
    digest = digest if digest is not None else (node.blob_hash or "")
    head = blobs.head(db, digest, file_kinds.HEAD_BYTES) if digest else b""
    return file_kinds.detect(node.name, head)


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


def _held(v: View, node: Node) -> tuple[str, bool]:
    """What this session may do to a **trashed** node, and whether its person
    owns it: the node's own owners and shares (`access.held`), under the
    chat's grants and the data group's ceilings for where it lay.

    Never `v.level(node.path)`: a path is judged from the live nodes on it,
    and a trashed node's own shares are not among them -- a share that
    restricts one file inside a shared folder would stop counting the moment
    the file is in the trash."""
    own = access.held(v.db, v.user, node)
    level = access.lowest(
        own.level, v.resolver.session(node.path), v.resolver.ceiling(node.path)
    )
    return level, own.owned and level != LEVEL_NONE


def restore(v: View, node_id: str) -> Node:
    """Bring a trashed node, and what went to the trash with it, back."""
    node = v.db.get(Node, node_id)
    if node is None or node.org_id != v.org_id or node.trashed_at is None:
        raise no_such(node_id)
    level, _ = _held(v, node)
    if level == LEVEL_NONE:
        raise no_such(node_id)
    if not at_least(level, LEVEL_STAGE):
        raise read_only(node.path)
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
    """This person's trash: trashed nodes at the top of what went, that they
    **put there themselves or own** -- and may still change. Not everything
    trashed in a directory they can write: in a shared folder that would be
    everybody's."""
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
        level, owned = _held(v, node)
        # `rm` writes who trashed it on every node that goes.
        if at_least(level, LEVEL_STAGE) and (owned or node.updated_by == v.user.id):
            out.append(node)
    return out


def _doomed(v: View, top: Node) -> list[Node] | None:
    """`top` and every trashed node below it, when this session may remove
    **each of them** for good: its person owns it, or holds `commit` on it by
    the node's own shares. None when any one is not theirs to destroy."""
    found: list[Node] = []
    seen: set[str] = set()
    queue = [top]
    while queue:
        node = queue.pop()
        if node.id in seen or node.trashed_at is None:
            continue
        seen.add(node.id)
        level, _ = _held(v, node)
        if not at_least(level, LEVEL_COMMIT):
            return None
        found.append(node)
        queue.extend(v.db.scalars(select(Node).where(Node.parent_id == node.id)))
    return found


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
    rename_subtree(v.db, v.org_id, source, target, node_id=node.id)
    node.parent_id = parent.id if parent else None
    node.name = name
    node.updated_by = v.user.id
    v.db.flush()
    v.refresh()
    return node


# How many nodes one copy makes, and how often its name is chosen again when
# somebody takes it first.
COPY_MAX = 5000
COPY_NAME_TRIES = 8


def _free_name(v: View, directory: str, name: str, *, is_dir: bool) -> str:
    """`name`, or `name (2)`, `name (3)` ... -- the first that nothing in
    `directory` is called. A file keeps its extension: `notes (2).md`."""
    stem, dot, ext = name.rpartition(".")
    if is_dir or not dot or not stem:
        stem, ext = name, ""
    candidate, n = name, 1
    while lookup(v.db, v.org_id, _join(directory, candidate)) is not None:
        n += 1
        candidate = f"{stem} ({n})" + (f".{ext}" if ext else "")
    return candidate


def cp(v: View, raw_from: str, raw_to_dir: str, name: str | None = None) -> Node:
    """Copy a file, or a directory and what the session can read below it,
    into the directory `raw_to_dir`. Returns the copy.

    The source needs reading and every path the copy makes needs changing --
    the same rules as `read` and `write`. A name already taken is never
    overwritten: the copy is called `name (2).ext`. Contents are not
    duplicated: the copy names the same blobs, counted once more each. A
    copy starts with one version, made now by this person (and this chat);
    the source's history stays the source's. A directory cannot be copied
    into itself, and a mount is not copied at all.
    """
    source = v.path(raw_from)
    into = v.path(raw_to_dir)
    node = _visible_node(v, source)
    if node is None:
        raise denied(source)
    parent = _visible_node(v, into)
    if parent is not None and parent.kind == KIND_FILE:
        raise not_a_directory(into)
    try:
        wanted = access.check_name(name) if name else node.name
    except BadPath as problem:
        raise no_such(str(name)) from problem
    if node.kind != KIND_FILE and access.is_within(into, source):
        raise denied(_join(into, wanted))
    if node.kind == KIND_FILE:
        nodes = [node]
    else:
        nodes = sorted(_subtree(v.db, v.org_id, source), key=lambda n: n.path)
        v.resolver.remember(nodes)
        if any(n.kind == KIND_MOUNT for n in nodes):
            raise denied(source)
        # What the session cannot see is not there to copy.
        nodes = [n for n in nodes if v.resolver.visible(n.path)]
    if len(nodes) > COPY_MAX:
        # A folder copied beside itself doubles each time: bounded per copy.
        raise TooMany(
            f"Too much to copy at once: {len(nodes)} items, and one copy makes at most "
            f"{COPY_MAX}. Copy it in parts: {source}",
            source,
        )
    _writable(v, _join(into, wanted))
    chat_id = v.chat.id if v.chat else None
    made: Node | None = None
    target = ""
    for each in nodes:
        if made is None:
            # The copy's own name: free when looked for, and taken by the
            # tree's rule when made. Somebody making the same name in
            # between is a reason to take the next one, not an error.
            copy = None
            for _ in range(COPY_NAME_TRIES):
                target = _join(into, _free_name(v, into, wanted, is_dir=node.kind != KIND_FILE))
                _writable(v, target)
                try:
                    with v.db.begin_nested():
                        copy = _make_node(
                            v.db, v.org_id, target, each.kind, owner_id=v.user.id, by=v.user.id
                        )
                    break
                except IntegrityError:
                    v.db.expire_all()
            if copy is None:
                raise already_exists(target)
        else:
            path = target + each.path[len(source):]
            _writable(v, path)
            copy = _make_node(v.db, v.org_id, path, each.kind, owner_id=v.user.id, by=v.user.id)
        if each.kind == KIND_FILE:
            copy.mime = each.mime
            copy.content_kind = each.content_kind
            if each.blob_hash:
                _set_content(
                    v.db, copy, each.blob_hash, each.size or 0,
                    by=v.user.id, chat_id=chat_id, message_id=v.message_id,
                )
        if made is None:
            made = copy
    v.db.flush()
    v.refresh()
    return made


def purge(v: View, node_id: str | None = None) -> int:
    """Remove for good what is in the trash -- one thing (`node_id`), or
    everything in this person's trash (`trash`). Returns how many nodes went.
    The contents are let go of, not deleted: a blob nothing names any more is
    collected by the sweep (`services/blobs.py`).

    **Removing for good is the owner's**: of the node, or of a directory
    above it (a home's owner for everything in it), or somebody holding
    `commit` on it -- judged by the trashed node's own shares, and for
    **every node below** as `rm` does, not only the top one. One thing named
    that is not all theirs is refused whole. Emptying passes over what its
    person put in the trash but may not destroy: it stays, for its owner."""
    if node_id is None:
        groups = [found for top in trash(v) if (found := _doomed(v, top)) is not None]
    else:
        node = v.db.get(Node, node_id)
        if node is None or node.org_id != v.org_id or node.trashed_at is None:
            raise no_such(node_id)
        level, _ = _held(v, node)
        if level == LEVEL_NONE:
            raise no_such(node_id)
        found = _doomed(v, node)
        if found is None:
            raise denied(node.path)
        groups = [found]
    doomed: list[Node] = []
    seen: set[str] = set()
    for group in groups:
        for node in group:
            if node.id not in seen:
                seen.add(node.id)
                doomed.append(node)
    if not doomed:
        return 0
    ids = [n.id for n in doomed]
    for start in range(0, len(ids), 500):
        batch = ids[start:start + 500]
        for digest in v.db.scalars(
            select(NodeVersion.blob_hash).where(NodeVersion.node_id.in_(batch))
        ):
            blobs.decref(v.db, digest)
        v.db.execute(delete(NodeVersion).where(NodeVersion.node_id.in_(batch)))
        v.db.execute(delete(NodeShare).where(NodeShare.node_id.in_(batch)))
    for node in doomed:
        blobs.decref(v.db, node.blob_hash)
    # Deepest first, so no row is ever left naming a parent that has gone.
    for node in sorted(doomed, key=lambda n: n.path.count("/"), reverse=True):
        v.db.delete(node)
        v.db.flush()
    v.refresh()
    return len(doomed)


def rename_subtree(
    db: DBSession, org_id: str, old: str, new: str, *, node_id: str | None = None
) -> int:
    """Rewrite `path` for `old` and everything below it.

    One UPDATE: `path = :new || substr(path, len(:old) + 1)` where the path is
    `old` or starts with `old/` -- the prefix matched with `LIKE`, `%`, `_` and
    the escape character escaped, so a directory called `50%_off` moves only
    itself. Session expired after, since the ORM's copies are stale.

    **A live node is found by its path; a trashed one only by its parents**
    (`node_id`, the node being moved): what was trashed out of the moved
    directory follows it, so a restore lands where the directory is now. A
    trashed node that merely *has* the old path -- an earlier directory of
    the same name, trashed before this one was made, and what was in it -- is
    somebody else's history and stays where it was. A path alone cannot tell
    the two apart; a parent can.
    """
    db.flush()
    result = db.execute(
        text(
            "WITH RECURSIVE moved(id) AS ("
            "  SELECT :node"
            "  UNION"
            "  SELECT n.id FROM nodes n JOIN moved m ON n.parent_id = m.id"
            ") "
            "UPDATE nodes SET path = :new || substr(path, :cut) "
            "WHERE org_id = :org AND (path = :old OR path LIKE :pattern ESCAPE '\\') "
            "AND (trashed_at IS NULL OR id IN (SELECT id FROM moved))"
        ),
        {
            "new": new,
            "cut": len(old) + 1,
            "org": org_id,
            "old": old,
            "pattern": access.like_prefix(old),
            "node": node_id or "",
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


def start(v: View) -> str:
    """Where a person's explorer opens: `/` when they can read it (an
    organisation's owner or admin), otherwise their own home. A member's `/`
    is a passage, which cannot be listed, so opening there would greet every
    member with a refusal."""
    if v.resolver.visible("/"):
        return "/"
    home = home_of(v.db, v.user)
    return home.path if home is not None else "/"


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
    "cp",
    "ensure_dir",
    "ensure_home",
    "ensure_layout",
    "home_of",
    "kind_of",
    "list_dir",
    "lookup",
    "mkdir",
    "mv",
    "purge",
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
