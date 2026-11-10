"""The `/api/v2` routes the apps need to catch up with the web (the parity
audit's server half): pinning, folders and moving a chat, search, memory, the
library's writes, language and theme, and a chat's switches. Each goes through
the core its page twin uses, so what holds on the web holds here."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import Chat, User
from lembas.security import api_tokens


@pytest.fixture(autouse=True)
def _fresh_rates():
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()
    yield


def _bearer(db, email="frodo@shire.test", scopes=("app",)) -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == email))
    _, raw = api_tokens.create(db, user, name="LLeMbas for Android", scopes=list(scopes))
    return {"Authorization": f"Bearer {raw}"}


# --- Pin ---------------------------------------------------------------------------------------
def test_a_chat_is_pinned_and_unpinned_through_patch(client, db, registered, make_chat):
    chat_id = make_chat()
    headers = _bearer(db)
    pinned = client.patch(f"/api/v2/chats/{chat_id}", headers=headers, json={"pinned": True})
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["chat"]["pinned"] is True
    # Absent leaves it alone.
    renamed = client.patch(f"/api/v2/chats/{chat_id}", headers=headers, json={"title": "Kept"})
    assert renamed.json()["chat"]["pinned"] is True
    listed = client.get("/api/v2/chats", headers=headers).json()["chats"]
    assert [c["pinned"] for c in listed if c["id"] == chat_id] == [True]
    client.patch(f"/api/v2/chats/{chat_id}", headers=headers, json={"pinned": False})
    db.expire_all()
    assert db.get(Chat, chat_id).pinned is False


def test_the_web_pin_moves_the_row_into_pinned_chats(client, db, registered, make_chat):
    first, second = make_chat(), make_chat()
    page = client.get("/chat").text
    assert 'data-testid="pinned-chats"' not in page
    assert 'data-testid="chat-pin"' in page
    response = client.patch(f"/api/chats/{second}", data={"pinned": "1"})
    # The sidebar comes back to swap in, as for archiving.
    assert response.status_code == 200 and 'id="sidebar-tree"' in response.text
    pinned_part, _, rest = response.text.partition('id="section-chats"')
    assert 'data-testid="pinned-chats"' in pinned_part and second in pinned_part
    assert second not in rest and first in rest
    client.patch(f"/api/chats/{second}", data={"pinned": "0"})
    db.expire_all()
    assert db.get(Chat, second).pinned is False


# --- Folders -----------------------------------------------------------------------------------
def test_folders_are_made_renamed_nested_and_deleted_and_a_chat_moves(
    client, db, registered, make_chat
):
    headers = _bearer(db)
    made = client.post("/api/v2/folders", headers=headers, json={"name": "  Waybread  "})
    assert made.status_code == 201, made.text
    outer = made.json()["folder"]
    assert outer["name"] == "Waybread" and outer["parent_id"] is None
    inner = client.post(
        "/api/v2/folders", headers=headers, json={"name": "Recipes", "parent_id": outer["id"]}
    ).json()["folder"]
    assert inner["parent_id"] == outer["id"]
    assert client.post("/api/v2/folders", headers=headers, json={"name": " "}).status_code == 400

    renamed = client.patch(
        f"/api/v2/folders/{inner['id']}", headers=headers, json={"name": "Old recipes"}
    )
    assert renamed.json()["folder"]["name"] == "Old recipes"
    # Never into itself or its own subtree.
    assert client.patch(
        f"/api/v2/folders/{outer['id']}", headers=headers, json={"parent_id": inner["id"]}
    ).status_code == 400
    top = client.patch(
        f"/api/v2/folders/{inner['id']}", headers=headers, json={"parent_id": None}
    ).json()["folder"]
    assert top["parent_id"] is None

    chat_id = make_chat()
    moved = client.patch(
        f"/api/v2/chats/{chat_id}", headers=headers, json={"folder_id": outer["id"]}
    ).json()["chat"]
    assert moved["folder_id"] == outer["id"]
    listed = client.get("/api/v2/folders", headers=headers).json()["folders"]
    assert {f["name"] for f in listed} == {"Waybread", "Old recipes"}

    # Deleting a folder never deletes a chat: it falls back to no folder.
    assert client.delete(f"/api/v2/folders/{outer['id']}", headers=headers).status_code == 204
    db.expire_all()
    assert db.get(Chat, chat_id).folder_id is None


def test_someone_elses_folder_is_not_found(client, db, registered):
    from tests.conftest import join

    headers = _bearer(db)
    folder = client.post("/api/v2/folders", headers=headers, json={"name": "Mine"}).json()
    join(client, {"name": "Sam", "email": "sam@shire.test", "password": "po-tay-toes-boil"})
    theirs = _bearer(db, "sam@shire.test")
    fid = folder["folder"]["id"]
    # A member may lack folder.manage (403) or the app door; either way the
    # folder is not theirs and stays as it was.
    assert client.patch(
        f"/api/v2/folders/{fid}", headers=theirs, json={"name": "x"}
    ).status_code in (403, 404)
    assert client.delete(f"/api/v2/folders/{fid}", headers=theirs).status_code in (403, 404)
    from lembas.db.models import Folder

    db.expire_all()
    assert db.get(Folder, fid).name == "Mine"


# --- Search ------------------------------------------------------------------------------------
def test_search_finds_chats_by_title_and_words_and_memories(client, db, registered, make_chat):
    from lembas.db.models import Message

    chat_id = make_chat()
    db.add(Message(chat_id=chat_id, role="user", content="How long does <b>lembas</b> keep?"))
    db.commit()
    headers = _bearer(db)
    client.post("/api/v2/memories", headers=headers, json={"content": "Likes lembas warm."})
    found = client.get("/api/v2/search", headers=headers, params={"q": "lembas"}).json()
    assert found["in"] == "all" and "memories" in found["offered"]
    by_kind = {section["kind"]: section for section in found["results"]}
    (chat_hit,) = by_kind["chats"]["hits"]
    assert chat_hit["id"] == chat_id and chat_hit["kind"] == "chat"
    # Plain text, the match given as a range: no markup travels.
    assert "<mark>" not in chat_hit["snippet"] and "<b>lembas</b>" in chat_hit["snippet"]
    start, end = chat_hit["marks"][0]
    assert chat_hit["snippet"][start:end].lower() == "lembas"
    (memory_hit,) = by_kind["memories"]["hits"]
    assert memory_hit["kind"] == "memory" and memory_hit["snippet"] == "Likes lembas warm."
    only = client.get(
        "/api/v2/search", headers=headers, params={"q": "lembas", "in": "memories"}
    ).json()
    assert [s["kind"] for s in only["results"]] == ["memories"]
    assert client.get("/api/v2/search", headers=headers, params={"q": " "}).json()["results"] == []


def test_the_snippet_reader_keeps_text_and_ranges():
    from lembas.api import search as search_web
    from lembas.api.v2.library import plain

    text, marks = plain(search_web.snippet("a <tag> & lembas bread", "lembas"))
    assert text == "a <tag> & lembas bread" and marks == [[10, 16]]


# --- Memory ------------------------------------------------------------------------------------
def test_memories_are_listed_added_edited_and_removed(client, db, registered):
    headers = _bearer(db)
    added = client.post("/api/v2/memories", headers=headers, json={"content": " Prefers  tea. "})
    assert added.status_code == 201, added.text
    memory = added.json()["memory"]
    assert memory["content"] == "Prefers tea." and memory["author"] == "user"
    assert memory["data_group"]["id"]
    # An exact repeat is the same record.
    again = client.post("/api/v2/memories", headers=headers, json={"content": "Prefers tea."})
    assert again.json()["memory"]["id"] == memory["id"]
    assert client.post("/api/v2/memories", headers=headers, json={"content": ""}).status_code == 400
    edited = client.patch(
        f"/api/v2/memories/{memory['id']}", headers=headers, json={"content": "Prefers coffee."}
    )
    assert edited.json()["memory"]["content"] == "Prefers coffee."
    listed = client.get("/api/v2/memories", headers=headers).json()
    assert [m["content"] for m in listed["memories"]] == ["Prefers coffee."]
    assert listed["groups"]
    assert client.delete(f"/api/v2/memories/{memory['id']}", headers=headers).status_code == 204
    assert client.get("/api/v2/memories", headers=headers).json()["memories"] == []
    assert client.delete(f"/api/v2/memories/{memory['id']}", headers=headers).status_code == 404


# --- Library writes ----------------------------------------------------------------------------
def test_the_library_is_written_as_the_explorer_writes(client, db, registered):
    headers = _bearer(db)
    home = client.get("/api/v2/files", headers=headers).json()["path"]
    made = client.post("/api/v2/files/mkdir", headers=headers, json={"path": home, "name": "notes"})
    assert made.status_code == 201, made.text
    folder = f"{home.rstrip('/')}/notes"
    assert any(e["path"] == folder and e["kind"] == "dir" for e in made.json()["entries"])

    up = client.post(
        "/api/v2/files/upload", headers=headers, params={"path": folder},
        files={"files": ("bread.md", b"# Lembas\n", "text/markdown")},
    )
    assert up.status_code == 201, up.text
    assert [e["name"] for e in up.json()["entries"]] == ["bread.md"]

    renamed = client.post(
        "/api/v2/files/rename", headers=headers,
        json={"path": f"{folder}/bread.md", "name": "waybread.md"},
    )
    assert [e["name"] for e in renamed.json()["entries"]] == ["waybread.md"]
    assert client.post(
        "/api/v2/files/rename", headers=headers,
        json={"path": f"{folder}/waybread.md", "name": "a/b"},
    ).status_code == 400

    moved = client.post(
        "/api/v2/files/move", headers=headers, json={"path": f"{folder}/waybread.md", "to": home}
    )
    assert moved.status_code == 200 and moved.json()["path"] == home
    assert any(e["name"] == "waybread.md" for e in moved.json()["entries"])
    text = client.get(
        "/api/v2/files/content", headers=headers, params={"path": f"{home.rstrip('/')}/waybread.md"}
    ).json()["text"]
    assert text == "# Lembas\n"

    gone = client.delete(
        "/api/v2/files", headers=headers, params={"path": f"{home.rstrip('/')}/waybread.md"}
    )
    assert gone.status_code == 204
    listed = client.get("/api/v2/files", headers=headers, params={"path": home}).json()
    assert all(e["name"] != "waybread.md" for e in listed["entries"])
    # A refusal is the explorer's, as a 400 with its reason.
    missing = client.post(
        "/api/v2/files/rename", headers=headers, json={"path": f"{folder}/none.md", "name": "x"}
    )
    assert missing.status_code == 400 and missing.json()["detail"]


# --- Me ----------------------------------------------------------------------------------------
def test_language_and_theme_are_set_on_me(client, db, registered):
    headers = _bearer(db, scopes=("app", "models"))
    me = client.get("/api/v2/me", headers=headers).json()
    assert set(me["scopes"]) == {"app", "models"}
    assert {"id": "sk", "name": "Slovenčina"} in me["languages"]
    assert {t["id"] for t in me["themes"]} >= {"moria", "shire"}
    assert me["language_setting"] == ""
    changed = client.patch("/api/v2/me", headers=headers, json={"language": "sk", "theme": "shire"})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["language"], body["language_setting"], body["theme"]) == ("sk", "sk", "shire")
    # Refusals in the language just chosen.
    refused = client.patch("/api/v2/me", headers=headers, json={"theme": "mordor"})
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Táto téma nie je k dispozícii."
    assert client.patch("/api/v2/me", headers=headers, json={"language": "xx"}).status_code == 400
    back = client.patch("/api/v2/me", headers=headers, json={"language": ""}).json()
    assert back["language_setting"] == ""


# --- Scope -------------------------------------------------------------------------------------
def test_a_chats_switches_are_read_and_set(client, db, registered, make_chat):
    from lembas.db.models import Connection, Model
    from lembas.services.crypto import encrypt

    connection = Connection(name="local", base_url="http://upstream.test/v1",
                            api_key_encrypted=encrypt("k"))
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="qwen", display_name="Qwen",
                 capabilities_json={"tools": True}))
    db.commit()
    chat_id = make_chat()
    headers = _bearer(db)
    scope = client.get(f"/api/v2/chats/{chat_id}/scope", headers=headers).json()["scope"]
    assert scope["families"] and all(f["on"] for f in scope["families"])
    gate = scope["families"][0]["gate"]
    off = client.post(
        f"/api/v2/chats/{chat_id}/scope", headers=headers,
        json={"kind": "family", "name": gate, "on": False},
    ).json()["scope"]
    assert [f["on"] for f in off["families"] if f["gate"] == gate] == [False]
    on = client.post(
        f"/api/v2/chats/{chat_id}/scope", headers=headers,
        json={"kind": "family", "name": gate, "on": True},
    ).json()["scope"]
    assert [f["on"] for f in on["families"] if f["gate"] == gate] == [True]
    assert client.post(
        f"/api/v2/chats/{chat_id}/scope", headers=headers, json={"kind": "x", "name": "y"}
    ).status_code == 400

    chat = db.get(Chat, chat_id)
    chat.scope_json = {"allow": ["bash:ls *"]}
    db.commit()
    assert client.get(f"/api/v2/chats/{chat_id}/scope", headers=headers).json()["scope"][
        "allow"
    ] == ["bash:ls *"]
    cleared = client.post(f"/api/v2/chats/{chat_id}/allow/clear", headers=headers).json()
    assert cleared["scope"]["allow"] == []
