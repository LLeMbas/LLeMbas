"""Who can see a document, a note or a skill.

The most consequential tests in the library: everything else is a feature not
working, this is somebody reading somebody else's material.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import (
    PRINCIPAL_GROUP,
    PRINCIPAL_USER,
    Group,
    Note,
    Share,
    User,
)
from lembas.security.passwords import hash_password
from lembas.services import sharing
from lembas.services.library import documents as documents_service
from lembas.services.library import notes as notes_service


@pytest.fixture
def people(db):
    """Three accounts: an owner, a stranger, and an administrator."""
    made = {}
    for name, role in (("frodo", "user"), ("gollum", "user"), ("gandalf", "admin")):
        user = User(
            name=name, email=f"{name}@shire.test", password_hash=hash_password("x"), role=role
        )
        db.add(user)
        made[name] = user
    db.commit()
    return made


def _note(db, owner, title="Secret"):
    return notes_service.create(db, owner=owner, title=title, body="The ring is in the drawer.")


# --- The rule ----------------------------------------------------------------
def test_the_owner_sees_their_own(db, people):
    note = _note(db, people["frodo"])
    assert sharing.can_read(db, note, people["frodo"])
    assert note in db.scalars(notes_service.visible(db, people["frodo"]))


def test_a_stranger_sees_nothing(db, people):
    note = _note(db, people["frodo"])
    assert not sharing.can_read(db, note, people["gollum"])
    assert note not in db.scalars(notes_service.visible(db, people["gollum"]))


def test_an_administrator_gets_no_free_pass(db, people):
    """Admins bypass permissions elsewhere, deliberately -- an admin can grant
    themselves those in two clicks. This is different: nobody made this
    available to anyone, and administering a box is not being invited."""
    note = _note(db, people["frodo"])
    assert not sharing.can_read(db, note, people["gandalf"])
    assert note not in db.scalars(notes_service.visible(db, people["gandalf"]))


def test_sharing_with_a_person_lets_them_read(db, people):
    note = _note(db, people["frodo"])
    sharing.set_grants(db, note, user_ids=[people["gollum"].id], group_ids=[])
    assert sharing.can_read(db, note, people["gollum"])
    assert note in db.scalars(notes_service.visible(db, people["gollum"]))


def test_sharing_with_a_group_lets_its_members_read(db, people):
    group = Group(name="Fellowship")
    group.users.append(people["gollum"])
    db.add(group)
    db.commit()

    note = _note(db, people["frodo"])
    sharing.set_grants(db, note, user_ids=[], group_ids=[group.id])
    assert sharing.can_read(db, note, people["gollum"])


def test_leaving_a_group_takes_the_access_with_it(db, people):
    group = Group(name="Fellowship")
    group.users.append(people["gollum"])
    db.add(group)
    db.commit()
    note = _note(db, people["frodo"])
    sharing.set_grants(db, note, user_ids=[], group_ids=[group.id])

    group.users.remove(people["gollum"])
    db.commit()
    db.refresh(people["gollum"])
    assert not sharing.can_read(db, note, people["gollum"])


def test_signed_out_sees_nothing(db, people):
    _note(db, people["frodo"])
    assert list(db.scalars(notes_service.visible(db, None))) == []


# --- Sharing grants reading only ---------------------------------------------
def test_a_share_does_not_grant_writing(db, people):
    """Two people editing one note with no history and no merge is worse than
    the inconvenience of copying it."""
    note = _note(db, people["frodo"])
    sharing.set_grants(db, note, user_ids=[people["gollum"].id], group_ids=[])
    assert sharing.can_read(db, note, people["gollum"])
    assert not sharing.can_write(note, people["gollum"])
    assert sharing.can_write(note, people["frodo"])


# --- Managing grants ---------------------------------------------------------
def test_set_grants_replaces_rather_than_adds(db, people):
    note = _note(db, people["frodo"])
    sharing.set_grants(db, note, user_ids=[people["gollum"].id], group_ids=[])
    sharing.set_grants(db, note, user_ids=[people["gandalf"].id], group_ids=[])

    assert not sharing.can_read(db, note, people["gollum"])
    assert sharing.can_read(db, note, people["gandalf"])


def test_sharing_with_yourself_is_ignored(db, people):
    note = _note(db, people["frodo"])
    sharing.set_grants(db, note, user_ids=[people["frodo"].id], group_ids=[])
    assert sharing.grants_for(db, note) == []


def test_deleting_a_note_drops_its_shares(db, people):
    """Shares carry no foreign key to their resource -- one column pointing at
    three tables cannot have one -- so nothing cascades."""
    note = _note(db, people["frodo"])
    sharing.set_grants(db, note, user_ids=[people["gollum"].id], group_ids=[])
    notes_service.delete(db, note)
    assert db.scalar(select(Share).where(Share.resource_id == note.id)) is None


def test_forgetting_a_principal_drops_their_shares(db, people):
    """A stale row would grant access to whoever next received that id."""
    note = _note(db, people["frodo"])
    sharing.set_grants(db, note, user_ids=[people["gollum"].id], group_ids=[])
    assert sharing.forget_principal(db, PRINCIPAL_USER, people["gollum"].id) == 1
    assert sharing.grants_for(db, note) == []


def test_two_kinds_of_resource_do_not_collide(db, people):
    """One shares table across three resource types, so the type must be part
    of the match -- otherwise a note and a base sharing an id would share each
    other's access."""
    note = _note(db, people["frodo"])
    base = documents_service.create_base(db, owner=people["frodo"], name="Papers")
    sharing.set_grants(db, note, user_ids=[people["gollum"].id], group_ids=[])

    assert sharing.can_read(db, note, people["gollum"])
    assert not sharing.can_read(db, base, people["gollum"])


@pytest.mark.parametrize("unshareable", ["Memory", "Document"])
def test_resource_type_refuses_something_unshareable(db, people, unshareable):
    """Memory is not shareable at all -- a record about a person is not content
    to hand round. A Document is shared through the base it lives in, so asking
    to share one directly is a mistake worth catching loudly."""
    import lembas.db.models as models

    with pytest.raises(ValueError):
        sharing.resource_type(getattr(models, unshareable))


# --- Through the search path -------------------------------------------------
def test_search_does_not_leak_across_owners(db, people):
    """The index is searched first and the visibility filter applied to what it
    returned. Getting that order wrong leaks a hit even without the contents."""
    notes_service.create(
        db, owner=people["frodo"], title="Mallorn", body="A golden tree of Lothlorien."
    )
    assert notes_service.search(db, people["frodo"], "golden")
    assert notes_service.search(db, people["gollum"], "golden") == []
    assert notes_service.search(db, people["gandalf"], "golden") == []


def test_sharing_a_base_shares_what_is_in_it(db, people):
    """Documents are shared through their base. "This folder is the team's" is
    the granularity people think in, and per-document grants would mean
    answering "who can see this?" by checking every file."""
    base = documents_service.create_base(db, owner=people["frodo"], name="Trees")
    document = documents_service.store_upload(
        db,
        owner=people["frodo"],
        payload=b"The mallorn is a golden tree.",
        filename="tree.txt",
        base=base,
    )
    assert documents_service.search(db, people["gollum"], "mallorn") == []

    sharing.set_grants(db, base, user_ids=[people["gollum"].id], group_ids=[])
    found = documents_service.search(db, people["gollum"], "mallorn")
    assert [d.id for d in found] == [document.id]


def test_a_document_in_an_unshared_base_stays_private(db, people):
    """Two bases, one shared: the other must not come with it."""
    shared = documents_service.create_base(db, owner=people["frodo"], name="Public")
    private = documents_service.create_base(db, owner=people["frodo"], name="Private")
    documents_service.store_upload(
        db, owner=people["frodo"], payload=b"A mallorn tree.", filename="a.txt", base=shared
    )
    documents_service.store_upload(
        db, owner=people["frodo"], payload=b"A mallorn secret.", filename="b.txt", base=private
    )
    sharing.set_grants(db, shared, user_ids=[people["gollum"].id], group_ids=[])

    found = documents_service.search(db, people["gollum"], "mallorn")
    assert [d.base_id for d in found] == [shared.id]


def test_scoping_to_a_base_cannot_be_used_to_reach_one(db, people):
    """Naming a base you cannot see returns nothing rather than granting it."""
    private = documents_service.create_base(db, owner=people["frodo"], name="Private")
    documents_service.store_upload(
        db, owner=people["frodo"], payload=b"A mallorn tree.", filename="a.txt", base=private
    )
    found = documents_service.search(
        db, people["gollum"], "mallorn", base_ids=[private.id]
    )
    assert found == []


def test_the_shares_table_records_what_was_asked_for(db, people):
    group = Group(name="Fellowship")
    db.add(group)
    db.commit()
    note = _note(db, people["frodo"])
    sharing.set_grants(
        db, note, user_ids=[people["gollum"].id], group_ids=[group.id]
    )
    kinds = {(s.principal_type, s.principal_id) for s in sharing.grants_for(db, note)}
    assert kinds == {
        (PRINCIPAL_USER, people["gollum"].id),
        (PRINCIPAL_GROUP, group.id),
    }


def test_visibility_is_a_query_filter_not_a_python_loop(db, people):
    """visible_to returns a condition so callers can page and order on the
    database side; a Python filter would break pagination silently."""
    for index in range(3):
        _note(db, people["frodo"], title=f"Note {index}")
    _note(db, people["gollum"], title="Theirs")

    rows = db.scalars(
        notes_service.visible(db, people["frodo"]).order_by(Note.title).limit(2)
    )
    assert [n.title for n in rows] == ["Note 0", "Note 1"]


def test_a_document_follows_its_base(db, people):
    document = documents_service.store_upload(
        db, owner=people["frodo"], payload=b"x", filename="a.txt"
    )
    assert document in db.scalars(documents_service.visible(db, people["frodo"]))
    assert document not in db.scalars(documents_service.visible(db, people["gollum"]))
    assert list(db.scalars(documents_service.visible(db, None))) == []


def test_an_uploaded_document_always_lands_in_a_base(db, people):
    """base_id is nullable only so the column could be added to a table that
    already had rows; the service never leaves it unset."""
    document = documents_service.store_upload(
        db, owner=people["frodo"], payload=b"x", filename="a.txt"
    )
    assert document.base_id is not None


@pytest.mark.parametrize("absent", [None, ""])
def test_documents_predating_bases_are_filed_at_startup(db, people, absent):
    """Both spellings of "no base": NULL, and the empty string a release that
    added the column with a type-derived default left behind.

    The empty string has to be written with foreign keys off, because that is
    the only way it could ever have got there -- ALTER TABLE ADD COLUMN does not
    check existing rows, but an UPDATE would.
    """
    from sqlalchemy import text

    document = documents_service.store_upload(
        db, owner=people["frodo"], payload=b"x", filename="a.txt"
    )
    db.commit()

    db.execute(text("PRAGMA foreign_keys=OFF"))
    db.execute(
        text("UPDATE documents SET base_id = :value WHERE id = :id"),
        {"value": absent, "id": document.id},
    )
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))
    db.expire(document)
    assert document not in db.scalars(documents_service.visible(db, people["frodo"]))

    assert documents_service.sweep_unfiled(db) == 1
    db.refresh(document)
    assert document.base_id is not None
    assert document in db.scalars(documents_service.visible(db, people["frodo"]))
