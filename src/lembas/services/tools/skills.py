"""`skills_list`, `skill_view` and `skill_manage`, over skill files (spec 3.0.0).

A skill is `<skills dir>/<name>/SKILL.md` in the tree (`services/skill_files.py`
finds them, `services/skill_format.py` checks them). Every read and write goes
through the chat's view of the tree, so a skill the chat cannot read does not
exist for it, one it can only read cannot be changed, and every change is a
file version -- which is what `revert` puts back.
"""

from __future__ import annotations

import posixpath
from typing import Any

from lembas.db.models import Chat, User
from lembas.db.session import session_scope
from lembas.services import blobs, fs, skill_files
from lembas.services import skill_format as fmt
from lembas.services.tools.core import (
    FAMILY_SKILLS,
    RISK_WRITE,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)

# Kept for callers that composed and parsed skills through this module.
compose_skill = fmt.compose
parse_skill = fmt.parse


def _view(db, context: ToolContext) -> fs.View | None:
    user = db.get(User, context.owner_id) if context.owner_id else None
    if user is None:
        return None
    chat = db.get(Chat, context.chat_id) if context.chat_id else None
    return fs.view(db, user, chat, message_id=context.message_id or None)


async def _run_skills_list(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        v = _view(db, context)
        shown = skill_files.all_skills(v, exclude=context.skills_off) if v is not None else []
        event = {
            "name": "skills_list",
            "status": "ok",
            "results": [{"title": skill.name, "path": skill.path} for skill in shown],
        }
        if not shown:
            return ToolOutcome("No skills yet.", event)
        lines = [f"{skill.name} — {skill.description}" for skill in shown]
        return ToolOutcome("\n".join(lines) + "\n\nLoad one with skill_view(name).", event)


async def _run_skill_view(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    name = str(args.get("name") or "").strip()
    with session_scope() as db:
        v = _view(db, context)
        skill = skill_files.find(v, name, exclude=context.skills_off) if v is not None else None
        if skill is None:
            listed = ", ".join(
                s.name for s in skill_files.all_skills(v, exclude=context.skills_off)
            ) if v is not None else ""
            return ToolOutcome(
                f'There is no skill "{name}". Skills: {listed or "(none)"}',
                {"name": "skill_view", "status": "error", "error": "Not found."},
            )
        text = fs.read(v, skill.path).decode("utf-8", errors="replace")
        parsed = fmt.parse(text)
        body = parsed[1] if parsed else text
        return ToolOutcome(
            f"# Skill: {skill.name}\n{skill.description}\nFile: {skill.path}\n\n{body}",
            {
                "name": "skill_view",
                "query": skill.name,
                "status": "ok",
                "path": skill.path,
                "results": [{"title": skill.name, "path": skill.path}],
            },
        )


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


class _Refused(Exception):
    """A refusal raised inside `skill_manage`'s transaction, which it rolls back."""


def _previous(v: fs.View, path: str) -> bytes | None:
    """The content a skill's file had before its last change, or None."""
    versions = fs.versions(v, path)
    if len(versions) < 2:
        return None
    return b"".join(blobs.iter_parts(v.db, versions[1].blob_hash))


async def _run_skill_manage(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """The spec's `skill_manage`: a list of operations that apply together.

    What this server offers of it (`narrow.llembas`): create, patch, delete and
    revert, on the skill's SKILL.md -- no supporting files, categories or
    project skills. Every operation is checked against the skills as they would
    be after the ones before it; the writes then happen in one transaction, so
    "if one fails none is kept" holds. A former `skill_edit` call arrives as a
    patch carrying `_set` (`resolve_alias`).
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

    done: list[str] = []
    results: list[dict[str, str]] = []
    try:
        with session_scope() as db:
            v = _view(db, context)
            if v is None:
                raise _Refused("Skills can be changed only by a person's chat.")
            # name -> SKILL.md text as it would stand, or None once deleted.
            state: dict[str, str | None] = {}
            for i, op in enumerate(ops):
                action = str(op.get("action") or "")
                label = f"{action} {op.get('name') or ''}"
                at = f"operation {i + 1} ({label}): " if len(ops) > 1 else ""
                if action not in ("create", "patch", "delete", "revert"):
                    raise _Refused(f"{at}action is create, patch, delete or revert here.")
                try:
                    name = fmt.check_name(str(op.get("name") or ""))
                except ValueError as exc:
                    raise _Refused(f"{at}{exc}") from exc
                existing = skill_files.find(v, name)
                if name in state:
                    current = state[name]
                elif existing is not None:
                    current = fs.read(v, existing.path).decode("utf-8", errors="replace")
                else:
                    current = None
                path = (
                    existing.path
                    if existing is not None
                    else posixpath.join(skill_files.home_root(v), name, fmt.FILE_NAME)
                )
                try:
                    if action == "create":
                        if current is not None:
                            raise _Refused(
                                f'{at}a skill "{name}" already exists ({path}). Use patch.'
                            )
                        if op.get("content") is not None:
                            text = str(op["content"])
                        else:
                            description = str(op.get("description") or "").strip()
                            body = str(op.get("instructions") or "").strip()
                            if not description or not body:
                                raise _Refused(
                                    f"{at}create needs description and instructions (or "
                                    "content: the whole SKILL.md)."
                                )
                            text = fmt.compose(name, description, body)
                        fmt.validate(text, name)
                        fs.write(v, path, text.encode("utf-8"), mime="text/markdown")
                        state[name] = text
                    elif current is None:
                        raise _Refused(f'{at}there is no skill "{name}" here.')
                    elif action == "delete":
                        fs.rm(v, posixpath.dirname(path))
                        state[name] = None
                    elif action == "revert":
                        before = _previous(v, path)
                        if before is None:
                            raise _Refused(f'{at}"{name}" has no earlier version kept.')
                        fs.write(v, path, before, mime="text/markdown")
                        state[name] = before.decode("utf-8", errors="replace")
                    else:  # patch
                        setting = op.get("_set")
                        if isinstance(setting, dict):
                            parsed = fmt.parse(current) or ({}, current)
                            description = (
                                str(setting.get("description") or "").strip()
                                or parsed[0].get("description", "")
                            )
                            body = (
                                parsed[1]
                                if setting.get("body") is None
                                else str(setting["body"]).strip()
                            )
                            after = fmt.compose(name, description, body)
                        elif op.get("old_string") is not None:
                            try:
                                after = _replace_once(
                                    current,
                                    str(op["old_string"]),
                                    str(op.get("new_string") or ""),
                                    bool(op.get("replace_all")),
                                )
                            except ValueError as exc:
                                raise _Refused(
                                    f"{at}{exc} old_string must be the exact text now in the "
                                    f"skill — read it with skill_view first. SKILL.md "
                                    f"begins:\n{current[:500]}"
                                ) from exc
                        elif op.get("content") is not None:
                            after = str(op["content"])
                        else:
                            raise _Refused(
                                f"{at}patch needs old_string and new_string (or content: a "
                                "whole new SKILL.md)."
                            )
                        try:
                            fmt.validate(after, name)
                        except ValueError as exc:
                            raise _Refused(f"{at}the patch would break SKILL.md: {exc}") from exc
                        fs.write(v, path, after.encode("utf-8"), mime="text/markdown")
                        state[name] = after
                except ValueError as exc:
                    raise _Refused(f"{at}{exc}") from exc
                v.refresh()
                done.append(f"{action} {name}")
                if action != "delete":
                    results.append({"title": name, "path": path})
    except _Refused as exc:
        # Raised inside the transaction: nothing of this call is kept.
        return refused(str(exc))
    except fs.FsError as exc:
        return refused(exc.message)
    summary = ", ".join(done)
    tail = (
        " The version it replaced is kept: revert again to undo this."
        if ops[0].get("action") == "revert"
        else ""
    )
    return ToolOutcome(
        f"Done: {summary}.{tail}\nskill_view shows a skill as it is now; the skills list in "
        "your instructions shows the change from your next message.",
        {"name": "skill_manage", "query": summary[:80], "status": "ok", "results": results},
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
