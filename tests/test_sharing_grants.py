"""Sharing: what it grants, what it does not, and what it leaves behind.

Half of this file is about grants that outlive what they name. `Share` carries
no foreign key in either direction — `principal_id` points at a user *or* a
group and `resource_id` at one of four tables, neither of which SQLite can
express — so nothing cascades, and every delete has to say so explicitly.
`sharing.forget_principal` existed for exactly this and was called by nobody.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import (
    ROLE_USER,
    Group,
    Note,
    Report,
    Share,
    User,
)
from lembas.security import permissions
from lembas.services import reports as reports_service
from lembas.services import sharing
from lembas.services.library import notes as notes_service


@pytest.fixture
def owner(db, registered) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


@pytest.fixture
def reader(db) -> User:
    person = User(
        email="sam@shire.test", name="Sam", password_hash="x", role=ROLE_USER
    )
    db.add(person)
    db.commit()
    return person


# --- Dangling grants ------------------------------------------------------------
def test_deleting_a_group_drops_the_shares_naming_it(db, client, registered, owner, reader):
    """It never did. A group id is a random hex string that nothing reissues
    today and nothing promises not to reissue tomorrow."""
    group = Group(name="team")
    group.users.append(reader)
    db.add(group)
    db.commit()
    note = notes_service.create(db, owner=owner, title="Secret", body="mellon")
    sharing.set_grants(db, note, user_ids=[], group_ids=[group.id])
    assert db.scalars(select(Share)).all()

    client.post(f"/admin/groups/{group.id}/delete", follow_redirects=False)

    db.expire_all()
    assert db.scalars(select(Share)).all() == []


def test_deleting_an_account_drops_both_halves(db, client, registered, owner, reader):
    """Grants **to** them, and grants **of** their own work. The second is the
    one nothing else could catch: their rows cascade, and the shares of those
    rows have nothing to cascade from."""
    theirs = notes_service.create(db, owner=reader, title="Theirs", body="x")
    sharing.set_grants(db, theirs, user_ids=[owner.id], group_ids=[])
    mine = notes_service.create(db, owner=owner, title="Mine", body="y")
    sharing.set_grants(db, mine, user_ids=[reader.id], group_ids=[])
    assert len(db.scalars(select(Share)).all()) == 2

    client.post(f"/admin/users/{reader.id}/delete", follow_redirects=False)

    db.expire_all()
    assert db.scalars(select(Share)).all() == []


def test_deleting_a_report_drops_its_grants(db, owner, reader):
    report = reports_service.create(db, owner=owner, title="Findings", body="x")
    sharing.set_grants(db, report, user_ids=[reader.id], group_ids=[])

    reports_service.delete(db, report)

    db.expire_all()
    assert db.scalars(select(Share)).all() == []


# --- Reports ---------------------------------------------------------------------
def test_a_shared_report_is_visible_and_not_deletable(db, owner, reader):
    """Sharing grants reading. Being able to see a report is not being able to
    delete it out from under the person who filed it."""
    report = reports_service.create(db, owner=owner, title="Findings", body="x")
    sharing.set_grants(db, report, user_ids=[reader.id], group_ids=[])

    assert reports_service.get(db, report.id, reader) is not None
    assert reports_service.owned(db, report.id, reader) is None
    assert reports_service.owned(db, report.id, owner) is not None


def test_an_unshared_report_stays_invisible(db, owner, reader):
    report = reports_service.create(db, owner=owner, title="Findings", body="x")

    assert reports_service.get(db, report.id, reader) is None
    assert list(db.scalars(reports_service.visible(reader))) == []


def test_a_shared_report_appears_in_the_feed_and_the_filter(db, owner, reader):
    report = reports_service.create(db, owner=owner, title="Findings", body="x")
    theirs = reports_service.create(db, owner=reader, title="Mine", body="y")
    sharing.set_grants(db, report, user_ids=[reader.id], group_ids=[])

    everything = {r.id for r in db.scalars(reports_service.visible(reader))}
    only_shared = {
        r.id for r in db.scalars(select(Report).where(sharing.only_shared(Report, reader)))
    }

    assert everything == {report.id, theirs.id}
    assert only_shared == {report.id}


def test_reading_somebody_elses_report_does_not_clear_their_dot(db, client, registered, owner):
    """`unread` is the owner's notification. Somebody it was shared with opening
    it would silence a dot meant for a person who has not seen it."""
    other = User(
        email="sam@shire.test", name="Sam", password_hash="x", role=ROLE_USER
    )
    db.add(other)
    db.commit()
    report = reports_service.create(db, owner=other, title="Theirs", body="x")
    report.unread = True
    db.commit()
    sharing.set_grants(db, report, user_ids=[owner.id], group_ids=[])

    assert client.get(f"/reports/{report.id}").status_code == 200

    db.refresh(report)
    assert report.unread is True


# --- Shared with me --------------------------------------------------------------
def test_only_shared_excludes_your_own(db, owner, reader):
    mine = notes_service.create(db, owner=reader, title="Mine", body="x")
    theirs = notes_service.create(db, owner=owner, title="Theirs", body="y")
    sharing.set_grants(db, theirs, user_ids=[reader.id], group_ids=[])

    rows = {n.id for n in db.scalars(select(Note).where(sharing.only_shared(Note, reader)))}

    assert rows == {theirs.id}
    assert mine.id not in rows


def test_the_filter_is_a_url_you_can_keep(db, client, registered):
    for path in ("/library/notes", "/library/skills", "/library/knowledge", "/reports"):
        page = client.get(f"{path}?shared=1")
        assert page.status_code == 200, path
        assert "Shared with me" in page.text, path


# --- The panel -------------------------------------------------------------------
def test_a_grant_is_stored_the_moment_it_is_made(db, client, registered, owner, reader):
    """It used to ride along with the resource's save form, so ticking a box and
    navigating away did nothing — silently."""
    note = notes_service.create(db, owner=owner, title="Note", body="x")

    response = client.post(
        f"/api/library/share/note/{note.id}",
        data={"principal_type": "user", "principal_id": reader.id, "on": "true"},
    )

    assert response.status_code == 200
    assert sharing.can_read(db, note, reader) is True
    # And the answer is the panel, showing what is now true.
    assert "Shared with" in response.text


def test_a_grant_can_be_taken_back(db, client, registered, owner, reader):
    note = notes_service.create(db, owner=owner, title="Note", body="x")
    sharing.set_grants(db, note, user_ids=[reader.id], group_ids=[])

    client.post(
        f"/api/library/share/note/{note.id}",
        data={"principal_type": "user", "principal_id": reader.id, "on": "false"},
    )

    db.expire_all()
    assert sharing.can_read(db, note, reader) is False


def test_the_panel_searches_rather_than_listing_everybody(db, client, registered, owner):
    """It rendered every group and every account on the instance, unpaginated,
    on every detail page."""
    for n in range(30):
        db.add(
            User(
                email=f"p{n}@shire.test",
                name=f"Person {n}",
                password_hash="x",
            )
        )
    db.commit()
    note = notes_service.create(db, owner=owner, title="Note", body="x")

    everything = client.get(f"/api/library/share/note/{note.id}").text
    assert everything.count('name="principal_id"') == 0  # values ride in hx-vals
    assert "Person 29" not in everything

    found = client.get(f"/api/library/share/note/{note.id}?q=Person+29").text
    assert "Person 29" in found


def test_a_quote_in_the_search_does_not_break_the_panels_buttons(
    db, client, registered, owner, reader
):
    """`hx-vals` carries the values, and it was built by pasting the search term
    into a JSON string. Jinja escapes the quote for HTML, but the parser decodes
    it again before htmx parses the JSON -- so a `"` ended the string, made the
    attribute unparseable, and every checkbox in the panel silently stopped
    submitting anything. `q` is the last key, so an injected one would also have
    won a duplicate-key parse.

    Built with `| tojson` over the whole object now, which escapes for JSON
    first and lets Jinja escape that for HTML. An existing grant is what keeps a
    row on screen whatever the search says.
    """
    import html
    import json
    import re

    note = notes_service.create(db, owner=owner, title="Note", body="x")
    sharing.set_grants(db, note, user_ids=[reader.id], group_ids=[])

    hostile = '", "principal_id": "smuggled'
    page = client.get(
        f"/api/library/share/note/{note.id}", params={"q": hostile}
    ).text

    values = re.findall(r"hx-vals='([^']*)'", page)
    assert values, "the panel rendered no hx-vals at all"
    for raw in values:
        parsed = json.loads(html.unescape(raw))
        assert parsed["principal_id"] != "smuggled"
        assert parsed["q"] == hostile


def test_an_existing_grant_stays_listed_whatever_the_search_says(
    db, client, registered, owner, reader
):
    """Otherwise the only way to remove a grant would be to search for the name
    it was given to."""
    note = notes_service.create(db, owner=owner, title="Note", body="x")
    sharing.set_grants(db, note, user_ids=[reader.id], group_ids=[])

    page = client.get(f"/api/library/share/note/{note.id}?q=nobody-matches-this").text

    assert "sam@shire.test" in page


def test_somebody_it_was_shared_with_cannot_share_it_on(db, client, registered, owner, reader):
    """What keeps "who can see this?" answerable by asking one person."""
    note = notes_service.create(db, owner=owner, title="Note", body="x")
    sharing.set_grants(db, note, user_ids=[reader.id], group_ids=[])

    other = TestClient(client.app)
    other.post(
        "/auth/register",
        data={"name": "Merry", "email": "merry@shire.test", "password": "a-fine-second-breakfast"},
        follow_redirects=False,
    )
    # Signed in as somebody else entirely: the panel is not theirs to open.
    assert other.get(f"/api/library/share/note/{note.id}").status_code == 404


def test_a_grant_naming_nothing_is_refused(db, client, registered, owner):
    """A crafted id would write a grant that is invisible in the panel and
    unremovable from it."""
    note = notes_service.create(db, owner=owner, title="Note", body="x")

    client.post(
        f"/api/library/share/note/{note.id}",
        data={"principal_type": "user", "principal_id": "not-a-real-id", "on": "true"},
    )

    assert db.scalars(select(Share)).all() == []


def test_sharing_is_on_by_default(db, registered, owner):
    """It was off, which meant sharing shipped documented as done and
    unreachable: the panel only renders for somebody who holds this."""
    assert permissions.has(db, owner, "library.share") is True
    assert permissions.DEFAULT_PERMISSIONS["library.share"] is True


# --- Read and write, split -------------------------------------------------------
def test_a_reader_can_search_notes_and_not_write_them(db, reader):
    from lembas.db.models import Chat, Connection, Model
    from lembas.services import settings_store
    from lembas.services import tools as tools_service
    from lembas.services.crypto import encrypt

    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt(""))
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    chat = Chat(user_id=reader.id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()

    settings_store.update(db, {"default_permissions": {"tools.notes.write": False}})
    offered = {tool.name for tool in tools_service.resolve_tools(db, chat, reader).defs}

    assert "notes_search" in offered
    assert "note_view" in offered
    assert "note_manage" not in offered


def test_the_write_half_defaults_on(db, reader):
    """An instance that never looks behaves exactly as it did."""
    for key in ("tools.notes.write", "tools.memory.write", "tools.skills.write"):
        assert permissions.DEFAULT_PERMISSIONS[key] is True
