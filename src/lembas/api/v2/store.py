"""Skills and notes for the apps: read-only, the central store's lists and pages.

The same store the web pages and the MCP tools read (`services/skillstore/`),
asked as the person (`policy.person`): every skill, and the notes of the data
groups they may use plus the shared ones -- a note of another group is a 404
like one that is not there. Behind `library.use`. Nothing here changes
anything: changing goes through the web pages and the MCP tools.

| Route | Answer |
|---|---|
| `GET /skills?q=&category=` | `{"skills": [skill], "categories": [{name, count}], "waiting"}` |
| `GET /skills/{name}` | `{"skill": skill + {body, sections, history}}` |
| `GET /notes?subject=&kind=&status=` | `{"notes": [note], "kinds": [{name, count}]}` |
| `GET /notes/{name}` | `{"note": note + {body, sections, history}}` |

A body is Markdown as it is stored: somebody else's writing, to be rendered
as untrusted text.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from lembas.api.deps import Db
from lembas.api.v2._common import Me, need
from lembas.services import data_groups
from lembas.services.skillstore import catalog, pointers, policy, repo, service
from lembas.web.i18n import t

router = APIRouter()


def _changed(row) -> dict[str, Any]:
    return {
        "at": row.changed_at.isoformat() if row.changed_at else None,
        "by": row.changed_by,
        "harness": row.changed_harness,
        "model": row.changed_model,
        "model_said": bool(row.changed_model_said),
        "origin": row.changed_origin,
    }


def skill(row, reads: int = 0) -> dict[str, Any]:
    return {
        "name": row.name,
        "description": row.description,
        "category": row.category or pointers.GENERAL,
        "subcategory": row.subcategory,
        "pinned": bool(row.pinned),
        "main": row.name == repo.MAIN,
        "size": row.size,
        "reads": reads,
        "changed": _changed(row),
    }


def note(row, group_name: str = "") -> dict[str, Any]:
    return {
        "name": row.ident,
        "title": row.title,
        "kind": row.kind,
        "summary": row.summary,
        "subjects": list(row.subjects_json or []),
        "status": row.status,
        "data_group_id": row.data_group_id,
        "data_group": group_name,
        "shared": bool(row.shared),
        "created": row.created,
        "updated": row.updated,
        "confirmed": row.confirmed,
        "confirmed_by": row.confirmed_by,
        "author": {
            "name": row.author,
            "harness": row.harness,
            "model": row.model,
            "model_said": bool(row.model_said),
        },
        "size": row.size,
    }


def change(commit) -> dict[str, Any]:
    trailers = commit.trailers
    return {
        "id": commit.sha,
        "at": commit.at,
        "subject": commit.subject,
        "by": trailers.get("Account") or commit.author,
        "harness": trailers.get("Harness", ""),
        "model": trailers.get("Model", "") or trailers.get("Model-Said", ""),
        "model_said": bool(trailers.get("Model-Said")),
        "origin": commit.origin,
        "reason": trailers.get("Reason", ""),
        "approved_by": trailers.get("Approved-By", ""),
    }


def page(listed: dict[str, Any], row, history: list) -> dict[str, Any]:
    """A list entry with what its own page adds."""
    return {
        **listed,
        "body": row.body,
        "sections": list(row.sections_json or []),
        "history": [change(commit) for commit in history],
    }


@router.get("/skills")
async def list_skills(caller: Me, db: Db, q: str = "", category: str = "") -> dict[str, Any]:
    need(db, caller.user, "library.use")
    rows = catalog.skills(db)
    if q.strip():
        hits = await catalog.search(
            db, policy.person(db, caller.user), q[:200], what="skills", limit=25
        )
        by_name = {row.name: row for row in rows}
        rows = [by_name[hit["name"]] for hit in hits if hit["name"] in by_name]
    if category:
        rows = [row for row in rows if (row.category or pointers.GENERAL) == category]
    reads = catalog.reads(db)
    return {
        "skills": [skill(row, reads.get(row.name, 0)) for row in rows],
        "categories": [{"name": name, "count": count} for name, count in catalog.categories(db)],
        "waiting": service.pending_count(db, caller.user),
    }


@router.get("/skills/{name}")
async def one_skill(caller: Me, db: Db, name: str) -> dict[str, Any]:
    need(db, caller.user, "library.use")
    row = catalog.skill(db, name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That skill no longer exists."))
    listed = skill(row, catalog.reads(db).get(row.name, 0))
    return {"skill": page(listed, row, service.history(service.TARGET_SKILL, row.name, 30))}


@router.get("/notes")
async def list_notes(
    caller: Me, db: Db, subject: str = "", kind: str = "", status: str = "active"
) -> dict[str, Any]:
    need(db, caller.user, "library.use")
    who = policy.person(db, caller.user)
    wanted = status if status in ("active", "archived", "all") else "active"
    rows = catalog.notes(db, who, subject=subject[:200], kind=kind, status=wanted)
    names = {g.id: g.name for g in data_groups.usable(db, caller.user)}
    return {
        "notes": [note(row, names.get(row.data_group_id, "")) for row in rows],
        "kinds": [{"name": name, "count": count} for name, count in catalog.kinds(db, who)],
    }


@router.get("/notes/{name:path}")
async def one_note(caller: Me, db: Db, name: str) -> dict[str, Any]:
    need(db, caller.user, "library.use")
    row = catalog.note(db, policy.person(db, caller.user), name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That note no longer exists."))
    listed = note(row, data_groups.name_of(db, row.data_group_id))
    return {"note": page(listed, row, service.history(service.TARGET_NOTE, row.ident, 30))}


__all__ = ["change", "note", "page", "router", "skill"]
