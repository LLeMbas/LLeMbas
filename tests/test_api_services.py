"""What a signed-in device gets besides the models: the instance by
name, voice and web search through the instance's own servers, and the page a
person types a device's code into.

The LLeMbas CLI's `webui` connection is one address and one token, so everything here
is reached with a `models` token and refused where the web UI would refuse the
same person.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from lembas.api.v1.services import connection_name
from lembas.db.models import User
from lembas.security import api_tokens
from lembas.services import branding, settings_store
from lembas.services.crypto import encrypt


@pytest.fixture(autouse=True)
def _fresh():
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()
    branding.forget()
    yield
    branding.forget()


def _bearer(db, scopes=("models",)) -> dict[str, str]:
    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="test", scopes=list(scopes))
    return {"Authorization": f"Bearer {raw}"}


def _voice(db) -> None:
    settings_store.update(
        db,
        {
            "stt_enabled": True,
            "stt_base_url": "http://stt.test/v1",
            "stt_api_key_encrypted": encrypt("stt-key"),
            "tts_enabled": True,
            "tts_base_url": "http://tts.test/v1",
            "tts_voice": "af_heart",
            "tts_speed": 1.25,
        },
        key=settings_store.AUDIO,
    )


def _search(db) -> None:
    settings_store.update(
        db,
        {"enabled": True, "provider": "searxng", "searxng_base_url": "http://search.test"},
        key=settings_store.SEARCH,
    )


def test_a_connection_name_is_the_instance_name():
    assert connection_name("Example") == "example"
    assert connection_name("Môj Lembas AI") == "moj-lembas-ai"
    assert connection_name("") == "lembas" and connection_name("!!!") == "lembas"


def test_discovery_names_the_instance(client, db):
    settings_store.update(db, {"instance_name": "Example"})
    branding.forget()
    body = client.get("/.well-known/lembas.json").json()
    assert body["name"] == "Example" and body["connection"] == "example"
    assert body["api"]["instance"].endswith("/api/v1/instance")


def test_the_instance_says_what_this_account_may_use(client, db, registered):
    headers = _bearer(db)
    settings_store.update(db, {"fetch_enabled": False}, key=settings_store.SEARCH)
    off = client.get("/api/v1/instance", headers=headers).json()
    assert off["services"] == {
        "stt": False, "tts": False, "search": False, "fetch": False, "images": False,
    }
    assert off["account"]["email"] == "frodo@shire.test"
    _voice(db)
    _search(db)
    settings_store.update(db, {"fetch_enabled": True}, key=settings_store.SEARCH)
    on = client.get("/api/v1/instance", headers=headers).json()
    assert on["services"] == {
        "stt": True, "tts": True, "search": True, "fetch": True, "images": False,
    }


def test_the_instance_wants_a_token(client, db, registered):
    assert client.get("/api/v1/instance").status_code == 401


def test_transcription_goes_to_the_instances_server(client, db, registered, mock_http):
    _voice(db)
    seen = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"text": " Speak friend and enter. "})

    mock_http(upstream)
    response = client.post(
        "/v1/audio/transcriptions",
        headers=_bearer(db),
        files={"file": ("speech.wav", b"RIFF....WAVE", "audio/wav")},
        data={"model": "whatever"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"text": "Speak friend and enter."}
    assert seen["url"] == "http://stt.test/v1/audio/transcriptions"
    # The server's key, which never left this one.
    assert seen["auth"] == "Bearer stt-key"


def test_speech_uses_the_accounts_voice(client, db, registered, mock_http):
    _voice(db)
    seen = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})

    mock_http(upstream)
    response = client.post(
        "/v1/audio/speech",
        headers=_bearer(db),
        json={"input": "Mellon.", "response_format": "wav"},
    )
    assert response.status_code == 200 and response.content == b"RIFFwav"
    assert seen["voice"] == "af_heart" and seen["speed"] == 1.25
    assert seen["response_format"] == "wav" and seen["input"] == "Mellon."


def test_voice_that_is_off_is_a_404(client, db, registered):
    headers = _bearer(db)
    response = client.post("/v1/audio/speech", headers=headers, json={"input": "x"})
    assert response.status_code == 404
    assert "not on" in response.json()["error"]["message"]


def test_search_through_the_instance(client, db, registered, mock_http):
    _search(db)

    def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "search.test"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "Lembas", "url": "https://example.org/l", "content": "Waybread."}
                ]
            },
        )

    mock_http(upstream)
    response = client.post("/api/v1/search", headers=_bearer(db), json={"query": "lembas"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "searxng"
    assert body["results"] == [
        {"title": "Lembas", "url": "https://example.org/l", "snippet": "Waybread."}
    ]


def test_search_that_is_off_is_a_404(client, db, registered):
    response = client.post("/api/v1/search", headers=_bearer(db), json={"query": "x"})
    assert response.status_code == 404


def test_fetch_keeps_the_ssrf_guard(client, db, registered):
    settings_store.update(db, {"fetch_enabled": True}, key=settings_store.SEARCH)
    response = client.post(
        "/api/v1/fetch", headers=_bearer(db), json={"url": "http://127.0.0.1:8000/"}
    )
    assert response.status_code == 502


def test_a_library_token_reaches_none_of_it(client, db, registered):
    _voice(db)
    headers = _bearer(db, scopes=("library",))
    assert client.get("/api/v1/instance", headers=headers).status_code == 403
    assert client.post("/v1/audio/speech", headers=headers, json={"input": "x"}).status_code == 403


def test_the_devices_card_takes_a_code(client, db, registered):
    """On Settings → Devices, beside the devices it lists. The tab's old name,
    Connections, still opens it."""
    for name in ("devices", "connections"):
        page = client.get(f"/settings?tab={name}").text
        assert 'id="tab-devices" checked' in page
    assert 'action="/device"' in page and 'id="settings-device-code"' in page
    # Any other tab name opens the first one.
    assert 'id="tab-account" checked' in client.get("/settings?tab=nonsense").text


def test_a_decision_returns_to_the_devices(client, db, registered):
    started = client.post("/api/device/code", json={"device_name": "x"}).json()
    response = client.post(
        "/device",
        data={"code": started["user_code"], "decision": "deny"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/settings?tab=devices")
