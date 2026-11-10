"""An agent chat's approval, over the apps' door and the page's.

A fake device asks `session/request_permission` in a chat started through
`/api/v2`, while the app is already following the reply. The app's JSON stream
must carry the card -- not only the status line that says one is waiting -- in
the shape a device approval really has (the command is `detail`, the text an
`editable` item lets the person correct), and the page's HTML stream the same
card. Answering it through `/api/v2/chats/{id}/interactions/{card}`, with the
command corrected, must reach the device.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable

import pytest
from sqlalchemy import select

from lembas.db.models import User
from lembas.security import api_tokens
from lembas.services import device_link
from tests.test_device_link import _device, _event, _reply, _wait_linked
from tests.test_device_protocol2 import ALL, _hello


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    yield
    device_link.reset()


def _app_bearer(db) -> dict[str, str]:
    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="LLeMbas Desktop", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def _parse(raw: str) -> tuple[str, str]:
    event, data = "", []
    for line in raw.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    return event, "\n".join(data)


def _follow_soon(
    client, stream: Callable[[], AsyncIterator[str]], until: Callable[[str, str], bool]
):
    """Follow a stream inside the app's own loop -- the test client buffers a
    whole response, and this one has not ended -- from now, until
    `until(event, data)`; a future of every frame read."""

    async def read() -> list[tuple[str, str]]:
        seen: list[tuple[str, str]] = []
        frames = stream()
        deadline = time.monotonic() + 5
        try:
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(anext(frames), deadline - time.monotonic())
                except (StopAsyncIteration, TimeoutError):
                    break
                event, data = _parse(raw)
                if not event:
                    continue
                seen.append((event, data))
                if until(event, data):
                    break
        finally:
            await frames.aclose()
        return seen

    return client.portal.start_task_soon(read)


def _ask_permission(ws, command: str) -> None:
    ws.send_text(json.dumps({
        "jsonrpc": "2.0", "id": 77, "method": "session/request_permission",
        "params": {
            "sessionId": "s1",
            "toolCall": {
                "toolCallId": "t1", "title": command, "kind": "execute",
                "rawInput": {"command": command},
            },
            "options": [
                {"optionId": "once", "kind": "allow_once"},
                {"optionId": "session", "kind": "allow_always"},
                {"optionId": "deny", "kind": "reject_once"},
            ],
        },
    }))


def test_a_device_approval_reaches_the_app_and_its_answer_the_device(client, db, registered):
    from lembas.api.chats import _follow
    from lembas.api.v2.stream import follow_json

    token_id, raw = _device(db)
    headers = _app_bearer(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        # The capabilities LLeMbas CLI 2.0 lists: turns named, jobs, show.
        _hello(ws, [*ALL, "jobs", "show", "plan_state", "context"])
        _wait_linked(token_id)

        made = client.post(
            "/api/v2/chats",
            headers=headers,
            json={
                "text": "List the files",
                "kind": "agent",
                "device": token_id,
                "directory": "/srv/work/app",
                "mode": "edit",
            },
        )
        assert made.status_code == 201, made.text
        chat_id = made.json()["chat"]["id"]
        reply_id = made.json()["reply"]["id"]

        new = ws.receive_json()
        assert new["method"] == "session/new"
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": new["id"], "result": {"sessionId": "s1"}}))
        prompt = ws.receive_json()
        while prompt.get("method") != "session/prompt":
            prompt = ws.receive_json()
        turn = ((prompt["params"].get("_meta") or {}).get("lembas") or {}).get("turnId")
        _event(ws, "s1", {"type": "prompt", "text": "List the files", "turnId": turn})
        _event(ws, "s1", {"type": "task", "state": "start", "turnId": turn})
        _event(
            ws, "s1", {"type": "tool_start", "id": "t1", "name": "bash", "args": {"command": "ls"}}
        )

        # The app and the page follow the reply before the device asks, as
        # they do when the person is watching.
        app = _follow_soon(
            client, lambda: follow_json(chat_id, reply_id),
            lambda event, data: event == "ask" and bool(json.loads(data)["cards"]),
        )
        page = _follow_soon(
            client, lambda: _follow(chat_id, reply_id),
            lambda event, data: event == "ask" and "Run a command" in data,
        )
        time.sleep(0.2)
        _ask_permission(ws, "ls")

        frames = app.result(timeout=10)
        statuses = [json.loads(d)["text"] for e, d in frames if e == "status"]
        asks = [json.loads(d)["cards"] for e, d in frames if e == "ask"]
        assert "Waiting for you to allow bash…" in statuses, frames
        assert asks and asks[-1], frames
        [card] = asks[-1]
        assert card["kind"] == "approval"
        [item] = card["items"]
        # The device's approval as it really is: a heading, and the command as
        # the `detail` an editable item lets the person correct.
        assert item["title"] == "Run a command"
        assert item["detail"] == "ls" and item["editable"] is True
        assert item["tool"] == "bash"
        # An approval has no "Something else".
        assert item["free_text"] is False

        html = [d for e, d in page.result(timeout=10) if e == "ask"]
        assert html and card["id"] in html[-1] and "Run a command" in html[-1]

        answered = client.post(
            f"/api/v2/chats/{chat_id}/interactions/{card['id']}",
            headers=headers,
            json={"verdict": "allow", "texts": {item["key"]: "ls -la"}},
        )
        assert answered.status_code == 200, answered.text
        permitted = ws.receive_json()
        assert permitted["id"] == 77
        assert permitted["result"]["outcome"]["optionId"] == "once"
        assert permitted["result"]["_meta"]["lembas"]["command"] == "ls -la"

        # Answered once: a second answer is the 409 the app reads as "already".
        again = client.post(
            f"/api/v2/chats/{chat_id}/interactions/{card['id']}",
            headers=headers,
            json={"verdict": "deny"},
        )
        assert again.status_code == 409

        _event(ws, "s1", {"type": "text", "text": "Two entries."})
        _event(ws, "s1", {"type": "task", "state": "end", "turnId": turn})
        ws.send_text(
            json.dumps({"jsonrpc": "2.0", "id": prompt["id"], "result": {"stopReason": "end_turn"}})
        )
        assert _reply(db, chat_id).content == "Two entries."
