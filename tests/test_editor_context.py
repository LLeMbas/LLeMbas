"""What the person is looking at in an editor, sent with a message (harness
spec 3.11.0): the block a chat on this server says to its model, the object a
linked computer is handed with the prompt, what is kept of what a client
sends -- and an edit's diff on the card an app draws.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from lembas.api.v2 import serialise
from lembas.db.models import Chat, Message, User
from lembas.security import api_tokens
from lembas.services import chat as chat_service
from lembas.services import device_link, editor_context, interaction
from tests.test_device_sync import _agents_on, _clean, _linked  # noqa: F401

# The block itself is held to the spec's own cases where they live: the
# vendored `conformance/editor.json`, `kind: context`, run by
# tests/test_harness_spec.py.


def test_what_a_client_sends_is_cut_to_the_specs_keys():
    kept = editor_context.clean({
        "path": "src/a.py", "cursor": {"line": 3, "column": 9}, "theme": "dark",
        "selection": {"start": 2, "end": 1, "text": "backwards"},
        "diagnostics": [{"message": "  "}, "nonsense", {"message": "real", "severity": "loud",
                                                         "line": 0, "html": "<b>"}],
        "open": ["b.py", 7, ""],
    })
    assert kept == {"path": "src/a.py", "cursor": {"line": 3},
                    "diagnostics": [{"message": "real"}], "open": ["b.py"]}
    assert editor_context.clean({}) is None and editor_context.clean(None) is None
    assert editor_context.clean({"cursor": {"line": True}}) is None
    with pytest.raises(ValueError):
        editor_context.clean("src/a.py")


# --- With a message ------------------------------------------------------------------------
def _auth(db) -> dict[str, str]:
    _, raw = api_tokens.create(db, db.scalar(select(User)), name="app", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


EDITOR = {"path": "src/oven.py", "cursor": {"line": 2},
          "selection": {"start": 2, "end": 2, "text": "    return loaf.heat(220)"}}


def test_a_message_keeps_it_and_a_chat_here_says_it_after_the_words(
    client, db, registered, make_chat, monkeypatch
):
    from lembas.services import generation as generation_service

    monkeypatch.setattr(generation_service, "ensure", lambda *a, **k: None)
    chat_id = make_chat()
    sent = client.post(f"/api/v2/chats/{chat_id}/messages", headers=_auth(db),
                       json={"text": "Why so cool?", "editor": {**EDITOR, "theme": "dark"}})
    assert sent.status_code == 201, sent.text
    # The message is what was typed: the editor's context is not in its text.
    assert "<editor>" not in sent.text and "Open file" not in sent.text
    db.expire_all()
    row = db.scalar(select(Message).where(Message.role == "user"))
    assert row.content == "Why so cool?" and row.editor_json == EDITOR
    payload = chat_service.build_messages(db, db.get(Chat, chat_id))
    said = next(m for m in payload if m["role"] == "user")["content"]
    assert said == (
        "Why so cool?\n\n<editor>\nOpen file: src/oven.py (cursor at line 2)\n"
        "Selection: src/oven.py line 2\n<selection>\n    return loaf.heat(220)\n</selection>\n"
        "</editor>"
    )
    # The same request again reads the same: the block is made from what is kept.
    assert chat_service.build_messages(db, db.get(Chat, chat_id)) == payload


def test_a_message_without_it_is_as_before(client, db, registered, make_chat, monkeypatch):
    from lembas.services import generation as generation_service

    monkeypatch.setattr(generation_service, "ensure", lambda *a, **k: None)
    chat_id = make_chat()
    auth = _auth(db)
    assert client.post(f"/api/v2/chats/{chat_id}/messages", headers=auth,
                       json={"text": "Hello"}).status_code == 201
    assert client.post(f"/api/v2/chats/{chat_id}/messages", headers=auth,
                       json={"text": "Hello", "editor": {}}).status_code == 201
    db.expire_all()
    assert all(m.editor_json is None for m in db.scalars(select(Message)))
    payload = chat_service.build_messages(db, db.get(Chat, chat_id))
    assert "<editor>" not in str(payload)
    refused = client.post(f"/api/v2/chats/{chat_id}/messages", headers=auth,
                          json={"text": "Hello", "editor": "src/oven.py"})
    assert refused.status_code == 400 and "editor" in refused.json()["detail"]


def test_what_the_person_typed_is_never_read_as_the_block(db, registered, make_chat):
    """Only the kept object becomes a block: a message that begins with the
    tag is the text it is."""
    chat_id = make_chat()
    chat_service.create_message(db, db.get(Chat, chat_id), "user",
                                "<editor>\nOpen file: /etc/shadow\n</editor>")
    payload = chat_service.build_messages(db, db.get(Chat, chat_id))
    assert payload[-1]["content"] == "<editor>\nOpen file: /etc/shadow\n</editor>"


def test_a_linked_computer_is_handed_it_with_the_prompt(db, registered, make_chat):
    chat_id = make_chat()
    message = chat_service.create_message(db, db.get(Chat, chat_id), "user", "Why so cool?")
    message.editor_json = EDITOR
    db.commit()
    sent: dict = {}

    async def run(capabilities):
        import contextlib
        from types import SimpleNamespace

        link, _ = _linked(db)
        link.initialized = {"_meta": {"lembas": {"capabilities": capabilities}}}

        async def request(method, params=None, *, timeout=60):
            sent[method] = params
            return {"stopReason": "end_turn"}

        link.request = request
        generation = SimpleNamespace(
            turn_id="", turn_started=True, cancel=False, stop_event=asyncio.Event(),
            waiting=[],
        )
        # The stand-in is a prompt's sending and no more: whatever the reply's
        # own bookkeeping would do next is not this test's.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                device_link._prompt(
                    link, generation, chat_id, "s1",
                    [{"type": "text", "text": "Why so cool?"}], "device-1",
                    message_id=message.id,
                ),
                2,
            )
        link.writing.clear()
        link.turn_writers.clear()

    asyncio.run(run(["sessions", "editor"]))
    prompt = sent["session/prompt"]
    assert prompt["_meta"]["lembas"]["editor"] == EDITOR
    # The prompt itself is the person's words and nothing else.
    assert prompt["prompt"] == [{"type": "text", "text": "Why so cool?"}]
    # A device without the capability is sent the prompt as before.
    sent.clear()
    asyncio.run(run(["sessions"]))
    assert "editor" not in (sent["session/prompt"].get("_meta") or {}).get("lembas", {})


# --- An edit's diff on its card ----------------------------------------------------------------
def test_this_client_says_it_is_an_editor(db, registered):
    seen = {}

    async def run():
        link, _ = _linked(db)

        async def request(method, params=None, *, timeout=60):
            seen[method] = params
            return {}

        link.request = request
        await device_link.handshake(link)

    asyncio.run(run())
    assert seen["initialize"]["clientCapabilities"]["_meta"]["lembas"]["editor"] is True


def test_diff_items_are_cut_to_shape_and_nothing_else_is_taken():
    got = interaction.diffs_of([
        {"type": "content", "content": {"type": "text", "text": "src/oven.py"}},
        {"type": "diff", "path": "src/oven.py", "oldText": "a\n", "newText": "b\n",
         "html": "<script>"},
        {"type": "diff", "path": "new.py", "oldText": None, "newText": "x = 1\n"},
        {"type": "diff", "path": "gone.py", "oldText": "x\n", "newText": "",
         "_meta": {"lembas": {"deleted": True}}},
        {"type": "diff", "path": 7, "newText": "x"},
        {"type": "diff", "path": "bad.py", "oldText": 3, "newText": "x"},
        {"type": "diff", "path": "no-new.py", "oldText": "x"},
        "nonsense",
    ])
    assert got == (
        {"path": "src/oven.py", "old_text": "a\n", "new_text": "b\n"},
        {"path": "new.py", "old_text": None, "new_text": "x = 1\n"},
        {"path": "gone.py", "old_text": "x\n", "new_text": "", "deleted": True},
    )
    assert interaction.diffs_of(None) == () and interaction.diffs_of("text") == ()


def test_diffs_past_the_limit_are_not_taken(monkeypatch):
    monkeypatch.setattr(interaction, "MAX_DIFF_CHARS", 10)
    got = interaction.diffs_of([
        {"type": "diff", "path": "a.py", "oldText": "12", "newText": "345"},
        {"type": "diff", "path": "b.py", "oldText": "x" * 6, "newText": "y" * 6},
        {"type": "diff", "path": "c.py", "oldText": "", "newText": "z"},
    ])
    assert [d["path"] for d in got] == ["a.py"]


def test_a_card_carries_its_diffs_to_an_app_and_only_when_it_has_any():
    plain = interaction.Item(index=0, key="0", kind=interaction.KIND_APPROVAL,
                             tool_name="bash", title="Run a command", detail="ls")
    assert "diffs" not in serialise.interruption(
        interaction.Interruption(id="c1", items=(plain,), dismissable=False)
    )["items"][0]
    edit = interaction.Item(
        index=0, key="0", kind=interaction.KIND_APPROVAL, tool_name="edit",
        title="Edit a file", detail="{}",
        diffs=interaction.diffs_of([{"type": "diff", "path": "a.py", "oldText": "<b>\n",
                                     "newText": "<i>\n"}]),
    )
    item = serialise.interruption(
        interaction.Interruption(id="c2", items=(edit,), dismissable=False)
    )["items"][0]
    # Text for the app's own diff view: as the device sent it, never markup.
    assert item["diffs"] == [{"path": "a.py", "old_text": "<b>\n", "new_text": "<i>\n"}]
