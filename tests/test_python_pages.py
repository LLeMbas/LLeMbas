"""The `python` tool and the page it runs in: when it says there is none.

"No open browser page is attached" used to be the answer to four different
things -- no page, a page on its way, a page asleep, a run out of time -- and
to a chat open only in an app, which had no way to be a page at all. See
`services/pages.py`, `api/pyodide.py`, `api/v2/python.py` and `python.js`.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from lembas.db.models import Chat, User
from lembas.security import api_tokens
from lembas.services import pages, pyodide_files, settings_store
from lembas.services import tools as tools_service
from lembas.services.tools import files as file_tools
from tests.conftest import script
from tests.test_pyodide_files import CONTENT, _downloader, fake_lock  # noqa: F401


def _context(chat_id: str, user_id: str):
    return tools_service.ToolContext(
        owner_id=user_id, chat_id=chat_id, display=True, message_id=""
    )


def _call(context, **args):
    return tools_service.run_tool(context, "python", json.dumps({"code": "print(1)", **args}))


# --- 1. A page on its way ----------------------------------------------------------
async def test_the_tool_waits_for_a_page_that_is_on_its_way(db, user_id, make_chat, monkeypatch):
    """The first message of a new chat starts its reply while the browser is
    still being redirected to the chat's page: the call used to be refused at
    once, for a page that announced itself a second later."""
    chat_id = make_chat()
    monkeypatch.setattr(file_tools, "PYTHON_ATTACH_WAIT", 5.0)
    task = asyncio.create_task(_call(_context(chat_id, user_id)))
    await asyncio.sleep(0.3)
    assert not task.done(), "refused before the page had a chance to arrive"

    pages.announce(chat_id, "late-page-1", {"python"})
    request = await pages.take(chat_id, "late-page-1", timeout=3)
    assert request["code"] == "print(1)"
    assert pages.answer(chat_id, "late-page-1", request["call_id"], {"stdout": "1\n"})
    outcome = await asyncio.wait_for(task, 3)
    assert outcome.content == "1"


async def test_with_no_page_at_all_it_says_so_after_the_wait(db, user_id, make_chat, monkeypatch):
    chat_id = make_chat()
    monkeypatch.setattr(file_tools, "PYTHON_ATTACH_WAIT", 0.3)
    started = asyncio.get_running_loop().time()
    outcome = await _call(_context(chat_id, user_id))
    assert asyncio.get_running_loop().time() - started >= 0.3
    assert outcome.content == file_tools.python_unavailable()


# --- 2. Signs of life ---------------------------------------------------------------
async def test_a_page_that_is_asking_for_calls_is_alive(monkeypatch):
    """Only the heartbeat counted, and a browser slows a background tab's
    timers: a page in the middle of a long poll was forgotten."""
    now = [1000.0]
    monkeypatch.setattr(pages.time, "monotonic", lambda: now[0])
    pages.announce("c1", "page-0001", {"python"})
    now[0] += pages.TTL - 1
    assert await pages.take("c1", "page-0001", timeout=0) is None  # asked: alive
    now[0] += pages.TTL - 1
    assert pages.attached("c1")
    now[0] += pages.TTL + 1
    assert not pages.attached("c1")


def test_the_ttl_allows_for_a_throttled_background_tab():
    # A page announces every 20 seconds; a throttled tab manages one a minute.
    assert pages.TTL >= 120
    assert "setInterval(announce, 20000)" in script("python.js")


async def test_an_unknown_page_is_told_to_announce_not_answered_at_once(
    client, db, registered, make_chat, fake_lock, monkeypatch  # noqa: F811
):
    with pytest.raises(pages.UnknownPage):
        await pages.take("c1", "nobody-01", timeout=5)

    await pyodide_files.download(_downloader(CONTENT))
    settings_store.update(db, {"pyodide_enabled": True}, key=settings_store.GENERAL)
    chat_id = make_chat()
    assert client.get(f"/chat/{chat_id}/page/take?page=nobody-0001").status_code == 410
    # And the page backs off: it announces, then asks again after a moment.
    js = script("python.js")
    gone = js[js.index("response.status === 410") :]
    assert gone.index("announce();") < gone.index("throw gone")
    assert "error.unknown ? 2000" in js


def test_the_page_attaches_again_when_it_is_shown_again():
    """`pagehide` withdrew and stopped for good; a page restored by Back was
    never attached again."""
    js = script("python.js")
    assert 'window.addEventListener("pageshow", start)' in js
    assert 'document.addEventListener("visibilitychange"' in js
    start = js[js.index("function start()") :][:200]
    assert "stopped = false" in start and "announce();" in start and "loop();" in start
    # One loop at a time, however often it is started.
    assert "if (stopped || looping) return;" in js


# --- 3. The next page ---------------------------------------------------------------
async def test_a_call_the_newest_page_does_not_take_goes_to_the_next():
    pages.announce("c1", "awake-001", {"python"})
    await asyncio.sleep(0.01)
    pages.announce("c1", "asleep-01", {"python"})  # the newest: a background tab
    running = asyncio.create_task(pages.run("c1", {"code": "x"}, timeout=5, pickup=0.2))
    await asyncio.sleep(0.05)

    request = await pages.take("c1", "awake-001", timeout=3)
    assert request["code"] == "x"
    assert pages.answer("c1", "awake-001", request["call_id"], {"stdout": "ok"})
    assert (await running)["stdout"] == "ok"
    # The page that slept wakes to nothing: the call is not run twice.
    assert await pages.take("c1", "asleep-01", timeout=0.05) is None
    assert not pages.answer("c1", "asleep-01", request["call_id"], {"stdout": "twice"})


# --- 4. Three honest answers ---------------------------------------------------------
async def test_a_page_that_never_takes_the_call_is_not_no_page(
    db, user_id, make_chat, monkeypatch
):
    chat_id = make_chat()
    monkeypatch.setattr(pages, "PICKUP", 0.1)
    pages.announce(chat_id, "asleep-01", {"python"})
    outcome = await _call(_context(chat_id, user_id))
    assert outcome.content == file_tools.python_text("no_answer")
    assert outcome.content != file_tools.python_unavailable()
    assert "did not answer" in outcome.content


async def test_a_run_past_its_time_says_it_ran_out_of_time(db, user_id, make_chat, monkeypatch):
    chat_id = make_chat()
    monkeypatch.setattr(file_tools, "PYTHON_SECONDS", -14.9)  # the wait is seconds + 15
    pages.announce(chat_id, "busy-0001", {"python"})
    task = asyncio.create_task(_call(_context(chat_id, user_id)))
    assert await pages.take(chat_id, "busy-0001", timeout=3)  # taken, never answered
    outcome = await asyncio.wait_for(task, 5)
    assert outcome.content.startswith("Python stopped: the run took longer than")


async def test_a_page_that_closes_mid_run_and_one_that_closes_before():
    pages.announce("c1", "page-0001", {"python"})
    running = asyncio.create_task(pages.run("c1", {"code": ""}, timeout=5))
    assert await pages.take("c1", "page-0001", timeout=3)
    pages.withdraw("c1", "page-0001")
    with pytest.raises(pages.NoAnswer):
        await running
    # Closed before it took anything, and it was the only one: no page.
    pages.announce("c1", "page-0002", {"python"})
    waiting = asyncio.create_task(pages.run("c1", {"code": ""}, timeout=5))
    await asyncio.sleep(0)
    pages.withdraw("c1", "page-0002")
    with pytest.raises(pages.NotAttached):
        await asyncio.wait_for(waiting, 2)


def test_the_three_sentences_are_the_specs_or_its_words():
    """The vendored spec carries them from 3.7.0; until it is pinned, the code
    holds the same words."""
    assert "{{" not in file_tools.python_text("timeout")
    assert str(file_tools.PYTHON_SECONDS) in file_tools.python_text("timeout")
    assert file_tools.python_text("no_answer") != file_tools.python_text("timeout")


# --- 6. /api/v2: an app's window is a page ----------------------------------------------
def _bearer(db) -> dict[str, str]:
    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="LLeMbas Desktop", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


async def test_an_app_can_be_the_page_python_runs_in(
    client, db, registered, make_chat, fake_lock  # noqa: F811
):
    api_tokens.reset_rates()
    headers = _bearer(db)
    chat_id = make_chat()
    base = f"/api/v2/chats/{chat_id}/page"
    # Off: nothing to be a page for, and `GET /me` says so.
    assert client.post(f"{base}/announce", headers=headers,
                       json={"page_id": "desktop-0001"}).status_code == 409
    assert client.get("/api/v2/me", headers=headers).json()["python"] == {
        "enabled": False, "version": "", "files": []}

    await pyodide_files.download(_downloader(CONTENT))
    settings_store.update(db, {"pyodide_enabled": True}, key=settings_store.GENERAL)
    state = client.get("/api/v2/me", headers=headers).json()["python"]
    assert state["enabled"] is True and state["version"] == "9.9.9"
    assert {f["name"] for f in state["files"]} == set(CONTENT)
    assert all(len(f["sha256"]) == 64 and f["size"] > 0 for f in state["files"])

    assert client.get(f"{base}/take?page=desktop-0001", headers=headers).status_code == 410
    assert client.post(f"{base}/announce", headers=headers,
                       json={"page_id": "desktop-0001"}).status_code == 204
    assert pages.attached(chat_id)

    user = db.scalar(select(User))
    task = asyncio.create_task(_call(_context(chat_id, user.id)))
    # The call is queued before the page asks. This test has two event loops
    # (its own, where the tool runs, and the test client's, where the long
    # poll waits), and a queue does not wake a getter on another loop: a poll
    # that got there first slept its whole wait and answered 204. One loop
    # serves both in the application.
    for _ in range(500):
        if pages._calls:
            break
        await asyncio.sleep(0.01)
    assert pages._calls, "the python call was never queued"
    taken = await asyncio.to_thread(
        client.get, f"{base}/take?page=desktop-0001", headers=headers
    )
    assert taken.status_code == 200 and taken.json()["code"] == "print(1)"
    call_id = taken.json()["call_id"]
    assert client.post(f"{base}/answer", headers=headers, json={
        "page_id": "someone-else", "call_id": call_id, "result": {"stdout": "no"}
    }).status_code == 404
    assert client.post(f"{base}/answer", headers=headers, json={
        "page_id": "desktop-0001", "call_id": call_id, "result": {"stdout": "1\n"}
    }).status_code == 204
    assert (await asyncio.wait_for(task, 5)).content == "1"

    assert client.post(f"{base}/withdraw", headers=headers,
                       json={"page_id": "desktop-0001"}).status_code == 204
    assert not pages.attached(chat_id)
    # The door: a token, the caller's own chat, a well-formed page id.
    assert client.post(f"{base}/announce", json={"page_id": "desktop-0001"}).status_code == 401
    assert client.post("/api/v2/chats/" + "0" * 32 + "/page/announce", headers=headers,
                       json={"page_id": "desktop-0001"}).status_code == 404
    assert client.post(f"{base}/announce", headers=headers,
                       json={"page_id": "x"}).status_code == 400


async def test_the_pyodide_files_through_the_apps_door(
    client, db, registered, fake_lock  # noqa: F811
):
    api_tokens.reset_rates()
    headers = _bearer(db)
    assert client.get("/api/v2/pyodide/9.9.9/pyodide.mjs", headers=headers).status_code == 404
    await pyodide_files.download(_downloader(CONTENT))
    got = client.get("/api/v2/pyodide/9.9.9/pyodide.asm.wasm", headers=headers)
    assert got.status_code == 200 and got.content == CONTENT["pyodide.asm.wasm"]
    assert got.headers["content-type"] == "application/wasm"
    for bad in ("9.9.9/VERIFIED", "1.0.0/pyodide.mjs", "9.9.9/..%2F..%2Flembas.db"):
        assert client.get(f"/api/v2/pyodide/{bad}", headers=headers).status_code == 404
    assert client.get("/api/v2/pyodide/9.9.9/pyodide.mjs").status_code == 401


def test_an_agent_chat_is_never_a_page_for_python(client, db, registered, make_chat,
                                                  fake_lock):  # noqa: F811
    asyncio.run(pyodide_files.download(_downloader(CONTENT)))
    settings_store.update(db, {"pyodide_enabled": True}, key=settings_store.GENERAL)
    api_tokens.reset_rates()
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat.kind = "agent"
    db.commit()
    assert client.post(f"/api/v2/chats/{chat_id}/page/announce", headers=_bearer(db),
                       json={"page_id": "desktop-0001"}).status_code == 409


# --- The wait is only for a page that may come ------------------------------------------
def _aged(db, chat_id: str, minutes: int = 30) -> None:
    from datetime import UTC, datetime, timedelta

    chat = db.get(Chat, chat_id)
    chat.created_at = datetime.now(UTC) - timedelta(minutes=minutes)
    db.commit()


async def test_a_chat_no_page_has_been_near_is_told_at_once(db, user_id, make_chat, monkeypatch):
    """A chat driven only from a client that cannot run Python (the phone)
    waited the whole grace at every python call, for a page that was never
    coming."""
    chat_id = make_chat()
    _aged(db, chat_id)
    monkeypatch.setattr(file_tools, "PYTHON_ATTACH_WAIT", 5.0)
    loop = asyncio.get_running_loop()
    started = loop.time()
    outcome = await _call(_context(chat_id, user_id))
    assert loop.time() - started < 1.0
    assert outcome.content == file_tools.python_unavailable()


async def test_a_chat_whose_page_was_just_here_is_waited_for(db, user_id, make_chat, monkeypatch):
    chat_id = make_chat()
    _aged(db, chat_id)
    monkeypatch.setattr(file_tools, "PYTHON_ATTACH_WAIT", 5.0)
    pages.announce(chat_id, "reloading-1", {"python"})
    pages.withdraw(chat_id, "reloading-1")  # the tab is reloading
    assert not pages.attached(chat_id) and pages.seen_recently(chat_id)

    task = asyncio.create_task(_call(_context(chat_id, user_id)))
    await asyncio.sleep(0.3)
    assert not task.done(), "refused while its page was on the way back"
    pages.announce(chat_id, "reloading-2", {"python"})
    request = await pages.take(chat_id, "reloading-2", timeout=3)
    assert pages.answer(chat_id, "reloading-2", request["call_id"], {"stdout": "1\n"})
    assert (await asyncio.wait_for(task, 3)).content == "1"

    # Long gone is not "just here".
    assert not pages.seen_recently(chat_id, within=0)


# --- The registry does not keep what is gone ---------------------------------------------
async def test_expired_pages_of_chats_nobody_asks_about_are_swept(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(pages.time, "monotonic", lambda: now[0])
    pages.announce("quiet-chat", "page-0001", {"python"})
    now[0] += pages.TTL + pages.SWEEP_EVERY + 1
    # Any access, to any chat, sweeps: the quiet chat is never asked about again.
    assert not pages.attached("another-chat")
    assert "quiet-chat" not in pages._pages
    now[0] += max(pages.RECENT, pages.TTL) + pages.SWEEP_EVERY + 1
    pages.attached("another-chat")
    assert "quiet-chat" not in pages._last_seen


async def test_a_call_that_moved_on_leaves_nothing_queued_for_the_page_it_left():
    pages.announce("c1", "awake-001", {"python"})
    await asyncio.sleep(0.01)
    pages.announce("c1", "asleep-01", {"python"})
    big = {"code": "x", "files": [{"path": "/a", "content_b64": "A" * 1000}]}
    running = asyncio.create_task(pages.run("c1", big, timeout=5, pickup=0.1))
    await asyncio.sleep(0.2)  # offered to the sleeping page, then moved on
    assert pages._pages["c1"]["asleep-01"].requests.qsize() == 0

    request = await pages.take("c1", "awake-001", timeout=3)
    assert pages.answer("c1", "awake-001", request["call_id"], {"stdout": "ok"})
    await running
    assert all(page.requests.qsize() == 0 for page in pages._pages["c1"].values())

    # And one nobody took at all.
    with pytest.raises(pages.NoAnswer):
        await pages.run("c1", big, timeout=5, pickup=0.05)
    assert all(page.requests.qsize() == 0 for page in pages._pages["c1"].values())
