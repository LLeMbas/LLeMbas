"""A tool call drawn by its kind: a command with its output behind the
click and no host line, an edit with its most-changed lines open, a read as one
line, a search with its hits -- the same live and after a reload, for a device's
calls and this instance's own alike.
"""

from __future__ import annotations

from types import SimpleNamespace

from lembas.services import device_link, steps, tool_blocks
from lembas.services import generation as generation_service
from lembas.web.templating import templates

DIFF = "\n".join(
    [
        "--- a/app.py",
        "+++ b/app.py",
        "@@ -1,3 +1,3 @@",
        " import os",
        "-x = 1",
        "+x = 2",
        "@@ -40,6 +40,9 @@",
        " def main():",
        "-    pass",
        "+    run()",
        "+    stop()",
        "+    log()",
        "+    done()",
        "@@ -90,2 +93,2 @@",
        "-a",
        "+b",
    ]
)


def _render(events: list[dict]) -> str:
    return templates.get_template("chat/_tool_activity.html").render(
        tool_events=events, message_id="m1", step_index=0, live=False
    )


def test_kinds_come_from_the_spec_and_old_names_alike():
    assert tool_blocks.kind_of("bash") == tool_blocks.SHELL
    assert tool_blocks.kind_of("shell_run") == tool_blocks.SHELL
    assert tool_blocks.kind_of("apply_patch") == tool_blocks.EDIT
    assert tool_blocks.kind_of("file_write") == tool_blocks.EDIT
    assert tool_blocks.kind_of("read") == tool_blocks.READ
    assert tool_blocks.kind_of("grep") == tool_blocks.SEARCH
    assert tool_blocks.kind_of("todo") == tool_blocks.HIDDEN
    assert tool_blocks.kind_of("some_mcp_tool") == tool_blocks.TOOL


def test_the_preview_keeps_the_most_changed_hunks_in_file_order():
    lines, more = tool_blocks.preview(DIFF, budget=6)
    shown = [text for _, text in lines]
    # The middle hunk changed most (5 lines), then the first (2) would pass 6.
    assert "+    run()" in shown
    assert shown.index("@@ -40,6 +40,9 @@") >= 0
    assert more is True
    assert tool_blocks.diff_counts(DIFF) == (6, 3)


def test_a_device_call_keeps_what_its_block_needs():
    g = generation_service.Generation(chat_id="c", message_id="m")
    device_link.apply_event(
        g,
        {
            "type": "tool_start",
            "id": "t1",
            "name": "bash",
            "args": {"command": "npm test", "description": "run the tests"},
        },
    )
    device_link.apply_event(
        g,
        {
            "type": "tool_end",
            "id": "t1",
            "name": "bash",
            "result": {
                "output": "12 passing",
                "title": "run the tests · exit 0",
                "meta": {"exit": 0, "cwd": "~/app"},
            },
        },
    )
    event = g.tool_events[-1]
    assert event["args"]["command"] == "npm test"
    assert event["exit"] == 0
    assert "detail" not in event
    block = tool_blocks.block_for(event)
    assert block.kind == "shell" and block.title == "npm test" and block.chips == ("exit 0",)


def test_a_command_shows_itself_and_no_host_line():
    html = _render(
        [
            {
                "name": "bash",
                "kind": "agent",
                "query": "x",
                "args": {"command": "ls -la"},
                "text": "total 0",
                "status": "ok",
                "exit": 0,
                "detail": "device-1:/home/dev/project",
            }
        ]
    )
    assert "ls -la" in html and "total 0" in html
    assert "device-1:/home/dev/project" not in html
    assert "tool-activity--shell" in html


def test_an_edit_shows_its_change_open_and_the_rest_one_click_away():
    big = DIFF + "\n@@ -200,1 +203,20 @@\n" + "\n".join(f"+line {n}" for n in range(20))
    html = _render(
        [
            {
                "name": "edit",
                "kind": "agent",
                "query": "app.py",
                "args": {"path": "app.py"},
                "diff": big,
                "status": "ok",
                "text": "",
            }
        ]
    )
    assert "tool-edit" in html and "diff--preview" in html
    assert "+26" in html and "−3" in html
    assert "Show the whole change" in html
    # The preview is not inside a closed <details>.
    assert html.index("diff--preview") < html.index("tool-edit__whole")


def test_a_read_is_one_line():
    html = _render(
        [
            {
                "name": "read",
                "kind": "agent",
                "query": "src/app.py · 1–40 of 120",
                "path": "src/app.py · 1–40 of 120",
                "text": "lots of code",
                "status": "ok",
            }
        ]
    )
    assert "tool-line--read" in html and "src/app.py · 1–40 of 120" in html
    assert "lots of code" not in html
    assert "<details" not in html


def test_live_and_reload_draw_the_same_blocks():
    g = generation_service.Generation(chat_id="c", message_id="m1")
    g.content.append("Changing it.")
    g.tool_events.append(
        {
            "name": "edit",
            "kind": "agent",
            "query": "app.py",
            "args": {"path": "app.py"},
            "diff": DIFF,
            "status": "ok",
            "text": "",
        }
    )
    g.close_step()
    live = "".join(
        templates.get_template("chat/_step.html").render(
            step=s, message=SimpleNamespace(id="m1", reasoning_ms=0)
        )
        for s in steps.closed_from(g, since=0)
    )
    stored = SimpleNamespace(
        content=g.text,
        reasoning="",
        error="",
        tool_calls_json=g.tool_events,
        steps_json=g.steps,
        reasoning_ms=0,
    )
    reloaded = "".join(
        templates.get_template("chat/_step.html").render(
            step=s, message=SimpleNamespace(id="m1", reasoning_ms=0)
        )
        for s in steps.for_message(stored)
    )
    assert live == reloaded
