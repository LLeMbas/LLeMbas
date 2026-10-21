"""Chat, folders, and the streaming reply path against a mocked endpoint."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from lembas.db.models import Chat, Connection, Folder, Message, Model
from lembas.services import chat as chat_service
from lembas.services.crypto import encrypt
from lembas.services.llm.openai_client import Endpoint, LLMError, delta_text, list_models
from lembas.services.sse import event


# --- SSE framing -------------------------------------------------------------
def test_sse_event_framing():
    assert event("token", "hello") == "event: token\ndata: hello\n\n"


def test_sse_splits_newlines_across_data_lines():
    """A payload with a newline must become several data: lines. Sending a raw
    newline truncates the event, which is what breaks the first code block a
    model emits."""
    assert event("token", "a\nb") == "event: token\ndata: a\ndata: b\n\n"


def test_sse_round_trips_through_the_browser_rejoin_rule():
    payload = "line one\nline two\n\nline four"
    framed = event("token", payload)
    body = framed.split("\n", 1)[1]
    rejoined = "\n".join(
        line.removeprefix("data: ") for line in body.split("\n") if line.startswith("data:")
    )
    assert rejoined == payload


# --- Delta parsing -----------------------------------------------------------
def test_delta_text_reads_the_normal_shape():
    assert delta_text({"choices": [{"delta": {"content": "hi"}}]}) == "hi"


def test_delta_text_handles_typed_content_parts():
    chunk = {"choices": [{"delta": {"content": [{"type": "text", "text": "hi"}]}}]}
    assert delta_text(chunk) == "hi"


@pytest.mark.parametrize(
    "chunk", [{}, {"choices": []}, {"choices": [{}]}, {"choices": [{"delta": {}}]}]
)
def test_delta_text_tolerates_junk(chunk):
    """Providers vary; an unexpected chunk shape must not kill a reply."""
    assert delta_text(chunk) == ""


# --- Endpoint URL handling ---------------------------------------------------
@pytest.mark.parametrize(
    ("base", "expected"),
    [
        ("http://host:1234", "http://host:1234/v1/models"),
        ("http://host:1234/v1", "http://host:1234/v1/models"),
        ("http://host:1234/", "http://host:1234/v1/models"),
    ],
)
def test_base_url_with_or_without_v1(base, expected):
    """Users should not have to guess which form is expected."""
    assert Endpoint(base_url=base.rstrip("/"), api_key="", extra_headers={}).url(
        "models"
    ) == expected


def test_no_authorization_header_without_a_key():
    """Local runners often reject an empty bearer token outright."""
    assert "Authorization" not in Endpoint("http://h", "", {}).headers()
    assert Endpoint("http://h", "k", {}).headers()["Authorization"] == "Bearer k"


# --- Model discovery ---------------------------------------------------------
async def test_list_models_accepts_the_bare_list_shape():
    """The spec says {"data": [...]}, but some servers return a bare list."""

    def handler(_request):
        return httpx.Response(200, json=[{"id": "a"}, {"id": "b"}])

    original = httpx.AsyncClient

    class Patched(original):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    httpx.AsyncClient = Patched
    try:
        assert [m["id"] for m in await list_models(Endpoint("http://h", "", {}))] == ["a", "b"]
    finally:
        httpx.AsyncClient = original


async def test_list_models_reports_a_rejected_key_readably():
    def handler(_request):
        return httpx.Response(401, json={"error": {"message": "Incorrect API key."}})

    original = httpx.AsyncClient

    class Patched(original):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    httpx.AsyncClient = Patched
    try:
        with pytest.raises(LLMError) as caught:
            await list_models(Endpoint("http://h", "bad", {}))
        assert "rejected" in caught.value.message
        assert "Incorrect API key." in caught.value.message
    finally:
        httpx.AsyncClient = original


# --- Titles ------------------------------------------------------------------
def test_fallback_title_keeps_a_short_message_intact():
    assert chat_service.fallback_title("What is lembas?") == "What is lembas?"


def test_fallback_title_trims_on_a_word_boundary():
    title = chat_service.fallback_title("word " * 60)
    assert len(title) <= chat_service.MAX_TITLE_LENGTH + 1
    assert title.endswith("…")


def test_fallback_title_of_nothing():
    assert chat_service.fallback_title("   ") == "New chat"


async def test_the_title_prompt_carries_the_exchange(mock_http):
    """The wording is a fragment an administrator can edit, so what reaches the
    endpoint has to be the substituted text, not the template."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"][0]["content"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "A short name"}}]})

    mock_http(handler)
    endpoint = Endpoint("http://x.test", "", {})
    title = await chat_service.generate_title(
        endpoint,
        "m",
        "What is lembas?",
        "Waybread.",
        template="Name this: {{question}} / {{answer}} / {{nonsense}}",
    )

    assert title == "A short name"
    assert seen == ["Name this: What is lembas? / Waybread. / {{nonsense}}"]


async def test_a_title_from_a_model_that_thinks_first(mock_http):
    """The bug: a reasoning model puts `<think>` in the very field the title is
    read from, so every chat on one was named "<think>Okay, the user wants a
    short title for" -- or, once the guard caught that as too long, fell back to
    the first prompt and looked as though titling had never run at all."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "<think>Six words, an emoji</think>\n"
                            "🌳 Mallorn trees explained"
                        }
                    }
                ]
            },
        )

    mock_http(handler)
    title = await chat_service.generate_title(
        Endpoint("http://x.test", "", {}),
        "m",
        "What is a mallorn?",
        "A golden tree.",
        template="Name this: {{question}}",
    )
    assert title == "🌳 Mallorn trees explained"


async def test_the_title_call_leaves_room_to_think(mock_http):
    """24 tokens is ample for six words and nowhere near enough for a model that
    reasons first: the budget went on thinking and the content came back empty.
    Too small is not a shorter title, it is no title."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "A name"}}]})

    mock_http(handler)
    await chat_service.generate_title(
        Endpoint("http://x.test", "", {}), "m", "q", "a", template="Name this: {{question}}"
    )
    assert seen[0]["max_tokens"] == chat_service.TITLE_MAX_TOKENS
    assert seen[0]["max_tokens"] >= 256


async def test_the_title_call_sends_no_reasoning_effort(mock_http):
    """Tempting, and wrong. Those two fields appear only when somebody has opted
    in, so a provider strict about unknown parameters sees the request it always
    did -- and a 400 here is caught and turned into a fallback title, which is
    titling silently switching itself off."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "A name"}}]})

    mock_http(handler)
    await chat_service.generate_title(
        Endpoint("http://x.test", "", {}), "m", "q", "a", template="Name this: {{question}}"
    )
    assert "reasoning_effort" not in seen[0]
    assert "chat_template_kwargs" not in seen[0]


async def test_a_title_that_is_only_thinking_falls_back(mock_http):
    """Nothing but reasoning means nothing to name it with. The first prompt is
    a better title than an empty one."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "<think>still deciding"}}]}
        )

    mock_http(handler)
    title = await chat_service.generate_title(
        Endpoint("http://x.test", "", {}),
        "m",
        "What is a mallorn?",
        "A golden tree.",
        template="Name this: {{question}}",
    )
    assert title == "What is a mallorn?"


def test_the_shipped_title_prompt_asks_for_an_emoji():
    """It makes a sidebar of twenty chats scannable, and it is asked for rather
    than assumed -- a model that ignores it gives a title without one."""
    from lembas.services import prompts

    fragment = next(f for f in prompts.BUILTIN if f.key == "task.title")
    assert "emoji" in fragment.default
    assert "{{question}}" in fragment.default
    assert "{{answer}}" in fragment.default


async def test_an_empty_title_prompt_asks_no_model_at_all(mock_http):
    """Clearing the fragment is how auto-titling is turned off. It must not
    cost a request that is then thrown away."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("the endpoint was contacted")

    mock_http(handler)
    endpoint = Endpoint("http://x.test", "", {})
    title = await chat_service.generate_title(
        endpoint, "m", "What is lembas?", "Waybread.", template="   "
    )
    assert title == "What is lembas?"


# --- Chats and folders (through the API) -------------------------------------
def _add_connection(db) -> Connection:
    # Port 1 refuses connections, which is what the error-path test relies on.
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()
    return connection


# --- Starting a chat ---------------------------------------------------------
def test_starting_a_chat_creates_it_and_redirects(client: TestClient, db, registered):
    _add_connection(db)
    response = client.post("/api/chats/start", data={"content": "Hello there"})
    assert response.status_code == 204
    assert response.headers["HX-Redirect"].startswith("/chat/")

    chat = db.scalar(select(Chat))
    assert chat.model_id == "test-model"
    messages = db.scalars(select(Message).order_by(Message.created_at)).all()
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "Hello there"


def test_starting_with_nothing_creates_no_chat(client: TestClient, db, registered):
    """The whole point of lazy creation: an abandoned composer leaves nothing."""
    _add_connection(db)
    assert client.post("/api/chats/start", data={"content": "  "}).status_code == 204
    assert db.scalar(select(Chat)) is None


def test_visiting_the_chat_page_creates_nothing(client: TestClient, db, registered):
    _add_connection(db)
    assert client.get("/chat").status_code == 200
    assert db.scalar(select(Chat)) is None


def test_starting_a_chat_honours_the_requested_model(client: TestClient, db, registered):
    """The pinned-model shortcuts pass ?model=, which arrives here."""
    connection = _add_connection(db)
    db.add(Model(connection_id=connection.id, model_id="other-model", position=5))
    db.commit()

    client.post("/api/chats/start", data={"content": "hi", "model_id": "other-model"})
    assert db.scalar(select(Chat)).model_id == "other-model"


def test_starting_a_chat_ignores_a_model_you_cannot_reach(client: TestClient, db, registered):
    connection = _add_connection(db)
    db.add(Model(connection_id=connection.id, model_id="secret", position=5, public=False))
    db.commit()

    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    client.post("/api/chats/start", data={"content": "hi", "model_id": "secret"})
    assert db.scalar(select(Chat)).model_id == "test-model"


def test_posting_a_message_stores_both_turns(
    client: TestClient, db, registered, make_chat, monkeypatch
):
    """What the route itself does, with no reply running behind it.

    `ensure` is stubbed out because the generation is a background task: it
    would race this test to the database, and against a connection that
    refuses instantly it sometimes wins -- writing the error and marking the
    row complete before the assertions below can read it. What the route
    guarantees is the pair of rows and the streaming shell; whether a reply has
    got anywhere yet is a different test's business.
    """
    from lembas.services import generation as generation_service

    monkeypatch.setattr(generation_service, "ensure", lambda *a, **k: None)

    _add_connection(db)
    chat_id = make_chat()

    response = client.post(f"/api/chats/{chat_id}/messages", data={"content": "Hello there"})
    assert response.status_code == 200

    messages = db.scalars(select(Message).order_by(Message.created_at)).all()
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "Hello there"
    # The assistant row is created empty and incomplete; that is what carries
    # the sse-connect the browser uses to start the stream.
    assert messages[1].content == ""
    assert messages[1].complete is False
    assert "sse-connect" in response.text


def test_empty_message_is_ignored(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    assert client.post(f"/api/chats/{chat_id}/messages", data={"content": "   "}).status_code == 204
    assert db.scalar(select(Message)) is None


def test_a_chat_belonging_to_someone_else_is_not_found(
    client: TestClient, db, registered, make_chat
):
    _add_connection(db)
    chat_id = make_chat()

    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    # 404 and not 403: whether that id exists is not this endpoint's to reveal.
    assert client.get(f"/chat/{chat_id}").status_code == 404


def test_renaming_a_chat_stops_it_being_auto_titled(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    client.patch(f"/api/chats/{chat_id}", data={"title": "My own title"})

    chat = db.get(Chat, chat_id)
    db.refresh(chat)
    assert chat.title == "My own title"
    assert chat.title_generated is True


def test_deleting_a_chat_removes_its_messages(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    client.post(f"/api/chats/{chat_id}/messages", data={"content": "Hello"})

    client.delete(f"/api/chats/{chat_id}")
    assert db.scalar(select(Chat)) is None
    assert db.scalar(select(Message)) is None


def test_deleting_a_folder_keeps_the_chats_inside_it(client: TestClient, db, registered, make_chat):
    """Losing a conversation to a mis-clicked folder delete is unforgivable."""
    _add_connection(db)
    client.post("/api/folders", data={"name": "Quests"})
    folder = db.scalar(select(Folder))

    chat_id = make_chat()
    client.patch(f"/api/chats/{chat_id}", data={"folder_id": folder.id})

    client.delete(f"/api/folders/{folder.id}")

    chat = db.get(Chat, chat_id)
    db.refresh(chat)
    assert chat is not None
    assert chat.folder_id is None


def test_an_archived_chat_inside_a_folder_is_not_listed(
    client: TestClient, db, registered, make_chat
):
    """Regression: the unfiled list has always filtered archived chats, but the
    folder branch went through the ORM relationship and filtered nothing, so an
    archived chat kept showing as long as it was filed."""
    _add_connection(db)
    client.post("/api/folders", data={"name": "Quests"})
    folder = db.scalar(select(Folder))

    chat_id = make_chat()
    client.patch(f"/api/chats/{chat_id}", data={"folder_id": folder.id, "title": "Mount Doom"})
    chat = db.get(Chat, chat_id)
    chat.archived = True
    db.commit()

    page = client.get("/chat").text

    # The original guarantee, and now a narrower assertion than "nowhere on the
    # page": archiving puts a chat in the Archived group, so it IS on the page
    # -- being able to find it again is the difference between archiving it and
    # deleting it. What must not happen is it still showing inside its folder,
    # which is the bug this test was written for.
    before_archived = page.split('nav-group--archived', 1)[0]
    assert "Mount Doom" not in before_archived
    assert "Mount Doom" in page
    # And the folder must say so, rather than claiming to hold something.
    assert "Empty" in page


def test_a_folder_cannot_be_moved_inside_itself(client: TestClient, db, registered):
    client.post("/api/folders", data={"name": "Outer"})
    folder = db.scalar(select(Folder))
    response = client.patch(f"/api/folders/{folder.id}", data={"parent_id": folder.id})
    assert response.status_code == 400


# --- Request building --------------------------------------------------------
def test_request_forwards_only_known_sampling_parameters(db, user_id):
    """A stray key in params_json must not become a 400 from the provider that
    looks like a LLeMbas bug."""
    connection = _add_connection(db)
    chat = Chat(
        user_id=user_id,
        model_id="test-model",
        connection_id=connection.id,
        params_json={"temperature": 0.4, "nonsense": "drop me"},
    )
    db.add(chat)
    db.commit()

    payload = chat_service.build_request(db, chat)
    assert payload["temperature"] == 0.4
    assert "nonsense" not in payload


def test_history_skips_failed_and_empty_turns(db, user_id):
    """Sending an empty assistant message upsets several providers."""
    connection = _add_connection(db)
    chat = Chat(user_id=user_id, model_id="test-model", connection_id=connection.id)
    db.add(chat)
    db.commit()

    db.add_all(
        [
            Message(chat_id=chat.id, role="user", content="one"),
            Message(chat_id=chat.id, role="assistant", content="", error="boom"),
            Message(chat_id=chat.id, role="user", content="two"),
        ]
    )
    db.commit()

    messages = chat_service.build_request(db, chat)["messages"]
    contents = [m["content"] for m in messages if m["role"] != "system"]
    assert contents == ["one", "two"]


def test_system_prompt_leads_the_message_list(db, user_id):
    connection = _add_connection(db)
    chat = Chat(
        user_id=user_id,
        model_id="test-model",
        connection_id=connection.id,
        system_prompt="You are terse.",
    )
    db.add(chat)
    db.commit()

    messages = chat_service.build_request(db, chat)["messages"]
    assert messages[0]["role"] == "system"
    # The harness precedes it inside the same message; the authored prompt is
    # last, where it is closest to the conversation.
    assert messages[0]["content"].endswith("You are terse.")


def test_streaming_reports_an_unreachable_endpoint_in_the_thread(
    client: TestClient, db, registered
, make_chat):
    """A failed turn must never be an unexplained blank bubble."""
    _add_connection(db)  # points at 127.0.0.1:1, which refuses connections
    chat_id = make_chat()
    client.post(f"/api/chats/{chat_id}/messages", data={"content": "Hello"})
    message = db.scalar(select(Message).where(Message.role == "assistant"))

    response = client.get(f"/api/chats/{chat_id}/messages/{message.id}/stream")
    assert response.status_code == 200
    assert "Could not reach" in response.text
    assert "alert--error" in response.text

    db.refresh(message)
    assert message.complete is True
    assert message.error


# --- Stopping a stream -------------------------------------------------------
def test_stopping_asks_the_generation_to_stop(client: TestClient, db, registered, make_chat):
    """A half-written answer the reader chose to cut short is still worth
    having; discarding it would be a surprise."""
    from lembas.services import generation as generation_service

    _add_connection(db)
    chat_id = make_chat()
    client.post(f"/api/chats/{chat_id}/messages", data={"content": "hi"})
    message = db.scalar(select(Message).where(Message.role == "assistant"))

    assert client.post(
        f"/api/chats/{chat_id}/messages/{message.id}/stop"
    ).status_code == 204

    running = generation_service.get(message.id)
    # The endpoint points at 127.0.0.1:1, so the task may already have failed
    # and finished; either way the request must be accepted, not error.
    assert running is None or running.cancel or running.done


def test_stopping_someone_elses_message_is_refused(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    client.post(f"/api/chats/{chat_id}/messages", data={"content": "hi"})
    message = db.scalar(select(Message).where(Message.role == "assistant"))

    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    assert client.post(
        f"/api/chats/{chat_id}/messages/{message.id}/stop"
    ).status_code == 404


def test_the_streaming_bubble_carries_the_sse_connection(
    client: TestClient, db, registered, make_chat
):
    """Stop lives on the composer's send button now, and the JS finds the
    running message through this attribute."""
    _add_connection(db)
    chat_id = make_chat()
    response = client.post(f"/api/chats/{chat_id}/messages", data={"content": "hi"})
    assert "sse-connect" in response.text
    assert f"/api/chats/{chat_id}/messages/" in response.text


def test_the_streaming_bubble_renders_markdown_not_raw_tokens(
    client: TestClient, db, registered, make_chat
):
    """The body receives re-rendered Markdown, so formatting appears as the
    model writes rather than snapping in at the end."""
    _add_connection(db)
    chat_id = make_chat()
    response = client.post(f"/api/chats/{chat_id}/messages", data={"content": "hi"})
    assert 'sse-swap="render"' in response.text
    assert 'hx-swap="innerHTML"' in response.text


def test_reasoning_starts_closed(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    response = client.post(f"/api/chats/{chat_id}/messages", data={"content": "hi"})
    block = response.text[response.text.index("reasoning--live"):]
    assert not block[: block.index(">")].strip().endswith("open")


# --- Rewinding ---------------------------------------------------------------
def _exchange(client: TestClient, db, chat_id: str, text: str) -> Message:
    client.post(f"/api/chats/{chat_id}/messages", data={"content": text})
    assistant = db.scalars(
        select(Message).where(Message.role == "assistant").order_by(Message.created_at)
    ).all()[-1]
    assistant.content = f"reply to {text}"
    assistant.complete = True
    db.commit()
    return assistant


def test_editing_rewinds_and_discards_later_messages(
    client: TestClient, db, registered, make_chat
):
    _add_connection(db)
    chat_id = make_chat()
    _exchange(client, db, chat_id, "first")
    _exchange(client, db, chat_id, "second")
    assert db.scalar(select(func.count()).select_from(Message)) == 4

    first_user = db.scalars(
        select(Message).where(Message.role == "user").order_by(Message.created_at)
    ).first()
    # The ids as strings, taken now: these rows are about to be deleted, and an
    # ORM instance read afterwards raises ObjectDeletedError.
    discarded_ids = set(
        db.scalars(
            select(Message.id).where(
                Message.chat_id == chat_id, Message.role == "assistant"
            )
        )
    )
    client.post(
        f"/api/chats/{chat_id}/messages/{first_user.id}/edit",
        data={"content": "first, revised"},
    )

    # The edit happened in the request's session; this one still holds the old
    # instance in its identity map.
    db.expire_all()
    remaining = db.scalars(select(Message).order_by(Message.created_at)).all()
    assert [m.role for m in remaining] == ["user", "assistant"]
    assert remaining[0].content == "first, revised"
    # A *fresh* assistant row, which is what restarts the stream: a different row
    # from the one that was discarded, with nothing written into it yet.
    #
    # ⚠ Deliberately not `complete is False`. A real generation is started here
    # against the fixture's unreachable endpoint, and it does finish -- it errors
    # with "could not reach" and `_persist` marks the row complete. Whether that
    # has happened by the time this line runs is a race, and asserting on it made
    # this test pass only while that failure stayed slower than the rest of the
    # request. It began flaking the moment unrelated work shifted the timing.
    assert remaining[1].id not in discarded_ids
    assert remaining[1].content == ""


def test_a_rewind_takes_a_message_written_in_the_same_microsecond(
    client: TestClient, db, registered, make_chat
):
    """`_messages_after` compared timestamps with a bare `>`, so a row sharing the
    edited turn's microsecond was never "after" it and survived the rewind -- an
    orphan below the message being edited, in the transcript and in every later
    request. `_send` writes a user turn and its assistant placeholder back to
    back, so that pair is precisely what ties.

    Not fixed with `thread_tail`'s `(created_at, id)` tiebreak: `Message.id` is a
    random UUID, so that would settle a tie by coin toss. A tie is read as
    "later" instead, which is the safe direction for an operation whose purpose
    is to discard what follows.
    """
    _add_connection(db)
    chat_id = make_chat()
    _exchange(client, db, chat_id, "first")
    _exchange(client, db, chat_id, "second")

    rows = db.scalars(select(Message).order_by(Message.created_at)).all()
    edited = rows[0]
    # Every later row now shares the edited turn's timestamp exactly.
    for row in rows[1:]:
        row.created_at = edited.created_at
    db.commit()

    client.post(
        f"/api/chats/{chat_id}/messages/{edited.id}/edit", data={"content": "first, revised"}
    )

    db.expire_all()
    remaining = db.scalars(select(Message).order_by(Message.created_at, Message.id)).all()
    assert [m.role for m in remaining] == ["user", "assistant"], (
        "a message sharing the edited turn's microsecond survived the rewind"
    )
    assert remaining[0].content == "first, revised"


def test_the_edit_form_says_how_much_will_be_lost(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    _exchange(client, db, chat_id, "first")
    _exchange(client, db, chat_id, "second")

    first_user = db.scalars(
        select(Message).where(Message.role == "user").order_by(Message.created_at)
    ).first()
    page = client.get(f"/api/chats/{chat_id}/messages/{first_user.id}/edit").text
    assert "3 messages after this one will be deleted" in page


def test_only_your_own_turns_can_be_edited(client: TestClient, db, registered, make_chat):
    """Rewriting what the model said would be inventing history."""
    _add_connection(db)
    chat_id = make_chat()
    assistant = _exchange(client, db, chat_id, "hello")
    assert client.get(
        f"/api/chats/{chat_id}/messages/{assistant.id}/edit"
    ).status_code == 404


def test_an_edit_cannot_empty_a_message(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    _exchange(client, db, chat_id, "hello")
    user_message = db.scalar(select(Message).where(Message.role == "user"))

    assert client.post(
        f"/api/chats/{chat_id}/messages/{user_message.id}/edit", data={"content": "   "}
    ).status_code == 400


def test_cancelling_an_edit_restores_the_bubble(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    _exchange(client, db, chat_id, "unchanged")
    user_message = db.scalar(select(Message).where(Message.role == "user"))

    page = client.get(f"/api/chats/{chat_id}/messages/{user_message.id}/cancel-edit").text
    assert "unchanged" in page
    assert "edit-form" not in page


# --- A turn nobody typed ------------------------------------------------------
def _machine_turn(db, chat_id: str, content: str = "A background job you started has finished"):
    """What `jobs.wake` writes: a user-role turn the application produced."""
    from lembas.db.models import Chat
    from lembas.services import chat as chat_service

    chat = db.get(Chat, chat_id)
    return chat_service.create_message(db, chat, "user", content, machine=True)


def test_a_machine_turn_is_not_shown_as_the_readers_own(
    client: TestClient, db, registered, make_chat
):
    """A background job finishing is a user turn because the request needs it to
    be, not because the reader said it. Rendering it under their name with their
    initial beside it is the application putting words in their mouth."""
    _add_connection(db)
    chat_id = make_chat()
    _machine_turn(db, chat_id)

    page = client.get(f"/chat/{chat_id}").text

    assert "msg--machine" in page
    assert "Background job" in page
    assert "msg__initial" not in page, "no initial in the gutter for a turn nobody typed"


def test_a_machine_turn_offers_no_pencil(client: TestClient, db, registered, make_chat):
    """Editing rewinds and re-sends under the reader's own authority, and what a
    machine reported is not theirs to rewrite."""
    _add_connection(db)
    chat_id = make_chat()
    event = _machine_turn(db, chat_id)

    page = client.get(f"/chat/{chat_id}").text

    assert f"/messages/{event.id}/edit" not in page


def test_a_machine_turn_cannot_be_edited(client: TestClient, db, registered, make_chat):
    """The hidden button is a courtesy; the route is the rule."""
    _add_connection(db)
    chat_id = make_chat()
    event = _machine_turn(db, chat_id)

    assert client.get(f"/api/chats/{chat_id}/messages/{event.id}/edit").status_code == 404
    assert (
        client.post(
            f"/api/chats/{chat_id}/messages/{event.id}/edit",
            data={"content": "something I would rather it had said"},
        ).status_code
        == 404
    )


def test_a_machine_turn_keeps_its_output(client: TestClient, db, registered, make_chat):
    """The fenced log is most of why somebody reads one of these at all."""
    _add_connection(db)
    chat_id = make_chat()
    _machine_turn(
        db,
        chat_id,
        "[job abc] `pytest -q`\nIt finished successfully.\n\n```\n1529 passed\n```",
    )

    page = client.get(f"/chat/{chat_id}").text

    assert "1529 passed" in page


def test_a_machine_turn_still_reaches_the_model_as_a_user_turn(db, registered, make_chat):
    """The wire role is load-bearing: `_inject` sends a queued turn verbatim and
    every template requires the first non-system message to be `user`. `machine`
    changes the bubble and nothing else."""
    from lembas.db.models import Chat
    from lembas.services import chat as chat_service

    chat_id = make_chat()
    _machine_turn(db, chat_id, "a job finished")

    sent = chat_service.build_messages(db, db.get(Chat, chat_id))

    assert [(m["role"], m["content"]) for m in sent] == [("user", "a job finished")]


def test_an_ordinary_turn_is_not_a_machine_event(db, registered, make_chat):
    """Every row written before the column reads the same way, because
    `sync_schema` adds a NOT NULL boolean with a literal default of 0."""
    from lembas.db.models import Chat
    from lembas.services import chat as chat_service

    chat_id = make_chat()
    chat = db.get(Chat, chat_id)

    assert chat_service.create_message(db, chat, "user", "hello").machine is False


# --- Background generation ---------------------------------------------------
def test_sending_launches_the_generation_immediately(
    client: TestClient, db, registered, make_chat
):
    """The reply is produced by a task, not by the browser watching it. That is
    what lets you navigate away without cutting it off."""
    from lembas.services import generation as generation_service

    _add_connection(db)
    chat_id = make_chat()
    client.post(f"/api/chats/{chat_id}/messages", data={"content": "hi"})
    message = db.scalar(select(Message).where(Message.role == "assistant"))

    assert generation_service.get(message.id) is not None


def test_starting_a_chat_launches_the_generation(client: TestClient, db, registered):
    from lembas.services import generation as generation_service

    _add_connection(db)
    client.post("/api/chats/start", data={"content": "hi"})
    message = db.scalar(select(Message).where(Message.role == "assistant"))
    assert generation_service.get(message.id) is not None


def test_asking_twice_does_not_start_a_second_generation(
    client: TestClient, db, registered, make_chat
):
    """A page load finding an unfinished reply must attach, not restart."""
    from lembas.services import generation as generation_service

    _add_connection(db)
    chat_id = make_chat()
    client.post(f"/api/chats/{chat_id}/messages", data={"content": "hi"})
    message = db.scalar(select(Message).where(Message.role == "assistant"))

    first = generation_service.get(message.id)
    assert generation_service.ensure(chat_id, message.id) is first


# --- Unread -------------------------------------------------------------------
def test_the_unread_poll_reports_dots(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat.unread = True
    db.commit()

    response = client.get("/api/chats/unread")
    assert f'id="unread-{chat_id}"' in response.text
    # This chat's own span, not the whole body: the response also carries the
    # section dots, and one of those being hidden is right rather than wrong.
    chat_dot = next(
        span for span in response.text.split("<span") if f'id="unread-{chat_id}"' in span
    )
    assert "hidden" not in chat_dot
    assert "lembas:unread" in response.headers.get("HX-Trigger", "")


def test_an_arrival_is_announced_once(client: TestClient, db, registered, make_chat):
    """Otherwise the same reply would toast every ten seconds forever."""
    _add_connection(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat.unread = True
    db.commit()

    assert "HX-Trigger" in client.get("/api/chats/unread").headers
    assert "HX-Trigger" not in client.get("/api/chats/unread").headers


def test_opening_a_chat_marks_it_read(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat.unread = True
    db.commit()

    client.get(f"/chat/{chat_id}")
    db.expire_all()
    assert db.get(Chat, chat_id).unread is False


def test_a_read_chat_reports_a_hidden_dot(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    response = client.get("/api/chats/unread")
    assert f'id="unread-{chat_id}"' in response.text
    assert "hidden" in response.text


def test_the_unread_poll_only_sees_your_own_chats(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    mine = make_chat()

    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    assert mine not in client.get("/api/chats/unread").text


def test_the_sidebar_shows_an_unread_dot(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat.unread = True
    db.commit()

    # Rendered on another page, so the dot is visible while looking elsewhere.
    page = client.get("/chat").text
    assert f'id="unread-{chat_id}" class="unread-dot"' in page
    assert 'hx-get="/api/chats/unread"' in page


# --- The composer's one row --------------------------------------------------
def test_the_send_button_is_the_last_thing_in_the_toolbar(client: TestClient, db, registered):
    """What the layout depends on. `.composer__actions` is pushed right by
    `margin-left: auto` and refuses to shrink, and both only work while it is
    the last child -- when the row wrapped instead, it was the last child that
    dropped to a second line, so an agent chat pushed Send and the microphone
    off the row entirely."""
    _add_connection(db)
    html = client.get("/chat").text
    toolbar = html.split('class="composer__toolbar"', 1)[1]

    assert 'class="composer__actions"' in toolbar
    assert toolbar.index("composer__actions") > toolbar.index("composer__tools")
    assert "data-composer-action" in toolbar


def test_the_agent_controls_shrink_rather_than_pushing_send_off_the_row(
    client: TestClient, db, registered
):
    """They live in `.composer__context`, which is the only flex child allowed
    to shrink and scroll. Anything moved out of it stops shrinking and starts
    pushing Send onto a second line again -- which is what this whole row was
    rearranged to stop."""
    from lembas.db.models import TOKEN_DEVICE, User
    from lembas.security import api_tokens
    from lembas.services import device_link, settings_store

    _add_connection(db)
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    # A linked LLeMbas CLI: the agent fork offers nothing without one.
    user = db.get(User, _user_id(db))
    token, _ = api_tokens.create(
        db, user, name="CLI on box", scopes=["models", "link"], kind=TOKEN_DEVICE,
        device_host="box",
    )

    async def nothing(*_a, **_k):
        return None

    device_link.LINKS[token.id] = device_link.Link(
        token_id=token.id, user_id=user.id, name="CLI on box",
        send_text=nothing, close=nothing,
        hello={"device": {"host": "box"}, "limits": {"roots": ["/work"]}},
    )

    # The agent side of the sidebar (the composer has no Chat/Agent switch).
    try:
        html = client.get("/chat?kind=agent").text
    finally:
        device_link.reset()
    assert 'data-agent-picker' in html, "the agent fork offered nothing; this test went blind"

    # The three that appear on an agent chat sit between the start of
    # `.composer__context` and the start of `.composer__actions` -- which is
    # what puts them inside the one child that is allowed to give, and keeps
    # the actions last.
    opens = html.index('class="composer__context"')
    actions = html.index('class="composer__actions"')
    for control in ("ssh_profile_id", "data-dir-browse", 'name="agent_mode"'):
        assert opens < html.index(control) < actions, control


def _chat_css() -> str:
    from pathlib import Path

    import lembas

    return (Path(lembas.__file__).parent / "web/static/css/chat.css").read_text()


def test_the_composer_toolbar_can_never_wrap(client: TestClient):
    """This is what the old blanket ban on `@media` in this file was protecting.

    The toolbar used to wrap, and `.composer__actions` is last in the DOM with
    `margin-left: auto` -- so the moment an agent chat added a connection, a
    directory and a mode to the row, Send and the microphone were what dropped
    to a second line. The fix was to say which child gives, not to rearrange the
    row at a threshold, and the test that pinned it refused every media query in
    the file so that nobody would "fix" a regression with a breakpoint instead.

    The ban outlived its usefulness: a phone needs bigger targets and different
    spacing, and refusing all width- and pointer-awareness here made the file
    unable to say so. What it was *actually* protecting is asserted directly
    now, which is both narrower and stronger -- the old test would have passed a
    version of this file that wrapped the toolbar without a media query.
    """
    css = _chat_css()
    toolbar = css.split(".composer__toolbar {", 1)[1].split("}", 1)[0]
    assert "flex-wrap: nowrap" in toolbar

    actions = css.split(".composer__actions {", 1)[1].split("}", 1)[0]
    assert "flex: none" in actions
    assert "flex-wrap" not in actions

    # The one child allowed to give, and the reason the rest never have to.
    context = css.split(".composer__context {", 1)[1].split("}", 1)[0]
    assert "min-width: 0" in context
    assert "overflow-x: auto" in context

    # And the effort: a chip of its own in the row, fixed at `flex: none`,
    # pushes Send off a phone. So the effort lives in the settings button, an
    # icon button like attach with no text on it, and no setting can change
    # the row's width. Nothing styles a text-bearing effort box.
    assert ".composer__effort {" not in css
    assert ".composer__settings-toggle {" not in css
    assert ".composer__effort-short" not in css


def _media_blocks(css: str) -> list[str]:
    """Each `@media` block's own contents, by balancing braces.

    Splitting on "@media" and taking what follows gives everything to the end of
    the file, so a test written that way asserts about the whole stylesheet
    while appearing to be about one block -- and fails on a rule three hundred
    lines below the query.
    """
    blocks = []
    for start in (i for i in range(len(css)) if css.startswith("@media", i)):
        opened = css.index("{", start)
        depth, cursor = 0, opened
        while cursor < len(css):
            if css[cursor] == "{":
                depth += 1
            elif css[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        blocks.append(css[opened + 1 : cursor])
    return blocks


def test_no_breakpoint_may_undo_the_toolbar_rule(client: TestClient):
    """A media query in this file is allowed; one that lets the toolbar wrap or
    lets the actions shrink is the original bug with a threshold in front of
    it."""
    for body in _media_blocks(_chat_css()):
        assert "flex-wrap: wrap" not in body
        assert ".composer__actions" not in body or "flex: none" in body


def test_width_awareness_in_this_file_is_deliberate(client: TestClient):
    """Every media query here carries a comment immediately above it.

    The replacement for "none allowed": a breakpoint in this file has to say why
    it exists, because the failure this file is shaped around is somebody
    reaching for one instead of fixing the sizing.
    """
    css = _chat_css()
    for index, line in enumerate(css.splitlines()):
        if line.strip().startswith("@media"):
            above = "\n".join(css.splitlines()[max(0, index - 12):index])
            assert "*" in above, f"undocumented @media at line {index + 1}"


def _user_id(db):
    from sqlalchemy import select

    from lembas.db.models import User

    return db.scalar(select(User.id))


def test_both_paths_render_a_step_through_the_same_partial():
    """A live step and a stored one are the same markup, so a reply cannot
    rearrange itself the moment the stream ends -- which is what it used to do
    the other way round, three zones either side.

    They arrive by different routes and that is deliberate: the finished bubble
    loops `_steps.html`, while the stream renders one `_step.html` at a time and
    swaps the accumulated prefix in. Both bottom out in the same file, which is
    the property worth pinning.
    """
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).parent
    steps_partial = (root / "web/templates/chat/_steps.html").read_text()
    follower = (root / "api/chats.py").read_text()

    assert 'include "chat/_step.html"' in steps_partial
    assert 'get_template("chat/_step.html")' in follower


def test_no_template_still_asks_for_a_tools_frame():
    """`tools` is gone: what it carried lives inside `steps`, which moves once a
    round instead of twelve times a second. A leftover `sse-swap="tools"` would
    be a container nothing ever fills."""
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).parent / "web/templates"
    for path in root.rglob("*.html"):
        assert 'sse-swap="tools"' not in path.read_text(), path


def test_no_sse_swap_element_contains_another():
    """The regression that made an agent reply render nothing at all.

    `#steps-{id}` is itself an `sse-swap` target, so its whole `innerHTML` is
    replaced every time a round closes. An `sse-swap` element nested inside is
    therefore torn out and rebuilt at every round boundary -- with the frames
    aimed at it arriving in the same pass, at something that is no longer the
    element the listener was bound to. An ordinary chat never noticed, because
    it closes no steps and the swap never happens; an agent chat lost its answer
    from the first tool call onwards.

    Asserted structurally rather than by rendering, so it holds for whichever
    branch of the template a given reply takes.
    """
    import re
    from html.parser import HTMLParser
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).parent / "web/templates"

    class Nesting(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.stack = []
            self.found = []

        def handle_starttag(self, tag, attrs):
            got = dict(attrs)
            swaps = "sse-swap" in got
            if swaps and self.depth:
                self.found.append(got.get("sse-swap"))
            if tag not in ("br", "img", "input", "hr", "meta", "link", "use"):
                self.stack.append(swaps)
                self.depth += 1 if swaps else 0

        def handle_endtag(self, tag):
            if self.stack and self.stack.pop():
                self.depth -= 1

    for path in root.rglob("*.html"):
        source = path.read_text()
        stripped = re.sub(r"\{%.*?%\}|\{#.*?#\}", "", source, flags=re.S)
        stripped = re.sub(r"\{\{.*?\}\}", "x", stripped, flags=re.S)
        parser = Nesting()
        parser.feed(stripped)
        assert not parser.found, f"{path.name} nests sse-swap: {parser.found}"


def test_the_finished_bubble_carries_no_live_containers():
    """A finished reply with an `sse-swap` in it is a container waiting for a
    stream that is over."""
    from types import SimpleNamespace

    from lembas.web.templating import templates

    html = templates.get_template("chat/_steps.html").render(
        {"steps": [], "live": False, "message": SimpleNamespace(id="m1", reasoning_ms=0)}
    )

    assert "sse-swap" not in html


def test_nothing_inside_the_composer_form_fetches_without_saying_where_it_lands():
    """The bug that blanked every agent chat, and the reason it was invisible.

    htmx INHERITS `hx-target` from ancestors. The composer's form carries
    `hx-target="#thread"` so that sending a message appends a bubble to the
    transcript -- so anything inside that form which fetches, and does not name
    its own target, aims at `#thread` too. The background-jobs chip did exactly
    that with `hx-swap="outerHTML"`: on load it replaced the entire transcript
    with itself, and the reply appeared and then vanished, the reader's own
    prompt with it.

    Asserted as the property rather than by rendering, because the markup was
    never wrong -- `hx-swap="outerHTML"` on an element with no target reads as
    "swap yourself", and it means that only when nothing above it disagrees.
    Same family as the trigger bound where the event does not go.
    """
    import re
    from pathlib import Path

    import lembas

    source = (
        Path(lembas.__file__).parent / "web/templates/chat/_composer.html"
    ).read_text()

    opened = source.index('<form class="composer__form"')
    closed = source.index("</form>", opened)
    inside = source[opened:closed]
    # The form's own attributes are the ones being inherited; skip its tag.
    inside = inside[inside.index(">") + 1 :]

    for tag in re.finditer(r"<[a-z]+\s[^>]*>", inside):
        markup = tag.group()
        if not re.search(r'hx-(get|post|put|patch|delete)=', markup):
            continue
        assert "hx-target=" in markup or 'hx-swap="none"' in markup, (
            "this fetches from inside a form targeting #thread and does not say "
            f"where its answer goes:\n{markup}"
        )


def test_the_composer_form_answers_only_its_own_request():
    """The other half of the bug that blanked agent chats, and the one that had
    been quietly costing typed messages for far longer.

    htmx events bubble. This form contains six things that fetch -- two scope
    switches, "ask me about these again", the agent mode select, the effort
    select and the jobs chip -- and every one of their `htmx:afterRequest`
    events reaches the form's own `hx-on::after-request`. Without the guard,
    changing the mode or the effort called `this.reset()` on a composer somebody
    was typing in, and dragged the view to the bottom. The chip polls, so it did
    it every five seconds; that is the only reason it was ever noticed.
    """
    import re
    from pathlib import Path

    import lembas

    source = (
        Path(lembas.__file__).parent / "web/templates/chat/_composer.html"
    ).read_text()

    handler = re.search(r'hx-on::after-request="([^"]*)"', source)
    assert handler, "the composer no longer clears itself after sending"
    assert "event.target === this" in handler.group(1), (
        "a descendant's request will run this handler without the guard"
    )


def test_the_think_frame_lands_beside_the_reasoning_body_not_around_it():
    """The live block has two swap targets inside one static `<details>` -- the
    label and the body. Two siblings is fine; one inside the other is what
    blanked every agent chat, because the outer swap tears out the inner
    element while the frames aimed at it are still arriving."""
    from pathlib import Path

    import lembas

    source = (
        Path(lembas.__file__).parent / "web/templates/chat/_message.html"
    ).read_text()

    label = source.index('sse-swap="think"')
    body = source.index('sse-swap="reasoning"')
    between = source[min(label, body) : max(label, body)]
    # Neither element may open a tag that the other closes: siblings, not nested.
    assert "</span>" in between or "</div>" in between
    assert between.count("<div") <= 1


# --- Who a finished reply says it came from ----------------------------------
def test_a_finished_bubble_names_the_model_that_wrote_it(db, client, registered, make_chat):
    """The `done` frame and the tail route render the bubble from scratch, and
    both looked the models up as *nobody* -- which `models_visible_to` answers
    with an empty list, not with everything. So a reply was attributed correctly
    for as long as it was streaming and lost its avatar and its author line at
    the instant it finished, then corrected itself on the next page load.

    Asserted on the rendered HTML rather than on the argument: passing `owner`
    is what the old code looked like it was doing, and an assertion on the call
    would have been green throughout.
    """
    from lembas.api import chats as chats_api
    from lembas.db.models import Chat, Connection, Message, Model, User

    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="mithril-7b", display_name="Mithril 7B"))
    db.commit()

    chat_id = make_chat(model_id="mithril-7b")
    chat = db.get(Chat, chat_id)
    owner = db.get(User, chat.user_id)
    message = Message(
        chat_id=chat.id, role="assistant", content="Spoken.",
        complete=True, model_id="mithril-7b",
    )
    db.add(message)
    db.commit()

    html = chats_api._render_bubble(db, chat, owner, message)
    assert "Mithril 7B" in html


def test_the_reply_limit_covers_every_way_of_starting_one(db):
    """It was enforced in `_send` alone, so an account at its ceiling reached it
    by sending into an existing chat and walked past it by pressing New chat --
    and by editing, by sending a queued message, and by regenerating.

    Reading the source is the honest test here: driving four routes to the point
    of refusal needs four live generations, which is a fixture that would tell
    you more about the fixture than about the guard.
    """
    import inspect

    from lembas.api import chats as chats_api

    # The cores, since the app's JSON door (`api/v2`) shares them with the
    # page routes: a guard in a route alone would be one door in two.
    for core in (
        chats_api.start_new_chat,
        chats_api.rewind_to,
        chats_api.send_queued_now,
        chats_api.regenerate_reply,
        chats_api.write_turn,
    ):
        assert "_refuse_extra_reply" in inspect.getsource(core), core.__name__


def test_a_new_chat_is_not_written_before_the_limit_is_checked(db):
    """A refusal that has already created the row leaves an empty chat in the
    sidebar as the visible result of being told no."""
    import inspect

    from lembas.api import chats as chats_api

    body = inspect.getsource(chats_api.start_new_chat)
    assert body.index("_refuse_extra_reply") < body.index("= _new_chat(")
