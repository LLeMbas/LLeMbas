"""A plan, as a structure rather than a list of sentences.

Plan mode used to produce `{title, steps}` and then forget it. That is enough to
propose something and useless for carrying it out: there is nowhere to record
what was found, nothing to tick off, and — worst — the plan was not in the
prompt at all once execution started, so a model could not have kept it current
if it had wanted to.

Version 2 is findings, objectives and phases of tasks. Three rules hold it up.

**`steps` is always written.** Flattened from every phase's tasks, in order. It
is what `execute_plan` reads, so nothing downstream had to learn version 2 and
every row already on disk keeps working.

**`normalise` is the only reader.** A `{title, steps}` row becomes one phase
called "Plan" whose tasks are those steps, so the card, the harness and the
Execute button have exactly one shape to deal with rather than two.

**Ids are generated here and never chosen by the model.** They appear in
`render_block` so the model can quote one back to `plan_update`; letting it name
them would mean validating names it made up, and a collision would silently
re-tick a different task.
"""

from __future__ import annotations

from typing import Any

VERSION = 2

# Bounds. A plan is read by a person and injected into every request while the
# work is going on, so "as many as you like" costs the window forever and buries
# the four items that mattered.
MAX_PHASES = 8
MAX_TASKS = 12
MAX_OBJECTIVES = 8
MAX_FINDINGS = 20
MAX_TEXT = 300
MAX_TITLE = 120

# The ceiling on the block put in front of the model each turn.
MAX_PLAN_CHARS = 2000

TASK_STATUSES = ("todo", "doing", "done", "dropped")
OBJECTIVE_STATUSES = ("open", "done", "dropped")
PHASE_STATUSES = ("pending", "active", "done")

_DONE = {"done", "dropped"}


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _status(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    wanted = str(value or "").strip().lower()
    return wanted if wanted in allowed else fallback


def _listed(value: Any) -> list[Any]:
    """A list, from a list or from the one thing a model sent instead.

    The same tolerance `generation._questions_in` shows, for the same reason: a
    small model sends something close to the schema rather than the schema, and
    refusing costs a whole round trip to say so.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# --- Reading -------------------------------------------------------------------
def normalise(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Any stored plan, as version 2.

    A `{title, steps}` row -- which is every row that exists today -- becomes one
    phase called "Plan" whose tasks are the steps. Everything downstream then has
    one shape, and the version-1 branch lives here and nowhere else.
    """
    raw = raw or {}
    if not raw:
        return {}

    title = _text(raw.get("title"), MAX_TITLE) or "A plan"
    findings = [
        {"id": f"f{n}", "text": _text(item.get("text") if isinstance(item, dict) else item)}
        for n, item in enumerate(_listed(raw.get("findings"))[:MAX_FINDINGS], start=1)
    ]
    findings = [f for f in findings if f["text"]]

    objectives = []
    for n, item in enumerate(_listed(raw.get("objectives"))[:MAX_OBJECTIVES], start=1):
        source = item if isinstance(item, dict) else {"text": item}
        text = _text(source.get("text"))
        if text:
            objectives.append(
                {
                    "id": f"o{n}",
                    "text": text,
                    "status": _status(source.get("status"), OBJECTIVE_STATUSES, "open"),
                }
            )

    phases = _phases(raw)
    if not phases:
        # Version 1, or a model that sent only steps. One phase, so the rest of
        # the codebase never sees the older shape.
        tasks = [_text(step) for step in _listed(raw.get("steps"))]
        phases = [
            {
                "id": "p1",
                "title": "Plan",
                "status": "pending",
                "tasks": [
                    {"id": f"t{n}", "text": text, "status": "todo", "note": ""}
                    for n, text in enumerate([t for t in tasks if t][:MAX_TASKS], start=1)
                ],
            }
        ]

    plan = {
        "version": VERSION,
        "title": title,
        "summary": _text(raw.get("summary")),
        "findings": findings,
        "objectives": objectives,
        "phases": phases,
    }
    plan["steps"] = flatten(plan)
    return plan


def _phases(raw: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    counter = 0
    for n, item in enumerate(_listed(raw.get("phases"))[:MAX_PHASES], start=1):
        source = item if isinstance(item, dict) else {"title": item}
        tasks = []
        for entry in _listed(source.get("tasks"))[:MAX_TASKS]:
            got = entry if isinstance(entry, dict) else {"text": entry}
            text = _text(got.get("text"))
            if not text:
                continue
            counter += 1
            tasks.append(
                {
                    "id": f"t{counter}",
                    "text": text,
                    "status": _status(got.get("status"), TASK_STATUSES, "todo"),
                    "note": _text(got.get("note")),
                }
            )
        title = _text(source.get("title"), MAX_TITLE)
        if not title and not tasks:
            continue
        out.append(
            {
                "id": f"p{n}",
                "title": title or f"Phase {n}",
                "status": _status(source.get("status"), PHASE_STATUSES, "pending"),
                "tasks": tasks,
            }
        )
    return out


def flatten(plan: dict[str, Any]) -> list[str]:
    """Every task, in order, as plain sentences.

    This is `steps`, and it is why version 2 needed no migration: `execute_plan`
    reads it and does not know the rest exists.
    """
    return [task["text"] for phase in plan.get("phases", []) for task in phase.get("tasks", [])]


# --- Writing --------------------------------------------------------------------
def build(**raw: Any) -> dict[str, Any]:
    """A plan from what `plan_submit` was given."""
    return normalise(raw)


def merge(plan: dict[str, Any], patch: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The plan with one update applied, and what changed, in words.

    Returns the words as well as the plan because the model gets them back as
    the tool's result -- "t3 is done, t4 is now doing" is what tells it the
    bookkeeping landed, and a silent success reads as a call that did nothing.
    """
    plan = normalise(plan)
    if not plan:
        return {}, []

    changed: list[str] = []
    tasks = {task["id"]: task for phase in plan["phases"] for task in phase["tasks"]}
    objectives = {item["id"]: item for item in plan["objectives"]}

    for entry in _listed(patch.get("task_status")):
        got = entry if isinstance(entry, dict) else {"id": entry}
        task = tasks.get(_text(got.get("id"), 32))
        if task is None:
            continue
        task["status"] = _status(got.get("status"), TASK_STATUSES, task["status"])
        if got.get("note") is not None:
            task["note"] = _text(got.get("note"))
        changed.append(f"{task['id']} is {task['status']}")

    for entry in _listed(patch.get("objective_status")):
        got = entry if isinstance(entry, dict) else {"id": entry}
        objective = objectives.get(_text(got.get("id"), 32))
        if objective is None:
            continue
        objective["status"] = _status(
            got.get("status"), OBJECTIVE_STATUSES, objective["status"]
        )
        changed.append(f"{objective['id']} is {objective['status']}")

    for raw in _listed(patch.get("findings")):
        text = _text(raw.get("text") if isinstance(raw, dict) else raw)
        if not text or len(plan["findings"]) >= MAX_FINDINGS:
            continue
        plan["findings"].append({"id": f"f{len(plan['findings']) + 1}", "text": text})
        changed.append("a finding was recorded")

    counter = max((int(t["id"][1:]) for t in tasks.values() if t["id"][1:].isdigit()), default=0)
    for entry in _listed(patch.get("add_tasks")):
        got = entry if isinstance(entry, dict) else {"text": entry}
        text = _text(got.get("text"))
        if not text:
            continue
        phase = _phase_for(plan, _text(got.get("phase"), 32))
        if phase is None or len(phase["tasks"]) >= MAX_TASKS:
            continue
        counter += 1
        phase["tasks"].append(
            {"id": f"t{counter}", "text": text, "status": "todo", "note": ""}
        )
        changed.append(f"t{counter} was added")

    if patch.get("summary") is not None:
        plan["summary"] = _text(patch.get("summary"))

    _restate_phases(plan)
    plan["steps"] = flatten(plan)
    return plan, changed


def _phase_for(plan: dict[str, Any], wanted: str) -> dict[str, Any] | None:
    """The named phase, or the one work is currently in."""
    for phase in plan["phases"]:
        if phase["id"] == wanted:
            return phase
    for phase in plan["phases"]:
        if phase["status"] == "active":
            return phase
    for phase in plan["phases"]:
        if any(task["status"] not in _DONE for task in phase["tasks"]):
            return phase
    return plan["phases"][-1] if plan["phases"] else None


def _restate_phases(plan: dict[str, Any]) -> None:
    """A phase's status follows from its tasks, so it cannot disagree with them.

    Asking the model to keep both current would mean a plan that says "phase 1:
    done" over four tasks marked todo, which is worse than either alone.
    """
    started = False
    for phase in plan["phases"]:
        if not phase["tasks"]:
            continue
        if all(task["status"] in _DONE for task in phase["tasks"]):
            phase["status"] = "done"
            continue
        # The first phase with anything left in it is the one being worked on;
        # everything after it is still to come. There is exactly one active
        # phase by construction, which is what stops the render showing three.
        phase["status"] = "pending" if started else "active"
        started = True


# --- For the prompt ---------------------------------------------------------------
def render_block(plan: dict[str, Any] | None, budget: int = MAX_PLAN_CHARS) -> str:
    """The plan as the model sees it each turn, within a budget.

    Budgeted rather than dumped, exactly like the project listing: a finished
    phase collapses to one line, the phase being worked on is shown in full, and
    the ids are visible because they are what `plan_update` takes.
    """
    plan = normalise(plan)
    if not plan or budget <= 0:
        return ""

    lines = [f"**{plan['title']}**"]
    if plan["summary"]:
        lines.append(plan["summary"])

    if plan["objectives"]:
        lines.append("")
        lines.append("What it is for:")
        for item in plan["objectives"]:
            mark = "x" if item["status"] == "done" else "-" if item["status"] == "dropped" else " "
            lines.append(f"- [{mark}] {item['id']} {item['text']}")

    if plan["findings"]:
        lines.append("")
        lines.append("What was found:")
        for item in plan["findings"][-MAX_FINDINGS:]:
            lines.append(f"- {item['text']}")

    lines.append("")
    for phase in plan["phases"]:
        done = sum(1 for task in phase["tasks"] if task["status"] in _DONE)
        if phase["status"] == "done" and phase["tasks"]:
            lines.append(f"✓ {phase['title']} ({len(phase['tasks'])} tasks, done)")
            continue
        lines.append(f"{phase['title']} ({done}/{len(phase['tasks'])})")
        for task in phase["tasks"]:
            mark = {"done": "x", "doing": ">", "dropped": "-"}.get(task["status"], " ")
            note = f" — {task['note']}" if task["note"] else ""
            lines.append(f"  [{mark}] {task['id']} {task['text']}{note}")

    text = "\n".join(lines).strip()
    if len(text) <= budget:
        return text
    cut = text[:budget]
    at = cut.rfind("\n")
    if at > budget // 2:
        cut = cut[:at]
    return f"{cut.rstrip()}\n… (the rest is in the plan card above)"


__all__ = [
    "MAX_PLAN_CHARS",
    "VERSION",
    "build",
    "flatten",
    "merge",
    "normalise",
    "render_block",
]
