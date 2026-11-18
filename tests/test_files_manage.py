"""File management on both doors: copy, save, bytes with ranges, the trash,
versions, batches, a zip, the upload ceiling -- `/api/v2/files` for the apps
and `/files` for the page, over the same `services/fs.py`."""

from __future__ import annotations

import io
import json
import zipfile
from urllib.parse import quote

import pytest
from sqlalchemy import select

from lembas.db.models import User
from lembas.security import api_tokens
from lembas.services import blobs, fs, settings_store

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
SVG = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"


@pytest.fixture(autouse=True)
def _fresh_rates():
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()
    yield


@pytest.fixture
def me(db, registered):
    user = db.scalars(select(User).order_by(User.created_at)).first()
    home = fs.ensure_home(db, user).path
    db.commit()
    _, raw = api_tokens.create(db, user, name="LLeMbas for Android", scopes=["app"])
    return user, home, {"Authorization": f"Bearer {raw}"}


def _write(db, user, path: str, data: bytes):
    node = fs.write(fs.view(db, user), path, data)
    db.commit()
    return node


def _names(listing: dict) -> list[str]:
    return [e["name"] for e in listing["entries"]]


# --- Kinds ------------------------------------------------------------------------------------
def test_a_listing_and_a_content_answer_say_what_a_file_is(client, db, me):
    user, home, headers = me
    _write(db, user, f"{home}/notes.md", b"# Notes")
    _write(db, user, f"{home}/map.png", PNG)
    _write(db, user, f"{home}/logo.svg", SVG)
    _write(db, user, f"{home}/data.csv", b"a,b\n1,2\n")
    fs.mkdir(fs.view(db, user), f"{home}/folder")
    db.commit()
    listed = client.get("/api/v2/files", headers=headers, params={"path": home}).json()
    kinds = {e["name"]: (e["kind"], e["file_kind"], e["mime"]) for e in listed["entries"]}
    assert kinds["folder"] == ("dir", None, "")
    assert kinds["notes.md"] == ("file", "markdown", "text/markdown")
    assert kinds["map.png"] == ("file", "image", "image/png")
    assert kinds["logo.svg"] == ("file", "svg", "image/svg+xml")
    assert kinds["data.csv"] == ("file", "table", "text/csv")

    def content(name: str) -> dict:
        return client.get(
            "/api/v2/files/content", headers=headers, params={"path": f"{home}/{name}"}
        ).json()

    notes = content("notes.md")
    assert (notes["kind"], notes["file_kind"], notes["text"]) == ("markdown", "markdown", "# Notes")
    assert notes["writable"] is True and notes["level"] in ("stage", "commit")
    picture = content("map.png")
    assert picture["kind"] == "image" and picture["withheld"]["reason"] == "not_text"
    # An SVG comes as its source: text, of its own kind.
    drawing = content("logo.svg")
    assert drawing["kind"] == "svg" and drawing["text"].startswith("<svg")
    assert content("data.csv")["kind"] == "table"


def test_an_upload_is_typed_by_its_bytes_not_by_what_was_claimed(client, db, me):
    _user, home, headers = me
    up = client.post(
        "/api/v2/files/upload", headers=headers, params={"path": home},
        files=[("files", ("page.png", b"<script>alert(1)</script>", "image/png")),
               ("files", ("real.bin", PNG, "text/html"))],
    )
    assert up.status_code == 201, up.text
    found = {e["name"]: (e["file_kind"], e["mime"]) for e in up.json()["entries"]}
    assert found["page.png"] == ("text", "text/plain")
    assert found["real.bin"] == ("image", "image/png")
    # Asked for inline, words called a picture still go out as a download.
    raw = client.get(
        "/api/v2/files/raw", headers=headers, params={"path": f"{home}/page.png", "inline": 1}
    )
    assert raw.headers["content-type"] == "application/octet-stream"
    assert raw.headers["content-disposition"].startswith("attachment")


# --- Copy --------------------------------------------------------------------------------------
def test_copy_through_the_api(client, db, me):
    user, home, headers = me
    _write(db, user, f"{home}/a.md", b"a")
    made = client.post(
        "/api/v2/files/copy", headers=headers, json={"path": f"{home}/a.md", "to": home}
    )
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["path"] == home and body["copied"]["name"] == "a (2).md"
    assert {"a.md", "a (2).md"} <= set(_names(body))
    named = client.post(
        "/api/v2/files/copy", headers=headers,
        json={"path": f"{home}/a.md", "to": home, "name": "b.md"},
    ).json()
    assert named["copied"]["path"] == f"{home}/b.md"
    refused = client.post(
        "/api/v2/files/copy", headers=headers, json={"path": f"{home}/none.md", "to": home}
    )
    assert refused.status_code == 400 and refused.json()["detail"]
    assert client.post(
        "/api/v2/files/copy", headers=headers,
        json={"path": f"{home}/a.md", "to": home, "name": "x/y"},
    ).status_code == 400


# --- Save --------------------------------------------------------------------------------------
def test_a_save_over_a_newer_file_is_a_409_with_what_it_holds_now(client, db, me):
    user, home, headers = me
    path = f"{home}/a.md"
    _write(db, user, path, b"one")
    opened = client.get("/api/v2/files/content", headers=headers, params={"path": path}).json()
    saved = client.put(
        "/api/v2/files/content", headers=headers,
        json={"path": path, "text": "two", "base": opened["sha"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["text"] == "two" and saved.json()["sha"] != opened["sha"]
    # Somebody else's tab, still holding the first version.
    late = client.put(
        "/api/v2/files/content", headers=headers,
        json={"path": path, "text": "three", "base": opened["sha"]},
    )
    assert late.status_code == 409
    conflict = late.json()
    assert conflict["conflict"] is True and conflict["sha"] == saved.json()["sha"]
    assert conflict["version"] and conflict["detail"]
    assert fs.read(fs.view(db, user), path) == b"two"
    forced = client.put(
        "/api/v2/files/content", headers=headers,
        json={"path": path, "text": "three", "base": opened["sha"], "force": True},
    )
    assert forced.status_code == 200 and forced.json()["text"] == "three"
    # A path with no file makes one; no text is not a save.
    new = client.put(
        "/api/v2/files/content", headers=headers, json={"path": f"{home}/new.txt", "text": ""}
    )
    assert new.status_code == 200 and new.json()["kind"] == "text"
    assert client.put(
        "/api/v2/files/content", headers=headers, json={"path": path}
    ).status_code == 400
    assert client.put(
        "/api/v2/files/content", headers=headers,
        json={"path": "/", "text": "x"},
    ).status_code == 400


# --- Bytes -------------------------------------------------------------------------------------
@pytest.mark.parametrize("door", ["api", "page"])
def test_bytes_go_out_safely_and_in_ranges(client, db, me, door, monkeypatch):
    user, home, headers = me
    monkeypatch.setattr(blobs, "PART_SIZE", 16)
    data = bytes(range(256)) * 2
    _write(db, user, f"{home}/clip.bin", data)
    _write(db, user, f"{home}/map.png", PNG)
    _write(db, user, f"{home}/logo.svg", SVG)
    _write(db, user, f"{home}/page.html", b"<script>alert(1)</script>")

    def get(name: str, **extra):
        send = dict(extra.pop("headers", {}))
        if door == "api":
            return client.get("/api/v2/files/raw", headers={**headers, **send},
                              params={"path": f"{home}/{name}", **extra})
        return client.get("/files/raw", headers=send, params={"path": f"{home}/{name}", **extra})

    whole = get("clip.bin")
    assert whole.status_code == 200 and whole.content == data
    assert whole.headers["accept-ranges"] == "bytes"
    assert whole.headers["content-length"] == str(len(data))
    assert whole.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in whole.headers["content-security-policy"]
    assert whole.headers["content-disposition"].startswith("attachment")

    for asked, start, end in (("bytes=0-0", 0, 0), ("bytes=10-40", 10, 40),
                              ("bytes=500-", 500, 511), ("bytes=-12", 500, 511),
                              ("bytes=30-9999", 30, 511)):
        part = get("clip.bin", headers={"Range": asked})
        assert part.status_code == 206, asked
        assert part.content == data[start:end + 1]
        assert part.headers["content-range"] == f"bytes {start}-{end}/{len(data)}"
        assert part.headers["content-length"] == str(end - start + 1)
    past = get("clip.bin", headers={"Range": "bytes=512-"})
    assert past.status_code == 416 and past.headers["content-range"] == f"bytes */{len(data)}"
    # A range of a file that changed since is the whole new file.
    stale = get("clip.bin", headers={"Range": "bytes=0-3", "If-Range": '"somethingelse"'})
    assert stale.status_code == 200 and stale.content == data

    # Inline only for what a browser decodes and never runs -- by the bytes.
    picture = get("map.png", inline=1)
    assert picture.headers["content-type"] == "image/png"
    assert "content-disposition" not in picture.headers
    assert get("map.png").headers["content-type"] == "application/octet-stream"
    for name in ("logo.svg", "page.html"):
        never = get(name, inline=1)
        assert never.headers["content-type"] == "application/octet-stream", name
        assert never.headers["content-disposition"].startswith("attachment")
    assert get("nothing.bin").status_code == 404


def test_an_older_version_is_downloaded_by_its_id(client, db, me):
    user, home, headers = me
    path = f"{home}/a.md"
    _write(db, user, path, b"one")
    _write(db, user, path, b"two")
    listed = client.get("/api/v2/files/versions", headers=headers, params={"path": path}).json()
    assert listed["path"] == path and [v["current"] for v in listed["versions"]] == [True, False]
    newest, oldest = listed["versions"]
    assert newest["sha"] == listed["sha"] and oldest["size"] == 3 and newest["by"] == user.name
    assert newest["at"] and newest["chat_id"] is None
    old = client.get(
        "/api/v2/files/raw", headers=headers, params={"path": path, "version": oldest["id"]}
    )
    assert old.content == b"one"
    back = client.post(
        "/api/v2/files/versions/restore", headers=headers,
        json={"path": path, "version": oldest["id"]},
    )
    assert back.status_code == 200, back.text
    assert len(back.json()["versions"]) == 3 and back.json()["sha"] == oldest["sha"]
    assert fs.read(fs.view(db, user), path) == b"one"
    assert client.post(
        "/api/v2/files/versions/restore", headers=headers, json={"path": path, "version": "no"}
    ).status_code == 400
    assert client.get(
        "/api/v2/files/versions", headers=headers, params={"path": f"{home}/none"}
    ).status_code == 404


# --- The trash ---------------------------------------------------------------------------------
def test_the_trash_is_listed_restored_and_emptied(client, db, me):
    user, home, headers = me
    for name in ("a.md", "b.md", "c.md"):
        _write(db, user, f"{home}/{name}", name.encode())
        assert client.delete(
            "/api/v2/files", headers=headers, params={"path": f"{home}/{name}"}
        ).status_code == 204
    items = client.get("/api/v2/files/trash", headers=headers).json()["items"]
    assert sorted(i["name"] for i in items) == ["a.md", "b.md", "c.md"]
    by_name = {i["name"]: i for i in items}
    assert by_name["a.md"]["path"] == f"{home}/a.md" and by_name["a.md"]["trashed_at"]
    assert by_name["a.md"]["kind"] == "file"

    restored = client.post(
        "/api/v2/files/restore", headers=headers, json={"id": by_name["a.md"]["id"]}
    )
    assert restored.status_code == 200 and restored.json()["restored"] == f"{home}/a.md"
    assert len(restored.json()["items"]) == 2
    assert client.post(
        "/api/v2/files/restore", headers=headers, json={"id": "nothing"}
    ).status_code == 400

    one = client.delete(
        "/api/v2/files/trash", headers=headers, params={"id": by_name["b.md"]["id"]}
    )
    assert one.status_code == 200 and one.json()["removed"] == 1
    assert [i["name"] for i in one.json()["items"]] == ["c.md"]
    rest = client.delete("/api/v2/files/trash", headers=headers)
    assert rest.json() == {"removed": 1, "items": []}
    listed = client.get("/api/v2/files", headers=headers, params={"path": home}).json()
    assert "a.md" in _names(listed) and "b.md" not in _names(listed)


# --- Several at once ---------------------------------------------------------------------------
def test_a_batch_answers_for_each_path_and_does_what_it_can(client, db, me):
    user, home, headers = me
    for name in ("a.md", "b.md", "c.md"):
        _write(db, user, f"{home}/{name}", name.encode())
    fs.mkdir(fs.view(db, user), f"{home}/into")
    _write(db, user, f"{home}/into/b.md", b"already here")

    moved = client.post(
        "/api/v2/files/batch", headers=headers,
        json={"op": "move", "to": f"{home}/into",
              "paths": [f"{home}/a.md", f"{home}/b.md", f"{home}/none.md"]},
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert (body["op"], body["atomic"], body["done"], body["failed"]) == ("move", False, 1, 2)
    first, second, third = body["results"]
    assert first == {"path": f"{home}/a.md", "ok": True, "to": f"{home}/into/a.md"}
    assert second["ok"] is False and "Already exists" in second["error"] and "to" not in second
    assert third["ok"] is False and "No such file" in third["error"]
    # What could be done was done, and stayed done.
    assert fs.read(fs.view(db, user), f"{home}/into/a.md") == b"a.md"
    assert fs.read(fs.view(db, user), f"{home}/b.md") == b"b.md"

    copied = client.post(
        "/api/v2/files/batch", headers=headers,
        json={"op": "copy", "to": f"{home}/into", "paths": [f"{home}/b.md", f"{home}/c.md"]},
    ).json()
    assert [r["to"] for r in copied["results"]] == [f"{home}/into/b (2).md", f"{home}/into/c.md"]

    gone = client.post(
        "/api/v2/files/batch", headers=headers,
        json={"op": "delete", "paths": [f"{home}/b.md", f"{home}/b.md"]},
    ).json()
    assert [r["ok"] for r in gone["results"]] == [True, False] and gone["to"] is None

    for bad in ({"op": "shred", "paths": [f"{home}/c.md"]}, {"op": "delete", "paths": []},
                {"op": "delete"}, {"op": "move", "paths": [f"{home}/c.md"]},
                {"op": "delete", "paths": [1]}):
        assert client.post("/api/v2/files/batch", headers=headers, json=bad).status_code == 400


def _unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        return {name: archive.read(name) for name in archive.namelist()}


def test_a_zip_is_written_as_it_is_read_and_capped(client, db, me, monkeypatch):
    user, home, headers = me
    monkeypatch.setattr(blobs, "PART_SIZE", 1024)
    big = bytes(range(256)) * 20
    _write(db, user, f"{home}/d/one.md", b"1")
    _write(db, user, f"{home}/d/sub/big.bin", big)
    fs.mkdir(fs.view(db, user), f"{home}/d/empty")
    _write(db, user, f"{home}/lone.txt", b"lone")
    db.commit()
    out = client.post(
        "/api/v2/files/zip", headers=headers, json={"paths": [f"{home}/d", f"{home}/lone.txt"]}
    )
    assert out.status_code == 200, out.text
    assert out.headers["content-type"] == "application/zip"
    assert 'filename="files.zip"' in out.headers["content-disposition"]
    assert _unzip(out.content) == {
        "d/": b"", "d/empty/": b"", "d/one.md": b"1", "d/sub/": b"", "d/sub/big.bin": big,
        "lone.txt": b"lone",
    }
    one = client.post("/api/v2/files/zip", headers=headers, json={"paths": [f"{home}/d"]})
    assert 'filename="d.zip"' in one.headers["content-disposition"]

    # The page's door: the same archive from a form.
    page = client.post("/files/zip", data={"paths": json.dumps([f"{home}/lone.txt"])})
    assert page.status_code == 200 and _unzip(page.content) == {"lone.txt": b"lone"}

    # Over the ceiling: refused before a byte, in words.
    monkeypatch.setattr("lembas.services.file_ops.zip_limit", lambda _db: 1000)
    over = client.post("/api/v2/files/zip", headers=headers, json={"paths": [f"{home}/d"]})
    assert over.status_code == 413 and "zip" in over.json()["detail"]
    refused = client.post(
        "/files/zip", data={"paths": json.dumps([f"{home}/d"])}, follow_redirects=False
    )
    assert refused.status_code == 303 and "error=" in refused.headers["location"]
    assert client.post(
        "/api/v2/files/zip", headers=headers, json={"paths": [f"{home}/none"]}
    ).status_code == 404


# --- The upload ceiling ------------------------------------------------------------------------
def test_an_upload_over_the_ceiling_is_refused_and_leaves_nothing(client, db, me):
    _user, home, headers = me
    assert settings_store.extraction(db)["tree_upload_mb"] == 512
    settings_store.update(db, {"tree_upload_mb": 1}, key=settings_store.EXTRACTION)
    db.commit()
    big = b"x" * (1024 * 1024 + 1)
    files = [("files", ("small.txt", b"fits", "text/plain")),
             ("files", ("big.bin", big, "application/octet-stream"))]
    over = client.post(
        "/api/v2/files/upload", headers=headers, params={"path": home}, files=files
    )
    assert over.status_code == 413
    assert "big.bin" in over.json()["detail"] and "1 MB" in over.json()["detail"]
    page = client.post("/files/upload", data={"path": home}, files=files, follow_redirects=False)
    assert page.status_code == 303 and "error=" in page.headers["location"]
    assert "big.bin" in page.headers["location"]
    # All or nothing: the file that fitted was not kept either.
    listed = client.get("/api/v2/files", headers=headers, params={"path": home}).json()
    assert "small.txt" not in _names(listed) and "big.bin" not in _names(listed)
    exactly = client.post(
        "/api/v2/files/upload", headers=headers, params={"path": home},
        files=[("files", ("edge.bin", big[:-1], "application/octet-stream"))],
    )
    assert exactly.status_code == 201


def test_the_ceilings_are_an_administrators(client, db, me):
    saved = client.post(
        "/admin/extraction", data={"tree_upload_mb": "64", "tree_zip_mb": "9999999"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    db.expire_all()
    values = settings_store.extraction(db)
    assert (values["tree_upload_mb"], values["tree_zip_mb"]) == (64, 102_400)
    page = client.get("/admin/extraction")
    assert 'name="tree_upload_mb"' in page.text and 'name="tree_zip_mb"' in page.text


# --- The page ----------------------------------------------------------------------------------
def test_the_explorer_copies_selects_and_picks_a_folder(client, db, me):
    user, home, _headers = me
    _write(db, user, f"{home}/a.md", b"a")
    _write(db, user, f"{home}/b.md", b"b")
    fs.mkdir(fs.view(db, user), f"{home}/into/deeper")
    db.commit()
    page = client.get(f"/files?path={quote(home)}").text
    assert 'data-testid="batch-bar"' in page and 'data-testid="select-all"' in page
    assert page.count('data-testid="file-select"') == 4  # with the home's own skills/
    assert 'data-pick-folder="move"' in page and 'data-pick-folder="copy"' in page
    # Move no longer asks for a typed path.
    assert "A path such as" not in page and 'hx-post="/files/move"' not in page

    picker = client.get(f"/files/picker?path={quote(home + '/into')}")
    assert picker.status_code == 200
    assert f'data-pick-dir="{home}/into"' in picker.text
    assert f'data-pick-dir="{home}/into/deeper"' in picker.text  # opened down to where you are
    assert "<a " not in picker.text  # a name is chosen here, never followed
    branch = client.get(f"/files/dirs?path={quote(home + '/into')}&pick=1").text
    assert f'data-pick-dir="{home}/into/deeper"' in branch and "pick=1" in branch

    copied = client.post(
        "/files/copy", data={"path": f"{home}/a.md", "to": home, "back_to": home},
        follow_redirects=False,
    )
    assert copied.status_code == 303 and "saved=" in copied.headers["location"]
    assert quote("a (2).md") in copied.headers["location"]

    done = client.post(
        "/files/batch",
        data={"op": "move", "to": f"{home}/into", "back_to": home,
              "paths": json.dumps([f"{home}/a.md", f"{home}/b.md"])},
        follow_redirects=False,
    )
    assert done.status_code == 303 and "saved=" in done.headers["location"]
    assert fs.read(fs.view(db, user), f"{home}/into/b.md") == b"b"
    partly = client.post(
        "/files/batch",
        data={"op": "delete", "back_to": home,
              "paths": json.dumps([f"{home}/a (2).md", f"{home}/gone.md"])},
        follow_redirects=False,
    )
    assert partly.status_code == 303 and "error=" in partly.headers["location"]
    nothing = client.post(
        "/files/batch", data={"op": "delete", "paths": "not json"}, follow_redirects=False
    )
    assert "error=" in nothing.headers["location"]

    trash = client.get("/files/trash").text
    assert 'data-testid="trash-empty"' in trash and "a (2).md" in trash
    emptied = client.post("/files/trash/empty", follow_redirects=False)
    assert emptied.status_code == 303 and "saved=" in emptied.headers["location"]
    assert fs.trash(fs.view(db, user)) == []


def test_an_svg_is_shown_as_its_source_never_as_a_picture(client, db, me):
    user, home, _headers = me
    _write(db, user, f"{home}/logo.svg", SVG)
    _write(db, user, f"{home}/map.png", PNG)
    page = client.get(f"/files/view?path={quote(home + '/logo.svg')}").text
    assert "&lt;script&gt;" in page and "<img" not in page.split('data-testid="file-viewer"')[-1]
    picture = client.get(f"/files/view?path={quote(home + '/map.png')}").text
    assert 'class="viewer__image"' in picture and "inline=1" in picture
