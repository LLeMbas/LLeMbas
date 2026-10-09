"""Data groups: which provider may read which part of the people's data.

List plus detail, the shape every admin list here follows. The list says what
each group holds; the detail says which providers read it, which services send
its data somewhere else, and lets an administrator change both.

The one sentence this page exists to make answerable is "which provider has
seen this?". So the detail page lists every place a group's data can leave by --
its connections, its embedder and its reviewer -- and flags the ones whose
connection is in a *different* group, because those are the ones nobody would
think to check.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from lembas.api.deps import AdminUser, Db
from lembas.db.models import Connection, DataGroup, Model, User
from lembas.services import data_groups, settings_store
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/data-groups", tags=["admin-data-groups"])


@dataclass(frozen=True)
class Exit:
    """One way a group's data leaves it: a provider, and whether it is outside."""

    what: str
    model: str
    connection: str
    elsewhere: str  # the other group's name, or "" when it is this group's own


def labels() -> dict[str, str]:
    """The words `data_groups.COUNTED` and `exits` produce, in the reader's language.

    Written out as literal `t()` calls because the templates look them up by
    key, and a key that exists only as data is one the catalogue extractor never
    finds -- so it would stay English forever, silently. Called per request, not
    at import, because the language is the request's.
    """
    return {
        "chats": t("chats"),
        "memories": t("memories"),
        "notes": t("notes"),
        "skills": t("skills"),
        "knowledge bases": t("knowledge bases"),
        "reports": t("reports"),
        "connections": t("connections"),
        "Chat models": t("Chat models"),
        "Embedding": t("Embedding"),
        "Image review": t("Image review"),
    }


def _group(db, group_id: str) -> DataGroup:
    group = data_groups.get(db, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("No such data group."))
    return group


def _service_model(db, model_id: str, connection_id: str) -> Model | None:
    if not model_id:
        return None
    return db.scalar(
        select(Model)
        .join(Connection)
        .where(Model.model_id == model_id, Connection.enabled.is_(True))
        .order_by(Model.connection_id != (connection_id or ""), Connection.position)
    )


def exits(db, group: DataGroup) -> list[Exit]:
    """Every provider this group's data is sent to, as the instance has it set.

    Personal remaps are not here: they belong to one person, change what that
    person's providers read, and are listed on that person's own settings page.
    """
    found: list[Exit] = []
    for connection in db.scalars(select(Connection).order_by(Connection.position)):
        if data_groups.for_connection(db, None, connection.id) == group.id:
            found.append(Exit("Chat models", "", connection.name, ""))

    extraction = settings_store.extraction(db)
    images = settings_store.images(db)
    services = (
        (
            "Embedding",
            group.embedding_model_id or str(extraction.get("embedding_model_id") or ""),
            group.embedding_connection_id if group.embedding_model_id else "",
        ),
        (
            "Image review",
            group.review_model_id
            or (str(images.get("review_model_id") or "") if images.get("review_enabled") else ""),
            group.review_connection_id if group.review_model_id else "",
        ),
    )
    for what, model_id, connection_id in services:
        model = _service_model(db, model_id, connection_id)
        if model is None:
            continue
        other = data_groups.for_connection(db, None, model.connection_id)
        found.append(
            Exit(
                what,
                model.label,
                model.connection.name if model.connection else "",
                data_groups.name_of(db, other) if other != group.id else "",
            )
        )
    return found


def _capable(db, capability: str) -> list[Model]:
    rows = db.scalars(
        select(Model)
        .join(Connection)
        .where(Model.enabled.is_(True), Connection.enabled.is_(True))
        .order_by(Model.position, Model.model_id)
    )
    return [m for m in rows if (m.capabilities_json or {}).get(capability)]


@router.get("")
async def data_groups_page(request: Request, db: Db, user: AdminUser, saved: str = ""):
    groups = data_groups.instance_groups(db)
    personal = [g for g in data_groups.all_groups(db) if g.owner_id is not None]
    owners = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_([g.owner_id for g in personal])))
    }
    connections = {
        group.id: db.scalar(
            select(func.count())
            .select_from(Connection)
            .where(data_groups.condition(Connection, group.id))
        )
        or 0
        for group in groups
    }
    return render(
        request,
        "admin/data_groups.html",
        {
            "groups": groups,
            "personal": personal,
            "owners": owners,
            "connections": connections,
            "counts": {g.id: data_groups.counts(db, g.id) for g in [*groups, *personal]},
            "labels": labels(),
            "saved": saved,
        },
    )


@router.post("")
async def create_group(db: Db, user: AdminUser, name: str = Form(...)) -> Response:
    name = " ".join(name.split())[:120]
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("A data group needs a name."))
    position = db.scalar(select(func.coalesce(func.max(DataGroup.position), 0))) + 1
    group = DataGroup(name=name, position=position)
    db.add(group)
    db.commit()
    return RedirectResponse(f"/admin/data-groups/{group.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{group_id}")
async def data_group_detail(
    request: Request, db: Db, user: AdminUser, group_id: str, saved: str = "", error: str = ""
):
    group = _group(db, group_id)
    all_connections = list(db.scalars(select(Connection).order_by(Connection.position)))
    return render(
        request,
        "admin/data_group_detail.html",
        {
            "group": group,
            "owner": db.get(User, group.owner_id) if group.owner_id else None,
            "connections": all_connections,
            "member_ids": {
                c.id
                for c in all_connections
                if data_groups.for_connection(db, None, c.id) == group.id
            },
            "embedders": _capable(db, "embeddings"),
            "reviewers": _capable(db, "vision"),
            "exits": exits(db, group),
            "counts": data_groups.counts(db, group.id),
            "in_use": data_groups.in_use(db, group.id),
            "labels": labels(),
            "saved": saved,
            "error": error,
        },
    )


def _pair(value: str) -> tuple[str, str]:
    """`model_id|connection_id` from a select, or two blanks for the fallback."""
    model_id, _, connection_id = (value or "").partition("|")
    return model_id.strip()[:300], connection_id.strip()[:32]


@router.post("/{group_id}")
async def save_group(request: Request, db: Db, user: AdminUser, group_id: str) -> Response:
    """Name, description, services and -- for an instance group -- its connections.

    Connections are read from a list that is always submitted, so unticking the
    last one is a signal and not an absence. A connection taken out of a group
    goes back to the default one, never to "no group": there is no such thing.
    """
    group = _group(db, group_id)
    form = await request.form()
    name = " ".join(str(form.get("name") or "").split())[:120]
    if name:
        group.name = name
    group.description = str(form.get("description") or "").strip()[:2000]
    group.embedding_model_id, group.embedding_connection_id = _pair(
        str(form.get("embedding") or "")
    )
    group.review_model_id, group.review_connection_id = _pair(str(form.get("reviewer") or ""))

    if group.owner_id is None and "connections_sent" in form:
        wanted = {str(v) for v in form.getlist("connection_ids") if v}
        for connection in db.scalars(select(Connection)):
            current = connection.data_group_id or data_groups.DEFAULT_GROUP
            if connection.id in wanted:
                connection.data_group_id = group.id
            elif current == group.id and not group.is_default:
                connection.data_group_id = data_groups.DEFAULT_GROUP
    db.commit()
    return RedirectResponse(
        f"/admin/data-groups/{group.id}?saved=1", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{group_id}/delete")
async def delete_group(db: Db, user: AdminUser, group_id: str) -> Response:
    group = _group(db, group_id)
    try:
        data_groups.delete(db, group)
    except ValueError as exc:
        from urllib.parse import quote

        return RedirectResponse(
            f"/admin/data-groups/{group_id}?error={quote(str(exc))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        "/admin/data-groups?saved=deleted", status_code=status.HTTP_303_SEE_OTHER
    )
