"""User and group administration."""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import AdminUser, Db
from lembas.db.models import (
    ROLE_ADMIN,
    ROLE_PENDING,
    ROLE_USER,
    Chat,
    Group,
    Model,
    User,
)
from lembas.security import permissions
from lembas.security.passwords import hash_password, validate_password
from lembas.security.sessions import revoke_all_for_user
from lembas.services import chat as chat_service
from lembas.services import settings_store
from lembas.services import usage as usage_service
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-users"])

ROLES = (ROLE_ADMIN, ROLE_USER, ROLE_PENDING)


def _user(db: DBSession, user_id: str) -> User:
    found = db.get(User, user_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That user no longer exists."))
    return found


def _group(db: DBSession, group_id: str) -> Group:
    found = db.get(Group, group_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That group no longer exists."))
    return found


def _admin_count(db: DBSession) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(User.role == ROLE_ADMIN, User.active.is_(True))
    )


def _would_orphan_the_instance(db: DBSession, user: User) -> bool:
    """True if changing this user would leave nobody able to administer.

    An instance with no active administrator can only be recovered from the
    command line, so every path that could cause it is blocked in the UI.
    """
    return user.role == ROLE_ADMIN and user.active and _admin_count(db) <= 1


# --- Users -------------------------------------------------------------------
# List plus detail, which is the shape this codebase already mandates for admin
# lists and the one `/admin/models` follows. The single page it replaces
# rendered a full form per account *and* a membership grid, and edited that
# membership from the opposite side to `/admin/groups` -- so a full-form POST
# from either overwrote what the other had just shown.
#
# Membership is now edited from **one** side, the group's. A user's page links
# to their groups and does not offer to change them, because two controls
# writing one value is how each becomes the answer to "why did my change not
# stick?".
PAGE_SIZE = 25


@router.get("/users")
async def users_page(
    request: Request, db: Db, user: AdminUser, q: str = "", saved: str = "", page: int = 1
):
    query = select(User).order_by(User.created_at)
    if q.strip():
        pattern = f"%{q.strip()}%"
        query = query.where(or_(User.name.ilike(pattern), User.email.ilike(pattern)))

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(1, page), pages)
    rows = list(db.scalars(query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)))

    return render(
        request,
        "admin/users.html",
        {
            "users": rows,
            "usage": {row.id: usage_service.summary(db, row) for row in rows},
            "roles": ROLES,
            "q": q,
            "saved": saved,
            "pager": {"page": page, "pages": pages, "total": total},
            "admin_count": _admin_count(db),
        },
    )


def _tokens_of(db, target) -> list:
    from sqlalchemy import select

    from lembas.db.models import ApiToken

    return list(
        db.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == target.id)
            .order_by(ApiToken.kind, ApiToken.created_at.desc())
        )
    )


def _linked_ids(target) -> set[str]:
    from lembas.services import device_link

    return {link.token_id for link in device_link.linked_for(target.id)}


@router.get("/users/{user_id}")
async def user_detail(request: Request, db: Db, user: AdminUser, user_id: str, saved: str = ""):
    """One account, and the answer to "what can this person actually do?".

    That answer is `permissions.explain`, which is `resolve`'s working shown
    rather than thrown away. Read-only on purpose: every one of those switches
    is set somewhere else -- the baseline, or a named group -- and a control here
    would be a third place to change one thing.
    """
    target = _user(db, user_id)
    return render(
        request,
        "admin/user_detail.html",
        {
            "target": target,
            "roles": ROLES,
            "explained": permissions.explain(db, target),
            "permission_groups": permissions.permission_groups(),
            "limits": permissions.limits_for(db, target),
            "limit_defs": permissions.LIMIT_DEFS,
            "usage": usage_service.summary(db, target),
            "models": permissions.models_visible_to(db, target),
            # Their tokens and signed-in devices, here rather than on a
            # page listing every token on the instance.
            "tokens": _tokens_of(db, target),
            "linked": _linked_ids(target),
            "saved": saved,
            "admin_count": _admin_count(db),
        },
    )


@router.post("/users")
async def create_user(
    db: Db,
    user: AdminUser,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(ROLE_USER),
) -> Response:
    """Create an account directly, without going through registration."""
    email = email.strip().lower()
    if (problem := validate_password(password)) is not None:
        return RedirectResponse(f"/admin/users?saved={quote(problem)}", status_code=303)
    if db.scalar(select(User).where(User.email == email)) is not None:
        return RedirectResponse(
            f"/admin/users?saved={quote(t('That email is already registered.'))}",
            status_code=303,
        )

    db.add(
        User(
            name=name.strip()[:120] or email,
            email=email,
            password_hash=hash_password(password),
            role=role if role in ROLES else ROLE_USER,
        )
    )
    db.commit()
    log.info("%s created account %s", user.email, email)
    return RedirectResponse(
        f"/admin/users?saved={quote(t('Created %(email)s.', email=email))}", status_code=303
    )


@router.post("/users/{user_id}")
async def update_user(
    db: Db,
    user: AdminUser,
    user_id: str,
    name: str = Form(...),
    role: str = Form(ROLE_USER),
    active: bool = Form(False),
) -> Response:
    """Name, role and whether the account is active. **Not membership.**

    That moved to the group's page. It used to be here as well, and a full-form
    POST from either side overwrote whatever the other had -- two controls, one
    value, and no answer to which one wins.
    """
    target = _user(db, user_id)

    losing_admin = target.role == ROLE_ADMIN and (role != ROLE_ADMIN or not active)
    if losing_admin and _would_orphan_the_instance(db, target):
        return RedirectResponse(
            "/admin/users?saved="
            + quote(t("That is the only administrator. Promote someone else first.")),
            status_code=303,
        )

    target.name = name.strip()[:120] or target.name
    target.role = role if role in ROLES else target.role
    target.active = active

    # A deactivated or demoted user must lose their live sessions immediately,
    # otherwise the change only takes effect when their cookie happens to expire.
    if not active:
        revoke_all_for_user(db, target)

    db.commit()
    if not active:
        await _cut_off(target.id)
    log.info("%s updated account %s (role=%s active=%s)", user.email, target.email, role, active)
    return RedirectResponse(
        f"/admin/users/{target.id}?saved={quote(t('Saved %(email)s.', email=target.email))}",
        status_code=303,
    )


@router.post("/users/{user_id}/password")
async def reset_password(
    db: Db, user: AdminUser, user_id: str, password: str = Form(...)
) -> Response:
    target = _user(db, user_id)
    if (problem := validate_password(password)) is not None:
        return RedirectResponse(
            f"/admin/users/{user_id}?saved={quote(problem)}", status_code=303
        )

    target.password_hash = hash_password(password)
    db.commit()
    # Everywhere that account was signed in is now signed out. An admin reset
    # usually means the account is compromised or the person is gone.
    revoke_all_for_user(db, target)
    log.info("%s reset the password for %s", user.email, target.email)
    return RedirectResponse(
        f"/admin/users/{target.id}?saved={quote(t('Password reset. Sessions revoked.'))}",
        status_code=303,
    )


async def _cut_off(user_id: str) -> None:
    """End an account's live device links and shells at once. Signing the
    browser out is not enough: a linked LLeMbas CLI and an open terminal are
    sockets of their own, and they would otherwise run until they idled out."""
    import contextlib

    from lembas.services import device_link
    from lembas.services.agent import terminal as terminal_service

    for link in device_link.linked_for(user_id):
        device_link.unregister(link)
        with contextlib.suppress(Exception):
            await link.close(device_link.CLOSE_REFUSED, "account closed")
    await terminal_service.close_for_owner(user_id)


@router.post("/users/{user_id}/delete")
async def delete_user(db: Db, user: AdminUser, user_id: str) -> Response:
    target = _user(db, user_id)

    if target.id == user.id:
        return RedirectResponse(
            f"/admin/users?saved={quote(t('You cannot delete your own account.'))}",
            status_code=303,
        )
    if _would_orphan_the_instance(db, target):
        return RedirectResponse(
            f"/admin/users?saved={quote(t('That is the only administrator.'))}",
            status_code=303,
        )

    email = target.email
    # Chats and folders cascade; that is the point of deleting an account.
    #
    # The chats cascade, their attachment rows
    # cascade, and every file those rows named stays on disk with nothing left
    # that will ever look at it. Before the delete, while the rows still say
    # which files they are.
    chat_service.delete_chats(db, list(db.scalars(select(Chat).where(Chat.user_id == target.id))))
    # Its device tokens cascade; the deletes waiting for those devices would not,
    # and no device of a deleted account links again to take them.
    from lembas.db.models import ApiToken
    from lembas.security import api_tokens

    api_tokens.forget_device(
        db, list(db.scalars(select(ApiToken.id).where(ApiToken.user_id == target.id)))
    )
    target_id = target.id
    db.delete(target)
    db.commit()
    await _cut_off(target_id)
    log.info("%s deleted account %s", user.email, email)
    return RedirectResponse(
        f"/admin/users?saved={quote(t('Deleted %(email)s.', email=email))}", status_code=303
    )


# --- Groups ------------------------------------------------------------------
# The same list-plus-detail shape. The old page rendered every group's full
# permission grid, every member and every model on one screen, which is fine for
# two groups and unreadable at ten.
@router.get("/groups")
async def groups_page(request: Request, db: Db, user: AdminUser, saved: str = ""):
    groups = list(db.scalars(select(Group).order_by(Group.name)))
    return render(
        request,
        "admin/groups.html",
        {
            "groups": groups,
            "granted": {
                group.id: sum(1 for on in (group.permissions_json or {}).values() if on)
                for group in groups
            },
            "permission_groups": permissions.permission_groups(),
            "baseline": permissions.baseline_permissions(db),
            "saved": saved,
        },
    )


@router.get("/groups/{group_id}")
async def group_detail(request: Request, db: Db, user: AdminUser, group_id: str, saved: str = ""):
    group = _group(db, group_id)
    return render(
        request,
        "admin/group_detail.html",
        {
            "group": group,
            "users": list(db.scalars(select(User).order_by(User.name))),
            "models": list(db.scalars(select(Model).order_by(Model.position, Model.model_id))),
            "permission_groups": permissions.permission_groups(),
            "baseline": permissions.baseline_permissions(db),
            "limit_defs": permissions.LIMIT_DEFS,
            "limits": group.limits_json or {},
            "saved": saved,
        },
    )


@router.post("/groups")
async def create_group(db: Db, user: AdminUser, name: str = Form(...)) -> Response:
    name = name.strip()[:120]
    if not name:
        return RedirectResponse(
            f"/admin/groups?saved={quote(t('A group needs a name.'))}", status_code=303
        )
    if db.scalar(select(Group).where(Group.name == name)) is not None:
        return RedirectResponse(
            f"/admin/groups?saved={quote(t('A group with that name already exists.'))}",
            status_code=303,
        )

    db.add(Group(name=name))
    db.commit()
    log.info("%s created group %s", user.email, name)
    return RedirectResponse(
        f"/admin/groups?saved={quote(t('Created %(name)s.', name=name))}", status_code=303
    )


@router.post("/groups/{group_id}")
async def update_group(
    request: Request,
    db: Db,
    user: AdminUser,
    group_id: str,
    name: str = Form(...),
    description: str = Form(""),
    permission: list[str] = Form(default=[]),
    user_ids: list[str] = Form(default=[]),
    model_ids: list[str] = Form(default=[]),
) -> Response:
    group = _group(db, group_id)
    form = await request.form()

    group.name = name.strip()[:120] or group.name
    group.description = description.strip()[:1000]
    # The submitted checkbox list is the complete new state; absent means the
    # group does not grant that permission, not that it denies it.
    group.permissions_json = {key: True for key in permission if key in permissions.PERMISSION_KEYS}
    group.users = list(db.scalars(select(User).where(User.id.in_(user_ids or []))))
    group.models = list(db.scalars(select(Model).where(Model.id.in_(model_ids or []))))

    # Quotas. Only what was submitted and could be read as a number is stored, so
    # a blank box means "this group has no opinion" and contributes nothing to
    # the resolution -- which is what `limits_for` needs in order to tell it
    # apart from a deliberate zero, and zero here means *no limit*.
    wanted: dict[str, int] = {}
    for key in permissions.LIMIT_KEYS:
        raw = str(form.get(f"limit_{key}") or "").strip()
        if not raw:
            continue
        try:
            wanted[key] = max(0, int(raw))
        except ValueError:
            continue
    group.limits_json = wanted

    db.commit()
    log.info("%s updated group %s", user.email, group.name)
    return RedirectResponse(
        f"/admin/groups/{group.id}?saved={quote(t('Saved %(name)s.', name=group.name))}",
        status_code=303,
    )


@router.post("/groups/{group_id}/delete")
async def delete_group(db: Db, user: AdminUser, group_id: str) -> Response:
    group = _group(db, group_id)
    name = group.name
    # Members and model links go with it; the users themselves are untouched.
    db.delete(group)
    db.commit()
    log.info("%s deleted group %s", user.email, name)
    return RedirectResponse(
        f"/admin/groups?saved={quote(t('Deleted %(name)s.', name=name))}", status_code=303
    )


@router.post("/permissions/defaults")
async def save_baseline(
    db: Db, user: AdminUser, permission: list[str] = Form(default=[])
) -> Response:
    """The permissions every user has before any group widens them."""
    settings_store.update(
        db,
        {
            "default_permissions": {
                key: (key in permission) for key in permissions.PERMISSION_KEYS
            }
        },
    )
    log.info("%s changed the baseline permissions", user.email)
    return RedirectResponse(
        f"/admin/groups?saved={quote(t('Default permissions saved.'))}", status_code=303
    )
