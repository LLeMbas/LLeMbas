"""Finding things: keywords, meaning, and the two fused.

`fts.search_ids` was already the one seam every store searches through. This
sits beside it and keeps that true — the four stores still call one function and
still get ids back, and what changed is what is behind it.

## Reciprocal rank fusion, and why not a weight

Two rankings have to become one, and their scores are not comparable: bm25 is a
negative number whose scale depends on the corpus, cosine is 0..1. Normalising
them onto a common scale means picking a constant, and that constant is a knob
nobody can tune without a labelled test set they do not have.

RRF uses the **ranks** and not the scores: `1 / (K + rank)`, summed. It has one
constant, `K`, it is famously insensitive to it, and it degrades to exactly one
of the two lists when the other is empty — which is what makes "no embedding
model configured" mean the keyword search, unchanged, with no branch anywhere
that says so.

## The query is embedded by the caller, not here

`search` is synchronous, because every store's `search()` is and every one of
them is called from both a route and a tool runner. Embedding is an HTTP request.
So a caller that can await gets the query vector first and passes it in; one that
cannot passes nothing and gets keywords. `embed_query` is the async half, and
being able to answer `None` for every "no" is what keeps that from being a branch
at each call site.

## Visibility is still somebody else's job

Both halves return ids, and both are scored across *everything* — the filter is
applied to the row query afterwards, in each store, through
`services/sharing.py`. That order is deliberate and is the same one the
full-text path has always used: filtering afterwards is what makes it impossible
for a hit on somebody else's record to leak, even as a count.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Chunk
from lembas.services.library import chunks as chunk_service
from lembas.services.library.fts import SearchHit, fts_query, search_ids

log = logging.getLogger(__name__)

# The one constant in reciprocal rank fusion. 60 is what the original paper used
# and what everything since has copied; the method's whole appeal is that the
# result barely moves for anything in the tens. It is not a tuning knob and is
# deliberately not a setting -- a number nobody can evaluate is a number nobody
# should be asked about.
RRF_K = 60

# How many chunks are scored before they are collapsed to records. Larger than
# the number of records wanted, because one long document can own several of the
# best chunks and would otherwise crowd everything else out of the answer.
CHUNK_MULTIPLIER = 6


def embeddable(db: DBSession) -> bool:
    from lembas.services.library import indexing

    return indexing.enabled(db)


def worker_for(db: DBSession, group: str | None = None):
    """The configured embedder, resolved while a session is open.

    Split from the awaiting half deliberately. A caller that must not hold a
    database session across an HTTP request -- a tool runner, which is about to
    open its own -- resolves here, closes, and awaits `embed_with`. One that
    already holds a request's session and is content to keep it can use
    `embed_query` instead.
    """
    from lembas.services.library import indexing

    return indexing.embedder(db, group)


class QueryVector(list):
    """A query embedding that knows which model made it.

    A plain list everywhere it is used as one. The extra attribute is what lets
    `semantic_ids` skip chunks another model embedded: two models of the same
    width produce vectors that score against each other perfectly happily and
    mean nothing, and a data group may have its own embedder, so
    two such models on one instance is an ordinary arrangement rather than a
    rebuild left half done.
    """

    model_id: str = ""


async def embed_with(worker, needle: str) -> list[float] | None:
    """The query as a vector, or None.

    None for every "no": no model configured, an empty query, an endpoint that
    is down. Each of them means the same thing to the caller — search by
    keywords — so none of them is an error, and a search that quietly stops
    being semantic is far better than one that 500s because a model server was
    restarting.
    """
    from lembas.services.llm import embeddings as embeddings_service
    from lembas.services.llm.openai_client import LLMError

    if worker is None or not (needle or "").strip():
        return None
    try:
        vectors = await embeddings_service.embed(worker.endpoint, worker.model_id, [needle])
    except LLMError as exc:
        log.info("could not embed a query: %s", exc)
        return None
    if not vectors:
        return None
    vector = QueryVector(vectors[0])
    vector.model_id = worker.model_id
    return vector


async def embed_query(db: DBSession, needle: str) -> list[float] | None:
    """`worker_for` and `embed_with`, for a caller happy to hold its session."""
    return await embed_with(worker_for(db), needle)


def semantic_ids(
    db: DBSession, kind: str, vector: list[float], *, limit: int = 20
) -> list[SearchHit]:
    """Record ids whose best chunk is closest to `vector`, best first.

    A brute-force scan, and that is the right answer at this scale: a library of
    ten thousand chunks is forty megabytes of float32 and a few million
    multiply-adds, which is milliseconds. A real index is a later change behind
    this same call, which is why the signature says nothing about how.

    **A record scores as its best chunk, not its average.** One paragraph that
    answers the question is what makes a document worth returning; averaging
    would rank a long document about something else above a short one that says
    exactly the thing, because most of the long one is not about anything.

    Chunks whose width does not match the query's are skipped. That is a change
    of embedding model with a rebuild still pending, and scoring across two
    spaces produces a confident wrong answer rather than a missing one.

    **And chunks another model made are skipped when the query says which model
    it came from.** Width alone cannot tell two 1024-wide models apart, and with
    an embedder per data group two of them on one instance is ordinary. A plain
    list -- a caller that built its own vector -- keeps the width check alone.
    """
    if not vector:
        return []
    width = len(vector)
    query = select(Chunk.resource_id, Chunk.vector, Chunk.dims).where(
        Chunk.resource_type == kind
    )
    made_by = getattr(vector, "model_id", "")
    if made_by:
        query = query.where(Chunk.model_id == made_by)
    rows = db.execute(query).all()

    best: dict[str, float] = {}
    for resource_id, blob, dims in rows:
        if int(dims or 0) != width:
            continue
        stored = chunk_service.unpack(blob, int(dims))
        if not stored:
            continue
        score = chunk_service.dot(vector, stored)
        key = str(resource_id)
        if score > best.get(key, -2.0):
            best[key] = score

    ordered = sorted(best.items(), key=lambda pair: pair[1], reverse=True)
    return [SearchHit(id=key, rank=score) for key, score in ordered[: max(1, limit)]]


def fuse(*rankings: list[SearchHit], limit: int = 20) -> list[SearchHit]:
    """Reciprocal rank fusion of any number of rankings.

    The returned `rank` is the fused score, and it is **larger for better**,
    which is the opposite of bm25's convention. Nothing downstream reads it --
    every caller uses the order — but it is worth saying out loud rather than
    leaving somebody to infer it from a negative number that is no longer there.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, hit in enumerate(ranking):
            scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (RRF_K + position + 1)
    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    return [SearchHit(id=key, rank=score) for key, score in ordered[: max(1, limit)]]


def search(
    db: DBSession,
    index: str,
    needle: str,
    *,
    kind: str = "",
    vector: list[float] | None = None,
    limit: int = 20,
) -> list[SearchHit]:
    """Ids matching `needle`, keywords and meaning fused.

    With no `vector` this is `fts.search_ids` and nothing else — the same call,
    the same results, in the same order. That is what makes an instance with no
    embedding model byte-for-byte what it always was, and it is asserted by a
    test rather than left as a claim.
    """
    keyword = search_ids(db, index, needle, limit=limit)
    if not vector or not kind:
        return keyword
    meaning = semantic_ids(db, kind, vector, limit=limit * CHUNK_MULTIPLIER)
    if not meaning:
        return keyword
    if not keyword and not fts_query(needle):
        # Nothing typed that FTS could match — a query of pure punctuation, or
        # one whose every word is a separator. The semantic side still has an
        # answer, and fusing a list with nothing is that list.
        return meaning[:limit]
    return fuse(keyword, meaning, limit=limit)


__all__ = [
    "CHUNK_MULTIPLIER",
    "RRF_K",
    "embed_query",
    "embeddable",
    "fuse",
    "search",
    "semantic_ids",
]
