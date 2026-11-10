"""The app's contract, written down as files: `tests/fixtures/api_v2/*.json`.

One example of every shape `/api/v2` returns and every frame its stream sends,
built from rows and a `Generation` through the real serialisers. The Android app
copies these files at a tagged server version, hash-locked, and parses every one
in its own tests -- so a change here that an app would misread is caught on both
sides rather than on a phone.

When a shape changes on purpose, regenerate and commit the files with the change:

    LEMBAS_WRITE_CONTRACT=1 pytest tests/test_api_v2_contract.py

A change an existing app would misread is a new `APP_API`, not a regenerate.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from lembas.api.v2 import APP_API, serialise
from lembas.api.v2.misc import SHOWN_PERMISSIONS
from lembas.api.v2.stream import frame, status_payload
from lembas.db.models import Chat, Message, Model
from lembas.services import device_link, interaction
from lembas.services import generation as generation_service
from lembas.services import metrics as metrics_service
from lembas.services import progress as progress_service

FIXTURES = Path(__file__).parent / "fixtures" / "api_v2"
WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _chat() -> Chat:
    return Chat(
        id="chat1",
        user_id="user1",
        kind="chat",
        title="Waybread",
        model_id="qwen",
        folder_id=None,
        pinned=False,
        archived=False,
        temporary=False,
        unread=True,
        agent_mode="",
        params_json={"reasoning_effort": "low"},
        system_prompt="",
        plan_json={},
        created_at=WHEN,
        updated_at=WHEN,
    )


def _agent_chat() -> Chat:
    chat = _chat()
    chat.id, chat.kind, chat.agent_mode = "chat2", "agent", "edit"
    chat.device_token_id, chat.project_dir = "token1", "/home/dev/project"
    chat.device_session_id = "ses_01waybread"
    return chat


def _models() -> list[Model]:
    """Three models, one per control a chat draws for their levels (harness
    spec 3.2.0): a Thinking switch, a slider, and none at all."""
    switch = Model(
        model_id="qwen", display_name="Qwen", context_length=131072,
        capabilities_json={"reasoning": True, "vision": False, "tools": True},
        reasoning_efforts=["medium"], thinking_switch=True, parallel=2,
        image_path="0a1b2c3d4e5f.png", params_json={},
    )
    slider = Model(
        model_id="gpt-oss", display_name="", context_length=0,
        capabilities_json={"reasoning": True, "vision": False, "tools": True},
        reasoning_efforts=["low", "medium", "high"], thinking_switch=False,
        single_session=True, image_path="", params_json={"reasoning_effort": "high"},
    )
    plain = Model(
        model_id="vision-small", display_name="Vision", context_length=8192,
        capabilities_json={"reasoning": False, "vision": True, "tools": False},
        reasoning_efforts=[], thinking_switch=False, image_path="", params_json={},
    )
    return [switch, slider, plain]


THINKING = "The reader wants a recipe."
TEXT = "Here is how it is made:\n\n```sh\nbake --slow\n```\n"
LATER = "Done. *Wrap it in a leaf.*"
TOOL = {
    "id": "call1",
    "name": "bash",
    "kind": "shell",
    "status": "ok",
    "args": {"command": "bake --slow"},
    "text": "baked\n",
    "exit": 0,
    "purpose": "Bake one loaf",
}

# A `show` card: one from the chat's own tool (a file in the tree), one a linked
# device sent. The card names the file; its text is fetched separately
# (`GET /api/v2/chats/{chat}/show`) and never rides in the event.
SHOW_TREE = {
    "id": "call2",
    "name": "show",
    "status": "ok",
    "path": "/home/frodo/recipe.md",
    "query": "/home/frodo/recipe.md",
    "show": {
        "path": "/home/frodo/recipe.md",
        "name": "recipe.md",
        "kind": "markdown",
        "language": "markdown",
        "line": 3,
        "node_id": "node1",
        "version_id": "ver1",
        "sha": "a" * 64,
        "size": 120,
        "mime": "text/markdown",
    },
}
SHOW_DEVICE = {
    "id": "call3",
    "name": "show",
    "status": "ok",
    "path": "src/oven.py",
    "show": {
        "path": "src/oven.py",
        "name": "oven.py",
        "kind": "code",
        "language": "python",
        "line": None,
        "node_id": "",
        "version_id": "",
        "sha": "b" * 64,
        "size": 64,
        "mime": "text/x-python",
        "source": "device",
        "shown_id": "shown1",
        "url": "/api/chats/chat2/shown/shown1",
    },
}


def _assistant() -> Message:
    return Message(
        id="msg2",
        chat_id="chat1",
        role="assistant",
        content=TEXT + LATER,
        reasoning=THINKING,
        reasoning_ms=1200,
        tool_calls_json=[TOOL],
        steps_json=[
            {
                "thinking_to": len(THINKING),
                "text_to": len(TEXT),
                "tools_to": 1,
                "thinking_ms": 1200,
                "steer": [{"id": "msg9", "text": "and add honey"}],
            }
        ],
        usage_json={
            "prompt_tokens": 120,
            "completion_tokens": 40,
            "total_tokens": 160,
            "context_tokens": 160,
            "context_limit": 8192,
            "estimated": False,
            "elapsed_ms": 4000,
            "rounds": 2,
            "thinking_ms": 1200,
        },
        plan_json={},
        model_id="qwen",
        complete=True,
        stopped=False,
        error="",
        queued=False,
        machine=False,
        origin="",
        created_at=WHEN,
    )


def _user() -> Message:
    return Message(
        id="msg1",
        chat_id="chat1",
        role="user",
        content="How is lembas made?",
        complete=True,
        stopped=False,
        error="",
        queued=False,
        machine=False,
        origin="",
        created_at=WHEN,
    )


def _queued() -> Message:
    """A prompt typed while a reply runs, behind it and one earlier prompt."""
    row = _user()
    row.id, row.content, row.queued, row.complete = "msg3", "And the leaves?", True, False
    return row


def _plan() -> dict:
    return {"title": "Bake lembas", "steps": ["Gather mallorn leaves", "Bake slowly"]}


def _cards() -> list[interaction.Interruption]:
    question = interaction.Item(
        index=0,
        key="q0",
        kind=interaction.KIND_QUESTION,
        tool_name="ask_user",
        title="Honey or not?",
        header="Taste",
        options=(
            interaction.Option(label="Honey", recommended=True),
            interaction.Option(label="Plain", description="As the elves have it"),
        ),
    )
    # A linked device's approval, as `device_link._permission` builds it: a
    # heading, the command as `detail` -- the text an `editable` item lets the
    # person correct, sent back as `texts[key]` -- and no free text.
    approval = interaction.Item(
        index=0,
        key="0",
        kind=interaction.KIND_APPROVAL,
        tool_name="bash",
        title="Run a command",
        detail="rm -rf build",
        reason="Edit mode asks before anything that runs a command.",
        purpose="Start clean",
        editable=True,
        allow_free_text=False,
        job="explore (job1)",
    )
    # Spec 3.4.0: a subagent of the device's tree asks for what nobody in the
    # tree could allow; the card names it.
    asked_by_subagent = interaction.Item(
        index=0,
        key="0",
        kind=interaction.KIND_APPROVAL,
        tool_name="bash",
        title="Run a command",
        detail="git push",
        reason="Edit mode asks before anything that runs a command.",
        editable=True,
        allow_free_text=False,
        subagent=("s1", "parser", "reviews the parser"),
    )
    plan = interaction.Item(
        index=0,
        key="p0",
        kind=interaction.KIND_PLAN,
        tool_name="plan_submit",
        title="Bake lembas",
        plan=_plan(),
        mode_choice=True,
    )
    return [
        interaction.Interruption(id="card1", items=(question,)),
        interaction.Interruption(id="card2", items=(approval,)),
        interaction.Interruption(id="card3", items=(plan,), dismissable=True),
        interaction.Interruption(id="card4", items=(asked_by_subagent,)),
    ]


def _generation() -> generation_service.Generation:
    generation = generation_service.Generation(chat_id="chat1", message_id="msg2")
    generation.reasoning = [THINKING]
    generation.content = [TEXT, "Done. *Wrap"]
    generation.tool_events = [TOOL]
    generation.steps = list(_assistant().steps_json)
    generation.status = "Running bake --slow"
    generation.plan = _plan()
    generation.waiting = _cards()
    generation.jobs = {
        "job1": {
            "id": "job1",
            "kind": "subagent",
            "state": "running",
            "description": "Find the recipe",
            "agent": "explore",
            "model": "qwen",
            "started": WHEN.isoformat(),
            "ended": None,
            "seconds": 12,
            "unread": 0,
            "steps": 3,
            "tools": 2,
            "background": True,
            "exit": None,
            "command": "",
        }
    }
    generation.prompt_tokens, generation.completion_tokens = 120, 20
    generation.context_limit = 8192
    return generation


def _team() -> dict[str, object]:
    """`GET /chats/{chat}/team`: a lead, a member that leads an area and its
    member, and the room -- a message and a report."""
    agent = {"agent": "general", "lead": False, "model": "local/qwen"}
    return {
        "offered": True,
        "min": 1,
        "max": 4,
        "size": 3,
        "parallel": 2,
        "goal": "Bake for the journey",
        "agents": [
            {**agent, "name": "lead", "manager": None, "role": "", "state": "working",
             "agent": "", "lead": True, "depth": 0},
            {**agent, "name": "baker", "manager": "lead", "role": "owns the oven",
             "state": "working", "lead": True, "depth": 1},
            {**agent, "name": "wrapper", "manager": "baker", "role": "owns the leaves",
             "state": "waiting", "depth": 2},
        ],
        "room": [
            {"n": 1, "from": "baker", "to": ["wrapper"], "text": "Leaves by noon?",
             "report": False, "created": WHEN.isoformat()},
            {"n": 2, "from": "wrapper", "to": ["baker"], "text": "Done: *mallorn* leaves.",
             "report": True, "created": WHEN.isoformat()},
        ],
        "cursor": 2,
    }


def _activity() -> dict[str, object]:
    """`GET /chats/{chat}/activity`, `GET .../agents/{kind}/{id}` and the
    `activity` event: a member that leads with its member and a helper of
    its own, a command of the lead's, the room, the context and an output."""
    member = {
        "kind": "member", "id": "baker", "type": "member", "name": "baker",
        "role": "owns the oven", "brief": "Bake the waybread.", "state": "working",
        "seconds": 95, "started": WHEN.isoformat(), "model": "local/qwen", "agent": "general",
        "manager": "lead", "depth": 0, "lead": True,
        "step": {"tool": "read", "label": "Read", "title": "recipe.md", "error": False,
                 "running": True},
        "text": "The dough needs…", "steps": 4, "tools": 3, "stoppable": True,
        "transcript": True,
        "children": [
            {"kind": "member", "id": "wrapper", "type": "member", "name": "wrapper",
             "role": "owns the leaves", "brief": "Wrap each cake.", "state": "queued",
             "seconds": 0, "started": WHEN.isoformat(), "model": "local/qwen",
             "agent": "general", "manager": "baker", "depth": 1, "lead": False,
             "step": None, "text": "", "steps": 0, "tools": 0, "stoppable": True,
             "transcript": True, "children": []},
            {"kind": "job", "id": "baker:job1", "type": "subagent", "name": "baker:job1",
             "role": "find mallorn leaves", "brief": "Where do mallorn grow?",
             "state": "done", "seconds": 12, "started": WHEN.isoformat(),
             "model": "local/qwen", "agent": "explore", "manager": "baker", "depth": 1,
             "lead": False, "step": None, "text": "In Lothlórien.", "steps": 2, "tools": 1,
             "stoppable": False, "transcript": True, "children": []},
        ],
    }
    command = {
        "kind": "job", "id": "job1", "type": "command", "name": "job1",
        "role": "pytest", "brief": "pytest -q", "state": "working", "seconds": 30,
        "started": WHEN.isoformat(), "model": "", "agent": "", "manager": "lead",
        "depth": 0, "lead": False, "step": None, "text": "", "steps": 0, "tools": 0,
        "stoppable": True, "transcript": False, "children": [],
    }
    summary = {"queued": 1, "working": 2, "waiting": 0, "done": 1, "stopped": 0, "error": 0,
               "active": 3, "finished": 1, "total": 4}
    view = {
        "version": "0123456789abcdef",
        "summary": summary,
        "agents": [member, command],
        "team": {k: v for k, v in _team().items()
                 if k in ("min", "max", "size", "goal", "room", "cursor")},
        "team_offered": True,
        "context": {
            "used": 41000, "limit": 128000, "percent": 32.0, "measured": True,
            "threshold": 95, "auto_compact": True, "until_compact": 80600,
            "compactable": True, "tools_count": 24, "mcp_count": 2,
            "categories": [
                {"key": "messages", "tokens": 30000, "percent": 23.4},
                {"key": "system", "tokens": 4000, "percent": 3.1},
                {"key": "free", "tokens": 80600, "percent": 63.0},
            ],
        },
        "outputs": [
            {"path": "/home/frodo/recipe.md", "name": "recipe.md", "writes": 2,
             "created": False, "added": 42, "removed": 6, "at": WHEN.isoformat(),
             "url": "/files/view?path=/home/frodo/recipe.md"},
        ],
        "plan": None,
    }
    turns = [
        {"role": "user", "text": "Bake the waybread.", "machine": False},
        {"role": "assistant", "text": "Baking.", "tools": [
            {"name": "read", "title": "recipe.md", "isError": False}],
         "error": "", "running": True},
    ]
    return {
        "activity": {"activity": serialise.activity(view)},
        "agent": {"agent": serialise.agent(member), "transcript": turns},
        "event_activity": {"version": "0123456789abcdef", "summary": summary},
    }


def _files() -> dict[str, object]:
    """`GET /files` and `GET /files/content`: a listing, a text file, a withheld one."""
    from lembas.services.file_ui import Opened, Row

    rows = [
        Row(name="recipes", path="/home/frodo/recipes", kind="dir", size=0, level="commit",
            updated_at=WHEN, node_id="node1", mime=""),
        Row(name="lembas.md", path="/home/frodo/lembas.md", kind="file", size=27,
            level="commit", updated_at=WHEN, node_id="node2", mime="text/markdown"),
    ]

    def opened(**kwargs) -> Opened:
        base = {"path": "/home/frodo/lembas.md", "name": "lembas.md", "node": None,
                "version": None, "size": 27, "sha": "ab" * 32, "mime": "text/markdown",
                "kind": "markdown", "language": "markdown",
                "text": "# Lembas\n\nWrap it in a leaf.\n", "level": "commit"}
        return Opened(**{**base, **kwargs})

    picture = opened(path="/home/frodo/map.png", name="map.png", size=2048,
                     mime="image/png", kind="image", language="", text="")
    return {
        "files_listing": serialise.file_listing("/home/frodo", rows),
        "file_content": serialise.file_content(opened()),
        "file_content_withheld": serialise.file_content(
            picture,
            {"reason": serialise.WITHHELD_NOT_TEXT,
             "message": "This is not a text file, so it is not shown here."},
        ),
    }


def _schedules() -> dict[str, object]:
    """`/schedules`, a run, the compile, the preview, the scheduling models and
    the `schedules` event of `GET /events`."""
    from lembas.api.v2.schedules import version_of
    from lembas.db.models import Schedule, ScheduleRun, User
    from lembas.services.schedule import rule, runs

    owner = User(id="user1", settings_json={"timezone": "UTC"})
    schedule = Schedule(
        id="sched1", user_id="user1", title="Waybread check",
        instruction="Check the waybread is wrapped.", request="every second Monday at 7:30",
        rule_json=rule.validate({"at": {"nth": [{"n": 2, "weekday": 0}], "times": ["07:30"]}}),
        target="chat", chat_id="chat1", model_id="qwen", enabled=True,
        next_fire_at=datetime(2026, 1, 12, 7, 30, tzinfo=UTC), fired_count=3,
        last_error="", origin="user", mode="fresh", notify="matters", catch_up="one",
        grace_minutes=None, effort="high", agents=None, tools_json=[], failure_streak=0,
        created_at=WHEN,
    )
    run = ScheduleRun(
        id="run1", schedule_id="sched1", user_id="user1", status="ok", timing="late",
        missed=0, due_at=WHEN, first_try_at=WHEN, started_at=WHEN, finished_at=WHEN,
        waiting_for="", retry_at=None, attempts=1, retries=0, model_id="gpt-oss",
        connection_id="conn1", model_label="GPT-OSS", model_reason="switched",
        switched_from="Qwen", prompt_tokens=1200, completion_tokens=340, cost_micro=0,
        currency="", chat_id="chat1", message_id="msg2", report_path="",
        preview="All the waybread is wrapped.", error_class="", error="", notified=True,
    )
    waiting = ScheduleRun(
        id="run2", schedule_id="sched1", user_id="user1", status="waiting", timing="on_time",
        missed=2, due_at=WHEN, first_try_at=WHEN, started_at=None, finished_at=None,
        waiting_for="model", retry_at=datetime(2026, 1, 2, 3, 9, 5, tzinfo=UTC), attempts=2,
        retries=0, model_id="", connection_id=None, model_label="", model_reason="",
        switched_from="", prompt_tokens=0, completion_tokens=0, cost_micro=0, currency="",
        chat_id="", message_id="", report_path="", preview="", error_class="no_model",
        error="Waiting for a free model: Qwen: it is answering someone.", notified=False,
        waiting_json={"model": "Qwen", "reason": "busy", "since": WHEN_MS},
    )
    from lembas.services import push
    from lembas.services.schedule import notify

    def english(text: str) -> str:
        return text

    told, told_extra = notify.push_parts(
        schedule, run, failed=False, ask_pause=False, translate=english
    )
    run.notification_json = told_extra["notification"]
    failed = ScheduleRun(
        id="run3", schedule_id="sched1", user_id="user1", status="failed", timing="on_time",
        missed=0, due_at=WHEN, chat_id="chat1", message_id="msg4", preview="",
        error_class="model", error="The model's server answered 500: out of memory.\nmore",
        notified=True,
    )
    asked, asked_extra = notify.push_parts(
        schedule, failed, failed=True, ask_pause=True, translate=english
    )
    pushes = {
        "push_schedule": push.build_payload(
            "LLeMbas", body=told, url=notify.link(schedule, run), kind=notify.KIND_RESULT,
            extra=told_extra,
        ),
        "push_schedule_failed": push.build_payload(
            "LLeMbas", body=asked, url=notify.link(schedule, failed), kind=notify.KIND_FAILED,
            extra=asked_extra,
        ),
    }
    listed = [runs.schedule_dict(None, schedule, owner=owner, last=run)]
    models = _models()
    return {
        "schedule": {"schedule": listed[0]},
        "schedules": {"schedules": listed},
        "schedule_runs": {"runs": [runs.run_dict(waiting), runs.run_dict(run)], "more": False},
        "schedule_run": {"run": runs.run_dict(run), "text": "All the waybread is *wrapped*."},
        "schedule_compile": {
            "ok": True, "reason": None, "title": "Waybread check",
            "instruction": "Check the waybread is wrapped.", "target": "chat",
            "rule": schedule.rule_json,
            "description": "On the second Monday of each month at 07:30",
            "next_runs": ["2026-01-12T07:30:00+00:00", "2026-02-09T07:30:00+00:00",
                          "2026-03-09T07:30:00+00:00", "2026-04-13T07:30:00+00:00",
                          "2026-05-11T07:30:00+00:00"],
            "timezone": "UTC",
        },
        "schedule_preview": {
            "rule": {}, "description": None, "next_runs": [], "timezone": "UTC",
        },
        "schedule_models": {
            "models": [
                {**serialise.model(models[1]), "free": False,
                 "busy": "it is answering someone", "chosen": True},
                {**serialise.model(models[0]), "free": True, "busy": None, "chosen": False},
            ],
            "instance": ["gpt-oss", "qwen"],
            "mine": ["gpt-oss"],
        },
        "event_schedules": {"version": version_of(listed), "schedules": listed},
        **pushes,
    }


WHEN_MS = int(WHEN.timestamp() * 1000)

TREE = {
    "min": 1, "max": 4, "size": 2, "open_slots": 1,
    "agents": [
        {"id": "lead", "name": "lead", "position": "the main agent", "parent": None,
         "state": "working", "model": "qwen-27b", "mode": "edit", "request": [],
         "manager": True, "context": {"used": 41200, "window": 131072},
         "progress": {"phase": "thinking", "since": WHEN_MS}, "findings": 0, "messages": 2},
        {"id": "s2", "name": "parser", "position": "reviews the parser", "parent": "lead",
         "state": "working", "model": "qwen-27b", "mode": "manual", "request": ["write"],
         "manager": False, "task": "Review src/parse.ts.",
         "context": {"used": 9100, "window": 131072},
         "progress": {"phase": "running_tool", "since": WHEN_MS, "tool": "read",
                      "what": "src/parse.ts"},
         "findings": 2, "messages": 3},
    ],
    "ended": [
        {"id": "s1", "name": "scout", "position": "maps the code", "parent": "lead",
         "state": "done", "model": "qwen-27b", "mode": "edit", "request": [],
         "manager": False, "task": "Map it.", "progress": None, "findings": 1, "messages": 1,
         "ended": WHEN_MS},
    ],
}


def _statuses() -> dict[str, object]:
    """The `status` frame (spec 3.4.0, progress.md §5): the free text and the
    phase beside it -- a tool running, the person's turn (a card open), how a
    stopped reply ended, and a reply with no phase."""
    clock = progress_service.now_ms
    progress_service.now_ms = lambda: WHEN_MS
    try:
        running = generation_service.Generation(chat_id="chat1", message_id="msg2")
        running.status = "Running bake --slow"
        running.tracker.set("running_tool", tool="bash", what="bake --slow")
        asking = _generation()
        asking.status = "Waiting for you to allow bash…"
        stopped = generation_service.Generation(chat_id="chat1", message_id="msg2")
        stopped.tracker.set("stopped", reason="person")
        stopped.done = True
        idle = generation_service.Generation(chat_id="chat1", message_id="msg2")
        return {
            "event_status": status_payload(running),
            "event_status_person": status_payload(asking),
            "event_status_stopped": status_payload(stopped),
            "event_status_idle": status_payload(idle),
        }
    finally:
        progress_service.now_ms = clock


def _tree() -> dict[str, object]:
    """`GET /chats/{chat}/tree` for a chat on the server (spec 3.4.0): the
    main agent, a sub-manager with a narrowed subagent under it, one that
    finished, and the room -- built by the rules, as `tree_view` reads them."""
    from lembas.services import team as rules
    from lembas.services import tree_view

    now = WHEN.timestamp()
    team = rules.Team(min=2, max=4)
    team.lead.model, team.lead.mode = "local/qwen", "auto"
    rules.add(team, rules.LEAD, {"name": "area", "role": "owns the API", "task": "Direct it.",
                                 "lead": True, "goal": "An API"}, model="local/qwen")
    rules.add(team, rules.LEAD, {"name": "routes", "role": "writes the routes",
                                 "task": "Write the routes.", "parent": "area",
                                 "mode": "edit", "request": ["execute"]}, model="local/qwen")
    rules.add(team, rules.LEAD, {"name": "scout", "role": "maps the code", "task": "Map it."},
              model="local/qwen")
    rules.note_finding(team, "routes", "Login is POST /session.", now=now)
    rules.set_context(team, "routes", 9100, 131072)
    rules.set_progress(team, "routes", {"phase": "running_tool", "since": WHEN_MS,
                                        "tool": "read", "what": "src/api.py"})
    rules.set_progress(team, rules.LEAD, {"phase": "waiting_for_subagents", "since": WHEN_MS,
                                          "running": 2})
    rules.send(team, "routes", ["parent"], "Routes are in.", now=now)
    rules.finish_task(team, "scout", "Mapped: see map.md.", now=now)
    for post in team.room:
        post.created = now
    return {"tree": rules.tree_info(team),
            "room": [tree_view._room_entry(p) for p in team.room], "live": True}


def _shapes() -> dict[str, object]:
    generation = _generation()
    tail = serialise.tail(generation, "4s")
    for card in generation.waiting:
        card.expires_at = 0.0  # a monotonic deadline means nothing in a file
    assistant = serialise.message(_assistant())
    return {
        "chat": serialise.chat(
            _chat(), running=False, subagents={"offered": True, "named_only": False, "cap": 32}
        ),
        # The first serves its own subagents; the second, one request at a
        # time, only a model the person names on another server; the third
        # has no tools, so none (harness spec 3.3.0, subagents.md §4).
        "models": {
            "models": [
                serialise.model(
                    row,
                    default=index == 0,
                    subagents=[
                        {"offered": True, "named_only": False},
                        {"offered": True, "named_only": True},
                        {"offered": False, "named_only": False},
                    ][index % 3],
                )
                for index, row in enumerate(_models())
            ],
            "subagents_cap": 32,
        },
        # `/models?kind=agent&device=` for a new agent chat (spec 3.4.0):
        # every model says the agent chat's fact, and `agent_subagents` once.
        "models_agent": {
            "models": [
                serialise.model(row, default=index == 0,
                                subagents={"offered": True, "named_only": False})
                for index, row in enumerate(_models())
            ],
            "subagents_cap": 32,
            "agent_subagents": {"offered": True, "device_runs": True},
        },
        # An agent chat's subagents are its device's (spec 3.4.0, subagents.md
        # §14): offered where the instance and the person allow them; the
        # setting goes down with every turn.
        "chat_agent": serialise.chat(
            _agent_chat(), running=True,
            subagents={"offered": True, "named_only": False, "cap": 32},
        ),
        # A page of the sidebar: `subagents` is not worked out there (null).
        "chats_listed": {"chats": [serialise.chat(_chat(), running=False)], "more": False},
        "message_user": serialise.message(_user()),
        "message_queued": serialise.message(_queued(), ahead=2),
        "message_assistant": assistant,
        "event_steps": {"steps": serialise.live_steps(generation), "tail": tail},
        "event_tail": tail,
        "event_steered": {"ids": ["msg9"]},
        "event_plan": serialise.plan(generation.plan),
        **_statuses(),
        # The subagent tree (spec 3.4.0, subagents.md §12) as the stream's
        # `tree` frame carries it: an agent chat's, from its device.
        "event_tree": {"tree": device_link.clean_tree(TREE)},
        "tree": _tree(),
        # `GET /me` (Android reads `language` and `services`): the account,
        # what it may do and which services it may use. `version` is the
        # server's own; here a fixed one.
        "me": serialise.me(
            SimpleNamespace(id="user1", name="Frodo", email="frodo@shire.test",
                            settings_json={"theme": "shire"}),
            version="2.0.0", language="sk", default_model="qwen",
            permissions={key: key != "chat.delete" for key in SHOWN_PERMISSIONS},
            services={"stt": True, "tts": False, "search": True, "fetch": True,
                      "images": False},
        ),
        # A subagent's conversation (read-only) and its findings.
        "subagent_transcript": {
            "id": "s2", "state": "working",
            "turns": [
                {"role": "user", "text": "Write the routes.", "machine": True},
                {"role": "assistant", "text": "Reading the API first.",
                 "tools": [{"name": "read", "title": "src/api.py", "isError": False}],
                 "error": "", "running": True},
            ],
        },
        "subagent_findings": {
            "id": "s2", "findings": [{"n": 1, "text": "Login is POST /session.",
                                      "time": WHEN_MS}],
        },
        "event_ask": {"cards": serialise.asks(generation.waiting)},
        "event_ask_empty": {"cards": []},
        "event_jobs": {"jobs": serialise.jobs(generation.jobs)},
        "event_metrics": serialise.metrics(metrics_service.from_message(_assistant().usage_json)),
        "event_done": {
            "message": assistant,
            "chat": {"id": "chat1", "title": "Waybread"},
            "next": None,
        },
        "event_close": {},
        "tool_show": {"calls": [serialise.tool_call(SHOW_TREE), serialise.tool_call(SHOW_DEVICE)]},
        "team": {"team": serialise.team(_team())},
        **_files(),
        **_activity(),
        **_schedules(),
    }


@pytest.mark.parametrize("name", sorted(_shapes()))
def test_the_shape_matches_its_fixture(name):
    shape = json.loads(json.dumps(_shapes()[name]))
    path = FIXTURES / f"{name}.json"
    if os.environ.get("LEMBAS_WRITE_CONTRACT"):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(shape, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert path.exists(), f"{path.name} missing: LEMBAS_WRITE_CONTRACT=1 pytest {__file__}"
    assert json.loads(path.read_text(encoding="utf-8")) == shape, (
        f"{name} changed. If on purpose, regenerate (see the module docstring); if an "
        "existing app would misread it, it is a new APP_API."
    )


def test_every_fixture_is_still_produced():
    """A file nobody produces any more is a shape the app still trusts."""
    produced = {f"{name}.json" for name in _shapes()}
    on_disk = {p.name for p in FIXTURES.glob("*.json")} - {"version.json"}
    assert on_disk == produced


def test_the_version_file_names_the_api():
    path = FIXTURES / "version.json"
    if os.environ.get("LEMBAS_WRITE_CONTRACT"):
        path.write_text(json.dumps({"app_api": APP_API}) + "\n", encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8")) == {"app_api": APP_API}


def test_a_frame_is_one_sse_event_with_json_in_it():
    text = frame("status", {"text": "Thinking"})
    assert text.startswith("event: status\n") and json.loads(
        text.split("data: ", 1)[1].strip()
    ) == {"text": "Thinking"}


def test_the_steps_carry_markdown_and_no_html():
    steps = _shapes()["message_assistant"]["steps"]
    kinds = [s["kind"] for s in steps]
    assert kinds == ["thinking", "text", "tools", "steer", "text"]
    assert steps[1]["markdown"].startswith("Here is how it is made")
    assert steps[2]["calls"][0]["block"] == {
        "kind": "shell",
        "title": "bake --slow",
        "chips": ["exit 0"],
        "added": 0,
        "removed": 0,
    }
    assert "html" not in json.dumps(steps)


def test_a_show_card_names_the_file_and_never_carries_it():
    tree, device = _shapes()["tool_show"]["calls"]
    assert tree["show"]["node_id"] == "node1" and tree["show"]["version_id"] == "ver1"
    assert "source" not in tree["show"]
    assert device["show"]["source"] == "device" and device["show"]["shown_id"] == "shown1"
    # The cookie door's address is the page's, not the app's.
    assert "url" not in device["show"]
    assert "text" not in tree["show"] and "text" not in device["show"]


def test_me_has_the_keys_its_fixture_has(client, db, registered):
    """The fixture's `me` is built from fixed values; the route's answer has
    exactly its keys, and its permissions and services keys, so the two
    cannot drift apart."""
    from sqlalchemy import select

    from lembas.db.models import User
    from lembas.security import api_tokens

    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="app", scopes=["app"])
    got = client.get("/api/v2/me", headers={"Authorization": f"Bearer {raw}"}).json()
    want = json.loads((FIXTURES / "me.json").read_text(encoding="utf-8"))
    assert set(got) == set(want)
    assert set(got["services"]) == set(want["services"])
    assert set(got["permissions"]) == set(want["permissions"])
    assert set(got["account"]) == set(want["account"])
