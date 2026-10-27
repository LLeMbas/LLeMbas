"""The chat's file tools, over the organisation's tree (harness spec 3.0.0).

`read`, `write`, `edit`, `list`, `glob`, `grep`, `mkdir`, `mv`, `rm`, `show`
and `python`: the same names, descriptions and parameters as the LLeMbas CLI's
(`sessions: both` for the first six and `show`; `mkdir`, `mv`, `rm` and
`python` are the server's alone), here over `services/fs.py` instead of a disk.

**Every access goes through the resolver.** A tool builds an `fs.View` of the
chat -- the person, the chat's grants, its data group's floor -- and `fs` asks
`security/access.py` before it touches a node, raising `FsError` in the exact
words of `filesystem.md`. Those words are the tool's answer, so the model hears
the same sentence for the same case in a chat and on a device.

**Paths come back absolute.** Whatever the call gave, an answer names the
resolved absolute path (filesystem.md, *Paths*); a relative path starts at the
chat's start directory.

**Writing needs reading.** An existing file is changed only after this chat
read it, and only while it is as the chat last saw it -- the CLI's guard, kept
here in the chat's stored results (`services/context.py` `last_seen`) rather
than in a process's memory, since a chat outlives any one reply.

What each result is, for its insertion into the conversation (ids, read
headers, stubs, diffs), is handed over in `ToolOutcome.context` and decided in
`services/context.py`.
"""

from __future__ import annotations

import base64
import mimetypes
import posixpath
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from lembas.db.models import (
    KIND_DIR,
    KIND_FILE,
    KIND_MOUNT,
    LEVEL_RO,
    LEVEL_STAGE,
    Chat,
    Node,
    User,
)
from lembas.db.session import session_scope
from lembas.security import access
from lembas.services import context as context_service
from lembas.services import fs, harness_spec, pages
from lembas.services.tools.core import (
    FAMILY_FILES,
    FAMILY_PYTHON,
    RISK_EXECUTE,
    RISK_INTERACT,
    RISK_READ,
    RISK_WRITE,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)
from lembas.services.tools.replace import ReplaceError, replace

# The read tool's window and its longest line (the CLI's read.ts).
MAX_LINES = 2000
MAX_LINE = 2000
# Read whole only up to this; past it, grep.
MAX_READ_BYTES = 50_000_000
# What glob and grep return, and how far grep scans before it stops counting.
MAX_HITS = 200
MAX_SCAN_LINES = 1000
# A file grep skips: a binary or a very large one.
MAX_GREP_BYTES = 5_000_000
# The tree a listing shows, in lines.
MAX_LIST_LINES = 400
# The most `show` sends to a viewer (acp.md, `_lembas/show`: at most 1 MB).
SHOW_MAX_BYTES = 1024 * 1024
# A nearby instruction file handed over with a read, at most this much of it.
NEARBY_CHARS = 20_000
INSTRUCTION_NAMES = ("AGENTS.md", "CLAUDE.md")

# `python`'s limits, as its description's variables say them
# (`core.LLEMBAS_VARS`): the page's runner enforces them, and the server waits
# a little longer than one run may take.
PYTHON_SECONDS = 30
PYTHON_MEMORY = "512 MB"
PYTHON_OUTPUT_CHARS = 20_000
PYTHON_FILE_BYTES = 10 * 1024 * 1024


class ToolRefusal(Exception):
    """A refusal the model is told as it is."""


@dataclass
class Session:
    db: Any
    user: User
    chat: Chat
    view: fs.View


@contextmanager
def _session(context: ToolContext) -> Iterator[Session]:
    """The chat's view of the tree, in one transaction committed on success."""
    with session_scope() as db:
        user = db.get(User, context.owner_id) if context.owner_id else None
        chat = db.get(Chat, context.chat_id) if context.chat_id else None
        if user is None or chat is None:
            raise ToolRefusal("The file tools work only inside a chat.")
        view = fs.view(db, user, chat, message_id=context.message_id or None)
        yield Session(db=db, user=user, chat=chat, view=view)


def _refused(name: str, message: str, **event: Any) -> ToolOutcome:
    return ToolOutcome(
        message, {"name": name, "status": "error", "error": message[:300], **event}
    )


async def _guarded(name: str, run, context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    try:
        return await run(context, args)
    except fs.FsError as exc:
        return _refused(name, exc.message, path=exc.path)
    except ToolRefusal as exc:
        return _refused(name, str(exc))


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


# --- read ------------------------------------------------------------------------------
def read_body(
    text: str, offset: int | None = None, limit: int | None = None
) -> tuple[str, int, int, int]:
    """The read tool's body for a file's text, and the lines shown (`from`, `to`,
    `of`): numbered lines from `offset` (1-indexed), at most `limit` (2000), a
    line longer than 2000 characters cut, the note when lines remain; "(empty
    file)" for an empty one. The CLI's `readBody`, character for character."""
    lines = text.split("\n")
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()
    if len(lines) == 1 and lines[0] == "":
        lines.pop()
    start = (offset or 1) - 1
    end = min(len(lines), start + (limit or MAX_LINES))
    shown = "\n".join(
        f"{start + i + 1}: {line[:MAX_LINE] + ' […cut]' if len(line) > MAX_LINE else line}"
        for i, line in enumerate(lines[start:end])
    )
    more = (
        f"\n\n({len(lines) - end} more lines — read again with offset {end + 1})"
        if end < len(lines)
        else ""
    )
    body = ("(empty file)" if not lines else shown) + more
    return body, start + 1, end, len(lines)


def _nearby_instructions(s: Session, context: ToolContext, path: str) -> str:
    """A directory's own instruction file, handed over the first time something
    under it is read in this reply -- for directories whose file is not already
    in the prompt (the chain from the boundary down to the start directory)."""
    chain = set(access.chain(s.view.cwd))
    boundary = access.normalise(s.chat.boundary or "/")
    out: list[str] = []
    for directory in access.chain(access.parent_of(path)):
        if directory in chain or not access.is_within(directory, boundary):
            continue
        if directory in context.instructions_seen:
            continue
        for name in INSTRUCTION_NAMES:
            candidate = posixpath.join(directory, name)
            if candidate == path:
                continue
            if not s.view.resolver.visible(candidate):
                continue
            node = fs.lookup(s.db, s.view.org_id, candidate)
            if node is None or node.kind != KIND_FILE:
                continue
            context.instructions_seen.add(directory)
            text = _decode(fs.read(s.view, candidate)).strip()[:NEARBY_CHARS]
            out.append(
                f'\n\n<instructions from="{candidate}">\nThis directory has its own '
                f"instructions; they apply to work under it.\n{text}\n</instructions>"
            )
            break
    return "".join(reversed(out))


def _listing(s: Session, path: str) -> str:
    entries = fs.list_dir(s.view, path)
    return "\n".join(entry.line() for entry in entries) or "(empty directory)"


async def _run_read(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    raw = str(args.get("path") or "")
    with _session(context) as s:
        path = s.view.path(raw)
        node = fs.stat(s.view, path)
        if node is None or node.kind != KIND_FILE:
            listing = _listing(s, path)
            return ToolOutcome(listing, {"name": "read", "status": "ok", "path": path})
        if (node.size or 0) > MAX_READ_BYTES:
            raise ToolRefusal(
                f"{path} is {round(node.size / 1e6)} MB — too large to read whole; search it "
                "with grep."
            )
        data = fs.read(s.view, path)
        if _binary(data):
            raise ToolRefusal(f"{path} is a binary file ({len(data)} bytes)")
        body, frm, to, of = read_body(_decode(data), _int(args.get("offset")),
                                      _int(args.get("limit")))
        body += _nearby_instructions(s, context, path)
        return ToolOutcome(
            body,
            {"name": "read", "status": "ok", "path": path, "query": f"{path} {frm}–{to}"},
            context={
                "kind": "read",
                "tool": "read",
                "path": raw,
                "key": path,
                "sha": context_service.sha256(data),
                "from": frm,
                "to": to,
                "of": of,
            },
        )


def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


# --- write and edit --------------------------------------------------------------------
def _guard_existing(s: Session, node: Node, path: str) -> None:
    """Refuse to change a file this chat has not read, or one changed since."""
    seen = context_service.last_seen(s.db, s.chat.id, path)
    if seen is None:
        raise ToolRefusal(f"{path} exists and has not been read in this session. Read it first.")
    if node.blob_hash and seen != node.blob_hash:
        raise ToolRefusal(
            f"{path} changed since you last read it. Read it again before changing it."
        )


def _stray_skill_note(path: str) -> str:
    """A note when a SKILL.md is written where no skill is loaded from."""
    from lembas.services import skill_files

    return skill_files.stray_note(path)


async def _run_write(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    raw = str(args.get("path") or "")
    content = str(args.get("content") if args.get("content") is not None else "")
    with _session(context) as s:
        path = s.view.path(raw)
        node = fs.lookup(s.db, s.view.org_id, path) if path != "/" else None
        before = ""
        existed = False
        if node is not None and s.view.resolver.visible(path):
            if node.kind != KIND_FILE:
                raise fs.is_a_directory(path)
            fs.check_writable(s.view, path)
            _guard_existing(s, node, path)
            data = fs.read(s.view, path)
            if _binary(data):
                raise ToolRefusal(f"{path} is a binary file; write cannot replace it.")
            before = _decode(data)
            existed = True
        encoded = content.encode("utf-8")
        fs.write(s.view, path, encoded)
        sha = context_service.sha256(encoded)
        note = _stray_skill_note(path)
        diff = context_service.patch(path, before, content)
        added, removed = _counts(diff)
        if existed:
            message = f"Replaced {path} (+{added} −{removed})."
            entry = {"path": raw, "key": path, "sha": sha,
                     "hunks": context_service.hunks(before, content)}
        else:
            message = f"Created {path} ({len(content.split(chr(10)))} lines)."
            entry = {"path": raw, "key": path, "sha": sha,
                     "created": context_service.line_count(content)}
        return ToolOutcome(
            message + note,
            {"name": "write", "status": "ok", "path": path, "diff": diff},
            context={"kind": "edit", "tool": "write", "files": [entry],
                     **({"note": note} if note else {})},
        )


def _counts(diff: str) -> tuple[int, int]:
    added = removed = 0
    for line in diff.split("\n"):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


async def _run_edit(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    raw = str(args.get("path") or "")
    old = str(args.get("old") if args.get("old") is not None else "")
    new = str(args.get("new") if args.get("new") is not None else "")
    with _session(context) as s:
        path = s.view.path(raw)
        node = fs.stat(s.view, path)
        if node is None or node.kind != KIND_FILE:
            raise fs.is_a_directory(path)
        fs.check_writable(s.view, path)
        _guard_existing(s, node, path)
        data = fs.read(s.view, path)
        if _binary(data):
            raise ToolRefusal(f"{path} is a binary file; edit cannot change it.")
        text = _decode(data)
        crlf = "\r\n" in text
        before = text.replace("\r\n", "\n") if crlf else text
        try:
            after = replace(
                before, old.replace("\r\n", "\n"), new.replace("\r\n", "\n"),
                bool(args.get("all")),
            )
        except ReplaceError as exc:
            raise ToolRefusal(f"{path}: {exc}") from exc
        written = after.replace("\n", "\r\n") if crlf else after
        encoded = written.encode("utf-8")
        fs.write(s.view, path, encoded)
        diff = context_service.patch(path, before, after)
        added, removed = _counts(diff)
        note = _stray_skill_note(path)
        return ToolOutcome(
            f"Edited {path} (+{added} −{removed})." + note,
            {"name": "edit", "status": "ok", "path": path, "diff": diff},
            context={
                "kind": "edit",
                "tool": "edit",
                "files": [{"path": raw, "key": path, "sha": context_service.sha256(encoded),
                           "hunks": context_service.hunks(before, after)}],
                **({"note": note} if note else {}),
            },
        )


# --- list, glob, grep ------------------------------------------------------------------
def _entry_line(s: Session, node: Node, parent_writable: bool) -> str:
    level = s.view.level(node.path)
    entry = fs.Entry(
        name=node.name, path=node.path, kind=node.kind, size=node.size or 0, level=level,
        read_only=parent_writable and level == LEVEL_RO,
    )
    return entry.line()


async def _run_list(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    raw = str(args.get("path") or ".")
    depth = max(1, min(6, _int(args.get("depth")) or 2))
    with _session(context) as s:
        path = s.view.path(raw)
        node = fs.stat(s.view, path)
        if node is not None and node.kind == KIND_FILE:
            raise fs.not_a_directory(path)
        lines: list[str] = []

        def walk(directory: str, indent: str, level: int) -> None:
            entries = fs.list_dir(s.view, directory)
            writable = access.at_least(s.view.level(directory), LEVEL_STAGE)
            for entry in entries:
                if len(lines) >= MAX_LIST_LINES:
                    return
                read_only = writable and entry.level == LEVEL_RO
                lines.append(indent + fs.Entry(
                    entry.name, entry.path, entry.kind, entry.size, entry.level, read_only
                ).line())
                if entry.kind in (KIND_DIR, KIND_MOUNT) and level + 1 < depth:
                    walk(entry.path, indent + "  ", level + 1)

        walk(path, "", 0)
        more = ""
        if len(lines) >= MAX_LIST_LINES:
            more = "\n(more entries — list a directory further down)"
        return ToolOutcome(
            ("\n".join(lines) or "(empty)") + more,
            {"name": "list", "status": "ok", "path": path, "hits": len(lines)},
        )


def glob_regex(pattern: str) -> re.Pattern[str]:
    """A glob as ripgrep's `--glob` reads one: `**` crosses directories, `*` and
    `?` do not, `{a,b}` is either, `[...]` a class. A pattern with no `/` matches
    a file's name at any depth (gitignore's rule)."""
    out: list[str] = []
    i = 0
    depth = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                if pattern[i + 2 : i + 3] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "{":
            depth += 1
            out.append("(?:")
        elif c == "}" and depth:
            depth -= 1
            out.append(")")
        elif c == "," and depth:
            out.append("|")
        elif c == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append(f"[{body}]")
                i = end
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _matcher(pattern: str):
    regex = glob_regex(pattern.lstrip("/") if pattern.startswith("/") else pattern)
    anchored = "/" in pattern.strip("/") or pattern.startswith("**")

    def matches(relative: str) -> bool:
        if anchored:
            return bool(regex.match(relative))
        return bool(regex.match(relative.rsplit("/", 1)[-1])) or bool(regex.match(relative))

    return matches


def _files_below(s: Session, root: str) -> list[Node]:
    """Every file under a directory the session can read, visible to it, newest first."""
    return [n for n in fs.below(s.view, root) if n.kind == KIND_FILE]


async def _run_glob(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    pattern = str(args.get("pattern") or "").strip()
    raw = str(args.get("path") or ".")
    with _session(context) as s:
        root = s.view.path(raw)
        node = fs.stat(s.view, root)
        if node is not None and node.kind == KIND_FILE:
            raise fs.not_a_directory(root)
        matches = _matcher(pattern or "**")
        prefix = "" if root == "/" else root
        found = [n for n in _files_below(s, root) if matches(n.path[len(prefix) + 1 :])]
        found.sort(key=lambda n: (n.updated_at is not None, n.updated_at), reverse=True)
        shown = [n.path for n in found[:MAX_HITS]]
        more = ""
        if len(found) > MAX_HITS:
            more = f"\n({len(found) - MAX_HITS} more — narrow the pattern)"
        return ToolOutcome(
            ("\n".join(shown) or "No files found.") + more,
            {"name": "glob", "status": "ok", "path": root, "hits": len(found)},
        )


async def _run_grep(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    pattern = str(args.get("pattern") or "")
    raw = str(args.get("path") or ".")
    literal = bool(args.get("literal"))
    flags = re.IGNORECASE if args.get("ignore_case") else 0
    try:
        regex = re.compile(re.escape(pattern) if literal else pattern, flags)
    except re.error as exc:
        return _refused("grep", f"invalid regex: {exc}")
    include = str(args.get("include") or "").strip()
    with _session(context) as s:
        target = s.view.path(raw)
        node = fs.stat(s.view, target)
        if node is not None and node.kind == KIND_FILE:
            files = [node]
            prefix = access.parent_of(target)
        else:
            files = sorted(_files_below(s, target), key=lambda n: n.path)
            prefix = target
        if include:
            wanted = _matcher(include)
            cut = 0 if prefix == "/" else len(prefix)
            files = [n for n in files if wanted(n.path[cut + 1 :])]
        lines: list[str] = []
        for each in files:
            if (each.size or 0) > MAX_GREP_BYTES:
                continue
            data = fs.read(s.view, each.path)
            if _binary(data):
                continue
            for number, line in enumerate(_decode(data).split("\n"), start=1):
                if regex.search(line):
                    lines.append(f"{each.path}:{number}:{line[:400]}")
            if len(lines) > MAX_SCAN_LINES:
                break
        hint = ""
        if not lines and not literal and re.search(r"[()\[\]{}.*+?|\\^$]", pattern):
            hint = (
                " The pattern is a regular expression; to search for the text as written, set "
                "literal: true."
            )
        more = (
            f"\n({len(lines) - MAX_HITS} more matches — narrow the search)"
            if len(lines) > MAX_HITS
            else ""
        )
        return ToolOutcome(
            ("\n".join(lines[:MAX_HITS]) or "No matches." + hint) + more,
            {"name": "grep", "status": "ok", "path": target, "hits": len(lines)},
        )


# --- search ----------------------------------------------------------------------------
SEARCH_DEFAULT = 10
SEARCH_MAX = 50


async def _run_search(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Passages by meaning and by words (`services/index`), filtered by the
    chat's view after scoring: `path:from-to`, then the passage."""
    from lembas.db.models import IndexChunk
    from lembas.services import index

    query = str(args.get("query") or "").strip()
    if not query:
        return _refused("search", "query is what to look for, in words.")
    limit = max(1, min(SEARCH_MAX, _int(args.get("limit")) or SEARCH_DEFAULT))
    raw = args.get("path")
    with session_scope() as db:
        # The embedder's question is an HTTP request; nothing is written here.
        vector = await index.embed_query(db, query)
    with _session(context) as s:
        prefix = None
        if raw not in (None, ""):
            prefix = s.view.path(str(raw))
            node = fs.stat(s.view, prefix)  # a passage is denied like a listing
            if node is not None and node.kind == KIND_FILE:
                raise fs.not_a_directory(prefix)
        hits = index.search_with(
            s.db, s.user, s.chat, query, vector, path_prefix=prefix, kinds=("file",),
            limit=limit,
        )
        texts = {
            row.id: row.text
            for row in s.db.query(IndexChunk).filter(
                IndexChunk.id.in_([hit.chunk_id for hit in hits])
            )
        }
        blocks = [
            f"{hit.path}:{hit.line_start}-{hit.line_end}\n"
            f"{(texts.get(hit.chunk_id) or hit.snippet).strip()}"
            for hit in hits
        ]
        return ToolOutcome(
            "\n\n".join(blocks) or "Nothing found.",
            {
                "name": "search",
                "status": "ok",
                "query": query,
                "hits": len(hits),
                "results": [
                    {"title": hit.path, "path": hit.path, "lines": [hit.line_start, hit.line_end]}
                    for hit in hits
                ],
            },
        )


# --- mkdir, mv, rm ---------------------------------------------------------------------
async def _run_mkdir(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    raw = str(args.get("path") or "")
    with _session(context) as s:
        path = s.view.path(raw)
        existed = path == "/" or fs.lookup(s.db, s.view.org_id, path) is not None
        fs.mkdir(s.view, path)
        said = f"{path}/ already exists." if existed else f"Made {path}/."
        return ToolOutcome(said, {"name": "mkdir", "status": "ok", "path": path, "query": path})


async def _run_mv(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    source_raw = str(args.get("from") or "")
    target_raw = str(args.get("to") or "")
    with _session(context) as s:
        source = s.view.path(source_raw)
        target = s.view.path(target_raw)
        fs.mv(s.view, source, target)
        return ToolOutcome(
            f"Moved {source} to {target}.",
            {"name": "mv", "status": "ok", "path": target, "query": f"{source} → {target}"},
        )


async def _run_rm(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    raw = str(args.get("path") or "")
    with _session(context) as s:
        path = s.view.path(raw)
        node = fs.rm(s.view, path)
        what = "directory" if node.kind != KIND_FILE else "file"
        return ToolOutcome(
            f"Moved the {what} {path} to the trash; the user can restore it from there.",
            {"name": "rm", "status": "ok", "path": path, "query": path, "node_id": node.id},
        )


# --- show ------------------------------------------------------------------------------
LANGUAGES = {
    "ts": "typescript", "tsx": "tsx", "js": "javascript", "mjs": "javascript",
    "cjs": "javascript", "jsx": "jsx", "json": "json", "py": "python", "rs": "rust",
    "go": "go", "rb": "ruby", "php": "php", "java": "java", "kt": "kotlin", "swift": "swift",
    "c": "c", "h": "c", "cc": "cpp", "cpp": "cpp", "hpp": "cpp", "cs": "csharp", "sh": "bash",
    "bash": "bash", "zsh": "bash", "fish": "fish", "css": "css", "scss": "scss", "sql": "sql",
    "yaml": "yaml", "yml": "yaml", "toml": "toml", "xml": "xml", "svg": "xml", "lua": "lua",
    "zig": "zig", "dart": "dart", "vue": "vue", "svelte": "svelte",
}


def show_kind(path: str) -> tuple[str, str]:
    """The viewer's kind for a file, by its name, and its language (acp.md):
    `.md` markdown, `.html`/`.htm` html, a known source extension code, anything
    else text. The CLI's `showKind`."""
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in ("md", "markdown"):
        return "markdown", ""
    if ext in ("html", "htm"):
        return "html", ""
    language = LANGUAGES.get(ext) or {"Dockerfile": "dockerfile", "Makefile": "makefile"}.get(
        name, ""
    )
    return ("code", language) if language else ("text", "")


def show_unavailable() -> str:
    return str(harness_spec.tools()["show"]["unavailable"])


async def _run_show(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """The payload the transcript's card is drawn from -- never the file's text:
    the viewer fetches the version named here, through the same resolver."""
    if not context.display:
        return _refused("show", show_unavailable())
    raw = str(args.get("path") or "")
    line = _int(args.get("line"))
    with _session(context) as s:
        path = s.view.path(raw)
        node = fs.stat(s.view, path)
        if node is None or node.kind != KIND_FILE:
            raise fs.is_a_directory(path)
        if (node.size or 0) > SHOW_MAX_BYTES:
            raise ToolRefusal(
                f"{path} is {round((node.size or 0) / 1024)} KB; at most 1 MB can be shown. "
                "Give the user the path instead."
            )
        data = fs.read(s.view, path)
        if _binary(data):
            raise ToolRefusal(
                f"{path} is not a text file; it cannot be shown. Give the user the path instead."
            )
        versions = fs.versions(s.view, path)
        kind, language = show_kind(path)
        payload = {
            "path": path,
            "name": node.name,
            "kind": kind,
            "language": language,
            "line": line,
            "node_id": node.id,
            "version_id": versions[0].id if versions else "",
            "sha": node.blob_hash or context_service.sha256(data),
            "size": len(data),
            "mime": node.mime or mimetypes.guess_type(node.name)[0] or "text/plain",
        }
        at = f" at line {line}" if line else ""
        return ToolOutcome(
            f"Shown to the user: {path}{at}.",
            {"name": "show", "status": "ok", "path": path, "query": path, "show": payload},
        )


# --- python ----------------------------------------------------------------------------
def python_unavailable() -> str:
    return str(harness_spec.tools()["python"]["unavailable"])


async def _run_python(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Hand the code and the files it names to the chat's page, and save back
    what it lists in `save` through the ordinary write path.

    The request the page is given (`services/pages.py` `take`):
    `{"call_id", "code", "files": [{"path", "content_b64"}], "save": [path],
    "limits": {"seconds", "memory", "output_chars"}}`; its answer (`answer`):
    `{"stdout", "result", "error", "files": [{"path", "content_b64"}]}`.
    """
    if not pages.attached(context.chat_id):
        return _refused("python", python_unavailable())
    code = str(args.get("code") or "")
    wanted = [str(p) for p in (args.get("files") or []) if str(p).strip()]
    save = [str(p) for p in (args.get("save") or []) if str(p).strip()]
    with _session(context) as s:
        handed: list[dict[str, str]] = []
        for raw in wanted:
            path = s.view.path(raw)
            node = fs.stat(s.view, path)
            if node is None or node.kind != KIND_FILE:
                raise fs.is_a_directory(path)
            if (node.size or 0) > PYTHON_FILE_BYTES:
                raise ToolRefusal(f"{path} is too large to copy in (at most 10 MB).")
            handed.append({"path": path, "content_b64": base64.b64encode(
                fs.read(s.view, path)).decode("ascii")})
        targets = []
        for raw in save:
            path = s.view.path(raw)
            fs.check_writable(s.view, path)
            targets.append(path)
    request = {
        "code": code,
        "files": handed,
        "save": targets,
        "limits": {"seconds": PYTHON_SECONDS, "memory": PYTHON_MEMORY,
                   "output_chars": PYTHON_OUTPUT_CHARS},
    }
    try:
        answer = await pages.run(context.chat_id, request, timeout=PYTHON_SECONDS + 15)
    except (pages.NotAttached, TimeoutError):
        return _refused("python", python_unavailable())
    stdout = str(answer.get("stdout") or "")[:PYTHON_OUTPUT_CHARS]
    result = answer.get("result")
    error = str(answer.get("error") or "")
    saved: list[str] = []
    if answer.get("files"):
        with _session(context) as s:
            for item in answer["files"]:
                path = s.view.path(str(item.get("path") or ""))
                if path not in targets:
                    continue
                fs.write(s.view, path, base64.b64decode(str(item.get("content_b64") or "")))
                saved.append(path)
    parts = [stdout.rstrip("\n")] if stdout else []
    if result not in (None, ""):
        parts.append(f"→ {result}")
    if error:
        parts.append(f"Error: {error}")
    if saved:
        parts.append("Saved: " + ", ".join(saved))
    first = code.strip().split("\n", 1)[0][:200]
    return ToolOutcome(
        "\n".join(parts) or "(no output)",
        {"name": "python", "status": "error" if error else "ok", "command": first,
         "saved": saved},
    )


def _def(name: str, family: str, run, risk: str) -> ToolDef:
    async def guarded(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return await _guarded(name, run, context, args)

    return ToolDef(
        name=name,
        family=family,
        description=spec_description(name),
        parameters=spec_parameters(name),
        run=guarded,
        risk=risk,
    )


TOOLS: tuple[ToolDef, ...] = (
    _def("read", FAMILY_FILES, _run_read, RISK_READ),
    _def("list", FAMILY_FILES, _run_list, RISK_READ),
    _def("glob", FAMILY_FILES, _run_glob, RISK_READ),
    _def("grep", FAMILY_FILES, _run_grep, RISK_READ),
    _def("search", FAMILY_FILES, _run_search, RISK_READ),
    _def("write", FAMILY_FILES, _run_write, RISK_WRITE),
    _def("edit", FAMILY_FILES, _run_edit, RISK_WRITE),
    _def("mkdir", FAMILY_FILES, _run_mkdir, RISK_WRITE),
    _def("mv", FAMILY_FILES, _run_mv, RISK_WRITE),
    _def("rm", FAMILY_FILES, _run_rm, RISK_WRITE),
    _def("show", FAMILY_FILES, _run_show, RISK_INTERACT),
    _def("python", FAMILY_PYTHON, _run_python, RISK_EXECUTE),
)

__all__ = [
    "LANGUAGES",
    "TOOLS",
    "glob_regex",
    "python_unavailable",
    "read_body",
    "show_kind",
    "show_unavailable",
]
