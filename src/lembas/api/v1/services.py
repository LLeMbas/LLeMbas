"""What a signed-in device gets besides the models.

The LLeMbas CLI's `webui` connection is one address and one token: its models come
from `/v1/models`, and its voice and web search from here, so the endpoints and
keys of the speech and search servers are set once -- on this instance -- and
every device uses them. A device that has none of its own needs none.

- `GET /api/v1/instance` -- this instance by name, the account, the model new
  chats start on, and which of the services below this account may use.
- `POST /v1/audio/transcriptions` and `POST /v1/audio/speech` -- OpenAI's shapes,
  so any client that speaks them works, over the instance's own speech servers
  with the account's own voice, speed and language.
- `POST /api/v1/search` and `POST /api/v1/fetch` -- the web search and page
  fetch a chat's model has, with the same switches and the same SSRF guard.

Speech is counted and priced -- per minute transcribed and per million
characters read, at the prices in the audio settings -- into the same usage
as a reply (`audio.record_transcription`, `audio.record_speech`). Nothing is
refused for what it costs. Pictures have their own
module, `api/v1/images.py`.

All of it needs the `models` scope and the same permission the web UI asks for:
a token never reaches what its holder could not do in a browser. The keys of the
servers behind it never leave this process.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import UploadFile

from lembas import __version__
from lembas.api.deps import Db
from lembas.api.v1._common import error_response
from lembas.security import permissions
from lembas.security.api_tokens import ModelsCaller
from lembas.services import audio as audio_service
from lembas.services import branding, settings_store
from lembas.services.llm.openai_client import LLMError

log = logging.getLogger(__name__)

router = APIRouter(tags=["services"])

# The same ceiling as dictation in the web UI (api/audio.py).
MAX_AUDIO_BYTES = 25 * 1024 * 1024
# A reply read aloud is a few thousand characters; this is a ceiling on nonsense.
MAX_SPEECH_CHARS = 20_000


def connection_name(name: str) -> str:
    """The instance's name as a connection name: `Example` → `example`.

    What the LLeMbas CLI calls the connection, so a model is `example/qwen`
    whatever address the instance was reached at. Accents go, anything else that is not a
    letter or a digit becomes one dash.
    """
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
    return slug[:40].strip("-") or "lembas"


def _audio_user(user) -> dict[str, Any]:
    return dict((user.settings_json or {}).get("audio") or {})


def available(db, user) -> dict[str, bool]:
    """Which services this account may use through the API: each one on for the
    instance, and allowed to this person, exactly as the web UI decides."""
    from lembas.api.v1.images import available as images_available
    from lembas.services import search as search_service

    audio = settings_store.audio(db)
    search = settings_store.search(db)
    return {
        "stt": bool(audio.get("stt_enabled") and (audio.get("stt_base_url") or "").strip())
        and permissions.has(db, user, "audio.transcribe"),
        "tts": bool(audio.get("tts_enabled") and (audio.get("tts_base_url") or "").strip())
        and permissions.has(db, user, "audio.listen"),
        "search": bool(search.get("enabled"))
        and not search_service.availability(str(search.get("provider") or "ddgs"))
        and permissions.has(db, user, "tools.web_search"),
        "fetch": bool(search.get("fetch_enabled")) and permissions.has(db, user, "tools.fetch"),
        # `POST /v1/images/generations`.
        "images": images_available(db, user),
    }


@router.get("/api/v1/instance")
async def instance(db: Db, caller: ModelsCaller) -> JSONResponse:
    from lembas.api.v1.openai import served_models
    from lembas.services import chat as chat_service

    user = caller.user
    brand = branding.for_db(db)
    start = chat_service.default_model(db, user)
    default = next(
        (
            s.id
            for s in served_models(db, user)
            if start and (s.model.model_id, s.connection.id) == start
        ),
        None,
    )
    return JSONResponse(
        {
            "service": "lembas",
            "version": __version__,
            "name": brand.name,
            "connection": connection_name(brand.name),
            "account": {"email": user.email, "name": user.name},
            "default_model": default,
            "services": available(db, user),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/v1/audio/transcriptions")
async def transcriptions(request: Request, db: Db, caller: ModelsCaller):
    """OpenAI's transcription call: a multipart `file`, and optionally
    `language` and `prompt`. `model` is ignored -- the instance's is used."""
    if not available(db, caller.user)["stt"]:
        return error_response(404, "Speech to text is not on for this account on this instance.")
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        return error_response(400, "`file` is required: the recording.")
    data = await upload.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        return error_response(413, "That recording is too long.")
    if not data:
        return error_response(400, "The recording was empty.")
    config = settings_store.audio(db)
    asked = form.get("language")
    language = (
        (asked if isinstance(asked, str) else "")
        or _audio_user(caller.user).get("language")
        or config.get("stt_language")
        or ""
    ).strip()
    try:
        text, seconds = await audio_service.transcribe_measured(
            audio_service.endpoint_for(config, "stt"),
            data=data,
            filename=upload.filename or "speech.wav",
            content_type=upload.content_type or "audio/wav",
            model=config.get("stt_model") or "whisper-1",
            language=language,
        )
    except LLMError as exc:
        log.info("transcription through the API failed: %s", exc.message)
        return error_response(502, exc.message, "upstream_error")
    audio_service.record_transcription(db, caller.user.id, seconds, token_id=caller.token.id)
    return JSONResponse({"text": text})


@router.post("/v1/audio/speech")
async def speech(request: Request, db: Db, caller: ModelsCaller):
    """OpenAI's speech call: `input`, and optionally `voice`, `speed` and
    `response_format`. Without a voice or a speed, the account's own from its
    settings -- what it hears in the web UI."""
    from lembas.api.audio import resolve_speed, resolve_voice

    if not available(db, caller.user)["tts"]:
        return error_response(404, "Text to speech is not on for this account on this instance.")
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return error_response(400, "The body must be a JSON object.")
    text = str(body.get("input") or "").strip()
    if not text:
        return error_response(400, "`input` is required: the text to speak.")
    if len(text) > MAX_SPEECH_CHARS:
        return error_response(413, f"That is more than {MAX_SPEECH_CHARS} characters.")
    config = settings_store.audio(db)
    voice = str(body.get("voice") or "").strip() or resolve_voice(config, caller.user)
    try:
        speed = float(body["speed"]) if "speed" in body else resolve_speed(config, caller.user)
    except (TypeError, ValueError):
        speed = resolve_speed(config, caller.user)
    fmt = str(body.get("response_format") or config.get("tts_format") or "mp3")
    try:
        media_type, stream = await audio_service.speak(
            audio_service.endpoint_for(config, "tts"),
            text,
            model=config.get("tts_model") or "tts-1",
            voice=voice,
            fmt=fmt,
            speed=min(max(speed, 0.25), 4.0),
        )
    except LLMError as exc:
        log.info("speech through the API failed: %s", exc.message)
        return error_response(502, exc.message, "upstream_error")
    audio_service.record_speech(db, caller.user.id, len(text), token_id=caller.token.id)
    return StreamingResponse(
        stream, media_type=media_type, headers={"Cache-Control": "no-store"}
    )


async def _json(request: Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


@router.post("/api/v1/search")
async def web_search(request: Request, db: Db, caller: ModelsCaller):
    """`{"query": …, "max_results": n}` → the results, from whichever provider
    the administrator chose; at most the administrator's number of them."""
    from lembas.services import search as search_service
    from lembas.services.search import SearchError

    if not available(db, caller.user)["search"]:
        return error_response(404, "Web search is not on for this account on this instance.")
    body = await _json(request)
    if body is None:
        return error_response(400, "The body must be a JSON object.")
    config = settings_store.search(db)
    try:
        limit = int(body["max_results"]) if body.get("max_results") is not None else None
    except (TypeError, ValueError):
        limit = None
    try:
        results = await search_service.run(config, str(body.get("query") or ""), limit=limit)
    except SearchError as exc:
        return error_response(502, exc.message, "upstream_error")
    return JSONResponse(
        {
            "provider": str(config.get("provider") or "ddgs"),
            "results": [{"title": r.title, "url": r.url, "snippet": r.snippet} for r in results],
        }
    )


@router.post("/api/v1/fetch")
async def web_fetch(request: Request, db: Db, caller: ModelsCaller):
    """`{"url": …}` → the page, through `services/fetch.py`: every redirect
    re-checked, and this network out of reach unless the administrator allowed
    it. `text` is markdown, as the LLeMbas CLI's `web_fetch` reads a page by
    every other route (its own fetch, Firecrawl), so the model is handed the
    same thing whichever one the CLI is set to."""
    from lembas.services import fetch as fetch_service

    if not available(db, caller.user)["fetch"]:
        return error_response(404, "Fetching pages is not on for this account on this instance.")
    body = await _json(request)
    if body is None:
        return error_response(400, "The body must be a JSON object.")
    url = str(body.get("url") or "").strip()
    if not url:
        return error_response(400, "`url` is required.")
    config = settings_store.search(db)
    try:
        page = await fetch_service.fetch(
            url, allow_private=bool(config.get("allow_private_fetch")), markdown=True
        )
    except fetch_service.FetchError as exc:
        return error_response(502, exc.message, "upstream_error")
    return JSONResponse(
        {"url": page.url, "title": page.title, "text": page.text, "truncated": page.truncated}
    )


__all__ = ["available", "connection_name", "router"]
