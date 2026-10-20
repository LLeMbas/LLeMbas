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
import re
import subprocess
import sys
from pathlib import Path

import pytest

from lembas.services import generation, harness_spec, plans, prompts, prototypes, reasoning
from lembas.services import tools as tools_service
from lembas.services.agent import policy, shell
from lembas.services.library import chunks, documents
from lembas.services.llm import openai_client

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
    "plan": lambda c: _plan(c),
    "prototype": lambda c: prototypes.info(c["info"], c["code"]),
    "library_scope": lambda c: documents.resolve_base(c["bases"], c["base"]),
    "toolcall_index": lambda c: _toolcalls(c),
    "fill": lambda c: _fill(c),
    "narrow": lambda c: _narrow(c),
    "memory": lambda c: _memory(c),
}


def _fill(c):
    try:
        return {"text": harness_spec.fill(c["text"], c["vars"])}
    except KeyError:
        names = harness_spec.VARIABLE.findall(c["text"])
        return {"missing": sorted({name for name in names if name not in c["vars"]})}


def _narrow(c):
    try:
        return {"parameters": harness_spec.narrow(c["parameters"], c["narrow"])}
    except harness_spec.NarrowError as exc:
        return {"error": exc.code}


def _memory(c):
    from lembas.services.library import memories

    r = memories.apply_ops(c["target"], c["entries"], c["ops"], c["limit"])
    return {
        "ok": r["ok"],
        "entries": r["after"] if r["ok"] else c["entries"],
        "message": r["message"],
        "usage": r["usage"],
        "shown": "entries" in r,
    }


def _toolcalls(c):
    """The spec writes a delta flat; the wire puts name and arguments under `function`."""
    acc = openai_client.ToolCallAccumulator()
    for delta in c["deltas"]:
        fragment = {k: delta[k] for k in ("index", "id") if k in delta}
        function = {k: delta[k] for k in ("name", "arguments") if k in delta}
        if function:
            fragment["function"] = function
        acc.feed([fragment])
    return acc.calls


def _plan(c):
    if c["op"] == "normalise":
        return plans.normalise(c["plan"])
    if c["op"] == "update":
        return plans.apply(c["plan"], c["changes"])
    if c["op"] == "todos":
        return plans.from_todos(c["todos"])
    raise AssertionError(f"unknown plan op {c['op']!r}")


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


def _semver(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.strip().lstrip("v").split("."))


def test_the_vendored_spec_matches_its_lock():
    """The lock is what makes the vendored copy a *pinned* one.

    Skipped, not failed, while the spec directory carries a newer version than
    the lock: that is the state between copying the CLI's `harness/` over for
    development and `fetch_harness.py --update vX.Y.Z` re-pinning it at
    release. Older than the lock, or the same version with other bytes, is an
    edit by hand and fails.
    """
    lock = json.loads((ROOT / "scripts" / "harness.lock.json").read_text())
    if _semver(harness_spec.version()) > _semver(lock["version"]):
        pytest.skip(
            f"the spec directory is {harness_spec.version()} and the lock pins "
            f"{lock['version']}: a development copy, re-pinned at release with "
            "scripts/fetch_harness.py --update"
        )
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
)
# Shared tools LLeMbas offers under the spec's own name, description and
# parameters -- the definition a model is sent is the spec's, read from it.
SHARED_ADOPTED = (
    "plan_write",
    "plan_update",
    "plan_submit",
    "knowledge_search",
    "knowledge_get",
    "ask_user",
    "task",
    "web_search",
    "web_fetch",
    "memory",
    "notes_search",
    "note_view",
    "note_manage",
    "skills_list",
    "skill_view",
    "skill_manage",
)
# Shared tools whose LLeMbas implementation keeps its own name or shape for now,
# and why -- the parity page lists the same.
TOOLS_PENDING: dict[str, str] = {}


def test_every_shared_tool_is_adopted_or_named_as_pending():
    shared = {n for n, t in harness_spec.tools().items() if t["scope"] == "shared"}
    named = set(TOOLS_PENDING) | set(SHARED_ADOPTED)
    assert shared == named, sorted(shared ^ named)
    assert not set(TOOLS_PENDING) & set(SHARED_ADOPTED)


def _registry():
    from lembas.services import subagent

    book = dict(tools_service.REGISTRY)
    for tool in subagent.tool_defs():
        book.setdefault(tool.name, tool)
    return book


@pytest.mark.parametrize("name", SHARED_ADOPTED)
def test_an_adopted_tool_is_sent_exactly_as_the_spec_says(name):
    """Description and parameters word for word, with the spec's variables
    filled the way LLeMbas fills them and the parameters narrowed by the
    tool's `narrow.llembas` -- nothing else of LLeMbas's own."""
    spec = harness_spec.tools()[name]
    tool = _registry()[name]
    variables = {**tools_service.SPEC_VARIABLES, **tools_service.LLEMBAS_VARS.get(name, {})}
    assert tool.description == harness_spec.fill(spec["description"], variables)
    expected = harness_spec.narrow(
        harness_spec.fill_parameters(spec["parameters"], variables),
        (spec.get("narrow") or {}).get("llembas"),
    )
    assert tool.parameters == expected
    assert "{{" not in json.dumps(tool.parameters)


def test_every_variable_of_an_adopted_tool_has_a_value_here():
    """A variable the CLI fills and LLeMbas does not would be an error at the
    first request; caught here instead."""
    for name in SHARED_ADOPTED:
        declared = set(harness_spec.tools()[name].get("variables") or {})
        given = set(tools_service.SPEC_VARIABLES) | set(tools_service.LLEMBAS_VARS.get(name, {}))
        assert declared <= given, (name, sorted(declared - given))


def test_a_former_name_still_runs_as_the_tool():
    """`aliases.llembas`: a name a transcript or a trained model still uses --
    unless LLeMbas still has a tool by that name, which keeps it."""
    for name, spec in harness_spec.tools().items():
        for alias in spec.get("aliases", {}).get("llembas", []):
            resolved, _args = tools_service.resolve_alias(alias, {})
            expected = alias if alias in tools_service.REGISTRY else name
            assert resolved == expected, alias
    # And the one that changed its arguments: a todo list arrives as a plan.
    name, args = tools_service.resolve_alias(
        "todo", {"todos": [{"content": "Fix it", "status": "in_progress"}]}
    )
    assert name == "plan_write"
    assert args["title"] == "Work" and args["phases"][0]["tasks"][0]["status"] == "doing"



def test_no_offered_tool_is_renamed_away():
    """The spec lists `fetch`, `notes_get`, `memory_add` and others as former
    names of spec tools LLeMbas does not have. Rewritten, each call to them
    came back "Unknown tool" -- 1.3.0 broke every one. A name that was offered
    keeps it, and an alias whose target was not offered is left alone."""
    offered = dict(tools_service.REGISTRY)
    for name in offered:
        assert tools_service.resolve_alias(name, {"x": 1}, offered)[0] == name, name
    # `subagent_run` -> `task` still converts where `task` is offered ...
    assert tools_service.resolve_alias("subagent_run", {"task": "t"}, {"task": object()})[0] == (
        "task"
    )
    # ... and not where it is not: nothing is gained by renaming to a stranger.
    assert tools_service.resolve_alias("subagent_run", {"task": "t"}, {})[0] == "subagent_run"

# --- The prompt texts -----------------------------------------------------------
def _identical():
    for path, entry in harness_spec.index()["prompts"].items():
        if entry.get("scope") == "shared" and entry.get("llembas_text") == "identical":
            for key in entry.get("llembas") or []:
                yield pytest.param(path, key, entry, id=f"{path}->{key}")


@pytest.mark.parametrize(("path", "key", "entry"), list(_identical()))
def test_a_shared_text_marked_identical_is_the_fragment_default(path, key, entry):
    """`prompts/index.json` says which LLeMbas fragment each shared text is, and
    whether the default is that text word for word. Where it says `identical`,
    it has to be: the fragment's default with every variable empty is the spec
    text with every variable empty -- except the one LLeMbas fills with a
    constant (`plan_dir`), which is filled the same on both sides."""
    fragment = {f.key: f for f in prompts.BUILTIN}[key]
    values = {
        name: prompts.PLAN_DIR if name == "plan_dir" else ""
        for name in entry.get("variables") or {}
    }
    ours = re.sub(r"\n{3,}", "\n\n", prompts.substitute(fragment.default, values)).strip()
    assert ours == harness_spec.prompt_with(path, values)


def test_the_execution_tools_are_the_clis():
    tools = harness_spec.tools()
    assert {name: tools[name]["scope"] for name in DEVICE_TOOLS} == dict.fromkeys(
        DEVICE_TOOLS, "cli"
    )
