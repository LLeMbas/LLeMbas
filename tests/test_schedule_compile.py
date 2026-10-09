"""Working a plain-language request into a schedule, and running unattended.

The compile is the one place a *model* decides when something happens. So the
tests here are mostly about it failing safely: prose, a fence, a cron string, an
endpoint that is down and a cleared fragment all have to end at the manual form
rather than at a schedule that never fires.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import KIND_TASK, Chat, Schedule, User
from lembas.services import prompts as prompts_service
from lembas.services import settings_store
from lembas.services.llm.openai_client import Endpoint, LLMError
from lembas.services.schedule import compile as compile_service


@pytest.fixture(autouse=True)
def scheduling_allowed(db, registered):
    from lembas.db.models import Connection, Model
    from lembas.services.crypto import encrypt

    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    settings_store.update(db, {"default_permissions": {"schedule.use": True}})
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()
    return None


def _user(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _endpoint() -> Endpoint:
    return Endpoint(base_url="http://127.0.0.1:1/v1", api_key="", extra_headers={})


def _template(db) -> str:
    return prompts_service.resolve(db, "task.schedule_compile")


async def _compile(db, reply: str, monkeypatch, request: str = "every monday at 3"):
    async def answer(endpoint, payload):
        return reply

    monkeypatch.setattr(compile_service, "complete", answer)
    return await compile_service.compile_request(
        _endpoint(), "test-model", request, template=_template(db), user=_user(db)
    )


# --- What it can read -------------------------------------------------------------
@pytest.mark.anyio
async def test_a_plain_json_answer_compiles(client: TestClient, db, registered, monkeypatch):
    compiled = await _compile(
        db,
        json.dumps(
            {
                "title": "Build check",
                "instruction": "Check the build and say what broke.",
                "target": "report",
                "schedule": {"at": {"weekdays": [0], "times": ["15:00"]}},
            }
        ),
        monkeypatch,
    )

    assert compiled.ok is True
    assert compiled.title == "Build check"
    assert compiled.target == "report"
    assert compiled.rule["at"]["times"] == ["15:00"]


@pytest.mark.anyio
async def test_a_fenced_answer_compiles(client: TestClient, db, registered, monkeypatch):
    """Small models fence their JSON however they were trained to. Refusing it
    costs a whole round trip to end up showing the manual form anyway — the
    same reasoning `tools.parse_arguments` already follows."""
    body = json.dumps(
        {"title": "T", "instruction": "I", "schedule": {"at": {"times": ["09:00"]}}}
    )
    compiled = await _compile(
        db, f"Here you go:\n```json\n{body}\n```\nHope that helps!", monkeypatch
    )

    assert compiled.ok is True
    assert compiled.rule["at"]["times"] == ["09:00"]


@pytest.mark.anyio
async def test_a_timer_with_no_start_begins_now(
    client: TestClient, db, registered, monkeypatch
):
    """"Every six hours" is written `{"every": {"hours": 6}}` and nothing else,
    which is the natural reading and cannot fire on its own — a timer measures
    from a start, and `rule.py` has no clock to invent one. Filled in here
    exactly as the manual form does, or the commonest request of all compiles to
    a schedule that never runs."""
    compiled = await _compile(
        db,
        json.dumps({"title": "T", "instruction": "I", "schedule": {"every": {"hours": 6}}}),
        monkeypatch,
        request="every six hours",
    )

    assert compiled.ok is True
    assert compiled.rule["every"] == {"minutes": 360}
    assert compiled.rule["start"]


@pytest.mark.anyio
async def test_thinking_is_stripped_before_parsing(
    client: TestClient, db, registered, monkeypatch
):
    """A model that thinks inline puts its reasoning in `content`, which is the
    field `complete` hands back verbatim — the trap auto-titling hit."""
    body = json.dumps({"title": "T", "instruction": "I", "schedule": {"at": {"times": ["09:00"]}}})
    compiled = await _compile(db, f"<think>Let me work this out…</think>{body}", monkeypatch)

    assert compiled.ok is True
    assert compiled.title == "T"


# --- How it fails ------------------------------------------------------------------
@pytest.mark.anyio
async def test_prose_falls_back_to_the_readers_own_words(
    client: TestClient, db, registered, monkeypatch
):
    """Never a schedule nobody asked for. The reader's words survive so the form
    is filled in rather than blank."""
    compiled = await _compile(db, "Sure! I'd suggest running that weekly.", monkeypatch)

    assert compiled.ok is False
    assert compiled.instruction == "every monday at 3"
    assert compiled.reason


@pytest.mark.anyio
async def test_a_rule_that_normalises_to_nothing_is_not_ok(
    client: TestClient, db, registered, monkeypatch
):
    """The compile's output is model output that becomes a *timer*, and this is
    the reason `rule.validate` had to be total."""
    compiled = await _compile(
        db, json.dumps({"title": "T", "instruction": "I", "schedule": "0 3 * * 1"}), monkeypatch
    )

    assert compiled.ok is False
    assert compiled.rule == {}
    assert "when" in compiled.reason


@pytest.mark.anyio
async def test_a_time_already_past_is_not_ok(client: TestClient, db, registered, monkeypatch):
    compiled = await _compile(
        db,
        json.dumps(
            {"title": "T", "instruction": "I", "schedule": {"start": "2020-01-01T09:00:00Z"}}
        ),
        monkeypatch,
    )

    assert compiled.ok is False
    assert "already passed" in compiled.reason


@pytest.mark.anyio
async def test_an_endpoint_that_is_down_is_not_an_error(
    client: TestClient, db, registered, monkeypatch
):
    async def refuse(endpoint, payload):
        raise LLMError("connection refused")

    monkeypatch.setattr(compile_service, "complete", refuse)
    compiled = await compile_service.compile_request(
        _endpoint(), "test-model", "daily at nine", template=_template(db), user=_user(db)
    )

    assert compiled.ok is False
    assert compiled.instruction == "daily at nine"


@pytest.mark.anyio
async def test_clearing_the_fragment_switches_off_the_compiling_not_the_feature(
    client: TestClient, db, registered, monkeypatch
):
    """`task.compact` set the precedent that clearing a fragment kills a
    feature. Here it must not: the manual form is what makes "an empty override
    means off" safe, and no request is made at all."""
    called = False

    async def answer(endpoint, payload):
        nonlocal called
        called = True
        return "{}"

    monkeypatch.setattr(compile_service, "complete", answer)
    compiled = await compile_service.compile_request(
        _endpoint(), "test-model", "daily at nine", template="", user=_user(db)
    )

    assert called is False
    assert compiled.ok is False
    assert compiled.instruction == "daily at nine"
    assert compiled.reason == ""


def test_the_prompt_carries_the_readers_zone(client: TestClient, db, registered):
    """The model works out "Monday at 3" and the ticker fires it. If they
    disagree about the zone, nothing errors — it simply runs at the wrong time."""
    user = _user(db)
    user.settings_json = {**(user.settings_json or {}), "timezone": "Asia/Tokyo"}
    db.commit()

    prompt = compile_service.render_prompt(
        _template(db), request="every monday at 3", user=user
    )
    assert "Asia/Tokyo" in prompt
    assert "every monday at 3" in prompt


# --- The review step ----------------------------------------------------------------
def test_describing_shows_it_back_rather_than_creating_it(
    client: TestClient, db, registered, monkeypatch
):
    """A timing a model chose and nobody looked at is exactly the standing
    instruction this codebase refuses to create silently elsewhere."""

    async def answer(endpoint, payload):
        return json.dumps(
            {
                "title": "Build check",
                "instruction": "Check the build.",
                "schedule": {"at": {"weekdays": [0], "times": ["15:00"]}},
            }
        )

    monkeypatch.setattr(compile_service, "complete", answer)

    response = client.post("/api/schedules/describe", data={"request": "mondays at 3"})

    assert response.status_code == 200
    assert "Every Monday at 15:00" in response.text
    assert "Build check" in response.text
    # Shown, not saved.
    assert db.scalars(select(Schedule)).all() == []


def test_describing_with_no_model_configured_still_answers(
    client: TestClient, db, registered
):
    from lembas.db.models import Connection

    for connection in db.scalars(select(Connection)):
        db.delete(connection)
    db.commit()

    response = client.post("/api/schedules/describe", data={"request": "mondays at 3"})

    assert response.status_code == 200
    assert "fill it in yourself" in response.text


# --- Unattended ---------------------------------------------------------------------
def test_ask_user_is_not_offered_in_a_task_chat(client: TestClient, db, registered):
    """Enforced in `resolve_tools`, not merely discouraged in the prompt.

    A parked `ask_user` holds the reply for the whole `approval_timeout` with
    nobody there to answer — a run that silently does nothing for fifteen
    minutes and then gives up. A rule living only in a system message is one a
    page the model just read can argue with.
    """
    from lembas.db.models import Model
    from lembas.services import tools as tools_service

    model = db.scalars(select(Model)).one()
    model.capabilities_json = {"tools": True, "tool_ask": True}
    db.commit()
    settings_store.update(db, {"default_permissions": {"tools.ask": True}})

    ordinary = Chat(user_id=_user(db).id, model_id="test-model")
    task = Chat(user_id=_user(db).id, model_id="test-model", kind=KIND_TASK)
    db.add_all([ordinary, task])
    db.commit()

    offered = {t.name for t in tools_service.resolve_tools(db, ordinary, _user(db)).defs}
    assert "ask_user" in offered

    withdrawn = {t.name for t in tools_service.resolve_tools(db, task, _user(db)).defs}
    assert "ask_user" not in withdrawn


def test_a_task_chat_is_told_what_it_is_for(client: TestClient, db, registered, monkeypatch):
    """A task chat accumulates every run, so by the tenth the instruction is far
    out of sight up the transcript."""
    from lembas.services import harness
    from lembas.services import schedules as schedules_service

    schedule = schedules_service.create(
        db,
        owner=_user(db),
        title="Build check",
        instruction="Check the build and say what broke.",
        rule={"at": {"weekdays": [0], "times": ["15:00"]}},
    )
    chat = db.get(Chat, schedule.chat_id)

    values = harness.context_variables(db, _user(db), [], chat)
    assert values["schedule_instruction"] == "Check the build and say what broke."
    assert values["schedule_summary"] == "Every Monday at 15:00"

    block = harness.compose(db, _user(db), [], chat)
    assert "nobody is necessarily reading it" in block
    assert "Check the build and say what broke." in block


def test_an_ordinary_chat_is_told_none_of_it(client: TestClient, db, registered):
    """`core.unattended` and `context.schedule` are gated on the same variable,
    so the warning cannot appear without the thing it warns about."""
    from lembas.services import harness

    chat = Chat(user_id=_user(db).id, model_id="test-model")
    db.add(chat)
    db.commit()

    block = harness.compose(db, _user(db), [], chat)
    assert "nobody is necessarily reading" not in block
    assert "This scheduled task" not in block


@pytest.mark.anyio
async def test_an_unusable_reply_is_asked_again_once(
    client: TestClient, db, registered, monkeypatch
):
    """Measured against a 4B model: the prompt is sound — ten realistic requests
    compiled ten times over, twice — but roughly one call in six came back empty
    or truncated, which a local runner swapping models under the request will
    do. One retry costs a second on a screen somebody is already waiting at."""
    replies = ["", json.dumps({"title": "T", "instruction": "I",
                               "schedule": {"at": {"times": ["09:00"]}}})]
    calls = 0

    async def answer(endpoint, payload):
        nonlocal calls
        calls += 1
        return replies.pop(0)

    monkeypatch.setattr(compile_service, "complete", answer)
    compiled = await compile_service.compile_request(
        _endpoint(), "test-model", "daily at nine", template=_template(db), user=_user(db)
    )

    assert calls == 2
    assert compiled.ok is True


@pytest.mark.anyio
async def test_it_gives_up_after_the_second_try(
    client: TestClient, db, registered, monkeypatch
):
    calls = 0

    async def answer(endpoint, payload):
        nonlocal calls
        calls += 1
        return "I'd suggest weekly."

    monkeypatch.setattr(compile_service, "complete", answer)
    compiled = await compile_service.compile_request(
        _endpoint(), "test-model", "daily at nine", template=_template(db), user=_user(db)
    )

    assert calls == 2
    assert compiled.ok is False
    assert compiled.instruction == "daily at nine"


@pytest.mark.anyio
async def test_an_endpoint_that_refuses_is_not_asked_twice(
    client: TestClient, db, registered, monkeypatch
):
    """It will refuse again, and the reader is better served by the form than by
    waiting twice for the same answer."""
    calls = 0

    async def refuse(endpoint, payload):
        nonlocal calls
        calls += 1
        raise LLMError("connection refused")

    monkeypatch.setattr(compile_service, "complete", refuse)
    await compile_service.compile_request(
        _endpoint(), "test-model", "daily at nine", template=_template(db), user=_user(db)
    )

    assert calls == 1


def test_the_weekday_numbering_is_spelled_out(client: TestClient, db, registered):
    """The one mistake a small model actually made in the audit: "every other
    tuesday" came back as Wednesday. Naming the wrong day is the error here that
    still looks like a working schedule, so the mapping is written out rather
    than left as "0-6, Monday is 0"."""
    template = _template(db)

    for day, number in (("Monday", 0), ("Wednesday", 2), ("Sunday", 6)):
        assert f"{day}={number}" in template
