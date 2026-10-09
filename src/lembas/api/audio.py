"""Dictation and read-aloud.

Both directions go through the server rather than from the browser to the audio
endpoint directly, for the same reason model requests do: the endpoint is often
on a private address the browser cannot reach, and its API key must never leave
this process.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.db.models import Chat, Message, User
from lembas.services import audio as audio_service
from lembas.services import settings_store
from lembas.services.llm.openai_client import LLMError
from lembas.services.markdown import speakable_text
from lembas.web.i18n import t

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audio", tags=["audio"])

# A minute of speech is well under a megabyte in any browser codec; this is a
# ceiling on nonsense, not a budget. Recorded audio is held in memory and never
# written to disk: it is not an attachment, has no owner and nothing would ever
# sweep it up.
MAX_AUDIO_BYTES = 25 * 1024 * 1024


def _user_audio(user: User) -> dict:
    return dict((user.settings_json or {}).get("audio") or {})


def resolve_voice(config: dict, user: User) -> str:
    """The voice a given user should be read to in.

    Their own choice, then the instance default, then whatever the endpoint
    picks. Not validated against the discovered list: a voice can disappear
    when a server is reconfigured, and falling back beats failing.
    """
    return (_user_audio(user).get("voice") or config.get("tts_voice") or "").strip()


def resolve_speed(config: dict, user: User) -> float:
    """The playback speed for this user, in the range every endpoint accepts.

    Key presence decides which layer wins, not truthiness: chained `or` would
    make a stored speed of 0 fall through to the default instead of being
    clamped, which is a different answer for no stated reason.
    """
    preferences = _user_audio(user)
    if "speed" in preferences:
        raw = preferences["speed"]
    elif "tts_speed" in config:
        raw = config["tts_speed"]
    else:
        return 1.0

    try:
        chosen = float(raw)
    except (TypeError, ValueError):
        return 1.0
    # Clamped rather than dropped, unlike the sampling parameters: a speed of 0
    # is not a slower reading, it is silence.
    return min(max(chosen, 0.25), 4.0)


@router.post(
    "/transcribe", dependencies=[Depends(require_permission("audio.transcribe"))]
)
async def transcribe(
    db: Db, user: RequiredUser, file: UploadFile = File(...)
) -> Response:
    """Turn a recording into text for the composer.

    Returns plain text, not HTML: the caller assigns it to a textarea's value,
    where it is never parsed as markup.
    """
    config = settings_store.audio(db)
    if not config.get("stt_enabled"):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, t("Dictation is not enabled on this instance.")
        )

    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, t("That recording is too long.")
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("The recording was empty."))
    # Credits: a priced transcription is gated like a priced reply.
    if short := audio_service.refusal(db, user, "stt"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, short)

    language = (_user_audio(user).get("language") or config.get("stt_language") or "").strip()

    try:
        text, seconds = await audio_service.transcribe_measured(
            audio_service.endpoint_for(config, "stt"),
            data=data,
            filename=file.filename or "speech.webm",
            content_type=file.content_type or "audio/webm",
            model=config.get("stt_model") or "whisper-1",
            language=language,
        )
    except LLMError as exc:
        log.info("transcription failed: %s", exc.message)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, exc.message) from exc

    # Counted and charged once it has worked: a failed transcription cost the
    # person nothing they could use.
    audio_service.record_transcription(db, user.id, seconds)
    return PlainTextResponse(text)


@router.get(
    "/speech/{chat_id}/{message_id}",
    dependencies=[Depends(require_permission("audio.listen"))],
)
async def speech(db: Db, user: RequiredUser, chat_id: str, message_id: str) -> Response:
    """Read one message aloud."""
    config = settings_store.audio(db)
    if not config.get("tts_enabled"):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, t("Read-aloud is not enabled on this instance.")
        )

    message = _owned_message(db, chat_id, message_id, user)
    text = speakable_text(message.content)
    if not text:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("There is nothing to read out."))
    if short := audio_service.refusal(db, user, "tts"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, short)

    try:
        media_type, stream = await audio_service.speak(
            audio_service.endpoint_for(config, "tts"),
            text,
            model=config.get("tts_model") or "tts-1",
            voice=resolve_voice(config, user),
            fmt=config.get("tts_format") or "mp3",
            speed=resolve_speed(config, user),
        )
    except LLMError as exc:
        log.info("speech failed: %s", exc.message)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, exc.message) from exc

    # The server has accepted the text, so it is read: counted and charged by
    # the character, before the first byte of audio goes out.
    audio_service.record_speech(db, user.id, len(text))
    return StreamingResponse(
        stream,
        media_type=media_type,
        # Not cached: the voice can change under the reader between plays, and
        # a message can be regenerated at the same URL.
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def available_voices(config: dict, *, refresh: bool = False) -> tuple[list[str], str]:
    """Discovered voices and, if discovery failed, why.

    Returns rather than raises: a settings page whose voice list could not be
    fetched should still render, with the reason next to an empty list.
    """
    if not config.get("tts_enabled") or not (config.get("tts_base_url") or "").strip():
        return [], ""
    try:
        return await audio_service.voices(
            audio_service.endpoint_for(config, "tts"), refresh=refresh
        ), ""
    except LLMError as exc:
        return [], exc.message


def _owned_message(db: DBSession, chat_id: str, message_id: str, user: User) -> Message:
    """The message, if it belongs to a chat this user owns.

    404 rather than 403 throughout, matching api/chats.py: whether a given id
    exists is not information these endpoints hand out.
    """
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That chat no longer exists."))
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That message no longer exists."))
    return message
