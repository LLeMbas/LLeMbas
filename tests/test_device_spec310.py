"""Harness spec 3.10.0, this instance's side: a turn the device's harness
starts is a turn like any other over the link.

A device that lists `harness_turns` says busy and names the turn while a turn
its harness started runs, takes a message steered into it, makes a prompt wait
behind it, hands back on the turn's `task` end what was steered too late
(`unsent`), and marks the turn `machine` in its history. A device that does not
list it (a CLI before 3.10.0) runs the same turns and says none of that -- and
stays in use, so every behaviour here is tested against both.

What is asserted throughout: a message shown in the chat reaches the device's
model exactly once, and a row that is the harness's is never sent to the
device as a prompt.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from lembas.api import chats as chats_api
from lembas.api.v2 import stream as v2_stream
from lembas.db.models import Chat, Message, User
from lembas.security import api_tokens
from lembas.services import device_link, settings_store, steps
from lembas.services import generation as generation_service
from tests.fake_harness_device import NEW, OLD, HarnessDevice, invariant, line
from tests.test_device_harness_turn import (
    JOB_NOTE,
    TEAM_REPORT,
    _device_chat,
    _following,
    _rows,
    _say,
)

H2 = "5c1e0b7a-0000-4000-8000-0000000000h2"
H3 = "5c1e0b7a-0000-4000-8000-0000000000h3"


@pytest.fixture(autouse=True)
def _clean(db):
    device_link.reset()
    device_link.DELETED.clear()
    api_tokens.reset_rates()
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    yield
    device_link.reset()
    device_link.DELETED.clear()


def _type(db, chat_id: str, text: str) -> tuple[Message, Message | None]:
    """The person types into the chat: the composer's own door."""
    db.expire_all()
    return chats_api.write_turn(db, db.get(Chat, chat_id), db.scalar(select(User)), text)


async def _until(ok, tries: int = 400) -> bool:
    """Until `ok()`; False when it never was -- the caller asserts on it."""
    for _ in range(tries):
        if ok():
            return True
        await asyncio.sleep(0.01)
    return bool(ok())


async def _quiet(db, chat_id: str, count: int) -> list[Message]:
    """The chat's rows once there are `count`, all finished, nothing running."""
    def settled() -> bool:
        rows = _rows(db, chat_id)
        return (
            len(rows) >= count
            and all(r.complete for r in rows)
            and generation_service.running_for(chat_id) is None
        )

    await _until(settled, 600)
    return _rows(db, chat_id)


# --- 1. The capability ---------------------------------------------------------------
def test_the_capability_is_read_from_what_the_device_listed(db, registered):
    assert device_link.HARNESS_TURNS == "harness_turns"
    assert HarnessDevice(db, NEW).link.can(device_link.HARNESS_TURNS)
    device_link.reset()
    assert not HarnessDevice(db, OLD).link.can(device_link.HARNESS_TURNS)


# --- 2. A message typed while a harness turn runs ------------------------------------
def test_a_message_typed_during_a_harness_turn_joins_it_and_is_shown_where_it_went_in(
    db, registered, make_chat
):
    """With the capability the device takes a steer during its own turn: the
    message goes into that turn, the reply marks where, and no turn of its own
    is made for it."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        await device.say("The tests pass. ")
        typed, reply = _type(db, chat_id, "and the linter?")
        assert reply is None  # joined to the reply running, not a reply of its own
        assert await _until(lambda: device.inbox)
        await device.take_in()
        await device.say("The linter is clean too.")
        await device.end()
        return typed.id, await _quiet(db, chat_id, 3)

    typed_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == ["_lembas/steer"]
    assert device.model_saw == ["and the linter?"]
    assert [(r.role, r.origin) for r in rows] == [
        ("user", device_link.ORIGIN_HARNESS), ("user", ""), ("assistant", ""),
    ]
    typed, reply = rows[1], rows[2]
    assert typed.id == typed_id and not typed.queued
    assert reply.content == "The tests pass. The linter is clean too."
    assert reply.complete and not reply.error
    # Shown where it went in: the reply's step mark names the message.
    assert steps.steered_ids(reply.steps_json) == {typed_id}


def test_a_message_steered_too_late_comes_back_and_is_sent_as_the_next_turn(
    db, registered, make_chat
):
    """The harness turn ended before taking the message in. The device hands it
    back on the turn's `task` end (`unsent`); it is queued again and goes as
    the next turn -- once."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        typed, _ = _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.inbox)
        await device.say("The tests pass.")
        await device.end()
        return typed.id, await _quiet(db, chat_id, 4)

    typed_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == ["_lembas/steer", "session/prompt"]
    prompt = next(p for m, p in device.asked if m == "session/prompt")
    assert prompt["_meta"]["lembas"]["turnId"] == typed_id
    assert device.model_saw == ["and the linter?"]
    by_id = {r.id: r for r in rows}
    assert not by_id[typed_id].queued
    replies = [r for r in rows if r.role == "assistant"]
    assert sorted(r.content for r in replies) == ["Answer to: and the linter?", "The tests pass."]
    # The harness turn's reply does not claim to have taken it in.
    first = next(r for r in replies if r.content == "The tests pass.")
    assert steps.steered_ids(first.steps_json) == set()
    assert all(r.complete and not r.error for r in rows)


def test_two_messages_one_taken_in_and_one_too_late_each_reach_the_model_once(
    db, registered, make_chat
):
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(TEAM_REPORT, H2)
        await _following(device)
        first, _ = _type(db, chat_id, "first thought")
        assert await _until(lambda: len(device.inbox) == 1)
        await device.take_in()
        second, _ = _type(db, chat_id, "second thought")
        assert await _until(lambda: len(device.inbox) == 1)
        await device.say("Seen the report.")
        await device.end()
        return first.id, second.id, await _quiet(db, chat_id, 5)

    first_id, second_id, rows = asyncio.run(scenario())
    assert device.model_saw == ["first thought", "second thought"]
    assert device.sent_methods() == ["_lembas/steer", "_lembas/steer", "session/prompt"]
    reply = next(r for r in rows if r.content == "Seen the report.")
    assert steps.steered_ids(reply.steps_json) == {first_id}
    assert [r.content for r in rows if r.role == "assistant" and r.id != reply.id] == [
        "Answer to: second thought"
    ]


def test_without_the_capability_a_typed_message_waits_and_is_sent_after_the_turn(
    db, registered, make_chat
):
    """A device before 3.10.0 refuses a steer during a harness turn and fails a
    prompt sent then. So nothing is sent to the session until the turn's end:
    the message waits in the chat's queue and goes as the next turn."""
    device = HarnessDevice(db, OLD)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        typed, reply = _type(db, chat_id, "and the linter?")
        assert reply is None
        await device.say("The tests pass.")
        # Long enough for the follower's half-second look at the queue, twice.
        await asyncio.sleep(1.2)
        during = list(device.asked)
        waiting = next(r for r in _rows(db, chat_id) if r.id == typed.id).queued
        await device.end()
        return typed.id, during, waiting, await _quiet(db, chat_id, 4)

    typed_id, during, waiting, rows = asyncio.run(scenario())
    assert during == [], "nothing goes to the session while the harness's turn runs"
    assert waiting is True
    assert device.refused == []
    assert device.sent_methods() == ["session/prompt"]
    assert device.model_saw == ["and the linter?"]
    assert sorted(r.content for r in rows if r.role == "assistant") == [
        "Answer to: and the linter?", "The tests pass.",
    ]
    assert not {r.id: r for r in rows}[typed_id].queued


def test_the_prompt_event_of_a_harness_turn_is_the_transcript_line(db, registered, make_chat):
    """What the device sends as the turn's `prompt` text is the line its
    transcript shows, not the whole note: kept as it came."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        await device.say("Done.")
        await device.end()
        return await _quiet(db, chat_id, 2)

    rows = asyncio.run(scenario())
    assert rows[0].content == line(JOB_NOTE) == "⏎ job3 (make test) exited with 0 after 5s."
    assert rows[0].machine and rows[0].origin == device_link.ORIGIN_HARNESS


def test_a_message_typed_before_the_turns_reply_exists_joins_it_or_is_sent_once(
    db, registered, make_chat
):
    """Typed in the moment after the harness's `prompt` and before its reply
    here exists: the reply started for the message becomes the *turn's* reply
    (the turn keeps its row and its own words), and the message is handed to
    the turn like any typed while a reply runs. Steered too late there, it
    comes back (`unsent`) and is answered by a reply of its own -- once. The
    device answers the steer *after* it has said the turn's end: the order
    that could lose it."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    answer = device.answer

    async def ends_first(method, params):
        said = await answer(method, params)
        if method == "_lembas/steer":
            await device.end()
        return said

    device.answer = ends_first

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        typed, reply = _type(db, chat_id, "and the linter?")
        assert reply is not None
        await asyncio.wait_for(generation_service._TASKS[reply.id], 10)
        return typed.id, reply.id, await _quiet(db, chat_id, 4)

    typed_id, reply_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == ["_lembas/steer", "session/prompt"]
    assert device.model_saw == ["and the linter?"]
    prompt = next(p for m, p in device.asked if m == "session/prompt")
    assert prompt["_meta"]["lembas"]["turnId"] == typed_id
    assert [(r.role, r.origin, r.content) for r in rows] == [
        ("user", device_link.ORIGIN_HARNESS, line(JOB_NOTE)), ("assistant", "", ""),
        ("user", "", "and the linter?"), ("assistant", "", "Answer to: and the linter?"),
    ]
    assert rows[1].id == reply_id and rows[0].device_turn_id == H2
    assert all(r.complete and not r.error and not r.queued for r in rows)
    invariant(device, rows)


def test_without_the_capability_a_message_with_no_reply_running_waits_for_the_turns_end(
    db, registered, make_chat
):
    """The same moment on a device before 3.10.0, which would refuse the steer
    and fail the prompt: neither is sent until the harness's turn has ended."""
    device = HarnessDevice(db, OLD)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        typed, reply = _type(db, chat_id, "and the linter?")
        assert reply is not None
        await asyncio.sleep(0.6)
        during = device.sent_methods()
        await device.say("The tests pass.")
        await device.end()
        await asyncio.wait_for(generation_service._TASKS[reply.id], 10)
        return during, reply.id, await _quiet(db, chat_id, 4)

    during, reply_id, rows = asyncio.run(scenario())
    assert during == []
    assert device.refused == []
    assert device.sent_methods() == ["session/prompt"]
    assert device.model_saw == ["and the linter?"]
    # The reply started for the message became the harness turn's own.
    assert [(r.role, r.content) for r in rows] == [
        ("user", line(JOB_NOTE)), ("assistant", "The tests pass."),
        ("user", "and the linter?"), ("assistant", "Answer to: and the linter?"),
    ]
    assert rows[1].id == reply_id
    invariant(device, rows)


def test_a_message_typed_while_a_prompt_waits_behind_a_harness_turn_joins_the_prompts_turn(
    db, registered, make_chat
):
    """A reply's prompt waits its turn on the device behind a turn the harness
    started. A second message typed meanwhile is not steered into the
    harness's turn -- its events are not this reply's, and what it handed back
    would have nobody to take it: it joins the reply's own turn once that
    runs, or goes as the next turn."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    first = _say(db, chat_id, "user", "first question")
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)

    async def scenario():
        # A harness turn this link did not hear begin, and no reply of it here.
        device.running, device.own = H3, True
        device.known.add(H3)
        generation_service.ensure(chat_id, reply_id)
        assert await _until(lambda: device.waiting)
        second, none = _type(db, chat_id, "second question")
        assert none is None
        await asyncio.sleep(1.2)
        steered_into_the_harness_turn = [m for m in device.sent_methods() if m == "_lembas/steer"]
        await device.end()
        return second.id, steered_into_the_harness_turn, await _quiet(db, chat_id, 4)

    second_id, early, rows = asyncio.run(scenario())
    assert early == []
    assert sorted(device.model_saw) == ["first question", "second question"]
    prompts = [p["_meta"]["lembas"]["turnId"] for m, p in device.asked if m == "session/prompt"]
    assert prompts[0] == first and prompts.count(first) == 1
    assert all(r.complete and not r.error and not r.queued for r in rows)


# --- 3. Reattaching after a restart --------------------------------------------------
# The decision for a row with turn T from what `_lembas/session/status` says:
# this instance's own statement of the table. The harness spec's
# `conformance/reattach.json` is run in test_harness_spec.py once the vendored
# copy carries it.
TABLE = [
    ("the person's turn still runs", "t7", False,
     {"busy": True, "turnId": "t7", "queued": [], "lastTurnId": "t6"}, "follow"),
    ("a harness turn still runs", "h2", True,
     {"busy": True, "turnId": "h2", "queued": [], "lastTurnId": "t7"}, "follow"),
    ("the person's prompt waits behind a harness turn", "t8", False,
     {"busy": True, "turnId": "h2", "queued": ["t8"], "lastTurnId": "t7"}, "wait"),
    ("the person's turn was the last to finish", "t7", False,
     {"busy": False, "queued": [], "lastTurnId": "t7"}, "import"),
    ("a harness turn was the last to finish", "h2", True,
     {"busy": False, "queued": [], "lastTurnId": "h2"}, "import"),
    ("the device does not know the person's turn", "t9", False,
     {"busy": False, "queued": [], "lastTurnId": "t7"}, "resend"),
    ("the same while a harness turn runs", "t9", False,
     {"busy": True, "turnId": "h3", "queued": [], "lastTurnId": "t7"}, "resend"),
    ("a harness turn the device no longer knows", "h2", True,
     {"busy": False, "queued": [], "lastTurnId": "t7"}, "import"),
    ("the first of two harness turns, while the second runs", "h2", True,
     {"busy": True, "turnId": "h3", "queued": [], "lastTurnId": "t8"}, "import"),
]


@pytest.mark.parametrize(("turn", "machine", "status", "do"), [row[1:] for row in TABLE],
                         ids=[row[0] for row in TABLE])
def test_what_reattaching_does_with_a_row(turn, machine, status, do):
    assert device_link.reattach_decision(turn, status, machine=machine) == do


@pytest.mark.parametrize("status", [row[3] for row in TABLE] + [{}, {"queued": None}])
def test_a_harness_turn_is_never_sent_again_whatever_the_device_says(status):
    for turn in ("h2", "h3", "t7", "t8", "unknown", ""):
        assert device_link.reattach_decision(turn, status, machine=True) != "resend"


def _harness_rows(db, chat_id, turn=H2, written="The tests ") -> str:
    """A harness turn's rows as a restart leaves them: the note with the
    device's id for the turn, and its reply part written."""
    note = _say(db, chat_id, "user", line(JOB_NOTE), origin=device_link.ORIGIN_HARNESS,
                machine=True)
    db.get(Message, note).device_turn_id = turn
    db.commit()
    return _say(db, chat_id, "assistant", written, complete_=False)


def test_a_restart_during_a_harness_turn_follows_it_by_its_id_and_sends_nothing(
    db, registered, make_chat
):
    """With the capability the device's status is believed: busy, and the turn
    named. The reply follows that turn again; the note is not sent."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    reply_id = _harness_rows(db, chat_id)
    device.running, device.own, device.last = H2, True, "t7"
    device.known.add(H2)
    device.history = [{"role": "user", "text": JOB_NOTE, "turnId": H2, "machine": True},
                      {"role": "assistant", "text": "The tests ", "reasoning": "", "tools": []}]

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        assert await _until(lambda: device.link.writing.get("s1") is not None)
        await device.say("pass.")
        await device.end()
        return await _quiet(db, chat_id, 2)

    rows = asyncio.run(scenario())
    assert device.sent_methods() == []
    # Asked what runs; and, the turn over, read whole from the history: what
    # it said between the restart and now was said to nobody.
    assert [m for m, _ in device.asked] == ["_lembas/session/status", "_lembas/session/history"]
    reply = rows[1]
    assert reply.id == reply_id and reply.content == "The tests pass."
    assert reply.complete and not reply.error and not reply.interrupted


def test_a_harness_turn_the_device_no_longer_knows_is_closed_as_it_stands(
    db, registered, make_chat
):
    """The device restarted in the middle of the turn. "T unknown: send the
    prompt again" is a person's turn's rule; this row is a note, and is never
    sent: what the history has for the turn is all there is -- here, nothing."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    reply_id = _harness_rows(db, chat_id)
    device.last = "t7"
    device.history = [{"role": "user", "text": "run the tests", "turnId": "t7"},
                      {"role": "assistant", "text": "Started.", "reasoning": "", "tools": []}]

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        return await _quiet(db, chat_id, 2)

    rows = asyncio.run(scenario())
    assert device.sent_methods() == []
    assert [m for m, _ in device.asked] == ["_lembas/session/status", "_lembas/session/history"]
    assert rows[1].id == reply_id and rows[1].content == "The tests "
    assert rows[1].complete and not rows[1].error
    assert device.model_saw == []


def test_the_first_of_two_harness_turns_is_read_by_its_id_while_the_second_runs(
    db, registered, make_chat
):
    """Two notes in a row across a restart: the first turn ended while this
    instance was away and the second is running. The first reply is read from
    the history by its own id -- not taken for the turn that runs, and not
    sent. The second, whose start nobody here heard, gets its own rows."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    reply_id = _harness_rows(db, chat_id, written="The ")
    device.running, device.own, device.last = H3, True, "t8"
    device.known.update({H2, H3})
    device.history = [
        {"role": "user", "text": JOB_NOTE, "turnId": H2, "machine": True},
        {"role": "assistant", "text": "The tests pass.", "reasoning": "", "tools": []},
        {"role": "user", "text": TEAM_REPORT, "turnId": H3, "machine": True},
        {"role": "assistant", "text": "The loader ", "reasoning": "", "tools": []},
    ]

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        first = await _quiet(db, chat_id, 2)
        await device.say("is found.")
        assert await _until(lambda: device.link.writing.get("s1") is not None)
        await device.end()
        return first, await _quiet(db, chat_id, 4)

    first, rows = asyncio.run(scenario())
    assert device.sent_methods() == []
    assert first[1].id == reply_id and first[1].content == "The tests pass."
    assert [(r.role, r.origin, r.device_turn_id) for r in rows[2:]] == [
        ("user", device_link.ORIGIN_HARNESS, H3), ("assistant", "", H3),
    ]
    assert rows[2].content == TEAM_REPORT and rows[2].machine
    assert rows[3].content == "The loader is found."
    assert all(r.complete and not r.error for r in rows)


def test_a_cut_off_harness_reply_does_not_take_the_next_turns_events_for_its_own(
    db, registered, make_chat
):
    """The same two notes, with the second turn's `prompt` already heard on
    this link when the first reply is taken up again: the events held are the
    second turn's. The first reply leaves them where they are."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    reply_id = _harness_rows(db, chat_id, written="The ")
    device.running, device.own, device.last = H3, True, "t8"
    device.history = [
        {"role": "user", "text": JOB_NOTE, "turnId": H2, "machine": True},
        {"role": "assistant", "text": "The tests pass.", "reasoning": "", "tools": []},
        {"role": "user", "text": TEAM_REPORT, "turnId": H3, "machine": True},
        {"role": "assistant", "text": "The loader ", "reasoning": "", "tools": []},
    ]
    held = [{"type": "text", "text": "The loader "}]
    device.link.buffered[("s1", H3)] = list(held)
    generation = generation_service.Generation(chat_id=chat_id, message_id=reply_id)
    generation.content = ["The "]

    asyncio.run(device_link._follow_mirror(
        device.link, generation, chat_id, "s1", "workstation", harness=True, device_turn=H2,
    ))
    assert generation.text == "The tests pass."
    assert device.link.buffered == {("s1", H3): held}
    assert device.sent_methods() == []


def test_a_persons_turn_the_device_never_had_is_sent_again_and_waits_behind_a_harness_turn(
    db, registered, make_chat
):
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    typed = _say(db, chat_id, "user", "and the linter?")
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)
    device.running, device.own, device.last = H3, True, "t7"
    device.known.add(H3)

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        assert await _until(lambda: device.waiting)
        assert device.status()["queued"] == [typed]
        # What the harness's turn says meanwhile is not this reply's.
        await device.say("Something about the report.")
        await asyncio.sleep(0.05)
        await device.say(" And more of it.")
        await device.end()
        return await _quiet(db, chat_id, 2)

    rows = asyncio.run(scenario())
    assert device.sent_methods() == ["session/prompt"]
    assert device.model_saw == ["and the linter?"]
    reply = next(r for r in rows if r.id == reply_id)
    assert reply.content == "Answer to: and the linter?"


def test_without_the_capability_a_restart_keeps_to_what_the_link_has_seen(
    db, registered, make_chat
):
    """A device before 3.10.0 says `busy: false` and names no turn while a
    harness turn runs, so its status cannot be asked about the turn by id: the
    reply is found as before -- and whatever it finds, nothing is sent."""
    device = HarnessDevice(db, OLD)
    chat_id = _device_chat(db, make_chat, device)
    reply_id = _harness_rows(db, chat_id)
    device.running, device.own = H2, True
    device.history = [{"role": "user", "text": JOB_NOTE, "turnId": H2},
                      {"role": "assistant", "text": "The tests ", "reasoning": "", "tools": []}]

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        return await _quiet(db, chat_id, 2)

    rows = asyncio.run(scenario())
    assert device.sent_methods() == []
    assert rows[1].id == reply_id and rows[1].complete and not rows[1].error


def test_the_devices_own_row_is_never_a_prompt_on_any_capability_set(db, registered, make_chat):
    """The guard under all of the above: whatever the device lists, the reply
    to a harness row asks and follows, and sends neither prompt nor steer."""
    for capabilities in (NEW, OLD, ("status",), ()):
        device_link.reset()
        device = HarnessDevice(db, capabilities)
        chat_id = _device_chat(db, make_chat, device)
        _harness_rows(db, chat_id)

        async def scenario(chat_id=chat_id):
            await generation_service.resume_interrupted(delay=0)
            return await _quiet(db, chat_id, 2)

        asyncio.run(scenario())
        assert device.sent_methods() == [], capabilities
        assert device.model_saw == []


# --- 4. The history ------------------------------------------------------------------
def test_a_harness_turn_in_an_adopted_sessions_history_is_stored_as_one(
    db, registered, make_chat
):
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    device_link._import_turns(db, db.get(Chat, chat_id), [
        {"role": "user", "text": "run the tests", "turnId": "t7"},
        {"role": "assistant", "text": "Started.", "reasoning": "", "tools": []},
        {"role": "user", "text": JOB_NOTE, "turnId": H2, "machine": True},
        {"role": "assistant", "text": "The tests pass.", "reasoning": "", "tools": []},
    ], "workstation", marked=device.link.can(device_link.HARNESS_TURNS))
    rows = _rows(db, chat_id)
    assert [(r.role, r.origin, r.machine, r.device_turn_id) for r in rows] == [
        ("user", device_link.ORIGIN_TERMINAL, False, None),
        ("assistant", "", False, None),
        ("user", device_link.ORIGIN_HARNESS, True, H2),
        ("assistant", "", False, None),
    ]
    assert rows[2].content == JOB_NOTE
    assert device_link.devices_own(rows[2])


def test_the_adoption_of_a_shared_session_reads_the_mark_only_from_a_device_that_lists_it(
    db, registered, make_chat
):
    """Through the announcement itself: a session a terminal shares becomes a
    chat with its history. A field the device did not list is not relied on."""
    stored = {}
    for capabilities in (NEW, OLD):
        device_link.reset()
        device = HarnessDevice(db, capabilities)
        session = f"shared-{len(stored)}"
        device.history = [
            {"role": "user", "text": "run the tests", "turnId": "t7"},
            {"role": "assistant", "text": "Started.", "reasoning": "", "tools": []},
            {"role": "user", "text": JOB_NOTE, "turnId": H2, "machine": True},
            {"role": "assistant", "text": "The tests pass.", "reasoning": "", "tools": []},
        ]

        async def scenario(device=device, session=session):
            await device.link._notification(
                "_lembas/session/announce", {"sessionId": session, "cwd": "/home/dev/app"}
            )
            await _until(lambda: session not in device.link.adopting and db.scalar(
                select(Chat.id).where(Chat.device_session_id == session)) is not None, 200)

        asyncio.run(scenario())
        db.expire_all()
        chat = db.scalar(select(Chat).where(Chat.device_session_id == session))
        stored[capabilities] = [
            (r.origin, r.machine) for r in _rows(db, chat.id) if r.role == "user"
        ]
    assert stored[NEW] == [(device_link.ORIGIN_TERMINAL, False), (device_link.ORIGIN_HARNESS, True)]
    assert stored[OLD] == [(device_link.ORIGIN_TERMINAL, False)] * 2


def _finished_while_away(db, make_chat, capabilities=NEW):
    """A person's turn cut off by a restart; on the device it finished, and
    then a subagent's report woke the session and that turn finished too."""
    device = HarnessDevice(db, capabilities)
    chat_id = _device_chat(db, make_chat, device)
    typed = _say(db, chat_id, "user", "run the tests")
    reply_id = _say(db, chat_id, "assistant", "Star", complete_=False)
    mark = {"machine": True} if device.knows else {}
    device.history = [
        {"role": "user", "text": "run the tests", "turnId": typed},
        {"role": "assistant", "text": "Started.", "reasoning": "", "tools": []},
        {"role": "user", "text": TEAM_REPORT, "turnId": H2, **mark},
        {"role": "assistant", "text": "The loader is found.", "reasoning": "", "tools": []},
    ]
    device.known.update({typed, H2})
    device.last = H2 if device.knows else typed
    return device, chat_id, typed, reply_id


def test_a_harness_turn_that_ran_while_this_instance_was_away_is_taken_from_the_history(
    db, registered, make_chat
):
    """The reply read from the history has its prompt row: the note, as the
    harness's turn. And reading the history again stores nothing twice."""
    device, chat_id, typed, reply_id = _finished_while_away(db, make_chat)

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        rows = await _quiet(db, chat_id, 4)
        # Read again, as any later reattach would.
        await device_link._take_in_missed(device.link, chat_id, "s1", device.history)
        return rows

    rows = asyncio.run(scenario())
    assert device.model_saw == []  # the device knew the turn: it did not run again
    assert [(r.role, r.content, r.origin, r.device_turn_id) for r in rows] == [
        ("user", "run the tests", "", None),
        # (The reply's row remembers the turn it writes: its message's.)
        ("assistant", "Started.", "", typed),
        ("user", TEAM_REPORT, device_link.ORIGIN_HARNESS, H2),
        ("assistant", "The loader is found.", "", H2),
    ]
    assert rows[2].machine and all(r.complete for r in rows)
    assert len(_rows(db, chat_id)) == 4
    assert db.get(Chat, chat_id).unread is True


def test_a_turn_already_mirrored_live_is_not_stored_again_from_the_history(
    db, registered, make_chat
):
    """Matched by the device's id for the turn, not by its text: the live row
    holds the transcript line, the history the whole note."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(TEAM_REPORT, H2)
        await _following(device)
        await device.say("The loader is found.")
        await device.end()
        rows = await _quiet(db, chat_id, 2)
        await device_link._take_in_missed(device.link, chat_id, "s1", device.history)
        return rows

    rows = asyncio.run(scenario())
    assert rows[0].device_turn_id == H2 and rows[0].content == line(TEAM_REPORT)
    assert [(r.role, r.content) for r in _rows(db, chat_id)] == [
        ("user", line(TEAM_REPORT)), ("assistant", "The loader is found."),
    ]


def test_a_turn_this_chat_already_has_is_not_mirrored_a_second_time(db, registered, make_chat):
    """A turn taken from the history, then said again as a `prompt`: by the
    device's id for it the chat has it, and no second pair of rows is made."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    device_link._import_turns(db, db.get(Chat, chat_id), [
        {"role": "user", "text": JOB_NOTE, "turnId": H2, "machine": True},
        {"role": "assistant", "text": "The tests pass.", "reasoning": "", "tools": []},
    ], "workstation", marked=True)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await asyncio.sleep(0.3)
        return _rows(db, chat_id)

    rows = asyncio.run(scenario())
    assert [(r.role, r.content) for r in rows] == [
        ("user", JOB_NOTE), ("assistant", "The tests pass."),
    ]
    assert not device.link.holding("s1")


def test_without_the_capability_the_history_is_read_as_before(db, registered, make_chat):
    """No `machine` in a history from before 3.10.0: nothing is guessed from a
    note's text, and no row is made for a turn nobody here heard."""
    device, chat_id, typed, reply_id = _finished_while_away(db, make_chat, OLD)

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        return await _quiet(db, chat_id, 2)

    rows = asyncio.run(scenario())
    assert [(r.role, r.content) for r in rows] == [
        ("user", "run the tests"), ("assistant", "Started."),
    ]
    assert device.sent_methods() == []


def test_a_harness_turn_that_began_while_the_link_was_down_is_found_and_followed(
    db, registered, make_chat
):
    """Nobody here heard its `prompt`: the chat is idle and complete, and the
    device is in the middle of answering a subagent's report. Its first event
    on this link has the device asked what runs; the turn is the harness's by
    its history, so it gets its row and its reply -- whole, from the history,
    when it ends."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    _say(db, chat_id, "user", "run the tests")
    _say(db, chat_id, "assistant", "Started.")
    device.running, device.own, device.last = H2, True, "t7"
    device.known.add(H2)
    device.history = [
        {"role": "user", "text": TEAM_REPORT, "turnId": H2, "machine": True},
        {"role": "assistant", "text": "The loader ", "reasoning": "", "tools": []},
    ]

    async def scenario():
        await device.say("is in src/load.py.")
        assert await _until(lambda: device.link.writing.get("s1") is not None)
        await device.say(" Nothing else reads it.")
        await device.end()
        return await _quiet(db, chat_id, 4)

    rows = asyncio.run(scenario())
    assert device.sent_methods() == []
    assert [(r.role, r.origin, r.device_turn_id) for r in rows[2:]] == [
        ("user", device_link.ORIGIN_HARNESS, H2), ("assistant", "", H2),
    ]
    assert rows[2].content == TEAM_REPORT and rows[2].machine
    assert rows[3].content == "The loader is in src/load.py. Nothing else reads it."
    assert rows[3].complete and not rows[3].error


def test_events_of_a_turn_sent_from_here_start_no_second_row_after_a_restart(
    db, registered, make_chat
):
    """The same first event, for a turn this chat already has: left to the
    reply that is taken up again for it."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    typed = _say(db, chat_id, "user", "run the tests")
    _say(db, chat_id, "assistant", "Star", complete_=False)
    device.running, device.own = typed, False
    device.known.add(typed)

    async def scenario():
        await device.say("ted.")
        await asyncio.sleep(0.3)
        return _rows(db, chat_id)

    rows = asyncio.run(scenario())
    assert len(rows) == 2
    assert [m for m, _ in device.asked] == ["_lembas/session/status"]
    assert device.link.probing == {} and device.link.buffered == {}


def test_without_the_capability_a_stray_event_asks_the_device_nothing(db, registered, make_chat):
    device = HarnessDevice(db, OLD)
    chat_id = _device_chat(db, make_chat, device)
    device.running, device.own = H2, True

    async def scenario():
        await device.say("The loader is found.")
        await asyncio.sleep(0.2)
        return _rows(db, chat_id)

    assert asyncio.run(scenario()) == []
    assert device.asked == []


# --- 6. The `reply` frame and the page's own follow path -------------------------------
def _replies_started(since: int) -> list[str]:
    return [message_id for _, _, message_id in generation_service.started_after(since)]


def test_a_steered_message_starts_no_reply_of_its_own(db, registered, make_chat):
    """What `/api/v2/events` says as `reply` is every reply `ensure` starts.
    A message that joins a harness turn starts none: the one reply said is the
    turn's, and the chat has no second, empty assistant row."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    before = generation_service.started()

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.inbox)
        await device.take_in()
        await device.say("Both are fine.")
        await device.end()
        return await _quiet(db, chat_id, 3)

    rows = asyncio.run(scenario())
    replies = [r for r in rows if r.role == "assistant"]
    assert len(replies) == 1 and replies[0].content == "Both are fine."
    assert _replies_started(before) == [replies[0].id]


def test_a_message_handed_back_starts_exactly_one_more_reply_after_the_turn(
    db, registered, make_chat
):
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    before = generation_service.started()

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.inbox)
        await device.say("The tests pass.")
        await device.end()
        return await _quiet(db, chat_id, 4)

    rows = asyncio.run(scenario())
    replies = {r.id: r.content for r in rows if r.role == "assistant"}
    started = _replies_started(before)
    assert [replies[i] for i in started] == ["The tests pass.", "Answer to: and the linter?"]
    assert "" not in replies.values()


async def _page_frames(chat_id: str, message_id: str, follow=None) -> dict[str, list[str]]:
    """What the page's own stream of a reply sends, by event name -- or the
    app's (`follow`: `/api/v2`'s stream of the same reply)."""
    frames: dict[str, list[str]] = {}
    async for frame in (follow or chats_api._follow)(chat_id, message_id):
        if frame.startswith("event: "):
            name, _, rest = frame[len("event: "):].partition("\n")
            frames.setdefault(name, []).append(rest)
    return frames


@pytest.mark.parametrize("taken_in", [True, False], ids=["taken-in", "handed-back"])
def test_the_pages_stream_of_a_harness_turn_with_a_message_joining_it(
    db, registered, make_chat, taken_in
):
    """The page follows the reply the turn has. A message taken in leaves the
    queue at the bottom of the thread for its place in the reply; one handed
    back stays a bubble of its own and gets its reply appended when the turn
    is over -- one streaming shell, for that reply and no other."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        reply_id = generation_service.running_for(chat_id).message_id
        page = asyncio.create_task(_page_frames(chat_id, reply_id))
        app = asyncio.create_task(_page_frames(chat_id, reply_id, v2_stream.follow_json))
        typed, _ = _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.inbox)
        if taken_in:
            await device.take_in()
        await device.say("The tests pass.")
        await asyncio.sleep(0.3)
        await device.end()
        frames = await asyncio.wait_for(page, 20)
        said = await asyncio.wait_for(app, 20)
        return typed.id, reply_id, frames, said, await _quiet(db, chat_id, 3 if taken_in else 4)

    typed_id, reply_id, frames, said, rows = asyncio.run(scenario())
    # The app's stream of the same reply: `steered` names the message taken
    # in, and says nothing of one that was handed back.
    steered = [json.loads(f.split("data: ", 1)[1]) for f in said.get("steered", [])]
    assert steered == ([{"ids": [typed_id]}] if taken_in else [])
    assert "done" in said
    done = "".join(frames["done"])
    gone = f'id="msg-{typed_id}" hx-swap-oob="delete"'
    assert f'id="msg-{reply_id}"' in done
    assistants = [r for r in rows if r.role == "assistant"]
    if taken_in:
        assert gone in "".join(frames.get("steered", [])) or gone in done
        assert "sse-connect" not in done
        assert len(assistants) == 1
    else:
        assert gone not in "".join(frames.get("steered", [])) and gone not in done
        other = next(r for r in assistants if r.id != reply_id)
        assert done.count("sse-connect") == 1
        assert f"/messages/{other.id}/" in done or other.id in done
        assert other.content == "Answer to: and the linter?"
