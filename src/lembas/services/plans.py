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

Since harness spec 1.1.0 the plan is one structure both LLeMbas and the LLeMbas
CLI keep per conversation (`plan_write`, `plan_update`, `plan_submit`), and the
shape, `apply`'s error texts and the former `todo` tool's conversion are pinned
by `harness_spec/conformance/plan.json`, which both test suites run.
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

# How the former `todo` tool's statuses read as a task's (`from_todos`).
TODO_STATUSES = {
    "pending": "todo",
    "in_progress": "doing",
    "completed": "done",
    "cancelled": "dropped",
}

# How a task is marked when the plan is rendered as text: a class in the card,
# a character here, because a model reads the text.
MARKS = {"todo": "[ ]", "doing": "[>]", "done": "[x]", "dropped": "[~]"}


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _status(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    wanted = str(value or "").strip().lower()
    return wanted if wanted in allowed else fallback


class _Ids:
    """Ids for one kind of item: kept from a stored plan, generated otherwise.

    A plan the model sent (`plan_write`) gets fresh sequential ids whatever it
    wrote -- ids are the implementation's, never the model's. A plan already
    stored carries `version` and ids that `apply` may have inserted out of
    order (`t5` in the first phase), and *those* must survive a re-read: the
    model is quoting them, and renumbering would silently re-tick a different
    task -- the failure the module docstring names.
    """

    def __init__(self, prefix: str, keep: bool) -> None:
        self.prefix = prefix
        self.keep = keep
        self.used: set[str] = set()
        self.highest = 0

    def next(self, given: Any) -> str:
        wanted = str(given or "").strip() if self.keep else ""
        if (
            wanted
            and wanted.startswith(self.prefix)
            and wanted[len(self.prefix) :].isdigit()
            and wanted not in self.used
        ):
            self.used.add(wanted)
            self.highest = max(self.highest, int(wanted[len(self.prefix) :]))
            return wanted
        self.highest += 1
        made = f"{self.prefix}{self.highest}"
        self.used.add(made)
        return made


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
    # A stored plan says its version; a model's does not (the tool schema has
    # no such field), and that is what decides whether ids are kept.
    keep_ids = raw.get("version") == VERSION

    finding_ids = _Ids("f", keep_ids)
    findings = []
    for item in _listed(raw.get("findings"))[:MAX_FINDINGS]:
        source = item if isinstance(item, dict) else {"text": item}
        text = _text(source.get("text"))
        if text:
            findings.append({"id": finding_ids.next(source.get("id")), "text": text})

    objective_ids = _Ids("o", keep_ids)
    objectives = []
    for item in _listed(raw.get("objectives"))[:MAX_OBJECTIVES]:
        source = item if isinstance(item, dict) else {"text": item}
        text = _text(source.get("text"))
        if text:
            objectives.append(
                {
                    "id": objective_ids.next(source.get("id")),
                    "text": text,
                    "status": _status(source.get("status"), OBJECTIVE_STATUSES, "open"),
                }
            )

    phases = _phases(raw, keep_ids)
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


def _phases(raw: dict[str, Any], keep_ids: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    task_ids = _Ids("t", keep_ids)
    phase_ids = _Ids("p", keep_ids)
    for n, item in enumerate(_listed(raw.get("phases"))[:MAX_PHASES], start=1):
        source = item if isinstance(item, dict) else {"title": item}
        tasks = []
        for entry in _listed(source.get("tasks"))[:MAX_TASKS]:
            got = entry if isinstance(entry, dict) else {"text": entry}
            text = _text(got.get("text"))
            if not text:
                continue
            tasks.append(
                {
                    "id": task_ids.next(got.get("id")),
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
                "id": phase_ids.next(source.get("id")),
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


# --- Changing ------------------------------------------------------------------
def _id_number(value: str, prefix: str) -> int:
    try:
        return int(str(value)[len(prefix) :])
    except (TypeError, ValueError):
        return 0


def apply(plan: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """`plan_update`'s changes applied to a stored plan.

    Returns `{"plan": …}` or `{"error": "…"}`, and on an error nothing has
    changed: every id and status is checked first, so a call that ticks one task
    and names a second one wrongly does not half-apply. The error names the
    known ids, because the model's next move is to quote one of them.

    New tasks continue the numbering after the highest existing task number, in
    the order given, and go after `after` when that task is in the named phase,
    else at the end of it. New findings continue the finding numbers. `steps` is
    flattened again afterwards, for the reason `flatten` gives.
    """
    current = normalise(plan)
    if not current:
        return {"error": "There is no plan yet. Write one with plan_write first."}
    changes = changes or {}

    tasks_by_id: dict[str, dict[str, Any]] = {}
    phase_of: dict[str, dict[str, Any]] = {}
    for phase in current["phases"]:
        for task in phase["tasks"]:
            tasks_by_id[task["id"]] = task
            phase_of[task["id"]] = phase
    phases_by_id = {phase["id"]: phase for phase in current["phases"]}
    objectives_by_id = {objective["id"]: objective for objective in current["objectives"]}

    def _known(book: dict[str, Any]) -> str:
        return ", ".join(book) if book else "none"

    task_changes = [c for c in _listed(changes.get("tasks")) if isinstance(c, dict)]
    phase_changes = [c for c in _listed(changes.get("phases")) if isinstance(c, dict)]
    objective_changes = [c for c in _listed(changes.get("objectives")) if isinstance(c, dict)]
    added_tasks = [c for c in _listed(changes.get("add_tasks")) if isinstance(c, dict)]
    added_findings = [_text(f) for f in _listed(changes.get("add_findings"))]

    # Validate everything before touching anything.
    for change in task_changes:
        task_id = str(change.get("id") or "").strip()
        if task_id not in tasks_by_id:
            return {"error": f'Unknown task id "{task_id}". Known: {_known(tasks_by_id)}.'}
        status = change.get("status")
        if status is not None and str(status).strip().lower() not in TASK_STATUSES:
            return {
                "error": f'Unknown status "{status}" for a task. '
                f"One of: {', '.join(TASK_STATUSES)}."
            }
    for change in phase_changes:
        phase_id = str(change.get("id") or "").strip()
        if phase_id not in phases_by_id:
            return {"error": f'Unknown phase id "{phase_id}". Known: {_known(phases_by_id)}.'}
        status = change.get("status")
        if status is not None and str(status).strip().lower() not in PHASE_STATUSES:
            return {
                "error": f'Unknown status "{status}" for a phase. '
                f"One of: {', '.join(PHASE_STATUSES)}."
            }
    for change in objective_changes:
        objective_id = str(change.get("id") or "").strip()
        if objective_id not in objectives_by_id:
            return {
                "error": f'Unknown objective id "{objective_id}". '
                f"Known: {_known(objectives_by_id)}."
            }
        status = change.get("status")
        if status is not None and str(status).strip().lower() not in OBJECTIVE_STATUSES:
            return {
                "error": f'Unknown status "{status}" for an objective. '
                f"One of: {', '.join(OBJECTIVE_STATUSES)}."
            }
    for addition in added_tasks:
        phase_id = str(addition.get("phase") or "").strip()
        if phase_id not in phases_by_id:
            return {"error": f'Unknown phase id "{phase_id}". Known: {_known(phases_by_id)}.'}
        after = str(addition.get("after") or "").strip()
        if after and after not in tasks_by_id:
            return {"error": f'Unknown task id "{after}". Known: {_known(tasks_by_id)}.'}
        if not _text(addition.get("text")):
            return {"error": "A new task needs its text."}

    # Then apply, in the order the schema lists them.
    for change in task_changes:
        task = tasks_by_id[str(change.get("id")).strip()]
        if change.get("status") is not None:
            task["status"] = str(change["status"]).strip().lower()
        if change.get("note") is not None:
            task["note"] = _text(change["note"])
    for change in objective_changes:
        objectives_by_id[str(change.get("id")).strip()]["status"] = (
            str(change["status"]).strip().lower()
        )
    for change in phase_changes:
        phases_by_id[str(change.get("id")).strip()]["status"] = (
            str(change["status"]).strip().lower()
        )

    highest = max((_id_number(task_id, "t") for task_id in tasks_by_id), default=0)
    for addition in added_tasks:
        phase = phases_by_id[str(addition.get("phase")).strip()]
        if len(phase["tasks"]) >= MAX_TASKS:
            continue
        highest += 1
        task = {
            "id": f"t{highest}",
            "text": _text(addition.get("text")),
            "status": "todo",
            "note": "",
        }
        after = str(addition.get("after") or "").strip()
        position = len(phase["tasks"])
        if after and phase_of.get(after) is phase:
            position = next(
                (n + 1 for n, existing in enumerate(phase["tasks"]) if existing["id"] == after),
                position,
            )
        phase["tasks"].insert(position, task)
        tasks_by_id[task["id"]] = task
        phase_of[task["id"]] = phase

    last_finding = max((_id_number(f["id"], "f") for f in current["findings"]), default=0)
    for text in added_findings:
        if not text or len(current["findings"]) >= MAX_FINDINGS:
            continue
        last_finding += 1
        current["findings"].append({"id": f"f{last_finding}", "text": text})

    current["steps"] = flatten(current)
    return {"plan": current}


def from_todos(items: Any) -> dict[str, Any]:
    """The former `todo` tool's list as a plan: one phase called "Work".

    A bare string is a pending item. The phase is active while anything is in
    progress, pending otherwise -- the list said nothing about phases, so the
    phase says only what its tasks say.
    """
    tasks = []
    for n, item in enumerate(_listed(items)[:MAX_TASKS], start=1):
        source = item if isinstance(item, dict) else {"content": item}
        text = _text(source.get("content") if "content" in source else source.get("text"))
        if not text:
            continue
        status = TODO_STATUSES.get(str(source.get("status") or "pending").strip().lower(), "todo")
        tasks.append({"id": f"t{n}", "text": text, "status": status, "note": ""})
    if not tasks:
        return {}
    plan = {
        "version": VERSION,
        "title": "Work",
        "summary": "",
        "findings": [],
        "objectives": [],
        "phases": [
            {
                "id": "p1",
                "title": "Work",
                "status": "active" if any(t["status"] == "doing" for t in tasks) else "pending",
                "tasks": tasks,
            }
        ],
    }
    plan["steps"] = flatten(plan)
    return plan


# --- Reading, as text --------------------------------------------------------
def render(plan: dict[str, Any] | None) -> str:
    """The plan as the model reads it: every line with its id.

    The ids are what `plan_update` takes, so they are in the text rather than
    beside it; a mark per task says where things stand. What a tool returns and
    what the prompt carries is this same text, so the two cannot disagree.
    """
    plan = normalise(plan)
    if not plan:
        return ""
    lines = [f"# {plan['title']}"]
    if plan["summary"]:
        lines.append(plan["summary"])
    if plan["findings"]:
        lines.append("Findings: " + "; ".join(f"[{f['id']}] {f['text']}" for f in plan["findings"]))
    if plan["objectives"]:
        lines.append(
            "Objectives: "
            + "; ".join(f"[{o['id']}] {o['text']} ({o['status']})" for o in plan["objectives"])
        )
    for phase in plan["phases"]:
        lines.append(f"## {phase['id']} {phase['title']} ({phase['status']})")
        for task in phase["tasks"]:
            line = f"[{task['id']}] {MARKS[task['status']]} {task['text']}"
            if task["note"]:
                line += f" — {task['note']}"
            lines.append(line)
    return "\n".join(lines)


def summary_lines(plan: dict[str, Any] | None, limit: int = 6) -> list[dict[str, str]]:
    """The few lines the panel above the composer shows.

    What is in progress, then what is left, then the last couple done -- the
    same choice the LLeMbas CLI makes above its prompt. Each line is a task
    with its status, for the template to mark.
    """
    plan = normalise(plan)
    tasks = [task for phase in plan.get("phases", []) for task in phase.get("tasks", [])]
    doing = [t for t in tasks if t["status"] == "doing"]
    todo = [t for t in tasks if t["status"] == "todo"]
    done = [t for t in tasks if t["status"] == "done"]
    return (doing + todo + done[-2:])[:limit]


def counts(plan: dict[str, Any] | None) -> tuple[int, int]:
    """(done, all) over every task that was not dropped."""
    plan = normalise(plan)
    tasks = [task for phase in plan.get("phases", []) for task in phase.get("tasks", [])]
    kept = [t for t in tasks if t["status"] != "dropped"]
    return sum(1 for t in kept if t["status"] == "done"), len(kept)


def open_tasks(plan: dict[str, Any] | None) -> bool:
    """Whether anything on the plan is still to do."""
    plan = normalise(plan)
    return any(
        task["status"] in ("todo", "doing")
        for phase in plan.get("phases", [])
        for task in phase.get("tasks", [])
    )


__all__ = [
    "MARKS",
    "VERSION",
    "apply",
    "counts",
    "flatten",
    "from_todos",
    "normalise",
    "open_tasks",
    "render",
    "summary_lines",
]
