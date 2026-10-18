"""Pausing a reply to ask the reader something.

Driven through the real generation loop with a scripted endpoint, because the
things worth pinning here are all about the loop: that Stop still works while
nothing is streaming, that a decision lands at the right index, and that the
card clears itself.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from lembas.db.models import ROLE_ASSISTANT, Chat, Connection, Message, Model
from lembas.services import generation as generation_service
from lembas.services import interaction, settings_store


def _chat_that_can_ask(db, user_id):
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()
    db.add(Message(chat_id=chat.id, role="user", content="Which one?", complete=True))
    db.commit()
    assistant = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(assistant)
    db.commit()
    return chat.id, assistant.id


def _ask_chunk(question: str, options: list[str] | None = None, *, index: int = 0, call_id="c1"):
    import json as _json

    arguments = {"question": question}
    if options is not None:
        arguments["options"] = options
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call_id,
                            "function": {
                                "name": "ask_user",
                                "arguments": _json.dumps(arguments),
                            },
                        }
                    ]
                }
            }
        ]
    }


def _text_chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


def _stub_stream(rounds, seen):
    async def stream_chat(_endpoint, payload):
        seen.append(payload)
        for chunk in rounds[min(len(seen) - 1, len(rounds) - 1)]:
            yield chunk

    return stream_chat


async def _until_paused(generation, *, timeout: float = 2.0):
    """Wait for the card to go up."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if generation.pending is not None:
            return generation.pending
        await asyncio.sleep(0.01)
    raise AssertionError("the reply never paused")


@pytest.fixture
def scripted(db, user_id, monkeypatch):
    """A reply that asks one question, then answers with whatever it was told."""
    chat_id, message_id = _chat_that_can_ask(db, user_id)
    payloads: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [[_ask_chunk("Tea or coffee?", ["Tea", "Coffee"])], [_text_chunk("Right you are.")]],
            payloads,
        ),
    )
    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    return generation, payloads, chat_id, message_id


# --- The tool is offered at all ----------------------------------------------
def test_ask_user_is_offered_to_an_ordinary_chat(db, user_id):
    """Not an agent feature. A model in a plain conversation should be able to
    stop and ask which of two things you meant."""
    from lembas.db.models import User
    from lembas.services import tools as tools_service

    chat_id, _message_id = _chat_that_can_ask(db, user_id)
    chat = db.get(Chat, chat_id)
    offered = tools_service.resolve_tools(db, chat, db.get(User, user_id))
    assert "ask_user" in offered.by_name


def test_ask_user_is_withheld_without_the_permission(db, user_id):
    from lembas.db.models import User
    from lembas.services import tools as tools_service

    user = db.get(User, user_id)
    # Administrators are given every permission, so the baseline only bites a
    # plain account.
    user.role = "user"
    settings_store.update(db, {"default_permissions": {"tools.ask": False}})
    db.commit()

    chat = db.get(Chat, _chat_that_can_ask(db, user_id)[0])
    assert "ask_user" not in tools_service.resolve_tools(db, chat, user).by_name


# --- The pause ---------------------------------------------------------------
async def test_the_reply_pauses_and_the_card_describes_the_question(scripted):
    generation, _payloads, _chat_id, _message_id = scripted
    task = asyncio.create_task(generation_service._run(generation))

    pending = await _until_paused(generation)
    assert pending.kind == interaction.KIND_QUESTION
    assert pending.items[0].title == "Tea or coffee?"
    assert [o.label for o in pending.items[0].options] == ["Tea", "Coffee"]
    assert "Waiting for your answer" in generation.status

    pending.resolve(interaction.ANSWER, answers={"q0": "Tea"})
    await task


async def test_the_answer_reaches_the_model_as_a_tool_result(scripted):
    generation, payloads, _chat_id, _message_id = scripted
    task = asyncio.create_task(generation_service._run(generation))

    pending = await _until_paused(generation)
    pending.resolve(interaction.ANSWER, answers={"q0": "Coffee, please"})
    await task

    turns = [m for m in payloads[1]["messages"] if m.get("role") == "tool"]
    assert len(turns) == 1
    assert "Coffee, please" in turns[0]["content"]
    assert turns[0]["tool_call_id"] == "c1"


async def test_the_card_is_cleared_once_it_is_answered(scripted):
    generation, _payloads, _chat_id, _message_id = scripted
    task = asyncio.create_task(generation_service._run(generation))

    pending = await _until_paused(generation)
    version = generation.version
    pending.resolve(interaction.ANSWER, answers={"q0": "Tea"})
    await task

    assert generation.pending is None
    assert generation.version > version, "clearing has to bump the version or no frame is sent"


async def test_the_transcript_keeps_what_was_asked_and_answered(scripted):
    generation, _payloads, _chat_id, _message_id = scripted
    task = asyncio.create_task(generation_service._run(generation))

    pending = await _until_paused(generation)
    pending.resolve(interaction.ANSWER, answers={"q0": "Tea"})
    await task

    event = generation.tool_events[0]
    assert event["kind"] == "ask"
    assert event["query"] == "Tea or coffee?"
    assert "Tea" in event["text"]


# --- Stop, while nothing is streaming ----------------------------------------
async def test_stop_ends_a_reply_that_is_waiting_for_an_answer(db, user_id, monkeypatch):
    """`cancel` is read only between streamed chunks, and there are no chunks
    while the card is up. Without the wakeup in request_stop the button does
    nothing at all here."""
    chat_id, message_id = _chat_that_can_ask(db, user_id)
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream([[_ask_chunk("Tea or coffee?")], [_text_chunk("unreachable")]], []),
    )

    generation = generation_service.ensure(chat_id, message_id)
    await _until_paused(generation)

    assert generation_service.request_stop(message_id) is True
    await asyncio.wait_for(asyncio.shield(generation_service._TASKS[message_id]), timeout=2)

    assert generation.stopped is True
    assert generation.pending is None


# --- Timeout ------------------------------------------------------------------
async def test_an_unanswered_question_expires_and_the_reply_finishes(db, user_id, monkeypatch):
    chat_id, message_id = _chat_that_can_ask(db, user_id)
    payloads: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream([[_ask_chunk("Tea or coffee?")], [_text_chunk("Never mind.")]], payloads),
    )
    # The clamp floor is 60s, so the timeout is forced directly rather than
    # through the settings.
    monkeypatch.setattr(
        generation_service.tools_service,
        "context_for",
        lambda *a, **k: _fast_context(*a, **k),
    )

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await asyncio.wait_for(generation_service._run(generation), timeout=5)

    turns = [m for m in payloads[1]["messages"] if m.get("role") == "tool"]
    assert "did not answer" in turns[0]["content"]
    assert generation.tool_events[0]["status"] == "error"


def _fast_context(db, user, chat=None, *, tools=None, **rest):
    """`**rest` so a new keyword on the real `context_for` does not fail this as
    an IndexError three assertions later."""
    from lembas.services import tools as tools_service

    context = tools_service.ToolContext(
        owner_id=user.id if user else "",
        tools=tools.by_name if tools is not None else None,
    )
    context.interaction_timeout = 0.2
    return context


# --- The primitive on its own -------------------------------------------------
def test_resolving_twice_only_counts_once():
    """Two tabs, or a double click. The second answer must not win."""

    async def go():
        pause = interaction.build("abc", [_item()], timeout=5)
        assert pause.resolve(interaction.ANSWER, answers={"q0": "first"}) is True
        assert pause.resolve(interaction.ANSWER, answers={"q0": "second"}) is False
        assert (await pause._future).answers == {"q0": "first"}

    asyncio.run(go())


def test_an_interruption_with_no_future_cannot_be_resolved():
    pause = interaction.Interruption(id="abc", items=(_item(),))
    assert pause.resolve(interaction.ANSWER) is False


def _opts(*labels: str) -> tuple[interaction.Option, ...]:
    """Options as the parser produces them: a label and an optional line under
    it. Bare strings are what a model sends and what `_options_in` normalises;
    `Item` itself only ever holds the normalised form."""
    return tuple(interaction.Option(label=label) for label in labels)


def _item(key: str = "q0", *, title: str = "Tea or coffee?", **kwargs) -> interaction.Item:
    return interaction.Item(
        index=0,
        key=key,
        kind=interaction.KIND_QUESTION,
        tool_name="ask_user",
        title=title,
        **kwargs,
    )


# --- Resolving one -------------------------------------------------------------
# `answer()` is exercised in-process rather than over the TestClient, because a
# future belongs to the loop that made it and TestClient runs the app on its
# own. In production both are the single uvicorn loop, which is the arrangement
# the in-process registry already requires.
async def test_answer_finds_the_pause_and_resolves_it(db, user_id):
    chat_id, message_id = _chat_that_can_ask(db, user_id)
    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    generation.pending = interaction.build("pause-1", [_item()], timeout=30)
    generation_service._RUNNING[message_id] = generation
    try:
        assert generation_service.answer(chat_id, "pause-1", answers={"q0": "Tea"}) is True
        assert (await generation.pending._future).answers == {"q0": "Tea"}
    finally:
        generation_service._RUNNING.pop(message_id, None)


async def test_answer_ignores_a_pause_in_another_chat(db, user_id):
    """The endpoint checks ownership of the chat, so the lookup is scoped to it
    -- an id on its own would not be an authorisation."""
    chat_id, message_id = _chat_that_can_ask(db, user_id)
    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    generation.pending = interaction.build("pause-1", [_item()], timeout=30)
    generation_service._RUNNING[message_id] = generation
    try:
        assert generation_service.answer("another-chat", "pause-1", answers={"q0": "x"}) is False
        assert not generation.pending._future.done()
    finally:
        generation_service._RUNNING.pop(message_id, None)


async def test_an_allow_choice_is_kept_as_a_verdict_not_as_typed_text():
    """"allow" is a decision, not something somebody wrote in the box."""
    pause = interaction.build("p", [_item()], timeout=5)
    pause.resolve(interaction.ALLOW)
    reply = await pause._future
    assert reply.permitted is True
    assert reply.outcome == interaction.ALLOW


def test_answering_something_that_has_gone_says_so(client, db, registered, user_id):
    chat_id, _message_id = _chat_that_can_ask(db, user_id)
    response = client.post(f"/api/chats/{chat_id}/interaction/nope", data={"choice": "Tea"})
    assert response.status_code == 204
    assert "no longer waiting" in response.headers["HX-Trigger"]


def test_another_account_cannot_answer_your_question(client, db, registered, user_id):
    """Without the ownership check, a guessed id would be answering -- and
    later, approving a command in -- somebody else's conversation."""
    from lembas.db.models import User

    chat_id, message_id = _chat_that_can_ask(db, user_id)
    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    # No future: the request must be refused before anything tries to resolve
    # it, so there is nothing here for it to reach.
    pause = interaction.Interruption(id="pause-2", items=(_item(),))
    generation.pending = pause
    generation_service._RUNNING[message_id] = generation
    try:
        client.post("/auth/logout")
        client.post(
            "/auth/register",
            data={"name": "Sam", "email": "sam@shire.test", "password": "correct horse battery"},
            follow_redirects=False,
        )
        intruder = db.scalar(select(User).where(User.email == "sam@shire.test"))
        intruder.role = "user"
        intruder.active = True
        db.commit()
        client.post(
            "/auth/login",
            data={"email": "sam@shire.test", "password": "correct horse battery"},
            follow_redirects=False,
        )

        response = client.post(
            f"/api/chats/{chat_id}/interaction/pause-2", data={"choice": "Tea"}
        )
        assert response.status_code == 404
        assert pause is generation.pending, "still waiting for the person it belongs to"
    finally:
        generation_service._RUNNING.pop(message_id, None)


# --- The card ------------------------------------------------------------------
def _render(pending) -> str:
    from lembas.api.chats import _ask_html

    return _ask_html("chat-1", pending)


def test_nothing_pending_renders_nothing():
    """The frame is sent unconditionally so the card can clear itself. An empty
    string is how it does that."""
    assert _render(None) == ""


def test_the_card_shows_the_question_and_its_options():
    pause = interaction.Interruption(
        id="p1",
        items=(
            _item(title="Tea or coffee?", options=_opts("Tea", "Coffee")),
        ),
    )
    html = _render(pause)
    assert "Tea or coffee?" in html
    assert 'value="Tea"' in html and 'value="Coffee"' in html
    # Stacked, never in a row: an option carries a description now.
    assert "interaction__option" in html
    assert 'hx-post="/api/chats/chat-1/interaction/p1"' in html
    assert "The model is asking you" in html, "attributed to the model, not to LLeMbas"


def test_an_approval_card_shows_what_the_model_said_it_was_doing():
    """Attributed, and kept apart from our own reason for stopping. An
    explanation a reader takes for the application's would be LLeMbas vouching
    for a command a model wrote."""
    pause = interaction.Interruption(
        id="p1",
        items=(
            interaction.Item(
                index=0,
                key="a0",
                kind=interaction.KIND_APPROVAL,
                tool_name="shell_run",
                title="Run a command on Box",
                detail="pytest -q",
                reason="Edit mode asks before anything that runs a command.",
                purpose="Checking the change did not break anything.",
            ),
        ),
    )
    html = _render(pause)
    assert "It says: Checking the change did not break anything." in html
    assert "pytest -q" in html
    assert "Edit mode asks" in html


def test_an_explanation_on_a_card_is_escaped():
    """It is model text, and the model may have been reading somebody else's
    file a moment ago."""
    pause = interaction.Interruption(
        id="p1",
        items=(
            interaction.Item(
                index=0,
                key="a0",
                kind=interaction.KIND_APPROVAL,
                tool_name="shell_run",
                title="Run a command on Box",
                detail="ls",
                purpose="<img src=x onerror=alert(1)>",
            ),
        ),
    )
    html = _render(pause)
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_the_card_never_offers_a_password_field():
    """A model talked into asking for a credential must not be handed a field
    that looks built for one."""
    pause = interaction.Interruption(
        id="p1",
        items=(
            _item(title="Confirm your password to continue:"),
        ),
    )
    html = _render(pause)
    assert 'type="password"' not in html
    assert 'type="text"' in html


def test_everything_on_the_card_is_escaped():
    """The question is model output, and the model may have been reading
    somebody else's file a moment ago."""
    pause = interaction.Interruption(
        id="p1",
        items=(
            _item(
                title="<img src=x onerror=alert(1)>",
                detail="rm -rf / <script>",
                options=_opts('"><script>bad</script>'),
            ),
        ),
    )
    html = _render(pause)
    assert "<img" not in html
    assert "<script>" not in html
    assert "&lt;img" in html


# --- Several questions, one card, one submit ---------------------------------
def _multi_chunk(questions: list[dict], *, call_id="c1", index=0):
    import json as _json

    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call_id,
                            "function": {
                                "name": "ask_user",
                                "arguments": _json.dumps({"questions": questions}),
                            },
                        }
                    ]
                }
            }
        ]
    }


async def test_several_questions_arrive_on_one_card(db, user_id, monkeypatch):
    chat_id, message_id = _chat_that_can_ask(db, user_id)
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [
                [
                    _multi_chunk(
                        [
                            {"question": "Which database?", "options": ["SQLite", "Postgres"]},
                            {"question": "Which port?"},
                            {"question": "Deploy now?", "options": ["Yes", "Later"]},
                        ]
                    )
                ],
                [_text_chunk("Understood.")],
            ],
            [],
        ),
    )

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    task = asyncio.create_task(generation_service._run(generation))
    pending = await _until_paused(generation)

    assert len(pending.items) == 3
    assert [i.title for i in pending.items] == ["Which database?", "Which port?", "Deploy now?"]
    assert [i.key for i in pending.items] == ["q0", "q1", "q2"]
    assert pending.items[1].options == (), "a question may have no options at all"
    assert "Waiting for your answers" in generation.status

    pending.resolve(interaction.ANSWER, answers={"q0": "Postgres", "q1": "5433", "q2": "Later"})
    await task


async def test_all_the_answers_come_back_in_one_tool_turn(db, user_id, monkeypatch):
    """One call, one turn -- however many questions it carried. The endpoint
    expects exactly one tool result per tool_call_id."""
    chat_id, message_id = _chat_that_can_ask(db, user_id)
    payloads: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [
                [_multi_chunk([{"question": "Which database?"}, {"question": "Which port?"}])],
                [_text_chunk("Understood.")],
            ],
            payloads,
        ),
    )

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    task = asyncio.create_task(generation_service._run(generation))
    pending = await _until_paused(generation)
    pending.resolve(interaction.ANSWER, answers={"q0": "Postgres", "q1": "5433"})
    await task

    turns = [m for m in payloads[1]["messages"] if m.get("role") == "tool"]
    assert len(turns) == 1
    content = turns[0]["content"]
    # Each answer is quoted beside its own question, so the model is not left
    # matching them up by position.
    assert "Which database?" in content and "Postgres" in content
    assert "Which port?" in content and "5433" in content
    assert content.index("Which database?") < content.index("Which port?")


async def test_a_question_left_blank_is_reported_as_skipped(db, user_id, monkeypatch):
    chat_id, message_id = _chat_that_can_ask(db, user_id)
    payloads: list[dict] = []
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream(
            [
                [_multi_chunk([{"question": "Which database?"}, {"question": "Which port?"}])],
                [_text_chunk("Understood.")],
            ],
            payloads,
        ),
    )

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    task = asyncio.create_task(generation_service._run(generation))
    pending = await _until_paused(generation)
    pending.resolve(interaction.ANSWER, answers={"q0": "Postgres", "q1": ""})
    await task

    content = [m for m in payloads[1]["messages"] if m.get("role") == "tool"][0]["content"]
    assert "Postgres" in content
    assert "left unanswered" in content and "Which port?" in content


async def test_two_ask_calls_in_one_round_share_a_card_but_answer_separately(
    db, user_id, monkeypatch
):
    """One card, because the reader should be interrupted once -- but two tool
    turns, because there were two calls."""
    import json as _json

    chat_id, message_id = _chat_that_can_ask(db, user_id)
    payloads: list[dict] = []
    both = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "a",
                            "function": {
                                "name": "ask_user",
                                "arguments": _json.dumps({"questions": [{"question": "First?"}]}),
                            },
                        },
                        {
                            "index": 1,
                            "id": "b",
                            "function": {
                                "name": "ask_user",
                                "arguments": _json.dumps({"questions": [{"question": "Second?"}]}),
                            },
                        },
                    ]
                }
            }
        ]
    }
    monkeypatch.setattr(
        generation_service,
        "stream_chat",
        _stub_stream([[both], [_text_chunk("Understood.")]], payloads),
    )

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    task = asyncio.create_task(generation_service._run(generation))
    pending = await _until_paused(generation)

    assert len(pending.items) == 2
    assert {i.index for i in pending.items} == {0, 1}, "one item per call"
    assert [i.key for i in pending.items] == ["q0", "q1"], "keys are unique across calls"

    pending.resolve(interaction.ANSWER, answers={"q0": "one", "q1": "two"})
    await task

    turns = [m for m in payloads[1]["messages"] if m.get("role") == "tool"]
    assert [t["tool_call_id"] for t in turns] == ["a", "b"]
    assert "one" in turns[0]["content"] and "two" in turns[1]["content"]


# --- Whatever shape the model actually emits ----------------------------------
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"questions": [{"question": "A"}, {"question": "B"}]}, ["A", "B"]),
        ({"question": "A"}, ["A"]),                       # the singular form
        ({"questions": "A"}, ["A"]),                      # a string where a list belonged
        ({"questions": ["A", "B"]}, ["A", "B"]),          # bare strings
        ({"questions": {"question": "A"}}, ["A"]),        # one object, not wrapped
        ({"questions": [{"nope": 1}, {"question": "B"}]}, ["B"]),  # junk is dropped
        ({}, []),
        ({"questions": []}, []),
    ],
)
def test_the_questions_are_read_however_they_were_spelled(args, expected):
    """A capable model sends the schema. A small one sends something close, and
    getting it wrong costs a round trip and shows a card saying nothing."""
    asked = generation_service._questions_in(args)
    assert [q["question"] for q in asked] == expected


def test_a_lone_bare_question_keeps_a_sibling_options_list():
    asked = generation_service._questions_in({"questions": ["Tea or coffee?"], "options": ["Tea"]})
    assert asked[0]["options"] == ["Tea"]


def test_too_many_questions_are_cut_off():
    args = {"questions": [{"question": f"Q{i}"} for i in range(20)]}
    assert len(generation_service._questions_in(args)) == interaction.MAX_QUESTIONS


def test_the_card_renders_every_question_with_its_own_fields():
    pause = interaction.Interruption(
        id="p1",
        items=(
            _item("q0", title="Which database?", options=_opts("SQLite", "Postgres")),
            _item("q1", title="Which port?"),
        ),
    )
    html = _render(pause)

    assert "Which database?" in html and "Which port?" in html
    # Radios rather than submit buttons: picking one must not send the form
    # while two other questions are still blank.
    assert 'name="choice.q0" value="SQLite"' in html
    assert 'name="text.q0"' in html and 'name="text.q1"' in html
    # Every question gets "Something else" and the box behind it, and a row to
    # ask something back, including one the model gave no options for -- those
    # rows are added here, never by the model, which is why the tool tells it
    # not to write an "other" of its own.
    assert html.count(f'value="{interaction.OTHER}"') == 4
    assert html.count("Something else") == 2
    assert html.count('name="ask.q0"') == 1 and html.count('name="ask.q1"') == 1
    assert html.count("Send answers") == 1, "one submit for the whole card"


def test_options_are_radios_when_exclusive_and_checkboxes_when_not():
    """The model says which, and the card has to show it: a radio group offered
    where a set was meant loses every answer but one, and checkboxes offered for
    alternatives invite an answer that contradicts itself."""
    one = _render(
        interaction.Interruption(
            id="p1", items=(_item("q0", options=_opts("Tea", "Coffee")),)
        )
    )
    many = _render(
        interaction.Interruption(
            id="p1", items=(_item("q0", options=_opts("Tea", "Coffee"), multiple=True),)
        )
    )

    assert 'type="radio"' in one and 'type="checkbox"' not in one
    assert 'type="checkbox"' in many and 'type="radio"' not in many
    # Including the "Something else" and "ask back" rows, which have to match
    # the rest or the card offers a set and then refuses to take one.
    assert one.count('type="radio"') == many.count('type="checkbox"') == 4


def test_an_option_can_carry_a_line_explaining_it():
    """"Rewrite it" and "Patch it" say nothing about which loses your
    uncommitted work."""
    pause = interaction.Interruption(
        id="p1",
        items=(
            _item(
                "q0",
                options=(
                    interaction.Option("Rewrite it", "Replaces the file whole."),
                    interaction.Option("Patch it"),
                ),
            ),
        ),
    )
    html = _render(pause)

    assert "Replaces the file whole." in html
    assert html.count("interaction__option-note") == 1, "and only where there is one"


def test_an_options_description_is_escaped_like_everything_else():
    pause = interaction.Interruption(
        id="p1",
        items=(_item("q0", options=(interaction.Option("ok", '<script>bad</script>'),)),),
    )

    assert "<script>bad" not in _render(pause)


def test_a_single_question_says_answer_rather_than_send_answers():
    html = _render(interaction.Interruption(id="p1", items=(_item(),)))
    assert "Send answers" not in html
    assert ">\n          Answer" in html or "Answer" in html


def test_the_endpoint_gathers_every_answer_at_once(client, db, registered, user_id):
    """The whole card in one POST -- what was typed beating what was picked."""
    chat_id, message_id = _chat_that_can_ask(db, user_id)
    seen: dict = {}

    def capture(chat, interaction_id, *, verdict="", answers=None, reason="", **_):
        seen["chat"] = chat
        seen["id"] = interaction_id
        seen["verdict"] = verdict
        seen["answers"] = answers
        return True

    from lembas.api import chats as chats_api

    original = chats_api.generation_service.answer
    chats_api.generation_service.answer = capture
    try:
        response = client.post(
            f"/api/chats/{chat_id}/interaction/p1",
            data={
                "choice.q0": "Postgres",
                "text.q0": "",
                # "Something else" ticked, and the box beside it filled in.
                "choice.q1": interaction.OTHER,
                "text.q1": "actually, later",
                # A box with no choice beside it: the approval card's corrected
                # command, which is the one place `text.` stands on its own.
                "text.q2": "5433",
            },
        )
        assert response.status_code == 204
    finally:
        chats_api.generation_service.answer = original

    assert seen["answers"] == {
        "q0": "Postgres",            # picked
        "q1": "actually, later",     # "Something else", and what was written
        "q2": "5433",                # written, nothing to pick
    }
    assert seen["verdict"] == ""
    assert message_id  # the chat was real


def test_the_endpoint_passes_a_verdict_through_untouched(client, db, registered, user_id):
    chat_id, _message_id = _chat_that_can_ask(db, user_id)
    seen: dict = {}

    from lembas.api import chats as chats_api

    original = chats_api.generation_service.answer
    chats_api.generation_service.answer = (
        lambda c, i, *, verdict="", answers=None, reason="", **_: (
            seen.update(verdict=verdict, answers=answers) or True
        )
    )
    try:
        client.post(f"/api/chats/{chat_id}/interaction/p1", data={"verdict": "allow"})
    finally:
        chats_api.generation_service.answer = original

    assert seen["verdict"] == "allow"
    assert seen["answers"] == {}


# --- Options as a model actually sends them -----------------------------------
def test_options_are_read_as_objects_or_as_bare_strings():
    """The schema asks for objects with a label. A small model sends a list of
    strings -- which is what the schema asked for until recently, and what most
    examples of this pattern look like -- so that is read rather than refused."""
    objects = generation_service._options_in(
        {"options": [{"label": "Rewrite", "description": "Replaces it whole."}, {"label": "Patch"}]}
    )
    strings = generation_service._options_in({"options": ["Rewrite", "Patch"]})

    assert [(o.label, o.description) for o in objects] == [
        ("Rewrite", "Replaces it whole."),
        ("Patch", ""),
    ]
    assert [(o.label, o.description) for o in strings] == [("Rewrite", ""), ("Patch", "")]


def test_an_option_that_is_neither_is_dropped_rather_than_stringified():
    """`{'a': 1}` rendered as a choice is worse than one choice fewer."""
    options = generation_service._options_in({"options": ["ok", 7, None, {"nope": 1}, ""]})

    assert [o.label for o in options] == ["ok"]


def test_too_many_options_are_cut_off():
    options = generation_service._options_in({"options": [f"o{i}" for i in range(20)]})

    assert len(options) == interaction.MAX_OPTIONS


def test_the_parser_never_adds_something_else_itself():
    """It belongs to the template, which adds it to every question and owns the
    box behind it. In the data it would be indistinguishable from one the model
    wrote -- and the model is told not to write one precisely because this row
    already exists."""
    options = generation_service._options_in({"options": ["Tea", "Coffee"]})

    assert all(o.label != "Something else" for o in options)
    assert all(o.label != interaction.OTHER for o in options)


async def test_whether_several_may_be_chosen_survives_into_the_card(db, user_id, monkeypatch):
    """Through the real loop, because the flag has to reach `Item` -- reading it
    off the arguments and never passing it on would look identical here."""
    import json as _json

    chat_id, message_id = _chat_that_can_ask(db, user_id)
    arguments = _json.dumps(
        {
            "questions": [
                {"question": "Which of these?", "options": ["a", "b"], "multiple": True},
                {"question": "And which one?", "options": ["c", "d"]},
            ]
        }
    )
    chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "ask_user", "arguments": arguments},
                        }
                    ]
                }
            }
        ]
    }
    monkeypatch.setattr(
        generation_service, "stream_chat", _stub_stream([[chunk], [_text_chunk("ok")]], [])
    )

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    task = asyncio.create_task(generation_service._run(generation))
    pending = await _until_paused(generation)

    assert pending.items[0].multiple is True
    # Exclusive is the default: a radio group offered where checkboxes were
    # meant costs one clarifying round, while checkboxes offered for
    # alternatives invite an answer that contradicts itself.
    assert pending.items[1].multiple is False

    pending.resolve(interaction.ANSWER, answers={"q0": "a", "q1": "c"})
    await task


def test_several_ticked_answers_all_come_back(client, db, registered, user_id):
    """A `multiple` question posts the same field name once per ticked box. The
    old reader kept the first and lost the rest, which is an answer that says
    something the reader did not."""
    chat_id, _message_id = _chat_that_can_ask(db, user_id)
    seen: dict = {}

    from lembas.api import chats as chats_api

    original = chats_api.generation_service.answer
    chats_api.generation_service.answer = (
        lambda chat, iid, *, verdict="", answers=None, reason="", **_: seen.update(answers=answers)
        or True
    )
    try:
        client.post(
            f"/api/chats/{chat_id}/interaction/p1",
            data={"choice.q0": ["Tea", "Coffee"], "text.q0": ""},
        )
    finally:
        chats_api.generation_service.answer = original

    assert seen["answers"] == {"q0": "Tea, Coffee"}


def test_something_else_ticked_with_an_empty_box_says_nothing(client, db, registered, user_id):
    """Rather than telling the model the answer is "__other__", which is exactly
    the shape of thing it would try to act on."""
    chat_id, _message_id = _chat_that_can_ask(db, user_id)
    seen: dict = {}

    from lembas.api import chats as chats_api

    original = chats_api.generation_service.answer
    chats_api.generation_service.answer = (
        lambda chat, iid, *, verdict="", answers=None, reason="", **_: seen.update(answers=answers)
        or True
    )
    try:
        client.post(
            f"/api/chats/{chat_id}/interaction/p1",
            data={"choice.q0": interaction.OTHER, "text.q0": "   "},
        )
    finally:
        chats_api.generation_service.answer = original

    assert seen["answers"] == {}


def test_a_ticked_option_and_something_else_come_back_together(client, db, registered, user_id):
    chat_id, _message_id = _chat_that_can_ask(db, user_id)
    seen: dict = {}

    from lembas.api import chats as chats_api

    original = chats_api.generation_service.answer
    chats_api.generation_service.answer = (
        lambda chat, iid, *, verdict="", answers=None, reason="", **_: seen.update(answers=answers)
        or True
    )
    try:
        client.post(
            f"/api/chats/{chat_id}/interaction/p1",
            data={"choice.q0": ["Tea", interaction.OTHER], "text.q0": "or juice"},
        )
    finally:
        chats_api.generation_service.answer = original

    assert seen["answers"] == {"q0": "Tea, or juice"}


# --- Refusing, and saying why --------------------------------------------------
# A bare "Don't" tells the model it was refused and nothing else, so it asks what
# you would rather -- a whole round spent on something you knew when you pressed
# the button. The box is how that round is skipped, and these are the two halves
# that have to hold: the reason reaches the model, and it changes what the model
# is told to do next. The model is on a linked device, which is told
# the reason (`device_link`, `_meta.lembas.feedback`) and words the refusal itself.
def _approval(key: str = "a0", **kwargs) -> interaction.Item:
    return interaction.Item(
        index=0,
        key=key,
        kind=interaction.KIND_APPROVAL,
        tool_name="shell_run",
        title="Run a command",
        detail="rm -rf build",
        **kwargs,
    )


async def test_a_refusal_can_carry_a_reason():
    pause = interaction.build("p", [_approval()], timeout=5)
    pause.resolve(interaction.DENY, reason="  not in that directory, use /srv  ")

    reply = await pause._future

    assert reply.permitted is False
    assert reply.reason == "not in that directory, use /srv", "trimmed, as typed"


async def test_a_plain_refusal_carries_none():
    """Which is what keeps the two wordings apart downstream."""
    pause = interaction.build("p", [_approval()], timeout=5)
    pause.resolve(interaction.DENY)

    assert (await pause._future).reason == ""


async def test_a_reason_is_bounded():
    """It lands in a tool result inside a request that has a window to fit in."""
    pause = interaction.build("p", [_approval()], timeout=5)
    pause.resolve(interaction.DENY, reason="x" * (interaction.MAX_REASON_CHARS + 500))

    assert len((await pause._future).reason) == interaction.MAX_REASON_CHARS


def test_the_card_offers_the_box_and_names_the_field(client, db, registered, user_id):
    pause = interaction.Interruption(id="p1", items=(_approval(),))

    html = _render(pause)

    assert "Give reason" in html
    assert 'name="reason"' in html
    # Both halves in the DOM with one hidden, for the reason the edit box gives:
    # a field created by a click submits nothing when the click handler fails.
    assert 'x-show="explaining"' in html
    # Disabled while hidden, so a card refused plainly cannot post an empty
    # `reason` that would have to be told apart from one somebody cleared.
    assert ':disabled="!explaining"' in html


def test_the_question_card_offers_no_deny_box():
    """It is not a refusal, and it has its own free-text row already."""
    html = _render(interaction.Interruption(id="p1", items=(_item(),)))

    assert "Give reason" not in html
    assert 'name="reason"' not in html


def test_the_endpoint_passes_the_reason_on(client, db, registered, user_id):
    chat_id, _message_id = _chat_that_can_ask(db, user_id)
    seen: dict = {}

    from lembas.api import chats as chats_api

    original = chats_api.generation_service.answer
    chats_api.generation_service.answer = (
        lambda c, i, *, verdict="", answers=None, reason="", **_: seen.update(
            verdict=verdict, reason=reason
        )
        or True
    )
    try:
        client.post(
            f"/api/chats/{chat_id}/interaction/p1",
            data={"verdict": "deny", "reason": "not on production"},
        )
    finally:
        chats_api.generation_service.answer = original

    assert seen == {"verdict": "deny", "reason": "not on production"}


def test_a_reason_never_travels_with_a_permission(client, db, registered, user_id):
    """Typed into the box, then Allow pressed instead. Reading it there would put
    the reader's "no, because…" onto a call that went ahead."""
    chat_id, _message_id = _chat_that_can_ask(db, user_id)
    seen: dict = {}

    from lembas.api import chats as chats_api

    original = chats_api.generation_service.answer
    chats_api.generation_service.answer = (
        lambda c, i, *, verdict="", answers=None, reason="", **_: seen.update(
            verdict=verdict, reason=reason
        )
        or True
    )
    try:
        client.post(
            f"/api/chats/{chat_id}/interaction/p1",
            data={"verdict": "allow", "reason": "I changed my mind"},
        )
    finally:
        chats_api.generation_service.answer = original

    assert seen == {"verdict": "allow", "reason": ""}
