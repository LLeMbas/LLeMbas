"""`knowledge_search` and `knowledge_get`: the knowledge library."""

from __future__ import annotations

from typing import Any

from lembas.db.models import (
    User,
)
from lembas.db.session import session_scope
from lembas.services.library import documents as documents_service
from lembas.services.tools.core import (
    FAMILY_KNOWLEDGE,
    MAX_DOCUMENT_CHARS,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)
from lembas.services.tools.query_vector import _query_vector


# --- Knowledge ---------------------------------------------------------------
async def _run_knowledge_search(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolOutcome(
            "No search terms were given.",
            {"name": "knowledge_search", "status": "error", "error": "No query."},
        )

    # `base`: only one knowledge base, named by id or by name among the ones
    # this chat may search. An unknown one is the tool's answer, naming what
    # there is -- the spec's `library_scope` cases pin the wording.
    scoped = documents_service.resolve_base(context.bases, str(args.get("base") or ""))
    if "error" in scoped:
        return ToolOutcome(
            scoped["error"],
            {"name": "knowledge_search", "query": query, "status": "error",
             "error": scoped["error"], "results": []},
        )
    base_ids = scoped["ids"] if str(args.get("base") or "").strip() else context.base_ids

    # Embedded before the session opens, because it is an HTTP request and a
    # session held across one is the trade `_maybe_compact` already refuses.
    # None for every "no" -- no model configured, endpoint down -- and the
    # search is then exactly the keyword one it has always been.
    vector = await _query_vector(query, context.data_group)

    with session_scope() as db:
        user = db.get(User, context.owner_id)
        found = documents_service.search(
            db,
            user,
            query,
            limit=6,
            base_ids=base_ids,
            vector=vector,
            group=context.data_group,
        )
        event = {
            "name": "knowledge_search",
            "query": query,
            "status": "ok",
            "results": [
                {"title": d.title, "id": d.id, "kind": d.kind, "host": d.source_url}
                for d in found
            ],
        }
        if not found:
            return ToolOutcome(
                f"Nothing in the knowledge library matches {query!r}.", event
            )

        lines = [f"Knowledge library matches for {query!r}:"]
        for document in found:
            lines.append(
                f"\n[{document.id}] {document.title}\n"
                f"{documents_service.snippet(document)}"
            )
        lines.append(
            "\nUse knowledge_get with an id in brackets to read a document in full."
        )
        return ToolOutcome("\n".join(lines), event)


async def _run_knowledge_get(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    document_id = str(args.get("id") or "").strip()
    try:
        offset = max(int(args.get("offset") or 0), 0)
    except (TypeError, ValueError):
        offset = 0
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        document = documents_service.get(db, document_id, user, context.data_group)
        if document is None:
            return ToolOutcome(
                "There is no such document, or it is not available to you.",
                {"name": "knowledge_get", "status": "error", "error": "Not found."},
            )
        event = {
            "name": "knowledge_get",
            "query": document.title,
            "status": "ok",
            "results": [{"title": document.title, "id": document.id}],
        }
        whole = document.extracted_text or document.extraction_error or "(no text)"
        total = len(document.extracted_text or "")
        if offset and offset >= len(whole):
            return ToolOutcome(
                f"{document.title}\n\n[Nothing past character {offset:,}: the document is "
                f"{total:,} characters long.]",
                event,
            )
        body = whole[offset:]
        if len(body) > MAX_DOCUMENT_CHARS:
            shown_to = offset + MAX_DOCUMENT_CHARS
            body = (
                f"{body[:MAX_DOCUMENT_CHARS]}\n\n"
                f"[Cut off here. This document is {total:,} characters and characters "
                f"{offset:,}–{shown_to:,} are above. Read on with offset={shown_to}, or "
                "search it with knowledge_search to find the part you need.]"
            )
            event["truncated"] = True
        elif offset:
            body = f"[From character {offset:,} of {total:,}.]\n\n{body}"
        return ToolOutcome(f"{document.title}\n\n{body}", event)


TOOLS: tuple[ToolDef, ...] = (
    # The two library readers are the harness spec's, word for word. The one
    # thing not copied: a document's id is a string here (`Document.id`),
    # where the spec, written for the CLI's numbered library, says integer.
    ToolDef(
        name="knowledge_search",
        family=FAMILY_KNOWLEDGE,
        description=spec_description("knowledge_search"),
        parameters=spec_parameters("knowledge_search"),
        run=_run_knowledge_search,
    ),
    ToolDef(
        name="knowledge_get",
        family=FAMILY_KNOWLEDGE,
        description=spec_description("knowledge_get"),
        parameters={
            **spec_parameters("knowledge_get"),
            "properties": {
                **spec_parameters("knowledge_get")["properties"],
                "id": {"type": "string"},
            },
        },
        run=_run_knowledge_get,
    ),
)
