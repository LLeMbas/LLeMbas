"""Turning a stored template and a model's arguments into a ComfyUI workflow.

A template is an API-format workflow with `{{placeholders}}` where the values
go. Which node holds the prompt is therefore the administrator's statement
rather than something guessed from node types -- sniffing for the first
`CLIPTextEncode` works on the shipped template and on nothing else, and gets
positive and negative the wrong way round the first time somebody reorders them.

**Substitution walks the parsed JSON, not the text of it.** A value that is
*exactly* `"{{steps}}"` is replaced by the number 20, not by the string "20";
ComfyUI validates types and refuses the second. A placeholder inside a longer
string still substitutes as text, which is what makes
`"{{prompt}}, masterpiece"` work. Doing it textually would also mean a prompt
containing a quotation mark produced a document that no longer parses, on the
one input guaranteed to contain arbitrary text.

The names are the tool's parameter names, so there is one vocabulary: what a
model may set, what the admin page documents and what a template may reference
cannot drift apart.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

# Every hole a template may carry. A name outside this set is left alone, the
# same rule `prompts.substitute` follows -- a literal `{{x}}` is not a feature,
# but silently deleting one is worse than leaving it visible.
PLACEHOLDERS = (
    "model",
    "prompt",
    "negative",
    "seed",
    "steps",
    "cfg",
    "width",
    "height",
    "sampler",
    "scheduler",
    "denoise",
    # How many pictures one run produces. Late to the list, and the reason is
    # worth stating: `batch_size` was a literal `1` in the base template, so an
    # administrator whose card can comfortably make four at a time had no way of
    # saying so short of editing the JSON. Not a tool parameter -- a model asking
    # for six images because it is unsure is exactly the cost this should not
    # invite -- so it fills from the instance default and nowhere else.
    "batch",
)

# What a model may name. Everything else in `PLACEHOLDERS` fills from a default.
MODEL_SETTABLE = tuple(name for name in PLACEHOLDERS if name != "batch")

# The floor, taken from the base template. An instance's own defaults sit above
# this (see `resolve`), and this stays as the last resort so a fresh install
# behaves exactly as it always did.
#
# `seed` is deliberately absent: it has no fixed default, because one would make
# every generation that did not name a seed identical -- and would make the
# retry loop produce the same rejected image four times over.
DEFAULTS: dict[str, Any] = {
    "negative": "text, watermark",
    "steps": 20,
    "cfg": 8.0,
    "width": 512,
    "height": 512,
    "sampler": "euler",
    "scheduler": "normal",
    "denoise": 1.0,
    "batch": 1,
}

# What each hole is for, and what it lands as. Read by the workflow editor, so
# somebody writing a template is told what `{{sampler}}` fills without reading
# this file -- and in particular is told the two names that do not match
# ComfyUI's own, which is the mistake that costs an afternoon.
DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "model": ("text", "The checkpoint. Fills ComfyUI's `ckpt_name`, not `model`."),
    "prompt": ("text", "What to draw. The only value a model must supply."),
    "negative": ("text", "What to keep out of the picture."),
    "seed": ("number", "The noise seed. Absent or negative means a fresh random one."),
    "steps": ("number", "How many denoising steps. More is slower, not always better."),
    "cfg": ("number", "How closely to follow the prompt. A decimal."),
    "width": ("number", "Pixels across. A multiple of 64."),
    "height": ("number", "Pixels down. A multiple of 64."),
    "sampler": ("text", "The sampling method. Fills ComfyUI's `sampler_name`, not `sampler`."),
    "scheduler": ("text", "The noise schedule."),
    "denoise": ("number", "How much of the latent to redraw. 1.0 for text-to-image."),
    "batch": ("number", "How many images one run makes. Fills `batch_size`."),
}

# ComfyUI's own ranges, read off `/object_info`. Clamped rather than refused: a
# model that asks for 300 steps has misjudged rather than misbehaved, and one
# clarifying round to say so is worse than doing the sensible thing.
LIMITS: dict[str, tuple[float, float]] = {
    "steps": (1, 150),
    "cfg": (0.0, 30.0),
    "width": (64, 2048),
    "height": (64, 2048),
    "denoise": (0.0, 1.0),
    # Not ComfyUI's ceiling, which is 4096, but a sane one: this multiplies
    # every generation's time and VRAM, and an administrator who wants more than
    # eight at once wants a different workflow rather than a bigger number here.
    "batch": (1, 8),
}

# ComfyUI's seed is a uint64. Generated here rather than left to the far side
# so the value can be reported back -- "it looked like this and here is how to
# get it again" is most of what a seed is for.
MAX_SEED = 2**64 - 1

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")


def random_seed() -> int:
    return secrets.randbelow(MAX_SEED)


def instance_defaults(values: dict[str, Any] | None) -> dict[str, Any]:
    """The `default_*` keys out of the image settings, as placeholder names.

    Only the ones actually set: an absent or empty key means "no opinion", and
    must fall through to `DEFAULTS` rather than land as an empty string in a
    workflow. That is the same reading `resolve` gives a model's own arguments,
    and it is why an administrator can set two of these and leave the rest.
    """
    out: dict[str, Any] = {}
    for name in PLACEHOLDERS:
        if name in ("prompt", "seed", "model"):
            # A default prompt is not a thing; a default seed would make every
            # picture identical; the checkpoint has its own setting and its own
            # per-chat override, resolved before this is reached.
            continue
        value = (values or {}).get(f"default_{name}")
        if value is None or value == "":
            continue
        out[name] = value
    return out


def resolve(given: dict[str, Any], *, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """The full parameter set: what was asked for, over what this instance
    prefers, over the built-in floor.

    Three rungs, most specific winning, and the middle one is the new part. For
    the whole life of this feature there were only two -- so 512x512, euler and
    twenty steps were the values every instance got, whatever card it was
    running on, and the only ways to move them were to bake literals into a
    template instead of placeholders or to write prose in the instructions box
    and hope. `DEFAULTS` stays underneath so an instance that sets nothing
    behaves exactly as it did.

    Absent and null are both "no opinion", at both levels. A model that emits
    `"seed": null` rather than omitting the key is common enough that treating
    it as a request for seed zero would be a bug nobody could see.

    **A negative seed means random**, which is what `-1` means in ComfyUI's own
    interface, in A1111, and in every other thing that has ever asked somebody
    for a seed. A model that has read any of them will write it, and without
    this it went through the uint64 wrap and came out as 18446744073709551615 --
    a perfectly valid *fixed* seed, so "give me something new" produced the same
    picture every time. Exactly the wrong answer, arrived at silently.
    """
    values: dict[str, Any] = {**DEFAULTS, **instance_defaults(settings)}
    for name, value in (given or {}).items():
        # `batch` is absent from `MODEL_SETTABLE`, so a model naming it is
        # ignored here rather than refused -- the tool schema never offered it,
        # and one that invents the key has guessed rather than misbehaved.
        if name in MODEL_SETTABLE and value is not None and value != "":
            values[name] = value

    seed = _whole(values.get("seed"), default=-1)
    values["seed"] = random_seed() if seed < 0 else seed % (MAX_SEED + 1)
    for name in ("steps", "width", "height", "batch"):
        values[name] = _clamp(_whole(values.get(name), DEFAULTS[name]), name)
    for name in ("cfg", "denoise"):
        values[name] = _clamp(_decimal(values.get(name), DEFAULTS[name]), name)
    for name in ("prompt", "negative", "sampler", "scheduler", "model"):
        values[name] = str(values.get(name) or "")
    return values


def _whole(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _decimal(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: Any, name: str) -> Any:
    low, high = LIMITS.get(name, (None, None))
    if low is None:
        return value
    clamped = min(max(value, low), high)
    return int(clamped) if isinstance(value, int) else clamped


def fill(template: Any, values: dict[str, Any]) -> Any:
    """A copy of the template with its placeholders replaced.

    Recursive over dicts and lists, because a workflow is nested and a
    placeholder can be anywhere in it -- including inside a node's `_meta`,
    which is harmless and should not be treated specially.
    """
    if isinstance(template, dict):
        return {key: fill(value, values) for key, value in template.items()}
    if isinstance(template, list):
        return [fill(item, values) for item in template]
    if isinstance(template, str):
        return _fill_string(template, values)
    return template


def _fill_string(text: str, values: dict[str, Any]) -> Any:
    """One string, which may *become* a number.

    The whole-value case is what keeps types right: `"{{steps}}"` is the number
    and not a string that looks like one. Anything else is ordinary text
    substitution, so `"{{prompt}}, masterpiece"` reads as a sentence.
    """
    whole = _PLACEHOLDER.fullmatch(text.strip())
    if whole is not None:
        return values.get(whole.group(1), text)

    def swap(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values[name]) if name in values else match.group(0)

    return _PLACEHOLDER.sub(swap, text)


def placeholders_in(template: Any) -> set[str]:
    """Every `{{name}}` a template uses, for the admin page to report.

    A template that mentions none of them is almost certainly a workflow pasted
    straight out of ComfyUI without being parameterised, which would generate
    the same picture whatever anybody typed. Worth saying at save time rather
    than leaving somebody to discover it.
    """
    found: set[str] = set()
    if isinstance(template, dict):
        for value in template.values():
            found |= placeholders_in(value)
    elif isinstance(template, list):
        for item in template:
            found |= placeholders_in(item)
    elif isinstance(template, str):
        found |= {match.group(1) for match in _PLACEHOLDER.finditer(template)}
    return found
