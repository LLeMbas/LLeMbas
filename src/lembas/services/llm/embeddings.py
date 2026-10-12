"""Turning text into vectors, against an OpenAI-shaped `/v1/embeddings`.

The same reasoning as the chat and audio clients: plain httpx rather than an
SDK, because the target is llama.cpp, Ollama, LM Studio, Infinity or vLLM at
least as often as it is api.openai.com. They agree about the request and
disagree politely about the response, so this is tolerant about what comes back
and strict about what it hands on.

**Batched, because the cost is the round trip.** A hundred chunks one at a time
against a local endpoint is a hundred model loads' worth of latency for work
that fits in six requests. The batch size is a setting, because "how many at
once" is a property of the far side rather than of this code.

**Dimensions are discovered, never declared.** Nobody should have to look up
that bge-m3 is 1024 and nomic-embed-text is 768, and an instance that changes
model must not silently compare vectors from two different spaces --
`services/library/indexing.py` records the width beside every vector and
refuses to score across a mismatch.

Normalisation happens here, once, on the way out. Cosine similarity between two
unit vectors is their dot product, so normalising at write time turns every
later comparison into a multiply-and-add instead of two square roots per pair.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import httpx

from lembas.services.llm.openai_client import (
    Endpoint,
    LLMError,
    describe_http_error,
    require_openai,
    wrap_transport_error,
)

log = logging.getLogger(__name__)

# Longer than a chat request's, because a batch of sixteen chunks against a
# cold local endpoint includes loading the model.
TIMEOUT = 120.0


def normalise(vector: list[float]) -> list[float]:
    """A unit vector, or the input unchanged when it has no length.

    A zero vector is what an endpoint returns for empty input, and dividing by
    its norm is the one arithmetic error this path can make. It is left as it
    is: scoring it against anything gives zero, which is the honest answer.
    """
    length = math.sqrt(sum(value * value for value in vector))
    if length <= 0:
        return vector
    return [value / length for value in vector]


def _vectors_in(payload: Any) -> list[list[float]]:
    """The embeddings out of a response, whatever shape it arrived in.

    OpenAI's own answer is `{"data": [{"embedding": [...], "index": 0}]}`, and
    the index is honoured rather than assumed: nothing in the specification
    promises the order, and a provider that sorts differently would silently
    pair every chunk with somebody else's vector — which produces a search that
    works and is wrong, the worst failure this whole feature can have.
    """
    if not isinstance(payload, dict):
        raise LLMError("The embedding endpoint returned something unreadable.")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise LLMError("The embedding endpoint returned no vectors.")

    ordered: list[tuple[int, list[float]]] = []
    for position, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise LLMError("The embedding endpoint returned no vectors.")
        raw = entry.get("embedding")
        if not isinstance(raw, list) or not raw:
            raise LLMError("The embedding endpoint returned an empty vector.")
        index = entry.get("index")
        at = int(index) if isinstance(index, int) else position
        ordered.append((at, [float(value) for value in raw]))
    ordered.sort(key=lambda pair: pair[0])
    return [vector for _, vector in ordered]


async def embed(
    endpoint: Endpoint, model_id: str, texts: list[str], *, timeout: float = TIMEOUT
) -> list[list[float]]:
    """One request. Returns a unit vector per input, in the order given.

    Raises `LLMError` for everything -- a missing model, an endpoint that does
    not implement embeddings at all, a transport failure -- because every caller
    treats them the same way: the index is left as it was and the search falls
    back to keywords. Nothing here is worth a partial answer.
    """
    if not texts:
        return []
    require_openai(endpoint, "embeddings")
    body = {"model": model_id, "input": texts}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint.url("/embeddings"), headers=endpoint.headers(), json=body
            )
            # raise_for_status, then translate. `describe_http_error` takes the
            # exception rather than the response, which is what every other
            # client here hands it.
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise LLMError(describe_http_error(exc)) from exc
    except httpx.HTTPError as exc:
        raise wrap_transport_error(exc, endpoint) from exc
    except ValueError as exc:
        raise LLMError("The embedding endpoint did not return JSON.") from exc

    vectors = _vectors_in(payload)
    if len(vectors) != len(texts):
        # Not recoverable by guessing. A response with fewer vectors than inputs
        # would pair chunk three's text with chunk four's vector from there on,
        # for the life of the index.
        raise LLMError(
            f"Asked for {len(texts)} embeddings and got {len(vectors)}."
        )
    widths = {len(vector) for vector in vectors}
    if len(widths) != 1:
        raise LLMError("The embedding endpoint returned vectors of different widths.")
    return [normalise(vector) for vector in vectors]


__all__ = ["TIMEOUT", "embed", "normalise"]
