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
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    AUTHOR_MODEL,
    DEFAULT_GROUP,
    KIND_TASK,
    SOURCE_CHAT,
    Chat,
    SkillRevision,
    User,
)
from lembas.db.session import session_scope
from lembas.services import harness_spec, plans, settings_store
from lembas.services import prompts as prompts_service
from lembas.services import reports as reports_service
from lembas.services import scratch as scratch_service
from lembas.services import search as search_service
from lembas.services.library import documents as documents_service
from lembas.services.library import memories as memories_service
from lembas.services.library import notes as notes_service
from lembas.services.library import skills as skills_service
from lembas.services.llm.openai_client import ToolCallAccumulator
from lembas.services.search.base import SearchError
from lembas.web.i18n import N_

log = logging.getLogger(__name__)


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

# The plan: `plan_write`, `plan_update` and -- in Plan mode -- `plan_submit`, the
# one structure the harness spec has both projects keep per conversation. Not
# gated by a permission or a model flag, like `ask`: a chat that has tools at
# all has these, because the plan is how the person sees where the work is.
# Withheld from a helper's chat, which has one reply and nobody reading it.
FAMILY_PLAN = "plan"

# The chat's own working surface -- the canvas panel's scratch document.
# Deliberately not part of `notes`: a note is a durable artefact of the reader's
# that outlives the chat and is searchable, while this is the chat's own record
# of what it is doing, which is the line `plan_update` sits on. It is also its
# own switch, because narrowing notes off must not silently take the pad too.
FAMILY_SCRATCH = "scratch"

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
# The family a helper's one tool, `report`, is in: gated as `subagent`
# (`gate_of`), so the permission that let the parent delegate covers the reply;
# its own name, so the guidance about *sending* helpers (`tool.subagent`) is
# not admitted into a helper's prompt by the family it shares.
FAMILY_REPORT_BACK = "subagent:report"

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
    FAMILY_PLAN,
    FAMILY_IMAGE,
    FAMILY_REPORT,
    FAMILY_SCHEDULE,
    FAMILY_SUBAGENT,
)


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
# The spec's `interact` (2.0.0): a call whose effect is a message to somebody
# -- `task` starts a helper, `job_send` and `report` carry words between a
# helper and its main agent. Nothing on disk changes and nothing runs, so no
# mode asks about it and Plan mode does not withhold it; it is its own class
# rather than `read` so the table says what it is.
RISK_INTERACT = "interact"

RISKS = (RISK_READ, RISK_WRITE, RISK_EXECUTE, RISK_ASK, RISK_INTERACT)

# How much of a fetched page reaches the model in one call. `fetch()` returns up
# to 120_000 characters, which is roughly thirty thousand tokens -- one call would
# fill an ordinary window and, in an agent chat, spend the whole output budget on
# a single page. So `web_fetch` reads it in parts of this size, with `offset`
# for the next one -- the LLeMbas CLI's page size too.
MAX_FETCH_CHARS = 20_000

# The same bound for a knowledge document, and it was missing. `knowledge_get`
# returned `extracted_text` whole while every sibling reader capped and said so
# -- `web_fetch` above, `file_read`, the memories block, the skill index, the
# project listing. `MAX_EXTRACTED_CHARS` is 120_000 by default and an
# administrator can raise it, so one call on a long PDF filled an ordinary
# window with nothing anywhere reporting that it had.
#
# Larger than a fetched page on purpose. Somebody put this document in the
# library deliberately and named it in a search; a page the model followed a
# link to is a guess. Cut with the model told so rather than refused, which is
# what `web_fetch` and `file_read` both do -- a reader that fails on exactly the
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
    # Every base a `knowledge_search` here may name with `base`, as
    # `{"id", "name"}`: the scoped ones, else all the owner can see in this
    # data group. Snapshotted for the reason the rest of this is.
    bases: list[dict[str, str]] = field(default_factory=list)
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

# The two kinds of helper a `task` may name, as the harness spec's built-in
# agents (`prompts/agents/*.md`): the same names and descriptions the LLeMbas
# CLI offers. `explore` is the default and reads only; `general` has the full
# toolset the parent's chat allows and may write (the former `write: true`).
HELPER_AGENTS: tuple[tuple[str, str], ...] = (
    (
        "explore",
        "read-only research: find where things are, how they work, what a change would touch",
    ),
    (
        "general",
        "a self-contained piece of work with the full toolset, under the same permission mode",
    ),
)

# What LLeMbas fills the harness spec's tool-description variables with. One
# place, read by the definitions below and by the test that holds them equal
# to the spec. `agents`: the subagents a `task` may name, `name — description`
# separated by "; " as the spec's variable says.
SPEC_VARIABLES: dict[str, str] = {
    "agents": "; ".join(f"{name} — {about}" for name, about in HELPER_AGENTS)
}

# This server's facts for the library tools' variables (spec 1.2.0), where the
# CLI says its own: one memory about the person rather than files and
# projects, notes without a scope, skills without categories, supporting files
# or project skills, and a skills list that shows each description whole.
LLEMBAS_VARS: dict[str, dict[str, str]] = {
    "memory": {
        "project_home": "",
        "targets": (
            "'user' — the person you are talking to: their name, what they do, their "
            "preferences, how they like to work, and the standing facts about them worth "
            "having in every conversation."
        ),
        "past_work": "",
        "when_seen": "What you save is in your instructions from the person's next message.",
        "target_values": "'user': what is remembered about the person you are talking to.",
    },
    "notes_search": {"kept": ""},
    "note_manage": {"create_scope": "", "scope": "", "records": ""},
    "skills_list": {"category": ""},
    "skill_view": {
        "files": (
            ". Read it before following a skill: the skills list gives only its name and "
            "what it is for."
        )
    },
    "skill_manage": {"create_where": "", "files": "", "revert_deleted": "", "index_cut": ""},
}


def _variables(name: str) -> dict[str, str]:
    return {**SPEC_VARIABLES, **LLEMBAS_VARS.get(name, {})}


def spec_description(name: str) -> str:
    """A shared tool's description, as the spec writes it, variables filled."""
    return harness_spec.fill(harness_spec.tools()[name]["description"], _variables(name))


def spec_parameters(name: str) -> dict[str, Any]:
    """A shared tool's parameters as this server offers them: the spec's,
    variables filled, narrowed by `narrow.llembas` (a new copy each time)."""
    return harness_spec.offered_parameters(name, _variables(name))


def spec_aliases() -> dict[str, str]:
    """Former name -> spec name, for every alias the spec lists for LLeMbas."""
    out: dict[str, str] = {}
    for name, spec in harness_spec.tools().items():
        for alias in (spec.get("aliases") or {}).get("llembas") or []:
            out[str(alias)] = name
    return out


def resolve_alias(
    name: str, args: dict[str, Any], offered: Mapping[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    """A tool call by a former name, as the call it means.

    The spec keeps each tool's old names per project (`aliases.llembas`), and a
    transcript or a model trained on the old name still uses it. Most aliases
    take the same arguments; the ones that do not are converted here: `todo`'s
    list becomes one "Work" phase for `plan_write` (the spec's conversion,
    `plans.from_todos`), and `subagent_run`'s task, title, context and write
    become `task`'s prompt and description, with `write` kept as the helper's
    own extra. A name that is no alias comes back unchanged.

    So does a name this request has a tool by. 1.3.0 rewrote every alias
    regardless, while the spec already listed `fetch`, `notes_get`,
    `memory_add` and the rest as former names of tools this server did not have
    yet, and every call to them answered "Unknown tool". `offered` is what this
    request was offered: given it, the call keeps its name when that name is
    offered or the alias's target is not. Without it, the registry stands in
    for the first check.
    """
    target = spec_aliases().get(name)
    if target is None:
        return name, args
    if name in (REGISTRY if offered is None else offered):
        return name, args
    if offered is not None and target not in offered:
        return name, args
    if name == "todo":
        plan = plans.from_todos(args.get("todos"))
        return target, {k: v for k, v in plan.items() if k in ("title", "phases")} if plan else {}
    # The library's former names (spec 1.2.0): each was one action of a tool
    # that is now several, so the action is filled in from the name.
    if name in ("memory_add", "memory_forget"):
        content = args.get("content")
        if name == "memory_add":
            return target, {"target": "user", "action": "add", "content": content}
        return target, {"target": "user", "action": "remove", "old_text": content}
    if name in ("notes_create", "notes_edit", "notes_delete"):
        action = name.split("_", 1)[1]
        kept = {k: v for k, v in args.items() if k in ("id", "title", "body")}
        return target, {"action": action, **kept}
    if name == "skill_create":
        return target, {
            "operations": [
                {
                    "action": "create",
                    "name": args.get("name"),
                    "description": args.get("description"),
                    "instructions": args.get("body"),
                }
            ]
        }
    if name == "skill_edit":
        # Its description and body as they were sent, either one alone: the
        # runner reads `_set` against the skill as it stands, since a patch
        # would need the text this call never quoted.
        return target, {
            "operations": [
                {
                    "action": "patch",
                    "name": args.get("name"),
                    "reason": args.get("reason"),
                    "_set": {"description": args.get("description"), "body": args.get("body")},
                }
            ]
        }
    if name == "subagent_run":
        prompt = str(args.get("task") or "").strip()
        briefing = str(args.get("context") or "").strip()
        if briefing:
            prompt = f"{prompt}\n\n## Context\n{briefing}" if prompt else briefing
        converted = {
            "description": str(args.get("title") or "").strip()[:60],
            "prompt": prompt,
        }
        if args.get("write"):
            converted["write"] = True
        return target, converted
    return target, args


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
async def _run_web_fetch(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Retrieve one URL and hand back a part of it as markdown.

    Straight through `services/fetch.py`, which owns the SSRF guard, the
    hand-rolled redirect loop that re-checks every hop, and the content-type
    sniff. Deliberately not a second HTTP client: the working notes already name three
    places that follow redirects by hand as the ceiling, and a fourth is how one
    of them loses its check.

    Read in parts the way the LLeMbas CLI's `web_fetch` reads a page: up to
    `MAX_FETCH_CHARS` from `offset`, the title as a heading, then the address,
    and a closing line naming the offset to call again with while more is left.
    """
    from lembas.services import fetch as fetch_service

    url = str(args.get("url") or "").strip()
    if not url:
        return ToolOutcome(
            "No address was given.",
            {"name": "web_fetch", "status": "error", "error": "No URL."},
        )
    try:
        offset = max(int(args.get("offset") or 0), 0)
    except (TypeError, ValueError):
        offset = 0

    try:
        page = await fetch_service.fetch(
            url,
            allow_private=bool(context.search_config.get("allow_private_fetch")),
            markdown=True,
        )
    except fetch_service.FetchError as exc:
        # Its messages are already written to be shown to a person, which is
        # close enough to being written for a model to act on.
        return ToolOutcome(
            f"That page could not be read: {exc.message}",
            {"name": "web_fetch", "query": url, "status": "error", "error": exc.message},
        )

    text = page.text
    part = text[offset : offset + MAX_FETCH_CHARS]
    left = len(text) - offset - MAX_FETCH_CHARS
    if left > 0:
        more = (
            f"\n\n({left} more characters — call again with offset "
            f"{offset + MAX_FETCH_CHARS})"
        )
    elif page.truncated:
        more = "\n\n(The page was longer than this and has been cut off.)"
    else:
        more = ""
    event = {
        "name": "web_fetch",
        "kind": "fetch",
        "query": page.title or url,
        "detail": page.url,
        "status": "ok",
        "results": [],
        "text": part[:2000],
    }
    heading = f"# {page.title}\n" if page.title else ""
    return ToolOutcome(f"{heading}{page.url}\n\n{part}{more}", event)


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


# --- Memory ------------------------------------------------------------------
async def _run_memory(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """The spec's `memory`: one change or a list, applied together or not at
    all (`memories.apply`, the CLI's semantics and messages). One target here,
    `user` -- what is remembered about the person; the spec's other targets
    are the CLI's files, and `narrow.llembas` does not offer them."""
    target = str(args.get("target") or "user").strip()
    if target != "user":
        return ToolOutcome(
            "There is one memory here, about the person you are talking to: target 'user'.",
            {"name": "memory", "status": "error", "error": "No such target."},
        )
    raw_ops = args.get("operations")
    if isinstance(raw_ops, list) and raw_ops:
        ops = [op for op in raw_ops if isinstance(op, dict)]
    elif args.get("action"):
        ops = [
            {
                "action": args.get("action"),
                "content": args.get("content"),
                "new_text": args.get("new_text"),
                "old_text": args.get("old_text"),
            }
        ]
    else:
        ops = []
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        result = memories_service.apply(db, owner=user, ops=ops, group=context.data_group)
    marks = "".join(
        {"add": "+", "remove": "−"}.get(str(op.get("action")), "~") for op in ops
    )
    if not result["ok"]:
        shown = result.get("entries")
        if shown is not None:
            listed = "\n".join(f"{i}. {entry}" for i, entry in enumerate(shown, 1)) or "(none)"
            text = f"{result['message']}\n\nCurrent entries ({result['usage']}):\n{listed}"
        else:
            text = f"{result['message']}\n\nuser profile: {result['usage']}"
        return ToolOutcome(
            text,
            {"name": "memory", "query": marks, "status": "error", "error": result["message"]},
        )
    return ToolOutcome(
        f"{result['message']} user profile: {result['usage']}. Saved — this update is "
        "complete; do not repeat it.",
        {
            "name": "memory",
            "query": "; ".join(
                str(op.get("content") or op.get("new_text") or op.get("old_text") or "")
                for op in ops
            )[:200],
            "detail": marks,
            "status": "ok",
            "results": [],
        },
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
def _skill_hidden(context: ToolContext, skill: Any) -> bool:
    """Switched off for this chat. Enforced in the runners and not only in the
    listing: without it a model could name a skill it was never shown -- from
    an earlier turn, from a note -- and be handed it."""
    return skill is not None and skill.name in {
        skills_service.slugify(off) for off in context.skills_off
    }


async def _run_skills_list(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        shown = skills_service.enabled_for(
            db, user, exclude=context.skills_off, group=context.data_group
        )
        event = {
            "name": "skills_list",
            "status": "ok",
            "results": [{"title": skill.name, "id": skill.id} for skill in shown],
        }
        if not shown:
            return ToolOutcome("No skills yet.", event)
        lines = [f"{skill.name} — {skill.description}" for skill in shown]
        return ToolOutcome("\n".join(lines) + "\n\nLoad one with skill_view(name).", event)


async def _run_skill_view(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    name = str(args.get("name") or "").strip()
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        skill = skills_service.by_name(db, name, user, context.data_group)
        if _skill_hidden(context, skill):
            skill = None
        if skill is None:
            listed = ", ".join(
                s.name
                for s in skills_service.enabled_for(
                    db, user, exclude=context.skills_off, group=context.data_group
                )
            )
            return ToolOutcome(
                f'There is no skill "{name}". Skills: {listed or "(none)"}',
                {"name": "skill_view", "status": "error", "error": "Not found."},
            )
        return ToolOutcome(
            f"# Skill: {skill.name}\n{skill.description}\n\n{skill.body}",
            {
                "name": "skill_view",
                "query": skill.name,
                "status": "ok",
                "results": [{"title": skill.name, "id": skill.id}],
            },
        )


def compose_skill(name: str, description: str, instructions: str) -> str:
    """A SKILL.md from its parts, as the LLeMbas CLI writes one (`composeSkill`):
    what `skill_manage`'s `content` and a patch's `old_string` are read against,
    so a model moving between the two sees one shape."""
    one_line = " ".join(description.split())
    return (
        f"---\nname: {name}\ndescription: {json.dumps(one_line, ensure_ascii=False)}\n---\n\n"
        f"{instructions.strip()}\n"
    )


def parse_skill(text: str) -> tuple[dict[str, str], str] | None:
    """(frontmatter, body) of a SKILL.md, or None when it has no frontmatter.
    The values a skill needs are single lines -- a name and a description --
    so this reads `key: value`, with the value as JSON when it is quoted."""
    lines = text.replace("\r\n", "\n").lstrip("\ufeff").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if value[:1] == '"':
            try:
                value = str(json.loads(value))
            except ValueError:
                value = value.strip('"')
        elif value[:1] == "'" and value[-1:] == "'":
            value = value[1:-1].replace("''", "'")
        meta[key.strip()] = value
    return meta, "\n".join(lines[end + 1 :]).strip()


_SKILL_SHAPE = (
    "It must look like this:\n---\nname: {name}\ndescription: Use when …\n---\n\n"
    "The instructions.\n(Or send description and instructions instead of content.)"
)


def _skill_from_text(text: str, name: str) -> tuple[str, str]:
    """(description, body) of a whole SKILL.md for `name`, or ValueError saying
    what is wrong with it -- the CLI's `validateSkill`, against this server's
    limits."""
    parsed = parse_skill(text)
    if parsed is None:
        raise ValueError(
            "SKILL.md must start with frontmatter between two --- lines. "
            + _SKILL_SHAPE.format(name=name)
        )
    meta, body = parsed
    if skills_service.slugify(meta.get("name", "")) != name:
        raise ValueError(f'The frontmatter name must be "{name}".')
    description = meta.get("description", "").strip()
    if not description:
        raise ValueError(
            "The frontmatter needs a description: one sentence saying when to use the "
            "skill. " + _SKILL_SHAPE.format(name=name)
        )
    if len(description) > skills_service.MAX_DESCRIPTION_CHARS:
        raise ValueError(
            f"The description is {len(description)} characters; the limit is "
            f"{skills_service.MAX_DESCRIPTION_CHARS}."
        )
    if not body:
        raise ValueError(
            "SKILL.md needs instructions after the closing --- line. "
            + _SKILL_SHAPE.format(name=name)
        )
    if len(body) > skills_service.MAX_BODY_CHARS:
        raise ValueError(
            f"The instructions are {len(body):,} characters; the limit is "
            f"{skills_service.MAX_BODY_CHARS:,}."
        )
    return description, body


def _replace_once(text: str, old: str, new: str, replace_all: bool) -> str:
    count = text.count(old) if old else 0
    if count == 0:
        raise ValueError("old_string was not found.")
    if count > 1 and not replace_all:
        raise ValueError(
            f"old_string is there {count} times; give more of the text around it so it is "
            "unique, or set replace_all."
        )
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


async def _run_skill_manage(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """The spec's `skill_manage`: a list of operations that apply together.

    What this server offers of it (`narrow.llembas`): create, patch, delete and
    revert, on the skill itself -- no supporting files, categories or project
    skills. Every operation is checked against the skills as they would be
    after the ones before it, and only then is anything written: the service
    commits as it goes, so checking first is what keeps "if one fails none is
    kept" true. A former `skill_edit` call arrives as a patch carrying `_set`
    (`resolve_alias`), its description and body as they were sent.
    """

    def refused(message: str) -> ToolOutcome:
        return ToolOutcome(
            message, {"name": "skill_manage", "status": "error", "error": message[:200]}
        )

    ops = [op for op in (args.get("operations") or []) if isinstance(op, dict)]
    if not ops:
        return refused("operations is a list of at least one change.")
    lone = next((op for op in ops if op.get("action") in ("delete", "revert")), None)
    if lone is not None and len(ops) > 1:
        return refused(f"{lone.get('action')} must be the only operation in its call.")

    with session_scope() as db:
        user = db.get(User, context.owner_id)
        # name -> (description, body) as it would stand, or None once deleted.
        state: dict[str, tuple[str, str] | None] = {}
        plan: list[tuple[str, str, Any]] = []
        for i, op in enumerate(ops):
            action = str(op.get("action") or "")
            name = skills_service.slugify(str(op.get("name") or ""))
            at = f"operation {i + 1} ({action} {name}): " if len(ops) > 1 else ""
            if action not in ("create", "patch", "delete", "revert"):
                return refused(f"{at}action is create, patch, delete or revert here.")
            if not skills_service.SKILL_NAME_PATTERN.match(name):
                return refused(
                    f"{at}{str(op.get('name') or '')!r} is not a valid skill name: two or more "
                    "lowercase letters, digits or hyphens, such as 'weekly-report'."
                )
            own = skills_service.owned_by_name(db, name, user)
            if own is not None and (own.data_group_id or DEFAULT_GROUP) != (
                context.data_group or DEFAULT_GROUP
            ):
                own_here = None
                taken_elsewhere = True
            else:
                own_here, taken_elsewhere = own, False
            stored = (own_here.description, own_here.body) if own_here is not None else None
            current = state.get(name, stored)
            exists = current is not None
            try:
                if action == "create":
                    if exists or taken_elsewhere:
                        where = " in another data group" if taken_elsewhere else ""
                        return refused(f'{at}a skill "{name}" already exists{where}. Use patch.')
                    if op.get("content") is not None:
                        description, body = _skill_from_text(str(op["content"]), name)
                    else:
                        description = str(op.get("description") or "").strip()
                        body = str(op.get("instructions") or "").strip()
                        if not description or not body:
                            return refused(
                                f"{at}create needs description and instructions (or content: "
                                "the whole SKILL.md)."
                            )
                        description, body = _skill_from_text(
                            compose_skill(name, description, body), name
                        )
                    state[name] = (description, body)
                    plan.append(("create", name, (description, body)))
                    continue
                if not exists or current is None:
                    return refused(f'{at}there is no skill "{name}" of yours here.')
                if action == "delete":
                    state[name] = None
                    plan.append(("delete", name, None))
                    continue
                if action == "revert":
                    revision = db.scalars(
                        select(SkillRevision)
                        .where(SkillRevision.skill_id == own_here.id)
                        .order_by(SkillRevision.created_at.desc())
                    ).first()
                    if revision is None:
                        return refused(f'{at}"{name}" has no earlier version kept.')
                    plan.append(("revert", name, revision))
                    continue
                # patch
                setting = op.get("_set")
                if isinstance(setting, dict):
                    description = str(setting.get("description") or "").strip() or current[0]
                    body = (
                        current[1] if setting.get("body") is None else str(setting["body"]).strip()
                    )
                    description, body = _skill_from_text(
                        compose_skill(name, description, body), name
                    )
                else:
                    before = compose_skill(name, current[0], current[1])
                    if op.get("old_string") is not None:
                        try:
                            after = _replace_once(
                                before,
                                str(op["old_string"]),
                                str(op.get("new_string") or ""),
                                bool(op.get("replace_all")),
                            )
                        except ValueError as exc:
                            return refused(
                                f"{at}{exc} old_string must be the exact text now in the "
                                f"skill — read it with skill_view first. SKILL.md begins:\n"
                                f"{before[:500]}"
                            )
                    elif op.get("content") is not None:
                        after = str(op["content"])
                    else:
                        return refused(
                            f"{at}patch needs old_string and new_string (or content: a whole "
                            "new SKILL.md)."
                        )
                    try:
                        description, body = _skill_from_text(after, name)
                    except ValueError as exc:
                        return refused(f"{at}the patch would break SKILL.md: {exc}")
                state[name] = (description, body)
                plan.append(("patch", name, (description, body, str(op.get("reason") or ""))))
            except ValueError as exc:
                return refused(f"{at}{exc}")

        # Everything checked: now write.
        done: list[str] = []
        results = []
        for action, name, value in plan:
            if action == "create":
                skill = skills_service.create(
                    db, owner=user, name=name, description=value[0], body=value[1],
                    author=AUTHOR_MODEL, group=context.data_group,
                )
            else:
                skill = skills_service.owned_by_name(db, name, user)
                if action == "delete":
                    skills_service.delete(db, skill)
                    done.append(f"delete {name}")
                    continue
                if action == "revert":
                    skills_service.revert(db, skill, value, author=AUTHOR_MODEL)
                else:
                    skills_service.update(
                        db, skill, description=value[0], body=value[1],
                        author=AUTHOR_MODEL, note=value[2][:200],
                    )
            done.append(f"{action} {name}")
            results.append({"title": skill.name, "id": skill.id})
    summary = ", ".join(done)
    tail = (
        " The version it replaced is kept: revert again to undo this."
        if plan[0][0] == "revert"
        else ""
    )
    return ToolOutcome(
        f"Done: {summary}.{tail}\nskill_view shows a skill as it is now; the skills list in "
        "your instructions shows the change from your next message.",
        {
            "name": "skill_manage",
            "query": summary[:80],
            "status": "ok",
            "results": results,
        },
    )


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


# --- The plan ------------------------------------------------------------------
def _plan_event(name: str, plan: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """What the transcript keeps of a plan call: the structure, for the panel and
    the card, and nothing drawn in the thread (`block: plan`)."""
    return {
        "name": name,
        "status": "ok",
        "query": plan.get("title") or "",
        "results": [],
        "plan": plan,
        **extra,
    }


async def _run_plan_write(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """The whole plan, replacing any before it. The result is the plan rendered
    with the ids this side gave it, which is what plan_update then takes."""
    plan = plans.normalise(args)
    if not plan or not plan.get("steps"):
        return ToolOutcome(
            "A plan needs a title and at least one phase with a task in it. Nothing was "
            "written.",
            {"name": "plan_write", "status": "error", "error": "Empty plan.", "results": []},
        )
    return ToolOutcome(plans.render(plan), _plan_event("plan_write", plan))


def _current_plan(context: ToolContext) -> dict[str, Any]:
    if not context.chat_id:
        return {}
    with session_scope() as db:
        chat = db.get(Chat, context.chat_id)
        return chat.plan if chat is not None else {}


async def _run_plan_update(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Changes to the plan as it stands on the chat -- or, within one reply, as
    this reply last wrote it: the loop hands the latest in through `args` under
    `_current`, since `_persist` writes the row only at the end."""
    current = args.pop("_current", None) or _current_plan(context)
    result = plans.apply(current, args)
    if "error" in result:
        return ToolOutcome(
            result["error"],
            {"name": "plan_update", "status": "error", "error": result["error"], "results": []},
        )
    plan = result["plan"]
    return ToolOutcome(plans.render(plan), _plan_event("plan_update", plan))


async def _run_plan_submit(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """The plan put to the person. The loop sees `plan_final` and stops the
    reply on the plan card; what they decide becomes this call's result there
    (`generation._settle_plan`). This runner only hands the plan up."""
    current = args.pop("_current", None) or _current_plan(context)
    plan = plans.normalise(current)
    if not plan:
        return ToolOutcome(
            "There is no plan to submit yet. Write one with plan_write first.",
            {"name": "plan_submit", "status": "error", "error": "No plan.", "results": []},
        )
    return ToolOutcome(plans.render(plan), _plan_event("plan_submit", plan, plan_final=True))


REGISTRY: dict[str, ToolDef] = {
    tool.name: tool
    for tool in (
        # The two web tools are the harness spec's, word for word, as the
        # LLeMbas CLI sends them.
        ToolDef(
            name="web_search",
            family=FAMILY_SEARCH,
            description=spec_description("web_search"),
            parameters=spec_parameters("web_search"),
            run=_run_web_search,
        ),
        ToolDef(
            name="web_fetch",
            family=FAMILY_FETCH,
            description=spec_description("web_fetch"),
            parameters=spec_parameters("web_fetch"),
            run=_run_web_fetch,
        ),
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
            # same argument `plan_update` carries.
            risk=RISK_READ,
        ),
        ToolDef(
            name="memory",
            family=FAMILY_MEMORY,
            description=spec_description("memory"),
            parameters=spec_parameters("memory"),
            run=_run_memory,
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
            name="skills_list",
            family=FAMILY_SKILLS,
            description=spec_description("skills_list"),
            parameters=spec_parameters("skills_list"),
            run=_run_skills_list,
        ),
        ToolDef(
            name="skill_view",
            family=FAMILY_SKILLS,
            description=spec_description("skill_view"),
            parameters=spec_parameters("skill_view"),
            run=_run_skill_view,
        ),
        ToolDef(
            name="skill_manage",
            family=FAMILY_SKILLS,
            description=spec_description("skill_manage"),
            parameters=spec_parameters("skill_manage"),
            run=_run_skill_manage,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="ask_user",
            family=FAMILY_ASK,
            # The spec's text and schema: a `why`, up to four questions each
            # with a chip `header`, options with a `recommended` one, and the
            # answer format the card produces (`generation._answered`).
            description=spec_description("ask_user"),
            parameters=spec_parameters("ask_user"),
            # Never resolved by this runner. The reader answers it, in every
            # mode, and the loop turns their answer into the outcome -- see
            # services/interaction.py. The runner exists so that a call reaching
            # it by some path that skipped the loop fails loudly rather than
            # silently returning nothing.
            run=_run_ask_user,
            risk=RISK_ASK,
        ),
        # The plan, as the spec defines it. `interact` in the spec; here the
        # risk class governs the four modes of a machine, and these touch no
        # machine -- the same argument `scratch_write` makes -- so they are
        # reads, offered in every mode and never put on an approval card.
        ToolDef(
            name="plan_write",
            family=FAMILY_PLAN,
            description=spec_description("plan_write"),
            parameters=spec_parameters("plan_write"),
            run=_run_plan_write,
            risk=RISK_READ,
        ),
        ToolDef(
            name="plan_update",
            family=FAMILY_PLAN,
            description=spec_description("plan_update"),
            parameters=spec_parameters("plan_update"),
            run=_run_plan_update,
            risk=RISK_READ,
        ),
        # Offered only in Plan mode -- `resolve_tools` withholds it elsewhere.
        # It is the approval: the reply stops on the plan card and the answer
        # is the call's result.
        ToolDef(
            name="plan_submit",
            family=FAMILY_PLAN,
            description=spec_description("plan_submit"),
            parameters=spec_parameters("plan_submit"),
            run=_run_plan_submit,
            risk=RISK_READ,
        ),
    )
}


def plan_mode(chat: Chat | None) -> bool:
    """Whether this chat is planning: reading freely, changing nothing, and
    finishing with `plan_submit`. `Chat.agent_mode` holds it for both kinds;
    a helper's chat reuses the mode name for its reading set and is not this."""
    if chat is None or getattr(chat, "parent_chat_id", None):
        return False
    return getattr(chat, "agent_mode", "") == "plan"


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
    if gate == FAMILY_PLAN:
        # No permission and no switch of its own: a chat with tools has a plan.
        return True
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
    """Every helper tool, the helper's own `report` included: the registry maps
    a name to its family, and `harness._families` has to find `report`'s or the
    preamble never learns the helper may report."""
    from lembas.services import subagent as subagent_service

    return subagent_service.all_tool_defs()


def _subagent_defs(db: DBSession, chat: Chat, user: User | None) -> list[ToolDef]:
    """The helper tools this chat gets.

    A helper's own chat gets `report` and nothing else of the family: the one
    way it may speak to the main agent before its final reply (spec 2.0.0,
    `offered: subagent`), and no `task` -- a helper that could send helpers is
    a fan-out with no bound anybody set. Any other unattended chat (a
    scheduled task's) gets none: nobody is there to be told about a job.

    An ordinary chat gets `task` and the job tools, unless its model cannot be
    its own helper (it serves one request at a time and the spec's
    `capacity` rule refuses every call) -- a tool every call of which is
    refused is one a model finds out about one wasted round at a time.
    """
    from lembas.services import subagent as subagent_service

    if getattr(chat, "parent_chat_id", None):
        return subagent_service.child_tool_defs()
    if unattended(chat):
        return []
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
    # The helper's own family after the built-ins: not in FAMILIES, which is
    # the list of gates a chat can switch, and this one is switched with
    # `subagent`.
    return (*FAMILIES, FAMILY_REPORT_BACK, *rows)


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
    # kind. The recursion stops in `_subagent_defs`, which hands a helper's
    # chat `report` alone and never `task` -- the family stays, so the one
    # tool a helper has in it can be offered.
    if unattended(chat):
        off = off | {FAMILY_ASK}
    # A helper has one reply and nobody reading it, so a plan would be a plan
    # for no one; the same line prototypes draw.
    if getattr(chat, "parent_chat_id", None):
        off = off | {FAMILY_PLAN}

    # Plan mode: everything that changes the library or runs something is
    # withheld, not merely discouraged, and `plan_submit` appears -- it is the
    # approval, and a model that has it in another mode would submit a plan
    # nobody asked to approve.
    planning = plan_mode(chat)

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
            and not (planning and tool.risk in (RISK_WRITE, RISK_EXECUTE))
            and (planning or tool.name != "plan_submit")
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
_NEEDS_A_SKILL = frozenset({"skills_list", "skill_view"})


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
    "plan": N_("Keeping a plan"),
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
    item a person has just approved on a card, through `policy.always_patterns`
    -- the same split the matcher uses, so what is stored is what will be
    compared, and it refuses to produce anything at all for a command line an
    allow could not be trusted for. "Always" can therefore only ever mean "this
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
        bases=_searchable_bases(db, user, chat, speaker),
        skills_off=scoped_skills_off(chat),
        tools=tools.by_name if tools is not None else None,
        interaction_timeout=float(settings_store.agents(db)["approval_timeout"]),
        unattended=unattended(chat),
    )


def _searchable_bases(db: DBSession, user: User | None, chat: Chat | None, speaker) -> list[dict]:
    """The bases a `knowledge_search` in this chat may name: the attached ones,
    else every base the owner may see in the answering model's data group."""
    from lembas.services import data_groups

    if chat is None or user is None:
        return []
    group = data_groups.for_speaker(db, user, chat, speaker)
    attached = [
        base
        for base in chat.knowledge_bases
        if (base.data_group_id or DEFAULT_GROUP) == group
    ]
    rows = attached or list(db.scalars(documents_service.visible_bases(db, user, group)))
    return [{"id": base.id, "name": base.name} for base in rows]


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
    # The spec's `purpose` (purpose.json): one sentence for the person, added
    # to the parameters of every tool whose spec says `purpose: "purpose"`.
    # Shown beside the call in the transcript; the tool itself never sees it.
    purpose = ""
    if isinstance(parsed, dict) and "purpose" in parsed and takes_purpose(name):
        purpose = str(parsed.pop("purpose") or "").strip()[:300]
    try:
        outcome = await tool.run(context, parsed)
    except Exception as exc:  # a tool must never kill the reply
        log.exception("tool %s failed", name)
        outcome = ToolOutcome(
            f"The {name} tool failed: {exc}",
            {"name": name, "status": "error", "error": str(exc)[:200]},
        )
    if purpose:
        outcome.event["purpose"] = purpose
    return outcome


def takes_purpose(name: str) -> bool:
    """Whether the spec adds `purpose` to this tool's parameters."""
    spec = harness_spec.tools().get(name)
    return bool(spec) and spec.get("purpose") == "purpose"



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
