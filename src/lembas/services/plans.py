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

**Ids are generated here and never chosen by the model.** Letting it name them
would mean validating names it made up, and a collision would silently re-tick
a different task.
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

TASK_STATUSES = ("todo", "doing", "done", "dropped")
OBJECTIVE_STATUSES = ("open", "done", "dropped")
PHASE_STATUSES = ("pending", "active", "done")


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


__all__ = [
    "VERSION",
    "flatten",
    "normalise",
]
