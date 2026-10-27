"""The vocabulary every tool module shares: families, risk classes, limits, the
context a runner gets, what it hands back, a tool's definition, and the
harness spec's descriptions and parameters as this server fills them."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from lembas.db.models import (
    DEFAULT_GROUP,
)
from lembas.services import harness_spec

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
# The chat's file tools over the organisation's tree (harness spec 3.0.0,
# `sessions: chat`): read, write, edit, list, glob, grep, mkdir, mv, rm and
# show. No permission of its own: what a session may reach is the access
# resolver's (`security/access.py`) -- the person's own right, the chat's
# grants and the data group's floor -- and an agent chat has its device's.
FAMILY_FILES = "files"
# Python in the person's browser (`requires: page`): its own family because
# its risk is `execute` and a chat may want files without it.
FAMILY_PYTHON = "python"
# `recall`: any stored result of this chat again. Offered with any tools.
FAMILY_RECALL = "recall"

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
    FAMILY_FILES,
    FAMILY_PYTHON,
    FAMILY_RECALL,
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
    # The reply this call belongs to: a file written by it records the message
    # in its version (`services/fs.py`), which is what undoing one session's
    # changes reads. Set by the generation loop; empty elsewhere.
    message_id: str = ""
    # The directories whose instruction file a `read` has already handed the
    # model in this reply (filesystem.md, "nearby" files): once each. Per
    # reply, because an earlier reply's tool results are not in front of the
    # model any more.
    instructions_seen: set[str] = field(default_factory=set)
    # Whether the session is shown somewhere a file can be opened (`show`'s
    # `requires: display`): every chat a person reads, not a helper's hidden one.
    display: bool = False


@dataclass
class ToolOutcome:
    """What running a tool produced, for the model and for the reader.

    The two are deliberately different. `content` is the flat text the model
    reads back; `event` is what the transcript shows, and keeps results
    structured so they can be rendered as links rather than as a wall of URLs.
    """

    content: str
    event: dict[str, Any] = field(default_factory=dict)
    # What the result is, for its insertion (`services/context.py`): a read's
    # path, lines and hash, an edit's files and hunks, a recall's target. None
    # is any other output, inserted under the call's name and arguments.
    context: dict[str, Any] | None = None


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
    # Pyodide in the person's browser: the standard library only, and the
    # limits the page's runner holds a call to (`tools/files.py`).
    "python": {
        "packages": "none",
        "seconds": "30",
        "memory": "512 MB",
        "output": "20,000 characters",
    },
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
