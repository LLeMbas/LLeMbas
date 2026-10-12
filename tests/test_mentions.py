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


# --- The picker --------------------------------------------------------------
def test_a_plain_chat_gets_a_picker(client: TestClient, db, registered):
    """`@` works everywhere, with nothing to offer yet and no machine to ask."""
    response = client.get("/api/files/mention-picker")

    assert response.status_code == 200
    assert "Nothing to mention yet." in response.text


# --- What `@` offers where there is no machine at all ------------------------
def _library(db):
    """A note, a skill and a base belonging to the registered reader."""
    from lembas.db.models import KnowledgeBase, Note, Skill

    owner = db.scalars(select(User)).first()
    note = Note(owner_id=owner.id, title="Mallorn notes", body="Golden leaves.")
    skill = Skill(
        owner_id=owner.id, name="bake-lembas", description="How to bake it", body="Steps."
    )
    base = KnowledgeBase(owner_id=owner.id, name="Contracts")
    db.add_all([note, skill, base])
    db.commit()
    return note, skill, base


def test_notes_and_skills_are_offered_in_a_plain_chat(client: TestClient, db, registered):
    """A plain chat has no project files, which is exactly why
    the rest of the library has to be reachable there."""
    _library(db)

    body = client.get("/api/files/mention-picker", params={"q": "mallorn"}).text
    assert "Mallorn notes" in body

    body = client.get("/api/files/mention-picker", params={"q": "lembas"}).text
    assert "bake-lembas" in body


def test_a_knowledge_base_is_only_offered_inside_a_chat(
    client: TestClient, db, registered, make_chat
):
    """There is nothing to attach it to before a chat exists -- the same reason
    project files are absent on the new-chat screen."""
    _library(db)
    chat_id = make_chat()

    assert "Contracts" not in client.get("/api/files/mention-picker").text
    assert "Contracts" in client.get(
        "/api/files/mention-picker", params={"chat_id": chat_id}
    ).text


def test_a_url_is_offered_as_a_page_to_read(client: TestClient, db, registered):
    body = client.get(
        "/api/files/mention-picker", params={"q": "https://tolkien.test/mallorn"}
    ).text

    assert "Fetch this page" in body
    assert "https://tolkien.test/mallorn" in body


def test_a_note_arrives_with_its_text_and_its_name(client: TestClient, db, registered, make_chat):
    note, _skill, _base = _library(db)
    chat_id = make_chat()

    response = client.post(
        "/api/files/from-note", data={"note_id": note.id, "chat_id": chat_id}
    )

    assert response.status_code == 200
    attachment = db.scalars(select(Attachment)).one()
    assert "Golden leaves." in attachment.extracted_text
    # Provenance, for the reason a project file carries it: a model handed four
    # documents cannot name one back when asked to work on it.
    assert attachment.source_label == "Note"
    assert attachment.source_path == "Mallorn notes"


def test_a_skill_can_be_handed_over_directly(client: TestClient, db, registered, make_chat):
    """The index is in the harness and `skill_get` fetches on demand -- but only
    if the model decides to. `@` is the reader saying "use this one"."""
    _note, skill, _base = _library(db)
    chat_id = make_chat()

    client.post("/api/files/from-skill", data={"skill_id": skill.id, "chat_id": chat_id})

    attachment = db.scalars(select(Attachment)).one()
    assert attachment.source_label == "Skill"
    assert "Steps." in attachment.extracted_text


def test_a_base_is_attached_as_a_reference_not_a_copy(
    client: TestClient, db, registered, make_chat
):
    """Scoping, not copying. A folder of contracts in the window would cost the
    context on every request forever to answer one question."""
    from lembas.db.models import Chat

    _note, _skill, base = _library(db)
    chat_id = make_chat()

    response = client.post(f"/api/chats/{chat_id}/bases", data={"base_id": base.id})

    assert response.status_code == 200
    db.expire_all()
    assert [b.id for b in db.get(Chat, chat_id).knowledge_bases] == [base.id]
    # Nothing was copied into the message.
    assert db.scalars(select(Attachment)).all() == []


def test_attaching_the_same_base_twice_is_not_an_error(
    client: TestClient, db, registered, make_chat
):
    from lembas.db.models import Chat

    _note, _skill, base = _library(db)
    chat_id = make_chat()

    client.post(f"/api/chats/{chat_id}/bases", data={"base_id": base.id})
    client.post(f"/api/chats/{chat_id}/bases", data={"base_id": base.id})

    db.expire_all()
    assert len(db.get(Chat, chat_id).knowledge_bases) == 1


def test_a_library_document_now_carries_its_provenance(client: TestClient, db, registered):
    """This was the one attach path that dropped it, while a project file beside
    it carried path and machine."""
    from lembas.services.fetch import Fetched
    from lembas.services.library import documents as documents_service

    owner = db.scalars(select(User)).first()
    base = documents_service.default_base(db, owner)
    document = documents_service.store_page(
        db,
        owner=owner,
        base=base,
        page=Fetched(url="https://tolkien.test/c", title="The Contract", text="Terms."),
    )

    client.post("/api/files/from-knowledge", data={"document_id": document.id})

    attachment = db.scalars(select(Attachment)).one()
    assert attachment.source_path == "The Contract"
    assert attachment.source_label == base.name


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
    client.post(
        "/auth/register",
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
