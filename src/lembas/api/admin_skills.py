"""Admin -> Skills and notes: the policy levels, who approves, and the remote.

The settings of the central store (`services/skillstore/policy.py`). The
remote's token and key are write-only here: stored encrypted, shown as set or
not set, never rendered back -- an empty field keeps what is stored.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from lembas.api._redirect import back
from lembas.api.deps import AdminUser, Db
from lembas.services.skillstore import catalog, gitrun, policy, repo, service
from lembas.web.i18n import t
from lembas.web.templating import render

router = APIRouter(prefix="/admin/skills", tags=["admin-skills"])

PAGE = "/admin/skills"


@router.get("")
async def skills_settings(request: Request, db: Db, user: AdminUser, saved: str = "",
                          error: str = ""):
    catalog.ensure(db)
    values = policy.settings(db)
    return render(
        request,
        "admin/skills.html",
        {
            "values": values,
            "levels": policy.LEVELS,
            "kinds": policy.KINDS,
            "has_token": bool(values.get("remote_token_encrypted")),
            "has_key": bool(values.get("remote_ssh_key_encrypted")),
            "transport": gitrun.transport(values["remote_address"])
            if values["remote_address"] else "",
            "head": repo.head()[:12],
            "counts": {
                "skills": len(catalog.skills(db)),
                "waiting": service.pending_count(db, user),
            },
            "saved": saved,
            "error": error,
        },
    )


@router.post("/policy")
async def save_policy(request: Request, db: Db, user: AdminUser) -> Response:
    form = await request.form()

    def level(name: str, default: str) -> str:
        value = str(form.get(name) or "")
        return value if value in policy.LEVELS else default

    kinds = {}
    for kind in policy.KINDS:
        value = str(form.get(f"kind_{kind}") or "")
        if value in policy.LEVELS:
            kinds[kind] = value
    skills_approvers = str(form.get("skills_approvers") or "")
    notes_approvers = str(form.get("notes_approvers") or "")
    policy.save(db, {
        "skills_edit": level("skills_edit", policy.APPROVE),
        "notes_add": level("notes_add", policy.AUTO),
        "notes_change": level("notes_change", policy.APPROVE),
        "notes_kinds": kinds,
        "shared_kinds": [k for k in policy.KINDS if form.get(f"shared_{k}")],
        "skills_approvers": skills_approvers
        if skills_approvers in policy.SKILL_APPROVERS else "admins",
        "notes_approvers": notes_approvers
        if notes_approvers in policy.NOTE_APPROVERS else "involved",
    })
    return back(PAGE, t("Saved."))


@router.post("/remote")
async def save_remote(
    db: Db, user: AdminUser, address: str = Form(""), branch: str = Form("main"),
    username: str = Form(""), token: str = Form(""), ssh_key: str = Form(""),
) -> Response:
    try:
        service.remote_set(
            db, address=address, branch=branch, username=username, token=token or None,
            ssh_key=ssh_key or None,
        )
    except service.Refused as exc:
        return back(PAGE, str(exc), ok=False)
    if not address.strip():
        return back(PAGE, t("The store has no remote now."))
    result = await asyncio.to_thread(_sync, False)
    return _told(result)


def _sync(force: bool):
    from lembas.db.session import session_scope

    with session_scope() as db:
        return service.sync_now(db, force=force)


def _told(result: repo.Synced) -> Response:
    if result.state == repo.SYNC_OK:
        return back(PAGE, result.message or t("The store and its remote agree."))
    return back(PAGE, result.message or t("The remote could not be reached."), ok=False)


@router.post("/sync")
async def sync_now(db: Db, user: AdminUser) -> Response:
    return _told(await asyncio.to_thread(_sync, True))


@router.post("/take-remote")
async def take_remote(db: Db, user: AdminUser) -> Response:
    def run():
        from lembas.db.session import session_scope

        with session_scope() as session:
            return service.take_remote(session)

    return _told(await asyncio.to_thread(run))


@router.post("/forget-hosts")
async def forget_hosts(db: Db, user: AdminUser) -> Response:
    gitrun.forget_host_keys()
    return back(PAGE, t("The remembered host keys were forgotten."))


@router.post("/reindex")
async def reindex(db: Db, user: AdminUser) -> Response:
    catalog.reindex(db)
    return back(PAGE, t("The index was rebuilt from the files."))


__all__ = ["router"]
