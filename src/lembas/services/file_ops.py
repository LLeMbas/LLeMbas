"""What the explorer and the apps do to several files at once, and the limits
on what comes in and goes out: uploads, batches, a download as a zip.

One implementation for the page's door (`api/tree.py`) and the apps'
(`api/v2/files.py`), above `services/fs.py` -- so every path still goes
through the view, one at a time.

**A batch is not all-or-nothing.** Each path is done and committed by itself,
and the answer says for each whether it worked and, when not, why in the
tree's own words. Ten files moved and one refused is ten files moved.

**An upload has a ceiling** (`tree_upload_mb`, an administrator's), per file.
The sizes are checked before anything is written, so a refused upload leaves
nothing behind.

**A zip is written as it is read**: no archive is built in memory or on disk.
What it would hold is added up first (`tree_zip_mb`), so a refusal comes
before the first byte and never as a download that stops half way.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import KIND_FILE
from lembas.security.access import BadPath, normalise
from lembas.services import blobs, fs, settings_store

MB = 1024 * 1024
BATCH_MAX = 500
OPS = ("move", "copy", "delete")


def upload_limit(db: DBSession) -> int:
    return int(settings_store.extraction(db)["tree_upload_mb"]) * MB


def zip_limit(db: DBSession) -> int:
    return int(settings_store.extraction(db)["tree_zip_mb"]) * MB


class TooLarge(Exception):
    """A file (or a zip) over the ceiling: `name`, and the ceiling in MB."""

    def __init__(self, name: str, limit: int) -> None:
        super().__init__(name)
        self.name = name
        self.limit_mb = max(limit // MB, 1)


# --- Uploads -------------------------------------------------------------------------
def _size(upload: Any) -> int | None:
    size = getattr(upload, "size", None)
    if size is not None:
        return int(size)
    try:
        here = upload.file.tell()
        upload.file.seek(0, 2)
        size = upload.file.tell()
        upload.file.seek(here)
        return int(size)
    except (AttributeError, OSError, ValueError):
        return None


def store_uploads(v: fs.View, where: str, uploads: Iterable[Any]) -> int:
    """Write each upload into the directory `where`; returns how many.

    Raises `TooLarge` before writing anything when one is over the ceiling,
    and `fs.FsError` as `fs.write` does. Each file is read a megabyte at a
    time into the blob store, which keeps at most one part in hand."""
    limit = upload_limit(v.db)
    named = []
    for upload in uploads:
        if not getattr(upload, "filename", ""):
            continue
        name = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
        size = _size(upload)
        if size is not None and size > limit:
            raise TooLarge(name, limit)
        named.append((name, upload))

    def chunks(name: str, source: Any) -> Iterator[bytes]:
        total = 0
        while True:
            part = source.file.read(MB)
            if not part:
                return
            total += len(part)
            if total > limit:  # a size that was not known, or not true
                raise TooLarge(name, limit)
            yield part

    for name, upload in named:
        fs.write(v, f"{where.rstrip('/')}/{name}", chunks(name, upload))
    return len(named)


# --- Batches -------------------------------------------------------------------------
def batch(v: fs.View, op: str, paths: Iterable[str], to: str = "") -> list[dict[str, Any]]:
    """Move, copy or delete each of `paths`, one at a time, each committed by
    itself. One answer per path, in order: `{path, ok, to?, error?}` -- `to`
    is where a moved or copied thing is now (a copy's name may have been
    numbered)."""
    if op not in OPS:
        raise ValueError(op)
    out: list[dict[str, Any]] = []
    for raw in paths:
        item: dict[str, Any] = {"path": str(raw), "ok": False}
        try:
            source = normalise(str(raw) or "/")
            item["path"] = source
            if op == "delete":
                fs.rm(v, source)
            elif op == "move":
                target = f"{to.rstrip('/')}/{source.rsplit('/', 1)[-1]}"
                item["to"] = fs.mv(v, source, target).path
            else:
                item["to"] = fs.cp(v, source, to).path
            v.db.commit()
            item["ok"] = True
        except BadPath:
            v.db.rollback()
            item["error"] = fs.no_such(str(raw)).message
        except fs.FsError as refused:
            v.db.rollback()
            v.refresh()
            item.pop("to", None)
            item["error"] = refused.message
        out.append(item)
    return out


def batch_answer(op: str, to: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """What `/api/v2/files/batch` answers: every path's own result, in order,
    and the two counts. `atomic` is always false -- it is there to be read."""
    done = sum(1 for r in results if r["ok"])
    return {
        "op": op,
        "to": to or None,
        "atomic": False,
        "results": results,
        "done": done,
        "failed": len(results) - done,
    }


# --- A zip, written as it is read ---------------------------------------------------
@dataclass(frozen=True)
class ZipEntry:
    name: str  # inside the archive; a directory's ends with "/"
    digest: str
    size: int
    changed: datetime | None


def entry_name(name: str) -> str:
    """A name as it goes into an archive. The tree allows a backslash in a
    name; in an archive it is a path separator to the programs that unpack
    on Windows, and `..\\..\\x` would be written outside the folder chosen.
    So are control characters nobody typed on purpose. Each becomes `_`."""
    return "".join("_" if ch == "\\" or ord(ch) < 32 or ord(ch) == 127 else ch for ch in name)


def zip_plan(v: fs.View, paths: Iterable[str]) -> list[ZipEntry]:
    """What a zip of `paths` holds: each file, and each directory with what the
    view can read below it. Raises `fs.FsError` for a path the view cannot
    read and `TooLarge` when it adds up to more than the ceiling."""
    limit = zip_limit(v.db)
    entries: list[ZipEntry] = []
    taken: set[str] = set()
    files: set[str] = set()
    total = 0

    def free(name: str, among: set[str] | None = None) -> str:
        among = taken if among is None else among
        stem, dot, ext = name.rpartition(".")
        if not dot or not stem or "/" in ext:
            stem, ext = name, ""
        candidate, n = name, 1
        while candidate in among:
            n += 1
            candidate = f"{stem} ({n})" + (f".{ext}" if ext else "")
        among.add(candidate)
        return candidate

    for raw in paths:
        path = v.path(raw)
        node = fs.stat(v, path)
        if node is None:
            raise fs.denied(path)
        if node.kind == KIND_FILE:
            found = [node]
            top = free(entry_name(node.name))
        else:
            found = fs.below(v, path)
            top = free(entry_name(node.name))
            entries.append(ZipEntry(f"{top}/", "", 0, node.updated_at))
        for each in found:
            inside = top + entry_name(each.path[len(path):])
            if each.kind != KIND_FILE:
                entries.append(ZipEntry(f"{inside}/", "", 0, each.updated_at))
                continue
            total += int(each.size or 0)
            if total > limit:
                raise TooLarge(node.name, limit)
            # Two names that differ only by what was replaced are two files.
            inside = free(inside, files)
            entries.append(ZipEntry(inside, each.blob_hash or "", int(each.size or 0),
                                    each.updated_at))
    return entries


class _Sink:
    """Where `zipfile` writes: it cannot seek, so sizes follow each file."""

    def __init__(self) -> None:
        self.held = bytearray()

    def write(self, data: bytes) -> int:
        self.held += data
        return len(data)

    def flush(self) -> None:
        return None

    def take(self) -> bytes:
        out = bytes(self.held)
        self.held.clear()
        return out


def zip_stream(entries: list[ZipEntry]) -> Iterator[bytes]:
    """The archive, piece by piece, in a session of its own (it outlives the
    request). At most one part of one file is in hand at a time."""
    from lembas.db.session import session_scope

    sink = _Sink()
    with session_scope() as db, zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            when = (entry.changed or datetime.now(UTC)).timetuple()[:6]
            info = zipfile.ZipInfo(entry.name, date_time=(max(when[0], 1980), *when[1:]))
            if entry.name.endswith("/"):
                archive.writestr(info, b"")
                continue
            info.compress_type = zipfile.ZIP_DEFLATED
            with archive.open(info, "w", force_zip64=True) as dest:
                if entry.digest:
                    for part in blobs.iter_parts(db, entry.digest):
                        dest.write(part)
                        if sink.held:
                            yield sink.take()
            if sink.held:
                yield sink.take()
    if sink.held:
        yield sink.take()


def zip_name(paths: list[str]) -> str:
    """What the download is called: the one thing chosen, or `files`."""
    if len(paths) == 1:
        base = paths[0].rstrip("/").rsplit("/", 1)[-1]
        if base:
            return f"{base}.zip"
    return "files.zip"


__all__ = [
    "BATCH_MAX",
    "OPS",
    "TooLarge",
    "ZipEntry",
    "batch",
    "batch_answer",
    "store_uploads",
    "upload_limit",
    "zip_limit",
    "zip_name",
    "zip_plan",
    "zip_stream",
]
