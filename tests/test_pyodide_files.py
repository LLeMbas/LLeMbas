"""Pyodide fetched by an administrator: pinned, checked file by file, served from here.

No test reaches the network: every download is a downloader handed in, over a
lock made for its bytes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re

import pytest

from lembas.services import pyodide_files, settings_store

CONTENT = {
    "pyodide.js": b"var loadPyodide = 1;\n",
    "pyodide.asm.mjs": b"export default 1;\n",
    "pyodide.asm.wasm": b"\x00asm\x01\x00\x00\x00",
    "python_stdlib.zip": b"PK\x05\x06" + b"\x00" * 18,
    "pyodide-lock.json": b"{}\n",
}


def _lock(content: dict[str, bytes]) -> dict:
    return {
        "version": "9.9.9",
        "url": "https://pyodide.test/v9.9.9/full/",
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
            for name, data in content.items()
        },
    }


def _downloader(content: dict[str, bytes], asked: list[str] | None = None):
    async def fetch(url: str):
        if asked is not None:
            asked.append(url)
        data = content[url.rsplit("/", 1)[-1]]
        for i in range(0, len(data), 7):
            yield data[i : i + 7]

    return fetch


@pytest.fixture
def fake_lock(monkeypatch):
    monkeypatch.setattr(pyodide_files, "lock", lambda: _lock(CONTENT))
    pyodide_files.forget()
    yield
    pyodide_files.forget()


def test_the_committed_lock_pins_the_runners_files_at_one_address():
    pyodide_files.lock.cache_clear()
    pinned = pyodide_files.lock()
    assert set(pinned["files"]) == set(CONTENT)
    assert pinned["url"].startswith("https://") and pinned["url"].endswith("/")
    assert f"v{pinned['version']}/" in pinned["url"]
    for entry in pinned["files"].values():
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) and entry["size"] > 0


def test_a_download_is_verified_and_lands_in_the_data_directory(fake_lock):
    asked: list[str] = []
    assert not pyodide_files.available()
    where = asyncio.run(pyodide_files.download(_downloader(CONTENT, asked)))
    assert where == pyodide_files.directory()
    assert "vendor/pyodide/9.9.9" in where.as_posix()
    assert asked == [f"https://pyodide.test/v9.9.9/full/{name}" for name in CONTENT]
    assert pyodide_files.available()
    assert (where / "pyodide.asm.wasm").read_bytes() == CONTENT["pyodide.asm.wasm"]


@pytest.mark.parametrize(
    "broken",
    [
        {**CONTENT, "python_stdlib.zip": b"PK\x05\x06" + b"\x01" * 18},  # same size, other bytes
        {**CONTENT, "pyodide.js": CONTENT["pyodide.js"] + b"//"},  # larger
        {**CONTENT, "pyodide-lock.json": b"{"},  # shorter
    ],
)
def test_any_mismatch_refuses_the_whole_download_and_keeps_nothing(fake_lock, broken):
    progress = pyodide_files.Progress()
    with pytest.raises(pyodide_files.PyodideError):
        asyncio.run(pyodide_files.download(_downloader(broken), progress=progress))
    assert progress.state == "failed" and progress.error
    assert not pyodide_files.available()
    assert not pyodide_files.directory().exists()
    assert not any(pyodide_files.root().iterdir())


def test_a_lock_bumped_since_reads_as_absent(fake_lock, monkeypatch):
    asyncio.run(pyodide_files.download(_downloader(CONTENT)))
    other = {**CONTENT, "pyodide.js": b"var loadPyodide = 2;\n"}
    monkeypatch.setattr(pyodide_files, "lock", lambda: {**_lock(other), "version": "9.9.9"})
    assert not pyodide_files.available()


def test_served_from_here_only_the_pinned_files(client, fake_lock):
    assert client.get("/vendor/pyodide/9.9.9/pyodide.js").status_code == 404
    asyncio.run(pyodide_files.download(_downloader(CONTENT)))
    js = client.get("/vendor/pyodide/9.9.9/pyodide.asm.mjs")
    assert js.status_code == 200 and js.content == CONTENT["pyodide.asm.mjs"]
    assert js.headers["content-type"].startswith("text/javascript")
    wasm = client.get("/vendor/pyodide/9.9.9/pyodide.asm.wasm")
    assert wasm.headers["content-type"] == "application/wasm"
    assert client.get("/vendor/pyodide/9.9.9/VERIFIED").status_code == 404
    assert client.get("/vendor/pyodide/1.0.0/pyodide.js").status_code == 404
    assert client.get("/vendor/pyodide/9.9.9/..%2F..%2Flembas.db").status_code == 404


def test_the_switch_waits_for_the_files(client, db, registered, fake_lock):
    page = client.get("/admin/general").text
    card = page[page.index('data-testid="python-card"'):]
    card = card[: card.index("</section>")]
    assert 'action="/admin/general/python/download"' in card
    assert re.search(r'name="pyodide_enabled"[^>]*disabled', card)
    client.post("/admin/general/python", data={"pyodide_enabled": "true"})
    db.expire_all()
    assert not settings_store.get_group(db, settings_store.GENERAL).get("pyodide_enabled")

    asyncio.run(pyodide_files.download(_downloader(CONTENT)))
    page = client.get("/admin/general").text
    assert 'action="/admin/general/python/remove"' in page
    client.post("/admin/general/python", data={"pyodide_enabled": "true"})
    db.expire_all()
    assert settings_store.get_group(db, settings_store.GENERAL).get("pyodide_enabled")


def test_the_download_button_starts_one_download(client, registered, fake_lock, monkeypatch):
    started: list[bool] = []
    monkeypatch.setattr(pyodide_files, "start", lambda downloader=None: started.append(True))
    assert client.post("/admin/general/python/download").status_code in (200, 303)
    assert started == [True]


def test_while_it_runs_the_card_polls_itself_and_stops_when_done(client, registered, fake_lock):
    pyodide_files.PROGRESS.state = "running"
    pyodide_files.PROGRESS.done_bytes = 10
    card = client.get("/admin/general/python/progress").text
    assert 'hx-trigger="every 1s"' in card and "<progress" in card
    pyodide_files.PROGRESS.state = "failed"
    pyodide_files.PROGRESS.error = "pyodide.js does not match its pinned SHA-256."
    card = client.get("/admin/general/python/progress").text
    assert "hx-trigger" not in card and 'data-testid="pyodide-error"' in card


def test_remove_deletes_the_files_and_switches_python_off(client, db, registered, fake_lock):
    asyncio.run(pyodide_files.download(_downloader(CONTENT)))
    settings_store.update(db, {"pyodide_enabled": True})
    db.commit()
    client.post("/admin/general/python/remove")
    db.expire_all()
    assert not pyodide_files.available() and not pyodide_files.root().exists()
    assert not settings_store.get_group(db, settings_store.GENERAL).get("pyodide_enabled")


def test_the_runner_loads_pyodide_from_this_server(client, db, registered, make_chat,
                                                    fake_lock):
    from lembas.db.models import Chat

    asyncio.run(pyodide_files.download(_downloader(CONTENT)))
    settings_store.update(db, {"pyodide_enabled": True})
    db.commit()
    chat = db.get(Chat, make_chat())
    page = client.get(f"/chat/{chat.id}").text
    assert 'data-pyodide-index="/vendor/pyodide/9.9.9/"' in page
    assert "cdn" not in page.split("data-python-runner", 1)[1].split("</div>", 1)[0]


def test_the_lock_is_valid_json_committed_with_the_package():
    data = json.loads(pyodide_files.LOCKFILE.read_text(encoding="utf-8"))
    assert data["version"] and data["files"]
