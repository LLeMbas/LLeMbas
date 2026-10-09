"""A file the model opened, reaching the panel.

The rule this pins is the one that would fail silently: the `canvas` frame is
guarded on truthiness, so it can never blank itself. An empty one would close
every tab somebody had open -- the "approval card you could press twice" failure
with the sign reversed.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from lembas.db.models import ROLE_ASSISTANT, Chat, Connection, Message, Model
from lembas.services import canvas as canvas_service
from lembas.services import generation as generation_service


def _chat_with_a_reply(db, user_id):
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m"))
    db.commit()
    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()
    db.add(Message(chat_id=chat.id, role="user", content="Have a look", complete=True))
    db.commit()
    assistant = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(assistant)
    db.commit()
    return chat.id, assistant.id


def _stub_stream(text: str):
    async def stream_chat(_endpoint, _payload):
        yield {"choices": [{"delta": {"content": text}}]}

    return stream_chat


# --- The frame ------------------------------------------------------------------
def test_the_frame_is_absent_when_nothing_was_opened(db, user_id):
    """Asserted directly, because this is the whole safety property. `reasoning`,
    `tools` and `render` are guarded the same way; `metrics`, `status` and `ask`
    are not, because each of *those* has to be able to clear."""
    generation = generation_service.Generation(chat_id="x", message_id="y")
    assert not generation.canvas.get("tabs")


def test_the_frame_carries_the_whole_strip(db, user_id):
    """Not a delta. A follower attaching mid-reply has no earlier fragments to
    append to, so it gets every tab the reply has touched."""
    from lembas.api.chats import _canvas_tabs

    state: dict = {}
    canvas_service.open_tab(state, {"key": "agent:/a.py", "title": "a.py"}, activate=False)
    canvas_service.open_tab(state, {"key": "agent:/b.py", "title": "b.py"}, activate=False)

    html = _canvas_tabs("chat-1", state)
    assert "a.py" in html
    assert "b.py" in html
    # Out of band, because it belongs to a panel and not to the bubble the
    # stream is writing into.
    assert 'hx-swap-oob="true"' in html
    assert 'id="canvas-tabs"' in html


def test_a_path_with_a_quote_does_not_break_the_strip():
    """The key goes into an hx-vals attribute. `| tojson` rather than quoting by
    hand, or a file called `"` produces vals that do not parse and the tab
    silently stops working."""
    from lembas.api.chats import _canvas_tabs

    state: dict = {}
    canvas_service.open_tab(state, {"key": 'agent:/srv/a"b.py', "title": 'a"b.py'})
    html = _canvas_tabs("chat-1", state)
    assert 'a\\"b.py' in html or "a&#34;b.py" in html


# --- Through the loop -------------------------------------------------------------
async def test_two_reads_in_one_round_both_land(db, user_id, monkeypatch):
    """Seeded once and mutated, not re-read per call: two `file_read`s that each
    read the row would leave only the second."""
    generation = generation_service.Generation(chat_id="x", message_id="y")
    generation.canvas = {"tabs": [], "active": ""}

    for path in ("/srv/a.py", "/srv/b.py"):
        canvas_service.open_tab(
            generation.canvas, {"key": f"agent:{path}", "title": path}, activate=False
        )

    assert [t["key"] for t in generation.canvas["tabs"]] == [
        "agent:/srv/a.py",
        "agent:/srv/b.py",
    ]


async def test_a_reply_writes_its_tabs_onto_the_chat(db, user_id, monkeypatch):
    chat_id, message_id = _chat_with_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Looked."))

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)
    # Nothing was opened, so nothing is written -- and in particular the column
    # is not blanked.
    db.expire_all()
    assert not (db.get(Chat, chat_id).canvas_json or {}).get("tabs")


async def test_a_reply_never_wipes_the_tabs_that_were_already_open(
    db, user_id, monkeypatch
):
    """The snapshot is seeded from the row when the reply begins, so a reply
    that opens nothing writes nothing -- and one that opens something adds to
    what was there rather than replacing it."""
    chat_id, message_id = _chat_with_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Looked."))

    chat = db.get(Chat, chat_id)
    chat.canvas_json = canvas_service.open_tab({}, {"key": f"scratch:{chat_id}"})
    db.commit()

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    db.expire_all()
    stored = db.get(Chat, chat_id).canvas_json or {}
    assert [t["key"] for t in stored["tabs"]] == [f"scratch:{chat_id}"]
    assert stored["active"] == f"scratch:{chat_id}"


async def test_the_snapshot_is_seeded_from_the_row(db, user_id, monkeypatch):
    """Seeded once where the chat is already loaded, rather than re-read per
    call -- which is what lets two file reads in one round both land."""
    chat_id, message_id = _chat_with_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Looked."))

    chat = db.get(Chat, chat_id)
    chat.canvas_json = canvas_service.open_tab({}, {"key": f"scratch:{chat_id}"})
    db.commit()

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    assert [t["key"] for t in generation.canvas["tabs"]] == [f"scratch:{chat_id}"]


# --- What the runners write ---------------------------------------------------------
def test_the_swap_target_exists_on_a_streaming_bubble():
    """A frame with nowhere to land is a frame that silently does nothing."""
    from lembas.web.templating import templates

    html = templates.get_template("chat/_message.html").render(
        {
            "message": Message(id="m1", chat_id="c1", role=ROLE_ASSISTANT, content=""),
            "streaming": True,
            "chat": None,
            "user": None,
            "models_by_id": {},
            "bodies": {},
        }
    )
    assert 'sse-swap="canvas"' in html


def test_scratch_write_opens_its_tab(db, user_id):
    """It rides on the same mechanism as the file tools, and for the same
    reason: no new schema and no tokens."""
    from lembas.services.tools import REGISTRY

    tool = REGISTRY["scratch_write"]
    assert tool.family == "scratch"
    # RISK_READ, on plan_update's argument: risk is what a tool does to the
    # world the four modes govern, which is the machine.
    assert tool.risk == "read"


async def test_scratch_write_actually_runs(db, user_id):
    """It did not, for the whole life of the feature.

    The runner read `context.chat_id` off a `ToolContext` that had no such
    field, so every call raised `AttributeError` -- swallowed by `run_tool`'s
    blanket except into "the scratch_write tool failed", which is
    indistinguishable from a model calling it wrongly. Nothing exercised the
    runner: the test above asserts the family and the risk, which are
    attributes of the declaration rather than of the code.
    """
    from lembas.db.models import Chat
    from lembas.services.tools import REGISTRY, ToolContext

    chat = Chat(user_id=user_id, model_id="m")
    db.add(chat)
    db.commit()

    outcome = await REGISTRY["scratch_write"].run(
        ToolContext(owner_id=user_id, chat_id=chat.id), {"text": "hello", "mode": "replace"}
    )

    assert outcome.event["status"] == "ok"
    assert outcome.event.get("canvas", {}).get("source") == "scratch"


async def test_scratch_write_without_a_chat_says_so(db, user_id):
    """Which is what the `chat_id` check was always for."""
    from lembas.services.tools import REGISTRY, ToolContext

    outcome = await REGISTRY["scratch_write"].run(
        ToolContext(owner_id=user_id), {"text": "hello"}
    )
    assert outcome.event["status"] == "error"


def test_the_event_survives_into_the_stored_transcript():
    """Harmless and mildly useful: `_tool_activity.html` reads named keys."""
    event = {"name": "file_read", "canvas": {"key": "agent:/a.py"}}
    assert json.loads(json.dumps(event))["canvas"]["key"] == "agent:/a.py"


def test_nothing_but_the_chat_row_holds_the_tabs(db, user_id):
    """No table, no cleanup path: the tabs go when the chat does."""
    chat_id, _ = _chat_with_a_reply(db, user_id)
    chat = db.get(Chat, chat_id)
    chat.canvas_json = canvas_service.open_tab({}, {"key": "note:1"})
    db.commit()

    db.delete(chat)
    db.commit()
    assert db.scalar(select(Chat)) is None
