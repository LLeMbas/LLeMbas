"""A device's background jobs, shown and controlled from here (spec 2.0.0).

The device owns its jobs: it runs them, it names them, it ends them. What this
side does is *show* them -- the `job`, `job_note` and `sub_tool` events onto the
reply, the strip above the composer, the helper panel -- and pass the person's
Stop and messages over the link. So most of what is asserted here is what one
event does to a reply, and which `_lembas/*` request a button becomes. The fake
device answers requests with canned results and records what it was asked.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from sqlalchemy import select

from lembas.db.models import KIND_AGENT, TOKEN_DEVICE, Chat, Message, User
from lembas.security import api_tokens
from lembas.services import device_link, settings_store
from lembas.services.generation import Generation

from .conftest import js_code, js_says, script

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    yield
    device_link.reset()


def _generation() -> Generation:
    return Generation(chat_id="c", message_id="m")


JOB7 = {
    "id": "job7",
    "kind": "subagent",
    "state": "running",
    "description": "find the loader",
    "agent": "explore",
    "started": "2026-10-20T10:00:00+00:00",
    "unread": 0,
}


# --- The events, onto the reply ---------------------------------------------------
def test_a_job_event_puts_the_job_on_the_reply_and_an_end_updates_it():
    generation = _generation()
    device_link.apply_event(generation, {"type": "job", "state": "start", "job": JOB7})
    assert generation.jobs["job7"]["state"] == "running"
    assert generation.jobs["job7"]["description"] == "find the loader"

    device_link.apply_event(
        generation, {"type": "job", "state": "end", "job": {**JOB7, "state": "done", "steps": 6}}
    )
    assert generation.jobs["job7"]["state"] == "done"
    assert generation.jobs["job7"]["steps"] == 6
    # Nothing a device says reaches the reply unbounded.
    device_link.apply_event(
        generation,
        {"type": "job", "state": "start", "job": {"id": "x" * 500, "description": "y" * 9000}},
    )
    assert all(len(key) <= 32 for key in generation.jobs)
    assert all(len(job["description"]) <= 200 for job in generation.jobs.values())


def test_a_subagents_tool_lines_nest_under_its_task_block_and_in_its_panel():
    """`sub_tool` carries the `task` call it belongs to; when that call ends,
    the lines are drawn under its block -- and a job's last few are kept on
    its record for the panel."""
    generation = _generation()
    device_link.apply_event(generation, {"type": "job", "state": "start", "job": JOB7})
    device_link.apply_event(
        generation, {"type": "tool_start", "id": "t1", "name": "task", "args": {"description": "d"}}
    )
    for n in range(10):
        device_link.apply_event(
            generation,
            {
                "type": "sub_tool",
                "callId": "t1",
                "jobId": "job7",
                "name": "grep",
                "title": f"pattern{n}",
                "isError": n == 9,
            },
        )
    assert len(generation.sub_tools["t1"]) == 10
    # The panel keeps the last eight.
    assert [t["title"] for t in generation.jobs["job7"]["recent"]] == [
        f"pattern{n}" for n in range(2, 10)
    ]
    device_link.apply_event(
        generation,
        {"type": "tool_end", "id": "t1", "name": "task", "result": {"output": "report"}},
    )
    task = generation.tool_events[-1]
    assert task["name"] == "task"
    assert len(task["children"]) == 10
    assert task["children"][-1] == {"name": "grep", "title": "pattern9", "isError": True}
    assert "t1" not in generation.sub_tools


def test_a_retract_drops_what_the_open_step_streamed_and_keeps_the_closed_ones():
    """The device retries a step: what it streamed since the last round
    closed is not the reply. Before, the partial text stayed and the retry's
    text was appended after it."""
    generation = _generation()
    device_link.apply_event(generation, {"type": "text", "text": "Round one. "})
    device_link.apply_event(generation, {"type": "step", "n": 2})
    device_link.apply_event(generation, {"type": "reasoning", "text": "hmm"})
    device_link.apply_event(generation, {"type": "text", "text": "Half a sent"})
    assert generation.text == "Round one. Half a sent"

    device_link.apply_event(generation, {"type": "retract"})

    assert generation.text == "Round one. "
    assert generation.thinking == ""
    device_link.apply_event(generation, {"type": "text", "text": "The whole sentence."})
    assert generation.text == "Round one. The whole sentence."


def test_a_job_note_inside_a_turn_is_shown_where_it_went_in():
    generation = _generation()
    device_link.apply_event(generation, {"type": "text", "text": "Working. "})
    note = "[A background job you started has finished:]\njob3 (npm test) exited with 0 after 5s."
    device_link.apply_event(generation, {"type": "job_note", "jobId": "job3", "text": note})
    steer = generation.steps[-1]["steer"]
    assert steer[0]["text"].startswith("[A background job you started has finished:]")


def test_a_plain_notice_reaches_the_status_line_too():
    """Only `warn` did; an info notice (an agent file that would not parse, a
    command moved to the background) said nothing anywhere."""
    generation = _generation()
    device_link.apply_event(
        generation, {"type": "notice", "level": "info", "message": "bash moved to job2"}
    )
    assert generation.status == "bash moved to job2"


# --- The link: the strip's requests, the reattach --------------------------------
class _Device:
    """A linked device that lists `jobs`, answers with canned results, and
    remembers what it was asked."""

    def __init__(self, db, user: User, *, capabilities=("jobs", "status")):
        from lembas.db.models import Connection, Model

        if db.scalar(select(Connection)) is None:
            # A model to show the chat page with: without one the page is the
            # "no models yet" screen and has no composer to put a strip above.
            connection = Connection(
                name="c", base_url="http://127.0.0.1:1/v1", api_key_encrypted=""
            )
            db.add(connection)
            db.commit()
            db.add(
                Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True})
            )
            db.commit()
        token, _raw = api_tokens.create(
            db, user, name="LLeMbas CLI on d", scopes=["models", "link"],
            kind=TOKEN_DEVICE, device_host="d",
        )
        self.token_id = token.id
        self.asked: list[tuple[str, dict]] = []
        self.answers: dict[str, object] = {
            "_lembas/jobs": {"jobs": [JOB7]},
            "_lembas/job/stop": {"state": "stopped"},
            "_lembas/job/send": {"state": "running"},
            "_lembas/job/transcript": {
                "id": "job7",
                "turns": [
                    {"role": "user", "text": "Find the loader."},
                    {
                        "role": "assistant",
                        "text": "It is in `src/load.ts`. <script>alert(1)</script>",
                        "tools": [{"id": "1", "name": "grep", "args": {"pattern": "load"}}],
                    },
                ],
            },
            "_lembas/session/status": {"exists": True, "busy": False, "jobs": [JOB7]},
            "_lembas/session/history": {"turns": []},
        }
        link = device_link.Link(
            token_id=token.id, user_id=user.id, name=token.name, send_text=None, close=None,
            hello={"device": {"host": "d"}, "limits": {"roots": ["/w"], "max_mode": "edit"}},
            initialized={"_meta": {"lembas": {"capabilities": list(capabilities)}}},
        )

        async def request(method, params=None, *, timeout=60):
            self.asked.append((method, params or {}))
            return self.answers.get(method, {})

        link.request = request  # type: ignore[method-assign]
        device_link.LINKS[token.id] = link
        self.link = link


def _user(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _device_chat(db, device: _Device, *, session_id="s1") -> Chat:
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    chat = Chat(
        user_id=_user(db).id, kind=KIND_AGENT, title="t", model_id="m",
        device_token_id=device.token_id, device_session_id=session_id, project_dir="/w",
    )
    db.add(chat)
    db.commit()
    return chat


async def test_the_strip_lists_the_devices_jobs_and_stop_goes_over_the_link(
    client, db, registered
):
    device = _Device(db, _user(db))
    chat = _device_chat(db, device)

    page = client.get(f"/api/chats/{chat.id}/jobs")
    assert page.status_code == 200
    assert "job7" in page.text and "find the loader" in page.text
    # With rows, the strip polls itself and says where the answer goes.
    assert 'hx-trigger="every 10s"' in page.text and 'hx-target="this"' in page.text
    assert f'hx-post="/api/chats/{chat.id}/jobs/job7/stop"' in page.text
    assert device.asked[-1][0] == "_lembas/jobs"

    stopped = client.post(f"/api/chats/{chat.id}/jobs/job7/stop")
    assert stopped.status_code == 200
    assert ("_lembas/job/stop", {"sessionId": "s1", "id": "job7"}) in device.asked


async def test_a_message_from_the_panel_goes_over_the_link_and_the_panel_is_safe(
    client, db, registered
):
    device = _Device(db, _user(db))
    chat = _device_chat(db, device)

    sent = client.post(f"/api/chats/{chat.id}/jobs/job7/send", data={"text": "The YAML one."})
    assert sent.status_code == 200
    assert ("_lembas/job/send", {"sessionId": "s1", "id": "job7", "text": "The YAML one."}) in (
        device.asked
    )
    # The panel body came back: the transcript, with the device's words escaped
    # or sanitised and the tool line drawn.
    assert "Find the loader." in sent.text
    assert "<script>" not in sent.text
    assert "src/load.ts" in sent.text
    assert "load" in sent.text and "tool-line" in sent.text
    assert f'hx-post="/api/chats/{chat.id}/jobs/job7/send"' in sent.text
    # Running, so the transcript polls -- the transcript, never the body under
    # a half-typed message.
    assert 'id="jobpanel-turns"' in sent.text and "?part=turns" in sent.text

    empty = client.post(f"/api/chats/{chat.id}/jobs/job7/send", data={"text": "  "})
    assert empty.status_code == 400

    turns = client.get(f"/api/chats/{chat.id}/jobs/job7?part=turns")
    assert turns.status_code == 200 and "jobpanel__send" not in turns.text


async def test_a_device_without_the_capability_is_not_asked(client, db, registered):
    device = _Device(db, _user(db), capabilities=("status",))
    chat = _device_chat(db, device)

    page = client.get(f"/api/chats/{chat.id}/jobs")
    assert page.status_code == 200 and "hidden" in page.text
    assert not any(method.startswith("_lembas/job") for method, _ in device.asked)
    assert client.post(f"/api/chats/{chat.id}/jobs/job7/stop").status_code == 409


async def test_reattaching_refills_the_strip_from_the_status(db, registered):
    """`_follow` ends at `done`, so a reply taken up again after a restart
    learns the session's jobs from `status.jobs`, not from events it missed."""
    device = _Device(db, _user(db))
    chat = _device_chat(db, device)
    generation = Generation(chat_id=chat.id, message_id="m")

    await device_link._reattach(device.link, generation, chat.id, "s1", "d")

    assert generation.jobs["job7"]["state"] == "running"


# --- The harness's own turn, mirrored -----------------------------------------------
async def test_a_machine_prompt_is_mirrored_as_the_harness_turn(db, registered):
    """A finished job's note starts a turn on the device (`prompt` with
    `machine: true`). Nobody typed it, and the session is not a terminal's --
    before, only an announced session's prompts were mirrored, so the chat
    showing the session never saw the turn its model was given."""
    device = _Device(db, _user(db))
    chat = _device_chat(db, device)
    note = "[A background job you started has finished:]\njob3 (npm test) exited with 0 after 5s."

    # The dispatcher the WebSocket calls, with the harness's own prompt.
    await device.link._notification(
        "_lembas/event",
        {"sessionId": "s1", "event": {"type": "prompt", "text": note, "machine": True}},
    )
    for _ in range(50):
        await asyncio.sleep(0.02)
        db.expire_all()
        rows = list(db.scalars(select(Message).where(Message.chat_id == chat.id)))
        if rows:
            break
    users = [m for m in rows if m.role == "user"]
    assert len(users) == 1
    assert users[0].machine is True and users[0].origin == ""
    assert users[0].content == note
    # The turn ends on the device; the mirror following it settles.
    await device.link._notification(
        "_lembas/event", {"sessionId": "s1", "event": {"type": "task", "state": "end"}}
    )
    await asyncio.sleep(0.1)

    # A plain prompt in a session no terminal shares is still not mirrored --
    # it is a turn sent from here, whose row already exists.
    await device.link._notification(
        "_lembas/event",
        {"sessionId": "s1", "event": {"type": "prompt", "text": "typed here"}},
    )
    await asyncio.sleep(0.1)
    db.expire_all()
    assert not [
        m for m in db.scalars(select(Message).where(Message.chat_id == chat.id))
        if m.content == "typed here"
    ]


def test_the_machine_badge_says_what_wrote_the_turn(db, registered):
    """One badge said "Background job" for a schedule's firing, a helper's task
    and a job's note alike."""
    from lembas.web.templating import templates

    user = _user(db)

    def badge(chat_kind: str, parent: str | None) -> str:
        chat = Chat(id="c1", user_id=user.id, kind=chat_kind, parent_chat_id=parent, title="t")
        message = Message(
            id="m1", chat_id="c1", role="user", content="x", machine=True, complete=True
        )
        html = templates.get_template("chat/_message.html").render(
            {"message": message, "chat": chat, "user": user, "models_by_id": {}}
        )
        return re.search(r'<span class="badge">.*?</span>', html, re.S).group(0)

    assert "Scheduled run" in badge("task", None)
    assert "From the main agent" in badge("chat", "parent")
    assert "Background job" in badge("agent", None)


# --- The approval card names the subagent ------------------------------------------
async def test_the_approval_card_says_which_subagent_asks():
    from lembas.services import interaction
    from lembas.web.templating import templates

    item = interaction.Item(
        index=0, key="0", kind=interaction.KIND_APPROVAL, tool_name="bash",
        title="Run a command", detail="ls", job="explore (job7)",
    )
    pause = interaction.build("i1", [item], timeout=10)
    html = templates.get_template("chat/_interaction.html").render(
        {"ask": pause, "chat": Chat(id="c", user_id="u", title="t")}
    )
    assert "explore (job7) asks:" in html


# --- The page and the scripts: what has to be there for any of this to show ---------
def test_the_page_carries_the_strip_the_panel_and_the_frame_target(client, db, registered):
    device = _Device(db, _user(db))
    chat = _device_chat(db, device)
    page = client.get(f"/chat/{chat.id}").text
    assert 'id="jobs-strip"' in page and "job7" in page
    assert 'id="jobpanel"' in page and 'data-window="#jobpanel"' in page
    # The strip is not inside the composer's form: an inherited `hx-target`
    # once put a chip's answer into the transcript.
    assert page.index('id="jobs-strip"') < page.index('id="composer-form"')


def test_the_streaming_shell_has_somewhere_for_the_jobs_frame_to_land():
    from pathlib import Path

    import lembas

    message = (Path(lembas.__file__).parent / "web/templates/chat/_message.html").read_text()
    assert 'sse-swap="jobs"' in message


def test_the_panel_opens_when_a_transcript_lands_in_it():
    """The strip's Follow button fetches into `#jobpanel-body`; without this
    hook the swap would land in a hidden panel and nothing would appear."""
    code = js_code(script("app.js"))
    assert js_says(
        code,
        "htmx:afterSwap",
        'target.id === "jobpanel-body"',
        'setPanel("#jobpanel", true, "side")',
    )


def test_the_hidden_strip_wins_over_its_own_display():
    """`[hidden]` needs `!important` in our CSS (Working notes): the strip sets
    its own `display`, and without it an empty strip would draw its border."""
    from pathlib import Path

    import lembas

    css = (Path(lembas.__file__).parent / "web/static/css/chat.css").read_text()
    assert ".jobs-strip[hidden] { display: none !important; }" in css
