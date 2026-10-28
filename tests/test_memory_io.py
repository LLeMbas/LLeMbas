"""Settings → Memory: export, import (the 1.7.3 format v1), forget-all."""

from __future__ import annotations

import json

from sqlalchemy import select

from lembas.db.models import Memory, User
from lembas.services import memories, memory_io


def _me(db):
    return db.scalars(select(User).order_by(User.created_at)).first()


def test_export_is_format_v1(client, db, registered):
    user = _me(db)
    memories.add(db, owner=user, content="Prefers metric units.", author="user")
    response = client.get("/api/memories/export")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    document = response.json()
    assert document["format"] == "lembas-export"
    assert document["format_version"] == 1
    assert document["kind"] == "memory"
    assert document["owner"]["email"] == user.email
    item = document["items"][0]
    assert item["type"] == "memory"
    assert item["data"]["content"] == "Prefers metric units."
    assert item["data"]["data_group_ref"] == "default"


def test_a_17_export_imports_and_names_what_it_drops(client, db, registered):
    user = _me(db)
    memories.add(db, owner=user, content="Already here.", author="user")
    document = {
        "format": "lembas-export",
        "format_version": 1,
        "kind": "memory",
        "app_version": "1.7.3",
        "created_at": "2026-10-01T12:00:00Z",
        "owner": {"name": "Frodo", "email": "frodo@shire.test"},
        "counts": {"memory": 4},
        "items": [
            {"type": "memory", "ref": "a", "name": "Second breakfast",
             "data": {"content": "Likes second breakfast.", "author": "model",
                      "data_group": "Default", "data_group_ref": "default"}},
            {"type": "memory", "ref": "b", "name": "Already here.",
             "data": {"content": "Already here.", "author": "user", "data_group_ref": "default"}},
            {"type": "memory", "ref": "c", "name": "empty", "data": {"content": "  "}},
            {"type": "note", "ref": "d", "name": "A note", "data": {}},
        ],
    }
    response = client.post(
        "/api/memories/import",
        files={"file": ("m.json", json.dumps(document).encode(), "application/json")},
        data={"into": ""},
    )
    assert response.status_code == 200
    assert 'data-testid="import-report"' in response.text
    assert "Second breakfast" in response.text
    assert "A note" in response.text and "Already here." in response.text
    contents = {m.content for m in db.scalars(select(Memory).where(Memory.owner_id == user.id))}
    assert contents == {"Already here.", "Likes second breakfast."}


def test_the_header_is_checked_first():
    import pytest

    for raw, word in (
        (b"not json", "JSON"),
        (json.dumps({"format": "other"}).encode(), "LLeMbas"),
        (json.dumps({"format": "lembas-export", "format_version": 2, "kind": "memory",
                     "items": []}).encode(), "version"),
        (json.dumps({"format": "lembas-export", "format_version": 1, "kind": "admin",
                     "items": []}).encode(), "memory"),
    ):
        with pytest.raises(memory_io.Refused) as caught:
            memory_io.load(raw)
        assert word in str(caught.value)


def test_a_refused_file_is_said_on_the_page(client, db, registered):
    response = client.post(
        "/api/memories/import", files={"file": ("x.json", b"{}", "application/json")}
    )
    assert response.status_code == 200
    assert "not a LLeMbas export" in response.text


def test_forget_all_in_one_group(client, db, registered):
    user = _me(db)
    memories.add(db, owner=user, content="One.", author="user")
    memories.add(db, owner=user, content="Two.", author="user")
    response = client.post("/api/memories/purge", data={"group": "default"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert db.scalars(select(Memory).where(Memory.owner_id == user.id)).first() is None


def test_the_memory_tab_has_the_three_forms(client, db, registered):
    page = client.get("/settings?tab=memory").text
    assert 'data-testid="memory-io"' in page
    for action in ("/api/memories/export", "/api/memories/import", "/api/memories/purge"):
        assert f'action="{action}"' in page
