"""A model's capabilities: what the provider says, with the administrator's word over it.

Three dicts on `Model`, the same keys in each -- `context_length`, `vision`,
`tools`, `reasoning`, `embeddings`:

- `capabilities_detected_json`: what the provider said on the last refresh,
  where it says anything (`detect.model_capabilities`), with `source`;
- `capabilities_override_json`: what an administrator set by hand. **Always
  wins**; a key absent from it follows detection;
- `capabilities_json` (and the `context_length` column): the effective values,
  which is what everything else reads. Recomputed here whenever either of the
  other two changes, never written on its own.

A key neither detected nor overridden keeps what the effective dict already
holds: a value set before detection existed, or by an older form, is not wiped
by a refresh that learned nothing.
"""

from __future__ import annotations

from typing import Any

from lembas.services.llm.openai_client import MAX_CONTEXT, MIN_CONTEXT

FLAGS = ("vision", "tools", "reasoning", "embeddings")
KEYS = ("context_length", *FLAGS)


def clean(raw: Any) -> dict[str, Any]:
    """Only the known keys, each of its own type: a bool for a flag, a whole
    number of tokens in range for the context length."""
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key in FLAGS:
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    length = raw.get("context_length")
    if isinstance(length, int) and not isinstance(length, bool) and (
        MIN_CONTEXT <= length <= MAX_CONTEXT
    ):
        out["context_length"] = length
    return out


def effective(model) -> dict[str, Any]:
    """The values a model is used with: override over detection over what it had."""
    detected = clean(model.capabilities_detected_json)
    override = clean(model.capabilities_override_json)
    current = dict(model.capabilities_json or {})
    out: dict[str, Any] = {}
    for key in FLAGS:
        if key in override:
            out[key] = override[key]
        elif key in detected:
            out[key] = detected[key]
        elif key in current:
            out[key] = bool(current[key])
    if "context_length" in override:
        out["context_length"] = override["context_length"]
    elif "context_length" in detected:
        out["context_length"] = detected["context_length"]
    elif model.context_length:
        out["context_length"] = int(model.context_length)
    return out


def apply(model) -> None:
    """Write the effective values onto the model: the flags into
    `capabilities_json` (beside the `tool_*` switches, which are left alone),
    the length into `context_length`."""
    values = effective(model)
    merged = dict(model.capabilities_json or {})
    for key in FLAGS:
        if key in values:
            merged[key] = values[key]
    model.capabilities_json = merged
    if "context_length" in values:
        model.context_length = values["context_length"]


def record_detected(model, found: dict[str, Any], source: str = "") -> None:
    """A refresh's findings, replacing the last ones, then the effective values."""
    detected = clean(found)
    if detected and source:
        detected["source"] = source
    model.capabilities_detected_json = detected
    apply(model)


def set_override(model, values: dict[str, Any]) -> None:
    """An administrator's word, replacing the last, then the effective values."""
    model.capabilities_override_json = clean(values)
    apply(model)


__all__ = ["FLAGS", "KEYS", "apply", "clean", "effective", "record_detected", "set_override"]
