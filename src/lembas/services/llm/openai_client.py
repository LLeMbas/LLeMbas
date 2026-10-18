"""Client for OpenAI-compatible chat endpoints -- and the one place that
dispatches to Anthropic's.

`list_models`, `stream_chat` and `complete` look at `Endpoint.kind` first and
hand an Anthropic connection to `anthropic_client`, which speaks the Messages
API and translates both ways, so every caller keeps building and reading the
OpenAI shapes it always did and none of them needs an `if`.

Deliberately plain httpx rather than the official SDK. The target is not just
api.openai.com but LM Studio, vLLM, llama.cpp, Ollama's compatibility layer,
OpenRouter and anything else exposing /v1 -- and they differ in small ways. A
thin client passes request parameters through untouched and is tolerant about
what comes back, which is exactly what talking to all of them requires.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from lembas.config import settings
from lembas.db.models import Connection
from lembas.services.crypto import decrypt
from lembas.services.llm import sse

log = logging.getLogger(__name__)


class LLMError(Exception):
    """An upstream failure with a message fit to show a user.

    Every failure path in this module raises this rather than letting an httpx
    or JSON exception escape, so callers have exactly one thing to catch and
    the chat UI always has something intelligible to display.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class StreamError(LLMError):
    """An error the server wrote inside a 200 event stream (quirks.json
    `error-in-200-stream`, `model-unloaded`).

    Its own class because the HTTP status was fine: the retry that drops
    `stream_options` after a 400 is about the request, and must not fire for a
    400 the model's server reported while already answering.
    """


def _stream_error(error_object: dict[str, Any]) -> StreamError:
    """The LLMError for an error object found inside a stream."""
    error = error_object.get("error")
    message = sse.error_message(error)
    return StreamError(
        message or "The endpoint reported an error inside the reply.",
        status_code=sse.error_status(error),
    )


@dataclass(frozen=True)
class Endpoint:
    """Everything needed to call a connection, with the key already decrypted.

    A frozen snapshot rather than the ORM object because streaming outlives the
    request that started it, and a detached SQLAlchemy instance is a trap.
    """

    base_url: str
    api_key: str
    extra_headers: dict[str, str]
    name: str = ""
    # The dialect: "openai" or "anthropic". See `Connection.kind`.
    kind: str = "openai"
    # Each model's known output ceiling, as (model id, tokens), for a dialect
    # that requires one on every request (Anthropic's `max_tokens`). A tuple of
    # pairs because the snapshot is frozen.
    output_limits: tuple[tuple[str, int], ...] = ()
    # Each model's context window, as (model id, tokens), likewise.
    context_limits: tuple[tuple[str, int], ...] = ()

    @classmethod
    def from_connection(cls, connection: Connection) -> Endpoint:
        kind = connection.kind or "openai"
        limits: tuple[tuple[str, int], ...] = ()
        contexts: tuple[tuple[str, int], ...] = ()
        if kind == "anthropic":
            # Read only for the dialect that needs them, so an OpenAI connection
            # does not load its model rows on every request.
            limits = tuple(
                (model.model_id, int(model.output_limit))
                for model in connection.models
                if (model.output_limit or 0) > 0
            )
            contexts = tuple(
                (model.model_id, int(model.context_length))
                for model in connection.models
                if (model.context_length or 0) > 0
            )
        return cls(
            base_url=connection.base_url.rstrip("/"),
            api_key=decrypt(connection.api_key_encrypted),
            extra_headers=dict(connection.extra_headers_json or {}),
            name=connection.name,
            kind=kind,
            output_limits=limits,
            context_limits=contexts,
        )

    @property
    def is_anthropic(self) -> bool:
        return self.kind == "anthropic"

    def output_limit(self, model_id: str) -> int:
        """The model's recorded output ceiling, or 0 when none is recorded."""
        return next((limit for name, limit in self.output_limits if name == model_id), 0)

    def context_limit(self, model_id: str) -> int:
        """The model's recorded context window, or 0 when none is recorded."""
        return next((limit for name, limit in self.context_limits if name == model_id), 0)

    def url(self, path: str) -> str:
        # Accept both "http://host:1234" and "http://host:1234/v1" so users do
        # not have to guess which form this expects.
        base = self.base_url
        if not base.endswith("/v1") and "/v1/" not in base:
            base = f"{base}/v1"
        return f"{base}/{path.lstrip('/')}"

    def root_url(self, path: str) -> str:
        """A URL at the *server's* root rather than under `/v1`.

        llama-server's own endpoints -- `/props` is the one that matters here --
        sit beside the OpenAI-compatible surface, not inside it. A base URL may
        be written either way (`http://host:8080` or `.../v1`), so the suffix is
        stripped rather than assumed absent.
        """
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return f"{base}/{path.lstrip('/')}"

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        # Local endpoints frequently need no key at all; sending an empty
        # bearer token makes some of them reject the request outright.
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


async def fetch_chat_template(endpoint: Endpoint) -> str:
    """The model's own Jinja chat template, from llama-server's `/props`.

    The one place the truth about a model's accepted values is actually
    written down: `/props` returns `chat_template` verbatim, and that template
    is what raises when it meets a `reasoning_effort` it does not know.

    Returns "" rather than raising for anything that is not a llama-server --
    OpenAI, vLLM and the rest have no such route, and "this endpoint cannot
    tell us" is a normal answer here, not a failure.
    """
    if endpoint.is_anthropic:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                endpoint.root_url("props"), headers=endpoint.headers()
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, json.JSONDecodeError):
        return ""
    template = payload.get("chat_template") if isinstance(payload, dict) else ""
    return template if isinstance(template, str) else ""


def describe_http_error(exc: httpx.HTTPStatusError) -> str:
    """Turn an upstream error response into something worth reading.

    Public because the audio and search clients talk to the same class of
    server and want the same translation; LLMError stays the one thing a
    caller has to catch.

    Providers put the useful part in wildly different places, so try the common
    shapes before falling back to the raw body.
    """
    status = exc.response.status_code
    detail = ""
    try:
        payload = exc.response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                detail = error.get("message", "")
            elif isinstance(error, str):
                detail = error
            detail = detail or payload.get("message", "") or payload.get("detail", "")
    except (ValueError, json.JSONDecodeError):
        detail = exc.response.text[:300]

    friendly = {
        401: "The API key was rejected.",
        403: "The API key is not permitted to use this model.",
        404: "The endpoint or model was not found.",
        429: "Rate limited by the provider.",
    }.get(status)

    if friendly and detail:
        return f"{friendly} {detail}"
    return friendly or detail or f"The endpoint returned HTTP {status}."


def require_openai(endpoint: Endpoint, what: str) -> None:
    """Refuse a job only OpenAI's dialect has: embeddings, audio, images.

    Anthropic's API has no such routes, so an Anthropic connection that reaches
    one of them -- a setting made before the connection changed type, say --
    gets a sentence saying so rather than a 404 from somebody else's server.
    """
    if endpoint.is_anthropic:
        raise LLMError(
            f"{endpoint.name or endpoint.base_url} is an Anthropic connection, and "
            f"Anthropic's API has no {what}. Choose a model on an OpenAI-compatible "
            "connection for it."
        )


def wrap_transport_error(exc: httpx.RequestError, endpoint: Endpoint) -> LLMError:
    if isinstance(exc, httpx.ConnectError):
        return LLMError(
            f"Could not reach {endpoint.base_url}. Is the endpoint running and "
            f"the URL correct?"
        )
    if isinstance(exc, httpx.TimeoutException):
        return LLMError(
            f"{endpoint.base_url} did not respond within "
            f"{settings.request_timeout:.0f}s."
        )
    return LLMError(f"Could not reach {endpoint.base_url}: {exc}")


async def list_models(endpoint: Endpoint) -> list[dict[str, Any]]:
    """Fetch the models a connection advertises via GET /v1/models."""
    if endpoint.is_anthropic:
        from lembas.services.llm import anthropic_client

        return await anthropic_client.list_models(endpoint)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint.url("models"), headers=endpoint.headers())
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise LLMError(describe_http_error(exc), status_code=exc.response.status_code) from exc
    except httpx.RequestError as exc:
        raise wrap_transport_error(exc, endpoint) from exc
    except ValueError as exc:
        raise LLMError("The endpoint returned a response that was not JSON.") from exc

    # The spec says {"data": [...]}, but some servers return a bare list.
    entries = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise LLMError("The endpoint's model list was not in the expected format.")

    models = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id"):
            models.append(entry)
        elif isinstance(entry, str):
            models.append({"id": entry})
    return models


# Where the runners that bother to advertise a context length put it. There is
# no standard field, so this is a list of what the common ones actually emit.
_CONTEXT_KEYS = ("context_length", "max_model_len", "context_window", "max_context_length")

# Below the first, the number is not a context length; above the second it is a
# typo or a different unit. Either way, better to record nothing than a wrong
# figure a percentage would then be computed from.
MIN_CONTEXT = 256
MAX_CONTEXT = 10_000_000


def context_from(entry: dict[str, Any]) -> int:
    """A model's context length as advertised by /v1/models, or 0 if it is not.

    Strings are accepted because some servers quote the number, but only when
    they are digits alone -- "8192 tokens" is a label, not a measurement.
    """
    candidates = [entry.get(key) for key in _CONTEXT_KEYS]
    meta = entry.get("meta")
    if isinstance(meta, dict):
        candidates += [meta.get("n_ctx"), *(meta.get(key) for key in _CONTEXT_KEYS)]

    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value.isdigit():
                continue
            value = int(value)
        if isinstance(value, int) and MIN_CONTEXT <= value <= MAX_CONTEXT:
            return value
    return 0


# Endpoints that rejected `stream_options`, so it is asked for once per base URL
# per process and then never again. Not persisted: it is a property of whatever
# is running there now, and a restart is the right time to find out afresh.
_NO_STREAM_OPTIONS: set[str] = set()


async def stream_chat(
    endpoint: Endpoint,
    payload: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Stream a chat completion, yielding each parsed SSE data object.

    Yields the raw upstream chunks; interpreting them is the caller's job. The
    terminating "[DONE]" sentinel is consumed here and not yielded.

    `stream_options` asks for the final usage chunk, which is the only way to
    learn what a streamed reply actually cost. Not every server implements it,
    and an unknown key is a 400 from some of them -- the same hazard as sending
    a `tools` array to an endpoint without support. So it is asked for once,
    and an endpoint that refuses is remembered and never asked again. Retrying
    is safe because the status is checked before a single line is read: nothing
    has been yielded, so there is nothing to duplicate.
    """
    if endpoint.is_anthropic:
        from lembas.services.llm import anthropic_client

        async for chunk in anthropic_client.stream_chat(endpoint, payload):
            yield chunk
        return

    wants_usage = endpoint.base_url not in _NO_STREAM_OPTIONS
    retried_server_error = False

    while True:
        sent = False
        try:
            async for chunk in _stream_once(endpoint, payload, usage=wants_usage):
                sent = True
                yield chunk
            return
        except LLMError as exc:
            # Once a chunk has reached the caller the reply is under way, and a
            # second request would duplicate what was already shown.
            if sent:
                raise
            if wants_usage and exc.status_code in (400, 422) and not isinstance(exc, StreamError):
                _NO_STREAM_OPTIONS.add(endpoint.base_url)
                log.info(
                    "%s rejected stream_options; token counts will be estimated there",
                    endpoint.base_url,
                )
                wants_usage = False
                continue
            # A server error before anything was streamed is retried once: often
            # the model's own output failing the server's parser, or the model
            # swapped out from under the request by another client (llama-swap's
            # "model unloaded" frame, quirks.json `model-unloaded`). Sampling
            # differs on a second go.
            if (exc.status_code or 0) >= 500 and not retried_server_error:
                retried_server_error = True
                log.info("%s: server error before any output, retrying once: %s",
                         endpoint.base_url, exc.message)
                continue
            raise


async def _stream_once(
    endpoint: Endpoint,
    payload: dict[str, Any],
    *,
    usage: bool,
) -> AsyncIterator[dict[str, Any]]:
    body = {**payload, "stream": True}
    if usage:
        body["stream_options"] = {"include_usage": True}

    try:
        async with (
            httpx.AsyncClient(timeout=settings.request_timeout) as client,
            client.stream(
                "POST",
                endpoint.url("chat/completions"),
                headers=endpoint.headers(),
                json=body,
            ) as response,
        ):
            if response.status_code >= 400:
                # The body has not been read yet on a streaming response, and
                # the error detail is in it.
                await response.aread()
                response.raise_for_status()

            async for event, data in sse.iter_sse(response.aiter_lines()):
                if event == sse.COMMENT:
                    continue  # keep-alive
                if event == sse.ERROR:
                    # llama.cpp's mid-stream failure: a bare error line inside a
                    # 200, often the stream's unterminated last line.
                    raise _stream_error(sse.error_object(data))
                data = data.strip()
                if not data:
                    continue
                if data == sse.DONE:
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    # A malformed frame is not worth killing a reply over.
                    log.warning("skipping unparseable SSE frame: %.120s", data)
                    continue
                if isinstance(chunk, dict) and chunk.get("error"):
                    # An error framed as data (quirks.json `error-in-200-stream`):
                    # the error it is, not a chunk with nothing in it.
                    raise _stream_error(chunk)
                yield chunk

    except httpx.HTTPStatusError as exc:
        raise LLMError(describe_http_error(exc), status_code=exc.response.status_code) from exc
    except httpx.RequestError as exc:
        raise wrap_transport_error(exc, endpoint) from exc


async def complete(endpoint: Endpoint, payload: dict[str, Any]) -> str:
    """Non-streaming completion. Used for short internal calls like auto-titling."""
    if endpoint.is_anthropic:
        from lembas.services.llm import anthropic_client

        return await anthropic_client.complete(endpoint, payload)
    body = {**payload, "stream": False}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                endpoint.url("chat/completions"), headers=endpoint.headers(), json=body
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise LLMError(describe_http_error(exc), status_code=exc.response.status_code) from exc
    except httpx.RequestError as exc:
        raise wrap_transport_error(exc, endpoint) from exc
    except ValueError as exc:
        raise LLMError("The endpoint returned a response that was not JSON.") from exc

    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("The endpoint returned no completion.") from exc


def delta_reasoning(chunk: dict[str, Any]) -> str:
    """Pull a reasoning delta out of one streamed chunk.

    Providers disagree on the field name -- llama.cpp, llama-swap and vLLM use
    ``reasoning_content``, some others just ``reasoning`` -- so both are read.
    Models that emit ``<think>`` tags inline in ``content`` instead are handled
    by lembas.services.reasoning.
    """
    try:
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        for field in ("reasoning_content", "reasoning"):
            value = delta.get(field)
            if isinstance(value, str) and value:
                return value
        return ""
    except (AttributeError, TypeError):
        return ""


def delta_tool_calls(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull tool-call fragments out of one streamed chunk.

    Each entry carries an ``index`` and, across chunks, a name that arrives
    once and an ``arguments`` string that arrives in pieces. Reassembling them
    is `ToolCallAccumulator`'s job; this only extracts.
    """
    try:
        choices = chunk.get("choices") or []
        if not choices:
            return []
        calls = (choices[0].get("delta") or {}).get("tool_calls")
        return calls if isinstance(calls, list) else []
    except (AttributeError, TypeError):
        return []


class ToolCallAccumulator:
    """Reassembles tool calls arriving as streamed fragments.

    An endpoint sends ``delta.tool_calls`` as a list of partial objects: the id
    and the function name arrive once, and ``arguments`` arrives as a string
    split across however many chunks the tokeniser produced. Entries are keyed
    by ``index`` because that is the only field guaranteed on every fragment --
    the id is absent from continuations, and matching on name breaks the moment
    a model calls the same tool twice in one turn.

    Some llama.cpp builds omit the index (quirks.json `tool-call-without-index`,
    conformance `toolcall_index.json`): such a fragment continues the call
    started last, and the first fragment of a stream without one starts call 0.
    Arguments are concatenated as text, never parsed on the way.
    """

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, Any]] = {}
        self._last = 0

    def feed(self, fragments: list[dict[str, Any]]) -> None:
        if not isinstance(fragments, list):
            return
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            index = fragment.get("index")
            if not isinstance(index, int) or isinstance(index, bool):
                index = self._last
            self._last = index
            call = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})

            if fragment.get("id"):
                call["id"] = str(fragment["id"])
            function = fragment.get("function") or {}
            if isinstance(function, dict):
                if function.get("name"):
                    call["name"] = str(function["name"])
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    call["arguments"] += arguments
                elif isinstance(arguments, dict):
                    # A server that sends the arguments already parsed.
                    call["arguments"] += json.dumps(arguments, ensure_ascii=False)

    @property
    def calls(self) -> list[dict[str, Any]]:
        """Completed calls, in the order the endpoint indexed them."""
        return [
            {
                # An id is required when the results are sent back, and not
                # every server supplies one.
                "id": call["id"] or f"call_{index}",
                "name": call["name"],
                "arguments": call["arguments"],
            }
            for index, call in sorted(self._calls.items())
            if call["name"]
        ]

    def __bool__(self) -> bool:
        return bool(self.calls)


def assistant_blocks(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """The assistant turn in its endpoint's own words, when a chunk carries it.

    Only the Anthropic translation sends one: the reply's content blocks
    in order -- thinking with its `signature`, text, tool use -- once, at the end
    of the message. A tool loop echoes them back on the assistant turn under the
    same key, because Anthropic wants a turn's thinking returned unchanged with
    its tool calls. Every other endpoint's chunks carry nothing here.
    """
    try:
        blocks = chunk.get("assistant_blocks")
    except AttributeError:
        return []
    return [b for b in blocks if isinstance(b, dict)] if isinstance(blocks, list) else []


def finish_reason(chunk: dict[str, Any]) -> str:
    """Why the model stopped, when the chunk says so.

    ``tool_calls`` here is the signal that the reply is not an answer but a
    request to run something and come back. Some servers send ``stop`` even
    when they emitted tool calls, so the accumulator's contents are the real
    authority and this is only a hint.
    """
    try:
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("finish_reason") or ""
    except (AttributeError, TypeError):
        return ""


def chunk_usage(chunk: dict[str, Any]) -> dict[str, int] | None:
    """Token counts from a usage chunk, or None if this is not one.

    A usage chunk carries `choices: []`, which is exactly the shape delta_text,
    delta_reasoning, delta_tool_calls and finish_reason all return early on --
    they have always tolerated it, so nothing else needs to change to let one
    through.

    Fields are read defensively because "the endpoint returned something odd"
    must never be the reason a reply fails; a bad shape simply means no counts.
    """
    try:
        raw = chunk.get("usage")
    except AttributeError:
        return None
    if not isinstance(raw, dict):
        return None

    counts: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value >= 0:
            counts[key] = int(value)

    # Some servers send a usage object of zeros on every chunk and the real
    # numbers only at the end. All-zero is indistinguishable from that, and
    # treating it as an answer would freeze the count at nothing.
    if not counts or not any(counts.values()):
        return None
    counts.setdefault(
        "total_tokens", counts.get("prompt_tokens", 0) + counts.get("completion_tokens", 0)
    )
    # How much of the prompt the server answered from its cache, which credits
    # price apart: OpenAI's `prompt_tokens_details.cached_tokens`, and
    # the same figure some servers put at the top level. llama.cpp says it in
    # `timings.cache_n` beside the usage.
    details = raw.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else raw.get("cached_tokens")
    if cached is None:
        timings = chunk.get("timings") if isinstance(chunk, dict) else None
        cached = timings.get("cache_n") if isinstance(timings, dict) else None
    if isinstance(cached, (int, float)) and not isinstance(cached, bool) and cached > 0:
        counts["cached_tokens"] = int(cached)
    # Prompt tokens written to the cache (Anthropic), which cost more
    # than ordinary input; part of `prompt_tokens`, as `cached_tokens` is.
    written = details.get("cache_write_tokens") if isinstance(details, dict) else None
    if written is None:
        written = raw.get("cache_write_tokens")
    if isinstance(written, (int, float)) and not isinstance(written, bool) and written > 0:
        counts["cache_write_tokens"] = int(written)
    return counts


def delta_text(chunk: dict[str, Any]) -> str:
    """Pull the text out of one streamed chunk, tolerating provider variation."""
    try:
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            return content
        # Some providers send content as a list of typed parts even in deltas.
        if isinstance(content, list):
            return "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return ""
    except (AttributeError, TypeError):
        return ""
