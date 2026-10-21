"""The library: knowledge documents, notes, skills — and memory in settings.

List-plus-detail throughout, the same shape as the model admin: compact rows
with search and pagination, and a full form on its own page. A library is
expected to run to hundreds of items, and a page that renders a form per row is
unusable at that size.

Every read goes through ``services.sharing.visible_to`` and every write through
``owner_id``. Sharing grants reading only -- two people editing one note with no
history and no merge is worse than the inconvenience of copying it.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.api.pages import sidebar_context
from lembas.db.models import (
    AUTHOR_USER,
    Document,
    KnowledgeBase,
    Note,
    Skill,
    SkillRevision,
    User,
)
from lembas.security import permissions
from lembas.services import data_groups, settings_store, sharing
from lembas.services import files as files_service
from lembas.services.fetch import FetchError, fetch
from lembas.services.library import documents as documents_service
from lembas.services.library import memories as memories_service
from lembas.services.library import notes as notes_service
from lembas.services.library import retrieval
from lembas.services.library import skills as skills_service
from lembas.services.markdown import render_markdown
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_permission("library.use"))], tags=["library"])

PAGE_SIZE = 30


def _page(db: DBSession, query, page: int):
    """One page of a visibility-filtered query, plus what the pager needs."""
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 1), pages)
    rows = list(db.scalars(query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)))
    return rows, {"page": page, "pages": pages, "total": total}


def _groups(db: DBSession, user: User) -> dict:
    """What `library/_group.html` needs on every library page.

    `several_groups` false is the ordinary instance, and then nothing about
    groups is rendered anywhere in the library.
    """
    usable = data_groups.usable(db, user)
    return {
        "several_groups": len(usable) > 1,
        "data_groups": usable,
        "group_names": {group.id: group.name for group in data_groups.all_groups(db)},
        "may_move_groups": data_groups.may_manage(db, user),
    }


def _chosen_group(db: DBSession, user: User, value: str) -> str:
    """A submitted group, if this person may use it; otherwise the default."""
    value = (value or "").strip()
    if value and data_groups.may_use(db, user, value):
        return value
    return data_groups.DEFAULT_GROUP


def _move(db: DBSession, user: User, row, value) -> None:
    """Move a record into another group, when that was asked and is allowed.

    `None` is a form that did not carry the field -- a single-group instance,
    or somebody without `data.manage` -- and leaves the record where it is.
    """
    if value is None or not data_groups.may_manage(db, user):
        return
    wanted = str(value).strip()
    if wanted and data_groups.may_use(db, user, wanted):
        row.data_group_id = wanted


def _group_filter(query, model, group: str):
    return query.where(data_groups.condition(model, group)) if group else query


def _shared_context(db: DBSession, user: User, resource, kind: str) -> dict:
    """What the share placeholder needs, which is now three facts.

    The panel itself is fetched from `api/sharing.py`, so the names, the search
    and the grants are no longer built here -- and neither is a query for every
    account on the instance on every detail page.
    """
    return {
        "can_share": permissions.has(db, user, "library.share"),
        "is_owner": resource.owner_id == user.id,
        "share_kind": kind,
        "share_id": resource.id,
    }


# --- Shell -------------------------------------------------------------------
@router.get("/library")
async def library_home(user: RequiredUser):
    return RedirectResponse("/library/knowledge", status_code=status.HTTP_303_SEE_OTHER)


# --- Knowledge ---------------------------------------------------------------
# Route order matters: /library/knowledge/document/{id} must be registered
# before /library/knowledge/{base_id}, or "document" is parsed as a base id.
# FastAPI matches in registration order and this has bitten before.
@router.get("/library/knowledge")
async def knowledge_list(
    request: Request,
    db: Db,
    user: RequiredUser,
    error: str = "",
    shared: bool = False,
    group: str = "",
):
    """The bases, not the documents. A library is a set of places first.

    `shared=1` narrows to bases other people have given this reader — the same
    filter the notes and skills lists carry, and the one that makes "what have
    people shared with me?" a question with an answer.
    """
    query = (
        select(KnowledgeBase).where(sharing.only_shared(KnowledgeBase, user))
        if shared
        else documents_service.visible_bases(db, user)
    )
    query = _group_filter(query, KnowledgeBase, group)
    bases = list(db.scalars(query.order_by(KnowledgeBase.name)))
    counts = {
        base.id: db.scalar(
            select(func.count()).select_from(Document).where(Document.base_id == base.id)
        )
        or 0
        for base in bases
    }
    return render(
        request,
        "library/knowledge.html",
        {
            "section": "knowledge",
            "bases": bases,
            "counts": counts,
            "shared": shared,
            "error": error,
            "group": group,
            **_groups(db, user),
            **sidebar_context(db, user),
        },
    )


@router.post("/api/library/bases")
async def create_base(
    db: Db,
    user: RequiredUser,
    name: str = Form(""),
    description: str = Form(""),
    data_group_id: str = Form(""),
) -> Response:
    try:
        base = documents_service.create_base(
            db,
            owner=user,
            name=name,
            description=description,
            group=_chosen_group(db, user, data_group_id),
        )
    except ValueError as exc:
        return RedirectResponse(
            f"/library/knowledge?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        f"/library/knowledge/{base.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/library/knowledge/document/{document_id}")
async def knowledge_detail(request: Request, db: Db, user: RequiredUser, document_id: str):
    document = documents_service.get(db, document_id, user)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That document is not available."))
    return render(
        request,
        "library/knowledge_detail.html",
        {
            "section": "knowledge",
            "document": document,
            "is_owner": sharing.can_write(document, user),
            # Only bases this person owns: moving a document into one they can
            # merely read would hand it to that base's owner.
            "user_bases": list(
                db.scalars(
                    select(KnowledgeBase)
                    .where(KnowledgeBase.owner_id == user.id)
                    .order_by(KnowledgeBase.name)
                )
            ),
            **_groups(db, user),
            **sidebar_context(db, user),
        },
    )


@router.get("/library/knowledge/{base_id}")
async def base_detail(
    request: Request, db: Db, user: RequiredUser, base_id: str, q: str = "", page: int = 1
):
    base = documents_service.get_base(db, base_id, user)
    if base is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That knowledge base is not available."))

    if q.strip():
        # The reader's search box gets the same recall a model's does. `None`
        # when nothing is configured, which is the keyword search unchanged.
        vector = await retrieval.embed_query(db, q)
        rows = documents_service.search(
            db, user, q, limit=PAGE_SIZE, base_ids=[base.id], vector=vector
        )
        pager = {"page": 1, "pages": 1, "total": len(rows)}
    else:
        rows, pager = _page(
            db,
            documents_service.visible(db, user, base_ids=[base.id]).order_by(
                Document.created_at.desc()
            ),
            page,
        )
    return render(
        request,
        "library/base_detail.html",
        {
            "section": "knowledge",
            "base": base,
            "documents": rows,
            "q": q,
            "pager": pager,
            **_shared_context(db, user, base, "base"),
            **_groups(db, user),
            **sidebar_context(db, user),
        },
    )


@router.post("/api/library/bases/{base_id}")
async def update_base(request: Request, db: Db, user: RequiredUser, base_id: str) -> Response:
    base = documents_service.get_base(db, base_id, user)
    if base is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That knowledge base is not available."))
    if not sharing.can_write(base, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("That base is not yours to change."))

    form = await request.form()
    name = " ".join(str(form.get("name", "")).split())[:200]
    if name:
        base.name = name
    base.description = str(form.get("description", "")).strip()[:2000]
    # A base moves with every document in it: they have no group of their own.
    _move(db, user, base, form.get("data_group_id"))
    db.commit()
    return RedirectResponse(
        f"/library/knowledge/{base.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/library/bases/{base_id}/delete")
async def delete_base(db: Db, user: RequiredUser, base_id: str) -> Response:
    base = documents_service.get_base(db, base_id, user)
    if base is None or not sharing.can_write(base, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That knowledge base is not available."))
    documents_service.delete_base(db, base)
    return RedirectResponse("/library/knowledge", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/library/documents")
async def upload_document(
    db: Db,
    user: RequiredUser,
    file: UploadFile = File(...),
    title: str = Form(""),
    base_id: str = Form(""),
) -> Response:
    base = documents_service.get_base(db, base_id, user) if base_id else None
    if base is not None and not sharing.can_write(base, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("That base is not yours to add to."))

    payload = await file.read(files_service.limits().max_upload_bytes + 1)
    try:
        document = documents_service.store_upload(
            db,
            owner=user,
            payload=payload,
            filename=file.filename or "file",
            title=title,
            base=base,
        )
    except files_service.FileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return RedirectResponse(
        f"/library/knowledge/{document.base_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/library/documents/link")
async def save_link(
    db: Db, user: RequiredUser, url: str = Form(...), base_id: str = Form("")
) -> Response:
    base = documents_service.get_base(db, base_id, user) if base_id else None
    if base is not None and not sharing.can_write(base, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("That base is not yours to add to."))

    config = settings_store.search(db)
    try:
        page = await fetch(url, allow_private=bool(config.get("allow_private_fetch")))
    except FetchError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message) from exc
    document = documents_service.store_page(db, owner=user, page=page, base=base)
    return RedirectResponse(
        f"/library/knowledge/{document.base_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/library/documents/{document_id}")
async def update_document(
    request: Request, db: Db, user: RequiredUser, document_id: str
) -> Response:
    document = documents_service.get(db, document_id, user)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That document is not available."))
    if not sharing.can_write(document, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("That document is not yours to change."))

    form = await request.form()
    document.title = str(form.get("title", document.title)).strip()[:300] or document.title
    document.description = str(form.get("description", "")).strip()[:2000]

    # Moving between bases changes who can see it, which is the whole point of
    # bases -- so the destination has to be one this person can write to.
    wanted = str(form.get("base_id", "")).strip()
    if wanted and wanted != document.base_id:
        destination = documents_service.get_base(db, wanted, user)
        current = db.get(KnowledgeBase, document.base_id) if document.base_id else None
        # Into a base in another data group is a move between groups, which
        # changes which providers may read it -- `data.manage`, like any move.
        crosses = current is not None and destination is not None and (
            data_groups.group_of(current) != data_groups.group_of(destination)
        )
        if (
            destination is not None
            and sharing.can_write(destination, user)
            and (not crosses or data_groups.may_manage(db, user))
        ):
            document.base_id = destination.id

    db.commit()
    return RedirectResponse(
        f"/library/knowledge/document/{document.id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/library/documents/{document_id}/delete")
async def delete_document(db: Db, user: RequiredUser, document_id: str) -> Response:
    document = documents_service.get(db, document_id, user)
    if document is None or not sharing.can_write(document, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That document is not available."))
    base_id = document.base_id
    documents_service.delete(db, document)
    return RedirectResponse(
        f"/library/knowledge/{base_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/api/library/documents/{document_id}/content")
async def document_content(db: Db, user: RequiredUser, document_id: str) -> Response:
    """Serve a document's file.

    Non-images go out as attachments with nosniff, exactly as chat attachments
    do: an uploaded .html must not be able to execute in this origin.
    """
    document = documents_service.get(db, document_id, user)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That document is not available."))
    path = documents_service.stored_path(document.stored_name)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That file is no longer on disk."))

    headers = {"X-Content-Type-Options": "nosniff"}
    if not document.is_image:
        headers["Content-Disposition"] = files_service.content_disposition(
            "attachment", document.filename
        )
    return FileResponse(path, media_type=document.media_type, headers=headers)


# --- Notes -------------------------------------------------------------------
@router.get("/library/notes")
async def notes_list(
    request: Request,
    db: Db,
    user: RequiredUser,
    q: str = "",
    page: int = 1,
    shared: bool = False,
    group: str = "",
):
    """`shared=1` narrows to what other people have given this reader.

    A separate view rather than a badge in the mixed list. A badge answers "is
    this mine?" for a row already on screen; the question somebody has is "what
    have people given me?", which a mixed list of two hundred cannot answer.
    Searching inside it is deliberately left out -- the search path returns
    ranked ids and re-filtering them by owner would silently shorten the page.
    """
    if q.strip():
        rows = notes_service.search(
            db,
            user,
            q,
            limit=PAGE_SIZE,
            vector=await retrieval.embed_query(db, q),
            group=group or None,
        )
        pager = {"page": 1, "pages": 1, "total": len(rows)}
    else:
        query = (
            select(Note).where(sharing.only_shared(Note, user))
            if shared
            else notes_service.visible(db, user)
        )
        query = _group_filter(query, Note, group)
        rows, pager = _page(db, query.order_by(Note.updated_at.desc()), page)
    return render(
        request,
        "library/notes.html",
        {
            "section": "notes",
            "notes": rows,
            "q": q,
            "shared": shared,
            "pager": pager,
            "group": group,
            **_groups(db, user),
            **sidebar_context(db, user),
        },
    )


@router.get("/library/notes/new")
async def new_note(request: Request, db: Db, user: RequiredUser, group: str = ""):
    return render(
        request,
        "library/note_detail.html",
        {
            "section": "notes",
            "note": None,
            "group": group,
            **_groups(db, user),
            **sidebar_context(db, user),
        },
    )


@router.get("/library/notes/{note_id}")
async def note_detail(request: Request, db: Db, user: RequiredUser, note_id: str):
    note = notes_service.get(db, note_id, user)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That note is not available."))
    return render(
        request,
        "library/note_detail.html",
        {
            "section": "notes",
            "note": note,
            "body_html": render_markdown(note.body),
            **_shared_context(db, user, note, "note"),
            **_groups(db, user),
            **sidebar_context(db, user),
        },
    )


@router.post("/api/library/notes")
async def create_note(
    db: Db,
    user: RequiredUser,
    title: str = Form(""),
    body: str = Form(""),
    data_group_id: str = Form(""),
) -> Response:
    note = notes_service.create(
        db,
        owner=user,
        title=title,
        body=body,
        author=AUTHOR_USER,
        group=_chosen_group(db, user, data_group_id),
    )
    return RedirectResponse(f"/library/notes/{note.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/library/notes/{note_id}")
async def update_note(request: Request, db: Db, user: RequiredUser, note_id: str) -> Response:
    note = notes_service.get(db, note_id, user)
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That note is not available."))
    if not sharing.can_write(note, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("That note is not yours to change."))

    form = await request.form()
    _move(db, user, note, form.get("data_group_id"))
    notes_service.update(db, note, title=str(form.get("title", "")), body=str(form.get("body", "")))
    return RedirectResponse(f"/library/notes/{note.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/library/notes/{note_id}/delete")
async def delete_note(db: Db, user: RequiredUser, note_id: str) -> Response:
    note = notes_service.get(db, note_id, user)
    if note is None or not sharing.can_write(note, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That note is not available."))
    notes_service.delete(db, note)
    return RedirectResponse("/library/notes", status_code=status.HTTP_303_SEE_OTHER)


# --- Skills ------------------------------------------------------------------
@router.get("/library/skills")
async def skills_list(
    request: Request,
    db: Db,
    user: RequiredUser,
    q: str = "",
    page: int = 1,
    shared: bool = False,
    group: str = "",
):
    """`shared=1` narrows to what other people have given this reader.

    A separate view rather than a badge in the mixed list. A badge answers "is
    this mine?" for a row already on screen; the question somebody has is "what
    have people given me?", which a mixed list of two hundred cannot answer.
    Searching inside it is deliberately left out -- the search path returns
    ranked ids and re-filtering them by owner would silently shorten the page.
    """
    if q.strip():
        rows = skills_service.search(
            db,
            user,
            q,
            limit=PAGE_SIZE,
            vector=await retrieval.embed_query(db, q),
            group=group or None,
        )
        pager = {"page": 1, "pages": 1, "total": len(rows)}
    else:
        query = (
            select(Skill).where(sharing.only_shared(Skill, user))
            if shared
            else skills_service.visible(db, user)
        )
        query = _group_filter(query, Skill, group)
        rows, pager = _page(db, query.order_by(Skill.name), page)
    return render(
        request,
        "library/skills.html",
        {
            "section": "skills",
            "skills": rows,
            "q": q,
            "shared": shared,
            "pager": pager,
            "group": group,
            **_groups(db, user),
            **sidebar_context(db, user),
        },
    )


@router.get("/library/skills/new")
async def new_skill(request: Request, db: Db, user: RequiredUser, group: str = ""):
    return render(
        request,
        "library/skill_detail.html",
        {
            "section": "skills",
            "skill": None,
            "group": group,
            **_groups(db, user),
            **sidebar_context(db, user),
        },
    )


@router.get("/library/skills/{skill_id}")
async def skill_detail(request: Request, db: Db, user: RequiredUser, skill_id: str):
    skill = skills_service.get(db, skill_id, user)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That skill is not available."))
    return render(
        request,
        "library/skill_detail.html",
        {
            "section": "skills",
            "skill": skill,
            "revisions": skill.revisions,
            **_shared_context(db, user, skill, "skill"),
            **_groups(db, user),
            **sidebar_context(db, user),
        },
    )


@router.post("/api/library/skills")
async def create_skill(
    db: Db,
    user: RequiredUser,
    name: str = Form(""),
    description: str = Form(""),
    body: str = Form(""),
    data_group_id: str = Form(""),
) -> Response:
    try:
        skill = skills_service.create(
            db,
            owner=user,
            name=name,
            description=description,
            body=body,
            author=AUTHOR_USER,
            group=_chosen_group(db, user, data_group_id),
        )
    except skills_service.SkillError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.translated()) from exc
    return RedirectResponse(f"/library/skills/{skill.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/library/skills/{skill_id}")
async def update_skill(request: Request, db: Db, user: RequiredUser, skill_id: str) -> Response:
    skill = skills_service.get(db, skill_id, user)
    if skill is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That skill is not available."))
    if not sharing.can_write(skill, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("That skill is not yours to change."))

    form = await request.form()
    _move(db, user, skill, form.get("data_group_id"))
    skills_service.update(
        db,
        skill,
        description=str(form.get("description", "")),
        body=str(form.get("body", "")),
        enabled="enabled" in form,
        author=AUTHOR_USER,
        note="edited by hand",
    )
    return RedirectResponse(f"/library/skills/{skill.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/library/skills/{skill_id}/revert/{revision_id}")
async def revert_skill(
    db: Db, user: RequiredUser, skill_id: str, revision_id: str
) -> Response:
    skill = skills_service.get(db, skill_id, user)
    if skill is None or not sharing.can_write(skill, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That skill is not available."))
    revision = db.get(SkillRevision, revision_id)
    if revision is None or revision.skill_id != skill.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That revision no longer exists."))

    skills_service.revert(db, skill, revision, author=AUTHOR_USER)
    return RedirectResponse(f"/library/skills/{skill.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/library/skills/{skill_id}/delete")
async def delete_skill(db: Db, user: RequiredUser, skill_id: str) -> Response:
    skill = skills_service.get(db, skill_id, user)
    if skill is None or not sharing.can_write(skill, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That skill is not available."))
    skills_service.delete(db, skill)
    return RedirectResponse("/library/skills", status_code=status.HTTP_303_SEE_OTHER)


# --- Memory ------------------------------------------------------------------
# Lives in Settings rather than in the library: it is a set of short facts about
# the reader, not content they collected.
def _said(exc: ValueError) -> str:
    """A refusal in the reader's language; any other ValueError as it came."""
    if isinstance(exc, memories_service.Refused):
        return exc.translated()
    return str(exc)


@router.post("/api/library/memories")
async def add_memory(
    db: Db, user: RequiredUser, content: str = Form(""), data_group_id: str = Form("")
) -> Response:
    try:
        memories_service.add(
            db,
            owner=user,
            content=content,
            author=AUTHOR_USER,
            group=_chosen_group(db, user, data_group_id),
        )
    except ValueError as exc:
        return RedirectResponse(
            f"/settings?error={quote(_said(exc))}", status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        f"/settings?saved={quote(t('Memory added.'))}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/library/memories/{memory_id}")
async def update_memory(
    db: Db, user: RequiredUser, memory_id: str, content: str = Form("")
) -> Response:
    memory = memories_service.get(db, memory_id, user)
    if memory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That memory no longer exists."))
    try:
        memories_service.update(db, memory, content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _said(exc)) from exc
    return RedirectResponse(
        f"/settings?saved={quote(t('Memory updated.'))}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/library/memories/{memory_id}/delete")
async def delete_memory(db: Db, user: RequiredUser, memory_id: str) -> Response:
    memory = memories_service.get(db, memory_id, user)
    if memory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That memory no longer exists."))
    memories_service.delete(db, memory)
    return RedirectResponse(
        f"/settings?saved={quote(t('Memory removed.'))}", status_code=status.HTTP_303_SEE_OTHER
    )


