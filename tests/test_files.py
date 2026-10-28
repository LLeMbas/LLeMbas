"""Attachments: upload validation, extraction, and how they reach the model."""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from lembas.db.models import Attachment, Chat, Connection, Message, Model
from lembas.services import chat as chat_service
from lembas.services import files as files_service
from lembas.services import settings_store
from lembas.services.crypto import encrypt
from tests.conftest import join


def turns(body: dict) -> list[dict]:
    """The conversation turns, without the system preamble in front of them.

    `build_request` always emits a system message now -- the harness carries the
    date even for a model with no tools -- so a test about a *user* turn has to
    say which turn it means rather than assume index 0.
    """
    return [message for message in body["messages"] if message["role"] != "system"]


# --- Fixtures ----------------------------------------------------------------
def png_bytes(width: int = 40, height: int = 30, mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), "red").save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "blue").save(buffer, format="JPEG")
    return buffer.getvalue()


def pdf_bytes(pages: list[str]) -> bytes:
    """A real PDF with a text layer, built with pypdf + reportlab-free drawing.

    pypdf cannot author text, so the file is assembled by hand. It is minimal
    but genuinely parseable, which is the point -- a fake would not exercise
    extraction at all.
    """
    objects: list[bytes] = []

    def stream_for(text: str) -> bytes:
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        return f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")

    page_ids = [4 + i * 2 for i in range(len(pages))]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for index, text in enumerate(pages):
        content = stream_for(text)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {page_ids[index] + 1} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode()
    )
    return bytes(out)


@pytest.fixture
def chat_with_model(client: TestClient, db, registered, make_chat):
    """A chat whose model has vision turned on."""
    connection = Connection(name="T", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt(""))
    db.add(connection)
    db.commit()
    db.add(
        Model(
            connection_id=connection.id,
            model_id="seeing-model",
            capabilities_json={"vision": True},
        )
    )
    db.commit()
    return make_chat()


# --- Type detection and processing -------------------------------------------
def test_png_is_recognised_as_an_image():
    prepared = files_service.prepare(png_bytes(), "photo.png")
    assert prepared.kind == "image"
    assert prepared.width == 40 and prepared.height == 30


def test_large_images_are_downscaled():
    """A phone photo is megabytes of base64 and a large slice of the context."""
    prepared = files_service.prepare(jpeg_bytes(4000, 3000), "big.jpg")
    assert max(prepared.width, prepared.height) == files_service.MAX_IMAGE_EDGE
    assert prepared.height == int(3000 * (files_service.MAX_IMAGE_EDGE / 4000))


def test_small_images_are_left_alone():
    prepared = files_service.prepare(jpeg_bytes(100, 80), "small.jpg")
    assert (prepared.width, prepared.height) == (100, 80)


def test_transparent_images_stay_png():
    prepared = files_service.prepare(png_bytes(mode="RGBA"), "logo.png")
    assert prepared.media_type in ("image/png", "image/jpeg")


def test_a_file_lying_about_its_type_is_judged_by_its_bytes():
    """The extension says PNG; the content is text, and content wins."""
    prepared = files_service.prepare(b"just some words", "trick.png")
    assert prepared.kind == "text"


def test_pdf_text_is_extracted():
    prepared = files_service.prepare(pdf_bytes(["Lembas keeps a traveller going."]), "doc.pdf")
    assert prepared.kind == "document"
    assert prepared.pages == 1
    assert "Lembas keeps a traveller going." in prepared.extracted_text


def test_multi_page_pdfs_are_labelled_by_page():
    prepared = files_service.prepare(pdf_bytes(["First page here", "Second page here"]), "d.pdf")
    assert prepared.pages == 2
    assert "[page 1]" in prepared.extracted_text
    assert "[page 2]" in prepared.extracted_text


def test_a_pdf_with_no_text_layer_says_so():
    """A scan otherwise looks like the model simply ignored the document."""
    prepared = files_service.prepare(pdf_bytes([" "]), "scan.pdf")
    assert prepared.extraction_error
    assert "scanned" in prepared.extraction_error.lower()


def test_a_corrupt_pdf_is_stored_with_an_error_not_rejected():
    prepared = files_service.prepare(b"%PDF-1.4\nthis is not really a pdf", "broken.pdf")
    assert prepared.kind == "document"
    assert prepared.extraction_error


def test_text_files_are_decoded():
    prepared = files_service.prepare(b"line one\nline two", "notes.txt")
    assert prepared.kind == "text"
    assert prepared.extracted_text == "line one\nline two"


def test_source_files_keep_a_sensible_media_type():
    assert files_service.prepare(b"print('hi')", "x.py").media_type == "text/x-python"


def test_binary_files_are_rejected():
    with pytest.raises(files_service.FileError):
        files_service.prepare(b"\x00\x01\x02\x03" * 100, "mystery.bin")


def test_empty_files_are_rejected():
    with pytest.raises(files_service.FileError):
        files_service.prepare(b"", "empty.txt")


def test_oversized_files_are_rejected():
    with pytest.raises(files_service.FileError) as caught:
        files_service.prepare(b"x" * (files_service.limits().max_upload_bytes + 1), "huge.txt")
    assert "MB" in str(caught.value)


def test_extracted_text_is_capped(monkeypatch):
    """Through the setting rather than the constant. The constant is only the
    default now; what `prepare` reads is the snapshot, which is the thing that
    would have gone on returning 120,000 if the wiring were wrong."""
    monkeypatch.setattr(files_service, "_LIMITS", files_service.Limits(max_extracted_chars=50))
    prepared = files_service.prepare(b"x" * 500, "long.txt")
    assert len(prepared.extracted_text) == 50
    assert prepared.truncated is True


# --- Path safety -------------------------------------------------------------
@pytest.mark.parametrize(
    "name", ["../../etc/passwd", "..\\windows", "/etc/passwd", ".hidden", ""]
)
def test_stored_path_refuses_traversal(name):
    assert files_service.stored_path(name) is None


def test_stored_names_are_random_not_the_uploaders(client: TestClient, db, registered):
    response = client.post(
        "/api/files", files={"file": ("../../evil.txt", b"content", "text/plain")}
    )
    assert response.status_code == 200
    attachment = db.scalar(select(Attachment))
    assert "/" not in attachment.stored_name
    assert ".." not in attachment.stored_name
    # The display name is kept, but only as a label.
    assert attachment.filename == "evil.txt"


# --- Upload through the API --------------------------------------------------
def test_upload_returns_a_chip(client: TestClient, db, registered):
    response = client.post(
        "/api/files", files={"file": ("notes.txt", b"hello there", "text/plain")}
    )
    assert response.status_code == 200
    assert "notes.txt" in response.text
    assert 'name="file_ids"' in response.text


def test_a_rejected_upload_returns_a_readable_error(client: TestClient, db, registered):
    response = client.post(
        "/api/files", files={"file": ("bad.bin", b"\x00\x01" * 500, "application/octet-stream")}
    )
    assert response.status_code == 200
    assert "chip--error" in response.text
    assert db.scalar(select(Attachment)) is None


def test_uploading_needs_permission(client: TestClient, db, registered):
    client.post("/auth/logout", follow_redirects=False)
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    settings_store.update(db, {"default_permissions": {"files.upload": False}})
    response = client.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")})
    assert response.status_code == 403


def test_you_cannot_fetch_someone_elses_file(client: TestClient, db, registered):
    client.post("/api/files", files={"file": ("secret.txt", b"mine", "text/plain")})
    attachment = db.scalar(select(Attachment))

    client.post("/auth/logout", follow_redirects=False)
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    assert client.get(f"/api/files/{attachment.id}/content").status_code == 404


def test_non_images_are_served_as_downloads(client: TestClient, db, registered):
    """An uploaded .html served inline would run in this origin."""
    client.post("/api/files", files={"file": ("page.html", b"<b>hi</b>", "text/html")})
    attachment = db.scalar(select(Attachment))

    response = client.get(f"/api/files/{attachment.id}/content")
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_images_are_served_inline(client: TestClient, db, registered):
    client.post("/api/files", files={"file": ("p.png", png_bytes(), "image/png")})
    attachment = db.scalar(select(Attachment))
    response = client.get(f"/api/files/{attachment.id}/content")
    assert "inline;" in response.headers["content-disposition"]


def test_an_unsent_attachment_can_be_removed(client: TestClient, db, registered):
    client.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")})
    attachment = db.scalar(select(Attachment))
    stored = files_service.stored_path(attachment.stored_name)

    assert client.delete(f"/api/files/{attachment.id}").status_code == 200
    assert db.scalar(select(Attachment)) is None
    assert not stored.exists()


def test_a_sent_attachment_cannot_be_removed(client: TestClient, db, chat_with_model):
    """Deleting it would rewrite a conversation the user has already read."""
    client.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")})
    attachment = db.scalar(select(Attachment))
    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "look", "file_ids": [attachment.id]},
    )
    assert client.delete(f"/api/files/{attachment.id}").status_code == 409


# --- Attaching to a message --------------------------------------------------
def test_sending_binds_the_attachment_to_the_message(client: TestClient, db, chat_with_model):
    client.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")})
    attachment = db.scalar(select(Attachment))

    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "have a look", "file_ids": [attachment.id]},
    )
    db.refresh(attachment)
    message = db.scalar(select(Message).where(Message.role == "user"))
    assert attachment.message_id == message.id


def test_a_message_with_only_an_attachment_is_accepted(client: TestClient, db, chat_with_model):
    """"Here, look at this" with no words is a legitimate turn."""
    client.post("/api/files", files={"file": ("a.png", png_bytes(), "image/png")})
    attachment = db.scalar(select(Attachment))

    response = client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "", "file_ids": [attachment.id]},
    )
    assert response.status_code == 200
    assert db.scalar(select(Message).where(Message.role == "user")) is not None


def test_a_truly_empty_message_is_still_ignored(client: TestClient, db, chat_with_model):
    response = client.post(f"/api/chats/{chat_with_model}/messages", data={"content": "  "})
    assert response.status_code == 204
    assert db.scalar(select(Message)) is None


def test_you_cannot_attach_someone_elses_file(
    client: TestClient, db, chat_with_model, make_chat
):
    """A forged id must not pull another user's file into a conversation."""
    client.post("/api/files", files={"file": ("mine.txt", b"secret", "text/plain")})
    stolen = db.scalar(select(Attachment))

    client.post("/auth/logout", follow_redirects=False)
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    their_chat = make_chat(email="sam@shire.test")
    client.post(
        f"/api/chats/{their_chat}/messages",
        data={"content": "gimme", "file_ids": [stolen.id]},
    )

    db.refresh(stolen)
    assert stolen.message_id is None


# --- What reaches the model --------------------------------------------------
def _user_message(db, chat_id: str) -> Message:
    return db.scalar(
        select(Message).where(Message.chat_id == chat_id, Message.role == "user")
    )


def test_images_become_multimodal_parts_for_a_vision_model(client: TestClient, db, chat_with_model):
    client.post("/api/files", files={"file": ("p.png", png_bytes(), "image/png")})
    attachment = db.scalar(select(Attachment))
    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "what is this?", "file_ids": [attachment.id]},
    )

    chat = db.get(Chat, chat_with_model)
    payload = chat_service.build_request(db, chat)
    content = turns(payload)[0]["content"]

    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "what is this?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/")


def test_images_are_withheld_from_a_model_without_vision(client: TestClient, db, chat_with_model):
    """Most endpoints reject the whole request rather than ignoring the image."""
    model = db.scalar(select(Model))
    model.capabilities_json = {"vision": False}
    db.commit()

    client.post("/api/files", files={"file": ("p.png", png_bytes(), "image/png")})
    attachment = db.scalar(select(Attachment))
    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "what is this?", "file_ids": [attachment.id]},
    )

    chat = db.get(Chat, chat_with_model)
    content = turns(chat_service.build_request(db, chat))[0]["content"]
    assert isinstance(content, str)
    assert content == "what is this?"


def test_a_plain_turn_stays_a_plain_string(client: TestClient, db, chat_with_model):
    """The list form is a reliable 400 from endpoints that do not implement it."""
    client.post(f"/api/chats/{chat_with_model}/messages", data={"content": "just words"})
    chat = db.get(Chat, chat_with_model)
    assert turns(chat_service.build_request(db, chat))[0]["content"] == "just words"


def test_document_text_is_prepended_in_tags(client: TestClient, db, chat_with_model):
    client.post(
        "/api/files", files={"file": ("report.txt", b"Quarterly results were good.", "text/plain")}
    )
    attachment = db.scalar(select(Attachment))
    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "summarise this", "file_ids": [attachment.id]},
    )

    chat = db.get(Chat, chat_with_model)
    content = turns(chat_service.build_request(db, chat))[0]["content"]
    assert '<document name="report.txt">' in content
    assert "Quarterly results were good." in content
    # The question comes after the material it refers to.
    assert content.index("</document>") < content.index("summarise this")


def test_documents_reach_a_model_without_vision(client: TestClient, db, chat_with_model):
    model = db.scalar(select(Model))
    model.capabilities_json = {}
    db.commit()

    client.post("/api/files", files={"file": ("notes.txt", b"important detail", "text/plain")})
    attachment = db.scalar(select(Attachment))
    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "read it", "file_ids": [attachment.id]},
    )

    chat = db.get(Chat, chat_with_model)
    content = turns(chat_service.build_request(db, chat))[0]["content"]
    assert "important detail" in content


def test_truncation_is_declared_to_the_model(client: TestClient, db, chat_with_model, monkeypatch):
    """A model asked about page 400 should be able to say it did not see it."""
    monkeypatch.setattr(files_service, "_LIMITS", files_service.Limits(max_extracted_chars=20))
    client.post("/api/files", files={"file": ("big.txt", b"y" * 200, "text/plain")})
    attachment = db.scalar(select(Attachment))
    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "read", "file_ids": [attachment.id]},
    )

    chat = db.get(Chat, chat_with_model)
    assert "(truncated)" in turns(chat_service.build_request(db, chat))[0]["content"]


def test_an_attachment_only_turn_still_reaches_the_model(client: TestClient, db, chat_with_model):
    client.post("/api/files", files={"file": ("p.png", png_bytes(), "image/png")})
    attachment = db.scalar(select(Attachment))
    client.post(
        f"/api/chats/{chat_with_model}/messages", data={"content": "", "file_ids": [attachment.id]}
    )

    chat = db.get(Chat, chat_with_model)
    messages = turns(chat_service.build_request(db, chat))
    assert len(messages) == 1
    assert messages[0]["content"][0]["type"] == "image_url"


# --- Housekeeping ------------------------------------------------------------
def test_orphans_are_swept(client: TestClient, db, registered):
    client.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")})
    attachment = db.scalar(select(Attachment))
    path = files_service.stored_path(attachment.stored_name)

    assert files_service.sweep_orphans(db, timedelta(hours=24)) == 0  # too new
    assert files_service.sweep_orphans(db, timedelta(seconds=-1)) == 1
    assert db.scalar(select(Attachment)) is None
    assert not path.exists()


def test_sent_attachments_are_never_swept(client: TestClient, db, chat_with_model):
    client.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")})
    attachment = db.scalar(select(Attachment))
    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "keep", "file_ids": [attachment.id]},
    )

    assert files_service.sweep_orphans(db, timedelta(seconds=-1)) == 0
    assert db.scalar(select(Attachment)) is not None


def test_deleting_a_message_deletes_its_attachments(client: TestClient, db, chat_with_model):
    client.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")})
    attachment = db.scalar(select(Attachment))
    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "x", "file_ids": [attachment.id]},
    )

    client.delete(f"/api/chats/{chat_with_model}")
    assert db.scalar(select(Attachment)) is None


# --- Composer wiring ---------------------------------------------------------
# These assert on the rendered HTML rather than on behaviour, because the bug
# they guard against lives entirely in the template: every server-side test
# passed while the browser silently never sent file_ids at all.
def test_the_attachments_container_is_inside_the_composer_form(
    client: TestClient, db, chat_with_model
):
    """The chips carry the hidden file_ids inputs. Outside the form they are
    not serialised, and hx-include does not help -- it only has an effect on
    the element issuing the request, not on a child of it."""
    import re

    page = client.get(f"/chat/{chat_with_model}").text
    form = re.search(r'<form class="composer__form".*?</form>', page, re.S)
    assert form, "composer form not found"
    assert 'id="attachments"' in form.group(0)


def test_the_file_input_is_outside_the_composer_form(client: TestClient, db, chat_with_model):
    """Inside, it would be submitted as an empty file part on every message."""
    import re

    page = client.get(f"/chat/{chat_with_model}").text
    form = re.search(r'<form class="composer__form".*?</form>', page, re.S)
    assert 'id="file-input"' not in form.group(0)
    assert 'id="file-input"' in page


def test_the_new_chat_composer_also_contains_the_attachments(
    client: TestClient, db, chat_with_model
):
    import re

    page = client.get("/chat").text
    form = re.search(r'<form class="composer__form".*?</form>', page, re.S)
    assert form and 'id="attachments"' in form.group(0)


def test_the_chip_carries_a_file_ids_input(client: TestClient, db, registered):
    """That input is the entire mechanism by which an upload reaches a message."""
    response = client.post(
        "/api/files", files={"file": ("a.txt", b"hi", "text/plain")}
    )
    assert 'name="file_ids"' in response.text
    assert 'type="hidden"' in response.text


def test_a_browser_serialising_the_form_actually_sends_the_attachment(
    client: TestClient, db, chat_with_model
):
    """End-to-end wiring check.

    Uploads a file, splices the returned chip into the page exactly as the
    browser does, then serialises the composer form the way a browser would --
    every named input inside <form> -- and posts that. This is the test that
    fails when the chips drift back outside the form.
    """
    import re

    chip = client.post(
        "/api/files", files={"file": ("proof.png", png_bytes(), "image/png")}
    ).text
    attachment = db.scalar(select(Attachment))
    assert attachment.message_id is None

    page = client.get(f"/chat/{chat_with_model}").text
    form_html = re.search(r'<form class="composer__form".*?</form>', page, re.S).group(0)
    # The chips are inserted into #attachments, which lives inside the form.
    form_html = form_html.replace(
        '<div class="composer__attachments" id="attachments"></div>',
        f'<div class="composer__attachments" id="attachments">{chip}</div>',
    )

    fields: list[tuple[str, str]] = []
    for tag in re.findall(r"<(?:input|textarea)\b[^>]*>", form_html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name:
            continue
        value = re.search(r'value="([^"]*)"', tag)
        fields.append((name.group(1), value.group(1) if value else ""))

    assert ("file_ids", attachment.id) in fields, f"file_ids not serialised: {fields}"

    client.post(f"/api/chats/{chat_with_model}/messages", data=dict(fields) | {"content": "look"})

    db.refresh(attachment)
    message = db.scalar(select(Message).where(Message.role == "user"))
    assert attachment.message_id == message.id

    chat = db.get(Chat, chat_with_model)
    content = turns(chat_service.build_request(db, chat))[0]["content"]
    assert isinstance(content, list), "the image never reached the model"
    assert any(p.get("type") == "image_url" for p in content)


# --- The extraction settings ---------------------------------------------------
# The constants above are defaults now, and what `prepare` actually reads is a
# process-level snapshot. Every one of these failures would be silent: a limit
# that looks configured and is not.
def test_a_saved_limit_reaches_the_snapshot(db, client, registered):
    from lembas.services import settings_store

    client.post(
        "/admin/extraction",
        data={
            "max_upload_mb": "5",
            "max_image_edge": "800",
            "jpeg_quality": "70",
            "max_pdf_pages": "10",
            "max_extracted_chars": "2000",
            "orphan_hours": "3",
            "extra_text_extensions": "env\n.conf",
        },
        follow_redirects=False,
    )

    bounds = files_service.limits()
    assert bounds.max_upload_bytes == 5 * 1024 * 1024
    assert bounds.max_extracted_chars == 2000
    assert bounds.max_image_edge == 800
    assert settings_store.extraction(db)["extra_text_extensions"] == ["env", ".conf"]


def test_an_extra_extension_gets_a_leading_dot(db, client, registered):
    """Typed both ways by different people, and a mapping somebody has to get
    right twice is one they get wrong once."""
    client.post(
        "/admin/extraction", data={"extra_text_extensions": "env"}, follow_redirects=False
    )

    assert files_service.limits().media_type_for(".env") == "text/plain"


def test_an_unknown_extension_is_still_stored_as_text(db):
    """Decodability is what decides. The list only picks a media type, which is
    why an unlisted extension has always worked and must go on working."""
    prepared = files_service.prepare(b"hello there", "notes.wat")
    assert prepared.kind == "text"
    assert prepared.extension == ".txt"


def test_a_number_out_of_range_is_clamped(db, client, registered):
    client.post(
        "/admin/extraction",
        data={"max_upload_mb": "99999", "jpeg_quality": "1"},
        follow_redirects=False,
    )

    bounds = files_service.limits()
    assert bounds.max_upload_bytes == 512 * 1024 * 1024
    assert bounds.jpeg_quality == 30


def test_the_snapshot_is_dropped_when_the_page_saves(db, client, registered):
    """Read once per process. A save that did not drop it would take effect at
    the next restart, which is the failure this codebase keeps cataloguing."""
    assert files_service.limits().max_upload_bytes == 20 * 1024 * 1024

    client.post("/admin/extraction", data={"max_upload_mb": "1"}, follow_redirects=False)

    assert files_service.limits().max_upload_bytes == 1024 * 1024


def test_only_an_administrator_may_change_extraction(client, registered):
    client.post("/auth/logout", follow_redirects=False)
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )

    assert client.post("/admin/extraction", data={"max_upload_mb": "1"}).status_code == 403
    assert client.post("/admin/extraction/search", data={}).status_code == 403


def test_deleting_a_chat_removes_the_files_it_held(
    client: TestClient, db, chat_with_model
):
    """Deleting a Chat cascades to its message and attachment *rows* and leaves
    every file on disk -- a generated image, an uploaded PDF -- with nothing that
    will ever look at them again, since `sweep_orphans` only considers uploads
    that were never attached.

    `remove_files_for_chats` was written for this and was called from exactly one
    place, the temporary-chat sweep. The delete button was not it. That is
    `sharing.forget_principal` a third time: a correct helper, absent from the
    path that needs it.
    """
    client.post("/api/files", files={"file": ("a.png", png_bytes(), "image/png")})
    attachment = db.scalar(select(Attachment))
    path = files_service.stored_path(attachment.stored_name)
    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "here", "file_ids": [attachment.id]},
    )
    assert path.exists()

    client.delete(f"/api/chats/{chat_with_model}")

    assert db.scalar(select(Attachment)) is None
    assert not path.exists(), "the row went and the file stayed"


def test_delete_chats_is_what_every_path_uses(client: TestClient, db, chat_with_model):
    """One function, so the next thing that deletes a chat cannot forget. It has
    to unlink *before* the rows go -- they are what says which files to remove."""
    client.post("/api/files", files={"file": ("a.png", png_bytes(), "image/png")})
    attachment = db.scalar(select(Attachment))
    path = files_service.stored_path(attachment.stored_name)
    client.post(
        f"/api/chats/{chat_with_model}/messages",
        data={"content": "here", "file_ids": [attachment.id]},
    )

    chat = db.get(Chat, chat_with_model)
    assert chat_service.delete_chats(db, [chat]) == 1
    db.commit()

    assert not path.exists()
    assert chat_service.delete_chats(db, []) == 0
    assert chat_service.delete_chats(db, [None]) == 0
