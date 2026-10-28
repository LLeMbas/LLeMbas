"""Every piece of text LLeMbas injects into a model's context, as data.

A *fragment* is one addressable, editable, defaulted piece of the prompt: a
guidance bullet, a section heading, the block of remembered facts, the
instruction that titles a chat. `services/harness.py` assembles them; this module
owns what they are, how they are stored and how their variables expand. It knows
nothing about memories, skills, chats or tools, which is what keeps it testable
on its own.

**A fragment carries its gate as data, not as a callable.** `families`,
`requires` and `when_tools` are tuples and a flag, so a row in a database can
carry exactly the same three fields. That is the whole reason custom tools will
not need a new code path: `register_source` is the entire integration surface,
and the assembler, the save handler, the admin template and the preview all stay
as they are.

**Defaults live here, overrides live in the database.** Only text an
administrator actually changed is stored, so improving a default in a later
release still reaches every instance that never touched that fragment. Two rules
follow from that and are relied on everywhere:

    absent key            -> use the built-in default
    key present, empty    -> the fragment is off

which is why there is no separate `enabled` flag: clearing the box in the admin
page *is* the switch.

**Variables are ``{{name}}``, and anything unrecognised is left alone.** See
`substitute` for why that syntax, and why there is no ``{{#if}}``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session as DBSession

from lembas.services import harness_spec, settings_store

# --- Shape -------------------------------------------------------------------
GROUP_CORE = "core"
GROUP_TOOLS = "tools"
GROUP_CONTEXT = "context"
GROUP_SEAM = "seam"
GROUP_TASKS = "tasks"

GROUP_LABELS: dict[str, str] = {
    GROUP_CORE: "Core",
    GROUP_TOOLS: "Tools",
    GROUP_CONTEXT: "Context",
    GROUP_SEAM: "Handover",
    GROUP_TASKS: "Tasks",
}

# The groups that make up the operational preamble in front of a conversation.
# Two are deliberately left out. `seam` sits *between* the preamble and the
# authored prompt and is placed by `harness.join`, which is the only thing that
# knows whether there is an authored prompt for it to introduce. `tasks` are
# whole requests of their own, not part of a chat's system message at all.
HARNESS_GROUPS = (GROUP_CORE, GROUP_TOOLS, GROUP_CONTEXT)

# One fragment's ceiling, and the whole group's. Same reasoning as the clamps in
# api/admin_search.py: a settings field with no bound is a way to break the
# instance from a form.
MAX_FRAGMENT_CHARS = 8000
MAX_STORED_CHARS = 60_000

# "core.today", "tool.web_search". The prefix is the group a fragment was born
# in rather than the group it displays under, so a custom tool's key stays
# `tool.<slug>` however the page is later reorganised.
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9][a-z0-9_-]*$")

# Fragments that were folded into another when the shared system text arrived
# (1.3.0): the key an administrator's override may still be stored under, and
# the fragment it is now part of. An override under an old key is sent nowhere
# -- `assemble` reads the catalogue -- so the admin page shows it beside its
# successor as an older override to review, rather than losing it quietly.
# What `{{plan_dir}}` in the spec's Plan mode text is here: the CLI writes a
# plan file under its plans directory, LLeMbas keeps the plan as a structure.
PLAN_DIR = "the plan"

RENAMED_KEYS: dict[str, str] = {
    "core.tools_preamble": "core.shared",
    "core.tool_list": "core.shared",
    "core.keep_working": "core.shared",
    "core.untrusted": "core.shared",
}


@dataclass(frozen=True)
class Fragment:
    """One injectable piece of prompt, and the conditions under which it appears.

    The three gates are checked in this order, and any of them failing means the
    fragment contributes nothing at all -- not an empty heading, not a blank
    line:

      `when_tools`  True: only when the model was offered at least one tool.
      `families`    only when one of these tool families is offered.
      `requires`    only when every named variable resolves to something.

    `requires` is what replaced a hand-written pair of guidance variants. The
    sentence that refers to a section belongs *inside* that section, so it cannot
    survive the section's absence -- telling a model to consult a heading that is
    not there is a good way to make it invent one.
    """

    key: str
    label: str
    group: str
    default: str
    hint: str = ""
    # Documentation for the legend, not a whitelist. The assembler substitutes
    # whatever the context holds, so an administrator who wants {{user_name}} in
    # the notes guidance simply gets it.
    variables: tuple[str, ...] = ()
    # Assembly order, global across groups. Separate from `group`, which is a UI
    # concern only -- that is what lets a custom tool slot its guidance between
    # two built-ins without the page having to care.
    order: int = 0
    families: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    when_tools: bool | None = None


@dataclass(frozen=True)
class Variable:
    """One name that may appear in double braces, for the legend."""

    name: str
    label: str
    description: str


# --- Variables ---------------------------------------------------------------
# One source for the admin page's legend. A name absent from here still
# substitutes if the caller supplies it; this list is what gets *documented*.
VARIABLES: tuple[Variable, ...] = (
    Variable("today", "Today's date", "The current date, written out in full."),
    Variable("now", "Date and time", "The current date and time, with the offset from UTC."),
    Variable(
        "schedule_instruction",
        "Scheduled instruction",
        "In a scheduled task's chat: what it is to do each time it runs. Empty "
        "everywhere else, which is what makes it the gate on the unattended "
        "guidance as well as its content.",
    ),
    Variable(
        "schedule_summary",
        "Schedule",
        "In a scheduled task's chat: how often it runs, in words.",
    ),
    Variable(
        "subagent",
        "Is a helper",
        "Set inside the chat of a helper another model sent, and empty "
        "everywhere else — so it is the gate on the guidance a helper reads "
        "about being one. It carries no text worth printing; it is a flag "
        "wearing a variable's clothes, because `requires` is how a fragment "
        "gates itself and a flag has nowhere else to live.",
    ),
    Variable(
        "name",
        "The helper's agent",
        "In a helper's chat: which kind of helper it is -- `explore` or `general`, "
        "the harness spec's built-in agents. Empty everywhere else.",
    ),
    Variable(
        "instructions",
        "The helper's instructions",
        "In a helper's chat: the agent's own instructions (the spec's "
        "`prompts/agents/<name>.md`). Empty everywhere else.",
    ),
    Variable(
        "report",
        "The helper may report",
        "In a helper's chat that has the `report` tool: the sentence saying a "
        "finding or a question may be sent to the main agent before the final "
        "reply, and that a message from it may arrive meanwhile. Empty otherwise.",
    ),
    Variable(
        "read_only",
        "The helper reads only",
        "In the chat of a helper that may change nothing (the `explore` agent): "
        "the sentence saying so. Empty otherwise.",
    ),
    Variable(
        "plan_tools",
        "The planning bullet",
        "The shared system text's bullet about plan_write and plan_update: the same "
        "words the LLeMbas CLI sends, and empty in a helper's chat, which has no plan "
        "tools -- the bullet goes with it.",
    ),
    Variable(
        "ask_tool",
        "The asking bullet",
        "The shared system text's bullet about ask_user, the same words the LLeMbas "
        "CLI sends, and empty in a helper's chat, which has nobody to ask.",
    ),
    Variable(
        "helper_models",
        "Models a helper may run on",
        "Where `task` is offered: the models this person may use in the chat's "
        "data group, as `connection/model` -- what `task`'s `model` may name. "
        "Empty where there is no helper tool.",
    ),
    Variable(
        "start",
        "Where relative paths start",
        "In a chat with the file tools: its start directory, as a code span.",
    ),
    Variable(
        "view",
        "What the chat can reach",
        "In a chat with the file tools: every path it can reach with its level "
        "(read-only, stage, commit), in one sentence. The gate on the files "
        "guidance: empty, and it is not sent.",
    ),
    Variable(
        "repositories",
        "The repository sentence",
        "When the chat can reach a repository from a forge: how changes there are "
        "staged and committed. Empty otherwise.",
    ),
    Variable(
        "show",
        "The show sentence",
        "When show is offered: to open a file the person should see rather than "
        "paste it. Empty otherwise.",
    ),
    Variable(
        "python",
        "The python sentence",
        "When python is offered: that Python runs in the person's browser, and "
        "only while a page of the chat is open. Empty otherwise.",
    ),
    Variable(
        "other_model",
        "The model being quoted",
        "In a chat whose model was changed: the name of the model whose earlier "
        "words follow. Blank everywhere else, because it is a property of one "
        "quotation rather than of a request.",
    ),
    Variable(
        "timezone",
        "Timezone",
        "The reader's timezone, as an IANA name. Empty when they have not chosen "
        "one, in which case the times above are the server's.",
    ),
    Variable(
        "temporary",
        "This is a temporary chat",
        "Set in a person's temporary chat, which reads but writes nothing, and "
        "empty otherwise -- including a helper's chat, which is also temporary. "
        "A flag: the gate on the temporary-chat guidance.",
    ),
    Variable(
        "temporary_lifetime",
        "When a temporary chat goes",
        "When this temporary chat is deleted, as the administrator set it: "
        "\"1 hour after the last message\", or \"when the person leaves it\".",
    ),
    Variable("instance_name", "Instance name", "What this installation is called."),
    Variable("user_name", "User's name", "The name of the person in the conversation."),
    Variable("model_name", "Model", "The display name of the model answering."),
    Variable("max_rounds", "Tool rounds", "How many rounds of tool calls one reply may take."),
    Variable(
        "round_budget",
        "Round budget applies",
        "Set when an administrator has put a ceiling on an ordinary chat's tool "
        "rounds, and blank otherwise — including in every agent chat. Nothing "
        "renders it; it exists so a fragment can say "
        "`requires=('round_budget',)` and appear only where there is a budget "
        "worth planning within.",
    ),
    Variable(
        "memory_limit",
        "Memory length",
        "The character limit on a single remembered fact.",
    ),
    Variable("tool_names", "Tool names", "The tools offered on this request, comma separated."),
    # The variables of the shared system text (harness spec `system/default.md`,
    # the `core.shared` fragment): the facts in which LLeMbas and the LLeMbas
    # CLI differ, each one a sentence or empty. A bullet holding only an empty
    # one disappears from the text.
    Variable(
        "intro",
        "Opening line",
        "The first line of the shared system text: what the model is here to "
        "help with, naming the person when their account has a name.",
    ),
    Variable(
        "surface",
        "Where the output is shown",
        "One sentence on where the reply is rendered: here, as Markdown in a web page.",
    ),
    Variable(
        "tool_list",
        "The tools offered",
        "The sentence naming exactly the tools offered on this request, built from "
        "`tool_names`. Empty with no tools.",
    ),
    Variable(
        "plan_context",
        "The current plan, as a section",
        "The plan this chat is working to, rendered with its ids under a heading, "
        "with the sentence to work to it and quote the ids. Empty when there is "
        "no plan, which takes its bullet with it.",
    ),
    Variable(
        "plan",
        "The current plan",
        "The plan this chat is working to, rendered with its ids and a mark per "
        "task. Empty when there is none.",
    ),
    Variable(
        "file_tools",
        "File tools",
        "The LLeMbas CLI's bullet about its file tools. Always empty here: a chat "
        "has no files of its own to edit.",
    ),
    Variable(
        "background",
        "Background commands",
        "The LLeMbas CLI's bullet about background commands. Always empty here.",
    ),
    Variable(
        "git",
        "The Git section",
        "The LLeMbas CLI's `# Git` section. Always empty here.",
    ),
    Variable(
        "mode_plan",
        "Plan mode",
        "Set when this chat is in Plan mode -- reading freely, changing nothing, "
        "and finishing with plan_submit -- and empty otherwise. A flag: the gate "
        "on the Plan mode guidance.",
    ),
    Variable(
        "mode_manual",
        "Manual mode",
        "Set when an agent chat is in Manual mode. Only the admin preview sets it: "
        "an agent chat's prompt is assembled by the linked LLeMbas CLI, from the "
        "same text.",
    ),
    Variable(
        "mode_edit",
        "Edit mode",
        "Set when an agent chat is in Edit mode. Only the admin preview sets it, "
        "for the reason `mode_manual` gives.",
    ),
    Variable(
        "mode_auto",
        "Auto mode",
        "Set when an agent chat is in Auto mode. Only the admin preview sets it, "
        "for the reason `mode_manual` gives.",
    ),
    Variable(
        "memories",
        "Memories",
        "Everything remembered about this person, one per line. Empty when there is nothing.",
    ),
    Variable(
        "skills",
        "Skill index",
        "Each available skill's name and when to use it, one per line.",
    ),
    Variable(
        "image_templates",
        "Image templates",
        "Each enabled image workflow's name and what it is for, one per line. "
        "Empty when none has been set up.",
    ),
    Variable(
        "image_models",
        "Image checkpoints",
        "The checkpoints an administrator has listed on the image generation "
        "page, comma separated.",
    ),
    Variable(
        "image_instructions",
        "Image house rules",
        "Whatever an administrator wrote in the Extra instructions box on the "
        "image generation page. Empty when they wrote nothing.",
    ),
    Variable(
        "document_names",
        "Attached files",
        "The names of files attached to this conversation. Empty when there are none.",
    ),
    Variable("question", "Question", "The first message. Chat title task only."),
    Variable("answer", "Answer", "The first reply. Chat title task only."),
    Variable(
        "request",
        "The request",
        "What somebody said they wanted to happen, in their own words. "
        "Working out a schedule only.",
    ),
    Variable(
        "targets",
        "Destinations",
        "Where a scheduled run's result may be sent, as a list of the values "
        "that are accepted. Working out a schedule only.",
    ),
    Variable(
        "transcript",
        "Transcript",
        "The turns being summarised, oldest first. Compaction task only.",
    ),
    Variable(
        "previous_summary",
        "Earlier summary",
        "The summary from a previous compaction, if there was one. Compaction task only.",
    ),
)

VARIABLE_NAMES = frozenset(variable.name for variable in VARIABLES)


# --- Substitution ------------------------------------------------------------
# Why {{name}} and not {name}, ${name} or [[name]]: prompt text is full of JSON,
# format strings, shell and Markdown, and the *name grammar* is what keeps them
# apart. Lowercase letters, digits and underscores only, which means {"total": 1},
# {{"a": 1}}, ${PATH}, {{Foo}} and {{a-b}} are not even candidates for
# substitution. The text is a value rendered into a textarea and into a request
# body -- it never reaches Jinja, so a stray {{ is inert.
VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


def substitute(text: str, variables: Mapping[str, str]) -> str:
    """Expand ``{{name}}`` against `variables`, leaving anything else alone.

    Three rules, each of which has a test:

    *Unknown name passes through verbatim*, braces included. That is the
    fallback that makes the syntax safe to choose at all: every collision with
    real prompt text degrades to "you get exactly what you typed".

    *Known name with an empty value becomes empty*, not a pass-through. Pass-
    through is for names that are not variables, not for variables that happen to
    have nothing in them -- otherwise a user with no name set would see the
    literal ``{{user_name}}`` reach the model.

    *One pass, never recursive.* `re.sub` does not rescan what it inserted, and
    that is a security property rather than an accident: ``{{memories}}`` and
    ``{{skills}}`` carry text a model wrote, and a memory whose content is
    literally ``{{skills}}`` must not expand into the skill index.

    A line that contained a known variable and is blank once expanded is dropped
    entirely, so a section whose only content was a variable does not leave a
    stranded heading or a hole -- and so is a line that is left as a bare bullet
    (``- {{name}}`` with nothing in ``name``), which is the harness spec's rule
    for the texts it shares with the LLeMbas CLI. There is no ``{{#if}}``: the moment a settings
    screen has a conditional it wants `else`, `not` and loops, and it has become
    a template language with nowhere to report a syntax error. Fragment-level
    `requires` covers the cases that matter; when it does not, the answer is to
    split the fragment, which reads better anyway.
    """
    lines: list[str] = []
    for line in text.split("\n"):
        rendered, expanded = _expand(line, variables)
        if expanded and (not rendered.strip() or _BARE_BULLET.match(rendered)):
            continue
        lines.append(rendered)
    return "\n".join(lines)


# A bullet with nothing after it, once a variable has been expanded to nothing.
_BARE_BULLET = re.compile(r"^\s*[-*•]\s*$")


def _expand(line: str, variables: Mapping[str, str]) -> tuple[str, bool]:
    """One line expanded, and whether any *known* variable was replaced in it."""
    expanded = False

    def _swap(match: re.Match[str]) -> str:
        nonlocal expanded
        name = match.group(1)
        if name not in variables:
            return match.group(0)
        expanded = True
        return variables[name]

    return VARIABLE_PATTERN.sub(_swap, line), expanded


def variables_in(text: str) -> list[str]:
    """The variable names a piece of text refers to, in order, without repeats."""
    seen: list[str] = []
    for match in VARIABLE_PATTERN.finditer(text):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


# --- Sources -----------------------------------------------------------------
Source = Callable[[DBSession], Iterable[Fragment]]

_SOURCES: list[Source] = []


def register_source(source: Source) -> None:
    """Add a supplier of fragments.

    This is the seam custom tools plug into. A source yielding

        Fragment(key=f"tool.{row.slug}", label=row.name, group=GROUP_TOOLS,
                 default=row.guidance, families=(row.family,), order=500 + row.position)

    gets that tool's guidance into the harness, onto the admin page and into the
    preview without touching anything here. The row supplies the *default*; an
    administrator's edit still lands in the shared settings group, so there is
    one write path and a tool that is deleted and recreated keeps its wording.
    """
    _SOURCES.append(source)


def _builtin_source(db: DBSession) -> Iterable[Fragment]:
    return BUILTIN


def catalogue(db: DBSession) -> dict[str, Fragment]:
    """Every fragment on offer, keyed. The first source to claim a key keeps it."""
    book: dict[str, Fragment] = {}
    for source in _SOURCES:
        for fragment in source(db):
            book.setdefault(fragment.key, fragment)
    return book


def grouped(db: DBSession) -> list[tuple[str, str, list[Fragment]]]:
    """The catalogue as (group key, group label, fragments) for the admin page."""
    book = catalogue(db)
    out: list[tuple[str, str, list[Fragment]]] = []
    for group, label in GROUP_LABELS.items():
        members = sorted(
            (f for f in book.values() if f.group == group), key=lambda f: (f.order, f.key)
        )
        if members:
            out.append((group, label, members))
    return out


# --- Storage -----------------------------------------------------------------
def stored(db: DBSession) -> dict[str, str]:
    """The overrides an administrator has saved, keyed by fragment.

    Fragment keys are the ones with a dot in them; the group also holds plain
    settings such as `max_harness_chars` alongside.
    """
    group = settings_store.get_group(db, settings_store.PROMPTS)
    return {key: str(value) for key, value in group.items() if "." in key}


def resolve(db: DBSession, key: str, *, overrides: Mapping[str, str] | None = None) -> str:
    """The text a fragment currently has: the override if there is one, else the default.

    `overrides=None` reads the database. Passing a mapping uses it verbatim,
    which is how the admin page previews text that has not been saved yet.
    """
    values = stored(db) if overrides is None else overrides
    if key in values:
        return values[key]
    fragment = catalogue(db).get(key)
    return fragment.default if fragment is not None else ""


def is_overridden(db: DBSession, key: str) -> bool:
    return key in stored(db)


def save(db: DBSession, values: Mapping[str, str]) -> dict[str, str]:
    """Record the fragments in `values`, and only those.

    Three outcomes per submitted key:

      equal to its default  -> the override is *removed*, so a later release's
                               improved wording still reaches this instance
      empty                 -> stored as empty, which is how a fragment is off
      anything else         -> stored

    A key that is **not** submitted is left exactly as it was. That is not an
    accident of the form: a fragment can be absent from the page because the
    thing that contributes it is currently switched off -- a disabled custom
    tool, say -- and a save must not throw away wording for something it was
    never shown. Removing an override means saying so, either by restoring its
    default text or by `clear`.

    Returns every override in force afterwards.
    """
    book = catalogue(db)
    keep = dict(stored(db))

    for key, raw in values.items():
        fragment = book.get(key)
        if fragment is None:
            continue
        # Browsers submit CRLF from a textarea. Without normalising, nothing an
        # administrator saves ever compares equal to its default and every
        # fragment would show as edited forever.
        text = str(raw).replace("\r\n", "\n").strip("\n")[:MAX_FRAGMENT_CHARS]
        if text.strip() == fragment.default.strip():
            keep.pop(key, None)
        else:
            keep[key] = text

    budget = MAX_STORED_CHARS
    bounded: dict[str, str] = {}
    for key, text in keep.items():
        bounded[key] = text[:budget]
        budget = max(budget - len(text), 0)

    # replace() rather than update(), because update() merges and an override
    # that has gone back to its default has to actually disappear. The group
    # also holds plain settings alongside the fragments; those must survive.
    plain = {
        key: value
        for key, value in settings_store.get_group(db, settings_store.PROMPTS).items()
        if "." not in key
    }
    settings_store.replace(db, {**plain, **bounded}, key=settings_store.PROMPTS)
    return bounded


def clear(db: DBSession) -> None:
    """Drop every override, returning the instance to the built-in wording."""
    plain = {
        key: value
        for key, value in settings_store.get_group(db, settings_store.PROMPTS).items()
        if "." not in key
    }
    settings_store.replace(db, plain, key=settings_store.PROMPTS)


# --- Assembly ----------------------------------------------------------------
def render(
    db: DBSession,
    key: str,
    variables: Mapping[str, str],
    *,
    overrides: Mapping[str, str] | None = None,
) -> str:
    """One fragment, resolved and expanded. Used for the standalone task prompts."""
    return substitute(resolve(db, key, overrides=overrides), variables).strip()


def _admitted(
    fragment: Fragment,
    *,
    variables: Mapping[str, str],
    families: Iterable[str],
    has_tools: bool,
) -> bool:
    if fragment.when_tools is True and not has_tools:
        return False
    if fragment.when_tools is False and has_tools:
        return False
    if fragment.families and not set(fragment.families) & set(families):
        return False
    return all(str(variables.get(name, "")).strip() for name in fragment.requires)


def _weld(chunks: list[str]) -> str:
    """Join rendered fragments, keeping a run of bullets tight.

    Guidance fragments are single bullets and belong to one list; separating them
    with blank lines would turn five lines into eleven for no gain. Anything else
    gets a blank line, because it is a paragraph or a section.
    """
    if not chunks:
        return ""
    out = chunks[0]
    for chunk in chunks[1:]:
        previous = out.rsplit("\n", 1)[-1].lstrip()
        adjacent_bullets = previous.startswith("- ") and chunk.lstrip().startswith("- ")
        out += ("\n" if adjacent_bullets else "\n\n") + chunk
    return out


def assemble(
    db: DBSession,
    *,
    groups: Iterable[str],
    variables: Mapping[str, str],
    families: Iterable[str] = (),
    has_tools: bool = False,
    overrides: Mapping[str, str] | None = None,
    limit: int = 0,
) -> str:
    """Every admitted fragment in the given groups, in order, expanded and joined."""
    values = stored(db) if overrides is None else overrides
    wanted = set(groups)
    fragments = sorted(
        (f for f in catalogue(db).values() if f.group in wanted),
        key=lambda f: (f.order, f.key),
    )

    chunks: list[str] = []
    for fragment in fragments:
        text = values.get(fragment.key, fragment.default)
        # Empty means an administrator turned this fragment off.
        if not text.strip():
            continue
        if not _admitted(
            fragment, variables=variables, families=families, has_tools=has_tools
        ):
            continue
        rendered = substitute(text, variables).strip()
        if rendered:
            chunks.append(rendered)

    out = _weld(chunks)
    if limit and len(out) > limit:
        out = out[:limit].rstrip() + "\n…"
    return out


# --- The built-in fragments --------------------------------------------------
# Order is global and sparse so a custom tool can be slotted between two of
# these later without renumbering anything.
BUILTIN: tuple[Fragment, ...] = (
    Fragment(
        key="core.heading",
        label="Heading",
        group=GROUP_CORE,
        order=10,
        hint="Opens the block, and marks where our instructions end and the "
        "authored prompt begins.",
        default="## How to work",
    ),
    Fragment(
        key="core.today",
        label="Today's date",
        group=GROUP_CORE,
        order=20,
        variables=("today", "timezone"),
        hint="A model has no clock. Without this it cannot tell whether what it "
        "recalls is current, and will not think to check. {{timezone}} is the "
        "reader's own zone, or the server's where they have not chosen one -- "
        "the same zone the date above is already stated in, so it names a value "
        "rather than making a decision.",
        default=(
            "Today is {{today}}. Your training data stops well before this, so treat "
            "anything time-sensitive as something to check rather than something you "
            "already know.\n"
            "- Times the person gives you are in {{timezone}} unless they say otherwise."
        ),
    ),
    Fragment(
        key="core.identity",
        label="Who is talking",
        group=GROUP_CORE,
        order=30,
        variables=("instance_name", "user_name"),
        requires=("user_name",),
        hint="Skipped entirely when the account has no name — kept separate from "
        "the date so a missing name drops one sentence rather than both.",
        default="You are the assistant in {{instance_name}}, talking to {{user_name}}.",
    ),
    Fragment(
        key="core.style",
        label="How to answer",
        group=GROUP_CORE,
        order=40,
        hint="Language and formatting. Clear this to let the model answer however "
        "it was trained to.",
        default=(
            "Answer in the language the person wrote in, unless they ask for another. "
            "Write in Markdown: short paragraphs, lists only where a list is genuinely "
            "clearer, and fenced code blocks with the language named. Do not open by "
            "restating the question or close by offering further help — answer, then stop."
        ),
    ),
    Fragment(
        key="core.temporary",
        label="Temporary chat",
        group=GROUP_CORE,
        order=44,
        variables=("temporary_lifetime",),
        requires=("temporary",),
        hint="Only in a person's temporary chat, which may read files and memories but "
        "change nothing. Without it a model plans around tools it does not have, or "
        "promises to remember something it cannot write down.",
        default=(
            "- This is a temporary chat. It is deleted {{temporary_lifetime}}, and "
            "nothing in it is kept: you can read files, memories and skills, "
            "but you cannot save, change or schedule anything, and anything you are "
            "told here is forgotten. If the person wants something kept, tell them to "
            "choose Keep this chat first."
        ),
    ),
    # --- The permission mode ------------------------------------------------------
    # The spec's modes/*.md, one fragment each, gated on a flag per mode. Plan
    # mode is the one a chat here can be in; the other three are an agent
    # chat's, whose prompt the linked LLeMbas CLI assembles from the same text
    # -- they exist here so the admin preview can show what that chat reads.
    Fragment(
        key="mode.plan",
        label="Plan mode",
        group=GROUP_CORE,
        order=46,
        requires=("mode_plan",),
        hint="Only in Plan mode: reading freely, changing nothing, and finishing "
        "with plan_submit, which is how the plan is approved. The tools that "
        "write or run are withheld as well, not merely discouraged.",
        default=harness_spec.prompt_with("modes/plan.md", {"plan_dir": PLAN_DIR}),
    ),
    Fragment(
        key="mode.manual",
        label="Manual mode",
        group=GROUP_CORE,
        order=46,
        requires=("mode_manual",),
        hint="An agent chat's, assembled on the device from the same text; "
        "here for the preview.",
        default=harness_spec.prompt("modes/manual.md"),
    ),
    Fragment(
        key="mode.edit",
        label="Edit mode",
        group=GROUP_CORE,
        order=46,
        requires=("mode_edit",),
        hint="An agent chat's, assembled on the device from the same text; "
        "here for the preview.",
        default=harness_spec.prompt("modes/edit.md"),
    ),
    Fragment(
        key="mode.auto",
        label="Auto mode",
        group=GROUP_CORE,
        order=46,
        requires=("mode_auto",),
        hint="An agent chat's, assembled on the device from the same text; "
        "here for the preview.",
        default=harness_spec.prompt("modes/auto.md"),
    ),
    Fragment(
        key="core.honesty",
        label="Not knowing",
        group=GROUP_CORE,
        order=50,
        hint="Its own fragment rather than part of the style, because tools hand a "
        "model real ids and inventing one is a confident, silent failure.",
        default=(
            "If you do not know something and cannot check it, say so. Do not invent a "
            "citation, a URL, a filename, an id or a quotation. A made-up source is worse "
            "than no source, because nobody can catch it by reading."
        ),
    ),
    Fragment(
        key="core.shared",
        label="How to work (shared with the LLeMbas CLI)",
        group=GROUP_CORE,
        order=100,
        when_tools=True,
        variables=(
            "intro",
            "surface",
            "tool_list",
            "file_tools",
            "plan_tools",
            "plan_context",
            "ask_tool",
            "background",
            "git",
        ),
        hint="The harness spec's system text (system/default.md), word for word "
        "the one the LLeMbas CLI sends: tone, conventions, doing tasks, using "
        "tools, the plan. The variables are the facts the two differ in; a "
        "bullet whose variable is empty disappears. It replaced "
        "core.tools_preamble, core.tool_list, core.keep_working and "
        "core.untrusted, whose wording it carries.",
        default=harness_spec.prompt("system/default.md"),
    ),
    Fragment(
        key="core.rounds",
        label="The round budget",
        group=GROUP_CORE,
        order=110,
        when_tools=True,
        requires=("round_budget",),
        variables=("max_rounds",),
        hint="An ordinary chat only, and only when it has a ceiling at all. "
        "What is worth telling a model with a budget is different in kind "
        "from what is worth telling one that should keep going until the work "
        "is done — not the same sentence with a different number in it — so "
        "this is gated on `round_budget`, which `_agent_values` blanks and "
        "which is also blank when an administrator has set no ceiling; with no "
        "ceiling, the shared text's own line about working until done stands.",
        default=(
            "You get at most {{max_rounds}} rounds of tool calls before you have to "
            "answer with what you have. Several tools can be called in one round, so "
            "ask for everything you need at once rather than one thing at a time. "
            "Plan within that: two careful searches beat six that run out halfway. If "
            "what comes back is not enough, say what you would look up next rather "
            "than answering as though it were."
        ),
    ),
    Fragment(
        key="core.interjection",
        label="Being interrupted",
        group=GROUP_CORE,
        order=115,
        when_tools=True,
        hint="A message typed while you are working is handed to you between two "
        "rounds of tool calls. Without this a model reads it as a fresh "
        "conversation and starts the whole task again.",
        default=(
            "A new message from the person you are working for can arrive between "
            "rounds of tool calls, while you are still working. Take it into account "
            "from that point on. You do not need to start again or to re-explain what "
            "you have already done — carry on, adjusted."
        ),
    ),
    Fragment(
        key="core.no_replay",
        label="Results are not kept",
        group=GROUP_CORE,
        order=120,
        when_tools=True,
        hint="Tool results are deliberately not replayed as context on later turns. "
        "Without this the model cannot tell why it has forgotten what it just read.",
        default=(
            "Tool results are not kept after this reply. What a tool returns is visible "
            "to you now and will be gone by the next message, so put anything worth "
            "keeping into the answer itself — the fact, the figure, the URL. If it is "
            "worth having in a later conversation, write it to a file or a memory."
        ),
    ),
    Fragment(
        key="core.attachments",
        label="Attached files",
        group=GROUP_CORE,
        order=140,
        variables=("document_names",),
        requires=("document_names",),
        hint="Only when the conversation carries an attachment. Explains the "
        "<document> wrapper the file's text arrives in.",
        default=(
            "Files the person attached appear inside their message wrapped in "
            '<document name="..."> tags: {{document_names}}. The text inside is the '
            "file's contents, not something they typed. A tag marked (truncated) means "
            "you were given only the beginning of that file."
        ),
    ),
    Fragment(
        key="seam.authored_lead",
        label="Handover to the authored prompt",
        group=GROUP_SEAM,
        order=150,
        hint="Sits on the line between this block and the system prompt an "
        "administrator or the user wrote, and appears only when there is one. "
        "Settles which side wins when the two disagree.",
        default=(
            "Everything below the line was written by whoever set up this instance or "
            "this chat. Where it conflicts with the guidance above, it wins."
        ),
    ),
    # --- Tools ---------------------------------------------------------------
    Fragment(
        key="tool.web_search",
        label="Web search",
        group=GROUP_TOOLS,
        order=200,
        families=("web_search",),
        hint="Appears when the web_search tool is offered.",
        default=(
            "- Look things up rather than trusting your recall, whenever the answer "
            "depends on current facts, on details you are not certain of, or on anything "
            "that may have changed. If the first results are thin or beside the point, "
            "search again with different words instead of answering from them — two or "
            "three searches are normal. Name the source of anything you take from a "
            "result, with its URL."
        ),
    ),
    Fragment(
        key="tool.fetch",
        label="Fetching a page",
        group=GROUP_TOOLS,
        order=205,
        families=("fetch",),
        hint="Appears when the web_fetch tool is offered. The sentence about "
        "JavaScript is the one that earns its place: an empty page is the "
        "commonest confusing result, and without it a model concludes the "
        "page is gone rather than that it could not be read.",
        default=(
            "- You can read one web page at a time with web_fetch, given its address. "
            "Use it after a search when the snippet is not enough, on a link somebody "
            "gave you, or on a link inside a page you have just read. It returns the "
            "page as markdown, a long page in parts, and cannot run JavaScript, so a "
            "page that comes back empty is usually one that builds itself in the "
            "browser rather than one that is missing. Quote the address of anything "
            "you take from it."
        ),
    ),
    Fragment(
        key="tool.memory",
        label="Memory",
        group=GROUP_TOOLS,
        order=230,
        families=("memory",),
        variables=("memory_limit",),
        hint="Appears when the memory tool is offered. What is "
        "remembered costs tokens on every request forever, which is why the "
        "wording is about restraint — and why it says to read what is already "
        "there first: the same fact stored twice in different words costs the "
        "window twice and makes either one ambiguous to remove afterwards.",
        default=(
            "- You can remember durable facts about this person — a preference, a "
            "constraint, a name, how they like to be addressed. Use the memory tool for "
            "those: one fact per entry, under {{memory_limit}} characters. Everything "
            "remembered is already in this message, so read it before adding: saying the "
            "same thing again in different words costs the window twice and makes either one hard "
            "to remove afterwards. Do not remember the details of a single task, anything "
            "that will be untrue next month, or anything secret — keys, passwords, or "
            "health details they have not asked you to keep. Anything longer than a "
            "sentence, or about the work rather than about them, does not belong here. "
            "When something you remembered turns out to be wrong, replace or remove it "
            "with the memory tool rather than adding a correction beside it. "
            "“Remind me to…” is not a memory: remembering that something should happen "
            "does not make it happen, and a schedule does."
        ),
    ),
    Fragment(
        key="tool.skills",
        label="Skills: reading one",
        group=GROUP_TOOLS,
        order=240,
        families=("skills",),
        requires=("skills",),
        hint="Only once there is at least one skill. Gated on the family alone, "
        "a person with no skills would get 'the list below gives each one's "
        "name' above no list, and skill_view in the tools array — which is "
        "exactly why models hunt for skills that do not exist. The writing half "
        "is its own fragment below, because that half is most useful precisely "
        "when there are none.",
        default=(
            "- Skills are procedures you have saved. The list below gives only each one's "
            "name and when to use it; read the full instructions with skill_view before "
            "following one. If following one shows it to be wrong or incomplete, improve it "
            "with a skill_manage patch — the previous version is kept and revert restores it."
        ),
    ),
    Fragment(
        key="tool.skills_write",
        label="Skills: saving one",
        group=GROUP_TOOLS,
        order=241,
        families=("skills",),
        hint="The other half, and deliberately NOT gated on there being any: "
        "somebody with no skills is exactly who most needs to be told they can "
        "save the first one.",
        default=(
            "- If you work out a repeatable way to do something you expect to be asked for "
            "again, save it with skill_manage. The description has to say when to use it, "
            "since that is all you will see next time."
        ),
    ),
    Fragment(
        key="tool.image",
        label="Generating an image",
        group=GROUP_TOOLS,
        order=243,
        families=("image",),
        hint="Appears when image generation is offered. Two sentences here earn "
        "their place against the tool's own descriptions. The picture already "
        "being on screen, because without it the commonest thing a model does "
        "next is offer to show you the image — which it cannot do and which has "
        "already happened. And the shape of a prompt: a small model left to "
        "itself passes the request through verbatim, which is why so many "
        "generations look like nobody thought about them.",
        default=(
            "- You can draw a picture with image_generate. Only `prompt` is required.\n"
            "- Write the prompt as a description, not as the request you were given. "
            "Comma-separated phrases work better than a sentence, and the order matters "
            "— subject first, then what it is doing, then the setting, then the light, "
            "then the style and medium. \"a red bicycle\" is a worse prompt than \"a red "
            "bicycle leaning on a whitewashed wall, morning light, long shadows, 35mm "
            "photograph, shallow depth of field\". Expand what you were asked for into "
            "one of these; do not ask the person to write it for you.\n"
            "- Use `negative` for what must not appear, as plain nouns: \"blurry, extra "
            "fingers, text, watermark\". Never phrase it as an instruction — \"no text\" "
            "puts text in the picture.\n"
            "- Set `width` and `height` to suit the subject rather than leaving both at "
            "the default: taller than wide for a person, wider than tall for a place. "
            "Match the size the checkpoint expects; far above it produces duplicated "
            "limbs rather than more detail.\n"
            "- The other parameters have sensible defaults. Change one when you have a "
            "reason — fewer steps for a quick draft, lower cfg when a picture looks "
            "harsh — and leave it out otherwise.\n"
            "- The picture appears in the conversation as soon as the tool returns. It "
            "is already on screen: do not offer to show it, link to it, or describe how "
            "to open it. Say what you made and what you would change.\n"
            "- If it fails because the machine ran out of video memory, try once more at "
            "a smaller size or with a lighter checkpoint. Do not repeat the same request "
            "unchanged."
        ),
    ),
    Fragment(
        key="tool.image_choices",
        label="Image models and templates",
        group=GROUP_TOOLS,
        order=244,
        families=("image",),
        requires=("image_templates",),
        variables=("image_templates", "image_models"),
        hint="Only once there is at least one workflow to choose between. Split "
        "from the fragment above for the reason `tool.skills` is split from "
        "`tool.skills_write`: an instance with one template should not be told "
        "to weigh up its options, and a list that is not there is worse than no "
        "sentence about it.",
        default=(
            "- The templates you can draw with, and what each is for:\n"
            "{{image_templates}}\n"
            "- The checkpoints you can name: {{image_models}}\n"
            "- Choose the template and checkpoint that suit what is being asked for. "
            "If none obviously fits, leave both out and the usual ones are used."
        ),
    ),
    Fragment(
        key="tool.image_instructions",
        label="Image generation: house rules",
        group=GROUP_TOOLS,
        order=245,
        families=("image",),
        requires=("image_instructions",),
        variables=("image_instructions",),
        hint="Whatever an administrator wrote in the Extra instructions box on "
        "the image generation page. Absent entirely when that box is empty, "
        "which is why this is a fragment of its own rather than a paragraph in "
        "the one above -- an empty heading saying nothing is worse than no "
        "heading.",
        default="{{image_instructions}}",
    ),
    # --- Context -------------------------------------------------------------
    Fragment(
        key="core.unattended",
        label="Nobody is watching",
        group=GROUP_CORE,
        order=35,
        requires=("schedule_instruction",),
        hint="Only in a scheduled task's chat. The point a model cannot work "
        "out for itself is that there is no reader — so the usual moves of "
        "asking what was meant, or stopping to check, end the run having done "
        "nothing. This is the prompt half; the enforcement is that `ask_user` "
        "is not offered here at all, because a rule living only in a system "
        "message is one a page the model just read can argue with.",
        default=(
            "- This chat runs on a schedule and nobody is necessarily reading it. You "
            "cannot ask a question and wait for an answer: there is no one to answer, "
            "and the run would simply end. Where something is ambiguous, choose the "
            "most reasonable reading, do the work, and say plainly in your reply what "
            "you assumed and what you would want confirmed. Finish what you were asked "
            "to do in this one reply."
        ),
    ),
    Fragment(
        key="context.schedule",
        label="What this task is for",
        group=GROUP_CONTEXT,
        order=310,
        variables=("schedule_instruction", "schedule_summary"),
        requires=("schedule_instruction",),
        hint="A task chat accumulates every run, so by the tenth the original "
        "instruction is far out of sight. This puts it back in front of the "
        "model each turn, the same way the current plan is — one lookup, and no "
        "guessing from the transcript.",
        default=(
            "## This scheduled task\n"
            "It runs: {{schedule_summary}}\n"
            "Each time, you are to: {{schedule_instruction}}\n"
            "Earlier runs are above. Say what has changed since the last one rather "
            "than repeating it, unless there is nothing above to compare with."
        ),
    ),
    Fragment(
        key="tool.schedule",
        label="Scheduling",
        group=GROUP_TOOLS,
        order=248,
        families=("schedule",),
        variables=("now",),
        hint="Appears when the scheduling tools are offered. Most of this is the "
        "rule vocabulary, which is also in the tool's own schema — repeated "
        "here because the failure it prevents is expensive and silent: a "
        "schedule that names the wrong day looks exactly like a working one on "
        "every screen, and nobody finds out until it fires. The opening "
        "sentence is the one that matters most, and it is here because of what "
        "happened without it: asked to schedule something, a model wrote a "
        "note, because a note was the nearest thing in its tool list and "
        "nothing said scheduling existed.",
        default=(
            "- You can make things happen later. schedule_create sets up work that runs "
            "because time has passed rather than because somebody asked just now — once, "
            "or on a repeat. Use it whenever the person says *when*: “in ten minutes”, "
            "“every Monday at noon”, “each morning”, “remind me”. Writing a note or a "
            "memory instead makes nothing happen at the time; those are read only when "
            "somebody goes looking. schedule_list shows what already exists, "
            "schedule_update changes one and schedule_cancel stops it.\n"
            "- It is {{now}} where this person is, and every time you write is read in "
            "their zone. Work “in ten minutes” and “tomorrow at nine” out from that clock "
            "rather than guessing.\n"
            '- Use "every" for a plain timer and "at" for a calendar: "in ten minutes" is '
            '{"every": {"minutes": 10}} with a start, and "every Monday at noon" is '
            '{"at": {"weekdays": [0], "times": ["12:00"]}}. Monday is 0 — count the days '
            "off rather than guessing, because naming the wrong one still looks like a "
            "working schedule.\n"
            "- Choose where the result goes. A reminder or a short daily fact goes to "
            "messages; something to read and keep goes to a report; work that builds on "
            "the previous run stays in its own chat.\n"
            "- Write the instruction so it stands alone. It is read days later by a model "
            "that was not here, with nobody to ask what you meant.\n"
            "- Say the resulting timing back in your reply — the tool gives it to you in "
            "words. That sentence is the only chance the person has to notice a mistake "
            "before the first run."
        ),
    ),
    Fragment(
        key="tool.subagent",
        label="Helpers",
        group=GROUP_TOOLS,
        order=252,
        families=("subagent",),
        variables=("helper_models",),
        hint="Appears when task is offered. Two things a model gets "
        "wrong about delegation and neither is in the schema. It under-uses it "
        "— answering four independent questions one after another when they "
        "could have run at once — and then over-uses it, sending a helper to "
        "do a single search. The dividing line is whether the pieces are "
        "independent, so that is what the wording is built around. The last "
        "line is the list task's description promises: the models a helper may "
        "run on, which only this instance knows.",
        default=(
            "- You can delegate. task hands one self-contained piece of work to "
            "another model that runs on its own and gives you its answer. Several calls "
            "in the same turn run at the same time, which is the point of it: four "
            "questions that do not depend on each other take as long as the slowest, "
            "not as long as all four.\n"
            "- Delegate when the work splits into independent parts, each worth more "
            "than one lookup — different sources to read, different areas to survey, "
            "two approaches to compare. Do it yourself when it is one search, one page "
            "or one file: a helper costs a whole reply, so using one to save a single "
            "call is slower than not.\n"
            "- Write each task as if to somebody who has just walked in. A helper starts "
            "with none of this conversation, and cannot ask you or the reader anything "
            "— so say what is wanted, what a good answer contains, and any name, path "
            "or decision it could not look up. Half a task produces half an answer with "
            "no sign that anything was missing.\n"
            "- Give each helper a different piece. Two with the same task come back with "
            "the same answer twice, at twice the cost.\n"
            "- What comes back is another model's work. Read it, say where it disagrees "
            "with what you already had, and do not repeat a claim you cannot check just "
            "because a helper made it.\n"
            "- Models a helper may run on, as `connection/model` for task's `model`: "
            "{{helper_models}}"
        ),
    ),
    Fragment(
        key="core.subagent",
        label="You are the helper",
        group=GROUP_CORE,
        order=36,
        requires=("subagent",),
        variables=("name", "instructions", "report", "read_only"),
        hint="Only inside a helper's own chat: the harness spec's subagent "
        "preamble (blocks/subagent.md), the same words the LLeMbas CLI gives "
        "its subagents, with the agent's name and instructions filled in. The "
        "things a helper cannot work out for itself: it works for the main "
        "agent rather than the person, it cannot ask anybody anything, and its "
        "final reply is its whole report. This is the prompt half; the "
        "enforcement is that ask_user and task are not offered here at all, "
        "and that everything which writes has been withdrawn from the explore "
        "agent.",
        default=harness_spec.prompt("blocks/subagent.md"),
    ),
    Fragment(
        key="context.memories",
        label="What is remembered",
        group=GROUP_CONTEXT,
        order=310,
        families=("memory",),
        variables=("memories",),
        requires=("memories",),
        hint="The remembered facts themselves, injected whole on every turn. "
        "Skipped entirely when there are none. It does not say these 'still "
        "apply', which nothing checks — and which would teach a model to trust "
        "a stale memory over what the person has just said.",
        default=(
            "### What you know about this person\n"
            "\n"
            "These were remembered in earlier conversations. If something here is "
            "contradicted by what they say now, believe them and change it with the "
            "memory tool.\n"
            "\n"
            "{{memories}}"
        ),
    ),
    Fragment(
        key="context.skills",
        label="Skills available",
        group=GROUP_CONTEXT,
        order=320,
        families=("skills",),
        variables=("skills",),
        requires=("skills",),
        hint="Names and descriptions only. The body of a skill is fetched with "
        "skill_view, so a large library costs almost nothing here.",
        default=(
            "### Skills available\n"
            "\n"
            "{{skills}}\n"
            "\n"
            "Read one with skill_view before following it."
        ),
    ),
    Fragment(
        key="tool.workspace",
        label="The files a chat works in",
        group=GROUP_TOOLS,
        order=206,
        families=("files",),
        variables=("start", "view", "repositories", "show", "python"),
        requires=("view",),
        hint="Appears when the file tools are offered. The harness spec's text "
        "(blocks/workspace.md): where relative paths start, every path this "
        "chat can reach with its level -- a granted directory under one that is "
        "not is reached only by its full path, so the model has to be told it "
        "-- and the sentences for a repository, show and python, each gone when "
        "that does not apply.",
        default=harness_spec.prompt("blocks/workspace.md"),
    ),
    Fragment(
        key="tool.context",
        label="Tool results: ids, stubs and recall",
        group=GROUP_TOOLS,
        order=254,
        families=("recall",),
        hint="Appears when recall is offered. The harness spec's text "
        "(blocks/context.md), shared with the LLeMbas CLI: every result has an "
        "id, an unchanged re-read and a repeated listing are stubs, long output "
        "is shortened, and recall shows any result again. Without it a model "
        "reads a stub as an empty file.",
        default=harness_spec.prompt("blocks/context.md"),
    ),
    Fragment(
        key="tool.ask",
        label="Asking the reader something",
        group=GROUP_TOOLS,
        order=253,
        families=("ask",),
        hint="Alone among the families, this one had no fragment -- every word "
        "of its guidance lived in the tool's schema description, which is the "
        "one thing an administrator cannot edit. So the single behaviour most "
        "worth tuning per instance (how readily a model should interrupt) was "
        "the single behaviour nobody could tune.",
        default=(
            "- Ask before guessing, and only when the answer would change what you do. "
            "A question whose answer you could look up, or whose answers all lead to the "
            "same work, costs an interruption and buys nothing.\n"
            "- Ask everything you need in ONE ask_user call. Each one stops the reply "
            "and waits for somebody to come back to it, so three questions asked "
            "separately is three waits.\n"
            "- Always give options. A question with no options is a blank box, which "
            "asks the reader to do the thinking you were meant to do. Say whether they "
            "are alternatives or a set. Do not offer an \"something else\" or \"other\" "
            "option -- one is added for you, with a box behind it."
        ),
    ),
    # --- Tasks ---------------------------------------------------------------
    Fragment(
        key="task.title",
        label="Chat title",
        group=GROUP_TASKS,
        order=400,
        variables=("question", "answer"),
        hint="A separate one-message request, not part of any chat, made once "
        "the first reply has finished so the title can describe the exchange "
        "rather than only the question. Clear it to stop asking a model for "
        "titles: chats are then named from their first message, and no request "
        "is made at all. The emoji is asked for rather than assumed — it makes "
        "a sidebar of twenty chats scannable — and a model that ignores the "
        "instruction simply gives a title without one.",
        default=(
            "Summarise this exchange as a title of at most six words, beginning with "
            "a single emoji that fits it. Reply with the title alone: no quotes, no "
            "punctuation at the end, no preamble, no explanation. Use the language of "
            "the exchange.\n"
            "\n"
            "User: {{question}}\n"
            "\n"
            "Assistant: {{answer}}"
        ),
    ),
    Fragment(
        key="task.image_review",
        label="Reviewing a generated image",
        group=GROUP_TASKS,
        order=405,
        hint="A separate one-message request carrying the picture that was just "
        "made, asked of a vision model before the reader is shown anything. "
        "Clear it to stop reviewing: the first image is then kept, which is "
        "what happens anyway when nothing on the instance has vision. The bias "
        "towards KEEP is deliberate — a reviewer that retries on taste rather "
        "than on faults spends somebody's GPU four times over and usually ends "
        "up back at the first image.",
        default=(
            "You are checking a picture that was just generated against the request "
            "it was generated from. Judge only whether it is a competent attempt at "
            "what was asked for.\n"
            "\n"
            "Answer on the first line with one word: KEEP or RETRY. If RETRY, put "
            "one short sentence on the second line saying what is wrong.\n"
            "\n"
            "Say RETRY only for something clearly wrong: the subject that was asked "
            "for is missing, the image is mangled or unreadable, or it shows "
            "something quite different from the request. Say KEEP for anything that "
            "answers the request, including work you would have composed "
            "differently. Taste is not a fault, and there is no guarantee the next "
            "attempt will be better."
        ),
    ),
    Fragment(
        key="task.compact",
        label="Compaction summary",
        group=GROUP_TASKS,
        order=410,
        variables=("transcript", "previous_summary"),
        hint="A separate one-message request, not part of any chat. Clear it to "
        "turn compaction off entirely: the button says so and nothing is "
        "summarised automatically.",
        default=(
            "Summarise the conversation below so it can be carried forward after the "
            "earlier turns are dropped from your context. This is a working record, "
            "not a report for a reader.\n"
            "\n"
            "Keep, under these headings and in this order:\n"
            "\n"
            "## What we are doing\n"
            "The goal, and where we have got to.\n"
            "\n"
            "## Decisions\n"
            "Anything settled, and why. A decision without its reason gets argued "
            "again.\n"
            "\n"
            "## Facts established\n"
            "Names, numbers, versions, file paths, URLs and identifiers, copied "
            "exactly. Do not round them, paraphrase them or reconstruct one from "
            "memory — if it is not in the transcript, leave it out.\n"
            "\n"
            "## Open threads\n"
            "What is unfinished, and what was about to happen next.\n"
            "\n"
            "Leave out pleasantries, retracted ideas and anything already superseded. "
            "Do not answer the conversation: you are recording it. Write in the "
            "language of the conversation, and stay under 500 words.\n"
            "\n"
            "{{previous_summary}}\n"
            "\n"
            "## Transcript\n"
            "\n"
            "{{transcript}}"
        ),
    ),
    Fragment(
        key="chat.other_model_said",
        label="Quoting another model",
        group=GROUP_TASKS,
        order=450,
        variables=("other_model",),
        hint="What another speaker's answer is labelled as when it reaches this "
        "one. It matters more than it looks: sent unlabelled, every earlier reply "
        "arrives as something *this* model said, so it defends sentences it never "
        "wrote and cannot disagree with them — which is the whole point of the "
        "way back. Relabelling is also what keeps the history alternating, which "
        "several chat templates require.",
        default="{{other_model}} answered:",
    ),
    Fragment(
        key="task.compact_lead",
        label="How a summary is introduced",
        group=GROUP_TASKS,
        order=420,
        hint="Sits in front of the summary, in the turn that replaces the "
        "messages no longer being sent. Without it a model reads the summary as "
        "something the person has just typed.",
        default=(
            "Here is a summary of the earlier part of this conversation. Those "
            "messages are no longer in your context. Treat this summary as an "
            "accurate record of them and rely on it rather than on what you can no "
            "longer see; if it does not cover something you need, say so instead of "
            "filling the gap."
        ),
    ),
    Fragment(
        key="task.compact_ack",
        label="The model's acknowledgement",
        group=GROUP_TASKS,
        order=430,
        hint="One assistant turn after the summary, so the conversation still "
        "alternates user, assistant, user. Several chat templates reject a "
        "history that does not.",
        default=(
            "Understood. I have the summary of the earlier turns and will carry on "
            "from there."
        ),
    ),
    Fragment(
        key="task.schedule_compile",
        label="Working out a schedule",
        group=GROUP_TASKS,
        order=440,
        variables=("request", "now", "timezone", "targets"),
        hint="One request, made once, when somebody describes something they "
        "want to happen later. It turns their words into a recurrence and into "
        "an instruction that reads sensibly with no conversation around it — "
        "which is how it will be read, days later, by a model that was not "
        "there when it was typed. Clearing this switches off the *working out*, "
        "not scheduling: the setup screen then asks for the time in its own "
        "fields, with the reader's words already filled in. The reply is parsed "
        "leniently and anything unusable falls back to that same form, so a "
        "model that answers in prose costs a moment rather than a broken "
        "schedule.",
        default=(
            "Turn the request below into a schedule. Reply with one JSON object and "
            "nothing else — no commentary, no code fence.\n"
            "\n"
            "It is currently {{now}} ({{timezone}}). Times you write are in that zone.\n"
            "\n"
            "The object has these keys:\n"
            '- "title": a short name for this, five words or fewer.\n'
            '- "instruction": what should be done each time it runs, written out in '
            "full. It will be read on its own, with none of this conversation around "
            "it and nobody available to answer a question about it, so say everything "
            "it needs. Write it as an instruction, not as a description.\n"
            '- "target": where the result goes — one of: {{targets}}. Use "report" '
            "when the point is something to read later, and \"chat\" otherwise.\n"
            '- "schedule": an object saying when, with these optional keys:\n'
            '    "start": an ISO timestamp for the first (or only) run.\n'
            '    "every": one of {"minutes": n}, {"hours": n}, {"days": n}, '
            '{"weeks": n} — a plain timer.\n'
            '    "at": {"weekdays": [...], "days": [1-31], "months": [1-12], '
            '"times": ["HH:MM"]} — a calendar. Leave a list out to mean every one '
            "of them.\n"
            "      Weekdays are numbered Monday=0, Tuesday=1, Wednesday=2, "
            "Thursday=3, Friday=4, Saturday=5, Sunday=6. Count them off rather "
            "than guessing: naming the wrong day is the one mistake here that "
            "still looks like a working schedule.\n"
            '    "count": how many times in total, if they said a number.\n'
            '    "until": an ISO timestamp to stop after, if they gave one.\n'
            "\n"
            'Use "every" for "in ten minutes" or "every six hours". Use "at" for '
            '"every Monday at 3" or "daily at nine". Use both only for something '
            'like "every other Tuesday". For a one-off, give "start" alone.\n'
            "\n"
            "If they did not say when, guess the most ordinary reading rather than "
            "leaving it out — daily at 09:00 for something described as daily.\n"
            "\n"
            "The request:\n"
            "{{request}}"
        ),
    ),
)

register_source(_builtin_source)
