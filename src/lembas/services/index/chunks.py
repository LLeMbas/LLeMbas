"""Cutting text into pieces small enough to embed, measured in tokens.

The rules are the harness spec's (`filesystem.md`, *Indexing, by tokens*;
`conformance/chunk_tokens.json`), shared with the LLeMbas CLI.

## Why tokens and not characters

An embedder refuses input past its context -- 512 tokens is common -- and dense
text (another language than English, code, hashes) holds far more tokens per
character than prose. 1.x cut at 1,200 characters, which a page of Slovak or a
lock file passes, and the embedder then refused the whole document. So a piece
is at most `SIZE` tokens (room for the embedder's own markers under 512) and
repeats the last `OVERLAP` tokens of the one before it.

## How it cuts

The window is the longest stretch of at most `size` tokens from where the piece
starts. The cut is just after a paragraph break, else a line break, else a
sentence's ". ", looked for in the window's **last third** (by characters) --
a break near the *start* would make a piece a fraction of the size, which is
how a long document turns into hundreds of tiny rows that each match nothing --
else at the end of the window's last token. The next piece begins at the start
of the token `overlap` tokens before the cut, so a sentence that straddles a
cut is whole in one of the two pieces. CRLF reads as LF, the text and every
piece are trimmed, pieces under 40 characters are dropped (the embedding of six
words is mostly noise), and a text that fits is one piece.

## Tokens, three ways

A tokenizer here is a function from text to the character spans of its tokens.
`words` is the spec's stand-in (a run of non-whitespace), for the conformance
cases. `estimate` stands in for an embedder that cannot say how it counts:
`ceil(UTF-8 bytes / 2)` tokens -- an estimate, not a bound, never measured
against a real tokenizer. And `spans_from_pieces` turns an embedder's own
token pieces (llama.cpp's `/tokenize`) into spans where the server offers them.
**A piece the embedder still refuses for its length is split in two**
(`halve`) and both halves are embedded; nothing is dropped.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

SIZE = 480
OVERLAP = 64
MIN_SIZE = 16
MIN_CHUNK_CHARS = 40

Span = tuple[int, int]
Tokenizer = Callable[[str], list[Span]]

_WORD = re.compile(r"\S+")


def words(text: str) -> list[Span]:
    """The spec's stand-in tokenizer: a token is a run of non-whitespace."""
    return [m.span() for m in _WORD.finditer(text)]


def estimate(text: str) -> list[Span]:
    """`ceil(UTF-8 bytes / 2)` tokens, as spans that never split a character.

    Characters are grouped two bytes at a time; a character of three or four
    bytes is a token of its own and counts as two (an empty span after it).
    A single byte left before a multi-byte character is a token too, so the
    count is the estimate or a little over it -- the safe side for an embedder
    that refuses long input -- and every cut still lands between characters.
    """
    spans: list[Span] = []
    start: int | None = None
    held = 0
    for i, char in enumerate(text):
        size = len(char.encode("utf-8", "surrogatepass"))
        if size >= 2:
            if start is not None:
                spans.append((start, i))
                start, held = None, 0
            spans.append((i, i + 1))
            spans.extend((i + 1, i + 1) for _ in range((size + 1) // 2 - 1))
            continue
        if start is None:
            start, held = i, 0
        held += size
        if held >= 2:
            spans.append((start, i + 1))
            start, held = None, 0
    if start is not None:
        spans.append((start, len(text)))
    return spans


def count_estimate(text: str) -> int:
    return -(-len(text.encode("utf-8", "surrogatepass")) // 2)


def spans_from_pieces(text: str, pieces: list[str]) -> list[Span] | None:
    """An embedder's token pieces as spans of `text`, or None if they do not add up.

    Tokenizers mark spaces their own way ("▁", "Ġ") and some pieces are bytes of
    a character rather than characters, so the pieces are matched against the
    text loosely: each piece's visible characters are found in order. Anything
    that does not line up answers None, and the caller falls back to `estimate`.
    """
    spans: list[Span] = []
    at = 0
    for piece in pieces:
        visible = piece.replace("▁", " ").replace("Ġ", " ").replace("Ċ", "\n")
        core = visible.strip()
        if not core:
            spans.append((at, at))
            continue
        found = text.find(core, at)
        if found < 0 or found - at > 8:
            return None
        spans.append((found, found + len(core)))
        at = found + len(core)
    return spans


@dataclass(frozen=True)
class Piece:
    """One piece: its text, its token count, and where it is in the text."""

    text: str
    tokens: int
    start: int  # character offsets in the normalised text
    end: int


def normalise(text: str) -> str:
    return (text or "").replace("\r\n", "\n")


def _boundary(body: str, start: int, end: int) -> int:
    floor = start + (end - start) * 2 // 3
    for marker in ("\n\n", "\n", ". "):
        found = body.rfind(marker, floor, end)
        if found > floor:
            return found + len(marker)
    return end


def split(
    text: str,
    tokenizer: Tokenizer = estimate,
    *,
    size: int = SIZE,
    overlap: int = OVERLAP,
) -> list[Piece]:
    """`text` as pieces of at most `size` tokens (see the module docstring)."""
    normal = normalise(text)
    lead = len(normal) - len(normal.lstrip())
    body = normal.strip()
    if not body:
        return []
    size = max(MIN_SIZE, int(size))
    overlap = max(0, min(int(overlap), size // 2))
    spans = tokenizer(body)
    n = len(spans)
    if n <= size:
        return [Piece(body, n, lead, lead + len(body))]

    pieces: list[Piece] = []
    first = 0  # index of the token the piece starts at
    while first < n:
        start = spans[first][0]
        last_window = first + size >= n
        end = len(body) if last_window else _boundary(body, start, spans[first + size - 1][1])
        raw = body[start:end]
        piece = raw.strip()
        # The tokens before the cut: those that start before it.
        before = n if last_window else first
        while before < n and spans[before][0] < end:
            before += 1
        if len(piece) >= MIN_CHUNK_CHARS:
            offset = start + (len(raw) - len(raw.lstrip()))
            pieces.append(Piece(piece, before - first, lead + offset, lead + offset + len(piece)))
        if last_window:
            break
        first = max(before - overlap, first + 1)
    return pieces


def halve(text: str) -> tuple[str, str] | None:
    """A piece the embedder refused, cut in two at the best place near its middle.

    A paragraph break, else a line break, else a sentence's ". ", else a space,
    whichever is nearest the middle; else the middle itself. None for a piece
    too short to cut.
    """
    body = text.strip()
    if len(body) < 2:
        return None
    middle = len(body) // 2
    window = max(1, len(body) // 4)
    for marker in ("\n\n", "\n", ". ", " "):
        best = -1
        for match in re.finditer(re.escape(marker), body):
            cut = match.end()
            if abs(cut - middle) <= window and (best < 0 or abs(cut - middle) < abs(best - middle)):
                best = cut
        if 0 < best < len(body):
            left, right = body[:best].strip(), body[best:].strip()
            if left and right:
                return left, right
    return body[:middle].strip() or body[:middle], body[middle:].strip() or body[middle:]


def line_range(text: str, start: int, end: int) -> tuple[int, int]:
    """1-based, inclusive line numbers of `text[start:end]`."""
    first = text.count("\n", 0, start) + 1
    return first, first + text.count("\n", start, max(start, end))


__all__ = [
    "MIN_CHUNK_CHARS",
    "OVERLAP",
    "SIZE",
    "Piece",
    "Tokenizer",
    "count_estimate",
    "estimate",
    "halve",
    "line_range",
    "normalise",
    "split",
    "spans_from_pieces",
    "words",
]
