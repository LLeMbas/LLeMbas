"""Searching the index: keywords and meaning, fused, then filtered by access.

## The contract (what the `search` tool and the search page call)

    hits = await search(db, user, chat, "how do we deploy", path_prefix="/proj",
                        kinds=("file",), limit=20)

- `user` is who is asking; `chat` the session they ask from, or None for the
  person's own search box. In a chat, a file hit must be readable **in that
  session** (its grants, its data group's floor, its boundary); outside one, by
  the person.
- `path_prefix` narrows files to a directory (absolute, or relative to nothing:
  it is normalised against `/`). It is a narrowing, never a widening: a prefix
  outside the session's boundary finds no files.
- `kinds` is any of `"file"` and `"chat"`. Chats are the asker's own (a chat is
  found by its owner only; sharing chats, when it exists, is where to widen
  `_chat_scope`).
- Returns `Hit`s, best first: kind, path (files) or chat id and message id
  (chats), node id, a title, a snippet, the line range of the piece in the
  text, and the fused score (larger is better; only the order means anything).
  At most one hit per file and per message -- its best piece.
- Never raises for a bad query: a query FTS cannot parse matches nothing by
  keyword, an embedder that is down means keywords only.

## Scoring first, filtering after

Candidates are scored across the whole organisation (narrowed only by the
prefix, the chat boundary and the kind, which are plain path conditions), and
**then** each one is put to `security/access.py`. That order is what makes a
hit in a directory the session may not read impossible to leak, not even as a
count: nothing about a file the session cannot see changes what it is shown.
When filtering leaves fewer hits than asked for and the candidate lists were
full, the lists are widened and scored again, a few times at most -- a narrow
session in a large organisation is the case this is for.

## The two halves

- **Keywords**: FTS5 bm25 over `index_fts`, every term required first, then
  any of them (a model writes questions, not keyword lists).
- **Meaning**: the query embedded by the instance's embedder; candidates from
  the one-bit `index_vec` scan (`BIT_CANDIDATES` by Hamming distance, ~128
  bytes a piece), reranked exactly by cosine on the stored float32 vectors.
  When the scope holds at most `EXACT_LIMIT` vectors the bit scan is skipped
  and the scope is ranked exactly -- it is faster at that size, and a small
  scope inside a large organisation would otherwise be starved by candidates
  from elsewhere.

Fused by **reciprocal rank** (`fuse`): bm25 and cosine are not on one scale,
ranks are. With no embedder, no sqlite-vec, or no vectors yet, the meaning half
is empty and the result is the keyword ranking, unchanged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

from lembas.db import vec
from lembas.db.models import INDEXED_CHAT, INDEXED_FILE, LEVEL_RO, Chat, User
from lembas.security import access
from lembas.services.index import pipeline, schema
from lembas.services.llm.openai_client import LLMError

log = logging.getLogger(__name__)

# The one constant in reciprocal rank fusion; 60 is the original paper's, and
# the result barely moves for anything in the tens. Not a setting.
RRF_K = 60
# Candidates per half, as a multiple of the hits wanted: one file can own
# several of the best pieces.
CANDIDATES = 6
WIDENINGS = 3
# The bit scan's k. Measured: bit + rerank of 500 had recall 1.00 at 20 on
# real embeddings, 200 had 0.98.
BIT_CANDIDATES = 500
# At or under this many vectors in scope, rank them all exactly instead.
EXACT_LIMIT = 5_000
SNIPPET_CHARS = 240

KINDS = (INDEXED_FILE, INDEXED_CHAT)


@dataclass(frozen=True)
class Hit:
    kind: str  # "file" or "chat"
    path: str  # a file's path; "" for a chat
    node_id: str  # a file's node, or the chat
    message_id: str | None
    title: str  # a file's name, or the chat's title
    snippet: str
    line_start: int
    line_end: int
    score: float
    chunk_id: int


# --- FTS query text (from services/library/fts.py) -------------------------------
# Every token quoted and the operators dropped: FTS5's MATCH syntax has
# operators and a quoting rule, so a bare `?` or an unbalanced quote is a syntax
# error rather than a search that finds nothing. Costs typing an FTS expression
# on purpose, which nobody does; buys a search that cannot be made to throw.
_TOKENS = re.compile(r"[^\W_]+(?:'[^\W_]+)*", re.UNICODE)
MAX_TERMS = 24


def terms(needle: str) -> list[str]:
    return _TOKENS.findall(needle or "")[:MAX_TERMS]


def fts_query(needle: str, *, operator: str = "AND") -> str:
    quoted = ['"' + token.replace('"', '""') + '"' for token in terms(needle)]
    return f" {operator} ".join(quoted)


def fuse(*rankings: list[int]) -> list[tuple[int, float]]:
    """Reciprocal rank fusion of rankings of ids; larger score is better."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for position, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + position + 1)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


# --- Scope ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Scope:
    """Plain conditions on `path_hash p`: organisation, kind, directory, owner."""

    sql: str
    params: dict

    @property
    def empty(self) -> bool:
        return self.sql == "0"


def _file_prefix(chat: Chat | None, path_prefix: str | None) -> str | None:
    """The directory files must be in, or None when the two do not meet."""
    boundary = access.normalise(chat.boundary or "/") if chat is not None else "/"
    if not path_prefix:
        return boundary
    try:
        wanted = access.normalise(path_prefix)
    except access.BadPath:
        return None
    if access.is_within(wanted, boundary):
        return wanted
    if access.is_within(boundary, wanted):
        return boundary
    return None


def scope_for(
    user: User, chat: Chat | None, path_prefix: str | None, kinds: tuple[str, ...]
) -> Scope:
    parts: list[str] = []
    params: dict = {"org": user.org_id, "user": user.id}
    if INDEXED_FILE in kinds:
        prefix = _file_prefix(chat, path_prefix)
        if prefix == "/":
            parts.append("(p.kind = 'file')")
        elif prefix is not None:
            params["prefix"] = prefix
            params["below"] = access.like_prefix(prefix)
            parts.append(
                "(p.kind = 'file' AND (p.path = :prefix OR p.path LIKE :below ESCAPE '\\'))"
            )
    if INDEXED_CHAT in kinds:
        parts.append(_chat_scope())
    if not parts:
        return Scope("0", {})
    return Scope(f"p.org_id = :org AND ({' OR '.join(parts)})", params)


def _chat_scope() -> str:
    # A chat is its owner's. Shared chats, when they exist, widen this.
    return "(p.kind = 'chat' AND p.user_id = :user)"


# --- The two halves -------------------------------------------------------------
def keyword_ids(db: DBSession, needle: str, scope: Scope, limit: int) -> list[int]:
    if scope.empty or not fts_query(needle):
        return []

    def run(match: str) -> list[int]:
        try:
            rows = db.execute(
                text(
                    f"SELECT c.id FROM {schema.FTS} f JOIN index_chunk c ON c.id = f.rowid "
                    f"WHERE {schema.FTS} MATCH :q AND EXISTS (SELECT 1 FROM path_hash p "
                    f"WHERE p.hash = c.hash AND {scope.sql}) "
                    f"ORDER BY bm25({schema.FTS}) LIMIT :n"
                ),
                {**scope.params, "q": match, "n": limit},
            ).all()
        except Exception:  # a broken index must not break the search
            log.exception("index: full-text search failed")
            db.rollback()
            return []
        return [int(row[0]) for row in rows]

    return run(fts_query(needle)) or run(fts_query(needle, operator="OR"))


def meaning_ids(
    db: DBSession, vector: bytes, model: str, dims: int, scope: Scope, limit: int
) -> list[int]:
    """Chunk ids nearest `vector` (float32 bytes), exact cosine order."""
    if scope.empty or not vec.available() or not vector:
        return []
    params = {**scope.params, "q": vector, "model": model, "bytes": dims * 4, "n": limit}
    where = (
        "t.model = :model AND c.vector IS NOT NULL AND length(c.vector) = :bytes "
        f"AND EXISTS (SELECT 1 FROM path_hash p WHERE p.hash = c.hash AND {scope.sql})"
    )
    try:
        width = schema.vec_width(db)
        exact = width != dims
        if not exact:
            # Counted on the hash index alone, and only up to the limit: reading
            # the vectors to count them would cost more than the scan it decides.
            in_scope = db.execute(
                text(
                    "SELECT count(*) FROM (SELECT 1 FROM index_chunk c WHERE c.hash IN "
                    f"(SELECT p.hash FROM path_hash p WHERE {scope.sql}) LIMIT :cap)"
                ),
                {**scope.params, "cap": EXACT_LIMIT + 1},
            ).scalar()
            exact = int(in_scope or 0) <= EXACT_LIMIT
        if exact:
            rows = db.execute(
                text(
                    "SELECT c.id FROM index_chunk c JOIN index_text t ON t.hash = c.hash "
                    f"WHERE {where} ORDER BY vec_distance_cosine(c.vector, :q) LIMIT :n"
                ),
                params,
            ).all()
            return [int(row[0]) for row in rows]
        k = max(BIT_CANDIDATES, limit)
        candidates = [
            int(row[0])
            for row in db.execute(
                text(
                    f"SELECT rowid FROM {schema.VEC} "
                    "WHERE embedding MATCH vec_quantize_binary(:q) AND k = :k"
                ),
                {"q": vector, "k": k},
            ).all()
        ]
        if not candidates:
            return []
        marks = ", ".join(str(i) for i in candidates)
        rows = db.execute(
            text(
                "SELECT c.id FROM index_chunk c JOIN index_text t ON t.hash = c.hash "
                f"WHERE c.id IN ({marks}) AND {where} "
                "ORDER BY vec_distance_cosine(c.vector, :q) LIMIT :n"
            ),
            params,
        ).all()
        return [int(row[0]) for row in rows]
    except Exception:  # vectors are the optional half
        log.exception("index: vector search failed")
        db.rollback()
        return []


# --- Embedding the query ------------------------------------------------------------
@dataclass(frozen=True)
class QueryVector:
    vector: bytes
    model: str
    dims: int


async def embed_query(db: DBSession, needle: str) -> QueryVector | None:
    """The query embedded by the instance's embedder, or None for every "no".

    None: no embedder, no sqlite-vec, an empty query, an endpoint that is down.
    Each means keywords only to the caller, never an error.
    """
    worker = pipeline.embedder(db)
    if worker is None or not (needle or "").strip():
        return None
    try:
        vectors = await pipeline._embed(worker, [needle])
    except LLMError as exc:
        log.info("index: could not embed a query: %s", exc)
        return None
    if not vectors:
        return None
    return QueryVector(pipeline.pack(vectors[0]), worker.model_id, len(vectors[0]))


# --- Putting it together ---------------------------------------------------------
def _snippet(body: str, needle: str) -> str:
    flat = body.strip()
    if len(flat) <= SNIPPET_CHARS:
        return flat
    lowered = flat.casefold()
    at = -1
    for term in terms(needle):
        at = lowered.find(term.casefold())
        if at >= 0:
            break
    if at < 0:
        return flat[:SNIPPET_CHARS].rstrip() + "…"
    start = max(0, at - SNIPPET_CHARS // 3)
    end = min(len(flat), start + SNIPPET_CHARS)
    return ("…" if start else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


def _expand(
    db: DBSession,
    ranked: list[tuple[int, float]],
    scope: Scope,
    resolver: access.Resolver,
    needle: str,
    limit: int,
) -> list[Hit]:
    if not ranked:
        return []
    marks = ", ".join(str(chunk_id) for chunk_id, _ in ranked)
    chunks = {
        int(row[0]): row
        for row in db.execute(
            text(
                "SELECT id, hash, text, line_start, line_end FROM index_chunk "
                f"WHERE id IN ({marks})"
            )
        ).all()
    }
    hashes = sorted({row[1] for row in chunks.values()})
    places: dict[str, list] = {}
    for start in range(0, len(hashes), 500):
        part = hashes[start : start + 500]
        names = {f"h{n}": value for n, value in enumerate(part)}
        listed = ", ".join(f":{name}" for name in names)
        for row in db.execute(
            text(
                "SELECT p.hash, p.kind, p.path, p.node_id, p.message_id FROM path_hash p "
                f"WHERE p.hash IN ({listed}) AND {scope.sql} ORDER BY p.path"
            ),
            {**scope.params, **names},
        ).all():
            places.setdefault(row[0], []).append(row)

    hits: list[Hit] = []
    seen: set[tuple[str, str, str | None]] = set()
    titles: dict[str, str] = {}
    for chunk_id, score in ranked:
        chunk = chunks.get(chunk_id)
        if chunk is None:
            continue
        for _hash, kind, path, node_id, message_id in places.get(chunk[1], []):
            key = (kind, node_id, message_id)
            if key in seen:
                continue
            if kind == INDEXED_FILE:
                # The access post-filter: after scoring, per path.
                if not access.at_least(resolver.level(path), LEVEL_RO):
                    continue
                title = path.rsplit("/", 1)[-1]
            else:
                if node_id not in titles:
                    found = db.get(Chat, node_id)
                    titles[node_id] = found.title if found is not None else ""
                title = titles[node_id]
            seen.add(key)
            hits.append(
                Hit(
                    kind=kind,
                    path=path,
                    node_id=node_id,
                    message_id=message_id,
                    title=title,
                    snippet=_snippet(chunk[2], needle),
                    line_start=int(chunk[3]),
                    line_end=int(chunk[4]),
                    score=score,
                    chunk_id=chunk_id,
                )
            )
            if len(hits) >= limit:
                return hits
    return hits


def search_with(
    db: DBSession,
    user: User,
    chat: Chat | None,
    query: str,
    vector: QueryVector | None,
    *,
    path_prefix: str | None = None,
    kinds: tuple[str, ...] = (INDEXED_FILE,),
    limit: int = 20,
) -> list[Hit]:
    """`search` with the query vector already in hand (or None: keywords only).

    For a caller that must not hold a session across the embedding request: it
    calls `embed_query`, closes, opens again and calls this.
    """
    limit = max(1, min(int(limit), 200))
    wanted = tuple(kind for kind in kinds if kind in KINDS)
    scope = scope_for(user, chat, path_prefix, wanted)
    if scope.empty or not (query or "").strip():
        return []
    resolver = access.resolver(db, user, chat)
    size = limit * CANDIDATES
    hits: list[Hit] = []
    for _ in range(WIDENINGS):
        keyword = keyword_ids(db, query, scope, size)
        meaning = (
            meaning_ids(db, vector.vector, vector.model, vector.dims, scope, size)
            if vector is not None
            else []
        )
        hits = _expand(db, fuse(keyword, meaning), scope, resolver, query, limit)
        if len(hits) >= limit or (len(keyword) < size and len(meaning) < size):
            break
        size *= 4
    return hits


async def search(
    db: DBSession,
    user: User,
    chat: Chat | None,
    query: str,
    *,
    path_prefix: str | None = None,
    kinds: tuple[str, ...] = (INDEXED_FILE,),
    limit: int = 20,
) -> list[Hit]:
    """Search the index as `user` (in `chat`). The contract is the module docstring."""
    vector = await embed_query(db, query)
    return search_with(
        db, user, chat, query, vector, path_prefix=path_prefix, kinds=kinds, limit=limit
    )


__all__ = [
    "BIT_CANDIDATES",
    "EXACT_LIMIT",
    "KINDS",
    "RRF_K",
    "Hit",
    "QueryVector",
    "embed_query",
    "fts_query",
    "fuse",
    "search",
    "search_with",
]
