"""A person's shells: listed, ended, and not left behind.

Three terminals a person, each alive for half an hour after its panel closed,
and no way to end one: the limit was reached by looking at a few folders on
the new-chat screen, and then nothing could be opened anywhere. See
`services/agent/terminal.py` and `api/terminal.py`.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from lembas.db.models import User
from lembas.security import api_tokens
from lembas.services import device_link
from lembas.services.agent import terminal as terminal_service
from tests.conftest import script
from tests.test_device_link import _device, _wait_linked
from tests.test_device_protocol2 import ALL, _hello
from tests.test_terminal_before_chat import _app_bearer, _open_on_device, _ready


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    yield
    asyncio.run(terminal_service.shutdown())
    terminal_service._SESSIONS.clear()
    device_link.reset()


class _Shell(terminal_service.Session):
    """A shell with nothing behind it: the registry's rules are what is tested."""

    async def start(self) -> None:
        return None


def _put(key: str, owner: str = "u1", directory: str = "/srv/app") -> _Shell:
    shell = _Shell(key, owner_id=owner, label="workstation", project_dir=directory)
    terminal_service._SESSIONS[key] = shell
    return shell


# --- 1. Listing and ending -----------------------------------------------------------
async def test_a_persons_shells_are_listed_and_one_can_be_ended():
    mine = _put("chat1")
    draft = _put(terminal_service.draft_key("u1", "tok", "/srv/other"), directory="/srv/other")
    _put("chat9", owner="u2")

    listed = [terminal_service.describe(s) for s in terminal_service.shells_of("u1")]
    assert [s["directory"] for s in listed] == ["/srv/app", "/srv/other"]
    assert listed[0]["chat"] == "chat1" and listed[1]["chat"] == ""
    assert all(s["device"] == "workstation" and s["seconds"] >= 0 for s in listed)
    # The id is the list's own: a draft's registry key is made of ids.
    assert all("u1" not in s["id"] and "tok" not in s["id"] for s in listed)

    assert not await terminal_service.end_shell("u2", mine.sid), "somebody else's"
    assert await terminal_service.end_shell("u1", draft.sid)
    assert draft.closed and draft.closed_reason == terminal_service.CLOSED_ENDED
    assert not await terminal_service.end_shell("u1", draft.sid), "already gone"
    assert [s.sid for s in terminal_service.shells_of("u1")] == [mine.sid]


@pytest.mark.slow
def test_at_the_limit_the_refusal_lists_the_shells_and_ending_one_makes_room(
    client, db, registered
):
    from lembas.services import settings_store

    settings_store.update(db, {"terminal_max_per_user": 1}, key=settings_store.AGENTS)
    token_id, raw = _device(db)
    headers = _app_bearer(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, [*ALL])
        _wait_linked(token_id)
        first = f"/api/v2/devices/{token_id}/terminal/ws?directory=/srv/one"
        with client.websocket_connect(first, headers=headers) as term:
            _open_on_device(ws)
            shell_id = _ready(term)["id"]

            # A chat's shell would be the second: refused, with the list.
            owner = db.scalar(select(User))
            other = terminal_service.draft_key(owner.id, "another-device", "/srv/two")
            with pytest.raises(terminal_service.TooManyShells):
                asyncio.run(terminal_service.open_session(
                    other, owner_id=owner.id, label="x", max_per_user=1,
                    device_token_id="another-device",
                ))
            listed = client.get("/api/v2/terminals", headers=headers).json()["shells"]
            assert [s["id"] for s in listed] == [shell_id]
            assert listed[0]["directory"] == "/srv/one" and listed[0]["watched"] is True
            # The page's door lists the same.
            assert client.get("/api/terminals").json()["shells"][0]["id"] == shell_id

            assert client.post(f"/api/v2/terminals/{shell_id}/end", headers=headers
                               ).status_code == 204
            closed = json.loads(term.receive_text())
            assert closed["t"] == "closed" and closed["reason"] == "ended"
        assert client.get("/api/v2/terminals", headers=headers).json() == {"shells": []}
        assert client.post(f"/api/v2/terminals/{shell_id}/end", headers=headers
                           ).status_code == 404
        assert client.post("/api/terminals/nope/end").status_code == 404
    assert client.get("/api/v2/terminals").status_code == 401


async def test_the_sockets_refusal_carries_the_list(monkeypatch):
    """What the panel draws its list from: `shells` beside `message`."""
    from lembas.api import terminal as terminal_api

    _put("chat1")
    sent: list[dict] = []

    class Socket:
        async def send_text(self, text):
            sent.append(json.loads(text))

        async def close(self):
            return None

    opening = {"owner_id": "u1", "label": "workstation", "project_dir": "/srv/two",
               "idle_timeout": 60.0, "max_sessions": 20, "max_per_user": 1,
               "device_token_id": "tok"}
    await terminal_api.serve(Socket(), "chat2", "", opening, "u1@example.test", cols=80, rows=24)
    assert sent[0]["t"] == "error" and "End one first" in sent[0]["message"]
    assert [s["directory"] for s in sent[0]["shells"]] == ["/srv/app"]


# --- 2. A shell opened before its chat ----------------------------------------------
async def test_a_draft_ends_when_its_last_viewer_goes(monkeypatch):
    monkeypatch.setattr(terminal_service, "DRAFT_LINGER", 0.05)
    key = terminal_service.draft_key("u1", "tok", "/srv/app")
    shell = _put(key)
    viewer = shell.attach()
    shell.detach(viewer)
    await terminal_service.unwatched(key, shell)
    assert not shell.closed, "a moment for the panel to come back, or a chat to take it"
    await asyncio.sleep(0.15)
    assert shell.closed and terminal_service.count_for("u1") == 0


async def test_a_draft_somebody_came_back_to_or_a_chat_took_over_carries_on(monkeypatch):
    monkeypatch.setattr(terminal_service, "DRAFT_LINGER", 0.05)
    key = terminal_service.draft_key("u1", "tok", "/srv/app")
    back = _put(key)
    back.detach(back.attach())
    await terminal_service.unwatched(key, back)
    again = back.attach()  # the panel reconnected
    await asyncio.sleep(0.15)
    assert not back.closed
    back.detach(again)

    taken = _put(terminal_service.draft_key("u1", "tok", "/srv/two"), directory="/srv/two")
    taken.detach(taken.attach())
    await terminal_service.unwatched(taken.chat_id, taken)
    assert terminal_service.adopt(terminal_service.draft_key("u1", "tok", "/srv/two"), "chat7")
    await asyncio.sleep(0.15)
    assert not taken.closed and terminal_service.get("chat7") is taken
    # A chat's own shell is never ended for being unwatched.
    await terminal_service.unwatched("chat7", taken)
    await asyncio.sleep(0.15)
    assert not taken.closed


async def test_changing_folder_does_not_leave_the_old_shell_behind():
    """The panel opens the new folder's socket before the old one has finished
    closing: the old shell still had its viewer, so it was passed over -- and
    then left with nobody, holding one of the person's terminals for half an
    hour."""
    old_key = terminal_service.draft_key("u1", "tok", "/srv/old")
    old = _put(old_key, directory="/srv/old")
    viewer = old.attach()  # the first socket: still open
    new_key = terminal_service.draft_key("u1", "tok", "/srv/new")

    # The second socket arrives.
    assert await terminal_service.close_other_drafts("u1", new_key) == 0
    assert not old.closed and old.end_when_unwatched
    _put(new_key, directory="/srv/new")

    # The first socket finishes closing: its shell goes at once.
    old.detach(viewer)
    await terminal_service.unwatched(old_key, old)
    assert old.closed
    assert [s.project_dir for s in terminal_service.shells_of("u1")] == ["/srv/new"]


# --- 6. The same directory, written two ways -----------------------------------------
def test_a_directory_is_one_key_however_it_was_written():
    same = {
        terminal_service.draft_key("u1", "tok", path)
        for path in ("/srv/work/app", "/srv/work/app/", " /srv/work/app ", "/srv//work/app//")
    }
    assert len(same) == 1
    assert terminal_service.normal_dir("/") == "/"
    assert terminal_service.normal_dir("") == ""
    assert terminal_service.normal_dir("~/projects/") == "~/projects"
    # Backslashes are the device's own business.
    assert terminal_service.normal_dir("C:\\Users\\dev\\") == "C:\\Users\\dev\\"
    assert terminal_service.draft_key("u1", "tok", "/a") != terminal_service.draft_key(
        "u1", "tok", "/b"
    )


async def test_the_chat_takes_the_draft_over_when_its_directory_is_written_differently():
    draft = _put(terminal_service.draft_key("u1", "tok", "/srv/work/app/"))
    # As `start_new_chat` asks, with the chat's stored directory.
    assert terminal_service.adopt(
        terminal_service.draft_key("u1", "tok", "/srv/work/app"), "chat1"
    )
    assert terminal_service.get("chat1") is draft


# --- 3. The button says why -----------------------------------------------------------
def test_a_device_that_opens_no_terminal_says_so_and_how_to_switch_it_on(
    client, db, registered
):
    from lembas.api import terminal as terminal_api
    from lembas.db.models import Chat, Model
    from tests.test_sidebar_settings_composer import _device as plain_device
    from tests.test_sidebar_settings_composer import _enable_agents, _model

    _model(db)
    _enable_agents(db)
    token = plain_device(db, "workstation", linked=True)  # lists no capabilities
    owner = db.scalar(select(User))

    code, why = terminal_api.availability(db, owner, token.id)
    assert code == "device"
    assert "does not open terminals" in why and "remote.terminal" in why
    assert "LLeMbas Desktop" in why

    headers = _app_bearer(db)
    device = client.get("/api/v2/devices", headers=headers).json()["devices"][0]
    assert device["terminal"] is False and device["terminal_reason"] == "device"
    assert device["terminal_hint"] == why

    model = db.scalar(select(Model))
    chat = Chat(user_id=owner.id, kind="agent", title="x", model_id=model.model_id,
                connection_id=model.connection_id, device_token_id=token.id,
                project_dir="/srv/x")
    db.add(chat)
    db.commit()
    page = client.get(f"/chat/{chat.id}").text
    button = page[page.index("data-terminal-button") - 200:][:900]
    assert 'aria-disabled="true"' in button and "does not open terminals" in button
    assert 'id="terminal"' in page, "the panel is there for when the device opens one"

    # It links with terminals on: the device poll says so, and the page follows.
    device_link.LINKS[token.id].initialized = {
        "_meta": {"lembas": {"capabilities": ["terminal"]}}}
    assert terminal_api.availability(db, owner, token.id) == ("", "")
    poll = client.get(f"/api/chats/{chat.id}/device-model").text
    assert 'id="terminal-state"' in poll and 'hx-swap-oob="true"' in poll
    assert 'data-off=""' in poll
    on = client.get("/api/v2/devices", headers=headers).json()["devices"][0]
    assert on["terminal"] is True and on["terminal_reason"] == "" and on["terminal_hint"] == ""

    # And when it is not linked at all.
    device_link.LINKS.clear()
    code, why = terminal_api.availability(db, owner, token.id)
    assert code == "unlinked" and "not linked right now" in why
    assert "not linked right now" in client.get(f"/api/chats/{chat.id}/device-model").text


def test_the_page_follows_the_state_and_a_disabled_button_says_why():
    js = script("terminal.js")
    assert 'event.target.id === "terminal-state"' in js
    off = js[js.index("function terminalOff"):][:600]
    assert 'setAttribute("aria-disabled", "true")' in off
    assert 'removeAttribute("aria-disabled")' in off
    # The press is answered before the handler that would open the panel.
    press = js[js.index("/* Capture phase: before the delegated handler"):][:700]
    assert "event.stopPropagation();" in press and "window.lembas.notify(why)" in press
    assert press.rstrip().endswith("}, true);") or "}, true);" in press


# --- 4 and 5. Coming back, the clipboard, a narrow panel -----------------------------
def test_the_panel_reconnects_by_itself_and_says_when_the_shell_is_new():
    js = script("terminal.js")
    assert "Close and reopen to reconnect" not in js
    soon = js[js.index("function reconnectSoon"):][:700]
    assert "Math.min(30, Math.pow(2, retries))" in soon and "retries >= MAX_RETRIES" in soon
    control = js[js.index('if (payload.t === "closed" || payload.t === "error")'):][:1600]
    # Only a lost connection is retried: a shell that exited or was ended is over.
    assert 'payload.reason === "error" || payload.reason === "shutdown"' in control
    assert "if (lost) return reconnectSoon(message);" in control
    assert "This is a new shell" in js and "!payload.resumed" in js
    assert 'closest("[data-terminal-reconnect]")' in js
    assert 'closest("[data-terminal-end]")' in js and "/end" in js


def test_the_clipboard_has_a_fallback_and_every_promise_a_catch():
    js = script("terminal.js")
    copy = js[js.index("function copySelection"):][:500]
    assert "window.isSecureContext" in copy and ".catch(fallback)" in copy
    assert "window.lembas.copyText(text)" in copy
    paste = js[js.index("function pasteClipboard"):][:900]
    assert "return false;" in paste and ".catch(function ()" in paste
    # Without the API the key is left to the browser's own paste.
    assert "return !pasteClipboard();" in js
    assert "navigator.clipboard.writeText(selection)" not in js


def test_a_panel_narrower_than_the_fewest_columns_keeps_them_and_scrolls():
    js = script("terminal.js")
    assert f"var MIN_COLS = {terminal_service.MIN_COLS};" in js
    assert "if (term.cols < MIN_COLS) term.resize(MIN_COLS, term.rows);" in js
    from pathlib import Path

    import lembas

    css = (Path(lembas.__file__).parent / "web/static/css/app.css").read_text()
    assert ".terminal__screen { overflow-x: auto; }" in css


# --- Coming back to a draft ------------------------------------------------------------
async def test_a_draft_somebody_went_back_to_is_no_longer_marked(monkeypatch):
    """The new-chat screen moved to another folder (the first shell is marked
    to end when its viewer goes) and then back: the mark must go, or the shell
    the person is in again ends the moment its panel closes."""
    monkeypatch.setattr(terminal_service, "DRAFT_LINGER", 0.3)
    key = terminal_service.draft_key("u1", "tok", "/srv/old")
    shell = _put(key, directory="/srv/old")
    first = shell.attach()
    await terminal_service.close_other_drafts("u1", terminal_service.draft_key("u1", "tok", "/n"))
    assert shell.end_when_unwatched

    again = shell.attach()  # back in the first folder
    assert not shell.end_when_unwatched
    shell.detach(first)
    shell.detach(again)
    await terminal_service.unwatched(key, shell)
    assert not shell.closed, "ended at once, as if it were still marked"
    await asyncio.sleep(0.1)
    assert not shell.closed  # inside the ordinary wait


async def test_the_wait_starts_again_each_time_the_last_viewer_goes(monkeypatch):
    """Left at 0, back at 5 s, left again at 18 s: the first leaving's timer
    used to end the shell at 20 s -- two seconds after the second leaving."""
    monkeypatch.setattr(terminal_service, "DRAFT_LINGER", 0.4)  # "20 s"
    key = terminal_service.draft_key("u1", "tok", "/srv/app")
    shell = _put(key)
    shell.detach(shell.attach())
    await terminal_service.unwatched(key, shell)  # left at 0
    first_wait = shell.linger
    await asyncio.sleep(0.1)
    viewer = shell.attach()  # back at "5 s"
    assert shell.linger is None
    await asyncio.sleep(0.01)
    assert first_wait.cancelled()
    await asyncio.sleep(0.25)
    shell.detach(viewer)  # left at "18 s"
    await terminal_service.unwatched(key, shell)
    await asyncio.sleep(0.1)  # "20 s"
    assert not shell.closed
    await asyncio.sleep(0.45)  # its own full wait after the second leaving
    assert shell.closed
