"""Bringing a 1.x library across: `lembas.db.old` into files under `~/Imported/`.

2.0 starts on a new database; the update renames the 1.x one to
`lembas.db.old` and carries nothing over (`db/session.py:set_aside_1x`). This
is the way across for what people kept in the 1.x library, and it is an
administrator's action -- Admin → Import, or `lembas-server import old` -- never
something the update runs.

**What it writes.** Plain files in the chosen person's home, owned by that
person, under `/home/<person>/Imported/` -- private to them, like everything
else in a home, until they share or move it:

- `Notes/<title>.md` -- one per note, the title as its heading;
- `Reports/<title>.md` -- one per report, its summary under the heading;
- `Skills/<name>/SKILL.md` -- the skill as it is now, its 1.x revisions as the
  file's earlier versions, oldest first;
- `Knowledge/<base>/…` -- each document's original upload from `uploads/library/`
  when it is still there, otherwise its extracted text as `<title>.md`. The
  index reads them from scratch; no chunk or embedding comes across.

When the content of more than one 1.x account is brought across, each gets its
own directory, `~/Imported/<name>/Notes/` and so on, so whose was whose stays
visible. Imported skills are files, not live skills: a skill counts where a
chat looks for skills (`/skills/`, `~/skills/`), and moving one there is the
person's decision.

**Memories** are not files; they come across as memory records only when asked
(`memories=True`), and only into the account with the same email address --
someone's memories are about them, and nobody else's account should hold them.

**Read-only, and repeatable.** The old file is never opened for writing: it is
copied with SQLite's online backup into a temporary file (which also folds in a
WAL, should one be beside it), and read there. Re-running skips a file whose
path already holds the same bytes; different bytes at a taken name get the
next free name (`Title (2).md`). Names are made safe for the tree and unique.

**Names and drops.** Everything not taken is in the report with the reason.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import sqlite3
import tempfile
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.config import settings
from lembas.db.models import DEFAULT_GROUP, LEVEL_STAGE, User
from lembas.db.session import OLD_SUFFIX
from lembas.security.access import at_least
from lembas.services import fs, memory_io, skill_format
from lembas.services.import_report import Report
from lembas.web.i18n import N_

log = logging.getLogger(__name__)

ROOT = "Imported"  # under the owner's home: /home/<person>/Imported
MAX_NAME = 120
CHUNK = 1024 * 1024

# The 1.x tables this reads, and what each becomes.
TABLES = ("users", "notes", "reports", "skills", "skill_revisions", "knowledge_bases",
          "documents", "memories", "data_groups")


class Refused(ValueError):
    """The import cannot start at all; the English text is marked with `N_`."""

    def __init__(self, text: str, **fields: object) -> None:
        self.text, self.fields = text, fields
        super().__init__(text % fields if fields else text)

    def translated(self) -> str:
        from lembas.web.i18n import t

        return t(self.text, **self.fields)


def old_path() -> Path:
    return settings.db_path.with_name(settings.db_path.name + OLD_SUFFIX)


# --- Names ----------------------------------------------------------------------------
_UNSAFE = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]+")


def safe_name(raw: str, *, fallback: str = "Untitled", suffix: str = "") -> str:
    """One path segment from a title: no separators or control characters, no
    leading dot, at most `MAX_NAME` characters, NFC, `suffix` kept on the end."""
    name = unicodedata.normalize("NFC", str(raw or ""))
    name = " ".join(_UNSAFE.sub(" ", name).split()).strip(" .")
    if suffix and name.lower().endswith(suffix.lower()):
        name = name[: -len(suffix)].rstrip(" .")
    name = name[: MAX_NAME - len(suffix)].rstrip(" .") or fallback
    return name + suffix


def _numbered(name: str, n: int) -> str:
    if n == 1:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        return f"{name} ({n})"
    return f"{stem} ({n}).{ext}"


# --- The old file -----------------------------------------------------------------------
@contextlib.contextmanager
def snapshot(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """A read-only look at the old database, through a private copy.

    The original is opened `mode=ro` and copied with the online backup API into
    a temporary file beside the data, which is then read; the copy is deleted
    afterwards. A WAL beside the old file is folded into the copy by the backup."""
    path = path or old_path()
    if not path.exists():
        raise Refused(N_("There is no 1.x database here (lembas.db.old) to import from."))
    fd, name = tempfile.mkstemp(prefix=".import-", suffix=".db", dir=settings.data_dir)
    os.close(fd)
    copy_path = Path(name)
    try:
        try:
            source = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                target = sqlite3.connect(str(copy_path))
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
        except sqlite3.Error as problem:
            raise Refused(N_("The 1.x database could not be read: %(error)s"),
                          error=str(problem)) from problem
        connection = sqlite3.connect(str(copy_path))
        connection.row_factory = sqlite3.Row
        try:
            tables = _tables(connection)
            if "nodes" in tables or "users" not in tables:
                raise Refused(N_("That file is not a LLeMbas 1.x database."))
            yield connection
        finally:
            connection.close()
    finally:
        copy_path.unlink(missing_ok=True)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _rows(connection: sqlite3.Connection, table: str, order: str = "created_at") -> list[dict]:
    if table not in _tables(connection):
        return []
    columns = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
    sql = f"SELECT * FROM {table}" + (f" ORDER BY {order}" if order in columns else "")
    return [dict(row) for row in connection.execute(sql)]


def survey(path: Path | None = None) -> dict[str, Any]:
    """What the old file holds, for the page: counts per kind and per account."""
    with snapshot(path) as old:
        users = _rows(old, "users")
        counts: dict[str, int] = {}
        per_user: dict[str, int] = {u["id"]: 0 for u in users}
        for table in ("notes", "reports", "skills", "documents", "memories"):
            rows = _rows(old, table, order="")
            counts[table] = len(rows)
            for row in rows:
                if row.get("owner_id") in per_user and table != "memories":
                    per_user[row["owner_id"]] += 1
        accounts = [
            {"email": u["email"], "name": u["name"], "items": per_user.get(u["id"], 0)}
            for u in users
        ]
    return {"counts": counts, "accounts": accounts}


# --- Writing ----------------------------------------------------------------------------
class _Writer:
    """Puts files into one person's tree, a name at a time, never twice."""

    def __init__(self, db: DBSession, owner: User, report: Report) -> None:
        self.db, self.owner, self.report = db, owner, report
        self.view = fs.view(db, owner)

    def _hash(self, chunks: list[bytes] | Path) -> str:
        digest = hashlib.sha256()
        for chunk in _chunks(chunks):
            digest.update(chunk)
        return digest.hexdigest()

    def put(self, directory: str, name: str, data: bytes | Path, *, kind: str,
            mime: str = "") -> str | None:
        """Write `data` as `directory/name` or the next free `name (n)`; None
        when the same bytes are already at one of those names."""
        digest = self._hash(data)
        n = 1
        while True:
            candidate = f"{directory}/{_numbered(name, n)}"
            node = fs.lookup(self.db, self.owner.org_id, candidate)
            if node is None:
                fs.write(self.view, candidate, _chunks(data), mime=mime)
                self.report.take(kind, candidate)
                return candidate
            if node.kind == "file" and node.blob_hash == digest:
                self.report.skip(kind, candidate, N_("already imported"))
                return None
            n += 1

    def put_skill(self, directory: str, name: str, versions: list[bytes]) -> None:
        """A skill's directory with `SKILL.md`, its versions oldest first."""
        current = versions[-1]
        digest = self._hash([current])
        n = 1
        while True:
            folder = f"{directory}/{name if n == 1 else f'{name}-{n}'}"
            path = f"{folder}/{skill_format.FILE_NAME}"
            node = fs.lookup(self.db, self.owner.org_id, path)
            taken = node is not None or fs.lookup(self.db, self.owner.org_id, folder) is not None
            if node is not None and node.kind == "file" and node.blob_hash == digest:
                self.report.skip("skill", path, N_("already imported"))
                return
            if not taken:
                for version in versions:
                    fs.write(self.view, path, [version], mime="text/markdown")
                if len(versions) > 1:
                    self.report.take("skill", path, N_("with %(count)s earlier versions"),
                                     count=len(versions) - 1)
                else:
                    self.report.take("skill", path)
                return
            n += 1


def _chunks(data: bytes | Path | list[bytes]) -> Iterator[bytes]:
    if isinstance(data, Path):
        with data.open("rb") as handle:
            while chunk := handle.read(CHUNK):
                yield chunk
    elif isinstance(data, (bytes, bytearray)):
        yield bytes(data)
    else:
        yield from data


def _markdown(title: str, *parts: str) -> bytes:
    body = "\n\n".join(p.strip() for p in parts if p and p.strip())
    if body.lstrip().startswith("# "):
        return (body + "\n").encode("utf-8")
    return (f"# {title.strip() or 'Untitled'}\n\n{body}\n").encode()


def _library_file(stored_name: str) -> Path | None:
    """The original upload behind a 1.x document, when it is still on disk."""
    if not stored_name or "/" in stored_name or "\\" in stored_name or stored_name.startswith("."):
        return None
    base = (settings.uploads_dir / "library").resolve()
    path = (base / stored_name).resolve()
    if path.parent != base or not path.is_file():
        return None
    return path


# --- The run ----------------------------------------------------------------------------
def run(
    db: DBSession,
    owner: User,
    *,
    path: Path | None = None,
    from_email: str = "",
    memories: bool = False,
) -> Report:
    """Import the old library into `owner`'s home (`~/Imported/`), owned by `owner`.

    `from_email` limits it to one 1.x account; empty takes every account's.
    Raises `Refused` when it cannot start; anything after that is in the report.
    """
    report = Report()
    with snapshot(path) as old:
        users = {u["id"]: u for u in _rows(old, "users")}
        wanted = (from_email or "").strip().lower()
        if wanted:
            users = {k: u for k, u in users.items() if (u["email"] or "").lower() == wanted}
            if not users:
                raise Refused(N_("The 1.x database has no account %(email)s."), email=wanted)

        notes = [r for r in _rows(old, "notes") if r.get("owner_id") in users]
        reports = [r for r in _rows(old, "reports") if r.get("owner_id") in users]
        skills = [r for r in _rows(old, "skills") if r.get("owner_id") in users]
        revisions: dict[str, list[dict]] = {}
        for row in _rows(old, "skill_revisions"):
            revisions.setdefault(row.get("skill_id"), []).append(row)
        bases = {r["id"]: r for r in _rows(old, "knowledge_bases") if r.get("owner_id") in users}
        documents = [r for r in _rows(old, "documents") if r.get("owner_id") in users]

        contributing = {r["owner_id"] for r in (*notes, *reports, *skills, *documents)}
        home = fs.ensure_home(db, owner)
        root = fs.ensure_dir(db, owner.org_id, f"{home.path}/{ROOT}", owner=owner).path
        writer = _Writer(db, owner, report)
        if not at_least(writer.view.level(root), LEVEL_STAGE):
            raise Refused(N_("%(path)s belongs to someone else; the import cannot write there."),
                          path=root)

        # One directory per 1.x account when there is more than one, so the
        # origin of every file stays visible.
        tops: dict[str, str] = {}
        used: set[str] = set()
        for user_id in sorted(contributing, key=lambda k: users[k]["email"]):
            if len(contributing) == 1:
                tops[user_id] = root
                continue
            base = safe_name(users[user_id]["email"].split("@")[0], fallback="account")
            name, n = base, 1
            while name.lower() in used:
                n += 1
                name = f"{base}-{n}"
            used.add(name.lower())
            tops[user_id] = f"{root}/{name}"

        for row in notes:
            title = row.get("title") or ""
            writer.put(f"{tops[row['owner_id']]}/Notes", safe_name(title, suffix=".md"),
                       _markdown(title, row.get("body") or ""), kind="note",
                       mime="text/markdown")

        for row in reports:
            title = row.get("title") or ""
            summary = (row.get("summary") or "").strip()
            body = row.get("body") or ""
            if not body.strip() and not summary:
                report.skip("report", title or row["id"],
                            N_("empty") if not row.get("error") else N_("it failed in 1.x"))
                continue
            writer.put(f"{tops[row['owner_id']]}/Reports", safe_name(title, suffix=".md"),
                       _markdown(title, f"*{summary}*" if summary else "", body),
                       kind="report", mime="text/markdown")

        for row in skills:
            raw = row.get("name") or ""
            name = raw if skill_format.SKILL_NAME_PATTERN.match(raw) else skill_format.slugify(raw)
            if not name or not skill_format.SKILL_NAME_PATTERN.match(name):
                report.skip("skill", raw or row["id"], N_("its name cannot be a skill's name"))
                continue
            history = sorted(revisions.get(row["id"], []), key=lambda r: r.get("created_at") or "")
            versions = [
                skill_format.compose(name, r.get("description") or "", r.get("body") or "")
                .encode("utf-8")
                for r in history
            ]
            versions.append(
                skill_format.compose(name, row.get("description") or "", row.get("body") or "")
                .encode("utf-8")
            )
            # A revision is the state before a change; one equal to the next
            # state is not a version of its own.
            unique = [v for i, v in enumerate(versions) if i == 0 or v != versions[i - 1]]
            writer.put_skill(f"{tops[row['owner_id']]}/Skills", name, unique)

        base_dirs: dict[str, str] = {}
        for base_id, base in bases.items():
            if base.get("owner_id") in tops:
                base_dirs[base_id] = (
                    f"{tops[base['owner_id']]}/Knowledge/{safe_name(base.get('name') or '')}"
                )
        for row in documents:
            title = row.get("title") or row.get("filename") or ""
            directory = base_dirs.get(row.get("base_id") or "",
                                      f"{tops[row['owner_id']]}/Knowledge")
            original = (
                _library_file(row.get("stored_name") or "")
                if (row.get("source") or "upload") == "upload"
                else None
            )
            if original is not None:
                writer.put(directory, safe_name(row.get("filename") or title, fallback="file"),
                           original, kind="document", mime=row.get("media_type") or "")
                continue
            text = row.get("extracted_text") or ""
            if not text.strip():
                report.skip("document", title or row["id"],
                            N_("neither the original file nor any extracted text is left"))
                continue
            source = row.get("source_url") or ""
            writer.put(directory, safe_name(title, suffix=".md"),
                       _markdown(title, f"<{source}>" if source else "", text),
                       kind="document", mime="text/markdown")

        if memories:
            _memories(db, old, users, report)
        else:
            count = sum(1 for r in _rows(old, "memories", order="") if r.get("owner_id") in users)
            if count:
                report.note(N_("%(count)s memories were left in the 1.x database; they come "
                               "across only when asked for, into the account with the same "
                               "email address."), count=count)
        report.note(N_("Chats, attachments, connections and models are not imported from the "
                       "1.x database. Connections and models come from an administrator's "
                       "export file."))
        db.commit()
    log.info("imported %s files from %s for %s (%s skipped)", len(report.taken),
             old_path().name, owner.email, len(report.skipped))
    return report


def _memories(db: DBSession, old: sqlite3.Connection, users: dict[str, dict],
              report: Report) -> None:
    """Each 1.x account's memories into the 2.0 account with the same email,
    through the memory import (`services/memory_io.py`), name-and-drop alike."""
    groups = {r["id"]: r.get("name") or "" for r in _rows(old, "data_groups", order="")}
    by_owner: dict[str, list[dict]] = {}
    for row in _rows(old, "memories"):
        if row.get("owner_id") in users:
            by_owner.setdefault(row["owner_id"], []).append(row)
    for owner_id, rows in by_owner.items():
        email = (users[owner_id]["email"] or "").lower()
        account = db.scalar(select(User).where(func.lower(User.email) == email))
        if account is None:
            report.skip("memory", email, N_("no account with that email address here "
                                            "(%(count)s memories)"), count=len(rows))
            continue
        document = {
            "items": [
                {
                    "type": "memory",
                    "ref": row["id"],
                    "name": " ".join(str(row.get("content") or "").split())[:60],
                    "data": {
                        "content": row.get("content") or "",
                        "author": row.get("author") or "",
                        "data_group": groups.get(row.get("data_group_id") or DEFAULT_GROUP, ""),
                        "data_group_ref": row.get("data_group_id") or DEFAULT_GROUP,
                    },
                }
                for row in rows
            ]
        }
        result = memory_io.import_into(db, account, document, into="")
        for name in result.taken:
            report.take("memory", f"{email}: {name}")
        for name, why in result.dropped:
            # memory_io's reasons are already in the reader's language.
            report.skip("memory", f"{email}: {name}", "%(why)s", why=why)


__all__ = ["ROOT", "Refused", "old_path", "run", "safe_name", "snapshot", "survey"]
