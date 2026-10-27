"""The library at the HTTP boundary: who may read, who may write, who may delete.

`tests/test_library.py` covers the four stores by calling the services. That
leaves untested the layer where the answers to "is this yours?" actually live:
the router's single `library.use` dependency, the `sharing.can_write` check in
front of every write, and the three ways a route says no -- a redirect to the
login page, a 403, and a 404.

Every assertion here is on a database row. A route that answers 403 and changes
the row anyway, or answers 200 and changes nothing, is exactly the failure this
file exists to catch, and neither is visible from a status code alone.

**404 versus 403 is a real distinction here, not an accident.** 404 means "you
cannot see this at all", and it is deliberately the same answer a missing id
gets, so probing ids tells a stranger nothing. 403 means "you can see it and it
is not yours to change" -- reachable only through a share, which grants reading
only. The deletes fold both into 404, and the tests below say so where it
happens rather than pretending it is uniform.
"""

from __future__ import annotations

import re

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import (
    Document,
    KnowledgeBase,
    Memory,
    Note,
    Share,
    Skill,
    SkillRevision,
    User,
)
from lembas.services import settings_store, sharing
from lembas.services.library import documents as documents_service
from lembas.services.library import memories as memories_service
from lembas.services.library import notes as notes_service
from lembas.services.library import skills as skills_service
from tests.conftest import join

STRANGER = {"name": "Sam", "email": "sam@shire.test", "password": "gardening-is-hard"}


# --- People -------------------------------------------------------------------
@pytest.fixture
def owner(db, registered) -> User:
    """The first account, which is therefore the administrator."""
    return db.scalars(select(User).order_by(User.created_at)).first()


@pytest.fixture
def stranger(client, registered) -> TestClient:
    """A second signed-in account, in its own browser.

    Registered second on purpose: the first account becomes the administrator,
    and an administrator bypasses every permission. Ownership questions have to
    be asked of somebody who does not.
    """
    other = TestClient(client.app)
    response = join(other, STRANGER)
    assert response.status_code == 303, response.text
    return other


@pytest.fixture
def stranger_user(db, stranger) -> User:
    return db.scalar(select(User).where(User.email == STRANGER["email"]))


@pytest.fixture
def public(monkeypatch):
    """Make every hostname resolve to a public address, resolving nothing."""
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )


# --- Rows ---------------------------------------------------------------------
def _base(db, owner: User, name: str = "Papers") -> KnowledgeBase:
    return documents_service.create_base(db, owner=owner, name=name)


def _document(db, owner: User, base: KnowledgeBase | None = None, **kwargs) -> Document:
    fields = {
        "payload": b"The west gate of Moria was built by Narvi.",
        "filename": "gate.txt",
        "title": "Gate",
        **kwargs,
    }
    return documents_service.store_upload(db, owner=owner, base=base, **fields)


def _note(db, owner: User, title: str = "Mellon", body: str = "Speak friend.") -> Note:
    return notes_service.create(db, owner=owner, title=title, body=body)


def _skill(db, owner: User, name: str = "weekly-report", body: str = "Step one.") -> Skill:
    return skills_service.create(
        db, owner=owner, name=name, description="When a week ends.", body=body
    )


def _memory(db, owner: User, content: str = "Prefers metric units.") -> Memory:
    return memories_service.add(db, owner=owner, content=content)


# --- The one gate in front of everything --------------------------------------
def _routes():
    """Every route this router serves, with its path parameters filled in.

    Read off the router rather than listed by hand, so a route added tomorrow is
    covered by the permission test the day it appears.
    """
    from lembas.api import library

    for route in library.router.routes:
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, re.sub(r"\{[^}]+\}", "no-such-id", route.path)


def test_every_route_is_behind_library_use(db, client, stranger):
    """The gate is one dependency on the router, so a route added without a
    thought inherits it -- and a route moved to another router silently loses it.

    Without this, somebody with the library switched off keeps a working set of
    URLs: the pages vanish from their sidebar and every one of them still answers.
    Asserted against a fabricated id so nothing here depends on what exists.
    """
    settings_store.update(db, {"default_permissions": {"library.use": False}})

    refused = []
    for method, path in _routes():
        response = stranger.request(method, path, follow_redirects=False)
        refused.append((method, path, response.status_code))

    assert [row for row in refused if row[2] != 403] == []
    assert len(refused) >= 28, "the router lost routes; this test is no longer covering them"


def test_the_gate_is_checked_before_the_write_happens(db, client, stranger, stranger_user):
    """A guard that runs after the row is written is not a guard. Asserted on the
    absence of the note rather than on the status code, because a 403 returned
    over a completed write looks identical from the outside."""
    settings_store.update(db, {"default_permissions": {"library.use": False}})

    stranger.post(
        "/api/library/notes", data={"title": "Smuggled", "body": "x"}, follow_redirects=False
    )

    db.expire_all()
    assert db.scalars(select(Note)).all() == []


def test_an_administrator_bypasses_the_permission(db, client, registered, owner):
    """Deliberate, and the same rule the rest of the codebase follows: an admin
    can grant themselves the permission in two clicks, so withholding it is
    theatre. Worth pinning because the ownership tests below rely on the
    *opposite* being true of sharing."""
    settings_store.update(db, {"default_permissions": {"library.use": False}})

    client.post("/api/library/notes", data={"title": "Mine", "body": "x"}, follow_redirects=False)

    db.expire_all()
    assert [n.title for n in db.scalars(select(Note))] == ["Mine"]


def test_signed_out_changes_nothing(db, client, registered, owner):
    """A delete needs a session. Without this, a link somebody was sent removes
    a note from an account nobody is signed in to."""
    note = _note(db, owner)
    nobody = TestClient(client.app)

    nobody.post(f"/api/library/notes/{note.id}/delete", follow_redirects=False)

    db.expire_all()
    assert db.get(Note, note.id) is not None


# --- Reading somebody else's work ---------------------------------------------
def test_a_strangers_note_is_not_readable(db, client, stranger, owner):
    """404 rather than 403: an id somebody does not own must be indistinguishable
    from an id that does not exist, or the detail page becomes an oracle for
    which notes are on the instance."""
    note = _note(db, owner, title="Private", body="mellon")

    response = stranger.get(f"/library/notes/{note.id}")

    assert response.status_code == 404
    assert "mellon" not in response.text


def test_a_strangers_skill_is_not_readable(db, client, stranger, owner):
    skill = _skill(db, owner, body="The secret procedure.")

    response = stranger.get(f"/library/skills/{skill.id}")

    assert response.status_code == 404
    assert "The secret procedure." not in response.text


def test_a_strangers_base_is_not_readable(db, client, stranger, owner):
    base = _base(db, owner, name="Contracts")
    _document(db, owner, base, title="The lease")

    response = stranger.get(f"/library/knowledge/{base.id}")

    assert response.status_code == 404
    assert "The lease" not in response.text


def test_a_strangers_document_is_not_readable(db, client, stranger, owner):
    """Visibility comes from the base, never the document -- so this is also the
    check that `documents_service.get` resolves through the base rather than
    trusting an id it was handed."""
    document = _document(db, owner, _base(db, owner))

    response = stranger.get(f"/library/knowledge/document/{document.id}")

    assert response.status_code == 404


def test_a_strangers_document_file_is_not_served(db, client, stranger, owner):
    """The bytes, not the page. A route that authorises the detail view and
    serves the file to anyone is a library that is private in the UI only."""
    document = _document(db, owner, _base(db, owner), payload=b"Narvi built it.")

    response = stranger.get(f"/api/library/documents/{document.id}/content")

    assert response.status_code == 404
    assert b"Narvi built it." not in response.content


def test_an_administrator_cannot_read_somebody_elses_note(db, client, registered, owner):
    """`permissions.resolve` gives an admin everything and `services/sharing.py`
    deliberately has no admin branch. Reading somebody's private notes is not
    configuration, and being able to reach the database is not being invited."""
    other = User(email="merry@shire.test", name="Merry", password_hash="x")
    db.add(other)
    db.commit()
    note = _note(db, other, title="Theirs", body="not for you")

    assert owner.is_admin is True
    response = client.get(f"/library/notes/{note.id}")

    assert response.status_code == 404
    assert "not for you" not in response.text


def test_a_shared_note_is_readable(db, client, stranger, stranger_user, owner):
    """The other half of the rule above: a grant does reach the detail page.
    Without this passing, the refusals could be a route that refuses everybody."""
    note = _note(db, owner, title="Shared", body="Speak friend.")
    sharing.set_grants(db, note, user_ids=[stranger_user.id], group_ids=[])

    response = stranger.get(f"/library/notes/{note.id}")

    assert response.status_code == 200
    assert "Speak friend." in response.text


# --- Writing somebody else's work ---------------------------------------------
def test_a_stranger_cannot_edit_a_note(db, client, stranger, owner):
    note = _note(db, owner, title="Mine", body="Original.")

    stranger.post(
        f"/api/library/notes/{note.id}",
        data={"title": "Theirs now", "body": "Rewritten."},
        follow_redirects=False,
    )

    db.expire_all()
    assert db.get(Note, note.id).body == "Original."


def test_a_stranger_cannot_delete_a_note(db, client, stranger, owner):
    note = _note(db, owner)

    stranger.post(f"/api/library/notes/{note.id}/delete", follow_redirects=False)

    db.expire_all()
    assert db.get(Note, note.id) is not None


def test_a_shared_note_is_read_only(db, client, stranger, stranger_user, owner):
    """Sharing grants reading and nothing else. Two people editing one note with
    no history and no merge is worse than the inconvenience of copying it.

    The two refusals differ and that is what this pins: `update` answers 403
    because the reader can see the note, while `delete` folds "not visible" and
    "not yours" into one 404. Both refuse; only the wording differs.
    """
    note = _note(db, owner, title="Shared", body="Original.")
    sharing.set_grants(db, note, user_ids=[stranger_user.id], group_ids=[])

    edited = stranger.post(
        f"/api/library/notes/{note.id}", data={"body": "Rewritten."}, follow_redirects=False
    )
    removed = stranger.post(f"/api/library/notes/{note.id}/delete", follow_redirects=False)

    assert edited.status_code == 403
    assert removed.status_code == 404
    db.expire_all()
    assert db.get(Note, note.id).body == "Original."


def test_a_stranger_cannot_edit_a_skill(db, client, stranger, owner):
    """A skill is instructions a model follows without being asked twice.
    Somebody else editing one is somebody else's words in your model's prompt."""
    skill = _skill(db, owner, body="Original.")

    stranger.post(
        f"/api/library/skills/{skill.id}",
        data={"description": "Changed.", "body": "Rewritten.", "enabled": "on"},
        follow_redirects=False,
    )

    db.expire_all()
    assert db.get(Skill, skill.id).body == "Original."


def test_a_stranger_cannot_delete_a_skill(db, client, stranger, owner):
    skill = _skill(db, owner)

    stranger.post(f"/api/library/skills/{skill.id}/delete", follow_redirects=False)

    db.expire_all()
    assert db.get(Skill, skill.id) is not None


def test_a_stranger_cannot_revert_a_skill(db, client, stranger, owner):
    """Revert is a write dressed as history: it replaces the body with an older
    one. A route that checked only "does this revision exist" would let anybody
    roll back anybody's skill."""
    skill = _skill(db, owner, body="First.")
    skills_service.update(db, skill, body="Second.")
    revision = skill.revisions[0]

    stranger.post(
        f"/api/library/skills/{skill.id}/revert/{revision.id}", follow_redirects=False
    )

    db.expire_all()
    assert db.get(Skill, skill.id).body == "Second."


def test_a_stranger_cannot_rename_a_base(db, client, stranger, owner):
    base = _base(db, owner, name="Contracts")

    stranger.post(
        f"/api/library/bases/{base.id}", data={"name": "Theirs"}, follow_redirects=False
    )

    db.expire_all()
    assert db.get(KnowledgeBase, base.id).name == "Contracts"


def test_a_shared_base_cannot_be_renamed_or_deleted(db, client, stranger, stranger_user, owner):
    """A base is the unit of sharing, so somebody who was given one has the
    strongest claim to being able to change it -- and still cannot. Renaming it
    would change what it says on the owner's own page."""
    base = _base(db, owner, name="Contracts")
    _document(db, owner, base)
    sharing.set_grants(db, base, user_ids=[stranger_user.id], group_ids=[])

    renamed = stranger.post(
        f"/api/library/bases/{base.id}", data={"name": "Theirs"}, follow_redirects=False
    )
    removed = stranger.post(f"/api/library/bases/{base.id}/delete", follow_redirects=False)

    assert renamed.status_code == 403
    assert removed.status_code == 404
    db.expire_all()
    assert db.get(KnowledgeBase, base.id).name == "Contracts"
    assert len(db.scalars(select(Document)).all()) == 1


def test_a_stranger_cannot_delete_a_base(db, client, stranger, owner):
    """Not visible at all, so 404 -- and the documents inside it survive, which
    is the part that matters: `delete_base` takes its contents with it."""
    base = _base(db, owner, name="Contracts")
    _document(db, owner, base)

    stranger.post(f"/api/library/bases/{base.id}/delete", follow_redirects=False)

    db.expire_all()
    assert db.get(KnowledgeBase, base.id) is not None
    assert len(db.scalars(select(Document)).all()) == 1


def test_a_stranger_cannot_edit_a_document(db, client, stranger, owner):
    document = _document(db, owner, _base(db, owner), title="The lease")

    stranger.post(
        f"/api/library/documents/{document.id}",
        data={"title": "Theirs"},
        follow_redirects=False,
    )

    db.expire_all()
    assert db.get(Document, document.id).title == "The lease"


def test_a_stranger_cannot_delete_a_document(db, client, stranger, owner):
    """The file on disk as well as the row. A delete that removed the bytes and
    then refused would leave the owner with a document that cannot be opened."""
    document = _document(db, owner, _base(db, owner))
    stored = documents_service.stored_path(document.stored_name)
    assert stored is not None

    stranger.post(f"/api/library/documents/{document.id}/delete", follow_redirects=False)

    db.expire_all()
    assert db.get(Document, document.id) is not None
    assert stored.is_file()


def test_a_document_in_a_shared_base_is_readable_and_not_writable(
    db, client, stranger, stranger_user, owner
):
    """The one store whose visibility does not come from itself. A reader who
    can see a document through somebody else's base must not be able to retitle
    it or delete it out of that base."""
    base = _base(db, owner, name="Contracts")
    document = _document(db, owner, base, title="The lease")
    sharing.set_grants(db, base, user_ids=[stranger_user.id], group_ids=[])

    assert stranger.get(f"/library/knowledge/document/{document.id}").status_code == 200
    edited = stranger.post(
        f"/api/library/documents/{document.id}", data={"title": "Theirs"}, follow_redirects=False
    )
    removed = stranger.post(
        f"/api/library/documents/{document.id}/delete", follow_redirects=False
    )

    assert edited.status_code == 403
    assert removed.status_code == 404
    db.expire_all()
    assert db.get(Document, document.id).title == "The lease"


def test_a_stranger_cannot_edit_a_memory(db, client, stranger, owner):
    """Memories are injected into every request. Somebody else writing one is
    somebody else putting a standing instruction in front of your model."""
    memory = _memory(db, owner, content="Prefers metric units.")

    response = stranger.post(
        f"/api/library/memories/{memory.id}", data={"content": "Trusts strangers."},
        follow_redirects=False,
    )

    assert response.status_code == 404
    db.expire_all()
    assert db.get(Memory, memory.id).content == "Prefers metric units."


def test_a_stranger_cannot_delete_a_memory(db, client, stranger, owner):
    """Memories are not shareable at all, so there is no 403 case here: anything
    that is not yours is invisible."""
    memory = _memory(db, owner)

    response = stranger.post(
        f"/api/library/memories/{memory.id}/delete", follow_redirects=False
    )

    assert response.status_code == 404
    db.expire_all()
    assert db.get(Memory, memory.id) is not None


# --- Deletes that are supposed to work ----------------------------------------
def test_deleting_a_note_forgets_its_grants(db, client, registered, owner):
    """`Share` carries no foreign key in either direction, so nothing cascades.
    A grant left behind names an id nothing owns, and would grant access to
    whoever next received it."""
    note = _note(db, owner)
    sharing.set_grants(
        db, note, user_ids=[], group_ids=["a-group-that-will-outlive-this"]
    )

    client.post(f"/api/library/notes/{note.id}/delete", follow_redirects=False)

    db.expunge_all()
    assert db.get(Note, note.id) is None
    assert db.scalars(select(Share)).all() == []


def test_deleting_a_skill_takes_its_history_and_its_grants(db, client, registered, owner):
    """Revisions are the entire safety story for a model rewriting its own
    instructions. Orphaned ones are rows nothing can ever reach again."""
    skill = _skill(db, owner, body="First.")
    skills_service.update(db, skill, body="Second.")
    sharing.set_grants(db, skill, user_ids=[], group_ids=["team"])
    assert db.scalars(select(SkillRevision)).all()

    client.post(f"/api/library/skills/{skill.id}/delete", follow_redirects=False)

    db.expunge_all()
    assert db.get(Skill, skill.id) is None
    assert db.scalars(select(SkillRevision)).all() == []
    assert db.scalars(select(Share)).all() == []


def test_deleting_a_document_removes_the_file(db, client, registered, owner):
    """The row going and the bytes staying is a disk that fills with files
    nothing references and nothing will ever delete."""
    document = _document(db, owner, _base(db, owner))
    stored = documents_service.stored_path(document.stored_name)
    assert stored is not None and stored.is_file()

    client.post(f"/api/library/documents/{document.id}/delete", follow_redirects=False)

    db.expunge_all()
    assert db.get(Document, document.id) is None
    assert not stored.is_file()


def test_deleting_a_base_takes_its_documents_and_their_files(db, client, registered, owner):
    """A base is a place, not a label: leaving its contents behind would need an
    "unfiled" concept that exists only to hold the wreckage of deletes. The
    other base is here because a delete that took everything would look correct
    from inside a single-base test."""
    doomed = _base(db, owner, name="Contracts")
    kept = _base(db, owner, name="Recipes")
    inside = _document(db, owner, doomed)
    elsewhere = _document(db, owner, kept)
    stored = documents_service.stored_path(inside.stored_name)
    sharing.set_grants(db, doomed, user_ids=[], group_ids=["team"])

    client.post(f"/api/library/bases/{doomed.id}/delete", follow_redirects=False)

    db.expunge_all()
    assert db.get(KnowledgeBase, doomed.id) is None
    assert db.get(Document, inside.id) is None
    assert not stored.is_file()
    assert db.get(Document, elsewhere.id) is not None
    assert db.scalars(select(Share)).all() == []


def test_deleting_a_memory_works_for_its_owner(db, client, registered, owner):
    memory = _memory(db, owner)

    client.post(f"/api/library/memories/{memory.id}/delete", follow_redirects=False)

    db.expunge_all()
    assert db.get(Memory, memory.id) is None


def test_reverting_a_skill_restores_the_body_and_keeps_the_way_back(
    db, client, registered, owner
):
    """Going back has to be undoable too, or a revert made by mistake is the one
    change in this store with no record of what it replaced."""
    skill = _skill(db, owner, body="First.")
    skills_service.update(db, skill, body="Second.")
    revision = skill.revisions[0]

    client.post(
        f"/api/library/skills/{skill.id}/revert/{revision.id}", follow_redirects=False
    )

    db.expire_all()
    restored = db.get(Skill, skill.id)
    assert restored.body == "First."
    assert [r.body for r in restored.revisions] == ["Second.", "First."]


def test_a_revision_belonging_to_another_skill_is_refused(db, client, registered, owner):
    """The revision id is a second, independent handle on somebody's data. Without
    the `revision.skill_id != skill.id` check, one skill could be overwritten with
    the contents of any other -- including one shared to you and not yours."""
    victim = _skill(db, owner, name="victim", body="Victim body.")
    donor = _skill(db, owner, name="donor", body="Donor body.")
    skills_service.update(db, donor, body="Donor changed.")
    foreign = donor.revisions[0]

    response = client.post(
        f"/api/library/skills/{victim.id}/revert/{foreign.id}", follow_redirects=False
    )

    assert response.status_code == 404
    db.expire_all()
    assert db.get(Skill, victim.id).body == "Victim body."


# --- Bases --------------------------------------------------------------------
def test_creating_a_base_writes_it_to_the_signed_in_account(db, client, registered, owner):
    client.post(
        "/api/library/bases", data={"name": "  Contracts  ", "description": "Leases."},
        follow_redirects=False,
    )

    db.expire_all()
    bases = db.scalars(select(KnowledgeBase)).all()
    assert [(b.name, b.owner_id) for b in bases] == [("Contracts", owner.id)]


def test_a_duplicate_base_name_makes_no_second_row(db, client, registered, owner):
    """The name is what somebody picks from when filing a document. Two called
    "Contracts" is a choice nobody can make correctly."""
    _base(db, owner, name="Contracts")

    response = client.post(
        "/api/library/bases", data={"name": "Contracts"}, follow_redirects=False
    )

    db.expire_all()
    assert len(db.scalars(select(KnowledgeBase)).all()) == 1
    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_two_people_may_each_have_a_base_of_the_same_name(db, client, stranger, owner):
    """Uniqueness is per owner. A shared instance where the first person to say
    "My documents" takes the name from everybody else is unusable."""
    _base(db, owner, name="Contracts")

    stranger.post("/api/library/bases", data={"name": "Contracts"}, follow_redirects=False)

    db.expire_all()
    assert len(db.scalars(select(KnowledgeBase)).all()) == 2


def test_renaming_a_base_keeps_its_documents(db, client, registered, owner):
    base = _base(db, owner, name="Contracts")
    document = _document(db, owner, base)

    client.post(
        f"/api/library/bases/{base.id}",
        data={"name": "Leases", "description": "Signed ones."},
        follow_redirects=False,
    )

    db.expire_all()
    assert db.get(KnowledgeBase, base.id).name == "Leases"
    assert db.get(Document, document.id).base_id == base.id


def test_an_empty_name_leaves_the_base_named(db, client, registered, owner):
    """A rename form submitted with the field cleared must not produce a base
    with no name, which is a row nobody can pick out of a list."""
    base = _base(db, owner, name="Contracts")

    client.post(f"/api/library/bases/{base.id}", data={"name": "  "}, follow_redirects=False)

    db.expire_all()
    assert db.get(KnowledgeBase, base.id).name == "Contracts"


# --- Documents ----------------------------------------------------------------
def test_an_upload_lands_in_the_base_it_named(db, client, registered, owner):
    base = _base(db, owner, name="Contracts")

    client.post(
        "/api/library/documents",
        data={"title": "The lease", "base_id": base.id},
        files={"file": ("lease.txt", b"Signed at Bag End.", "text/plain")},
        follow_redirects=False,
    )

    db.expire_all()
    stored = db.scalars(select(Document)).all()
    assert [(d.title, d.base_id) for d in stored] == [("The lease", base.id)]


def test_an_upload_naming_an_invisible_base_goes_to_your_own(db, client, stranger, owner):
    """`get_base` cannot see it, so the request falls back to the uploader's own
    default base. The property that matters is that nothing lands in somebody
    else's library -- and the redirect goes to a base the uploader can open, so
    the document is not silently lost either."""
    theirs = _base(db, owner, name="Contracts")

    stranger.post(
        "/api/library/documents",
        data={"base_id": theirs.id},
        files={"file": ("mine.txt", b"My own file.", "text/plain")},
        follow_redirects=False,
    )

    db.expire_all()
    landed = db.scalars(select(Document)).all()
    assert len(landed) == 1
    assert landed[0].base_id != theirs.id
    assert db.get(KnowledgeBase, landed[0].base_id).owner_id != owner.id


def test_an_upload_into_a_base_shared_to_you_is_refused(db, client, stranger, stranger_user, owner):
    """Visible and still not writable. Adding a document to somebody's base would
    put your file in front of everybody they shared it with, under their name."""
    base = _base(db, owner, name="Contracts")
    sharing.set_grants(db, base, user_ids=[stranger_user.id], group_ids=[])

    response = stranger.post(
        "/api/library/documents",
        data={"base_id": base.id},
        files={"file": ("mine.txt", b"My own file.", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.scalars(select(Document)).all() == []


def test_a_document_can_be_moved_between_your_own_bases(db, client, registered, owner):
    """Moving is what changes who can see a document, since visibility comes from
    the base. It has to actually move."""
    origin = _base(db, owner, name="Inbox")
    destination = _base(db, owner, name="Contracts")
    document = _document(db, owner, origin)

    client.post(
        f"/api/library/documents/{document.id}",
        data={"title": "The lease", "base_id": destination.id},
        follow_redirects=False,
    )

    db.expire_all()
    assert db.get(Document, document.id).base_id == destination.id


def test_a_document_cannot_be_moved_into_a_base_you_only_read(
    db, client, stranger, stranger_user, owner
):
    """Moving it there would hand it to that base's owner and to everybody they
    shared it with. The route drops the move and keeps the rest of the save,
    which is why this asserts on `base_id` rather than on a status code."""
    theirs = _base(db, owner, name="Contracts")
    sharing.set_grants(db, theirs, user_ids=[stranger_user.id], group_ids=[])
    mine = _base(db, stranger_user, name="Mine")
    document = _document(db, stranger_user, mine)

    stranger.post(
        f"/api/library/documents/{document.id}",
        data={"title": "Kept", "base_id": theirs.id},
        follow_redirects=False,
    )

    db.expire_all()
    assert db.get(Document, document.id).base_id == mine.id


def test_a_documents_file_is_served_as_an_attachment(db, client, registered, owner):
    """An uploaded .html served inline executes in this origin, with the session
    cookie. The header is the whole defence, so it is asserted rather than the
    body."""
    document = _document(
        db, owner, _base(db, owner), payload=b"<h1>hello</h1>", filename="page.html"
    )

    response = client.get(f"/api/library/documents/{document.id}/content")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment")


def test_a_document_whose_file_is_gone_says_so(db, client, registered, owner):
    """A stored name that no longer resolves is a 404, not a traceback: files can
    go missing under a server and the page above this one still has to render."""
    document = _document(db, owner, _base(db, owner))
    documents_service.stored_path(document.stored_name).unlink()

    assert client.get(f"/api/library/documents/{document.id}/content").status_code == 404


# --- The link route, which reaches out ----------------------------------------
def test_saving_a_link_refuses_a_loopback_address(db, client, registered, owner):
    """This route hands a user-supplied URL to a fetcher running on a server that
    can reach LLeMbas itself, the router, and every other service on the box. The
    refusal has to happen here, not just in the tests of `fetch`."""
    response = client.post(
        "/api/library/documents/link",
        data={"url": "http://127.0.0.1:8080/admin"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    db.expire_all()
    assert db.scalars(select(Document)).all() == []


def test_saving_a_link_refuses_a_name_that_resolves_to_loopback(db, client, registered, owner):
    """The check is on the resolved address. A hostname pointing at 127.0.0.1 is
    the obvious way past one that only reads the text of the URL."""
    response = client.post(
        "/api/library/documents/link", data={"url": "http://localhost/"}, follow_redirects=False
    )

    assert response.status_code == 400
    db.expire_all()
    assert db.scalars(select(Document)).all() == []


def test_saving_a_link_refuses_a_redirect_onto_the_server(
    db, client, registered, owner, mock_http, monkeypatch
):
    """Every hop, not just the first. httpx's own following would validate the
    address somebody typed and then land wherever it was sent."""
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
        if host == "example.com"
        else [(2, 1, 6, "", ("127.0.0.1", 80))],
    )
    mock_http(lambda _r: httpx.Response(302, headers={"location": "http://127.0.0.1:8080/admin"}))

    response = client.post(
        "/api/library/documents/link",
        data={"url": "https://example.com/"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    db.expire_all()
    assert db.scalars(select(Document)).all() == []


def test_saving_a_link_keeps_the_page_as_text(db, client, registered, owner, mock_http, public):
    """The point of keeping a page is what it said. Stored as text rather than
    HTML, so nothing has to be reduced again on every read."""
    base = _base(db, owner, name="Reading")
    mock_http(
        lambda _r: httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><head><title>Mallorn</title></head>"
                "<body><p>A golden tree.</p></body></html>"
            ),
        )
    )

    client.post(
        "/api/library/documents/link",
        data={"url": "https://example.com/mallorn", "base_id": base.id},
        follow_redirects=False,
    )

    db.expire_all()
    stored = db.scalars(select(Document)).all()
    assert len(stored) == 1
    assert stored[0].title == "Mallorn"
    assert stored[0].extracted_text == "A golden tree."
    assert stored[0].base_id == base.id


def test_the_administrators_switch_reaches_the_link_route(db, client, registered, owner, mock_http):
    """`allow_private_fetch` is an instance setting, and a route that never read
    it would leave the switch in the admin page doing nothing at all -- the exact
    failure this codebase keeps cataloguing."""
    settings_store.update(db, {"allow_private_fetch": True}, key=settings_store.SEARCH)
    mock_http(
        lambda _r: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<p>An internal page.</p>"
        )
    )

    client.post(
        "/api/library/documents/link",
        data={"url": "http://127.0.0.1:9/notes"},
        follow_redirects=False,
    )

    db.expire_all()
    assert [d.extracted_text for d in db.scalars(select(Document))] == ["An internal page."]


def test_saving_a_link_into_a_base_shared_to_you_is_refused(
    db, client, stranger, stranger_user, owner, mock_http, public
):
    """The same gate the upload route has. Without it, the fetch is the way round
    a refusal on the other door into the same base."""
    base = _base(db, owner, name="Contracts")
    sharing.set_grants(db, base, user_ids=[stranger_user.id], group_ids=[])
    mock_http(
        lambda _r: httpx.Response(200, headers={"content-type": "text/html"}, text="<p>x</p>")
    )

    response = stranger.post(
        "/api/library/documents/link",
        data={"url": "https://example.com/", "base_id": base.id},
        follow_redirects=False,
    )

    assert response.status_code == 403
    db.expire_all()
    assert db.scalars(select(Document)).all() == []


# --- Route order, which fails silently ----------------------------------------
def test_the_static_segments_are_not_parsed_as_ids(db, client, registered, owner):
    """FastAPI matches in registration order, so `/library/notes/new` reaching the
    detail route would 404 on a note called "new" -- and there would be no way to
    write one. This has already been a bug once in the model admin."""
    assert client.get("/library/notes/new").status_code == 200
    assert client.get("/library/skills/new").status_code == 200

    document = _document(db, owner, _base(db, owner))
    assert client.get(f"/library/knowledge/document/{document.id}").status_code == 200


def test_the_link_route_is_not_parsed_as_a_document_id(db, client, registered, owner):
    """`/api/library/documents/link` is registered before
    `/api/library/documents/{document_id}`. Registered after it, saving a page
    would answer "that document is not available" and nobody would guess why."""
    response = client.post(
        "/api/library/documents/link", data={"url": "not-a-url"}, follow_redirects=False
    )

    assert response.status_code == 400


# --- Memory -------------------------------------------------------------------
def test_an_empty_memory_is_not_recorded(db, client, registered, owner):
    """A blank line in front of the model on every turn, forever, and no way to
    tell which of several it is when removing one."""
    response = client.post("/api/library/memories", data={"content": "   "}, follow_redirects=False)

    db.expire_all()
    assert db.scalars(select(Memory)).all() == []
    assert "error=" in response.headers["location"]


def test_emptying_a_memory_leaves_it_alone(db, client, registered, owner):
    memory = _memory(db, owner, content="Prefers metric units.")

    response = client.post(
        f"/api/library/memories/{memory.id}", data={"content": ""}, follow_redirects=False
    )

    assert response.status_code == 400
    db.expire_all()
    assert db.get(Memory, memory.id).content == "Prefers metric units."


def test_the_same_memory_twice_makes_one_record(db, client, registered, owner):
    """The commonest failure in this store, and worse than wasted tokens: the
    same preference saved twice makes removing it ambiguous for both."""
    for _ in range(2):
        client.post(
            "/api/library/memories",
            data={"content": "Prefers metric units."},
            follow_redirects=False,
        )

    db.expire_all()
    assert len(db.scalars(select(Memory)).all()) == 1


# --- A name somebody else owns ------------------------------------------------
def test_a_shared_skill_does_not_take_its_name_from_you(db, client, stranger, stranger_user, owner):
    """`Skill` is unique on (owner_id, name), and `create_base` next door checks
    ownership -- this one checks visibility. Share one "weekly-report" with a
    team and nobody on that team can make their own, which reads as the name
    being reserved instance-wide by whoever got there first."""
    theirs = _skill(db, owner, name="weekly-report")
    sharing.set_grants(db, theirs, user_ids=[stranger_user.id], group_ids=[])

    stranger.post(
        "/api/library/skills",
        data={
            "name": "weekly-report",
            "description": "How I write mine.",
            "body": "Step one.",
        },
        follow_redirects=False,
    )

    db.expire_all()
    mine = db.scalars(select(Skill).where(Skill.owner_id == stranger_user.id)).all()
    assert [s.name for s in mine] == ["weekly-report"]
