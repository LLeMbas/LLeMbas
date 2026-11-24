"""What an editor asks: `POST /v1/lembas/complete` and `/v1/lembas/editor`.

`/v1/lembas/complete` is autocomplete: the text before the cursor and the
text after it go in, the text to insert comes out (`services/llm/fim.py` says
how each kind of connection is asked). It is `/v1`'s door and `/v1`'s rules --
a token with the `models` scope, the holder's own models, the connection's
key that never leaves this server -- and like everything on `/v1` it is
**stateless**: no chat, no message, nothing kept but the count. A completion
is counted and priced as a reply is (`openai._record`), and never priced on a
subscription's connection.

The model is the request's `model` where that is allowed for autocomplete,
else the person's autocomplete model, else the instance's default
(`services/editor.py`). None is a 409 that says where to choose one.

Completions have a rate of their own, per token (`api_completions_per_minute`,
600 unless an administrator says otherwise; `api_tokens.BUCKET_COMPLETE`): an
editor asks after every pause in typing, and that never touches the rate the
same token's other calls are counted in. Both 429s -- that rate, and more than
`MAX_AT_ONCE` under way -- say in words that suggestions are coming too fast.

A client that goes away cancels the upstream request (`_watched`): the
connection is looked at while the model's server is awaited, streamed or not.

`GET`/`PUT /v1/lembas/editor` are the person's two model settings. The same
pair is `/api/v2/editor` for the apps.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from lembas.api.deps import Db
from lembas.api.v1 import openai as openai_api
from lembas.api.v1._common import error_response
from lembas.db.models import TOKEN_DEVICE, Model
from lembas.security import api_tokens
from lembas.security.api_tokens import Caller, CompleteCaller, ModelsCaller
from lembas.services import chat as chat_service
from lembas.services import editor, tokens
from lembas.services.llm import fim
from lembas.services.llm.openai_client import Endpoint, LLMError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/lembas", tags=["editor"])

# The most of a file one request carries on each side of the cursor, in bytes
# of UTF-8. An editor sends the text around the cursor, not the repository.
MAX_SIDE_BYTES = 64 * 1024
# The whole body: both sides, every character escaped the long way, and the
# rest of the fields.
MAX_BODY_BYTES = 2 * 6 * MAX_SIDE_BYTES + 16 * 1024
DEFAULT_TOKENS = 128
MAX_TOKENS = 512
# Completions one account may have in flight. An editor cancels the one
# before when it asks again, so more than a few at once is a loop gone wrong.
MAX_AT_ONCE = 4
# How often a completion looks whether its client is still there, in seconds.
WATCH_EVERY = 0.25

_IN_FLIGHT: dict[str, int] = {}


def in_flight(user_id: str) -> int:
    return _IN_FLIGHT.get(user_id, 0)


def _release(user_id: str) -> None:
    left = _IN_FLIGHT.get(user_id, 1) - 1
    if left > 0:
        _IN_FLIGHT[user_id] = left
    else:
        _IN_FLIGHT.pop(user_id, None)


async def _read_body(request: Request) -> dict[str, Any] | Response:
    """The JSON body, read to a limit: a refusal for one past it, in words."""
    too_large = error_response(
        413,
        "The request is too large: at most 64 KiB of text before the cursor and "
        "64 KiB after it. Send the text nearest the cursor.",
    )
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return too_large
    raw = bytearray()
    async for piece in request.stream():
        raw += piece
        if len(raw) > MAX_BODY_BYTES:
            return too_large
    try:
        body = json.loads(bytes(raw).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return error_response(400, "The body is not JSON.")
    if not isinstance(body, dict):
        return error_response(400, "The body must be a JSON object.")
    return body


def _text(body: dict[str, Any], key: str) -> str | None:
    """A text field: "" when absent, None when it is not text at all."""
    value = body.get(key)
    if value is None:
        return ""
    return value if isinstance(value, str) else None


def _interactive_refusal(caller: Caller, model: Model) -> str:
    """Why this token may not complete on a model sold for a person's own
    turns, or "". A linked device and an app are a person at a keyboard, and
    autocomplete is that person typing; any other token is a program
    (`openai._key_refusal`, harness spec 3.9.0 `interactive.md`)."""
    from lembas.services import interactive

    token = caller.token
    if getattr(token, "kind", "") == TOKEN_DEVICE:
        return ""
    if api_tokens.SCOPE_APP in (token.scopes_json or []):
        return ""
    if not interactive.connection_only(model.connection):
        return ""
    ref = chat_service.model_ref(model)
    return interactive.scheduled_run(ref, [{"id": ref, "interactive_only": True}])["refused"]


def _quiet(model: Model) -> tuple[dict[str, Any], bool]:
    """What a chat request carries so that this model does not think, or
    thinks as little as it can -- and whether it thinks regardless."""
    meta = chat_service.model_meta(model)
    offered = chat_service.levels(meta["thinking_switch"], meta["efforts"])
    if chat_service.LEVEL_OFF in offered:
        return chat_service.level_fields(chat_service.LEVEL_OFF, **meta), False
    efforts = [level for level in offered if level in chat_service.EFFORTS]
    if efforts:
        return chat_service.level_fields(efforts[0], **meta), True
    return {}, bool((model.capabilities_json or {}).get("reasoning"))


def _ask(model: Model, body: dict[str, Any], max_tokens: int) -> fim.Ask:
    connection = model.connection
    quiet, thinks = _quiet(model)
    return fim.Ask(
        endpoint=Endpoint.from_connection(connection),
        model_id=model.model_id,
        server=str((connection.health_json or {}).get("server") or ""),
        prefix=body["prefix"],
        suffix=body["suffix"],
        max_tokens=max_tokens,
        path=body["path"][:1000],
        language=body["language"][:100],
        params=dict(model.params_json or {}),
        quiet=quiet,
        thinks=thinks,
        context=int(model.context_length or 0),
    )


def _usage(ask: fim.Ask, said: dict[str, Any] | None, text: str) -> dict[str, Any]:
    """What the completion used: the server's own count, or an estimate from
    the lengths where it gave none (`estimated`) -- a completion is counted
    either way."""
    if said:
        prompt = int(said.get("prompt_tokens") or 0)
        written = int(said.get("completion_tokens") or 0)
        return {**said, "prompt_tokens": prompt, "completion_tokens": written,
                "total_tokens": prompt + written}
    before, after = fim.window(ask.prefix, ask.suffix, ask.context)
    prompt = tokens.estimate(before) + tokens.estimate(after)
    written = tokens.estimate(text)
    return {"prompt_tokens": prompt, "completion_tokens": written,
            "total_tokens": prompt + written, "estimated": True}


def _public(usage: dict[str, Any]) -> dict[str, Any]:
    """The usage an answer shows: the counts, not a provider's every field."""
    out = {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    if usage.get("estimated"):
        out["estimated"] = True
    return out


@router.post("/complete")
async def complete(request: Request, db: Db, caller: CompleteCaller):
    body = await _read_body(request)
    if isinstance(body, Response):
        return body
    fields = {key: _text(body, key) for key in ("prefix", "suffix", "path", "language", "model")}
    for key, value in fields.items():
        if value is None:
            return error_response(400, f"`{key}` must be a string.")
    if "prefix" not in body:
        return error_response(400, "`prefix` is required: the text before the cursor.")
    for key in ("prefix", "suffix"):
        if len(fields[key].encode("utf-8")) > MAX_SIDE_BYTES:
            side = "before" if key == "prefix" else "after"
            return error_response(
                413,
                f"`{key}` is too large: at most 64 KiB of text {side} the cursor. "
                "Send the text nearest the cursor.",
            )
    if not fields["prefix"] and not fields["suffix"]:
        return error_response(400, "There is nothing to complete: both sides are empty.")
    asked = body.get("max_tokens")
    if asked is None:
        max_tokens = DEFAULT_TOKENS
    elif isinstance(asked, bool) or not isinstance(asked, int) or asked < 1:
        return error_response(400, "`max_tokens` must be a whole number above 0.")
    else:
        max_tokens = min(asked, MAX_TOKENS)

    user = caller.user
    try:
        model = editor.for_completion(db, user, fields["model"].strip())
    except editor.Refused as exc:
        kind = {403: "permission_error", 404: "not_found_error"}.get(
            exc.status_code, "invalid_request_error"
        )
        return error_response(exc.status_code, exc.message, kind)
    if refused := _interactive_refusal(caller, model):
        return error_response(403, refused, "permission_error")
    if in_flight(user.id) >= MAX_AT_ONCE:
        return error_response(
            429,
            f"Suggestions are being asked for faster than they are written: {MAX_AT_ONCE} "
            "completions are under way for this account. Nothing is wrong: cancel the "
            "one before, or wait a moment.",
            "rate_limit_error",
        )

    ask = _ask(model, fields, max_tokens)
    ref = chat_service.model_ref(model)
    user_id, model_row_id, token_id = user.id, model.id, caller.token.id
    _IN_FLIGHT[user_id] = in_flight(user_id) + 1

    # What was written and what the server said it used, whichever way the
    # request ends: a completion that was cancelled half-way is counted too.
    state: dict[str, Any] = {"text": "", "usage": None, "finish": fim.FINISH_STOP,
                             "failed": False}

    async def run() -> AsyncIterator[str]:
        try:
            async for kind, value in fim.stream(ask):
                if kind == fim.DELTA:
                    state["text"] += value
                    yield value
                else:
                    state["usage"], state["finish"] = value["usage"], value["finish"]
        except LLMError:
            state["failed"] = True
            raise

    def count() -> None:
        if state["failed"] and not state["text"]:
            return  # the model's server refused: there was no completion
        usage = state["usage"]
        if usage is None and state["text"]:
            usage = _usage(ask, None, state["text"])
        # With no text and no count -- cancelled before the first word -- the
        # request is counted and its tokens are not guessed.
        openai_api._record(user_id, usage, 1, model_row_id, token_id)

    async def settle() -> None:
        _release(user_id)
        await asyncio.to_thread(count)

    def final() -> dict[str, Any]:
        return {
            "text": state["text"],
            "model": ref,
            "finish": state["finish"],
            "usage": _public(_usage(ask, state["usage"], state["text"])),
        }

    pieces = _watched(request, run())
    cleanup = openai_api._once(pieces.aclose, settle)
    try:
        if not body.get("stream"):
            async for _piece in pieces:
                pass
            return JSONResponse(final())
        try:
            first = await anext(pieces)
        except StopAsyncIteration:
            first = None
    except _Gone:
        await cleanup()
        # Nobody reads this: the client closed the connection.
        return Response(status_code=499)
    except LLMError as exc:
        await cleanup()
        return error_response(exc.status_code or 502, exc.message, "upstream_error")
    except BaseException:
        await cleanup()
        raise
    finally:
        if not body.get("stream"):
            await cleanup()

    async def relay() -> AsyncIterator[bytes]:
        def frame(payload: dict[str, Any]) -> bytes:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

        try:
            if first is not None:
                yield frame({"delta": first})
            try:
                async for piece in pieces:
                    yield frame({"delta": piece})
            except _Gone:
                return
            except LLMError as exc:
                # The status line is long gone: the error goes in the stream.
                yield frame({"error": {"message": exc.message, "type": "upstream_error",
                                       "code": exc.status_code or 502}})
                return
            yield frame(final())
        finally:
            await cleanup()

    return openai_api._Relay(relay(), cleanup)


class _Gone(Exception):
    """The client closed the connection."""


async def _watched(request: Request, stream: AsyncIterator[str]) -> AsyncIterator[str]:
    """`stream`, given up the moment its client is gone -- also while the
    model's server has not said a word yet, which is when an editor cancels
    most: the next keystroke. Closing this closes `stream`, and with it the
    connection to the model's server."""
    try:
        while True:
            step = asyncio.ensure_future(anext(stream))
            try:
                while not step.done():
                    await asyncio.wait({step}, timeout=WATCH_EVERY)
                    if not step.done() and await request.is_disconnected():
                        raise _Gone
            finally:
                if not step.done():
                    step.cancel()
                    await asyncio.gather(step, return_exceptions=True)
            try:
                piece = step.result()
            except StopAsyncIteration:
                return
            yield piece
    finally:
        await stream.aclose()


# --- The two model settings ------------------------------------------------------
@router.get("/editor")
async def editor_settings(db: Db, caller: ModelsCaller) -> dict[str, Any]:
    """`{autocomplete: {model, source, allowed}, main: {model}}`. A model is
    its `/v1/models` id or null; `source` says whose choice the autocomplete
    model is (`person`, `instance`, or null with no model); `allowed` lists
    the models an administrator allows for autocomplete, or is null when any
    of the person's chat models may be used."""
    return editor.view(db, caller.user)


@router.put("/editor")
async def set_editor_settings(request: Request, db: Db, caller: ModelsCaller):
    """Set the person's choices: `autocomplete_model` and `main_model`, each a
    model id or null (back to the instance's). A field left out keeps what is
    stored. The settings come back as `GET` gives them."""
    try:
        body = await openai_api._read_json(request)
    except HTTPException as exc:
        return error_response(exc.status_code, str(exc.detail))
    changes = {
        key: body[key] for key in ("autocomplete_model", "main_model") if key in body
    }
    try:
        editor.save(db, caller.user, **changes)
    except editor.Refused as exc:
        kind = {403: "permission_error", 404: "not_found_error"}.get(
            exc.status_code, "invalid_request_error"
        )
        return error_response(exc.status_code, exc.message, kind)
    return editor.view(db, caller.user)


__all__ = ["MAX_AT_ONCE", "MAX_SIDE_BYTES", "MAX_TOKENS", "in_flight", "router"]
