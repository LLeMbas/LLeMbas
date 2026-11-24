"""A linked computer's files (harness spec 3.11.0, capability `fs`): the
relay (`services/device_files.py`) and the apps' door, `/api/v2/devices/{id}/fs…`.

The device is a `Link` whose requests are answered here by a small stand-in
that keeps files in a dict and refuses as the spec has a device refuse;
nothing runs on this machine. What is held here is this server's half: whose
device it is, what is passed on and how, what a device's answer is cut to,
and which HTTP status each refusal becomes.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from sqlalchemy import select

from lembas.db.models import User
from lembas.security import api_tokens
from lembas.services import device_files, device_link, settings_store
from tests.conftest import join
from tests.test_device_sync import _agents_on, _clean, _linked  # noqa: F401
from tests.test_media_jobs import PNG

ROOT = "/home/dev/project"


def _hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _refuse(reason: str, message: str, **data) -> device_link.DeviceError:
    error = device_link.DeviceError(message)
    error.reason = reason
    error.data = {"reason": reason, **data}
    return error


class Computer:
    """A device's side of `_lembas/fs/*`, in memory."""

    def __init__(self, *, trash=True, read_only=False, chunk=1024 * 1024):
        self.files: dict[str, bytes] = {
            f"{ROOT}/oven.py": b"def bake():\n    return 'lembas'\n",
            f"{ROOT}/loaf.png": PNG,
            f"{ROOT}/src/mill.py": b"grain = 3\n",
        }
        self.dirs = {ROOT, f"{ROOT}/src"}
        self.trash, self.read_only, self.chunk = trash, read_only, chunk
        self.uploads: dict[str, dict] = {}
        self.asked: list[tuple[str, dict]] = []
        self.mtime = 1767323045000

    def _inside(self, path: str) -> None:
        if not isinstance(path, str) or not (path == ROOT or path.startswith(ROOT + "/")):
            raise _refuse("outside", f"{path} is outside what this computer shares.")
        if path.endswith("/.env"):
            raise _refuse("protected", f"{path} is protected on this computer.")

    def _entry(self, path: str) -> dict:
        name = path.rsplit("/", 1)[-1]
        if path in self.dirs:
            return {"name": name, "path": path, "kind": "dir", "size": 0, "mtime": self.mtime}
        return {"name": name, "path": path, "kind": "file", "size": len(self.files[path]),
                "mtime": self.mtime}

    def __call__(self, method: str, params: dict):
        self.asked.append((method, dict(params or {})))
        name = method.removeprefix("_lembas/fs/")
        if name in ("write", "mkdir", "move", "copy", "delete") and self.read_only:
            raise _refuse("read_only", "This computer only shows its files here.")
        return getattr(self, f"_{name}")(params or {})

    def _list(self, p):
        if not p.get("path"):
            return {"path": "", "entries": [self._entry(ROOT)]}
        path = p["path"]
        self._inside(path)
        if path not in self.dirs:
            raise _refuse("not_a_directory", f"{path} is not a directory.")
        below = sorted(
            {q for q in (*self.dirs, *self.files) if q.rsplit("/", 1)[0] == path},
            key=lambda q: (q not in self.dirs, q),
        )
        return {"path": path, "parent": "" if path == ROOT else path.rsplit("/", 1)[0],
                "entries": [self._entry(q) for q in below]}

    def _stat(self, p):
        self._inside(p["path"])
        if p["path"] not in self.files and p["path"] not in self.dirs:
            raise _refuse("missing", f"{p['path']} does not exist.")
        entry = self._entry(p["path"])
        if p.get("hash") and p["path"] in self.files:
            entry["hash"] = _hash(self.files[p["path"]])
        return entry

    def _read(self, p):
        self._inside(p["path"])
        if p["path"] in self.dirs:
            raise _refuse("not_a_file", f"{p['path']} is a directory.")
        if p["path"] not in self.files:
            raise _refuse("missing", f"{p['path']} does not exist.")
        data = self.files[p["path"]]
        offset = p.get("offset") or 0
        piece = data[offset : offset + min(p.get("length") or self.chunk, self.chunk)]
        out = {"path": p["path"], "size": len(data), "mtime": self.mtime, "offset": offset,
               "data": base64.b64encode(piece).decode(), "eof": offset + len(piece) >= len(data)}
        if offset == 0 and out["eof"]:
            out["hash"] = _hash(data)
        return out

    def _check(self, path, head):
        now = self.files.get(path)
        if "base" in head:
            base = head["base"]
            if base is None and now is not None:
                raise _refuse("exists", f"{path} already exists.")
            if base is not None and (now is None or _hash(now) != base):
                raise _refuse(
                    "changed", f"{path} changed on the computer since you opened it.",
                    hash=_hash(now) if now is not None else None, mtime=self.mtime,
                )
        elif not head.get("overwrite"):
            raise _refuse("invalid", "A write names what it is based on, or says overwrite.")

    def _write(self, p):
        piece = base64.b64decode(p["data"])
        if "upload" in p:
            upload = self.uploads[p["upload"]]
        else:
            self._inside(p["path"])
            self._check(p["path"], p)
            upload = {"head": dict(p), "data": b""}
        upload["data"] += piece
        if p.get("more"):
            key = p.get("upload") or f"u{len(self.uploads) + 1}"
            self.uploads[key] = upload
            return {"upload": key, "received": len(upload["data"])}
        head = upload["head"]
        self._check(head["path"], head)
        self.files[head["path"]] = upload["data"]
        self.mtime += 1000
        return {"path": head["path"], "size": len(upload["data"]), "mtime": self.mtime,
                "hash": _hash(upload["data"])}

    def _mkdir(self, p):
        self._inside(p["path"])
        self.dirs.add(p["path"])
        return {"path": p["path"]}

    def _move(self, p, keep=False):
        self._inside(p["from"])
        self._inside(p["to"])
        if p["from"] not in self.files:
            raise _refuse("missing", f"{p['from']} does not exist.")
        if p["to"] in self.files and not p.get("overwrite"):
            raise _refuse("exists", f"{p['to']} already exists.")
        self.files[p["to"]] = self.files[p["from"]]
        if not keep:
            del self.files[p["from"]]
        return {"path": p["to"]}

    def _copy(self, p):
        return self._move(p, keep=True)

    def _delete(self, p):
        self._inside(p["path"])
        if p["path"] not in self.files:
            raise _refuse("missing", f"{p['path']} does not exist.")
        if not self.trash and not p.get("permanent"):
            raise _refuse("no_trash", f"This computer has no trash for {p['path']}: deleting "
                          "it cannot be undone.")
        del self.files[p["path"]]
        return {"deleted": True, "trash": self.trash and not p.get("permanent")}

    def _search(self, p):
        self._inside(p["path"])
        if p.get("content"):
            return {"root": p["path"], "matches": [
                {"path": q.removeprefix(p["path"] + "/"), "line": n, "text": line}
                for q, data in sorted(self.files.items())
                for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1)
                if p["query"] in line
            ]}
        return {"root": p["path"], "files": [
            {"path": q.removeprefix(p["path"] + "/"), "kind": "file"}
            for q in sorted(self.files) if p["query"] in q.rsplit("/", 1)[-1]
        ]}


@pytest.fixture
def rig(db, registered):
    """A linked computer that shows its files, and an app's token."""
    computer = Computer()
    link, _ = _linked(db, answer=computer)
    link.initialized = {"_meta": {"lembas": {"capabilities": ["sessions", "fs"]}}}
    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="LLeMbas Desktop", scopes=["app"])
    return {
        "computer": computer, "link": link, "user": user,
        "auth": {"Authorization": f"Bearer {raw}"},
        "base": f"/api/v2/devices/{link.token_id}/fs",
    }


# --- Whose computer ------------------------------------------------------------------------
def test_the_door_is_the_apps_token(client, rig):
    client.cookies.clear()
    assert client.get(rig["base"]).status_code == 401


def test_another_persons_computer_is_not_there(client, db, rig):
    settings_store.update(db, {"default_permissions": {"api.use": True, "tools.agent": True}})
    join(client, {"name": "Sam", "email": "sam@shire.test", "password": "second-breakfast-now"})
    sam = db.scalar(select(User).where(User.email == "sam@shire.test"))
    _, raw = api_tokens.create(db, sam, name="LLeMbas Desktop", scopes=["app"])
    auth = {"Authorization": f"Bearer {raw}"}
    oven = f"{ROOT}/oven.py"
    for method, url, kwargs in (
        ("get", rig["base"], {}),
        ("get", rig["base"] + "/stat", {"params": {"path": oven}}),
        ("get", rig["base"] + "/content", {"params": {"path": oven}}),
        ("get", rig["base"] + "/raw", {"params": {"path": oven}}),
        ("get", rig["base"] + "/search", {"params": {"path": ROOT, "q": "oven"}}),
        ("put", rig["base"] + "/content", {"json": {"path": oven, "text": "x", "force": True}}),
        ("post", rig["base"] + "/upload",
         {"params": {"path": ROOT}, "files": [("files", ("a.txt", b"x"))]}),
        ("post", rig["base"] + "/mkdir", {"json": {"path": f"{ROOT}/x"}}),
        ("post", rig["base"] + "/rename", {"json": {"path": oven, "name": "y.py"}}),
        ("post", rig["base"] + "/move", {"json": {"from": oven, "to": f"{ROOT}/y.py"}}),
        ("post", rig["base"] + "/move", {"json": {"paths": [oven], "to": f"{ROOT}/src"}}),
        ("post", rig["base"] + "/copy", {"json": {"from": oven, "to": f"{ROOT}/y.py"}}),
        ("post", rig["base"] + "/delete", {"json": {"paths": [oven]}}),
        ("delete", rig["base"], {"params": {"path": oven}}),
    ):
        answer = getattr(client, method)(url, headers=auth, **kwargs)
        assert answer.status_code == 404, (method, url)
        assert answer.json()["reason"] == "not_found"
    # Nothing reached the computer.
    assert rig["computer"].asked == []


def test_an_administrator_gets_nothing_for_being_one(client, db, rig):
    """The computer is its person's: the first account here is the instance's
    administrator, and it is refused another member's device like anybody."""
    settings_store.update(db, {"default_permissions": {"api.use": True, "tools.agent": True}})
    join(client, {"name": "Sam", "email": "sam@shire.test", "password": "second-breakfast-now"})
    sam = db.scalar(select(User).where(User.email == "sam@shire.test"))
    link = rig["link"]
    token = db.get(api_tokens.ApiToken, link.token_id)
    token.user_id = sam.id
    link.user_id = sam.id
    db.commit()
    assert rig["user"].is_admin
    assert client.get(rig["base"], headers=rig["auth"]).status_code == 404


def test_a_computer_that_is_not_linked_or_too_old_says_so(client, rig):
    rig["link"].initialized = {"_meta": {"lembas": {"capabilities": ["sessions"]}}}
    old = client.get(rig["base"], headers=rig["auth"])
    assert old.status_code == 409 and old.json()["reason"] == "cli_too_old"
    assert "too old" in old.json()["detail"]
    device_link.LINKS.clear()
    away = client.get(rig["base"], headers=rig["auth"])
    assert away.status_code == 409 and away.json()["reason"] == "device_not_linked"


def test_a_computer_that_does_not_answer_is_a_504(client, rig, monkeypatch):
    async def never(method, params=None, *, timeout=60):
        raise TimeoutError

    rig["link"].request = never
    answer = client.get(rig["base"], headers=rig["auth"])
    assert answer.status_code == 504 and answer.json()["reason"] == "no_answer"


def test_devices_say_whether_they_show_their_files(client, rig):
    (device,) = client.get("/api/v2/devices", headers=rig["auth"]).json()["devices"]
    assert device["files"] is True and device["files_writable"] is True
    rig["link"].hello["limits"]["max_mode"] = "plan"
    (device,) = client.get("/api/v2/devices", headers=rig["auth"]).json()["devices"]
    assert device["files"] is True and device["files_writable"] is False


# --- Browsing and reading ---------------------------------------------------------------------
def test_the_roots_then_a_directory(client, rig):
    roots = client.get(rig["base"], headers=rig["auth"]).json()
    assert roots["path"] == "" and [e["path"] for e in roots["entries"]] == [ROOT]
    here = client.get(rig["base"], headers=rig["auth"], params={"path": ROOT}).json()
    assert here["device"] == rig["link"].token_id and here["parent"] == ""
    assert [(e["name"], e["kind"]) for e in here["entries"]] == [
        ("src", "dir"), ("loaf.png", "file"), ("oven.py", "file")]
    oven = here["entries"][2]
    assert oven == {"name": "oven.py", "path": f"{ROOT}/oven.py", "kind": "file", "size": 32,
                    "mtime": 1767323045000, "updated_at": "2026-01-02T03:04:05+00:00",
                    "file_kind": "code", "link": None, "outside": False, "protected": False,
                    "runs_code": False, "writable": True}
    assert here["writable"] is True and roots["writable"] is False
    assert here["entries"][0]["file_kind"] is None
    # `hidden` is passed on, and only when asked.
    client.get(rig["base"], headers=rig["auth"], params={"path": ROOT, "hidden": 1})
    assert rig["computer"].asked[-1] == ("_lembas/fs/list", {"path": ROOT, "hidden": True})
    assert "hidden" not in rig["computer"].asked[-2][1]


def test_what_a_device_answers_is_cut_to_shape(db, rig):
    """A device's answer is somebody else's data: no key it invented and no
    value of the wrong type reaches an app."""
    got = device_files.listing("t", {"path": 7, "parent": None, "entries": [
        {"name": "x" * 900, "path": "/p/" + "y" * 9000, "kind": "socket", "size": "big",
         "mtime": -5, "link": 3, "outside": "yes", "protected": 1, "html": "<script>"},
        "not an entry", {"name": "no path"},
    ]})
    (entry,) = got["entries"]
    assert got["path"] == "" and got["parent"] == ""
    assert len(entry["name"]) == 255 and len(entry["path"]) == 4096
    assert entry["kind"] == "other" and entry["size"] == 0 and entry["mtime"] == 0
    assert entry["link"] is None and entry["outside"] is False and entry["protected"] is False
    assert "html" not in entry


def test_a_text_file_for_an_editor_carries_its_hash(client, rig):
    got = client.get(rig["base"] + "/content", headers=rig["auth"],
                     params={"path": f"{ROOT}/oven.py"}).json()
    data = rig["computer"].files[f"{ROOT}/oven.py"]
    # The tree's own shape (`GET /files/content`), with the device's hash as `sha`.
    assert got == {"path": f"{ROOT}/oven.py", "name": "oven.py", "size": len(data),
                   "mtime": 1767323045000, "mime": "text/x-python", "kind": "code",
                   "file_kind": "code", "writable": True, "language": "python",
                   "sha": _hash(data), "text": data.decode()}


def test_a_file_that_is_not_text_is_withheld_by_its_bytes(client, rig):
    got = client.get(rig["base"] + "/content", headers=rig["auth"],
                     params={"path": f"{ROOT}/loaf.png"}).json()
    assert got["text"] == "" and got["withheld"]["reason"] == "not_text"
    assert got["withheld"]["message"] and got["file_kind"] == "image"
    # Called a text file, and a picture: its bytes decide.
    rig["computer"].files[f"{ROOT}/notes.txt"] = PNG
    got = client.get(rig["base"] + "/content", headers=rig["auth"],
                     params={"path": f"{ROOT}/notes.txt"}).json()
    assert got["withheld"]["reason"] == "not_text"


def test_a_text_file_past_a_megabyte_is_withheld(client, rig):
    rig["computer"].files[f"{ROOT}/big.log"] = b"line\n" * 300_000
    got = client.get(rig["base"] + "/content", headers=rig["auth"],
                     params={"path": f"{ROOT}/big.log"}).json()
    assert got["text"] == "" and got["withheld"]["reason"] == "too_large"
    # One part was asked for: the rest is the download's.
    assert [m for m, _ in rig["computer"].asked].count("_lembas/fs/read") == 1


def test_the_bytes_are_a_download_read_in_parts(client, rig):
    data = bytes(range(256)) * 5000
    rig["computer"].files[f"{ROOT}/dump.bin"] = data
    rig["computer"].chunk = 300_000
    answer = client.get(rig["base"] + "/raw", headers=rig["auth"],
                        params={"path": f"{ROOT}/dump.bin"})
    assert answer.status_code == 200 and answer.content == data
    assert answer.headers["content-type"] == "application/octet-stream"
    assert "attachment" in answer.headers["content-disposition"]
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in answer.headers["content-security-policy"]
    offsets = [p["offset"] for m, p in rig["computer"].asked if m == "_lembas/fs/read"]
    assert offsets == [0, 300_000, 600_000, 900_000, 1_200_000]


def test_inline_is_for_what_the_bytes_say_is_a_picture_and_nothing_else(client, rig):
    picture = client.get(rig["base"] + "/raw", headers=rig["auth"],
                         params={"path": f"{ROOT}/loaf.png", "inline": 1})
    assert picture.headers["content-type"] == "image/png" and picture.content == PNG
    # A web page called a picture stays a download, whatever is asked.
    rig["computer"].files[f"{ROOT}/holiday.png"] = b"<!doctype html><script>alert(1)</script>"
    page = client.get(rig["base"] + "/raw", headers=rig["auth"],
                      params={"path": f"{ROOT}/holiday.png", "inline": 1})
    assert page.headers["content-type"] == "application/octet-stream"
    assert "attachment" in page.headers["content-disposition"]


@pytest.mark.parametrize(
    ("path", "status", "reason"),
    [
        ("/etc/passwd", 403, "outside"),
        (f"{ROOT}/.env", 403, "protected"),
        (f"{ROOT}/nothing.txt", 404, "missing"),
        (f"{ROOT}/src", 400, "not_a_file"),
    ],
)
def test_the_devices_refusal_is_passed_on_with_its_reason(client, rig, path, status, reason):
    for url in ("/content", "/raw"):
        answer = client.get(rig["base"] + url, headers=rig["auth"], params={"path": path})
        assert answer.status_code == status, answer.text
        body = answer.json()
        # The device's own sentence, and its reason as a word.
        assert body["reason"] == reason and path in body["detail"]


def test_a_path_is_required(client, rig):
    for method, url in (("get", "/content"), ("get", "/raw"), ("get", "/stat"),
                        ("delete", "")):
        answer = getattr(client, method)(rig["base"] + url, headers=rig["auth"])
        assert answer.status_code == 400 and answer.json()["reason"] == "invalid"
    assert rig["computer"].asked == []


def test_stat_with_a_hash(client, rig):
    got = client.get(rig["base"] + "/stat", headers=rig["auth"],
                     params={"path": f"{ROOT}/oven.py", "hash": 1}).json()
    assert got["sha"] == _hash(rig["computer"].files[f"{ROOT}/oven.py"])


# --- Writing ------------------------------------------------------------------------------
def test_a_save_carries_the_base_and_answers_the_new_hash(client, rig):
    path = f"{ROOT}/oven.py"
    base = _hash(rig["computer"].files[path])
    answer = client.put(rig["base"] + "/content", headers=rig["auth"],
                        json={"path": path, "text": "def bake():\n    return 2\n", "base": base})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"path": path, "size": 25, "mtime": 1767323046000,
                             "sha": _hash(b"def bake():\n    return 2\n")}
    assert rig["computer"].files[path] == b"def bake():\n    return 2\n"
    assert rig["computer"].asked[-1][1]["base"] == base


def test_a_file_that_changed_on_the_computer_is_a_conflict_and_is_not_written(client, rig):
    path = f"{ROOT}/oven.py"
    base = _hash(rig["computer"].files[path])
    rig["computer"].files[path] = b"somebody else's edit\n"
    answer = client.put(rig["base"] + "/content", headers=rig["auth"],
                        json={"path": path, "text": "mine\n", "base": base})
    assert answer.status_code == 409
    assert answer.json() == {
        "detail": f"{path} changed on the computer since you opened it.",
        "reason": "changed", "conflict": True,
        "sha": _hash(b"somebody else's edit\n"), "mtime": 1767323045000,
    }
    assert rig["computer"].files[path] == b"somebody else's edit\n"


def test_a_new_file_never_writes_over_one_and_no_base_is_refused(client, rig):
    path = f"{ROOT}/oven.py"
    exists = client.put(rig["base"] + "/content", headers=rig["auth"],
                        json={"path": path, "text": "x", "base": None})
    assert exists.status_code == 409 and exists.json()["reason"] == "exists"
    bare = client.put(rig["base"] + "/content", headers=rig["auth"],
                      json={"path": path, "text": "x"})
    assert bare.status_code == 400 and bare.json()["reason"] == "invalid"
    made = client.put(rig["base"] + "/content", headers=rig["auth"],
                      json={"path": f"{ROOT}/new.md", "text": "# New\n", "base": None})
    assert made.status_code == 200 and rig["computer"].files[f"{ROOT}/new.md"] == b"# New\n"
    forced = client.put(rig["base"] + "/content", headers=rig["auth"],
                        json={"path": path, "text": "x", "overwrite": True})
    assert forced.status_code == 200 and rig["computer"].files[path] == b"x"
    assert "base" not in rig["computer"].asked[-1][1]


@pytest.mark.parametrize(
    "body",
    [{"path": f"{ROOT}/a.txt"}, {"path": f"{ROOT}/a.txt", "text": 5},
     {"text": "x", "base": None}, {"path": f"{ROOT}/a.txt", "text": "x", "base": 9},
     {"path": f"{ROOT}/a.txt", "text": "x" * (1024 * 1024 + 1), "base": None}],
)
def test_a_malformed_save_never_reaches_the_computer(client, rig, body):
    answer = client.put(rig["base"] + "/content", headers=rig["auth"], json=body)
    assert answer.status_code in (400, 413) and answer.json()["reason"] in ("invalid", "too_large")
    assert rig["computer"].asked == []


def test_a_read_only_computer_refuses_every_write(client, rig):
    rig["computer"].read_only = True
    path = f"{ROOT}/oven.py"
    for method, url, kwargs in (
        ("put", "/content", {"json": {"path": path, "text": "x", "overwrite": True}}),
        ("post", "/mkdir", {"json": {"path": f"{ROOT}/x"}}),
        ("post", "/move", {"json": {"from": path, "to": f"{ROOT}/y.py"}}),
        ("post", "/copy", {"json": {"from": path, "to": f"{ROOT}/y.py"}}),
        ("delete", "", {"params": {"path": path}}),
    ):
        answer = getattr(client, method)(rig["base"] + url, headers=rig["auth"], **kwargs)
        assert answer.status_code == 403 and answer.json()["reason"] == "read_only", url
    assert len(rig["computer"].files) == 3


def test_an_upload_goes_in_parts_and_never_writes_over(client, rig, monkeypatch):
    from lembas.services.media import shown as shown_media

    monkeypatch.setattr(shown_media, "limits", lambda: {
        "max_bytes": 64 * 1024 * 1024, "chunk_bytes": 4096, "wait_seconds": 5})
    data = bytes(range(256)) * 40
    answer = client.post(
        rig["base"] + "/upload", headers=rig["auth"], params={"path": f"{ROOT}/src"},
        files=[("files", ("../../evil/sack.bin", data, "application/octet-stream")),
               ("files", ("note.md", b"# Note\n", "text/markdown"))],
    )
    assert answer.status_code == 201, answer.text
    body = answer.json()
    # A name is a name: nothing of the path it came with survives.
    assert [w["path"] for w in body["written"]] == [f"{ROOT}/src/sack.bin", f"{ROOT}/src/note.md"]
    assert body["written"][0]["sha"] == _hash(data)
    assert rig["computer"].files[f"{ROOT}/src/sack.bin"] == data
    assert {e["name"] for e in body["listing"]["entries"]} == {"mill.py", "note.md", "sack.bin"}
    writes = [p for m, p in rig["computer"].asked if m == "_lembas/fs/write"]
    # The first part names the file and its base; the rest name the upload.
    assert writes[0]["path"] == f"{ROOT}/src/sack.bin" and writes[0]["base"] is None
    assert writes[0]["more"] is True and writes[1]["upload"] == "u1" and "path" not in writes[1]
    assert "more" not in writes[2] and len(writes) == 4
    # Again: the names are taken.
    again = client.post(rig["base"] + "/upload", headers=rig["auth"],
                        params={"path": f"{ROOT}/src"}, files=[("files", ("note.md", b"x"))])
    assert again.status_code == 409 and again.json()["reason"] == "exists"
    over = client.post(rig["base"] + "/upload", headers=rig["auth"],
                       params={"path": f"{ROOT}/src", "overwrite": 1},
                       files=[("files", ("note.md", b"x"))])
    assert over.status_code == 201 and rig["computer"].files[f"{ROOT}/src/note.md"] == b"x"


def test_an_upload_past_the_instances_size_is_refused_in_words(client, rig, monkeypatch):
    from types import SimpleNamespace

    from lembas.services import files as files_service

    monkeypatch.setattr(files_service, "limits",
                        lambda: SimpleNamespace(max_upload_bytes=1024 * 1024))
    answer = client.post(rig["base"] + "/upload", headers=rig["auth"], params={"path": ROOT},
                         files=[("files", ("film.bin", b"\x00" * (1024 * 1024 + 1)))])
    assert answer.status_code == 413 and answer.json()["reason"] == "too_large"
    assert "film.bin" in answer.json()["detail"] and "1 MB" in answer.json()["detail"]
    assert rig["computer"].asked == []


def test_mkdir_move_and_copy(client, rig):
    made = client.post(rig["base"] + "/mkdir", headers=rig["auth"], json={"path": f"{ROOT}/out"})
    assert made.status_code == 201 and made.json() == {"path": f"{ROOT}/out"}
    copied = client.post(rig["base"] + "/copy", headers=rig["auth"],
                         json={"from": f"{ROOT}/oven.py", "to": f"{ROOT}/out/oven.py"})
    assert copied.status_code == 200 and copied.json() == {"path": f"{ROOT}/out/oven.py"}
    taken = client.post(rig["base"] + "/move", headers=rig["auth"],
                        json={"from": f"{ROOT}/src/mill.py", "to": f"{ROOT}/out/oven.py"})
    assert taken.status_code == 409 and taken.json()["reason"] == "exists"
    moved = client.post(rig["base"] + "/move", headers=rig["auth"],
                        json={"from": f"{ROOT}/src/mill.py", "to": f"{ROOT}/out/oven.py",
                              "overwrite": True})
    assert moved.status_code == 200 and f"{ROOT}/src/mill.py" not in rig["computer"].files
    out = client.post(rig["base"] + "/move", headers=rig["auth"],
                      json={"from": f"{ROOT}/oven.py", "to": "/tmp/oven.py"})
    assert out.status_code == 403 and out.json()["reason"] == "outside"
    assert client.post(rig["base"] + "/move", headers=rig["auth"],
                       json={"from": f"{ROOT}/oven.py"}).status_code == 400


def test_delete_goes_to_the_trash_or_asks_first(client, rig):
    gone = client.delete(rig["base"], headers=rig["auth"], params={"path": f"{ROOT}/oven.py"})
    assert gone.status_code == 200 and gone.json() == {"deleted": True, "trash": True}
    rig["computer"].trash = False
    asked = client.delete(rig["base"], headers=rig["auth"], params={"path": f"{ROOT}/loaf.png"})
    assert asked.status_code == 409 and asked.json()["reason"] == "no_trash"
    assert "cannot be undone" in asked.json()["detail"]
    assert f"{ROOT}/loaf.png" in rig["computer"].files
    for_good = client.delete(rig["base"], headers=rig["auth"],
                             params={"path": f"{ROOT}/loaf.png", "permanent": 1})
    assert for_good.json() == {"deleted": True, "trash": False}


def test_search_by_name_and_by_content(client, rig):
    names = client.get(rig["base"] + "/search", headers=rig["auth"],
                       params={"path": ROOT, "q": "mill"}).json()
    assert names == {"root": ROOT, "truncated": False,
                     "files": [{"path": "src/mill.py", "kind": "file"}]}
    lines = client.get(rig["base"] + "/search", headers=rig["auth"],
                       params={"path": ROOT, "q": "grain", "content": 1, "limit": 5000}).json()
    assert lines["matches"] == [{"path": "src/mill.py", "line": 1, "text": "grain = 3"}]
    asked = rig["computer"].asked[-1][1]
    assert asked["content"] is True and asked["limit"] == 500 and "regex" not in asked
    assert client.get(rig["base"] + "/search", headers=rig["auth"],
                      params={"path": ROOT}).status_code == 400


def test_a_file_that_changes_while_it_is_downloaded_ends_the_download(db, rig):
    """Half of one file and half of another is not a download."""
    import asyncio

    computer = rig["computer"]
    computer.files[f"{ROOT}/dump.bin"] = b"a" * 5000
    computer.chunk = 2000

    async def run():
        first = await device_files.read_first(db, rig["user"], rig["link"].token_id,
                                              f"{ROOT}/dump.bin")
        got = []
        with pytest.raises(device_files.Refused) as caught:
            async for piece in device_files.read_parts(
                db, rig["user"], rig["link"].token_id, f"{ROOT}/dump.bin", first
            ):
                got.append(piece)
                computer.files[f"{ROOT}/dump.bin"] = b"b" * 7000
        return got, caught.value

    got, refused = asyncio.run(run())
    assert got == [b"a" * 2000] and refused.reason == "changed"


# --- The shapes an app already knows --------------------------------------------------------
def test_a_save_says_what_it_is_based_on_in_three_ways(client, rig):
    path = f"{ROOT}/oven.py"
    made = client.put(rig["base"] + "/content", headers=rig["auth"],
                      json={"path": f"{ROOT}/new.txt", "text": "", "create": True})
    assert made.status_code == 200 and rig["computer"].asked[-1][1]["base"] is None
    taken = client.put(rig["base"] + "/content", headers=rig["auth"],
                       json={"path": path, "text": "", "create": True})
    assert taken.status_code == 409 and taken.json()["reason"] == "exists"
    forced = client.put(rig["base"] + "/content", headers=rig["auth"],
                        json={"path": path, "text": "x", "force": True})
    assert forced.status_code == 200 and rig["computer"].asked[-1][1]["overwrite"] is True


def test_mkdir_and_rename_by_name(client, rig):
    made = client.post(rig["base"] + "/mkdir", headers=rig["auth"],
                       json={"path": ROOT, "name": "out"})
    assert made.status_code == 201 and made.json() == {"path": f"{ROOT}/out"}
    renamed = client.post(rig["base"] + "/rename", headers=rig["auth"],
                          json={"path": f"{ROOT}/oven.py", "name": "stove.py"})
    assert renamed.status_code == 200 and renamed.json() == {"path": f"{ROOT}/stove.py"}
    assert f"{ROOT}/stove.py" in rig["computer"].files
    for name in ("a/b", "..", ""):
        bad = client.post(rig["base"] + "/rename", headers=rig["auth"],
                          json={"path": f"{ROOT}/stove.py", "name": name})
        assert bad.status_code == 400 and bad.json()["reason"] == "invalid"


def test_several_paths_at_once_are_not_all_or_nothing(client, rig):
    client.post(rig["base"] + "/mkdir", headers=rig["auth"], json={"path": f"{ROOT}/out"})
    moved = client.post(rig["base"] + "/move", headers=rig["auth"], json={
        "paths": [f"{ROOT}/oven.py", f"{ROOT}/nothing.txt"], "to": f"{ROOT}/out"})
    assert moved.status_code == 200
    body = moved.json()
    assert (body["op"], body["to"], body["atomic"]) == ("move", f"{ROOT}/out", False)
    assert (body["done"], body["failed"]) == (1, 1)
    assert body["results"][0] == {"path": f"{ROOT}/oven.py", "ok": True,
                                  "to": f"{ROOT}/out/oven.py"}
    assert body["results"][1]["ok"] is False and body["results"][1]["reason"] == "missing"
    assert "does not exist" in body["results"][1]["error"]
    copied = client.post(rig["base"] + "/copy", headers=rig["auth"], json={
        "paths": [f"{ROOT}/loaf.png"], "to": f"{ROOT}/out"}).json()
    assert copied["done"] == 1 and f"{ROOT}/out/loaf.png" in rig["computer"].files
    # Deleting: to the trash; without one, each asks first.
    rig["computer"].trash = False
    asked = client.post(rig["base"] + "/delete", headers=rig["auth"], json={
        "paths": [f"{ROOT}/loaf.png"]}).json()
    assert asked["failed"] == 1 and asked["results"][0]["reason"] == "no_trash"
    gone = client.post(rig["base"] + "/delete", headers=rig["auth"], json={
        "paths": [f"{ROOT}/loaf.png"], "permanent": True}).json()
    assert gone["results"] == [{"path": f"{ROOT}/loaf.png", "ok": True, "trash": False}]
    assert client.post(rig["base"] + "/delete", headers=rig["auth"],
                       json={"paths": []}).status_code == 400


# --- Files that run code ---------------------------------------------------------------------
def test_files_that_run_code_are_read_only_unless_the_device_says_otherwise(client, rig):
    computer = rig["computer"]
    computer.dirs |= {f"{ROOT}/.git", f"{ROOT}/.agent"}
    computer.files[f"{ROOT}/.git/config"] = b"[core]\n"
    computer.files[f"{ROOT}/.agent/config.yaml"] = b"mcp: {}\n"
    computer.files[f"{ROOT}/.agent/plan.md"] = b"# Plan\n"
    (device,) = client.get("/api/v2/devices", headers=rig["auth"]).json()["devices"]
    assert device["files_that_run_code"] is False
    here = client.get(rig["base"], headers=rig["auth"], params={"path": ROOT, "hidden": 1}).json()
    marks = {e["name"]: (e["runs_code"], e["writable"]) for e in here["entries"]}
    assert marks[".git"] == (True, False) and marks[".agent"] == (True, False)
    assert marks["oven.py"] == (False, True)
    # Reading is always allowed; the answer says it is not to be changed.
    config = client.get(rig["base"] + "/content", headers=rig["auth"],
                        params={"path": f"{ROOT}/.git/config"}).json()
    assert config["text"] == "[core]\n" and config["writable"] is False
    agent = client.get(rig["base"] + "/content", headers=rig["auth"],
                       params={"path": f"{ROOT}/.agent/config.yaml"}).json()
    assert agent["writable"] is False
    # A project's other documents are documents.
    plan = client.get(rig["base"] + "/content", headers=rig["auth"],
                      params={"path": f"{ROOT}/.agent/plan.md"}).json()
    assert plan["writable"] is True
    # The device says so itself: then they are files like any other.
    rig["link"].hello["limits"]["files_that_run_code"] = True
    (device,) = client.get("/api/v2/devices", headers=rig["auth"]).json()["devices"]
    assert device["files_that_run_code"] is True
    config = client.get(rig["base"] + "/content", headers=rig["auth"],
                        params={"path": f"{ROOT}/.git/config"}).json()
    assert config["writable"] is True


def test_the_devices_sentence_for_a_file_that_runs_code_is_passed_on_unchanged(client, rig):
    sentence = ("~/.bashrc runs when a shell starts. Changing it from here is off on this "
                "computer: lembas remote files-that-run-code on")

    def refuse(params):
        raise _refuse("runs_code", sentence)

    rig["computer"]._write = refuse
    answer = client.put(rig["base"] + "/content", headers=rig["auth"],
                        json={"path": "/home/dev/.bashrc", "text": "x", "force": True})
    assert answer.status_code == 403
    assert answer.json() == {"detail": sentence, "reason": "runs_code"}


def test_a_change_waits_its_turn_on_the_device(db, rig):
    """A device makes one change at a time: the second is not late at once."""
    import asyncio

    seen = {}

    async def request(method, params=None, *, timeout=60):
        seen[method] = timeout
        return {"path": "/x", "size": 0, "mtime": 0, "hash": "", "deleted": True}

    rig["link"].request = request

    async def run():
        user, token = rig["user"], rig["link"].token_id
        await device_files.write(db, user, token, f"{ROOT}/a", b"x", overwrite=True)
        await device_files.mkdir(db, user, token, f"{ROOT}/d")
        await device_files.transfer(db, user, token, "move", f"{ROOT}/a", f"{ROOT}/b")
        await device_files.delete(db, user, token, f"{ROOT}/b")
        await device_files.list_dir(db, user, token, ROOT)

    asyncio.run(run())
    for method in ("write", "mkdir", "move", "delete"):
        assert seen[f"_lembas/fs/{method}"] >= 300, method
    assert seen["_lembas/fs/list"] <= 30


def test_this_client_announces_no_buffers_of_its_own(db, registered):
    """ACP `fs` is an editor's on the same machine; the link's client holds
    none of the device's files."""
    import asyncio

    seen = {}

    async def run():
        link, _ = _linked(db)

        async def request(method, params=None, *, timeout=60):
            seen[method] = params
            return {}

        link.request = request
        await device_link.handshake(link)

    asyncio.run(run())
    said = seen["initialize"]["clientCapabilities"].get("fs") or {}
    assert not said.get("readTextFile") and not said.get("writeTextFile")


def test_a_download_asks_whose_device_it_is_for_every_part_on_a_short_session(db, rig):
    """The request's own session is not kept for a download that takes
    minutes; and a device that stops being this person's ends it."""
    import asyncio

    from lembas.db.models import ApiToken

    computer = rig["computer"]
    computer.files[f"{ROOT}/dump.bin"] = b"a" * 5000
    computer.chunk = 2000
    user_id, token_id = rig["user"].id, rig["link"].token_id

    async def run():
        first = await device_files.read_first(db, rig["user"], token_id, f"{ROOT}/dump.bin")
        db.close()  # as the route does before it streams
        got = []
        with pytest.raises(device_files.Refused) as caught:
            async for piece in device_files.read_parts(
                None, user_id, token_id, f"{ROOT}/dump.bin", first
            ):
                got.append(piece)
                if len(got) == 2:
                    token = db.get(ApiToken, token_id)
                    token.revoked_at = token.created_at
                    db.commit()
                    db.close()
        return got, caught.value

    got, refused = asyncio.run(run())
    assert got == [b"a" * 2000, b"a" * 2000] and refused.reason == "not_found"
