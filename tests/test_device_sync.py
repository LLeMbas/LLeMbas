"""One session, for real: the device says whether its session is
working, and a message typed here into a working session joins that turn rather
than coming back "already working"; a message taken in mid-reply is shown at the
point the model took it in, live and after a reload; deleting, renaming and
compacting reach the session on the device; a reply cut off here is followed
again instead of sent twice; and the audit log has pages.

The device is a `Link` with its requests answered here, as in
test_device_extras.py; nothing runs on this machine.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from lembas.db.models import TOKEN_DEVICE, AuditEvent, Chat, Message, User
from lembas.security import api_tokens
from lembas.services import device_link, settings_store, steps
from lembas.services import generation as generation_service

CAPS = ("directories", "configure", "steer", "sessions", "status", "delete", "compact", "title")


@pytest.fixture(autouse=True)
def _agents_on(db):
    """Device chats are behind Admin -> Agents, asked on every turn."""
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    device_link.DELETED.clear()
    api_tokens.reset_rates()
    yield
    device_link.reset()
    device_link.DELETED.clear()


def _linked(db, *, answer=None):
    user = db.scalar(select(User))
    token, _ = api_tokens.create(
        db,
        user,
        name="LLeMbas CLI on device-1",
        scopes=["models", "link"],
        kind=TOKEN_DEVICE,
        device_host="device-1",
    )

    async def send_text(text):
        sent.append(json.loads(text))

    async def nothing(*_a, **_k):
        return None

    sent: list[dict] = []
    link = device_link.Link(
        token_id=token.id,
        user_id=user.id,
        name="LLeMbas CLI on device-1",
        send_text=send_text,
        close=nothing,
        hello={"device": {"host": "device-1"},
               "limits": {"roots": ["/home/dev"], "max_mode": "auto"}},
    )
    link.initialized = {"_meta": {"lembas": {"capabilities": list(CAPS)}}}
    asked: list[tuple[str, dict]] = []

    async def request(method, params=None, *, timeout=60):
        asked.append((method, params))
        if answer is not None:
            result = answer(method, params)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        return {}

    link.request = request
    device_link.LINKS[token.id] = link
    return link, asked


def _device_chat(db, make_chat, link, session_id="s1") -> str:
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat.kind = "agent"
    chat.device_token_id = link.token_id
    chat.device_session_id = session_id
    chat.project_dir = "/home/dev/app"
    db.commit()
    return chat_id


def _say(db, chat_id, role, text, **kw) -> str:
    from lembas.services import chat as chat_service

    message = chat_service.create_message(db, db.get(Chat, chat_id), role, text, **kw)
    db.commit()
    return message.id


# --- The device is the authority on busy ------------------------------------------------
def test_task_events_say_whether_the_session_works(db, registered):
    link, _ = _linked(db)

    async def scenario():
        await link._notification(
            "_lembas/event", {"sessionId": "s1", "event": {"type": "task", "state": "start"}}
        )
        assert "s1" in link.busy
        await link._notification(
            "_lembas/event", {"sessionId": "s1", "event": {"type": "task", "state": "end"}}
        )
        assert "s1" not in link.busy

    asyncio.run(scenario())


def test_a_message_to_a_working_session_joins_its_turn(db, registered, make_chat):
    """A turn the terminal started (and nobody here followed): what is typed here
    goes in as a steer, the reply follows the turn, and nothing is sent as a
    second prompt -- which is what came back "already working on a prompt"."""
    link, asked = _linked(db)
    chat_id = _device_chat(db, make_chat, link)
    asked_id = _say(db, chat_id, "user", "also run the tests")
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)
    link.busy.add("s1")
    g = generation_service.Generation(chat_id=chat_id, message_id=reply_id)

    async def scenario():
        run = asyncio.create_task(
            device_link.run(
                g,
                chat_id=chat_id,
                token_id=link.token_id,
                project_dir="/home/dev/app",
                mode="auto",
                prompt="also run the tests",
                device_name="device-1",
                session_id="s1",
                message_id=asked_id,
            )
        )
        for _ in range(100):
            if link.writing.get("s1") is g:
                break
            await asyncio.sleep(0.01)
        events = [
            {"type": "text", "text": "Running them. "},
            {"type": "steered", "texts": ["also run the tests"], "ids": [asked_id]},
            {"type": "text", "text": "All green."},
            {"type": "task", "state": "end"},
        ]
        for e in events:
            await link._notification("_lembas/event", {"sessionId": "s1", "event": e})
        await asyncio.wait_for(run, 5)

    asyncio.run(scenario())
    methods = [m for m, _ in asked]
    assert "session/prompt" not in methods
    assert (
        "_lembas/steer",
        {"sessionId": "s1", "text": "also run the tests", "messageId": asked_id},
    ) in asked
    assert g.text == "Running them. All green."
    assert not g.error
    # The message is in the reply, at the point it went in.
    assert steps.steered_ids(g.steps) == {asked_id}
    kinds = [
        s.kind for s in steps._build(text=g.text, thinking="", events=[], marks=g.steps, live=False)
    ]
    assert kinds == ["text", "steer", "text"]


def test_a_prompt_event_after_the_turn_ended_is_not_this_reply(db, registered):
    """The terminal starts a turn of its own right after one the web sent: its
    events are not appended to the reply that already ended."""
    link, _ = _linked(db)
    g = generation_service.Generation(chat_id="c", message_id="m")
    link.writing["s1"] = g

    async def scenario():
        for e in [
            {"type": "text", "text": "done"},
            {"type": "task", "state": "end"},
            {"type": "task", "state": "start"},
            {"type": "text", "text": "not mine"},
        ]:
            await link._notification("_lembas/event", {"sessionId": "s1", "event": e})

    asyncio.run(scenario())
    assert g.text == "done"


# --- Where a message taken in mid-reply shows ---------------------------------------------
def test_a_native_reply_marks_what_it_took_in(db, registered, make_chat):
    chat_id = make_chat()
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)
    waiting = _say(db, chat_id, "user", "and the README", queued=True)
    g = generation_service.Generation(chat_id=chat_id, message_id=reply_id)
    g.content.append("Looked at the code.")
    entry = generation_service._inject(g, chat_id, False)
    assert entry is not None
    assert g.steps[-1]["steer"] == [{"id": waiting, "text": "and the README"}]
    assert g.steps[-1]["text_to"] == len("Looked at the code.")


def _model(db):
    from lembas.db.models import Connection, Model

    connection = Connection(name="c", base_url="http://127.0.0.1:1/v1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m"))
    db.commit()


def test_after_a_reload_the_message_is_in_the_reply_not_above_it(client, db, registered, make_chat):
    _model(db)
    chat_id = make_chat()
    _say(db, chat_id, "user", "fix the bug")
    waiting = _say(db, chat_id, "user", "and the README")
    reply = db.get(Message, _say(db, chat_id, "assistant", "Fixed. Updated the README too."))
    reply.steps_json = [
        {
            "round": 0,
            "thinking_to": 0,
            "text_to": 6,
            "tools_to": 0,
            "thinking_ms": 0,
            "steer": [{"id": waiting, "text": "and the README"}],
        },
    ]
    db.commit()
    page = client.get(f"/chat/{chat_id}").text
    # No bubble of its own...
    assert f'id="msg-{waiting}"' not in page
    # ...but inside the reply, between what came before and after it.
    inside = page.index('class="steer"')
    assert page.index("Fixed.") < inside < page.index("Updated the README")


def test_the_terminal_typing_mid_task_shows_too(db, registered):
    g = generation_service.Generation(chat_id="c", message_id="m")
    device_link.apply_event(g, {"type": "steered", "texts": ["typed there"]})
    assert g.steps[-1]["steer"] == [{"id": "", "text": "typed there"}]


# --- Delete, rename, compact ---------------------------------------------------------------
def test_deleting_a_device_chat_deletes_the_session_there(client, db, registered, make_chat):
    link, asked = _linked(db)
    chat_id = _device_chat(db, make_chat, link)
    assert client.delete(f"/api/chats/{chat_id}").status_code == 204
    for _ in range(50):
        if asked:
            break
        asyncio.run(asyncio.sleep(0.01))
    assert ("_lembas/session/delete", {"sessionId": "s1"}) in asked
    # Announced again by a device that has not caught up: it does not come back.
    asyncio.run(
        link._notification("_lembas/session/announce", {"sessionId": "s1", "cwd": "/home/dev/app"})
    )
    assert "s1" not in link.announced


def test_a_session_deleted_on_the_device_takes_its_chat(db, registered, make_chat):
    link, _ = _linked(db)
    chat_id = _device_chat(db, make_chat, link)
    asyncio.run(link._notification("_lembas/session/deleted", {"sessionId": "s1"}))
    db.expire_all()
    assert db.get(Chat, chat_id) is None


def test_renaming_reaches_the_device(client, db, registered, make_chat):
    link, asked = _linked(db)
    chat_id = _device_chat(db, make_chat, link)
    assert client.patch(f"/api/chats/{chat_id}", data={"title": "Better name"}).status_code in (
        200,
        204,
    )
    for _ in range(50):
        if asked:
            break
        asyncio.run(asyncio.sleep(0.01))
    assert ("_lembas/session/title", {"sessionId": "s1", "title": "Better name"}) in asked


def test_compact_on_a_device_chat_asks_the_device(client, db, registered, make_chat):
    link, asked = _linked(db, answer=lambda m, p: {"summary": "We fixed the bug."})
    chat_id = _device_chat(db, make_chat, link)
    _say(db, chat_id, "user", "fix the bug")
    _say(db, chat_id, "assistant", "Fixed.")
    response = client.post(f"/api/chats/{chat_id}/compact")
    assert response.status_code == 200, response.text
    assert asked == [("_lembas/session/compact", {"sessionId": "s1"})]
    db.expire_all()
    assert "We fixed the bug." in (db.get(Chat, chat_id).compact_summary or "")


def test_compact_says_why_when_the_model_cannot_be_reached(
    client, db, registered, make_chat, mock_http
):
    from lembas.db.models import Connection, Model

    connection = Connection(name="c", base_url="http://x.test/v1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m"))
    db.commit()
    chat_id = make_chat()
    _say(db, chat_id, "user", "one")
    _say(db, chat_id, "assistant", "two")
    response = client.post(f"/api/chats/{chat_id}/compact")
    assert response.status_code in (409, 502)
    assert "compact" in response.json()["detail"].lower()


# --- Taken up again, never sent twice ------------------------------------------------------
def test_a_reply_cut_off_by_a_restart_follows_the_device_again(db, registered, make_chat):
    def answer(method, params):
        if method == "_lembas/session/status":
            return {"exists": True, "held": "here", "busy": True}
        return {}

    link, asked = _linked(db, answer=answer)
    chat_id = _device_chat(db, make_chat, link)
    _say(db, chat_id, "user", "build it")
    reply_id = _say(db, chat_id, "assistant", "Building... ")
    reply = db.get(Message, reply_id)
    reply.interrupted = True
    reply.stopped = True
    db.commit()

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        g = generation_service.get(reply_id)
        for _ in range(100):
            if link.writing.get("s1") is g:
                break
            await asyncio.sleep(0.01)
        for e in [{"type": "text", "text": "done."}, {"type": "task", "state": "end"}]:
            await link._notification("_lembas/event", {"sessionId": "s1", "event": e})
        await asyncio.wait_for(generation_service._TASKS[reply_id], 5)

    asyncio.run(scenario())
    assert [m for m, _ in asked] == ["_lembas/session/status"]
    db.expire_all()
    row = db.get(Message, reply_id)
    assert row.complete and not row.interrupted and not row.stopped
    assert row.content == "Building... done."
    assert row.origin == ""


def test_a_turn_that_finished_meanwhile_is_read_from_the_history(db, registered, make_chat):
    def answer(method, params):
        if method == "_lembas/session/status":
            return {"exists": True, "held": "here", "busy": False}
        if method == "_lembas/session/history":
            return {
                "turns": [
                    {"role": "user", "text": "build it"},
                    {"role": "assistant", "text": "Built, all 12 tests pass."},
                ]
            }
        return {}

    link, asked = _linked(db, answer=answer)
    chat_id = _device_chat(db, make_chat, link)
    g = generation_service.Generation(chat_id=chat_id, message_id="m")
    asyncio.run(device_link._reattach(link, g, chat_id, "s1", "device-1"))
    assert g.text == "Built, all 12 tests pass."
    assert "session/prompt" not in [m for m, _ in asked]


def test_an_ordinary_reply_cut_off_carries_on_as_a_turn_of_its_own(
    db, registered, make_chat, monkeypatch
):
    chat_id = make_chat()
    _say(db, chat_id, "user", "write a poem")
    reply_id = _say(db, chat_id, "assistant", "Roses are")
    db.get(Message, reply_id).interrupted = True
    db.commit()
    started = []
    monkeypatch.setattr(generation_service, "ensure", lambda c, m, **k: started.append((c, m)))
    assert asyncio.run(generation_service.resume_interrupted(delay=0)) == 1
    db.expire_all()
    rows = db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    ).all()
    assert [r.origin for r in rows][-2:] == ["resume", ""]
    assert started == [(chat_id, rows[-1].id)]
    assert db.get(Message, reply_id).interrupted is False


# --- The audit log has pages -------------------------------------------------------------
def test_the_audit_log_has_numbered_pages(client, db, registered):
    now = datetime.now(UTC)
    for n in range(130):
        db.add(
            AuditEvent(
                at=now - timedelta(minutes=n),
                action="test.thing",
                actor_kind="system",
                actor_label="tester",
                summary=f"event {n}",
            )
        )
    db.commit()
    page = client.get("/admin/audit?action=test&size=50&page=2").text
    assert "130 events · page 2 of 3" in page
    assert "event 50<" in page and "event 49<" not in page
    assert 'aria-current="page">2<' in page
    last = client.get("/admin/audit?action=test&size=50&page=99").text
    assert "page 3 of 3" in last
