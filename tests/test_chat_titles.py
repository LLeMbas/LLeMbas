"""How a chat gets its name, and how it gets a different one."""

from __future__ import annotations

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
    async def stream_chat(_endpoint, _payload):
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

    db.expire_all()
    assert db.get(Chat, chat_id).title == "Search indexing, explained"


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
