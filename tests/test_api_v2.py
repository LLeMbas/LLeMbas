"""The app's door: `/api/v2`, the chats as JSON behind a bearer token.

What matters here is that it is the *same* door as the page's in everything but
the shape: the same scope rules as `/v1`, the same ownership, the same cores for
sending, editing, answering and changing a chat, and a stream that follows the
same `Generation` under the same rules as the HTML one.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from lembas.db.models import ApiToken, Chat, Connection, Message, Model, PushSubscription, User
from lembas.security import api_tokens
from lembas.services import generation as generation_service
from lembas.services import interaction
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
    )
    db.add(connection)
    db.commit()
    db.add(
        Model(
            connection_id=connection.id,
            model_id="qwen",
            display_name="Qwen",
            reasoning_efforts=["low", "high"],
            capabilities_json={"tools": False, "vision": True},
        )
    )
    db.commit()
    return connection


def _bearer(db, email="frodo@shire.test", scopes=("app",)) -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == email))
    _, raw = api_tokens.create(db, user, name="LLeMbas for Android", scopes=list(scopes))
    return {"Authorization": f"Bearer {raw}"}


def _upstream(text: str = "Lembas, *elvish* bread."):
    frames = [
        {"choices": [{"delta": {"content": text[:6]}}]},
        {"choices": [{"delta": {"content": text[6:]}}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 4}},
    ]
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions") and json.loads(request.content).get(
            "stream"
        ):
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        # The title call, and anything else: a plain answer.
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Waybread"}}], "usage": {}}
        )

    return handler


def _events(text: str) -> list[tuple[str, object]]:
    """The stream's frames as (event, payload), keepalives dropped."""
    out: list[tuple[str, object]] = []
    for block in text.split("\n\n"):
        name, data = "", []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if name:
            out.append((name, json.loads("\n".join(data)) if data else None))
    return out


# --- The door ------------------------------------------------------------------------
def test_discovery_names_the_door_and_the_scope(client):
    found = client.get("/.well-known/lembas.json").json()
    assert found["api"]["app"].endswith("/api/v2")
    assert found["app_api"] == 1
    assert "app" in found["scopes"]


def test_no_token_and_the_cookie_alone_are_both_refused(client, registered):
    # Signed in in the browser, and still refused: the cookie is not this door.
    response = client.get("/api/v2/chats")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


def test_a_token_without_the_app_scope_is_refused(client, db, registered):
    response = client.get("/api/v2/chats", headers=_bearer(db, scopes=("models",)))
    assert response.status_code == 403
    assert "app" in response.json()["detail"]


def test_the_app_scope_follows_api_use(client, db, registered):
    """`app` is a personal scope like `models`: whoever lost `api.use` since
    the token was made is refused, with nothing to revoke."""
    from lembas.security import permissions

    for user in db.scalars(select(User)):
        assert api_tokens.may_hold(db, user, "app") == permissions.has(db, user, "api.use")


def test_device_login_can_ask_for_the_app_scope(client, db, registered):
    code = client.post(
        "/api/device/code",
        json={"scope": "app models", "client_id": "lembas-android",
              "device_name": "LLeMbas for Android", "device_platform": "android"},
    ).json()
    client.post("/device", data={"code": code["user_code"], "decision": "approve"})
    from lembas.db.models import DeviceCode

    db.expire_all()
    db.scalar(select(DeviceCode)).last_polled_at = None
    db.commit()
    token = client.post(
        "/api/device/token",
        json={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
              "device_code": code["device_code"]},
    ).json()
    assert set(token["scope"].split()) == {"app", "models"}
    row = db.scalar(select(ApiToken).where(ApiToken.client == "lembas-android"))
    assert row.name == "LLeMbas for Android" and row.device_platform == "android"


# --- Chats -------------------------------------------------------------------------------
def test_a_chat_is_made_from_its_first_message_and_streams_as_json(
    client, db, registered, mock_http
):
    _models(db)
    mock_http(_upstream())
    headers = _bearer(db)

    made = client.post("/api/v2/chats", headers=headers, json={"text": "What is lembas?"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["chat"]["model"] == "qwen" and body["chat"]["kind"] == "chat"
    assert body["message"]["text"] == "What is lembas?"
    reply = body["reply"]
    assert reply["role"] == "assistant" and reply["complete"] is False

    stream = client.get(
        f"/api/v2/chats/{body['chat']['id']}/messages/{reply['id']}/stream", headers=headers
    )
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    events = _events(stream.text)
    names = [name for name, _ in events]
    assert names[-2:] == ["done", "close"]
    done = dict(events)["done"]
    # Markdown as the model wrote it -- never HTML.
    text_steps = [s for s in done["message"]["steps"] if s["kind"] == "text"]
    assert text_steps and text_steps[-1]["markdown"] == "Lembas, *elvish* bread."
    assert "<" not in json.dumps(done["message"]["steps"])
    assert done["message"]["metrics"]["completion_tokens"] == 4


def test_reattaching_after_the_reply_ended_gets_the_whole_state(
    client, db, registered, mock_http
):
    """A phone back from the background attaches late: its first pass is
    everything, ending in `done`, and it needs nothing from before."""
    _models(db)
    mock_http(_upstream())
    headers = _bearer(db)
    body = client.post("/api/v2/chats", headers=headers, json={"text": "Hi"}).json()
    url = f"/api/v2/chats/{body['chat']['id']}/messages/{body['reply']['id']}/stream"
    first = _events(client.get(url, headers=headers).text)
    again = _events(client.get(url, headers=headers).text)
    assert dict(first)["done"]["message"]["steps"] == dict(again)["done"]["message"]["steps"]


def test_the_list_the_detail_and_the_pages(client, db, registered, mock_http):
    _models(db)
    mock_http(_upstream())
    headers = _bearer(db)
    ids = []
    for word in ("one", "two", "three"):
        made = client.post("/api/v2/chats", headers=headers, json={"text": word}).json()
        ids.append(made["chat"]["id"])
        client.get(
            f"/api/v2/chats/{made['chat']['id']}/messages/{made['reply']['id']}/stream",
            headers=headers,
        )

    listed = client.get("/api/v2/chats?limit=2", headers=headers).json()
    assert len(listed["chats"]) == 2 and listed["more"] is True
    rest = client.get(
        f"/api/v2/chats?limit=2&before={listed['chats'][-1]['id']}", headers=headers
    ).json()
    assert {c["id"] for c in listed["chats"] + rest["chats"]} == set(ids)

    detail = client.get(f"/api/v2/chats/{ids[0]}", headers=headers).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["live"] is None

    page = client.get(
        f"/api/v2/chats/{ids[0]}/messages?limit=1", headers=headers
    ).json()
    assert page["more"] is True and page["messages"][0]["role"] == "assistant"
    older = client.get(
        f"/api/v2/chats/{ids[0]}/messages?limit=1&before={page['messages'][0]['id']}",
        headers=headers,
    ).json()
    assert older["messages"][0]["role"] == "user" and older["more"] is False


def test_opening_a_chat_marks_it_read(client, db, registered, make_chat):
    _models(db)
    chat = db.get(Chat, make_chat())
    chat.unread = True
    db.commit()
    headers = _bearer(db)
    assert client.get("/api/v2/chats/unread", headers=headers).json()["unread"] == [chat.id]
    client.get(f"/api/v2/chats/{chat.id}", headers=headers)
    assert client.get("/api/v2/chats/unread", headers=headers).json()["unread"] == []


def test_someone_elses_chat_is_not_found(client, db, registered, make_chat):
    _models(db)
    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    chat_id = make_chat(email="sam@shire.test")
    # Frodo's token, Sam's chat: 404, not 403 -- whether an id exists is not
    # something this door hands out either.
    headers = _bearer(db)
    assert client.get(f"/api/v2/chats/{chat_id}", headers=headers).status_code == 404
    assert client.patch(
        f"/api/v2/chats/{chat_id}", headers=headers, json={"title": "Mine now"}
    ).status_code == 404


def test_patch_goes_through_the_page_rules(client, db, registered, make_chat):
    _models(db)
    chat_id = make_chat()
    headers = _bearer(db)

    changed = client.patch(
        f"/api/v2/chats/{chat_id}", headers=headers,
        json={"title": "Waybread", "effort": "low", "archived": True},
    )
    assert changed.status_code == 200, changed.text
    chat = changed.json()["chat"]
    assert (chat["title"], chat["effort"], chat["archived"]) == ("Waybread", "low", True)

    # Absent is left alone; present and empty clears.
    kept = client.patch(f"/api/v2/chats/{chat_id}", headers=headers, json={"effort": None})
    assert kept.json()["chat"]["title"] == "Waybread" and kept.json()["chat"]["effort"] is None

    # A chat's connection is fixed when it is made -- the page's 409.
    assert client.patch(
        f"/api/v2/chats/{chat_id}", headers=headers, json={"device": "x"}
    ).status_code == 409


def test_deleting_a_chat(client, db, registered, make_chat):
    _models(db)
    chat_id = make_chat()
    headers = _bearer(db)
    assert client.delete(f"/api/v2/chats/{chat_id}", headers=headers).status_code == 204
    db.expire_all()
    assert db.get(Chat, chat_id) is None


def test_an_empty_message_is_refused(client, db, registered, make_chat):
    _models(db)
    headers = _bearer(db)
    assert client.post("/api/v2/chats", headers=headers, json={"text": " "}).status_code == 400
    chat_id = make_chat()
    assert client.post(
        f"/api/v2/chats/{chat_id}/messages", headers=headers, json={"text": ""}
    ).status_code == 400


def test_a_second_message_while_a_reply_runs_is_queued(client, db, registered, make_chat):
    """The page's queue, through the shared `write_turn`: no second reply."""
    _models(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    db.add(Message(chat_id=chat.id, role="assistant", content="", complete=False))
    db.commit()
    headers = _bearer(db)
    sent = client.post(
        f"/api/v2/chats/{chat_id}/messages", headers=headers, json={"text": "and also"}
    ).json()
    assert sent["reply"] is None and sent["message"]["queued"] is True


# --- Cards ------------------------------------------------------------------------------
def _parked(chat_id: str, message_id: str) -> interaction.Interruption:
    card = interaction.Interruption(
        id="card-1",
        items=(
            interaction.Item(
                index=0, key="q0", kind=interaction.KIND_QUESTION, tool_name="ask_user",
                title="Tea or coffee?",
                options=(interaction.Option(label="Tea"), interaction.Option(label="Coffee")),
            ),
        ),
    )
    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    generation.waiting.append(card)
    generation_service._RUNNING[message_id] = generation
    return card


def test_a_card_is_answered_once_and_the_second_answer_is_409(
    client, db, registered, make_chat
):
    import asyncio

    _models(db)
    chat_id = make_chat()
    headers = _bearer(db)

    async def scenario():
        card = _parked(chat_id, "m-1")
        card._future = asyncio.get_running_loop().create_future()
        return card

    loop = asyncio.new_event_loop()
    try:
        card = loop.run_until_complete(scenario())
        first = client.post(
            f"/api/v2/chats/{chat_id}/interactions/card-1", headers=headers,
            json={"choices": {"q0": ["Tea"]}},
        )
        assert first.status_code == 200, first.text
        assert card._future.result().answers == {"q0": "Tea"}
        second = client.post(
            f"/api/v2/chats/{chat_id}/interactions/card-1", headers=headers,
            json={"choices": {"q0": ["Coffee"]}},
        )
        assert second.status_code == 409
    finally:
        generation_service._RUNNING.pop("m-1", None)
        loop.close()


def test_a_card_serialises_with_its_options(db):
    from lembas.api.v2 import serialise

    card = _parked("c", "m-2")
    try:
        shaped = serialise.interruption(card)
        assert shaped["kind"] == "question" and shaped["id"] == "card-1"
        assert [o["label"] for o in shaped["items"][0]["options"]] == ["Tea", "Coffee"]
    finally:
        generation_service._RUNNING.pop("m-2", None)


# --- Around the chats ----------------------------------------------------------------------
def test_me_models_and_folders(client, db, registered):
    _models(db)
    headers = _bearer(db)
    me = client.get("/api/v2/me", headers=headers).json()
    assert me["account"]["email"] == "frodo@shire.test" and me["api"] == 1
    assert me["permissions"]["chat.create"] is True
    models = client.get("/api/v2/models", headers=headers).json()["models"]
    assert models[0]["id"] == "qwen" and models[0]["vision"] is True
    assert models[0]["efforts"] == ["low", "high"]
    assert client.get("/api/v2/folders", headers=headers).json() == {"folders": []}


def test_an_attachment_is_uploaded_sent_and_fetched(client, db, registered, mock_http):
    _models(db)
    mock_http(_upstream())
    headers = _bearer(db)
    up = client.post(
        "/api/v2/attachments", headers=headers,
        files={"file": ("notes.txt", b"Second breakfast at nine.", "text/plain")},
    )
    assert up.status_code == 201, up.text
    attachment = up.json()["attachment"]
    made = client.post(
        "/api/v2/chats", headers=headers,
        json={"text": "Read this", "attachments": [attachment["id"]]},
    ).json()
    assert made["message"]["attachments"][0]["id"] == attachment["id"]
    got = client.get(f"/api/v2/attachments/{attachment['id']}", headers=headers)
    assert got.status_code == 200 and got.headers["content-disposition"].startswith("attachment")
    # Part of a sent message now: not removable.
    assert client.delete(
        f"/api/v2/attachments/{attachment['id']}", headers=headers
    ).status_code == 409


def test_push_registers_under_the_token_and_goes_when_it_signs_out(
    client, db, registered, monkeypatch
):
    monkeypatch.setattr("lembas.api.push.fetch_service.check_url", lambda url, **_: url)
    headers = _bearer(db)
    key = client.get("/api/v2/push", headers=headers).json()
    assert key["key"] and key["registered"] is False
    assert client.post(
        "/api/v2/push", headers=headers,
        json={"endpoint": "https://ntfy.example/upabc", "p256dh": "BPk", "auth": "c2VjcmV0"},
    ).status_code == 204
    row = db.scalar(select(PushSubscription))
    assert row.token_id is not None and row.label == "LLeMbas for Android"
    assert client.get("/api/v2/push", headers=headers).json()["registered"] is True

    # Not https: refused, as a browser's would be.
    assert client.post(
        "/api/v2/push", headers=headers,
        json={"endpoint": "http://ntfy.example/x", "p256dh": "a", "auth": "b"},
    ).status_code == 400

    assert client.delete("/api/v1/token", headers=headers).status_code in (200, 204)
    db.expire_all()
    assert db.scalar(select(PushSubscription)) is None


def test_api_only_mode_closes_this_door_too(client, db, registered):
    """`/api/v2` is the chats, and API-only mode is the instance without them."""
    client.post("/admin/general", data={"instance_mode": "api"}, follow_redirects=False)
    response = client.get("/api/v2/chats", headers=_bearer(db))
    assert response.status_code == 404
