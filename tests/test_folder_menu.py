"""A root folder's heading on a phone: `[ NAME ] ⋯ +`.

At 48rem and below the three hover tools (rename, settings, delete) are hidden
and one menu button stands in for them, so the name keeps the room two boxes
leave it rather than four -- measured headless at 390x844, where four boxes left
it 23px, two letters. What the suite can pin is the structure: the menu is the
picker machinery, its items carry exactly the attributes the hover buttons do,
and the stylesheet swaps the two at the breakpoint.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select

import lembas
from lembas.db.models import Folder, User

CSS = (Path(lembas.__file__).parent / "web/static/css/app.css").read_text(encoding="utf-8")


def _folder_head(html: str, folder_id: str) -> str:
    start = html.index(f'data-section="folder:{folder_id}"')
    head = html.rindex('<div class="nav-group__head folder__head">', 0, start)
    body = html.index(f'id="section-folder-{folder_id}"', start)
    return html[head:body]


def test_a_root_folder_has_a_menu_with_the_same_three_actions(client, db, registered):
    owner = db.scalar(select(User))
    folder = Folder(user_id=owner.id, name="Research notes")
    db.add(folder)
    db.commit()
    head = _folder_head(client.get("/chat").text, folder.id)

    menu = head[head.index("nav-group__more") :]
    assert "data-picker" in menu and "data-picker-toggle" in menu and "data-picker-menu" in menu
    for attribute in (
        f'hx-patch="/api/folders/{folder.id}"',
        f'href="/folders/{folder.id}"',
        f'hx-delete="/api/folders/{folder.id}"',
    ):
        # Once among the hover tools, once in the menu.
        assert head.count(attribute) == 2, attribute
    # The rename asks for the name, the delete confirms -- in the menu too.
    assert menu.count('data-prompt-field="name"') == 1
    assert menu.count("hx-confirm=") == 1
    # Every item is a menu item, so pressing one puts the menu away (ui.js).
    assert len(re.findall(r'role="menuitem"', menu)) == 3


def test_a_child_folder_keeps_its_row_actions(client, db, registered):
    owner = db.scalar(select(User))
    parent = Folder(user_id=owner.id, name="Outer")
    db.add(parent)
    db.commit()
    child = Folder(user_id=owner.id, name="Inner", parent_id=parent.id)
    db.add(child)
    db.commit()
    html = client.get("/chat").text
    assert html.count("nav-group__more") == 1


def test_the_breakpoint_swaps_the_tools_for_the_menu():
    assert ".nav-group__more { display: none; flex: none; }" in CSS
    block = CSS[CSS.index(".nav-group__more { display: none") :]
    block = block[block.index("@media (max-width: 48rem)") :]
    block = block[: block.index("}\n}") + 3]
    assert ".folder__head .nav-group__tools { display: none; }" in block
    assert ".nav-group__more { display: block; }" in block
