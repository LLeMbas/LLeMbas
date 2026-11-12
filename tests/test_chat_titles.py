"""How a chat gets its name, and how it gets a different one."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import KIND_AGENT, ROLE_ASSISTANT, Chat, Connection, Message, Model, User
from lembas.services import chat as chat_service
from lembas.services import generation as generation_service
from lembas.services.crypto import encrypt


def _add_connection(db) -> Connection:
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()
    return connection


def _chat_awaiting_a_reply(db, user_id, *, kind="chat", question="Rebuild the search index"):
    connection = db.scalar(select(Connection))
    chat = Chat(
        user_id=user_id, model_id="test-model", connection_id=connection.id, kind=kind
    )
    db.add(chat)
    db.commit()
    db.add(Message(chat_id=chat.id, role="user", content=question, complete=True))
    db.commit()
    assistant = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(assistant)
    db.commit()
    return chat.id, assistant.id


def _stub_stream(text: str):
    async def stream_chat(_endpoint, _payload, **_):
        yield {"choices": [{"delta": {"content": text}}]}

    return stream_chat


# --- Where a title comes from ---------------------------------------------------
async def test_an_agent_chat_is_named_from_its_first_prompt(db, user_id, monkeypatch):
    """Somebody starting one states an objective, not a topic, so the opening
    words are already a title. No second completion is spent on it."""
    _add_connection(db)
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id, kind=KIND_AGENT)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Done."))

    asked = []

    async def _never(*args, **kwargs):
        asked.append(args)
        return "From the model"

    monkeypatch.setattr(chat_service, "generate_title", _never)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    db.expire_all()
    assert db.get(Chat, chat_id).title == "Rebuild the search index"
    assert asked == [], "an agent chat must not spend a completion on its name"


async def test_an_ordinary_chat_still_asks_a_model(db, user_id, monkeypatch):
    """The opening of an ordinary chat is a question, and its answer is what
    makes a title worth asking for."""
    _add_connection(db)
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Like so."))

    async def _titled(*args, **kwargs):
        return "Search indexing, explained"

    monkeypatch.setattr(chat_service, "generate_title", _titled)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)
    # The reply ends first; the name follows it.
    assert await generation.title_task == "Search indexing, explained"

    db.expire_all()
    assert db.get(Chat, chat_id).title == "Search indexing, explained"
    assert db.get(Chat, chat_id).title_generated is True


# --- The reply does not wait for its chat's name ---------------------------------
async def test_a_slow_title_does_not_hold_the_reply(db, user_id, monkeypatch):
    """The model's title used to be awaited before the reply was marked done:
    a finished answer stayed "Writing" for as long as a second completion took,
    queued behind the first on a one-slot server. Now the reply is saved and
    done with the name made from the prompt, and the model's comes after."""
    import asyncio

    _add_connection(db)
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Like so."))
    release = asyncio.Event()

    async def _slow(*args, **kwargs):
        await release.wait()
        return "Search indexing, explained"

    monkeypatch.setattr(chat_service, "generate_title", _slow)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    # A limit, so a regression fails here instead of hanging the suite.
    await asyncio.wait_for(generation_service._run(generation), 5)

    assert generation.done
    assert generation_service.title_pending(chat_id)
    db.expire_all()
    chat = db.get(Chat, chat_id)
    assert db.get(Message, message_id).complete is True
    assert chat.title == "Rebuild the search index"
    assert chat.title_generated is False, "still to be named by the model"

    release.set()
    assert await generation.title_task == "Search indexing, explained"
    await asyncio.sleep(0)
    assert not generation_service.title_pending(chat_id)
    db.expire_all()
    assert db.get(Chat, chat_id).title == "Search indexing, explained"


async def test_a_title_that_takes_too_long_is_given_up(db, user_id, monkeypatch):
    """A title is never worth holding a model for: past the limit the name from
    the prompt stays, and the chat counts as named."""
    import asyncio

    _add_connection(db)
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Like so."))
    monkeypatch.setattr(generation_service, "TITLE_TIMEOUT", 0.05)

    async def _never(*args, **kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(chat_service, "generate_title", _never)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)
    assert await asyncio.wait_for(generation.title_task, 5) == "Rebuild the search index"

    db.expire_all()
    chat = db.get(Chat, chat_id)
    assert chat.title == "Rebuild the search index"
    assert chat.title_generated is True


async def test_a_rename_while_the_model_is_naming_wins(db, user_id, monkeypatch):
    import asyncio

    _add_connection(db)
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Like so."))
    release = asyncio.Event()

    async def _slow(*args, **kwargs):
        await release.wait()
        return "From the model"

    monkeypatch.setattr(chat_service, "generate_title", _slow)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)
    chat = db.get(Chat, chat_id)
    chat.title = "Orthanc"
    chat.title_generated = True
    db.commit()

    release.set()
    assert await generation.title_task == "Orthanc"
    db.expire_all()
    assert db.get(Chat, chat_id).title == "Orthanc"


async def test_the_page_is_told_to_ask_for_the_name_and_stops_when_it_has_it(
    client: TestClient, db, registered, monkeypatch
):
    """The page's stream ends with `done`, so the name cannot come on it: the
    frame carries an element that asks `/title` once a second, and the answer
    keeps it asking only while the model is still at it."""
    import asyncio

    from lembas.api import chats as chats_api

    _add_connection(db)
    user = db.scalar(select(User))
    chat_id, message_id = _chat_awaiting_a_reply(db, user.id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Like so."))
    release = asyncio.Event()

    async def _slow(*args, **kwargs):
        await release.wait()
        return "Search indexing, explained"

    monkeypatch.setattr(chat_service, "generate_title", _slow)

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    frame, _ = await asyncio.to_thread(chats_api._final_frame, chat_id, message_id, generation)
    assert "Rebuild the search index" in frame
    assert f'hx-get="/api/chats/{chat_id}/title"' in frame

    release.set()
    await generation.title_task
    await asyncio.sleep(0)
    frames = chats_api._title_frames(
        db.get(Chat, chat_id), pending=generation_service.title_pending(chat_id)
    )
    db.expire_all()
    frames = chats_api._title_frames(
        db.get(Chat, chat_id), pending=generation_service.title_pending(chat_id)
    )
    assert "Search indexing, explained" in frames
    assert 'id="chat-title-wait"' in frames and "hx-get" not in frames


def test_the_title_route_answers_with_the_name(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()

    response = client.get(f"/api/chats/{chat_id}/title")
    assert response.status_code == 200
    assert 'id="chat-title"' in response.text
    # Nothing is naming it: the page is not told to ask again.
    assert 'id="chat-title-wait"' in response.text and "hx-get" not in response.text
    assert client.get("/api/chats/" + "0" * 32 + "/title").status_code == 404


async def test_an_agent_title_is_trimmed_on_a_word_boundary(db, user_id, monkeypatch):
    _add_connection(db)
    long = (
        "Rebuild the search index and then reindex every document in the "
        "knowledge base before the deploy"
    )
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id, kind=KIND_AGENT, question=long)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Done."))

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)

    db.expire_all()
    title = db.get(Chat, chat_id).title
    assert title == chat_service.fallback_title(long)
    assert len(title) <= chat_service.MAX_TITLE_LENGTH + 1
    assert title.endswith("…")


async def test_an_agent_chat_is_named_only_once(db, user_id, monkeypatch):
    _add_connection(db)
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id, kind=KIND_AGENT)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Done."))

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(generation)
    db.expire_all()
    chat = db.get(Chat, chat_id)
    assert chat.title_generated is True


# --- Renaming ---------------------------------------------------------------------
def test_a_rename_answers_with_both_places_the_title_appears(
    client: TestClient, db, registered, make_chat
):
    """One response, two out-of-band spans. The heading alone left the sidebar
    row showing the old name until the next reload, which reads as a rename
    that half worked."""
    _add_connection(db)
    chat_id = make_chat()

    response = client.patch(f"/api/chats/{chat_id}", data={"title": "Orthanc"})
    assert response.status_code == 200
    assert 'id="chat-title"' in response.text
    assert f'id="chat-link-label-{chat_id}"' in response.text
    assert "Orthanc" in response.text
    assert db.get(Chat, chat_id).title == "Orthanc"


def test_a_rename_stops_the_chat_being_auto_titled(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    client.patch(f"/api/chats/{chat_id}", data={"title": "Orthanc"})
    assert db.get(Chat, chat_id).title_generated is True


def test_a_title_is_escaped_on_the_way_back(client: TestClient, db, registered, make_chat):
    """It is text somebody typed, and it lands in two spans on a page that can
    open a shell."""
    _add_connection(db)
    chat_id = make_chat()

    response = client.patch(
        f"/api/chats/{chat_id}", data={"title": "<img src=x onerror=alert(1)>"}
    )
    assert "<img src=x" not in response.text
    assert "&lt;img" in response.text


def test_a_blank_rename_changes_nothing_and_says_nothing(
    client: TestClient, db, registered, make_chat
):
    """A chat with no name is one nobody can find in the sidebar."""
    _add_connection(db)
    chat_id = make_chat()
    db.get(Chat, chat_id).title = "Orthanc"
    db.commit()

    response = client.patch(f"/api/chats/{chat_id}", data={"title": "   "})
    assert response.status_code == 204
    assert db.get(Chat, chat_id).title == "Orthanc"


def test_a_patch_that_is_not_a_rename_still_answers_204(
    client: TestClient, db, registered, make_chat
):
    """The out-of-band pair is only right when the title actually moved. Sending
    it for every PATCH would overwrite the heading from an unrelated save."""
    _add_connection(db)
    chat_id = make_chat()
    assert client.patch(f"/api/chats/{chat_id}", data={"agent_mode": "auto"}).status_code == 204


def test_renaming_someone_elses_chat_is_a_404(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    other = User(name="Sam", email="sam@shire.test", password_hash="x")
    db.add(other)
    db.commit()
    chat = Chat(user_id=other.id, model_id="test-model", title="Theirs")
    db.add(chat)
    db.commit()

    assert client.patch(f"/api/chats/{chat.id}", data={"title": "Mine"}).status_code == 404
    assert db.get(Chat, chat.id).title == "Theirs"


# --- Where the button is ------------------------------------------------------------
def test_the_rename_button_is_on_the_heading_and_the_row(
    client: TestClient, db, registered, make_chat
):
    _add_connection(db)
    chat_id = make_chat()
    page = client.get(f"/chat/{chat_id}").text

    assert page.count(f'hx-patch="/api/chats/{chat_id}"') >= 2
    assert 'data-prompt-field="title"' in page


def test_the_rename_button_posts_at_a_route_that_serves_patch(
    client: TestClient, db, registered, make_chat
):
    """A control wired to a method its route does not serve fails silently."""
    _add_connection(db)
    chat_id = make_chat()
    assert client.post(f"/api/chats/{chat_id}", data={"title": "x"}).status_code == 405
    assert client.patch(f"/api/chats/{chat_id}", data={"title": "x"}).status_code == 200


def test_the_new_chat_screen_offers_no_rename(client: TestClient, db, registered):
    """There is no row to rename until the first message is sent."""
    _add_connection(db)
    assert 'data-prompt-field="title"' not in client.get("/chat").text


# --- The end of a reply, in a thread -------------------------------------------------
async def test_a_reply_regenerated_while_it_is_being_written_down_is_not_kept(
    db, user_id, monkeypatch
):
    """`_persist` runs in a thread. It asked "has another generation taken
    this row?" once, at its start; a regeneration between that and the commit
    let the abandoned reply be committed over the fresh one's blank row --
    complete, its usage counted, marked unread."""
    import asyncio
    import threading

    from lembas.db.models import Usage
    from lembas.services import usage as usage_service

    _add_connection(db)
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id)
    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    generation.content.append("The abandoned answer.")
    generation.completion_tokens = 7
    generation_service._RUNNING[message_id] = generation

    reached, release = threading.Event(), threading.Event()
    real = usage_service.record

    def record(*args, **kwargs):  # the last thing before the commit
        out = real(*args, **kwargs)
        reached.set()
        release.wait(5)
        return out

    monkeypatch.setattr(usage_service, "record", record)
    writing = asyncio.create_task(
        asyncio.to_thread(generation_service._persist, generation, "", 1.0)
    )
    assert await asyncio.to_thread(reached.wait, 5)
    # What `restart` does: the row belongs to another generation now.
    generation_service._RUNNING[message_id] = generation_service.Generation(
        chat_id=chat_id, message_id=message_id
    )
    release.set()
    await writing

    db.expire_all()
    row = db.get(Message, message_id)
    assert row.complete is False and row.content == ""
    assert db.scalar(select(Usage)) is None, "the superseded reply's usage was counted"
    assert db.get(Chat, chat_id).unread is False


async def test_a_cancel_while_the_reply_is_written_down_starts_nothing_new(
    db, user_id, monkeypatch
):
    """A shutdown that lands during the threaded write: the reply is saved,
    and the next queued prompt is NOT handed to a new reply on a server that
    is going down."""
    import asyncio
    import threading

    _add_connection(db)
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Like so."))

    async def _titled(*args, **kwargs):
        return "A title"

    monkeypatch.setattr(chat_service, "generate_title", _titled)
    reached, release = threading.Event(), threading.Event()
    real = generation_service._persist

    def persist(*args, **kwargs):
        reached.set()
        release.wait(5)
        return real(*args, **kwargs)

    drained: list[str] = []
    monkeypatch.setattr(generation_service, "_persist", persist)
    monkeypatch.setattr(generation_service, "_drain", lambda g: drained.append(g.message_id))

    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    task = asyncio.create_task(generation_service._run(generation))
    assert await asyncio.to_thread(reached.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert generation.done and drained == []
    assert generation.title_task is None, "no title is asked for on the way down"
    db.expire_all()
    assert db.get(Message, message_id).complete is True
    assert db.get(Message, message_id).content == "Like so."
    # Not cancelled: the queue is drained as before.
    other_chat, other = _chat_awaiting_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "_persist", real)
    await generation_service._run(
        generation_service.Generation(chat_id=other_chat, message_id=other)
    )
    assert drained == [other]


async def test_two_replies_back_to_back_ask_for_one_title(db, user_id, monkeypatch):
    """A queued second message is answered while the first reply's title is
    still out: it used to launch a second title call -- up to the limit again
    on a one-slot model -- for a name already on its way."""
    import asyncio

    _add_connection(db)
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Like so."))
    release = asyncio.Event()
    calls: list[str] = []

    async def _slow(_endpoint, _model, question, *_a, **_k):
        calls.append(question)
        await release.wait()
        return "Search indexing, explained"

    monkeypatch.setattr(chat_service, "generate_title", _slow)
    first = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    await generation_service._run(first)
    assert generation_service.title_pending(chat_id)

    db.add(Message(chat_id=chat_id, role="user", content="And the second?", complete=True))
    db.commit()
    again = Message(chat_id=chat_id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(again)
    db.commit()
    second = generation_service.Generation(chat_id=chat_id, message_id=again.id)
    await generation_service._run(second)
    await asyncio.sleep(0.05)
    # The same answer is waited on by both replies' followers.
    assert second.title_task is first.title_task

    release.set()
    assert await first.title_task == "Search indexing, explained"
    assert len(calls) == 1
    db.expire_all()
    assert db.get(Chat, chat_id).title == "Search indexing, explained"

    # A chat named meanwhile is not asked about at all.
    third = Message(chat_id=chat_id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(third)
    db.commit()
    await generation_service._run(
        generation_service.Generation(chat_id=chat_id, message_id=third.id)
    )
    assert len(calls) == 1


async def test_a_rename_is_never_overwritten_and_shutdown_cancels_the_naming(
    db, user_id, monkeypatch
):
    import asyncio

    _add_connection(db)
    chat_id, message_id = _chat_awaiting_a_reply(db, user_id)
    # The write carries its own condition: a renamed chat is not touched.
    chat = db.get(Chat, chat_id)
    chat.title, chat.title_generated = "Orthanc", True
    db.commit()
    assert generation_service._store_title(chat_id, "From the model") == "Orthanc"
    db.expire_all()
    assert db.get(Chat, chat_id).title == "Orthanc"

    other_chat, other = _chat_awaiting_a_reply(db, user_id)
    monkeypatch.setattr(generation_service, "stream_chat", _stub_stream("Like so."))

    async def _never(*args, **kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(chat_service, "generate_title", _never)
    generation = generation_service.Generation(chat_id=other_chat, message_id=other)
    await generation_service._run(generation)
    await asyncio.sleep(0.05)
    assert generation_service.title_pending(other_chat)
    await generation_service.shutdown()
    assert generation.title_task.cancelled() and generation_service._TITLES == {}
