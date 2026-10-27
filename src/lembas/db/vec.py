"""sqlite-vec, loaded into every connection when it is installed.

The search index (`services/index/`) keeps its vectors in SQLite: a `vec0`
table of one-bit vectors as the candidate scan, and the float32 vectors beside
the chunks for the exact rerank (`vec_distance_cosine`). Both need the
extension, and the extension is **optional** -- the `search` extra. Without it
nothing breaks: `available()` answers False, the indexer writes no vectors and
the search is the keyword half alone, which is what an instance with no
embedding model gets anyway.

Loaded in `db/session.py`'s connect listener, per connection, because a
loadable extension is per connection in SQLite. Python's `sqlite3` must have
been built with extension loading; a build without it is the same as the
package missing.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Whether the last connection opened had it. One process, one answer: the
# package is either importable or not, so the first connection decides it.
_LOADED: bool | None = None


def load(dbapi_connection) -> bool:
    """Load sqlite-vec into a raw `sqlite3` connection. True when it is there."""
    global _LOADED
    if _LOADED is False:
        return False
    try:
        import sqlite_vec
    except ImportError:
        _LOADED = False
        return False
    try:
        dbapi_connection.enable_load_extension(True)
        try:
            sqlite_vec.load(dbapi_connection)
        finally:
            dbapi_connection.enable_load_extension(False)
    except Exception as exc:  # a Python built without extensions, a bad wheel
        if _LOADED is None:
            log.warning("sqlite-vec is installed but could not be loaded: %s", exc)
        _LOADED = False
        return False
    _LOADED = True
    return True


def available() -> bool:
    """Whether connections have sqlite-vec. False before any connection opened."""
    return bool(_LOADED)


__all__ = ["available", "load"]
