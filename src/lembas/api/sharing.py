"""Giving somebody else access to one thing.

Its own routes and its own fragment, rather than a block of checkboxes riding
along with the resource's save form. Three reasons, in the order they bite:

- **It rendered every group and every person on the instance, unpaginated, on
  every detail page.** That is fine for a household and unusable for anything
  else, and the page it is on has nothing to do with how many accounts exist.
- **A share was only stored if the resource was saved.** Ticking a box and
  navigating away did nothing, silently, which is the shape of failure this
  codebase keeps cataloguing.
- Sharing a *report* has no save form to ride along with at all.

So: search, and each grant is its own POST. The fragment re-renders itself after
every change, which is what keeps "who can see this" a thing you read rather
than a thing you reconstruct from checkboxes.

**Only the owner may reach any of it.** Somebody a thing was shared with cannot
share it on -- that is what keeps "who can see this?" answerable by asking one
person -- and the check is `sharing.can_write`, which is ownership and nothing
else.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from sqlalchemy import or_, select

from lembas.api.deps import Db, RequiredUser
from lembas.db.models import (
    PRINCIPAL_GROUP,
    PRINCIPAL_USER,
    Group,
    KnowledgeBase,
    Note,
    Report,
    Skill,
    User,
)
from lembas.security import permissions
from lembas.services import sharing
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/library/share", tags=["sharing"])

# What a URL may name, and what it resolves to. A fixed table rather than a
# lookup by string on `sharing.RESOURCE_TYPES`, because that one maps class to
# string and this needs the other direction -- and because a route segment is
# request input, so the set of things it may name belongs written down.
KINDS: dict[str, type] = {
    "base": KnowledgeBase,
    "note": Note,
    "skill": Skill,
    "report": Report,
}

# Candidates offered at once. Enough that a small instance never has to type
# anything, few enough that a large one is not a page of names.
MAX_CANDIDATES = 12


def _resource(db: Db, kind: str, resource_id: str, user: User):
    model = KINDS.get(kind)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("Not a shareable kind."))
    resource = db.get(model, resource_id)
    # Ownership, not readability. Being able to see a thing is not being able to
    # give it away, and the 404 rather than a 403 is deliberate: somebody who
    # cannot share it has no business learning whether it exists.
    if resource is None or not sharing.can_write(resource, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That is not yours to share."))
    return resource


def _panel(request: Request, db: Db, user: User, kind: str, resource, q: str = "") -> Response:
    grants = sharing.grants_for(db, resource)
    shared_users = [g.principal_id for g in grants if g.principal_type == PRINCIPAL_USER]
    shared_groups = [g.principal_id for g in grants if g.principal_type == PRINCIPAL_GROUP]

    needle = q.strip()
    pattern = f"%{needle}%"
    group_query = select(Group).order_by(Group.name)
    people_query = select(User).where(User.id != user.id).order_by(User.name)
    if needle:
        group_query = group_query.where(Group.name.ilike(pattern))
        people_query = people_query.where(
            or_(User.name.ilike(pattern), User.email.ilike(pattern))
        )

    # Anything already shared is shown whatever the search says, or the only way
    # to remove a grant would be to search for the name it was given to.
    groups = list(db.scalars(group_query.limit(MAX_CANDIDATES)))
    people = list(db.scalars(people_query.limit(MAX_CANDIDATES)))
    for existing in db.scalars(select(Group).where(Group.id.in_(shared_groups or [""]))):
        if existing.id not in {g.id for g in groups}:
            groups.insert(0, existing)
    for existing in db.scalars(select(User).where(User.id.in_(shared_users or [""]))):
        if existing.id not in {p.id for p in people}:
            people.insert(0, existing)

    return render(
        request,
        "library/_share_panel.html",
        {
            "kind": kind,
            "resource": resource,
            "q": needle,
            "groups": groups,
            "people": people,
            "shared_users": shared_users,
            "shared_groups": shared_groups,
            "share_count": len(grants),
            # Whether the lists were cut, so the panel can say "search for
            # somebody" rather than implying these are all the names there are.
            "truncated": len(people) >= MAX_CANDIDATES or len(groups) >= MAX_CANDIDATES,
        },
    )


@router.get("/{kind}/{resource_id}")
async def share_panel(
    request: Request, db: Db, user: RequiredUser, kind: str, resource_id: str, q: str = ""
) -> Response:
    resource = _resource(db, kind, resource_id, user)
    if not permissions.has(db, user, "library.share"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("You may not share things."))
    return _panel(request, db, user, kind, resource, q)


@router.post("/{kind}/{resource_id}")
async def set_share(
    request: Request,
    db: Db,
    user: RequiredUser,
    kind: str,
    resource_id: str,
    principal_type: str = Form(""),
    principal_id: str = Form(""),
    on: bool = Form(False),
    q: str = Form(""),
) -> Response:
    """Add or remove one grant, and answer with the panel.

    One grant per request rather than a submitted set, because the set is what
    made the old panel need every name on the instance in front of you before
    you could change one of them.
    """
    resource = _resource(db, kind, resource_id, user)
    if not permissions.has(db, user, "library.share"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("You may not share things."))
    if principal_type not in (PRINCIPAL_USER, PRINCIPAL_GROUP):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("Unknown principal."))

    grants = sharing.grants_for(db, resource)
    users = [g.principal_id for g in grants if g.principal_type == PRINCIPAL_USER]
    groups = [g.principal_id for g in grants if g.principal_type == PRINCIPAL_GROUP]
    target = users if principal_type == PRINCIPAL_USER else groups

    # Validated against what exists, so a crafted id cannot write a grant naming
    # nothing -- which would be invisible in the panel and unremovable from it.
    exists = db.get(User if principal_type == PRINCIPAL_USER else Group, principal_id)
    if on and exists is not None and principal_id not in target:
        target.append(principal_id)
    elif not on and principal_id in target:
        target.remove(principal_id)

    sharing.set_grants(db, resource, user_ids=users, group_ids=groups)
    log.info(
        "%s %s %s %s with %s", user.email, "shared" if on else "unshared", kind,
        resource_id, principal_id,
    )
    return _panel(request, db, user, kind, resource, q)
