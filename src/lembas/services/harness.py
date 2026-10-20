"""Telling the model how to use what it has been given.

A model handed a `tools` array will often ignore it. It answers from recall
because that is what it was trained to do, and nothing in the request suggests
otherwise. The harness is the part of the prompt that says otherwise: what day it
is, one line per tool about *when* to reach for it, the memories, and the list of
skills available.

The text itself is not here. Every piece of it is a fragment in
``services/prompts.py``, defaulted there and overridable by an administrator on
``/admin/prompts``; this module decides which fragments apply to a given request
and what their variables resolve to. That split is what lets a custom tool
contribute its own guidance later by registering a fragment source and nothing
else.

**On the "system prompts are precedence, not concatenation" rule.** That rule
governs the three authored layers -- instance, model, chat -- and it is untouched
here: exactly one of them still wins, and ``chat.effective_system_prompt`` still
decides which. This is a different axis. It describes the machinery rather than
the behaviour, nobody authored it, and there is nothing for it to disagree with.
So it is prepended to whichever authored prompt won, inside one system message,
under a heading that makes the seam obvious.

One system message rather than two because several endpoints reject a second one.
The authored prompt goes last, where it is closest to the conversation.

A model with no tools still gets the core fragments -- the date above all, since
it has no clock and is being asked about a present it cannot see. That is a
change from the original behaviour, where no tools meant no harness at all;
clearing those fragments in the admin page restores it exactly.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import KIND_TASK, User
from lembas.services import branding, harness_spec, plans, prompts, settings_store
from lembas.services import prototypes as prototypes_service
from lembas.services.library import memories as memories_service
from lembas.services.library import skills as skills_service
from lembas.services.schedule import clock

log = logging.getLogger(__name__)

# A ceiling on the whole block, so that a large library cannot quietly eat the
# context window. An administrator can lower it; `max_harness_chars` of 0 means
# "use this".
#
# It has to be larger than everything the shipped defaults are already allowed
# to put in. It once was not: the agent fragments and a project listing and an
# AGENTS.md together crossed an 8,000 ceiling, and `prompts.assemble` cuts the
# *tail*, which by fragment order is exactly the context worth having -- the
# project's own instructions were dropped whole, silently. The agent surface
# has since moved to the linked LLeMbas CLI, which assembles its own prompt;
# what is bounded here now is the shared system text, the guidance per tool
# family, the plan, the memories and the skill index.
#
# The big blocks carry their own budgets, applied before assembly, so they are
# bounded whatever this is. What this bounds is the *fragments* growing without
# anybody noticing -- so it is set well above the sum of what those budgets
# grant, and `tests/test_harness.py` pins a margin as well as a fit, so the
# room is a fact rather than a hope. Raising the ceiling costs nothing by
# itself -- it is a limit, not a size.
#
# 36,000 since 1.3.0: the shared system text is about five thousand characters
# longer than the four fragments it replaced, and the fit test now counts the
# memories at their whole budget (4,000) and a full plan twice over, as the
# prompt carries it. With every family on, the fragments alone are 18,500.
MAX_HARNESS_CHARS = 36000

# How much of the ceiling the shipped fragments may occupy at full budget. The
# rest is headroom for an administrator's own wording, which is the thing this
# limit exists to leave room for -- an override is usually longer than the
# default it replaces, not shorter.
HARNESS_MARGIN = 0.2

# How many attached filenames to name in the prompt. Enough to show what the
# tags will look like, few enough that a chat with thirty files does not spend
# the window listing them -- this is an explanation, not a manifest.
MAX_NAMED_DOCUMENTS = 5


def _families(db: DBSession, tools: list[dict[str, Any]]) -> list[str]:
    """Which families are represented in an offered tool list, in a fixed order.

    Resolved against the database rather than the import-time registry, because
    an administrator-defined tool is a row and would otherwise contribute no
    family at all -- which is to say its guidance would never be admitted.
    """
    from lembas.services import tools as tools_service

    book = tools_service.registry(db)
    offered = {
        book[name].family
        for tool in tools
        if (name := (tool.get("function") or {}).get("name")) in book
    }
    return [family for family in tools_service.families(db) if family in offered]


def _tool_names(tools: list[dict[str, Any]]) -> str:
    return ", ".join(
        name for tool in tools if (name := (tool.get("function") or {}).get("name"))
    )


# The two bullets of the shared system text that a helper does without (spec
# 2.0.0, `prompts/index.json`: `plan_tools`, `ask_tool`): an ordinary chat has
# the plan tools and somebody to ask, a helper has neither, and the bullet-drop
# rule takes them out of its prompt. The same words the LLeMbas CLI fills
# (`src/prompt/assemble.ts` PLAN_TOOLS, ASK_TOOL).
PLAN_TOOLS = (
    "For work with three or more steps, write a plan with plan_write from the start and keep "
    "it current with plan_update as you go rather than at the end: mark a task doing when you "
    "start it and done once you have checked it works, drop one that turns out to be "
    "unnecessary, and add work the plan did not anticipate when you find it. The plan is what "
    "somebody reads to see where you are, so a plan updated only at the end is a report rather "
    "than a plan. If what you find makes the plan wrong rather than merely incomplete, say so "
    "and ask with ask_user rather than quietly planning something else."
)
ASK_TOOL = (
    "When a decision is genuinely the user's — a trade-off only they can weigh, a requirement "
    "the request leaves open — ask with ask_user: everything you need in one call, with the "
    "options you consider plausible and the one you would choose marked recommended. Do not "
    "ask what you can find out yourself."
)

# The sentences the shared system text takes from this side. Module constants
# rather than fragments: they are the facts the LLeMbas CLI differs in, not
# guidance an administrator tunes -- that is `core.shared` itself.
SURFACE = "Your output is rendered as Markdown in a web page."


def tool_list_sentence(names: str) -> str:
    """`{{tool_list}}`: exactly what is offered, so the model does not spend a
    round discovering its own list by calling something that is not there."""
    if not names:
        return ""
    return (
        f"The tools you have on this request are: {names}. That is the whole list. "
        "Anything not named there does not exist here — calling it costs a round and "
        "returns nothing."
    )


def intro_sentence(user_name: str) -> str:
    """`{{intro}}`: what the model is here for, naming the person when known."""
    whom = f"what {user_name} is working on" if user_name else "what the user is working on"
    return f"Use the instructions below and the tools you have to help with {whom}."


def plan_context(plan: dict | None) -> str:
    """`{{plan_context}}`: the plan under a heading, with the sentence to work to
    it. Empty with no plan, which takes its bullet with it."""
    text = plans.render(plan)
    if not text:
        return ""
    return (
        "### The current plan\n\n"
        f"{text}\n\n"
        "Work to it and quote the ids above rather than restating the plan."
    )


def knowledge_scope_sentence(bases: str) -> str:
    """`{{knowledge_scope}}`: which bases this chat is limited to, or nothing."""
    if not bases:
        return ""
    return (
        f"Knowledge searches here cover only: {bases}. Finding nothing there means "
        "nothing is there, not that the library is empty."
    )


def _image_templates(db: DBSession) -> str:
    """One line per workflow, name and description.

    The description is the load-bearing half, the same way it is for a skill:
    it is the only thing the model has to choose with, and "workflow-2" is not
    a choice. Capped, because a list of thirty costs the window on every
    request forever.
    """
    from lembas.db.models import ImageWorkflow

    rows = list(
        db.scalars(
            select(ImageWorkflow)
            .where(ImageWorkflow.enabled.is_(True))
            .order_by(ImageWorkflow.position, ImageWorkflow.slug)
            .limit(12)
        )
    )
    return "\n".join(f"- {row.slug}: {row.description or row.name}" for row in rows)


def _image_models(db: DBSession) -> str:
    """The checkpoints an administrator has listed, comma separated."""
    return ", ".join(settings_store.images(db).get("checkpoints") or [])


def _document_names(db: DBSession, chat) -> str:
    """The names of the non-image files attached anywhere in this chat."""
    from lembas.db.models import Attachment

    rows = list(
        db.scalars(
            select(Attachment.filename)
            .where(Attachment.chat_id == chat.id, Attachment.kind != "image")
            .order_by(Attachment.created_at)
            .limit(MAX_NAMED_DOCUMENTS + 1)
        ).all()
    )
    if not rows:
        return ""
    if len(rows) > MAX_NAMED_DOCUMENTS:
        return ", ".join(rows[:MAX_NAMED_DOCUMENTS]) + " and others"
    return ", ".join(rows)


def context_variables(
    db: DBSession,
    user: User | None,
    tools: list[dict[str, Any]] | None,
    chat=None,
    speaker=None,
) -> dict[str, str]:
    """What every ``{{name}}`` in a fragment resolves to for this request.

    The expensive ones are guarded by family, exactly as the memory block always
    was: a model with no skills tool must not cause a skills query, and has no
    business being told the memories either.
    """
    from lembas.services import tools as tools_service

    offered = tools or []
    families = _families(db, offered)
    # The reader's zone, not the server's. Telling somebody in another country
    # that it is Tuesday when it is Wednesday where they are was survivable
    # while the answer was only ever prose; it stops being survivable the moment
    # they can say "every Monday at 3" and something has to work out when that
    # is. `zone_for` falls back to the server's, so an instance where nobody has
    # set one behaves exactly as it always did.
    stamp = clock.now_for(user)

    # Whose data this request may carry. The data group of the *answering*
    # model -- the chat's own for the chat's model, the member's own for a crowd
    # member -- so a model is handed exactly one group's memories, skills and
    # personality and never the chat's merely for being in it. No chat is the
    # admin preview, which has no speaker and shows every group.
    group: str | None = None
    if chat is not None:
        from lembas.services import chat as chat_service
        from lembas.services import data_groups

        group = data_groups.for_speaker(
            db, user, chat, speaker or chat_service.speaker_for(db, chat)
        )

    values: dict[str, str] = {
        "today": stamp.strftime("%A %-d %B %Y"),
        "now": stamp.strftime("%A %-d %B %Y, %H:%M (UTC%z)"),
        # Named so a model working out a schedule can say which zone it meant,
        # and so `core.today` can carry it without a second fragment.
        #
        # The fallback is load-bearing and used to be absent. `name_for` returns
        # "" for anybody who has never chosen a zone -- the default state of
        # every account -- and the comment here claimed that dropped the line
        # rather than announcing the server's zone as a decision. It did not:
        # `substitute` drops a line only when it is *blank* after expansion, and
        # this variable sits inside a sentence, so every such request shipped
        # "- Times the person gives you are in  unless they say otherwise."
        #
        # Naming the server's zone was never the thing being avoided anyway.
        # `stamp` is `clock.now_for(user)`, which already falls back to it, so
        # `{{today}}` and `{{now}}` are *already* in that zone and `{{now}}`
        # already prints its offset. Withholding the label from a value the
        # model has been given is not restraint, it is a hole. This is the
        # fallback `schedule/compile.py` has always had, for the same reason.
        "timezone": clock.name_for(user) or str(clock.server_zone()),
        "instance_name": branding.for_db(db).name,
        "user_name": (user.name or "") if user is not None else "",
        "model_name": "",
        # What this request will actually allow, so the model is not told a
        # number that is not its own.
        "max_rounds": str(settings_store.chat_rounds(db) or 0),
        # Not rendered anywhere. It is the gate on `core.rounds`: an ordinary
        # chat has a ceiling worth planning within, an agent chat is told to
        # keep going instead, and those are different sentences rather than the
        # same sentence with a different number in it. Blank when there is no
        # ceiling at all, so the fragment vanishes rather than promising zero.
        "round_budget": str(settings_store.chat_rounds(db) or ""),
        "memory_limit": str(memories_service.MAX_MEMORY_CHARS),
        "tool_names": _tool_names(offered),
        # The shared system text's variables (harness spec `system/default.md`):
        # what this side is, said once. The CLI's three -- its file tools, its
        # background commands, its Git section -- are empty here and their
        # bullets go with them.
        "intro": intro_sentence((user.name or "") if user is not None else ""),
        "surface": SURFACE,
        "tool_list": tool_list_sentence(_tool_names(offered)),
        "file_tools": "",
        "background": "",
        "git": "",
        # Planning and asking: every chat but a helper's, which has no plan
        # tools and nobody to ask (emptied below, and the bullets go with it).
        "plan_tools": PLAN_TOOLS,
        "ask_tool": ASK_TOOL,
        "memories": memories_service.block(db, user, group) if "memory" in families else "",
        "skills": (
            skills_service.index_block(
                db, user, exclude=tools_service.scoped_skills_off(chat), group=group
            )
            if "skills" in families
            else ""
        ),
        # What can be drawn, and with what. Guarded by family for the reason the
        # memory block is: an instance with no ComfyUI must not pay a settings
        # read and a table scan to tell a model about a tool it was not offered.
        # Database reads only -- `context_variables` is synchronous and on the
        # request path, so asking ComfyUI itself what it has would hold the
        # request open while somebody's box thought about it. The admin page
        # discovers; this reads what it stored.
        "image_templates": _image_templates(db) if "image" in families else "",
        "image_models": _image_models(db) if "image" in families else "",
        # A flag, and the gate on `core.prototypes`. Not a tool family: a
        # prototype is a fenced block in the reply, so a model with no tools at
        # all can write one -- which is the instance this exists for.
        "prototypes": "yes" if prototypes_service.offered(db, user, chat) else "",
        # The one sentence that differs with the network setting, as a variable
        # rather than two fragments: what may be loaded is a fact about this
        # instance, and the rest of the guidance is the same either way.
        "prototype_sources": _prototype_sources(db),
        # Where a prototype is seen: here, rendered by the page, so nothing to
        # say -- the CLI fills this with "saved as a file".
        "prototype_surface": "",
        # The gate on `core.temporary`, and its one variable: how long the chat
        # has. A person's temporary chat only, never a helper's, which is also
        # `temporary` but is told what it may do by `core.subagent`.
        "temporary": "",
        "temporary_lifetime": "",
        "image_instructions": (
            str(settings_store.images(db).get("instructions") or "") if "image" in families else ""
        ),
        "knowledge_bases": "",
        "knowledge_scope": "",
        "document_names": "",
        # The plan this chat is working to, rendered with its ids: bare, and
        # as the section the shared text carries. Empty with none.
        "plan": "",
        "plan_context": "",
        # The mode flags: the gates on `mode.*`. Plan mode is the one a chat
        # here can be in; the other three are an agent chat's, set only by the
        # admin preview.
        "mode_plan": "",
        "mode_manual": "",
        "mode_edit": "",
        "mode_auto": "",
        # Empty everywhere but a scheduled task's own chat, which is what makes
        # it the gate on `core.unattended` as well as the content of
        # `context.schedule`. Two fragments, one variable, and no way for the
        # warning to appear without the thing it warns about.
        "schedule_instruction": "",
        "schedule_summary": "",
        # Set only in a helper's own chat, and the gate on `core.subagent`.
        # Deliberately not the same variable as `schedule_instruction` even
        # though both mean "nobody is reading": the two say different things to
        # a model, and one fragment covering both would have to say neither.
        "subagent": "",
        # The preamble's own variables (the spec's `blocks/subagent.md`): which
        # agent the helper is, its instructions, whether it may report to the
        # main agent, whether it may change anything. Empty everywhere else.
        "name": "",
        "instructions": "",
        "report": "",
        "read_only": "",
        # Where `task` is offered: the models a helper may run on.
        "helper_models": "",
    }

    if chat is not None:
        # Imported here rather than at the top: `services/tools.py` imports
        # this module, and a top-level import back is a cycle.
        from lembas.services import chat as chat_service

        speaking = speaker or chat_service.speaker_for(db, chat)
        model = chat_service.model_row(db, speaking)
        values["model_name"] = model.label if model is not None else speaking.model_id
        if chat_service.read_only_temporary(chat):
            values["temporary"] = "yes"
            values["temporary_lifetime"] = chat_service.lifetime_words(
                chat_service.temporary_lifetime(db), translate=False
            )
        # Naming the bases a chat is scoped to matters: without it the model
        # cannot tell "there is nothing about this" from "I am only allowed to
        # see the contracts folder", and phrases a miss as the former.
        # Only the attached bases in this speaker's group: a base from another
        # group is not searchable here, and naming it would leak its name.
        in_group = [
            base
            for base in chat.knowledge_bases
            if (base.data_group_id or data_groups.DEFAULT_GROUP) == group
        ]
        if "knowledge" in families and in_group:
            values["knowledge_bases"] = ", ".join(base.name for base in in_group)
            values["knowledge_scope"] = knowledge_scope_sentence(values["knowledge_bases"])
        values["document_names"] = _document_names(db, chat)

        # The plan, whichever reply wrote it: one column read, no scan. In
        # front of the model on every turn while it exists, in every mode --
        # a plan it cannot see is a plan it cannot keep current.
        plan = chat.plan
        if plan:
            values["plan"] = plans.render(plan)
            values["plan_context"] = plan_context(plan)
        if tools_service.plan_mode(chat):
            values["mode_plan"] = "yes"

        # Not gated on a family: a scheduled task has no tools of its own, and
        # the thing that must reach the model is precisely that nobody is
        # reading. One primary-key lookup, the same deal `plan` gets.
        if chat.kind == KIND_TASK:
            values.update(_schedule_values(db, chat, user))

        # Not gated on a family either, and for the same reason: what has to
        # reach a helper is that it is one. A column read, no query.
        if chat.parent_chat_id:
            from lembas.services import subagent as subagent_service

            agent = subagent_service.agent_of(chat)
            values["subagent"] = "yes"
            values["plan_tools"] = ""
            values["ask_tool"] = ""
            values["name"] = agent
            values["instructions"] = harness_spec.prompt(f"agents/{agent}.md")
            # The two sentences this side fills: `report` when the tool is
            # offered (its family is its own, `subagent:report`), `read_only`
            # when the chat may change nothing.
            if tools_service.FAMILY_REPORT_BACK in families:
                values["report"] = subagent_service.REPORT_SENTENCE
            if tools_service.scoped_writes_off(chat):
                values["read_only"] = subagent_service.READ_ONLY_SENTENCE
        elif "subagent" in families and user is not None:
            from lembas.services import subagent as subagent_service

            values["helper_models"] = subagent_service.helper_models(db, user, chat)

    return values


def _prototype_sources(db: DBSession) -> str:
    if prototypes_service.network(db):
        return (
            "It may load libraries, fonts and pictures over https, but prefer inline code "
            "where that is easy: the reader may be offline."
        )
    return (
        "Nothing can be loaded from the internet, so make it self-contained: inline the "
        "CSS and JavaScript, draw charts yourself with SVG or <canvas>, and use system fonts."
    )


def _schedule_values(db: DBSession, chat, user) -> dict[str, str]:
    """What a scheduled task's chat is for, and how often it comes round.

    A task chat accumulates every run, so by the tenth the original instruction
    is far out of sight up the transcript. Put back in front of the model each
    turn rather than left to be inferred -- exactly what `Chat.plan_message_id`
    exists to do for a plan.
    """
    from lembas.services import schedules as schedules_service

    schedule = schedules_service.for_chat(db, chat)
    if schedule is None:
        # The schedule was removed and its chat kept. There is nothing standing
        # to say, so the fragments vanish rather than describing a timer that no
        # longer exists.
        return {}
    return {
        "schedule_instruction": schedule.instruction or schedule.request or "",
        "schedule_summary": schedules_service.describe(schedule, owner=user),
    }


def limit_for(db: DBSession) -> int:
    """The ceiling on the assembled block."""
    stored = settings_store.get(db, "max_harness_chars", key=settings_store.PROMPTS)
    return int(stored or 0) or MAX_HARNESS_CHARS


def compose_from(
    db: DBSession,
    *,
    variables: dict[str, str],
    families: list[str],
    has_tools: bool,
    overrides: dict[str, str] | None = None,
) -> str:
    """Assemble the preamble from already-resolved variables.

    Separate from `compose` because the admin preview has no chat and must not
    invent one: a transient Chat whose `knowledge_bases` collection cannot be
    populated without real rows is a trap, and taking a plain dict of variables
    instead sidesteps it entirely.
    """
    return prompts.assemble(
        db,
        groups=prompts.HARNESS_GROUPS,
        variables=variables,
        families=families,
        has_tools=has_tools,
        overrides=overrides,
        limit=limit_for(db),
    )


def compose(
    db: DBSession,
    user: User | None,
    tools: list[dict[str, Any]] | None,
    chat=None,
    speaker=None,
) -> str:
    """The operational preamble for this request, or "" when there is nothing to say."""
    offered = tools or []
    return compose_from(
        db,
        variables=context_variables(db, user, offered, chat, speaker),
        families=_families(db, offered),
        has_tools=bool(offered),
    )


def join(harness: str, authored: str, *, lead: str = "") -> str:
    """Put the harness in front of whichever authored prompt won.

    Separated from `compose` so the precedence between instance, model and chat
    stays testable on its own -- this function is the only place the two axes
    meet. `lead` is the sentence that sits on the seam and says which side wins
    when they disagree; it is a fragment like everything else, and an empty one
    leaves the bare rule that was there before.
    """
    if not harness:
        return authored
    if not authored:
        return harness
    seam = f"{lead}\n\n---" if lead else "---"
    return f"{harness}\n\n{seam}\n\n{authored}"
