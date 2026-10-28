"""Chats dragged into folders, lists dragged into order (js/dnd.js)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

import lembas
from lembas.api.ordering import reorder
from lembas.db.models import Chat, CustomTool, Folder, User

ROOT = Path(lembas.__file__).parent


def _folder(db, name: str) -> Folder:
    user = db.scalars(select(User).order_by(User.created_at)).first()
    folder = Folder(user_id=user.id, name=name)
    db.add(folder)
    db.commit()
    return folder


# --- Chats into folders --------------------------------------------------------


def test_a_chat_dropped_on_a_folder_moves_and_the_tree_comes_back(
    client, db, registered, make_chat
):
    folder = _folder(db, "Rivendell")
    chat_id = make_chat()
    response = client.patch(f"/api/chats/{chat_id}", data={"folder_id": folder.id})
    assert response.status_code == 200
    assert 'id="sidebar-tree"' in response.text
    db.expire_all()
    assert db.get(Chat, chat_id).folder_id == folder.id

    # And back out, onto "no folder".
    response = client.patch(f"/api/chats/{chat_id}", data={"folder_id": ""})
    assert response.status_code == 200
    db.expire_all()
    assert db.get(Chat, chat_id).folder_id is None


def test_a_patch_that_does_not_move_is_still_no_content(client, db, registered, make_chat):
    chat_id = make_chat()
    response = client.patch(f"/api/chats/{chat_id}", data={"folder_id": ""})
    assert response.status_code == 204


def test_rows_are_draggable_and_folders_are_targets(client, db, registered, make_chat):
    folder = _folder(db, "Lothlorien")
    chat = db.get(Chat, make_chat())
    chat.title = "Elvish"
    db.commit()
    page = client.get("/chat").text
    assert 'data-folder-drop=""' in page
    assert f'data-folder-drop="{folder.id}"' in page
    assert 'draggable="true"' in page
    # The keyboard's way to the same thing.
    assert f'data-move-chat="{chat.id}"' in page


def test_without_folders_there_is_nothing_to_drag_to(client, db, registered, make_chat):
    chat = db.get(Chat, make_chat())
    chat.title = "Alone"
    db.commit()
    page = client.get("/chat").text
    assert "data-move-chat" not in page


# --- Lists in order --------------------------------------------------------------


def _rows(*ids):
    return [SimpleNamespace(id=i) for i in ids]


def test_the_rows_shown_take_their_own_slots():
    rows = _rows("a", "b", "c", "d", "e")
    # Page showed b, d (a filter hid the others); d dragged above b.
    assert [r.id for r in reorder(rows, ["d", "b"])] == ["a", "d", "c", "b", "e"]


def test_unknown_and_repeated_ids_change_nothing_else():
    rows = _rows("a", "b", "c")
    assert [r.id for r in reorder(rows, ["c", "zz", "c", "a"])] == ["c", "b", "a"]
    assert [r.id for r in reorder(rows, [])] == ["a", "b", "c"]


def _tool(db, slug, position):
    tool = CustomTool(
        slug=slug, name=slug.title(), url_template="https://example.test/", position=position
    )
    db.add(tool)
    db.commit()
    return tool


def test_the_order_route_saves_and_goes_back(client, db, registered):
    one, two, three = _tool(db, "one", 0), _tool(db, "two", 1), _tool(db, "three", 2)
    response = client.post(
        "/admin/order/tools",
        data={"ids": f"{three.id},{one.id},{two.id}", "back": "/admin/tools?filter=all"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/admin/tools?filter=all&saved=")
    db.expire_all()
    assert [t.slug for t in db.scalars(select(CustomTool).order_by(CustomTool.position))] == [
        "three",
        "one",
        "two",
    ]


def test_the_order_route_never_sends_anybody_elsewhere(client, db, registered):
    response = client.post(
        "/admin/order/tools",
        data={"ids": "", "back": "//evil.test/x"},
        follow_redirects=False,
    )
    assert response.headers["location"].startswith("/admin/tools")


def test_an_unknown_list_is_not_found(client, db, registered):
    assert client.post("/admin/order/chats", data={"ids": ""}).status_code == 404


def test_the_tools_list_is_sortable(client, db, registered):
    _tool(db, "one", 0)
    page = client.get("/admin/tools").text
    assert 'data-sortable-url="/admin/order/tools"' in page
    assert "data-sortable-handle" in page


def test_the_script_has_a_keyboard_and_a_touch_path():
    source = (ROOT / "web/static/js/dnd.js").read_text(encoding="utf-8")
    assert '"ArrowUp"' in source and '"ArrowDown"' in source
    assert "pointerdown" in source
    base = (ROOT / "web/templates/base.html").read_text(encoding="utf-8")
    assert base.count("js/dnd.js") == 1
