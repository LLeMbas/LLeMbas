"""The composer's `/` and `@` menus through `/api/v2`, for LLeMbas Desktop.

The same code as the page's (`services/menus.py`), so these check the door --
the token, whose device and chat, what the answer looks like -- and that the
two doors agree.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import Attachment, Chat, DataGroup, Message, User
from lembas.security import api_tokens
from lembas.services import device_link, fs
from lembas.services import files as files_service
from tests.conftest import join
from tests.test_sidebar_settings_composer import _device, _enable_agents, _model


@pytest.fixture(autouse=True)
def _clean():
    api_tokens.reset_rates()
    yield
    device_link.LINKS.clear()


def _bearer(db, email="frodo@shire.test") -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == email))
    _, raw = api_tokens.create(db, user, name="LLeMbas Desktop", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def _linked(db, *capabilities: str, answers: dict | None = None):
    """A linked device that answers `_lembas/*` from `answers`, and records
    what it was asked."""
    _model(db)
    _enable_agents(db)
    token = _device(db, "workstation", linked=True)
    link = device_link.LINKS[token.id]
    link.initialized = {"_meta": {"lembas": {"capabilities": list(capabilities)}}}
    asked: list[tuple[str, dict]] = []

    async def request(method, params=None, *, timeout=60):
        asked.append((method, dict(params or {})))
        return (answers or {}).get(method, {})

    link.request = request  # type: ignore[method-assign]
    return token, asked


def _sam(client, db) -> User:
    """A second member. What is theirs is what the first account's token must
    not reach -- an administrator gets nothing for being one."""
    db.commit()
    join(client, {"name": "Sam", "email": "sam@shire.test", "password": "second-breakfast-now"})
    db.expire_all()
    return db.scalar(select(User).where(User.email == "sam@shire.test"))


# --- A device's commands ------------------------------------------------------------
def test_a_devices_commands_for_a_directory(client, db, registered):
    token, asked = _linked(db, "commands", answers={"_lembas/commands": {"commands": [
        {"name": "review", "description": "Review the diff", "input": {"hint": "[base]"},
         "_meta": {"lembas": {"kind": "skill"}}},
        {"name": "not a name", "description": "dropped"},
    ]}})

    got = client.get(
        f"/api/v2/devices/{token.id}/commands", params={"cwd": "/srv/app"},
        headers=_bearer(db),
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["commands"] == [
        {"name": "review", "description": "Review the diff", "hint": "[base]", "kind": "skill"}
    ]
    assert body["device"]
    assert asked == [("_lembas/commands", {"cwd": "/srv/app"})]
    # The page's door says the same.
    page = client.get(f"/api/devices/{token.id}/commands", params={"cwd": "/srv/app"}).json()
    assert page["commands"] == body["commands"]


def test_a_chat_with_a_session_answers_for_its_session(client, db, registered):
    token, asked = _linked(db, "commands")
    owner = db.scalar(select(User))
    chat = Chat(user_id=owner.id, kind="agent", model_id="m", device_token_id=token.id,
                device_session_id="s1", project_dir="/srv/app")
    db.add(chat)
    db.commit()
    device_link.LINKS[token.id].commands["s1"] = [
        {"name": "plan", "description": "Plan it", "hint": "", "kind": ""}
    ]

    got = client.get(
        f"/api/v2/devices/{token.id}/commands", params={"chat": chat.id, "cwd": "/elsewhere"},
        headers=_bearer(db),
    ).json()
    assert [c["name"] for c in got["commands"]] == ["plan"]
    assert asked == [], "what the session announced is what it offers"


def test_a_device_that_cannot_answer_offers_nothing_and_says_nothing(client, db, registered):
    token, asked = _linked(db)  # lists no capabilities
    headers = _bearer(db)
    commands = client.get(
        f"/api/v2/devices/{token.id}/commands", params={"cwd": "/srv"}, headers=headers
    )
    files = client.get(
        f"/api/v2/devices/{token.id}/files", params={"q": "x", "cwd": "/srv"}, headers=headers
    )
    assert commands.json()["commands"] == [] and files.json()["files"] == []
    assert files.json()["error"] == "" and asked == []


# --- A device's files ---------------------------------------------------------------
def test_a_devices_files(client, db, registered):
    token, asked = _linked(db, "files", answers={"_lembas/files/search": {"files": [
        {"path": "README.md", "kind": "file"}, {"path": "src/", "kind": "dir"},
        {"path": "", "kind": "file"}, {"path": "two\nlines", "kind": "file"},
    ]}})

    got = client.get(
        f"/api/v2/devices/{token.id}/files", params={"q": "rea", "cwd": "/srv/app"},
        headers=_bearer(db),
    )
    assert got.status_code == 200, got.text
    assert got.json()["files"] == [
        {"path": "README.md", "kind": "file", "name": "README.md"},
        {"path": "src/", "kind": "dir", "name": "src"},
    ]
    assert asked == [("_lembas/files/search", {"cwd": "/srv/app", "query": "rea", "limit": 20})]


def test_the_device_and_the_chat_must_be_the_callers(client, db, registered):
    token, asked = _linked(db, "files", "commands")
    owner = db.scalar(select(User))
    headers = _bearer(db)
    # No token: this door does not open for the cookie.
    assert client.get(f"/api/v2/devices/{token.id}/commands").status_code == 401
    # A chat on another device.
    stray = Chat(user_id=owner.id, kind="agent", model_id="m", device_token_id="elsewhere")
    db.add(stray)
    db.commit()
    assert client.get(
        f"/api/v2/devices/{token.id}/files", params={"chat": stray.id}, headers=headers
    ).status_code == 404
    # Somebody else's device, linked or not, is not there at all.
    token.user_id = _sam(client, db).id
    db.commit()
    for path in ("commands", "files"):
        assert client.get(
            f"/api/v2/devices/{token.id}/{path}", params={"cwd": "/srv"}, headers=headers
        ).status_code == 404
    assert asked == []


# --- `@` on the server ----------------------------------------------------------------
def _sent(db, chat_id: str, user_id: str, name: str, text: str = "x") -> Attachment:
    message = Message(chat_id=chat_id, role="user", content="see attached")
    db.add(message)
    db.commit()
    row = files_service.store_text(
        db, user_id=user_id, chat_id=chat_id, filename=name, text=text
    )
    row.message_id = message.id
    db.commit()
    return row


def test_mentions_offer_the_chats_attachments_tree_files_and_a_url(
    client, db, registered, make_chat
):
    _model(db)
    chat_id = make_chat()
    owner = db.scalar(select(User))
    sent = _sent(db, chat_id, owner.id, "road-notes.txt")
    home = fs.ensure_home(db, owner).path
    fs.write(fs.view(db, owner), f"{home}/maps/The road goes on.md", b"# Road\n")
    headers = _bearer(db)

    items = client.get(
        "/api/v2/mentions", params={"q": "road", "chat": chat_id}, headers=headers
    ).json()["items"]
    by_kind = {item["kind"]: item for item in items}
    assert by_kind["attachment"]["id"] == sent.id
    assert by_kind["attachment"]["name"] == "road-notes.txt"
    assert by_kind["file"]["path"] == f"{home}/maps/The road goes on.md"
    assert by_kind["file"]["name"] == "The road goes on.md"

    url = client.get(
        "/api/v2/mentions", params={"q": "https://tolkien.test/mallorn"}, headers=headers
    ).json()["items"]
    assert url == [{"kind": "url", "url": "https://tolkien.test/mallorn",
                    "name": "https://tolkien.test/mallorn", "detail": ""}]
    assert client.get("/api/v2/mentions", headers=headers).json() == {"items": []}


def test_mentions_never_offer_what_is_somebody_elses(client, db, registered, make_chat):
    _model(db)
    headers = _bearer(db)
    sam = _sam(client, db)
    home = fs.ensure_home(db, sam).path
    fs.write(fs.view(db, sam), f"{home}/secret-plans.md", b"x")
    theirs = Chat(user_id=sam.id, model_id="m")
    db.add(theirs)
    db.commit()
    _sent(db, theirs.id, sam.id, "secret-notes.txt")

    for params in ({"q": "secret"}, {"q": "secret", "chat": theirs.id}):
        items = client.get("/api/v2/mentions", params=params, headers=headers).json()["items"]
        assert items == [], params


# --- Choosing from `@` -------------------------------------------------------------
def test_an_earlier_attachment_is_copied(client, db, registered, make_chat):
    _model(db)
    chat_id = make_chat()
    owner = db.scalar(select(User))
    sent = _sent(db, chat_id, owner.id, "road-notes.txt", "The road goes ever on.")

    made = client.post(
        "/api/v2/attachments/from", headers=_bearer(db),
        json={"attachment": sent.id, "chat": chat_id},
    )
    assert made.status_code == 201, made.text
    copy = made.json()["attachment"]
    assert copy["id"] != sent.id and copy["name"] == "road-notes.txt"
    row = db.get(Attachment, copy["id"])
    assert row.message_id is None and row.chat_id == chat_id
    assert row.extracted_text == "The road goes ever on."


def test_an_attachment_from_another_data_group_or_person_is_not_available(
    client, db, registered, make_chat
):
    _model(db)
    chat_id = make_chat()
    owner = db.scalar(select(User))
    sent = _sent(db, chat_id, owner.id, "road-notes.txt")
    db.add(DataGroup(id="hosted", name="Hosted"))
    elsewhere = Chat(user_id=owner.id, model_id="m", data_group_id="hosted")
    db.add(elsewhere)
    db.commit()
    headers = _bearer(db)

    crossed = client.post(
        "/api/v2/attachments/from", headers=headers,
        json={"attachment": sent.id, "chat": elsewhere.id},
    )
    assert crossed.status_code == 404
    sam = _sam(client, db)
    other = Chat(user_id=sam.id, model_id="m")
    db.add(other)
    db.commit()
    theirs = _sent(db, other.id, sam.id, "sams-notes.txt")
    assert client.post(
        "/api/v2/attachments/from", headers=headers, json={"attachment": theirs.id}
    ).status_code == 404
    assert client.post(
        "/api/v2/attachments/from", headers=headers, json={"attachment": "nope"}
    ).status_code == 404


def test_a_file_of_the_tree_is_attached_as_it_is_now(client, db, registered, make_chat):
    _model(db)
    chat_id = make_chat()
    owner = db.scalar(select(User))
    home = fs.ensure_home(db, owner).path
    fs.write(fs.view(db, owner), f"{home}/maps/road.md", b"# The road\n\nGoes ever on.\n")
    headers = _bearer(db)

    made = client.post(
        "/api/v2/attachments/from", headers=headers,
        json={"path": f"{home}/maps/road.md", "chat": chat_id},
    )
    assert made.status_code == 201, made.text
    row = db.get(Attachment, made.json()["attachment"]["id"])
    assert row.filename == "road.md" and "Goes ever on." in row.extracted_text
    assert row.source_path == f"{home}/maps/road.md"

    # Missing, a directory, and somebody else's: one answer.
    for path in (f"{home}/maps/gone.md", f"{home}/maps", "/home"):
        refused = client.post(
            "/api/v2/attachments/from", headers=headers, json={"path": path, "chat": chat_id}
        )
        assert refused.status_code == 404, path
    # Somebody else's home: an administrator's token gets nothing for being one.
    sam = _sam(client, db)
    sams = fs.ensure_home(db, sam).path
    fs.write(fs.view(db, sam), f"{sams}/diary.md", b"Dear diary.\n")
    db.commit()
    assert client.post(
        "/api/v2/attachments/from", headers=headers, json={"path": f"{sams}/diary.md"}
    ).status_code == 404


def test_a_urls_text_is_attached(client, db, registered, monkeypatch):
    from lembas.services import fetch as fetch_service

    _model(db)
    seen: list[str] = []

    async def fetched(url, **_):
        seen.append(url)
        if "refused" in url:
            raise fetch_service.FetchError("That address is private or local.")
        return fetch_service.Fetched(url=url, title="Mallorn", text="Golden leaves.")

    monkeypatch.setattr(fetch_service, "fetch", fetched)
    headers = _bearer(db)

    made = client.post(
        "/api/v2/attachments/from", headers=headers, json={"url": " https://tolkien.test/m "}
    )
    assert made.status_code == 201, made.text
    row = db.get(Attachment, made.json()["attachment"]["id"])
    assert row.filename == "Mallorn.txt" and "Golden leaves." in row.extracted_text
    assert seen == ["https://tolkien.test/m"]

    # The fetcher's own refusal, in its own words.
    refused = client.post(
        "/api/v2/attachments/from", headers=headers, json={"url": "http://refused.test/"}
    )
    assert refused.status_code == 400 and "private or local" in refused.json()["detail"]


def test_exactly_one_thing_is_named_and_the_permission_is_the_pages(
    client, db, registered, make_chat
):
    _model(db)
    headers = _bearer(db)
    for body in ({}, {"attachment": "a", "path": "/b"}, {"chat": "c"}):
        assert client.post(
            "/api/v2/attachments/from", headers=headers, json=body
        ).status_code == 400, body
    assert client.post("/api/v2/attachments/from", json={"path": "/x"}).status_code == 401
