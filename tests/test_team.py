"""Teams (harness spec 3.2.0, team.md): subagents that work together.

Three layers, as the code has them:

- the rules on plain data (`services/team.py`) -- size and offer, the order
  of refusals, a manager's refusal, delivery and who sees what, reports,
  stopping a subtree, slots and their queue -- beyond what the spec's
  conformance cases (tests/test_harness_spec.py) walk through;
- the chat's team on this server (`services/team_run.py`): the setting, the
  note, and one whole run with the suite's scripted model, where a lead adds
  two members who message each other and report;
- what a person and an app see (`services/activity.py`, the activity panel
  and `/api/v2`), model text escaped.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from sqlalchemy import select

from lembas.db.models import (
    ROLE_ASSISTANT,
    Chat,
    Connection,
    Message,
    Model,
    TeamAgent,
    TeamMessage,
    User,
)
from lembas.db.session import session_scope
from lembas.security import api_tokens
from lembas.services import activity, settings_store, team_run
from lembas.services import generation as generation_service
from lembas.services import subagent as subagent_service
from lembas.services import team as rules
from lembas.services.crypto import encrypt

TEXTS = rules.texts()


def _team(low=1, high=4, parallel=None, single=False) -> rules.Team:
    return rules.Team(min=low, max=high, parallel=parallel, single_session=single)


def _add(team, name, by=rules.LEAD, **extra):
    args = {"name": name, "role": f"owns {name}", "task": f"Do {name}.", "goal": "A parser"}
    args.update(extra)
    return rules.add(team, by, args)


# --- §2 and §3: offered, the setting, the size ------------------------------------
@pytest.mark.parametrize(
    ("parallel", "single", "offered"),
    [
        (None, False, True),
        (None, True, False),
        (1, False, False),
        (2, True, True),
        (4, False, True),
    ],
)
def test_a_team_is_offered_on_a_model_that_serves_two_at_once(parallel, single, offered):
    assert rules.capacity_allows(parallel, single) is offered


def test_the_setting_is_refused_outside_its_bounds_and_narrowed_by_a_lower_cap():
    assert rules.bounds_refusal(1, 4, 32) == ""
    assert rules.bounds_refusal(0, 4, 32)
    assert rules.bounds_refusal(5, 4, 32)
    assert rules.bounds_refusal(1, 33, 32)
    assert rules.bounds_refusal("x", 4, 32)
    # Read, never stored: max = min(max, cap), min = min(min, max).
    assert rules.read_setting(6, 10, 4) == (4, 4)
    assert rules.read_setting(None, None, 32) == (1, 4)


def test_the_size_counts_members_whatever_their_state_and_running_helpers():
    team = _team(high=4)
    _add(team, "parser")
    rules.finish(team, "parser", "done")
    rules.task_started(team, rules.LEAD)
    assert team.size() == 3  # lead, a finished member, a helper
    assert _add(team, "tester").agent is not None
    assert _add(team, "third").text == rules._say("full", max=4)
    # task past the size answers `full`; a helper ending makes room.
    assert rules.task_refusal(team) == rules._say("full", max=4)
    rules.task_done(team, rules.LEAD)
    assert rules.task_refusal(team) == ""


def test_one_agent_allowed_means_no_team_and_no_helper():
    team = _team(high=1)
    assert rules.unmet(team) == rules.spec()["unavailable"]["one_agent"]
    one_agent = rules.spec()["unavailable"]["one_agent"]
    assert rules.add(team, rules.LEAD, {"name": "x"}).text == one_agent
    assert rules.task_refusal(team) == rules.spec()["unavailable"]["one_agent"]


def test_a_model_that_lost_its_capacity_answers_one_request():
    team = _team(parallel=1)
    assert rules.unmet(team) == rules.spec()["unavailable"]["one_request"]


# --- §4: team_add's refusals, in the spec's order ----------------------------------
def test_team_add_refuses_in_order():
    team = _team(high=2)
    assert rules.add(team, rules.LEAD, {"name": "all"}).text == TEXTS["all_name"]
    assert rules.add(team, rules.LEAD, {"name": "Bad Name"}).text == rules._say(
        "bad_name", name="Bad Name"
    )
    assert rules.add(team, rules.LEAD, {"name": "lead"}).text == rules._say("taken", name="lead")
    # The lead's first member needs the team's goal.
    assert rules.add(team, rules.LEAD, {"name": "parser", "role": "r", "task": "t"}).text == (
        TEXTS["no_goal"]
    )
    added = _add(team, "parser")
    assert added.text.startswith("Added parser (owns parser): it is working.")
    assert team.goal == "A parser"
    assert _add(team, "parser").text == rules._say("taken", name="parser")
    assert _add(team, "other").text == rules._say("full", max=2)


# --- §6: a manager does not do the work --------------------------------------------
def test_a_manager_is_refused_work_and_keeps_reading_and_managing():
    team = _team()
    assert rules.manager_refusal(team, rules.LEAD, "write") == ""  # no member yet
    _add(team, "parser")
    refusal = TEXTS["manager"]
    for tool in ("write", "edit", "apply_patch", "mv", "rm", "python", "commit"):
        assert rules.manager_refusal(team, rules.LEAD, tool) == refusal, tool
    for tool in ("read", "grep", "glob", "web_search", "task", "job_stop", "team_send"):
        assert rules.manager_refusal(team, rules.LEAD, tool) == "", tool
    assert rules.manager_refusal(team, rules.LEAD, "bash", args={"command": "git status"}) == ""
    assert rules.manager_refusal(team, rules.LEAD, "bash", args={"command": "rm -rf x"}) == refusal
    # An MCP tool that does not say it only reads is work.
    assert rules.manager_refusal(team, rules.LEAD, "mcp_thing", "execute") == refusal
    assert rules.manager_refusal(team, rules.LEAD, "mcp_reader", "read") == ""
    # The member itself does the work.
    assert rules.manager_refusal(team, "parser", "write") == ""


def test_min_two_makes_the_lead_a_manager_from_the_start():
    assert rules.manager_refusal(_team(low=2), rules.LEAD, "write") == TEXTS["manager"]


# --- §5, §7: delivery, marks, visibility, reports ----------------------------------
def test_a_message_is_delivered_marked_and_wakes_a_finished_member():
    team = _team()
    _add(team, "parser")
    _add(team, "tester")
    rules.finish(team, "tester", "first report")
    team.effects.clear()
    answer, delivered = rules.send(team, "parser", ["tester"], "grammar ready")
    assert answer == "Sent to tester."
    body = "[Team message from parser to tester:]\ngrammar ready"
    assert delivered == {"tester": [body]}
    # Finished, so it is woken with the message as a new turn.
    assert [(e.kind, e.name, e.texts) for e in team.effects] == [("start", "tester", [body])]
    assert team.get("tester").state == rules.WORKING


def test_a_message_to_a_working_agent_arrives_at_its_next_step():
    team = _team()
    _add(team, "parser")
    team.effects.clear()
    rules.send(team, rules.LEAD, "parser", "use the new grammar")
    assert [e.kind for e in team.effects] == ["deliver"]


def test_send_refusals_and_all():
    team = _team()
    assert rules.send(team, rules.LEAD, ["all"], "hi")[0] == TEXTS["nobody"]
    _add(team, "parser")
    _add(team, "tester")
    assert rules.send(team, rules.LEAD, ["ghost"], "hi")[0] == rules._say(
        "unknown", name="ghost", names="lead, parser, tester"
    )
    assert rules.send(team, "parser", ["parser"], "hi")[0] == TEXTS["self"]
    _, delivered = rules.send(team, "parser", ["tester", "all"], "everyone")
    assert sorted(delivered) == ["lead", "tester"]
    assert delivered["lead"][0].startswith("[Team message from parser to all:]")


def test_a_manager_is_not_sent_its_members_messages_but_reads_them():
    team = _team(high=6)
    _add(team, "area", lead=True)
    _add(team, "one", by="area", goal="")
    _add(team, "two", by="area", goal="")
    _add(team, "outsider")
    _, delivered = rules.send(team, "one", ["two"], "between us")
    assert list(delivered) == ["two"]
    # Who may see it: the lead all; the sub-manager its subtree; not others.
    entry = team.room[-1]
    assert rules.may_see(team, rules.LEAD, entry)
    assert rules.may_see(team, "area", entry)
    assert rules.may_see(team, "one", entry)
    assert not rules.may_see(team, "outsider", entry)
    text, shown = rules.read_text(team, "area")
    assert [p.n for p in shown] == [entry.n]
    assert "#1 one → two: between us" in text
    assert rules.read_text(team, "outsider")[0].endswith("No messages since #0.")


def test_a_final_reply_is_a_report_to_the_manager():
    team = _team(high=6)
    _add(team, "area", lead=True)
    _add(team, "one", by="area", goal="")
    delivered = rules.finish(team, "one", "")
    assert delivered == {"area": ["[Team report from one to area:]\n(no report)"]}
    assert team.room[-1].report
    delivered = rules.finish(team, "area", "Area done.", "steps")
    assert delivered == {"lead": ["[Team report from area to lead:]\nArea done.\n\n[ended: steps]"]}


# --- §9: stopping a subtree --------------------------------------------------------
def test_team_stop_takes_a_whole_subtree_and_only_one_under_the_caller():
    team = _team(high=8)
    _add(team, "area", lead=True)
    _add(team, "one", by="area", goal="")
    _add(team, "two", by="area", goal="")
    _add(team, "solo")
    assert rules.stop(team, "area", "solo")[0] == rules._say("not_yours", name="solo")
    assert rules.stop(team, "area", "area")[0] == rules._say("not_yours", name="area")
    assert rules.stop(team, "solo", "all")[0] == TEXTS["nobody_to_stop"]
    text, names = rules.stop(team, rules.LEAD, "area")
    assert names == ["area", "one", "two"]
    assert text == "Stopped area, one, two. The team is 2 of 8 agents."
    # Their names may be used again.
    assert _add(team, "one").agent is not None


def test_the_person_stopping_a_member_is_told_to_the_lead_once():
    team = _team()
    _add(team, "parser")
    assert rules.person_stop(team, "parser") == ["parser"]
    blocks = rules.note_blocks(team)
    assert rules.stopped_block(["parser"]) in blocks
    assert rules.stopped_block(["parser"]) not in rules.note_blocks(team)


# --- §8: slots ----------------------------------------------------------------------
def test_members_wait_for_a_slot_and_start_in_order_the_lead_first():
    team = _team(high=5, parallel=2)  # the lead's turn holds one slot
    first = _add(team, "one")
    second = _add(team, "two")
    third = _add(team, "three")
    assert first.agent.state == rules.WORKING
    assert second.agent.state == rules.WAITING
    assert second.text.startswith("Added two (owns two): it waits for a free slot")
    assert third.agent.state == rules.WAITING
    # The lead's turn ends: the next in line starts, in the order they began waiting.
    rules.finish(team, rules.LEAD, "")
    assert team.get("two").state == rules.WORKING
    assert team.get("three").state == rules.WAITING
    # A report waking the lead puts it first in the queue.
    rules.finish(team, "one", "done")
    assert team.lead.state == rules.WORKING
    assert team.get("three").state == rules.WAITING


def test_the_size_note_says_the_setting_once_and_again_after_a_change():
    team = _team(low=2, high=4)
    first = rules.note_blocks(team)
    assert first == [rules.size_block(2, 4)]
    assert "at least 1 of them" in first[0]
    assert rules.note_blocks(team) == []
    team.max = 1
    assert rules.note_blocks(team) == [rules.harness_spec.note()["team_one"]]


# --- The server: the setting, offer, notes ------------------------------------------
@pytest.fixture
def team_ready(db, registered):
    settings_store.update(db, {"enabled": True}, key=settings_store.SUBAGENTS)
    settings_store.update(db, {"default_permissions": {"tools.subagent": True}})
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
    subagent_service.clear()
    team_run.clear()
    yield connection
    team_run.clear()
    subagent_service.clear()


def _user(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _chat(db, connection) -> Chat:
    chat = Chat(user_id=_user(db).id, title="t", model_id="test-model", connection_id=connection.id)
    db.add(chat)
    db.commit()
    return chat


def test_model_parallel_reads_one_place(team_ready):
    class Hosted:
        single_session = False

    class Single:
        single_session = True

    class Parallel:
        parallel = 3
        single_session = True

    assert team_run.model_parallel(Hosted()) is None
    assert team_run.model_parallel(Single()) == 1
    assert team_run.model_parallel(Parallel()) == 3


def test_the_team_tools_are_offered_on_a_hosted_model_and_not_on_a_single_one(db, team_ready):
    from lembas.services import tools as tools_service

    chat = _chat(db, team_ready)
    user = _user(db)
    names = {t.name for t in tools_service.resolve_tools(db, chat, user).defs}
    assert set(rules.TOOLS) <= names
    model = db.scalars(select(Model)).first()
    model.single_session = True
    db.commit()
    chat2 = _chat(db, team_ready)
    names = {t.name for t in tools_service.resolve_tools(db, chat2, user).defs}
    assert not set(rules.TOOLS) & names


def test_the_size_setting_is_saved_on_the_chat_and_refused_outside(client, db, team_ready):
    chat = _chat(db, team_ready)
    response = client.patch(f"/api/chats/{chat.id}", data={"agents_min": "2", "agents_max": "6"})
    assert response.status_code < 400, response.text
    db.refresh(chat)
    assert (chat.agents_min, chat.agents_max) == (2, 6)
    response = client.patch(f"/api/chats/{chat.id}", data={"agents_min": "5", "agents_max": "3"})
    assert response.status_code == 422
    db.refresh(chat)
    assert (chat.agents_min, chat.agents_max) == (2, 6)


def test_the_setting_counts_the_chats_own_agent(client, db, team_ready):
    """Min and Max count this chat's own agent (the owner, 2026-11-09): Max 1
    is the agent alone -- no member, no `task` helper -- and Max 4 is the
    agent and up to three, whatever mix of members and helpers they are."""
    chat = _chat(db, team_ready)
    response = client.patch(f"/api/chats/{chat.id}", data={"agents_min": "1", "agents_max": "1"})
    assert response.status_code < 400, response.text
    db.refresh(chat)
    one_agent = rules.spec()["unavailable"]["one_agent"]
    team = team_run.load(db, chat.id)
    assert (team.min, team.max, team.size()) == (1, 1, 1)
    assert team_run.task_refusal(db, chat) == one_agent
    assert rules.add(team, rules.LEAD, {"name": "x", "role": "r", "task": "t"}).text == one_agent

    response = client.patch(f"/api/chats/{chat.id}", data={"agents_min": "1", "agents_max": "4"})
    assert response.status_code < 400, response.text
    db.refresh(chat)
    team = team_run.load(db, chat.id)
    assert team.max == 4 and team_run.task_refusal(db, chat) == ""
    for name in ("one", "two", "three"):
        assert _add(team, name).agent is not None, name
    assert team.size() == 4  # the agent and three subagents
    assert _add(team, "four").text == rules._say("full", max=4)
    assert rules.task_refusal(team) == rules._say("full", max=4)


def test_the_setting_is_one_range_with_a_min_and_a_max_handle(client, db, team_ready):
    chat = _chat(db, team_ready)
    chat.agents_min, chat.agents_max = 2, 5
    db.commit()
    page = client.get(f"/chat/{chat.id}").text
    assert "data-team-range" in page
    for end, value in (("min", 2), ("max", 5)):
        tag = re.search(rf'<input[^>]*id="agents-{end}"[^>]*>', page, re.S).group(0)
        assert 'type="range"' in tag and f'value="{value}"' in tag
        assert f'aria-label="{end.capitalize()}"' in tag
        assert f'hx-patch="/api/chats/{chat.id}"' in tag
    assert "Min 2 · Max 5" in page
    assert "1 is the agent alone, no subagents" in page
    assert "Fewest" not in page and 'type="number" id="agents-' not in page

    settings = client.get("/settings?tab=models").text
    assert 'id="pref-agents-min"' in settings and 'id="pref-agents-max"' in settings
    assert re.search(
        r'<input[^>]*id="pref-agents-max"[^>]*type="range"|type="range"[^>]*id="pref-agents-max"',
        settings,
        re.S,
    )


def test_the_size_note_rides_on_the_first_user_message(db, team_ready):
    from lembas.services import tools as tools_service

    chat = _chat(db, team_ready)
    tools = tools_service.enabled_tools(db, chat, _user(db))
    block, state = team_run.note_block(db, chat, tools)
    assert block == rules.size_block(1, 4)
    assert state == "1-4"


# --- One whole run, with the suite's scripted model ---------------------------------
MEMBER = re.compile(r'You are "([a-z][a-z0-9-]*)", a member of a team')


def _call(name: str, args: dict) -> list[dict]:
    return [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": f"c-{name}",
                                "function": {"name": name, "arguments": json.dumps(args)},
                            }
                        ]
                    }
                }
            ]
        }
    ]


def _say(text: str) -> list[dict]:
    return [{"choices": [{"delta": {"content": text}}]}]


def _script(payload: dict) -> list[dict]:
    """Who is asking and how far into its turn: the system prompt names a
    member; the tool results since the last user message count the rounds."""
    messages = payload["messages"]
    system = str(messages[0].get("content") or "") if messages else ""
    found = MEMBER.search(system)
    who = found.group(1) if found else "lead"
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    turn = len(users)
    rounds = len([m for m in messages[users[-1] :] if m.get("role") == "tool"]) if users else 0
    if who == "lead":
        if turn == 1 and rounds == 0:
            return _call(
                "team_add",
                {
                    "name": "parser",
                    "role": "owns the grammar",
                    "task": "Write the grammar; tell tester when it is ready.",
                    "goal": "A parser for the journey's notes",
                },
            )
        if turn == 1 and rounds == 1:
            return _call(
                "team_add",
                {"name": "tester", "role": "owns the tests", "task": "Write the tests."},
            )
        return _say("The team is on it." if turn == 1 else "All done.")
    # The tester, added second, tells the parser it is ready; the parser
    # answers once that message reaches it (a new turn, or its next step).
    if rounds == 0 and ((who == "tester" and turn == 1) or (who == "parser" and turn > 1)):
        other = "tester" if who == "parser" else "parser"
        return _call("team_send", {"to": [other], "message": f"{who} is ready"})
    return _say(f"{who} report: finished." if turn == 1 else f"{who}: noted.")


async def _until(check, seconds: float = 30.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the team did not settle in time")


async def test_a_lead_adds_two_members_who_message_each_other_and_report(
    db, team_ready, monkeypatch
):
    from lembas.services import wake as wake_service

    seen: list[dict] = []

    async def stream_chat(_endpoint, payload):
        seen.append(payload)
        for chunk in _script(payload):
            yield chunk

    async def no_title(*_args, **_kwargs):
        return None

    monkeypatch.setattr(generation_service, "stream_chat", stream_chat)
    monkeypatch.setattr("lembas.services.chat.generate_title", no_title)
    monkeypatch.setattr(team_run, "POLL_SECONDS", 0.05)
    chat = _chat(db, team_ready)
    lead_id = chat.id

    await wake_service.wake_chat(lead_id, "Build a parser for the journey's notes.")

    def settled() -> bool:
        with session_scope() as s:
            reports = s.scalars(
                select(TeamMessage).where(
                    TeamMessage.lead_chat_id == lead_id, TeamMessage.report.is_(True)
                )
            ).all()
            senders = {r.sender for r in reports}
            busy = any(
                generation_service.running_for(c) is not None
                for c in [lead_id]
                + [a.chat_id for a in s.scalars(select(TeamAgent)) if a.chat_id]
            )
            last = s.scalars(
                select(Message)
                .where(Message.chat_id == lead_id, Message.role == ROLE_ASSISTANT)
                .order_by(Message.created_at.desc())
            ).first()
            at_rest = all(a.state == "finished" for a in s.scalars(select(TeamAgent)))
            return (
                {"parser", "tester"} <= senders
                and not busy
                and at_rest
                and last is not None
                and last.content == "All done."
            )

    await _until(settled)

    with session_scope() as s:
        room = s.scalars(
            select(TeamMessage).where(TeamMessage.lead_chat_id == lead_id).order_by(TeamMessage.n)
        ).all()
        pairs = [(m.sender, list(m.to_json), m.report) for m in room]
        assert ("parser", ["tester"], False) in pairs
        assert ("tester", ["parser"], False) in pairs
        assert ("parser", ["lead"], True) in pairs
        assert ("tester", ["lead"], True) in pairs
        members = {a.name: a for a in s.scalars(select(TeamAgent))}
        assert set(members) == {"parser", "tester"}
        assert members["tester"].goal == "A parser for the journey's notes"
        # A member's hidden chat took the other's message in, marked.
        texts = [
            m.content
            for m in s.scalars(
                select(Message).where(Message.chat_id == members["tester"].chat_id)
            )
        ]
        assert any("[Team message from parser to tester:]" in (t or "") for t in texts)
        # The lead was woken by the reports, marked as such.
        lead_texts = [
            m.content for m in s.scalars(select(Message).where(Message.chat_id == lead_id))
        ]
        assert any("[Team report from" in (t or "") for t in lead_texts)

    # The member's system prompt is team_member.md, the lead's has team_lead.md.
    member_prompts = [p["messages"][0]["content"] for p in seen if MEMBER.search(
        str(p["messages"][0].get("content") or ""))]
    assert member_prompts and 'and you report to "lead"' in member_prompts[0]
    lead_prompt = next(
        p["messages"][0]["content"] for p in seen
        if not MEMBER.search(str(p["messages"][0].get("content") or ""))
    )
    assert rules.harness_spec.prompt("blocks/team_lead.md").strip()[:60] in lead_prompt

    # What the person sees of it: two finished members and the room.
    with session_scope() as s:
        lead = s.get(Chat, lead_id)
        view = await activity.snapshot(s, lead, s.get(User, lead.user_id), with_context=False)
    states = {a["name"]: a["state"] for a in view["agents"] if a["kind"] == "member"}
    assert states == {"parser": "done", "tester": "done"}
    assert view["team"]["cursor"] >= 4


# --- What a person and an app see -----------------------------------------------------
def _seed_team(db, chat: Chat) -> None:
    """A team at rest, written as the runtime writes one: a member that
    leads with its own member, and a room with a message and a report."""
    from lembas.db.models import Team as TeamRow

    db.add(TeamRow(lead_chat_id=chat.id, goal="<b>Bake</b>", seq=3, data_json={}))
    for i, (name, manager, lead) in enumerate(
        [("baker", "lead", True), ("wrapper", "baker", False)], start=1
    ):
        child = Chat(
            user_id=chat.user_id,
            title=name,
            model_id=chat.model_id,
            connection_id=chat.connection_id,
            parent_chat_id=chat.id,
            unattended=True,
        )
        db.add(child)
        db.flush()
        db.add(
            TeamAgent(
                lead_chat_id=chat.id,
                name=name,
                manager=manager,
                role="<script>alert(1)</script>" if name == "wrapper" else "owns the oven",
                may_lead=lead,
                agent="general",
                model="Test/test-model",
                chat_id=child.id,
                state="finished",
                position=i,
            )
        )
        db.add(Message(chat_id=child.id, role="user", content=f"Brief for {name}", complete=True))
        db.add(
            Message(
                chat_id=child.id,
                role=ROLE_ASSISTANT,
                content=f"{name} wrote **this**",
                complete=True,
                tool_calls_json=[{"name": "read", "status": "ok", "query": "notes.md"}],
            )
        )
    db.add(TeamMessage(lead_chat_id=chat.id, n=1, sender="baker", to_json=["wrapper"],
                       recipients_json=["wrapper"], text="Leaves by noon?", report=False))
    db.add(TeamMessage(lead_chat_id=chat.id, n=2, sender="wrapper", to_json=["baker"],
                       recipients_json=["baker"], text="<img src=x onerror=alert(1)> done",
                       report=True))
    db.commit()


def test_the_activity_panel_shows_the_tree_progress_and_room_escaped(client, db, team_ready):
    chat = _chat(db, team_ready)
    _seed_team(db, chat)
    response = client.get(f"/api/chats/{chat.id}/activity")
    assert response.status_code == 200, response.text
    html = response.text
    for section in ("Context", "Outputs", "Background tasks"):
        assert section in html
    assert 'id="agent-member-baker"' in html and 'id="agent-member-wrapper"' in html
    assert "Brief for wrapper" in html
    assert "notes.md" in html  # its latest step
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "Leaves by noon?" in html and "report" in html
    # Its conversation, read-only, in the helper panel.
    response = client.get(f"/api/chats/{chat.id}/team/wrapper/transcript")
    assert response.status_code == 200
    assert "Brief for wrapper" in response.text
    assert "jobpanel__send" not in response.text


def test_the_tasks_section_alone_polls_while_something_works(client, db, team_ready):
    chat = _chat(db, team_ready)
    _seed_team(db, chat)
    row = db.scalars(select(TeamAgent).where(TeamAgent.name == "wrapper")).first()
    row.state = "working"
    db.commit()
    html = client.get(f"/api/chats/{chat.id}/activity?part=tasks").text
    assert 'id="activity-tasks"' in html and "every 3s" in html
    assert "1 running" in html


def test_the_person_stops_a_member_and_those_under_it(client, db, team_ready):
    chat = _chat(db, team_ready)
    _seed_team(db, chat)
    response = client.post(f"/api/chats/{chat.id}/team/baker/stop")
    assert response.status_code == 200
    db.expire_all()
    left = {r.name for r in db.scalars(select(TeamAgent)) if r.left is not None}
    assert left == {"baker", "wrapper"}
    assert team_run.take_stopped(db, chat, persist=False) == ["baker", "wrapper"]


def _bearer(db) -> dict[str, str]:
    _, raw = api_tokens.create(db, _user(db), name="app", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def test_the_apps_read_the_same_panel(client, db, team_ready):
    api_tokens.reset_rates()
    chat = _chat(db, team_ready)
    _seed_team(db, chat)
    headers = _bearer(db)
    body = client.get(f"/api/v2/chats/{chat.id}/activity", headers=headers).json()["activity"]
    assert [a["name"] for a in body["agents"]] == ["baker"]
    assert [c["name"] for c in body["agents"][0]["children"]] == ["wrapper"]
    assert body["team"]["room"][1]["report"] is True
    assert body["summary"]["done"] == 2
    assert body["context"]["categories"]
    since = client.get(
        f"/api/v2/chats/{chat.id}/activity?since=1&context=false", headers=headers
    ).json()["activity"]
    assert [m["n"] for m in since["team"]["room"]] == [2] and since["context"] is None
    one = client.get(f"/api/v2/chats/{chat.id}/agents/member/wrapper", headers=headers).json()
    assert one["agent"]["brief"] == "Brief for wrapper"
    assert one["transcript"][0]["text"] == "Brief for wrapper"
    team = client.get(f"/api/v2/chats/{chat.id}/team", headers=headers).json()["team"]
    assert team["cursor"] == 2
    stopped = client.post(f"/api/v2/chats/{chat.id}/agents/member/baker/stop", headers=headers)
    assert stopped.json() == {"stopped": ["baker", "wrapper"]}
    gone = client.post(f"/api/v2/chats/{chat.id}/agents/member/baker/stop", headers=headers)
    assert gone.status_code == 409
