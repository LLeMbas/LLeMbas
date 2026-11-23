"""A chat on a linked device, under the conditions a review named: a steer
answered late, a link that drops in the middle of a turn, turns so short they
end before their reply here exists, a restart after a message was taken into a
reply, and a device that says things no honest device would.

Every test that has a chat ends on the same line, `invariant(device, rows)`:

    every message the chat shows as the person's reaches the model exactly
    once; every harness turn the device ran has one row and its whole reply;
    no reply row holds another turn's text.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from lembas.api import chats as chats_api
from lembas.db.models import Chat, Message, User
from lembas.security import api_tokens
from lembas.services import device_link, settings_store
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
H4 = "5c1e0b7a-0000-4000-8000-0000000000h4"


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
    db.expire_all()
    return chats_api.write_turn(db, db.get(Chat, chat_id), db.scalar(select(User)), text)


async def _until(ok, tries: int = 600) -> bool:
    for _ in range(tries):
        if ok():
            return True
        await asyncio.sleep(0.01)
    return bool(ok())


async def _quiet(db, chat_id: str, count: int, tries: int = 900) -> list[Message]:
    """The chat's rows once there are `count`, all finished, nothing running."""
    def settled() -> bool:
        rows = _rows(db, chat_id)
        return (
            len(rows) >= count
            and all(r.complete for r in rows)
            and generation_service.running_for(chat_id) is None
        )

    await _until(settled, tries)
    return _rows(db, chat_id)


def _shape(rows) -> list[tuple[str, str]]:
    return [(r.role, r.content) for r in rows]


# --- 1. A steer answered late, then handed back -------------------------------------
def test_a_steer_answered_late_and_then_handed_back_is_sent_once(db, registered, make_chat):
    """The message joins the turn the device is in; the steer's answer comes
    after the wait for it. Late is not refused -- but the turn may still end
    without taking the message in, and then says so (`unsent`). It is sent,
    once. (It was shown as delivered and never sent.)"""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    device.late_steers = True

    async def scenario():
        # Only the turn's `task` start is heard yet: no row of it exists.
        await device.wake(JOB_NOTE, H2, prompt=False)
        typed, reply = _type(db, chat_id, "and the linter?")
        assert reply is not None
        assert await _until(lambda: device.inbox)
        await device.say_prompt()
        await device.say("The tests pass.")
        await device.end()
        return typed.id, await _quiet(db, chat_id, 4)

    typed_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == ["_lembas/steer", "session/prompt"]
    prompt = next(p for m, p in device.asked if m == "session/prompt")
    assert prompt["_meta"]["lembas"]["turnId"] == typed_id
    assert _shape(rows) == [
        ("user", line(JOB_NOTE)), ("assistant", "The tests pass."),
        ("user", "and the linter?"), ("assistant", "Answer to: and the linter?"),
    ]
    assert all(not r.error and not r.queued for r in rows)
    invariant(device, rows)


def test_a_reply_that_joined_a_turn_before_its_prompt_was_said_is_that_turns_reply(
    db, registered, make_chat
):
    """Between a harness turn's `task` start and its `prompt` a message typed
    here joins the turn. The turn still gets its row, the reply is the turn's,
    and the message is shown where the model took it in."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2, prompt=False)
        typed, reply = _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.inbox)
        await device.say_prompt()
        await device.take_in()
        await device.say("Both are fine.")
        await device.end()
        return reply.id, await _quiet(db, chat_id, 3)

    reply_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == ["_lembas/steer"]
    assert [(r.role, r.origin, r.device_turn_id) for r in rows] == [
        ("user", device_link.ORIGIN_HARNESS, H2), ("user", "", None), ("assistant", "", H2),
    ]
    assert rows[2].id == reply_id and rows[2].content == "Both are fine."
    invariant(device, rows)


# --- 2. A message handed into a turn, and the link or the instance going away -------
def test_the_link_drops_and_the_turn_goes_on_the_message_is_taken_in_once(
    db, registered, make_chat
):
    """A message was handed into a harness turn; the link drops and comes
    back while the turn runs. The reply waits for the device, follows the
    turn again by its id, and holds the turn's whole text -- what was said to
    nobody included. The message goes in once and nothing is sent again."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        await device.say("The tests ")
        typed, _ = _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.inbox)
        device.drop()
        await device.say("pass. ")
        device.relink()
        assert await _until(lambda: device.link.writing.get("s1") is not None)
        await device.take_in()
        await device.say("The linter is clean.")
        await device.end()
        return typed.id, await _quiet(db, chat_id, 3)

    typed_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == ["_lembas/steer"]
    assert device.model_saw == ["and the linter?"]
    reply = rows[-1]
    assert reply.content == "The tests pass. The linter is clean."
    assert reply.complete and not reply.error
    assert not {r.id: r for r in rows}[typed_id].queued
    invariant(device, rows)


def test_the_turn_ends_while_the_link_is_down_the_message_is_marked_and_never_sent(
    db, registered, make_chat
):
    """The same, and the turn ends while the link is down: what it handed
    back, if anything, was said to nobody. Whether the message went in cannot
    be known from what the device says afterwards -- so it is not sent again
    on a guess. The chat says so on the message (`unsure`, waiting) and its
    person decides; the reply is read whole from the device's history."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        await device.say("The tests ")
        typed, _ = _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.inbox)
        device.drop()
        await device.say("pass.")
        await device.end()
        device.relink()
        rows = await _quiet(db, chat_id, 3)
        # Nothing drains it either: long enough for a drained reply to start.
        await asyncio.sleep(0.3)
        return typed.id, rows

    typed_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == ["_lembas/steer"]
    typed = {r.id: r for r in rows}[typed_id]
    assert typed.queued and typed.origin == device_link.ORIGIN_UNSURE
    reply = next(r for r in rows if r.role == "assistant")
    assert reply.content == "The tests pass." and reply.complete and not reply.error
    assert len(_rows(db, chat_id)) == 3
    invariant(device, rows)


def test_a_restart_after_a_message_was_taken_into_a_reply_asks_about_the_replys_own_turn(
    db, registered, make_chat
):
    """A reply took a second message in and was moved after it. Cut off by a
    restart, it is taken up by the turn its row remembers -- its own prompt's
    -- and not by the message that now sorts last before it: that one was
    sent again as a prompt of its own, and the model read it twice."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    first = _say(db, chat_id, "user", "first question")
    second = _say(db, chat_id, "user", "second thought")
    reply_id = _say(db, chat_id, "assistant", "Part", complete_=False)
    db.get(Message, reply_id).device_turn_id = first
    db.commit()
    device.running, device.own = first, False
    device.known.add(first)
    device.model_saw = ["first question", "second thought"]
    device.said[first] = "Part"
    device.history = [{"role": "user", "text": "first question", "turnId": first},
                      {"role": "assistant", "text": "Part", "reasoning": "", "tools": []}]

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        assert await _until(lambda: device.link.writing.get("s1") is not None)
        await device.say(" two.")
        await device.end()
        return await _quiet(db, chat_id, 3)

    rows = asyncio.run(scenario())
    assert device.sent_methods() == []
    assert device.model_saw == ["first question", "second thought"]
    assert rows[-1].id == reply_id and rows[-1].content == "Part two."
    assert not {r.id: r for r in rows}[second].queued
    invariant(device, rows)


def test_a_restart_of_a_reply_that_joined_a_turn_never_sends_its_message_as_a_prompt(
    db, registered, make_chat
):
    """The reply joined a turn that was running on the device (its row says
    which) and the message went in there. After a restart the device is asked
    about *that* turn: over, so its reply is read from the history -- and the
    message, whose `steered` nobody here heard, is marked rather than sent as
    a prompt under its own id, which the device has never seen as a turn."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    typed = _say(db, chat_id, "user", "and the linter?")
    reply_id = _say(db, chat_id, "assistant", "Work", complete_=False)
    db.get(Message, reply_id).device_turn_id = "t-other"
    db.commit()
    device.last = "t-other"
    device.known.add("t-other")
    device.model_saw = ["and the linter?"]
    device.said["t-other"] = "Work done."
    device.history = [{"role": "user", "text": "tidy up", "turnId": "t-other"},
                      {"role": "assistant", "text": "Work done.", "reasoning": "", "tools": []}]

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        rows = await _quiet(db, chat_id, 2)
        await asyncio.sleep(0.3)
        return rows

    rows = asyncio.run(scenario())
    assert device.sent_methods() == []
    assert device.model_saw == ["and the linter?"]
    assert rows[1].id == reply_id and rows[1].content == "Work done."
    assert rows[0].id == typed and rows[0].queued
    assert rows[0].origin == device_link.ORIGIN_UNSURE
    invariant(device, rows)


def test_a_message_of_unknown_fate_is_sent_when_its_person_says_so_and_only_then(
    db, registered, make_chat, client
):
    """`unsure` waits: the queue's drain passes it by, a running turn is not
    handed it, and "Send now" sends it as an ordinary turn."""
    from lembas.db.models import Connection, Model

    # A model to draw the chat page with: without one it is the first-run screen.
    connection = Connection(name="c", base_url="http://127.0.0.1:1/v1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    _say(db, chat_id, "user", "first question")
    _say(db, chat_id, "assistant", "Answer to: first question")
    waiting = _say(db, chat_id, "user", "and the linter?", queued=True,
                   origin=device_link.ORIGIN_UNSURE)
    device.model_saw = ["first question"]
    device.said["t1"] = "Answer to: first question"

    with generation_service.session_scope() as other:
        assert generation_service._next_waiting(other, chat_id) is None
    page = client.get(f"/chat/{chat_id}").text
    assert "data-fate-unknown" in page and "It is not known whether this reached" in page
    sent = client.post(f"/api/chats/{chat_id}/messages/{waiting}/send-now")
    assert sent.status_code == 200
    # Under the id it has always had: the one a steer of it carried.
    prompt = next(p for m, p in device.asked if m == "session/prompt")
    assert prompt["_meta"]["lembas"]["turnId"] == waiting

    async def scenario():
        return await _quiet(db, chat_id, 4)

    rows = asyncio.run(scenario())
    row = {r.id: r for r in rows}[waiting]
    assert not row.queued and row.origin == ""
    invariant(device, rows)


def test_a_message_the_device_carried_over_is_marked_taken_in_by_its_id_in_the_later_turn(
    db, registered, make_chat
):
    """Spec 3.10.0, as amended: what a turn ended without taking in while no
    link was up is kept by the device and goes in at the next turn's first
    step, said with `steered` and the message's id. The chat's own row is
    recognised by that id -- no longer of unknown fate, shown where it went
    in, and not drawn a second time as words typed in the terminal."""
    from lembas.services import steps

    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        typed, _ = _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.inbox)
        device.drop()
        await device.say("The tests pass.")
        await device.end()
        device.relink()
        await _quiet(db, chat_id, 3)
        # The next turn on the device: a second note wakes it.
        await device.wake(TEAM_REPORT, H3)
        await _following(device)
        await device.say("The linter is clean and the loader is found.")
        await device.end()
        return typed.id, await _quiet(db, chat_id, 5)

    typed_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == ["_lembas/steer"]
    assert device.model_saw == ["and the linter?"]
    typed = {r.id: r for r in rows}[typed_id]
    assert not typed.queued and typed.origin == ""
    later = next(r for r in rows if r.content.startswith("The linter is clean"))
    assert steps.steered_ids(later.steps_json) == {typed_id}
    assert sum(len(s.get("steer") or []) for s in later.steps_json) == 1
    invariant(device, rows)


# --- A steered message is given to the model once: the device says what became of it ----
def _send_now(db, chat_id: str, message_id: str) -> str:
    """What "Send now" does (`send_queued_now`): the message, under its own
    id, as a turn with a reply of its own. Returns the reply's id."""
    from lembas.services import chat as chat_service

    db.expire_all()
    row = db.get(Message, message_id)
    row.queued, row.origin = False, ""
    db.commit()
    reply = chat_service.create_message(
        db, db.get(Chat, chat_id), "assistant", "", complete_=False
    )
    db.commit()
    generation_service.ensure(chat_id, reply.id)
    return reply.id


async def _steered_then_link_lost(db, device, chat_id, *, take_in: bool):
    """A message handed into a harness turn; the link drops; the turn takes
    it in, or not, and ends with nobody listening. Back up, the chat cannot
    know which: the message is `unsure`."""
    await device.wake(JOB_NOTE, H2)
    await _following(device)
    typed, _ = _type(db, chat_id, "and the linter?")
    assert await _until(lambda: device.inbox)
    device.drop()
    if take_in:
        await device.take_in()
    await device.say("The tests pass.")
    await device.end()
    device.relink()
    rows = await _quiet(db, chat_id, 3)
    assert {r.id: r for r in rows}[typed.id].origin == device_link.ORIGIN_UNSURE
    return typed.id


@pytest.mark.parametrize("fate", ["taken", "carried"])
def test_sending_a_message_the_device_still_knows_runs_nothing_and_leaves_no_empty_reply(
    db, registered, make_chat, fate
):
    """Its person presses Send on a message of unknown fate. The prompt goes
    under the message's own id; the device knows that id as a message it was
    steered, runs nothing, and says what became of it. `taken`: the row is
    delivered. `carried`: it waits, saying the computer has it, until the
    turn that takes it in says so. No reply row is left for the prompt that
    did not run, and the model is given the message once."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        typed_id = await _steered_then_link_lost(db, device, chat_id, take_in=fate == "taken")
        reply_id = _send_now(db, chat_id, typed_id)
        await asyncio.wait_for(generation_service._TASKS[reply_id], 10)
        between = [(r.id, r.queued, r.origin) for r in _rows(db, chat_id)]
        if fate == "carried":
            await device.wake(TEAM_REPORT, H3)
            await _following(device)
            await device.say("The linter is clean.")
            await device.end()
            await _quiet(db, chat_id, 5)
        return typed_id, reply_id, between, _rows(db, chat_id)

    typed_id, reply_id, between, rows = asyncio.run(scenario())
    prompt = next(p for m, p in device.asked if m == "session/prompt")
    assert prompt["_meta"]["lembas"]["turnId"] == typed_id
    assert device.model_saw == ["and the linter?"]
    assert reply_id not in {r.id for r in rows} and len(between) == 3
    held = next(row for row in between if row[0] == typed_id)
    if fate == "taken":
        assert held[1:] == (False, "")
    else:
        assert held[1:] == (True, device_link.ORIGIN_CARRIED)
    typed = {r.id: r for r in rows}[typed_id]
    assert not typed.queued and typed.origin == ""
    assert all(r.content or r.role == "user" for r in rows), "an empty reply was left behind"
    invariant(device, rows)


def test_a_message_waiting_in_the_running_turn_and_then_handed_back_runs_as_a_prompt(
    db, registered, make_chat
):
    """`waiting`: the message is in the queue of the turn that runs. Sent
    again it does not run; it waits here. The turn then ends without taking
    it in and hands it back (`unsent`): the device has let go of the id, the
    message is an ordinary waiting one again, and it is sent -- and this
    time it runs."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        # A harness turn runs on the device, with the message in its queue
        # (steered before this instance restarted); nothing of it is heard yet.
        device.running, device.own = H2, True
        device.known.add(H2)
        device.harness_ran.append(H2)
        device.note = JOB_NOTE
        device.history = [{"role": "user", "text": JOB_NOTE, "turnId": H2, "machine": True}]
        waiting = _say(db, chat_id, "user", "and the linter?", queued=True,
                       origin=device_link.ORIGIN_UNSURE)
        device.inbox.append(("and the linter?", waiting))
        reply_id = _send_now(db, chat_id, waiting)
        await asyncio.wait_for(generation_service._TASKS[reply_id], 10)
        held = [(r.id, r.queued, r.origin) for r in _rows(db, chat_id)]
        assert held == [(waiting, True, device_link.ORIGIN_CARRIED)]  # and no reply row
        await device.emit({"type": "task", "state": "start", "turnId": H2})
        await device.say_prompt()
        assert await _until(lambda: device.link.writing.get("s1") is not None)
        await device.say("The tests pass.")
        await device.end()  # hands the message back
        return waiting, await _quiet(db, chat_id, 4)

    waiting, rows = asyncio.run(scenario())
    assert device.model_saw == ["and the linter?"]
    prompts = [p["_meta"]["lembas"]["turnId"] for m, p in device.asked if m == "session/prompt"]
    assert prompts == [waiting, waiting]  # the first did not run; the second did
    typed = {r.id: r for r in rows}[waiting]
    assert not typed.queued and typed.origin == ""
    assert _shape(rows) == [
        ("user", line(JOB_NOTE)), ("assistant", "The tests pass."),
        ("user", "and the linter?"), ("assistant", "Answer to: and the linter?"),
    ]
    invariant(device, rows)


# --- 3. Turns shorter than the time their reply takes to exist -----------------------
def test_three_short_harness_turns_back_to_back_each_get_their_own_row_and_reply(
    db, registered, make_chat
):
    """Each turn starts, answers and ends before the first one's rows are made
    here. What is held is held per turn: no turn's events go into another's
    reply, and none is left without one."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        # The rows of the first cannot be made yet (as while a chat is adopted).
        device.link.adopting["s1"] = gate = asyncio.Event()
        for turn, text in ((H2, "One."), (H3, "Two."), (H4, "Three.")):
            await device.wake(JOB_NOTE, turn)
            await device.say(text)
            await device.end()
        gate.set()
        device.link.adopting.pop("s1", None)
        return await _quiet(db, chat_id, 6)

    rows = asyncio.run(scenario())
    assert device.sent_methods() == []
    assert sorted(r.content for r in rows if r.role == "assistant") == ["One.", "Three.", "Two."]
    assert not device.link.buffered and not device.link.mirroring
    invariant(device, rows)


def test_a_turn_that_ends_and_the_next_that_starts_while_the_first_reply_is_awaited(
    db, registered, make_chat
):
    """The second turn is still running when the first one's reply is made:
    it is followed live, into its own reply."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        device.link.adopting["s1"] = gate = asyncio.Event()
        await device.wake(JOB_NOTE, H2)
        await device.say("One.")
        await device.end()
        await device.wake(TEAM_REPORT, H3)
        await device.say("Two, ")
        gate.set()
        device.link.adopting.pop("s1", None)
        assert await _until(lambda: len(_rows(db, chat_id)) >= 4)
        await device.say("and more.")
        await device.end()
        return await _quiet(db, chat_id, 4)

    rows = asyncio.run(scenario())
    assert _shape(rows) == [
        ("user", line(JOB_NOTE)), ("assistant", "One."),
        ("user", line(TEAM_REPORT)), ("assistant", "Two, and more."),
    ]
    invariant(device, rows)


# --- 4. A typed message and a harness turn's rows racing ------------------------------
def test_a_queued_message_drained_while_a_harness_turns_rows_are_made_does_not_take_its_reply(
    db, registered, make_chat
):
    """A message handed back by a person's turn is drained into a reply of its
    own at the very moment a harness turn has begun and its rows are not made
    yet. The harness turn is shown -- its note, its reply -- and the message
    joins it like any message typed while a reply runs. (The drained reply
    took the turn's events, the note was never shown, and its output stood as
    the reply to the person's message.)"""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    answer = device.answer
    woken: list[bool] = []

    async def wakes_right_after(method, params):
        said = await answer(method, params)
        if method == "session/prompt" and not woken:
            woken.append(True)
            await device.wake(TEAM_REPORT, H2)
        return said

    device.answer = wakes_right_after

    async def scenario():
        device.pause = asyncio.Event()
        _type(db, chat_id, "first question")
        assert await _until(lambda: "Answer" in device.said.get(device.running, ""))
        typed, none = _type(db, chat_id, "and the linter?")
        assert none is None
        assert await _until(lambda: device.inbox)
        device.pause.set()  # the turn ends without taking it in: handed back
        assert await _until(lambda: device.running == H2 and device.inbox)
        await device.take_in()
        await device.say("The loader is found.")
        await device.end()
        return typed.id, await _quiet(db, chat_id, 5)

    typed_id, rows = asyncio.run(scenario())
    assert device.model_saw == ["first question", "and the linter?"]
    assert device.sent_methods() == ["session/prompt", "_lembas/steer", "_lembas/steer"]
    assert _shape(rows) == [
        ("user", "first question"), ("assistant", "Answer to: first question"),
        ("user", line(TEAM_REPORT)), ("user", "and the linter?"),
        ("assistant", "The loader is found."),
    ]
    assert rows[2].device_turn_id == H2 and rows[2].origin == device_link.ORIGIN_HARNESS
    invariant(device, rows)


def test_a_prompt_waiting_behind_a_harness_turn_does_not_cost_the_turn_its_rows(
    db, registered, make_chat
):
    """A reply's prompt waits its turn on the device behind a harness turn
    whose `prompt` is said after the reply was sent. One reply at a time in a
    chat -- so the harness turn is written, finished, from its events when it
    ends, instead of not at all."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        # The harness's turn has begun on the device; nothing of it is heard yet.
        device.running, device.own = H2, True
        device.known.add(H2)
        device.harness_ran.append(H2)
        device.note = JOB_NOTE
        device.history = [{"role": "user", "text": JOB_NOTE, "turnId": H2, "machine": True}]
        typed, reply = _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.waiting)
        await device.emit({"type": "task", "state": "start", "turnId": H2})
        await device.say_prompt()
        await device.say("The tests pass.")
        await device.end()
        return reply.id, await _quiet(db, chat_id, 4)

    reply_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == ["session/prompt"]
    by_id = {r.id: r for r in rows}
    assert by_id[reply_id].content == "Answer to: and the linter?"
    note = next(r for r in rows if r.device_turn_id == H2 and r.role == "user")
    assert note.origin == device_link.ORIGIN_HARNESS and note.machine
    invariant(device, rows)


# --- Stop names the turn it means ----------------------------------------------------
def test_stop_on_a_reply_whose_prompt_still_waits_leaves_the_running_turn_alone(
    db, registered, make_chat
):
    """A reply's prompt waits on the device behind a turn the harness started.
    Stop on that reply means *that* prompt. To a device with `harness_turns`
    the cancel names the reply's turn: the prompt is taken out of the queue
    and never runs, and the harness's turn goes on to its end. (A cancel
    without an id stops whatever runs -- the harness's turn -- and the
    message ran afterwards.)"""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        device.running, device.own = H2, True
        device.known.add(H2)
        device.harness_ran.append(H2)
        device.note = JOB_NOTE
        device.history = [{"role": "user", "text": JOB_NOTE, "turnId": H2, "machine": True}]
        typed, reply = _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.waiting)
        await device.emit({"type": "task", "state": "start", "turnId": H2})
        await device.say_prompt()
        await device.say("The tests ")
        generation_service.request_stop(reply.id)
        assert await _until(lambda: device.cancels)
        assert device.running == H2, "the harness's turn was stopped"
        await device.say("pass.")
        await device.end()
        return typed.id, reply.id, await _quiet(db, chat_id, 4)

    typed_id, reply_id, rows = asyncio.run(scenario())
    cancel = next(s for s in device.sent if s.get("method") == "session/cancel")
    assert cancel["params"]["_meta"]["lembas"]["turnId"] == typed_id
    assert device.cancels == [typed_id] and device.model_saw == []
    by_id = {r.id: r for r in rows}
    assert by_id[reply_id].stopped and by_id[reply_id].content == ""
    note = next(r for r in rows if r.device_turn_id == H2 and r.role == "user")
    reply = rows[rows.index(note) + 1]
    assert reply.content == "The tests pass." and not reply.stopped
    invariant(device, rows, withdrawn=(typed_id,))


def test_without_the_capability_stop_withdraws_a_message_not_yet_sent(db, registered, make_chat):
    """A device before 3.10.0 is sent nothing while a turn its harness started
    runs: the reply's prompt waits *here*. Stop then withdraws it here -- it is
    never sent, no cancel goes out, and the harness's turn is left alone."""
    device = HarnessDevice(db, OLD)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        device.running, device.own = H2, True
        link = device.link
        link.busy.add("s1")
        link.current["s1"] = link.harness_turns["s1"] = H2
        typed, reply = _type(db, chat_id, "and the linter?")
        await asyncio.sleep(0.4)
        generation_service.request_stop(reply.id)
        await asyncio.wait_for(generation_service._TASKS[reply.id], 10)
        await asyncio.sleep(0.2)
        return typed.id, reply.id, _rows(db, chat_id)

    typed_id, reply_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == [] and device.refused == []
    assert [s.get("method") for s in device.sent] == []
    assert device.running == H2 and device.model_saw == []
    assert {r.id: r for r in rows}[reply_id].stopped
    device.harness_ran.clear()  # (its rows are not this test's subject)
    invariant(device, rows, withdrawn=(typed_id,))


def test_without_the_capability_a_prompt_already_waiting_there_is_said_not_to_be_withdrawn(
    db, registered, make_chat
):
    """The prompt is already on a device that cannot be told which turn a
    cancel means. A plain cancel would stop the turn that *runs* -- another's
    -- and leave this prompt to run anyway: so none is sent, and the reply
    says the message could not be taken back."""
    device = HarnessDevice(db, NEW)  # it queues prompts, as any device with `turns` does ...
    # ... and lists less:
    device.link.initialized = {"_meta": {"lembas": {"capabilities": list(OLD)}}}
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        device.running, device.own = H2, True
        device.known.add(H2)
        typed, reply = _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.waiting)
        await device.emit({"type": "task", "state": "start", "turnId": H2})
        generation_service.request_stop(reply.id)
        await asyncio.wait_for(generation_service._TASKS[reply.id], 20)
        return reply.id, _rows(db, chat_id)

    reply_id, rows = asyncio.run(scenario())
    assert device.cancels == [] and device.running == H2
    reply = {r.id: r for r in rows}[reply_id]
    assert reply.stopped and "cannot take it back" in reply.content
    # (No invariant line: on such a device the message does run later, unseen
    # here -- which is exactly what the reply now says.)


def test_a_device_before_3_10_is_sent_the_cancel_it_knows(db, registered):
    """No turn id for a device that does not list `harness_turns`, and none
    for a reply that has no turn of its own."""
    reply = generation_service.Generation(chat_id="c", message_id="m")
    reply.turn_id = "t7"
    new, old = HarnessDevice(db, NEW).link, HarnessDevice(db, OLD).link
    assert device_link.cancel_params(new, reply, "s1") == {
        "sessionId": "s1", "_meta": {"lembas": {"turnId": "t7"}},
    }
    assert device_link.cancel_params(old, reply, "s1") == {"sessionId": "s1"}
    reply.turn_id = ""
    assert device_link.cancel_params(new, reply, "s1") == {"sessionId": "s1"}


# --- 9. A dropped link does not end a harness reply ----------------------------------
@pytest.mark.parametrize("ends", ["after", "while-down"])
def test_a_link_that_drops_in_the_middle_of_a_harness_turn_does_not_end_its_reply(
    db, registered, make_chat, ends
):
    """The reply waits for the device as a reply to a typed turn does. The
    turn still running is followed on; one that ended meanwhile is read from
    the history. Either way the row holds the whole reply and no error. (It
    was closed with "link dropped" and the part written so far, for good.)"""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        await device.say("The tests ")
        device.drop()
        await device.say("pass, ")
        if ends == "while-down":
            await device.say("all of them.")
            await device.end()
        device.relink()
        if ends == "after":
            assert await _until(lambda: device.link.writing.get("s1") is not None)
            await device.say("all of them.")
            await device.end()
        return await _quiet(db, chat_id, 2)

    rows = asyncio.run(scenario())
    assert device.sent_methods() == []
    assert rows[1].content == "The tests pass, all of them."
    assert rows[1].complete and not rows[1].error and not rows[1].stopped
    invariant(device, rows)


# --- 8. A refusal comes before the stop ------------------------------------------------
def test_regenerating_a_running_harness_reply_is_refused_without_stopping_the_turn(
    db, registered, make_chat
):
    """A turn the device started cannot be regenerated from here (its row has
    no id the device could rewind to). Said while it runs, that refusal used
    to come *after* the running reply was stopped -- the device's turn was
    cancelled, and then nothing was done."""
    from fastapi import HTTPException

    device = HarnessDevice(db, (*NEW, "rewind"))
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        await device.say("The tests ")
        reply_id = generation_service.running_for(chat_id).message_id
        note_id = _rows(db, chat_id)[0].id
        db.expire_all()
        chat, user = db.get(Chat, chat_id), db.scalar(select(User))
        with pytest.raises(HTTPException) as refused:
            await chats_api.regenerate_reply(db, chat, user, reply_id)
        with pytest.raises(HTTPException):
            await chats_api.rewind_to(db, chat, user, note_id, "something else")
        still = generation_service.running_for(chat_id)
        assert still is not None and not still.cancel and not still.stopped
        await device.say("pass.")
        await device.end()
        return refused.value.status_code, await _quiet(db, chat_id, 2)

    code, rows = asyncio.run(scenario())
    assert code == 409
    assert [s.get("method") for s in device.sent] == []  # no `session/cancel`
    assert rows[1].content == "The tests pass." and not rows[1].stopped
    invariant(device, rows)


# --- A harness turn that started and ended while nobody listened ------------------------
def test_when_a_device_links_its_recent_chats_catch_up_on_what_the_harness_did_alone(
    db, registered, make_chat
):
    """A subagent's report woke the session and the turn finished while this
    instance was away; the chat has no reply to take up, so nothing read its
    history. At link-up the device's most recently changed chats are asked
    once: the turn after the last one the chat knows is stored -- once."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    typed = _say(db, chat_id, "user", "run the tests")
    _say(db, chat_id, "assistant", "Started.")
    device.model_saw = ["run the tests"]
    device.said = {typed: "Started.", H2: "The loader is found.", "old": "Long ago."}
    device.harness_ran = [H2]
    device.last = H2
    device.history = [
        # Before anything this chat knows by id: not a place to start from.
        {"role": "user", "text": JOB_NOTE, "turnId": "old", "machine": True},
        {"role": "assistant", "text": "Long ago.", "reasoning": "", "tools": []},
        {"role": "user", "text": "run the tests", "turnId": typed},
        {"role": "assistant", "text": "Started.", "reasoning": "", "tools": []},
        {"role": "user", "text": TEAM_REPORT, "turnId": H2, "machine": True},
        {"role": "assistant", "text": "The loader is found.", "reasoning": "", "tools": []},
    ]

    async def scenario():
        assert await device_link.catch_up(device.link) == 1
        assert await device_link.catch_up(device.link) == 1  # asked again: nothing twice

    asyncio.run(scenario())
    rows = _rows(db, chat_id)
    assert _shape(rows) == [
        ("user", "run the tests"), ("assistant", "Started."),
        ("user", TEAM_REPORT), ("assistant", "The loader is found."),
    ]
    assert rows[2].origin == device_link.ORIGIN_HARNESS and rows[2].device_turn_id == H2
    assert device.sent_methods() == []
    old = HarnessDevice(db, OLD)
    assert asyncio.run(device_link.catch_up(old.link)) == 0
    invariant(device, rows)


# --- 6. What a turn hands back is matched, not counted --------------------------------
def test_a_text_handed_back_that_was_never_handed_over_requeues_nothing(
    db, registered, make_chat
):
    """The model took the message in and the device never said so; the turn's
    end hands back some other text. By count that put the delivered message
    back in the queue, and it was sent a second time."""
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)

    async def scenario():
        await device.wake(JOB_NOTE, H2)
        await _following(device)
        typed, _ = _type(db, chat_id, "and the linter?")
        assert await _until(lambda: device.inbox)
        device.swallow(["something this chat never said"])
        await device.say("Both are fine.")
        await device.end()
        rows = await _quiet(db, chat_id, 3)
        await asyncio.sleep(0.3)
        return typed.id, rows

    typed_id, rows = asyncio.run(scenario())
    assert device.sent_methods() == ["_lembas/steer"]
    assert device.model_saw == ["and the linter?"]
    assert not {r.id: r for r in rows}[typed_id].queued
    assert len(_rows(db, chat_id)) == 3
    invariant(device, rows)


def test_an_end_that_names_no_turn_leaves_nothing_behind_for_a_later_reply(db, registered):
    device = HarnessDevice(db, NEW)

    async def scenario():
        await device.emit({"type": "task", "state": "end", "unsent": ["stale"]})
        await device.emit({"type": "task", "state": "end", "turnId": H2, "unsent": ["kept"]})

    asyncio.run(scenario())
    assert device.link.unsent == {("s1", H2): ["kept"]}
    # (No chat here: the invariant is the three tests above, on what this feeds.)


# --- 5. What a device can make this instance hold -------------------------------------
def test_invented_sessions_and_turns_do_not_grow_the_link_without_bound(db, registered):
    device = HarnessDevice(db, NEW)
    link = device.link
    many = device_link.MAX_LINK_SESSIONS + 500

    async def scenario():
        for n in range(many):
            await device.emit({"type": "task", "state": "start", "turnId": f"t{n}"},
                              session=f"invented-{n}")
        for n in range(200):
            await device.emit(
                {"type": "task", "state": "end", "turnId": f"u{n}",
                 "unsent": ["x" * (device_link.MAX_UNSENT_CHARS + 50)] * 500},
                session="quiet",
            )
        for n in range(device_link.MAX_HELD_TURNS + 40):
            link.open_buffer((f"held-{n}", "t"))
        link.open_buffer(("big", "t"))
        for _ in range(device_link.MAX_HELD_EVENTS + 100):
            link.hold(("big", "t"), {"type": "text", "text": "x"})
        link.hold(("big", "t"), {"type": "task", "state": "end"})
        await asyncio.sleep(0)

    asyncio.run(scenario())
    limit = device_link.MAX_LINK_SESSIONS
    assert len(link.busy) <= limit and len(link.synced) <= limit and len(link.current) <= limit
    assert len(link.asked) <= limit and len(link.probing) <= device_link.MAX_PROBING
    assert len(link.unsent) <= device_link.MAX_UNSENT_TURNS
    assert all(len(texts) <= device_link.MAX_UNSENT_TEXTS for texts in link.unsent.values())
    assert all(len(t) <= device_link.MAX_UNSENT_CHARS for v in link.unsent.values() for t in v)
    assert len(link.buffered) <= device_link.MAX_HELD_TURNS
    big = link.buffered[("big", "t")]
    assert len(big) == device_link.MAX_HELD_EVENTS + 1 and device_link._ends_task(big[-1])


def test_a_history_that_lists_thousands_of_turns_adds_a_bounded_number_of_rows(
    db, registered, make_chat
):
    device = HarnessDevice(db, NEW)
    chat_id = _device_chat(db, make_chat, device)
    huge = "y" * (device_link.MAX_REPLY_CHARS + 1000)
    turns: list[dict] = []
    for n in range(300):
        turns.append({"role": "user", "text": huge, "turnId": f"invented-{n}", "machine": True})
        turns.append({"role": "assistant", "text": huge, "reasoning": huge,
                      "tools": [{"id": f"c{i}", "name": "read", "args": {}} for i in range(900)]})

    asyncio.run(device_link._take_in_missed(device.link, chat_id, "s1", turns))
    rows = _rows(db, chat_id)
    assert len(rows) == 2 * device_link.MAX_MISSED_TURNS
    assert all(len(r.content) <= device_link.MAX_REPLY_CHARS for r in rows)
    assert all(len(r.content) <= device_link.MAX_NOTE_CHARS for r in rows if r.role == "user")
    assert all(len(r.reasoning or "") <= device_link.MAX_REPLY_CHARS for r in rows)
    assert all(len(r.tool_calls_json or []) <= device_link.MAX_IMPORT_TOOLS for r in rows)

    generation = generation_service.Generation(chat_id=chat_id, message_id="none")
    device.history = turns[:2]
    asyncio.run(
        device_link._import_turn(device.link, generation, "s1", "workstation", "invented-0")
    )
    assert len(generation.text) <= device_link.MAX_REPLY_CHARS
    assert len(generation.tool_events) <= device_link.MAX_IMPORT_TOOLS


def test_the_two_words_for_a_message_of_unknown_fate_are_one(db):
    assert device_link.ORIGIN_UNSURE == generation_service.ORIGIN_UNSURE == "unsure"
    assert device_link.ORIGIN_UNSURE not in device_link.DEVICE_ORIGINS
    assert OLD != NEW
