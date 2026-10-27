"""The API a program uses: tokens, `/v1`, the admin REST API, device login.

Two doors: a bearer token reaches `/v1` and `/api/v1`, never a page; the cookie
reaches pages, never `/v1`. Scopes are re-checked against what the holder may do
now. `/v1/models` carries the harness spec's model metadata, which is what a
logged-in LLeMbas CLI configures itself from.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from lembas.db.models import (
    TOKEN_DEVICE,
    ApiToken,
    AuditEvent,
    Chat,
    Connection,
    DeviceSessionDelete,
    Model,
    Usage,
    User,
)
from lembas.security import api_tokens
from lembas.services import settings_store
from lembas.services.crypto import encrypt


@pytest.fixture(autouse=True)
def _fresh_rates():
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()
    yield


def _models(db) -> Connection:
    connection = Connection(
        name="local",
        base_url="http://upstream.test/v1",
        api_key_encrypted=encrypt("upstream-key"),
        one_model_at_a_time=True,
    )
    db.add(connection)
    db.commit()
    db.add(
        Model(
            connection_id=connection.id,
            model_id="qwen",
            display_name="Qwen",
            context_length=131072,
            reasoning_efforts=["low", "high"],
            capabilities_json={"tools": True, "vision": False},
            notes="Good at code.",
            single_session=True,
        )
    )
    db.commit()
    return connection


def _token(db, email="frodo@shire.test", scopes=("models",)) -> str:
    user = db.scalar(select(User).where(User.email == email))
    _, raw = api_tokens.create(db, user, name="test", scopes=list(scopes))
    return raw


def _bearer(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


# --- Tokens --------------------------------------------------------------------
def test_a_token_is_stored_as_its_hash(db, registered):
    raw = _token(db)
    row = db.scalar(select(ApiToken))
    assert raw.startswith("lmb_") and row.prefix == raw[:10]
    assert raw not in (row.token_hash, row.prefix) and len(row.token_hash) == 64


def test_v1_wants_a_token_and_not_the_cookie(client, db, registered):
    _models(db)
    # Signed in in the browser, and still refused: the cookie is not a door here.
    response = client.get("/v1/models")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


def test_a_page_does_not_take_a_token(client, db, registered):
    raw = _token(db)
    client.cookies.clear()
    response = client.get("/settings", headers=_bearer(raw), follow_redirects=False)
    assert response.status_code in (303, 307)


def test_models_lists_the_spec_metadata(client, db, registered):
    _models(db)
    response = client.get("/v1/models", headers=_bearer(_token(db)))
    assert response.status_code == 200
    (entry,) = response.json()["data"]
    assert entry["id"] == "local/qwen" and entry["object"] == "model"
    assert entry["provider"] == "local" and entry["api"] == "openai"
    assert entry["kind"] == "chat"
    assert entry["context"] == 131072
    assert entry["efforts"] == ["low", "high"]
    assert entry["tools"] is True and entry["vision"] is False
    assert entry["notes"] == "Good at code."
    assert entry["capacity"] == {"group": "local", "single_session": True}


def test_the_same_id_on_two_connections_is_named_by_both(client, db, registered):
    _models(db)
    other = Connection(name="cloud", base_url="http://c.test/v1", api_key_encrypted=encrypt(""))
    db.add(other)
    db.commit()
    db.add(Model(connection_id=other.id, model_id="qwen"))
    db.commit()
    ids = {m["id"] for m in client.get("/v1/models", headers=_bearer(_token(db))).json()["data"]}
    assert ids == {"local/qwen", "cloud/qwen"}


def test_a_scope_not_given_is_refused(client, db, registered):
    _models(db)
    raw = _token(db, scopes=("admin:read",))
    assert client.get("/v1/models", headers=_bearer(raw)).status_code == 403


def test_losing_the_permission_narrows_every_token(client, db, registered):
    _models(db)
    raw = _token(db)
    user = db.scalar(select(User))
    user.role = "user"
    db.commit()
    # api.use is off by default: the token still exists, and no longer works.
    assert client.get("/v1/models", headers=_bearer(raw)).status_code == 403


def test_a_revoked_token_is_refused(client, db, registered):
    _models(db)
    raw = _token(db)
    api_tokens.revoke(db, db.scalar(select(ApiToken)))
    assert client.get("/v1/models", headers=_bearer(raw)).status_code == 401


def test_the_rate_limit(client, db, registered):
    _models(db)
    settings_store.update(db, {"api_requests_per_minute": 2})
    raw = _token(db)
    codes = [client.get("/v1/models", headers=_bearer(raw)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# --- Chat completions ----------------------------------------------------------
def test_a_completion_goes_to_the_models_connection_and_is_counted(
    client, db, registered, mock_http
):
    _models(db)
    seen = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "Lembas."}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
                "model": "qwen",
            },
        )

    mock_http(upstream)
    response = client.post(
        "/v1/chat/completions",
        headers=_bearer(_token(db)),
        json={"model": "qwen", "messages": [{"role": "user", "content": "Food?"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "Lembas."
    assert seen["url"] == "http://upstream.test/v1/chat/completions"
    # The connection's key, which never left this server.
    assert seen["auth"] == "Bearer upstream-key"
    usage = db.scalar(select(Usage))
    assert (usage.tokens_in, usage.tokens_out) == (7, 3)


def test_a_streamed_completion_is_relayed_and_counted(client, db, registered, mock_http):
    _models(db)
    frames = [
        {"choices": [{"delta": {"content": "Lem"}}]},
        {"choices": [{"delta": {"content": "bas"}}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    ]
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"

    def upstream(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        assert sent["stream"] is True and sent["stream_options"]["include_usage"] is True
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    mock_http(upstream)
    response = client.post(
        "/v1/chat/completions",
        headers=_bearer(_token(db)),
        json={"model": "qwen", "stream": True, "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 200
    text = response.text
    assert '"Lem"' in text and '"bas"' in text and "[DONE]" in text
    usage = db.scalar(select(Usage))
    assert (usage.tokens_in, usage.tokens_out) == (5, 2)


def test_an_unknown_model_is_a_404_in_openais_shape(client, db, registered):
    _models(db)
    response = client.post(
        "/v1/chat/completions",
        headers=_bearer(_token(db)),
        json={"model": "gpt-9", "messages": []},
    )
    assert response.status_code == 404
    assert "error" in response.json()


# --- Administration ------------------------------------------------------------
def test_admin_read_lists_accounts(client, db, registered):
    raw = _token(db, scopes=("admin:read",))
    response = client.get("/api/v1/admin/users", headers=_bearer(raw))
    assert response.status_code == 200
    assert response.json()["data"][0]["email"] == "frodo@shire.test"


def test_admin_scopes_are_only_for_administrators(db, registered):
    user = db.scalar(select(User))
    user.role = "user"
    db.commit()
    token, _ = api_tokens.create(db, user, name="x", scopes=["admin:read", "admin:audit"])
    assert token.scopes_json == []


def test_the_audit_log_over_the_api_and_as_csv(client, db, registered):
    raw = _token(db, scopes=("admin:audit",))
    client.post("/auth/logout")
    client.post(
        "/auth/login", data={"email": "frodo@shire.test", "password": "wrong-password"}
    )
    events = client.get("/api/v1/admin/audit?action=auth", headers=_bearer(raw)).json()["data"]
    assert any(e["action"] == "auth.login_failed" for e in events)
    csv_text = client.get("/api/v1/admin/audit?format=csv", headers=_bearer(raw)).text
    assert csv_text.startswith("at,action,")


# --- Discovery and device login --------------------------------------------------
def test_the_discovery_document(client):
    from lembas import __version__
    from lembas.services import harness_spec

    body = client.get("/.well-known/lembas.json").json()
    assert body["service"] == "lembas" and body["version"] == __version__
    # 1 still, for an older CLI that compares it exactly.
    assert body["protocol"] == 1 and body["protocols"] == [1, 2]
    assert body["harness_spec"] == harness_spec.version()
    assert body["login"]["device"]["code"].endswith("/api/device/code")
    assert body["api"]["openai"].endswith("/v1")


def test_a_device_signs_in(client, db, registered):
    _models(db)
    started = client.post(
        "/api/device/code",
        json={
            "client_id": "lembas 1.0.0",
            "device_name": "LLeMbas CLI on device-1",
            "device_host": "device-1",
            "scope": "models",
        },
    ).json()
    code, device_code = started["user_code"], started["device_code"]
    assert len(code) == 9 and code[4] == "-"

    grant = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
             "device_code": device_code}
    assert client.post("/api/device/token", json=grant).json()["error"] == "authorization_pending"

    # The person sees what is asking before approving.
    page = client.get(f"/device?code={code}").text
    assert "LLeMbas CLI on device-1" in page and "device-1" in page
    client.post("/device", data={"code": code, "decision": "approve"})

    from lembas.db.models import DeviceCode

    db.expire_all()
    row = db.scalar(select(DeviceCode))
    row.last_polled_at = None  # not "slow down": the test polls faster than a device would
    db.commit()
    granted = client.post("/api/device/token", json=grant).json()
    assert granted["access_token"].startswith("lmb_")
    assert granted["account"]["email"] == "frodo@shire.test"

    # The token works, and is a device token naming the machine.
    assert client.get("/v1/models", headers=_bearer(granted["access_token"])).status_code == 200
    token = db.scalar(select(ApiToken).where(ApiToken.kind == "device"))
    assert token.device_host == "device-1" and token.client == "lembas 1.0.0"
    # Once only.
    assert client.post("/api/device/token", json=grant).json()["error"] == "invalid_grant"

    actions = {e.action for e in db.scalars(select(AuditEvent))}
    assert {"device.code", "device.approve", "device.token"} <= actions


def test_a_denied_device_is_told(client, db, registered):
    started = client.post("/api/device/code", json={"device_name": "x"}).json()
    client.post("/device", data={"code": started["user_code"], "decision": "deny"})
    grant = {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
             "device_code": started["device_code"]}
    assert client.post("/api/device/token", json=grant).json()["error"] == "access_denied"


def test_a_device_signs_itself_out(client, db, registered):
    _models(db)
    raw = _token(db)
    assert client.delete("/api/v1/token", headers=_bearer(raw)).status_code == 204
    assert client.get("/v1/models", headers=_bearer(raw)).status_code == 401


def _device(db, user, host="device-1") -> tuple[ApiToken, str]:
    name = f"LLeMbas CLI on {host}"
    scopes = ["models", "link"]
    kind = TOKEN_DEVICE
    return api_tokens.create(db, user, name=name, scopes=scopes, kind=kind, device_host=host)


def _sign_out(client, raw: str, body: dict) -> int:
    return client.request("DELETE", "/api/v1/token", headers=_bearer(raw), json=body).status_code


def test_a_device_logging_in_again_hands_its_chats_to_the_new_token(client, db, registered):
    user = db.scalar(select(User))
    old, old_raw = _device(db, user)
    new, new_raw = _device(db, user)
    old_id, new_id = old.id, new.id
    chat = Chat(user_id=user.id, model_id="", device_token_id=old_id, device_session_id="ses_1")
    db.add(chat)
    db.add(DeviceSessionDelete(token_id=old_id, session_id="ses_gone"))
    db.commit()
    assert _sign_out(client, old_raw, {"replaced_by": new_raw}) == 204
    db.expire_all()
    assert db.get(Chat, chat.id).device_token_id == new_id
    assert db.scalar(select(DeviceSessionDelete)).token_id == new_id
    assert db.get(ApiToken, old_id) is None
    assert client.get("/v1/models", headers=_bearer(new_raw)).status_code == 200
    assert any("agent chat" in e.summary for e in db.scalars(select(AuditEvent)))


def test_chats_never_go_to_another_accounts_token(client, db, registered):
    user = db.scalar(select(User))
    sam = User(email="sam@shire.test", name="Sam", password_hash="x", role="user")
    db.add(sam)
    db.commit()
    _, sams_raw = _device(db, sam)
    for successor in (sams_raw, "lmb_not_a_token", 7):
        old, old_raw = _device(db, user)
        old_id = old.id
        chat = Chat(user_id=user.id, model_id="", device_token_id=old_id, device_session_id="s")
        db.add(chat)
        db.commit()
        assert _sign_out(client, old_raw, {"replaced_by": successor}) == 204
        db.expire_all()
        assert db.get(Chat, chat.id).device_token_id == old_id


def test_a_device_cannot_be_approved_without_the_permission(client, db, registered):
    user = db.scalar(select(User))
    user.role = "user"
    db.commit()
    started = client.post("/api/device/code", json={"device_name": "x"}).json()
    response = client.post("/device", data={"code": started["user_code"], "decision": "approve"})
    assert response.status_code == 403


# --- Settings ----------------------------------------------------------------------
def test_a_token_is_made_and_shown_once(client, db, registered):
    page = client.post("/api/tokens", data={"name": "script", "scopes": ["models"]})
    assert page.status_code == 200
    token = db.scalar(select(ApiToken))
    assert token.prefix in page.text and "Copy it now" in page.text
    # Only the prefix is shown afterwards.
    settings = client.get("/settings").text
    assert token.prefix in settings


def test_revoking_ones_own_token(client, db, registered):
    raw = _token(db)
    token = db.scalar(select(ApiToken))
    client.post(f"/api/tokens/{token.id}/revoke")
    db.expire_all()
    assert api_tokens.resolve(db, raw) is None


def test_a_bare_error_line_in_the_upstream_stream_is_relayed_as_a_framed_error(
    client, db, registered, mock_http
):
    """llama.cpp writes a mid-stream failure as a bare JSON line, often the
    stream's unterminated last line. The relay frames it as `data:` so the
    client reads the error it is rather than an empty reply."""
    _models(db)
    body = (
        'data: {"choices":[{"delta":{"content":"Lem"}}]}\n\n'
        ': keepalive\n\n'
        '{"error":{"code":500,"message":"model unloaded"}}'
    )
    mock_http(
        lambda r: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )
    response = client.post(
        "/v1/chat/completions",
        headers=_bearer(_token(db)),
        json={"model": "qwen", "stream": True, "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 200
    events = [e for e in response.text.split("\n\n") if e]
    assert events[0].startswith('data: {"choices"') and '"model": "local/qwen"' in events[0]
    assert events[1] == ": keepalive"
    assert json.loads(events[2].removeprefix("data: ")) == {
        "error": {"code": 500, "message": "model unloaded"}
    }
