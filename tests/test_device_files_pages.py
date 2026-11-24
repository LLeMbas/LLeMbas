"""A linked computer as one more place of the Files page (`/files/device/{id}…`):
browse, open, edit text, upload, download -- the cookie's door to the relay
`tests/test_device_files.py` holds. The computer is that file's stand-in.
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from lembas.db.models import User
from lembas.services import device_link, settings_store
from tests.conftest import join
from tests.test_device_files import ROOT, Computer
from tests.test_device_sync import _agents_on, _clean, _linked  # noqa: F401
from tests.test_media_jobs import PNG

PAGE = b"<!doctype html><script>alert(1)</script>"


def _hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def rig(db, registered):
    computer = Computer()
    link, _ = _linked(db, answer=computer)
    link.hello["limits"]["roots"] = [ROOT]
    link.initialized = {"_meta": {"lembas": {"capabilities": ["sessions", "fs"]}}}
    return {"computer": computer, "link": link, "base": f"/files/device/{link.token_id}"}


def test_the_files_page_names_the_linked_computer_as_a_place(client, rig):
    page = client.get("/files", follow_redirects=True).text
    assert 'data-testid="files-places"' in page and 'data-testid="files-place"' in page
    assert f'href="/files/device/{rig["link"].token_id}"' in page
    assert "linked computer" in page and "device-1" in page


def test_a_computer_that_does_not_show_its_files_is_not_a_place(client, rig):
    rig["link"].initialized = {"_meta": {"lembas": {"capabilities": ["sessions"]}}}
    page = client.get("/files", follow_redirects=True).text
    assert 'data-testid="files-places"' not in page and "/files/device/" not in page


def test_the_computers_roots_then_a_folder(client, rig):
    roots = client.get(rig["base"])
    assert roots.status_code == 200
    assert f'href="{rig["base"]}?path=/home/dev/project"' in roots.text
    # No folder is open: nothing to upload into.
    assert 'data-testid="device-upload"' not in roots.text
    here = client.get(rig["base"], params={"path": ROOT}).text
    assert here.count('data-testid="device-row"') == 3
    assert f'href="{rig["base"]}?path=/home/dev/project/src"' in here
    assert f'href="{rig["base"]}/view?path=/home/dev/project/oven.py"' in here
    assert f'href="{rig["base"]}/raw?path=/home/dev/project/loaf.png"' in here
    assert 'data-testid="device-upload"' in here
    # Where you are: the server's files, the computer, the shared folder.
    crumbs = here.split('data-testid="file-crumbs"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/files"' in crumbs and "device-1" in crumbs and ">project<" in crumbs
    assert "/home/dev<" not in crumbs and 'path=/home"' not in crumbs


def test_an_entry_the_computer_will_not_open_is_named_and_not_a_link(client, rig):
    computer = rig["computer"]
    real = computer._list

    def listed(params):
        got = real(params)
        if params.get("path") == ROOT:
            got["entries"] += [
                {"name": ".env", "path": f"{ROOT}/.env", "kind": "file", "size": 0, "mtime": 0,
                 "protected": True},
                {"name": "shared", "path": f"{ROOT}/shared", "kind": "dir", "size": 0,
                 "mtime": 0, "link": "/srv/shared", "outside": True},
                {"name": '<img src=x onerror="alert(1)">', "path": f"{ROOT}/x.txt",
                 "kind": "file", "size": 1, "mtime": 0},
            ]
        return got

    computer._list = listed
    page = client.get(rig["base"], params={"path": ROOT}).text
    assert "protected" in page and "outside the shared folders" in page
    assert "path=/home/dev/project/.env" not in page
    assert "path=/home/dev/project/shared" not in page
    # A name is text, whatever it holds.
    assert "<img src=x" not in page and "&lt;img src=x" in page


def test_another_persons_computer_is_refused_on_every_route(client, db, rig):
    settings_store.update(db, {"default_permissions": {"tools.agent": True}})
    join(client, {"name": "Sam", "email": "sam@shire.test", "password": "second-breakfast-now"})
    assert db.scalar(select(User).where(User.email == "sam@shire.test")) is not None
    page = client.get(rig["base"], params={"path": ROOT})
    assert 'data-testid="device-problem"' in page.text and "oven.py" not in page.text
    view = client.get(rig["base"] + "/view", params={"path": f"{ROOT}/oven.py"})
    assert 'data-testid="device-problem"' in view.text and "lembas" not in view.text.split(
        'data-testid="device-problem"')[1][:600]
    assert client.get(rig["base"] + "/raw", params={"path": f"{ROOT}/oven.py"}).status_code == 404
    saved = client.post(rig["base"] + "/save", headers={"accept": "application/json"},
                        data={"path": f"{ROOT}/oven.py", "content": "x", "force": "1"})
    assert saved.status_code == 404
    client.post(rig["base"] + "/upload", data={"path": ROOT},
                files=[("files", ("a.txt", b"x"))])
    assert rig["computer"].asked == []
    # And Sam's own Files page does not name it.
    assert "/files/device/" not in client.get("/files", follow_redirects=True).text


def test_a_text_file_opens_in_the_editor_with_its_hash(client, rig):
    page = client.get(rig["base"] + "/view", params={"path": f"{ROOT}/oven.py"}).text
    data = rig["computer"].files[f"{ROOT}/oven.py"]
    assert 'data-testid="file-editor"' in page and "data-editor" in page
    assert f'action="{rig["base"]}/save"' in page
    assert f'name="base" value="{_hash(data)}"' in page
    assert "def bake():" in page and 'data-language="python"' in page
    assert "cm-codemirror.mjs" in page


def test_saving_writes_through_the_base_check(client, rig):
    path = f"{ROOT}/oven.py"
    base = _hash(rig["computer"].files[path])
    ok = client.post(rig["base"] + "/save", headers={"accept": "application/json"},
                     data={"path": path, "content": "x = 1\n", "base": base})
    assert ok.status_code == 200 and ok.json()["sha"] == _hash(b"x = 1\n")
    assert rig["computer"].files[path] == b"x = 1\n"
    # The same base again: the file is something else by now.
    stale = client.post(rig["base"] + "/save", headers={"accept": "application/json"},
                        data={"path": path, "content": "mine\n", "base": base})
    assert stale.status_code == 409 and stale.json()["conflict"] is True
    assert "changed on the computer" in stale.json()["detail"]
    assert rig["computer"].files[path] == b"x = 1\n"
    # "Save anyway", which the person was asked about.
    forced = client.post(rig["base"] + "/save", headers={"accept": "application/json"},
                         data={"path": path, "content": "mine\n", "base": base, "force": "1"})
    assert forced.status_code == 200 and rig["computer"].files[path] == b"mine\n"


def test_saving_without_javascript_comes_back_to_the_file(client, rig):
    path = f"{ROOT}/oven.py"
    base = _hash(rig["computer"].files[path])
    done = client.post(rig["base"] + "/save", data={"path": path, "content": "y\n", "base": base},
                       follow_redirects=False)
    assert done.status_code == 303 and "/view?path=" in done.headers["location"]
    assert "saved=" in done.headers["location"]
    stale = client.post(rig["base"] + "/save", data={"path": path, "content": "z\n", "base": base},
                        follow_redirects=False)
    assert "error=" in stale.headers["location"]
    # A save that names no base is not a save.
    bare = client.post(rig["base"] + "/save", headers={"accept": "application/json"},
                       data={"path": path, "content": "z\n"})
    assert bare.status_code == 400 and rig["computer"].files[path] == b"y\n"


def test_a_read_only_computer_shows_the_text_and_no_editor(client, rig):
    rig["link"].hello["limits"]["max_mode"] = "plan"
    page = client.get(rig["base"] + "/view", params={"path": f"{ROOT}/oven.py"}).text
    assert 'data-testid="file-editor"' not in page and "read-only" in page
    assert "def bake():" in page
    here = client.get(rig["base"], params={"path": ROOT}).text
    assert 'data-testid="device-upload"' not in here


def test_a_picture_is_shown_and_a_page_called_a_picture_is_not(client, rig):
    page = client.get(rig["base"] + "/view", params={"path": f"{ROOT}/loaf.png"}).text
    assert 'data-testid="viewer-image"' in page and "inline=1" in page
    raw = client.get(rig["base"] + "/raw", params={"path": f"{ROOT}/loaf.png", "inline": 1})
    assert raw.headers["content-type"] == "image/png" and raw.content == PNG
    rig["computer"].files[f"{ROOT}/holiday.png"] = PAGE
    view = client.get(rig["base"] + "/view", params={"path": f"{ROOT}/holiday.png"}).text
    # Its bytes are text: shown as text, escaped, never as a page or a picture.
    assert 'data-testid="viewer-image"' not in view.split('data-testid="device-file"')[1]
    assert "<script>alert(1)</script>" not in view and "&lt;script&gt;alert(1)" in view
    raw = client.get(rig["base"] + "/raw", params={"path": f"{ROOT}/holiday.png", "inline": 1})
    assert raw.headers["content-type"] == "application/octet-stream"
    assert "sandbox" in raw.headers["content-security-policy"]


def test_a_file_with_no_viewer_is_a_card_with_download(client, rig):
    rig["computer"].files[f"{ROOT}/core.dump"] = b"\x00\x01\x02" * 100
    page = client.get(rig["base"] + "/view", params={"path": f"{ROOT}/core.dump"}).text
    assert 'data-testid="viewer-card"' in page and 'data-testid="file-editor"' not in page
    assert f'href="{rig["base"]}/raw?path=/home/dev/project/core.dump"' in page


def test_upload_writes_into_the_folder_and_never_over(client, rig):
    done = client.post(rig["base"] + "/upload", data={"path": f"{ROOT}/src"},
                       files=[("files", ("note.md", b"# Note\n"))], follow_redirects=False)
    assert done.status_code == 303 and "saved=" in done.headers["location"]
    assert rig["computer"].files[f"{ROOT}/src/note.md"] == b"# Note\n"
    again = client.post(rig["base"] + "/upload", data={"path": f"{ROOT}/src"},
                        files=[("files", ("note.md", b"other"))], follow_redirects=False)
    assert "error=" in again.headers["location"]
    assert rig["computer"].files[f"{ROOT}/src/note.md"] == b"# Note\n"


def test_a_refusal_of_the_computer_is_said_on_the_page(client, rig):
    page = client.get(rig["base"], params={"path": "/etc"})
    assert 'data-testid="device-problem"' in page.text
    assert "outside what this computer shares" in page.text
    device_link.LINKS.clear()
    away = client.get(rig["base"], params={"path": ROOT})
    assert "not one of yours" in away.text or "not linked right now" in away.text


def test_a_post_from_another_site_is_refused(client, rig):
    """The Origin check covers these routes like every page route."""
    path = f"{ROOT}/oven.py"
    answer = client.post(rig["base"] + "/save", headers={"origin": "https://example.org"},
                         data={"path": path, "content": "x", "force": "1"})
    assert answer.status_code == 403
    assert rig["computer"].files[path].startswith(b"def bake")


def test_a_file_that_runs_code_opens_read_only(client, rig):
    rig["computer"].dirs.add(f"{ROOT}/.git")
    rig["computer"].files[f"{ROOT}/.git/config"] = b"[core]\n"
    page = client.get(rig["base"] + "/view", params={"path": f"{ROOT}/.git/config"}).text
    assert 'data-testid="file-editor"' not in page and "[core]" in page
    assert "files that run code" in page
    rig["link"].hello["limits"]["files_that_run_code"] = True
    page = client.get(rig["base"] + "/view", params={"path": f"{ROOT}/.git/config"}).text
    assert 'data-testid="file-editor"' in page


def test_a_chat_is_drawn_for_a_person_with_no_model_on_this_server(client, db, registered,
                                                                    make_chat):
    """Somebody whose only chats run on a linked computer has no chat model
    here, and still has their conversation: the "No models available" card is
    a fresh install's, never in place of a chat that exists."""
    from lembas.db.models import Chat, Model
    from lembas.services import chat as chat_service

    assert db.scalar(select(Model)) is None
    chat_id = make_chat()
    chat_service.create_message(db, db.get(Chat, chat_id), "user", "Where is the waybread?")
    chat_service.create_message(db, db.get(Chat, chat_id), "assistant", "In the leaf.")
    page = client.get(f"/chat/{chat_id}").text
    assert "Where is the waybread?" in page and "In the leaf." in page
    assert "No models available" not in page
    # A fresh install's first page still says what to do.
    assert "No models available" in client.get("/", follow_redirects=True).text
