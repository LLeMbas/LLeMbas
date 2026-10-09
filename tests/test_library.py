"""The four stores: ingestion, search, memory limits, skill history."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from lembas.db.migrations import ensure_fts
from lembas.db.models import AUTHOR_MODEL, Attachment, Document, User
from lembas.db.session import get_engine
from lembas.security.passwords import hash_password
from lembas.services import files as files_service
from lembas.services.library import documents as documents_service
from lembas.services.library import memories as memories_service
from lembas.services.library import notes as notes_service
from lembas.services.library import skills as skills_service
from lembas.services.library.fts import fts_query


@pytest.fixture
def owner(db):
    user = User(name="Frodo", email="f@shire.test", password_hash=hash_password("x"))
    db.add(user)
    db.commit()
    return user


# --- The index ---------------------------------------------------------------
def test_ensure_fts_is_idempotent():
    """It runs at every startup, like the column sync beside it."""
    assert ensure_fts(get_engine()) == []
    assert ensure_fts(get_engine()) == []


def test_the_query_builder_survives_punctuation():
    """FTS5 MATCH has operators and a quoting rule, so a bare quote or asterisk
    is a syntax error rather than a search that finds nothing."""
    assert fts_query('what "is" a mallorn?') == '"what" AND "is" AND "a" AND "mallorn"'
    assert fts_query("a AND b OR NEAR *") == '"a" AND "AND" AND "b" AND "OR" AND "NEAR"'
    assert fts_query("   ") == ""
    assert fts_query("!!!") == ""


def test_editing_moves_a_note_in_the_index(db, owner):
    """The triggers are what keep an external-content index correct."""
    note = notes_service.create(db, owner=owner, title="Tree", body="A golden tree.")
    assert notes_service.search(db, owner, "golden")

    notes_service.update(db, note, body="A silver tree.")
    assert notes_service.search(db, owner, "golden") == []
    assert notes_service.search(db, owner, "silver")


def test_a_deleted_note_leaves_the_index(db, owner):
    note = notes_service.create(db, owner=owner, title="Tree", body="A golden tree.")
    notes_service.delete(db, note)
    assert notes_service.search(db, owner, "golden") == []


def test_the_description_is_searched_as_well_as_the_contents(db, owner):
    """A line of description is how somebody makes a document findable when its
    own words do not include the term."""
    document = documents_service.store_upload(
        db, owner=owner, payload=b"Opaque contents.", filename="a.txt", title="A"
    )
    document.description = "Everything about invoicing."
    db.commit()
    assert documents_service.search(db, owner, "invoicing")


def test_a_natural_language_question_still_finds_the_document(db, owner):
    """The caller is usually a model, which asks "who built the west gate of
    Moria and what is its password" rather than "moria gate". Requiring every
    term would lose the match on one absent word."""
    documents_service.store_upload(
        db,
        owner=owner,
        payload=b"The west gate of Moria was built by Narvi. The password is mellon.",
        filename="gate.txt",
        title="Moria gate",
    )
    found = documents_service.search(
        db, owner, "who built the west gate of Moria and what is its password"
    )
    assert [d.title for d in found] == ["Moria gate"]


def test_requiring_every_term_still_wins_when_it_can(db, owner):
    """AND first, so a document matching all the words beats one matching some."""
    documents_service.store_upload(
        db, owner=owner, payload=b"Golden mallorn trees of Lothlorien.", filename="a.txt",
        title="Both",
    )
    documents_service.store_upload(
        db, owner=owner, payload=b"Golden light on the water.", filename="b.txt", title="One",
    )
    found = documents_service.search(db, owner, "golden mallorn")
    assert [d.title for d in found] == ["Both"]


def test_a_broken_index_does_not_break_the_page(db, owner):
    """Search degrading to "finds nothing" is bad; a 500 on the library page is
    worse, and a failed statement otherwise poisons the whole session."""
    notes_service.create(db, owner=owner, title="Tree", body="Golden.")
    db.execute(text("DROP TABLE notes_fts"))
    db.commit()

    assert notes_service.search(db, owner, "golden") == []
    # The session must still be usable afterwards.
    assert notes_service.recent(db, owner)


# --- Ingestion ---------------------------------------------------------------
def test_a_document_and_an_attachment_extract_identically(db, owner):
    """Both go through files.prepare, which is what guarantees the same file
    produces the same text whichever way it arrived."""
    payload = b"# Waybread\n\nOne bite is enough."

    document = documents_service.store_upload(
        db, owner=owner, payload=payload, filename="lembas.md"
    )
    attachment = files_service.store(
        db, user_id=owner.id, chat_id=None, payload=payload, filename="lembas.md"
    )
    assert document.extracted_text == attachment.extracted_text
    assert document.kind == attachment.kind


def test_a_document_keeps_its_own_file(db, owner):
    document = documents_service.store_upload(
        db, owner=owner, payload=b"hello", filename="a.txt"
    )
    path = documents_service.stored_path(document.stored_name)
    assert path is not None and path.read_bytes() == b"hello"


def test_the_library_path_check_refuses_an_escape(db):
    """Same resolve-and-check as chat attachments, against a different root."""
    assert documents_service.stored_path("../../etc/passwd") is None
    assert documents_service.stored_path("") is None
    assert documents_service.stored_path(".hidden") is None


def test_attaching_a_document_copies_it(db, owner):
    """History must not change under a conversation because a document was
    edited or deleted later."""
    document = documents_service.store_upload(
        db, owner=owner, payload=b"The ring is round.", filename="ring.txt"
    )
    attachment = files_service.copy_document(
        db, user_id=owner.id, chat_id=None, document=document
    )
    documents_service.delete(db, document)

    db.refresh(attachment)
    assert attachment.extracted_text == "The ring is round."
    assert files_service.stored_path(attachment.stored_name) is not None
    assert db.get(Document, document.id) is None
    assert db.get(Attachment, attachment.id) is not None


# --- Memory ------------------------------------------------------------------
def test_a_memory_is_trimmed_rather_than_refused(db, owner):
    """A tool that writes an essay is told so and can put the long version in a
    note; failing the write would just lose it."""
    memory = memories_service.add(db, owner=owner, content="x" * 5000)
    assert len(memory.content) == memories_service.MAX_MEMORY_CHARS


def test_an_empty_memory_is_refused(db, owner):
    with pytest.raises(ValueError):
        memories_service.add(db, owner=owner, content="   ")


def test_memory_whitespace_is_collapsed(db, owner):
    memory = memories_service.add(db, owner=owner, content="  two\n\nlines  ")
    assert memory.content == "two lines"


def test_the_injected_block_is_bounded(db, owner):
    """Every memory costs tokens on every request, so the block has a ceiling.
    Nothing disappears -- the full list is still in settings."""
    for index in range(200):
        memories_service.add(db, owner=owner, content=f"Fact number {index}. " + "x" * 200)

    block = memories_service.block(db, owner)
    assert len(block) < memories_service.MAX_TOTAL_CHARS + 200
    assert "more, see your settings" in block


def test_the_oldest_memories_survive_truncation(db, owner):
    """A fact that has lasted is likelier to be a standing preference than
    something said once this morning."""
    memories_service.add(db, owner=owner, content="The oldest fact.")
    for index in range(100):
        memories_service.add(db, owner=owner, content=f"Later fact {index}. " + "y" * 200)

    assert "The oldest fact." in memories_service.block(db, owner)


def test_there_is_a_hard_ceiling_on_records(db, owner, monkeypatch):
    monkeypatch.setattr(memories_service, "MAX_RECORDS", 3)
    for index in range(3):
        memories_service.add(db, owner=owner, content=f"Fact {index}")
    with pytest.raises(ValueError, match="note instead"):
        memories_service.add(db, owner=owner, content="One too many")


def test_memory_block_of_nobody_is_empty(db):
    assert memories_service.block(db, None) == ""


# --- Skills ------------------------------------------------------------------
def test_a_skill_name_is_slugified(db, owner):
    skill = skills_service.create(
        db, owner=owner, name="Weekly Report!", description="When asked.", body="x"
    )
    assert skill.name == "weekly-report"


def test_a_skill_needs_a_description(db, owner):
    """It is the only thing the model sees until it opens the skill."""
    with pytest.raises(skills_service.SkillError, match="when to use it"):
        skills_service.create(db, owner=owner, name="thing", description="  ", body="x")


def test_a_duplicate_name_is_refused(db, owner):
    skills_service.create(db, owner=owner, name="report", description="When.", body="x")
    with pytest.raises(skills_service.SkillError, match="already exists"):
        skills_service.create(db, owner=owner, name="report", description="When.", body="y")


def test_every_edit_keeps_what_was_there(db, owner):
    """The whole safety story for a model rewriting its own instructions."""
    skill = skills_service.create(
        db, owner=owner, name="report", description="When.", body="First."
    )
    skills_service.update(db, skill, body="Second.", author=AUTHOR_MODEL)
    db.refresh(skill)

    assert skill.body == "Second."
    assert skill.author == AUTHOR_MODEL
    assert [r.body for r in skill.revisions] == ["First."]


def test_an_edit_that_changes_nothing_writes_no_revision(db, owner):
    skill = skills_service.create(
        db, owner=owner, name="report", description="When.", body="First."
    )
    skills_service.update(db, skill, body="First.", description="When.")
    db.refresh(skill)
    assert skill.revisions == []


def test_reverting_restores_and_is_itself_undoable(db, owner):
    skill = skills_service.create(
        db, owner=owner, name="report", description="When.", body="First."
    )
    skills_service.update(db, skill, body="Second.", author=AUTHOR_MODEL)
    db.refresh(skill)

    skills_service.revert(db, skill, skill.revisions[-1], author="user")
    db.refresh(skill)
    assert skill.body == "First."
    # Going back is undoable too: the state before the revert was kept.
    assert "Second." in [r.body for r in skill.revisions]


def test_the_index_lists_only_enabled_skills(db, owner):
    skills_service.create(db, owner=owner, name="on", description="Use me.", body="x")
    off = skills_service.create(db, owner=owner, name="off", description="Not me.", body="x")
    skills_service.update(db, off, enabled=False)

    index = skills_service.index_block(db, owner)
    assert "on: Use me." in index
    assert "off" not in index


def test_the_index_is_name_and_description_only(db, owner):
    """Bodies are fetched with skill_get; injecting them is what makes a hundred
    skills unaffordable."""
    skills_service.create(
        db, owner=owner, name="report", description="When asked.", body="SECRET STEPS"
    )
    assert "SECRET STEPS" not in skills_service.index_block(db, owner)


def test_a_skill_is_found_by_the_name_a_model_would_use(db, owner):
    skills_service.create(db, owner=owner, name="weekly-report", description="W.", body="x")
    assert skills_service.by_name(db, "Weekly Report", owner) is not None
    assert skills_service.by_name(db, "nope", owner) is None
