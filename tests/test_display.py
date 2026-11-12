"""One display everywhere (harness spec 3.7.0, `display.md`), on the server.

The spec's own cases run in `tests/test_harness_spec.py` (area `display`).
These are the places the rule is used: how a call is drawn on the page and
sent to the apps, what a device's events become, and the end a reply keeps.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from lembas.api.v2 import serialise
from lembas.db.models import Chat, Message, User
from lembas.services import device_link, display, interaction, tool_blocks
from lembas.services import generation as generation_service
from lembas.services import progress as progress_service


# --- A call's title, state, duration and facts ------------------------------------------
def test_a_diff_is_counted_by_its_hunks_whatever_its_headers_look_like():
    """Counted by line prefix, with only `--- a/` and `+++ b/` known as
    headers, a patch as the LLeMbas CLI writes them -- no `a/`, no `b/` -- was
    one line removed and one added that nobody wrote."""
    cli = "--- src/app.py\n+++ src/app.py\n@@ -1,2 +1,3 @@\n one\n+one and a half\n two\n"
    assert tool_blocks.diff_counts(cli) == (1, 0)
    git = "--- a/q.sql\n+++ b/q.sql\n@@ -1,3 +1,3 @@\n select 1;\n--- old\n+++ new\n select 2;\n"
    assert tool_blocks.diff_counts(git) == (1, 1), "a removed line that begins with `-- `"
    assert tool_blocks.diff_counts("") == (0, 0)


def test_a_call_has_one_title_and_it_is_the_progress_lines():
    args = {"pattern": "TODO\\(", "path": "src"}
    block = tool_blocks.block_for({"name": "grep", "args": args, "status": "ok", "hits": 3})
    # What it does before where: a grep by its pattern, not its directory.
    assert block.title == "TODO\\(" == progress_service.what_of(args) == display.title(args)
    assert block.chips == ("3 hits",)
    bash = {"name": "bash", "args": {"command": "make\n  test", "description": "Build"}}
    assert tool_blocks.block_for({**bash, "status": "ok"}).title == "make"
    # A stored call that kept no arguments falls back to what it did keep.
    old = tool_blocks.block_for({"name": "bash", "query": "ls -la", "status": "ok"})
    assert old.title == "ls -la"


def test_a_block_says_which_state_it_is_in_and_how_long_it_took():
    call = {"name": "bash", "args": {"command": "make"}}
    running = tool_blocks.block_for({**call, "status": "running"})
    assert (running.state, running.duration, running.chips) == ("running", "", ())
    done = tool_blocks.block_for({**call, "status": "ok", "exit": 0, "ms": 8449})
    # `exit` is said only when it is not 0.
    assert (done.state, done.duration, done.chips) == ("done", "8.4s", ())
    failed = tool_blocks.block_for({**call, "status": "error", "exit": 2, "ms": 65_000})
    assert (failed.state, failed.duration, failed.chips) == ("failed", "1m 05s", ("exit 2",))
    denied = tool_blocks.block_for({**call, "status": "denied", "ms": 12})
    assert (denied.state, denied.duration) == ("denied", ""), "a call not run took no time"
    # A stored call whose time was not kept shows none.
    assert tool_blocks.block_for({**call, "status": "ok"}).duration == ""


def test_the_apps_get_the_label_the_state_and_the_time():
    sent = serialise.tool_call(
        {"name": "bash", "args": {"command": "make"}, "status": "ok", "exit": 0, "ms": 412}
    )
    assert sent["ms"] == 412
    assert sent["block"]["state"] == "done" and sent["block"]["duration"] == "0.4s"
    assert sent["block"]["title"] == "make" and sent["block"]["kind"] == "shell"
    assert sent["block"]["label"], "the tool's name as a person reads it"
    running = serialise.tool_call({"name": "grep", "args": {"pattern": "x"}, "status": "running"})
    assert running["block"]["state"] == "running" and "ms" not in running


async def test_a_servers_own_call_is_timed(monkeypatch):
    from lembas.services import tools as tools_service
    from lembas.services.tools import ToolOutcome

    async def run_tool(context, name, raw, parsed=None):
        await asyncio.sleep(0.05)
        return ToolOutcome("ok", {"name": name, "status": "ok"})

    monkeypatch.setattr(tools_service, "run_tool", run_tool)
    outcomes = await generation_service._run_calls(
        None, [{"name": "read", "arguments": "{}"}], [{}]
    )
    assert 40 <= outcomes[0].event["ms"] < 5000


# --- A call that is running ------------------------------------------------------------
def test_a_running_call_is_a_card_on_the_page_and_in_the_apps_tail():
    """A running call used to be a word in the progress line and nothing in
    the transcript."""
    from lembas.api import chats as chats_api

    generation = generation_service.Generation(chat_id="c", message_id="m")
    assert chats_api._running_calls_html("m", generation) == ""
    generation.start_call("call1", "bash", {"command": "make test"})
    generation.start_call("call2", "plan_write", {"title": "x"})  # never drawn as a call

    html = chats_api._running_calls_html("m", generation)
    assert "make test" in html and "tool-activity--running" in html
    assert html.count("tool-state--running") == 1
    tail = serialise.tail(generation, "")
    assert [c["block"]["state"] for c in tail["calls"]] == ["running"]
    assert tail["calls"][0]["block"]["title"] == "make test"
    assert "started" not in tail["calls"][0]

    generation.end_call("call1")
    generation.end_call()
    assert chats_api._running_calls_html("m", generation) == ""
    assert serialise.tail(generation, "")["calls"] == []


# --- What a device's events become --------------------------------------------------------
def test_a_devices_call_runs_takes_its_time_and_a_refused_one_is_denied():
    generation = generation_service.Generation(chat_id="c", message_id="m")
    device_link.apply_event(generation, {
        "type": "tool_start", "id": "t1", "name": "bash", "title": "bun test",
        "args": {"command": "bun test"},
    })
    assert [c["name"] for c in generation.running_calls.values()] == ["bash"]
    device_link.apply_event(generation, {
        "type": "tool_end", "id": "t1", "name": "bash", "ms": 8449,
        "result": {"output": "42 pass", "meta": {"exit": 0}},
    })
    assert generation.running_calls == {}
    ended = generation.tool_events[-1]
    assert ended["ms"] == 8449 and tool_blocks.block_for(ended).duration == "8.4s"

    # Refused: it never started. One event, and the state is `denied`.
    device_link.apply_event(generation, {
        "type": "tool_denied", "id": "t2", "name": "bash", "reason": "Not in Plan mode.",
    })
    refused = generation.tool_events[-1]
    block = tool_blocks.block_for(refused)
    assert (block.state, block.duration) == ("denied", "")
    assert "Not in Plan mode." in refused["error"]


async def test_a_devices_card_names_its_tool_and_offers_always_only_where_the_device_does():
    link = device_link.Link(token_id="tok", user_id="u1", name="cli", send_text=None,
                            close=None, hello={})
    generation = generation_service.Generation(chat_id="c", message_id="m")
    seen: list[interaction.Item] = []
    answered: list[dict] = []
    link.writer = lambda session_id: generation  # type: ignore[method-assign]

    async def ask(generation, pause, session_id, call_id, *, timeout):
        seen.extend(pause.items)
        return interaction.Reply(outcome=interaction.ALLOW_ALWAYS)

    async def answer(message_id, result=None, **_):
        answered.append(result)

    link._ask = ask  # type: ignore[method-assign]
    link._answer = answer  # type: ignore[method-assign]
    call = {"toolCallId": "t1", "title": "git push origin main", "kind": "execute",
            "rawInput": {"command": "git push origin main"}}
    once_only = [{"optionId": "once"}, {"optionId": "deny"}]
    await link._permission(1, {"sessionId": "s1", "toolCall": call, "options": once_only,
                               "_meta": {"lembas": {"tool": "bash", "reason": "Asks."}}})
    assert seen[-1].tool_name == "bash" and seen[-1].always is False
    assert serialise.item(seen[-1])["always"] is False
    # "Always" pressed where it was not offered is once.
    assert answered[-1]["outcome"]["optionId"] == "once"

    both = [{"optionId": "once"}, {"optionId": "session"}, {"optionId": "deny"}]
    patch = {"toolCallId": "t2", "title": "apply_patch", "kind": "edit",
             "rawInput": {"patch": "*** Update File: a.py\n"}}
    await link._permission(2, {"sessionId": "s1", "toolCall": patch, "options": both,
                               "_meta": {"lembas": {"tool": "apply_patch"}}})
    # The tool's own name, not the ACP kind it was guessed from.
    assert seen[-1].tool_name == "apply_patch" and seen[-1].always is True
    assert answered[-1]["outcome"]["optionId"] == "session"


async def test_the_page_offers_always_only_where_the_card_does(db):
    from lembas.api.chats import _ask_html

    def card(always: bool) -> str:
        item = interaction.Item(index=0, key="0", kind=interaction.KIND_APPROVAL,
                                tool_name="bash", title="Run a command", detail="ls",
                                allow_free_text=False, always=always)
        return _ask_html("chat1", interaction.build("i1", [item], timeout=60))

    assert 'value="allow_always"' in card(True)
    assert 'value="allow_always"' not in card(False) and 'value="allow"' in card(False)
    question = interaction.Item(index=0, key="0", kind=interaction.KIND_QUESTION, tool_name="",
                                title="Which?", options=(interaction.Option(label="A"),))
    assert serialise.item(question)["always"] is False


# --- How a reply that did not finish ended -------------------------------------------------
def _reply(db, **fields) -> Message:
    owner = db.scalar(select(User))
    chat = Chat(user_id=owner.id, model_id="m")
    db.add(chat)
    db.commit()
    row = Message(chat_id=chat.id, role="assistant", content="Half an answer", **fields)
    db.add(row)
    db.commit()
    return row


def test_a_stored_reply_keeps_how_it_ended(db, registered):
    assert display.message_end(_reply(db)) is None
    by_you = display.message_end(_reply(db, stopped=True, end_reason="person"))
    assert by_you == {"phase": "stopped", "reason": "person", "text": "Stopped: by you"}
    # Stored before the reason was kept: stopped, and no more is claimed.
    assert display.message_end(_reply(db, stopped=True))["text"] == "Stopped"
    # Wrapped up at a limit: not `stopped` in the row's old sense, known by its reason.
    assert display.message_end(_reply(db, end_reason="budget"))["text"] == "Stopped: budget"
    failed = display.message_end(_reply(db, error="Upstream said 503.\nTraceback…"))
    assert failed == {"phase": "failed", "reason": "Upstream said 503.",
                      "text": "Failed: Upstream said 503."}
    # A reply still being written has no end yet.
    assert display.message_end(_reply(db, stopped=True, complete=False)) is None

    sent = serialise.message(_reply(db, stopped=True, end_reason="steps"))
    assert sent["end"] == {"phase": "stopped", "reason": "steps", "text": "Stopped: step limit"}
    assert serialise.message(_reply(db))["end"] is None


async def test_the_reason_is_written_with_the_reply(db, registered, monkeypatch):
    row = _reply(db, complete=False)
    generation = generation_service.Generation(chat_id=row.chat_id, message_id=row.id)
    generation.content.append("Half an answer")
    generation.stopped = True
    generation_service._final_progress(generation)
    generation_service._persist(generation, "", 1.0)
    db.expire_all()
    stored = db.get(Message, row.id)
    assert stored.stopped is True and stored.end_reason == "person"

    limit = _reply(db, complete=False)
    wrapped = generation_service.Generation(chat_id=limit.chat_id, message_id=limit.id)
    wrapped.wrap_limit = "round limit"
    generation_service._final_progress(wrapped)
    generation_service._persist(wrapped, "", 1.0)
    db.expire_all()
    assert db.get(Message, limit.id).end_reason == "steps"

    fine = _reply(db, complete=False)
    finished = generation_service.Generation(chat_id=fine.chat_id, message_id=fine.id)
    generation_service._final_progress(finished)
    generation_service._persist(finished, "", 1.0)
    db.expire_all()
    assert not db.get(Message, fine.id).end_reason


def test_the_line_stays_on_the_reply_and_nothing_else_says_it(client, db, registered):
    from tests.test_thread_window import _model

    _model(db)
    stopped = _reply(db, stopped=True, end_reason="person")
    page = client.get(f"/chat/{stopped.chat_id}").text
    assert 'data-reply-end="stopped"' in page and "Stopped: by you" in page
    assert "This reply is cut short" not in page

    failed = _reply(db, error="Upstream said 503.")
    page = client.get(f"/chat/{failed.chat_id}").text
    assert 'data-reply-end="failed"' in page and "Failed: Upstream said 503." in page
    assert "msg__error" not in page.split('data-reply-end="failed"')[0].rsplit("<article", 1)[-1]
    assert "data-reply-end" not in client.get(f"/chat/{_reply(db).chat_id}").text


def test_a_refused_call_announced_over_acp_is_not_mistaken_for_anything(db):
    """From spec 3.7.0 a refused call is one `tool_call`, failed, with
    `_meta.lembas.denied`. The server draws a device's calls from
    `_lembas/event` (`tool_denied`, above); ACP's own update is read for the
    commands and the plan only, and this one changes nothing."""
    link = device_link.Link(token_id="tok", user_id="u1", name="cli", send_text=None,
                            close=None, hello={})
    asyncio.run(link._notification("session/update", {
        "sessionId": "s1",
        "update": {"sessionUpdate": "tool_call", "toolCallId": "t9", "title": "rm -rf /",
                   "status": "failed", "_meta": {"lembas": {"denied": True}},
                   "content": [{"type": "content",
                                "content": {"type": "text", "text": "The hardline refused it."}}]},
    }))
    assert link.commands == {} and json.dumps(link.announced) == "{}"
