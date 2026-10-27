"""The snapshot a running tool needs, taken while the session is open."""

from __future__ import annotations

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    DEFAULT_GROUP,
    Chat,
    User,
)
from lembas.services import settings_store
from lembas.services.library import documents as documents_service
from lembas.services.tools.core import ToolContext, ToolSet
from lembas.services.tools.resolve import has_display, scoped_skills_off, unattended


def context_for(
    db: DBSession,
    user: User | None,
    chat: Chat | None = None,
    *,
    tools: ToolSet | None = None,
    speaker=None,
) -> ToolContext:
    """The snapshot a running tool needs, taken while the session is open.

    `speaker` is the model answering, and it decides which model a tool acts *as*:
    whose endpoint the image
    reviewer and the Preserve-VRAM unload reach for. It defaults to the chat's own
    model.
    """
    from lembas.services import chat as chat_service
    from lembas.services import data_groups
    if chat is not None and speaker is None:
        speaker = chat_service.speaker_for(db, chat)

    return ToolContext(
        agent=None,
        owner_id=user.id if user else "",
        chat_id=chat.id if chat is not None else "",
        search_config=settings_store.search(db),
        image_config=settings_store.images(db),
        image_workflow_id=(chat.image_workflow_id or "") if chat is not None else "",
        image_checkpoint=(chat.image_checkpoint or "") if chat is not None else "",
        model_id=(speaker.model_id or "") if speaker is not None else "",
        connection_id=(speaker.connection_id or "") if speaker is not None else "",
        data_group=(
            data_groups.for_speaker(db, user, chat, speaker)
            if chat is not None
            else DEFAULT_GROUP
        ),
        base_ids=[base.id for base in chat.knowledge_bases] if chat is not None else [],
        bases=_searchable_bases(db, user, chat, speaker),
        skills_off=scoped_skills_off(chat),
        tools=tools.by_name if tools is not None else None,
        interaction_timeout=float(settings_store.agents(db)["approval_timeout"]),
        unattended=unattended(chat),
        display=has_display(chat),
    )


def _searchable_bases(db: DBSession, user: User | None, chat: Chat | None, speaker) -> list[dict]:
    """The bases a `knowledge_search` in this chat may name: the attached ones,
    else every base the owner may see in the answering model's data group."""
    from lembas.services import data_groups

    if chat is None or user is None:
        return []
    group = data_groups.for_speaker(db, user, chat, speaker)
    attached = [
        base
        for base in chat.knowledge_bases
        if (base.data_group_id or DEFAULT_GROUP) == group
    ]
    rows = attached or list(db.scalars(documents_service.visible_bases(db, user, group)))
    return [{"id": base.id, "name": base.name} for base in rows]
