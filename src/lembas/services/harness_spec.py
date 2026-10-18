"""The harness spec this server shares with the LLeMbas CLI, as vendored.

`src/lembas/harness_spec/` is a pinned, hash-checked copy of the LLeMbas
CLI's `harness/` directory (`scripts/fetch_harness.py`). It is the design both
projects implement: every tool's name, description and parameters as a model is
sent them, the hardline and how a command is spelled plainly, the "always allow"
table, the modes, model metadata, the prompt texts, the loop's budgets, and
conformance cases with their expected results, which `tests/test_harness_spec.py`
runs.

Read once, at first use. Never edited here: a change goes into the spec in
the LLeMbas CLI first, then arrives by `fetch_harness.py --update`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

SPEC_DIR = Path(__file__).resolve().parent.parent / "harness_spec"

# `{{name}}` in a prompt text or a tool description: the facts the two projects
# differ in. The grammar is the one `services/prompts.py` substitutes, so a
# text's JSON, shell and Markdown are never candidates.
VARIABLE = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")

# A line that is nothing but a bullet and a variable. The spec's rule: it
# disappears when the variable is empty, so one text serves a project that has
# the fact and one that does not.
_BULLET_ONLY = re.compile(r"^\s*[-*•]\s*$")


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


def hardline() -> dict[str, Any]:
    return _json("permission/hardline.json")


def arity() -> dict[str, Any]:
    return _json("permission/arity.json")


def loop() -> dict[str, Any]:
    """How one prompt runs: the step backstop, the budgets, the wrap-up, the nudges."""
    return _json("loop.json")


def index() -> dict[str, Any]:
    """`prompts/index.json`: which texts are shared, which fragment each is, its variables."""
    return _json("prompts/index.json")


def prompt(relative: str) -> str:
    """A prompt text, with its provenance comments taken out (as the LLeMbas CLI sends it)."""
    raw = (SPEC_DIR / "prompts" / relative).read_text(encoding="utf-8")
    return re.sub(r"<!--[\s\S]*?-->\n?", "", raw).strip()


def fill(text: str, variables: Mapping[str, str]) -> str:
    """`text` with every `{{name}}` filled, the spec's way.

    A line that is only a bullet and a variable goes when the variable is empty.
    A name the caller did not supply is an error, not a pass-through: the spec
    says none may reach a model unfilled, and a `{{plan_context}}` arriving in a
    system message as four braces is exactly the quiet failure that rule is for.
    """
    missing = sorted({name for name in VARIABLE.findall(text) if name not in variables})
    if missing:
        raise KeyError(f"unfilled variable(s) in a spec text: {', '.join(missing)}")

    lines: list[str] = []
    for line in text.split("\n"):
        expanded = False

        def _swap(match: re.Match[str]) -> str:
            nonlocal expanded
            expanded = True
            return str(variables[match.group(1)])

        rendered = VARIABLE.sub(_swap, line)
        if expanded and (not rendered.strip() or _BULLET_ONLY.match(rendered)):
            continue
        lines.append(rendered)
    # Filling can leave two blank lines where a section was empty.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def prompt_with(relative: str, variables: Mapping[str, str]) -> str:
    """A prompt text with its variables filled -- see `fill`."""
    return fill(prompt(relative), variables)


def conformance_dir() -> Path:
    return SPEC_DIR / "conformance"


__all__ = [
    "SPEC_DIR",
    "arity",
    "conformance_dir",
    "fill",
    "hardline",
    "index",
    "loop",
    "prompt",
    "prompt_with",
    "tools",
    "version",
]
