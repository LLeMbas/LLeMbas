"""Uploading, serving and removing chat attachments."""

from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.db.models import Attachment, Chat, Document, KnowledgeBase, Note
from lembas.security import permissions
from lembas.services import data_groups, settings_store
from lembas.services import files as files_service
from lembas.services.fetch import FetchError, fetch
from lembas.services.library import documents as documents_service
from lembas.services.library import notes as notes_service
from lembas.services.library import skills as skills_service
from lembas.web.i18n import t
from lembas.web.templating import templates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/files", tags=["files"])


def _owned(db: Db, attachment_id: str, user_id: str) -> Attachment:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That file no longer exists."))
    return attachment


@router.post("", dependencies=[Depends(require_permission("files.upload"))])
async def upload(
    request: Request,
    db: Db,
    user: RequiredUser,
    file: UploadFile = File(...),
    chat_id: str = "",
) -> Response:
    """Accept one file and return the chip that represents it in the composer.

    The attachment is stored immediately but left unbound: it only joins a
    message when that message is sent. That is what lets a file be removed
    before sending, and what the orphan sweep later cleans up.
    """
    payload = await file.read()

    try:
        attachment = files_service.store(
            db,
            user_id=user.id,
            chat_id=chat_id or None,
            payload=payload,
            filename=file.filename or "file",
        )
    except files_service.FileError as exc:
        # 200 with an error chip rather than a 4xx: htmx swaps the response
        # body either way, and an error the user can read beats a silent
        # failure in the console.
        return templates.TemplateResponse(
            request,
            "chat/_attachment_error.html",
            {"request": request, "filename": file.filename or "file", "error": str(exc)},
        )

    return templates.TemplateResponse(
        request,
        "chat/_attachment_chip.html",
        {"request": request, "attachment": attachment},
    )


@router.post("/link", dependencies=[Depends(require_permission("files.upload"))])
async def attach_link(
    request: Request, db: Db, user: RequiredUser, url: str = Form(""), chat_id: str = Form("")
) -> Response:
    """Fetch a web page and attach its text.

    The page is reduced to text here and stored, rather than being fetched again
    when the message is sent: the same rule as PDF extraction. A reply must not
    change because a page was edited between composing and sending.
    """
    config = settings_store.search(db)
    try:
        page = await fetch(url, allow_private=bool(config.get("allow_private_fetch")))
    except FetchError as exc:
        return templates.TemplateResponse(
            request,
            "chat/_attachment_error.html",
            {"request": request, "filename": url[:80] or "link", "error": exc.message},
        )

    attachment = files_service.store_text(
        db,
        user_id=user.id,
        chat_id=chat_id or None,
        filename=f"{page.title[:120] or 'page'}.txt",
        text=page.text,
        truncated=page.truncated,
        source_note=page.url,
    )
    return templates.TemplateResponse(
        request, "chat/_attachment_chip.html", {"request": request, "attachment": attachment}
    )


@router.post("/from-knowledge", dependencies=[Depends(require_permission("files.upload"))])
async def attach_from_knowledge(
    request: Request, db: Db, user: RequiredUser, document_id: str = Form(""),
    chat_id: str = Form(""), model_id: str = Form(""),
) -> Response:
    """Attach a library document to the message being composed.

    The document is **copied**, not referenced. History must not change under a
    conversation because a document was later edited or deleted -- the same
    reason a PDF's text is extracted once at upload rather than per request.
    """
    group = data_groups.for_composer(db, user, chat_id, model_id)
    document = documents_service.get(db, document_id, user, group)
    if document is None:
        return templates.TemplateResponse(
            request,
            "chat/_attachment_error.html",
            {
                "request": request,
                "filename": "document",
                "error": t("That document is not available."),
            },
        )

    attachment = files_service.copy_document(
        db, user_id=user.id, chat_id=chat_id or None, document=document
    )
    return templates.TemplateResponse(
        request, "chat/_attachment_chip.html", {"request": request, "attachment": attachment}
    )


def _chip(request: Request, attachment: Attachment) -> Response:
    return templates.TemplateResponse(
        request, "chat/_attachment_chip.html", {"request": request, "attachment": attachment}
    )


def _not_available(request: Request, what: str) -> Response:
    return templates.TemplateResponse(
        request,
        "chat/_attachment_error.html",
        {"request": request, "filename": what, "error": f"That {what} is not available."},
    )


@router.post("/from-note", dependencies=[Depends(require_permission("files.upload"))])
async def attach_from_note(
    request: Request,
    db: Db,
    user: RequiredUser,
    note_id: str = Form(""),
    chat_id: str = Form(""),
    model_id: str = Form(""),
) -> Response:
    """Attach a note the model wrote earlier.

    A copy, like every other attach path: a note is edited far more often than a
    document, and a transcript that changes underneath itself because somebody
    tidied a note later is the thing all of this is arranged to prevent.
    """
    note = notes_service.get(
        db, note_id, user, data_groups.for_composer(db, user, chat_id, model_id)
    )
    if note is None:
        return _not_available(request, "note")

    return _chip(
        request,
        files_service.store_text(
            db,
            user_id=user.id,
            chat_id=chat_id or None,
            filename=f"{note.title or 'note'}.txt",
            text=note.body,
            source_path=note.title or "",
            source_label="Note",
        ),
    )


@router.post("/from-scratch", dependencies=[Depends(require_permission("files.upload"))])
async def attach_from_scratch(
    request: Request, db: Db, user: RequiredUser, chat_id: str = Form("")
) -> Response:
    """Attach this chat's scratch document.

    A copy, like every other attach path, and here the reason is at its
    sharpest: the pad goes on being written after the message is sent, by the
    person and by the model, and a transcript that changed underneath itself
    every time either of them typed would be no record at all.
    """
    from lembas.services import scratch as scratch_service

    chat = db.get(Chat, chat_id) if chat_id else None
    if chat is None or chat.user_id != user.id:
        return _not_available(request, "scratch document")

    doc = scratch_service.get(db, chat)
    if doc is None or not (doc.body or "").strip():
        return _not_available(request, "scratch document")

    return _chip(
        request,
        files_service.store_text(
            db,
            user_id=user.id,
            chat_id=chat.id,
            filename=f"{doc.title or 'scratch'}.md",
            text=doc.body,
            source_path=doc.title or "Scratch",
            source_label="Scratch",
        ),
    )


@router.post("/from-skill", dependencies=[Depends(require_permission("files.upload"))])
async def attach_from_skill(
    request: Request,
    db: Db,
    user: RequiredUser,
    skill_id: str = Form(""),
    chat_id: str = Form(""),
    model_id: str = Form(""),
) -> Response:
    """Hand a skill over directly, rather than hoping the model fetches it.

    The index of enabled skills is already in the harness and `skill_view` pulls
    a body on demand -- but only if the model decides to. `@` is the reader
    saying "use this one", which is a different act and deserves a way to say it.
    """
    skill = skills_service.get(
        db, skill_id, user, data_groups.for_composer(db, user, chat_id, model_id)
    )
    if skill is None:
        return _not_available(request, "skill")

    return _chip(
        request,
        files_service.store_text(
            db,
            user_id=user.id,
            chat_id=chat_id or None,
            filename=f"{skill.name}.md",
            text=skill.body,
            source_path=skill.name,
            source_label="Skill",
        ),
    )


@router.post("/from-attachment", dependencies=[Depends(require_permission("files.upload"))])
async def attach_from_attachment(
    request: Request,
    db: Db,
    user: RequiredUser,
    attachment_id: str = Form(""),
    chat_id: str = Form(""),
    model_id: str = Form(""),
) -> Response:
    """Point at something already in this conversation, without uploading again.

    Copied rather than referenced, like everything else here -- an attachment
    belongs to the message it was sent with, and two messages sharing one row
    would make deleting either of them a question rather than an answer.
    """
    original = db.get(Attachment, attachment_id)
    if original is None or original.user_id != user.id:
        return _not_available(request, "attachment")
    # Another chat's attachment is that chat's data, in that chat's group.
    source = db.get(Chat, original.chat_id) if original.chat_id else None
    if source is not None and data_groups.for_chat(db, source) != data_groups.for_composer(
        db, user, chat_id, model_id
    ):
        return _not_available(request, "attachment")

    return _chip(
        request,
        files_service.copy_attachment(
            db, user_id=user.id, chat_id=chat_id or None, attachment=original
        ),
    )


@router.get("/knowledge-picker", dependencies=[Depends(require_permission("files.upload"))])
async def knowledge_picker(
    request: Request,
    db: Db,
    user: RequiredUser,
    q: str = "",
    chat_id: str = "",
    model_id: str = "",
) -> Response:
    """The list of documents shown by the composer's Knowledge option.

    Only the composer's own data group: see `data_groups.for_composer`.
    """
    group = data_groups.for_composer(db, user, chat_id, model_id)
    if q.strip():
        found = documents_service.search(db, user, q, limit=20, group=group)
    else:
        found = list(
            db.scalars(
                documents_service.visible(db, user, group=group)
                .order_by(Document.created_at.desc())
                .limit(20)
            )
        )
    return templates.TemplateResponse(
        request,
        "chat/_knowledge_picker.html",
        # `user` is read by the template to mark documents shared by someone
        # else; render() would inject it, but this is a fragment.
        {"request": request, "documents": found, "q": q, "chat_id": chat_id, "user": user},
    )


@router.get("/mention-picker", dependencies=[Depends(require_permission("files.upload"))])
async def mention_picker(
    request: Request,
    db: Db,
    user: RequiredUser,
    q: str = "",
    chat_id: str = "",
    model_id: str = "",
) -> Response:
    """What `@` offers from here: the library, this chat's attachments, a URL.

    A device chat's project files are not among them: the composer asks the
    linked device for those beside this, and lists them first. This is a
    keystroke-latency path and it must never wait on a machine.

    Filtered server-side, like the knowledge picker beside it and for the same
    reason: the library is searched with FTS rather than filtered in the
    browser, which is what makes it work at five hundred documents.
    """
    needle = q.strip().lower()

    documents: list = []
    notes: list = []
    skills: list = []
    bases: list = []
    if permissions.has(db, user, "library.use"):
        # The library half offers only the composer's own data group: whatever is
        # picked becomes part of the conversation and goes to the chat's model.
        group = data_groups.for_composer(db, user, chat_id, model_id)
        if needle:
            documents = documents_service.search(db, user, q, limit=10, group=group)
            notes = notes_service.search(db, user, q, limit=5, group=group)
            skills = skills_service.search(db, user, q, limit=5, group=group)
        else:
            documents = list(
                db.scalars(
                    documents_service.visible(db, user, group=group)
                    .order_by(Document.created_at.desc())
                    .limit(10)
                )
            )
            notes = list(
                db.scalars(
                    notes_service.visible(db, user, group)
                    .order_by(Note.updated_at.desc())
                    .limit(5)
                )
            )
            skills = list(db.scalars(skills_service.visible(db, user, group).limit(5)))

        # A whole base is a *reference*, not a copy: attaching one scopes the
        # chat to it and the model searches inside it. Dumping the contents of
        # a folder of contracts into the window would be the wrong shape
        # entirely, and `Chat.knowledge_bases` already means exactly this.
        # Only in an existing chat, because there is nothing to attach it to
        # before one exists -- the same reason project files are absent there.
        if chat_id:
            bases = [
                base
                for base in db.scalars(
                    documents_service.visible_bases(db, user, group).order_by(
                        KnowledgeBase.name
                    )
                )
                if not needle or needle in base.name.lower()
            ][:5]

    # A URL typed after `@` is a page to read, not a name to look up. The
    # fetcher, its SSRF guard and its HTML-to-text already live behind
    # `/api/files/link`; this only offers it.
    website = q.strip() if q.strip().lower().startswith(("http://", "https://")) else ""

    attachments: list = []
    if chat_id and needle:
        attachments = list(
            db.scalars(
                select(Attachment)
                .where(
                    Attachment.user_id == user.id,
                    Attachment.chat_id == chat_id,
                    Attachment.message_id.is_not(None),
                )
                .order_by(Attachment.created_at.desc())
                .limit(20)
            )
        )
        attachments = [a for a in attachments if needle in a.filename.lower()][:5]

    return templates.TemplateResponse(
        request,
        "chat/_mention_picker.html",
        {
            "request": request,
            "user": user,
            "documents": documents,
            "notes": notes,
            "skills": skills,
            "bases": bases,
            "attachments": attachments,
            "website": website,
            "q": q,
            "chat_id": chat_id,
        },
    )


@router.delete("/{attachment_id}")
async def remove(db: Db, user: RequiredUser, attachment_id: str) -> Response:
    """Detach a file before it has been sent."""
    attachment = _owned(db, attachment_id, user.id)
    if attachment.message_id is not None:
        # Deleting it now would rewrite a conversation that has already been
        # sent to a model and read by the user.
        raise HTTPException(
            status.HTTP_409_CONFLICT, t("That file is part of a sent message.")
        )
    files_service.delete(db, attachment)
    return Response(status_code=status.HTTP_200_OK)


@router.get("/{attachment_id}/content")
async def content(db: Db, user: RequiredUser, attachment_id: str) -> Response:
    """Serve an attachment back to its owner."""
    attachment = _owned(db, attachment_id, user.id)
    path = files_service.stored_path(attachment.stored_name)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That file is no longer on disk."))

    # inline for images so they render in the thread; attachment for everything
    # else so a text/html upload can never be executed in this origin.
    disposition = "inline" if attachment.is_image else "attachment"
    return FileResponse(
        path,
        media_type=attachment.media_type if attachment.is_image else "application/octet-stream",
        headers={
            "Content-Disposition": files_service.content_disposition(
                disposition, attachment.filename
            ),
            "Cache-Control": "private, max-age=604800",
            # Belt and braces: even for images, never let a browser sniff its
            # way to treating the bytes as something executable.
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{attachment_id}/text")
async def extracted_text(db: Db, user: RequiredUser, attachment_id: str) -> Response:
    """The text a document contributed to the prompt.

    Worth being able to see: a PDF that extracted badly explains a strange
    reply, and there is otherwise no way to tell what the model was given.
    """
    attachment = _owned(db, attachment_id, user.id)
    return Response(
        attachment.extracted_text or attachment.extraction_error,
        media_type="text/plain; charset=utf-8",
    )
