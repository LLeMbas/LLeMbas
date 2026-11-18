"""What a connection offers besides chat: `Connection.media_json`.

```
{"prefer_audio": bool, "prefer_image": bool,
 "models": [{"capability": "stt" | "tts" | "image" | ..., "model": "<id>",
             "enabled": bool, "price": <money>, "voice": "<tts only>",
             "name": "<shown>", "description": "<shown>",
             "options": {<what this model is asked with, per capability>}}]}
```

**A capability is a value, not a column.** `stt`, `tts` and `image` were the
first (`CAPABILITIES`); `image_edit`, `video`, `music` and `model3d` (`NAMED`)
are the generations beyond a picture, served by `services/media/generate.py`
and, where they take long, as jobs (`services/media/jobs.py`). What serves
each is said by `dialect`: a provider's API where this server speaks it, a
ComfyUI's workflow of that kind for all four. A new one is a string here and a
function in `services/media/`, never a schema change.

**Every connection type keeps its media here**, the two that serve nothing
else included: a ComfyUI's models are its checkpoints (capability `image`), a
speech server's are the model ids it transcribes and reads with. Those two are
the instance's own equipment (`equipment`): no chat is ever "on" them, so
their models are reached only as the instance's default.

A JSON document on the connection and not model rows, on purpose: a media
model is typed by an administrator rather than discovered (a refresh deletes
the rows an endpoint no longer lists), can never be chosen for a chat (every
listing of `models` would have to learn to leave it out), and is priced in a
unit of its own. One column, added to rows that exist as an empty dict.

A price is money in the **connection's** currency, in the unit of its
capability: per minute transcribed, per million characters read aloud, per
picture -- the units the instance's own defaults are priced in.

Everything here is pure: it reads a row (or a dict) and returns plain values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lembas.services.llm import providers

CAP_STT = "stt"
CAP_TTS = "tts"
CAP_IMAGE = "image"
CAPABILITIES = (CAP_STT, CAP_TTS, CAP_IMAGE)
# The generations beyond a picture.
CAP_IMAGE_EDIT = "image_edit"
CAP_VIDEO = "video"
CAP_MUSIC = "music"
CAP_MODEL3D = "model3d"
NAMED = (CAP_IMAGE_EDIT, CAP_VIDEO, CAP_MUSIC, CAP_MODEL3D)
ALL = CAPABILITIES + NAMED
# What a model row is (`Model.kind`), in the same vocabulary: the one list of
# models says a capability for every line, whichever table it comes from.
CAP_CHAT = "chat"
CAP_EMBEDDING = "embedding"
EVERY = (CAP_CHAT, CAP_EMBEDDING, *ALL)
# Everything that makes a thing rather than hears or reads: one switch on a
# connection ("use its own generation") covers them all.
GENERATIONS = (CAP_IMAGE, *NAMED)
# What takes minutes and is therefore a job, not an answer held open.
LONG = (CAP_VIDEO, CAP_MUSIC, CAP_MODEL3D)

# The audio formats a speech model may be asked for (`services/audio.FORMATS`).
AUDIO_FORMATS = ("mp3", "wav", "flac", "aac")
NAME_MAX = 120
DESCRIPTION_MAX = 500
MAX_VOICES = 200
MAX_SIZES = 8

# The most media models one connection keeps. A ComfyUI's checkpoints are a
# folder somebody filled, so the ceiling is a folder's, not a form's.
MAX_MODELS = 200
MODEL_MAX = 200
VOICE_MAX = 120


@dataclass(frozen=True)
class Entry:
    """One media model of a connection."""

    capability: str
    model: str
    enabled: bool = True
    price: float = 0.0
    # Text to speech only: the voice this model reads in. A provider's voices
    # are its own, so this is not the instance's default voice.
    voice: str = ""
    # What an administrator calls it and says about it; empty: the id.
    name: str = ""
    description: str = ""
    # How this model is asked (`clean_options`): only keys somebody set.
    options: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    @property
    def label(self) -> str:
        return self.name or self.model

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "capability": self.capability,
            "model": self.model,
            "enabled": self.enabled,
            "price": self.price,
        }
        if self.capability == CAP_TTS and self.voice:
            out["voice"] = self.voice
        if self.name:
            out["name"] = self.name
        if self.description:
            out["description"] = self.description
        if self.options:
            out["options"] = dict(self.options)
        return out


def _price(value: Any) -> float:
    try:
        number = float(str(value).strip().replace(",", ".")) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 and number == number and number != float("inf") else 0.0


def _printable(value: Any, limit: int) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isprintable())[:limit]


def _number(value: Any, low: float, high: float, *, whole: bool = False) -> Any:
    """A number inside its bounds, or None for "nobody said"."""
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    number = min(max(number, low), high)
    return int(number) if whole else number


def _names(value: Any, limit: int, most: int) -> list[str]:
    """A list of names: from a list, or one per line (or comma) of text."""
    if isinstance(value, str):
        value = value.replace(",", "\n").splitlines()
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        name = _printable(item, limit)
        if name and name not in out:
            out.append(name)
    return out[:most]


def size_of(value: Any) -> tuple[int, int] | None:
    """`1024x768` (or `1024*768`, `1024×768`) as two sides, each 64 to 4096."""
    parts = str(value or "").lower().replace("*", "x").replace("×", "x").split("x")
    if len(parts) != 2:
        return None
    try:
        width, height = int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        return None
    return (width, height) if 64 <= width <= 4096 and 64 <= height <= 4096 else None


# The options a model of each capability can carry. Every one has a reader:
# `speech.speak` and `speech.transcribe` (language, speed, format),
# `instance.audio_view` (the voice list a person chooses from), the image
# tool (`sizes`, `negative`; and for a ComfyUI checkpoint the sampling four).
OPTION_KEYS: dict[str, tuple[str, ...]] = {
    CAP_STT: ("language",),
    CAP_TTS: ("voices", "speed", "format", "language"),
    CAP_IMAGE: ("sizes", "negative", "steps", "cfg", "sampler", "scheduler"),
    CAP_IMAGE_EDIT: ("sizes", "negative"),
    # A video model: the resolutions it takes (`720P`, or `1280x720` for the
    # ones asked by size), the longest clip, and whether a first picture is
    # passed the older way (`img_url`).
    CAP_VIDEO: ("resolutions", "max_seconds"),
}
RESOLUTIONS = ("480P", "720P", "1080P")


def clean_options(capability: str, raw: Any) -> dict[str, Any]:
    """A model's options as stored: the keys its capability has, each
    checked, and only the ones that say something -- an absent key is "no
    opinion", which falls through to the instance's or the provider's own."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for key in OPTION_KEYS.get(capability, ()):
        value = raw.get(key)
        if value in (None, "", []):
            continue
        if key == "language":
            cleaned: Any = _printable(value, 32)
        elif key == "voices":
            cleaned = _names(value, VOICE_MAX, MAX_VOICES)
        elif key == "speed":
            cleaned = _number(value, 0.25, 4.0)
        elif key == "format":
            cleaned = str(value).strip().lower()
            cleaned = cleaned if cleaned in AUDIO_FORMATS else None
        elif key == "sizes":
            cleaned = [
                f"{pair[0]}x{pair[1]}"
                for pair in map(size_of, _names(value, 16, MAX_SIZES * 2))
                if pair is not None
            ][:MAX_SIZES]
        elif key == "negative":
            cleaned = str(value).strip()[:500]
        elif key == "resolutions":
            cleaned = [
                name for name in (n.upper() for n in _names(value, 8, 6)) if name in RESOLUTIONS
            ]
        elif key == "max_seconds":
            cleaned = _number(value, 1, 60, whole=True)
        elif key == "steps":
            cleaned = _number(value, 1, 150, whole=True)
        elif key == "cfg":
            cleaned = _number(value, 0.0, 30.0)
        else:  # sampler, scheduler: a name the ComfyUI knows
            cleaned = _printable(value, 80)
        if cleaned not in (None, "", []):
            out[key] = cleaned
    return out


def _entry(raw: Any) -> Entry | None:
    if not isinstance(raw, dict):
        return None
    capability = str(raw.get("capability") or "").strip().lower()
    # A model id is sent to a provider as it stands: no control characters,
    # and nothing that is not a printable id.
    model = _printable(raw.get("model"), MODEL_MAX)
    if capability not in ALL or not model:
        return None
    return Entry(
        capability=capability,
        model=model,
        enabled=bool(raw.get("enabled")),
        price=_price(raw.get("price")),
        voice=_printable(raw.get("voice"), VOICE_MAX) if capability == CAP_TTS else "",
        name=_printable(raw.get("name"), NAME_MAX),
        description=str(raw.get("description") or "").strip()[:DESCRIPTION_MAX],
        options=clean_options(capability, raw.get("options")),
    )


def normalise(raw: Any) -> dict[str, Any]:
    """What is stored: the two switches and the models, each one once per
    capability (the first wins), in the order given."""
    raw = raw if isinstance(raw, dict) else {}
    seen: set[tuple[str, str]] = set()
    models: list[dict[str, Any]] = []
    for item in raw.get("models") if isinstance(raw.get("models"), list) else []:
        entry = _entry(item)
        if entry is None or (entry.capability, entry.model) in seen:
            continue
        seen.add((entry.capability, entry.model))
        models.append(entry.as_dict())
        if len(models) >= MAX_MODELS:
            break
    return {
        "prefer_audio": bool(raw.get("prefer_audio")),
        "prefer_image": bool(raw.get("prefer_image")),
        "models": models,
    }


def of(connection: Any) -> dict[str, Any]:
    """A connection's media, normalised; empty for a row that has none."""
    return normalise(getattr(connection, "media_json", None) or {})


def entries(
    connection: Any, capability: str | None = None, *, enabled: bool = False
) -> list[Entry]:
    out = [e for e in map(_entry, of(connection)["models"]) if e is not None]
    if capability is not None:
        out = [e for e in out if e.capability == capability]
    return [e for e in out if e.enabled] if enabled else out


def find(connection: Any, capability: str, model: str = "") -> Entry | None:
    """The connection's enabled model for a capability: the one named, else
    the first."""
    offered = entries(connection, capability, enabled=True)
    if model:
        return next((e for e in offered if e.model == model), None)
    return offered[0] if offered else None


def prefers(connection: Any, capability: str) -> bool:
    """Whether the connection's switch for this capability is on: one for
    both directions of speech, one for everything it generates -- pictures,
    edited pictures, video, music, 3D models."""
    media = of(connection)
    return bool(media["prefer_image"] if capability in GENERATIONS else media["prefer_audio"])


def own(connection: Any, capability: str) -> Entry | None:
    """The model a chat on this connection uses for a capability: the switch
    is on and a model is enabled -- else None, and the instance's default."""
    if connection is None or not prefers(connection, capability):
        return None
    return find(connection, capability)


# --- What a provider speaks -----------------------------------------------------
# How a media request reaches a connection ("dialect"): the OpenAI-shaped
# routes on its base URL, or one of Alibaba Cloud Model Studio's own.
OPENAI = "openai"
ALIBABA_CHAT_ASR = "alibaba_chat_asr"  # chat/completions with an `input_audio` part
DASHSCOPE_ASR = "dashscope_asr"  # native multimodal-generation, `parameters.format`
DASHSCOPE_TTS_CHAT = "dashscope_tts_chat"  # native multimodal-generation, `input.text`
DASHSCOPE_TTS = "dashscope_tts"  # native `audio/tts/SpeechSynthesizer`
DASHSCOPE_IMAGE = "dashscope_image"  # native multimodal-generation, synchronous
DASHSCOPE_EDIT = "dashscope_edit"  # the same route, with the pictures to edit
DASHSCOPE_VIDEO = "dashscope_video"  # native video-synthesis, a task that is polled
OPENAI_EDIT = "openai_edit"  # `POST /images/edits`, multipart


# The ComfyUI: drawn by `services/images/`, with a workflow and a checkpoint.
COMFYUI = "comfyui"


def equipment(provider_type: str | None) -> bool:
    """Whether a connection is the instance's own equipment -- a ComfyUI, a
    speech server -- rather than a provider of chat models. No chat is on
    one, so its models serve only as the instance's default; and it answers
    as the instance did before these were connections (the same names on the
    usage page and in `/v1/models`)."""
    return not providers.serves_chat(provider_type)


def capabilities_of(provider_type: str | None, base_url: str | None = "") -> tuple[str, ...]:
    """The capabilities a connection of this type can keep models for."""
    type_id = providers.get(provider_type).id
    if type_id == providers.COMFYUI:
        # Its checkpoints. What else a ComfyUI makes -- edits, video, music,
        # 3D -- is a workflow of that kind (`ImageWorkflow.kind`), not a model.
        return (CAP_IMAGE,)
    if type_id == providers.SPEECH:
        return (CAP_STT, CAP_TTS)
    if not supported(provider_type, base_url):
        return ()
    # Only what this server can ask a provider for: a capability with no
    # dialect would be a model that is listed and never answers.
    return tuple(
        capability for capability in ALL if dialect(provider_type, base_url, capability, "")
    )


# --- What a type keeps besides the columns every connection has -----------------
COMFY_TIMEOUT = 600.0


def type_options(connection: Any) -> dict[str, Any]:
    """`Connection.options_json`, checked, for the type it is. A ComfyUI: how
    long a generation may take, whether the chat's model is unloaded while it
    draws, its default workflow, and the samplers and schedulers it was found
    to have. Nothing for any other type."""
    raw = getattr(connection, "options_json", None)
    raw = raw if isinstance(raw, dict) else {}
    if providers.get(getattr(connection, "provider_type", "")).id != providers.COMFYUI:
        return {}
    timeout = _number(raw.get("timeout"), 10.0, 3600.0)
    return {
        "timeout": timeout if timeout is not None else COMFY_TIMEOUT,
        "preserve_vram": bool(raw.get("preserve_vram")),
        "default_workflow_id": _printable(raw.get("default_workflow_id"), 32),
        "samplers": _names(raw.get("samplers"), 80, 200),
        "schedulers": _names(raw.get("schedulers"), 80, 200),
    }


def with_entry(connection: Any, entry: Entry, *, was: tuple[str, str] | None = None) -> dict:
    """The connection's media with `entry` put in: in place of the one it
    names (`was`: its capability and id before an edit), else at the end."""
    media = of(connection)
    key = was or (entry.capability, entry.model)
    models, placed = [], False
    for item in media["models"]:
        if (item["capability"], item["model"]) == key and not placed:
            models.append(entry.as_dict())
            placed = True
        elif (item["capability"], item["model"]) != (entry.capability, entry.model):
            models.append(item)
    if not placed:
        models.append(entry.as_dict())
    return normalise({**media, "models": models})


def without_entry(connection: Any, capability: str, model: str) -> dict:
    media = of(connection)
    return normalise(
        {**media, "models": [
            m for m in media["models"] if (m["capability"], m["model"]) != (capability, model)
        ]}
    )


def get_entry(connection: Any, capability: str, model: str) -> Entry | None:
    """The stored model of that capability and id, enabled or not."""
    return next((e for e in entries(connection, capability) if e.model == model), None)


def is_alibaba(provider_type: str | None, base_url: str | None = "") -> bool:
    if providers.get(provider_type).id in (providers.ALIBABA, providers.ALIBABA_PLAN):
        return True
    found = providers.by_host(base_url or "")
    return found is not None and found.id in (providers.ALIBABA, providers.ALIBABA_PLAN)


def supported(provider_type: str | None, base_url: str | None = "") -> bool:
    """Whether a connection of this type can carry media at all: anything
    that speaks OpenAI's API, and Alibaba's two types. Anthropic's API has
    neither speech nor pictures."""
    return (
        providers.protocol_of(provider_type) == providers.PROTOCOL_OPENAI
        or is_alibaba(provider_type, base_url)
    )


def dialect(provider_type: str | None, base_url: str | None, capability: str, model: str) -> str:
    """Which API a media model on a connection is reached through; "" when
    this server speaks none for that capability there (music and 3D models
    from a provider; video from anything but Alibaba's API -- OpenAI's own
    video API was withdrawn, and no other is a documented, stable shape)."""
    if providers.get(provider_type).id == providers.COMFYUI:
        return COMFYUI
    if capability in (CAP_MUSIC, CAP_MODEL3D):
        return ""
    if not is_alibaba(provider_type, base_url):
        if capability == CAP_VIDEO:
            return ""
        return OPENAI_EDIT if capability == CAP_IMAGE_EDIT else OPENAI
    if capability == CAP_VIDEO:
        return DASHSCOPE_VIDEO
    if capability == CAP_IMAGE_EDIT:
        return DASHSCOPE_EDIT
    name = model.lower()
    if capability == CAP_STT:
        # Qwen3-ASR is documented on the OpenAI-compatible chat route; the
        # Qwen-Audio and Fun-ASR recognisers only on the native one.
        return DASHSCOPE_ASR if name.startswith(("qwen-audio", "fun-asr")) else ALIBABA_CHAT_ASR
    if capability == CAP_TTS:
        return DASHSCOPE_TTS_CHAT if name.startswith(("qwen3-tts", "qwen-tts")) else DASHSCOPE_TTS
    return DASHSCOPE_IMAGE


# --- What a known provider is pre-filled with -------------------------------------
# Offered in the form, switched off, for an administrator to tick: model ids
# from each provider's own documentation. No prices: they move, and a figure
# typed here would be one nobody checked.
_OFFERS: dict[str, tuple[Entry, ...]] = {
    providers.OPENAI: (
        Entry(CAP_STT, "whisper-1", False),
        Entry(CAP_STT, "gpt-4o-mini-transcribe", False),
        Entry(CAP_TTS, "tts-1", False, voice="alloy"),
        Entry(CAP_TTS, "gpt-4o-mini-tts", False, voice="alloy"),
        Entry(CAP_IMAGE, "gpt-image-1", False),
        Entry(CAP_IMAGE, "dall-e-3", False),
    ),
    providers.ALIBABA: (
        Entry(CAP_STT, "qwen3-asr-flash", False),
        Entry(CAP_TTS, "qwen3-tts-flash", False, voice="Cherry"),
        Entry(CAP_IMAGE, "qwen-image-3.0", False),
        Entry(CAP_IMAGE, "wan2.6-t2i", False),
        Entry(CAP_IMAGE, "z-image-turbo", False),
        Entry(CAP_IMAGE_EDIT, "qwen-image-2.0", False),
        Entry(CAP_VIDEO, "wan2.7-t2v", False),
        Entry(CAP_VIDEO, "wan2.7-i2v", False),
    ),
    # What the Token Plan's list of models names for each capability.
    providers.ALIBABA_PLAN: (
        Entry(CAP_STT, "qwen-audio-3.0-asr-flash", False),
        Entry(CAP_TTS, "qwen-audio-3.0-tts-plus", False),
        Entry(CAP_IMAGE, "qwen-image-3.0-pro", False),
        Entry(CAP_IMAGE, "wan2.7-image", False),
        # The plan's own video model; a person's own turns only, like its chat.
        Entry(CAP_VIDEO, "wan3.0-video", False),
    ),
}
# A plan that serves chat models only (Alibaba's Coding Plan).
_CHAT_ONLY_HOSTS = ("coding-intl.dashscope.aliyuncs.com", "coding.dashscope.aliyuncs.com")


def offers(provider_type: str | None, base_url: str | None = "") -> tuple[Entry, ...]:
    """The media models a known provider type documents; none for a generic
    OpenAI-compatible connection, whose administrator types the ids."""
    host = providers._host(base_url)
    if host in _CHAT_ONLY_HOSTS:
        return ()
    return _OFFERS.get(providers.get(provider_type).id, ())


def form_rows(connection: Any) -> list[dict[str, Any]]:
    """The rows the connection form shows: what is stored, then what the
    provider offers and is not stored yet (switched off), then one blank row
    per capability to type a model id into."""
    kinds = capabilities_of(
        getattr(connection, "provider_type", ""), getattr(connection, "base_url", "")
    )
    stored = [e for e in entries(connection) if e.capability in kinds]
    have = {(e.capability, e.model) for e in stored}
    rows = [{**e.as_dict(), "voice": e.voice, "offered": False} for e in stored]
    for offer in offers(
        getattr(connection, "provider_type", ""), getattr(connection, "base_url", "")
    ):
        if (offer.capability, offer.model) not in have:
            rows.append({**offer.as_dict(), "voice": offer.voice, "offered": True})
    for capability in kinds:
        rows.append(
            {"capability": capability, "model": "", "enabled": False, "price": 0.0,
             "voice": "", "offered": False}
        )
    return rows


__all__ = [
    "ALIBABA_CHAT_ASR",
    "ALL",
    "CAPABILITIES",
    "COMFYUI",
    "EVERY",
    "NAMED",
    "capabilities_of",
    "clean_options",
    "equipment",
    "get_entry",
    "size_of",
    "type_options",
    "with_entry",
    "without_entry",
    "CAP_IMAGE",
    "CAP_IMAGE_EDIT",
    "CAP_MODEL3D",
    "CAP_MUSIC",
    "CAP_VIDEO",
    "DASHSCOPE_EDIT",
    "DASHSCOPE_VIDEO",
    "GENERATIONS",
    "LONG",
    "OPENAI_EDIT",
    "CAP_STT",
    "CAP_TTS",
    "DASHSCOPE_ASR",
    "DASHSCOPE_IMAGE",
    "DASHSCOPE_TTS",
    "DASHSCOPE_TTS_CHAT",
    "OPENAI",
    "Entry",
    "dialect",
    "entries",
    "find",
    "form_rows",
    "is_alibaba",
    "normalise",
    "of",
    "offers",
    "own",
    "prefers",
    "supported",
]
