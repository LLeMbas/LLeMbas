"""Which tools one request is offered: families, gates, permissions, the
chat's narrowing and the modes."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    KIND_AGENT,
    KIND_TASK,
    Chat,
    User,
)
from lembas.services import search as search_service
from lembas.services import settings_store
from lembas.services.tools.core import (
    FAMILIES,
    FAMILY_ASK,
    FAMILY_CUSTOM,
    FAMILY_FETCH,
    FAMILY_FILES,
    FAMILY_IMAGE,
    FAMILY_MCP,
    FAMILY_PLAN,
    FAMILY_PYTHON,
    FAMILY_RECALL,
    FAMILY_REPORT_BACK,
    FAMILY_SCHEDULE,
    FAMILY_SEARCH,
    FAMILY_SUBAGENT,
    RISK_EXECUTE,
    RISK_WRITE,
    ToolDef,
    ToolSet,
    gate_of,
)
from lembas.services.tools.registry import REGISTRY
from lembas.web.i18n import N_


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
    if gate in (FAMILY_PLAN, FAMILY_FILES, FAMILY_PYTHON, FAMILY_RECALL):
        # No permission and no switch of its own: a chat with tools has a plan,
        # its stored results, and its files -- what a session may reach in the
        # tree is the access resolver's to say, path by path, not a switch's.
        # Whether a chat can have them at all is `_offered_in` below.
        return True
    if gate in (FAMILY_CUSTOM, FAMILY_MCP, FAMILY_ASK):
        # Deliberately without `library.use`: an HTTP endpoint an administrator
        # wrote has nothing to do with this person's own memories and skills,
        # and requiring that permission for it would be a coincidence of naming
        # rather than a rule. The same goes for being asked a question.
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
    because the module they live in imports `services/tools` for `ToolDef`
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
    called `write` that turns out to be somebody's HTTP endpoint is the
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
    # already allow -- exactly as a chat's grants narrow what its file tools
    # reach and can never widen it. A crafted request that turned
    # something on here would still be reaching for a tool the gates had
    # already removed.
    off = scoped_off(chat)
    # The reader's own defaults for the answering model, after the gates
    # like everything else under this line and minus whatever this chat switched
    # back on itself -- a chat's explicit setting wins over a person's default.
    if user_defaults and user is not None and model is not None:
        from lembas.services import model_prefs

        off = off | (model_prefs.tools_off(db, user, model.model_id) - scoped_on(chat))
    # The skills this chat can read, as files in its view of the tree
    # (`services/skill_files.py`): none, and listing and reading them are
    # withdrawn below.
    from lembas.services import fs, skill_files

    empty_library = user is None or not skill_files.count(
        fs.view(db, user, chat), exclude=scoped_skills_off(chat)
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
            and _offered_in(tool, chat)
        )
    )


# Skills tools that are meaningless with an empty library.
_NEEDS_A_SKILL = frozenset({"skills_list", "skill_view"})

# The families a chat on this server works with itself (harness spec 3.0.0,
# `sessions: chat`): an agent chat works on its device's disk with the
# device's own tools and results, and is offered none of them here.
_CHAT_ONLY = frozenset({FAMILY_FILES, FAMILY_PYTHON, FAMILY_RECALL})


def has_display(chat: Chat | None) -> bool:
    """`show`'s `requires: display`: the chat is one a person reads in the web
    UI -- not a helper's hidden chat, not an agent chat (whose device shows
    files through the link, `acp.md` `_lembas/show`)."""
    return (
        chat is not None
        and chat.kind != KIND_AGENT
        and not getattr(chat, "parent_chat_id", None)
    )


def _offered_in(tool: ToolDef, chat: Chat) -> bool:
    """Whether this chat can have the tool at all, beyond the gates: the
    spec's `sessions` and `requires`."""
    if tool.family not in _CHAT_ONLY:
        return True
    if chat.kind == KIND_AGENT:
        return False
    if tool.name == "show":
        return has_display(chat)
    if tool.family == FAMILY_PYTHON:
        # `requires: page`: a browser page of this chat that has said it can run
        # Python (`services/pages.py`). Never a schedule's or a helper's chat.
        from lembas.services import pages

        return not unattended(chat) and pages.attached(getattr(chat, "id", "") or "")
    return True


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
    "files": N_("Working with files"),
    "python": N_("Running Python"),
    "recall": N_("Recalling earlier results"),
    "web_search": N_("Web search"),
    "fetch": N_("Fetching pages"),
    "memory": N_("Memory"),
    "skills": N_("Skills"),
    "ask": N_("Asking you questions"),
    "plan": N_("Keeping a plan"),
    "image": N_("Generating images"),
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
