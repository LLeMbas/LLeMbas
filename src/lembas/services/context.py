"""Context hygiene: what a tool result becomes when it is inserted (spec 3.0.0).

The harness spec's `context.md`, with the texts and numbers in `loop.json`
`context` and the cases in the conformance area `context` -- the same rules the
LLeMbas CLI keeps in `src/session/results.ts`, which this follows line for line.

Every result gets an id (`#0001`); a read says its lines and its content's
hash; an unchanged re-read inside an earlier read, and a listing or search
repeated with the same result, are stubs pointing at the full result; large
output keeps its head and its tail; an edit answers with its diff; `recall`
shows any stored result again; compaction adds a table of the reads.

**The one rule: every measure acts when a result is inserted, and nothing
already sent is ever rewritten** -- changing earlier context throws the prompt
cache away from that point. A stub only ever points at a result still in the
conversation (`present`, the ids at the head of the tool turns of the request
being built). Here that matters more than in the CLI: a chat does not replay
an earlier reply's tool results into the next request (Working notes,
*Requests to models*), so a read in one reply is never a stub for a read in the
next -- the earlier one is not in front of the model any more.

Results are stored per chat in `tool_results` (`DbStore`) and go with the chat.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from sqlalchemy import func, select, update

from lembas.db.models import (
    RESULT_EDIT,
    RESULT_OUTPUT,
    RESULT_READ,
    RESULT_RECALL,
    ToolResult,
)
from lembas.services import harness_spec


def spec() -> dict[str, Any]:
    """`loop.json` `context`: the texts and the numbers."""
    return harness_spec.loop()["context"]


def sha256(data: str | bytes) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def _fill(text: str, values: dict[str, Any]) -> str:
    """`{{name}}` filled; loop.json's texts have no bullets to drop."""
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


def _short(sha: str) -> str:
    return sha[: int(spec()["sha_digits"])]


def stable_json(value: Any) -> str:
    """JSON with every object's keys sorted: how two calls' arguments are compared.

    The CLI's `JSON.stringify` writes no spaces and keeps non-ASCII as it is;
    so does this, so the stored text is the same on both sides.
    """
    return json.dumps(value if value is not None else {}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


# --- The unified diff ---------------------------------------------------------------
_NO_NEWLINE = "\\ No newline at end of file"


def hunks(before: str, after: str, context: int = 3) -> str:
    """The unified diff of a change, hunks only (the path is in the bracket),
    three lines of context -- jsdiff's `structuredPatch` as the CLI uses it,
    including the marker on a last line that has no newline."""
    old = before.split("\n")
    new = after.split("\n")
    # A final newline ends the last line; it is not an empty line of its own.
    old_eol = before.endswith("\n") or before == ""
    new_eol = after.endswith("\n") or after == ""
    if old and old[-1] == "" and before.endswith("\n"):
        old.pop()
    if new and new[-1] == "" and after.endswith("\n"):
        new.pop()
    if before == "":
        old = []
    if after == "":
        new = []
    # A last line without a newline differs from the same line with one.
    old_cmp = [*old[:-1], old[-1] + ("" if old_eol else "\x00")] if old else []
    new_cmp = [*new[:-1], new[-1] + ("" if new_eol else "\x00")] if new else []
    matcher = difflib.SequenceMatcher(None, old_cmp, new_cmp, autojunk=False)
    out: list[str] = []
    for group in matcher.get_grouped_opcodes(context):
        first, last = group[0], group[-1]
        i1, i2, j1, j2 = first[1], last[2], first[3], last[4]
        old_len, new_len = i2 - i1, j2 - j1
        old_start = i1 + 1 if old_len else i1
        new_start = j1 + 1 if new_len else j1
        lines = [f"@@ -{old_start},{old_len} +{new_start},{new_len} @@"]
        for tag, a1, a2, b1, b2 in group:
            if tag == "equal":
                for k in range(a1, a2):
                    lines.append(" " + old[k])
                    if k == len(old) - 1 and not old_eol:
                        lines.append(_NO_NEWLINE)
                continue
            if tag in ("replace", "delete"):
                for k in range(a1, a2):
                    lines.append("-" + old[k])
                    if k == len(old) - 1 and not old_eol:
                        lines.append(_NO_NEWLINE)
            if tag in ("replace", "insert"):
                for k in range(b1, b2):
                    lines.append("+" + new[k])
                    if k == len(new) - 1 and not new_eol:
                        lines.append(_NO_NEWLINE)
        out.append("\n".join(lines))
    return "\n".join(out)


def patch(path: str, before: str, after: str) -> str:
    """The whole unified diff with its file headers, for the transcript's edit block."""
    body = hunks(before, after)
    if not body:
        return ""
    return f"--- a{path}\n+++ b{path}\n{body}" if path.startswith("/") else (
        f"--- a/{path}\n+++ b/{path}\n{body}"
    )


def line_count(text: str) -> int:
    """How many lines a new file has, as the model wrote it."""
    if not text:
        return 0
    n = len(text.split("\n"))
    return n - 1 if text.endswith("\n") else n


# --- The cap ------------------------------------------------------------------------
def cap_output(text: str, rid: str, cap: dict[str, int] | None = None) -> str:
    """A result over the cap's lines or characters: its head and its tail, with
    the elided line between. The first line alone longer than the head is cut
    and marked (loop.json `cap`)."""
    cap = cap or spec()["cap"]
    lines = text.split("\n")
    if len(lines) <= cap["max_lines"] and len(text) <= cap["max_chars"]:
        return text
    head: list[str] = []
    chars = 0
    for line in lines:
        if len(head) >= cap["head_lines"]:
            break
        add = (1 if head else 0) + len(line)
        if chars + add > cap["head_chars"]:
            break
        head.append(line)
        chars += add
    used = len(head)
    if not head and lines:
        head.append(f"{lines[0][: cap['head_chars']]} […cut]")
        used = 1
    tail: list[str] = []
    chars = 0
    for i in range(len(lines) - 1, used - 1, -1):
        if len(tail) >= cap["tail_lines"]:
            break
        line = lines[i]
        add = (1 if tail else 0) + len(line)
        if chars + add > cap["tail_chars"]:
            break
        tail.insert(0, line)
        chars += add
    elided = len(lines) - used - len(tail)
    return "\n".join([*head, _fill(spec()["elided"], {"n": elided, "id": rid}), *tail])


# --- What is stored -------------------------------------------------------------------
@dataclass
class Stored:
    """One result as the chat keeps it: enough to answer `recall` and to decide a
    later stub. `text` is the full result (for a stub, what it stands for)."""

    id: str
    kind: str
    full: bool = True
    tool: str = ""
    path: str = ""
    key: str = ""
    sha: str = ""
    line_from: int = 0
    line_to: int = 0
    line_of: int = 0
    args: str = ""
    text_sha: str = ""
    text: str = ""


class Store(Protocol):
    def count(self) -> int: ...
    def save(self, row: Stored) -> None: ...
    def get(self, rid: str) -> Stored | None: ...
    def full_among(self, rids: Iterable[str]) -> list[Stored]: ...


@dataclass
class MemoryStore:
    """Results kept in memory: the conformance runner's, and a test's."""

    rows: list[Stored] = field(default_factory=list)

    def count(self) -> int:
        return len(self.rows)

    def save(self, row: Stored) -> None:
        self.rows.append(row)

    def get(self, rid: str) -> Stored | None:
        return next((r for r in self.rows if r.id == rid), None)

    def full_among(self, rids: Iterable[str]) -> list[Stored]:
        wanted = set(rids)
        return [r for r in self.rows if r.full and r.id in wanted]


def _row(row: ToolResult) -> Stored:
    return Stored(
        id=row.rid,
        kind=row.kind,
        full=row.full,
        tool=row.tool,
        path=row.path,
        key=row.key,
        sha=row.sha,
        line_from=row.line_from,
        line_to=row.line_to,
        line_of=row.line_of,
        args=row.args,
        text_sha=row.text_sha,
        text=row.text,
    )


@dataclass
class DbStore:
    """One chat's results in `tool_results`, each operation in its own session:
    the generation loop that inserts runs outside any session."""

    chat_id: str
    message_id: str | None = None
    _count: int | None = None

    def count(self) -> int:
        if self._count is None:
            from lembas.db.session import session_scope

            with session_scope() as db:
                self._count = int(
                    db.scalar(
                        select(func.count())
                        .select_from(ToolResult)
                        .where(ToolResult.chat_id == self.chat_id)
                    )
                    or 0
                )
        return self._count

    def save(self, row: Stored) -> None:
        from lembas.db.session import session_scope

        with session_scope() as db:
            db.add(
                ToolResult(
                    chat_id=self.chat_id,
                    message_id=self.message_id,
                    rid=row.id,
                    kind=row.kind,
                    full=row.full,
                    tool=row.tool[:100],
                    path=row.path,
                    key=row.key,
                    sha=row.sha,
                    line_from=row.line_from,
                    line_to=row.line_to,
                    line_of=row.line_of,
                    args=row.args,
                    text_sha=row.text_sha,
                    text=row.text,
                )
            )
        self._count = self.count() + 1

    def get(self, rid: str) -> Stored | None:
        from lembas.db.session import session_scope

        with session_scope() as db:
            row = db.scalar(
                select(ToolResult).where(
                    ToolResult.chat_id == self.chat_id, ToolResult.rid == rid
                )
            )
            return _row(row) if row is not None else None

    def full_among(self, rids: Iterable[str]) -> list[Stored]:
        wanted = list(set(rids))
        if not wanted:
            return []
        from lembas.db.session import session_scope

        with session_scope() as db:
            rows = db.scalars(
                select(ToolResult)
                .where(
                    ToolResult.chat_id == self.chat_id,
                    ToolResult.full.is_(True),
                    ToolResult.rid.in_(wanted),
                )
                .order_by(ToolResult.created_at, ToolResult.rid)
            )
            return [_row(row) for row in rows]


# --- What a tool hands over for its insertion -----------------------------------------
# `ToolOutcome.context`, filled in by the tool (the CLI's `ToolResult.context`):
#   {"kind": "read", "path", "key", "sha", "from", "to", "of"}   -- the body is the content
#   {"kind": "edit", "files": [{"path", "key", "sha", "hunks"} | {..., "created": n}
#                              | {"path", "deleted": True}], "note"?}
#   {"kind": "recall", "target", "offset"?, "limit"?}
# Anything else is an output, its arguments the call's.


class ResultContext:
    """One chat's results: the ids it gives, what it keeps, what each insertion says."""

    def __init__(
        self,
        store: Store,
        present: Callable[[], set[str]] = set,
        cap: dict[str, int] | None = None,
    ) -> None:
        self.store = store
        self.present = present
        self.cap = cap

    def next_id(self) -> str:
        """`#` and lowercase hex, at least `ids.digits` digits, never reused in the chat."""
        digits = int(spec()["ids"]["digits"])
        return f"#{self.store.count() + 1:0{digits}x}"

    def insert(self, entry: dict[str, Any], rid: str | None = None) -> str:
        """The text the model is given for one result, which is stored in full."""
        rid = rid or self.next_id()
        kind = entry.get("kind")
        if kind == RESULT_READ:
            return self._read(rid, entry)
        if kind == RESULT_EDIT:
            return self._edit(rid, entry)
        if kind == RESULT_RECALL:
            return self._recall(rid, entry)
        return self._output(rid, entry)

    # The four kinds -------------------------------------------------------------
    def _table(self) -> list[Stored]:
        """The dedupe table: full results still in the conversation, oldest first."""
        return self.store.full_among(self.present())

    def _read(self, rid: str, e: dict[str, Any]) -> str:
        texts = spec()
        frm, to, of = int(e["from"]), int(e["to"]), int(e["of"])
        if of == 0:
            head = _fill(texts["read_header_empty"], {"id": rid, "path": e["path"],
                                                      "sha": _short(e["sha"])})
        else:
            head = _fill(
                texts["read_header"],
                {"id": rid, "path": e["path"], "from": frm, "to": to, "of": of,
                 "sha": _short(e["sha"])},
            )
        base = Stored(
            id=rid, kind=RESULT_READ, tool=e.get("tool") or "read", path=e["path"],
            key=e.get("key") or e["path"], sha=e["sha"], line_from=frm, line_to=to,
            line_of=of, text=e["body"],
        )
        earlier = None
        if of > 0 and "read" in texts["dedupe"]["by_hash"]:
            matches = [
                r for r in self._table()
                if r.kind == RESULT_READ and r.key == base.key and r.sha == base.sha
                and r.line_from <= frm and to <= r.line_to
            ]
            earlier = matches[-1] if matches else None
        if earlier is not None:
            self.store.save(replace(base, full=False))
            stub = _fill(texts["unchanged"], {"earlier": earlier.id, "from": earlier.line_from,
                                              "to": earlier.line_to})
            return f"{head} {stub}"
        self.store.save(base)
        return f"{head}\n{e['body']}"

    def _edit(self, rid: str, e: dict[str, Any]) -> str:
        texts = spec()

        def one(f: dict[str, Any]) -> tuple[str, str, bool]:
            if f.get("deleted"):
                return f"{f['path']} deleted", "", False
            bracket = f"{f['path']} sha {_short(f['sha'])}"
            if "created" in f:
                return bracket, _fill(texts["created"], {"n": f["created"]}), True
            return bracket, f.get("hunks") or "", False

        files = list(e.get("files") or [])
        if len(files) == 1:
            bracket, body, inline = one(files[0])
            text = f"[{rid} {bracket}]" + ((" " if inline else "\n") + body if body else "")
        else:
            parts = [f"[{rid}]"]
            for f in files:
                bracket, body, inline = one(f)
                parts.append(f"[{bracket}]" + ((" " if inline else "\n") + body if body else ""))
            text = "\n".join(parts)
        if e.get("note"):
            text += str(e["note"])
        stored = re.sub(r"^[ \n]", "", text[text.index("]") + 1 :])
        last = files[-1] if files else {}
        self.store.save(
            Stored(id=rid, kind=RESULT_EDIT, tool=e.get("tool") or "edit",
                   path=str(last.get("path") or ""), key=str(last.get("key") or ""),
                   sha="" if last.get("deleted") else str(last.get("sha") or ""), text=stored)
        )
        newline = text.find("\n")
        if newline < 0:
            return text
        return f"{text[:newline]}\n{cap_output(text[newline + 1 :], rid, self.cap)}"

    def _output(self, rid: str, e: dict[str, Any]) -> str:
        texts = spec()
        tool = str(e.get("tool") or "")
        args = stable_json(e.get("args") or {})
        body = str(e.get("text") or "")
        text_sha = sha256(body)
        base = Stored(id=rid, kind=RESULT_OUTPUT, tool=tool, args=args, text_sha=text_sha,
                      text=body)
        earlier = None
        if not e.get("error") and tool in texts["dedupe"]["by_result"]:
            same = [r for r in self._table()
                    if r.kind == RESULT_OUTPUT and r.tool == tool and r.args == args]
            earlier = same[-1] if same else None
        header = _fill(texts["header"], {"id": rid})
        if earlier is not None and earlier.text_sha == text_sha:
            self.store.save(replace(base, full=False))
            return f"{header} {_fill(texts['same_result'], {'earlier': earlier.id})}"
        self.store.save(base)
        return f"{header}\n{cap_output(body, rid, self.cap)}"

    def _recall(self, rid: str, e: dict[str, Any]) -> str:
        texts = spec()
        raw = str(e.get("target") or "").strip().lower()
        target = raw if raw.startswith("#") else f"#{raw}"
        found = self.store.get(target)
        if found is None:
            text = _fill(texts["no_result"], {"target": target})
            self.store.save(Stored(id=rid, kind=RESULT_RECALL, tool="recall", text=text))
            return f"{_fill(texts['header'], {'id': rid})} {text}"
        lines = found.text.split("\n")
        frm = max(1, int(e.get("offset") or 1))
        to = min(len(lines), frm + int(e.get("limit") or 2000) - 1)
        shown = "\n".join(lines[frm - 1 : to])
        self.store.save(Stored(id=rid, kind=RESULT_RECALL, tool="recall", text=shown))
        header = _fill(texts["recall_header"],
                       {"id": rid, "target": target, "from": frm, "to": to, "of": len(lines)})
        return f"{header}\n{shown}"

    def compaction_table(self, reads: list[Stored] | None = None) -> str:
        """The table compaction adds after its summary: the newest full read of
        each path, newest last, at most `compaction.rows`; "" with none.

        `reads` are the full reads the summary covers: those made since the
        previous compaction in turns still part of the history being
        summarised. They default to the dedupe table, which in a session that
        resends every result (the CLI) holds exactly those; a chat, which does
        not, passes what it stored for the turns being summarised
        (`reads_since`, from `compaction.apply`)."""
        texts = spec()["compaction"]
        rows = self._table() if reads is None else reads
        newest: dict[str, Stored] = {}
        for r in rows:
            if r.kind == RESULT_READ and r.full and r.line_of > 0:
                newest.pop(r.key, None)
                newest[r.key] = r
        chosen = list(newest.values())[-int(texts["rows"]) :]
        if not chosen:
            return ""
        return "\n".join(
            [
                texts["heading"],
                "",
                texts["line"],
                "",
                "| Path | Lines | sha | Result |",
                "|---|---|---|---|",
                *(
                    f"| {r.path} | {r.line_from}-{r.line_to} of {r.line_of} | {_short(r.sha)} "
                    f"| {r.id} |"
                    for r in chosen
                ),
            ]
        )


_PRESENT = re.compile(r"^\[(#[0-9a-f]+)[\] ]")


def present_ids(messages: Iterable[dict[str, Any]]) -> set[str]:
    """The ids at the head of the tool results in a conversation: what a stub may name."""
    out: set[str] = set()
    for message in messages:
        if message.get("role") != "tool" or not isinstance(message.get("content"), str):
            continue
        hit = _PRESENT.match(message["content"])
        if hit:
            out.add(hit.group(1))
    return out


# --- A chat's stored results, outside an insertion ------------------------------------
def last_seen(db, chat_id: str, key: str) -> str | None:
    """The content hash this chat last saw of `key` -- by reading it or by
    writing it -- or None when it never has. What `write` and `edit` check
    before changing an existing file."""
    row = db.scalar(
        select(ToolResult.sha)
        .where(
            ToolResult.chat_id == chat_id,
            ToolResult.key == key,
            ToolResult.kind.in_((RESULT_READ, RESULT_EDIT)),
        )
        .order_by(ToolResult.created_at.desc(), ToolResult.rid.desc())
        .limit(1)
    )
    return row


def reads_since(db, chat_id: str, since=None, message_ids=None) -> list[Stored]:
    """A chat's full reads, oldest first, after `since` (a datetime) when given,
    and with `message_ids` only those made in one of those replies: what a
    compaction's table is drawn from (`compaction.apply`)."""
    query = select(ToolResult).where(
        ToolResult.chat_id == chat_id,
        ToolResult.kind == RESULT_READ,
        ToolResult.full.is_(True),
    )
    if since is not None:
        query = query.where(ToolResult.created_at > since)
    if message_ids is not None:
        if not message_ids:
            return []
        query = query.where(ToolResult.message_id.in_(list(message_ids)))
    return [_row(r) for r in db.scalars(query.order_by(ToolResult.created_at, ToolResult.rid))]


def detach(db, chat_id: str, message_id: str) -> None:
    """Results given in a reply that is produced again no longer belong to it:
    `recall` still shows them, but a compaction no longer counts them among
    the reads of the turns it covers. Caller commits."""
    db.execute(
        update(ToolResult)
        .where(ToolResult.chat_id == chat_id, ToolResult.message_id == message_id)
        .values(message_id=None)
    )


__all__ = [
    "DbStore",
    "MemoryStore",
    "ResultContext",
    "Stored",
    "cap_output",
    "detach",
    "hunks",
    "last_seen",
    "line_count",
    "patch",
    "present_ids",
    "reads_since",
    "sha256",
    "spec",
    "stable_json",
]
