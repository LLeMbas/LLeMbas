"""A copy of the database, taken safely while the application runs.

SQLite's own online backup (`Connection.backup`), never a file copy: the
database is in WAL mode, and copying `lembas.db` alone while a write sits in
`lembas.db-wal` copies a database that is missing it. This is what the
deployment notes have always done by hand before an update, now one function:
`lembas-server backup` from a shell, and the updater before it asks for an update.

Backups go to `<data>/backups/lembas-<UTC time>[-label].db`, mode 600, and the
newest `keep` are kept. The blob store (`lembas-blobs.db`, the files' contents)
is backed up beside it as the same name plus `-blobs` -- a sidecar, like SQLite's
own `-wal`, so the pair is pruned and found together and one is never kept
without the other.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from lembas.config import settings

log = logging.getLogger(__name__)

DEFAULT_KEEP = 10
BLOBS_SUFFIX = "-blobs"
_LABEL = re.compile(r"[^A-Za-z0-9._-]+")


def backup_dir() -> Path:
    return settings.data_dir / "backups"


def blobs_sidecar(main: Path) -> Path:
    """Where the blob store's copy of a backup goes: beside it, `-blobs`."""
    return main.with_name(main.name + BLOBS_SUFFIX)


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
    target.chmod(0o600)
    check = sqlite3.connect(str(target))
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()
    finally:
        check.close()
    if not result or result[0] != "ok":
        raise RuntimeError(f"the backup {target.name} failed its integrity check: {result}")


def take(label: str = "", *, keep: int = DEFAULT_KEEP) -> Path:
    """Back the database and the blob store up now. Returns the main file."""
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    clean = _LABEL.sub("-", label).strip("-")[:60]
    target = folder / f"lembas-{stamp}{'-' + clean if clean else ''}.db"
    sidecar = blobs_sidecar(target)
    try:
        _copy(settings.db_path, target)
        blobs_db = settings.blobs_path
        if blobs_db.exists():
            _copy(blobs_db, sidecar)
    except Exception:
        # Half a backup is no backup: never leave one file without the other.
        target.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        raise
    prune(keep)
    log.info("backed the database up to %s", target)
    return target


def prune(keep: int = DEFAULT_KEEP) -> int:
    """Keep the newest `keep` backups this module made; 0 keeps all."""
    if keep <= 0:
        return 0
    made = sorted(backup_dir().glob("lembas-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in made[keep:]:
        old.unlink(missing_ok=True)
        blobs_sidecar(old).unlink(missing_ok=True)
        removed += 1
    return removed


__all__ = ["BLOBS_SUFFIX", "DEFAULT_KEEP", "backup_dir", "blobs_sidecar", "prune", "take"]
