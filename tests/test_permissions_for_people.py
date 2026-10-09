"""Every permission is explained to the person who has it.

Settings → Account does not list the keys -- `chat.create`, `tools.knowledge` --
which are the admin grids' vocabulary and nobody else's. `FOR_PEOPLE` says each
one as a sentence, and this keeps it complete: a permission added to
`PERMISSION_DEFS` without a line here would otherwise render as nothing at all.
"""

from __future__ import annotations

from lembas.security import permissions


def test_every_permission_has_a_section_and_a_line():
    keys = {definition.key for definition in permissions.PERMISSION_DEFS}
    assert set(permissions.FOR_PEOPLE) == keys
    for key, (section, line) in permissions.FOR_PEOPLE.items():
        assert section in permissions.PERSON_SECTIONS, key
        assert line.strip() and line.endswith("."), key


def test_every_permission_has_a_label_and_a_description():
    for definition in permissions.PERMISSION_DEFS:
        assert definition.label.strip(), definition.key
        assert definition.description.strip(), definition.key
        assert definition.group in permissions.ADMIN_SECTIONS, definition.key


def test_one_line_is_one_line():
    """The admin description is the long form; this is the glance."""
    for key, (_section, line) in permissions.FOR_PEOPLE.items():
        assert len(line) <= 80, key


def test_for_people_keeps_every_permission_once_in_reading_order():
    granted = {definition.key: True for definition in permissions.PERMISSION_DEFS}
    sections = permissions.for_people(granted)
    rows = [row for _name, section_rows in sections for row in section_rows]
    assert len(rows) == len(permissions.PERMISSION_DEFS)
    assert [name for name, _rows in sections] == [
        name for name in permissions.PERSON_SECTIONS if any(name == s for s, _ in sections)
    ]


def test_the_settings_page_says_it_in_words(client, registered):
    page = client.get("/settings").text
    assert "Start chats" in page
    assert "Start new chats." in page
    assert "<code>chat.create</code>" not in page


def test_the_settings_page_says_it_in_slovak(client, db, registered):
    from sqlalchemy import select

    from lembas.db.models import User

    user = db.scalars(select(User)).first()
    user.settings_json = {**(user.settings_json or {}), "language": "sk"}
    db.commit()
    page = client.get("/settings").text
    assert "Začínať nové konverzácie." in page
