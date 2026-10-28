"""Turning text into vectors.

The failure this file is mostly about is the quiet one: a response paired with
the wrong input produces a search that works and is wrong, which nothing
downstream can notice.
"""

from __future__ import annotations

import math

import httpx
import pytest

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
