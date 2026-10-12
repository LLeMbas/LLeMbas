"""A copy of the database, taken safely while the application runs.

SQLite's own online backup (`Connection.backup`), never a file copy: the
database is in WAL mode, and copying `lembas.db` alone while a write sits in
`lembas.db-wal` copies a database that is missing it. This is what the
deployment notes have always done by hand before an update, now one function:
`lembas-server backup` from a shell, and the updater before it asks for an update.

Backups go to `<data>/backups/lembas-<UTC time>[-label].db`, mode 600, and the
newest `keep` are kept.
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
_LABEL = re.compile(r"[^A-Za-z0-9._-]+")


def backup_dir() -> Path:
    return settings.data_dir / "backups"


def take(label: str = "", *, keep: int = DEFAULT_KEEP) -> Path:
    """Back the database up now. Returns the file written."""
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    clean = _LABEL.sub("-", label).strip("-")[:60]
    target = folder / f"lembas-{stamp}{'-' + clean if clean else ''}.db"
    source = sqlite3.connect(str(settings.db_path))
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
        removed += 1
    return removed


__all__ = ["DEFAULT_KEEP", "backup_dir", "prune", "take"]
