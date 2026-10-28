"""What the file pages need from the tree, above `services/fs.py`.

The explorer, the editor, the viewer, the `show` card and the undo of a chat's
changes all go through a `fs.View` -- a person, or a person in a chat -- so a
page can never show or change more than the tools could. Nothing here reads a
node's content without first asking the view for its path.

**What is shown, and how.** A file is *text* when it is valid UTF-8 without a
NUL in its first part, and at most `VIEW_MAX` bytes; anything else is offered
as a download and nothing more. Text is then drawn by its kind
(`tools.files.show_kind`, the same table the `show` tool uses): Markdown
through `services/markdown.py`, code and plain text escaped, HTML only in the
sealed frame (`security/sealed.py`). The bytes of a download go out as
`application/octet-stream` with `attachment`, never inline, so a file written
by a model never runs as this application.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    KIND_DIR,
    KIND_FILE,
    KIND_MOUNT,
    LEVEL_RO,
    LEVEL_STAGE,
    Chat,
    Node,
    NodeVersion,
    User,
)
from lembas.security import access
from lembas.security.access import at_least
from lembas.services import blobs, fs

# The most a page shows or edits inline; the same megabyte `show` allows.
VIEW_MAX = 1024 * 1024
# Raster images a browser may show inline. Never SVG: it is a document that
# can carry script.
INLINE_IMAGES = ("image/png", "image/jpeg", "image/gif", "image/webp")


def show_kind(path: str) -> tuple[str, str]:
    from lembas.services.tools.files import show_kind as kind_of

    return kind_of(path)


# --- Where you are -----------------------------------------------------------------
def crumbs(path: str) -> list[tuple[str, str]]:
    """`[(name, path), …]` from the root to `path`; the root is `("", "/")`."""
    out = [("", "/")]
    if path == "/":
        return out
    so_far = ""
    for part in path.strip("/").split("/"):
        so_far += "/" + part
        out.append((part, so_far))
    return out


@dataclass(frozen=True)
class Row:
    name: str
    path: str
    kind: str
    size: int
    level: str
    updated_at: datetime | None
    node_id: str
    mime: str

    @property
    def is_dir(self) -> bool:
        return self.kind in (KIND_DIR, KIND_MOUNT)

    @property
    def writable(self) -> bool:
        return at_least(self.level, LEVEL_STAGE)


def listing(v: fs.View, path: str) -> list[Row]:
    """A directory's entries the view can read, directories first."""
    entries = fs.list_dir(v, path)
    nodes = {
        n.path: n
        for n in v.db.scalars(
            select(Node).where(
                Node.org_id == v.org_id,
                Node.trashed_at.is_(None),
                Node.path.in_([e.path for e in entries] or [""]),
            )
        )
    }
    out = []
    for entry in entries:
        node = nodes.get(entry.path)
        out.append(
            Row(
                name=entry.name,
                path=entry.path,
                kind=entry.kind,
                size=entry.size,
                level=entry.level,
                updated_at=node.updated_at if node is not None else None,
                node_id=node.id if node is not None else "",
                mime=node.mime if node is not None else "",
            )
        )
    return out


def subdirs(v: fs.View, path: str) -> list[Row]:
    return [row for row in listing(v, path) if row.is_dir]


# --- Content ---------------------------------------------------------------------
def is_text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


@dataclass
class Opened:
    """A file (or one of its versions) as the viewer draws it."""

    path: str
    name: str
    node: Node | None
    version: NodeVersion | None
    size: int
    sha: str
    mime: str
    kind: str  # markdown | code | text | html | image | binary | large
    language: str
    text: str
    level: str

    @property
    def writable(self) -> bool:
        return at_least(self.level, LEVEL_STAGE)


def version_of(db: DBSession, node: Node, version_id: str) -> NodeVersion | None:
    if not version_id:
        return None
    row = db.get(NodeVersion, version_id)
    return row if row is not None and row.node_id == node.id else None


def open_file(v: fs.View, path: str, version_id: str = "") -> Opened:
    """The file at `path` -- at `version_id` when given -- through the view.

    Raises `fs.FsError` in the spec's words for anything the view may not read,
    and for a version that is not this file's."""
    node = fs.stat(v, path)
    if node is None or node.kind != KIND_FILE:
        raise fs.is_a_directory(path)
    version = version_of(v.db, node, version_id)
    if version_id and version is None:
        raise fs.no_such(path)
    digest = version.blob_hash if version is not None else (node.blob_hash or "")
    size = version.size if version is not None else (node.size or 0)
    kind, language = show_kind(node.path)
    text = ""
    mime = node.mime or "application/octet-stream"
    if mime in INLINE_IMAGES:
        kind = "image"
    elif size > VIEW_MAX:
        kind = "large"
    else:
        data = blobs.read_bytes(v.db, digest) if digest else b""
        if is_text(data):
            text = data.decode("utf-8")
        else:
            kind = "binary"
    return Opened(
        path=node.path,
        name=node.name,
        node=node,
        version=version,
        size=size,
        sha=digest,
        mime=mime,
        kind=kind,
        language=language,
        text=text,
        level=v.level(node.path),
    )


def stream(v: fs.View, path: str, version_id: str = ""):
    """`(node, iterator of bytes)` for a download, through the view."""
    node = fs.stat(v, path)
    if node is None or node.kind != KIND_FILE:
        raise fs.is_a_directory(path)
    version = version_of(v.db, node, version_id)
    if version_id and version is None:
        raise fs.no_such(path)
    digest = version.blob_hash if version is not None else node.blob_hash
    return node, (blobs.iter_parts(v.db, digest) if digest else iter(()))


def history(v: fs.View, path: str) -> list[NodeVersion]:
    return fs.versions(v, path)


def restore_version(v: fs.View, path: str, version_id: str) -> Node:
    """Make an old version the current one -- as a new version, so nothing is lost."""
    node = fs.stat(v, path)
    if node is None or node.kind != KIND_FILE:
        raise fs.is_a_directory(path)
    version = version_of(v.db, node, version_id)
    if version is None:
        raise fs.no_such(path)
    return fs.write(v, node.path, blobs.iter_parts(v.db, version.blob_hash))


# --- Undoing what an agent did in one chat -----------------------------------------
@dataclass(frozen=True)
class Undo:
    """One file a chat changed, and what undoing it does."""

    node: Node
    path: str
    action: str  # "restore" (to the content before the chat) | "remove" (the chat made it)
    before: NodeVersion | None
    changed_since: bool  # someone else wrote it after the chat did


def undo_plan(db: DBSession, user: User, chat: Chat) -> list[Undo]:
    """Every file the chat wrote that its owner may still change, and how it
    goes back: to the version before the chat's first write, or -- when the
    chat made the file -- to the trash. Files already back as they were are
    left out."""
    v = fs.view(db, user)
    touched = list(
        db.scalars(
            select(NodeVersion)
            .where(NodeVersion.chat_id == chat.id)
            .order_by(NodeVersion.at, NodeVersion.id)
        )
    )
    first: dict[str, NodeVersion] = {}
    last: dict[str, NodeVersion] = {}
    for row in touched:
        first.setdefault(row.node_id, row)
        last[row.node_id] = row
    out: list[Undo] = []
    for node_id, earliest in first.items():
        node = db.get(Node, node_id)
        if node is None or node.org_id != user.org_id or node.trashed_at is not None:
            continue
        if not at_least(v.level(node.path), LEVEL_STAGE):
            continue
        older = db.scalars(
            select(NodeVersion)
            .where(NodeVersion.node_id == node_id, NodeVersion.at <= earliest.at,
                   NodeVersion.id != earliest.id)
            .order_by(NodeVersion.at.desc(), NodeVersion.id.desc())
        ).first()
        later = db.scalars(
            select(NodeVersion)
            .where(NodeVersion.node_id == node_id, NodeVersion.at >= last[node_id].at,
                   NodeVersion.id != last[node_id].id)
            .order_by(NodeVersion.at.desc())
        ).first()
        changed_since = later is not None and later.chat_id != chat.id
        if older is None:
            out.append(Undo(node, node.path, "remove", None, changed_since))
        elif older.blob_hash != node.blob_hash:
            out.append(Undo(node, node.path, "restore", older, changed_since))
    out.sort(key=lambda u: u.path)
    return out


def undo(db: DBSession, user: User, chat: Chat) -> list[Undo]:
    """Apply `undo_plan`; returns what was done. The caller commits."""
    plan = undo_plan(db, user, chat)
    v = fs.view(db, user)
    for item in plan:
        if item.action == "remove":
            fs.rm(v, item.path)
        else:
            fs.write(v, item.path, blobs.iter_parts(db, item.before.blob_hash))
    return plan


# --- The tick tree (session grants, data-group presets and floors) ------------------
@dataclass(frozen=True)
class Tick:
    """One row of the access editor."""

    name: str
    path: str
    is_dir: bool
    depth: int
    rule: str  # the row's own level, "" when unticked
    effective: str  # what the rules give this path
    passage: bool  # unticked, with something ticked below: reachable only by full path
    expanded: bool


def effective(rules: dict[str, str], path: str) -> str:
    """The level the tick-tree rule gives `path`: its nearest ticked ancestor-or-self."""
    return access.nearest(rules, path) or "none"


def is_passage(rules: dict[str, str], path: str) -> bool:
    """Unticked (no rule of its own) with a ticked path below it whose nearest
    ticked ancestor is not above this one -- the ancestor shows as a passage."""
    if path in rules:
        return False
    if access.nearest(rules, path):
        return False
    return any(p != path and access.is_within(p, path) for p in rules)


def tick_rows(
    v: fs.View, rules: dict[str, str], *, root: str = "/", depth: int = 0,
    open_paths: set[str] | None = None,
) -> list[Tick]:
    """The rows under `root` the view can read, with every directory that has
    a rule at or below it opened (so a ticked path is always on the page)."""
    wanted_open = set(open_paths or ())
    for path in rules:
        for ancestor in access.chain(path)[1:]:
            wanted_open.add(ancestor)
    rows: list[Tick] = []
    try:
        entries = fs.list_dir(v, root)
    except fs.FsError:
        entries = []
    for entry in entries:
        is_dir = entry.kind in (KIND_DIR, KIND_MOUNT)
        expanded = is_dir and entry.path in wanted_open
        rows.append(
            Tick(
                name=entry.name,
                path=entry.path,
                is_dir=is_dir,
                depth=depth,
                rule=rules.get(entry.path, ""),
                effective=effective(rules, entry.path),
                passage=is_passage(rules, entry.path),
                expanded=expanded,
            )
        )
        if expanded:
            rows.extend(tick_rows(v, rules, root=entry.path, depth=depth + 1,
                                  open_paths=wanted_open))
    # A rule on a path this view cannot list (someone else's, or deeper than
    # anything here) is still shown, so saving never drops it unseen.
    if depth == 0:
        listed = {row.path for row in rows}
        for path in sorted(rules):
            if path not in listed and path != "/":
                rows.append(
                    Tick(
                        name=path,
                        path=path,
                        is_dir=True,
                        depth=0,
                        rule=rules[path],
                        effective=rules[path],
                        passage=False,
                        expanded=False,
                    )
                )
    return rows


def parse_rules(form, *, levels: tuple[str, ...]) -> dict[str, str]:
    """`tick=<path>` checkboxes and `level:<path>` selects into `{path: level}`.

    A path that does not normalise, or a level not offered, is dropped."""
    out: dict[str, str] = {}
    for raw in form.getlist("tick"):
        try:
            path = access.normalise(str(raw))
        except access.BadPath:
            continue
        level = str(form.get(f"level:{raw}", "") or "")
        if level in levels:
            out[path] = level
    extra = str(form.get("add_path", "") or "").strip()
    extra_level = str(form.get("add_level", "") or "")
    if extra and extra_level in levels:
        with contextlib.suppress(access.BadPath):
            out[access.normalise(extra)] = extra_level
    return out


__all__ = [
    "INLINE_IMAGES",
    "LEVEL_RO",
    "VIEW_MAX",
    "Opened",
    "Row",
    "Tick",
    "Undo",
    "crumbs",
    "effective",
    "history",
    "is_passage",
    "is_text",
    "listing",
    "open_file",
    "parse_rules",
    "restore_version",
    "stream",
    "subdirs",
    "tick_rows",
    "undo",
    "undo_plan",
]
