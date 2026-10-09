"""The harness spec shared with the LLeMbas CLI, run through LLeMbas.

`src/lembas/harness_spec/` is vendored from the CLI (`scripts/fetch_harness.py`)
and must match its lock. Every conformance case of an area LLeMbas implements is
run here; the CLI runs the same files. A case failing on one side only is a
difference between the two harnesses -- the thing the spec exists to prevent.

An area LLeMbas does not implement yet is skipped *by name* with the reason, so
the gap is visible in every test run rather than forgotten.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from lembas.services import generation, harness_spec, reasoning
from lembas.services.agent import policy, shell
from lembas.services.library import chunks

ROOT = Path(__file__).resolve().parent.parent

AREAS = {
    "hardline": lambda c: r.id if (r := shell.hardline_command(c["line"])) else None,
    "split": lambda c: (lambda s: {"commands": s.commands, "unsafe": s.unsafe})(
        shell.split_command(c["line"])
    ),
    "plain": lambda c: shell.plain_commands(c["line"]),
    "arity": lambda c: " ".join(shell.prefix(shell.words(c["command"]))),
    "always": lambda c: policy.always_patterns("bash", c["line"]),
    "chunk": lambda c: chunks.split(c["text"], size=c["size"], overlap=c["overlap"]),
    "think": lambda c: _think(c),
    "effort": lambda c: {
        "refused": generation._effort_was_refused(c["message"]),
        "advertised": generation._advertised_efforts(c["message"]),
    },
}


def _think(c):
    splitter = reasoning.ReasoningSplitter()
    out = {"reasoning": "", "text": ""}
    pieces = [p for chunk in c["chunks"] for p in splitter.feed(chunk)] + list(splitter.flush())
    for kind, text in pieces:
        out["reasoning" if kind == "reasoning" else "text"] += text
    return out


# The file tools are not LLeMbas's: an agent chat runs on a linked LLeMbas CLI,
# which edits the files and runs these cases itself.
_ON_THE_DEVICE = "the file tools run on the linked LLeMbas CLI, not in LLeMbas"

# Areas of the spec LLeMbas does not run yet, or no longer runs, and why.
PENDING = {
    "permission": "LLeMbas's modes are still its own table (agent/policy.py POLICY); the spec's "
    "modes.json and default rules are adopted later -- see the LLeMbas CLI's harness-parity notes",
    "edit": _ON_THE_DEVICE,
    "unidiff": _ON_THE_DEVICE,
    "envelope": _ON_THE_DEVICE,
}


def _cases():
    for path in sorted(harness_spec.conformance_dir().glob("*.json")):
        area = path.stem
        doc = json.loads(path.read_text(encoding="utf-8"))
        for n, case in enumerate(doc["cases"]):
            yield pytest.param(area, case, id=f"{area}-{n}")


def test_the_vendored_spec_matches_its_lock():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "fetch_harness.py")], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_area_is_run_or_named_as_pending():
    areas = {p.stem for p in harness_spec.conformance_dir().glob("*.json")}
    assert areas <= set(AREAS) | set(PENDING), sorted(areas - set(AREAS) - set(PENDING))


@pytest.mark.parametrize(("area", "case"), list(_cases()))
def test_conformance(area, case):
    if area in PENDING:
        pytest.skip(f"{area}: {PENDING[area]}")
    if area not in AREAS:
        pytest.skip(f"{area}: not implemented in LLeMbas yet")
    want = case["expect"]
    given = {k: v for k, v in case.items() if k != "expect"}
    assert AREAS[area](given) == want, given


# --- The tools ----------------------------------------------------------------

# The tools an agent chat uses, offered by the linked LLeMbas CLI under the
# spec's own name, description and parameters. LLeMbas itself offers none of
# them, and the spec scopes them `cli`.
DEVICE_TOOLS = (
    "read",
    "write",
    "edit",
    "multiedit",
    "apply_patch",
    "list",
    "glob",
    "grep",
    "bash",
    "bash_output",
    "bash_list",
    "bash_kill",
    "todo",
    "plan_submit",
)
# Shared tools whose LLeMbas implementation keeps its own name or shape for now,
# and why -- the parity page lists the same.
TOOLS_PENDING = {
    "ask_user": "LLeMbas's own card and schema (services/interaction.py); same name",
    "task": "subagent_run, always on the parent's own model",
    "web_search": "LLeMbas's own description; same name",
    "web_fetch": "still fetch",
    "memory": "memory_add / memory_forget",
    "notes_search": "LLeMbas's notes tools, five",
    "note_view": "notes_get",
    "note_manage": "notes_create / notes_edit / notes_delete",
    "skills_list": "the skill index is in the prompt",
    "skill_view": "skill_get",
    "skill_manage": "skill_create / skill_edit",
    "knowledge_search": "LLeMbas's own description; same name",
    "knowledge_get": "LLeMbas's own description; same name",
}


def test_every_shared_tool_is_named_as_pending():
    shared = {n for n, t in harness_spec.tools().items() if t["scope"] == "shared"}
    assert shared == set(TOOLS_PENDING), sorted(shared ^ set(TOOLS_PENDING))


def test_the_execution_tools_are_the_clis():
    tools = harness_spec.tools()
    assert {name: tools[name]["scope"] for name in DEVICE_TOOLS} == dict.fromkeys(
        DEVICE_TOOLS, "cli"
    )
