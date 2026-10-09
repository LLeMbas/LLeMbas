"""What happens to a file between the upload and the model, and how it is found.

Two halves on one page because they are two ends of the same pipeline: what gets
extracted decides what there is to search, and the search settings decide what
becomes of it. Splitting them would mean an administrator setting a 300-page PDF
limit on one screen and wondering on another why half a book is missing from the
index.

Every save drops `files.forget()`, and this is the only module that calls it —
the same discipline `admin_branding` has with the branding snapshot, and for the
same reason: a process-level cache whose save does not drop it is a setting that
takes effect at the next restart.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from lembas.api.deps import AdminUser, Db
from lembas.db.models import Connection, Model
from lembas.services import files as files_service
from lembas.services import settings_store
from lembas.services.library import indexing
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/extraction", tags=["admin-extraction"])


def _embedding_models(db: Db) -> list[Model]:
    """Models an administrator has marked as producing embeddings.

    Filtered rather than listed in full, the same shape `/admin/images` uses for
    its reviewer: a chat model in this picker is a setting that looks configured
    and fails on the first request, which is the shape of failure this codebase
    keeps cataloguing.
    """
    return [
        model
        for model in db.scalars(
            select(Model).join(Connection).order_by(Model.position, Model.model_id)
        )
        if (model.capabilities_json or {}).get("embeddings")
        # Anthropic's API has no embeddings, whatever a box says.
        and not model.connection.is_anthropic
    ]


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


@router.get("")
async def extraction_page(request: Request, db: Db, user: AdminUser, saved: str = ""):
    values = settings_store.extraction(db)
    models = _embedding_models(db)
    return render(
        request,
        "admin/extraction.html",
        {
            "values": values,
            "extensions_text": "\n".join(values.get("extra_text_extensions") or []),
            "models": models,
            # A model that was chosen and has since lost its flag, or its
            # connection. Named rather than silently dropped from the picker:
            # a setting that vanishes is one nobody can tell from one that was
            # never made.
            "missing_model": (
                values["embedding_model_id"]
                if values["embedding_model_id"]
                and values["embedding_model_id"] not in {m.model_id for m in models}
                else ""
            ),
            "ready": indexing.enabled(db),
            "counts": indexing.counts(db),
            "progress": indexing.progress(),
            "saved": saved,
        },
    )


@router.post("")
async def save_extraction(
    db: Db,
    user: AdminUser,
    max_upload_mb: int = Form(20),
    max_image_edge: int = Form(1400),
    jpeg_quality: int = Form(85),
    max_pdf_pages: int = Form(300),
    max_extracted_chars: int = Form(120_000),
    orphan_hours: int = Form(24),
    extra_text_extensions: str = Form(""),
    reject_unreadable_pdf: bool = Form(False),
) -> Response:
    settings_store.update(
        db,
        {
            # Clamped here as well as on read, for the reason the agent settings
            # give: a number with no bound is a way to break the instance from
            # a form.
            "max_upload_mb": min(max(max_upload_mb, 1), 512),
            "max_image_edge": min(max(max_image_edge, 128), 8192),
            "jpeg_quality": min(max(jpeg_quality, 30), 100),
            "max_pdf_pages": min(max(max_pdf_pages, 1), 5000),
            "max_extracted_chars": min(max(max_extracted_chars, 1000), 5_000_000),
            "orphan_hours": min(max(orphan_hours, 1), 8760),
            "extra_text_extensions": _lines(extra_text_extensions),
            "reject_unreadable_pdf": reject_unreadable_pdf,
        },
        key=settings_store.EXTRACTION,
    )
    files_service.forget()
    log.info("extraction settings changed by %s", user.email)
    return RedirectResponse(
        f"/admin/extraction?saved={quote(t('Extraction saved.'))}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/search")
async def save_search(
    db: Db,
    user: AdminUser,
    embedding_model_id: str = Form(""),
    chunk_chars: int = Form(1200),
    chunk_overlap: int = Form(150),
    embed_batch: int = Form(16),
) -> Response:
    """The semantic half.

    Its own form and its own route, because the two halves have different
    consequences: changing a chunk size invalidates every vector already stored,
    and changing an upload limit does not. Keeping them apart is what lets the
    page say so beside the control that does it.
    """
    before = settings_store.extraction(db)
    settings_store.update(
        db,
        {
            "embedding_model_id": embedding_model_id.strip()[:300],
            "chunk_chars": min(max(chunk_chars, 200), 8000),
            "chunk_overlap": max(chunk_overlap, 0),
            "embed_batch": min(max(embed_batch, 1), 256),
        },
        key=settings_store.EXTRACTION,
    )
    files_service.forget()

    # Changing the model changes the vector space, so what is stored stops
    # meaning anything against a new query. Nothing is deleted -- the scorer
    # already skips a width that does not match the query's, so a stale index is
    # ignored rather than trusted -- but a rebuild is what makes it useful
    # again, and offering it here is cheaper than leaving somebody to notice.
    changed = before["embedding_model_id"] != embedding_model_id.strip()
    message = t("Search saved.")
    if changed and embedding_model_id.strip():
        message = t("Search saved. Rebuild the index to use the new model.")
    log.info("embedding model set to %r by %s", embedding_model_id, user.email)
    return RedirectResponse(
        f"/admin/extraction?saved={quote(message)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/rebuild")
async def rebuild(request: Request, db: Db, user: AdminUser) -> Response:
    """Start a rebuild, and answer with the progress card.

    A background task rather than a request that waits: embedding a library of a
    few thousand records is minutes of HTTP round trips, and a page that hangs
    for that long is one somebody reloads, which starts a second one.
    """
    started = indexing.start_rebuild()
    if started:
        log.info("index rebuild started by %s", user.email)
    return render(
        request,
        "admin/_index_progress.html",
        {"progress": indexing.progress(), "counts": indexing.counts(db), "ready": True},
    )


@router.get("/progress")
async def rebuild_progress(request: Request, db: Db, user: AdminUser) -> Response:
    """Polled while a rebuild runs. Stops polling itself when it finishes.

    Polled rather than streamed for the reason `/api/chats/unread` is: this is
    one small fragment on one page, and an SSE stream for it would be a second
    streaming path to keep correct.
    """
    return render(
        request,
        "admin/_index_progress.html",
        {
            "progress": indexing.progress(),
            "counts": indexing.counts(db),
            "ready": indexing.enabled(db),
        },
    )
