"""The harness spec this server shares with the LLeMbas CLI, as vendored.

`src/lembas/harness_spec/` is a pinned, hash-checked copy of the LLeMbas
CLI's `harness/` directory (`scripts/fetch_harness.py`). It is the design both
projects implement: every tool's name, description and parameters as a model is
sent them, the hardline and how a command is spelled plainly, the "always allow"
table, the modes, model metadata, the prompt texts, and conformance cases with
their expected results, which `tests/test_harness_spec.py` runs.

Read once, at first use. Never edited here: a change goes into the spec in
the LLeMbas CLI first, then arrives by `fetch_harness.py --update`.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

SPEC_DIR = Path(__file__).resolve().parent.parent / "harness_spec"


@cache
def version() -> str:
    return (SPEC_DIR / "VERSION").read_text().strip()


@cache
def _json(relative: str) -> Any:
    return json.loads((SPEC_DIR / relative).read_text(encoding="utf-8"))


@cache
def tools() -> dict[str, dict[str, Any]]:
    """Every tool of the spec, by name."""
    out: dict[str, dict[str, Any]] = {}
    for path in sorted((SPEC_DIR / "tools").glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        out[spec["name"]] = spec
    return out


def tool(name: str) -> dict[str, Any]:
    found = tools().get(name)
    if found is None:
        raise KeyError(f"harness spec {version()} has no tool {name!r}")
    return found


def purpose() -> dict[str, Any]:
    """The argument a call says what it is for in (`purpose.json`)."""
    return _json("purpose.json")


def described(name: str, variables: dict[str, str] | None = None) -> str:
    """A tool's description as the spec has it, its `{{variables}}` filled in. A
    variable left unfilled is an error: it would reach the model as a literal."""
    import re

    spec = tool(name)
    values = variables or {}

    def fill(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"harness spec: no value for {{{{{key}}}}} in {name}'s description")
        return values[key]

    return re.sub(r"\{\{(\w+)\}\}", fill, spec["description"])


def hardline() -> dict[str, Any]:
    return _json("permission/hardline.json")


def arity() -> dict[str, Any]:
    return _json("permission/arity.json")


def modes() -> dict[str, Any]:
    return _json("permission/modes.json")


def prompt(relative: str) -> str:
    """A prompt text, with its provenance comments taken out (as the LLeMbas CLI sends it)."""
    import re

    raw = (SPEC_DIR / "prompts" / relative).read_text(encoding="utf-8")
    return re.sub(r"<!--[\s\S]*?-->\n?", "", raw).strip()


def conformance_dir() -> Path:
    return SPEC_DIR / "conformance"


__all__ = [
    "SPEC_DIR",
    "arity",
    "conformance_dir",
    "described",
    "hardline",
    "modes",
    "prompt",
    "purpose",
    "tool",
    "tools",
    "version",
]
