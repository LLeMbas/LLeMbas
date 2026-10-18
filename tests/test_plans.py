"""The plan: one structure every chat keeps (services/plans.py), the tools
that write it, Plan mode for an ordinary chat, and the loop's budgets -- what
harness spec 1.1.0 brought."""

from __future__ import annotations

import asyncio
import json

from lembas.db.models import ROLE_ASSISTANT, Chat, Connection, Message, Model, User
from lembas.services import generation as generation_service
from lembas.services import harness, interaction, plans, settings_store
from lembas.services import tools as tools_service

PLAN = {
    "title": "Fix the login bug",
    "summary": "Two changes.",
    "findings": ["Session cookie is not renewed"],
    "objectives": ["Login works"],
    "phases": [
        {"title": "Code", "tasks": ["Renew the cookie", "Add the test"]},
        {"title": "Verify", "tasks": ["Run the suite"]},
    ],
}


def _chat_with_tools(db, user_id, **extra):
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id, **extra)
    db.add(chat)
    db.commit()
    return chat


def _reply(db, chat):
    db.add(Message(chat_id=chat.id, role="user", content="Fix the login bug.", complete=True))
    db.commit()
    assistant = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(assistant)
    db.commit()
    return assistant.id


def _context(db, user, chat):
    return tools_service.context_for(
        db, user, chat, tools=tools_service.resolve_tools(db, chat, user)
    )


def _call_chunk(name: str, arguments: dict, call_id: str = "c1") -> dict:
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ]
                }
            }
        ]
    }


def _text_chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


def _stub_stream(rounds, seen):
    async def stream_chat(_endpoint, payload):
        seen.append(payload)
        for chunk in rounds[min(len(seen) - 1, len(rounds) - 1)]:
            yield chunk

    return stream_chat


# --- The structure ---------------------------------------------------------------
def test_render_carries_the_ids_and_a_mark_per_task():
    text = plans.render(plans.normalise(PLAN))
    assert text.startswith("# Fix the login bug\nTwo changes.")
    assert "Findings: [f1] Session cookie is not renewed" in text
    assert "Objectives: [o1] Login works (open)" in text
    assert "## p1 Code (pending)\n[t1] [ ] Renew the cookie\n[t2] [ ] Add the test" in text
    assert "## p2 Verify (pending)\n[t3] [ ] Run the suite" in text


def test_a_stored_plan_keeps_the_ids_apply_gave_it():
    """`apply` inserts `t4` after `t1` in the first phase. Reading the stored
    plan back must not renumber it: the model is quoting those ids, and a
    renumbering would tick a different task than the one it named."""
    changed = plans.apply(
        plans.normalise(PLAN), {"add_tasks": [{"phase": "p1", "text": "Zero", "after": "t1"}]}
    )["plan"]
    assert [t["id"] for t in changed["phases"][0]["tasks"]] == ["t1", "t4", "t2"]
    assert plans.normalise(changed) == changed
    # A model's own ids, on the other hand, are never kept.
    fresh = plans.normalise({"title": "x", "phases": [{"tasks": [{"id": "t9", "text": "a"}]}]})
    assert fresh["phases"][0]["tasks"][0]["id"] == "t1"


def test_summary_lines_show_what_is_in_progress_then_what_is_left_then_the_last_done():
    plan = plans.apply(
        plans.normalise(PLAN),
        {"tasks": [{"id": "t1", "status": "done"}, {"id": "t2", "status": "doing"}]},
    )["plan"]
    assert [t["id"] for t in plans.summary_lines(plan)] == ["t2", "t3", "t1"]
    assert plans.counts(plan) == (1, 3)
    assert plans.open_tasks(plan)


# --- The tools ----------------------------------------------------------------------
async def test_plan_write_answers_with_the_rendered_plan(db, user_id):
    chat = _chat_with_tools(db, user_id)
    user = db.get(User, user_id)
    context = _context(db, user, chat)
    outcome = await tools_service.run_tool(context, "plan_write", json.dumps(PLAN))
    assert outcome.content.startswith("# Fix the login bug")
    assert "[t3] [ ] Run the suite" in outcome.content
    assert outcome.event["plan"]["version"] == 2
    assert "plan_final" not in outcome.event


async def test_plan_update_changes_the_chats_plan_and_names_the_known_ids(db, user_id):
    chat = _chat_with_tools(db, user_id, plan_json=plans.normalise(PLAN))
    user = db.get(User, user_id)
    context = _context(db, user, chat)

    outcome = await tools_service.run_tool(
        context, "plan_update", json.dumps({"tasks": [{"id": "t1", "status": "done"}]})
    )
    assert "[t1] [x] Renew the cookie" in outcome.content
    assert outcome.event["plan"]["phases"][0]["tasks"][0]["status"] == "done"

    wrong = await tools_service.run_tool(
        context, "plan_update", json.dumps({"tasks": [{"id": "t9", "status": "done"}]})
    )
    assert wrong.content == 'Unknown task id "t9". Known: t1, t2, t3.'
    assert wrong.event["status"] == "error"


def test_plan_mode_withholds_what_changes_things_and_offers_plan_submit(db, user_id):
    user = db.get(User, user_id)
    normal = _chat_with_tools(db, user_id)
    offered = tools_service.resolve_tools(db, normal, user).by_name
    assert {"plan_write", "plan_update"} <= set(offered)
    assert "plan_submit" not in offered
    assert "memory_add" in offered

    planning = _chat_with_tools(db, user_id, agent_mode="plan")
    offered = tools_service.resolve_tools(db, planning, user).by_name
    assert "plan_submit" in offered
    changing = (tools_service.RISK_WRITE, tools_service.RISK_EXECUTE)
    assert not any(t.risk in changing for t in offered.values())
    assert "notes_search" in offered  # the readers stay

    helper = _chat_with_tools(db, user_id, parent_chat_id=normal.id, unattended=True)
    assert not {"plan_write", "plan_update", "plan_submit"} & set(
        tools_service.resolve_tools(db, helper, user).by_name
    )


def test_the_former_todo_name_runs_as_plan_write():
    name, args = tools_service.resolve_alias(
        "todo", {"todos": [{"content": "Read it", "status": "completed"}, "Fix it"]}
    )
    assert name == "plan_write"
    plan = plans.normalise(args)
    assert plan["title"] == "Work"
    assert [(t["text"], t["status"]) for t in plan["phases"][0]["tasks"]] == [
        ("Read it", "done"),
        ("Fix it", "todo"),
    ]


# --- The chat carries it ------------------------------------------------------------
def test_the_chats_plan_falls_back_to_what_an_older_row_had(db, user_id):
    chat = _chat_with_tools(db, user_id)
    assert chat.plan == {}

    chat.todos_json = [{"content": "Old item", "status": "in_progress"}]
    db.commit()
    assert chat.plan["phases"][0]["tasks"][0]["status"] == "doing"

    message = Message(
        chat_id=chat.id, role=ROLE_ASSISTANT, content="", plan_json=plans.normalise(PLAN)
    )
    db.add(message)
    db.commit()
    chat.todos_json = []
    chat.plan_message_id = message.id
    db.commit()
    assert chat.plan["title"] == "Fix the login bug"

    chat.plan_json = plans.normalise({"title": "Newer", "phases": [{"tasks": ["x"]}]})
    db.commit()
    assert chat.plan["title"] == "Newer"


def test_the_prompt_carries_the_plan_and_the_mode(db, user_id):
    user = db.get(User, user_id)
    chat = _chat_with_tools(db, user_id, agent_mode="plan", plan_json=plans.normalise(PLAN))
    offered = tools_service.resolve_tools(db, chat, user).schemas
    values = harness.context_variables(db, user, offered, chat)
    assert values["plan"].startswith("# Fix the login bug")
    assert values["plan_context"].startswith("### The current plan")
    assert values["mode_plan"] == "yes"
    text = harness.compose(db, user, offered, chat)
    assert "Permission mode: plan." in text
    assert "[t1] [ ] Renew the cookie" in text
    assert "Work to it and quote the ids above" in text
    assert "plan under the plan" in text


# --- The routes ---------------------------------------------------------------------
def test_a_chat_switches_between_plan_and_normal(client, db, user_id):
    chat = _chat_with_tools(db, user_id)
    assert client.patch(f"/api/chats/{chat.id}", data={"agent_mode": "plan"}).status_code == 204
    db.expire_all()
    assert tools_service.plan_mode(db.get(Chat, chat.id))
    # An agent chat's modes mean nothing to a chat.
    client.patch(f"/api/chats/{chat.id}", data={"agent_mode": "auto"})
    db.expire_all()
    assert db.get(Chat, chat.id).agent_mode == "plan"
    assert client.patch(f"/api/chats/{chat.id}", data={"agent_mode": ""}).status_code == 204
    db.expire_all()
    assert not tools_service.plan_mode(db.get(Chat, chat.id))

    page = client.get(f"/chat/{chat.id}").text
    assert 'name="agent_mode"' in page and 'value="plan"' in page


def test_the_panel_shows_the_plan_and_the_bubble_its_snapshot(client, db, user_id):
    chat = _chat_with_tools(db, user_id, plan_json=plans.normalise(PLAN))
    db.add(Message(chat_id=chat.id, role="user", content="Hi", complete=True))
    db.add(
        Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="Here is the plan.", plan_json=PLAN)
    )
    db.commit()
    page = client.get(f"/chat/{chat.id}").text
    panel = page[page.index('id="plan-panel"') :]
    assert "hidden" not in panel[: panel.index(">")]
    assert "0/3" in panel and "Renew the cookie" in panel
    assert page.count("/execute-plan") == 2  # the panel's card and the bubble's
    assert "/messages/current/execute-plan" in page


def test_carrying_out_the_current_plan_leaves_plan_mode(client, db, user_id, monkeypatch):
    from fastapi import Response

    sent = []

    def fake_send(_request, _db, _chat, _user, content, **_kwargs):
        sent.append(content)
        return Response(status_code=204)

    monkeypatch.setattr("lembas.api.chats._send", fake_send)
    chat = _chat_with_tools(db, user_id, agent_mode="plan", plan_json=plans.normalise(PLAN))
    response = client.post(f"/api/chats/{chat.id}/messages/current/execute-plan")
    assert response.status_code == 204
    db.expire_all()
    assert db.get(Chat, chat.id).agent_mode == ""
    assert sent and "1. Renew the cookie" in sent[0]

    empty = _chat_with_tools(db, user_id)
    assert client.post(f"/api/chats/{empty.id}/messages/current/execute-plan").status_code == 404


# --- The loop -----------------------------------------------------------------------
async def test_a_written_plan_lands_on_the_chat_and_the_message(db, user_id, monkeypatch):
    chat = _chat_with_tools(db, user_id)
    message_id = _reply(db, chat)
    seen: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream([[_call_chunk("plan_write", PLAN)], [_text_chunk("Planned.")]], seen),
    )
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    await generation_service._run(generation)

    db.expire_all()
    assert db.get(Chat, chat.id).plan["title"] == "Fix the login bug"
    assert db.get(Message, message_id).plan["title"] == "Fix the login bug"
    assert db.get(Chat, chat.id).plan_message_id == message_id
    assert generation.text == "Planned."
    # The tool turn carried the plan with its ids back to the model.
    assert "[t1] [ ] Renew the cookie" in seen[1]["messages"][-1]["content"]


async def test_the_old_todo_call_is_a_plan_write(db, user_id, monkeypatch):
    chat = _chat_with_tools(db, user_id)
    message_id = _reply(db, chat)
    seen: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [
                [_call_chunk("todo", {"todos": [{"content": "Fix it", "status": "in_progress"}]})],
                [_text_chunk("Done.")],
            ],
            seen,
        ),
    )
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    await generation_service._run(generation)
    db.expire_all()
    assert db.get(Chat, chat.id).plan["phases"][0]["tasks"][0]["status"] == "doing"
    assert seen[1]["messages"][-2]["tool_calls"][0]["function"]["name"] == "plan_write"


async def test_plan_submit_waits_on_the_card_and_approval_ends_plan_mode(db, user_id, monkeypatch):
    chat = _chat_with_tools(db, user_id, agent_mode="plan")
    message_id = _reply(db, chat)
    seen: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [
                [_call_chunk("plan_write", PLAN)],
                [_call_chunk("plan_submit", {}, call_id="c2")],
                [_text_chunk("Carrying it out.")],
            ],
            seen,
        ),
    )
    # Plan mode withheld the writers for this request.
    assert "memory_add" not in {
        t["function"]["name"]
        for t in tools_service.resolve_tools(db, chat, db.get(User, user_id)).schemas
    }
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    task = asyncio.ensure_future(generation_service._run(generation))
    generation_service._RUNNING[message_id] = generation
    try:
        for _ in range(300):
            if generation.pending is not None:
                break
            await asyncio.sleep(0.01)
        card = generation.pending
        assert card is not None and card.kind == interaction.KIND_PLAN
        assert card.items[0].plan["title"] == "Fix the login bug"
        assert card.items[0].mode_choice is False
        assert generation_service.answer(chat.id, card.id, verdict=interaction.ALLOW)
        await asyncio.wait_for(task, 5)
    finally:
        generation_service._RUNNING.pop(message_id, None)

    db.expire_all()
    assert db.get(Chat, chat.id).agent_mode == ""
    assert generation.text == "Carrying it out."
    # The answer was the tool's result, and the next request had the writers.
    third = seen[2]
    assert "approved" in third["messages"][-1]["content"]
    assert "memory_add" in {t["function"]["name"] for t in third["tools"]}
    assert "plan_submit" not in {t["function"]["name"] for t in third["tools"]}


async def test_a_plan_put_aside_withdraws_the_tools_for_a_last_word(db, user_id, monkeypatch):
    chat = _chat_with_tools(db, user_id, agent_mode="plan", plan_json=plans.normalise(PLAN))
    message_id = _reply(db, chat)
    seen: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream([[_call_chunk("plan_submit", {})], [_text_chunk("As proposed.")]], seen),
    )
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    task = asyncio.ensure_future(generation_service._run(generation))
    generation_service._RUNNING[message_id] = generation
    try:
        for _ in range(300):
            if generation.pending is not None:
                break
            await asyncio.sleep(0.01)
        assert generation_service.answer(chat.id, generation.pending.id, verdict=interaction.DENY)
        await asyncio.wait_for(task, 5)
    finally:
        generation_service._RUNNING.pop(message_id, None)
    db.expire_all()
    assert db.get(Chat, chat.id).agent_mode == "plan"
    assert "tools" not in seen[1]
    assert "put the plan aside" in seen[1]["messages"][-1]["content"]


def test_the_budgets_come_from_the_spec_unless_an_administrator_says(db):
    budgets = generation_service.loop_budgets(db)
    assert budgets == {
        "wall_seconds": 900,
        "output_bytes": 1048576,
        "completion_tokens": 200000,
        "nudges": 2,
    }
    settings_store.update(db, {"loop_wall_seconds": 0, "loop_output_bytes": 10})
    budgets = generation_service.loop_budgets(db)
    assert budgets["wall_seconds"] == 0 and budgets["output_bytes"] == 10


async def test_past_the_output_budget_the_model_is_told_which_limit(db, user_id, monkeypatch):
    settings_store.update(db, {"loop_output_bytes": 10})
    chat = _chat_with_tools(db, user_id)
    message_id = _reply(db, chat)
    seen: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [[_call_chunk("plan_write", PLAN)], [_text_chunk("Here is what I have.")]], seen
        ),
    )
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    await generation_service._run(generation)
    assert generation.text == "Here is what I have."
    assert "tools" not in seen[1]
    last = seen[1]["messages"][-1]
    assert last["role"] == "user" and "tool-output limit" in last["content"]
    assert any(e.get("name") == "budget" for e in generation.tool_events)


async def test_a_round_of_only_thinking_is_asked_again(db, user_id, monkeypatch):
    chat = _chat_with_tools(db, user_id)
    message_id = _reply(db, chat)
    seen: list[dict] = []
    thinking = {"choices": [{"delta": {"reasoning_content": "Hmm."}}]}
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream([[thinking], [_text_chunk("Right.")]], seen),
    )
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    await generation_service._run(generation)
    assert generation.text == "Right."
    assert generation.nudges == 1
    assert len(seen) == 2
    assert seen[1]["messages"][-1]["role"] == "user"
    assert "Continue from where you stopped" in seen[1]["messages"][-1]["content"]


async def test_the_nudges_stop_at_the_specs_number(db, user_id, monkeypatch):
    chat = _chat_with_tools(db, user_id)
    message_id = _reply(db, chat)
    seen: list[dict] = []
    thinking = {"choices": [{"delta": {"reasoning_content": "Hmm."}}]}
    monkeypatch.setattr(
        generation_service, "stream_chat", _stub_stream([[thinking]], seen)
    )
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    await generation_service._run(generation)
    assert len(seen) == 3  # the round, and two nudges
    assert generation.nudges == 2
