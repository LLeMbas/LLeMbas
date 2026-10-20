"""A helper as a background job (spec 2.0.0), over the hidden chat it runs in.

`task background: true` returns at once with a job id; `job_list`, `job_output`,
`job_stop` and `job_send` work on that id across requests; `report` lets the
helper speak to the main agent before its final reply; the parent is woken with
the spec's note when the helper ends. The generation loop is stubbed as
`test_subagent.py` stubs it -- what is under test is the row, the wake and the
words, not the model.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from lembas.db.models import (
    JOB_RUNNING,
    JOB_STOPPED,
    ROLE_ASSISTANT,
    ROLE_USER,
    Chat,
    ChatJob,
    Connection,
    Message,
    Model,
    User,
)
from lembas.db.session import session_scope
from lembas.services import generation as generation_service
from lembas.services import harness, settings_store
from lembas.services import jobs as jobs_service
from lembas.services import subagent as subagent_service
from lembas.services import tools as tools_service
from lembas.services.agent import policy as agent_policy
from lembas.services.crypto import encrypt

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def delegation_allowed(db, registered):
    settings_store.update(db, {"enabled": True}, key=settings_store.SUBAGENTS)
    settings_store.update(db, {"default_permissions": {"tools.subagent": True}})
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(
        Model(connection_id=connection.id, model_id="test-model", capabilities_json={"tools": True})
    )
    db.commit()
    subagent_service.clear()
    yield
    subagent_service.clear()


def _user(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _chat(db, **kwargs) -> Chat:
    chat = Chat(user_id=_user(db).id, title="t", model_id="test-model", **kwargs)
    db.add(chat)
    db.commit()
    return chat


class _Parent:
    """The parent's `Generation`, as far as the helper reads it: the count of
    helpers it has spawned, its Stop flag, and the jobs it shows."""

    def __init__(self):
        self.subagents = 0
        self.cancel = False
        self.jobs: dict = {}
        self.version = 0

    def touch(self):
        self.version += 1


def _fake_wake(monkeypatch, *, answer: str = "Found it."):
    """`wake_chat` that answers a helper's chat at once and records every wake
    -- the helper's own, and the parent's when the note comes back."""
    from lembas.services import chat as chat_service

    wakes: list[tuple[str, str]] = []

    async def fake_wake(chat_id: str, content: str, *, model_id: str = "") -> str:
        wakes.append((chat_id, content))
        with session_scope() as db:
            chat = db.get(Chat, chat_id)
            if chat is None:
                return ""
            if chat.parent_chat_id:
                chat_service.create_message(db, chat, ROLE_USER, content, machine=True)
                reply = chat_service.create_message(db, chat, ROLE_ASSISTANT, answer)
                reply.tool_calls_json = [
                    {"name": "web_search", "status": "ok", "query": "loader", "args": {}}
                ]
                db.commit()
                return reply.id
            # The parent: the note is queued or starts a reply -- here only
            # recorded, so the parent's rows are not touched.
            return "parent-reply"

    monkeypatch.setattr("lembas.services.wake.wake_chat", fake_wake)
    monkeypatch.setattr("lembas.services.generation.running_for", lambda chat_id: None)
    return wakes


async def _call(db, chat: Chat, name: str, args: dict, *, parent: _Parent | None = None):
    """Through `resolve_tools`, as the loop does: what may be run is what was
    offered. The parent's `Generation` is what `running_for` hands back for
    the parent's chat."""
    user = _user(db)
    resolved = tools_service.resolve_tools(db, chat, user)
    context = tools_service.context_for(db, user, chat, tools=resolved)
    fake = parent if parent is not None else _Parent()
    original = generation_service.running_for

    def running_for(chat_id):
        return fake if chat_id == chat.id else original(chat_id)

    generation_service.running_for = running_for
    try:
        return await tools_service.run_tool(context, name, json.dumps(args))
    finally:
        generation_service.running_for = original


async def _settled():
    """Let the watchers of background jobs run to their end."""
    for _ in range(20):
        await asyncio.sleep(0)
    if subagent_service._WATCHERS:
        await asyncio.gather(*list(subagent_service._WATCHERS), return_exceptions=True)


# --- The tools a chat gets --------------------------------------------------------
def test_an_ordinary_chat_gets_the_job_tools_and_a_helper_gets_report_alone(db):
    user = _user(db)
    parent = _chat(db)
    offered = {t.name for t in tools_service.resolve_tools(db, parent, user).defs}
    assert {"task", "job_list", "job_output", "job_stop", "job_send"} <= offered
    assert "report" not in offered

    child = _chat(db, parent_chat_id=parent.id, unattended=True)
    offered = {t.name for t in tools_service.resolve_tools(db, child, user).defs}
    assert "report" in offered
    assert not {"task", "job_list", "job_send", "ask_user"} & offered


def test_interact_is_not_withheld_in_plan_mode_and_asks_in_no_mode(db):
    """The spec's `interact` risk: a message to somebody, not a change to
    anything. `task` used to be classed `read`; classed `write` it would have
    put a card in front of every research fan-out."""
    user = _user(db)
    planning = _chat(db, agent_mode="plan")
    assert "task" in {t.name for t in tools_service.resolve_tools(db, planning, user).defs}
    for mode in agent_policy.MODES:
        decision = agent_policy.decide(
            mode=mode, risk=tools_service.RISK_INTERACT, tool_name="task", command=""
        )
        assert decision.verdict == agent_policy.ALLOW, mode


def test_purpose_is_offered_where_the_spec_says(db):
    """`purpose` (purpose.json) is in `task`'s parameters and not in a tool
    whose spec says it takes none."""
    assert "purpose" in tools_service.spec_parameters("task")["properties"]
    assert "purpose" not in tools_service.spec_parameters("job_list")["properties"]


async def test_purpose_is_stripped_before_the_runner_and_shown_on_the_event(db):
    """A call carrying `purpose` has it taken off before the runner -- the tool
    never sees it -- and kept on the event, beside the call in the transcript."""
    seen: dict = {}

    async def run(context, args):
        seen.update(args)
        return tools_service.ToolOutcome("ok", {"name": "web_fetch", "status": "ok"})

    book = {
        "web_fetch": tools_service.ToolDef(
            name="web_fetch", family="fetch", description="", parameters={}, run=run
        )
    }
    context = tools_service.ToolContext(owner_id="u", tools=book)
    outcome = await tools_service.run_tool(
        context, "web_fetch", "", parsed={"url": "https://x", "purpose": "See the docs"}
    )
    assert seen == {"url": "https://x"}
    assert outcome.event["purpose"] == "See the docs"


def test_the_agents_variable_names_the_two_kinds(db):
    agents = tools_service.SPEC_VARIABLES["agents"]
    assert agents.startswith("explore — ")
    assert "; general — " in agents
    assert "task" in tools_service.spec_description("task") or "explore" in (
        tools_service.spec_description("task")
    )


# --- A background task -------------------------------------------------------------
async def test_a_background_task_returns_a_job_id_at_once_and_the_parent_is_woken(
    db, monkeypatch
):
    wakes = _fake_wake(monkeypatch, answer="The loader is in src/load.py.")
    parent = _chat(db)
    gen = _Parent()

    outcome = await _call(
        db,
        parent,
        "task",
        {"description": "find the loader", "prompt": "Find the loader.", "background": True},
        parent=gen,
    )

    assert outcome.event["status"] == "ok"
    assert outcome.event["job"] == "job1" and outcome.event["background"] is True
    assert "Started subagent explore as job1" in outcome.content
    # The row, and the strip's view of it on the parent's reply.
    job = jobs_service.by_id(db, parent.id, "job1")
    assert job is not None and job.agent == "explore"
    assert gen.jobs["job1"]["state"] in ("running", "done")

    await _settled()
    db.expire_all()
    job = jobs_service.by_id(db, parent.id, "job1")
    assert job.state == "done"
    assert job.data_json["report"] == "The loader is in src/load.py."
    # The parent was woken with the spec's note, mark first.
    parent_wakes = [text for chat_id, text in wakes if chat_id == parent.id]
    assert len(parent_wakes) == 1
    note = parent_wakes[0]
    assert note.startswith(jobs_service.marks()["subagent"])
    assert "job1 (explore: find the loader) finished after" in note
    assert "The loader is in src/load.py." in note
    assert "[explore subagent · " in note
    # The helper's chat is kept: `job_send` may start it again.
    assert db.get(Chat, job.child_chat_id) is not None


async def test_job_list_output_and_send_work_on_the_id_across_requests(db, monkeypatch):
    wakes = _fake_wake(monkeypatch, answer="Half way.")
    parent = _chat(db)
    await _call(
        db, parent, "task", {"description": "read the logs", "prompt": "Read.", "background": True}
    )
    await _settled()

    listed = await _call(db, parent, "job_list", {})
    assert "job1" in listed.content and "explore: read the logs" in listed.content
    assert "done" in listed.content

    output = await _call(db, parent, "job_output", {"id": "job1"})
    assert "job1 is done." in output.content
    assert "→ Web search loader" in output.content
    assert "Half way." in output.content
    again = await _call(db, parent, "job_output", {"id": "job1"})
    assert "Nothing new since you last looked." in again.content

    missing = await _call(db, parent, "job_output", {"id": "job9"})
    assert missing.event["status"] == "error" and "job_list" in missing.content

    # A message to a finished helper starts it again, under the same id, with
    # the spec's mark in front of the words.
    sent = await _call(db, parent, "job_send", {"id": "job1", "text": "Also check the dates."})
    assert "started again" in sent.content
    child_id = jobs_service.by_id(db, parent.id, "job1").child_chat_id
    child_wakes = [text for chat_id, text in wakes if chat_id == child_id]
    assert child_wakes[-1].startswith(jobs_service.marks()["to_subagent"])
    assert child_wakes[-1].endswith("Also check the dates.")
    await _settled()
    db.expire_all()
    job = jobs_service.by_id(db, parent.id, "job1")
    assert job.data_json["runs"] == 2 and job.state == "done"
    # And the parent got a second note.
    assert len([t for c, t in wakes if c == parent.id]) == 2


async def test_report_from_the_helper_reaches_the_parent_as_a_marked_message(db, monkeypatch):
    wakes = _fake_wake(monkeypatch)
    parent = _chat(db)
    started = {"description": "find the loader", "prompt": "Find.", "background": True}
    await _call(db, parent, "task", started)
    await _settled()
    child = db.get(Chat, jobs_service.by_id(db, parent.id, "job1").child_chat_id)

    outcome = await _call(db, child, "report", {"text": "There are two loaders; which one?"})

    assert outcome.event["status"] == "ok"
    note = [t for c, t in wakes if c == parent.id][-1]
    assert note == (
        jobs_service.marks()["message"]
        + "\njob1 (explore: find the loader):\nThere are two loaders; which one?"
    )
    # Only a helper has it to call.
    refused = await tools_service.run_tool(
        tools_service.context_for(
            db, _user(db), parent,
            tools=tools_service.ToolSet(tuple(subagent_service.child_tool_defs())),
        ),
        "report",
        json.dumps({"text": "x"}),
    )
    assert refused.event["status"] == "error"


async def test_job_stop_stops_the_helper_where_it_is(db, monkeypatch):
    """The child's running reply is asked to stop (`request_stop`), and what it
    had worked out comes back as its report, state `stopped`."""
    _fake_wake(monkeypatch, answer="Partial.")
    parent = _chat(db)
    await _call(
        db, parent, "task", {"description": "read the logs", "prompt": "Read.", "background": True}
    )
    # Nothing finished yet: the child "runs" until stopped.
    job = jobs_service.by_id(db, parent.id, "job1")
    child_id = job.child_chat_id
    running = {child_id: generation_service.Generation(chat_id=child_id, message_id="m-child")}
    stopped: list[str] = []
    monkeypatch.setattr(
        "lembas.services.generation.running_for", lambda chat_id: running.get(chat_id)
    )

    def request_stop(message_id):
        stopped.append(message_id)
        running.clear()
        return True

    monkeypatch.setattr("lembas.services.generation.request_stop", request_stop)
    monkeypatch.setattr(subagent_service, "POLL_SECONDS", 0.0)

    outcome = await _call(db, parent, "job_stop", {"id": "job1"})

    assert stopped == ["m-child"]
    assert "stopped" in outcome.content
    await _settled()
    db.expire_all()
    job = jobs_service.by_id(db, parent.id, "job1")
    assert job.state == JOB_STOPPED
    assert job.data_json["reason"] == "stopped"


async def test_stopping_the_parent_stops_the_helper_it_waits_on(db, monkeypatch):
    """Stop never reached a helper: the parent's `cancel` was read only between
    streamed chunks, and a reply waiting on `task` streams nothing. The wait
    watches the flag now."""
    _fake_wake(monkeypatch, answer="Partial.")
    parent = _chat(db)
    gen = _Parent()
    stopped: list[str] = []
    child_running = generation_service.Generation(chat_id="?", message_id="m-child")

    def running_for(chat_id):
        if chat_id == parent.id:
            return gen
        return child_running if child_running.chat_id == chat_id and not stopped else None

    def request_stop(message_id):
        stopped.append(message_id)
        return True

    monkeypatch.setattr("lembas.services.generation.running_for", running_for)
    monkeypatch.setattr("lembas.services.generation.request_stop", request_stop)
    monkeypatch.setattr(subagent_service, "POLL_SECONDS", 0.0)

    async def wait_then_cancel(chat_id, deadline, parent_generation=None):
        child_running.chat_id = chat_id
        gen.cancel = True
        return await original_wait(chat_id, deadline, parent_generation)

    original_wait = subagent_service._await_reply
    monkeypatch.setattr(subagent_service, "_await_reply", wait_then_cancel)

    outcome = await _call(db, parent, "task", {"description": "d", "prompt": "p"}, parent=gen)

    assert stopped == ["m-child"]
    assert outcome.event["status"] == "stopped"
    assert "It was stopped" in outcome.content


async def test_a_background_job_outlives_the_parents_stop(db, monkeypatch):
    """Esc in the CLI does not kill background subagents, and Stop here does
    not either: `stop_helpers_of` reaches the synchronous ones alone."""
    _fake_wake(monkeypatch)
    parent = _chat(db)
    with session_scope() as db2:
        chat = db2.get(Chat, parent.id)
        child = Chat(user_id=chat.user_id, title="h", model_id="test-model",
                     parent_chat_id=chat.id, unattended=True, temporary=True)
        db2.add(child)
        db2.commit()
        jobs_service.create(db2, parent.id, child_chat_id=child.id, description="bg",
                            agent="explore", model="Test/test-model", message_id="m1",
                            background=True)
        jobs_service.create(db2, parent.id, child_chat_id=child.id, description="sync",
                            agent="explore", model="Test/test-model", message_id="m2",
                            background=False)
    stopped: list[str] = []
    monkeypatch.setattr(
        subagent_service, "_stop", lambda chat_id: _record(stopped, chat_id)
    )

    told = await subagent_service.stop_helpers_of(parent.id)

    assert told == 1 and len(stopped) == 1


async def _record(into: list, value):
    into.append(value)


# --- The fixes --------------------------------------------------------------------------
async def test_resume_skips_a_helpers_chat(db, monkeypatch):
    """A helper's reply cut off by a shutdown has nobody waiting for it any
    more; resuming it spent the endpoint on work nothing read."""
    from lembas.services import chat as chat_service

    parent = _chat(db)
    child = _chat(db, parent_chat_id=parent.id, unattended=True)
    for chat in (parent, child):
        chat_service.create_message(db, chat, ROLE_USER, "q")
        reply = chat_service.create_message(db, chat, ROLE_ASSISTANT, "", complete_=False)
        reply.interrupted = True
    db.commit()
    taken: list[str] = []
    monkeypatch.setattr(
        generation_service, "ensure", lambda chat_id, message_id, **kw: taken.append(chat_id)
    )

    count = await generation_service.resume_interrupted(delay=0)

    assert count == 1 and taken == [parent.id]
    db.expire_all()
    assert all(not m.interrupted for m in db.scalars(select(Message)))


def test_the_helpers_token_ceiling_narrows_the_loops_budget():
    """`max_completion_tokens` was a setting nothing read."""
    budgets = {"wall_seconds": 900, "output_bytes": 1, "completion_tokens": 200_000, "nudges": 2}
    assert generation_service.helper_budgets(budgets, {"max_completion_tokens": 60_000})[
        "completion_tokens"
    ] == 60_000
    # A ceiling never widens, and zero is no ceiling of its own.
    assert generation_service.helper_budgets(budgets, {"max_completion_tokens": 900_000})[
        "completion_tokens"
    ] == 200_000
    assert generation_service.helper_budgets(budgets, {"max_completion_tokens": 0}) == budgets
    assert generation_service.helper_budgets(
        {**budgets, "completion_tokens": 0}, {"max_completion_tokens": 5}
    )["completion_tokens"] == 5


def test_a_stopped_helpers_block_says_so_and_shows_its_account():
    from lembas.web.templating import templates

    html = templates.get_template("chat/_tool_activity.html").render(
        {
            "tool_events": [
                {
                    "name": "task",
                    "status": "stopped",
                    "query": "read the logs",
                    "detail": "job2 · explore · 4 tool call(s), 600s, stopped at the time limit",
                    "text": "Partial.",
                    "children": [{"name": "grep", "title": "error", "isError": False}],
                }
            ],
            "message_id": "m",
            "step_index": 0,
        }
    )
    assert "stopped at the time limit" in html
    assert 'class="tool-chip tool-chip--bad">stopped' in html
    assert "tool-line--child" in html and ">error<" in html
    assert "tool-activity--error" not in html


def test_capacity_counts_the_connection_and_the_running_helpers(db):
    """`one_model_at_a_time` was never read, and helpers were only ever
    compared with the parent."""
    connection = db.scalar(select(Connection))
    connection.one_model_at_a_time = True
    other = Model(connection_id=connection.id, model_id="other", capabilities_json={"tools": True})
    db.add(other)
    db.commit()
    parent = _chat(db)

    # Another model of a connection that holds one at a time: refused beside
    # the parent's own.
    assert "one model at a time" in subagent_service.capacity_refusal(db, parent, other)
    # The parent's own model is fine on its own ...
    assert subagent_service.capacity_refusal(db, parent) == ""
    # ... until a helper runs on the other model: then helper vs helper.
    connection.one_model_at_a_time = False
    db.commit()
    assert subagent_service.capacity_refusal(db, parent, other) == ""
    connection.one_model_at_a_time = True
    db.commit()
    jobs_service.create(db, parent.id, child_chat_id="x", description="d", agent="explore",
                        model="Test/other", message_id="m", background=True,
                        model_id="other", connection_id=connection.id)
    assert "the helper job1" in subagent_service.capacity_refusal(db, parent)


def test_the_helper_reads_the_specs_preamble_filled_and_the_parent_the_model_list(db):
    user = _user(db)
    parent = _chat(db)
    child = _chat(db, parent_chat_id=parent.id, unattended=True,
                  scope_json={"write": False, "agent": "explore"})
    offered = tools_service.resolve_tools(db, child, user).schemas
    values = harness.context_variables(db, user, offered, child)
    assert values["name"] == "explore"
    assert values["instructions"].startswith("You can read, search and fetch")
    assert values["report"] == subagent_service.REPORT_SENTENCE
    assert values["read_only"] == subagent_service.READ_ONLY_SENTENCE
    # A helper has no plan tools and nobody to ask: the two bullets go.
    assert values["plan_tools"] == "" and values["ask_tool"] == ""
    block = harness.compose(db, user, offered, child)
    assert "plan_write" not in block and "ask_user" not in block
    assert 'You are the "explore" subagent' in block

    offered = tools_service.resolve_tools(db, parent, user).schemas
    values = harness.context_variables(db, user, offered, parent)
    assert values["plan_tools"] == harness.PLAN_TOOLS
    assert values["ask_tool"] == harness.ASK_TOOL
    assert values["helper_models"] == "Test/test-model"
    block = harness.compose(db, user, offered, parent)
    assert "Models a helper may run on" in block and "Test/test-model" in block


async def test_a_general_helper_writes_and_an_explore_helper_does_not(db, monkeypatch):
    _fake_wake(monkeypatch)
    settings_store.update(
        db, {"default_permissions": {"tools.subagent": True, "tools.notes": True}}
    )
    settings_store.update(db, {"keep_transcript": True}, key=settings_store.SUBAGENTS)
    parent = _chat(db)

    async def child_tools(agent: str) -> set[str]:
        await _call(db, parent, "task", {"description": agent, "prompt": "p", "agent": agent})
        child_id = [j for j in jobs_service.for_chat(db, parent.id) if j.description == agent][
            0
        ].child_chat_id
        child = db.get(Chat, child_id)
        return {t.name for t in tools_service.resolve_tools(db, child, _user(db)).defs}

    assert "note_manage" not in await child_tools("explore")
    assert "note_manage" in await child_tools("general")


def test_the_strip_lists_a_native_chats_running_helper(client, db, registered):
    parent = _chat(db)
    with session_scope() as db2:
        child = Chat(user_id=parent.user_id, title="h", model_id="test-model",
                     parent_chat_id=parent.id, unattended=True, temporary=True)
        db2.add(child)
        db2.commit()
        jobs_service.create(db2, parent.id, child_chat_id=child.id, description="find the loader",
                            agent="explore", model="Test/test-model", message_id="m1",
                            background=True)
    page = client.get(f"/api/chats/{parent.id}/jobs")
    assert "job1" in page.text and "find the loader" in page.text
    assert f'hx-get="/api/chats/{parent.id}/jobs/job1"' in page.text
    panel = client.get(f"/api/chats/{parent.id}/jobs/job1")
    assert panel.status_code == 200 and "jobpanel__send" in panel.text
    assert client.get(f"/chat/{parent.id}").text.count('id="jobs-strip"') == 1


def test_the_job_row_is_additive(db):
    """A new table and nothing else: an existing row's shape is untouched."""
    assert ChatJob.__tablename__ == "chat_jobs"
    assert jobs_service.next_job_id(db, "nowhere") == "job1"
    assert JOB_RUNNING == "running"
