"""Backups: the whole instance as one archive, and putting one back.

**One backup is one file**, `lembas-<UTC time>[-label].tar.zst` (`.tar.gz` when
the `zstandard` module is not installed), mode 600, in the backup directory
(`LEMBAS_BACKUP_DIR`, `<data>/backups/` by default). Inside, in this order:

- `manifest.json` -- `format` (`lembas-backup`), `format_version`,
  `app_version`, `created_at`, `label`, every file with its size and sha256,
  and the secret key when one was asked for;
- `lembas.db` and `lembas-blobs.db` -- each taken with SQLite's online backup
  (`Connection.backup`), never a file copy: the databases are in WAL mode, and
  copying the file alone while a write sits in its `-wal` copies a database
  that is missing it. Each copy passes `PRAGMA integrity_check` before it goes in;
- `uploads/` -- as it is on disk.

**The secret key** goes in only under a passphrase: encrypted with scrypt →
Fernet, exactly as an export's keys are (`services/export_format.py`). Without
it, a restore onto an instance with another `LEMBAS_SECRET_KEY` cannot read the
stored API keys, and the admin page says so.

**Restore is two steps.** `stage` checks an archive -- the manifest, every
file's size and hash, `integrity_check` on both databases, a 2.x database, and
the same major version -- and puts the checked files into `restore-pending/`.
Nothing running is touched. On the next start `init_db()` calls
`apply_pending`, which moves the current databases and uploads aside into
`pre-restore-<UTC time>/` and puts the backup in their place. When the backup
carried its secret key and that key differs from this instance's, the stored
keys are re-encrypted under this instance's key as it is applied (`rekey`).
`lembas.db.old`, the 1.x database, is neither in a backup nor touched by one.

**Keeping.** A backup taken by the schedule or by hand is pruned by the
schedule's rule: the newest of each of the last N days and of the last N
weeks, and always the newest one. `lembas-server backup --keep N` keeps the
newest N instead. Files this module did not name are never deleted.

The schedule rides on the ticker (`tick`): once a day at the set time.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import tarfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lembas import __version__
from lembas.config import settings
from lembas.web.i18n import N_

log = logging.getLogger(__name__)

FORMAT = "lembas-backup"
FORMAT_VERSION = 1
MANIFEST = "manifest.json"
PENDING = "restore-pending"
ASIDE = "pre-restore-"
REKEY = "rekey"
DEFAULT_KEEP = 10
CHUNK = 1024 * 1024

ARCHIVE = re.compile(r"^lembas-(\d{8}-\d{6})(?:-[A-Za-z0-9._-]+)?\.tar\.(zst|gz)$")
_LABEL = re.compile(r"[^A-Za-z0-9._-]+")

try:  # optional: smaller and faster, and not a hard dependency
    import zstandard
except ImportError:  # pragma: no cover - depends on the install
    zstandard = None


class Refused(ValueError):
    """An archive that will not be restored, and why (English, marked `N_`)."""

    def __init__(self, text: str, **fields: object) -> None:
        self.text, self.fields = text, fields
        super().__init__(text % fields if fields else text)

    def translated(self) -> str:
        from lembas.web.i18n import t

        return t(self.text, **self.fields)


def backup_dir() -> Path:
    return settings.backups_path


def extension() -> str:
    return "zst" if zstandard is not None else "gz"


# --- Taking one -----------------------------------------------------------------------
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _copy(source_path: Path, target: Path) -> None:
    """One online backup of one SQLite file, checked."""
    source = sqlite3.connect(str(source_path))
    try:
        copy = sqlite3.connect(str(target))
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    _integrity(target)


def _integrity(path: Path) -> None:
    check = sqlite3.connect(str(path))
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()
    finally:
        check.close()
    if not result or result[0] != "ok":
        raise RuntimeError(f"{path.name} failed its integrity check: {result}")


def _uploads() -> list[tuple[str, Path]]:
    """Every regular file under `uploads/`, as (name in the archive, path)."""
    root = settings.uploads_dir
    out: list[tuple[str, Path]] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            out.append(("uploads/" + path.relative_to(root).as_posix(), path))
    return out


@contextlib.contextmanager
def _writer(path: Path) -> Iterator[tarfile.TarFile]:
    with path.open("wb") as raw:
        if path.name.endswith(".zst"):
            compressor = zstandard.ZstdCompressor(level=10)
            with compressor.stream_writer(raw, closefd=False) as stream, tarfile.open(
                fileobj=stream, mode="w|"
            ) as tar:
                yield tar
        else:
            with tarfile.open(fileobj=raw, mode="w|gz") as tar:
                yield tar


ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
GZIP_MAGIC = b"\x1f\x8b"


@contextlib.contextmanager
def _reader(path: Path) -> Iterator[tarfile.TarFile]:
    """The archive as a stream, its compression told by its first bytes (an
    upload has whatever name the browser gave it)."""
    with path.open("rb") as raw:
        magic = raw.read(4)
        raw.seek(0)
        if magic == ZSTD_MAGIC:
            if zstandard is None:
                raise Refused(N_("This backup is zstd-compressed and the zstandard module is "
                                 "not installed here."))
            with zstandard.ZstdDecompressor().stream_reader(raw) as stream, tarfile.open(
                fileobj=stream, mode="r|"
            ) as tar:
                yield tar
        elif magic[:2] == GZIP_MAGIC:
            with tarfile.open(fileobj=raw, mode="r|gz") as tar:
                yield tar
        else:
            raise Refused(N_("That file is not a LLeMbas backup."))


def _add(tar: tarfile.TarFile, name: str, path: Path) -> None:
    info = tarfile.TarInfo(name)
    stat = path.stat()
    info.size, info.mtime, info.mode = stat.st_size, int(stat.st_mtime), 0o600
    with path.open("rb") as handle:
        tar.addfile(info, handle)


def _key_block(passphrase: str) -> dict[str, Any] | None:
    if not passphrase:
        return None
    from lembas.services import export_format

    fernet, header = export_format.new_encryption(passphrase)
    return {"encryption": header,
            "token": fernet.encrypt(settings.secret_key.encode("utf-8")).decode("ascii")}


def _stored_passphrase() -> str:
    from lembas.db.session import session_scope
    from lembas.services import crypto, settings_store

    with session_scope() as db:
        return crypto.decrypt(settings_store.backups(db).get("key_passphrase_encrypted") or "")


def take(label: str = "", *, keep: int | None = None, passphrase: str | None = None) -> Path:
    """Back the instance up now. Returns the archive.

    `passphrase` None uses the one stored for scheduled backups (if any); ""
    leaves the secret key out. `keep` None prunes by the schedule's rule,
    0 keeps everything, N keeps the newest N."""
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    clean = _LABEL.sub("-", label).strip("-.")[:60]
    target = folder / f"lembas-{stamp}{'-' + clean if clean else ''}.tar.{extension()}"
    n = 1
    while target.exists():  # two in one second
        n += 1
        target = folder / f"lembas-{stamp}-{clean + '-' if clean else ''}{n}.tar.{extension()}"
    if passphrase is None:
        passphrase = _stored_passphrase()
    work = folder / f".partial-{stamp}-{secrets.token_hex(4)}"
    work.mkdir(mode=0o700)
    partial = folder / f".{target.name}.partial"
    try:
        databases = [(settings.db_path.name, work / settings.db_path.name)]
        _copy(settings.db_path, databases[0][1])
        if settings.blobs_path.exists():
            databases.append((settings.blobs_path.name, work / settings.blobs_path.name))
            _copy(settings.blobs_path, databases[1][1])
        files = [*databases, *_uploads()]
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app_version": __version__,
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "label": clean,
            "files": [{"path": name, "size": path.stat().st_size, "sha256": _sha256(path)}
                      for name, path in files],
            "secret_key": None if settings.secret_key_is_ephemeral else _key_block(passphrase),
        }
        body = json.dumps(manifest, indent=2).encode("utf-8")
        with _writer(partial) as tar:
            info = tarfile.TarInfo(MANIFEST)
            info.size, info.mtime, info.mode = len(body), int(datetime.now(UTC).timestamp()), 0o600
            tar.addfile(info, io.BytesIO(body))
            for name, path in files:
                _add(tar, name, path)
        partial.chmod(0o600)
        partial.rename(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    prune(keep)
    log.info("backed the instance up to %s", target)
    return target


# --- The list -------------------------------------------------------------------------
@dataclass
class Archive:
    name: str
    path: Path
    size: int
    created: datetime

    @property
    def label(self) -> str:
        match = re.match(r"^lembas-\d{8}-\d{6}-?(.*?)\.tar\.(zst|gz)$", self.name)
        return match.group(1) if match else ""


def _created(name: str, path: Path) -> datetime:
    match = ARCHIVE.match(name)
    if match:
        with contextlib.suppress(ValueError):
            return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    return datetime.fromtimestamp(path.stat().st_mtime, UTC)


def archives() -> list[Archive]:
    """Every backup in the backup directory, newest first."""
    folder = backup_dir()
    if not folder.is_dir():
        return []
    found = [
        Archive(p.name, p, p.stat().st_size, _created(p.name, p))
        for p in folder.iterdir()
        if p.is_file() and ARCHIVE.match(p.name)
    ]
    return sorted(found, key=lambda a: (a.created, a.path.stat().st_mtime_ns, a.name),
                  reverse=True)


def find(name: str) -> Archive | None:
    """An archive by its file name, and only one this module would have named."""
    if not ARCHIVE.match(name or ""):
        return None
    return next((a for a in archives() if a.name == name), None)


def delete(name: str) -> bool:
    found = find(name)
    if found is None:
        return False
    found.path.unlink(missing_ok=True)
    return True


def _rule() -> tuple[int, int]:
    """(keep_daily, keep_weekly) as the administrator set them."""
    from lembas.db.session import session_scope
    from lembas.services import settings_store

    try:
        with session_scope() as db:
            values = settings_store.backups(db)
    except Exception:  # no database to ask: the defaults
        values = settings_store.defaults(settings_store.BACKUPS)
    return int(values["keep_daily"]), int(values["keep_weekly"])


def keepers(found: list[Archive], daily: int, weekly: int) -> set[str]:
    """Names kept by the rule: the newest, and the newest of each of the last
    `daily` days and `weekly` ISO weeks. Both 0 keeps everything."""
    if daily <= 0 and weekly <= 0:
        return {a.name for a in found}
    keep: set[str] = {found[0].name} if found else set()
    days: set[Any] = set()
    weeks: set[Any] = set()
    for archive in found:  # newest first
        day = archive.created.date()
        week = archive.created.isocalendar()[:2]
        if day not in days and len(days) < daily:
            days.add(day)
            keep.add(archive.name)
        if week not in weeks and len(weeks) < weekly:
            weeks.add(week)
            keep.add(archive.name)
    return keep


def prune(keep: int | None = None) -> int:
    """Delete backups past the rule (`keep` None), or past the newest `keep`
    (0 keeps all). Returns how many went."""
    found = archives()
    if keep is None:
        kept = keepers(found, *_rule())
    elif keep <= 0:
        return 0
    else:
        kept = {a.name for a in found[:keep]}
    removed = 0
    for archive in found:
        if archive.name not in kept:
            archive.path.unlink(missing_ok=True)
            removed += 1
    return removed


def manifest(path: Path) -> dict[str, Any]:
    """The manifest of an archive -- the first member -- or `Refused`."""
    try:
        with _reader(path) as tar:
            first = tar.next()
            if first is None or first.name != MANIFEST or not first.isfile():
                raise Refused(N_("That file is not a LLeMbas backup."))
            handle = tar.extractfile(first)
            document = json.loads(handle.read(4 * 1024 * 1024).decode("utf-8"))
    except Refused:
        raise
    except (tarfile.TarError, OSError, EOFError, ValueError) as problem:
        raise Refused(N_("That file is not a LLeMbas backup.")) from problem
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise Refused(N_("That file is not a LLeMbas backup."))
    return document


# --- Restoring --------------------------------------------------------------------------
def _major(version: str) -> str:
    return str(version or "").split(".", 1)[0]


def _member_target(root: Path, name: str, expected: dict[str, dict]) -> Path:
    if name not in expected:
        raise Refused(N_("The backup holds a file its manifest does not name: %(name)s"),
                      name=name)
    target = (root / name).resolve()
    if not str(target).startswith(str(root.resolve()) + os.sep):
        raise Refused(N_("The backup holds a file its manifest does not name: %(name)s"),
                      name=name)
    return target


def _extract(path: Path, root: Path, document: dict[str, Any]) -> None:
    files = document.get("files")
    if not isinstance(files, list):
        raise Refused(N_("The backup's manifest is damaged."))
    expected: dict[str, dict] = {}
    for entry in files:
        name = str(entry.get("path") or "") if isinstance(entry, dict) else ""
        if (not name or name.startswith("/") or ".." in name.split("/")
                or not (name in (settings.db_path.name, settings.blobs_path.name)
                        or name.startswith("uploads/"))):
            raise Refused(N_("The backup's manifest is damaged."))
        expected[name] = entry
    if settings.db_path.name not in expected:
        raise Refused(N_("The backup has no database in it."))
    seen: set[str] = set()
    with _reader(path) as tar:
        for member in tar:
            if member.name == MANIFEST:
                continue
            if not member.isfile():
                raise Refused(N_("The backup holds a file its manifest does not name: %(name)s"),
                              name=member.name)
            target = _member_target(root, member.name, expected)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            source = tar.extractfile(member)
            with target.open("wb") as out:
                while chunk := source.read(CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
            entry = expected[member.name]
            if size != entry.get("size") or digest.hexdigest() != entry.get("sha256"):
                raise Refused(N_("%(name)s in the backup does not match its manifest."),
                              name=member.name)
            seen.add(member.name)
    missing = sorted(set(expected) - seen)
    if missing:
        raise Refused(N_("The backup is missing files its manifest names: %(names)s"),
                      names=", ".join(missing[:5]))


def _check_databases(root: Path) -> None:
    from lembas.db.session import SCHEMA_VERSION

    for name in (settings.db_path.name, settings.blobs_path.name):
        path = root / name
        if not path.exists():
            continue
        try:
            _integrity(path)
        except (RuntimeError, sqlite3.Error) as problem:
            raise Refused(N_("%(name)s in the backup failed its integrity check."),
                          name=name) from problem
    connection = sqlite3.connect(str(root / settings.db_path.name))
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    if version < SCHEMA_VERSION:
        raise Refused(N_("The database in the backup is not a 2.x database."))


@dataclass
class Staged:
    app_version: str
    created_at: str
    warnings: list[str]


def stage(path: Path, passphrase: str = "") -> Staged:
    """Check an archive and put it into `restore-pending/` for the next start.
    Raises `Refused` with the reason; nothing running is touched either way."""
    document = manifest(path)
    version = document.get("format_version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise Refused(N_("That backup is format version %(theirs)s; this server reads version "
                         "%(ours)s."), theirs=version, ours=FORMAT_VERSION)
    theirs = str(document.get("app_version") or "")
    if _major(theirs) != _major(__version__):
        raise Refused(N_("That backup is from LLeMbas %(theirs)s; only a backup from %(major)s.x "
                         "can be restored here."), theirs=theirs or "?",
                      major=_major(__version__))
    warnings: list[str] = []
    old_key = ""
    block = document.get("secret_key")
    if isinstance(block, dict):
        if passphrase:
            from lembas.services import export_format

            try:
                fernet = export_format.opener(block.get("encryption"), passphrase)
                old_key = fernet.decrypt(str(block.get("token")).encode("ascii")).decode("utf-8")
            except Exception as problem:
                raise Refused(N_("Wrong passphrase for the secret key in this backup.")) \
                    from problem
        else:
            warnings.append(N_("The backup carries its secret key, but no passphrase was given: "
                               "stored keys will open only if this instance's secret key is the "
                               "one the backup was taken under."))
    else:
        warnings.append(N_("The backup does not carry its secret key: stored keys will open only "
                           "if this instance's secret key is the one the backup was taken "
                           "under."))
    data = settings.data_dir
    staging = data / f".restore-staging-{secrets.token_hex(4)}"
    staging.mkdir(mode=0o700)
    try:
        _extract(path, staging, document)
        _check_databases(staging)
        (staging / settings.db_path.name).chmod(0o600)
        if old_key and old_key != settings.secret_key:
            from lembas.services import crypto

            (staging / REKEY).write_bytes(crypto.fernet_for(settings.secret_key).encrypt(
                old_key.encode("utf-8")))
            (staging / REKEY).chmod(0o600)
        (staging / MANIFEST).write_text(json.dumps(
            {**document, "staged_from": path.name,
             "staged_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}, indent=2))
        pending = data / PENDING
        if pending.exists():
            shutil.rmtree(pending)
        staging.rename(pending)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    log.warning("a restore of %s is staged; it is applied at the next start", path.name)
    return Staged(theirs, str(document.get("created_at") or ""), warnings)


def pending() -> dict[str, Any] | None:
    """The staged restore's manifest, or None."""
    path = settings.data_dir / PENDING / MANIFEST
    if not path.is_file():
        return None
    with contextlib.suppress(ValueError, OSError):
        return json.loads(path.read_text())
    return None


def cancel() -> bool:
    folder = settings.data_dir / PENDING
    if not folder.exists():
        return False
    shutil.rmtree(folder)
    return True


_SIDECARS = ("", "-wal", "-shm")


def apply_pending(data_dir: Path | None = None) -> Path | None:
    """Put a staged restore in place, the current data moved aside first.
    Called by `init_db()` before anything opens the database. Returns the
    directory the current data went to, or None when nothing was staged."""
    data = data_dir or settings.data_dir
    folder = data / PENDING
    if not (folder / MANIFEST).is_file():
        return None
    from lembas.db.session import reset_engine

    reset_engine()
    aside = data / f"{ASIDE}{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    aside.mkdir(mode=0o700)
    db_name, blobs_name = settings.db_path.name, settings.blobs_path.name
    for base in (db_name, blobs_name):
        for suffix in _SIDECARS:
            current = data / (base + suffix)
            if current.exists():
                current.rename(aside / current.name)
    if (data / "uploads").exists():
        (data / "uploads").rename(aside / "uploads")
    for name in (db_name, blobs_name, "uploads"):
        if (folder / name).exists():
            (folder / name).rename(data / name)
    (data / "uploads").mkdir(exist_ok=True)
    if (folder / REKEY).exists():
        try:
            from lembas.services import crypto

            old_key = crypto.fernet_for(settings.secret_key).decrypt(
                (folder / REKEY).read_bytes()).decode("utf-8")
            count = rekey(data / db_name, old_key, settings.secret_key)
            log.warning("re-encrypted %s stored keys under this instance's secret key", count)
        except Exception:
            log.exception("the restored keys could not be re-encrypted; enter them again")
    (folder / MANIFEST).rename(aside / "restored-from.json")
    shutil.rmtree(folder, ignore_errors=True)
    log.warning("restored a backup; the data it replaced is in %s", aside)
    return aside


# Every stored secret: (table, column). Settings hold theirs as JSON keys
# ending `_encrypted`, in any group.
SECRET_COLUMNS = (
    ("connections", "api_key_encrypted"),
    ("connections", "billing_key_encrypted"),
    ("custom_tools", "secret_encrypted"),
    ("mcp_servers", "secret_encrypted"),
)


def rekey(db_path: Path, old_key: str, new_key: str) -> int:
    """Re-encrypt every stored key from `old_key` to `new_key`, in place.
    A value the old key does not open is left as it is. Returns how many moved."""
    from cryptography.fernet import InvalidToken

    from lembas.services import crypto

    old, new = crypto.fernet_for(old_key), crypto.fernet_for(new_key)

    def move(value: str) -> str | None:
        if not value:
            return None
        try:
            return new.encrypt(old.decrypt(value.encode("ascii"))).decode("ascii")
        except (InvalidToken, ValueError):
            return None

    moved = 0
    connection = sqlite3.connect(str(db_path))
    try:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        for table, column in SECRET_COLUMNS:
            if table not in tables:
                continue
            for row_id, value in connection.execute(f"SELECT id, {column} FROM {table}").fetchall():
                changed = move(value or "")
                if changed:
                    connection.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?",
                                       (changed, row_id))
                    moved += 1
        if "settings" in tables:
            for key, raw in connection.execute("SELECT key, value FROM settings").fetchall():
                with contextlib.suppress(ValueError, TypeError):
                    values = json.loads(raw)
                    if not isinstance(values, dict):
                        continue
                    dirty = False
                    for name, value in values.items():
                        if name.endswith("_encrypted") and isinstance(value, str):
                            changed = move(value)
                            if changed:
                                values[name] = changed
                                dirty = True
                                moved += 1
                    if dirty:
                        connection.execute("UPDATE settings SET value = ? WHERE key = ?",
                                           (json.dumps(values), key))
        connection.commit()
    finally:
        connection.close()
    return moved


# --- The schedule -----------------------------------------------------------------------
_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def parse_time(value: str) -> tuple[int, int] | None:
    match = _TIME.match(value or "")
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return (hour, minute) if hour < 24 and minute < 60 else None


def due(values: dict[str, Any], now: datetime) -> bool:
    """Whether the daily backup should run now (`now` in the server's time)."""
    if not values.get("enabled"):
        return False
    at = parse_time(str(values.get("time") or ""))
    if at is None:
        return False
    if values.get("last_day") == now.date().isoformat():
        return False
    return (now.hour, now.minute) >= at


async def tick(*, now: datetime | None = None) -> Path | None:
    """The scheduled backup, when due. Called by the ticker at most once a minute."""
    import asyncio

    from lembas.db.session import session_scope
    from lembas.services import settings_store

    now = now or datetime.now().astimezone()
    with session_scope() as db:
        values = settings_store.backups(db)
        if not due(values, now):
            return None
        # Claimed before it runs: a backup that fails is not retried every
        # minute for the rest of the day; the error is on the page.
        settings_store.update(db, {"last_day": now.date().isoformat()},
                              key=settings_store.BACKUPS)
    try:
        taken = await asyncio.to_thread(take, "daily")
        error = ""
    except Exception as problem:
        log.exception("the scheduled backup failed")
        taken, error = None, f"{type(problem).__name__}: {problem}"[:300]
    with session_scope() as db:
        settings_store.update(db, {"last_error": error}, key=settings_store.BACKUPS)
    return taken


__all__ = [
    "FORMAT",
    "FORMAT_VERSION",
    "Archive",
    "Refused",
    "Staged",
    "apply_pending",
    "archives",
    "backup_dir",
    "cancel",
    "delete",
    "due",
    "find",
    "keepers",
    "manifest",
    "parse_time",
    "pending",
    "prune",
    "rekey",
    "stage",
    "take",
    "tick",
]
