"""API-only instance mode: one gate in front of the harness product.

Full is the default and changes nothing. API only keeps `/v1`, `/api/v1`, device
login, Settings and Admin, and switches off chats, the library, reports,
schedules, `/mcp` and anything in the background that would start a reply.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from lembas.db.models import Chat, Connection, Message, Model, User
from lembas.security import api_tokens
from lembas.services import instance_mode, settings_store
from lembas.services.crypto import encrypt

SENTENCE = "This instance serves the API only"


@pytest.fixture(autouse=True)
def _fresh_rates():
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()
    yield


def _api_only(client) -> None:
    response = client.post("/admin/general", data={"instance_mode": "api"},
                           follow_redirects=False)
    assert response.status_code == 303


def _full(client) -> None:
    client.post("/admin/general", data={"instance_mode": "full"}, follow_redirects=False)


def _models(db) -> None:
    connection = Connection(name="local", base_url="http://upstream.test/v1",
                            api_key_encrypted=encrypt("upstream-key"))
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="qwen", display_name="Qwen"))
    db.commit()


def _bearer(db, scopes=("models",)) -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == "frodo@shire.test"))
    _, raw = api_tokens.create(db, user, name="test", scopes=list(scopes))
    return {"Authorization": f"Bearer {raw}"}


# --- The setting ---------------------------------------------------------------------
def test_the_default_is_full(db, registered):
    assert settings_store.instance_mode(db) == "full"
    assert settings_store.api_only(db) is False


def test_full_mode_changes_nothing(client, registered):
    assert client.get("/", follow_redirects=False).headers["location"] == "/chat"
    assert client.get("/chat").status_code == 200
    assert client.get("/api/chats/unread").status_code == 200
    page = client.get("/settings").text
    assert 'href="/library/knowledge"' in page and "sidebar__scroll" in page


def test_the_admin_form_offers_and_saves_it(client, db, registered):
    page = client.get("/admin/general").text
    assert 'name="instance_mode"' in page and 'value="api"' in page
    _api_only(client)
    db.expire_all()
    assert settings_store.api_only(db) is True
    # An unknown value, or a form without the field, keeps what is stored.
    client.post("/admin/general", data={"instance_mode": "everything"})
    client.post("/admin/general", data={"instance_name": "Rivendell"})
    db.expire_all()
    assert settings_store.instance_mode(db) == "api"


def test_a_stored_value_that_is_not_a_mode_reads_as_full(db, registered):
    settings_store.update(db, {"instance_mode": "bogus"})
    assert settings_store.api_only(db) is False


# --- Pages ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path", ["/", "/chat", "/chat/some-id", "/chat?kind=agent", "/library",
             "/library/knowledge", "/library/notes", "/reports", "/scheduled",
             "/folders/x/edit"],
)
def test_product_pages_land_on_settings_with_a_notice(client, registered, path):
    _api_only(client)
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == instance_mode.NOTICE_URL
    assert SENTENCE in client.get(response.headers["location"]).text


def test_an_htmx_request_is_told_to_navigate(client, registered):
    _api_only(client)
    response = client.get("/chat", headers={"HX-Request": "true"}, follow_redirects=False)
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == instance_mode.NOTICE_URL


def test_the_sidebar_has_no_chats(client, registered):
    _api_only(client)
    page = client.get("/settings").text
    assert "sidebar__scroll" not in page
    assert 'href="/chat' not in page
    assert 'href="/library/knowledge"' not in page
    assert 'href="/reports"' not in page and 'href="/scheduled"' not in page
    assert "/api/chats/unread" not in page
    # What stays: Settings, Admin, sign-out.
    assert 'href="/admin"' in page and 'action="/auth/logout"' in page
    # The admin's way back goes to Settings, not to chats.
    admin = client.get("/admin/general").text
    assert 'href="/chat"' not in admin and 'href="/settings"' in admin


# --- APIs ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/chats/unread"),
        ("post", "/api/chats/start"),
        ("post", "/api/chats/abc/messages"),
        ("get", "/api/chats/abc/canvas"),
        ("post", "/api/files"),
        ("get", "/api/files/mention-picker"),
        ("post", "/api/folders"),
        ("post", "/api/library/notes"),
        ("get", "/api/library/share/note/x"),
        ("post", "/api/reports/x/delete"),
        ("post", "/api/schedules"),
        ("post", "/api/audio/transcribe"),
        ("get", "/api/devices/abc/browse"),
        ("post", "/mcp"),
    ],
)
def test_product_apis_answer_404_with_a_sentence(client, registered, method, path):
    _api_only(client)
    response = getattr(client, method)(path)
    assert response.status_code == 404
    assert response.json()["detail"].startswith(SENTENCE)


def test_mcp_is_refused_even_with_a_library_token(client, db, registered):
    headers = _bearer(db, scopes=("library",))
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert client.post("/mcp", headers=headers, json=ping).status_code == 200
    _api_only(client)
    response = client.post("/mcp", headers=headers, json=ping)
    assert response.status_code == 404 and SENTENCE in response.json()["detail"]


def test_the_terminal_websocket_is_closed(client, registered):
    _api_only(client)
    with pytest.raises(WebSocketDisconnect), \
            client.websocket_connect("/api/chats/abc/terminal/ws"):
        pass


# --- What keeps working --------------------------------------------------------------
def test_the_api_keeps_working(client, db, registered):
    _models(db)
    headers = _bearer(db)
    _api_only(client)
    response = client.get("/v1/models", headers=headers)
    assert response.status_code == 200
    assert [m["id"] for m in response.json()["data"]] == ["local/qwen"]
    assert client.get("/api/v1/me/credits", headers=headers).status_code != 404
    assert client.get("/.well-known/lembas.json").status_code == 200


def test_device_login_keeps_working(client, registered):
    _api_only(client)
    started = client.post("/api/device/code", json={"device_name": "device-1"})
    assert started.status_code == 200
    code = started.json()["user_code"]
    assert client.get(f"/device?code={code}").status_code == 200
    client.post("/device", data={"code": code, "decision": "approve"})
    grant = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
             "device_code": started.json()["device_code"]}
    assert "access_token" in client.post("/api/device/token", json=grant).json()


@pytest.mark.parametrize(
    "path", ["/settings", "/settings?tab=connections", "/admin", "/admin/general",
             "/admin/users", "/admin/agents", "/admin/schedules", "/healthz",
             "/manifest.webmanifest", "/auth/login"],
)
def test_settings_admin_and_the_rest_still_render(client, registered, path):
    _api_only(client)
    response = client.get(path, follow_redirects=False)
    assert response.status_code in (200, 303), path
    if response.status_code == 303:
        assert response.headers["location"] != instance_mode.NOTICE_URL


def test_settings_posts_still_work(client, registered):
    _api_only(client)
    response = client.post("/api/preferences/theme", data={"theme": "shire"})
    assert response.status_code != 404
    response = client.post("/api/tokens", data={"name": "script", "scopes": ["models"]})
    assert response.status_code == 200


def test_switching_back_restores_everything(client, db, registered):
    _api_only(client)
    assert client.get("/chat", follow_redirects=False).status_code == 303
    _full(client)
    db.expire_all()
    assert settings_store.api_only(db) is False
    assert client.get("/chat").status_code == 200
    assert client.get("/api/chats/unread").status_code == 200
    assert "sidebar__scroll" in client.get("/settings").text


# --- Background work -----------------------------------------------------------------
def test_nothing_wakes_a_chat(db, registered, make_chat):
    from lembas.services import wake

    chat_id = make_chat()
    settings_store.update(db, {"instance_mode": "api"})
    before = len(db.scalars(select(Message).where(Message.chat_id == chat_id)).all())
    assert asyncio.run(wake.wake_chat(chat_id, "A schedule fired.")) == ""
    db.expire_all()
    after = len(db.scalars(select(Message).where(Message.chat_id == chat_id)).all())
    assert after == before
    assert db.get(Chat, chat_id) is not None


def test_the_ticker_fires_nothing(db, registered, monkeypatch):
    from lembas.services.schedule import ticker

    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    settings_store.update(db, {"instance_mode": "api"})
    claimed = []
    monkeypatch.setattr(ticker, "_due", lambda now: claimed.append(now) or None)
    assert asyncio.run(ticker.sweep()) == 0
    assert claimed == []  # returned before it looked for anything due
