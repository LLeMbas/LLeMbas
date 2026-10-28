"""Invites, the invite's sign-up page, and the first-run wizard.

There is no open sign-up (`services/organisations.py`). An account comes from
an administrator, or from a **single-use, expiring invite link** into an
organisation:

* `/admin/invites` -- an instance administrator, or an owner or admin of the
  organisation, makes one (optionally for one address, as a member or an
  admin, for 1, 7 or 30 days) and sees its link **once**; only its hash is
  kept. Pending invites can be revoked.
* `/invite/{token}` -- the page the link opens: name, address, password. The
  invite is spent in the same transaction that makes the account.

**First run.** The first account is made on `/auth/register` (the instance
administrator and the owner of the first organisation); its answer lands on
`/setup`, which names the organisation and the instance and hands over to the
connections page, where the provider is detected from a pasted address.
"""

from __future__ import annotations

import contextlib
from datetime import timedelta

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from lembas.api._redirect import back
from lembas.api.deps import CurrentUser, Db, RequiredUser
from lembas.db.models import ORG_ADMIN, ORG_MEMBER, Organisation
from lembas.services import organisations, settings_store
from lembas.web.i18n import N_, t
from lembas.web.templating import render

router = APIRouter(tags=["invites"])

DAYS = (1, 7, 30)

# The service's refusals are English sentences; these are the same sentences
# for the catalogue, so a person reads them in their own language.
SAID = {
    "This invite link is not valid.": N_("This invite link is not valid."),
    "This invite has already been used.": N_("This invite has already been used."),
    "This invite has expired.": N_("This invite has expired."),
    "This invite is for another email address.": N_("This invite is for another email address."),
}


def _said(problem: Exception) -> str:
    text = str(problem)
    return t(SAID[text]) if text in SAID else text


def _manager(user) -> None:
    if not (user.is_admin or user.manages_org):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("Only an administrator can invite."))


def _page(request, db, user, **extra):
    from lembas.api.devices import public_base

    return render(
        request,
        "admin/invites.html",
        {
            "section": "invites",
            "invites": organisations.pending_invites(db, user.org_id),
            "days": DAYS,
            "base": public_base(request),
            "saved": request.query_params.get("saved", ""),
            "error": request.query_params.get("error", ""),
            **extra,
        },
    )


@router.get("/admin/invites")
async def invites_page(request: Request, db: Db, user: RequiredUser):
    _manager(user)
    return _page(request, db, user)


@router.post("/admin/invites")
async def make_invite(
    request: Request,
    db: Db,
    user: RequiredUser,
    email: str = Form(""),
    role: str = Form(ORG_MEMBER),
    days: int = Form(7),
):
    _manager(user)
    from lembas.api.devices import public_base

    try:
        made = organisations.create_invite(
            db,
            user.org_id,
            created_by=user,
            email=email.strip() or None,
            org_role=role if role in (ORG_MEMBER, ORG_ADMIN) else ORG_MEMBER,
            ttl=timedelta(days=days if days in DAYS else 7),
        )
    except organisations.InviteError as problem:
        return back("/admin/invites", _said(problem), ok=False)
    # Rendered, not redirected: the link is shown this once, and a reload must
    # not show it again.
    return _page(request, db, user, link=f"{public_base(request)}/invite/{made.token}")


@router.post("/admin/invites/{invite_id}/revoke")
async def revoke(db: Db, user: RequiredUser, invite_id: str) -> Response:
    _manager(user)
    from lembas.db.models import Invite

    invite = db.get(Invite, invite_id)
    if invite is None or invite.org_id != user.org_id:
        return back("/admin/invites", t("That invite is not there any more."), ok=False)
    organisations.revoke_invite(db, invite_id)
    return back("/admin/invites", t("Invite revoked."))


# --- The link's page ----------------------------------------------------------------
@router.get("/invite/{token}")
async def accept_form(request: Request, db: Db, user: CurrentUser, token: str):
    if user is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    try:
        invite = organisations.find_invite(db, token)
    except organisations.InviteError as problem:
        return render(request, "auth/invite.html", {"problem": _said(problem)},
                      status_code=status.HTTP_404_NOT_FOUND)
    org = db.get(Organisation, invite.org_id)
    return render(
        request,
        "auth/invite.html",
        {
            "token": token,
            "org": org,
            "email": invite.email or "",
            "fixed_email": bool(invite.email),
        },
    )


@router.post("/invite/{token}")
async def accept(
    request: Request,
    db: Db,
    token: str,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
):
    from lembas.api.auth import _set_session_cookie
    from lembas.security.sessions import create_session
    from lembas.services import audit

    try:
        user = organisations.accept_invite(db, token, name=name, email=email, password=password)
    except (organisations.InviteError, organisations.AccountError) as problem:
        invite = None
        with contextlib.suppress(organisations.InviteError):
            invite = organisations.find_invite(db, token)
        return render(
            request,
            "auth/invite.html",
            {
                "token": token if invite else "",
                "org": db.get(Organisation, invite.org_id) if invite else None,
                "email": email.strip().lower(),
                "name": name.strip(),
                "fixed_email": bool(invite and invite.email),
                "problem": _said(problem),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    audit.record("auth.invite", user=user, request=request, summary=f"{user.email} joined")
    session = create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=request.client.host if request.client else "",
    )
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, session, request)
    return response


# --- First run ------------------------------------------------------------------------
@router.get("/setup")
async def setup_page(request: Request, db: Db, user: RequiredUser):
    if not user.is_admin:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    org = organisations.get(db, user.org_id)
    general = settings_store.get_group(db, settings_store.GENERAL)
    return render(
        request,
        "auth/setup.html",
        {"org": org, "instance_name": general.get("instance_name") or ""},
    )


@router.post("/setup")
async def save_setup(
    db: Db,
    user: RequiredUser,
    organisation: str = Form(""),
    instance_name: str = Form(""),
) -> Response:
    if not user.is_admin:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    org = organisations.get(db, user.org_id)
    name = " ".join(organisation.split())[:120]
    if org is not None and name:
        org.name = name
    settings_store.update(db, {"instance_name": " ".join(instance_name.split())[:60]})
    db.commit()
    from lembas.services import branding

    branding.forget()
    # The connection, and the provider detected from its address, are the
    # connections page's (Admin → Connections).
    return RedirectResponse("/admin/connections?first_run=1", status_code=status.HTTP_303_SEE_OTHER)


__all__ = ["router"]
