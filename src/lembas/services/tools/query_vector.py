"""The query embedding the library searches rank by -- shared by
`knowledge_search`, `notes_search` and `report_search`."""

from __future__ import annotations

from lembas.db.session import session_scope


async def _query_vector(query: str, group: str | None = None) -> list[float] | None:
    """The query as a vector, for the stores that can use one.

    Its own session, opened and closed before the caller opens theirs: this is
    an HTTP request, and holding a database session across one is the trade
    compaction and the project listing both already refuse.
    """
    if not query:
        return None
    from lembas.services.library import retrieval

    with session_scope() as db:
        worker = retrieval.worker_for(db, group)
    return await retrieval.embed_with(worker, query)
