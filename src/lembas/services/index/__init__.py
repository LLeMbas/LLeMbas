"""The search index over an organisation's files and a person's chats.

- `chunks` -- cutting text into pieces by tokens (the harness spec's rules).
- `extract` -- the text of a file, by its bytes (text, PDF, DOCX).
- `ignore` -- `.lembasignore`, gitignore-like, per directory.
- `schema` -- the FTS5 and sqlite-vec tables beside the models.
- `pipeline` -- what is where (`path_hash`), and indexing one content hash.
- `worker` -- noticing changes and working through the queue in the background.
- `query` -- the search: keywords and meaning fused, then filtered by access.

The tables are `db/models/index.py`; sqlite-vec is loaded by `db/vec.py`.
"""

from __future__ import annotations

from lembas.services.index.query import Hit, QueryVector, embed_query, search, search_with
from lembas.services.index.worker import (
    install,
    progress,
    rebuild,
    run_once,
    shutdown,
    start,
)

__all__ = [
    "Hit",
    "QueryVector",
    "embed_query",
    "install",
    "progress",
    "rebuild",
    "run_once",
    "search",
    "search_with",
    "shutdown",
    "start",
]
