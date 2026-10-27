"""Speech to text and text to speech, against OpenAI-shaped audio endpoints.

The same reasoning as the chat client: plain httpx rather than an SDK, because
the target is not api.openai.com so much as whisper.cpp's server, Speaches,
faster-whisper-server, Kokoro and anything else exposing ``/v1/audio/*``. They
agree on the request and disagree politely about the response, so this is
tolerant about what comes back.

Two endpoints, not one. A local install almost always runs transcription and
speech as separate processes -- they are different models on different
schedules -- and forcing them onto one base URL would mean the common case
could not be configured at all.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from lembas.config import settings as env_settings
from lembas.services.crypto import decrypt
from lembas.services.llm.openai_client import (
    Endpoint,
    LLMError,
    describe_http_error,
    wrap_transport_error,
)
from lembas.web.i18n import t

log = logging.getLogger(__name__)

# api.openai.com has no endpoint that lists voices, so when one is not offered
# these are what a caller can reasonably assume. Anything else -- Kokoro's sixty
# or so -- is discovered.
OPENAI_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")

# Formats every player in a browser can decode. opus is deliberately absent:
# some endpoints emit it in an ogg container that Safari will not play.
FORMATS = ("mp3", "wav", "flac", "aac")

# Discovery is cached because the voice list is read every time anyone opens
# their settings, and waking a model server to answer that is rude.
_VOICE_TTL = 300.0
_voice_cache: dict[str, tuple[float, list[str]]] = {}


def endpoint_for(config: dict[str, Any], side: str) -> Endpoint:
    """Build an Endpoint from the stored audio settings.

    `side` is "stt" or "tts". Endpoint is a frozen snapshot with the key
    already decrypted, so nothing downstream has to know the secret was ever
    encrypted -- or hold a database session while it streams.
    """
    base_url = (config.get(f"{side}_base_url") or "").strip()
    if not base_url:
        raise LLMError(t("No audio endpoint has been configured."))
    return Endpoint(
        base_url=base_url.rstrip("/"),
        api_key=decrypt(config.get(f"{side}_api_key_encrypted") or ""),
        extra_headers={},
        name=base_url,
    )


async def transcribe(
    endpoint: Endpoint,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    model: str = "whisper-1",
    language: str = "",
) -> str:
    """Turn recorded audio into text."""
    text, _ = await transcribe_measured(
        endpoint,
        data=data,
        filename=filename,
        content_type=content_type,
        model=model,
        language=language,
    )
    return text


# What a recording is assumed to run at when nothing better is known:
# 64 kbit/s, 8,000 bytes a second. Between the 32 kbit/s Opus a browser records
# and the 128 kbit/s of an MP3, so neither is charged wildly wrong; a WAV, or a
# server that reports the duration, never falls back to it.
ASSUMED_BYTES_PER_SECOND = 8_000


def measure_seconds(data: bytes, reported: Any = None) -> float:
    """How long a recording is, for what transcribing it costs.

    In order of trust: the duration the transcription server reported (whisper
    servers put `duration` in their JSON where they know it); a WAV's own header,
    which says it exactly; and failing both, the size at
    `ASSUMED_BYTES_PER_SECOND`. Decoding a compressed recording here would need
    a codec library this application does not otherwise have, for a figure the
    server usually supplies anyway.
    """
    try:
        seconds = float(reported)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds > 0:
        return seconds
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        import io
        import wave

        try:
            with wave.open(io.BytesIO(data)) as recording:
                rate = recording.getframerate()
                if rate:
                    return recording.getnframes() / rate
        except Exception:  # a header that lies is measured by its size
            pass
    return len(data) / ASSUMED_BYTES_PER_SECOND


async def transcribe_measured(
    endpoint: Endpoint,
    *,
    data: bytes,
    filename: str,
    content_type: str,
    model: str = "whisper-1",
    language: str = "",
) -> tuple[str, float]:
    """Turn recorded audio into text, and say how many seconds it was
    (`measure_seconds`), which is what transcription is priced by.

    `model` is sent even to servers that ignore it: whisper.cpp serves one model
    and does not care, while a router in front of several will not dispatch
    without it. `language` is omitted when empty, which is what asks the server
    to detect it -- sending an empty string instead makes some of them fail.
    """
    form: dict[str, Any] = {"model": model, "response_format": "json"}
    if language:
        form["language"] = language

    try:
        async with httpx.AsyncClient(timeout=env_settings.request_timeout) as client:
            response = await client.post(
                endpoint.url("audio/transcriptions"),
                headers=_headers_without_content_type(endpoint),
                data=form,
                files={"file": (filename, data, content_type or "application/octet-stream")},
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise LLMError(describe_http_error(exc), status_code=exc.response.status_code) from exc
    except httpx.RequestError as exc:
        raise wrap_transport_error(exc, endpoint) from exc

    try:
        payload = response.json()
    except ValueError:
        # response_format=text is what some servers give regardless of the ask.
        return response.text.strip(), measure_seconds(data)

    if isinstance(payload, dict):
        text = payload.get("text")
        if isinstance(text, str):
            return text.strip(), measure_seconds(data, payload.get("duration"))
        error = payload.get("error")
        if error:
            raise LLMError(str(error))
    raise LLMError(t("The transcription endpoint returned no text."))


async def speak(
    endpoint: Endpoint,
    text: str,
    *,
    model: str = "tts-1",
    voice: str = "",
    fmt: str = "mp3",
    speed: float = 1.0,
) -> tuple[str, AsyncIterator[bytes]]:
    """Synthesise speech, returning its content type and a byte stream.

    Streamed rather than buffered: a long reply is a lot of audio, and playback
    can start on the first chunk instead of after the last.
    """
    if not text.strip():
        raise LLMError(t("There is nothing to read out."))

    body: dict[str, Any] = {
        "model": model,
        "input": text,
        "response_format": fmt if fmt in FORMATS else "mp3",
    }
    if voice:
        body["voice"] = voice
    if speed and speed != 1.0:
        body["speed"] = speed

    client = httpx.AsyncClient(timeout=env_settings.request_timeout)
    try:
        request = client.build_request(
            "POST", endpoint.url("audio/speech"), headers=endpoint.headers(), json=body
        )
        response = await client.send(request, stream=True)
        if response.status_code >= 400:
            # Nothing has been read yet on a streaming response, and the error
            # detail is in the body.
            await response.aread()
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        await client.aclose()
        raise LLMError(describe_http_error(exc), status_code=exc.response.status_code) from exc
    except httpx.RequestError as exc:
        await client.aclose()
        raise wrap_transport_error(exc, endpoint) from exc
    except Exception:
        await client.aclose()
        raise

    media_type = response.headers.get("content-type", f"audio/{body['response_format']}")

    async def stream() -> AsyncIterator[bytes]:
        # The client is closed here rather than by the caller: it has to outlive
        # this function, and a response abandoned without aclose leaks a socket.
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return media_type, stream()


async def voices(endpoint: Endpoint, *, refresh: bool = False) -> list[str]:
    """Voices the speech endpoint offers, newest answer cached briefly.

    Falls back to the OpenAI six on a 404, which is not an error: the official
    API simply has no such endpoint, and its voices are a fixed list everyone
    already knows.
    """
    key = endpoint.base_url
    cached = _voice_cache.get(key)
    if cached and not refresh and time.monotonic() - cached[0] < _VOICE_TTL:
        return cached[1]

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                endpoint.url("audio/voices"), headers=endpoint.headers()
            )
            if response.status_code == 404:
                found = list(OPENAI_VOICES)
                _voice_cache[key] = (time.monotonic(), found)
                return found
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise LLMError(describe_http_error(exc), status_code=exc.response.status_code) from exc
    except httpx.RequestError as exc:
        raise wrap_transport_error(exc, endpoint) from exc
    except ValueError as exc:
        raise LLMError(t("The endpoint returned a response that was not JSON.")) from exc

    found = _parse_voices(payload)
    if not found:
        found = list(OPENAI_VOICES)
    _voice_cache[key] = (time.monotonic(), found)
    return found


def _parse_voices(payload: Any) -> list[str]:
    """Pull voice names out of whatever shape the server chose.

    Kokoro answers ``{"voices": [{"id": "af_heart", ...}]}``; older builds and
    some others answer ``{"voices": ["af_heart", ...]}``; a couple return the
    bare list. All three are the same information.
    """
    entries = payload
    if isinstance(payload, dict):
        for field in ("voices", "data"):
            if isinstance(payload.get(field), list):
                entries = payload[field]
                break
    if not isinstance(entries, list):
        return []

    names: list[str] = []
    for entry in entries:
        if isinstance(entry, str) and entry:
            names.append(entry)
        elif isinstance(entry, dict):
            name = entry.get("id") or entry.get("name") or entry.get("voice")
            if isinstance(name, str) and name:
                names.append(name)
    # Sorted and de-duplicated: sixty voices in the server's arbitrary order is
    # not a list anyone can pick from.
    return sorted(dict.fromkeys(names))


def _headers_without_content_type(endpoint: Endpoint) -> dict[str, str]:
    """Endpoint headers minus Content-Type.

    httpx sets the multipart Content-Type itself, including the boundary.
    Leaving the JSON one in place overrides it and the server sees a body it
    cannot parse.
    """
    return {k: v for k, v in endpoint.headers().items() if k.lower() != "content-type"}


# --- What speech costs ------------------------------------------------------
# Speech is priced in the audio settings, in money like a model's prices: per
# minute transcribed, per million characters read aloud. Both the web's own
# dictation and read-aloud and the API's `/v1/audio/*` go through these, so a
# minute dictated in a browser and one sent by a script cost the same and land
# in the same ledger. An administrator is unlimited here as everywhere.
def service_id(config: dict[str, Any], side: str) -> str:
    """What the ledger and `/v1/models` call one side: `stt/whisper-1`,
    `tts/kokoro`."""
    default = "whisper-1" if side == "stt" else "tts-1"
    return f"{side}/{str(config.get(f'{side}_model') or default).strip() or default}"


def price_of(config: dict[str, Any], side: str) -> float:
    return float(config.get("stt_per_minute" if side == "stt" else "tts_per_1m_chars") or 0)


def record_transcription(db, user_id: str, seconds: float, *, token_id: str | None = None) -> int:
    """Count and price one transcription of `seconds`. Returns what it cost
    in micro-units of the currency. Never raises; commits."""
    from lembas.services import pricing, settings_store
    from lembas.services import usage as usage_service

    config = settings_store.audio(db)
    cost = pricing.service_cost_micro(price_of(config, "stt"), seconds / 60)
    usage_service.record(
        db,
        user_id,
        model_id=service_id(config, "stt"),
        requests=1,
        cost_micro=cost,
        audio_seconds=seconds,
    )
    _commit(db)
    return cost


def record_speech(db, user_id: str, characters: int, *, token_id: str | None = None) -> int:
    """Count and price one reading aloud of `characters`. Returns what it
    cost in micro-units of the currency. Never raises; commits."""
    from lembas.services import pricing, settings_store
    from lembas.services import usage as usage_service

    config = settings_store.audio(db)
    cost = pricing.service_cost_micro(price_of(config, "tts"), characters / 1_000_000)
    usage_service.record(
        db,
        user_id,
        model_id=service_id(config, "tts"),
        requests=1,
        cost_micro=cost,
        speech_chars=characters,
    )
    _commit(db)
    return cost


def _commit(db) -> None:
    try:
        db.commit()
    except Exception:  # bookkeeping must never break the request
        db.rollback()
        log.warning("could not record speech usage", exc_info=True)


def forget_voices() -> None:
    """Drop the discovery cache. Used when an administrator changes the URL."""
    _voice_cache.clear()


def template_flags(db, user) -> dict[str, Any]:
    """What the chat templates need to know about audio.

    Lives here rather than in one page module because a message bubble is
    rendered from four places -- the chat page, the two message endpoints, and
    the SSE stream, which has no request at all -- and each of them needs the
    same three booleans. Getting one of them wrong is how a speaker button ends
    up on a page that cannot use it.
    """
    from lembas.security import permissions
    from lembas.services import settings_store

    config = settings_store.audio(db)
    allowed = permissions.resolve(db, user)
    listen = bool(config.get("tts_enabled")) and allowed.get("audio.listen", False)
    preferences = (user.settings_json or {}).get("audio") or {} if user else {}

    return {
        "audio": config,
        "user_audio": preferences,
        "can_dictate": bool(config.get("stt_enabled"))
        and allowed.get("audio.transcribe", False),
        "can_listen": listen,
        # Only meaningful when can_listen; the template guards on both.
        "audio_autoplay": listen
        and bool(preferences.get("autoplay", config.get("tts_autoplay"))),
    }


__all__ = [
    "FORMATS",
    "OPENAI_VOICES",
    "LLMError",
    "endpoint_for",
    "forget_voices",
    "speak",
    "transcribe",
    "voices",
]
