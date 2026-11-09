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

import pytest

from lembas.api.v2 import APP_API, serialise
from lembas.api.v2.stream import frame
from lembas.db.models import Chat, Message, Model
from lembas.services import generation as generation_service
from lembas.services import interaction
from lembas.services import metrics as metrics_service

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
    approval = interaction.Item(
        index=0,
        key="a0",
        kind=interaction.KIND_APPROVAL,
        tool_name="bash",
        title="rm -rf build",
        detail="Removes the build directory.",
        purpose="Start clean",
        editable=True,
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
        interaction.Interruption(id="card2", items=(approval,), why="explore (job1) asks"),
        interaction.Interruption(id="card3", items=(plan,), dismissable=True),
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


def _shapes() -> dict[str, object]:
    generation = _generation()
    tail = serialise.tail(generation, "4s")
    for card in generation.waiting:
        card.expires_at = 0.0  # a monotonic deadline means nothing in a file
    assistant = serialise.message(_assistant())
    return {
        "chat": serialise.chat(_chat(), running=False),
        "models": {
            "models": [
                serialise.model(row, default=index == 0) for index, row in enumerate(_models())
            ]
        },
        "chat_agent": serialise.chat(_agent_chat(), running=True),
        "message_user": serialise.message(_user()),
        "message_assistant": assistant,
        "event_steps": {"steps": serialise.live_steps(generation), "tail": tail},
        "event_tail": tail,
        "event_steered": {"ids": ["msg9"]},
        "event_plan": serialise.plan(generation.plan),
        "event_status": {"text": generation.status},
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
