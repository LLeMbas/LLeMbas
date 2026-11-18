"""Trying a media model: what the Test button on its page does.

Speech is tried by reading a phrase and measuring the bytes back;
transcription by sending half a second of generated silence, which is
*expected* to come back as no words -- that still proves the address resolves,
the key is accepted and the answer parses. A ComfyUI checkpoint is looked for
in what the ComfyUI says it has. A provider's picture model is not tried at
all: the only test would be a picture somebody pays for.
"""

from __future__ import annotations

import struct
from datetime import UTC, datetime

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Connection
from lembas.services.llm import providers
from lembas.services.llm.openai_client import LLMError
from lembas.services.media import sources, speech, store
from lembas.web.i18n import t

# Read out by the speech test. Short, and the one line this project would pick.
TEST_PHRASE = "Speak friend and enter."


def silent_wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    """A valid, silent WAV. Generated rather than committed: half a second of
    silence is a header and zeroes, and a binary fixture is one more thing
    nobody can review."""
    frames = int(rate * seconds)
    data = b"\x00\x00" * frames
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE",
        b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16,
        b"data", len(data),
    )
    return header + data


def testable(connection: Connection, entry: store.Entry) -> bool:
    """Whether Test has something to do for this model that costs nobody a
    generation."""
    if entry.capability in (store.CAP_STT, store.CAP_TTS):
        return True
    return (
        entry.capability == store.CAP_IMAGE
        and providers.get(connection.provider_type).id == providers.COMFYUI
    )


async def model(connection: Connection, entry: store.Entry) -> tuple[str, bool]:
    """Try one model. Returns what to say, and whether it is an error."""
    try:
        if entry.capability == store.CAP_TTS:
            source = sources._from_connection(connection, entry, own=False)
            _, stream = await speech.speak(
                source, TEST_PHRASE, voice=entry.voice,
                fmt=str(entry.options.get("format") or "mp3"),
                speed=float(entry.options.get("speed") or 1.0),
            )
            size = 0
            async for chunk in stream:
                size += len(chunk)
            return t("Spoke the test phrase: %(size)s bytes of audio.", size=f"{size:,}"), False
        if entry.capability == store.CAP_STT:
            source = sources._from_connection(connection, entry, own=False)
            text, _ = await speech.transcribe(
                source, data=silent_wav(), filename="test.wav", content_type="audio/wav",
                language=str(entry.options.get("language") or ""),
            )
            heard = t('Heard "%(text)s".', text=text) if text else t("Heard nothing, as expected.")
            return t("The endpoint answered. %(heard)s", heard=heard), False
        if testable(connection, entry):
            from lembas.services.crypto import decrypt
            from lembas.services.images import comfy

            checkpoints, _, _ = await comfy.discover(
                comfy.Config(
                    base_url=connection.base_url,
                    api_key=decrypt(connection.api_key_encrypted), timeout=30.0,
                )
            )
            if entry.model in checkpoints:
                return t("The ComfyUI has this checkpoint."), False
            return t("The ComfyUI answered, and does not list this checkpoint."), True
    except LLMError as exc:
        return exc.message, True
    return t("There is nothing to try for this model without generating something."), True


async def speech_server(db: DBSession, connection: Connection) -> tuple[str, bool]:
    """Test on a speech server's own page: its first enabled model of each
    direction is tried, and the outcome kept as the connection's health."""
    said: list[str] = []
    failed = False
    tried = 0
    for capability in (store.CAP_STT, store.CAP_TTS):
        entry = store.find(connection, capability)
        if entry is None:
            continue
        tried += 1
        message, error = await model(connection, entry)
        said.append(f"{entry.label}: {message}")
        failed = failed or error
    now = datetime.now(UTC)
    if not tried:
        message = t(
            "%(name)s has no model switched on yet. Add one under Models, then test again.",
            name=connection.name,
        )
        return message, True
    connection.last_checked_at = now
    connection.last_error = " ".join(said) if failed else ""
    connection.health_json = {
        **(connection.health_json or {}), "status": "error" if failed else "ok",
        "error": connection.last_error, "checked_at": now.isoformat(),
    }
    db.commit()
    return f"{connection.name}: " + " ".join(said), failed


__all__ = ["TEST_PHRASE", "model", "silent_wav", "speech_server", "testable"]
