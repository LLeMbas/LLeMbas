"""Skills and Notes: the central store's pages (`services/skillstore/`).

**Skills** -- the list by category, a skill's page with its history and an
editor, pinning, the main skill's prose, and the queue of changes that wait.
**Notes** -- lookup by subject, kind and status, and a note's page.

Every change a form here makes goes through `skillstore.service` as a person
acting by hand (`policy.person`), which is the same door the MCP tools use: an
administrator's edit of a skill applies, anybody else's waits in the queue,
and a note of a data group the reader may not use is a 404 like one that is
not there. Behind `library.use`, like memory. Skill and note text is somebody
else's writing -- an agent's, a remote's -- and is shown through
`services/markdown.py` or escaped, never as markup.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.api.pages import sidebar_context
from lembas.db.models import StoreProposal
from lembas.services import data_groups
from lembas.services.skillstore import catalog, doc, pointers, policy, repo, service
from lembas.web.i18n import t
from lembas.web.templating import render

router = APIRouter(tags=["skills"], dependencies=[Depends(require_permission("library.use"))])


def _back(url: str, *, saved: str = "", error: str = "") -> Response:
    mark = "&" if "?" in url else "?"
    if error:
        url += f"{mark}error={quote(error)}"
    elif saved:
        url += f"{mark}saved={quote(saved)}"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def _said(outcome: service.Outcome) -> str:
    """What a change came to, in the reader's language."""
    if outcome.status == service.QUEUED:
        return t("Not applied yet: the change waits in the queue for approval.")
    if not outcome.commit and outcome.status == service.APPLIED:
        return t("Nothing changed.")
    return t("Saved.")


def _context(db, user, section: str, **extra) -> dict:
    return {
        "chat": None,
        "section": section,
        "waiting": service.pending_count(db, user),
        "store_admin": policy.may_decide_skill(db, user),
        **extra,
        **sidebar_context(db, user),
    }


def _history(target: str, name: str) -> list[dict]:
    return [
        {
            "sha": commit.sha,
            "at": commit.at,
            "subject": commit.subject,
            "by": commit.trailers.get("Account") or commit.author,
            "harness": commit.trailers.get("Harness", ""),
            "model": commit.trailers.get("Model", "") or commit.trailers.get("Model-Said", ""),
            "model_said": bool(commit.trailers.get("Model-Said")),
            "session": commit.trailers.get("Session", ""),
            "origin": commit.origin,
            "reason": commit.trailers.get("Reason", ""),
            "approved_by": commit.trailers.get("Approved-By", ""),
        }
        for commit in service.history(target, name, 40)
    ]


# --- Skills ---------------------------------------------------------------------
@router.get("/skills")
async def skills_page(
    request: Request, db: Db, user: RequiredUser, q: str = "", category: str = "",
    saved: str = "", error: str = "",
):
    catalog.ensure(db)
    service.sync_if_due(db)
    rows = catalog.skills(db)
    counts = catalog.reads(db)
    found = None
    if q.strip():
        hits = await catalog.search(db, policy.person(db, user), q, what="skills", limit=25)
        found = [hit["name"] for hit in hits]
        by_name = {row.name: row for row in rows}
        rows = [by_name[name] for name in found if name in by_name]
    if category:
        rows = [row for row in rows if (row.category or pointers.GENERAL) == category]
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row.category or pointers.GENERAL, []).append(row)
    return render(
        request,
        "skills/list.html",
        _context(
            db, user, "skills",
            groups=sorted(groups.items()) if found is None else [("", rows)] if rows else [],
            categories=catalog.categories(db),
            picked=category,
            q=q,
            reads=counts,
            total=len(catalog.skills(db)),
            saved=saved,
            error=error,
        ),
    )


@router.get("/skills/queue")
async def queue_page(request: Request, db: Db, user: RequiredUser, saved: str = "",
                     error: str = ""):
    catalog.ensure(db)
    waiting = []
    for proposal in service.pending(db, user):
        diff, fits = service.proposal_diff(proposal)
        waiting.append({"row": proposal, "diff": diff, "fits": fits})
    return render(
        request,
        "skills/queue.html",
        _context(
            db, user, "skills",
            proposals=waiting,
            decided=service.decided(db, user),
            saved=saved,
            error=error,
        ),
    )


def _proposal(db, user, proposal_id: str) -> StoreProposal:
    row = db.get(StoreProposal, proposal_id)
    if row is None or not service.may_decide(db, user, row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That change is no longer waiting."))
    return row


@router.post("/skills/queue/{proposal_id}/approve")
async def approve(db: Db, user: RequiredUser, proposal_id: str, word: str = Form("")) -> Response:
    _proposal(db, user, proposal_id)
    try:
        service.approve(db, user, proposal_id, word)
    except (service.Refused, repo.StoreError) as exc:
        return _back("/skills/queue", error=str(exc))
    return _back("/skills/queue", saved=t("Approved and applied."))


@router.post("/skills/queue/{proposal_id}/reject")
async def reject(db: Db, user: RequiredUser, proposal_id: str, word: str = Form("")) -> Response:
    _proposal(db, user, proposal_id)
    try:
        service.reject(db, user, proposal_id, word)
    except service.Refused as exc:
        return _back("/skills/queue", error=str(exc))
    return _back("/skills/queue", saved=t("Rejected."))


@router.post("/skills/new")
async def new_skill(
    db: Db, user: RequiredUser, name: str = Form(""), description: str = Form(""),
    category: str = Form(""), subcategory: str = Form(""), body: str = Form(""),
) -> Response:
    try:
        outcome = service.skill_add(
            db, policy.person(db, user), name=name, description=description, body=body,
            category=category, subcategory=subcategory, reason="by hand",
        )
    except (service.Refused, repo.StoreError) as exc:
        return _back("/skills", error=str(exc))
    if outcome.status == service.QUEUED:
        return _back("/skills", saved=_said(outcome))
    return _back(f"/skills/s/{outcome.name}", saved=_said(outcome))


def _skill(db, name: str):
    row = catalog.skill(db, name)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That skill no longer exists."))
    return row


@router.get("/skills/s/{name}")
async def skill_page(request: Request, db: Db, user: RequiredUser, name: str, saved: str = "",
                     error: str = "", edit: str = ""):
    row = _skill(db, name)
    is_main = row.name == repo.MAIN
    listed = ""
    if is_main:
        listed = pointers.render(pointers.main_page(catalog.items(db)))
    return render(
        request,
        "skills/show.html",
        _context(
            db, user, "skills",
            skill=row,
            is_main=is_main,
            listed=listed,
            text=repo.read(repo.skill_rel(row.name)) or "",
            history=_history(service.TARGET_SKILL, row.name),
            reads=catalog.reads(db).get(row.name, 0),
            level=policy.settings(db)["skills_edit"],
            editing=bool(edit),
            saved=saved,
            error=error,
        ),
    )


@router.get("/skills/s/{name}/change/{sha}")
async def skill_change(request: Request, db: Db, user: RequiredUser, name: str, sha: str):
    row = _skill(db, name)
    return render(
        request,
        "skills/change.html",
        _context(
            db, user, "skills",
            title=row.name,
            back=f"/skills/s/{row.name}",
            sha=sha[:12],
            diff=service.commit_diff(service.TARGET_SKILL, row.name, sha),
        ),
    )


@router.post("/skills/s/{name}")
async def save_skill(
    db: Db, user: RequiredUser, name: str, description: str = Form(""),
    category: str = Form(""), subcategory: str = Form(""), body: str = Form(""),
    reason: str = Form(""),
) -> Response:
    row = _skill(db, name)
    current = repo.read(repo.skill_rel(row.name)) or ""
    text = doc.with_body(current, body.replace("\r\n", "\n"))
    if row.name != repo.MAIN:
        text = doc.set_fields(text, {
            "description": " ".join(description.split()) or None,
            "category": doc.slug(category) if category.strip() else None,
            "subcategory": doc.slug(subcategory) if subcategory.strip() else None,
        })
    try:
        outcome = service.skill_save(
            db, policy.person(db, user), name=row.name, text=text, reason=reason or "by hand"
        )
    except (service.Refused, repo.StoreError) as exc:
        return _back(f"/skills/s/{row.name}?edit=1", error=str(exc))
    return _back(f"/skills/s/{row.name}", saved=_said(outcome))


@router.post("/skills/s/{name}/pin")
async def pin_skill(db: Db, user: RequiredUser, name: str, pinned: str = Form("")) -> Response:
    row = _skill(db, name)
    try:
        service.skill_pin(db, policy.person(db, user), name=row.name, pinned=bool(pinned))
    except (service.Refused, repo.StoreError) as exc:
        return _back(f"/skills/s/{row.name}", error=str(exc))
    return _back(f"/skills/s/{row.name}", saved=t("Saved."))


@router.post("/skills/s/{name}/delete")
async def delete_skill(db: Db, user: RequiredUser, name: str) -> Response:
    row = _skill(db, name)
    try:
        outcome = service.skill_remove(
            db, policy.person(db, user), name=row.name, reason="by hand"
        )
    except (service.Refused, repo.StoreError) as exc:
        return _back(f"/skills/s/{row.name}", error=str(exc))
    if outcome.status == service.QUEUED:
        return _back(f"/skills/s/{row.name}", saved=_said(outcome))
    return _back("/skills", saved=t("Removed."))


# --- Notes ----------------------------------------------------------------------
@router.get("/notes")
async def notes_page(
    request: Request, db: Db, user: RequiredUser, subject: str = "", kind: str = "",
    state: str = "active", group: str = "", saved: str = "", error: str = "",
):
    catalog.ensure(db)
    service.sync_if_due(db)
    who = policy.person(db, user)
    state = state if state in ("active", "archived", "all") else "active"
    rows = catalog.notes(db, who, subject=subject, kind=kind, status=state, group=group)
    usable = data_groups.usable(db, user)
    return render(
        request,
        "notes/list.html",
        _context(
            db, user, "notes",
            notes=rows,
            kinds=sorted({k for k, _n in catalog.kinds(db, who)} | set(policy.KINDS)),
            subject=subject,
            picked_kind=kind,
            state=state,
            picked_group=group,
            groups=usable if len(usable) > 1 else [],
            group_names={g.id: g.name for g in usable},
            saved=saved,
            error=error,
        ),
    )


@router.post("/notes/new")
async def new_note(
    db: Db, user: RequiredUser, title: str = Form(""), subjects: str = Form(""),
    kind: str = Form("note"), summary: str = Form(""), body: str = Form(""),
    data_group_id: str = Form(""),
) -> Response:
    who = policy.person(db, user)
    if data_group_id and data_group_id in who.groups:
        who = policy.in_group(who, data_group_id)
    try:
        outcome = service.note_add(
            db, who, title=title, body=body.replace("\r\n", "\n"), subjects=subjects, kind=kind,
            summary=summary,
        )
    except (service.Refused, repo.StoreError) as exc:
        return _back("/notes", error=str(exc))
    return _back(f"/notes/n/{outcome.name}", saved=_said(outcome))


def _note(db, user, ident: str):
    row = catalog.note(db, policy.person(db, user), ident)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That note no longer exists."))
    return row


@router.get("/notes/change/{sha}/{ident:path}")
async def note_change(request: Request, db: Db, user: RequiredUser, sha: str, ident: str):
    row = _note(db, user, ident)
    return render(
        request,
        "skills/change.html",
        _context(
            db, user, "notes",
            title=row.title,
            back=f"/notes/n/{row.ident}",
            sha=sha[:12],
            diff=service.commit_diff(service.TARGET_NOTE, row.ident, sha),
        ),
    )


@router.get("/notes/n/{ident:path}")
async def note_page(request: Request, db: Db, user: RequiredUser, ident: str, saved: str = "",
                    error: str = "", edit: str = ""):
    row = _note(db, user, ident)
    return render(
        request,
        "notes/show.html",
        _context(
            db, user, "notes",
            note=row,
            history=_history(service.TARGET_NOTE, row.ident),
            group_name=data_groups.name_of(db, row.data_group_id),
            may_share=user.is_admin or row.author_id == user.id,
            shared_kind=row.kind in policy.settings(db)["shared_kinds"],
            kinds=sorted({row.kind} | set(policy.KINDS)),
            editing=bool(edit),
            saved=saved,
            error=error,
        ),
    )


@router.post("/notes/do/{action}/{ident:path}")
async def note_action(
    db: Db, user: RequiredUser, action: str, ident: str, text: str = Form(""),
    summary: str = Form(""), subjects: str = Form(""), kind: str = Form(""),
    body: str = Form(""), reason: str = Form(""),
) -> Response:
    row = _note(db, user, ident)
    who = policy.person(db, user)
    here = f"/notes/n/{row.ident}"
    try:
        if action == "save":
            outcome = service.note_change(
                db, who, ident=row.ident, section="all", text=body.replace("\r\n", "\n"),
                summary=summary, subjects=subjects, kind=kind, reason=reason or "by hand",
            )
        elif action == "append":
            outcome = service.note_append(db, who, ident=row.ident, text=text)
        elif action == "confirm":
            outcome = service.note_confirm(db, who, ident=row.ident)
        elif action in ("archive", "restore"):
            outcome = service.note_archive(
                db, who, ident=row.ident, reason="by hand", restore=action == "restore"
            )
        elif action in ("share", "unshare"):
            outcome = service.note_share(db, who, ident=row.ident, shared=action == "share")
        elif action == "delete":
            service.note_remove(db, who, ident=row.ident, reason="by hand")
            return _back("/notes", saved=t("Removed."))
        else:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
    except (service.Refused, repo.StoreError) as exc:
        return _back(here + ("?edit=1" if action == "save" else ""), error=str(exc))
    return _back(here, saved=_said(outcome))


__all__ = ["router"]
