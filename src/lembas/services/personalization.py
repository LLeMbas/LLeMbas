"""How a person wants to be talked to: custom instructions and a
personality, theirs alone and the same for every model.

What replaced per-model personas and impressions: one
setting per person rather than one per model, chosen rather than written by the
model, and opt-in -- an administrator turns the feature on for the instance
(Settings → General), and each person then chooses for themselves. Nothing is
sent until somebody has.

Both are appended to the system prompt, after the authored one, as the last
thing a model reads about how to behave. The presets are short on purpose: a
personality is a register, not a character sheet, and a long one crowds out the
instructions that matter.

The same presets are the LLeMbas CLI's (`harness/prompts/personality/` in the
shared spec), and a CLI signed in to this instance takes the person's choice
from here.
"""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.services import settings_store

# The ids and their labels, in the order they are offered. The text is the
# harness spec's (`prompts/personality/<id>.md`), with these as the fallback a
# copy older than spec 2.0.0 lacks.
PRESETS: dict[str, tuple[str, str]] = {
    "concise": (
        "Concise",
        "Be brief. Lead with the answer, then only what the reader needs to act on "
        "it. No preamble, no recap, no closing offers.",
    ),
    "pragmatic": (
        "Pragmatic",
        "Be practical and direct. Prefer what works over what is elegant, say "
        "plainly what you would do and why, and name trade-offs in a sentence.",
    ),
    "optimistic": (
        "Optimistic",
        "Be warm and encouraging. Notice what is going well and frame problems as "
        "solvable, without hiding them or overstating what works.",
    ),
    "funny": (
        "Funny",
        "Be lighthearted and witty where it fits -- a dry aside, a well-placed "
        "joke -- but never at the cost of being correct or clear, and drop it "
        "entirely when the reader is frustrated or the stakes are high.",
    ),
    "formal": (
        "Formal",
        "Be formal and precise: complete sentences, exact terms, no slang and no "
        "emoji.",
    ),
    "socratic": (
        "Socratic",
        "Help the reader think it through. Ask a guiding question where it would "
        "teach more than an answer, and give the answer when they ask for it.",
    ),
}
CUSTOM = "custom"
NONE = ""

MAX_INSTRUCTIONS = 4000
MAX_CUSTOM = 1500


def preset_text(preset: str) -> str:
    """The spec's wording for a preset, or the fallback above."""
    try:
        text = (
            resources.files("lembas.harness_spec")
            .joinpath("prompts", "personality", f"{preset}.md")
            .read_text(encoding="utf-8")
            .strip()
        )
        if text:
            return text
    except (FileNotFoundError, OSError, ValueError):
        pass
    return PRESETS.get(preset, ("", ""))[1]


def enabled(db: DBSession) -> bool:
    """Whether the instance offers it at all."""
    return bool(settings_store.get(db, "personalization_enabled"))


def of(user: Any) -> dict[str, Any]:
    """What this person chose: instructions, a preset id, and custom text --
    and whether they use it (`on`): a LLeMbas CLI signed in as them can
    switch it off without losing what was written. A choice saved
    without the flag is on."""
    raw = ((getattr(user, "settings_json", None) or {}).get("personalization")) or {}
    preset = _preset(str(raw.get("personality") or ""))
    return {
        "instructions": str(raw.get("instructions") or "")[:MAX_INSTRUCTIONS],
        "personality": preset,
        "custom": str(raw.get("custom") or "")[:MAX_CUSTOM],
        "on": bool(raw.get("on", True)),
    }


# A preset id this release does not list is kept as it is: a LLeMbas CLI
# on a newer harness spec may offer one, and saving through it must not blank
# the choice. It is sent when the vendored spec has its text, and is nothing
# otherwise -- never an error.
_PRESET_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")


def _preset(value: str) -> str:
    value = value.strip()
    if value in PRESETS or value == CUSTOM:
        return value
    return value if _PRESET_ID.fullmatch(value) else NONE


def cleaned(instructions: str, personality: str, custom: str, on: bool = True) -> dict[str, Any]:
    personality = _preset(personality)
    return {
        "instructions": instructions.strip()[:MAX_INSTRUCTIONS],
        "personality": personality,
        "custom": custom.strip()[:MAX_CUSTOM],
        "on": bool(on),
    }


def block(db: DBSession, user: Any) -> str:
    """What goes after the system prompt for this person, or "" when nothing does
    (the feature off, or nothing chosen)."""
    if user is None or not enabled(db):
        return ""
    chosen = of(user)
    if not chosen["on"]:
        return ""
    parts: list[str] = []
    if chosen["instructions"]:
        heading = "## How the person you are talking to wants to be helped"
        parts.append(f"{heading}\n\n{chosen['instructions']}")
    if chosen["personality"] == CUSTOM and chosen["custom"]:
        parts.append(f"## Personality\n\n{chosen['custom']}")
    elif chosen["personality"] and (text := preset_text(chosen["personality"])):
        parts.append(f"## Personality\n\n{text}")
    return "\n\n".join(parts)


__all__ = ["CUSTOM", "PRESETS", "block", "cleaned", "enabled", "of", "preset_text"]
