"""The 1.x library from `lembas.db.old` into files under `/Imported/`.

The old database is built from the 1.7.3 shape of the tables the import reads
(`fixtures/old_1x/schema.sql`, dumped from a fresh 1.7.3 database), with rows
in it -- a migration test that starts from an empty database is not testing
the migration.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from lembas.config import settings
from lembas.db.models import Memory, NodeVersion, User
from lembas.services import fs, import_old, organisations

SCHEMA = Path(__file__).parent / "fixtures" / "old_1x" / "schema.sql"
NOW = "2026-01-01 00:00:00.000000"


def _old(path: Path, *, wal: bool = False) -> None:
    con = sqlite3.connect(path)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA.read_text())

    def add(table: str, **row):
        row.setdefault("created_at", NOW)
        row.setdefault("updated_at", NOW)
        keys = ",".join(row)
        con.execute(f"INSERT INTO {table} ({keys}) VALUES ({','.join('?' * len(row))})",
                    tuple(row.values()))

    for uid, email in (("u1", "frodo@shire.test"), ("u2", "sam@shire.test")):
        add("users", id=uid, email=email, name=email[:4], password_hash="x", role="user",
            active=1, settings_json="{}")
    add("notes", id="n1", owner_id="u1", title="Ring / lore", body="It is precious.",
        author="user")
    add("notes", id="n2", owner_id="u1", title="Ring / lore", body="A second one.",
        author="model")
    add("reports", id="r1", owner_id="u1", title="Weekly", summary="All quiet.",
        body="Nothing to say.", source="manual", source_id="", schedule_id="", model_id="",
        unread=0, unread_notified=0, error="")
    add("reports", id="r2", owner_id="u1", title="Broken", summary="", body="", source="schedule",
        source_id="", schedule_id="", model_id="", unread=0, unread_notified=0, error="timeout")
    add("skills", id="s1", owner_id="u1", name="pipe-weed", description="Use when smoking.",
        body="Second version.", enabled=1, author="user")
    add("skill_revisions", id="sr1", skill_id="s1", description="Use when smoking.",
        body="First version.", author="user", note="", created_at="2025-01-01 00:00:00.000000")
    add("skills", id="s2", owner_id="u1", name="Bad Name!", description="d", body="b", enabled=1,
        author="user")
    add("knowledge_bases", id="k1", owner_id="u1", name="Maps", description="")
    library = settings.uploads_dir / "library"
    library.mkdir(parents=True, exist_ok=True)
    (library / "abc123.pdf").write_bytes(b"%PDF-1.4 the map")
    doc = {"owner_id": "u1", "description": "", "source": "upload", "source_url": "",
           "filename": "", "stored_name": "", "media_type": "", "size_bytes": 0, "kind": "text",
           "width": 0, "height": 0, "extracted_text": "", "pages": 0, "truncated": 0,
           "extraction_error": ""}
    add("documents", **{**doc, "id": "d1", "base_id": "k1", "title": "Map", "filename": "map.pdf",
                        "stored_name": "abc123.pdf", "media_type": "application/pdf"})
    add("documents", **{**doc, "id": "d2", "base_id": "k1", "title": "Gone file",
                        "stored_name": "missing.pdf", "extracted_text": "What was in it."})
    add("documents", **{**doc, "id": "d3", "base_id": None, "title": "Link", "source": "link",
                        "source_url": "https://example.test/a", "extracted_text": "Page text."})
    add("documents", **{**doc, "id": "d4", "base_id": "k1", "title": "Empty",
                        "stored_name": "nope.pdf"})
    add("data_groups", id="default", name="Default", description="", position=0, owner_id=None,
        embedding_model_id="", embedding_connection_id="", review_model_id="",
        review_connection_id="")
    add("memories", id="m1", owner_id="u1", content="Likes mushrooms.", author="model",
        data_group_id=None)
    add("notes", id="n3", owner_id="u2", title="Sam's", body="Taters.", author="user")
    con.commit()
    con.close()


@pytest.fixture
def frodo(db):
    org = organisations.default(db)
    user = User(email="frodo@shire.test", name="Frodo", password_hash="x", org_id=org.id)
    db.add(user)
    db.commit()
    fs.ensure_home(db, user)
    db.commit()
    return user


def _old_path() -> Path:
    path = import_old.old_path()
    _old(path)
    return path


def _read(db, user, path: str) -> str:
    return fs.read(fs.view(db, user), path).decode()


def test_one_account_lands_under_imported(db, frodo):
    _old_path()
    report = import_old.run(db, frodo, from_email="frodo@shire.test")
    taken = {e.name for e in report.taken}
    assert "/Imported/Notes/Ring lore.md" in taken
    assert "/Imported/Notes/Ring lore (2).md" in taken
    assert _read(db, frodo, "/Imported/Notes/Ring lore.md").startswith("# Ring / lore\n")
    assert "*All quiet.*" in _read(db, frodo, "/Imported/Reports/Weekly.md")
    # The original upload, byte for byte; the text where the file is gone.
    assert fs.read(fs.view(db, frodo), "/Imported/Knowledge/Maps/map.pdf") == b"%PDF-1.4 the map"
    assert "What was in it." in _read(db, frodo, "/Imported/Knowledge/Maps/Gone file.md")
    assert "<https://example.test/a>" in _read(db, frodo, "/Imported/Knowledge/Link.md")
    skill = _read(db, frodo, "/Imported/Skills/pipe-weed/SKILL.md")
    assert skill.startswith("---\nname: pipe-weed\n") and "Second version." in skill
    node = fs.lookup(db, frodo.org_id, "/Imported/Skills/pipe-weed/SKILL.md")
    versions = db.scalars(select(NodeVersion).where(NodeVersion.node_id == node.id)).all()
    assert len(versions) == 2  # the revision, then now
    # The owner owns it.
    assert fs.lookup(db, frodo.org_id, "/Imported").owner_id == frodo.id
    skipped = {(e.kind, e.name): e.why_english() for e in report.skipped}
    assert skipped[("report", "Broken")] == "it failed in 1.x"
    assert ("document", "Empty") in skipped
    assert "/Imported/Skills/bad-name/SKILL.md" in taken  # a 1.x name made a skill's
    assert any("memories were left" in n.why_english() for n in report.notes)
    # Not imported without asking.
    assert db.scalar(select(Memory).where(Memory.owner_id == frodo.id)) is None


def test_running_it_again_duplicates_nothing(db, frodo):
    _old_path()
    first = import_old.run(db, frodo, from_email="frodo@shire.test")
    second = import_old.run(db, frodo, from_email="frodo@shire.test")
    assert second.taken == []
    assert len(second.skipped) >= len(first.taken)
    assert all(e.why_english() != "" for e in second.skipped)


def test_two_accounts_get_a_directory_each(db, frodo):
    _old_path()
    import_old.run(db, frodo)
    assert fs.lookup(db, frodo.org_id, "/Imported/frodo/Notes/Ring lore.md") is not None
    assert fs.lookup(db, frodo.org_id, "/Imported/sam/Notes/Sam's.md") is not None


def test_memories_only_when_asked_and_only_to_the_same_email(db, frodo):
    _old_path()
    report = import_old.run(db, frodo, memories=True)
    assert [m.content for m in db.scalars(select(Memory))] == ["Likes mushrooms."]
    assert any(e.kind == "memory" for e in report.taken)


def test_the_old_file_is_read_only_and_a_wal_is_folded_in(db, frodo):
    path = import_old.old_path()
    _old(path, wal=True)
    before = path.read_bytes()
    import_old.run(db, frodo, from_email="frodo@shire.test")
    assert path.read_bytes() == before
    assert not list(settings.data_dir.glob(".import-*"))


def test_refusals(db, frodo):
    with pytest.raises(import_old.Refused):
        import_old.run(db, frodo)  # no .old
    _old_path()
    with pytest.raises(import_old.Refused):
        import_old.run(db, frodo, from_email="gandalf@shire.test")


def test_safe_names():
    assert import_old.safe_name("a/b\\c:d", suffix=".md") == "a b c d.md"
    assert import_old.safe_name("...", suffix=".md") == "Untitled.md"
    assert import_old.safe_name("x.md", suffix=".md") == "x.md"
    assert len(import_old.safe_name("y" * 500, suffix=".md")) == import_old.MAX_NAME


def test_the_admin_page_imports(client, db, registered):
    _old_path()
    page = client.get("/admin/import")
    assert page.status_code == 200
    assert "lembas.db.old" in page.text
    me = db.scalar(select(User).where(User.email == registered["email"]))
    response = client.post("/admin/import/old", data={"owner": me.id, "from_email": ""})
    assert response.status_code == 200
    assert "/Imported/" in response.text
