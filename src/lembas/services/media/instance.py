"""The instance's own equipment as connections: a ComfyUI, a speech server.

Until these were connection types, the instance's ComfyUI and its two speech
endpoints were addresses in the settings (`images.base_url`,
`audio.stt_base_url`, `audio.tts_base_url`) and everything that draws or
speaks read those groups. Three things live here:

- **Which model is the default** (`default_value`): `<connection id>/<model
  id>`, for any capability. For pictures it is one of two keys -- a
  provider's model (`images.connection`, as before) or a ComfyUI's checkpoint
  (`images.comfyui`; the checkpoint may be empty: "the chat's, or its first").
- **The view** (`audio_view`, `images_view`): the settings group as every
  reader has always read it, with the address, key, models and options of the
  equipment the default names laid over it. `settings_store.audio()` and
  `settings_store.images()` end in these, so the ComfyUI client, the tool,
  the harness, `/v1` and the pages need not know the address moved.
- **The carry** (`carry`): what an older release kept as addresses becomes
  connections, once per address, and the defaults point at them. Additive --
  the old keys stay as they are, for an older release run on this database
  and for a backup restored into this one -- and a second run finds its own
  marks and changes nothing.

A group nobody carried yet (set by an import, or by a test) is still read
where it is: `sources.default` falls back to the address in the settings when
no default names a connection.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Connection, ImageWorkflow
from lembas.services.llm import providers
from lembas.services.media import store
from lembas.services.media.store import CAP_IMAGE, CAP_STT, CAP_TTS

log = logging.getLogger(__name__)

# The stored default that means "none": nothing serves the capability by
# default, whatever an older release left in the address fields.
NONE = "none"

# Where each capability's default is kept: (settings group, key).
AUDIO_KEYS = {CAP_STT: "stt_connection", CAP_TTS: "tts_connection"}
IMAGE_KEY = "connection"  # a provider's image model
COMFY_KEY = "comfyui"  # a ComfyUI and one of its checkpoints

# What the carry calls the connections it makes. Stored names, like any an
# administrator types: not translated, and theirs to change.
NAME_COMFYUI = "ComfyUI"
NAME_SPEECH = "Speech"
NAME_STT = "Speech to text"
NAME_TTS = "Text to speech"


def default_key(capability: str) -> str:
    """Where the default of a generation beyond a picture is kept, in the
    image settings: `<connection id>/<model id>` for a provider's model,
    `<connection id>/<workflow slug>` for a ComfyUI's workflow of that kind."""
    return f"{capability}_default"


def enabled_key(capability: str) -> str:
    return "enabled" if capability == CAP_IMAGE else f"{capability}_enabled"


def comfy_values(connection: Connection) -> dict[str, Any]:
    """What the ComfyUI client needs of a ComfyUI connection, in the keys
    `images.tool.config_of` reads: for a workflow run on a ComfyUI that is
    not (or not only) the default for pictures."""
    options = store.type_options(connection)
    return {
        "comfyui_connection_id": connection.id,
        "comfyui_name": connection.name,
        "base_url": connection.base_url,
        "api_key_encrypted": connection.api_key_encrypted,
        "timeout": options["timeout"],
        "preserve_vram": options["preserve_vram"],
    }


def split(value: Any) -> tuple[str, str]:
    """`<connection id>/<model id>` as its halves. The model may be empty (a
    ComfyUI with no fixed checkpoint); no connection is two empty strings."""
    connection_id, _, model = str(value or "").partition("/")
    connection_id = connection_id.strip()
    return (connection_id, model) if connection_id and connection_id != NONE else ("", "")


def default_value(values: dict[str, Any], capability: str) -> str:
    """The stored default for a capability: `<connection id>/<model id>`,
    `none`, or "" -- nobody chose, and an address in the settings (if any) is
    what an older release meant."""
    if capability in store.NAMED:
        return str(values.get(default_key(capability)) or "").strip()
    if capability != CAP_IMAGE:
        return str(values.get(AUDIO_KEYS[capability]) or "").strip()
    chosen = str(values.get(IMAGE_KEY) or "").strip()
    return chosen or str(values.get(COMFY_KEY) or "").strip()


def default_changes(capability: str, value: str, connection: Connection | None) -> dict[str, str]:
    """The settings to write for a chosen default: `value` is `<connection
    id>/<model id>`, `none` or "". Pictures keep a ComfyUI under its own key,
    so that the other key reads as it always has."""
    if capability in store.NAMED:
        return {default_key(capability): value}
    if capability != CAP_IMAGE:
        return {AUDIO_KEYS[capability]: value}
    if connection is not None and _is(connection, providers.COMFYUI):
        return {IMAGE_KEY: "", COMFY_KEY: value}
    return {IMAGE_KEY: value, COMFY_KEY: ""}


def _is(connection: Any, type_id: str) -> bool:
    return providers.get(getattr(connection, "provider_type", "")).id == type_id


def equipment_default(
    db: DBSession, values: dict[str, Any], capability: str
) -> tuple[Connection | None, str] | None:
    """The equipment connection the default names and the model beside it --
    `(None, model)` when it names one that has gone -- or None when the
    default is not equipment at all (a provider's model, none, or unset)."""
    connection_id, model = split(default_value(values, capability))
    if not connection_id:
        return None
    if capability == CAP_IMAGE and str(values.get(IMAGE_KEY) or "").strip():
        return None  # a provider's image model
    connection = db.get(Connection, connection_id)
    if connection is None:
        # A ComfyUI's key can only have named a ComfyUI; for speech the row
        # is needed to know, and it has gone.
        return (None, model) if capability == CAP_IMAGE else None
    if not store.equipment(connection.provider_type):
        return None
    return connection, model


# --- The view --------------------------------------------------------------------
def audio_view(db: DBSession, values: dict[str, Any]) -> dict[str, Any]:
    """The audio settings with the default speech server's address, key,
    model and that model's options in the places they were always read."""
    for side in (CAP_STT, CAP_TTS):
        found = equipment_default(db, values, side)
        if found is None:
            continue
        connection, model = found
        entry = store.get_entry(connection, side, model) if connection is not None else None
        usable = bool(
            connection is not None and connection.enabled and entry is not None and entry.enabled
        )
        # The server this side is read from; "" while what was chosen cannot serve.
        values[f"{side}_server"] = connection.id if connection is not None else ""
        values[f"{side}_base_url"] = connection.base_url if usable else ""
        values[f"{side}_api_key_encrypted"] = connection.api_key_encrypted if usable else ""
        if entry is None:
            continue
        values[f"{side}_model"] = entry.model
        values["stt_per_minute" if side == CAP_STT else "tts_per_1m_chars"] = entry.price
        options = entry.options
        if side == CAP_STT:
            values["stt_language"] = str(options.get("language") or "")
        else:
            values["tts_voice"] = entry.voice
            values["tts_voices"] = list(options.get("voices") or [])
            if "format" in options:
                values["tts_format"] = options["format"]
            if "speed" in options:
                values["tts_speed"] = options["speed"]
    return values


def images_view(db: DBSession, values: dict[str, Any]) -> dict[str, Any]:
    """The image settings with the default ComfyUI's address, key, options
    and checkpoints in the places the ComfyUI code reads them."""
    found = equipment_default(db, values, CAP_IMAGE)
    if found is None:
        return values
    connection, model = found
    if connection is None or not connection.enabled:
        # What was chosen has gone or is off: nothing is drawn by default, and
        # the address an older release kept is not quietly used instead.
        values.update({"base_url": "", "api_key_encrypted": "", "checkpoints": [],
                       "comfyui_connection_id": connection.id if connection else ""})
        return values
    options = store.type_options(connection)
    models = store.entries(connection, CAP_IMAGE, enabled=True)
    names = [entry.model for entry in models]
    values.update(
        {
            "comfyui_connection_id": connection.id,
            "comfyui_name": connection.name,
            "base_url": connection.base_url,
            "api_key_encrypted": connection.api_key_encrypted,
            "timeout": options["timeout"],
            "preserve_vram": options["preserve_vram"],
            "samplers": options["samplers"],
            "schedulers": options["schedulers"],
            "default_workflow_id": options["default_workflow_id"],
            "checkpoints": names,
            "default_checkpoint": model if model in names else "",
            # Per checkpoint: what it is drawn with when nothing more specific
            # says (`checkpoint_settings`), and what one picture costs.
            "model_options": {e.model: dict(e.options) for e in models if e.options},
            "model_prices": {e.model: e.price for e in models if e.price},
        }
    )
    return values


def checkpoint_settings(values: dict[str, Any], checkpoint: str) -> dict[str, Any]:
    """The image settings as a generation with this checkpoint reads them:
    the checkpoint's own steps, cfg, sampler, scheduler, negative and size
    over the instance's `default_*`. What the model asks for still wins
    (`workflow.resolve`)."""
    options = (values.get("model_options") or {}).get(checkpoint) or {}
    if not options:
        return values
    out = dict(values)
    for name in ("steps", "cfg", "sampler", "scheduler", "negative"):
        if name in options:
            out[f"default_{name}"] = options[name]
    size = store.size_of((options.get("sizes") or [""])[0])
    if size is not None:
        out["default_width"], out["default_height"] = size
    return out


def price_per_image(values: dict[str, Any], checkpoint: str) -> float:
    """What one picture from this checkpoint costs: its own price where it
    has one, else the instance's."""
    own = (values.get("model_prices") or {}).get(checkpoint)
    return float(own if own else values.get("per_image") or 0)


def workflows(
    db: DBSession, values: dict[str, Any], *, limit: int | None = None, kind: str = CAP_IMAGE
) -> list[Any]:
    """The enabled workflows **of one kind** the ComfyUI in `values` may run:
    its own and the ones that belong to no connection in particular. Before a
    ComfyUI was a connection (`comfyui_connection_id` absent), all of them. A
    row from before workflows had a kind is a picture's."""
    query = select(ImageWorkflow).where(ImageWorkflow.enabled.is_(True))
    if kind == CAP_IMAGE:
        query = query.where(
            ImageWorkflow.kind.is_(None) | (ImageWorkflow.kind == "") | (ImageWorkflow.kind == kind)
        )
    else:
        query = query.where(ImageWorkflow.kind == kind)
    connection_id = str(values.get("comfyui_connection_id") or "")
    if connection_id:
        query = query.where(
            (ImageWorkflow.connection_id == connection_id)
            | ImageWorkflow.connection_id.is_(None)
            | (ImageWorkflow.connection_id == "")
        )
    query = query.order_by(ImageWorkflow.position, ImageWorkflow.slug)
    if limit:
        query = query.limit(limit)
    return list(db.scalars(query))


# --- The carry -------------------------------------------------------------------
def _position(db: DBSession) -> int:
    return int(db.scalar(select(func.coalesce(func.max(Connection.position), -1))) or 0) + 1


def _same_key(first: str, second: str) -> bool:
    """Whether two stored keys are one key.

    The same ciphertext is the same key, and is not opened to find that out.
    Fernet ciphertext differs on every encryption, so two different ones are
    compared opened -- and **one that cannot be opened is the same as
    nothing but itself**: `decrypt` answers "" for it (a database restored
    under another secret), which would otherwise make every unreadable key
    "the same key" as every other and as no key at all."""
    from lembas.services.crypto import decrypt

    first, second = first or "", second or ""
    if first == second:
        return True
    if not first or not second:
        return False
    opened = decrypt(first)
    return bool(opened) and opened == decrypt(second)


def _find(db: DBSession, type_id: str, base_url: str, key_encrypted: str) -> Connection | None:
    for connection in db.scalars(
        select(Connection)
        .where(Connection.provider_type == type_id, Connection.base_url == base_url)
        .order_by(Connection.position)
    ):
        if _same_key(connection.api_key_encrypted, key_encrypted):
            return connection
    return None


def _make(db: DBSession, type_id: str, name: str, base_url: str, key_encrypted: str) -> Connection:
    from lembas.services import settings_store

    taken = set(db.scalars(select(Connection.name)))
    chosen, number = name, 2
    while chosen in taken:
        chosen, number = f"{name} {number}", number + 1
    connection = Connection(
        name=chosen,
        base_url=base_url,
        # The ciphertext as it stands: the same secret key opens it here.
        api_key_encrypted=key_encrypted or "",
        provider_type=type_id,
        currency=settings_store.currency(db),
        position=_position(db),
        enabled=True,
    )
    db.add(connection)
    db.flush()
    return connection


def carry(db: DBSession) -> list[str]:
    """Make connections of the addresses an older release kept in the
    settings, and point the defaults at them. Returns what it did, in words;
    an empty list on every run after the first.

    Each address is carried once, remembered by a mark beside it
    (`stt_carried`, `tts_carried`, `images.carried`): the address and model
    it was. A different one arriving later -- an administrator's export
    imported, a backup restored -- is carried in its turn; the same one never
    twice, so a connection renamed, edited or removed afterwards stays as
    its administrator left it.
    """
    done: list[str] = []
    parts = (
        ("speech-to-text", lambda: _carry_speech(db, CAP_STT)),
        ("text-to-speech", lambda: _carry_speech(db, CAP_TTS)),
        ("ComfyUI", lambda: _carry_comfyui(db)),
    )
    # Each address on its own: one that cannot be carried (a value an older
    # release wrote in a shape this one does not expect) is said and left for
    # the next start, and does not take the other two with it.
    for what, part in parts:
        try:
            lines = part()
            db.commit()
        except Exception:
            db.rollback()
            log.exception(
                "media: the %s address in the settings could not be made a connection; "
                "it stays where it was and is tried again at the next start", what,
            )
            continue
        done.extend(lines)
    for line in done:
        log.info("media: %s", line)
    return done


def _carry_speech(db: DBSession, side: str) -> list[str]:
    from lembas.services import settings_store

    done: list[str] = []
    audio = settings_store.get_group(db, settings_store.AUDIO)
    address = str(audio.get(f"{side}_base_url") or "").strip().rstrip("/")
    if not address:
        return done
    model = str(audio.get(f"{side}_model") or "").strip() or (
        "whisper-1" if side == CAP_STT else "tts-1"
    )
    mark = f"{address}|{model}"
    if str(audio.get(f"{side}_carried") or "") == mark:
        return done
    # The key goes over as the ciphertext it is, never opened here.
    key = str(audio.get(f"{side}_api_key_encrypted") or "")
    connection = _find(db, providers.SPEECH, address, key)
    if connection is None:
        connection = _make(
            db, providers.SPEECH, NAME_STT if side == CAP_STT else NAME_TTS, address, key
        )
        done.append(f"speech server {address} is the connection {connection.name}")
    elif connection.name in (NAME_STT, NAME_TTS):
        # One server doing both directions: named for both.
        taken = set(db.scalars(select(Connection.name)))
        if NAME_SPEECH not in taken:
            connection.name = NAME_SPEECH
    if store.get_entry(connection, side, model) is None:
        options: dict[str, Any] = {}
        if side == CAP_STT:
            options["language"] = audio.get("stt_language")
        else:
            options["format"] = audio.get("tts_format")
            options["speed"] = audio.get("tts_speed")
        connection.media_json = store.with_entry(
            connection,
            store.Entry(
                capability=side,
                model=model,
                enabled=True,
                price=settings_store.money_amount(
                    audio.get("stt_per_minute" if side == CAP_STT else "tts_per_1m_chars")
                ),
                voice=str(audio.get("tts_voice") or "") if side == CAP_TTS else "",
                options=store.clean_options(side, options),
            ),
        )
        done.append(f"{side} model {model} is a model of {connection.name}")
    changes: dict[str, Any] = {f"{side}_carried": mark}
    # The default keeps pointing where it did: at this server, unless an
    # administrator had already chosen a connection's model instead.
    if not str(audio.get(AUDIO_KEYS[side]) or "").strip():
        changes[AUDIO_KEYS[side]] = f"{connection.id}/{model}"
    settings_store.update(db, changes, key=settings_store.AUDIO)
    return done


def _carry_comfyui(db: DBSession) -> list[str]:
    from lembas.services import settings_store

    done: list[str] = []
    images = settings_store.get_group(db, settings_store.IMAGES)
    address = str(images.get("base_url") or "").strip().rstrip("/")
    if not address or str(images.get("carried") or "") == address:
        return done
    key = str(images.get("api_key_encrypted") or "")
    connection = _find(db, providers.COMFYUI, address, key)
    if connection is None:
        connection = _make(db, providers.COMFYUI, NAME_COMFYUI, address, key)
        connection.options_json = {
            "timeout": images.get("timeout"),
            "preserve_vram": bool(images.get("preserve_vram")),
            "default_workflow_id": str(images.get("default_workflow_id") or ""),
            "samplers": [str(v) for v in images.get("samplers") or []],
            "schedulers": [str(v) for v in images.get("schedulers") or []],
        }
        checkpoints = [str(v) for v in images.get("checkpoints") or [] if str(v).strip()]
        connection.media_json = store.normalise(
            {"models": [
                {"capability": CAP_IMAGE, "model": name, "enabled": True}
                for name in checkpoints
            ]}
        )
        # The workflows there are were written for this ComfyUI.
        attached = db.execute(
            update(ImageWorkflow)
            .where(ImageWorkflow.connection_id.is_(None))
            .values(connection_id=connection.id)
        ).rowcount
        done.append(
            f"ComfyUI {address} is the connection {connection.name} "
            f"({len(checkpoints)} checkpoint(s), {attached} workflow(s))"
        )
    image_changes: dict[str, Any] = {"carried": address}
    if not default_value(images, CAP_IMAGE):
        image_changes[COMFY_KEY] = (
            f"{connection.id}/{str(images.get('default_checkpoint') or '').strip()}"
        )
    settings_store.update(db, image_changes, key=settings_store.IMAGES)
    return done


__all__ = [
    "AUDIO_KEYS",
    "COMFY_KEY",
    "IMAGE_KEY",
    "NONE",
    "audio_view",
    "carry",
    "checkpoint_settings",
    "default_changes",
    "default_value",
    "equipment_default",
    "images_view",
    "price_per_image",
    "split",
    "workflows",
]
