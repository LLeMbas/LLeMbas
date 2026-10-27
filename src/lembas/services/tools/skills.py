"""`skills_list`, `skill_view` and `skill_manage`, and the skill text format."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from lembas.db.models import (
    AUTHOR_MODEL,
    DEFAULT_GROUP,
    SkillRevision,
    User,
)
from lembas.db.session import session_scope
from lembas.services.library import skills as skills_service
from lembas.services.tools.core import (
    FAMILY_SKILLS,
    RISK_WRITE,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
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


TOOLS: tuple[ToolDef, ...] = (
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
)
