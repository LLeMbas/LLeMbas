"""The `_lembas` protocol 2 features, against a fake device.

Every feature is gated on a capability the device lists in `initialize`, so each
test here states what its device says it can do -- and the protocol-1 tests in
`test_device_link.py` and `test_device_sessions.py` keep proving that a device
listing none of them is served exactly as before.
"""

from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select

from lembas.db.models import Chat
from lembas.security import api_tokens
from lembas.services import device_link
from lembas.services import generation as generation_service
from tests.test_device_link import _device, _event, _reply, _wait_linked

ALL = ["directories", "configure", "steer", "sessions", "status", "delete", "compact",
       "title", "terminal", "files", "commands", "attachments", "ask", "plan", "model",
       "turns", "shell"]


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    yield
    device_link.reset()


def _hello(ws, capabilities=ALL, *, protocol=3, connection="example", roots=("/srv/work",)):
    ws.send_text(json.dumps({
        "jsonrpc": "2.0",
        "method": "_lembas/hello",
        "params": {
            "protocol": protocol,
            "version": "0.27.0",
            "harness_spec": "2.1.0",
            "device": {"host": "device-1"},
            "limits": {"roots": list(roots), "max_mode": "edit"},
        },
    }))
    init = ws.receive_json()
    assert init["method"] == "initialize"
    ws.send_text(json.dumps({
        "jsonrpc": "2.0",
        "id": init["id"],
        "result": {
            "protocolVersion": 1,
            "agentInfo": {"name": "LLeMbas CLI"},
            "agentCapabilities": {
                "promptCapabilities": {"image": True, "audio": False, "embeddedContext": True}
            },
            "_meta": {"lembas": {"protocol": protocol, "capabilities": list(capabilities),
                                 "connection": connection}},
        },
    }))


def _send(ws, body):
    ws.send_text(json.dumps({"jsonrpc": "2.0", **body}))


def _start(client, db, ws, token_id, content="Do the thing", session="s1"):
    """A device chat started from the composer, up to the device holding the prompt."""
    client.post(
        "/api/chats/start",
        data={"content": content, "kind": "agent", "ssh_profile_id": f"device:{token_id}",
              "project_dir": "/srv/work/app", "agent_mode": "edit"},
    )
    chat = db.scalar(select(Chat).order_by(Chat.created_at.desc()))
    new = ws.receive_json()
    assert new["method"] == "session/new"
    _send(ws, {"id": new["id"], "result": {"sessionId": session}})
    prompt = ws.receive_json()
    while prompt.get("method") != "session/prompt":
        prompt = ws.receive_json()
    # A device that names its turns starts each one by its id.
    turn = ((prompt["params"].get("_meta") or {}).get("lembas") or {}).get("turnId")
    if turn:
        _event(ws, session, {"type": "task", "state": "start", "turnId": turn})
    return chat, prompt


def _finish(ws, prompt, text="Done.", session="s1"):
    """The device's turn ends, and the prompt is answered."""
    turn = ((prompt["params"].get("_meta") or {}).get("lembas") or {}).get("turnId")
    if text:
        _event(ws, session, {"type": "text", "text": text})
    _event(ws, session, {"type": "task", "state": "end", "turnId": turn})
    _send(ws, {"id": prompt["id"], "result": {"stopReason": "end_turn"}})


def _until(predicate, what, tries=200):
    for _ in range(tries):
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"never: {what}")


def _cards(chat_id):
    running = generation_service.running_for(chat_id)
    return list(running.waiting) if running is not None else []


def _permission(ws, rpc_id, call_id, command, session="s1"):
    _send(ws, {"id": rpc_id, "method": "session/request_permission", "params": {
        "sessionId": session,
        "toolCall": {"toolCallId": call_id, "title": command, "kind": "execute",
                     "rawInput": {"command": command}},
        "options": [{"optionId": "once", "kind": "allow_once"},
                    {"optionId": "deny", "kind": "reject_once"}],
    }})


# --- 5. Several cards at once -------------------------------------------------
def test_a_second_approval_does_not_hide_the_first(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _permission(ws, 71, "t1", "make build")
        _permission(ws, 72, "t2", "make test")
        cards = _until(lambda: len(_cards(chat.id)) == 2 and _cards(chat.id), "two cards")
        assert [c.tool_call_id for c in cards] == ["t1", "t2"]

        # Both are on the page, each its own form.
        from lembas.api.chats import _ask_html

        html = _ask_html(chat.id, cards)
        assert html.count("<form") == 2
        assert all(f"/interaction/{c.id}" in html for c in cards)

        # Answering the second leaves the first open, and answers the second only.
        page = client.post(
            f"/api/chats/{chat.id}/interaction/{cards[1].id}", data={"verdict": "allow"}
        )
        assert page.status_code == 204
        answered = ws.receive_json()
        assert answered["id"] == 72 and answered["result"]["outcome"]["optionId"] == "once"
        left = _until(lambda: len(_cards(chat.id)) == 1 and _cards(chat.id), "one card left")
        assert left[0].tool_call_id == "t1"

        # Settled elsewhere: that card goes, and only that one.
        _permission(ws, 73, "t3", "make lint")
        _until(lambda: len(_cards(chat.id)) == 2, "the third card")
        _send(ws, {"method": "_lembas/permission/settled",
                   "params": {"sessionId": "s1", "toolCallId": "t1"}})
        settled = ws.receive_json()
        assert settled["id"] == 71  # denied here: the terminal's answer is the one that counts
        left = _until(lambda: len(_cards(chat.id)) == 1 and _cards(chat.id), "t3 alone")
        assert left[0].tool_call_id == "t3"
        assert generation_service.running_for(chat.id).status  # still says it waits

        client.post(f"/api/chats/{chat.id}/interaction/{left[0].id}", data={"verdict": "deny"})
        assert ws.receive_json()["id"] == 73
        _finish(ws, prompt, "Built.")
        assert _reply(db, chat.id).content == "Built."


# --- 4. ask_user and plan_exit from the device ----------------------------------
def _question(ws, rpc_id, call_id, *, patient=False, session="s1"):
    meta = {"lembas": {"patient": True}} if patient else {"lembas": {}}
    _send(ws, {"id": rpc_id, "method": "_lembas/question", "params": {
        "sessionId": session, "toolCallId": call_id, "why": "To pick the right store.",
        "questions": [
            {"header": "Database", "question": "Which database?", "options": [
                {"label": "SQLite", "recommended": True, "description": "One file"},
                {"label": "Postgres"}]},
            {"header": "Extras", "question": "Which extras?", "multiple": True, "options": [
                {"label": "Cache"}, {"label": "Queue, durable"}]},
            {"header": "Name", "question": "What is it called?", "options": []},
        ],
        "_meta": meta,
    }})


def test_a_device_question_is_asked_on_the_card_and_answered_in_its_shape(
    client, db, registered
):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _question(ws, 81, "q1")
        card = _until(lambda: _cards(chat.id) and _cards(chat.id)[0], "the question card")
        assert card.kind == "question" and card.dismissable and card.tool_call_id == "q1"
        assert [i.header for i in card.items] == ["Database", "Extras", "Name"]
        assert card.items[0].options[0].recommended

        from lembas.api.chats import _ask_html

        html = _ask_html(chat.id, card)
        assert "To pick the right store." in html and 'value="deny"' in html

        client.post(
            f"/api/chats/{chat.id}/interaction/{card.id}",
            data={"choice.q0": "SQLite", "choice.q1": ["Cache", "Queue, durable"],
                  "text.q2": "atlas"},
        )
        answered = ws.receive_json()
        assert answered["id"] == 81
        assert answered["result"] == {
            "outcome": "answered",
            "answers": [
                {"kind": "options", "labels": ["SQLite"]},
                {"kind": "options", "labels": ["Cache", "Queue, durable"]},
                {"kind": "custom", "text": "atlas"},
            ],
        }

        # Dismissed from the card.
        _question(ws, 82, "q2")
        card = _until(lambda: _cards(chat.id) and _cards(chat.id)[0], "the second card")
        client.post(f"/api/chats/{chat.id}/interaction/{card.id}", data={"verdict": "deny"})
        assert ws.receive_json()["result"] == {"outcome": "dismissed"}

        _finish(ws, prompt)
        _reply(db, chat.id)


def test_a_question_nobody_answers_is_dismissed_unless_patient(
    client, db, registered, monkeypatch
):
    monkeypatch.setattr(device_link, "ASK_WAIT", 0.3)
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _question(ws, 91, "q1")
        assert ws.receive_json()["result"] == {"outcome": "dismissed"}

        # Patient: somebody may answer at the terminal, so it waits -- and the
        # terminal's answer takes the card down.
        _question(ws, 92, "q2", patient=True)
        card = _until(lambda: _cards(chat.id) and _cards(chat.id)[0], "the patient card")
        time.sleep(0.5)
        assert _cards(chat.id) == [card]
        _send(ws, {"method": "_lembas/permission/settled",
                   "params": {"sessionId": "s1", "toolCallId": "q2"}})
        assert ws.receive_json()["id"] == 92
        _until(lambda: not _cards(chat.id), "the card gone")
        _finish(ws, prompt)
        _reply(db, chat.id)


def test_a_device_plan_is_approved_revised_or_dismissed(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)

        def plan(rpc_id, call_id):
            _send(ws, {"id": rpc_id, "method": "_lembas/plan", "params": {
                "sessionId": "s1", "toolCallId": call_id, "path": ".agent/plans/p.md",
                "text": "# Plan\n\n1. Read\n2. <script>alert(1)</script>Write",
                "_meta": {"lembas": {}},
            }})
            return _until(lambda: _cards(chat.id) and _cards(chat.id)[0], "the plan card")

        card = plan(101, "p1")
        assert card.kind == "plan"
        from lembas.api.chats import _ask_html

        html = _ask_html(chat.id, card)
        assert "<h1>Plan</h1>" in html and "<script>" not in html
        assert ".agent/plans/p.md" in html
        client.post(f"/api/chats/{chat.id}/interaction/{card.id}",
                    data={"verdict": "allow", "choice.plan": "manual"})
        assert ws.receive_json()["result"] == {"outcome": "approve", "mode": "manual"}

        card = plan(102, "p2")
        client.post(f"/api/chats/{chat.id}/interaction/{card.id}",
                    data={"verdict": "deny", "reason": "Write tests first."})
        assert ws.receive_json()["result"] == {"outcome": "revise",
                                               "feedback": "Write tests first."}

        card = plan(103, "p3")
        client.post(f"/api/chats/{chat.id}/interaction/{card.id}", data={"verdict": "deny"})
        assert ws.receive_json()["result"] == {"outcome": "dismissed"}

        _finish(ws, prompt)
        _reply(db, chat.id)


def test_a_question_with_no_reply_here_is_dismissed_or_left_to_the_terminal(
    client, db, registered
):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        _question(ws, 111, "q1", session="nobody")
        assert ws.receive_json()["result"] == {"outcome": "dismissed"}
        _question(ws, 112, "q2", session="nobody", patient=True)
        assert "error" in ws.receive_json()


# --- 7. Turn ids: the two races ------------------------------------------------------
# These drive a `Link` whose requests are answered here (the harness of
# test_device_sync.py): the races are about the order events and answers arrive
# in, which a real socket in a test cannot pin down.
import asyncio  # noqa: E402

from lembas.db.models import Message  # noqa: E402
from tests.test_device_sync import _device_chat, _say  # noqa: E402
from tests.test_device_sync import _linked as _sync_linked  # noqa: E402

TURNS = ("directories", "configure", "steer", "sessions", "status", "delete", "compact",
         "title", "turns")


def _turn_link(db, answer, capabilities=TURNS):
    from lembas.services import settings_store

    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    link, asked = _sync_linked(db, answer=answer)
    link.initialized = {"_meta": {"lembas": {"capabilities": list(capabilities)}}}
    return link, asked


async def _emit(link, session_id, *events):
    for event in events:
        await link._notification("_lembas/event", {"sessionId": session_id, "event": event})


def _cut_off(db, chat_id, text="build it"):
    """A person's turn and its reply, left unfinished by a restart."""
    asked = _say(db, chat_id, "user", text)
    reply_id = _say(db, chat_id, "assistant", "")
    row = db.get(Message, reply_id)
    row.complete = False
    row.interrupted = True
    db.commit()
    return asked, reply_id


def test_a_turn_the_device_has_not_started_yet_is_followed_when_it_starts(
    db, registered, make_chat
):
    """Race (a): restarted, asked, the device was idle -- it had not read the
    prompt yet. Read from the history, the row closes on the turn before, and
    the prompt then runs with nobody following it. By id, the device says the
    turn is queued, and the reply waits for it."""
    turn = {}

    def answer(method, params):
        if method == "_lembas/session/status":
            return {"exists": True, "busy": False, "queued": [turn["id"]], "lastTurnId": "t0"}
        if method == "_lembas/session/history":
            return {"turns": [{"role": "user", "text": "earlier"},
                              {"role": "assistant", "text": "The turn before."}]}
        return {}

    link, asked = _turn_link(db, answer)
    chat_id = _device_chat(db, make_chat, link)
    turn["id"], reply_id = _cut_off(db, chat_id)

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        for _ in range(200):
            if ("s1", turn["id"]) in link.queued_turns:
                break
            await asyncio.sleep(0.01)
        await _emit(link, "s1",
                    {"type": "task", "state": "start", "turnId": turn["id"]},
                    {"type": "prompt", "text": "build it", "turnId": turn["id"]},
                    {"type": "text", "text": "Built."},
                    {"type": "task", "state": "end", "turnId": turn["id"]})
        await asyncio.wait_for(generation_service._TASKS[reply_id], 5)

    asyncio.run(scenario())
    db.expire_all()
    row = db.get(Message, reply_id)
    assert row.complete and row.content == "Built."
    methods = [m for m, _ in asked]
    assert "session/prompt" not in methods and "_lembas/session/history" not in methods
    # Its own prompt event was not taken for a turn typed in the terminal.
    assert db.query(Message).filter(Message.chat_id == chat_id).count() == 2


def test_a_turn_the_device_never_saw_is_sent_again_by_its_id(db, registered, make_chat):
    turn = {}

    async def answer(method, params):
        if method == "_lembas/session/status":
            return {"exists": True, "busy": False, "queued": [], "lastTurnId": "t0"}
        if method == "session/prompt":
            sent = params["_meta"]["lembas"]["turnId"]
            await _emit(link, "s1",
                        {"type": "task", "state": "start", "turnId": sent},
                        {"type": "text", "text": "Built at last."},
                        {"type": "task", "state": "end", "turnId": sent})
            return {"stopReason": "end_turn"}
        return {}

    link, asked = _turn_link(db, answer)
    chat_id = _device_chat(db, make_chat, link)
    turn["id"], reply_id = _cut_off(db, chat_id)

    async def scenario():
        assert await generation_service.resume_interrupted(delay=0) == 1
        await asyncio.wait_for(generation_service._TASKS[reply_id], 5)

    asyncio.run(scenario())
    prompts = [p for m, p in asked if m == "session/prompt"]
    assert len(prompts) == 1 and prompts[0]["_meta"]["lembas"]["turnId"] == turn["id"]
    assert prompts[0]["prompt"][0]["text"] == "build it"
    db.expire_all()
    assert db.get(Message, reply_id).content == "Built at last."


def test_a_turn_that_finished_meanwhile_is_read_by_its_id(db, registered, make_chat):
    turn = {}

    def answer(method, params):
        if method == "_lembas/session/status":
            return {"exists": True, "busy": False, "queued": [], "lastTurnId": turn["id"]}
        if method == "_lembas/session/history":
            return {"turns": [
                {"role": "user", "text": "build it", "turnId": turn["id"]},
                {"role": "assistant", "text": "Built, 12 tests pass."},
                {"role": "user", "text": "typed later", "turnId": "x"},
                {"role": "assistant", "text": "Not this one."},
            ]}
        return {}

    link, asked = _turn_link(db, answer)
    chat_id = _device_chat(db, make_chat, link)
    turn["id"], reply_id = _cut_off(db, chat_id)
    g = generation_service.Generation(chat_id=chat_id, message_id=reply_id)
    g.turn_id = turn["id"]
    assert asyncio.run(device_link._reattach(link, g, chat_id, "s1", "device-1")) is False
    assert g.text == "Built, 12 tests pass."


def test_a_terminal_turn_right_after_a_web_turn_gets_its_own_row(db, registered, make_chat):
    """Race (b): the terminal's next turn starts while the web turn's reply is
    still being written here. Refusing to mirror it -- a reply is running in
    the chat -- would lose the turn. By id it is a turn of its own, shown once
    the web reply is done."""
    terminal = "6f1c2c1e-0000-4000-8000-000000000001"

    async def answer(method, params):
        if method == "session/prompt":
            web = params["_meta"]["lembas"]["turnId"]
            await _emit(link, "s1",
                        {"type": "task", "state": "start", "turnId": web},
                        {"type": "prompt", "text": "fix it", "turnId": web},
                        {"type": "text", "text": "Fixed."},
                        {"type": "task", "state": "end", "turnId": web},
                        # Queued in the terminal meanwhile; it starts at once.
                        {"type": "task", "state": "start", "turnId": terminal},
                        {"type": "prompt", "text": "now run the tests", "turnId": terminal})
            await asyncio.sleep(0.3)  # the mirror is made while this reply finishes
            await _emit(link, "s1",
                        {"type": "text", "text": "Tests pass."},
                        {"type": "task", "state": "end", "turnId": terminal})
            return {"stopReason": "end_turn"}
        return {}

    link, asked = _turn_link(db, answer)
    link.announced["s1"] = {"cwd": "/home/dev/app"}
    chat_id = _device_chat(db, make_chat, link)
    _say(db, chat_id, "user", "fix it")
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)

    async def scenario():
        generation_service.ensure(chat_id, reply_id)
        await asyncio.wait_for(generation_service._TASKS[reply_id], 5)
        for _ in range(300):
            mirrored = generation_service.running_for(chat_id)
            if mirrored is not None:
                await asyncio.wait_for(generation_service._TASKS[mirrored.message_id], 5)
                return
            await asyncio.sleep(0.02)

    asyncio.run(scenario())
    db.expire_all()
    rows = db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    ).all()
    assert [(r.role, r.origin, r.content) for r in rows] == [
        ("user", "", "fix it"),
        ("assistant", "", "Fixed."),
        ("user", "terminal", "now run the tests"),
        ("assistant", "", "Tests pass."),
    ]


def test_a_protocol_1_device_is_prompted_and_followed_as_before(db, registered, make_chat):
    """No `turns`: no turn id goes with the prompt, events without ids reach the
    reply writing, and a reattach reads `busy`."""
    async def answer(method, params):
        if method == "session/prompt":
            assert "_meta" not in params
            await _emit(link, "s1", {"type": "task", "state": "start"},
                        {"type": "text", "text": "As ever."}, {"type": "task", "state": "end"})
            return {"stopReason": "end_turn"}
        return {}

    link, asked = _turn_link(db, answer, capabilities=TURNS[:-1])
    chat_id = _device_chat(db, make_chat, link)
    _say(db, chat_id, "user", "go")
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)

    async def scenario():
        generation_service.ensure(chat_id, reply_id)
        await asyncio.wait_for(generation_service._TASKS[reply_id], 5)

    asyncio.run(scenario())
    db.expire_all()
    assert db.get(Message, reply_id).content == "As ever."
    assert not link.sent_turns


# --- 3. Attachments ----------------------------------------------------------------
def _upload(client, db, name, payload, media):
    from lembas.db.models import Attachment

    client.post("/api/files", files={"file": (name, payload, media)})
    return db.scalar(
        select(Attachment).where(Attachment.filename == name).order_by(Attachment.created_at.desc())
    ).id


def _start_with_files(client, db, ws, token_id, file_ids):
    client.post(
        "/api/chats/start",
        data={"content": "Look at these", "kind": "agent",
              "ssh_profile_id": f"device:{token_id}", "project_dir": "/srv/work/app",
              "agent_mode": "edit", "file_ids": file_ids},
    )
    chat = db.scalar(select(Chat).order_by(Chat.created_at.desc()))
    new = ws.receive_json()
    _send(ws, {"id": new["id"], "result": {"sessionId": "s1"}})
    return chat


def test_files_attached_in_a_device_chat_go_as_blocks(client, db, registered):
    from tests.test_files import png_bytes

    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        ids = [_upload(client, db, "shot.png", png_bytes(), "image/png"),
               _upload(client, db, "notes.txt", b"remember the milk", "text/plain")]
        chat = _start_with_files(client, db, ws, token_id, ids)
        prompt = ws.receive_json()
        blocks = prompt["params"]["prompt"]
        assert blocks[0] == {"type": "text", "text": "Look at these"}
        image = next(b for b in blocks if b["type"] == "image")
        # As stored here: re-encoded once on upload, as for any chat.
        assert image["mimeType"] in device_link.IMAGE_TYPES and image["data"] and image["uri"]
        resource = next(b for b in blocks if b["type"] == "resource")["resource"]
        assert resource["text"] == "remember the milk" and resource["uri"].endswith("notes.txt")
        turn = prompt["params"]["_meta"]["lembas"]["turnId"]
        _event(ws, "s1", {"type": "task", "state": "start", "turnId": turn})
        _finish(ws, prompt)
        _reply(db, chat.id)


def test_files_over_the_limit_are_refused_with_a_sentence(
    client, db, registered, monkeypatch
):
    monkeypatch.setattr(device_link, "MAX_BLOCK_BYTES", 10)
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        ids = [_upload(client, db, "big.txt", b"x" * 64, "text/plain")]
        client.post(
            "/api/chats/start",
            data={"content": "Look", "kind": "agent", "ssh_profile_id": f"device:{token_id}",
                  "project_dir": "/srv/work/app", "file_ids": ids},
        )
        chat = db.scalar(select(Chat))
        reply = _reply(db, chat.id)
        assert "big.txt" in reply.error and "Nothing was sent" in reply.error


def test_an_older_device_gets_the_text_and_is_told_what_it_cannot_take(
    client, db, registered
):
    from tests.test_files import png_bytes

    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, capabilities=["sessions", "status", "steer"])
        _wait_linked(token_id)
        ids = [_upload(client, db, "shot.png", png_bytes(), "image/png"),
               _upload(client, db, "notes.txt", b"remember the milk", "text/plain")]
        chat = _start_with_files(client, db, ws, token_id, ids)
        prompt = ws.receive_json()
        blocks = prompt["params"]["prompt"]
        assert [b["type"] for b in blocks] == ["text", "resource"]
        assert "shot.png" in blocks[0]["text"] and "not sent" in blocks[0]["text"]
        assert "_meta" not in prompt["params"]  # no turn id to a device without `turns`
        _send(ws, {"id": prompt["id"], "result": {"stopReason": "end_turn"}})
        _reply(db, chat.id)


def test_a_terminal_turn_shows_what_it_had_attached(db, registered, make_chat, client):
    async def answer(method, params):
        return {}

    _device(db)  # a connection and a model, so the chat page draws its thread

    link, _ = _turn_link(db, answer)
    link.announced["s1"] = {"cwd": "/home/dev/app"}
    chat_id = _device_chat(db, make_chat, link)

    async def scenario():
        await _emit(link, "s1",
                    {"type": "task", "state": "start", "turnId": "t-1"},
                    {"type": "prompt", "text": "what is in this?", "turnId": "t-1",
                     "attachments": [{"name": "diagram.png", "mimeType": "image/png",
                                      "size": 20480}, {"bogus": True}]},
                    {"type": "text", "text": "A diagram."},
                    {"type": "task", "state": "end", "turnId": "t-1"})
        for _ in range(300):
            running = generation_service.running_for(chat_id)
            if running is not None:
                await asyncio.wait_for(generation_service._TASKS[running.message_id], 5)
                return
            await asyncio.sleep(0.02)

    asyncio.run(scenario())
    db.expire_all()
    asked = db.scalar(select(Message).where(Message.chat_id == chat_id, Message.role == "user"))
    assert asked.terminal_attachments_json == [
        {"name": "diagram.png", "mimeType": "image/png", "size": 20480}
    ]
    page = client.get(f"/chat/{chat_id}").text
    assert "diagram.png" in page and "20.0 KB" in page
    assert "diagram.png" in page and "20.0 KB" in page


# --- 6. Model and effort switched on the device --------------------------------------
def test_a_model_switched_on_the_device_follows_into_the_chat(
    db, registered, make_chat, client
):
    _device(db)  # this instance's model "m"

    def answer(method, params):
        return {}

    link, _ = _turn_link(db, answer, capabilities=(*TURNS, "model"))
    link.initialized["_meta"]["lembas"]["connection"] = "example"
    chat_id = _device_chat(db, make_chat, link)
    chat = db.get(Chat, chat_id)
    chat.model_id = "other"
    db.commit()

    # One of this instance's: the chat's model becomes it, the effort too.
    asyncio.run(_emit(link, "s1", {"type": "model", "ref": "example/m", "effort": "high"}))
    db.expire_all()
    chat = db.get(Chat, chat_id)
    assert chat.model_id == "m" and not chat.device_model
    assert chat.params_json.get("reasoning_effort") == "high"
    assert link.configs[chat_id] == ("c/m", "high")

    # The device's own: a label, and no model sent with the next prompt.
    asyncio.run(_emit(link, "s1", {"type": "model", "ref": "local/qwen3", "effort": "off"}))
    db.expire_all()
    chat = db.get(Chat, chat_id)
    assert chat.device_model == "local/qwen3" and chat.model_id == "m"
    assert "reasoning_effort" not in chat.params_json
    assert device_link.chat_config(db, chat) == ("", "off")
    badge = client.get(f"/api/chats/{chat_id}/device-model").text
    assert "on the device: local/qwen3" in badge and "hidden" not in badge.split(">")[0]

    # A model of the same name on another instance is the device's own too.
    asyncio.run(_emit(link, "s1", {"type": "model", "ref": "elsewhere/m", "effort": "low"}))
    db.expire_all()
    assert db.get(Chat, chat_id).device_model == "elsewhere/m"

    # Choosing a model here takes the chat back.
    client.patch(f"/api/chats/{chat_id}", data={"model_id": "m"})
    db.expire_all()
    assert db.get(Chat, chat_id).device_model == ""


def test_a_device_without_the_model_capability_changes_nothing(db, registered, make_chat):
    _device(db)
    link, _ = _turn_link(db, lambda m, p: {})
    chat_id = _device_chat(db, make_chat, link)
    asyncio.run(_emit(link, "s1", {"type": "model", "ref": "local/qwen3", "effort": "off"}))
    db.expire_all()
    assert db.get(Chat, chat_id).device_model == ""


# --- 1 and 2. `@` files and `/` commands from the device ---------------------------
def test_at_offers_the_device_s_files_by_the_chat_s_session(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _finish(ws, prompt)
        _reply(db, chat.id)

        import threading

        got: dict = {}

        def ask():
            got["page"] = client.get(
                f"/api/devices/{token_id}/files", params={"q": "rea", "chat_id": chat.id}
            )

        asking = threading.Thread(target=ask)
        asking.start()
        search = ws.receive_json()
        assert search["method"] == "_lembas/files/search"
        assert search["params"] == {"sessionId": "s1", "query": "rea", "limit": 20}
        _send(ws, {"id": search["id"], "result": {"root": "/srv/work/app", "files": [
            {"path": "README.md", "kind": "file"}, {"path": "src/<b>", "kind": "dir"},
            {"path": "", "kind": "file"}]}})
        asking.join(10)
        html = got["page"].text
        assert 'data-mention-token="README.md"' in html and "data-mention-device" in html
        assert "&lt;b&gt;" in html and "<b>" not in html
        assert html.count("picker__option\"") == 2


def test_at_asks_by_directory_before_there_is_a_session_and_not_an_old_device(
    client, db, registered
):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, capabilities=["sessions"])
        _wait_linked(token_id)
        # A device that does not list `files` is never asked.
        page = client.get(f"/api/devices/{token_id}/files", params={"q": "x", "cwd": "/srv"})
        assert page.status_code == 200 and "picker__option" not in page.text
    # Somebody else's device is not there at all.
    assert client.get("/api/devices/nope/files").status_code == 404


def test_slash_offers_the_session_s_commands(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        # The device announces its commands for the session, ACP's way.
        _send(ws, {"method": "session/update", "params": {"sessionId": "s1", "update": {
            "sessionUpdate": "available_commands_update",
            "availableCommands": [
                {"name": "review", "description": "Review the diff",
                 "input": {"hint": "what to look at"}, "_meta": {"lembas": {"kind": "custom"}}},
                {"name": "deploy-docs", "description": "A skill",
                 "_meta": {"lembas": {"kind": "skill"}}},
                {"name": "bad name!"}, {"description": "no name"},
            ]}}})
        _until(lambda: device_link.link_for(token_id).commands.get("s1"), "the commands")
        body = client.get(f"/api/devices/{token_id}/commands",
                          params={"chat_id": chat.id}).json()
        assert body["commands"] == [
            {"name": "review", "description": "Review the diff", "hint": "what to look at",
             "kind": "custom"},
            {"name": "deploy-docs", "description": "A skill", "hint": "", "kind": "skill"},
        ]
        _finish(ws, prompt)
        _reply(db, chat.id)


def test_slash_asks_the_device_for_a_directory_with_no_session(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        import threading

        got: dict = {}
        asking = threading.Thread(target=lambda: got.update(body=client.get(
            f"/api/devices/{token_id}/commands", params={"cwd": "/srv/work/app"}).json()))
        asking.start()
        asked = ws.receive_json()
        assert asked["method"] == "_lembas/commands"
        assert asked["params"] == {"cwd": "/srv/work/app"}
        _send(ws, {"id": asked["id"], "result": {"commands": [{"name": "init",
                                                               "description": "Start"}]}})
        asking.join(10)
        assert [c["name"] for c in got["body"]["commands"]] == ["init"]


def test_a_device_command_s_result_is_said_in_the_reply(db, registered, make_chat):
    async def answer(method, params):
        if method == "session/prompt":
            return {"stopReason": "end_turn", "_meta": {"lembas": {
                "turnId": params["_meta"]["lembas"]["turnId"], "command": "review",
                "message": "Nothing to review: the tree is clean."}}}
        return {}

    link, asked = _turn_link(db, answer)
    chat_id = _device_chat(db, make_chat, link)
    _say(db, chat_id, "user", "/review")
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)

    async def scenario():
        generation_service.ensure(chat_id, reply_id)
        await asyncio.wait_for(generation_service._TASKS[reply_id], 5)

    asyncio.run(scenario())
    db.expire_all()
    assert db.get(Message, reply_id).content == "Nothing to review: the tree is clean."
    # A command that ran no turn is not taken for a turn finished before.
    assert "_lembas/session/history" not in [m for m, _ in asked]


# --- Audit fixes ---------------------------------------------------------------------
def test_a_question_left_blank_is_skipped_not_null(client, db, registered):
    """`null` reads, on the device, as the whole card dismissed; a question left
    blank is `{kind: "skipped"}`."""
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _question(ws, 81, "q1")
        card = _until(lambda: _cards(chat.id) and _cards(chat.id)[0], "the card")
        client.post(f"/api/chats/{chat.id}/interaction/{card.id}",
                    data={"choice.q0": "SQLite", "choice.q1": "Cache"})
        answers = ws.receive_json()["result"]["answers"]
        assert answers == [{"kind": "options", "labels": ["SQLite"]},
                           {"kind": "options", "labels": ["Cache"]},
                           {"kind": "skipped"}]
        _finish(ws, prompt)
        _reply(db, chat.id)


def test_settled_takes_down_only_its_own_session_s_card(client, db, registered):
    """Providers that number calls per response give two sessions the same
    tool call id; `settled` for one must leave the other's card alone."""
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        chat_a, prompt_a = _start(client, db, ws, token_id, content="A", session="sA")
        chat_b, prompt_b = _start(client, db, ws, token_id, content="B", session="sB")
        _permission(ws, 201, "call_0", "make a", session="sA")
        _until(lambda: len(_cards(chat_a.id)) == 1, "A's card")
        _permission(ws, 202, "call_0", "make b", session="sB")
        _until(lambda: len(_cards(chat_b.id)) == 1, "B's card")
        _send(ws, {"method": "_lembas/permission/settled",
                   "params": {"sessionId": "sA", "toolCallId": "call_0"}})
        assert ws.receive_json()["id"] == 201
        _until(lambda: not _cards(chat_a.id), "A's card gone")
        assert len(_cards(chat_b.id)) == 1
        card = _cards(chat_b.id)[0]
        client.post(f"/api/chats/{chat_b.id}/interaction/{card.id}", data={"verdict": "deny"})
        assert ws.receive_json()["id"] == 202
        _finish(ws, prompt_a, session="sA")
        _finish(ws, prompt_b, session="sB")
        _reply(db, chat_a.id)


def test_a_terminal_turn_s_card_never_lands_on_the_finished_web_reply(
    db, registered, make_chat
):
    """The web turn has ended on the device; the terminal's next turn asks for
    an approval before its mirror follows it. With no live reply for that turn
    it is the "nobody here" path -- denied, as it is not patient -- and the web
    reply that is finishing shows no card."""
    terminal = "6f1c2c1e-0000-4000-8000-000000000002"
    where: dict = {}
    sent: list = []

    async def answer(method, params):
        if method == "session/prompt":
            web = params["_meta"]["lembas"]["turnId"]
            await _emit(link, "s1",
                        {"type": "task", "state": "start", "turnId": web},
                        {"type": "text", "text": "Fixed."},
                        {"type": "task", "state": "end", "turnId": web},
                        {"type": "task", "state": "start", "turnId": terminal},
                        {"type": "prompt", "text": "now deploy", "turnId": terminal})
            await link.receive(json.dumps({
                "jsonrpc": "2.0", "id": 5, "method": "session/request_permission",
                "params": {"sessionId": "s1", "toolCall": {
                    "toolCallId": "c1", "title": "deploy", "kind": "execute",
                    "rawInput": {"command": "deploy"}},
                    "options": [{"optionId": "once", "kind": "allow_once"},
                                {"optionId": "deny", "kind": "reject_once"}]}}))
            await asyncio.sleep(0.2)
            web_reply = generation_service._RUNNING.get(where["reply"])
            where["card_on_web_reply"] = bool(web_reply and web_reply.waiting)
            return {"stopReason": "end_turn"}
        return {}

    link, asked = _turn_link(db, answer)
    original = link.send_text

    async def capture(text):
        sent.append(json.loads(text))
        await original(text)

    link.send_text = capture
    link.announced["s1"] = {"cwd": "/home/dev/app"}
    chat_id = _device_chat(db, make_chat, link)
    _say(db, chat_id, "user", "fix it")
    where["reply"] = _say(db, chat_id, "assistant", "", complete_=False)

    async def scenario():
        generation_service.ensure(chat_id, where["reply"])
        await asyncio.wait_for(generation_service._TASKS[where["reply"]], 5)

    asyncio.run(scenario())
    assert not where["card_on_web_reply"]
    denied = next(m for m in sent if m.get("id") == 5)
    assert denied["result"]["outcome"]["optionId"] == "deny"


def test_an_infinite_size_in_a_chip_does_not_drop_the_link(client, db, registered):
    assert device_link.attachment_chips([{"name": "a", "size": float("inf")}]) == [
        {"name": "a", "mimeType": "", "size": 0}]
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        _send(ws, {"method": "_lembas/session/announce",
                   "params": {"sessionId": "s9", "cwd": "/srv/work/app", "title": "x"}})
        time.sleep(0.3)
        ws.send_text('{"jsonrpc":"2.0","method":"_lembas/event","params":{"sessionId":"s9",'
                     '"event":{"type":"prompt","text":"hi","turnId":"tt",'
                     '"attachments":[{"name":"a","size":1e999}]}}}')
        time.sleep(0.5)
        assert device_link.link_for(token_id) is not None


def test_the_prompt_limit_counts_the_files_and_a_missing_file_says_so(db, registered):
    link, _ = _turn_link(db, lambda m, p: {}, capabilities=(*TURNS, "attachments"))
    # The text does not count toward the 25 MB of files.
    blocks = device_link.prompt_blocks(link, "x" * (device_link.MAX_PROMPT_BYTES + 1), [])
    assert len(blocks) == 1
    blocks = device_link.prompt_blocks(link, "look", [
        {"name": "gone.png", "kind": "image", "media_type": "image/png", "path": "/nonexistent"},
        {"name": "gone.bin", "kind": "document", "media_type": "application/zip", "path": ""},
    ])
    assert len(blocks) == 1
    assert "gone.png, gone.bin" in blocks[0]["text"] and "missing" in blocks[0]["text"]
    assert "does not take attachments" not in blocks[0]["text"]


def test_sent_turns_are_bounded_and_commands_go_with_their_session(db, registered):
    link, _ = _turn_link(db, lambda m, p: {})
    for n in range(device_link.MAX_SENT_TURNS + 50):
        device_link._remember_turn(link, f"t{n}")
    assert len(link.sent_turns) == device_link.MAX_SENT_TURNS
    assert "t0" not in link.sent_turns and f"t{device_link.MAX_SENT_TURNS + 49}" in link.sent_turns

    link.commands["s1"] = [{"name": "x"}]
    link.commands["s2"] = [{"name": "y"}]
    asyncio.run(link._notification("_lembas/session/deleted", {"sessionId": "s1"}))
    assert "s1" not in link.commands
    device_link.forget_session(link.token_id, "s2")
    assert "s2" not in link.commands


def test_an_odd_image_goes_as_a_file_and_an_unknown_method_is_not_found(db, registered, tmp_path):
    # Found by the cross-project protocol audit.
    link, _ = _turn_link(db, lambda m, p: {}, capabilities=(*TURNS, "attachments"))
    svg = tmp_path / "logo.svg"
    svg.write_text("<svg/>")
    blocks = device_link.prompt_blocks(link, "look", [
        {"name": "logo.svg", "kind": "image", "media_type": "image/svg+xml", "path": str(svg)},
    ])
    assert blocks[1]["type"] == "resource"
    assert blocks[1]["resource"]["mimeType"] == "image/svg+xml" and blocks[1]["resource"]["blob"]
    assert "does not take attachments" not in blocks[0]["text"]

    sent: list[str] = []

    async def capture(text: str) -> None:
        sent.append(text)

    link.send_text = capture
    asyncio.run(link.receive('{"jsonrpc":"2.0","id":7,"method":"_lembas/nothing","params":{}}'))
    assert json.loads(sent[-1])["error"]["code"] == -32601


def test_an_announced_model_on_another_connection_is_not_this_instances(db, registered, make_chat):
    from lembas.db.models import Chat

    link, _ = _turn_link(db, lambda m, p: {})
    link.initialized = {"_meta": {"lembas": {"connection": "example"}}}
    chat = db.get(Chat, make_chat())
    before = (chat.model_id, chat.connection_id)
    device_link._apply_announced(db, link, chat, {"model": f"ollama/{chat.model_id}"})
    assert (chat.model_id, chat.connection_id) == before
