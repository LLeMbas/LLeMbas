"""Speech over interchangeable sources: `transcribe(source, …)` and
`speak(source, …)`.

Behind each, by the source's dialect (`store.dialect`):

- **OpenAI-shaped** -- `/audio/transcriptions` and `/audio/speech` on the
  source's base URL: the instance's two endpoints as before, OpenAI, and any
  OpenAI-compatible connection an administrator gave speech models
  (`services/audio.py` is the client).
- **Alibaba Cloud Model Studio** -- which has neither route. Recognition is
  the chat route with an audio part (Qwen3-ASR) or the native API (Qwen-Audio
  ASR); synthesis is the native API, which answers with a link to a file
  rather than with audio. The file is collected here and the bytes streamed
  on; a reply longer than one request takes is read in pieces.
"""

from __future__ import annotations

import base64
import logging
import re
import struct
from collections.abc import AsyncIterator, Callable
from typing import Any

from lembas.services import audio as audio_service
from lembas.services.llm.openai_client import LLMError
from lembas.services.media import dashscope, sources, store
from lembas.services.media.sources import Source, Used
from lembas.services.media.store import CAP_STT, CAP_TTS
from lembas.web.i18n import t

log = logging.getLogger(__name__)

# What one recognition request takes inline on Alibaba's synchronous routes
# (the provider's limit on the audio; base64 grows it by a third).
ALIBABA_ASR_MAX_BYTES = 10 * 1024 * 1024
# The most text one synthesis request takes: the provider states 600
# characters for Qwen-TTS; nothing is stated for the SpeechSynthesizer route,
# so the same careful figure is used there.
ALIBABA_TTS_CHARS = 600
ALIBABA_DEFAULT_VOICE = "Cherry"
# The languages Qwen3-ASR takes as a hint; anything else is left to detection.
ALIBABA_ASR_LANGUAGES = frozenset(
    {
        "zh", "yue", "en", "ja", "de", "ko", "ru", "fr", "pt", "ar", "it", "es", "hi", "id",
        "th", "tr", "uk", "vi", "cs", "da", "fil", "fi", "is", "ms", "no", "pl", "sv",
    }
)


# --- Speech to text ---------------------------------------------------------------
async def transcribe(
    source: Source, *, data: bytes, filename: str, content_type: str, language: str = ""
) -> tuple[str, float]:
    """The text of a recording, and its length in seconds."""
    if source.endpoint is None:
        raise LLMError(t("No audio endpoint has been configured."))
    try:
        if source.dialect == store.ALIBABA_CHAT_ASR:
            return await _alibaba_chat_asr(source, data, content_type, language)
        if source.dialect == store.DASHSCOPE_ASR:
            return await _dashscope_asr(source, data, content_type, language)
        return await audio_service.transcribe_measured(
            source.endpoint, data=data, filename=filename, content_type=content_type,
            model=source.model, language=language,
        )
    except LLMError as exc:
        raise sources.scrubbed(source, exc) from exc


def _mime(content_type: str) -> str:
    """`audio/webm;codecs=opus` → `audio/webm`."""
    return (content_type or "").split(";")[0].strip().lower() or "audio/webm"


def _data_uri(data: bytes, content_type: str) -> str:
    if len(data) > ALIBABA_ASR_MAX_BYTES:
        raise LLMError(t("That recording is longer than this provider transcribes at once."))
    return f"data:{_mime(content_type)};base64,{base64.b64encode(data).decode('ascii')}"


def _short_language(language: str) -> str:
    code = (language or "").strip().lower().split("-")[0]
    return code if code in ALIBABA_ASR_LANGUAGES else ""


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        )
    return ""


async def _alibaba_chat_asr(
    source: Source, data: bytes, content_type: str, language: str
) -> tuple[str, float]:
    body: dict[str, Any] = {
        "model": source.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio",
                     "input_audio": {"data": _data_uri(data, content_type)}}
                ],
            }
        ],
        "stream": False,
    }
    if code := _short_language(language):
        body["asr_options"] = {"language": code}
    payload = await dashscope.post(
        source.endpoint, source.endpoint.url("chat/completions"), body
    )
    message = dashscope.dig(payload, "choices", 0, "message")
    if not isinstance(message, dict):
        raise LLMError(t("The transcription endpoint returned no text."))
    return _text_of(message.get("content")).strip(), audio_service.measure_seconds(
        data, dashscope.dig(payload, "usage", "seconds")
    )


# What `parameters.format` is for a recording, by its container: the route
# documents wav, mp3 and opus, and a browser records Opus in WebM or Ogg.
_FORMATS = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
            "audio/mpeg": "mp3", "audio/mp3": "mp3"}


async def _dashscope_asr(
    source: Source, data: bytes, content_type: str, language: str
) -> tuple[str, float]:
    parameters: dict[str, Any] = {"format": _FORMATS.get(_mime(content_type), "opus")}
    if code := _short_language(language):
        parameters["language_hints"] = [code]
    body = {
        "model": source.model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio",
                         "input_audio": {"data": _data_uri(data, content_type)}}
                    ],
                }
            ]
        },
        "parameters": parameters,
    }
    payload = await dashscope.post(
        source.endpoint, dashscope.native_url(source.endpoint, dashscope.GENERATION), body
    )
    text = dashscope.dig(payload, "output", "text")
    if not isinstance(text, str):
        text = dashscope.dig(payload, "output", "sentence", "text")
    if not isinstance(text, str):
        # The shape Qwen3-ASR answers in on this route.
        message = dashscope.dig(payload, "output", "choices", 0, "message")
        text = _text_of(message.get("content")) if isinstance(message, dict) else None
    if not isinstance(text, str):
        raise LLMError(t("The transcription endpoint returned no text."))
    return text.strip(), audio_service.measure_seconds(
        data,
        dashscope.dig(payload, "usage", "duration") or dashscope.dig(payload, "usage", "seconds"),
    )


# --- Text to speech ---------------------------------------------------------------
async def speak(
    source: Source,
    text: str,
    *,
    voice: str = "",
    fmt: str = "mp3",
    speed: float = 1.0,
    on_piece: Callable[[int, bool], None] | None = None,
) -> tuple[str, AsyncIterator[bytes]]:
    """Speech for `text`: its content type and a stream of bytes. The first
    request is made before this returns, so a source that refuses does so
    here -- where another can still be tried -- and not inside the stream.

    `voice` is the reader's own choice and belongs to the instance's
    endpoint; a connection's model reads in the voice set beside it.

    `on_piece(characters, first)` is called for what a source has accepted:
    once with the whole text where it is sent in one request, and once per
    piece where a long text is read in several -- so that what is counted is
    what was read, also when a later piece is refused."""
    if source.endpoint is None:
        raise LLMError(t("No audio endpoint has been configured."))
    if not text.strip():
        raise LLMError(t("There is nothing to read out."))
    try:
        if source.dialect in (store.DASHSCOPE_TTS_CHAT, store.DASHSCOPE_TTS):
            return await _dashscope_speak(source, text, speed, on_piece)
        own_voice = source.voice if source.origin == "connection" else voice
        answer = await audio_service.speak(
            source.endpoint, text, model=source.model, voice=own_voice, fmt=fmt, speed=speed
        )
    except LLMError as exc:
        raise sources.scrubbed(source, exc) from exc
    if on_piece is not None:
        on_piece(len(text), True)
    return answer


_SENTENCE_END = re.compile(r"(?<=[.!?…。！？;:\n])\s+")


def pieces(text: str, limit: int = ALIBABA_TTS_CHARS) -> list[str]:
    """`text` in pieces of at most `limit` characters, cut at the end of a
    sentence where there is one, else at a space, else where the limit falls."""
    out: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text.strip()):
        sentence = sentence.strip()
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if current:
                out.append(current)
                current = ""
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if not sentence:
            continue
        if current and len(current) + 1 + len(sentence) > limit:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


def wav_parts(data: bytes) -> tuple[bytes, bytes] | None:
    """A WAV file as its header (everything up to the samples) and its
    samples, or None when it is not one."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    position = 12
    while position + 8 <= len(data):
        name = data[position : position + 4]
        (size,) = struct.unpack("<I", data[position + 4 : position + 8])
        if name == b"data":
            start = position + 8
            return data[:start], data[start : start + size if size < 0xFFFFFFF0 else None]
        position += 8 + size + (size & 1)
    return None


def _open_ended(header: bytes) -> bytes:
    """A WAV header that says "as long as it turns out": several files'
    samples follow it as one stream, whose length is not known when the
    first byte goes out."""
    return header[:4] + b"\xff\xff\xff\xff" + header[8:-4] + b"\xff\xff\xff\xff"


async def _dashscope_piece(source: Source, text: str, speed: float) -> bytes:
    if source.dialect == store.DASHSCOPE_TTS_CHAT:
        url = dashscope.native_url(source.endpoint, dashscope.GENERATION)
        spoken: dict[str, Any] = {"text": text, "voice": source.voice or ALIBABA_DEFAULT_VOICE}
    else:
        url = dashscope.native_url(source.endpoint, dashscope.SPEECH)
        spoken = {"text": text, "format": "mp3"}
        if source.voice:
            spoken["voice"] = source.voice
        if speed and speed != 1.0:
            spoken["rate"] = min(max(speed, 0.5), 2.0)
    payload = await dashscope.post(source.endpoint, url, {"model": source.model, "input": spoken})
    audio = dashscope.dig(payload, "output", "audio")
    audio = audio if isinstance(audio, dict) else {}
    if audio.get("url"):
        return await dashscope.collect(audio["url"], limit=dashscope.MAX_AUDIO_BYTES)
    encoded = audio.get("data")
    if isinstance(encoded, str) and encoded:
        try:
            return base64.b64decode(encoded)
        except ValueError as exc:
            raise LLMError(
                t("The speech endpoint returned audio that could not be read.")
            ) from exc
    raise LLMError(t("The speech endpoint returned no audio."))


async def _dashscope_speak(
    source: Source, text: str, speed: float,
    on_piece: Callable[[int, bool], None] | None = None,
) -> tuple[str, AsyncIterator[bytes]]:
    parts = pieces(text)
    if not parts:
        raise LLMError(t("There is nothing to read out."))
    first = await _dashscope_piece(source, parts[0], speed)
    wav = wav_parts(first)
    if on_piece is not None:
        on_piece(len(parts[0]), True)

    async def stream() -> AsyncIterator[bytes]:
        if wav is None:
            yield first
        else:
            yield _open_ended(wav[0]) if len(parts) > 1 else wav[0]
            yield wav[1]
        for number, part in enumerate(parts[1:], start=2):
            try:
                data = await _dashscope_piece(source, part, speed)
            except LLMError as exc:
                # The reading stops where the provider did; what was read
                # stays read and is what was counted. Nothing can be said
                # inside a stream of audio, so it is said in the log.
                log.warning(
                    "reading aloud on %s stopped at piece %d of %d: %s",
                    source.label, number, len(parts), sources.scrub(source, exc.message),
                )
                return
            later = wav_parts(data)
            if (later is None) != (wav is None):
                # One stream is one format. A WAV's samples are never
                # followed by another container's bytes, nor the reverse.
                log.warning(
                    "reading aloud on %s stopped at piece %d of %d: the format changed",
                    source.label, number, len(parts),
                )
                return
            if on_piece is not None:
                on_piece(len(part), False)
            yield later[1] if later is not None else data

    return ("audio/wav" if wav is not None else "audio/mpeg"), stream()


# --- With the chat's connection, the fallback and the ledger -----------------------
async def transcribe_for(
    db,
    user,
    *,
    connection=None,
    interactive: bool = True,
    data: bytes,
    filename: str,
    content_type: str,
    language: str = "",
) -> Used:
    """A recording transcribed by the chat's connection's own model, or by
    the instance's default when it has none or its own fails -- counted and
    priced against whichever answered. `result` is `(text, seconds)`."""
    chain = sources.chain(db, CAP_STT, connection, interactive=interactive)
    if not chain:
        raise LLMError(t("No audio endpoint has been configured."))

    async def call(source: Source):
        return await transcribe(
            source, data=data, filename=filename, content_type=content_type, language=language
        )

    used = await sources.first_that_works(chain, call)
    # Counted once it has worked: a failed transcription cost the person
    # nothing they could use.
    sources.record(db, user.id, used.source, seconds=used.result[1])
    _commit(db)
    return used


async def speak_for(
    db,
    user,
    *,
    connection=None,
    interactive: bool = True,
    text: str,
    voice: str = "",
    fmt: str = "mp3",
    speed: float = 1.0,
) -> Used:
    """`text` read by the chat's connection's own model, or by the default,
    as `transcribe_for`. `result` is `(media type, byte stream)`."""
    chain = sources.chain(db, CAP_TTS, connection, interactive=interactive)
    if not chain:
        raise LLMError(t("No audio endpoint has been configured."))
    user_id = user.id

    async def call(source: Source):
        # Counted as the source accepts it, by the character: the whole text
        # where it goes in one request, each piece where it is read in
        # several. The later pieces are read after this request has ended, so
        # each count is written in a session of its own.
        def count(characters: int, first: bool) -> None:
            from lembas.db.session import session_scope

            try:
                with session_scope() as session:
                    sources.record(
                        session, user_id, source, characters=characters,
                        requests=1 if first else 0,
                    )
            except Exception:  # bookkeeping must never break the reading
                log.warning("could not record speech usage", exc_info=True)

        return await speak(source, text, voice=voice, fmt=fmt, speed=speed, on_piece=count)

    return await sources.first_that_works(chain, call)


def _commit(db) -> None:
    try:
        db.commit()
    except Exception:  # bookkeeping must never break the request
        db.rollback()


def headers(used: Used) -> dict[str, str]:
    """Which source answered, for a client to show: `X-Lembas-Source` (the
    name, percent-encoded) and, when the first one tried did not answer,
    `X-Lembas-Fallback: 1`."""
    from urllib.parse import quote

    # Header values are Latin-1, and a connection's name is anything.
    out = {"X-Lembas-Source": quote(used.source.label, safe="")}
    if used.fell_back:
        out["X-Lembas-Fallback"] = "1"
    return out


__all__ = [
    "headers",
    "pieces",
    "speak",
    "speak_for",
    "transcribe",
    "transcribe_for",
    "wav_parts",
]
