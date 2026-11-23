"""A turn the device's harness started gets its reply written.

The device wakes its lead with a turn of its own -- a finished job's note, a
team report -- and says so with a `prompt` event marked `machine`. The chat
showing the session mirrors it: the note as a row, and the reply the device's
model writes to it. Nothing about it is this instance's to send: the device
already runs the turn.

The prompt row alone was tested once, and the reply was not: it was treated as
a turn typed here, so the note went *back* to the device (as a message into the
turn, and when that was refused, as a prompt of its own) while the device's
real answer sat in the link's buffer and was never written. The chat kept a
note followed by an empty, finished reply.

So every test here ends on the reply's text, and on what was sent to the
device for the turn: nothing. The fake device records every request and every
notification.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from lembas.db.models import TOKEN_DEVICE, ApiToken, Chat, Message, User
from lembas.security import api_tokens
from lembas.services import device_link, settings_store
from lembas.services import generation as generation_service

BASE = ("directories", "configure", "steer", "sessions", "status", "delete", "compact",
        "title", "jobs")
WITH_TURNS = (*BASE, "turns")

JOB_NOTE = (
    "[A background job you started has finished:]\n"
    "job3 (make test) exited with 0 after 5s."
)
TEAM_REPORT = (
    "[Team report from scout to lead:]\n"
    "The loader is in src/load.py; nothing else reads it."
)
TURN = "7d0a51f2-0000-4000-8000-00000000000a"
TURN_2 = "7d0a51f2-0000-4000-8000-00000000000b"


@pytest.fixture(autouse=True)
def _clean(db):
    device_link.reset()
    device_link.DELETED.clear()
    api_tokens.reset_rates()
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    yield
    device_link.reset()
    device_link.DELETED.clear()


class Device:
    """A linked device, answered here. `asked` is every request this instance
    made of it, `sent` every notification: together, all that went over the
    link."""

    def __init__(self, db, capabilities, answer=None):
        user = db.scalar(select(User))
        token = db.scalar(select(ApiToken).where(ApiToken.kind == TOKEN_DEVICE))
        if token is None:
            token, _ = api_tokens.create(
                db, user, name="LLeMbas CLI on workstation", scopes=["models", "link"],
                kind=TOKEN_DEVICE, device_host="workstation",
            )
        self.token_id = token.id
        self.asked: list[tuple[str, dict]] = []
        self.sent: list[dict] = []
        self.answer = answer

        async def send_text(text):
            self.sent.append(json.loads(text))

        async def nothing(*_a, **_k):
            return None

        link = device_link.Link(
            token_id=token.id, user_id=user.id, name="LLeMbas CLI on workstation",
            send_text=send_text, close=nothing,
            hello={"device": {"host": "workstation"},
                   "limits": {"roots": ["/home/dev"], "max_mode": "auto"}},
        )
        link.initialized = {"_meta": {"lembas": {"capabilities": list(capabilities)}}}

        async def request(method, params=None, *, timeout=60):
            self.asked.append((method, params or {}))
            if self.answer is None:
                return {}
            result = self.answer(method, params or {})
            if asyncio.iscoroutine(result):
                result = await result
            return result

        link.request = request  # type: ignore[method-assign]
        device_link.LINKS[token.id] = link
        self.link = link
        self.turns = "turns" in capabilities

    async def emit(self, *events, session="s1"):
        for event in events:
            await self.link._notification(
                "_lembas/event", {"sessionId": session, "event": event}
            )

    def of(self, turn: str, *events: dict) -> list[dict]:
        """Events as this device sends them: named by their turn, or not."""
        return [{**e, "turnId": turn} if self.turns else e for e in events]

    def harness_turn(self, note: str, turn: str = TURN) -> list[dict]:
        """A harness turn up to its prompt: the task starts, the note is given."""
        return self.of(
            turn,
            {"type": "task", "state": "start"},
            {"type": "prompt", "text": note, "machine": True},
        )

    def answer_events(self, turn: str = TURN, text=("The tests pass. ", "Nothing else to do.")):
        """What the device's model does with the note: text, a tool, text."""
        return self.of(
            turn,
            {"type": "step", "n": 1},
            {"type": "text", "text": text[0]},
            {"type": "tool_start", "id": "t1", "name": "read", "args": {"path": "report.txt"}},
            {"type": "tool_end", "id": "t1", "name": "read", "result": {"output": "ok"}},
            {"type": "text", "text": text[1]},
            {"type": "done", "reason": "stop"},
            {"type": "task", "state": "end"},
        )

    def turn_traffic(self) -> list:
        """What went to the device that a turn could be made of."""
        quiet = {"_lembas/session/status", "_lembas/session/history"}
        return [m for m, _ in self.asked if m not in quiet] + [s.get("method") for s in self.sent]


def _device_chat(db, make_chat, device, session_id="s1") -> str:
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat.kind = "agent"
    chat.device_token_id = device.token_id
    chat.device_session_id = session_id
    chat.project_dir = "/home/dev/app"
    chat.agent_mode = "edit"
    db.commit()
    return chat_id


def _say(db, chat_id, role, text, **kw) -> str:
    from lembas.services import chat as chat_service

    message = chat_service.create_message(db, db.get(Chat, chat_id), role, text, **kw)
    db.commit()
    return message.id


def _rows(db, chat_id) -> list[Message]:
    db.expire_all()
    return list(
        db.scalars(select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at))
    )


async def _settled(db, chat_id, count: int, device=None) -> list[Message]:
    """The chat's rows once there are `count` and every reply is finished --
    or as they stand the moment anything is sent to the device, which is the
    failure these tests are about and must not be waited out."""
    rows: list[Message] = []
    for _ in range(400):
        rows = _rows(db, chat_id)
        finished = len(rows) >= count and all(r.complete for r in rows)
        if finished and generation_service.running_for(chat_id) is None:
            return rows
        if device is not None and device.turn_traffic():
            return rows
        await asyncio.sleep(0.02)
    return rows


async def _following(device, session="s1") -> None:
    """Until a reply here is following the session's turn, as it is within
    milliseconds of a real turn's first event -- or something was sent."""
    for _ in range(200):
        if device.link.writing.get(session) is not None or device.turn_traffic():
            return
        await asyncio.sleep(0.01)


CASES = pytest.mark.parametrize("capabilities", [WITH_TURNS, BASE], ids=["turns", "no-turns"])
NOTES = pytest.mark.parametrize("note", [JOB_NOTE, TEAM_REPORT], ids=["job-note", "team-report"])


# --- The reply is the device's, and nothing is sent ------------------------------------
@CASES
@NOTES
def test_the_reply_to_a_harness_turn_is_written_and_nothing_is_sent(
    db, registered, make_chat, capabilities, note
):
    """No page has the chat open: nothing follows the reply but its own task."""
    device = Device(db, capabilities)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.emit(*device.harness_turn(note))
        await device.emit(*device.answer_events())
        return await _settled(db, chat_id, 2, device)

    rows = asyncio.run(scenario())
    assert device.turn_traffic() == []
    assert device.asked == []
    assert [(r.role, r.machine, r.origin) for r in rows] == [
        ("user", True, device_link.ORIGIN_HARNESS),
        ("assistant", False, ""),
    ]
    assert rows[0].content == note
    reply = rows[1]
    assert reply.complete and not reply.error and not reply.stopped
    assert reply.content == "The tests pass. Nothing else to do."
    assert [t["name"] for t in reply.tool_calls_json] == ["read"]
    # Nobody was reading: the chat says it has something new.
    assert db.get(Chat, chat_id).unread is True


@CASES
def test_a_harness_turn_right_after_a_web_turn_gets_its_own_reply(
    db, registered, make_chat, capabilities
):
    """The web turn's reply is still being written here when the harness
    starts the next one: it waits for that reply and is not lost, and only the
    web turn's prompt ever goes to the device."""
    device = Device(db, capabilities)

    async def answer(method, params):
        if method == "session/prompt":
            web = ((params.get("_meta") or {}).get("lembas") or {}).get("turnId", "")
            await device.emit(*device.of(
                web,
                {"type": "task", "state": "start"},
                {"type": "prompt", "text": "run the tests"},
                {"type": "text", "text": "Started them in the background."},
                {"type": "task", "state": "end"},
            ))
            await device.emit(*device.harness_turn(JOB_NOTE))
            await asyncio.sleep(0.3)  # the note's rows are made while this reply finishes
            await device.emit(*device.answer_events())
            return {"stopReason": "end_turn"}
        return {}

    device.answer = answer
    chat_id = _device_chat(db, make_chat, device)
    _say(db, chat_id, "user", "run the tests")
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)

    async def scenario():
        generation_service.ensure(chat_id, reply_id)
        await asyncio.wait_for(generation_service._TASKS[reply_id], 5)
        return await _settled(db, chat_id, 4)

    rows = asyncio.run(scenario())
    assert [(r.role, r.machine, r.content) for r in rows] == [
        ("user", False, "run the tests"),
        ("assistant", False, "Started them in the background."),
        ("user", True, JOB_NOTE),
        ("assistant", False, "The tests pass. Nothing else to do."),
    ]
    assert [m for m, _ in device.asked if m in ("session/prompt", "_lembas/steer")] == [
        "session/prompt"
    ]


@CASES
def test_two_notes_in_a_row_are_two_turns_with_a_reply_each(
    db, registered, make_chat, capabilities
):
    """The second note's turn starts the moment the first one ends, before the
    first reply is saved here."""
    device = Device(db, capabilities)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.emit(*device.harness_turn(JOB_NOTE))
        await _following(device)
        await device.emit(*device.answer_events(text=("First: ", "the tests pass.")))
        await device.emit(*device.harness_turn(TEAM_REPORT, TURN_2))
        await device.emit(*device.answer_events(TURN_2, text=("Second: ", "the loader is found.")))
        return await _settled(db, chat_id, 4, device)

    rows = asyncio.run(scenario())
    assert device.turn_traffic() == []
    assert [(r.role, r.content) for r in rows] == [
        ("user", JOB_NOTE),
        ("assistant", "First: the tests pass."),
        ("user", TEAM_REPORT),
        ("assistant", "Second: the loader is found."),
    ]
    assert all(r.complete and not r.error for r in rows)


# --- Across a restart -------------------------------------------------------------------
def _cut_off(db, chat_id, note=JOB_NOTE, written="The tests ") -> str:
    """A harness turn's rows as a restart leaves them: the note, and its reply
    part written."""
    _say(db, chat_id, "user", note, origin=device_link.ORIGIN_HARNESS, machine=True)
    reply_id = _say(db, chat_id, "assistant", written, complete_=False)
    return reply_id


@CASES
def test_a_restart_mid_turn_follows_the_device_again_and_sends_nothing(
    db, registered, make_chat, capabilities
):
    """The instance went down while the device answered its own note. Back up,
    the reply follows the turn again; the note is never sent to the device as
    a prompt of this instance's."""
    device = Device(db, capabilities)
    device.answer = lambda method, params: (
        {"exists": True, "held": "here", "busy": True}
        if method == "_lembas/session/status" else {}
    )
    chat_id = _device_chat(db, make_chat, device)
    reply_id = _cut_off(db, chat_id)

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        g = generation_service.get(reply_id)
        for _ in range(200):
            if device.link.writing.get("s1") is g or device.turn_traffic():
                break
            await asyncio.sleep(0.01)
        await device.emit(*device.of(
            "",
            {"type": "text", "text": "pass."},
            {"type": "task", "state": "end"},
        ))
        return await _settled(db, chat_id, 2, device)

    rows = asyncio.run(scenario())
    assert device.turn_traffic() == []
    assert [m for m, _ in device.asked] == ["_lembas/session/status"]
    reply = rows[1]
    assert reply.complete and not reply.interrupted and not reply.error
    assert reply.content == "The tests pass."
    assert reply.origin == ""


@CASES
def test_a_turn_that_ended_during_the_restart_is_read_from_the_device(
    db, registered, make_chat, capabilities
):
    """Not left empty: the device finished the turn while this instance was
    down, and its answer is in the device's history."""
    device = Device(db, capabilities)

    def answer(method, params):
        if method == "_lembas/session/status":
            return {"exists": True, "held": "here", "busy": False}
        if method == "_lembas/session/history":
            return {"turns": [
                {"role": "user", "text": "run the tests"},
                {"role": "assistant", "text": "Started them in the background."},
                {"role": "assistant", "text": "The tests pass. Nothing else to do."},
            ]}
        return {}

    device.answer = answer
    chat_id = _device_chat(db, make_chat, device)
    _say(db, chat_id, "user", "run the tests")
    _say(db, chat_id, "assistant", "Started them in the background.")
    reply_id = _cut_off(db, chat_id, written="")

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        return await _settled(db, chat_id, 4, device)

    rows = asyncio.run(scenario())
    assert device.turn_traffic() == []
    assert rows[-1].id == reply_id and rows[-1].complete
    assert rows[-1].content == "The tests pass. Nothing else to do."


# --- What a turn typed here still does ---------------------------------------------------
def test_a_note_this_instance_wrote_is_still_sent_to_the_device(db, registered, make_chat):
    """`machine` alone is not the mark: a schedule's firing or a finished
    generation's note is written *here* into an agent chat, and the device has
    never seen it. It goes as a prompt, as before."""
    device = Device(db, WITH_TURNS)

    async def answer(method, params):
        if method == "session/prompt":
            turn = params["_meta"]["lembas"]["turnId"]
            await device.emit(*device.of(
                turn,
                {"type": "task", "state": "start"},
                {"type": "text", "text": "Noted."},
                {"type": "task", "state": "end"},
            ))
            return {"stopReason": "end_turn"}
        return {}

    device.answer = answer
    chat_id = _device_chat(db, make_chat, device)
    _say(db, chat_id, "user", "A picture you ordered is ready.", machine=True)
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)

    async def scenario():
        generation_service.ensure(chat_id, reply_id)
        await asyncio.wait_for(generation_service._TASKS[reply_id], 5)

    asyncio.run(scenario())
    prompts = [p for m, p in device.asked if m == "session/prompt"]
    assert len(prompts) == 1
    assert prompts[0]["prompt"][0]["text"] == "A picture you ordered is ready."
    db.expire_all()
    assert db.get(Message, reply_id).content == "Noted."


# --- The other places that tell the device's own turn from one typed here ----------------
def test_a_harness_turn_cannot_be_rewound_from_here(client, db, registered, make_chat):
    """Its row's id is not a turn id the device knows -- the device started
    the turn -- so regenerating its reply is refused here, as for a turn typed
    in the terminal, and the device is not asked to rewind to an id it never
    had."""
    device = Device(db, (*WITH_TURNS, "rewind"))
    chat_id = _device_chat(db, make_chat, device)
    _say(db, chat_id, "user", JOB_NOTE, origin=device_link.ORIGIN_HARNESS, machine=True)
    reply_id = _say(db, chat_id, "assistant", "The tests pass.")

    response = client.post(f"/api/chats/{chat_id}/messages/{reply_id}/regenerate")
    assert response.status_code == 409
    assert "_lembas/session/rewind" not in [m for m, _ in device.asked]


def test_the_devices_own_turns_are_named_in_one_place():
    typed = Message(role="user", content="x", origin="")
    assert not device_link.devices_own(typed)
    assert not device_link.devices_own(None)
    assert not device_link.devices_own(Message(role="user", content="x", machine=True))
    for origin in (device_link.ORIGIN_TERMINAL, device_link.ORIGIN_HARNESS):
        assert device_link.devices_own(Message(role="user", content="x", origin=origin))


# --- The empty replies already stored ---------------------------------------------------
def test_an_empty_reply_to_a_harness_turn_says_it_was_not_recorded(
    client, db, registered, make_chat
):
    """Rows from before the fix: a note, then a finished reply with nothing in
    it. No text is invented for them; the bubble says so in one line instead
    of standing blank. An empty reply anywhere else is left as it was."""
    from lembas.db.models import Connection, Model

    # A model to draw the chat page with: without one it is the first-run screen.
    connection = Connection(name="c", base_url="http://127.0.0.1:1/v1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    device = Device(db, WITH_TURNS)
    chat_id = _device_chat(db, make_chat, device)
    _say(db, chat_id, "user", JOB_NOTE, machine=True)
    lost = _say(db, chat_id, "assistant", "")
    _say(db, chat_id, "user", "and now?")
    typed = _say(db, chat_id, "assistant", "")
    _say(db, chat_id, "user", TEAM_REPORT, origin=device_link.ORIGIN_HARNESS, machine=True)
    written = _say(db, chat_id, "assistant", "The loader is found.")
    # A note written *here* into the chat -- a schedule's firing -- is `machine`
    # too, and its reply was sent for: empty, it was the model's to leave so.
    _say(db, chat_id, "user", "Check the nightly build and say only what broke.", machine=True)
    silent = _say(db, chat_id, "assistant", "")
    # An old harness row as the device's `prompt` event words it.
    _say(db, chat_id, "user", "⏎ job4 (make lint) exited with 0 after 2s.", machine=True)
    lost_line = _say(db, chat_id, "assistant", "")

    page = client.get(f"/chat/{chat_id}").text

    def bubble(message_id: str) -> str:
        start = page.index(f'id="msg-{message_id}"')
        return page[start : page.index("</article>", start)]

    assert "data-reply-unrecorded" in bubble(lost)
    assert "The reply to this was not recorded." in bubble(lost)
    assert "data-reply-unrecorded" not in bubble(typed)
    assert "data-reply-unrecorded" not in bubble(written)
    assert "data-reply-unrecorded" not in bubble(silent)
    assert "data-reply-unrecorded" in bubble(lost_line)

    # An ordinary chat's empty reply is not a device's lost answer.
    plain = make_chat()
    _say(db, plain, "user", "A picture you ordered is ready.", machine=True)
    _say(db, plain, "assistant", "")
    assert "data-reply-unrecorded" not in client.get(f"/chat/{plain}").text
