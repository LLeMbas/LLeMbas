"""A schedule's run in a chat on this server, with a team: the run is not over
while its members work (harness spec 3.10.0, *The run waits for its
subagents*). The lead's first reply is an interim answer; each report wakes
the lead with another reply, and the run's answer is the last of them.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from lembas.db.models import KIND_TASK, Chat, Message, ScheduleRun, TeamAgent, User
from lembas.services import generation as generation_service
from lembas.services import schedules as schedules_service
from lembas.services import team as rules
from lembas.services.schedule import runner, runs


def _run_with_a_member(db, state=None):
    owner = db.scalar(select(User))
    schedule = schedules_service.create(
        db, owner=owner, title="Audit", instruction="Audit the loader.",
        rule={"at": {"times": ["09:00"]}},
    )
    task = Chat(user_id=owner.id, kind=KIND_TASK, title="run")
    work = Chat(user_id=owner.id, kind=KIND_TASK, title="scout", parent_chat_id=None)
    db.add_all([task, work])
    db.commit()
    first = Message(chat_id=task.id, role="assistant", content="I have asked scout to look.",
                    complete=True)
    member = TeamAgent(lead_chat_id=task.id, name="scout", chat_id=work.id,
                       state=state or rules.WORKING)
    db.add_all([first, member])
    db.commit()
    run = ScheduleRun(schedule_id=schedule.id, user_id=owner.id, status="running",
                      chat_id=task.id, message_id=first.id)
    db.add(run)
    db.commit()
    return run.id, task.id, work.id, first.id, member.id


def _clock(monkeypatch):
    """Time that moves only when the follower sleeps: three seconds a look."""
    now = [0.0]
    looks: list[float] = []

    async def sleep(seconds):
        now[0] += seconds
        looks.append(now[0])
        await asyncio.sleep(0)

    monkeypatch.setattr(runner, "_sleep", sleep)
    monkeypatch.setattr(runner, "_monotonic", lambda: now[0])
    return now, looks


def test_a_run_whose_members_still_work_is_settled_on_the_leads_last_reply(
    db, registered, monkeypatch
):
    run_id, task_id, work_id, first_id, member_id = _run_with_a_member(db)
    now, looks = _clock(monkeypatch)
    settled: list[tuple[str, str]] = []

    async def settle(run_id_, generation, *, timed_out=False, chat_id=""):
        with generation_service.session_scope() as other:
            settled.append((other.get(ScheduleRun, run_id_).message_id, str(timed_out)))

    monkeypatch.setattr(runner, "settle", settle)
    # The member writes for twenty minutes -- twice the inactivity limit --
    # and then reports: the lead is woken with a second reply.
    working = generation_service.Generation(chat_id=work_id, message_id="member-reply")
    monkeypatch.setitem(generation_service._RUNNING, "member-reply", working)
    final: list[str] = []

    async def scenario():
        following = asyncio.create_task(runner.follow(run_id))
        # (Bounded by the follower itself: a run settled early ends the wait.)
        while now[0] < 2 * runs.INACTIVITY_SECONDS and not following.done():
            working.touch()
            await asyncio.sleep(0)
        assert not following.done() and not settled, "settled on the interim reply"
        with generation_service.session_scope() as other:
            other.get(TeamAgent, member_id).state = rules.IDLE
            reply = Message(chat_id=task_id, role="assistant", complete=True,
                            content="Scout found it: the loader is in src/load.py.")
            other.add(reply)
            other.commit()
            final.append(reply.id)
        working.done = True
        await asyncio.wait_for(following, 5)

    asyncio.run(scenario())
    assert settled == [(final[0], "False")]


def test_a_run_where_nobody_writes_is_still_stopped_by_the_inactivity_limit(
    db, registered, monkeypatch
):
    run_id, task_id, work_id, first_id, member_id = _run_with_a_member(db)
    now, looks = _clock(monkeypatch)
    settled: list[bool] = []

    async def settle(run_id_, generation, *, timed_out=False, chat_id=""):
        settled.append(timed_out)

    monkeypatch.setattr(runner, "settle", settle)
    asyncio.run(asyncio.wait_for(runner.follow(run_id), 20))
    assert settled == [True]
    assert runs.INACTIVITY_SECONDS < now[0] < runs.INACTIVITY_SECONDS + 60


def test_a_run_without_a_team_settles_on_its_reply_as_before(db, registered, monkeypatch):
    run_id, task_id, work_id, first_id, member_id = _run_with_a_member(db, state=rules.IDLE)
    _clock(monkeypatch)
    settled: list[bool] = []

    async def settle(run_id_, generation, *, timed_out=False, chat_id=""):
        settled.append(timed_out)

    monkeypatch.setattr(runner, "settle", settle)
    asyncio.run(asyncio.wait_for(runner.follow(run_id), 5))
    assert settled == [False]
    assert db.get(ScheduleRun, run_id).message_id == first_id
