"""Speech to text and text to speech."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from lembas.services import audio as audio_service
from lembas.services import settings_store
from lembas.services.llm.openai_client import Endpoint, LLMError
from lembas.services.markdown import speakable_text
from tests.conftest import join


# --- What gets read out ------------------------------------------------------
def test_speakable_text_drops_code_blocks():
    """A speech model reads every bracket and underscore of a code fence aloud,
    which is unlistenable and longer than the prose it was buried in."""
    spoken = speakable_text("Try this:\n\n```py\nprint('hello world')\n```\n\nThat is all.")
    assert "print" not in spoken
    assert "Try this:" in spoken
    assert "That is all." in spoken


def test_speakable_text_drops_the_code_block_language_label():
    """The label lives in its own div inside the block, so a naive non-greedy
    match for the wrapper's closing tag leaves both it and the code behind."""
    assert "python" not in speakable_text("```python\nx = 1\n```")


def test_speakable_text_keeps_inline_code():
    assert "os.path" in speakable_text("Use `os.path` for that.")


def test_speakable_text_reduces_links_to_their_words():
    spoken = speakable_text("Read [the docs](https://example.com/very/long) first.")
    assert "the docs" in spoken
    assert "https" not in spoken


def test_speakable_text_unescapes_entities():
    """The renderer escapes & and >; reading "amp semicolon" out is nonsense."""
    assert speakable_text("Salt & pepper, 5 > 3") == "Salt & pepper, 5 > 3"


def test_speakable_text_of_nothing():
    assert speakable_text("") == ""
    assert speakable_text("```\nonly code\n```") == ""


def test_speakable_text_is_capped():
    """Endpoints reject or truncate very long input, and a reply this long is
    not one anybody is listening to in full."""
    from lembas.services.markdown import MAX_SPEAKABLE

    assert len(speakable_text("word " * 10000)) == MAX_SPEAKABLE


# --- Voice discovery ---------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {"voices": ["af_heart", "am_adam"]},
        {"voices": [{"id": "af_heart"}, {"id": "am_adam"}]},
        {"voices": [{"name": "af_heart"}, {"name": "am_adam"}]},
        ["af_heart", "am_adam"],
    ],
)
async def test_voices_reads_every_shape_a_server_might_use(mock_http, payload):
    """Kokoro answers with objects, older builds with strings, some with a bare
    list. All three are the same information."""
    mock_http(lambda _r: httpx.Response(200, json=payload))
    audio_service.forget_voices()

    found = await audio_service.voices(Endpoint("http://tts", "", {}))
    assert found == ["af_heart", "am_adam"]


async def test_voices_falls_back_on_404(mock_http):
    """api.openai.com has no voices endpoint. That is not an error -- its voices
    are a fixed list everybody already knows."""
    mock_http(lambda _r: httpx.Response(404))
    audio_service.forget_voices()

    assert await audio_service.voices(Endpoint("http://tts", "", {})) == list(
        audio_service.OPENAI_VOICES
    )


async def test_voices_are_cached(mock_http):
    calls = []

    def handler(_request):
        calls.append(1)
        return httpx.Response(200, json={"voices": ["af_heart"]})

    mock_http(handler)
    audio_service.forget_voices()

    endpoint = Endpoint("http://tts", "", {})
    await audio_service.voices(endpoint)
    await audio_service.voices(endpoint)
    assert len(calls) == 1

    await audio_service.voices(endpoint, refresh=True)
    assert len(calls) == 2


# --- Transcription -----------------------------------------------------------
async def test_transcribe_reads_the_json_shape(mock_http):
    mock_http(lambda _r: httpx.Response(200, json={"text": "  speak friend  "}))
    text = await audio_service.transcribe(
        Endpoint("http://stt", "", {}),
        data=b"x",
        filename="a.wav",
        content_type="audio/wav",
    )
    assert text == "speak friend"


async def test_transcribe_tolerates_a_plain_text_body(mock_http):
    """Some servers answer in text no matter what response_format was asked
    for."""
    mock_http(lambda _r: httpx.Response(200, text="speak friend"))
    text = await audio_service.transcribe(
        Endpoint("http://stt", "", {}), data=b"x", filename="a.wav", content_type="audio/wav"
    )
    assert text == "speak friend"


async def test_transcribe_omits_an_empty_language(mock_http):
    """An empty language must be left out entirely -- sending "" makes some
    servers fail rather than detecting it."""
    seen = {}

    def handler(request):
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "ok"})

    mock_http(handler)
    await audio_service.transcribe(
        Endpoint("http://stt", "", {}),
        data=b"x",
        filename="a.wav",
        content_type="audio/wav",
        language="",
    )
    assert b'name="language"' not in seen["body"]


async def test_transcribe_reports_a_rejected_key_readably(mock_http):
    mock_http(lambda _r: httpx.Response(401, json={"error": {"message": "Bad key."}}))
    with pytest.raises(LLMError) as caught:
        await audio_service.transcribe(
            Endpoint("http://stt", "k", {}),
            data=b"x",
            filename="a.wav",
            content_type="audio/wav",
        )
    assert "rejected" in caught.value.message


# --- Endpoint construction ---------------------------------------------------
def test_endpoint_for_refuses_an_unconfigured_side():
    with pytest.raises(LLMError):
        audio_service.endpoint_for({"tts_base_url": ""}, "tts")


def test_endpoint_for_decrypts_the_stored_key():
    from lembas.services.crypto import encrypt

    endpoint = audio_service.endpoint_for(
        {"tts_base_url": "http://tts/", "tts_api_key_encrypted": encrypt("secret")}, "tts"
    )
    assert endpoint.api_key == "secret"
    assert endpoint.base_url == "http://tts"


# --- Settings ----------------------------------------------------------------
def test_audio_settings_round_trip(db):
    settings_store.update(
        db, {"tts_enabled": True, "tts_voice": "af_heart"}, key=settings_store.AUDIO
    )
    values = settings_store.audio(db)
    assert values["tts_enabled"] is True
    assert values["tts_voice"] == "af_heart"
    # Untouched keys still come back from the defaults.
    assert values["tts_format"] == "mp3"


def test_audio_and_general_settings_do_not_collide(db):
    settings_store.update(db, {"instance_name": "Rivendell"})
    settings_store.update(db, {"tts_enabled": True}, key=settings_store.AUDIO)
    assert settings_store.get(db, "instance_name") == "Rivendell"
    assert "instance_name" not in settings_store.audio(db)


def test_a_resubmitted_mask_keeps_the_stored_key(client: TestClient, db, registered):
    client.post(
        "/admin/audio",
        data={"tts_enabled": "true", "tts_base_url": "http://tts", "tts_api_key": "secret"},
        follow_redirects=False,
    )
    stored = settings_store.audio(db)["tts_api_key_encrypted"]
    assert stored

    # Saving again with the mask in the field must not wipe the credential.
    from lembas.services.crypto import UNCHANGED_SENTINEL

    client.post(
        "/admin/audio",
        data={
            "tts_enabled": "true",
            "tts_base_url": "http://tts",
            "tts_api_key": UNCHANGED_SENTINEL,
        },
        follow_redirects=False,
    )
    assert settings_store.audio(db)["tts_api_key_encrypted"] == stored


def test_an_emptied_key_field_clears_it(client: TestClient, db, registered):
    client.post(
        "/admin/audio",
        data={"tts_base_url": "http://tts", "tts_api_key": "secret"},
        follow_redirects=False,
    )
    client.post(
        "/admin/audio",
        data={"tts_base_url": "http://tts", "tts_api_key": ""},
        follow_redirects=False,
    )
    assert settings_store.audio(db)["tts_api_key_encrypted"] == ""


# --- The API -----------------------------------------------------------------
def test_transcribe_is_absent_until_it_is_enabled(client: TestClient, registered):
    response = client.post(
        "/api/audio/transcribe", files={"file": ("a.wav", b"x", "audio/wav")}
    )
    assert response.status_code == 404


def test_transcribe_rejects_an_empty_recording(client: TestClient, db, registered):
    client.post(
        "/admin/audio",
        data={"stt_enabled": "true", "stt_base_url": "http://stt"},
        follow_redirects=False,
    )
    response = client.post(
        "/api/audio/transcribe", files={"file": ("a.wav", b"", "audio/wav")}
    )
    assert response.status_code == 400


def test_transcribe_rejects_an_oversized_recording(client: TestClient, db, registered):
    from lembas.api.audio import MAX_AUDIO_BYTES

    client.post(
        "/admin/audio",
        data={"stt_enabled": "true", "stt_base_url": "http://stt"},
        follow_redirects=False,
    )
    response = client.post(
        "/api/audio/transcribe",
        files={"file": ("a.wav", b"x" * (MAX_AUDIO_BYTES + 10), "audio/wav")},
    )
    # Refused without the body ever reaching the transcription endpoint.
    assert response.status_code == 413


def test_speech_404s_on_someone_elses_chat(client: TestClient, db, registered, make_chat):
    client.post(
        "/admin/audio",
        data={"tts_enabled": "true", "tts_base_url": "http://tts"},
        follow_redirects=False,
    )
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-and-more"},
        follow_redirects=False,
    )
    # Now signed in as Sam, asking for Frodo's chat.
    chat_id = make_chat(email=registered["email"])
    response = client.get(f"/api/audio/speech/{chat_id}/anything")
    assert response.status_code == 404


def test_the_audio_tab_appears_only_once_audio_is_configured(
    client: TestClient, db, registered
):
    assert "tab-audio" not in client.get("/settings").text
    client.post(
        "/admin/audio",
        data={"tts_enabled": "true", "tts_base_url": "http://tts"},
        follow_redirects=False,
    )
    assert "tab-audio" in client.get("/settings").text


def _tab_lists(html: str) -> tuple[list[str], list[str]]:
    import re

    return (
        re.findall(r'<input[^>]*name="settings-tab"[^>]*id="(tab-[a-z]+)"', html),
        re.findall(r'<section class="tabs__panel" data-tab="(tab-[a-z]+)"', html),
    )


def test_every_settings_tab_has_a_panel_in_the_same_position(
    client: TestClient, db, registered
):
    """The tabs are radios plus sibling selectors, and CSS cannot compare a
    radio's id with a panel's data-tab -- the link is positional. A tab whose
    panel is somewhere else in the order silently shows the wrong one, and a tab
    with no panel shows a blank page. Neither is visible in review."""
    settings_store.update(
        db,
        {"tts_enabled": True, "tts_base_url": "http://tts", "stt_enabled": True},
        key=settings_store.AUDIO,
    )
    tabs, panels = _tab_lists(client.get("/settings").text)

    assert tabs, "no tabs were found; the markup must have changed"
    assert tabs == panels


def test_the_positions_still_line_up_when_a_conditional_tab_is_absent(
    client: TestClient, registered
):
    """Audio only appears once it is configured. It has to drop out of both
    lists at once or everything after it shifts by one."""
    tabs, panels = _tab_lists(client.get("/settings").text)
    assert "tab-audio" not in tabs
    assert tabs == panels


def test_saving_audio_preferences(client: TestClient, db, registered):
    client.post(
        "/api/preferences/audio",
        data={"voice": "am_adam", "speed": "1.5", "autoplay": "true"},
        follow_redirects=False,
    )
    from sqlalchemy import select

    from lembas.db.models import User

    user = db.scalar(select(User).where(User.email == registered["email"]))
    db.refresh(user)
    assert user.settings_json["audio"] == {
        "autoplay": True,
        "voice": "am_adam",
        "speed": 1.5,
    }


def test_an_unreadable_speed_leaves_the_rest_of_the_form_intact(
    client: TestClient, db, registered
):
    client.post(
        "/api/preferences/audio",
        data={"voice": "am_adam", "speed": "quickly"},
        follow_redirects=False,
    )
    from sqlalchemy import select

    from lembas.db.models import User

    user = db.scalar(select(User).where(User.email == registered["email"]))
    db.refresh(user)
    assert user.settings_json["audio"]["voice"] == "am_adam"
    assert "speed" not in user.settings_json["audio"]


# --- Resolving what a given reader hears -------------------------------------
def _user_with(settings):
    from lembas.db.models import User

    return User(name="x", email="x@x.test", password_hash="", settings_json=settings)


def test_a_readers_voice_beats_the_instance_default():
    from lembas.api.audio import resolve_voice

    user = _user_with({"audio": {"voice": "am_adam"}})
    assert resolve_voice({"tts_voice": "af_heart"}, user) == "am_adam"


def test_the_instance_voice_is_used_when_the_reader_has_no_preference():
    from lembas.api.audio import resolve_voice

    assert resolve_voice({"tts_voice": "af_heart"}, _user_with({})) == "af_heart"


def test_speed_is_clamped_to_what_every_endpoint_accepts():
    from lembas.api.audio import resolve_speed

    assert resolve_speed({}, _user_with({"audio": {"speed": 99}})) == 4.0
    assert resolve_speed({}, _user_with({"audio": {"speed": 0}})) == 0.25
    assert resolve_speed({}, _user_with({"audio": {"speed": "fast"}})) == 1.0
