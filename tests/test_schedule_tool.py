"""Scheduling, as something a model can do.

The bug this closes is not a broken feature; it is an absent one. There was no
scheduling tool, so a model asked to "remind me every Monday" looked down a list
containing `notes_create` ("worth having in a later conversation") and
`memory_add` ("Remember one short, durable fact"), wrote a note, and said it had
scheduled something. Every screen agreed with it.

So the tests here are in two halves: the tool does what the form does, through
the same normaliser; and it is *offered* exactly when it can work, because a
model that cannot see it is back to writing notes.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from lembas.db.models import (
    KIND_TASK,
    ORIGIN_MODEL,
    ROLE_USER,
    TARGET_REPORT,
    Chat,
    Connection,
    Model,
    Schedule,
    User,
)
from lembas.services import settings_store
from lembas.services import tools as tools_service
from lembas.services.crypto import encrypt


@pytest.fixture(autouse=True)
def scheduling_allowed(db, registered):
    """The instance switch on and the permission granted, so these tests are
    about the tools rather than about the gates — which have their own test
    below, asserting both directions."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    settings_store.update(db, {"default_permissions": {"schedule.use": True}})
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(
        Model(
            connection_id=connection.id,
            model_id="test-model",
            capabilities_json={"tools": True},
        )
    )
    db.commit()


def _user(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _chat(db, user) -> Chat:
    chat = Chat(user_id=user.id, title="t", model_id="test-model")
    db.add(chat)
    db.commit()
    return chat


def _context(db, user):
    """Built through `resolve_tools`, not by hand, and that is the point: what
    may be *run* is what was *offered*. `run_tool` consults `context.tools`, so
    a context carrying None would fall back to `REGISTRY` -- which holds the
    import-time built-ins only and has never held these."""
    resolved = tools_service.resolve_tools(db, _chat(db, user), user)
    return tools_service.context_for(db, user, tools=resolved)


async def _run(db, name: str, args: dict):
    return await tools_service.run_tool(_context(db, _user(db)), name, json.dumps(args))


# --- Creating -------------------------------------------------------------------
async def test_a_model_can_schedule_a_calendar_run(db, registered):
    outcome = await _run(
        db,
        "schedule_create",
        {
            "title": "Fun fact",
            "instruction": "Send one random fun fact.",
            "target": TARGET_REPORT,
            "schedule": {"at": {"weekdays": [0], "times": ["12:00"]}},
        },
    )

    schedule = db.scalars(select(Schedule)).one()
    assert schedule.title == "Fun fact"
    assert schedule.target == TARGET_REPORT
    assert schedule.rule_json["at"]["weekdays"] == [0]
    assert schedule.next_fire_at is not None
    # And its chat, made with it -- the first firing may be days away with
    # nobody present to make one.
    assert db.get(Chat, schedule.chat_id).kind == KIND_TASK
    assert outcome.event["status"] == "ok"


async def test_a_model_can_schedule_a_timer(db, registered):
    """"In ten minutes" is `every` with a start, and it is the shape a model is
    likeliest to get wrong -- the first report of this feature failing was
    exactly that request."""
    outcome = await _run(
        db,
        "schedule_create",
        {
            "instruction": "Say something random.",
            "schedule": {"every": {"minutes": 10}, "start": "2099-01-01T00:00:00Z"},
        },
    )

    schedule = db.scalars(select(Schedule)).one()
    assert schedule.rule_json["every"] == {"minutes": 10}
    assert outcome.event["status"] == "ok"


async def test_the_reply_is_told_the_timing_in_words(db, registered):
    """A schedule is invisible until it fires, which may be days away. The one
    moment anybody can check that Monday was understood as Monday is the
    sentence in the reply, so the tool hands it over and says to quote it."""
    outcome = await _run(
        db,
        "schedule_create",
        {
            "instruction": "Check the build.",
            "schedule": {"at": {"weekdays": [0], "times": ["12:00"]}},
        },
    )

    assert "Monday" in outcome.content
    assert "12:00" in outcome.content
    assert "Monday" in outcome.event["detail"]
    # Said plainly enough that a model has no excuse for answering "done".
    assert "in your reply" in outcome.content


async def test_a_model_made_schedule_says_so_on_the_row(db, registered):
    """`ORIGIN_MODEL` has been declared with no writer since the feature
    shipped. It is what lets the Scheduled list say which of these nobody
    typed."""
    await _run(
        db,
        "schedule_create",
        {"instruction": "x", "schedule": {"at": {"times": ["09:00"]}}},
    )
    assert db.scalars(select(Schedule)).one().origin == ORIGIN_MODEL


async def test_a_timing_nothing_could_run_at_is_refused_in_words(db, registered):
    """`rule.validate` empties anything it cannot read, and `create` refuses
    that rather than writing a schedule with no next run -- which would look
    exactly like a working one on every screen it appears on.

    The message matters as much as the refusal: it says what is wrong with the
    timing, which is what makes the model's next attempt different from this
    one rather than a repeat.
    """
    outcome = await _run(
        db, "schedule_create", {"instruction": "x", "schedule": {"whenever": "sometimes"}}
    )

    assert outcome.event["status"] == "error"
    assert "when" in outcome.content.lower()
    assert db.scalars(select(Schedule)).all() == []


async def test_an_instruction_is_required_and_says_why(db, registered):
    """It is read days later by a model that was not here, so an empty one is a
    schedule that fires and does nothing."""
    outcome = await _run(
        db, "schedule_create", {"schedule": {"at": {"times": ["09:00"]}}}
    )

    assert outcome.event["status"] == "error"
    assert db.scalars(select(Schedule)).all() == []


# --- Reading, changing, stopping ------------------------------------------------
async def test_listing_gives_ids_and_timings(db, registered):
    await _run(
        db,
        "schedule_create",
        {"title": "Nightly", "instruction": "x", "schedule": {"at": {"times": ["21:00"]}}},
    )
    schedule = db.scalars(select(Schedule)).one()

    outcome = await _run(db, "schedule_list", {})

    assert schedule.id in outcome.content
    assert "Nightly" in outcome.content
    assert outcome.event["results"][0]["id"] == schedule.id


async def test_listing_nothing_says_so_rather_than_failing(db, registered):
    outcome = await _run(db, "schedule_list", {})
    assert outcome.event["status"] == "ok"
    assert outcome.event["results"] == []


async def test_changing_the_timing_takes_effect(db, registered):
    await _run(
        db, "schedule_create", {"instruction": "x", "schedule": {"at": {"times": ["09:00"]}}}
    )
    schedule = db.scalars(select(Schedule)).one()

    await _run(
        db,
        "schedule_update",
        {"id": schedule.id, "schedule": {"at": {"weekdays": [4], "times": ["17:00"]}}},
    )

    db.refresh(schedule)
    assert schedule.rule_json["at"]["weekdays"] == [4]


async def test_pausing_and_resuming(db, registered):
    await _run(
        db, "schedule_create", {"instruction": "x", "schedule": {"at": {"times": ["09:00"]}}}
    )
    schedule = db.scalars(select(Schedule)).one()

    await _run(db, "schedule_update", {"id": schedule.id, "enabled": False})
    db.refresh(schedule)
    assert not schedule.enabled

    await _run(db, "schedule_update", {"id": schedule.id, "enabled": True})
    db.refresh(schedule)
    assert schedule.enabled


async def test_cancelling_removes_the_schedule_and_keeps_the_transcript(db, registered):
    """Removing a timer must not delete a conversation as a side effect, which
    is `delete`'s own default and the reason this passes `keep_chat`."""
    await _run(
        db, "schedule_create", {"instruction": "x", "schedule": {"at": {"times": ["09:00"]}}}
    )
    schedule = db.scalars(select(Schedule)).one()
    chat_id = schedule.chat_id

    await _run(db, "schedule_cancel", {"id": schedule.id})

    assert db.scalars(select(Schedule)).all() == []
    kept = db.get(Chat, chat_id)
    assert kept is not None
    assert kept.kind != KIND_TASK  # reachable again, rather than in no list at all


async def test_somebody_elses_schedule_is_invisible(db, registered):
    """`schedules.get` takes the user and answers None for a row that is not
    theirs. That is the whole authorisation here, as it is in the routes."""
    from lembas.security.passwords import hash_password

    await _run(
        db, "schedule_create", {"instruction": "x", "schedule": {"at": {"times": ["09:00"]}}}
    )
    schedule = db.scalars(select(Schedule)).one()

    other = User(email="other@example.test", name="Other", password_hash=hash_password("x" * 12))
    db.add(other)
    db.commit()

    resolved = tools_service.resolve_tools(db, _chat(db, other), other)
    context = tools_service.context_for(db, other, tools=resolved)
    listed = await tools_service.run_tool(context, "schedule_list", "{}")
    assert listed.event["results"] == []

    cancelled = await tools_service.run_tool(
        context, "schedule_cancel", json.dumps({"id": schedule.id})
    )
    assert cancelled.event["status"] == "error"
    assert db.scalars(select(Schedule)).all() != []


# --- Being offered at all -------------------------------------------------------
def _offered(db, user) -> set[str]:
    chat = Chat(user_id=user.id, title="t", model_id="test-model")
    db.add(chat)
    db.commit()
    return {tool.name for tool in tools_service.resolve_tools(db, chat, user).defs}


def test_the_tools_are_offered_when_scheduling_is_on(db, registered):
    assert "schedule_create" in _offered(db, _user(db))


def test_nothing_is_offered_when_the_instance_has_scheduling_off(db, registered):
    """An instance with the feature off must not hand out a tool that would
    work — the switch is the administrator's, and a model that could schedule
    round it is the switch not existing."""
    settings_store.update(db, {"enabled": False}, key=settings_store.SCHEDULES)
    db.commit()
    assert "schedule_create" not in _offered(db, _user(db))


def test_nothing_is_offered_without_the_permission(db, registered):
    """`schedule.use`, the same one the pages require: a reader who may not set
    a schedule up by hand may not have a model do it for them."""
    settings_store.update(db, {"default_permissions": {"schedule.use": False}})
    db.commit()
    user = _user(db)
    # An admin resolves to every permission, deliberately -- see
    # `permissions.resolve`. So the gate can only be tested on somebody who is
    # not one, and the first account registered always is.
    user.role = ROLE_USER
    db.commit()
    assert "schedule_create" not in _offered(db, user)


def test_the_guidance_reaches_the_model(db, registered):
    """`harness._families` maps an offered tool's name back to a family through
    `registry(db)`. A tool missing from there is one whose fragment never
    appears — which has cost two features their instructions already, so it is
    asserted rather than assumed."""
    from lembas.services import harness

    user = _user(db)
    chat = Chat(user_id=user.id, title="t", model_id="test-model")
    db.add(chat)
    db.commit()

    offered = [
        {"function": {"name": tool.name}}
        for tool in tools_service.resolve_tools(db, chat, user).defs
    ]
    text = harness.compose(db, user, offered, chat)

    assert "schedule_create" in text
    # And the sentence that stops it reaching for a note instead.
    assert "note" in text.lower()


def test_memory_points_at_scheduling(db, registered):
    """The near-miss descriptions are what the model actually reached for, so
    both say what they are not for. Pinned on the defaults rather than on the
    rendered prompt: an administrator may reword them, and the point is that
    the shipped wording says it."""
    from lembas.services import prompts

    by_key = {fragment.key: fragment for fragment in prompts.BUILTIN}
    memory = by_key["tool.memory"].default

    assert "schedule" in memory


def test_the_list_says_which_ones_nobody_typed(client, db, registered):
    """A schedule is invisible until it fires, so the list is where a model's
    decision is checkable at all. Without the badge, one it set up and one the
    reader wrote are the same row."""
    schedule = Schedule(
        user_id=_user(db).id,
        title="Set up by a model",
        instruction="x",
        rule_json={"at": {"times": ["09:00"]}},
        origin=ORIGIN_MODEL,
        enabled=True,
    )
    db.add(schedule)
    db.commit()

    body = client.get("/scheduled").text

    assert "Set up by a model" in body
    assert "set up for you" in body
