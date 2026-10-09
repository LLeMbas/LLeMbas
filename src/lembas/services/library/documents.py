"""The knowledge library: documents a person has collected.

Ingestion is deliberately **not** written here. A knowledge document and a chat
attachment are the same processing problem -- sniff the bytes, downscale the
image, extract the PDF once -- so both go through
``services.files.prepare``. Keeping one pipeline is what guarantees the same
PDF produces the same text whichever way it arrived, and it is why `Document`
carries the same content columns as `Attachment`.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.config import settings
from lembas.db.models import (
    CHUNK_DOCUMENT,
    DEFAULT_GROUP,
    SOURCE_LINK,
    SOURCE_UPLOAD,
    Document,
    KnowledgeBase,
    User,
)
from lembas.services import files as files_service
from lembas.services import sharing
from lembas.services.fetch import Fetched
from lembas.services.library import retrieval
from lembas.web.i18n import t

log = logging.getLogger(__name__)

INDEX = "documents_fts"

# What a first base is called when one has to be invented -- on the first
# upload, or for documents that predate bases existing.
DEFAULT_BASE_NAME = "My documents"

# How much of a document's text a search result carries back to the model. A
# whole 100-page extract would swallow the context window; this is enough to
# judge relevance and to answer from, and `knowledge_get` fetches the rest.
SNIPPET_CHARS = 1200


def library_dir() -> Path:
    """Where library files live, beside but separate from chat attachments."""
    path = settings.uploads_dir / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stored_path(stored_name: str) -> Path | None:
    """Resolve a stored name, refusing anything outside the library directory.

    The same check as ``services.files.stored_path``, against a different root.
    """
    if not stored_name or "/" in stored_name or "\\" in stored_name or stored_name.startswith("."):
        return None
    base = library_dir().resolve()
    path = (base / stored_name).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path if path.is_file() else None


# --- Bases -------------------------------------------------------------------
def visible_bases(db: DBSession, user: User | None, group: str | None = None):
    """Bases this user may see; `group` narrows to one data group, for a model."""
    return select(KnowledgeBase).where(sharing.visible_to(KnowledgeBase, user, group))


def get_base(
    db: DBSession, base_id: str, user: User | None, group: str | None = None
) -> KnowledgeBase | None:
    base = db.get(KnowledgeBase, base_id)
    if base is None or not sharing.can_read(db, base, user, group):
        return None
    return base


def create_base(
    db: DBSession,
    *,
    owner: User,
    name: str,
    description: str = "",
    group: str = DEFAULT_GROUP,
) -> KnowledgeBase:
    name = " ".join((name or "").split())[:200] or DEFAULT_BASE_NAME
    existing = db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.owner_id == owner.id, KnowledgeBase.name == name
        )
    )
    if existing is not None:
        # Unique per person across every data group: the constraint is
        # `(owner_id, name)` and cannot be changed without rebuilding the table.
        if (existing.data_group_id or DEFAULT_GROUP) != (group or DEFAULT_GROUP):
            raise ValueError(
                t(
                    "You already have a knowledge base called %(name)s in another "
                    "data group. Names are unique across your groups.",
                    name=repr(name),
                )
            )
        raise ValueError(t("You already have a knowledge base called %(name)s.", name=repr(name)))

    base = KnowledgeBase(
        owner_id=owner.id,
        data_group_id=group or DEFAULT_GROUP,
        name=name,
        description=description.strip()[:2000],
    )
    db.add(base)
    db.commit()
    return base


def default_base(db: DBSession, owner: User, group: str = DEFAULT_GROUP) -> KnowledgeBase:
    """The base a document goes into when none was chosen, in one data group.

    Made on demand rather than at registration, so an account that never uses
    the library never grows an empty one. Outside the default group it is named
    after the group, because a base name is unique per person across every group
    and two called "My documents" cannot both exist.
    """
    from lembas.services import data_groups

    group = group or DEFAULT_GROUP
    base = db.scalar(
        select(KnowledgeBase)
        .where(
            KnowledgeBase.owner_id == owner.id,
            data_groups.condition(KnowledgeBase, group),
        )
        .order_by(KnowledgeBase.created_at)
    )
    if base is not None:
        return base
    name = DEFAULT_BASE_NAME
    if group != DEFAULT_GROUP:
        name = f"{DEFAULT_BASE_NAME} — {data_groups.name_of(db, group)}"[:190]
    taken = set(
        db.scalars(select(KnowledgeBase.name).where(KnowledgeBase.owner_id == owner.id))
    )
    wanted, counter = name, 2
    while wanted in taken:
        wanted = f"{name} ({counter})"
        counter += 1
    base = KnowledgeBase(owner_id=owner.id, name=wanted, data_group_id=group)
    db.add(base)
    db.commit()
    return base


def delete_base(db: DBSession, base: KnowledgeBase) -> None:
    """Delete a base and everything in it.

    The documents go too -- a base is a place, not a label, and leaving its
    contents behind with nowhere to live would need an "unfiled" concept that
    exists only to hold the wreckage of deletes.
    """
    for document in list(base.documents):
        path = stored_path(document.stored_name)
        if path is not None:
            path.unlink(missing_ok=True)
    sharing.forget_resource(db, base)
    db.delete(base)
    db.commit()


def sweep_unfiled(db: DBSession) -> int:
    """File documents that predate knowledge bases into their owner's default.

    `Document.base_id` is nullable only because the column had to be added to a
    table that already had rows. This is what makes "always set" true in
    practice, and it runs at startup beside the orphaned-upload sweep.
    """
    # Empty string as well as NULL: an earlier release added the column with a
    # type-derived default, so a deployment that upgraded through it has rows
    # holding "" rather than NULL. Both mean the same thing here.
    unfiled = list(
        db.scalars(select(Document).where((Document.base_id.is_(None)) | (Document.base_id == "")))
    )
    if not unfiled:
        return 0

    bases: dict[str, KnowledgeBase] = {}
    for document in unfiled:
        owner = db.get(User, document.owner_id)
        if owner is None:
            continue
        if owner.id not in bases:
            bases[owner.id] = default_base(db, owner)
        document.base_id = bases[owner.id].id

    db.commit()
    log.info("filed %d document(s) that predated knowledge bases", len(unfiled))
    return len(unfiled)


# --- Creating ----------------------------------------------------------------
def store_upload(
    db: DBSession,
    *,
    owner: User,
    payload: bytes,
    filename: str,
    title: str = "",
    base: KnowledgeBase | None = None,
    group: str = DEFAULT_GROUP,
) -> Document:
    """Add an uploaded file to the library. Raises files.FileError if unusable.

    A document is in the data group of its base. `group` only chooses which
    default base it lands in when no base was given.
    """
    prepared = files_service.prepare(payload, filename)
    base = base or default_base(db, owner, group)

    stored_name = f"{secrets.token_hex(16)}{prepared.extension}"
    (library_dir() / stored_name).write_bytes(prepared.payload)

    display = files_service.safe_display_name(filename)
    document = Document(
        owner_id=owner.id,
        base_id=base.id,
        title=(title.strip() or display)[:300],
        source=SOURCE_UPLOAD,
        filename=display,
        stored_name=stored_name,
        media_type=prepared.media_type,
        size_bytes=len(prepared.payload),
        kind=prepared.kind,
        width=prepared.width,
        height=prepared.height,
        extracted_text=prepared.extracted_text,
        pages=prepared.pages,
        truncated=prepared.truncated,
        extraction_error=prepared.extraction_error,
    )
    db.add(document)
    db.commit()
    log.info("library: stored %r (%s) for %s", document.title, document.kind, owner.email)
    return document


def store_page(
    db: DBSession,
    *,
    owner: User,
    page: Fetched,
    base: KnowledgeBase | None = None,
    group: str = DEFAULT_GROUP,
) -> Document:
    """Add a fetched web page to the library.

    Saved as text rather than as the original HTML: the point of keeping it is
    what it said, and the markup would have to be reduced again on every read.
    """
    base = base or default_base(db, owner, group)
    document = Document(
        owner_id=owner.id,
        base_id=base.id,
        title=page.title[:300] or page.url[:300],
        source=SOURCE_LINK,
        source_url=page.url,
        filename="",
        media_type="text/plain",
        size_bytes=len(page.text.encode("utf-8")),
        kind="text",
        extracted_text=page.text,
        truncated=page.truncated,
    )
    db.add(document)
    db.commit()
    log.info("library: saved page %r for %s", document.title, owner.email)
    return document


# --- Reading -----------------------------------------------------------------
def visible(
    db: DBSession,
    user: User | None,
    *,
    base_ids: list[str] | None = None,
    group: str | None = None,
):
    """Documents this user may see, optionally narrowed to some bases.

    Visibility comes from the base, not the document: a document is readable by
    whoever can read the base it lives in. That is the whole reason bases are
    shareable and documents are not -- and it is also why a document has no
    data group of its own: it is in its base's.
    """
    condition = Document.base_id.in_(
        select(KnowledgeBase.id).where(sharing.visible_to(KnowledgeBase, user, group))
    )
    query = select(Document).where(condition)
    if base_ids:
        # Still filtered by visibility above, so naming a base you cannot see
        # returns nothing rather than granting access to it.
        query = query.where(Document.base_id.in_(base_ids))
    return query


def get(
    db: DBSession, document_id: str, user: User | None, group: str | None = None
) -> Document | None:
    document = db.get(Document, document_id)
    if document is None:
        return None
    base = db.get(KnowledgeBase, document.base_id) if document.base_id else None
    if base is None or not sharing.can_read(db, base, user, group):
        return None
    return document


def can_write(document: Document, user: User | None) -> bool:
    """Whether this person may change a document's text.

    Ownership, through the same helper every other library store uses. Sharing
    grants **reading only**, so being able to see a document through somebody
    else's base is never enough to rewrite it -- and reading is already settled
    by `get`, which resolves visibility through the base.

    Its own function rather than `sharing.can_write` at the call site because
    `Document` is the one store whose visibility does not come from itself, and
    a reader arriving at a bare `sharing.can_write(document, …)` would have to
    go and check whether that is the right question.
    """
    return sharing.can_write(document, user)


def set_text(db: DBSession, document: Document, text: str) -> Document:
    """Replace the extracted text a person reads and a model searches.

    The stored file is untouched: the bytes are the record, and this is what was
    made of them. That is the same line PDF extraction draws -- extracted once
    at upload, so a reply cannot change because a parser was upgraded -- and it
    is why editing this is safe for transcripts: `files.copy_document` copies
    the text when a document is attached, so an edit only changes what future
    searches find.

    `extraction_error` is cleared, because replacing a failed extraction by hand
    is the main reason to want this at all; leaving the old apology beside the
    new text would be the page contradicting itself.

    The commit fires the `documents_fts` UPDATE trigger, so search stays correct
    with nothing else to do. See `db/migrations.py:ensure_fts`.
    """
    ceiling = files_service.limits().max_extracted_chars
    document.extracted_text = text[:ceiling]
    document.truncated = len(text) > ceiling
    document.extraction_error = ""
    db.commit()
    return document


def search(
    db: DBSession,
    user: User | None,
    needle: str,
    *,
    limit: int = 10,
    base_ids: list[str] | None = None,
    vector: list[float] | None = None,
    group: str | None = None,
) -> list[Document]:
    """Documents matching `needle` that this user may see, best match first.

    The index is searched first and the visibility filter applied to the rows
    it returned. That order matters: filtering afterwards is what makes it
    impossible for a hit on somebody else's document to leak, even as a count.

    `vector` is the query already embedded, or None. It comes from the caller
    rather than being worked out here because this is synchronous and embedding
    is an HTTP request -- see `services/library/retrieval.py`. None means the
    keyword search exactly as it always was.
    """
    hits = retrieval.search(
        db, INDEX, needle, kind=CHUNK_DOCUMENT, vector=vector, limit=limit * 4
    )
    if not hits:
        return []

    order = {hit.id: position for position, hit in enumerate(hits)}
    rows = list(
        db.scalars(
            visible(db, user, base_ids=base_ids, group=group).where(
                Document.id.in_(list(order))
            )
        )
    )
    rows.sort(key=lambda document: order.get(document.id, len(order)))
    return rows[:limit]


def snippet(document: Document) -> str:
    """The part of a document a search result carries."""
    text = (document.extracted_text or "").strip()
    if len(text) <= SNIPPET_CHARS:
        return text
    return text[:SNIPPET_CHARS].rstrip() + "…"


# --- Removing ----------------------------------------------------------------
def delete(db: DBSession, document: Document) -> None:
    path = stored_path(document.stored_name)
    if path is not None:
        path.unlink(missing_ok=True)
    db.delete(document)
    db.commit()
