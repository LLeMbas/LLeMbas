"""Tools a model may call while it answers.

A registry of named callables with a JSON schema each: offered to the endpoint,
executed here when it asks. MCP servers and agentic execution plug in at the
same place, which is why the registry is keyed and grouped rather than being a
handful of if-statements.

Three things gate whether a tool is offered:

* the instance is configured for it (web search has a provider, and so on),
* the reader has the permission, and
* the chat's model is marked as having that tool.

The last is not optional politeness. Sending a ``tools`` array to an endpoint
that does not implement tool calling fails the entire request, exactly the way
sending image parts to a model without vision does.

Tools that *write* -- notes, memories, skills -- need a database session and a
user, and they run inside a background generation that outlives the request. So
they are handed a `ToolContext` carrying an owner id rather than a live session,
and open their own scope, the same way `services.generation` does.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import AUTHOR_MODEL, DEFAULT_GROUP, KIND_TASK, SOURCE_CHAT, Chat, User
from lembas.db.session import session_scope
from lembas.services import prompts as prompts_service
from lembas.services import reports as reports_service
from lembas.services import scratch as scratch_service
from lembas.services import search as search_service
from lembas.services import settings_store
from lembas.services.library import documents as documents_service
from lembas.services.library import memories as memories_service
from lembas.services.library import notes as notes_service
from lembas.services.library import skills as skills_service
from lembas.services.search.base import SearchError
from lembas.web.i18n import N_

log = logging.getLogger(__name__)

# How many times a model may call tools before it has to answer with words, in
# an ORDINARY chat. An agent chat is sized by `agent/policy.py:Limits.steps`
# instead, which is two orders of magnitude larger, because an agent reply is
# meant to run until the work is done.
#
# A **ceiling, not a schedule.** The loop ends the moment a round comes back
# with no tool calls -- that is the model saying it has what it needs, and it is
# the same termination condition every agentic harness uses. This number only
# catches the case where it never says so: a small model that has decided
# searching is the answer, searching until the context runs out at a full
# request each.
#
# It was 1, then 5, and now 0 meaning no ceiling at all. Both numbers were the
# same mistake at different scales: low enough to be reached by ordinary work is
# low enough to be a schedule rather than a ceiling, overriding the model's
# judgement on every turn instead of catching a runaway. One left the library
# searchable and not readable, since `knowledge_get` and `notes_get` read a
# document "by the id a search returned". Five ended a piece of research at its
# sixth search.
#
# What bounds an ordinary chat now is the context window, and the loop falls
# back to `generation.MAX_TOOL_ROUNDS` as a runaway backstop -- the shape
# `Limits.steps` already had for an agent chat.
#
# `settings_store.chat_rounds()` is what the loop and the harness read; this is
# the fallback for callers with no session, and a test pins the two together.
MAX_ROUNDS = 0

# Tool families, matching the per-model capability flags and the permission
# keys. The three names differ by prefix only, which is deliberate: adding a
# family means adding one entry here and one permission.
FAMILY_SEARCH = "web_search"
# Reading one page, given its address. Its own family rather than part of
# `web_search`: an administrator may reasonably want a model that can look
# things up but not follow an arbitrary URL it read somewhere, and the SSRF
# surface is entirely on this side.
FAMILY_FETCH = "fetch"
FAMILY_KNOWLEDGE = "knowledge"
FAMILY_NOTES = "notes"
FAMILY_MEMORY = "memory"
FAMILY_SKILLS = "skills"

# A tool that is a database row gets a family of its own, so that it can carry
# its own guidance -- "custom:weather", "mcp:github". Everything before the
# colon is the *gate*: the capability flag and the permission are per gate, not
# per row, because a server advertising forty tools must not mean forty
# checkboxes on every model.
FAMILY_CUSTOM = "custom"
FAMILY_MCP = "mcp"

# Stopping to ask the reader something. Its own family because it belongs to no
# other one: it is offered in an ordinary chat as much as an agent chat, and it
# is the only tool the model cannot resolve by itself.
FAMILY_ASK = "ask"

# The chat's own working surface -- the canvas panel's scratch document.
# Deliberately not part of `notes`: a note is a durable artefact of the reader's
# that outlives the chat and is searchable, while this is the chat's own record
# of what it is doing, which is the line `plan_update` sits on. It is also its
# own switch, because narrowing notes off must not silently take the pad too.
FAMILY_SCRATCH = "scratch"

# Acting on the machine an agent chat is pointed at. Offered only when the chat
# is one, has a usable connection, and the feature is switched on -- see
# services/agent/session.py:resolve, which answers all three at once.
FAMILY_AGENT = "agent"

# Drawing a picture on a ComfyUI an administrator configured. A family of its
# own for the reason `fetch` is one: an instance may reasonably want a model
# that can look things up but not spend a minute of GPU on every request, and
# the whole cost of this one is somewhere else.
FAMILY_IMAGE = "image"

# Filing a finished piece of work where the reader will find it later.
# Deliberately not part of `notes`, and the line is the one a note already
# draws from the other side: a note is something to be found again *by the
# model*, searched for mid-conversation and edited when it turns out to be
# wrong. A report is addressed to a person, read once, and never answered --
# so it is the destination for work nobody was watching, which is exactly what
# a note is not. Narrowing notes off must not take it away, and turning it on
# must not hand out the notebook.
FAMILY_REPORT = "report"

# Setting work up to happen later, or repeatedly. Its own family and emphatically
# not part of `notes`: the line between them is the whole reason this exists. A
# note is something to find again; a schedule is something that *happens*, and a
# model with only the first reached for it when asked for the second -- wrote the
# note, said it had scheduled something, and nothing anywhere disagreed.
FAMILY_SCHEDULE = "schedule"

# Handing a self-contained piece of work to a second model that runs on its own
# and reports back. Its own family because it is the one tool whose cost is
# another whole reply -- an instance may reasonably offer everything else and
# not this, and on a single local endpoint four helpers at once is four times
# the queue rather than four times the speed.
FAMILY_SUBAGENT = "subagent"

# The built-in families, in the order they are offered.
FAMILIES = (
    FAMILY_SEARCH,
    FAMILY_FETCH,
    FAMILY_KNOWLEDGE,
    FAMILY_NOTES,
    FAMILY_MEMORY,
    FAMILY_SKILLS,
    FAMILY_SCRATCH,
    FAMILY_ASK,
    FAMILY_IMAGE,
    FAMILY_REPORT,
    FAMILY_SCHEDULE,
    FAMILY_SUBAGENT,
)

GATES = (*FAMILIES, FAMILY_CUSTOM, FAMILY_MCP)


def gate_of(family: str) -> str:
    """The part a capability flag and a permission are named after."""
    return family.split(":", 1)[0]


# What a tool does to the world. Only agent chats consult it -- an ordinary chat
# behaves exactly as it always did -- but it is declared on every tool, because
# the permission modes are a table indexed by it and a tool whose class is a
# guess is a tool whose gate is a guess.
RISK_READ = "read"
RISK_WRITE = "write"
RISK_EXECUTE = "execute"
# Never resolves to "allowed", in any mode. `ask_user` is the only tool that
# carries it: stopping to ask is the whole of what it does.
RISK_ASK = "ask"

RISKS = (RISK_READ, RISK_WRITE, RISK_EXECUTE, RISK_ASK)

# How much of a fetched page reaches the model. `fetch()` returns up to 120_000
# characters, which is roughly thirty thousand tokens -- one call would fill an
# ordinary window and, in an agent chat, spend the whole output budget on a
# single page. Cut with the model told so, rather than refused.
MAX_FETCH_CHARS = 20_000

# The same bound for a knowledge document, and it was missing. `knowledge_get`
# returned `extracted_text` whole while every sibling reader capped and said so
# -- `fetch` above, `file_read`, the memories block, the skill index, the
# project listing. `MAX_EXTRACTED_CHARS` is 120_000 by default and an
# administrator can raise it, so one call on a long PDF filled an ordinary
# window with nothing anywhere reporting that it had.
#
# Larger than a fetched page on purpose. Somebody put this document in the
# library deliberately and named it in a search; a page the model followed a
# link to is a guess. Cut with the model told so rather than refused, which is
# what `fetch` and `file_read` both do -- a reader that fails on exactly the
# documents worth reading is worse than one that hands back the first part and
# says there was more.
MAX_DOCUMENT_CHARS = 40_000


@dataclass
class ToolContext:
    """What a tool needs to do its work, without holding a session open.

    `owner_id` rather than a User for the same reason `Endpoint` is a frozen
    snapshot rather than a Connection: a generation outlives the request that
    started it, and a detached SQLAlchemy instance is a trap.
    """

    owner_id: str
    # Which conversation this call belongs to. Needed by anything that writes
    # something the chat owns rather than something the *reader* owns -- the
    # scratch document, a generated image -- and empty for a call with no chat
    # behind it, which is what those runners check first.
    chat_id: str = ""
    search_config: dict[str, Any] = field(default_factory=dict)
    # Which knowledge bases this chat is scoped to. Empty means "everything the
    # owner can see", which is what a chat with none attached should do.
    base_ids: list[str] = field(default_factory=list)
    # Name -> definition for the tools actually offered on this request. None
    # means nobody resolved a set, and only then does `run_tool` fall back to
    # the import-time registry. A dict, *even an empty one*, is authoritative:
    # a model naming a tool it was not offered must not get it run.
    tools: dict[str, ToolDef] | None = None
    # How long a reply waits for someone to answer a question or approve
    # something. Read from the instance settings while the session was open,
    # like everything else here.
    interaction_timeout: float = 900.0
    # Whether there is anybody who could answer. False for an ordinary chat;
    # true for a scheduled task's and a subagent's. `ask_user` is already
    # withdrawn when it is set, so what this reaches is `_authorise`, which
    # answers an approval with a refusal instead of pausing on a card nobody
    # can see. Without it the reply stalls for `interaction_timeout` and then
    # gives up having done nothing -- which is the failure the withdrawal was
    # added to prevent, arriving by the other door.
    unattended: bool = False
    # Set only for an agent chat: the machine to act on, the mode in force, and
    # the decrypted credential. None everywhere else, which is what every agent
    # runner checks first. `generation` clears it when the reply ends.
    agent: Any = None
    # Skills this chat has switched off, by name. Enforced in `_run_skill_get`
    # and not only in the listing: without that the narrowing is advisory, since
    # a model can name a skill it was never shown and the runner would fetch it
    # anyway. Same rule as "what may be run is what was offered".
    skills_off: frozenset[str] = field(default_factory=frozenset)
    # Image generation, snapshotted like everything else here. `image_config` is
    # the instance settings group; the two below are this chat's preferences,
    # used when the model names neither. `model_id` and `connection_id` are what
    # the reviewer and the Preserve VRAM unload need to find the chat's own
    # endpoint -- its own, and no other, because the VRAM being freed belongs to
    # one machine.
    image_config: dict[str, Any] = field(default_factory=dict)
    image_workflow_id: str = ""
    image_checkpoint: str = ""
    model_id: str = ""
    connection_id: str = ""
    # Which data group this call reads and writes, resolved from the answering
    # model's connection. Every library runner passes it to its store and every
    # write stamps it, so a model reaches exactly one group's records -- by
    # search *and* by id, since a model that learned an id from somewhere else
    # must not be able to fetch the record past the filter.
    data_group: str = DEFAULT_GROUP


@dataclass
class ToolOutcome:
    """What running a tool produced, for the model and for the reader.

    The two are deliberately different. `content` is the flat text the model
    reads back; `event` is what the transcript shows, and keeps results
    structured so they can be rendered as links rather than as a wall of URLs.
    """

    content: str
    event: dict[str, Any] = field(default_factory=dict)


Runner = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolOutcome]]


@dataclass(frozen=True)
class ToolDef:
    name: str
    family: str
    description: str
    parameters: dict[str, Any]
    run: Runner
    # Declared rather than derived from the name: `notes_edit` and
    # `knowledge_get` are not told apart by spelling, and the consequence of
    # guessing is that a mode silently permits something it meant to ask about.
    # Defaulted so that reading is what a tool has to be talked out of.
    risk: str = RISK_READ

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolSet:
    """What one request may call: the schemas to send, and how to run them.

    The two halves have to travel together. `enabled_tools` used to return
    schemas alone, which worked only because every runner was reachable through
    the import-time `REGISTRY`. A tool that is a database row is not, so the
    resolution has to be carried from the session that made it to the loop that
    uses it.
    """

    defs: tuple[ToolDef, ...] = ()

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema for tool in self.defs]

    @property
    def by_name(self) -> dict[str, ToolDef]:
        return {tool.name: tool for tool in self.defs}

    def __bool__(self) -> bool:
        return bool(self.defs)


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


_STRING = {"type": "string"}


# --- Web search --------------------------------------------------------------
async def _run_web_search(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolOutcome(
            "No search query was given.",
            {"name": "web_search", "status": "error", "error": "No query was given."},
        )

    limit = args.get("max_results")
    try:
        limit = int(limit) if limit is not None else None
    except (TypeError, ValueError):
        limit = None

    try:
        results = await search_service.run(context.search_config, query, limit=limit)
    except SearchError as exc:
        log.info("web search failed for %r: %s", query[:60], exc.message)
        return ToolOutcome(
            f"The search failed: {exc.message}",
            {"name": "web_search", "query": query, "status": "error", "error": exc.message},
        )

    event = {
        "name": "web_search",
        "query": query,
        "status": "ok",
        "results": [
            {"title": r.title, "url": r.url, "snippet": r.snippet, "host": r.host}
            for r in results
        ],
    }
    if not results:
        return ToolOutcome(f"No results were found for {query!r}.", event)

    lines = [f"Search results for {query!r}:"]
    for index, result in enumerate(results, start=1):
        lines.append(f"\n[{index}] {result.title}\n{result.url}\n{result.snippet}")
    return ToolOutcome("\n".join(lines), event)


# --- Fetching one page ---------------------------------------------------------
async def _run_fetch(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Retrieve one URL and hand back its text.

    Straight through `services/fetch.py`, which owns the SSRF guard, the
    hand-rolled redirect loop that re-checks every hop, and the content-type
    sniff. Deliberately not a second HTTP client: the working notes already name three
    places that follow redirects by hand as the ceiling, and a fourth is how one
    of them loses its check.
    """
    from lembas.services import fetch as fetch_service

    url = str(args.get("url") or "").strip()
    if not url:
        return ToolOutcome(
            "No address was given.",
            {"name": "fetch", "status": "error", "error": "No URL."},
        )

    try:
        page = await fetch_service.fetch(
            url, allow_private=bool(context.search_config.get("allow_private_fetch"))
        )
    except fetch_service.FetchError as exc:
        # Its messages are already written to be shown to a person, which is
        # close enough to being written for a model to act on.
        return ToolOutcome(
            f"That page could not be read: {exc.message}",
            {"name": "fetch", "query": url, "status": "error", "error": exc.message},
        )

    text = page.text[:MAX_FETCH_CHARS]
    cut = page.truncated or len(page.text) > MAX_FETCH_CHARS
    event = {
        "name": "fetch",
        "kind": "fetch",
        "query": page.title or url,
        "detail": page.url,
        "status": "ok",
        "results": [],
        "text": text[:2000],
    }
    note = "\n\n(The page was longer than this and has been cut off.)" if cut else ""
    return ToolOutcome(f"{page.title}\n{page.url}\n\n{text}{note}", event)


# --- Knowledge ---------------------------------------------------------------
async def _run_knowledge_search(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolOutcome(
            "No search terms were given.",
            {"name": "knowledge_search", "status": "error", "error": "No query."},
        )

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
            base_ids=context.base_ids,
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
        body = document.extracted_text or document.extraction_error or "(no text)"
        if len(body) > MAX_DOCUMENT_CHARS:
            body = (
                f"{body[:MAX_DOCUMENT_CHARS]}\n\n"
                f"[Cut off here. This document is {len(document.extracted_text or ''):,} "
                f"characters and the first {MAX_DOCUMENT_CHARS:,} are above. Search it "
                "with knowledge_search to find the part you need.]"
            )
            event["truncated"] = True
        return ToolOutcome(f"{document.title}\n\n{body}", event)


async def _query_vector(query: str, group: str | None = None) -> list[float] | None:
    """The query as a vector, for the stores that can use one.

    Its own session, opened and closed before the caller opens theirs: this is
    an HTTP request, and holding a database session across one is the trade
    compaction and the project listing both already refuse.
    """
    if not query:
        return None
    from lembas.services.library import retrieval

    with session_scope() as db:
        worker = retrieval.worker_for(db, group)
    return await retrieval.embed_with(worker, query)


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
        lines.append("\nUse notes_get with an id to read one in full.")
        return ToolOutcome("\n".join(lines), event)


async def _run_notes_get(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        note = notes_service.get(db, str(args.get("id") or ""), user, context.data_group)
        if note is None:
            return ToolOutcome(
                "There is no such note, or it is not available to you.",
                {"name": "notes_get", "status": "error", "error": "Not found."},
            )
        return ToolOutcome(
            f"{note.title}\n\n{note.body}",
            {
                "name": "notes_get",
                "query": note.title,
                "status": "ok",
                "results": [{"title": note.title, "id": note.id}],
            },
        )


async def _run_notes_create(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    title = str(args.get("title") or "").strip()
    body = str(args.get("body") or "").strip()
    if not body:
        return ToolOutcome(
            "A note needs a body.",
            {"name": "notes_create", "status": "error", "error": "Empty body."},
        )
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        note = notes_service.create(
            db,
            owner=user,
            title=title,
            body=body,
            author=AUTHOR_MODEL,
            group=context.data_group,
        )
        return ToolOutcome(
            f"Saved note {note.id} — {note.title!r}.",
            {
                "name": "notes_create",
                "query": note.title,
                "status": "ok",
                "results": [{"title": note.title, "id": note.id}],
            },
        )


async def _run_notes_edit(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        note = notes_service.get(db, str(args.get("id") or ""), user, context.data_group)
        if note is None or note.owner_id != context.owner_id:
            return ToolOutcome(
                "There is no such note, or it belongs to someone else. A note "
                "shared with you can be read but not changed.",
                {"name": "notes_edit", "status": "error", "error": "Not writable."},
            )
        notes_service.update(
            db,
            note,
            title=args.get("title"),
            body=args.get("body"),
        )
        return ToolOutcome(
            f"Updated note {note.id}.",
            {
                "name": "notes_edit",
                "query": note.title,
                "status": "ok",
                "results": [{"title": note.title, "id": note.id}],
            },
        )


async def _run_notes_delete(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        note = notes_service.get(db, str(args.get("id") or ""), user, context.data_group)
        if note is None or note.owner_id != context.owner_id:
            return ToolOutcome(
                "There is no such note, or it belongs to someone else.",
                {"name": "notes_delete", "status": "error", "error": "Not writable."},
            )
        title = note.title
        notes_service.delete(db, note)
        return ToolOutcome(
            f"Deleted note {title!r}.",
            {"name": "notes_delete", "query": title, "status": "ok", "results": []},
        )


# --- The chat's scratch document ---------------------------------------------
async def _run_scratch_write(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Write into the pad the person can see beside the conversation.

    Opens its own session, like every other runner: a generation outlives the
    session that resolved it.

    `append` is a service function rather than a read-and-concatenate here,
    because two calls in one round would otherwise each read the same body and
    the second would drop the first.
    """
    with session_scope() as db:
        chat = db.get(Chat, context.chat_id) if context.chat_id else None
        if chat is None:
            return ToolOutcome(
                "There is no chat to write into.",
                {"name": "scratch_write", "status": "error", "error": "No chat."},
            )
        doc = scratch_service.for_chat(db, chat)
        text = str(args.get("text") or "")
        if str(args.get("mode") or "append").strip().lower() == "replace":
            scratch_service.update(db, doc, body=text, author=AUTHOR_MODEL)
            what = "Replaced"
        else:
            scratch_service.append(db, doc, text, author=AUTHOR_MODEL)
            what = "Added to"
        return ToolOutcome(
            f"{what} the scratch document ({len(doc.body)} characters). "
            "It is on screen beside the conversation.",
            {
                "name": "scratch_write",
                "query": doc.title,
                "status": "ok",
                "results": [],
                # Opens the tab, the same way a file tool does. Never brings it
                # to the front -- see `canvas.open_tab`.
                "canvas": {
                    "key": f"scratch:{chat.id}",
                    "title": doc.title,
                    "source": "scratch",
                },
            },
        )


# --- Personality -------------------------------------------------------------
# --- Memory ------------------------------------------------------------------
async def _run_memory_add(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    content = str(args.get("content") or "").strip()
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        try:
            memory = memories_service.add(
                db, owner=user, content=content, author=AUTHOR_MODEL, group=context.data_group
            )
        except ValueError as exc:
            return ToolOutcome(
                str(exc), {"name": "memory_add", "status": "error", "error": str(exc)}
            )

        note = ""
        if len(content) > memories_service.MAX_MEMORY_CHARS:
            # Trimmed rather than refused, with the model told so -- it can then
            # decide to put the long version in a note.
            note = (
                f" It was shortened to {memories_service.MAX_MEMORY_CHARS} characters; "
                f"use notes for anything longer."
            )
        return ToolOutcome(
            f"Remembered: {memory.content}{note}",
            {
                "name": "memory_add",
                "query": memory.content,
                "status": "ok",
                "results": [],
            },
        )


async def _run_memory_forget(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Remove one memory, or refuse and say why.

    Exact match first, then substring, and an ambiguous substring removes
    nothing. This used to be a case-insensitive substring FIRST-match delete
    with nothing warning about it, so `memory_forget("coffee")` against "Drinks
    coffee black" and "Allergic to coffee" silently deleted whichever was older
    -- a wrong deletion nobody would ever find out about, from a tool whose
    description invited exactly the short fragment that misfires.

    Exact-first is not a nicety: without it, quoting a memory in full still
    fails whenever that text happens to be a substring of another one.
    """
    wanted = str(args.get("content") or "").strip().lower()
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        records = memories_service.all_for(db, user, context.data_group)
        if not wanted:
            return ToolOutcome(
                "Say which memory to remove, quoting its text.",
                {"name": "memory_forget", "status": "error", "error": "Nothing given."},
            )

        exact = [m for m in records if m.content.strip().lower() == wanted]
        matches = exact or [m for m in records if wanted in m.content.lower()]

        if not matches:
            return ToolOutcome(
                "No memory matches that. The full list is in the prompt already.",
                {"name": "memory_forget", "status": "error", "error": "No match."},
            )
        if len(matches) > 1:
            listed = "\n".join(f"- {m.content}" for m in matches[:10])
            return ToolOutcome(
                f"That matches {len(matches)} memories, so nothing was removed. "
                f"Quote the whole text of the one you mean:\n{listed}",
                {"name": "memory_forget", "status": "error", "error": "Ambiguous."},
            )

        content = matches[0].content
        memories_service.delete(db, matches[0])
        return ToolOutcome(
            f"Forgotten: {content}",
            {"name": "memory_forget", "query": content, "status": "ok", "results": []},
        )


# --- Reports -----------------------------------------------------------------
async def _run_report_write(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    title = str(args.get("title") or "").strip()
    body = str(args.get("body") or "").strip()
    summary = str(args.get("summary") or "").strip()
    if not body:
        return ToolOutcome(
            "A report needs a body. Write what you found, not a note saying you found it.",
            {"name": "report_write", "status": "error", "error": "Empty body."},
        )
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        if user is None:
            return ToolOutcome(
                "That report could not be filed.",
                {"name": "report_write", "status": "error", "error": "No such owner."},
            )
        report = reports_service.create(
            db,
            owner=user,
            title=title,
            body=body,
            summary=summary,
            source=SOURCE_CHAT,
            source_id=context.chat_id or "",
            model_id=context.model_id or "",
            group=context.data_group,
        )
        return ToolOutcome(
            f"Filed report {report.id} — {report.title!r}. "
            "The reader will find it under Reports; they cannot reply to it there.",
            {
                "name": "report_write",
                "query": report.title,
                "status": "ok",
                "results": [{"title": report.title, "id": report.id}],
            },
        )


async def _run_report_search(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    query = str(args.get("query") or "").strip()
    vector = await _query_vector(query)
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        found = (
            reports_service.search(
                db, user, query, limit=8, vector=vector, group=context.data_group
            )
            if query
            else reports_service.recent(db, user, limit=8, group=context.data_group)
        )
        event = {
            "name": "report_search",
            "query": query,
            "status": "ok",
            "results": [{"title": r.title, "id": r.id} for r in found],
        }
        if not found:
            return ToolOutcome("There are no reports matching that.", event)
        lines = ["Reports:"]
        for report in found:
            when = report.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"\n[{report.id}] {when} — {report.title}\n{reports_service.snippet(report)}"
            )
        lines.append("\nUse report_get with an id to read one in full.")
        return ToolOutcome("\n".join(lines), event)


async def _run_report_get(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        report = reports_service.get(db, str(args.get("id") or ""), user, context.data_group)
        if report is None:
            return ToolOutcome(
                "There is no such report.",
                {"name": "report_get", "status": "error", "error": "Not found."},
            )
        when = report.created_at.strftime("%Y-%m-%d %H:%M")
        return ToolOutcome(
            f"{report.title}\nFiled {when}\n\n{report.body}",
            {
                "name": "report_get",
                "query": report.title,
                "status": "ok",
                "results": [{"title": report.title, "id": report.id}],
            },
        )


# --- Skills ------------------------------------------------------------------
async def _run_skill_get(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    name = str(args.get("name") or "").strip()
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        skill = skills_service.by_name(db, name, user, context.data_group)
        # Enforced here and not only in the listing. Without this the per-chat
        # narrowing is advisory: a model can name a skill it was never shown --
        # from an earlier turn, from a note -- and the runner would fetch it.
        if skill is not None and skill.name in {
            skills_service.slugify(off) for off in context.skills_off
        }:
            skill = None
        if skill is None:
            return ToolOutcome(
                f"There is no skill called {name!r}.",
                {"name": "skill_get", "status": "error", "error": "Not found."},
            )
        return ToolOutcome(
            f"Skill {skill.name}: {skill.description}\n\n{skill.body}",
            {
                "name": "skill_get",
                "query": skill.name,
                "status": "ok",
                "results": [{"title": skill.name, "id": skill.id}],
            },
        )


async def _run_skill_create(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        try:
            skill = skills_service.create(
                db,
                owner=user,
                name=str(args.get("name") or ""),
                description=str(args.get("description") or ""),
                body=str(args.get("body") or ""),
                author=AUTHOR_MODEL,
                group=context.data_group,
            )
        except skills_service.SkillError as exc:
            return ToolOutcome(
                str(exc), {"name": "skill_create", "status": "error", "error": str(exc)}
            )
        return ToolOutcome(
            f"Created skill {skill.name!r}.",
            {
                "name": "skill_create",
                "query": skill.name,
                "status": "ok",
                "results": [{"title": skill.name, "id": skill.id}],
            },
        )


async def _run_skill_edit(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        skill = skills_service.by_name(
            db, str(args.get("name") or ""), user, context.data_group
        )
        if skill is None or skill.owner_id != context.owner_id:
            return ToolOutcome(
                "There is no such skill, or it belongs to someone else.",
                {"name": "skill_edit", "status": "error", "error": "Not writable."},
            )
        skills_service.update(
            db,
            skill,
            description=args.get("description"),
            body=args.get("body"),
            author=AUTHOR_MODEL,
            note=str(args.get("reason") or "")[:200],
        )
        return ToolOutcome(
            f"Updated skill {skill.name!r}. The previous version was kept and can "
            f"be restored.",
            {
                "name": "skill_edit",
                "query": skill.name,
                "status": "ok",
                "results": [{"title": skill.name, "id": skill.id}],
            },
        )


# --- The registry ------------------------------------------------------------
# --- Asking the reader -------------------------------------------------------
async def _run_ask_user(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Never reached on the normal path.

    `services.generation` intercepts every `ask` call before the runners are
    reached, because the answer comes from a person and `ToolContext` is a
    session-free snapshot that deliberately holds no way to reach one. Getting
    here means some other path called `run_tool` directly, and saying so is
    better than returning an empty answer the model would treat as a reply.

    It read `args["question"]`, singular, against a schema that declares
    `questions` and a list -- so the event it built always carried an empty
    `query`, and the card showed a refusal with no sign of what had been asked.
    Harmless only because this path is unreachable, which is exactly why nothing
    caught it: schema drift on a branch no test exercises. Tolerant of the same
    spellings `generation._questions_in` accepts, rather than importing it,
    which would be a circular import for one field on a dead path.
    """
    asked: Any = args.get("questions") or args.get("question") or ""
    if isinstance(asked, list):
        asked = asked[0] if asked else ""
    if isinstance(asked, dict):
        asked = asked.get("question") or ""
    question = str(asked).strip()
    return ToolOutcome(
        "That question could not be put to anyone, so it has gone unanswered. "
        "Carry on without it, or say what you need.",
        {
            "name": "ask_user",
            "kind": "ask",
            "query": question,
            "status": "error",
            "error": "No one was there to ask.",
            "results": [],
        },
    )


REGISTRY: dict[str, ToolDef] = {
    tool.name: tool
    for tool in (
        ToolDef(
            name="web_search",
            family=FAMILY_SEARCH,
            description=(
                "Search the web for current information. Use this when the answer "
                "depends on recent events, on facts you are unsure of, or on "
                "anything that may have changed since your training data. Returns "
                "a numbered list of results with titles, URLs and short extracts."
            ),
            parameters=_object(
                {
                    "query": {
                        "type": "string",
                        "description": "The search terms. Keep them short and specific.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "How many results to return.",
                    },
                },
                ["query"],
            ),
            run=_run_web_search,
        ),
        ToolDef(
            name="fetch",
            family=FAMILY_FETCH,
            description=(
                "Retrieve one web page and read it as text. Use it on an address "
                "you already have — from a search result, from the person you are "
                "talking to, or from a link in a page you have just read. "
                "Redirects are followed and the markup is removed, so what comes "
                "back is the prose rather than the HTML. It cannot run "
                "JavaScript: a page that comes back empty is usually one that "
                "builds itself in the browser rather than one that is missing. It "
                "is not a general HTTP client — GET only, no headers, no body — "
                "and a long page is cut off at the end."
            ),
            parameters=_object(
                {
                    "url": {
                        **_STRING,
                        "description": "The http or https address of the page.",
                    }
                },
                ["url"],
            ),
            run=_run_fetch,
        ),
        ToolDef(
            name="knowledge_search",
            family=FAMILY_KNOWLEDGE,
            description=(
                "Search the user's own collected documents, files and saved web "
                "pages. Use this before searching the web when the question is "
                "about their material rather than about the world."
            ),
            parameters=_object(
                {"query": {**_STRING, "description": "Words likely to appear in the document."}},
                ["query"],
            ),
            run=_run_knowledge_search,
        ),
        ToolDef(
            name="knowledge_get",
            family=FAMILY_KNOWLEDGE,
            description=(
                "Read one knowledge document, by the id a search returned. A "
                "long one is cut off at the end rather than refused, and you "
                "are told when that happened."
            ),
            parameters=_object({"id": _STRING}, ["id"]),
            run=_run_knowledge_get,
        ),
        ToolDef(
            name="notes_search",
            family=FAMILY_NOTES,
            description=(
                "Search your notes. These are things you or the user wrote down in "
                "earlier conversations. With no query, returns the most recent."
            ),
            parameters=_object({"query": _STRING}, []),
            run=_run_notes_search,
        ),
        ToolDef(
            name="notes_get",
            family=FAMILY_NOTES,
            description="Read one note in full, by the id a search returned.",
            parameters=_object({"id": _STRING}, ["id"]),
            run=_run_notes_get,
        ),
        ToolDef(
            name="notes_create",
            family=FAMILY_NOTES,
            description=(
                "Write a note. Use this for something worth having in a later "
                "conversation that is too long or too detailed for a memory: a "
                "procedure, a summary, a set of preferences with reasons."
            ),
            parameters=_object(
                {"title": _STRING, "body": {**_STRING, "description": "Markdown."}},
                ["title", "body"],
            ),
            run=_run_notes_create,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="notes_edit",
            family=FAMILY_NOTES,
            description="Change a note you can write to. Omit a field to leave it alone.",
            parameters=_object({"id": _STRING, "title": _STRING, "body": _STRING}, ["id"]),
            run=_run_notes_edit,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="notes_delete",
            family=FAMILY_NOTES,
            description="Delete a note that is no longer true or useful.",
            parameters=_object({"id": _STRING}, ["id"]),
            run=_run_notes_delete,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="scratch_write",
            family=FAMILY_SCRATCH,
            description=(
                "Write into this chat's scratch document, which the person can "
                "see and edit beside the conversation. Use it for something you "
                "are building up as you work — a draft, a table of findings, a "
                "list you keep adding to — rather than putting it in the reply "
                "and rewriting the whole thing each turn. It is not searchable "
                "later and belongs to this chat alone; use a note for anything "
                "worth keeping beyond it."
            ),
            parameters=_object(
                {
                    "mode": {
                        **_STRING,
                        "enum": ["append", "replace"],
                        "description": "append is the default.",
                    },
                    "text": {**_STRING, "description": "Markdown."},
                },
                ["text"],
            ),
            run=_run_scratch_write,
            # What a tool does to the *world the four modes govern*, which is the
            # machine -- and this cannot touch it. RISK_WRITE would put an
            # approval card on screen every time the model jotted a paragraph,
            # which is exactly the interruption batching exists to prevent. The
            # same argument `plan_update` carries. An administrator who
            # disagrees puts it in `deny_default`.
            risk=RISK_READ,
        ),
        ToolDef(
            name="memory_add",
            family=FAMILY_MEMORY,
            description=(
                "Remember one short, durable fact about the user — a preference, a "
                "constraint, a name, how they like to be addressed. Every memory is "
                "put in front of you on every turn, up to a budget, so keep them few "
                "and keep them short; text over the limit is shortened rather than "
                "refused, and you are told. Check what is already remembered before "
                "adding: a fact you have stored already in slightly different words "
                "costs the same again and makes both of them harder to remove. Never "
                "store a password, a key or anything else secret."
            ),
            parameters=_object(
                {"content": {**_STRING, "description": "One fact, in one sentence."}},
                ["content"],
            ),
            run=_run_memory_add,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="memory_forget",
            family=FAMILY_MEMORY,
            description=(
                "Remove a memory that is no longer true. Quote it in full — the "
                "whole sentence as it appears in your prompt. A fragment that "
                "matches more than one removes nothing and tells you which ones it "
                "matched, because deleting the wrong memory is not something anyone "
                "would find out about."
            ),
            parameters=_object(
                {"content": {**_STRING, "description": "The memory's whole text."}},
                ["content"],
            ),
            run=_run_memory_forget,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="report_write",
            family=FAMILY_REPORT,
            description=(
                "File a report: a finished piece of work, written for the person "
                "to read later. Use this when you have been asked for one, and "
                "when you finish a long piece of work whose result is worth "
                "keeping — an investigation, a summary of what you found, an "
                "account of what you changed. A report is read on its own, away "
                "from this conversation and possibly long after it, and THE "
                "READER CANNOT REPLY TO IT. So write it whole: say what you were "
                "asked, what you found and what you conclude, and do not refer "
                "to 'the above' or ask a question at the end."
            ),
            parameters=_object(
                {
                    "title": {
                        **_STRING,
                        "description": (
                            "One line naming what this is about, as it will appear "
                            "in a list of dozens. 'Build failures this week', not "
                            "'Report' or 'Results'."
                        ),
                    },
                    "body": {
                        **_STRING,
                        "description": (
                            "The report itself, in Markdown. Headings and lists are "
                            "rendered. This is the whole of what the reader gets, so "
                            "it should stand on its own with no further context."
                        ),
                    },
                    "summary": {
                        **_STRING,
                        "description": (
                            "One sentence for the list page, so the report can be "
                            "triaged without opening it. Say the finding, not the "
                            "subject: 'Three tests fail on ARM only', not 'About the "
                            "test failures'. Omit it and the first line of the body "
                            "is used instead."
                        ),
                    },
                },
                ["title", "body"],
            ),
            run=_run_report_write,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="report_search",
            family=FAMILY_REPORT,
            description=(
                "Search reports filed earlier, yours and the reader's. With no "
                "query, returns the most recent. Worth doing before writing a "
                "recurring report, so this week's can say what changed since last "
                "week's rather than repeating it."
            ),
            parameters=_object({"query": _STRING}, []),
            run=_run_report_search,
        ),
        ToolDef(
            name="report_get",
            family=FAMILY_REPORT,
            description="Read one report in full, by the id a search returned.",
            parameters=_object({"id": _STRING}, ["id"]),
            run=_run_report_get,
        ),
        ToolDef(
            name="skill_get",
            family=FAMILY_SKILLS,
            description=(
                "Read the full instructions for one of the skills listed in your "
                "prompt. Do this before following a skill — the list gives only its "
                "name and what it is for."
            ),
            parameters=_object({"name": _STRING}, ["name"]),
            run=_run_skill_get,
        ),
        ToolDef(
            name="skill_create",
            family=FAMILY_SKILLS,
            description=(
                "Write a new skill: a reusable procedure for a task you expect to be "
                "asked again. The description must say when to use it, since that is "
                "all you will see next time."
            ),
            parameters=_object(
                {
                    "name": {**_STRING, "description": "Short slug, e.g. 'weekly-report'."},
                    "description": {**_STRING, "description": "When to use this skill."},
                    "body": {**_STRING, "description": "The instructions, in Markdown."},
                },
                ["name", "description", "body"],
            ),
            run=_run_skill_create,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="skill_edit",
            family=FAMILY_SKILLS,
            description=(
                "Improve one of your skills. The previous version is kept and can be "
                "restored, so say why you changed it."
            ),
            parameters=_object(
                {
                    "name": _STRING,
                    "description": _STRING,
                    "body": _STRING,
                    "reason": {**_STRING, "description": "Why the change was made."},
                },
                ["name"],
            ),
            run=_run_skill_edit,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="ask_user",
            family=FAMILY_ASK,
            description=(
                "Ask the person you are talking to one or more questions, and wait "
                "for their answers before going on. Use it when you genuinely need "
                "a decision only they can make — which of several approaches to "
                "take, a detail you cannot infer, permission for something "
                "consequential.\n\n"
                "**Always give options.** A question with no options is a blank box, "
                "and a blank box asks the person to do the thinking you were meant "
                "to do: offer the two to six answers you actually think are "
                "plausible, in the order you would recommend them. Do NOT add an "
                "option meaning “other”, “something else”, “none of these” or "
                "“let me type it” — one is added for you, on every question, with a "
                "box behind it. Yours would have no box and would do nothing.\n\n"
                "Say whether the options are exclusive. `multiple: false` (the "
                "default) is for alternatives, where picking one rules out the "
                "rest; `multiple: true` is for a set, where any number may be "
                "chosen. Give an option a `description` wherever the label alone "
                "does not say what choosing it would mean — that is what makes a "
                "real decision possible rather than a guess between two words.\n\n"
                "Ask everything you need in ONE call: they answer the whole card at "
                "once and it costs them a single interruption, where asking twice "
                "in a row costs two. Do not use it for anything you can work out "
                "yourself, and never ask for a password, a key or any other secret."
            ),
            parameters=_object(
                {
                    "questions": {
                        "type": "array",
                        "description": (
                            "The questions to put, answered together. Ask up to "
                            "about four at a time; more than that is a form, not a "
                            "conversation."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    **_STRING,
                                    "description": "One question, in plain language.",
                                },
                                "options": {
                                    "type": "array",
                                    "description": (
                                        "The answers to offer, two to six of them. "
                                        "Required. Never include an “other” or "
                                        "“something else” option — one is always "
                                        "added for you."
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {
                                                **_STRING,
                                                "description": (
                                                    "The choice itself, in a few words."
                                                ),
                                            },
                                            "description": {
                                                **_STRING,
                                                "description": (
                                                    "Optional: one line on what "
                                                    "choosing this would mean, where "
                                                    "the label alone does not say."
                                                ),
                                            },
                                        },
                                        "required": ["label"],
                                    },
                                },
                                "multiple": {
                                    "type": "boolean",
                                    "description": (
                                        "Whether more than one option may be chosen. "
                                        "False (the default) for alternatives, true "
                                        "for a set."
                                    ),
                                },
                            },
                            "required": ["question", "options"],
                        },
                    },
                },
                ["questions"],
            ),
            # Never resolved by this runner. The reader answers it, in every
            # mode, and the loop turns their answer into the outcome -- see
            # services/interaction.py. The runner exists so that a call reaching
            # it by some path that skipped the loop fails loudly rather than
            # silently returning nothing.
            run=_run_ask_user,
            risk=RISK_ASK,
        ),
    )
}


def _family_allowed(
    family: str,
    *,
    config: dict,
    capabilities: dict,
    allowed: dict,
    images: bool = False,
    schedules: bool = False,
    subagents: bool = False,
) -> bool:
    """Whether one family is on for this chat.

    A model configured before the per-tool flags existed has no `tool_*` keys.
    Absent counts as on when `tools` is on, so an upgrade does not silently take
    web search away from every model already set up for it.
    """
    gate = gate_of(family)
    default = bool(capabilities.get("tools"))
    if not capabilities.get(f"tool_{gate}", default):
        return False

    if gate == FAMILY_SEARCH:
        return bool(
            allowed.get("tools.web_search")
            and config.get("enabled")
            and not search_service.availability(str(config.get("provider") or "ddgs"))
        )
    if gate == FAMILY_FETCH:
        # Its own instance switch, and no `library.use`. The switch is worth
        # having on its own: it stops a *model* fetching while the `@`-link
        # attach path keeps working, because that one is a person's instruction
        # rather than a model's choice.
        return bool(allowed.get("tools.fetch") and config.get("fetch_enabled"))
    if gate == FAMILY_IMAGE:
        # Its own branch rather than a name in the tuple below, and the second
        # half is why: an instance with no ComfyUI, or one with no checkpoints
        # listed, must not offer this at all. A model that calls it there spends
        # a round to be told the thing it was offered does not work, which is
        # the shape `resolve_tools` already refuses for `skill_get` with an
        # empty library. `settings_store.images_ready` answers all three.
        return bool(allowed.get("tools.image") and images)
    if gate == FAMILY_SCHEDULE:
        # `schedule.use` rather than a `tools.schedule` of its own: a reader who
        # may set a schedule up by hand may say so to a model instead, and a
        # second permission beside the first would only ever be answered "the
        # same as that one". `schedules` is the instance switch, passed in for
        # the reason `images` is -- an instance with scheduling off must not
        # offer this at all, or a model spends a round being told the tool it
        # was handed does not work.
        return bool(allowed.get("schedule.use") and schedules)
    if gate == FAMILY_SUBAGENT:
        # Its own permission and its own instance switch, for the reason the
        # image tool has both: what this costs is a second reply, which is not
        # a cost the tools around it have, and an instance whose endpoint is one
        # local card has a real reason to say no. `subagents` is passed in
        # rather than read here so that the whole gate is answered from the
        # snapshot `resolve_tools` already took.
        return bool(allowed.get("tools.subagent") and subagents)
    if gate in (
        FAMILY_CUSTOM,
        FAMILY_MCP,
        FAMILY_ASK,
        FAMILY_SCRATCH,
        FAMILY_REPORT,
    ):
        # Deliberately without `library.use`: an HTTP endpoint an administrator
        # wrote has nothing to do with this person's own documents and notes,
        # and requiring the library permission for it would be a coincidence of
        # naming rather than a rule. The same goes for being asked a question,
        # for a pad that belongs to this chat and goes nowhere else, for what a
        # model makes of itself and of the person in front of it, and for
        # filing a report -- which is addressed to the reader rather than kept
        # for the model, and is the fallback destination for scheduled work, so
        # gating it behind the library would switch that off for anyone whose
        # instance does not use one.
        return bool(allowed.get(f"tools.{gate}"))
    return bool(allowed.get(f"tools.{gate}") and allowed.get("library.use"))


def _row_defs(db: DBSession, user: User | None, *, everything: bool = False) -> list[ToolDef]:
    """Tool definitions built from rows, in the order they claim names.

    Custom tools first, then MCP servers, because a custom tool's name is
    written by hand and refused if it collides while an MCP tool's is derived
    and renamed silently -- the one that can adapt should be the one that has to.

    Imported here rather than at the top because both modules need `ToolDef`
    from this one.
    """
    from lembas.services import custom_tools
    from lembas.services.mcp import registry as mcp_registry

    custom = custom_tools.tool_defs(db, user, everything=everything)
    taken = {*REGISTRY, *(tool.name for tool in custom)}
    return [*custom, *mcp_registry.tool_defs(db, user, everything=everything, taken=taken)]


def _schedule_defs() -> list[ToolDef]:
    """The scheduling tools.

    Not in `REGISTRY` even though they need no rows and no settings to build,
    because the module they live in imports `services/tools.py` for `ToolDef`
    and the risk constants -- so importing it back at module scope is a cycle.
    A function keeps the import inside the call, which is the same shape
    `_agent_defs` and `_image_defs` already have.
    """
    from lembas.services.schedule import tool as schedule_tool

    return schedule_tool.tool_defs()


def _subagent_registry_defs() -> list[ToolDef]:
    from lembas.services import subagent as subagent_service

    return subagent_service.tool_defs()


def _subagent_defs(db: DBSession, chat: Chat, user: User | None) -> list[ToolDef]:
    """The helper tool, or nothing when this chat's model cannot be its own
    helper (it serves one request at a time) -- a tool every call of which is
    refused is one a model finds out about one wasted round at a time."""
    from lembas.services import subagent as subagent_service

    if subagent_service.capacity_refusal(db, chat):
        return []
    return subagent_service.tool_defs()


def _image_defs(db: DBSession, values: dict | None = None) -> list[ToolDef]:
    """The image tool, whose schema carries this instance's own choices.

    Built per request rather than at import, because the templates a model may
    name and the checkpoints it may draw with are rows and settings. That is the
    same reason a custom tool cannot live in `REGISTRY`, and it is why this has
    to be listed in `registry(db)` below as well -- a name that resolves to no
    family is a tool whose guidance never reaches the model.
    """
    from lembas.services.images import tool as image_tool

    return [image_tool.tool_def(db, values if values is not None else settings_store.images(db))]


def _book(defs: list[ToolDef]) -> dict[str, ToolDef]:
    """Keyed by name, first claim winning.

    The built-ins are laid down first, so a row can never shadow one -- a tool
    called `notes_delete` that turns out to be somebody's HTTP endpoint is the
    kind of surprise that has no good failure mode.
    """
    book = dict(REGISTRY)
    for tool in defs:
        book.setdefault(tool.name, tool)
    return book


def registry(db: DBSession) -> dict[str, ToolDef]:
    """Every tool that exists on this instance, keyed by name, ungated.

    `REGISTRY` holds the built-ins alone, because it is built at import time and
    an administrator-defined tool is a row. Callers that only need to map a name
    back to a family use this; callers deciding what to *offer* use
    `resolve_tools`, which applies the gates as well.

    """
    return _book(
        [
            *_row_defs(db, None, everything=True),
            *_image_defs(db),
            # Listed here, ungated, or `harness._families` cannot map
            # `schedule_create` back to a family and the guidance never
            # reaches the model. That omission has cost two features their
            # instructions already.
            *_schedule_defs(),
            # The registry wants the tool's name and family, not this chat's
            # candidates -- so the bare definition, which needs no chat.
            *_subagent_registry_defs(),
        ]
    )


def families(db: DBSession) -> tuple[str, ...]:
    """Every family that exists, the built-ins in their fixed order first."""
    rows = tuple(tool.family for tool in _row_defs(db, None, everything=True))
    return (*FAMILIES, *rows)


def resolve_tools(
    db: DBSession,
    chat: Chat,
    user: User | None,
    speaker=None,
    *,
    user_defaults: bool = True,
) -> ToolSet:
    """Every tool this chat may call right now, with its runner attached.

    The capabilities are the **answering** model's. `tools` being off is the first
    gate and returns nothing at all.

    `user_defaults=False` leaves out the reader's own per-model defaults
    (`services/model_prefs.py`) -- for the Settings page, which lists what
    the model offers before them so a gate switched off there can come back.
    """
    from lembas.security import permissions
    from lembas.services import chat as chat_service

    capabilities = {}
    model = (
        chat_service.model_row(db, speaker)
        if speaker is not None
        else chat_service.model_for(db, chat)
    )
    if model is not None:
        capabilities = model.capabilities_json or {}

    if not capabilities.get("tools"):
        return ToolSet()

    allowed = permissions.resolve(db, user)
    config = settings_store.search(db)
    image_values = settings_store.images(db)
    images_ready = settings_store.images_ready(db)
    schedules_on = bool(settings_store.schedules(db).get("enabled"))
    subagents_on = bool(settings_store.subagents(db).get("enabled"))

    # Resolved against what this reader may see, not against everything that
    # exists: a tool restricted to a group is not offered outside it. The image
    # tool is built only when it could be offered, because building its schema
    # reads the workflow table and there is no sense doing that for an instance
    # with no ComfyUI.
    book = _book(
        [
            *_row_defs(db, user),
            *(_image_defs(db, image_values) if images_ready else []),
            *(_schedule_defs() if schedules_on else []),
            *(_subagent_defs(db, chat, user) if subagents_on else []),
        ]
    )

    # What this chat has switched off, applied AFTER the gates and never
    # instead of them. A chat can only ever *narrow* what the model's
    # capabilities, the reader's permissions and the instance configuration
    # already allow -- exactly as `chat.knowledge_bases` narrows
    # `knowledge_search` and can never widen it. A crafted request that turned
    # something on here would still be reaching for a tool the gates had
    # already removed.
    off = scoped_off(chat)
    # The reader's own defaults for the answering model, after the gates
    # like everything else under this line and minus whatever this chat switched
    # back on itself -- a chat's explicit setting wins over a person's default.
    if user_defaults and user is not None and model is not None:
        from lembas.services import model_prefs

        off = off | (model_prefs.tools_off(db, user, model.model_id) - scoped_on(chat))
    # Counted in the answering model's own data group: skills in another group
    # are not readable here, so they must not keep `skill_get` on offer.
    from lembas.services import data_groups

    empty_library = not skills_service.count_enabled(
        db,
        user,
        exclude=scoped_skills_off(chat),
        group=data_groups.for_speaker(db, user, chat, speaker),
    )

    # A scheduled task runs with nobody present, so `ask_user` cannot work here:
    # it pauses the reply and waits for a POST that will never come, until
    # `approval_timeout` expires -- a run that silently does nothing for fifteen
    # minutes and then gives up. Withdrawn from the offered set rather than
    # merely discouraged in `core.unattended`, because a rule living only in a
    # system message is one a page the model just read can argue with. The
    # fragment is the half that stops it *planning* around a tool it has not got.
    #
    # A subagent's chat is unattended for a different reason and arrives at the
    # same place, which is why the question asked is `unattended` and not the
    # kind: it is also where the *recursion* stops. A helper that could spawn a
    # helper is a fan-out with no bound anybody set.
    if unattended(chat):
        off = off | {FAMILY_ASK, FAMILY_SUBAGENT}

    # Everything that changes something, withheld. Set by `services/subagent.py`
    # on the chat it creates and by nothing else, so absent means on exactly as
    # every other key here does. Keyed on the tool's declared **risk** rather
    # than on a list of names, because a list is a thing that goes out of date
    # silently: a tool added next year would default into a read-only helper's
    # set unless somebody remembered.
    #
    # `RISK_EXECUTE` is deliberately not included. In an agent chat it is
    # governed by the mode and the allow list instead, which is a finer
    # instrument -- `git log` is a read whatever its risk class says.
    writes_off = scoped_writes_off(chat)

    # Reading and writing, split for the three gates where the two are genuinely
    # different decisions. A second check keyed on the tool's **risk**, applied
    # after the gate rather than instead of it -- so it can only ever narrow
    # what `_family_allowed` already allowed, and an instance that has never
    # looked at it behaves exactly as it did, all three defaulting on.
    #
    # Here rather than in `_family_allowed` because that one is given a family
    # and this needs the tool: the whole point is that two tools in one family
    # get different answers.
    def may_write(tool: ToolDef) -> bool:
        gate = gate_of(tool.family)
        if tool.risk != RISK_WRITE or gate not in permissions.SPLIT_GATES:
            return True
        return bool(allowed.get(f"tools.{gate}.write", True))

    return ToolSet(
        tuple(
            tool
            for tool in book.values()
            if _family_allowed(
                tool.family,
                config=config,
                capabilities=capabilities,
                allowed=allowed,
                images=images_ready,
                schedules=schedules_on,
                subagents=subagents_on,
            )
            and gate_of(tool.family) not in off
            and not (writes_off and tool.risk == RISK_WRITE)
            and may_write(tool)
            # Nothing to read and nothing to improve. Offering `skill_get` with
            # no skills is what makes a model spend a round looking one up and
            # being told it does not exist -- and `context.skills` already
            # vanishes, so the prompt says "read one with skill_get" above a
            # list that is not there. `skill_create` stays: writing the first
            # one is exactly what somebody with none needs.
            and not (empty_library and tool.name in _NEEDS_A_SKILL)
        )
    )


# Skills tools that are meaningless with an empty library.
_NEEDS_A_SKILL = frozenset({"skill_get", "skill_edit"})


def unattended(chat: Chat | None) -> bool:
    """Whether there is anybody who could answer a question in this chat.

    Two things make a chat unattended and they are not the same fact. A
    scheduled task's chat is one because of what starts it; a subagent's is one
    because of what it *is*. `Chat.unattended` is the column both now set, and
    the kind is still consulted beside it because the column was added to a
    table that already had task chats in it -- `sync_schema` backfills a new
    NOT NULL column with its type default, so every task chat written before
    this reads back as attended. Dropping the kind check would silently give
    every existing scheduled task a tool that stalls it for fifteen minutes.
    """
    if chat is None:
        return False
    return bool(getattr(chat, "unattended", False)) or chat.kind == KIND_TASK


def scoped_writes_off(chat: Chat | None) -> bool:
    """Whether this chat has had everything that changes something withdrawn.

    One key rather than a family list, because "may not write" is a property of
    the *conversation* and not of any one gate: a read-only helper must not
    write a note, file a report, save a memory or edit a file, and those are
    four gates it would otherwise have to name — and a fifth would arrive
    unnamed. Absent means writes are on, the same convention as everything else
    under `scope_json`.

    A person's temporary chat is the second way in: it reads the
    library and writes nothing, until `Keep this chat` makes it an ordinary one.
    """
    if chat is None:
        return False
    from lembas.services.chat import read_only_temporary

    if read_only_temporary(chat):
        return True
    return (getattr(chat, "scope_json", None) or {}).get("write") is False


def scoped_off(chat: Chat | None) -> frozenset[str]:
    """Gates this chat has switched off. **Absent means on**, always.

    One representation of "on" -- the key not being there -- so that "why is
    this off?" has one answer rather than two.
    """
    if chat is None:
        return frozenset()
    wanted = (getattr(chat, "scope_json", None) or {}).get("families") or {}
    return frozenset(str(name) for name, on in wanted.items() if on is False)


def scoped_on(chat: Chat | None) -> frozenset[str]:
    """Gates this chat switched back **on** over its owner's per-model default.
    Stored as `True` only then -- see `api/chats.py:set_scope` -- so
    absent still means "on unless something above says otherwise"."""
    if chat is None:
        return frozenset()
    wanted = (getattr(chat, "scope_json", None) or {}).get("families") or {}
    return frozenset(str(name) for name, on in wanted.items() if on is True)


def effective_off(db: DBSession, chat: Chat | None, user: User | None) -> frozenset[str]:
    """What this chat has off once its owner's default for its model is
    counted: its own offs, plus the default's, minus what it turned
    back on. For the menus and for gates read outside `resolve_tools`."""
    off = scoped_off(chat)
    if chat is None or user is None or not getattr(chat, "model_id", ""):
        return off
    from lembas.services import model_prefs

    return off | (model_prefs.tools_off(db, user, chat.model_id) - scoped_on(chat))


# What a gate is called in a menu. A gate covers several tools, so no single
# tool's label is the right name for it. Marked here, translated where shown.
GATE_LABELS = {
    "web_search": N_("Web search"),
    "fetch": N_("Fetching pages"),
    "knowledge": N_("Your knowledge library"),
    "notes": N_("Notes"),
    "memory": N_("Memory"),
    "skills": N_("Skills"),
    "ask": N_("Asking you questions"),
    "scratch": N_("Writing in the canvas"),
    "image": N_("Generating images"),
    "report": N_("Filing reports"),
    "schedule": N_("Scheduling work"),
    "subagent": N_("Sending helpers"),
    "agent": N_("Running commands"),
    "custom": N_("Custom tools"),
    "mcp": N_("MCP servers"),
    "prototypes": N_("Prototypes"),
}


def gate_label(gate: str, example: str = "") -> str:
    """What the menus call a gate, in the reader's language."""
    from lembas.services import tool_labels
    from lembas.web import i18n

    if gate in GATE_LABELS:
        return i18n.t(GATE_LABELS[gate])
    return tool_labels.label_for(example) or gate


def scoped_skills_off(chat: Chat | None) -> frozenset[str]:
    """Individual skills this chat has switched off, by name."""
    if chat is None:
        return frozenset()
    wanted = (getattr(chat, "scope_json", None) or {}).get("skills") or {}
    return frozenset(str(name) for name, on in wanted.items() if on is False)


def scoped_allow(chat: Chat | None) -> tuple[str, ...]:
    """Actions this chat has been told to stop asking about.

    The one key under `scope_json` that *widens* rather than narrows, and it is
    worth being explicit about why that does not break the rule beside it. That
    rule governs which tools a chat may reach, where a crafted POST turning
    something on would reach past gates the model's capabilities and the
    reader's permissions had already closed. This is a different axis: every
    tool here was offered already, and what is recorded is only whether the
    reader is asked again before it runs.

    What makes it safe is that **no pattern ever comes from a request**. Each
    entry is derived server-side in `api/chats.py:answer_interaction` from an
    item a person has just approved on a card, through `policy.subject` -- the
    same normaliser the matcher uses, so what is stored is exactly what will be
    compared, and it refuses to produce anything at all for a command line
    carrying a shell metacharacter. "Always" can therefore only ever mean "this
    exact thing again".

    There is a second server-side writer now: `services/subagent.py` puts its
    fixed safe list here when it creates a helper's chat. That does not weaken
    the property above -- the list is a constant in this codebase, the chat is
    created here and never by a request, and the model asking for the helper
    chooses none of it.
    """
    if chat is None:
        return ()
    wanted = (getattr(chat, "scope_json", None) or {}).get("allow") or []
    if not isinstance(wanted, list):
        return ()
    return tuple(str(entry) for entry in wanted if str(entry).strip())


def enabled_tools(db: DBSession, chat: Chat, user: User | None) -> list[dict[str, Any]]:
    """The tool schemas to offer for this chat.

    The shape is unchanged on purpose: the inspector and `build_request` want
    exactly this. Anything that will also *run* a tool wants `resolve_tools`.
    """
    return resolve_tools(db, chat, user).schemas


def context_for(
    db: DBSession,
    user: User | None,
    chat: Chat | None = None,
    *,
    tools: ToolSet | None = None,
    speaker=None,
) -> ToolContext:
    """The snapshot a running tool needs, taken while the session is open.

    `speaker` is the model answering, and it decides which model a tool acts *as*:
    whose endpoint the image
    reviewer and the Preserve-VRAM unload reach for. It defaults to the chat's own
    model.
    """
    from lembas.services import chat as chat_service
    from lembas.services import data_groups
    if chat is not None and speaker is None:
        speaker = chat_service.speaker_for(db, chat)

    return ToolContext(
        agent=None,
        owner_id=user.id if user else "",
        chat_id=chat.id if chat is not None else "",
        search_config=settings_store.search(db),
        image_config=settings_store.images(db),
        image_workflow_id=(chat.image_workflow_id or "") if chat is not None else "",
        image_checkpoint=(chat.image_checkpoint or "") if chat is not None else "",
        model_id=(speaker.model_id or "") if speaker is not None else "",
        connection_id=(speaker.connection_id or "") if speaker is not None else "",
        data_group=(
            data_groups.for_speaker(db, user, chat, speaker)
            if chat is not None
            else DEFAULT_GROUP
        ),
        base_ids=[base.id for base in chat.knowledge_bases] if chat is not None else [],
        skills_off=scoped_skills_off(chat),
        tools=tools.by_name if tools is not None else None,
        interaction_timeout=float(settings_store.agents(db)["approval_timeout"]),
        unattended=unattended(chat),
    )


def parse_arguments(tool: ToolDef | None, raw: str) -> dict[str, Any]:
    """One tool call's arguments, as a dict, however badly they were spelled.

    **The only place a call's arguments are interpreted.** It used to live
    inside `run_tool`, while the approval card had its own plain `json.loads`
    that returned `{}` on failure -- so a model emitting malformed JSON got a
    card headed "Run a command" with an empty body, while the fallback below
    handed the raw string to `shell_run` as its command and ran it. The card
    showed one thing and the machine did another, and `policy.decide` was
    handed an empty command line it could match against neither list.

    So the loop parses once and the same dict reaches the card, the policy and
    the runner. Callers that only have a name resolve the `ToolDef` first; a
    `None` tool still parses valid JSON, which is what an unknown name needs.
    """
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        # Small models emit malformed argument JSON often enough that this is a
        # normal path, not an exceptional one. Treat the whole string as the
        # tool's first argument rather than giving up: what it says is required,
        # else the first thing it declares, and only then a guess -- a schema
        # somebody else wrote need not have either.
        parameters = tool.parameters if tool is not None else {}
        properties = parameters.get("properties") or {}
        names = parameters.get("required") or list(properties) or ["query"]
        parsed = {str(names[0]): raw.strip()}
    if not isinstance(parsed, dict):
        return {"query": str(parsed)}
    return parsed


async def run_tool(
    context: ToolContext,
    name: str,
    arguments: str,
    *,
    parsed: dict[str, Any] | None = None,
) -> ToolOutcome:
    """Execute one tool call.

    Never raises. A tool that fails hands the model an explanation and lets it
    carry on -- a failed lookup should produce "I could not find that" rather
    than killing the whole reply.

    The lookup is against what was *offered*, not against everything that
    exists. Reaching for the registry directly meant a model naming a tool its
    chat was gated out of -- a family switched off for the model, a permission
    the reader does not have -- had it run anyway, because only the offer was
    ever filtered.

    `parsed` is the arguments the caller has already interpreted. The generation
    loop passes it so that what a person approved is what runs; a caller with
    only the raw string gets the same result, because both go through
    `parse_arguments`.
    """
    book = REGISTRY if context.tools is None else context.tools
    tool = book.get(name)
    if tool is None:
        return ToolOutcome(
            f"There is no tool called {name!r}.",
            {"name": name, "status": "error", "error": "Unknown tool."},
        )

    if parsed is None:
        parsed = parse_arguments(tool, arguments)
    try:
        return await tool.run(context, parsed)
    except Exception as exc:  # noqa: BLE001 - a tool must never kill the reply
        log.exception("tool %s failed", name)
        return ToolOutcome(
            f"The {name} tool failed: {exc}",
            {"name": name, "status": "error", "error": str(exc)[:200]},
        )


class ToolCallAccumulator:
    """Reassembles tool calls arriving as streamed fragments.

    An endpoint sends ``delta.tool_calls`` as a list of partial objects: the id
    and the function name arrive once, and ``arguments`` arrives as a string
    split across however many chunks the tokeniser produced. Entries are keyed
    by ``index`` because that is the only field guaranteed on every fragment --
    the id is absent from continuations, and matching on name breaks the moment
    a model calls the same tool twice in one turn.
    """

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, Any]] = {}

    def feed(self, fragments: list[dict[str, Any]]) -> None:
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            index = fragment.get("index")
            if not isinstance(index, int):
                # Some servers omit index entirely when there is only one call.
                index = 0
            call = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})

            if fragment.get("id"):
                call["id"] = str(fragment["id"])
            function = fragment.get("function") or {}
            if isinstance(function, dict):
                if function.get("name"):
                    call["name"] = str(function["name"])
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    call["arguments"] += arguments

    @property
    def calls(self) -> list[dict[str, Any]]:
        """Completed calls, in the order the endpoint indexed them."""
        return [
            {
                # An id is required when the results are sent back, and not
                # every server supplies one.
                "id": call["id"] or f"call_{index}",
                "name": call["name"],
                "arguments": call["arguments"],
            }
            for index, call in sorted(self._calls.items())
            if call["name"]
        ]

    def __bool__(self) -> bool:
        return bool(self.calls)


def assistant_turn(
    calls: list[dict[str, Any]],
    content: str,
    blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The assistant message to send back with the tool results.

    The endpoint needs its own tool_calls echoed before the tool replies, or it
    has nothing to match the tool_call_ids against.

    `blocks` is the turn in the endpoint's own words, when it sent one
    (Anthropic, whose thinking has to come back unchanged, signature and all, with
    the tool calls it led to). Carried only when there is one, so an OpenAI
    endpoint sees exactly the message it always did.
    """
    turn: dict[str, Any] = {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]},
            }
            for call in calls
        ],
    }
    if blocks:
        turn["assistant_blocks"] = list(blocks)
    return turn


def tool_turn(call: dict[str, Any], content: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call["id"],
        "name": call["name"],
        "content": content,
    }


def _row_source(db: DBSession):
    """One harness fragment per administrator-defined tool.

    The seam `prompts.register_source` exists for. The row supplies the default
    text and the admin page supplies the override, which is why a tool deleted
    and recreated under the same slug keeps whatever wording somebody chose for
    it -- the override outlives the row.

    Gated on the tool's own family, so the guidance appears exactly when the
    tool it describes is offered and never otherwise. An MCP server gets one
    fragment rather than one per advertised tool: forty entries on the prompts
    page is a page nobody would read.
    """
    from lembas.db.models import CustomTool, McpServer

    for row in db.scalars(select(CustomTool).order_by(CustomTool.position, CustomTool.slug)):
        yield prompts_service.Fragment(
            key=f"tool.custom_{row.slug}",
            label=row.name or row.slug,
            group=prompts_service.GROUP_TOOLS,
            order=500 + row.position,
            families=(f"{FAMILY_CUSTOM}:{row.slug}",),
            hint=f"Appears when the {row.slug} tool is offered.",
            default=row.guidance or "",
        )

    for server in db.scalars(select(McpServer).order_by(McpServer.position, McpServer.slug)):
        yield prompts_service.Fragment(
            key=f"tool.mcp_{server.slug}",
            label=server.name or server.slug,
            group=prompts_service.GROUP_TOOLS,
            order=600 + server.position,
            families=(f"{FAMILY_MCP}:{server.slug}",),
            hint=f"Appears when any tool from {server.name or server.slug} is offered.",
            default=server.guidance or "",
        )


__all__ = [
    "FAMILIES",
    "FAMILY_AGENT",
    "MAX_ROUNDS",
    "REGISTRY",
    "ToolCallAccumulator",
    "ToolContext",
    "ToolDef",
    "ToolOutcome",
    "ToolSet",
    "assistant_turn",
    "context_for",
    "enabled_tools",
    "families",
    "parse_arguments",
    "registry",
    "resolve_tools",
    "run_tool",
    "scoped_allow",
    "tool_turn",
]


# Registered at import. `services.tools` is imported by the chat routes, the
# generation service and the prompts admin, so the source is in place before
# anything renders a catalogue.
prompts_service.register_source(_row_source)
