"""Who may see a knowledge base, a note, a skill or a report.

One rule, in one place, for all four: you can see a resource if you own it, if
it was shared with you by name, or if it was shared with a group you are in.

Documents are deliberately absent from that list. They are shared through the
knowledge base they belong to -- "this folder is the team's" is the granularity
people think in, and per-document grants would mean answering "who can see
this?" by checking every file. See services.library.documents.visible.

Everything that lists or searches a library store goes through `visible_to`.
Writing the same condition into each query would work right up until one of
them was written slightly differently, and the way that failure shows up is
somebody reading somebody else's notes.

**Administrators are not exempt.** They are elsewhere in this codebase --
`security.permissions.resolve` hands an admin every permission -- and that is
right for configuration, because an admin can grant themselves those two clicks
away. This is a different thing. Nobody made these records available to anyone,
and being able to reach a database is not the same as being invited.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import ColumnElement, and_, delete, or_, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    PRINCIPAL_GROUP,
    PRINCIPAL_USER,
    RESOURCE_BASE,
    RESOURCE_NOTE,
    RESOURCE_REPORT,
    RESOURCE_SKILL,
    KnowledgeBase,
    Note,
    Report,
    Share,
    Skill,
    User,
)

log = logging.getLogger(__name__)

# The mapping between a model class and the string stored in Share. Kept here
# so no caller has to remember which literal goes with which table.
RESOURCE_TYPES: dict[Any, str] = {
    KnowledgeBase: RESOURCE_BASE,
    Note: RESOURCE_NOTE,
    Skill: RESOURCE_SKILL,
    # A report joins the list and a memory still does not. A finished piece of
    # work is the thing somebody most wants to hand over -- "here is what the
    # Monday run found" -- and a report is read once and never answered, so
    # sharing it has none of the two-editors problem that keeps writing off the
    # table everywhere else here.
    Report: RESOURCE_REPORT,
}


def resource_type(model: Any) -> str:
    kind = RESOURCE_TYPES.get(model if isinstance(model, type) else type(model))
    if kind is None:
        raise ValueError(f"{model!r} is not a shareable resource")
    return kind


def principal_ids(user: User | None) -> tuple[list[str], list[str]]:
    """The ids a share could name to reach this user: themselves, their groups."""
    if user is None:
        return [], []
    return [user.id], [group.id for group in user.groups]


def visible_to(
    model: Any, user: User | None, group: str | None = None
) -> ColumnElement[bool]:
    """A WHERE clause selecting the rows of `model` this user may see.

    Returned as a condition rather than a query so callers can add their own
    filtering, ordering and pagination without this module knowing about any of
    it.

    `group` narrows to one data group, and is what every path that hands rows to
    a *model* passes -- a model reads only its own group's data. `None` is the
    person's own view of their library, which shows every group they have: the
    isolation is between providers, not between a person and their records.
    """
    if user is None:
        # Signed out sees nothing. Not an empty library -- no library.
        return model.id.is_(None)

    users, groups = principal_ids(user)
    shared = select(Share.resource_id).where(
        Share.resource_type == resource_type(model),
        or_(
            (Share.principal_type == PRINCIPAL_USER) & Share.principal_id.in_(users),
            (Share.principal_type == PRINCIPAL_GROUP) & Share.principal_id.in_(groups or [""]),
        ),
    )
    seen = or_(model.owner_id == user.id, model.id.in_(shared))
    if group is None:
        return seen
    from lembas.services import data_groups

    return and_(seen, data_groups.condition(model, group))


def only_shared(model: Any, user: User | None) -> ColumnElement[bool]:
    """Rows this user may see and does **not** own.

    The "Shared with me" filter. Worth having as its own listing rather than a
    badge in the mixed one: a badge answers "is this mine?" for a row already on
    screen, and the question somebody actually has is "what have people given
    me?", which a mixed list of two hundred cannot answer at all.
    """
    if user is None:
        return model.id.is_(None)
    return and_(visible_to(model, user), model.owner_id != user.id)


def owned_by(model: Any, user: User | None) -> ColumnElement[bool]:
    """Rows this user may *change*.

    Sharing grants reading, never writing. Two people editing one note with no
    history and no merge is worse than the inconvenience of copying it.
    """
    if user is None:
        return model.id.is_(None)
    return model.owner_id == user.id


def can_read(
    db: DBSession, resource: Any, user: User | None, group: str | None = None
) -> bool:
    """Whether this user may read one row -- and, given `group`, whether it is in it.

    The group half is what makes fetching a record *by id* obey the same
    boundary as searching for it. Without it a model that learned an id from
    another group's transcript could read the record straight past the filter.
    """
    if user is None or resource is None:
        return False
    if group is not None:
        from lembas.services import data_groups

        if data_groups.group_of(resource) != group:
            return False
    if resource.owner_id == user.id:
        return True
    users, groups = principal_ids(user)
    found = db.scalar(
        select(Share.id).where(
            Share.resource_type == resource_type(resource),
            Share.resource_id == resource.id,
            or_(
                (Share.principal_type == PRINCIPAL_USER) & Share.principal_id.in_(users),
                (Share.principal_type == PRINCIPAL_GROUP)
                & Share.principal_id.in_(groups or [""]),
            ),
        )
    )
    return found is not None


def can_write(resource: Any, user: User | None) -> bool:
    return user is not None and resource is not None and resource.owner_id == user.id


# --- Managing grants ---------------------------------------------------------
def grants_for(db: DBSession, resource: Any) -> list[Share]:
    return list(
        db.scalars(
            select(Share).where(
                Share.resource_type == resource_type(resource),
                Share.resource_id == resource.id,
            )
        )
    )


def set_grants(
    db: DBSession,
    resource: Any,
    *,
    user_ids: list[str],
    group_ids: list[str],
) -> None:
    """Replace a resource's shares with exactly these principals."""
    kind = resource_type(resource)
    db.execute(
        delete(Share).where(Share.resource_type == kind, Share.resource_id == resource.id)
    )

    wanted = [(PRINCIPAL_USER, i) for i in dict.fromkeys(user_ids) if i] + [
        (PRINCIPAL_GROUP, i) for i in dict.fromkeys(group_ids) if i
    ]
    for principal_type, principal_id in wanted:
        # Sharing with yourself is not wrong, just meaningless -- you own it.
        if principal_type == PRINCIPAL_USER and principal_id == resource.owner_id:
            continue
        db.add(
            Share(
                resource_type=kind,
                resource_id=resource.id,
                principal_type=principal_type,
                principal_id=principal_id,
            )
        )
    db.commit()


def forget_resource(db: DBSession, resource: Any) -> None:
    """Drop every share of a resource that is being deleted.

    Shares carry no foreign key to their resource -- one column pointing at
    three tables cannot have one -- so nothing cascades and this has to be
    called explicitly.
    """
    db.execute(
        delete(Share).where(
            Share.resource_type == resource_type(resource),
            Share.resource_id == resource.id,
        )
    )


def forget_principal(db: DBSession, principal_type: str, principal_id: str) -> int:
    """Drop every share naming a user or group that has been deleted.

    Same reason as above: no foreign key, so nothing cascades. Called when an
    account or a group goes; a stale row would otherwise grant access to
    whoever next received that id, which is not a risk worth carrying for the
    sake of a tidy delete.
    """
    result = db.execute(
        delete(Share).where(
            Share.principal_type == principal_type, Share.principal_id == principal_id
        )
    )
    return result.rowcount or 0


def forget_owner(db: DBSession, owner_id: str) -> int:
    """Drop every share of everything a departing account owned.

    Their rows cascade when the account goes; the shares of those rows do not,
    because `Share.resource_id` has no foreign key to point at. Left behind,
    they are grants naming resources that no longer exist -- harmless today,
    and a grant to whoever next receives one of those ids if a future store
    ever reuses them.

    Called *before* the delete, while the rows are still there to be found.
    `forget_principal` is the other half and covers shares pointing *at* them.
    """
    removed = 0
    for model in RESOURCE_TYPES:
        owned = select(model.id).where(model.owner_id == owner_id)
        result = db.execute(
            delete(Share).where(
                Share.resource_type == RESOURCE_TYPES[model],
                Share.resource_id.in_(owned),
            )
        )
        removed += result.rowcount or 0
    return removed


__all__ = [
    "can_read",
    "can_write",
    "forget_owner",
    "forget_principal",
    "forget_resource",
    "grants_for",
    "only_shared",
    "owned_by",
    "resource_type",
    "set_grants",
    "visible_to",
]
