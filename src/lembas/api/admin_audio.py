"""Audio administration: the transcription and speech endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from lembas.api.deps import AdminUser, Db
from lembas.services import audio as audio_service
from lembas.services import settings_store
from lembas.services.crypto import UNCHANGED_SENTINEL, decrypt, keep_or_replace, mask
from lembas.services.llm.openai_client import LLMError
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/audio", tags=["admin-audio"])

# Read out by the speech test. Short, and the one line this project would pick.
TEST_PHRASE = "Speak friend and enter."


def _page_context(db: Db) -> dict:
    config = settings_store.audio(db)
    return {
        "values": config,
        "formats": audio_service.FORMATS,
        "masked": {
            "stt": mask(decrypt(config.get("stt_api_key_encrypted") or "")),
            "tts": mask(decrypt(config.get("tts_api_key_encrypted") or "")),
        },
        "unchanged": UNCHANGED_SENTINEL,
        "currency_sign": settings_store.CURRENCIES.get(settings_store.currency(db), ""),
    }


@router.get("")
async def audio_page(request: Request, db: Db, user: AdminUser, saved: bool = False):
    from lembas.api.audio import available_voices

    context = _page_context(db)
    voices, error = await available_voices(context["values"])
    return render(
        request,
        "admin/audio.html",
        {**context, "voices": voices, "voice_error": error, "saved": saved},
    )


@router.post("")
async def save_audio(
    db: Db,
    user: AdminUser,
    stt_enabled: bool = Form(False),
    stt_base_url: str = Form(""),
    stt_api_key: str = Form(""),
    stt_model: str = Form(""),
    stt_language: str = Form(""),
    tts_enabled: bool = Form(False),
    tts_base_url: str = Form(""),
    tts_api_key: str = Form(""),
    tts_model: str = Form(""),
    tts_voice: str = Form(""),
    tts_format: str = Form("mp3"),
    tts_speed: float = Form(1.0),
    tts_autoplay: bool = Form(False),
    stt_per_minute: str = Form(""),
    tts_per_1m_chars: str = Form(""),
) -> Response:
    """Save both endpoints.

    Unchecked checkboxes are absent from a form post, which is why every toggle
    defaults to False here -- that absence *is* the "off" signal.
    """
    current = settings_store.audio(db)

    settings_store.update(
        db,
        {
            "stt_enabled": stt_enabled,
            "stt_base_url": stt_base_url.strip().rstrip("/"),
            "stt_api_key_encrypted": keep_or_replace(
                stt_api_key, current.get("stt_api_key_encrypted") or ""
            ),
            "stt_model": stt_model.strip() or "whisper-1",
            "stt_language": stt_language.strip()[:16],
            "tts_enabled": tts_enabled,
            "tts_base_url": tts_base_url.strip().rstrip("/"),
            "tts_api_key_encrypted": keep_or_replace(
                tts_api_key, current.get("tts_api_key_encrypted") or ""
            ),
            "tts_model": tts_model.strip() or "tts-1",
            "tts_voice": tts_voice.strip()[:120],
            "tts_format": tts_format if tts_format in audio_service.FORMATS else "mp3",
            "tts_speed": min(max(tts_speed, 0.25), 4.0),
            "tts_autoplay": tts_autoplay,
            # Money, never negative; junk is free.
            "stt_per_minute": settings_store.money_amount(stt_per_minute),
            "tts_per_1m_chars": settings_store.money_amount(tts_per_1m_chars),
        },
        key=settings_store.AUDIO,
    )

    # The voice list belongs to whatever URL was configured before; keeping it
    # would show the previous server's voices against the new one.
    audio_service.forget_voices()
    log.info("audio settings saved by %s", user.email)
    return RedirectResponse("/admin/audio?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/test/{side}")
async def test_audio(request: Request, db: Db, user: AdminUser, side: str):
    """Contact one of the two endpoints and report what happened.

    Speech is tested by synthesising a phrase and measuring the bytes back;
    transcription by sending a short generated tone, which is *expected* to come
    back as no words at all. That still proves what matters -- the URL resolves,
    the key is accepted and the response parses.
    """
    context = _page_context(db)
    config = context["values"]
    message, kind = "", "success"

    try:
        if side == "tts":
            _, stream = await audio_service.speak(
                audio_service.endpoint_for(config, "tts"),
                TEST_PHRASE,
                model=config.get("tts_model") or "tts-1",
                voice=config.get("tts_voice") or "",
                fmt=config.get("tts_format") or "mp3",
                speed=float(config.get("tts_speed") or 1.0),
            )
            size = 0
            async for chunk in stream:
                size += len(chunk)
            message = t("Spoke the test phrase: %(size)s bytes of audio.", size=f"{size:,}")
        elif side == "stt":
            text = await audio_service.transcribe(
                audio_service.endpoint_for(config, "stt"),
                data=_silent_wav(),
                filename="test.wav",
                content_type="audio/wav",
                model=config.get("stt_model") or "whisper-1",
                language=config.get("stt_language") or "",
            )
            heard = t('Heard "%(text)s".', text=text) if text else t("Heard nothing, as expected.")
            message = t("The endpoint answered. %(heard)s", heard=heard)
        else:
            message, kind = t("Unknown endpoint."), "error"
    except LLMError as exc:
        message, kind = exc.message, "error"

    voices, voice_error = [], ""
    if side == "tts":
        from lembas.api.audio import available_voices

        voices, voice_error = await available_voices(config, refresh=True)

    return render(
        request,
        "admin/_audio_result.html",
        {
            "side": side,
            "message": message,
            "message_kind": kind,
            "voices": voices,
            "voice_error": voice_error,
            "values": config,
        },
    )


def _silent_wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    """A valid, silent WAV.

    Generated rather than committed: half a second of silence is fourteen lines
    of header arithmetic, and a binary fixture in the repository would be one
    more thing nobody can review.
    """
    import struct

    frames = int(rate * seconds)
    data = b"\x00\x00" * frames
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE",
        b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16,
        b"data", len(data),
    )
    return header + data
