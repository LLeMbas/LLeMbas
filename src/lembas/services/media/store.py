"""What a connection offers besides chat: `Connection.media_json`.

```
{"prefer_audio": bool, "prefer_image": bool,
 "models": [{"capability": "stt" | "tts" | "image", "model": "<id>",
             "enabled": bool, "price": <money>, "voice": "<tts only>"}]}
```

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

from dataclasses import dataclass
from typing import Any

from lembas.services.llm import providers

CAP_STT = "stt"
CAP_TTS = "tts"
CAP_IMAGE = "image"
CAPABILITIES = (CAP_STT, CAP_TTS, CAP_IMAGE)

# The most media models one connection keeps: a form, not a catalogue.
MAX_MODELS = 24
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

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "capability": self.capability,
            "model": self.model,
            "enabled": self.enabled,
            "price": self.price,
        }
        if self.capability == CAP_TTS and self.voice:
            out["voice"] = self.voice
        return out


def _price(value: Any) -> float:
    try:
        number = float(str(value).strip().replace(",", ".")) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 and number == number and number != float("inf") else 0.0


def _entry(raw: Any) -> Entry | None:
    if not isinstance(raw, dict):
        return None
    capability = str(raw.get("capability") or "").strip().lower()
    # A model id is sent to a provider as it stands: no control characters,
    # and nothing that is not a printable id.
    model = "".join(ch for ch in str(raw.get("model") or "").strip() if ch.isprintable())
    if capability not in CAPABILITIES or not model:
        return None
    voice = "".join(ch for ch in str(raw.get("voice") or "").strip() if ch.isprintable())
    return Entry(
        capability=capability,
        model=model[:MODEL_MAX],
        enabled=bool(raw.get("enabled")),
        price=_price(raw.get("price")),
        voice=voice[:VOICE_MAX] if capability == CAP_TTS else "",
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
    both directions of speech, one for pictures."""
    media = of(connection)
    return bool(media["prefer_image"] if capability == CAP_IMAGE else media["prefer_audio"])


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
    """Which API a media model on a connection is reached through."""
    if not is_alibaba(provider_type, base_url):
        return OPENAI
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
    ),
    # What the Token Plan's list of models names for each capability.
    providers.ALIBABA_PLAN: (
        Entry(CAP_STT, "qwen-audio-3.0-asr-flash", False),
        Entry(CAP_TTS, "qwen-audio-3.0-tts-plus", False),
        Entry(CAP_IMAGE, "qwen-image-3.0-pro", False),
        Entry(CAP_IMAGE, "wan2.7-image", False),
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
    stored = entries(connection)
    have = {(e.capability, e.model) for e in stored}
    rows = [{**e.as_dict(), "voice": e.voice, "offered": False} for e in stored]
    for offer in offers(
        getattr(connection, "provider_type", ""), getattr(connection, "base_url", "")
    ):
        if (offer.capability, offer.model) not in have:
            rows.append({**offer.as_dict(), "voice": offer.voice, "offered": True})
    for capability in CAPABILITIES:
        rows.append(
            {"capability": capability, "model": "", "enabled": False, "price": 0.0,
             "voice": "", "offered": False}
        )
    return rows


__all__ = [
    "ALIBABA_CHAT_ASR",
    "CAPABILITIES",
    "CAP_IMAGE",
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
