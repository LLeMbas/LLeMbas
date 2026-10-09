"""Search: one box over everything a person has here.

Chats and agent sessions, and -- for whoever may use them -- the library's
knowledge, notes and skills, reports, and memories. One page, `GET /search`,
reached from the magnifier in the sidebar's header rail, and carrying the chat
sidebar as the library does: you look something up mid-conversation and go
straight back.

**Every store is asked through the code that already decides who sees what.**
The library and reports go through their own `search` functions, which apply
`sharing.visible_to` to the rows the index returned -- the order that makes a
hit on somebody else's document impossible to leak, even as a count. Chats and
memories have no index and no sharing: they are this person's own, so the query
is `user_id`/`owner_id` first and the text second. Nothing here widens what a
page elsewhere already shows.

**Filters are offered by permission, not just refused by it.** A tab for
something the reader may not open would be a link to a 403, so a type nobody
can see is neither listed nor searched, and asking for it by URL falls back to
"All" rather than failing.

**"All" is a preview, a filtered view is the list.** Up to `PREVIEW` hits per
type, grouped under the type's name, with "Show all" when there may be more --
asked for as one more than shown, so "more" is known rather than guessed. A
filtered view shows up to `FULL`. No pager: a search that needs one wants a
better query, and every store's own page has its own list.

The vector for the library's hybrid search is embedded once and handed to every
store, and only when one of them is being searched -- it is an HTTP request.

API-only mode refuses `/search` outright (`services/instance_mode.py`): there are
no chats, library or reports to find.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from markupsafe import Markup, escape
from sqlalchemy import or_, select
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db, RequiredUser
from lembas.api.pages import sidebar_context
from lembas.db.models import KINDS, ROLE_ASSISTANT, ROLE_USER, Chat, Memory, Message, User
from lembas.services import reports as reports_service
from lembas.services.library import documents as documents_service
from lembas.services.library import notes as notes_service
from lembas.services.library import retrieval
from lembas.services.library import skills as skills_service
from lembas.web.templating import render

router = APIRouter(tags=["search"])

# The filters, in the order the tabs show them. "all" first and the default.
FILTERS = ("all", "chats", "knowledge", "notes", "skills", "reports", "memories")

# Which permission each type needs. Chats need none beyond being signed in: a
# person's own chats are always theirs to find.
NEEDS = {
    "chats": None,
    "knowledge": "library.use",
    "notes": "library.use",
    "skills": "library.use",
    "reports": "reports.use",
    "memories": "library.use",
}

PREVIEW = 5
FULL = 50

# How much of a message a chat hit quotes, around the first match.
SNIPPET_CHARS = 160


def allowed_types(can: dict[str, bool]) -> list[str]:
    """The types this reader may search, in tab order."""
    return [kind for kind in FILTERS[1:] if NEEDS[kind] is None or can.get(NEEDS[kind])]


def _like(needle: str) -> str:
    """A LIKE pattern matching `needle` anywhere, with its own `%` and `_` (and
    the escape character itself) taken literally. Without this a search for
    `50%` matches everything with a 50 in it, and `_` matches any character."""
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def snippet(text: str, needle: str, width: int = SNIPPET_CHARS) -> Markup:
    """About `width` characters of `text` around the first match of `needle`,
    with the match in `<mark>`.

    Built as `Markup` from escaped pieces -- never by marking the message safe
    and inserting tags into it. A message is whatever somebody (or a model)
    typed, and `<script>` in one must arrive on this page as text. If the needle
    is not found (SQLite's LIKE folds ASCII case only, Python's `lower()` folds
    more, and the two can disagree at the edges), the start of the text is
    quoted unmarked rather than nothing.
    """
    text = " ".join((text or "").split())
    at = text.lower().find(needle.lower()) if needle else -1
    if at < 0:
        quoted = text[:width]
        return escape(quoted) + (Markup("…") if len(text) > width else Markup(""))
    end = at + len(needle)
    room = max(width - len(needle), 0)
    start = max(at - room // 2, 0)
    stop = min(max(end + (room - (at - start)), end), len(text))
    # Pulled back when the window ran off the end, so a match near the end of a
    # long message still gets its share of context before it.
    start = max(min(start, stop - width), 0)
    return (
        (Markup("…") if start > 0 else Markup(""))
        + escape(text[start:at])
        + Markup("<mark>")
        + escape(text[at:end])
        + Markup("</mark>")
        + escape(text[end:stop])
        + (Markup("…") if stop < len(text) else Markup(""))
    )


def _search_chats(db: DBSession, user: User, needle: str, limit: int) -> list[dict]:
    """This person's chats and agent sessions whose title, or anything said in
    them, matches. Archived ones included and marked -- finding something put
    away is half of what a search is for. Temporary chats are not: they are not
    listed anywhere else either, and they are on their way out. Task chats are
    left out with them, for the reason `sidebar_context` gives: they belong to
    Scheduled, not to the list.

    Only what the person and the model *said* is searched, not tool output or
    the system prompt: a hit on a directory listing is not the conversation
    anybody is looking for.
    """
    pattern = _like(needle)
    spoken = (ROLE_USER, ROLE_ASSISTANT)
    said = select(Message.chat_id).where(
        Message.role.in_(spoken), Message.content.ilike(pattern, escape="\\")
    )
    chats = list(
        db.scalars(
            select(Chat)
            .where(
                Chat.user_id == user.id,
                Chat.temporary.is_(False),
                Chat.kind.in_(KINDS),
                or_(Chat.title.ilike(pattern, escape="\\"), Chat.id.in_(said)),
            )
            .order_by(Chat.updated_at.desc())
            .limit(limit)
        )
    )
    hits = []
    for chat in chats:
        first = db.scalar(
            select(Message.content)
            .where(
                Message.chat_id == chat.id,
                Message.role.in_(spoken),
                Message.content.ilike(pattern, escape="\\"),
            )
            .order_by(Message.created_at)
            .limit(1)
        )
        hits.append(
            {
                "title": chat.title,
                "url": f"/chat/{chat.id}",
                "icon": "terminal" if chat.kind == "agent" else "chat",
                "snippet": snippet(first, needle) if first else "",
                "archived": chat.archived,
            }
        )
    return hits


def _search_memories(db: DBSession, user: User, needle: str, limit: int) -> list[dict]:
    """This person's own memories, never anybody else's: a memory is not
    shareable at all. They are edited on Settings -> Memory, so that is where
    every hit leads."""
    rows = db.scalars(
        select(Memory)
        .where(Memory.owner_id == user.id, Memory.content.ilike(_like(needle), escape="\\"))
        .order_by(Memory.updated_at.desc())
        .limit(limit)
    )
    return [
        {
            "title": snippet(memory.content, needle),
            "url": "/settings?tab=memory",
            "icon": "user",
            "snippet": "",
            "archived": False,
        }
        for memory in rows
    ]


async def run(db: DBSession, user: User, needle: str, kinds: list[str], limit: int) -> dict:
    """Search each of `kinds` for `needle`, at most `limit` hits each.

    Returns `{kind: hits}`, each hit the same small dict whatever the store, so
    the template draws one kind of row.
    """
    vector = None
    if any(kind in ("knowledge", "notes", "skills", "reports") for kind in kinds):
        vector = await retrieval.embed_query(db, needle)

    found: dict[str, list[dict]] = {}
    for kind in kinds:
        if kind == "chats":
            found[kind] = _search_chats(db, user, needle, limit)
        elif kind == "memories":
            found[kind] = _search_memories(db, user, needle, limit)
        elif kind == "knowledge":
            found[kind] = [
                {
                    "title": document.title,
                    "url": f"/library/knowledge/document/{document.id}",
                    "icon": "archive",
                    "snippet": documents_service.snippet(document)[:SNIPPET_CHARS],
                    "archived": False,
                }
                for document in documents_service.search(
                    db, user, needle, limit=limit, vector=vector
                )
            ]
        elif kind == "notes":
            found[kind] = [
                {
                    "title": note.title,
                    "url": f"/library/notes/{note.id}",
                    "icon": "pencil",
                    "snippet": snippet(note.body, needle),
                    "archived": False,
                }
                for note in notes_service.search(db, user, needle, limit=limit, vector=vector)
            ]
        elif kind == "skills":
            found[kind] = [
                {
                    "title": skill.name,
                    "url": f"/library/skills/{skill.id}",
                    "icon": "sparkle",
                    "snippet": (skill.description or "")[:SNIPPET_CHARS],
                    "archived": False,
                }
                for skill in skills_service.search(db, user, needle, limit=limit, vector=vector)
            ]
        elif kind == "reports":
            found[kind] = [
                {
                    "title": report.title,
                    "url": f"/library/reports/{report.id}",
                    "icon": "file-text",
                    "snippet": (report.summary or "")[:SNIPPET_CHARS],
                    "archived": False,
                }
                for report in reports_service.search(
                    db, user, needle, limit=limit, vector=vector
                )
            ]
    return found


@router.get("/search")
async def search_page(request: Request, db: Db, user: RequiredUser, q: str = ""):
    """The search page. `q` is the query, `in` the filter (a reserved word in
    Python, so read off the query string rather than as a parameter)."""
    context = sidebar_context(db, user)
    offered = allowed_types(context["can"])
    chosen = request.query_params.get("in", "all")
    if chosen != "all" and chosen not in offered:
        chosen = "all"

    needle = q.strip()
    sections: list[dict] = []
    if needle:
        kinds = offered if chosen == "all" else [chosen]
        shown = PREVIEW if chosen == "all" else FULL
        # One more than is shown, so "Show all" appears only when there is
        # more to show.
        found = await run(db, user, needle, kinds, shown + 1)
        sections = [
            {"kind": kind, "hits": found[kind][:shown], "more": len(found[kind]) > shown}
            for kind in kinds
            if found.get(kind)
        ]

    return render(
        request,
        "search.html",
        {
            "section": "search",
            "q": q,
            "needle": needle,
            "filter": chosen,
            "offered": offered,
            "results": sections,
            **context,
        },
    )
