"""The main agent's tree on this server (harness spec 3.4.0, subagents.md
§5-§13): the tools each agent is given, who decides a subagent's call, and one
whole run with the suite's scripted model -- the main agent places a
sub-manager and a subagent under it in a stricter mode, the subagent keeps a
finding and finishes, the main agent replaces it, and the rows, the room and
the hidden chats say so.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import select

from lembas.db.models import Chat, TeamAgent, TeamMessage
from lembas.db.session import session_scope
from lembas.services import generation as generation_service
from lembas.services import team as rules
from lembas.services import team_run
from tests.test_team import _call, _chat, _say, _until, team_ready  # noqa: F401

TEXTS = rules.texts()


def test_the_main_agent_and_a_subagent_are_given_their_own_tools():
    lead = {t.name for t in team_run.tool_defs()}
    assert lead == set(rules.LEAD_TOOLS)
    member = {t.name for t in team_run.tool_defs(lead=False)}
    assert member == set(rules.MEMBER_TOOLS)
    assert "team_request" in {t.name for t in team_run.tool_defs(lead=False, request=True)}
    assert not {"team_add", "team_stop", "task"} & member


def _tree(main="auto", mode="manual", request=()):
    team = rules.Team(min=1, max=4)
    team.goal = "g"
    team.lead.mode = main
    team.agents.append(rules.Agent("api", manager=rules.LEAD, id="s1", mode=mode,
                                   request=list(request), state=rules.WORKING))
    return team


def _tool(name, risk):
    return SimpleNamespace(name=name, risk=risk, family="")


def test_a_narrowed_subagent_asks_the_main_agent_and_a_grant_lets_it_run():
    write = _tool("write", "write")
    assert team_run.delegation_refusal(_tree(), "api", write, {"path": "a.md"}) == (
        TEXTS["not_delegated"]
    )
    team = _tree(request=["write"])
    assert team_run.delegation_refusal(team, "api", write, {"path": "a.md"}) == (
        TEXTS["requestable"]
    )
    team.get("api").grants.append({"tool": "write", "permission": "edit", "pattern": "a.md"})
    assert team_run.delegation_refusal(team, "api", write, {"path": "a.md"}) == ""
    # What the rules allow runs in every mode: reading, the team's own tools.
    assert team_run.delegation_refusal(team, "api", _tool("read", "read"), {"path": "x"}) == ""
    assert team_run.delegation_refusal(team, "api", _tool("team_read", "read"), {}) == ""
    # Plan mode's refusal is final.
    planned = _tree(mode="plan", request=["write"])
    assert team_run.delegation_refusal(planned, "api", write, {"path": "a.md"}).startswith(
        "Denied: "
    )
    # The main agent itself in Plan mode: nobody may write.
    assert team_run.delegation_refusal(_tree(main="plan", mode="plan"), "api", write,
                                       {"path": "a.md"}).startswith("Denied: ")


def _script(payload: dict) -> list[dict]:
    messages = payload["messages"]
    system = str(messages[0].get("content") or "") if messages else ""
    tools = {t["function"]["name"] for t in payload.get("tools") or []}
    rounds = 0
    for m in reversed(messages):
        if m.get("role") == "user":
            break
        rounds += m.get("role") == "tool"
    if '"api"' in system and "team_finish" in tools:
        # The subagent: a finding, then it finishes.
        if rounds == 0:
            return _call("team_findings", {"action": "write", "text": "Routes are in api.py."})
        if rounds == 1:
            return _call("team_finish", {"report": "Done: api.py:12."})
        return _say("should not run")
    if '"area"' in system:
        return _say("area: noted.")
    users = [m for m in messages if m.get("role") == "user"]
    if len(users) == 1:
        script = [
            ("team_add", {"name": "area", "role": "owns the API", "task": "Direct it.",
                          "lead": True, "goal": "An API"}),
            ("team_add", {"name": "api", "role": "writes routes", "task": "Write routes.",
                          "parent": "area", "mode": "manual", "request": ["write"]}),
        ]
        if rounds < len(script):
            return _call(*script[rounds])
        return _say("On it.")
    return _say("Thanks.")


async def test_a_subagent_finds_finishes_and_ends_and_the_main_agent_reads_it(
    db, team_ready, monkeypatch  # noqa: F811
):
    from lembas.services import wake as wake_service

    async def stream_chat(_endpoint, payload, **_):
        for chunk in _script(payload):
            yield chunk

    async def no_title(*_args, **_kwargs):
        return None

    monkeypatch.setattr(generation_service, "stream_chat", stream_chat)
    monkeypatch.setattr("lembas.services.chat.generate_title", no_title)
    monkeypatch.setattr(team_run, "POLL_SECONDS", 0.05)
    chat = _chat(db, team_ready)
    lead_id = chat.id
    await wake_service.wake_chat(lead_id, "Build the API.")

    def ended() -> bool:
        with session_scope() as s:
            row = s.scalars(select(TeamAgent).where(TeamAgent.name == "api")).first()
            busy = any(generation_service.running_for(c.id) is not None
                       for c in s.scalars(select(Chat)))
            return row is not None and row.state == rules.DONE and not busy

    await _until(ended)
    with session_scope() as s:
        rows = {r.name: r for r in s.scalars(select(TeamAgent))}
        api, area = rows["api"], rows["area"]
        assert (api.agent_id, api.manager, api.mode, api.request_json) == (
            "s2", "area", "manual", ["write"])
        assert area.agent_id == "s1" and area.may_lead
        assert api.left is not None
        assert api.findings_json[0]["text"] == "Routes are in api.py."
        # The hidden chat stays, for the person to read.
        assert s.get(Chat, api.chat_id) is not None
        room = [(m.sender, m.to_json, m.kind) for m in s.scalars(
            select(TeamMessage).where(TeamMessage.lead_chat_id == lead_id))]
        assert ("api", ["area"], "report") in room
    team = None
    with session_scope() as s:
        team = team_run.load(s, lead_id)
    info = rules.tree_info(team)
    assert [a["name"] for a in info["ended"]] == ["api"]
    assert info["ended"][0]["state"] == "done" and info["ended"][0]["findings"] == 1
    assert rules.read_findings(team, rules.LEAD, "api").endswith("1. Routes are in api.py.")
    assert json.dumps(info)  # the tree is plain data


def test_a_subagents_context_is_full_at_the_specs_fraction_of_its_window():
    generation = generation_service.Generation(chat_id="x", message_id="m")
    generation.context_limit, generation.context_tokens = 1000, 850
    team_run.note_context(generation)
    assert team_run.context_full(generation)
    generation.context_tokens = 849
    team_run.note_context(generation)
    assert not team_run.context_full(generation)
    team_run.clear()


def _usage(chunks: list[dict], prompt: int) -> list[dict]:
    return [*chunks, {"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": 5}}]


async def test_a_full_context_pauses_a_subagent_for_one_final_turn_then_it_ends(
    db, team_ready, monkeypatch  # noqa: F811
):
    from lembas.db.models import Model
    from lembas.services import wake as wake_service

    model = db.scalars(select(Model)).first()
    model.context_length = 1000
    db.commit()
    finals: list[str] = []

    def script(payload: dict) -> list[dict]:
        messages = payload["messages"]
        system = str(messages[0].get("content") or "")
        last_user = next(str(m.get("content") or "") for m in reversed(messages)
                         if m.get("role") == "user")
        if '"api"' in system:
            if "your context is full" in last_user:
                finals.append(last_user)
                return _say("Saved; the auth routes are left.")
            return _usage(_call("team_findings", {"action": "write", "text": "Half done."}), 900)
        users = [m for m in messages if m.get("role") == "user"]
        tools_after = sum(1 for m in messages[messages.index(users[-1]):]
                          if m.get("role") == "tool")
        if len(users) == 1 and tools_after == 0:
            return _call("team_add", {"name": "api", "role": "writes routes",
                                      "task": "Write routes.", "goal": "An API"})
        return _say("Noted.")

    async def stream_chat(_endpoint, payload, **_):
        for chunk in script(payload):
            yield chunk

    async def no_title(*_args, **_kwargs):
        return None

    monkeypatch.setattr(generation_service, "stream_chat", stream_chat)
    monkeypatch.setattr("lembas.services.chat.generate_title", no_title)
    monkeypatch.setattr(team_run, "POLL_SECONDS", 0.05)
    chat = _chat(db, team_ready)
    lead_id = chat.id
    await wake_service.wake_chat(lead_id, "Build the API.")

    def gone() -> bool:
        with session_scope() as s:
            row = s.scalars(select(TeamAgent).where(TeamAgent.name == "api")).first()
            return row is not None and row.state == rules.OUT_OF_CONTEXT

    await _until(gone)
    assert len(finals) == 1
    with session_scope() as s:
        reports = [m.text for m in s.scalars(
            select(TeamMessage).where(TeamMessage.lead_chat_id == lead_id,
                                      TeamMessage.report.is_(True)))]
    assert any(r.startswith("api (s1) ran out of context and has ended.")
               and r.endswith("Saved; the auth routes are left.") for r in reports)
