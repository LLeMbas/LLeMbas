"""Which data group a connection, a chat or a speaker is in.

A data group is the unit of isolation between providers: a model reads the
memories, notes, skills, knowledge, reports, personality and impression of
exactly one group -- the one its connection resolves to -- and a chat belongs
to the group it was started in. See db/models/data_group.py for what the group
itself is.

**The resolution order lives here and nowhere else.** For a connection:

1. the person's own mapping, in `settings_json["data_groups"]`, honoured only
   while they hold `data.manage` and only to a group they may use -- so taking
   the permission away puts them back on the instance's arrangement without
   anybody having to find and clear what they set;
2. the administrator's, `Connection.data_group_id`;
3. the default group.

A mapping to a group that has since been deleted falls through to the next rung
rather than to nothing, for the same reason.

**A chat's group is stamped, not derived.** `for_chat` reads the row, and only
derives -- and stamps -- when the row predates the column. A chat whose model
has since been moved into another group therefore stays where it was, and
`refusal` is what stops that model being handed the chat's history. Deriving it
afresh every turn would instead carry the transcript into whichever group the
model happened to be in today.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    DEFAULT_GROUP,
    Chat,
    Connection,
    DataGroup,
    Memory,
    Model,
    Schedule,
    User,
)
from lembas.web.i18n import t

if TYPE_CHECKING:
    from lembas.services.chat import Speaker

log = logging.getLogger(__name__)

DEFAULT_NAME = "Default"

# Where a person's own connection-to-group choices live in `settings_json`.
SETTING_KEY = "data_groups"

# The permission that lets somebody make personal groups, move a connection
# into one for themselves, and move their own records between groups.
PERMISSION = "data.manage"

# Every table carrying `data_group_id` whose NULL means "written before groups
# existed" and therefore belongs in the default group. Chats are not on this
# list: a chat's group is derived from its model, see `for_chat`.
LIBRARY_TABLES: tuple[Any, ...] = (Memory, Schedule)

# Rows of these, counted per group, on the admin and settings pages.
COUNTED: tuple[tuple[Any, str], ...] = (
    (Chat, "chats"),
    (Memory, "memories"),
)


def group_of(row: Any) -> str:
    """The group a stored row belongs to. NULL is the default group."""
    return getattr(row, "data_group_id", None) or DEFAULT_GROUP


def condition(model: Any, group: str):
    """A WHERE clause selecting the rows of `model` in `group`.

    NULL counts as the default, so a row the startup sweep has not reached yet
    is never lost from the group it belongs to.
    """
    column = model.data_group_id
    if group == DEFAULT_GROUP:
        return or_(column.is_(None), column == DEFAULT_GROUP)
    return column == group


# --- The groups themselves -----------------------------------------------------
def ensure_default(db: DBSession) -> DataGroup:
    """The default group, created the first time anything asks for it."""
    group = db.get(DataGroup, DEFAULT_GROUP)
    if group is None:
        group = DataGroup(id=DEFAULT_GROUP, name=DEFAULT_NAME, position=0)
        db.add(group)
        db.commit()
    return group


def get(db: DBSession, group_id: str | None) -> DataGroup | None:
    if not group_id:
        return None
    if group_id == DEFAULT_GROUP:
        return ensure_default(db)
    return db.get(DataGroup, group_id)


def all_groups(db: DBSession) -> list[DataGroup]:
    """Every group on the instance, personal ones included. Administrators only."""
    ensure_default(db)
    return list(
        db.scalars(
            select(DataGroup).order_by(
                DataGroup.owner_id.is_not(None), DataGroup.position, DataGroup.name
            )
        )
    )


def instance_groups(db: DBSession) -> list[DataGroup]:
    ensure_default(db)
    return list(
        db.scalars(
            select(DataGroup)
            .where(DataGroup.owner_id.is_(None))
            .order_by(DataGroup.position, DataGroup.name)
        )
    )


def usable(db: DBSession, user: User | None) -> list[DataGroup]:
    """The groups this person's data may be in: the instance's, and their own."""
    groups = instance_groups(db)
    if user is not None:
        groups += list(
            db.scalars(
                select(DataGroup).where(DataGroup.owner_id == user.id).order_by(DataGroup.name)
            )
        )
    return groups


def may_use(db: DBSession, user: User | None, group_id: str) -> bool:
    group = get(db, group_id)
    if group is None:
        return False
    return group.owner_id is None or (user is not None and group.owner_id == user.id)


def several(db: DBSession, user: User | None) -> bool:
    """Whether there is any choice to show. One group means no chip, no select."""
    return len(usable(db, user)) > 1


def name_of(db: DBSession, group_id: str | None) -> str:
    group = get(db, group_id or DEFAULT_GROUP)
    return group.name if group is not None else (group_id or DEFAULT_NAME)


def may_manage(db: DBSession, user: User | None) -> bool:
    from lembas.security import permissions

    return user is not None and permissions.has(db, user, PERMISSION)


# --- Connections ---------------------------------------------------------------
def personal_map(user: User | None) -> dict[str, str]:
    """The person's own connection -> group choices, as stored."""
    if user is None:
        return {}
    stored = (user.settings_json or {}).get(SETTING_KEY) or {}
    if not isinstance(stored, dict):
        return {}
    return {str(k): str(v) for k, v in stored.items() if k and v}


def connection_groups(db: DBSession, user: User | None) -> dict[str, str]:
    """Every connection's group for this person, resolved once.

    One query for the lot, because the model lists call this for every model
    they show and a query per model would be one per row of every picker.
    """
    ensure_default(db)
    known = {group.id for group in db.scalars(select(DataGroup))}
    resolved: dict[str, str] = {}
    for connection_id, group_id in db.execute(select(Connection.id, Connection.data_group_id)):
        resolved[connection_id] = group_id if group_id in known else DEFAULT_GROUP

    if user is not None and may_manage(db, user):
        for connection_id, group_id in personal_map(user).items():
            if connection_id in resolved and group_id in known and may_use(db, user, group_id):
                resolved[connection_id] = group_id
    return resolved


def for_connection(db: DBSession, user: User | None, connection_id: str | None) -> str:
    if not connection_id:
        return DEFAULT_GROUP
    return connection_groups(db, user).get(connection_id, DEFAULT_GROUP)


def _connection_for(db: DBSession, model_id: str, connection_id: str | None) -> str | None:
    """The connection a (model id, connection) pair actually lands on.

    The connection when one is named and still enabled; otherwise the first
    enabled connection offering that id, which is exactly the one
    `chat.resolve_endpoint` would fall back to.
    """
    if connection_id:
        connection = db.get(Connection, connection_id)
        if connection is not None and connection.enabled:
            return connection.id
    return db.scalar(
        select(Model.connection_id)
        .join(Connection)
        .where(
            Model.model_id == model_id,
            Model.enabled.is_(True),
            Connection.enabled.is_(True),
        )
        .order_by(Connection.position)
    )


def for_pair(
    db: DBSession, user: User | None, model_id: str, connection_id: str | None = None
) -> str:
    """The group of a model named by its text id and, if known, its connection."""
    return for_connection(db, user, _connection_for(db, model_id, connection_id))


# --- Chats and speakers --------------------------------------------------------
def for_chat(db: DBSession, chat: Chat | None) -> str:
    """The group a chat belongs to.

    Read from the row. A row with none -- written before groups, or by a path
    that creates a chat without going through `_new_chat` -- is given the group
    of its model now, and keeps it.
    """
    if chat is None:
        return DEFAULT_GROUP
    if chat.data_group_id:
        return chat.data_group_id
    owner = db.get(User, chat.user_id) if chat.user_id else None
    group = DEFAULT_GROUP
    if chat.model_id:
        group = for_pair(db, owner, chat.model_id, chat.connection_id)
    chat.data_group_id = group
    return group


def for_speaker(
    db: DBSession, user: User | None, chat: Chat | None, speaker: Speaker | None
) -> str:
    """The group whose data this speaker is handed.

    The chat's own group for the chat's own model. For anybody else -- a crowd
    member, a schedule's model -- the group of *their* connection: a model reads
    its own group's stores and never the chat's, which is what keeps a crowd
    member from another provider out of this group's memories even when a rule
    has let it into the conversation.
    """
    if chat is not None and (speaker is None or speaker.model_id == chat.model_id):
        return for_chat(db, chat)
    if speaker is None or not speaker.model_id:
        return DEFAULT_GROUP
    return for_pair(db, user, speaker.model_id, speaker.connection_id)


def for_composer(db: DBSession, user: User | None, chat_id: str, model_id: str = "") -> str:
    """The group a composer is writing into: its chat's, or its chosen model's.

    What the `@` menu and the library picker filter on. A copy made from the
    library becomes part of the conversation and is sent to the chat's model,
    so offering another group's note there would be the boundary crossed by
    hand. On the new-chat screen there is no chat yet, and the model the
    composer has chosen decides -- it is the one the chat will be pinned to.
    """
    chat = db.get(Chat, chat_id) if chat_id else None
    if chat is not None and user is not None and chat.user_id == user.id:
        return for_chat(db, chat)
    if model_id:
        return for_pair(db, user, model_id)
    from lembas.services import chat as chat_service

    chosen = chat_service.default_model(db, user)
    return for_pair(db, user, *chosen) if chosen else DEFAULT_GROUP


def refusal(db: DBSession, user: User | None, chat: Chat, speaker: Speaker) -> str:
    """Why this speaker may not be sent this chat, or "" when it may.

    Only the chat's own model is checked here: a crowd member reads its own
    group's stores whatever the chat's group is, and whether it may join the
    conversation at all is decided where the crowd is assembled.

    Refused when the model's connection is now in a different group from the
    chat -- an administrator moved it, or the person remapped it. Sending the
    reply anyway would hand that provider the chat's whole history.
    """
    if speaker.model_id != chat.model_id:
        return ""
    chat_group = for_chat(db, chat)
    model_group = for_pair(db, user, speaker.model_id, speaker.connection_id)
    if model_group == chat_group:
        return ""
    return t(
        "This chat belongs to the data group %(chat_group)s, and its "
        "model is now in %(model_group)s, so it cannot be sent this "
        "chat's history. Pick a model in %(chat_group)s, or start a "
        "new chat.",
        chat_group=repr(name_of(db, chat_group)),
        model_group=repr(name_of(db, model_group)),
    )


# --- Housekeeping ----------------------------------------------------------------
def sweep_unassigned(db: DBSession) -> int:
    """Put every row written before data groups existed into the group it belongs to.

    Library rows go to the default group: before groups existed every
    connection was in it, so that is where every one of them was read. Chats
    get their model's group, which on an upgrade is the default too, and on a
    later run is the right answer for a chat some path created without
    stamping one. Runs at startup beside `documents.sweep_unfiled`.
    """
    ensure_default(db)
    moved = 0
    for model in LIBRARY_TABLES:
        result = db.execute(
            update(model).where(model.data_group_id.is_(None)).values(data_group_id=DEFAULT_GROUP)
        )
        moved += result.rowcount or 0
    for chat in db.scalars(select(Chat).where(Chat.data_group_id.is_(None))):
        for_chat(db, chat)
        moved += 1
    db.commit()
    if moved:
        log.info("data groups: %d rows assigned", moved)
    return moved


def counts(db: DBSession, group_id: str, *, owner: User | None = None) -> dict[str, int]:
    """How many of each kind of record are in a group, for one person or all."""
    found: dict[str, int] = {}
    for model, label in COUNTED:
        query = select(func.count()).select_from(model).where(condition(model, group_id))
        if owner is not None:
            column = model.user_id if model is Chat else model.owner_id
            query = query.where(column == owner.id)
        found[label] = db.scalar(query) or 0
    return found


def in_use(db: DBSession, group_id: str) -> dict[str, int]:
    """What still points at a group, which is what stops it being deleted."""
    found = counts(db, group_id)
    found["connections"] = (
        db.scalar(
            select(func.count())
            .select_from(Connection)
            .where(Connection.data_group_id == group_id)
        )
        or 0
    )
    return {label: n for label, n in found.items() if n}


def delete(db: DBSession, group: DataGroup) -> None:
    """Remove a group that nothing is in. Raises ValueError otherwise.

    Refused rather than cascaded. Deleting a group's records along with it is
    far too large a thing to do from one button, and moving them into another
    group silently would hand them to that group's providers.
    """
    if group.is_default:
        raise ValueError(t("The default group cannot be deleted."))
    busy = in_use(db, group.id)
    if busy:
        # The label words are the keys `api/admin_data_groups.labels` spells out.
        listed = ", ".join(t("%(n)s %(label)s", n=n, label=t(label)) for label, n in busy.items())
        raise ValueError(t("The group still holds %(listed)s. Move them out first.", listed=listed))
    # Nobody may go on naming a group that is gone; their mapping falls through.
    for user in db.scalars(select(User)):
        mapped = personal_map(user)
        if group.id in mapped.values():
            kept = {k: v for k, v in mapped.items() if v != group.id}
            user.settings_json = {**(user.settings_json or {}), SETTING_KEY: kept}
    db.delete(group)
    db.commit()


__all__ = [
    "DEFAULT_GROUP",
    "all_groups",
    "condition",
    "connection_groups",
    "counts",
    "delete",
    "ensure_default",
    "for_chat",
    "for_composer",
    "for_connection",
    "for_pair",
    "for_speaker",
    "get",
    "group_of",
    "in_use",
    "instance_groups",
    "may_manage",
    "may_use",
    "name_of",
    "personal_map",
    "refusal",
    "several",
    "sweep_unassigned",
    "usable",
]
