"""The tool loop: one reply, several requests."""

from __future__ import annotations

from lembas.db.models import ROLE_ASSISTANT, Chat, Connection, Message, Model
from lembas.services import generation as generation_service
from lembas.services import settings_store
from lembas.services import steps as steps_service
from lembas.services import tools as tools_service
from lembas.services.search.base import SearchResult


def _chat_with_tools(db, user_id):
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()
    db.add(Message(chat_id=chat.id, role="user", content="What is a mallorn?", complete=True))
    db.commit()
    assistant = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(assistant)
    db.commit()
    return chat.id, assistant.id


def _tool_call_chunk(name: str, arguments: str) -> dict:
    return {
        "choices": [
            {"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                "name": name, "arguments": arguments}}]}}
        ]
    }


def _text_chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


def _stub_stream(rounds, seen_payloads):
    """A stream_chat that returns a different scripted round each time."""

    async def stream_chat(_endpoint, payload):
        seen_payloads.append(payload)
        for chunk in rounds[min(len(seen_payloads) - 1, len(rounds) - 1)]:
            yield chunk

    return stream_chat


async def test_a_tool_call_produces_a_second_request(db, user_id, monkeypatch):
    """The whole point: one reply, two round trips, with the search result in
    the second one's messages."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)

    async def fake_search(_config, _query, *, limit=None):
        return [SearchResult("Mallorn", "https://tolkien.test/mallorn", "A golden tree.")]

    monkeypatch.setattr("lembas.services.search.run", fake_search)

    payloads = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [
                [_tool_call_chunk("web_search", '{"query": "mallorn"}')],
                [_text_chunk("A mallorn is a golden tree.")],
            ],
            payloads,
        ),
    )
    monkeypatch.setattr(
        "lembas.services.chat.generate_title", _never_called_title
    )

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    assert len(payloads) == 2, "the model asked for a tool, so it must be asked again"
    assert generation.text == "A mallorn is a golden tree."

    # The second request carries the assistant's own call back, then the result.
    followups = payloads[1]["messages"][-2:]
    assert followups[0]["tool_calls"][0]["function"]["name"] == "web_search"
    assert followups[1]["role"] == "tool"
    assert "https://tolkien.test/mallorn" in followups[1]["content"]

    # And the reader gets to see what it looked up.
    assert generation.tool_events[0]["query"] == "mallorn"
    assert generation.tool_events[0]["results"][0]["url"] == "https://tolkien.test/mallorn"


async def test_the_tools_array_is_absent_without_the_capability(db, user_id, monkeypatch):
    chat_id, message_id = _chat_with_tools(db, user_id)
    # Search enabled, but the model is not marked as supporting tools.
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    model = db.query(Model).first()
    model.capabilities_json = {}
    db.commit()

    payloads = []
    monkeypatch.setattr(
        generation_service, "stream_chat", _stub_stream([[_text_chunk("hi")]], payloads)
    )
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    await generation_service._run(
        generation_service.Generation(chat_id=chat_id, message_id=message_id)
    )
    assert "tools" not in payloads[0]


async def test_text_before_a_tool_call_is_kept(db, user_id, monkeypatch):
    """A model that narrates what it is about to look up must not lose that
    when the results come back."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)

    monkeypatch.setattr(
        "lembas.services.search.run", _empty_search
    )
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [
                [
                    _text_chunk("Let me look that up. "),
                    _tool_call_chunk("web_search", '{"query": "mallorn"}'),
                ],
                [_text_chunk("Nothing found.")],
            ],
            [],
        ),
    )
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)
    assert generation.text == "Let me look that up. Nothing found."


async def test_a_model_that_only_ever_calls_tools_is_stopped(db, user_id, monkeypatch):
    """Otherwise a small model that has decided searching is the answer keeps
    searching until the context runs out, at a full request each time."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)

    monkeypatch.setattr("lembas.services.search.run", _empty_search)
    # Set explicitly rather than read from the constant: a test that reads the
    # number under test passes whatever the number becomes, which is the
    # assertion nobody wanted.
    settings_store.update(db, {"max_chat_rounds": 3})

    payloads = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream([[_tool_call_chunk("web_search", '{"query": "x"}')]], payloads),
    )
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    # Three rounds that may call tools, the one that notices the ceiling, and
    # the one asked for an answer with the tools withdrawn.
    assert len(payloads) == 5
    # And the last request carried no tools at all, which is what makes it a
    # round the model can only answer.
    assert "tools" not in payloads[-1]
    # Recorded rather than silently dropped: an answer that stops here has to
    # be explicable.
    assert generation.tool_events[-1]["status"] == "error"
    assert "3 rounds" in generation.tool_events[-1]["error"]


async def test_running_out_of_rounds_still_produces_an_answer(db, user_id, monkeypatch):
    """The bug this exists for.

    A model that goes straight to tool calls has written no prose at all by the
    time a budget runs out, so ending the reply there produced an empty bubble
    with an error line under it -- a good piece of research, six searches deep,
    thrown away. The tools are withdrawn and it is asked once more instead: what
    it gathered is in the transcript either way, and one request turns it into
    an answer.
    """
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)
    monkeypatch.setattr("lembas.services.search.run", _empty_search)
    settings_store.update(db, {"max_chat_rounds": 2})

    payloads: list[dict] = []

    async def stream_chat(_endpoint, payload):
        payloads.append(payload)
        # Exactly what a real model does: call tools while it has them, and
        # answer when it has none.
        if payload.get("tools"):
            yield _tool_call_chunk("web_search", '{"query": "x"}')
        else:
            yield {"choices": [{"delta": {"content": "Here is what I found."}}]}

    monkeypatch.setattr(generation_service, "stream_chat", stream_chat)
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    assert generation.text == "Here is what I found."
    assert not generation.error
    # And the reader can still tell this apart from an answer the model chose
    # to give, which is what the event is for.
    assert generation.tool_events[-1]["status"] == "error"
    assert "2 rounds" in generation.tool_events[-1]["error"]


async def test_no_ceiling_by_default(db, user_id):
    """A number low enough to be reached by ordinary work is a schedule, not a
    ceiling. What bounds an ordinary chat is the context window."""
    assert settings_store.chat_rounds(db) == 0


async def test_without_a_ceiling_the_model_is_told_to_keep_working(db, user_id):
    """Exactly one of the two fragments ever appears. With no budget the model
    must not be left with nothing said about when to stop -- and must certainly
    not be told it has a budget of two hundred, which it would ration."""
    from lembas.db.models import User
    from lembas.services import harness
    from lembas.services import tools as tools_service

    chat_id, _message_id = _chat_with_tools(db, user_id)
    chat = db.get(Chat, chat_id)
    user = db.get(User, user_id)
    offered = tools_service.resolve_tools(db, chat, user).schemas

    values = harness.context_variables(db, user, offered, chat)
    assert values["round_budget"] == ""
    assert values["unbounded"] == "yes"

    text = harness.compose(db, user, offered, chat)
    assert "Keep working until the task is actually done" in text
    assert "rounds of tool calls before you have to" not in text


async def test_with_a_ceiling_the_model_is_told_the_budget(db, user_id):
    from lembas.db.models import User
    from lembas.services import harness
    from lembas.services import tools as tools_service

    settings_store.update(db, {"max_chat_rounds": 3})
    chat_id, _message_id = _chat_with_tools(db, user_id)
    chat = db.get(Chat, chat_id)
    user = db.get(User, user_id)
    offered = tools_service.resolve_tools(db, chat, user).schemas

    values = harness.context_variables(db, user, offered, chat)
    assert values["round_budget"] == "3"
    assert values["unbounded"] == ""

    text = harness.compose(db, user, offered, chat)
    assert "at most 3 rounds" in text
    assert "Keep working until the task is actually done" not in text


async def test_a_prompt_queued_during_a_one_round_reply_waits_for_its_own(
    db, user_id, monkeypatch
):
    """`_inject` only takes a prompt in while there is a round left to answer
    in. With a ceiling of one there never is, so a queued message is not
    swallowed into a reply that then has no chance to address it -- it waits for
    `_drain`, which always gives it a reply of its own.

    One is no longer the default, but it is still a setting somebody can choose,
    and "it happens to work" and "it is meant to work" look the same until
    somebody changes the guard.
    """
    settings_store.update(db, {"max_chat_rounds": 1})
    from lembas.db.models import Message
    from lembas.services import chat as chat_service

    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)
    chat = db.get(Chat, chat_id)
    queued = chat_service.create_message(db, chat, "user", "actually, do it the other way",
                                         queued=True)
    queued_id = queued.id

    monkeypatch.setattr("lembas.services.search.run", _empty_search)
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [[_tool_call_chunk("web_search", '{"query": "x"}')], [_text_chunk("Done.")]],
            [],
        ),
    )
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    # The row, not the payload: it was handed to a fresh reply by `_drain`,
    # which is what clears `queued`.
    db.expire_all()
    assert db.get(Message, queued_id).queued is False
    assert generation.drained is True


async def test_tool_activity_is_stored_with_the_message(db, user_id, monkeypatch):
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)

    async def fake_search(_config, _query, *, limit=None):
        return [SearchResult("Mallorn", "https://tolkien.test/m", "A tree.")]

    monkeypatch.setattr("lembas.services.search.run", fake_search)
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [
                [_tool_call_chunk("web_search", '{"query": "mallorn"}')],
                [_text_chunk("Done.")],
            ],
            [],
        ),
    )
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    await generation_service._run(
        generation_service.Generation(chat_id=chat_id, message_id=message_id)
    )

    stored = db.get(Message, message_id)
    db.refresh(stored)
    assert stored.tool_calls_json[0]["query"] == "mallorn"
    assert stored.complete is True


async def _empty_search(_config, _query, *, limit=None):
    return []


async def _never_called_title(*_args, **_kwargs):
    """Auto-titling makes its own request; these tests are about the tool loop."""
    return "A title"


# --- Progress and concurrency ------------------------------------------------
async def test_the_status_names_the_running_tool_and_is_cleared(db, user_id, monkeypatch):
    """A remote tool can take seconds with nothing streaming, and a silent
    pause is exactly what a hang looks like."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)

    seen: list[str] = []

    async def fake_search(_config, _query, *, limit=None):
        seen.append(generation.status)
        return []

    monkeypatch.setattr("lembas.services.search.run", fake_search)
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [[_tool_call_chunk("web_search", '{"query": "mallorn"}')], [_text_chunk("Done.")]],
            [],
        ),
    )

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    # In words, from services/tool_labels.py -- the same table the transcript
    # and the approval card read. It used to say "Running web_search…".
    assert seen == ["Running Web search…"]
    assert generation.status == "", "and it is cleared once they are done"


async def test_results_stay_paired_with_their_calls_when_run_together(db, user_id, monkeypatch):
    """Indexed rather than appended as they finish: an endpoint matching on
    tool_call_id would otherwise pair the right id with the wrong content."""
    import asyncio

    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)

    async def slow_first(_config, query, *, limit=None):
        # The first call finishes last, which is the whole point of the test.
        await asyncio.sleep(0.02 if query == "first" else 0)
        return [SearchResult(f"result for {query}", f"https://t.test/{query}", "")]

    monkeypatch.setattr("lembas.services.search.run", slow_first)

    two_calls = {
        "choices": [
            {"delta": {"tool_calls": [
                {"index": 0, "id": "a", "function": {
                    "name": "web_search", "arguments": '{"query": "first"}'}},
                {"index": 1, "id": "b", "function": {
                    "name": "web_search", "arguments": '{"query": "second"}'}},
            ]}}
        ]
    }
    payloads: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream([[two_calls], [_text_chunk("Done.")]], payloads),
    )

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    turns = [m for m in payloads[1]["messages"] if m.get("role") == "tool"]
    assert [turn["tool_call_id"] for turn in turns] == ["a", "b"]
    assert "first" in turns[0]["content"] and "second" in turns[1]["content"]
    # And the transcript keeps the same order.
    assert [event["query"] for event in generation.tool_events] == ["first", "second"]


async def test_a_custom_tool_runs_inside_the_loop(db, user_id, monkeypatch, mock_http):
    """End to end: a row becomes an offered schema, the model calls it, and the
    result comes back in the next request's messages."""
    import httpx

    from lembas.db.models import CustomTool

    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    mock_http(lambda _r: httpx.Response(200, json={"summary": "Sunny in Minas Tirith."}))

    db.add(
        CustomTool(
            slug="weather",
            name="Weather",
            description="Look up the weather.",
            url_template="https://api.test/{{city}}",
            parameters_json={"type": "object", "properties": {"city": {"type": "string"}}},
            response_mode="json",
            response_path="summary",
        )
    )
    db.commit()

    chat_id, message_id = _chat_with_tools(db, user_id)
    payloads: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [
                [_tool_call_chunk("weather", '{"city": "Minas Tirith"}')],
                [_text_chunk("It is sunny.")],
            ],
            payloads,
        ),
    )

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    offered = {tool["function"]["name"] for tool in payloads[0]["tools"]}
    assert "weather" in offered

    tool_turns = [m for m in payloads[1]["messages"] if m.get("role") == "tool"]
    assert tool_turns[0]["content"] == "Sunny in Minas Tirith."
    assert generation.tool_events[0]["kind"] == "custom"
    assert generation.tool_events[0]["label"] == "Weather"


def test_the_default_and_the_fallback_cannot_drift(db):
    """`tools.MAX_ROUNDS` exists for callers with no session; the setting is
    what the loop and the harness read. Two numbers meaning one thing is how
    a model gets told a budget it does not have."""
    assert tools_service.MAX_ROUNDS == settings_store.DEFAULT_CHAT_ROUNDS
    assert settings_store.chat_rounds(db) == tools_service.MAX_ROUNDS


async def test_a_ceiling_of_zero_does_not_mean_zero_rounds(db, user_id, monkeypatch):
    """It means no ceiling. Read carelessly it would mean the model never gets
    to call anything, which is the opposite."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    settings_store.update(db, {"max_chat_rounds": 0})
    chat_id, message_id = _chat_with_tools(db, user_id)

    monkeypatch.setattr("lembas.services.search.run", _empty_search)
    payloads = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [[_tool_call_chunk("web_search", '{"query": "x"}')], [_text_chunk("Done.")]],
            payloads,
        ),
    )
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    assert generation.text == "Done."
    assert len(payloads) == 2, "it called a tool and then answered, uninterrupted"


def test_the_ceiling_is_clamped(db):
    settings_store.update(db, {"max_chat_rounds": 9999})
    assert settings_store.chat_rounds(db) == 100
    settings_store.update(db, {"max_chat_rounds": -5})
    assert settings_store.chat_rounds(db) == 0


# --- The marks that make a reply a sequence ------------------------------------
async def test_text_after_a_tool_call_renders_after_it(db, user_id, monkeypatch):
    """The whole redesign, end to end. Before the marks existed this bubble
    showed both sentences together at the bottom, under the tool block, however
    many rounds apart the model had written them."""
    from lembas.db.models import Message
    from lembas.web.templating import templates

    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)

    monkeypatch.setattr("lembas.services.search.run", _empty_search)
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [
                [
                    _text_chunk("Let me look that up. "),
                    _tool_call_chunk("web_search", '{"query": "mallorn"}'),
                ],
                [_text_chunk("Nothing found.")],
            ],
            [],
        ),
    )
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    message = db.get(Message, message_id)
    db.refresh(message)
    assert message.steps_json, "the marks reached the row"
    assert message.content == "Let me look that up. Nothing found.", "and the text is untouched"

    html = templates.get_template("chat/_steps.html").render(
        {
            "steps": steps_service.for_message(message),
            "live": False,
            "message": message,
        }
    )
    assert html.index("Let me look that up") < html.index("tool-activity")
    assert html.index("tool-activity") < html.index("Nothing found")


async def test_a_budget_event_gets_a_step_of_its_own(db, user_id, monkeypatch):
    """`_wrap_up` and `_gave_up` append outside the round loop. Without a mark
    the one line saying why the reply stopped would land in the step still being
    written, where the live view has no tools slot -- so the reader would be told
    nothing at all."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)
    settings_store.update(db, {"max_chat_rounds": 1})

    monkeypatch.setattr("lembas.services.search.run", _empty_search)
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream([[_tool_call_chunk("web_search", '{"query": "x"}')]], []),
    )
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    built = steps_service.closed_from(generation, since=0)
    events = [event for step in built for event in step.events]
    # `kind`, not `name`: the out-of-rounds branch names the event after the
    # tool that was refused, so the reader sees which call was cut off.
    assert any("Stopped after" in (event.get("error") or "") for event in events), (
        "the explanation is in a closed step rather than stranded in the tail"
    )


async def test_a_reply_that_calls_nothing_writes_no_marks(db, user_id, monkeypatch):
    """And therefore renders as the old layout, which for a reply with no tool
    blocks is the same sequence anyway. That is what makes the compatibility
    branch honest rather than a special case."""
    from lembas.db.models import Message

    chat_id, message_id = _chat_with_tools(db, user_id)
    monkeypatch.setattr(
        generation_service, "stream_chat", _stub_stream([[_text_chunk("Just an answer.")]], [])
    )
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    await generation_service._run(
        generation_service.Generation(chat_id=chat_id, message_id=message_id)
    )

    message = db.get(Message, message_id)
    db.refresh(message)
    assert message.steps_json == []


async def test_thinking_time_is_recorded_per_round(db, user_id, monkeypatch):
    """Each thinking block reports its own round. `reasoning_ms` is the reply's
    *first* burst and is written once, so it could never say how long round
    seven thought for -- every block but the first said "Thought" and nothing.
    """
    import asyncio as _asyncio

    from lembas.db.models import Message

    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)

    monkeypatch.setattr("lembas.services.search.run", _empty_search)

    async def slow_stream(_endpoint, payload):
        """Two rounds, each thinking for a measurable moment."""
        yield {"choices": [{"delta": {"reasoning_content": "mulling it over"}}]}
        await _asyncio.sleep(0.05)
        yield {"choices": [{"delta": {"reasoning_content": " some more"}}]}
        if len([m for m in payload["messages"] if m.get("role") == "tool"]) == 0:
            yield _tool_call_chunk("web_search", '{"query": "x"}')
        else:
            yield _text_chunk("Done.")

    monkeypatch.setattr(generation_service, "stream_chat", slow_stream)
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    assert generation.thinking_ms > 0, "the reply spent time thinking"
    assert generation.steps, "and closed a step"
    assert generation.steps[0]["thinking_ms"] > 0, "stamped on the mark"
    # Cumulative on the mark, so a block's own duration is the difference --
    # the same rule the three lengths beside it follow.
    assert generation.steps[0]["thinking_ms"] <= generation.thinking_ms

    db.expire_all()
    stored = db.get(Message, message_id)
    assert stored.reasoning_ms == generation.thinking_ms, (
        "the row keeps the summed figure, not the first burst"
    )


async def test_the_round_clock_settles_when_thinking_stops(db, user_id, monkeypatch):
    """`round_thinking_ms` is written by the producer, not computed from a start
    time by the follower -- a model that has stopped thinking and moved on to a
    tool should show a settled number rather than a clock that keeps running."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    chat_id, message_id = _chat_with_tools(db, user_id)
    monkeypatch.setattr("lembas.services.search.run", _empty_search)
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream([[_text_chunk("No thinking at all.")]], []),
    )
    monkeypatch.setattr("lembas.services.chat.generate_title", _never_called_title)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    assert generation.round_thinking_ms == 0
    assert generation.thinking_ms == 0
