"""`@` attachments: what the picker offers, and what the model is told it got.

The point of the second half is the one the feature was asked for: a model
handed a file called `main.py` cannot tell which of four it is looking at, and
cannot name it back when asked to change something. So the path and the machine
travel with the contents.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Attachment, Message, User
from lembas.services import chat as chat_service
from tests.conftest import join


# --- The picker --------------------------------------------------------------
def test_a_plain_chat_gets_a_picker(client: TestClient, db, registered):
    """`@` works everywhere, with nothing to offer yet and no machine to ask."""
    response = client.get("/api/files/mention-picker")

    assert response.status_code == 200
    assert "Nothing to mention yet." in response.text


# --- What `@` offers where there is no machine at all ------------------------
def test_a_url_is_offered_as_a_page_to_read(client: TestClient, db, registered):
    body = client.get(
        "/api/files/mention-picker", params={"q": "https://tolkien.test/mallorn"}
    ).text

    assert "Fetch this page" in body
    assert "https://tolkien.test/mallorn" in body


# --- Attaching ---------------------------------------------------------------
def test_the_model_is_told_which_file_and_where(client: TestClient, db, registered):
    """The whole reason the columns exist. `main.py` alone is not an answer to
    "which one", and a model cannot name a file back that it was never given
    the path of."""
    from lembas.services import files as files_service

    user = db.scalars(select(User)).first()
    files_service.store_text(
        db,
        user_id=user.id,
        chat_id=None,
        filename="main.py",
        text="print('hello')\n",
        source_path="src/main.py",
        source_label="Container",
    )

    attachment = db.scalars(select(Attachment)).one()
    message = Message(chat_id=None, role="user", content="")
    message.attachments = [attachment]

    block = chat_service.document_context(message)

    assert 'path="src/main.py"' in block
    assert 'from="Container"' in block
    assert 'name="main.py"' in block


def test_a_quote_in_a_path_cannot_break_out_of_the_tag(client: TestClient, db, registered):
    """These are attribute values in a tag we write. A path containing a quote
    would otherwise close it early and the rest would read as instructions."""
    from lembas.services import files as files_service

    user = db.scalars(select(User)).first()
    attachment = files_service.store_text(
        db,
        user_id=user.id,
        chat_id=None,
        filename="x.txt",
        text="body",
        source_path='/tmp/a"><script>.txt',
        source_label='Box" evil',
    )
    message = Message(chat_id=None, role="user", content="")
    message.attachments = [attachment]

    block = chat_service.document_context(message)

    assert "<script>" not in block
    assert block.count("<document") == 1


def test_an_ordinary_upload_carries_no_provenance(client: TestClient, db, registered):
    """A file dragged in from a laptop has no address this instance could
    honestly report, so the tag stays as it was."""
    from lembas.services import files as files_service

    user = db.scalars(select(User)).first()
    attachment = files_service.store(
        db, user_id=user.id, chat_id=None, payload=b"hello", filename="notes.txt"
    )
    message = Message(chat_id=None, role="user", content="")
    message.attachments = [attachment]

    block = chat_service.document_context(message)

    assert "path=" not in block
    assert "from=" not in block


# --- /usage ------------------------------------------------------------------
def test_usage_sums_what_every_reply_recorded(client: TestClient, db, registered, make_chat):
    """Summed from what was stored rather than recomputed. An endpoint that
    reported nothing contributed an estimate at the time, and re-deriving it
    now would make the totals move under a chat nobody had touched."""
    from lembas.db.models import ROLE_ASSISTANT, Chat

    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    for prompt, completion in ((100, 20), (150, 30)):
        db.add(
            Message(
                chat_id=chat.id,
                role=ROLE_ASSISTANT,
                content="hi",
                usage_json={
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": prompt + completion,
                },
            )
        )
    db.commit()

    body = client.get(f"/api/chats/{chat_id}/usage").text

    assert "300" in body  # 250 sent + 50 written
    assert "2 replies" in body


def test_usage_is_owner_only(client: TestClient, db, registered, make_chat):
    """No admin branch, unlike the inspector beside it: these numbers describe
    somebody's conversation, and reading one is not a configuration act."""
    chat_id = make_chat()

    client.post("/auth/logout", follow_redirects=False)
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )

    assert client.get(f"/api/chats/{chat_id}/usage").status_code == 404


def test_an_unknown_window_size_is_not_reported_as_zero(
    client: TestClient, db, registered, make_chat
):
    """Unknown is not zero. Nobody has said how big this model's window is, so
    there is no percentage and compaction will never fire."""
    chat_id = make_chat()

    body = client.get(f"/api/chats/{chat_id}/usage").text

    assert "never compact itself" in body
