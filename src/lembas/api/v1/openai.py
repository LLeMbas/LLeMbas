"""An OpenAI-compatible API over the holder's own models.

`/v1/models`, `/v1/chat/completions` and `/v1/embeddings`, for a program holding
a token with the `models` scope -- a script, or the LLeMbas CLI logged in to
this instance. The holder sees exactly the models they could start a chat with
(permissions, groups and public flags applied), and every call is counted in
their usage and bound by their limits, as a reply in the web UI is.

**Stateless.** Nothing is kept: no chat, no message, no transcript. The request
goes to the model's connection with the connection's own key, which never leaves
this server, and the answer comes back as the upstream wrote it -- a streamed
reply is relayed frame by frame, not re-assembled.

**`/v1/models` carries the harness spec's model metadata** (`harness_spec/models/
schema.json`): context, efforts, vision, tools, notes and capacity, so a client
can configure itself from this list rather than from a hand-written file.

**Every AI service is listed**: each entry's `kind` says what it is --
`chat` and `embedding` for the instance's models (`Model.kind`; a model
whose kind nobody has set is `chat`, and is still taken by
`/v1/embeddings`), and `stt`, `tts` and `image` for the speech and picture
services configured under Admin, listed as `stt/<model>`, `tts/<model>` and
`comfyui/<checkpoint>` where they are on and this person may use them. Which
API a chat model is reached through behind this one -- OpenAI's or
Anthropic's, a field once called `kind` -- is `api`.
`/v1/chat/completions` refuses an embedding model and `/v1/embeddings` a chat
model, each with a 400 that says which to use.

Model ids are `<provider>/<upstream id>`: the connection's provider name,
then the id its endpoint uses -- `llama/bonsai`, `deepseek/deepseek-flash`.
The older forms -- the bare upstream id, and `<connection name>/<id>` only
where two visible connections serve the same one -- are still accepted by
`resolve`, so a client configured with them keeps working.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db
from lembas.api.v1._common import error_response
from lembas.config import settings
from lembas.db.models import MODEL_CHAT, MODEL_EMBEDDING, MODEL_KINDS, Connection, Model, User
from lembas.security.api_tokens import ModelsCaller
from lembas.services.llm import sse
from lembas.services.llm.openai_client import (
    Endpoint,
    LLMError,
    describe_http_error,
    require_openai,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["openai"])

# Replies in flight through this API, per account, for `concurrent_replies`.
_IN_FLIGHT: dict[str, int] = {}
# The same, per model row: whether a model is answering somebody through this
# door, which a scheduled run reads before taking it (`schedule/models.py`).
_IN_FLIGHT_MODELS: dict[str, int] = {}


def in_flight_on(model_row_id: str) -> int:
    """Requests this API is relaying to one model right now."""
    return _IN_FLIGHT_MODELS.get(model_row_id, 0)


def _hold(user_id: str, model_row_id: str) -> None:
    _IN_FLIGHT[user_id] = _IN_FLIGHT.get(user_id, 0) + 1
    if model_row_id:
        _IN_FLIGHT_MODELS[model_row_id] = _IN_FLIGHT_MODELS.get(model_row_id, 0) + 1


@dataclass(frozen=True)
class Served:
    """One model as this API names it."""

    id: str
    model: Model
    connection: Connection


def served_id(model: Model) -> str:
    """The id a model is served under: `<provider>/<model id>`. Unique
    by construction -- a provider name is unique across connections, and a model
    id within one."""
    return f"{model.connection.provider_name}/{model.model_id}"


def served_models(db: DBSession, user: User) -> list[Served]:
    """Every model this account may reach, embedding models included: this API
    serves `/v1/embeddings` too, and refuses a chat with one by its kind. (The
    chat pages' `available_models` leaves them out, which is right there.)"""
    from lembas.security import permissions

    return [
        Served(id=served_id(model), model=model, connection=model.connection)
        for model in sorted(
            permissions.models_visible_to(db, user), key=lambda m: (m.position, m.model_id)
        )
    ]


def _legacy_ids(served: list[Served]) -> dict[str, Served]:
    """The older ids of these models: the bare upstream id, or
    `<connection name>/<id>` where two connections served the same one. Worked
    out with the old rule over the same list, so an old id names exactly the
    model it named then and never one it did not."""
    counts: dict[str, int] = {}
    for entry in served:
        counts[entry.model.model_id] = counts.get(entry.model.model_id, 0) + 1
    out: dict[str, Served] = {}
    for entry in served:
        name = entry.model.model_id
        if counts[name] > 1:
            name = f"{entry.connection.name}/{name}"
        out.setdefault(name, entry)
    return out


def find(db: DBSession, user: User, wanted: str) -> Served | None:
    """The model `wanted` names for this account, or None: a served id first,
    then its older id (`_legacy_ids`)."""
    if not wanted:
        return None
    served = served_models(db, user)
    for entry in served:
        if entry.id == wanted:
            return entry
    return _legacy_ids(served).get(wanted)


def resolve(db: DBSession, user: User, wanted: str) -> Served:
    found = find(db, user, wanted)
    if found is not None:
        return found
    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        f"There is no model {wanted!r} for this account. GET /v1/models lists them.",
    )


def _family(model_id: str) -> str:
    lowered = model_id.lower()
    if "claude" in lowered:
        return "anthropic"
    if "gemini" in lowered:
        return "gemini"
    if lowered.startswith(("gpt", "o1", "o3", "o4")) or "/gpt" in lowered:
        return "gpt"
    return "local"


def metadata(served: Served) -> dict[str, Any]:
    """One `/v1/models` entry: OpenAI's four fields, then the spec's."""
    from lembas.services import chat as chat_service

    model, connection = served.model, served.connection
    capabilities = model.capabilities_json or {}
    params = model.params_json or {}
    entry: dict[str, Any] = {
        "id": served.id,
        "object": "model",
        "created": int(model.created_at.timestamp()) if model.created_at else 0,
        "owned_by": connection.name,
        "name": model.display_name or model.model_id,
        "family": _family(model.model_id),
        # The connection's provider name: the first half of the id.
        "provider": connection.provider_name,
        # What the model is for: `chat` or `embedding`.
        "kind": kind_of(model),
        # Which API it is reached through behind this one (once called
        # `kind`, before `kind` came to mean the line above).
        "api": connection.kind or "openai",
    }
    if model.context_length:
        entry["context"] = int(model.context_length)
    max_tokens = params.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        entry["max_output"] = max_tokens
    elif model.output_limit:
        entry["max_output"] = int(model.output_limit)
    # The efforts as a chat here offers them: a model not marked as reasoning
    # takes none (`[]`, its one level `default`), an unset list is said as the
    # common three this server falls back to. Then the spec 3.2.0 levels.
    levels = chat_service.levels_for(model)
    if "reasoning" in capabilities or model.reasoning_efforts:
        entry["efforts"] = list(chat_service.model_efforts(model))
    if model.thinking_switch:
        entry["thinking_switch"] = True
    # Harness spec 3.8.0 (pages.md): the model's flag, as the administrator
    # set it. Whether a chat is offered pages is also the instance's switch
    # and the chat's.
    if model.html_pages:
        entry["html_pages"] = True
    entry["levels"] = list(levels.offered)
    # The administrator's sampling settings for the model. This server
    # applies them anyway, under whatever a request sets; they are here so a
    # device shows the model as it is set up, and sends the same.
    for key in ("temperature", "top_p"):
        value = params.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            entry[key] = value
    # The level a new conversation starts with, by the spec's rule.
    entry["effort"] = levels.start
    if "vision" in capabilities:
        entry["vision"] = bool(capabilities.get("vision"))
    if "tools" in capabilities:
        entry["tools"] = bool(capabilities.get("tools"))
    if (model.notes or "").strip():
        entry["notes"] = model.notes.strip()[:300]
    capacity: dict[str, Any] = {}
    if connection.one_model_at_a_time:
        capacity["group"] = connection.name
    # `parallel` (spec 3.2.0) where it is known, and `single_session` beside it
    # at 1 for a client older than that.
    parallel = model.parallel_limit
    if parallel is not None:
        capacity["parallel"] = parallel
    if parallel == 1:
        capacity["single_session"] = True
    if capacity:
        entry["capacity"] = capacity
    return entry


def kind_of(model: Model) -> str:
    """`chat` or `embedding`. Unset -- an older row nobody has marked -- is a
    chat model here; `takes_embeddings` is where "unset" still matters."""
    return model.kind if model.kind in MODEL_KINDS else MODEL_CHAT


def takes_embeddings(model: Model) -> bool:
    """Whether `/v1/embeddings` accepts the model: an embedding model, or one
    nobody has given a kind (legacy rows `/v1/embeddings` served before kinds
    existed, which must not stop working on an upgrade)."""
    return model.kind in (None, "", MODEL_EMBEDDING)


def service_entries(db: DBSession, user: User, taken: set[str]) -> list[dict[str, Any]]:
    """The speech and picture services as `/v1/models` entries: each
    only where it is on for the instance and this person holds its permission
    (`services.available`), and none that would shadow a model's own id."""
    from lembas.api.v1 import images as images_api
    from lembas.api.v1.services import available
    from lembas.services import audio as audio_service
    from lembas.services import settings_store

    allowed = available(db, user)
    audio = settings_store.audio(db)
    entries: list[dict[str, Any]] = []

    def add(entry_id: str, kind: str, name: str, provider: str) -> None:
        if entry_id in taken:
            return
        taken.add(entry_id)
        entries.append(
            {
                "id": entry_id,
                "object": "model",
                "created": 0,
                "owned_by": "lembas",
                "name": name,
                "provider": provider,
                "kind": kind,
            }
        )

    for side in ("stt", "tts"):
        if allowed[side]:
            entry_id = audio_service.service_id(audio, side)
            add(entry_id, side, entry_id.split("/", 1)[1], side)
    if allowed["images"]:
        for checkpoint in settings_store.images(db)["checkpoints"]:
            add(images_api.service_id(checkpoint), "image", checkpoint, images_api.PROVIDER)
    return entries


@router.get("/models")
async def list_models(db: Db, caller: ModelsCaller) -> dict[str, Any]:
    """`default` marks the model this account's new chats start on, so a client
    configuring itself from this list starts on the same one. The
    speech and picture services follow the models (`service_entries`)."""
    from lembas.services import chat as chat_service

    start = chat_service.default_model(db, caller.user)
    data = []
    for served in served_models(db, caller.user):
        entry = metadata(served)
        if start and (served.model.model_id, served.connection.id) == start:
            entry["default"] = True
        data.append(entry)
    data.extend(service_entries(db, caller.user, {entry["id"] for entry in data}))
    return {"object": "list", "data": data}


def _refusal(db: DBSession, user: User, model: Model | None = None) -> str:
    from lembas.security import permissions
    from lembas.services import generation as generation_service

    ceiling = permissions.limit(db, user, "concurrent_replies")
    if ceiling > 0:
        from sqlalchemy import select

        from lembas.db.models import Chat

        chat_ids = set(db.scalars(select(Chat.id).where(Chat.user_id == user.id)))
        running = sum(1 for c in chat_ids if generation_service.running_for(c) is not None)
        running += _IN_FLIGHT.get(user.id, 0)
        if running >= ceiling:
            return (
                f"This account already has {running} replies being written, which is "
                "its limit. Wait for one to finish."
            )
    return ""


def _body_for(served: Served, body: dict[str, Any]) -> dict[str, Any]:
    """The upstream request: the client's, on the upstream's model id, with the
    model's own defaults under anything the client set."""
    defaults = {
        key: value
        for key, value in (served.model.params_json or {}).items()
        if key not in ("reasoning_effort",)
    }
    out = {**defaults, **body}
    out["model"] = served.model.model_id
    return out


def _record(
    user_id: str,
    usage: dict[str, Any] | None,
    replies: int = 1,
    model_row_id: str = "",
    token_id: str | None = None,
) -> None:
    """Usage and what the call cost -- what a reply in the web UI records.
    Counting only: nothing here refuses the next call."""
    from lembas.db.session import session_scope
    from lembas.services import pricing
    from lembas.services import usage as usage_service
    from lembas.services.llm import providers

    usage = usage or {}
    details = usage.get("prompt_tokens_details")
    cached = providers.cached_tokens(usage) or 0
    written = details.get("cache_write_tokens", 0) if isinstance(details, dict) else 0
    counts = {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "cached_tokens": int(cached or 0),
        "cache_write_tokens": int(written or 0),
    }
    with session_scope() as db:
        model = db.get(Model, model_row_id) if model_row_id else None
        priced = (
            pricing.price_reply(
                db, model, counts, reported_micro=providers.reported_cost_micro(usage)
            )
            if replies
            else None
        )
        usage_service.record(
            db,
            user_id,
            model_id=model.model_id if model is not None else "",
            prompt_tokens=counts["prompt_tokens"],
            cached_tokens=counts["cached_tokens"],
            completion_tokens=counts["completion_tokens"],
            requests=replies,
            cost_micro=priced["cost_micro"] if priced else 0,
            currency=priced["currency"] if priced else None,
        )
        if token_id:
            usage_service.record_token(
                db, token_id,
                prompt_tokens=counts["prompt_tokens"],
                completion_tokens=counts["completion_tokens"],
            )
        db.commit()


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The body is not JSON.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The body must be a JSON object.")
    return body


@router.post("/chat/completions")
async def chat_completions(request: Request, db: Db, caller: ModelsCaller):
    body = await _read_json(request)
    wanted = str(body.get("model") or "")
    if not wanted:
        return error_response(400, "`model` is required.")
    try:
        served = resolve(db, caller.user, wanted)
    except HTTPException as exc:
        return error_response(exc.status_code, str(exc.detail), "not_found_error")
    if kind_of(served.model) == MODEL_EMBEDDING:
        return error_response(
            400, f"{served.id} is an embedding model: send it to /v1/embeddings."
        )
    if reason := _refusal(db, caller.user, served.model):
        return error_response(429, reason, "rate_limit_error")
    model_row_id, token_id = served.model.id, caller.token.id

    endpoint = Endpoint.from_connection(served.connection)
    upstream = _body_for(served, body)
    if not endpoint.is_anthropic:
        # The effort where this provider reads it, and nothing it would refuse
        # (`providers.shape_request`): a client sends OpenAI's shape to every
        # model, whichever provider is behind it.
        from lembas.services.llm import providers

        upstream = providers.shape_request(endpoint.provider_type, upstream)
    user_id = caller.user.id
    streaming = bool(body.get("stream"))

    _hold(user_id, model_row_id)
    if endpoint.is_anthropic:
        return await _anthropic(served, endpoint, upstream, streaming, user_id, model_row_id,
                                token_id)
    if not streaming:
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
                response = await client.post(
                    endpoint.url("chat/completions"), headers=endpoint.headers(), json=upstream
                )
            if response.status_code >= 400:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    return error_response(
                        response.status_code, describe_http_error(exc), "upstream_error"
                    )
            data = response.json()
        except httpx.RequestError as exc:
            return error_response(
                502, f"The model's server could not be reached: {exc}", "upstream_error"
            )
        except ValueError:
            return error_response(
                502, "The model's server answered with something that is not JSON."
            )
        finally:
            _release(user_id, model_row_id)
        if isinstance(data, dict):
            await asyncio.to_thread(
                _record, user_id, data.get("usage"), 1, model_row_id, token_id
            )
            data["model"] = served.id
        return JSONResponse(data)

    # Ask for the closing usage frame, which is how a streamed reply's cost is
    # known. A client that did not ask gets it too; OpenAI's own clients ignore a
    # frame with no choices.
    upstream["stream_options"] = {
        **dict(upstream.get("stream_options") or {}),
        "include_usage": True,
    }

    client = httpx.AsyncClient(timeout=settings.request_timeout)
    try:
        upstream_response = await client.send(
            client.build_request(
                "POST", endpoint.url("chat/completions"), headers=endpoint.headers(), json=upstream
            ),
            stream=True,
        )
    except httpx.RequestError as exc:
        await client.aclose()
        _release(user_id, model_row_id)
        return error_response(
            502, f"The model's server could not be reached: {exc}", "upstream_error"
        )
    if upstream_response.status_code >= 400:
        await upstream_response.aread()
        await client.aclose()
        _release(user_id, model_row_id)
        try:
            upstream_response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return error_response(
                upstream_response.status_code, describe_http_error(exc), "upstream_error"
            )

    cleanup = _once(
        upstream_response.aclose, client.aclose, partial(_release, user_id, model_row_id)
    )

    async def relay() -> AsyncIterator[bytes]:
        usage: dict[str, Any] | None = None
        try:
            async for event, data in sse.iter_sse(upstream_response.aiter_lines()):
                if event == sse.COMMENT:
                    yield f":{data}\n\n".encode()  # a keep-alive, passed on as one
                    continue
                if event == sse.ERROR:
                    # Written outside the frame format upstream (quirks.json
                    # `error-in-200-stream`); framed properly here, so the
                    # client reads the error it is rather than an empty reply.
                    frame = sse.error_object(data)
                    yield f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode()
                    continue
                data = data.strip()
                if not data:
                    continue
                if data != sse.DONE:
                    try:
                        frame = json.loads(data)
                    except json.JSONDecodeError:
                        frame = None
                    if isinstance(frame, dict):
                        if frame.get("usage"):
                            usage = frame["usage"]
                        frame["model"] = served.id
                        yield f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode()
                        continue
                yield f"data: {data}\n\n".encode()
        finally:
            await cleanup()
            await asyncio.to_thread(_record, user_id, usage, 1, model_row_id, token_id)

    return _Relay(relay(), cleanup)


def _release(user_id: str, model_row_id: str = "") -> None:
    _IN_FLIGHT[user_id] = max(0, _IN_FLIGHT.get(user_id, 1) - 1)
    if model_row_id:
        _IN_FLIGHT_MODELS[model_row_id] = max(0, _IN_FLIGHT_MODELS.get(model_row_id, 1) - 1)


def _once(*steps: Callable[[], Awaitable[None] | None]) -> Callable[[], Awaitable[None]]:
    """A cleanup that runs its steps at most once, however many paths call it."""
    done = False

    async def run() -> None:
        nonlocal done
        if done:
            return
        done = True
        for step in steps:
            result = step()
            if result is not None:
                await result

    return run


class _Relay(StreamingResponse):
    """A stream whose cleanup runs even if its body is never read.

    The relay's own `finally` is not enough. A generator that never started has
    no `finally` to run, and Starlette never starts it when the client is gone
    before the first byte: the disconnect listener cancels `stream_response`
    while it waits on `http.response.start`, or (ASGI 2.4) that first `send`
    raises. The account's in-flight count then stayed up until a restart, and
    the upstream connection stayed open with it. So the response runs the same
    idempotent cleanup when it is done, whichever way that happened.
    """

    def __init__(self, body: AsyncIterator[bytes], cleanup: Callable[[], Awaitable[None]]) -> None:
        super().__init__(
            body,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._cleanup()


async def _anthropic(
    served: Served,
    endpoint: Endpoint,
    upstream: dict[str, Any],
    streaming: bool,
    user_id: str,
    model_row_id: str,
    token_id: str,
):
    """A request for a model on an Anthropic connection.

    Not relayed as written, as an OpenAI endpoint's answer is: it goes through
    the same translation a reply in the web UI does, and what comes back is
    OpenAI's shape. The closing frame also carries `assistant_blocks`, the turn
    in Anthropic's own words -- a client that sends it back on the assistant
    message keeps the model's thinking across a tool loop; one that does not
    loses nothing it had.
    """
    from lembas.services.llm import anthropic_client

    if not streaming:
        try:
            data = await anthropic_client.complete_response(endpoint, upstream)
        except LLMError as exc:
            return error_response(exc.status_code or 502, exc.message, "upstream_error")
        finally:
            _release(user_id, model_row_id)
        await asyncio.to_thread(_record, user_id, data.get("usage"), 1, model_row_id, token_id)
        data["model"] = served.id
        return JSONResponse(data)

    stream = anthropic_client.stream_chat(endpoint, upstream)
    try:
        first = await anext(stream)
    except StopAsyncIteration:
        first = None
    except LLMError as exc:
        _release(user_id, model_row_id)
        return error_response(exc.status_code or 502, exc.message, "upstream_error")

    cleanup = _once(stream.aclose, partial(_release, user_id, model_row_id))

    async def relay() -> AsyncIterator[bytes]:
        usage: dict[str, Any] | None = None

        def frame(chunk: dict[str, Any]) -> bytes:
            nonlocal usage
            if chunk.get("usage"):
                usage = chunk["usage"]
            chunk["model"] = served.id
            return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()

        try:
            if first is not None:
                yield frame(first)
            try:
                async for chunk in stream:
                    yield frame(chunk)
            except LLMError as exc:
                # Mid-stream the status line is long gone: the error goes in the
                # stream, in OpenAI's error shape.
                failure = {"error": {"message": exc.message, "type": "upstream_error",
                                     "code": exc.status_code or 502}}
                yield f"data: {json.dumps(failure)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        finally:
            await cleanup()
            await asyncio.to_thread(_record, user_id, usage, 1, model_row_id, token_id)

    return _Relay(relay(), cleanup)


@router.post("/embeddings")
async def embeddings(request: Request, db: Db, caller: ModelsCaller):
    body = await _read_json(request)
    try:
        served = resolve(db, caller.user, str(body.get("model") or ""))
    except HTTPException as exc:
        return error_response(exc.status_code, str(exc.detail), "not_found_error")
    endpoint = Endpoint.from_connection(served.connection)
    try:
        require_openai(endpoint, "embeddings")
    except LLMError as exc:
        return error_response(400, exc.message)
    if not takes_embeddings(served.model):
        return error_response(
            400,
            f"{served.id} is a chat model: send it to /v1/chat/completions, or an "
            "embedding model here.",
        )
    if reason := _refusal(db, caller.user, served.model):
        return error_response(429, reason, "rate_limit_error")
    upstream = {**body, "model": served.model.model_id}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.post(
                endpoint.url("embeddings"), headers=endpoint.headers(), json=upstream
            )
        if response.status_code >= 400:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                return error_response(
                    response.status_code, describe_http_error(exc), "upstream_error"
                )
        data = response.json()
    except httpx.RequestError as exc:
        return error_response(
            502, f"The model's server could not be reached: {exc}", "upstream_error"
        )
    except ValueError:
        return error_response(502, "The model's server answered with something that is not JSON.")
    if isinstance(data, dict):
        # Counted and charged like a reply, on the model's input price: the
        # caller asked for these vectors. The search index's own embedding is
        # the one that is not charged (`services/index`).
        usage = data.get("usage") or {}
        await asyncio.to_thread(
            _record,
            caller.user.id,
            {"prompt_tokens": usage.get("prompt_tokens", 0)},
            0,
            served.model.id,
            caller.token.id,
        )
        data["model"] = served.id
    log.debug("embeddings for %s in %.2fs", caller.user.email, time.monotonic() - started)
    return JSONResponse(data)


__all__ = [
    "find",
    "kind_of",
    "metadata",
    "resolve",
    "router",
    "served_id",
    "served_models",
    "service_entries",
    "takes_embeddings",
]
