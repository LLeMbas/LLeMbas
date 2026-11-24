"""The files of a linked computer, for the person's own hands.

Harness spec 3.11.0, `acp.md` *Device files* (capability `fs`, on the link
only): `_lembas/fs/list|stat|read|write|mkdir|move|copy|delete|search`. This
server cannot look at a device's disk; it **relays** what a person's file
manager asks -- an app through `/api/v2/devices/{id}/fs…`, the web's Files
page through `/files/device/{id}…` -- and both doors answer from here.

**The device decides.** Its roots, its protected paths, its own files, its
read-only mode and its trash are its rules, applied there; this module can
only narrow them further and never widens one. A refusal is the device's own
sentence with its reason as a word (`Refused.reason`), mapped to an HTTP
status a client can act on.

**Whose device.** `directories.owned_device`: one of the person's own device
tokens, linked, that they may work on now -- the door an agent chat on that
device stands behind. Nobody reaches another person's computer, an
administrator included.

**No model is in this.** These are the person's hands, not a tool: nothing
read here is put in front of a model, and nothing a model wrote decides a
path. What a device answers is still somebody else's data -- every listing
is cut to shape and length before it is passed on, and a file's bytes go to a
browser only as a download, or as the picture, sound or video their own
first bytes say they are (`api/_download.py`'s rule).

**Writing checks a base.** A save carries the hash the editor opened
(`base`), and the device refuses when the file holds something else by now
(`changed`, a 409 with the hash it has). No `base` is a refusal unless the
client says `overwrite`; `base: null` creates and never writes over.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import ApiToken, User
from lembas.services import file_kinds
from lembas.web.i18n import N_, t

CAPABILITY = "fs"

# What this server asks for at once and waits for (the spec's numbers are the
# device's own limits; `media.shown.limits` reads them where the vendored
# spec has them).
LIST_TIMEOUT = 15.0
CALL_TIMEOUT = 30.0
PART_TIMEOUT = 60.0
# A change waits its turn on the device, which makes one at a time for every
# link of it (acp.md, *One change at a time*): a second one is not late.
CHANGE_TIMEOUT = 300.0
# The most text the editor opens and saves, as for a file of the tree.
TEXT_MAX_BYTES = 1024 * 1024
# The most one uploaded or saved file may be through this server.
WRITE_MAX_BYTES = 256 * 1024 * 1024

MAX_ENTRIES = 2000
MAX_PATH = 4096
MAX_NAME = 255
MAX_SENTENCE = 500
MAX_MATCHES = 500
MAX_LINE = 400

NOT_LINKED = "device_not_linked"
CLI_TOO_OLD = "cli_too_old"
NO_ANSWER = "no_answer"
NOT_YOURS = "not_found"
BAD_ANSWER = "bad_answer"
REFUSED = "refused"

SENTENCES = {
    NOT_YOURS: N_("That computer is not one of yours, or is not linked to this account."),
    NOT_LINKED: N_("This computer is not linked right now."),
    CLI_TOO_OLD: N_(
        "This computer's LLeMbas CLI is too old to show its files here. Update it there."
    ),
    NO_ANSWER: N_("The computer did not answer."),
    BAD_ANSWER: N_("The computer answered with something this server cannot read."),
    "too_large_here": N_("That file is larger than can be sent through this server."),
}

# The device's reasons (acp.md) as HTTP statuses.
STATUS = {
    "off": 403, "outside": 403, "protected": 403, "own": 403, "read_only": 403,
    "runs_code": 403,
    "missing": 404, "exists": 409, "changed": 409, "no_trash": 409,
    "not_a_file": 400, "not_a_directory": 400, "invalid": 400, "too_large": 413,
    NOT_YOURS: 404, NOT_LINKED: 409, CLI_TOO_OLD: 409, NO_ANSWER: 504, BAD_ANSWER: 502,
}


class Refused(Exception):
    """Why not: an HTTP status, the reason as a word, a sentence, and what
    else the device said (`hash` and `mtime` of a changed file)."""

    def __init__(self, reason: str, message: str = "", data: dict[str, Any] | None = None):
        self.reason = reason or REFUSED
        self.status_code = STATUS.get(self.reason, 409)
        self.message = (message or t(SENTENCES.get(self.reason, "")) or self.reason)[:MAX_SENTENCE]
        self.data = dict(data or {})
        super().__init__(self.message)

    def body(self) -> dict[str, Any]:
        """The refusal as `/api/v2` answers it."""
        out: dict[str, Any] = {"detail": self.message, "reason": self.reason}
        if self.reason == "changed":
            # What the file holds now, as a save conflict of the tree says it
            # (`sha`; null when the file is gone, or the folder moved).
            out["conflict"] = True
            digest = self.data.get("hash")
            out["sha"] = digest if isinstance(digest, str) else None
            mtime = self.data.get("mtime")
            out["mtime"] = _int(mtime) if mtime is not None else None
        return out


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _path(value: Any) -> str:
    return value[:MAX_PATH] if isinstance(value, str) else ""


# --- Whose device, and its link ----------------------------------------------------------
def device(db: DBSession, user: User, token_id: str) -> ApiToken:
    from lembas.services import directories

    token = directories.owned_device(db, user, token_id)
    if token is None:
        raise Refused(NOT_YOURS)
    return token


def offered(token_id: str) -> bool:
    """Whether this device is linked now and shows its files."""
    from lembas.services import device_link

    return device_link.offers(token_id, CAPABILITY)


def places(db: DBSession, user: User) -> list[dict[str, str]]:
    """The person's linked computers that show their files, for the Files
    page: `{id, name}`."""
    from lembas.services import device_link, directories

    out = []
    for link in device_link.linked_for(user.id):
        if not link.can(CAPABILITY):
            continue
        if directories.owned_device(db, user, link.token_id) is None:
            continue
        out.append({"id": link.token_id, "name": device_link.short_name(link)[:MAX_NAME]})
    return sorted(out, key=lambda place: place["name"].lower())


def _owned(db: DBSession | None, user: User | str, token_id: str) -> str:
    """The device's token id, once more checked to be this person's -- on the
    caller's session, or (`db` None, `user` their id) on a short one of its
    own. A download that takes minutes asks this for every part and must not
    keep a database connection for all of it."""
    if db is not None and not isinstance(user, str):
        return device(db, user, token_id).id
    from lembas.db.session import session_scope

    with session_scope() as short:
        person = short.get(User, user if isinstance(user, str) else user.id)
        if person is None:
            raise Refused(NOT_YOURS)
        return device(short, person, token_id).id


async def call(
    db: DBSession | None, user: User | str, token_id: str, method: str, params: dict[str, Any],
    *, timeout: float = CALL_TIMEOUT,
) -> dict[str, Any]:
    """One `_lembas/fs/<method>` on the person's device, or `Refused`."""
    from lembas.services import device_link

    owned = _owned(db, user, token_id)
    link = device_link.link_for(owned)
    if link is None:
        raise Refused(NOT_LINKED)
    if not link.can(CAPABILITY):
        raise Refused(CLI_TOO_OLD)
    try:
        got = await link.request(f"_lembas/fs/{method}", params, timeout=timeout)
    except device_link.DeviceError as exc:
        raise Refused(exc.reason or REFUSED, str(exc), exc.data) from exc
    except (device_link.LinkClosed, TimeoutError) as exc:
        raise Refused(NO_ANSWER) from exc
    if not isinstance(got, dict):
        raise Refused(BAD_ANSWER)
    return got


# --- What the device lets these hands change ------------------------------------------------
# `loop.json` `files.runs_code` (spec 3.11.0), the part a path alone decides;
# read from the vendored spec where it has it. The shells' start-up files
# under the device user's home are the device's to name: this server does not
# know where that home is, and the device's refusal says it in words.
RUNS_CODE = {"segments": [".git"], "project_dir": ".agent",
             "project": ["config.yaml", "agents", "commands", "skills"]}


def _runs_code_rules() -> dict[str, Any]:
    from lembas.services import harness_spec

    files = harness_spec.loop().get("files")
    rules = files.get("runs_code") if isinstance(files, dict) else None
    return rules if isinstance(rules, dict) else RUNS_CODE


def runs_code(path: str) -> bool:
    """Whether a path is one of the files that run code, as far as its
    spelling says: git's own directory, and a project's agent configuration."""
    rules = _runs_code_rules()
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if any(p in (rules.get("segments") or []) for p in parts[:-1] + parts[-1:]):
        return True
    project = rules.get("project_dir") or ""
    if project and project in parts:
        at = parts.index(project)
        return at == len(parts) - 1 or parts[at + 1] in (rules.get("project") or [])
    return False


def limits(token_id: str) -> dict[str, bool]:
    """What the device said of itself: `writable` (its highest mode is not
    plan) and `runs_code` (it lets files that run code be changed,
    `limits.files_that_run_code`; off unless it says so)."""
    from lembas.services import device_link

    link = device_link.link_for(token_id)
    said = (link.hello.get("limits") or {}) if link is not None else {}
    return {
        "writable": link is not None and (link.max_mode or "") != "plan",
        "runs_code": isinstance(said, dict) and said.get("files_that_run_code") is True,
    }


def may_change(token_id: str, path: str) -> bool:
    """Whether a client should offer to change this path at all. The device
    decides for good; this only spares offering what it will refuse."""
    allowed = limits(token_id)
    return allowed["writable"] and (allowed["runs_code"] or not runs_code(path))


# --- What a device said, cut to shape ------------------------------------------------------
def entry(raw: dict[str, Any], token_id: str = "") -> dict[str, Any]:
    """One `Entry` as both doors say it. `runs_code`: one of the files that
    run code (by its path); `writable`: whether the device lets these hands
    change it, as far as it said -- both only where `token_id` names it."""
    from datetime import UTC, datetime

    path = _path(raw.get("path"))
    name = str(raw.get("name") or "") or path.rstrip("/").rsplit("/", 1)[-1] or path
    kind = raw.get("kind") if raw.get("kind") in ("file", "dir", "other") else "other"
    mtime = max(0, _int(raw.get("mtime")))
    closed = raw.get("outside") is True or raw.get("protected") is True
    out: dict[str, Any] = {
        "name": name[:MAX_NAME],
        "path": path,
        "kind": kind,
        "size": max(0, _int(raw.get("size"))),
        "mtime": mtime,
        # The same moment as the tree's entries say it; null when unknown.
        "updated_at": datetime.fromtimestamp(mtime / 1000, UTC).isoformat()
        if 0 < mtime < 4e12 else None,
        # What the viewer would make of it, by its name alone: for an icon.
        "file_kind": file_kinds.detect(name, None).kind if kind == "file" else None,
        "link": _path(raw.get("link")) if isinstance(raw.get("link"), str) else None,
        "outside": raw.get("outside") is True,
        "protected": raw.get("protected") is True,
        "runs_code": runs_code(path),
        "writable": bool(token_id) and not closed and may_change(token_id, path),
    }
    digest = raw.get("hash")
    if isinstance(digest, str) and digest:
        out["sha"] = digest[:80]
    return out


def listing(token_id: str, got: dict[str, Any]) -> dict[str, Any]:
    raw = got.get("entries")
    raw = raw if isinstance(raw, list) else []
    path = _path(got.get("path"))
    return {
        "device": token_id,
        "path": path,
        "parent": _path(got.get("parent")),
        # Whether something may be made or uploaded here.
        "writable": bool(path) and may_change(token_id, path),
        "entries": [
            entry(e, token_id) for e in raw[:MAX_ENTRIES] if isinstance(e, dict) and e.get("path")
        ],
        "truncated": got.get("truncated") is True or len(raw) > MAX_ENTRIES,
    }


def written(got: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": _path(got.get("path")),
        "size": max(0, _int(got.get("size"))),
        "mtime": max(0, _int(got.get("mtime"))),
        "sha": str(got.get("hash") or "")[:80],
    }


def found(got: dict[str, Any], *, content: bool) -> dict[str, Any]:
    """A search's answer: names, or (`content`) lines."""
    out: dict[str, Any] = {
        "root": _path(got.get("root")), "truncated": got.get("truncated") is True,
    }
    if content:
        raw = got.get("matches") if isinstance(got.get("matches"), list) else []
        out["matches"] = [
            {"path": _path(m.get("path")), "line": max(0, _int(m.get("line"))),
             "text": str(m.get("text") or "")[:MAX_LINE]}
            for m in raw[:MAX_MATCHES] if isinstance(m, dict) and m.get("path")
        ]
    else:
        raw = got.get("files") if isinstance(got.get("files"), list) else []
        out["files"] = [
            {"path": _path(f.get("path")),
             "kind": f.get("kind") if f.get("kind") in ("file", "dir", "other") else "file"}
            for f in raw[:MAX_MATCHES] if isinstance(f, dict) and f.get("path")
        ]
    return out


# --- The methods ----------------------------------------------------------------------------
async def list_dir(
    db: DBSession, user: User, token_id: str, path: str = "", *, hidden: bool = False
) -> dict[str, Any]:
    params: dict[str, Any] = {"hidden": True} if hidden else {}
    if path:
        params["path"] = path
    return listing(token_id, await call(db, user, token_id, "list", params, timeout=LIST_TIMEOUT))


async def stat(
    db: DBSession, user: User, token_id: str, path: str, *, with_hash: bool = False
) -> dict[str, Any]:
    params: dict[str, Any] = {"path": path}
    if with_hash:
        params["hash"] = True
    return entry(await call(db, user, token_id, "stat", params), token_id)


def _chunk() -> int:
    from lembas.services.media import shown as shown_media

    return shown_media.limits()["chunk_bytes"]


def _decoded(got: dict[str, Any]) -> bytes:
    try:
        return base64.b64decode(str(got.get("data") or ""), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise Refused(BAD_ANSWER) from exc


async def read_parts(
    db: DBSession | None, user: User | str, token_id: str, path: str, first: dict[str, Any]
) -> AsyncIterator[bytes]:
    """The file's bytes after its first part, read by offset until the device
    says `eof`. A file that changes size or time on the way ends the
    stream: half of one file and half of another is not a download."""
    size, mtime = _int(first.get("size")), _int(first.get("mtime"))
    piece = _decoded(first)
    yield piece
    offset, done = len(piece), bool(first.get("eof")) or not piece
    while not done:
        got = await call(
            db, user, token_id, "read", {"path": path, "offset": offset, "length": _chunk()},
            timeout=PART_TIMEOUT,
        )
        if _int(got.get("size")) != size or _int(got.get("mtime")) != mtime:
            raise Refused("changed", t(N_("The file changed on the computer while it was read.")))
        piece = _decoded(got)
        offset += len(piece)
        done = bool(got.get("eof")) or not piece
        if offset > size + _chunk():
            raise Refused(BAD_ANSWER)
        yield piece


async def read_first(db: DBSession, user: User, token_id: str, path: str) -> dict[str, Any]:
    """The first part of a file, with its `size`, `mtime` and -- when that is
    the whole file -- its `hash`."""
    return await call(
        db, user, token_id, "read", {"path": path, "offset": 0, "length": _chunk()},
        timeout=PART_TIMEOUT,
    )


WITHHELD = {
    "not_text": N_("This is not a text file, so it is not shown here."),
    "too_large": N_("This file is too large to show here."),
}


async def read_text(db: DBSession, user: User, token_id: str, path: str) -> dict[str, Any]:
    """A file for the editor, in the shape of the tree's `GET /files/content`:
    `{path, name, size, mtime, mime, kind, file_kind, writable, language, sha,
    text}` and, for a file the editor does not open, `withheld {reason,
    message}` (`not_text`, `too_large`) with `text` "". `sha` is what a save
    sends back as `base` (the device's own `sha256:…`)."""
    first = await read_first(db, user, token_id, path)
    at = _path(first.get("path")) or path[:MAX_PATH]
    name = at.rstrip("/").rsplit("/", 1)[-1][:MAX_NAME]
    size = max(0, _int(first.get("size")))
    head = _decoded(first)
    kind = file_kinds.detect(name, head[: file_kinds.HEAD_BYTES])
    out: dict[str, Any] = {
        "path": at,
        "name": name,
        "size": size,
        "mtime": max(0, _int(first.get("mtime"))),
        "mime": kind.mime,
        "kind": kind.kind,
        "file_kind": kind.kind,
        "writable": may_change(token_id, at),
        "language": kind.language,
        "sha": str(first.get("hash") or "")[:80],
        "text": "",
    }
    reason = ""
    if not kind.is_text or b"\x00" in head[:8192]:
        reason = "not_text"
    elif size > TEXT_MAX_BYTES or not first.get("eof"):
        reason = "too_large"
    if reason:
        out["withheld"] = {"reason": reason, "message": t(WITHHELD[reason])}
        return out
    out["text"] = head.decode("utf-8", errors="replace")
    if not out["sha"]:
        # A device that did not hash the whole file it sent: asked for it.
        out["sha"] = (await stat(db, user, token_id, path, with_hash=True)).get("sha", "")
    return out


_UNSET: Any = object()


async def write(
    db: DBSession, user: User, token_id: str, path: str, data: bytes,
    *, base: Any = _UNSET, overwrite: bool = False,
) -> dict[str, Any]:
    """Write a file, in parts where it is large. `base`: the hash the client
    last saw, or None for "it must not exist"; left out, the write is refused
    by the device unless `overwrite`."""
    if len(data) > WRITE_MAX_BYTES:
        raise Refused("too_large", t(SENTENCES["too_large_here"]))
    chunk = _chunk()
    head: dict[str, Any] = {"path": path}
    if base is not _UNSET:
        head["base"] = base
    if overwrite:
        head["overwrite"] = True
    if len(data) <= chunk:
        head["data"] = base64.b64encode(data).decode("ascii")
        return written(await call(db, user, token_id, "write", head, timeout=CHANGE_TIMEOUT))
    upload = ""
    got: dict[str, Any] = {}
    for offset in range(0, len(data), chunk):
        piece = base64.b64encode(data[offset : offset + chunk]).decode("ascii")
        last = offset + chunk >= len(data)
        params = {**head, "data": piece} if not offset else {"upload": upload, "data": piece}
        if not last:
            params["more"] = True
        got = await call(db, user, token_id, "write", params, timeout=CHANGE_TIMEOUT)
        if not last:
            upload = str(got.get("upload") or upload)
            if not upload:
                raise Refused(BAD_ANSWER)
    return written(got)


async def mkdir(db: DBSession, user: User, token_id: str, path: str) -> dict[str, Any]:
    got = await call(db, user, token_id, "mkdir", {"path": path}, timeout=CHANGE_TIMEOUT)
    return {"path": _path(got.get("path")) or path[:MAX_PATH]}


async def transfer(
    db: DBSession, user: User, token_id: str, method: str, source: str, target: str,
    *, overwrite: bool = False,
) -> dict[str, Any]:
    """`move` or `copy`."""
    params: dict[str, Any] = {"from": source, "to": target}
    if overwrite:
        params["overwrite"] = True
    got = await call(db, user, token_id, method, params, timeout=CHANGE_TIMEOUT)
    return {"path": _path(got.get("path")) or target[:MAX_PATH]}


async def delete(
    db: DBSession, user: User, token_id: str, path: str, *, permanent: bool = False
) -> dict[str, Any]:
    """To the device's trash; where it has none the device refuses
    (`no_trash`) until the person, asked, says `permanent`."""
    params: dict[str, Any] = {"path": path}
    if permanent:
        params["permanent"] = True
    got = await call(db, user, token_id, "delete", params, timeout=CHANGE_TIMEOUT)
    return {"deleted": got.get("deleted") is not False, "trash": got.get("trash") is True}


async def search(
    db: DBSession, user: User, token_id: str, path: str, query: str,
    *, content: bool = False, regex: bool = False, limit: int = 0,
) -> dict[str, Any]:
    params: dict[str, Any] = {"path": path, "query": query[:1000]}
    if content:
        params["content"] = True
    if regex:
        params["regex"] = True
    if limit > 0:
        params["limit"] = min(limit, MAX_MATCHES)
    return found(await call(db, user, token_id, "search", params), content=content)


MAX_BATCH = 500


async def batch(
    db: DBSession, user: User, token_id: str, op: str, paths: list[str], to: str = "",
    *, overwrite: bool = False, permanent: bool = False,
) -> dict[str, Any]:
    """`move` or `copy` several paths into the directory `to`, or `delete`
    them -- one after another, **not all-or-nothing**, in the shape of the
    tree's `POST /files/batch`: `{op, to, atomic: false, results: [{path, ok,
    to?, error?, reason?}], done, failed}`. Each failure is the device's own
    sentence and its reason as a word (`no_trash`: ask, then send that path
    again with `permanent`)."""
    results = []
    for path in paths[:MAX_BATCH]:
        row: dict[str, Any] = {"path": path[:MAX_PATH]}
        try:
            if op == "delete":
                done = await delete(db, user, token_id, path, permanent=permanent)
                row.update(ok=True, trash=done["trash"])
            else:
                name = path.rstrip("/").rsplit("/", 1)[-1]
                done = await transfer(
                    db, user, token_id, op, path, f"{to.rstrip('/')}/{name}", overwrite=overwrite
                )
                row.update(ok=True, to=done["path"])
        except Refused as refused:
            if refused.reason in (NOT_YOURS, NOT_LINKED, CLI_TOO_OLD, NO_ANSWER):
                raise  # nothing after this one would fare better
            row.update(ok=False, error=refused.message, reason=refused.reason)
        results.append(row)
    done_count = sum(1 for r in results if r["ok"])
    return {"op": op, "to": to[:MAX_PATH] if op != "delete" else None, "atomic": False,
            "results": results, "done": done_count, "failed": len(results) - done_count}


__all__ = [
    "CAPABILITY",
    "Refused",
    "TEXT_MAX_BYTES",
    "WRITE_MAX_BYTES",
    "batch",
    "call",
    "delete",
    "device",
    "entry",
    "found",
    "limits",
    "list_dir",
    "listing",
    "may_change",
    "mkdir",
    "offered",
    "places",
    "read_first",
    "read_parts",
    "read_text",
    "runs_code",
    "search",
    "stat",
    "transfer",
    "write",
    "written",
]
