"""The file pages: explorer, viewer, editor, show card, undo, tick tree.

Everything goes through `fs.View`; these tests hold the doors -- a page never
shows what the view cannot read, HTML never runs as this application, and a
chat's card is re-checked against the chat's grants now.
"""

from __future__ import annotations

from urllib.parse import quote

import pytest
from sqlalchemy import select

from lembas.db.models import Chat, Node, NodeVersion, SessionGrant, User
from lembas.services import file_ui, fs


@pytest.fixture
def me(db, registered):
    user = db.scalars(select(User).order_by(User.created_at)).first()
    home = fs.ensure_home(db, user)
    db.commit()
    return user, home.path


def _write(db, user, path, data: bytes, chat=None):
    v = fs.view(db, user, chat)
    node = fs.write(v, path, data)
    db.commit()
    return node


# --- The explorer --------------------------------------------------------------------


def test_the_explorer_lists_the_home_directory(client, db, me):
    user, home = me
    _write(db, user, f"{home}/notes.md", b"# Hello")
    page = client.get(f"/files?path={quote(home)}")
    assert page.status_code == 200
    assert "notes.md" in page.text
    assert 'data-in-place-root' in page.text
    assert 'data-testid="file-row"' in page.text


def test_a_file_address_opens_the_viewer(client, db, me):
    user, home = me
    _write(db, user, f"{home}/a.txt", b"x")
    response = client.get(f"/files?path={quote(home + '/a.txt')}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/files/view?path=")


def test_upload_mkdir_rename_move_delete_restore(client, db, me):
    user, home = me
    r = client.post(
        "/files/upload",
        data={"path": home},
        files=[("files", ("one.txt", b"first", "text/plain"))],
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text
    assert client.post("/files/mkdir", data={"path": home, "name": "box"},
                       follow_redirects=False).status_code == 303
    client.post("/files/rename", data={"path": f"{home}/one.txt", "name": "two.txt"})
    client.post("/files/move", data={"path": f"{home}/two.txt", "to": f"{home}/box"})
    db.expire_all()
    moved = fs.lookup(db, user.org_id, f"{home}/box/two.txt")
    assert moved is not None
    client.post("/files/delete", data={"path": f"{home}/box/two.txt"})
    db.expire_all()
    assert fs.lookup(db, user.org_id, f"{home}/box/two.txt") is None
    assert "two.txt" in client.get("/files/trash").text
    client.post("/files/restore", data={"node_id": moved.id})
    db.expire_all()
    assert fs.lookup(db, user.org_id, f"{home}/box/two.txt") is not None


def test_a_folder_name_with_a_slash_is_refused(client, db, me):
    _user, home = me
    r = client.post("/files/mkdir", data={"path": home, "name": "a/b"}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_someone_elses_files_are_not_listed(client, db, me):
    """An instance admin has no implicit access to people's files."""
    from lembas.services import organisations

    user, _home = me
    other = organisations.create_account(
        db, org_id=user.org_id, name="Sam", email="sam@shire.test", password="potatoes-and-more"
    )
    other_home = fs.ensure_home(db, other)
    db.commit()
    _write(db, other, f"{other_home.path}/secret.txt", b"lembas recipe")
    assert "secret.txt" not in client.get(f"/files?path={quote(other_home.path)}").text
    view = client.get(f"/files/view?path={quote(other_home.path + '/secret.txt')}")
    assert view.status_code == 404
    assert "lembas recipe" not in view.text
    raw = client.get(f"/files/raw?path={quote(other_home.path + '/secret.txt')}")
    assert raw.status_code == 404


# --- The viewer ----------------------------------------------------------------------


def test_markdown_is_rendered_and_sanitised(client, db, me):
    user, home = me
    _write(db, user, f"{home}/r.md", b"# Title\n\n<script>alert(1)</script>\n\n**bold**")
    page = client.get(f"/files/view?path={quote(home + '/r.md')}").text
    assert "<strong>bold</strong>" in page
    assert "<script>alert(1)</script>" not in page


def test_code_is_escaped_and_marked_for_highlighting(client, db, me):
    user, home = me
    _write(db, user, f"{home}/x.py", b"print('<b>hi</b>')")
    page = client.get(f"/files/view?path={quote(home + '/x.py')}").text
    assert 'data-highlight="python"' in page
    assert "&lt;b&gt;hi&lt;/b&gt;" in page


def test_html_is_only_ever_in_the_sealed_frame(client, db, me):
    user, home = me
    _write(db, user, f"{home}/p.html", b"<h1>page</h1><script>top.location='x'</script>")
    page = client.get(f"/files/view?path={quote(home + '/p.html')}").text
    assert "<script>top.location" not in page
    assert 'sandbox="allow-scripts"' in page
    assert "allow-same-origin" not in page
    frame = client.get(f"/files/frame?path={quote(home + '/p.html')}")
    policy = frame.headers["content-security-policy"]
    assert "sandbox" in policy and "connect-src 'none'" in policy
    assert "default-src 'none'" in policy
    assert "<h1>page</h1>" in frame.text


def test_a_download_never_runs_as_a_page(client, db, me):
    user, home = me
    _write(db, user, f"{home}/p.html", b"<script>1</script>")
    raw = client.get(f"/files/raw?path={quote(home + '/p.html')}")
    assert raw.headers["content-type"] == "application/octet-stream"
    assert raw.headers["content-disposition"].startswith("attachment")
    assert raw.headers["x-content-type-options"] == "nosniff"
    # inline only for raster images, never HTML or SVG
    raw = client.get(f"/files/raw?path={quote(home + '/p.html')}&inline=1")
    assert raw.headers["content-type"] == "application/octet-stream"


def test_a_binary_file_is_offered_as_a_download(client, db, me):
    user, home = me
    _write(db, user, f"{home}/b.bin", b"\x00\x01\x02")
    page = client.get(f"/files/view?path={quote(home + '/b.bin')}").text
    assert "not a text file" in page


def test_versions_are_listed_and_restored(client, db, me):
    user, home = me
    node = _write(db, user, f"{home}/v.txt", b"one")
    _write(db, user, f"{home}/v.txt", b"two")
    first = db.scalars(
        select(NodeVersion).where(NodeVersion.node_id == node.id).order_by(NodeVersion.at)
    ).first()
    page = client.get(f"/files/view?path={quote(home + '/v.txt')}").text
    assert 'data-testid="file-versions"' in page
    client.post("/files/versions/restore", data={"path": f"{home}/v.txt", "version": first.id})
    db.expire_all()
    assert fs.read(fs.view(db, user), f"{home}/v.txt") == b"one"


# --- The editor ----------------------------------------------------------------------


def test_the_editor_works_without_javascript(client, db, me):
    user, home = me
    node = _write(db, user, f"{home}/e.txt", b"old")
    page = client.get(f"/files/edit?path={quote(home + '/e.txt')}").text
    assert '<textarea class="textarea editor__text" name="content"' in page
    assert 'type="importmap"' in page
    r = client.post(
        "/files/save",
        data={"path": f"{home}/e.txt", "content": "new", "base": node.blob_hash},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert fs.read(fs.view(db, user), f"{home}/e.txt") == b"new"


def test_a_save_over_a_newer_file_is_refused_unless_forced(client, db, me):
    user, home = me
    node = _write(db, user, f"{home}/c.txt", b"one")
    stale = node.blob_hash
    _write(db, user, f"{home}/c.txt", b"two")
    headers = {"Accept": "application/json"}
    r = client.post("/files/save", data={"path": f"{home}/c.txt", "content": "mine",
                                         "base": stale}, headers=headers)
    assert r.status_code == 409
    assert r.json()["conflict"] is True
    r = client.post("/files/save", data={"path": f"{home}/c.txt", "content": "mine",
                                         "base": stale, "force": "1"}, headers=headers)
    assert r.status_code == 200 and r.json()["sha"]


def test_the_import_map_names_only_vendored_files():
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).parent / "web"
    page = (root / "templates/files/edit.html").read_text(encoding="utf-8")
    assert "http" not in page.split('type="importmap"')[1].split("</script>")[0]
    for name in ("cm-state.mjs", "cm-view.mjs", "cm-codemirror.mjs", "lezer-lr.mjs"):
        assert (root / "static/vendor" / name).exists()


# --- A chat's view: the show card ----------------------------------------------------


def _chat(db, user, **fields) -> Chat:
    chat = Chat(user_id=user.id, model_id="", **fields)
    db.add(chat)
    db.commit()
    return chat


def test_the_show_route_rechecks_the_chats_grants(client, db, me):
    user, home = me
    node = _write(db, user, f"{home}/plan.md", b"# Plan")
    chat = _chat(db, user)
    url = f"/chat/{chat.id}/show?node={node.id}"
    assert client.get(url).status_code == 200
    # The chat's grants narrowed to somewhere else: the card opens nothing.
    chat.grants_customised = True
    db.add(SessionGrant(chat_id=chat.id, path=f"{home}/skills", level="ro"))
    db.commit()
    response = client.get(url)
    assert response.status_code == 404
    assert "# Plan" not in response.text


def test_someone_elses_chat_has_no_show_route(client, db, me):
    from lembas.services import organisations

    user, home = me
    other = organisations.create_account(
        db, org_id=user.org_id, name="Pippin", email="pip@shire.test", password="second-breakfast"
    )
    node = _write(db, user, f"{home}/x.md", b"x")
    chat = _chat(db, other)
    assert client.get(f"/chat/{chat.id}/show?node={node.id}").status_code == 404


def test_the_card_is_drawn_from_the_show_payload():
    from lembas.web.templating import templates

    class Message:
        id = "m1"
        chat_id = "c1"

    event = {
        "name": "show",
        "status": "ok",
        "show": {"path": "/home/f/<b>.md", "name": "<b>.md", "kind": "markdown", "line": 3,
                 "node_id": "n1", "version_id": "v1"},
    }
    html = templates.get_template("chat/_tool_activity.html").render(
        {"tool_events": [event], "message": Message(), "message_id": "m1", "step_index": 0}
    )
    assert 'data-testid="show-card"' in html
    assert "/chat/c1/show?node=n1&amp;version=v1" in html
    assert "&amp;line=3" in html
    assert "<b>.md" not in html  # escaped


# --- Undo --------------------------------------------------------------------------------


def test_undo_puts_back_what_the_chat_changed(client, db, me):
    user, home = me
    _write(db, user, f"{home}/keep.txt", b"before")
    chat = _chat(db, user)
    _write(db, user, f"{home}/keep.txt", b"the agent's", chat=chat)
    _write(db, user, f"{home}/made.txt", b"new", chat=chat)
    plan = file_ui.undo_plan(db, user, chat)
    assert {(u.path.rsplit("/", 1)[-1], u.action) for u in plan} == {
        ("keep.txt", "restore"),
        ("made.txt", "remove"),
    }
    page = client.get(f"/chat/{chat.id}/undo").text
    assert "keep.txt" in page and "made.txt" in page
    client.post(f"/chat/{chat.id}/undo")
    db.expire_all()
    v = fs.view(db, user)
    assert fs.read(v, f"{home}/keep.txt") == b"before"
    assert fs.lookup(db, user.org_id, f"{home}/made.txt") is None
    # And nothing left to undo.
    assert file_ui.undo_plan(db, user, chat) == []


# --- The tick tree --------------------------------------------------------------------


def test_an_unticked_ancestor_of_a_tick_is_a_passage():
    rules = {"/AGENTS.md": "ro", "/Dir1/Dir3": "ro"}
    assert file_ui.is_passage(rules, "/Dir1")
    assert not file_ui.is_passage(rules, "/Dir1/Dir3")
    assert not file_ui.is_passage(rules, "/Dir2")
    assert file_ui.effective(rules, "/Dir1/Dir3/deep") == "ro"
    assert file_ui.effective(rules, "/Dir1/FILE1.txt") == "none"
    # Under a ticked folder nothing is a passage.
    assert not file_ui.is_passage({"/": "ro", "/a/b": "stage"}, "/a")


def test_a_chat_gets_grants_of_its_own_and_can_go_back(client, db, me):
    user, home = me
    chat = _chat(db, user)
    page = client.get(f"/chat/{chat.id}/access").text
    assert 'data-testid="tick-tree"' in page
    r = client.post(
        f"/chat/{chat.id}/access",
        data={"tick": [f"{home}/skills"], f"level:{home}/skills": "ro"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    chat = db.get(Chat, chat.id)
    assert chat.grants_customised
    assert [(g.path, g.level) for g in db.scalars(select(SessionGrant))] == [
        (f"{home}/skills", "ro")
    ]
    page = client.get(f"/chat/{chat.id}/access").text
    assert "is-passage" in page  # /home and the home directory above the tick
    client.post(f"/chat/{chat.id}/access", data={"reset": "1"})
    db.expire_all()
    assert not db.get(Chat, chat.id).grants_customised


def test_a_data_groups_floor_is_saved(client, db, me):
    from lembas.db.models import DataGroupRule
    from lembas.services import data_groups

    _user, home = me
    group = data_groups.DEFAULT_GROUP
    r = client.post(
        f"/data-groups/{group}/access?mode=floor",
        data={"tick": ["/"], "level:/": "ro", "add_path": "/home", "add_level": "stage"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    rows = {(x.path, x.level, x.mode) for x in db.scalars(select(DataGroupRule))}
    assert rows == {("/", "ro", "floor"), ("/home", "stage", "floor")}


def test_a_bad_level_or_path_is_dropped():
    from starlette.datastructures import FormData

    form = FormData([("tick", "/a"), ("level:/a", "root"), ("tick", "../.."),
                     ("tick", "/b"), ("level:/b", "ro")])
    assert file_ui.parse_rules(form, levels=("none", "ro", "stage", "commit")) == {"/b": "ro"}


def test_the_explorer_rows_are_on_one_grid_with_subgrid_rows():
    from pathlib import Path

    import lembas

    css = (Path(lembas.__file__).parent / "web/static/css/files.css").read_text()
    for block in (".file-row {", ".tick-row {", ".versions__item {"):
        body = css.split(block, 1)[1].split("}", 1)[0]
        assert "grid-template-columns: subgrid" in body, block
    assert "minmax(min(100%, 14rem), 1fr)" in css
    assert Node  # imported for the fixtures' sake
