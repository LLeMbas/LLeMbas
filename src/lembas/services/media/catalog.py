"""One list of models, each with a capability, whichever table keeps it.

A chat or embedding model is a row of `models`; a speech, picture (and later
video, music, 3D) model is an entry of its connection's `media_json`
(`store.py`). They stay where they are -- see the note there on why a media
model is not a row -- and this is the one place that lists both, for Admin ›
Models, a connection's own Models tab, and the marks a connection shows for
what it offers.

A media model has no id of its own, so its page is addressed by its
connection and a **key** made from its capability and id (`media_key`): the
same every time, and nothing a model id's own characters can break.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Connection, Model
from lembas.services.media import instance, store
from lembas.web.i18n import N_

# What each capability is called, in the order lists show them. Marked for the
# catalogues here and translated where shown (`t(label)`).
LABELS: dict[str, str] = {
    store.CAP_CHAT: N_("Chat"),
    store.CAP_EMBEDDING: N_("Embedding"),
    store.CAP_STT: N_("Speech to text"),
    store.CAP_TTS: N_("Text to speech"),
    store.CAP_IMAGE: N_("Pictures"),
    store.CAP_IMAGE_EDIT: N_("Picture editing"),
    store.CAP_VIDEO: N_("Video"),
    store.CAP_MUSIC: N_("Music"),
    store.CAP_MODEL3D: N_("3D models"),
}
# The icon of each, from the set `_macros.html` draws.
ICONS: dict[str, str] = {
    store.CAP_CHAT: "chat",
    store.CAP_EMBEDDING: "search",
    store.CAP_STT: "mic",
    store.CAP_TTS: "speaker",
    store.CAP_IMAGE: "image",
    store.CAP_IMAGE_EDIT: "image",
    store.CAP_VIDEO: "image",
    store.CAP_MUSIC: "speaker",
    store.CAP_MODEL3D: "image",
}
# What a media model's price is per.
UNITS: dict[str, str] = {
    store.CAP_STT: N_("per minute"),
    store.CAP_TTS: N_("per 1M characters"),
    store.CAP_IMAGE: N_("per picture"),
}


def media_key(capability: str, model: str) -> str:
    """The address of a media model within its connection."""
    return hashlib.sha256(f"{capability}\0{model}".encode()).hexdigest()[:16]


def capability_of(model: Model) -> str:
    """A model row's capability: embedding, or chat (also for a row from
    before rows said)."""
    return store.CAP_EMBEDDING if model.kind == store.CAP_EMBEDDING else store.CAP_CHAT


@dataclass(frozen=True)
class Item:
    """One line of the list."""

    capability: str
    label: str
    model_id: str
    connection: Connection
    enabled: bool
    href: str
    # Exactly one of the two: the row, or the connection's entry.
    model: Model | None = None
    entry: store.Entry | None = None
    # This is the instance's default for its capability (a media model), or
    # the model new chats start on.
    default: bool = False

    @property
    def media(self) -> bool:
        return self.entry is not None

    @property
    def key(self) -> str:
        return media_key(self.capability, self.model_id) if self.media else ""

    @property
    def initial(self) -> str:
        return (self.label.strip() or "?")[0].upper()


def media_href(connection_id: str, capability: str, model: str) -> str:
    return f"/admin/models/media/{connection_id}/{media_key(capability, model)}"


def find_media(connection: Connection, key: str) -> store.Entry | None:
    """The connection's media model with that key, enabled or not."""
    return next(
        (e for e in store.entries(connection) if media_key(e.capability, e.model) == key), None
    )


def _defaults(db: DBSession) -> set[tuple[str, str, str]]:
    """`(capability, connection id, model id)` of each chosen media default."""
    from lembas.services import settings_store

    out: set[tuple[str, str, str]] = set()
    for capability in store.CAPABILITIES:
        values = (
            settings_store.images(db)
            if capability == store.CAP_IMAGE
            else settings_store.audio(db)
        )
        connection_id, model = instance.split(instance.default_value(values, capability))
        if connection_id:
            out.add((capability, connection_id, model))
    return out


def items(
    db: DBSession,
    *,
    capability: str = "",
    connection_id: str = "",
    q: str = "",
    state: str = "",
) -> list[Item]:
    """Every model, filtered: the rows in the pickers' order, then each
    connection's media in the connections' order.

    `state`: `on`, `off`, or anything else for both."""
    from lembas.services import settings_store

    needle = q.strip().lower()
    start = str(settings_store.get(db, "default_model") or "")
    chosen = _defaults(db)
    out: list[Item] = []

    def keep(item: Item) -> bool:
        return (
            (not capability or item.capability == capability)
            and (not connection_id or item.connection.id == connection_id)
            and (state != "on" or item.enabled)
            and (state != "off" or not item.enabled)
            and (
                not needle
                or needle in item.model_id.lower()
                or needle in item.label.lower()
            )
        )

    for model in db.scalars(select(Model).order_by(Model.position, Model.model_id)):
        item = Item(
            capability=capability_of(model),
            label=model.label,
            model_id=model.model_id,
            connection=model.connection,
            enabled=bool(model.enabled),
            href=f"/admin/models/{model.id}",
            model=model,
            default=bool(start) and model.model_id == start,
        )
        if keep(item):
            out.append(item)
    for connection in db.scalars(
        select(Connection).order_by(Connection.position, Connection.name)
    ):
        offered = store.capabilities_of(connection.provider_type, connection.base_url)
        for entry in store.entries(connection):
            if entry.capability not in offered and entry.capability not in store.NAMED:
                continue
            item = Item(
                capability=entry.capability,
                label=entry.label,
                model_id=entry.model,
                connection=connection,
                enabled=bool(entry.enabled),
                href=media_href(connection.id, entry.capability, entry.model),
                entry=entry,
                default=(entry.capability, connection.id, entry.model) in chosen,
            )
            if keep(item):
                out.append(item)
    return out


def offers(connection: Connection) -> list[str]:
    """The capabilities a connection has an enabled model for, in the lists'
    order: the small marks beside its name."""
    have: set[str] = {capability_of(m) for m in connection.models if m.enabled}
    have |= {e.capability for e in store.entries(connection, enabled=True)}
    return [capability for capability in LABELS if capability in have]


def counts(found: list[Item]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in found:
        out[item.capability] = out.get(item.capability, 0) + 1
    return out


def context() -> dict[str, Any]:
    """What a template needs to draw a capability."""
    return {"capability_labels": LABELS, "capability_icons": ICONS, "capability_units": UNITS}


__all__ = [
    "ICONS",
    "LABELS",
    "UNITS",
    "Item",
    "capability_of",
    "context",
    "counts",
    "find_media",
    "items",
    "media_href",
    "media_key",
    "offers",
]
