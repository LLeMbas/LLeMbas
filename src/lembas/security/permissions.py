"""Permission vocabulary and resolution.

The model is deliberately small: a flat set of named booleans, granted by an
instance-wide baseline and widened by group membership. Permissions are a union
across groups -- being in a second group can only ever grant more, never take
away. That is the behaviour people expect, and the alternative (a deny that
wins) makes "why can this user not do X" unanswerable without simulating every
group.

Administrators bypass the whole thing. There is no permission that can be
withheld from an admin, because an admin can grant it back to themselves in two
clicks; pretending otherwise would be theatre.

Model *access* is separate and lives in models_visible_to(): a permission says
what a user may do, model access says which models they may do it with.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Connection, Model, User
from lembas.web.i18n import N_


@dataclass(frozen=True)
class PermissionDef:
    key: str
    label: str
    description: str
    default: bool
    group: str


# The order here is the order they render in the admin UI.
PERMISSION_DEFS: tuple[PermissionDef, ...] = (
    PermissionDef(
        "chat.create", N_("Start chats"), N_("Create new conversations."), True, "Chat"
    ),
    PermissionDef(
        "chat.delete", N_("Delete chats"), N_("Delete their own conversations."), True, "Chat"
    ),
    PermissionDef(
        "chat.system_prompt",
        N_("Set system prompts"),
        N_("Give an individual chat its own system prompt."),
        True,
        "Chat",
    ),
    PermissionDef(
        "chat.params",
        N_("Adjust sampling"),
        N_("Change temperature, top-p and similar per chat."),
        False,
        "Chat",
    ),
    PermissionDef(
        "chat.model_select",
        N_("Choose the model"),
        N_("Switch a chat to a different model. Without this, chats use the default."),
        True,
        "Chat",
    ),
    PermissionDef(
        "chat.prototypes",
        N_("See prototypes"),
        N_("Let a model answer with a live HTML page — a chart, a design, a small "
        "app — shown in the conversation or in a panel beside it. It runs in "
        "the reader's browser, sealed off from this application, and nothing "
        "runs on the server."),
        True,
        "Chat",
    ),
    PermissionDef(
        "folder.manage",
        N_("Manage folders"),
        N_("Create, rename, nest and delete folders."),
        True,
        "Workspace",
    ),
    PermissionDef(
        "files.upload",
        N_("Attach files"),
        N_("Attach images, PDFs and text files to a message. Images only reach "
        "models marked as having vision."),
        True,
        "Workspace",
    ),
    PermissionDef(
        "tools.web_search",
        N_("Search the web"),
        N_("Let a model look things up while it answers. Only offered to models "
        "marked as supporting tools, and only when web search is configured."),
        True,
        "Chat",
    ),
    PermissionDef(
        "tools.fetch",
        N_("Fetch a page"),
        N_("Let a model retrieve one web page and read it, given its address. "
        "Addresses on this machine and this network are refused unless an "
        "administrator has allowed them."),
        True,
        "Chat",
    ),
    PermissionDef(
        "tools.image",
        N_("Generate images"),
        N_("Let a model draw a picture and show it in the conversation. Only "
        "offered when an image generator has been configured, and every "
        "generation spends time on whatever machine is running it."),
        True,
        "Chat",
    ),
    PermissionDef(
        "tools.custom",
        N_("Use custom tools"),
        N_("Let a model call the HTTP tools an administrator has defined. Which "
        "ones depends on the groups each tool is restricted to."),
        True,
        "Chat",
    ),
    PermissionDef(
        "tools.mcp",
        N_("Use MCP servers"),
        N_("Let a model call tools from the MCP servers an administrator has "
        "added. Which ones depends on the groups each server is restricted to."),
        True,
        "Chat",
    ),
    PermissionDef(
        "tools.agent",
        N_("Run commands"),
        N_("Let them start agent chats on a LLeMbas CLI they have linked: its model "
        "reads files, writes files and runs commands on that machine, within the "
        "CLI's own limits and the chat's mode. Nothing runs on this server."),
        False,
        "Agent",
    ),
    PermissionDef(
        "agent.terminal",
        N_("Open a terminal"),
        N_("Open an interactive shell on their linked device, from inside an agent "
        "chat, where the device allows it. What they type there is theirs: the "
        "chat's mode governs the model, not the person at the keyboard."),
        False,
        "Agent",
    ),
    PermissionDef(
        "tools.subagent",
        N_("Delegate to a helper"),
        N_("Let a model hand a self-contained piece of work to a second one that "
        "runs on its own and reports back — reading and searching in parallel "
        "rather than one thing at a time. A helper cannot ask questions, "
        "cannot spawn helpers of its own, and can only do what this chat could "
        "already do without stopping to ask."),
        False,
        "Chat",
    ),
    PermissionDef(
        "tools.ask",
        N_("Be asked questions"),
        N_("Let a model stop mid-reply and ask you something, with answers to pick "
        "from or a box to write your own."),
        True,
        "Chat",
    ),
    PermissionDef(
        "tools.scratch",
        N_("Write in the canvas"),
        N_("Let a model build something up in this chat's scratch document, which "
        "sits open beside the conversation and can be edited and attached to a "
        "message. It belongs to the chat and is not searchable afterwards."),
        True,
        "Chat",
    ),
    PermissionDef(
        "schedule.use",
        N_("Schedule work"),
        N_("Set things to run later, on their own — once, or on a repeating "
        "timetable. This spends model time with nobody at the keyboard, so it "
        "is a capability chosen on purpose rather than one everybody has."),
        False,
        "Scheduling",
    ),
    PermissionDef(
        "reports.use",
        N_("Keep reports"),
        N_("Read the Reports section: finished pieces of work filed for them to "
        "read later, by a model that was asked for one or by something that ran "
        "while they were away."),
        True,
        "Reports",
    ),
    PermissionDef(
        "tools.report",
        N_("File reports"),
        N_("Let a model write a report when it finishes a piece of work, and read "
        "back ones it filed earlier. A report is addressed to the reader and "
        "cannot be replied to, so this costs nothing but a place to put things."),
        True,
        "Reports",
    ),
    PermissionDef(
        "audio.transcribe",
        N_("Dictate messages"),
        N_("Speak a message instead of typing it. Needs a transcription endpoint."),
        True,
        "Audio",
    ),
    PermissionDef(
        "audio.listen",
        N_("Play replies aloud"),
        N_("Have a reply read out. Needs a speech endpoint."),
        True,
        "Audio",
    ),
    PermissionDef(
        "library.use",
        N_("Use the library"),
        N_("Keep knowledge documents, notes, memories and skills of their own."),
        True,
        "Library",
    ),
    PermissionDef(
        "library.share",
        N_("Share library items"),
        N_("Give other people, or a group, access to their knowledge bases, notes, "
        "skills and reports. Sharing grants reading only — never changing, and "
        "never sharing on."),
        # On. It was off, which meant sharing shipped documented as done and
        # unreachable: the panel is only rendered for somebody who holds this,
        # so out of the box nobody could share anything and nothing said why.
        # An instance that wants it off can say so; one that never looked should
        # get the feature it was told it had.
        True,
        "Library",
    ),
    PermissionDef(
        "tools.knowledge",
        N_("Search their knowledge"),
        N_("Let a model search the documents this user has collected."),
        True,
        "Library",
    ),
    PermissionDef(
        "tools.notes",
        N_("Read and write notes"),
        N_("Let a model keep its own notes for this user, and read them back later."),
        True,
        "Library",
    ),
    PermissionDef(
        "tools.memory",
        N_("Remember things"),
        N_("Let a model record short facts about this user, shown to it on every "
        "turn."),
        True,
        "Library",
    ),
    PermissionDef(
        "tools.skills",
        N_("Use and write skills"),
        N_("Let a model follow saved instructions, and write new ones. Every "
        "change is recorded and can be rolled back."),
        True,
        "Library",
    ),
    # --- Reading and writing, split where the difference matters ------------
    # Three gates cover both, and for these three the two halves are genuinely
    # different decisions: a model that may *read* somebody's notes and not add
    # to them is a reasonable thing to want, and until now `tools.notes` was one
    # switch over five tools.
    #
    # Not split for every gate. `tools.web_search` has no write half; `report`
    # is a write with no read worth withholding; `agent` has modes, which are a
    # finer instrument than a permission and are per chat. A permission that
    # answers "the same as that one" is a permission nobody should be asked
    # about -- the reasoning `schedule.use` already carries.
    #
    # **All three default on**, so an instance that never looks behaves exactly
    # as it did: `_family_allowed` reads them only to *narrow* what the gate
    # already allowed.
    PermissionDef(
        "tools.notes.write",
        N_("Write notes"),
        N_("Let a model create, change and delete notes. Without it, it can still "
        "search and read the ones that are there."),
        True,
        "Library",
    ),
    PermissionDef(
        "tools.memory.write",
        N_("Record memories"),
        N_("Let a model add and forget short facts about this person. Without it, "
        "the memories it already has are still shown to it every turn."),
        True,
        "Library",
    ),
    PermissionDef(
        "tools.skills.write",
        N_("Write skills"),
        N_("Let a model write new skills and change existing ones. Without it, it "
        "follows the skills that are there and cannot add to them — which is "
        "the setting for an instance whose skills are curated by hand."),
        True,
        "Library",
    ),
    # Data groups keep one provider's models away from the data another's have
    # been handed. The administrator's arrangement applies to everybody; this
    # lets a person make groups of their own, put a connection into one for
    # themselves, and move their own records between groups. Off by default,
    # because it moves what a provider can read -- and an instance that never
    # looks should keep the arrangement its administrator made.
    PermissionDef(
        "data.manage",
        N_("Manage their own data groups"),
        N_("Make personal data groups, choose which of them each connection reads "
        "for this person, and move their own records between groups."),
        False,
        "Library",
    ),
    # The API. Off by default: a token is a credential that outlives the
    # browser it was made in, and a device signed in can spend this person's
    # usage from anywhere it is.
    PermissionDef(
        "api.use",
        N_("Use the API"),
        N_("Make API tokens and sign devices in (the LLeMbas CLI's /login): their own "
        "models through /v1, as themselves, counted in their usage."),
        False,
        "API",
    ),
    PermissionDef(
        "audit.read",
        N_("Read the audit log"),
        N_("See who signed in, who changed what and which devices were signed in, "
        "across the instance."),
        False,
        "API",
    ),
)

# The admin screens' section names, marked for the catalogue so the headings of
# the permission grids translate with everything else (`t(section_name)`).
ADMIN_SECTIONS = (
    N_("Chat"), N_("Workspace"), N_("Agent"), N_("Scheduling"), N_("Reports"), N_("Audio"),
    N_("Library"), N_("API"),
)

# --- The same permissions, said to the person who has them -------------------
# Settings → Account → "What you can do" does not list the keys -- `chat.create`,
# `tools.knowledge` -- which are the vocabulary of the admin grids and of nobody
# else. A person wants to know what they can *do*, in a sentence addressed to
# them, grouped the way they think of the application rather than the way the
# code gates it.
#
# The label is the definition's own (already written for a human); what is
# added here is a group and one line in the second person. Kept beside the
# definitions, and `tests/test_permissions_for_people.py` refuses a key in one
# and not the other, so a permission added above cannot ship unexplained.
CHATS = N_("Chats")
TOOLS = N_("Tools a model may use")
LIBRARY = N_("Library")
DEVICES = N_("Agents, devices and the API")
LATER = N_("Scheduling and reports")
VOICE = N_("Voice")
OTHERS = N_("Other people")

PERSON_SECTIONS = (CHATS, TOOLS, LIBRARY, DEVICES, LATER, VOICE, OTHERS)

FOR_PEOPLE: dict[str, tuple[str, str]] = {
    "chat.create": (CHATS, N_("Start new chats.")),
    "chat.delete": (CHATS, N_("Delete your own chats.")),
    "chat.system_prompt": (CHATS, N_("Give a chat its own system prompt.")),
    "chat.params": (CHATS, N_("Change temperature, top-p and the like for a chat.")),
    "chat.model_select": (CHATS, N_("Switch a chat to another model.")),
    "chat.prototypes": (CHATS, N_("See the live pages a model answers with.")),
    "folder.manage": (CHATS, N_("Create, rename, nest and delete folders.")),
    "files.upload": (CHATS, N_("Attach images, PDFs and text files to a message.")),
    "tools.web_search": (TOOLS, N_("Look things up on the web while answering.")),
    "tools.fetch": (TOOLS, N_("Read a web page from its address.")),
    "tools.image": (TOOLS, N_("Draw pictures in the conversation.")),
    "tools.custom": (TOOLS, N_("Call the HTTP tools an administrator has defined.")),
    "tools.mcp": (TOOLS, N_("Use tools from the MCP servers an administrator has added.")),
    "tools.subagent": (TOOLS, N_("Hand a self-contained task to a helper model.")),
    "tools.ask": (TOOLS, N_("Stop mid-reply and ask you a question.")),
    "tools.scratch": (TOOLS, N_("Write in the chat's canvas document.")),
    "tools.report": (LATER, N_("File a report when a piece of work is finished.")),
    "tools.agent": (DEVICES, N_("Start agent chats on a LLeMbas CLI you have linked.")),
    "agent.terminal": (DEVICES, N_("Open a shell on your linked device from an agent chat.")),
    "api.use": (DEVICES, N_("Make API tokens and sign the LLeMbas CLI in as you.")),
    "schedule.use": (LATER, N_("Set work to run later, once or on a timetable.")),
    "reports.use": (LATER, N_("Read the reports filed for you.")),
    "audio.transcribe": (VOICE, N_("Speak a message instead of typing it.")),
    "audio.listen": (VOICE, N_("Have replies read aloud.")),
    "library.use": (LIBRARY, N_("Keep documents, notes, memories and skills of your own.")),
    "library.share": (LIBRARY, N_("Let other people read your library items.")),
    "tools.knowledge": (LIBRARY, N_("Let a model search your documents.")),
    "tools.notes": (LIBRARY, N_("Let a model read your notes.")),
    "tools.notes.write": (LIBRARY, N_("Let a model write, change and delete notes.")),
    "tools.memory": (LIBRARY, N_("Let a model see what it remembers about you.")),
    "tools.memory.write": (LIBRARY, N_("Let a model add and forget facts about you.")),
    "tools.skills": (LIBRARY, N_("Let a model follow your saved skills.")),
    "tools.skills.write": (LIBRARY, N_("Let a model write and change skills.")),
    "data.manage": (
        LIBRARY, N_("Make your own data groups and choose what each connection reads.")
    ),
    "audit.read": (OTHERS, N_("Read the instance's audit log.")),
}


def for_people(granted: dict[str, bool]) -> list[tuple[str, list[tuple[str, str, bool]]]]:
    """`granted` (key -> on) as sections of (label, line, on), in reading order.

    Labels and lines are the English source; the template translates them with
    `t()`, because this runs once per page and the language is the reader's.
    """
    labels = {definition.key: definition.label for definition in PERMISSION_DEFS}
    sections: dict[str, list[tuple[str, str, bool]]] = {name: [] for name in PERSON_SECTIONS}
    for definition in PERMISSION_DEFS:
        section, line = FOR_PEOPLE[definition.key]
        sections[section].append(
            (labels[definition.key], line, bool(granted.get(definition.key)))
        )
    return [(name, rows) for name, rows in sections.items() if rows]


# Gates whose read and write halves are separate permissions. Keyed on the gate,
# with the permission derived as `tools.<gate>.write`, so adding a fourth is one
# entry here and one PermissionDef above.
SPLIT_GATES = ("notes", "memory", "skills")

PERMISSION_KEYS = tuple(d.key for d in PERMISSION_DEFS)
DEFAULT_PERMISSIONS = {d.key: d.default for d in PERMISSION_DEFS}


def permission_groups() -> dict[str, list[PermissionDef]]:
    """Definitions bucketed by their UI section, preserving declaration order."""
    grouped: dict[str, list[PermissionDef]] = {}
    for definition in PERMISSION_DEFS:
        grouped.setdefault(definition.group, []).append(definition)
    return grouped


def baseline_permissions(db: DBSession) -> dict[str, bool]:
    """Instance-wide permissions for a user in no group at all."""
    from lembas.services import settings_store

    stored = settings_store.get(db, "default_permissions") or {}
    return {key: bool(stored.get(key, DEFAULT_PERMISSIONS[key])) for key in PERMISSION_KEYS}


def resolve(db: DBSession, user: User | None) -> dict[str, bool]:
    """Effective permissions for a user."""
    if user is None:
        return dict.fromkeys(PERMISSION_KEYS, False)
    if user.is_admin:
        return dict.fromkeys(PERMISSION_KEYS, True)

    effective = baseline_permissions(db)
    for group in user.groups:
        granted = group.permissions_json or {}
        for key in PERMISSION_KEYS:
            # Union: a group can only widen. Absent means "no opinion", not
            # "deny", so a group need only list what it adds.
            if granted.get(key):
                effective[key] = True
    return effective


def has(db: DBSession, user: User | None, key: str) -> bool:
    return resolve(db, user).get(key, False)


def explain(db: DBSession, user: User | None) -> dict[str, dict]:
    """Every permission, whether this user has it, and **where it came from**.

    The question the admin screens could not answer. `resolve` has always
    computed the union and thrown the working away, so "why can this person do
    X?" meant opening every group they belong to and reading the grids by eye --
    which is exactly the simulation the union rule exists to avoid needing.

    `source` is "admin" (bypassing everything), "baseline", or the names of the
    groups that granted it. A permission that is off has no source, because
    nothing granted it -- there is no such thing as a deny here to point at.
    """
    keys = PERMISSION_KEYS
    if user is None:
        return {key: {"on": False, "source": []} for key in keys}
    if user.is_admin:
        return {key: {"on": True, "source": ["admin"]} for key in keys}

    baseline = baseline_permissions(db)
    out: dict[str, dict] = {}
    for key in keys:
        sources = ["baseline"] if baseline.get(key) else []
        sources += [
            group.name for group in user.groups if (group.permissions_json or {}).get(key)
        ]
        out[key] = {"on": bool(sources), "source": sources}
    return out


# --- Quotas -------------------------------------------------------------------
# What a group may raise, and what each number means. Every one of them is
# **zero for no limit**, which is the convention `max_completion_tokens` already
# uses, and it is what makes "unlimited" sayable at all.
#
# Four axes rather than one, because they fail differently. None of them is a
# budget: there is no monthly token allowance, and nothing is refused for what
# it costs -- these limit how much one person can hold at once.
LIMIT_DEFS: tuple[tuple[str, str, str], ...] = (
    (
        "concurrent_replies",
        "Replies at once",
        "How many of their chats may be writing at the same time. This is the "
        "one that stops one person queueing every other person's work behind "
        "them on a single endpoint.",
    ),
    (
        "agent_seconds",
        "Longest agent reply",
        "Seconds of wall clock for one reply in an agent chat, if lower than "
        "the instance's own. Waiting for somebody to approve something does "
        "not count.",
    ),
    (
        "images_per_day",
        "Images a day",
        "Each one is a minute of somebody's GPU and no tokens at all.",
    ),
    (
        "helpers_per_reply",
        "Helpers per reply",
        "How many subagents one reply may send, if lower than the instance's "
        "own.",
    ),
)

LIMIT_KEYS = tuple(key for key, _, _ in LIMIT_DEFS)

# Nobody is limited until somebody says so. A quota that arrived with an upgrade
# and started refusing replies would be the worst possible way to introduce one.
NO_LIMITS: dict[str, int] = dict.fromkeys(LIMIT_KEYS, 0)


def limits_for(db: DBSession, user: User | None) -> dict[str, int]:
    """What this user may spend, resolved across their groups.

    **By maximum**, which is the union rule applied to numbers: being in a second
    group can only ever grant more, never less. That is the same promise the
    permissions make, and having one of the two work the other way round is how
    "why can this person not do X" stops being answerable.

    **Zero wins outright**, because zero means "no limit". Taking the plain
    maximum would make a group saying "unlimited" count for less than one saying
    "a million", which is the union rule inverted for exactly one value -- and it
    is the value somebody sets when they mean *stop limiting this person*.

    An administrator is unlimited, for the reason `resolve` gives them every
    permission: they can raise their own quota in two clicks, and pretending
    otherwise is theatre.
    """
    if user is None or user.is_admin:
        return dict(NO_LIMITS)

    resolved = dict(NO_LIMITS)
    for key in LIMIT_KEYS:
        values = []
        for group in user.groups:
            raw = (group.limits_json or {}).get(key)
            if raw is None:
                continue  # no opinion, contributes nothing
            try:
                values.append(max(0, int(raw)))
            except (TypeError, ValueError):
                continue
        if not values or 0 in values:
            resolved[key] = 0
        else:
            resolved[key] = max(values)
    return resolved


def limit(db: DBSession, user: User | None, key: str) -> int:
    return limits_for(db, user).get(key, 0)


def models_visible_to(db: DBSession, user: User | None) -> list[Model]:
    """Models a user may start a chat with, in display order.

    A model is visible when it is enabled, its connection is enabled, it is
    on the person's organisation's list (when it has one), and either it is
    public or the user belongs to one of its groups.
    """
    query = (
        select(Model)
        .join(Connection)
        .where(Model.enabled.is_(True), Connection.enabled.is_(True))
        .order_by(Model.position, Model.model_id)
    )
    candidates = list(db.scalars(query))

    if user is not None and user.is_admin:
        return candidates

    if user is None:
        return []

    # The organisation's own list, when its admins have made one: empty is
    # every model the instance offers (`organisation.org_models`).
    narrowed = org_model_keys(db, user.org_id)
    if narrowed:
        candidates = [
            model
            for model in candidates
            if (model.model_id, model.connection_id) in narrowed
            or (model.model_id, "") in narrowed
        ]

    member_of = {group.id for group in user.groups}
    return [
        model
        for model in candidates
        if model.public or member_of.intersection({g.id for g in model.groups})
    ]


def org_model_keys(db: DBSession, org_id: str | None) -> set[tuple[str, str]]:
    """(model_id, connection_id) pairs an organisation is narrowed to; a
    connection of "" matches the model id on any connection. Empty: no list."""
    from lembas.db.models import org_models

    if not org_id:
        return set()
    return {
        (row.model_id, row.connection_id or "")
        for row in db.execute(
            select(org_models.c.model_id, org_models.c.connection_id).where(
                org_models.c.org_id == org_id
            )
        )
    }


def can_use_model(db: DBSession, user: User | None, model_id: str) -> bool:
    return any(model.model_id == model_id for model in models_visible_to(db, user))
