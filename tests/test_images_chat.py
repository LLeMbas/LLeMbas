"""Image generation where it meets the conversation.

Three seams, each of which fails silently if it is got wrong: how a generated
image is bound to the reply that made it, why it is never replayed on an
assistant turn, and what `/image` actually sends.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import lembas
from lembas.db.models import ROLE_ASSISTANT, ROLE_USER, Attachment, Chat, Connection, Message, Model
from lembas.services import chat as chat_service
from lembas.services import files as files_service
from lembas.services import generation as generation_service
from lembas.services.crypto import encrypt

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)


@pytest.fixture
def vision_chat(db, user_id):
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt(""))
    db.add(connection)
    db.commit()
    db.add(
        Model(
            connection_id=connection.id,
            model_id="m",
            capabilities_json={"vision": True, "tools": True},
        )
    )
    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()
    return chat


# --- Storing what was drawn ----------------------------------------------------
def test_a_generated_image_is_kept_as_it_arrived(db, user_id, vision_chat):
    """`_process_image` transcodes to JPEG q85 and downscales to 1400px, which
    is right for a phone photo and a visible loss on the one output this feature
    exists to produce."""
    attachment = files_service.store(
        db,
        user_id=user_id,
        chat_id=vision_chat.id,
        payload=PNG,
        filename="out.png",
        keep_original=True,
    )

    assert attachment.media_type == "image/png"
    assert files_service.stored_path(attachment.stored_name).read_bytes() == PNG


def test_an_ordinary_upload_is_still_processed(db, user_id, vision_chat):
    """The flag is opt-in, and the protection it skips still applies to
    everything that arrives from outside."""
    attachment = files_service.store(
        db, user_id=user_id, chat_id=vision_chat.id, payload=PNG, filename="photo.png"
    )
    assert attachment.media_type == "image/jpeg"


def test_a_corrupt_image_is_still_refused(db, user_id, vision_chat):
    """What `keep_original` skips is the resize and the transcode, not the
    check that this is an image at all."""
    with pytest.raises(files_service.FileError):
        files_service.store(
            db,
            user_id=user_id,
            chat_id=vision_chat.id,
            payload=b"\x89PNG\r\n\x1a\n" + b"rubbish",
            filename="broken.png",
            keep_original=True,
        )


def test_the_loop_binds_the_image_to_the_reply(db, user_id, vision_chat):
    """A runner cannot write the message row -- `_persist` is the single writer
    -- so the runner makes the attachment and the loop says which turn owns it,
    exactly as it already does for a canvas tab."""
    message = chat_service.create_message(db, vision_chat, ROLE_ASSISTANT, "here it is")
    attachment = files_service.store(
        db, user_id=user_id, chat_id=vision_chat.id, payload=PNG, filename="a.png"
    )

    generation_service._bind_attachments(db, vision_chat, message, [attachment.id])
    db.commit()

    db.refresh(attachment)
    assert attachment.message_id == message.id


def test_binding_refuses_a_row_from_another_chat(db, user_id, vision_chat):
    """The ids arrive on a tool event, which is a dict a runner built. Without
    the narrowing a forged one would pull somebody else's file into this
    conversation -- the reason `files.claim` is scoped the same way."""
    other = Chat(user_id=user_id, model_id="m")
    db.add(other)
    db.commit()
    elsewhere = files_service.store(
        db, user_id=user_id, chat_id=other.id, payload=PNG, filename="a.png"
    )
    message = chat_service.create_message(db, vision_chat, ROLE_ASSISTANT, "x")

    generation_service._bind_attachments(db, vision_chat, message, [elsewhere.id])
    db.commit()

    db.refresh(elsewhere)
    assert elsewhere.message_id is None


def test_binding_refuses_a_row_already_bound(db, user_id, vision_chat):
    first = chat_service.create_message(db, vision_chat, ROLE_ASSISTANT, "one")
    second = chat_service.create_message(db, vision_chat, ROLE_ASSISTANT, "two")
    attachment = files_service.store(
        db,
        user_id=user_id,
        chat_id=vision_chat.id,
        payload=PNG,
        filename="a.png",
        message_id=first.id,
    )

    generation_service._bind_attachments(db, vision_chat, second, [attachment.id])
    db.commit()

    db.refresh(attachment)
    assert attachment.message_id == first.id


# --- What reaches the model ----------------------------------------------------
def test_an_image_on_an_assistant_turn_is_never_replayed(db, user_id, vision_chat):
    """The multimodal list form on an `assistant` turn is rejected outright by
    OpenAI and by most local runners -- and it would break not that turn but
    every later one in the chat."""
    reply = chat_service.create_message(db, vision_chat, ROLE_ASSISTANT, "here it is")
    files_service.store(
        db,
        user_id=user_id,
        chat_id=vision_chat.id,
        payload=PNG,
        filename="a.png",
        message_id=reply.id,
    )
    db.refresh(reply)

    payload = chat_service.message_payload(reply, vision=True)

    assert isinstance(payload["content"], str), "no image_url parts on an assistant turn"
    assert payload["content"] == "here it is"


def test_an_image_a_person_sent_still_reaches_the_model(db, user_id, vision_chat):
    """The rule narrows assistant turns and nothing else."""
    turn = chat_service.create_message(db, vision_chat, ROLE_USER, "what is this?")
    files_service.store(
        db,
        user_id=user_id,
        chat_id=vision_chat.id,
        payload=PNG,
        filename="a.png",
        message_id=turn.id,
    )
    db.refresh(turn)

    payload = chat_service.message_payload(turn, vision=True)

    assert isinstance(payload["content"], list)
    assert any(part["type"] == "image_url" for part in payload["content"])


def test_a_generated_image_still_renders_in_the_bubble(
    client: TestClient, db, user_id, vision_chat
):
    """It is an attachment on the assistant message, and `_message.html` renders
    attachments for either role -- so the reader sees it without the template
    learning anything new."""
    reply = chat_service.create_message(db, vision_chat, ROLE_ASSISTANT, "here it is")
    attachment = files_service.store(
        db,
        user_id=user_id,
        chat_id=vision_chat.id,
        payload=PNG,
        filename="a.png",
        message_id=reply.id,
    )

    page = client.get(f"/chat/{vision_chat.id}").text
    assert f"/api/files/{attachment.id}/content" in page


# --- Forcing the tool ----------------------------------------------------------
def test_the_forced_tool_reaches_the_request(db, vision_chat):
    tools = [{"type": "function", "function": {"name": "image_generate", "parameters": {}}}]
    body = chat_service.build_request(db, vision_chat, tools=tools, force_tool="image_generate")

    assert body["tool_choice"] == {
        "type": "function",
        "function": {"name": "image_generate"},
    }


def test_nothing_is_forced_by_default(db, vision_chat):
    """A provider strict about unknown parameters must see exactly the request
    it always did until somebody types a slash command."""
    tools = [{"type": "function", "function": {"name": "image_generate", "parameters": {}}}]
    assert "tool_choice" not in chat_service.build_request(db, vision_chat, tools=tools)


def test_a_tool_that_was_not_offered_cannot_be_forced(db, vision_chat):
    """`resolve_tools` still decides what exists. Forcing something absent from
    the array is a request most endpoints reject outright."""
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]
    body = chat_service.build_request(db, vision_chat, tools=tools, force_tool="image_generate")
    assert "tool_choice" not in body


def test_forcing_needs_a_tools_array_at_all(db, vision_chat):
    assert "tool_choice" not in chat_service.build_request(
        db, vision_chat, force_tool="image_generate"
    )


def test_the_endpoint_only_accepts_names_from_the_allow_list(
    client: TestClient, db, registered, vision_chat, monkeypatch
):
    """This becomes `tool_choice`, so a name read straight off a form would let
    anyone who can send a message decide what the model must do next."""
    from lembas.api import chats as chats_api

    seen: dict = {}
    monkeypatch.setattr(
        chats_api.generation_service,
        "ensure",
        lambda chat_id, message_id, *, force_tool="": seen.update(force_tool=force_tool),
    )

    client.post(
        f"/api/chats/{vision_chat.id}/messages",
        data={"content": "hello", "force_tool": "shell_run"},
    )
    assert seen["force_tool"] == "", "not on the list, so not forced"

    # That first turn left an unfinished assistant row behind, and a second
    # message while one is in flight is *queued* rather than sent -- so it would
    # never reach `ensure` at all. Finish it first.
    db.query(Message).filter(Message.complete.is_(False)).update({"complete": True})
    db.commit()

    client.post(
        f"/api/chats/{vision_chat.id}/messages",
        data={"content": "a bicycle", "force_tool": "image_generate"},
    )
    assert seen["force_tool"] == "image_generate"


def test_the_image_command_is_in_the_table():
    """`/help` reads this list, so a command missing from it is one nobody can
    discover -- the direction this actually rots."""
    source = (Path(lembas.__file__).parent / "web/static/js/commands.js").read_text(
        encoding="utf-8"
    )
    assert 'name: "image"' in source
    assert 'body.append("force_tool", "image_generate")' in source
    assert "htmx.process" in source, "or the reply's sse-connect is inert markup"


def test_a_forced_round_does_not_force_the_next_one(db, vision_chat):
    """Leaving `tool_choice` in place would make every round call the tool
    again: draw a picture, be asked again, draw another."""
    source = (Path(lembas.__file__).parent / "services/generation.py").read_text(encoding="utf-8")
    assert 'payload.pop("tool_choice", None)' in source


def test_a_queued_turn_is_not_lost_when_it_was_forced(db, user_id, vision_chat):
    """`/image` typed while a reply is streaming queues like anything else. The
    forcing is on the generation, so a queued turn simply arrives unforced --
    which is right: by then the model has the words and the context."""
    db.add(Message(chat_id=vision_chat.id, role=ROLE_ASSISTANT, content="", complete=False))
    db.commit()

    from lembas.api import chats as chats_api

    assert chats_api._reply_in_flight(db, vision_chat) is True
    assert db.scalar(select(Attachment)) is None


# --- Which model reviews what was drawn ---------------------------------------
#
# The reviewer is named in the instance settings, and it used to be named by the
# `Model` row's primary key. "Test & refresh" on the connection screen deletes
# any model the endpoint has stopped listing and recreates it when it comes back
# with a new primary key -- so one refresh taken while an endpoint happened to be
# loading something else silently unset the administrator's choice. It did not
# fail: `_reviewer` falls back to the chat's own model, so the picture was
# reviewed by a different model than the one chosen, with nothing saying so.
def _reviewer_of(db, chat, settings: dict):
    from lembas.db.models import User
    from lembas.services import tools as tools_service
    from lembas.services.images import tool as image_tool

    user = db.get(User, chat.user_id)
    context = tools_service.context_for(db, user, chat, tools=tools_service.ToolSet())
    context.image_config = settings
    return image_tool._reviewer(context)


def test_the_reviewer_is_named_by_the_models_own_id(db, vision_chat):
    db.add(
        Model(
            connection_id=vision_chat.connection_id,
            model_id="reviewer",
            capabilities_json={"vision": True},
        )
    )
    db.commit()

    resolved = _reviewer_of(
        db, vision_chat, {"review_enabled": True, "review_model_id": "reviewer"}
    )

    assert resolved is not None
    assert resolved[1] == "reviewer"


def test_the_reviewer_survives_its_row_being_deleted_and_remade(db, vision_chat):
    """The refresh case, end to end: the row goes, an identical one arrives with
    a different primary key, and the choice still resolves."""
    db.add(
        Model(
            connection_id=vision_chat.connection_id,
            model_id="reviewer",
            capabilities_json={"vision": True},
        )
    )
    db.commit()
    settings = {"review_enabled": True, "review_model_id": "reviewer"}
    assert _reviewer_of(db, vision_chat, settings)[1] == "reviewer"

    row = db.scalar(select(Model).where(Model.model_id == "reviewer"))
    connection_id = row.connection_id
    db.delete(row)
    db.commit()
    db.add(
        Model(
            connection_id=connection_id,
            model_id="reviewer",
            capabilities_json={"vision": True},
        )
    )
    db.commit()

    assert _reviewer_of(db, vision_chat, settings)[1] == "reviewer"


def test_a_primary_key_stored_by_an_older_release_still_resolves(db, vision_chat):
    """The value written before the id was the rule is a primary key, and an
    instance that never touches the setting again must keep working."""
    db.add(
        Model(
            connection_id=vision_chat.connection_id,
            model_id="reviewer",
            capabilities_json={"vision": True},
        )
    )
    db.commit()
    row = db.scalar(select(Model).where(Model.model_id == "reviewer"))

    resolved = _reviewer_of(
        db, vision_chat, {"review_enabled": True, "review_model_id": row.id}
    )

    assert resolved is not None
    assert resolved[1] == "reviewer"


def test_the_admin_page_offers_the_models_own_id_as_the_value(client, db, vision_chat):
    """The other half. Storing the primary key is what created the problem, so
    the form must not put one back."""
    db.add(
        Model(
            connection_id=vision_chat.connection_id,
            model_id="reviewer",
            display_name="Reviewer",
            capabilities_json={"vision": True},
        )
    )
    db.commit()

    page = client.get("/admin/images").text
    row = db.scalar(select(Model).where(Model.model_id == "reviewer"))
    assert 'value="reviewer"' in page
    assert f'value="{row.id}"' not in page
