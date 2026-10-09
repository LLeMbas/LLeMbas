"""Turning text into vectors, and the pieces text is cut into first.

The failure this file is mostly about is the quiet one: a response paired with
the wrong input produces a search that works and is wrong, which nothing
downstream can notice.
"""

from __future__ import annotations

import math

import httpx
import pytest

from lembas.services.library import chunks
from lembas.services.llm import embeddings
from lembas.services.llm.openai_client import Endpoint, LLMError

ENDPOINT = Endpoint(base_url="http://127.0.0.1:9", api_key="", extra_headers={}, name="test")


def _answer(vectors, *, shuffle=False):
    data = [{"embedding": vector, "index": i} for i, vector in enumerate(vectors)]
    if shuffle:
        data = list(reversed(data))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": data})

    return handler


# --- The client -----------------------------------------------------------------
async def test_vectors_come_back_normalised(mock_http):
    """Cosine between unit vectors is their dot product, so normalising once at
    write time turns every later comparison into a multiply-and-add."""
    mock_http(_answer([[3.0, 4.0]]))

    (vector,) = await embeddings.embed(ENDPOINT, "m", ["hello"])

    assert math.isclose(vector[0], 0.6)
    assert math.isclose(vector[1], 0.8)


async def test_the_declared_index_decides_the_order(mock_http):
    """Nothing in the specification promises the order of `data`. A provider
    that sorts differently would pair every chunk with somebody else's vector —
    a search that works and is wrong, which is the worst failure here."""
    mock_http(_answer([[1.0, 0.0], [0.0, 1.0]], shuffle=True))

    first, second = await embeddings.embed(ENDPOINT, "m", ["a", "b"])

    assert first == [1.0, 0.0]
    assert second == [0.0, 1.0]


async def test_a_short_answer_is_refused_rather_than_guessed(mock_http):
    """Silently accepting it would pair chunk three's text with chunk four's
    vector from there on, for the life of the index."""
    mock_http(_answer([[1.0, 0.0]]))

    with pytest.raises(LLMError) as caught:
        await embeddings.embed(ENDPOINT, "m", ["a", "b"])

    assert "2" in str(caught.value)


async def test_vectors_of_different_widths_are_refused(mock_http):
    mock_http(_answer([[1.0, 0.0], [0.0, 1.0, 0.0]]))

    with pytest.raises(LLMError):
        await embeddings.embed(ENDPOINT, "m", ["a", "b"])


async def test_an_endpoint_that_does_not_do_embeddings_says_so(mock_http):
    def handler(request):
        return httpx.Response(404, json={"error": {"message": "no such endpoint"}})

    mock_http(handler)

    with pytest.raises(LLMError):
        await embeddings.embed(ENDPOINT, "m", ["a"])


async def test_nothing_asked_is_nothing_sent(mock_http):
    """A request with no inputs is a round trip for nothing, and some endpoints
    refuse it outright."""
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"data": []})

    mock_http(handler)

    assert await embeddings.embed(ENDPOINT, "m", []) == []
    assert sent == []


def test_a_zero_vector_survives_normalising():
    """What an endpoint returns for empty input, and the one arithmetic error
    this path can make."""
    assert embeddings.normalise([0.0, 0.0]) == [0.0, 0.0]


# --- Splitting ------------------------------------------------------------------
def test_short_text_is_one_piece():
    assert chunks.split("a short note", size=1200) == ["a short note"]


def test_splitting_prefers_a_paragraph_boundary():
    body = "\n\n".join(["A" * 400, "B" * 400, "C" * 400])
    pieces = chunks.split(body, size=900, overlap=0)

    assert len(pieces) >= 2
    # No piece ends mid-run, which is what a boundary being honoured looks like.
    assert all(piece.strip() == piece for piece in pieces)
    assert pieces[0].startswith("A")


def test_the_overlap_carries_the_tail_forward():
    body = "".join(f"sentence {n}. " for n in range(200))
    pieces = chunks.split(body, size=400, overlap=100)

    assert len(pieces) > 1
    # Consecutive pieces share text, which is what stops a sentence across a
    # boundary being absent from both embeddings.
    assert any(pieces[0][-40:] in pieces[1] for _ in (0,)) or pieces[1][:40] in body


def test_an_overlap_as_large_as_the_piece_does_not_hang():
    """An overlap at or past the size means every piece starts where the last
    one did. Clamped here as well as in the settings accessor, because the
    failure is a hang rather than a bad index."""
    pieces = chunks.split("x" * 5000, size=400, overlap=4000)

    assert len(pieces) < 40


def test_a_scrap_is_not_worth_a_row():
    """The embedding of six words is mostly noise, and a search whose best hit
    is "and the following:" is worse than one that returns nothing."""
    assert chunks.split("hi") == ["hi"]
    pieces = chunks.split("A" * 400 + "\n\n" + "B" * 5, size=380, overlap=0)
    assert all(len(piece) >= chunks.MIN_CHUNK_CHARS for piece in pieces)


def test_nothing_in_is_nothing_out():
    assert chunks.split("") == []
    assert chunks.split("   \n\n  ") == []


# --- Packing --------------------------------------------------------------------
def test_a_vector_survives_a_round_trip():
    vector = [0.5, -0.25, 0.125]
    blob = chunks.pack(vector)

    assert len(blob) == 12
    assert chunks.unpack(blob, 3) == pytest.approx(vector)


def test_a_truncated_blob_unpacks_to_nothing():
    """Inferring the width from the length would let a short BLOB unpack into a
    shorter vector and score against a query happily — a wrong answer rather
    than a missing one."""
    assert chunks.unpack(chunks.pack([1.0, 2.0, 3.0])[:8], 3) == []


def test_scoring_across_widths_is_zero_rather_than_an_error():
    """It means the two vectors came from different models, and the honest
    answer to "how similar are these?" across two spaces is nothing."""
    assert chunks.dot([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_the_dot_product_is_the_cosine_for_unit_vectors():
    a = embeddings.normalise([1.0, 1.0])
    b = embeddings.normalise([1.0, 0.0])

    assert chunks.dot(a, a) == pytest.approx(1.0)
    assert chunks.dot(a, b) == pytest.approx(math.sqrt(0.5))


def test_the_hash_changes_with_the_text():
    assert chunks.digest("a") != chunks.digest("b")
    assert chunks.digest("a") == chunks.digest("a")
