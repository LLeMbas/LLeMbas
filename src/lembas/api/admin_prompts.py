"""Prompt administration: every piece of text LLeMbas injects into a model.

The fragments themselves live in `services/prompts.py`; this is the screen that
edits them, and the preview that shows what they assemble into before anything
is saved.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from lembas.api.deps import AdminUser, Db
from lembas.services import chat as chat_service
from lembas.services import harness as harness_service
from lembas.services import prompts as prompts_service
from lembas.services import settings_store
from lembas.services import subagent as subagent_service
from lembas.services import tools as tools_service
from lembas.services.agent import policy
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/prompts", tags=["admin-prompts"])

# What the preview pretends is attached, so the attachment fragment can be read
# in place rather than imagined. An administrator can clear the field.
SAMPLE_DOCUMENTS = "report.pdf, notes.txt"

# The rest of what a preview has to pretend, and the reason it must.
#
# `harness.context_variables` fills most `requires` gates only when it is handed
# a real `Chat` -- the plan, a scheduled task's instruction, the flag saying
# this is a helper. The preview passes `chat=None`, so every one of those
# stayed empty and the gated fragments could never appear in it at all: an
# administrator editing one previewed a system message with it missing, and
# nothing said so.
#
# Samples rather than a transient Chat. `compose_from` takes plain variables
# precisely so this screen never has to build one. This is what
# `SAMPLE_DOCUMENTS` has always done, extended to the rest.
SAMPLE_PLAN = {
    "title": "Fix the parser",
    "findings": ["The failing test is test_parse_dates (tests/test_parser.py:41)"],
    "objectives": ["Dates with a two-digit year parse"],
    "phases": [
        {
            "title": "Code",
            "status": "active",
            "tasks": [
                {"text": "Read the failing test", "status": "done"},
                {"text": "Fix the parser", "status": "doing"},
            ],
        },
        {"title": "Verify", "tasks": ["Add a case for a two-digit year"]},
    ],
}

SAMPLE_SCHEDULE = {
    "schedule_instruction": "Summarise what changed in the repository since yesterday.",
    "schedule_summary": "every weekday at 08:00",
}

# Situations a chat can be in that are not a tool family, so nothing on the
# "Tools offered" row can reach them. `kind` and `parent_chat_id` in the model.
SITUATION_ORDINARY = ""
SITUATION_TASK = "task"
SITUATION_HELPER = "helper"


# A function so the labels are in the reader's language.
def situations() -> tuple[tuple[str, str], ...]:
    return (
        (SITUATION_ORDINARY, t("An ordinary chat")),
        (SITUATION_TASK, t("A scheduled task, running unattended")),
        (SITUATION_HELPER, t("A helper sent by another model")),
    )


def _families_of(db: Db, names: list[str]) -> list[str]:
    """Keep only real family names, in the registry's order.

    Read from the database rather than the constant: a family can belong to an
    administrator-defined tool, and one the preview cannot name is one whose
    guidance cannot be checked here.
    """
    wanted = set(names)
    return [family for family in tools_service.families(db) if family in wanted]


def _tool_names(db: Db, families: list[str]) -> str:
    return ", ".join(
        name for name, tool in tools_service.registry(db).items() if tool.family in families
    )


def _variables(
    db: Db,
    user: AdminUser,
    *,
    families: list[str],
    model_name: str = "",
    documents: str = "",
    situation: str = SITUATION_ORDINARY,
    mode: str = "",
) -> dict[str, str]:
    """The preview's variable values.

    Built from the administrator's *own* memories and skills rather than from
    invented ones: a preview against synthetic data cannot tell you whether your
    memory section reads well against what is actually in there. `AdminUser`
    means this is the operator looking at their own library.

    No Chat row is made. `harness.compose_from` takes plain variables precisely
    so that this screen never has to build a transient one.

    The samples are gated exactly as `context_variables` gates the real values --
    the plan on the `plan` family (a chat with the plan tools is one that may
    have a plan), the mode on the mode chosen, the schedule and helper blocks
    on the situation rather than on any family, because neither is a tool. A
    preview that admitted a fragment the real request would not is worse than
    one that omitted it, so the gating is mirrored rather than approximated.
    """
    from lembas.services import fs, plans, skill_files
    from lembas.services import memories as memories_service
    from lembas.services.agent import policy

    values = harness_service.context_variables(db, user, [], None)
    names = _tool_names(db, families)
    values.update(
        {
            "model_name": model_name,
            "tool_names": names,
            "tool_list": harness_service.tool_list_sentence(names),
            "memories": memories_service.block(db, user) if "memory" in families else "",
            "skills": (
                skill_files.index_block(fs.view(db, user))
                if "skills" in families and user is not None
                else ""
            ),
            "document_names": documents,
        }
    )
    if "plan" in families:
        plan = plans.normalise(SAMPLE_PLAN)
        values["plan"] = plans.render(plan)
        values["plan_context"] = harness_service.plan_context(plan)
    if mode in policy.MODES:
        values[f"mode_{mode}"] = "yes"
    if situation == SITUATION_TASK:
        values.update(SAMPLE_SCHEDULE)
    if situation == SITUATION_HELPER:
        from lembas.services import harness_spec

        values["subagent"] = "yes"
        values["name"] = subagent_service.AGENT_EXPLORE
        values["instructions"] = harness_spec.prompt("agents/explore.md")
        values["report"] = subagent_service.REPORT_SENTENCE
        values["read_only"] = subagent_service.READ_ONLY_SENTENCE
    return values


def _field_context(db: Db, key: str, *, value: str, overridden: bool) -> dict:
    return {
        "fragment": prompts_service.catalogue(db)[key],
        "value": value,
        "overridden": overridden,
    }


@router.get("")
async def prompts_page(request: Request, db: Db, user: AdminUser, saved: bool = False):
    stored = prompts_service.stored(db)
    models = chat_service.available_models(db, user)
    families = list(tools_service.families(db))
    # Overrides stored under a key that has since been folded into another
    # fragment: shown beside the successor to review, since nothing sends them.
    older: dict[str, list[tuple[str, str]]] = {}
    for old_key, new_key in prompts_service.RENAMED_KEYS.items():
        if old_key in stored:
            older.setdefault(new_key, []).append((old_key, stored[old_key]))

    return render(
        request,
        "admin/prompts.html",
        {
            "groups": prompts_service.grouped(db),
            "values": {
                fragment.key: stored.get(fragment.key, fragment.default)
                for fragment in prompts_service.catalogue(db).values()
            },
            "overridden": set(stored),
            "older": older,
            "variables": prompts_service.VARIABLES,
            # The legend shows what each name resolves to right now, with every
            # family on -- a legend nobody can check is just a list of words.
            # Every situation at once, unlike the preview: a chat is either a
            # scheduled task or a helper and never both, but a legend is a
            # reference rather than a rendering, and a name shown as empty
            # because of the situation it was built in reads as a name that
            # resolves to nothing.
            "resolved": {
                **_variables(
                    db,
                    user,
                    families=families,
                    model_name=models[0].label if models else "",
                    documents=SAMPLE_DOCUMENTS,
                    situation=SITUATION_TASK,
                ),
                "subagent": "yes",
                "name": "explore",
                "helper_models": ", ".join(
                    subagent_service.model_ref(m) for m in models[:3]
                ),
            },
            "models": models,
            "families": families,
            "situations": situations(),
            "modes": policy.MODE_LABELS,
            "registry": sorted(
                tools_service.registry(db).values(), key=lambda t: (t.family, t.name)
            ),
            "max_harness_chars": settings_store.get(
                db, "max_harness_chars", key=settings_store.PROMPTS
            ),
            "default_harness_chars": harness_service.MAX_HARNESS_CHARS,
            "sample_documents": SAMPLE_DOCUMENTS,
            "saved": saved,
        },
    )


# Registered before anything that could take a path parameter. There is no such
# route today, but /admin/models has already been bitten once by adding one.
@router.post("/default")
async def use_default(request: Request, db: Db, user: AdminUser, key: str = Form("")):
    """Fill one field with its built-in text, without saving anything.

    Deliberately not a write. The administrator may be halfway through editing
    something else, and a button that silently persisted would take that with
    it. Saving afterwards is what makes it stick -- and because the text then
    equals the default, `prompts.save` stores nothing and the override is gone.
    """
    fragment = prompts_service.catalogue(db).get(key)
    if fragment is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return render(
        request,
        "admin/_prompt_field.html",
        _field_context(db, key, value=fragment.default, overridden=False),
    )


@router.post("/reset")
async def reset_prompts(db: Db, user: AdminUser) -> Response:
    prompts_service.clear(db)
    log.info("prompt fragments reset to defaults by %s", user.email)
    return RedirectResponse("/admin/prompts?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/preview")
async def preview(request: Request, db: Db, user: AdminUser):
    """The whole system message, assembled from what is in the form right now.

    Unsaved text is what an administrator wants to see, so the submitted values
    are passed as overrides rather than read back from the database.
    """
    form = await request.form()
    overrides = _submitted(db, form)
    families = _families_of(db, [str(value) for value in form.getlist("preview_family")])
    model_name = str(form.get("preview_model") or "")
    documents = str(form.get("preview_documents") or "").strip()
    situation = str(form.get("preview_situation") or "")
    mode = str(form.get("preview_mode") or "")

    variables = _variables(
        db,
        user,
        families=families,
        model_name=model_name,
        documents=documents,
        situation=situation,
        mode=mode,
    )
    body = harness_service.compose_from(
        db,
        variables=variables,
        families=families,
        has_tools=bool(families),
        overrides=overrides,
    )
    authored = (settings_store.get(db, "system_prompt") or "").strip()
    lead = prompts_service.substitute(
        overrides.get("seam.authored_lead", prompts_service.resolve(db, "seam.authored_lead")),
        variables,
    ).strip()

    return render(
        request,
        "admin/_prompt_preview.html",
        {
            "system": harness_service.join(body, authored, lead=lead),
            "harness_chars": len(body),
            "limit": harness_service.limit_for(db),
            "authored": authored,
            "title_prompt": prompts_service.substitute(
                overrides.get("task.title", prompts_service.resolve(db, "task.title")),
                {"question": "What is lembas?", "answer": "Elvish waybread."},
            ).strip(),
        },
    )


def _submitted(db: Db, form) -> dict[str, str]:
    """The fragment texts present in a form post, normalised.

    Key presence is what is read, never a falsy value: an empty textarea is how
    a fragment is turned off, and FastAPI's `Form(...)` cannot tell `x=` from an
    absent `x`. Same reason `api/chats.py:update_chat` reads the raw form.
    """
    out: dict[str, str] = {}
    for key in prompts_service.catalogue(db):
        field = f"prompt.{key}"
        if field in form:
            out[key] = str(form.get(field) or "").replace("\r\n", "\n")
    return out


@router.post("")
async def save_prompts(request: Request, db: Db, user: AdminUser) -> Response:
    form = await request.form()
    stored = prompts_service.save(db, _submitted(db, form))

    try:
        cap = int(str(form.get("max_harness_chars") or 0))
    except ValueError:
        cap = 0
    settings_store.update(
        db,
        {"max_harness_chars": min(max(cap, 0), 100_000)},
        key=settings_store.PROMPTS,
    )

    log.info("prompt fragments saved by %s (%d edited)", user.email, len(stored))
    return RedirectResponse("/admin/prompts?saved=1", status_code=status.HTTP_303_SEE_OTHER)
