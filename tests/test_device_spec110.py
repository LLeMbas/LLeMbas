"""Harness spec 1.1.0 on the device link: what only the instance knows goes
to the device, the plan comes back as a structure, and a turn can be rewound.
(The library's scope went with the server's knowledge bases, spec 3.0.0: a
device that still lists `library` is sent nothing for it.)

Each piece is gated on a capability the device lists in `initialize`
(`context`, `library`, `prototypes`, `plan_state`, `rewind`), so a device
listing none of them is served exactly as before -- which the protocol-1 and
protocol-2 suites keep proving. A fake device speaks the device's half over
the real WebSocket, as in test_device_protocol2.py.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from lembas.db.models import TOKEN_DEVICE, Chat, Message, User
from lembas.security import api_tokens
from lembas.services import device_link, plans, settings_store
from lembas.services import generation as generation_service
from tests.test_device_link import _device, _event, _reply, _wait_linked
from tests.test_device_protocol2 import (
    ALL,
    TURNS,
    _cards,
    _cut_off,
    _finish,
    _hello,
    _send,
    _start,
    _turn_link,
    _until,
)
from tests.test_device_sync import _device_chat

SPEC_110 = ["context", "library", "prototypes", "plan_state", "rewind"]
EVERYTHING = [*ALL, *SPEC_110]

PLAN = {
    "title": "Fix the login bug",
    "summary": "Two changes.",
    "findings": ["Session cookie is not renewed"],
    "objectives": ["Login works"],
    "phases": [
        {"title": "Code",
         "tasks": ["Renew the cookie", {"text": "Add the test", "status": "doing"}]},
        {"title": "Verify", "tasks": [{"text": "Run the suite", "status": "todo"}]},
    ],
}


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    yield
    device_link.reset()


def _lembas_meta(message: dict) -> dict:
    return (message["params"].get("_meta") or {}).get("lembas") or {}


def _next(ws, method: str) -> dict:
    message = ws.receive_json()
    while message.get("method") != method:
        message = ws.receive_json()
    return message


def _personalized(db) -> None:
    settings_store.update(db, {"personalization_enabled": True,
                               "system_prompt": "You are the instance."})
    user = db.scalar(select(User))
    user.settings_json = {
        **(user.settings_json or {}),
        "personalization": {"instructions": "Call me Frodo.", "personality": "concise"},
    }
    db.commit()


# --- 1. The Context: session/new and every prompt --------------------------------
def test_the_context_goes_with_session_new_and_again_on_every_prompt(client, db, registered):
    _personalized(db)
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, EVERYTHING)
        _wait_linked(token_id)
        client.post(
            "/api/chats/start",
            data={"content": "Do the thing", "kind": "agent",
                  "ssh_profile_id": f"device:{token_id}",
                  "project_dir": "/srv/work/app", "agent_mode": "edit"},
        )
        chat = db.scalar(select(Chat).order_by(Chat.created_at.desc()))
        new = _next(ws, "session/new")
        # The mode and the model stay at the top of `_meta`, for protocol 1
        # devices; the Context is under `lembas`.
        assert new["params"]["_meta"]["mode"] == "edit"
        context = new["params"]["_meta"]["lembas"]
        assert context == {
            "system_prompt": "You are the instance.",
            "personalization": {"personality": "concise", "personality_custom": "",
                                "instructions": "Call me Frodo."},
        }
        _send(ws, {"id": new["id"], "result": {"sessionId": "s1"}})
        prompt = _next(ws, "session/prompt")
        meta = _lembas_meta(prompt)
        assert meta["turnId"] and meta["system_prompt"] == "You are the instance."
        # Spec 3.0.0: neither `library` nor `prototypes` is sent any more.
        assert "library" not in meta and "prototypes" not in meta
        _event(ws, "s1", {"type": "task", "state": "start", "turnId": meta["turnId"]})
        _finish(ws, prompt, "Done.")
        _reply(db, chat.id)

        # Changed here between two turns: the chat's own prompt, the
        # personalization off -- the next prompt says so.
        db.expire_all()
        chat = db.get(Chat, chat.id)
        chat.system_prompt = "Be brief."
        db.commit()
        settings_store.update(db, {"personalization_enabled": False})
        client.post(f"/api/chats/{chat.id}/messages", data={"content": "And again"})
        prompt = _next(ws, "session/prompt")
        meta = _lembas_meta(prompt)
        assert meta["system_prompt"] == "Be brief."
        assert meta["personalization"] is None
        assert "prototypes" not in meta
        _event(ws, "s1", {"type": "task", "state": "start", "turnId": meta["turnId"]})
        _finish(ws, prompt, "Again.")
        _until(lambda: db.expire_all() or db.scalar(
            select(Message).where(Message.chat_id == chat.id, Message.content == "Again.")
        ), "the second reply")


def test_a_device_without_the_capabilities_gets_no_context(client, db, registered):
    _personalized(db)
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, [*ALL, "library"])
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        # `library` is no Context any more, and nothing it did not list is sent.
        meta = _lembas_meta(prompt)
        assert set(meta) == {"turnId"}
        _finish(ws, prompt)
        _reply(db, chat.id)


def test_context_for_reads_the_chats_own_prompt_over_the_instances(db, registered, make_chat):
    _personalized(db)
    link, _ = _turn_link(db, lambda m, p: {}, capabilities=[*TURNS, "context"])
    chat = db.get(Chat, _device_chat(db, make_chat, link))
    user = db.get(User, chat.user_id)
    assert device_link.context_for(db, link, user, chat)["system_prompt"] == (
        "You are the instance."
    )
    chat.system_prompt = "  The chat's.  "
    db.commit()
    context = device_link.context_for(db, link, user, chat)
    assert context["system_prompt"] == "The chat's."
    assert "library" not in context and "prototypes" not in context
    # Switched off by the person: the device's own.
    user.settings_json = {"personalization": {"instructions": "x", "on": False}}
    db.commit()
    assert device_link.context_for(db, link, user, chat)["personalization"] is None


# --- 2. The plan as a structure ------------------------------------------------------
def _plan_update(ws, plan, session="s1"):
    entries = [{"content": t, "priority": "medium", "status": "pending"}
               for t in ["Renew the cookie", "Add the test", "Run the suite"]]
    _send(ws, {"method": "session/update", "params": {
        "sessionId": session,
        "update": {"sessionUpdate": "plan", "entries": entries if plan else [],
                   "_meta": {"lembas": {"plan": plan}}},
    }})


def test_a_plan_update_lands_on_the_reply_and_on_the_chat(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, EVERYTHING)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _plan_update(ws, PLAN)
        running = generation_service.running_for(chat.id)
        expected = plans.normalise(PLAN)
        _until(lambda: running.plan == expected, "the plan on the reply")
        assert running.todos is None
        _finish(ws, prompt, "Planned.")
        reply = _reply(db, chat.id)
        assert reply.plan_json == expected
        db.expire_all()
        assert db.get(Chat, chat.id).plan == expected

        # Cleared after the turn, with nobody writing: the chat's panel goes
        # too -- `_persist` never clears a plan, so this one reaches the row.
        _plan_update(ws, None)
        _until(lambda: db.expire_all() or db.get(Chat, chat.id).plan == {}, "the plan cleared")
        assert db.get(Chat, chat.id).plan_message_id is None


def test_a_device_without_plan_state_keeps_the_todo_path(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, ALL)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _plan_update(ws, PLAN)  # not listed: not read
        items = [{"content": "Read the form", "status": "completed"},
                 {"content": "Write a test", "status": "pending"}]
        _event(ws, "s1", {"type": "todos", "items": items})
        running = generation_service.running_for(chat.id)
        _until(lambda: running.todos, "the todo list")
        assert running.plan is None
        _finish(ws, prompt, "On it.")
        _reply(db, chat.id)
        db.expire_all()
        assert db.get(Chat, chat.id).plan == plans.from_todos(items)


def test_a_plan_engine_event_is_read_too():
    g = generation_service.Generation(chat_id="c", message_id="m")
    g.todos = [{"content": "x", "status": "pending"}]
    device_link.apply_event(g, {"type": "plan", "plan": PLAN})
    assert g.plan == plans.normalise(PLAN) and g.todos is None
    device_link.apply_event(g, {"type": "plan", "plan": None})
    assert g.plan is None


def test_the_device_plan_card_is_the_structured_one(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, EVERYTHING)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _send(ws, {"id": 201, "method": "_lembas/plan", "params": {
            "sessionId": "s1", "toolCallId": "p1", "path": ".agent/plans/p.md",
            "text": "# Plan as text\n\n1. Renew the cookie", "plan": PLAN,
            "_meta": {"lembas": {}},
        }})
        card = _until(lambda: _cards(chat.id) and _cards(chat.id)[0], "the plan card")
        item = card.items[0]
        assert card.kind == "plan" and card.tool_call_id == "p1" and card.dismissable
        assert item.plan == plans.normalise(PLAN)
        assert item.mode_choice and item.tool_name == "plan_submit"
        assert item.detail_html == ""  # the structure is drawn, not the text
        from lembas.api.chats import _ask_html

        html = _ask_html(chat.id, card)
        assert "Fix the login bug" in html and "Renew the cookie" in html
        assert "Plan as text" not in html and ".agent/plans/p.md" in html

        # The answer round-trips as before: approved into a mode.
        client.post(f"/api/chats/{chat.id}/interaction/{card.id}",
                    data={"verdict": "allow", "choice.plan": "manual"})
        assert ws.receive_json()["result"] == {"outcome": "approve", "mode": "manual"}

        # Sent back with what to change.
        _send(ws, {"id": 202, "method": "_lembas/plan", "params": {
            "sessionId": "s1", "toolCallId": "p2", "path": "p.md", "text": "", "plan": PLAN,
            "_meta": {"lembas": {}},
        }})
        card = _until(lambda: _cards(chat.id) and _cards(chat.id)[0], "the second card")
        client.post(f"/api/chats/{chat.id}/interaction/{card.id}",
                    data={"verdict": "deny", "reason": "Tests first."})
        assert ws.receive_json()["result"] == {"outcome": "revise", "feedback": "Tests first."}
        _finish(ws, prompt)
        _reply(db, chat.id)


def test_a_text_only_plan_is_still_the_text_card(client, db, registered):
    """A device with `plan_state` that sends no `plan` (or one without it
    sending one anyway) draws the text, as before."""
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, ALL)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _send(ws, {"id": 203, "method": "_lembas/plan", "params": {
            "sessionId": "s1", "toolCallId": "p3", "path": "p.md",
            "text": "# Plan as text", "plan": PLAN, "_meta": {"lembas": {}},
        }})
        card = _until(lambda: _cards(chat.id) and _cards(chat.id)[0], "the plan card")
        assert card.items[0].plan is None and "<h1>Plan as text</h1>" in card.items[0].detail_html
        assert card.items[0].tool_name == "plan_exit"
        client.post(f"/api/chats/{chat.id}/interaction/{card.id}", data={"verdict": "deny"})
        assert ws.receive_json()["result"] == {"outcome": "dismissed"}
        _finish(ws, prompt)
        _reply(db, chat.id)


def test_status_plan_seeds_the_reply_on_reattach(db, registered, make_chat):
    turn = {}

    def answer(method, params):
        if method == "_lembas/session/status":
            return {"exists": True, "busy": False, "queued": [], "lastTurnId": turn["id"],
                    "plan": PLAN}
        if method == "_lembas/session/history":
            return {"turns": [{"role": "user", "text": "build it", "turnId": turn["id"]},
                              {"role": "assistant", "text": "Built."}]}
        return {}

    link, asked = _turn_link(db, answer, capabilities=[*TURNS, "plan_state"])
    chat_id = _device_chat(db, make_chat, link)
    turn["id"], reply_id = _cut_off(db, chat_id)
    g = generation_service.Generation(chat_id=chat_id, message_id=reply_id)
    g.turn_id = turn["id"]
    assert asyncio.run(device_link._reattach(link, g, chat_id, "s1", "device-1")) is False
    assert g.plan == plans.normalise(PLAN) and g.text == "Built."
    db.expire_all()
    assert db.get(Chat, chat_id).plan == plans.normalise(PLAN)


def test_status_plan_is_not_read_without_the_capability(db, registered, make_chat):
    turn = {}

    def answer(method, params):
        if method == "_lembas/session/status":
            return {"exists": True, "busy": False, "queued": [], "lastTurnId": turn["id"],
                    "plan": PLAN}
        return {"turns": []}

    link, _ = _turn_link(db, answer)
    chat_id = _device_chat(db, make_chat, link)
    turn["id"], reply_id = _cut_off(db, chat_id)
    g = generation_service.Generation(chat_id=chat_id, message_id=reply_id)
    g.turn_id = turn["id"]
    asyncio.run(device_link._reattach(link, g, chat_id, "s1", "device-1"))
    assert g.plan is None


# --- 3. Rewind: Edit and Regenerate on a device chat ----------------------------------
def _answer_rewind(ws, result=None, error=None):
    asked = _next(ws, "_lembas/session/rewind")
    if error:
        _send(ws, {"id": asked["id"], "error": {"code": -32602, "message": error}})
    else:
        _send(ws, {"id": asked["id"], "result": result or {"reverted": 1}})
    return asked["params"]


def _turn_of(ws, session="s1"):
    prompt = _next(ws, "session/prompt")
    turn = _lembas_meta(prompt).get("turnId")
    _event(ws, session, {"type": "task", "state": "start", "turnId": turn})
    return prompt


def test_edit_and_regenerate_rewind_the_device_first(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, EVERYTHING)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _finish(ws, prompt, "First.")
        reply = _reply(db, chat.id)
        asked = db.scalar(select(Message).where(Message.chat_id == chat.id, Message.role == "user"))

        # The controls are there, and say what a rewind does not undo.
        page = client.get(f"/chat/{chat.id}").text
        assert f"/messages/{asked.id}/edit" in page
        assert f"/messages/{reply.id}/regenerate" in page
        assert "Files the device changed are not undone" in page
        assert client.get(f"/api/chats/{chat.id}/messages/{asked.id}/edit").status_code == 200

        # Regenerate: the device drops the turn, and the same words go again
        # as a new turn -- a new id, since the device answers a known id with
        # the old answer.
        response_holder = {}

        def regenerate():
            response_holder["r"] = client.post(
                f"/api/chats/{chat.id}/messages/{reply.id}/regenerate"
            )

        import threading

        worker = threading.Thread(target=regenerate)
        worker.start()
        rewound = _answer_rewind(ws)
        assert rewound == {"sessionId": "s1", "turnId": asked.id}
        prompt = _turn_of(ws)
        assert prompt["params"]["prompt"][0]["text"] == "Do the thing"
        new_turn = _lembas_meta(prompt)["turnId"]
        assert new_turn != asked.id
        _finish(ws, prompt, "Second.")
        worker.join(10)
        assert response_holder["r"].status_code == 200
        _until(lambda: db.expire_all() or _reply(db, chat.id).content == "Second.", "regenerated")
        asked = db.scalar(select(Message).where(Message.chat_id == chat.id, Message.role == "user"))
        assert asked.id == new_turn and asked.content == "Do the thing"
        assert db.query(Message).filter(Message.chat_id == chat.id).count() == 2

        # Edit: rewound to the edited turn, which goes as a new turn with
        # the new words.
        def edit():
            response_holder["e"] = client.post(
                f"/api/chats/{chat.id}/messages/{asked.id}/edit", data={"content": "Do it better"}
            )

        worker = threading.Thread(target=edit)
        worker.start()
        assert _answer_rewind(ws)["turnId"] == asked.id
        prompt = _turn_of(ws)
        assert prompt["params"]["prompt"][0]["text"] == "Do it better"
        assert _lembas_meta(prompt)["turnId"] != asked.id
        _finish(ws, prompt, "Better.")
        worker.join(10)
        assert response_holder["e"].status_code == 200
        _until(lambda: db.expire_all() or _reply(db, chat.id).content == "Better.", "edited")
        rows = list(db.scalars(select(Message).where(Message.chat_id == chat.id)
                               .order_by(Message.created_at)))
        assert [r.content for r in rows] == ["Do it better", "Better."]


def test_a_device_that_refuses_the_rewind_is_the_answer(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, EVERYTHING)
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _finish(ws, prompt, "First.")
        reply = _reply(db, chat.id)
        import threading

        holder = {}
        worker = threading.Thread(target=lambda: holder.setdefault(
            "r", client.post(f"/api/chats/{chat.id}/messages/{reply.id}/regenerate")))
        worker.start()
        _answer_rewind(ws, error="this session is working — rewind it when the reply is done")
        worker.join(10)
        assert holder["r"].status_code == 409
        assert "rewind it when the reply is done" in holder["r"].text
        # Nothing here changed either.
        db.expire_all()
        assert _reply(db, chat.id).content == "First."


def test_without_rewind_the_controls_stay_hidden_and_the_routes_refuse(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, [*ALL, "context", "library", "prototypes", "plan_state"])
        _wait_linked(token_id)
        chat, prompt = _start(client, db, ws, token_id)
        _finish(ws, prompt, "First.")
        reply = _reply(db, chat.id)
        asked = db.scalar(select(Message).where(Message.chat_id == chat.id, Message.role == "user"))
        page = client.get(f"/chat/{chat.id}").text
        assert f"/messages/{asked.id}/edit" not in page
        assert f"/messages/{reply.id}/regenerate" not in page
        assert client.get(f"/api/chats/{chat.id}/messages/{asked.id}/edit").status_code == 409
        assert client.post(f"/api/chats/{chat.id}/messages/{asked.id}/edit",
                           data={"content": "two"}).status_code == 409
        assert client.post(
            f"/api/chats/{chat.id}/messages/{reply.id}/regenerate"
        ).status_code == 409
    # Not linked at all: hidden as well, whatever the device could do.
    page = client.get(f"/chat/{chat.id}").text
    assert f"/messages/{asked.id}/edit" not in page


def test_rewind_asks_nothing_it_cannot(db, registered, make_chat):
    link, asked = _turn_link(db, lambda m, p: {"reverted": 2}, capabilities=[*TURNS, "rewind"])
    chat = db.get(Chat, _device_chat(db, make_chat, link))
    assert asyncio.run(device_link.rewind(link, chat, "t1")) == 2
    assert asyncio.run(device_link.rewind(link.token_id, chat, "t1")) == 2
    assert asked[-1] == ("_lembas/session/rewind", {"sessionId": "s1", "turnId": "t1"})
    assert asyncio.run(device_link.rewind(link, chat, "")) is None
    assert asyncio.run(device_link.rewind("no-such-token", chat, "t1")) is None
    link.initialized = {"_meta": {"lembas": {"capabilities": list(TURNS)}}}
    assert asyncio.run(device_link.rewind(link, chat, "t1")) is None
    assert not device_link.offers(link.token_id, "rewind")


# --- 4. Memory scoped to the chat on /mcp ------------------------------------------
def _library_token(db, name="dev") -> tuple[str, dict[str, str]]:
    token, raw = api_tokens.create(
        db, db.scalar(select(User)), name=name, scopes=["library", "link"],
        kind=TOKEN_DEVICE, device_host=name,
    )
    return token.id, {"Authorization": f"Bearer {raw}"}


def _call(client, headers, name, arguments, scope=None):
    params = {"name": name, "arguments": arguments}
    if scope is not None:
        params["_meta"] = {"lembas": {"scope": scope}, "progressToken": 1}
    return client.post(
        "/mcp", headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params},
    ).json()


def test_a_session_that_is_not_this_devices_is_refused(client, db, registered, make_chat):
    token_id, headers = _library_token(db)
    chat = db.get(Chat, make_chat())
    chat.device_token_id = token_id
    chat.device_session_id = "ses_1"
    db.commit()
    assert _call(client, headers, "memory", {"action": "add", "content": "x"}, scope="ses_1")[
        "result"
    ]
    # Another device of the same account, naming this device's session.
    _, others = _library_token(db, "other")
    refused = _call(client, others, "memory", {"action": "add", "content": "y"}, scope="ses_1")
    assert refused["error"] == {"code": -32602, "message": "not this device's session"}
    # A session nobody has.
    refused = _call(client, headers, "memory", {"action": "add", "content": "z"}, scope="ses_404")
    assert refused["error"]["code"] == -32602
    # No scope: as before.
    no_scope = _call(client, headers, "memory", {"action": "add", "content": "w"})
    assert no_scope["result"]["isError"] is False

