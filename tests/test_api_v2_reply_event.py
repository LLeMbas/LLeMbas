"""`reply` on `GET /api/v2/events`: an open chat learns of a reply it did not start.

The web page polls its chat (`/api/chats/{chat}/tail`); an app does not, and
its only stream of a chat is a reply's own, which it opens for a reply it
asked for. So a reply that begins by itself -- a schedule firing into the
chat, a finished job's note, a subagent's wake, a turn a linked device started
-- reached an open, idle chat in an app only when the chat was opened again.

The frame is sent from the one place replies start, `generation.ensure`, so
these tests start replies through it and never through a route.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from lembas.api.v2 import schedules as v2
from lembas.db.models import Chat, Message, User
from lembas.services import chat as chat_service
from lembas.services import generation as generation_service
from tests.conftest import join

pytestmark = pytest.mark.anyio


@pytest.fixture
def held(monkeypatch):
    """Replies that start and are then held open until the test lets them
    end: what is asserted is the frame, not a model's answer."""
    release = asyncio.Event()

    async def run(generation):
        await release.wait()
        generation.done = True

    monkeypatch.setattr(generation_service, "_run", run)
    yield release
    release.set()
    generation_service._RUNNING.clear()
    generation_service._TASKS.clear()


def _user_id(db, email="frodo@shire.test") -> str:
    return db.scalar(select(User.id).where(User.email == email))


def _reply_row(db, chat_id: str) -> str:
    chat = db.get(Chat, chat_id)
    chat_service.create_message(db, chat, "user", "A job you started has finished.", machine=True)
    reply = chat_service.create_message(db, chat, "assistant", "", complete_=False)
    db.commit()
    return reply.id


async def _frames(stream, count: int, *, name="reply", tries=40) -> list[dict]:
    """The next `count` frames of that name, as their payloads."""
    found: list[dict] = []
    for _ in range(tries):
        frame = await asyncio.wait_for(anext(stream), 10)
        if frame.startswith(f"event: {name}\n"):
            found.append(json.loads(frame.split("data: ", 1)[1]))
            if len(found) == count:
                return found
    raise AssertionError(f"{len(found)} of {count} `{name}` frames arrived")


async def test_a_reply_started_by_anything_is_said_to_the_chats_owner(
    client, db, registered, make_chat, held
):
    chat_id = make_chat()
    stream = v2._events(_user_id(db))
    # Nothing is running: the stream opens with the lists, and no `reply`.
    first = await anext(stream)
    assert first.startswith("event: schedules\n")

    reply_id = _reply_row(db, chat_id)
    generation_service.ensure(chat_id, reply_id)

    assert await _frames(stream, 1) == [
        {"chat_id": chat_id, "message_id": reply_id, "live": True}
    ]
    # Said once: asking for the same reply again starts nothing and says nothing.
    generation_service.ensure(chat_id, reply_id)
    other = make_chat()
    second = _reply_row(db, other)
    generation_service.ensure(other, second)
    assert await _frames(stream, 1) == [
        {"chat_id": other, "message_id": second, "live": True}
    ]
    await stream.aclose()


async def test_a_reply_being_written_when_the_stream_opens_is_said_at_once(
    client, db, registered, make_chat, held
):
    """A client back from the background needs no bookkeeping: what is running
    now is the first thing it is told, before the lists."""
    chat_id = make_chat()
    reply_id = _reply_row(db, chat_id)
    generation_service.ensure(chat_id, reply_id)

    stream = v2._events(_user_id(db))
    first = await anext(stream)
    assert first.startswith("event: reply\n")
    assert json.loads(first.split("data: ", 1)[1]) == {
        "chat_id": chat_id, "message_id": reply_id, "live": True,
    }
    # And not a second time when the loop reads what started.
    assert (await anext(stream)).startswith("event: schedules\n")
    assert (await anext(stream)).startswith("event: reports\n")
    await stream.aclose()


async def test_a_reply_that_ended_before_it_could_be_said_is_not_live(
    client, db, registered, make_chat, held
):
    chat_id = make_chat()
    stream = v2._events(_user_id(db))
    await anext(stream)
    reply_id = _reply_row(db, chat_id)
    generation_service.ensure(chat_id, reply_id)
    held.set()
    await asyncio.wait_for(generation_service._TASKS[reply_id], 5)

    assert await _frames(stream, 1) == [
        {"chat_id": chat_id, "message_id": reply_id, "live": False}
    ]
    await stream.aclose()


async def test_only_the_owner_hears_and_never_of_a_subagents_chat(
    client, db, registered, make_chat, held
):
    mine = make_chat()
    # A subagent's chat: hidden, under its manager's.
    child = make_chat()
    row = db.get(Chat, child)
    row.parent_chat_id = mine
    db.commit()
    owner = _user_id(db)
    join(client, {"name": "Sam", "email": "sam@shire.test", "password": "po-ta-toes-boil-em"})
    theirs = make_chat()
    db.get(Chat, theirs).user_id = _user_id(db, "sam@shire.test")
    db.commit()

    stream = v2._events(owner)
    await anext(stream)
    for chat_id in (theirs, child, mine):
        generation_service.ensure(chat_id, _reply_row(db, chat_id))

    frames = await _frames(stream, 1)
    assert [f["chat_id"] for f in frames] == [mine]
    # Nothing else was waiting behind it: the other two are never said.
    try:
        later = await asyncio.wait_for(anext(stream), 3)
    except TimeoutError:
        later = ""
    assert not later.startswith("event: reply\n")
    await stream.aclose()


async def test_without_schedules_the_stream_still_says_replies(
    client, db, registered, make_chat, held
):
    """`schedule.use` gates the lists, not the news that a chat has a reply."""
    chat_id = make_chat()
    stream = v2._events(_user_id(db), scheduled=False)
    reply_id = _reply_row(db, chat_id)
    generation_service.ensure(chat_id, reply_id)
    frame = await asyncio.wait_for(anext(stream), 10)
    assert frame.startswith("event: reply\n")
    assert json.loads(frame.split("data: ", 1)[1])["message_id"] == reply_id
    await stream.aclose()


def test_the_news_is_bounded():
    """In memory, and news rather than a record: the oldest go first."""
    before = generation_service.started()
    for n in range(generation_service.STARTS_KEPT + 5):
        generation_service._STARTS.append((before + n + 1, "c", f"m{n}"))
    assert len(generation_service._STARTS) == generation_service.STARTS_KEPT
    generation_service._STARTS.clear()


def test_a_turn_the_device_started_is_said_too(db, registered, make_chat):
    """End to end from the link: a harness turn's reply is started by nobody
    here, and the owner's stream says it."""
    from lembas.services import device_link, settings_store
    from tests.test_device_harness_turn import JOB_NOTE, WITH_TURNS, Device, _device_chat

    device_link.reset()
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    device = Device(db, WITH_TURNS)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        stream = v2._events(_user_id(db))
        await anext(stream)
        await device.emit(*device.harness_turn(JOB_NOTE))
        frames = await _frames(stream, 1)
        await device.emit(*device.answer_events())
        await stream.aclose()
        for _ in range(200):
            if generation_service.running_for(chat_id) is None:
                break
            await asyncio.sleep(0.02)
        return frames

    frames = asyncio.run(scenario())
    device_link.reset()
    assert frames[0]["chat_id"] == chat_id and frames[0]["live"] is True
    db.expire_all()
    reply = db.scalar(
        select(Message).where(Message.chat_id == chat_id, Message.role == "assistant")
    )
    assert frames[0]["message_id"] == reply.id
    assert reply.content == "The tests pass. Nothing else to do."
