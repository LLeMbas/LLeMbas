"""Splitting a record into pieces small enough to embed, and packing vectors.

One implementation, used by documents, notes, skills and reports. Three would
drift, and drift here is invisible: a splitter that behaves differently for
notes than for documents produces a search that works and ranks wrongly.

## How it splits

On **paragraph boundaries first**, falling back to lines and then to a hard cut,
because a chunk that ends mid-sentence is one whose embedding is about half a
thought. The overlap carries the tail of the previous chunk into the next, so a
sentence that straddles a boundary is whole in one of them.

Characters rather than tokens throughout. The count has to be made without
asking the endpoint -- `services/tokens.py` already establishes four characters
to a token as this codebase's estimate, and being 20% out about a chunk size is
a slightly different chunk, not a wrong one.

## Packing

float32, little-endian. A 1024-dimension vector is 4KB packed and about 20KB as
JSON text, and every one of them is read on every semantic search.
"""

from __future__ import annotations

import hashlib
import struct

# Below this a piece is not worth a row: the embedding of six words is mostly
# noise, and a search that returns "and the following:" as its best hit is worse
# than one that returns nothing.
MIN_CHUNK_CHARS = 40


def split(text: str, *, size: int = 1200, overlap: int = 150) -> list[str]:
    """A record's text as pieces of roughly `size` characters.

    `overlap` is how much of the previous piece rides along with the next. It is
    clamped to half the size here as well as in the settings accessor, because
    an overlap at or past the size means every piece starts where the last one
    did and the loop never advances -- a hang rather than a bad index, so it is
    refused in both places rather than in the more convenient one.
    """
    # CRLF read as LF (the harness spec's rule, shared with the LLeMbas CLI): a Windows
    # file's breaks are otherwise "\r\n\r\n", which the paragraph search never meets.
    body = (text or "").replace("\r\n", "\n").strip()
    if not body:
        return []
    size = max(200, int(size))
    overlap = max(0, min(int(overlap), size // 2))
    if len(body) <= size:
        return [body]

    pieces: list[str] = []
    start = 0
    while start < len(body):
        end = min(start + size, len(body))
        if end < len(body):
            end = _boundary(body, start, end)
        piece = body[start:end].strip()
        if len(piece) >= MIN_CHUNK_CHARS:
            pieces.append(piece)
        if end >= len(body):
            break
        start = max(end - overlap, start + 1)
    return pieces


def _boundary(body: str, start: int, end: int) -> int:
    """Where to cut, preferring a paragraph break and then a line break.

    Searched backwards from the hard limit, and only within the last third of
    the piece: a paragraph break near the *start* would produce a chunk a
    fraction of the size, which is how a long document turns into hundreds of
    tiny rows that each match nothing.
    """
    floor = start + (end - start) * 2 // 3
    for marker in ("\n\n", "\n", ". "):
        found = body.rfind(marker, floor, end)
        if found > floor:
            return found + len(marker)
    return end


def digest(text: str) -> str:
    """A hash of what a chunk set was built from.

    What makes re-indexing an unchanged record free, and what makes "is this
    index current?" answerable without embedding anything. sha256 rather than
    md5 for no reason beyond having no reason to prefer md5; both are being used
    as a change detector rather than against an adversary.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob: bytes, dims: int) -> list[float]:
    """A stored vector, or an empty list if the row does not add up.

    Length is checked against the declared width rather than inferred from it: a
    truncated BLOB would otherwise unpack into a shorter vector and score
    against a query happily, which is a wrong answer rather than a missing one.
    """
    if dims <= 0 or len(blob) != dims * 4:
        return []
    return list(struct.unpack(f"<{dims}f", blob))


def dot(left: list[float], right: list[float]) -> float:
    """Cosine similarity, given that both sides are already unit vectors.

    Normalisation happens once, at write time, in `llm/embeddings.py` -- so
    every comparison here is a multiply-and-add rather than two square roots per
    pair. A width mismatch scores zero rather than raising: it means the vectors
    came from two different models, and the honest answer to "how similar are
    these?" across two spaces is "this tells you nothing".
    """
    if len(left) != len(right) or not left:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


__all__ = ["MIN_CHUNK_CHARS", "digest", "dot", "pack", "split", "unpack"]
