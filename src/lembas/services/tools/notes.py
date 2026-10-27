"""`notes_search`, `note_view` and `note_manage`."""

from __future__ import annotations

from typing import Any

from lembas.db.models import (
    AUTHOR_MODEL,
    User,
)
from lembas.db.session import session_scope
from lembas.services.library import notes as notes_service
from lembas.services.tools.core import (
    FAMILY_NOTES,
    RISK_WRITE,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)
from lembas.services.tools.query_vector import _query_vector


# --- Notes -------------------------------------------------------------------
async def _run_notes_search(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    query = str(args.get("query") or "").strip()
    vector = await _query_vector(query, context.data_group)
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        found = (
            notes_service.search(
                db, user, query, limit=8, vector=vector, group=context.data_group
            )
            if query
            else notes_service.recent(db, user, limit=8, group=context.data_group)
        )
        event = {
            "name": "notes_search",
            "query": query,
            "status": "ok",
            "results": [{"title": n.title, "id": n.id} for n in found],
        }
        if not found:
            return ToolOutcome("There are no notes matching that.", event)
        lines = ["Notes:"]
        for note in found:
            lines.append(f"\n[{note.id}] {note.title}\n{notes_service.snippet(note)}")
        lines.append("\nRead one with note_view.")
        return ToolOutcome("\n".join(lines), event)


async def _run_note_view(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        note = notes_service.get(db, str(args.get("id") or "").strip(), user, context.data_group)
        if note is None:
            return ToolOutcome(
                f"There is no note {str(args.get('id') or '')!r} here. notes_search finds "
                "the ids.",
                {"name": "note_view", "status": "error", "error": "Not found."},
            )
        updated = note.updated_at.strftime("%Y-%m-%d %H:%M") if note.updated_at else ""
        return ToolOutcome(
            f"# {note.title}\n(updated {updated})\n\n{note.body}",
            {
                "name": "note_view",
                "query": note.title,
                "status": "ok",
                "results": [{"title": note.title, "id": note.id}],
            },
        )


async def _run_note_manage(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """The spec's `note_manage`: create, edit or delete one note.

    Only a note of one's own can be changed: a note somebody shared can be
    read and not written, the same rule the library pages keep.
    """
    action = str(args.get("action") or "").strip()

    def refused(message: str, error: str) -> ToolOutcome:
        return ToolOutcome(message, {"name": "note_manage", "status": "error", "error": error})

    if action not in ("create", "edit", "delete"):
        return refused("action is create, edit or delete.", "No action.")
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        if action == "create":
            title = str(args.get("title") or "").strip()
            body = str(args.get("body") or "").strip()
            if not title or not body:
                return refused("create needs a title and a body.", "Empty.")
            if len(body) > notes_service.MAX_BODY_CHARS:
                return refused(
                    f"A note is at most {notes_service.MAX_BODY_CHARS:,} characters; this "
                    f"one is {len(body):,}.",
                    "Too long.",
                )
            note = notes_service.create(
                db, owner=user, title=title, body=body, author=AUTHOR_MODEL,
                group=context.data_group,
            )
            return ToolOutcome(
                f"Saved note {note.id}.",
                {
                    "name": "note_manage",
                    "action": action,
                    "query": note.title,
                    "status": "ok",
                    "results": [{"title": note.title, "id": note.id}],
                },
            )
        wanted = str(args.get("id") or "").strip()
        if not wanted:
            return refused(f"{action} needs the note's id (notes_search finds it).", "No id.")
        note = notes_service.get(db, wanted, user, context.data_group)
        if note is None or note.owner_id != context.owner_id:
            return refused(
                f"There is no note {wanted!r} of yours here. A note shared with you can be "
                "read but not changed.",
                "Not writable.",
            )
        title = note.title
        if action == "delete":
            notes_service.delete(db, note)
            return ToolOutcome(
                f"Deleted note {wanted}.",
                {"name": "note_manage", "action": action, "query": title, "status": "ok",
                 "results": []},
            )
        body = args.get("body")
        if body is not None and len(str(body)) > notes_service.MAX_BODY_CHARS:
            return refused(
                f"A note is at most {notes_service.MAX_BODY_CHARS:,} characters.", "Too long."
            )
        notes_service.update(
            db, note, title=args.get("title"), body=None if body is None else str(body)
        )
        return ToolOutcome(
            f"Changed note {note.id}.",
            {
                "name": "note_manage",
                "action": action,
                "query": note.title,
                "status": "ok",
                "results": [{"title": note.title, "id": note.id}],
            },
        )


TOOLS: tuple[ToolDef, ...] = (
    # The library's tools are the harness spec's (1.2.0), filled with this
    # server's facts (`LLEMBAS_VARS`) and narrowed to what it has
    # (`narrow.llembas`) -- the same tools the LLeMbas CLI sends.
    ToolDef(
        name="notes_search",
        family=FAMILY_NOTES,
        description=spec_description("notes_search"),
        parameters=spec_parameters("notes_search"),
        run=_run_notes_search,
    ),
    ToolDef(
        name="note_view",
        family=FAMILY_NOTES,
        description=spec_description("note_view"),
        parameters=spec_parameters("note_view"),
        run=_run_note_view,
    ),
    ToolDef(
        name="note_manage",
        family=FAMILY_NOTES,
        description=spec_description("note_manage"),
        parameters=spec_parameters("note_manage"),
        run=_run_note_manage,
        risk=RISK_WRITE,
    ),
)
