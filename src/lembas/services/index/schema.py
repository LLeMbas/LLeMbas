"""The two index tables SQLAlchemy cannot describe: `index_fts` and `index_vec`.

**`index_fts`** is FTS5 over `index_chunk.text`, external content (the text is
not stored twice), kept in step by triggers -- the same arrangement as the
library's indexes in `db/migrations.py`, and made the same way: idempotently,
on every start. `unicode61 remove_diacritics 2` so "cafe" finds "café" and a
Slovak word typed without its háčeks still matches.

**`index_vec`** is sqlite-vec's `vec0` with one `bit[D]` column: each chunk's
vector quantised to one bit per dimension (128 bytes at 1024), scanned by
Hamming distance as the candidate pass. Its width is the embedder's, so it is
made when the first vector is written (`ensure_vec`), not at start; a model of
another width drops and remakes it (every old vector is from the old model and
is being re-embedded anyway). A width that is not a multiple of eight cannot be
bit-quantised: then there is no `index_vec`, and the search reranks every
vector exactly instead -- slower, still correct.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session as DBSession

from lembas.db import vec

log = logging.getLogger(__name__)

FTS = "index_fts"
VEC = "index_vec"

_FTS_STATEMENTS = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS} USING fts5("
    "text, content='index_chunk', content_rowid='id', "
    "tokenize='unicode61 remove_diacritics 2')",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS}_ai AFTER INSERT ON index_chunk BEGIN
          INSERT INTO {FTS}(rowid, text) VALUES (new.id, new.text);
        END""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS}_ad AFTER DELETE ON index_chunk BEGIN
          INSERT INTO {FTS}({FTS}, rowid, text) VALUES ('delete', old.id, old.text);
        END""",
    f"""CREATE TRIGGER IF NOT EXISTS {FTS}_au AFTER UPDATE OF text ON index_chunk BEGIN
          INSERT INTO {FTS}({FTS}, rowid, text) VALUES ('delete', old.id, old.text);
          INSERT INTO {FTS}(rowid, text) VALUES (new.id, new.text);
        END""",
)


def ensure(engine: Engine) -> list[str]:
    """Make `index_fts` and its triggers if they are missing. Returns what it made."""
    with engine.begin() as connection:
        fresh = (
            connection.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"), {"n": FTS}
            ).first()
            is None
        )
        for statement in _FTS_STATEMENTS:
            connection.execute(text(statement))
        if fresh:
            connection.execute(text(f"INSERT INTO {FTS}({FTS}) VALUES ('rebuild')"))
    return [FTS] if fresh else []


_WIDTH = re.compile(r"bit\[(\d+)\]", re.IGNORECASE)


def vec_width(db: DBSession) -> int:
    """The width `index_vec` was made for, 0 when there is none."""
    row = db.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"), {"n": VEC}
    ).first()
    if row is None or not row[0]:
        return 0
    found = _WIDTH.search(row[0])
    return int(found.group(1)) if found else 0


def bit_capable(dims: int) -> bool:
    return dims > 0 and dims % 8 == 0


def ensure_vec(db: DBSession, dims: int) -> bool:
    """`index_vec` at `dims` bits; True when it is there to write to."""
    if not vec.available() or not bit_capable(dims):
        return False
    width = vec_width(db)
    if width == dims:
        return True
    if width:
        log.info("index: the embedder's width changed (%d -> %d); remaking %s", width, dims, VEC)
        db.execute(text(f"DROP TABLE {VEC}"))
    db.execute(text(f"CREATE VIRTUAL TABLE {VEC} USING vec0(embedding bit[{dims}])"))
    return True


def drop_vec_rows(db: DBSession, chunk_ids: list[int]) -> None:
    if not chunk_ids or not vec.available() or not vec_width(db):
        return
    for start in range(0, len(chunk_ids), 500):
        batch = chunk_ids[start : start + 500]
        marks = ", ".join(str(int(i)) for i in batch)
        db.execute(text(f"DELETE FROM {VEC} WHERE rowid IN ({marks})"))


__all__ = ["FTS", "VEC", "bit_capable", "drop_vec_rows", "ensure", "ensure_vec", "vec_width"]
