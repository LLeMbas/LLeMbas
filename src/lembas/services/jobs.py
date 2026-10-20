"""Background work a chat owns, and what a session is told about it.

The harness spec (2.0.0, `loop.json` `jobs`) has one word for work that runs
beside a conversation: a **job**. On the LLeMbas CLI a job is a command in the
background or a subagent; here there is no shell, so every job is a **helper**
-- a `task` started with `background: true`, running in a hidden child chat
(`services/subagent.py`). This module is two things about those:

- **The note.** `note()` composes the message a session is given when a job it
  started ends, or a subagent writes to it: the mark on the first line (the
  spec's `jobs.marks`, said rather than hidden, because a model refuses an
  unmarked one as an injection), then the facts. Pure, and run against the
  spec's `conformance/jobs.json` by `tests/test_harness_spec.py`, so the CLI
  and this server tell a model the same thing in the same words.

- **The registry.** `chat_jobs` rows (`db/models/chat.py:ChatJob`): one per
  helper, with the id the model knows it by (`job1`, `job2`, ... per chat), its
  state, and how far the model has read its output. A row rather than a dict
  on the parent's `Generation` because a job's whole point is to outlive the
  reply that started it -- `job_list` answers on the next request, and the
  strip above the composer finds the chat's running helpers from any page
  load, after a restart included.

What a helper *does* -- the child chat, the wake, the polling, the stop -- is
`services/subagent.py`'s. This module knows nothing about generation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from lembas.services import harness_spec
from lembas.web.i18n import N_, t

log = logging.getLogger(__name__)

# A job's state as the strip and the panel say it. Marked for the catalogue
# here and translated where shown (`state_word`), as the mode hints are.
STATE_WORDS: dict[str, str] = {
    "running": N_("running"),
    "done": N_("done"),
    "stopped": N_("stopped"),
    "error": N_("failed"),
    "lost": N_("lost"),
}


def state_word(state: str) -> str:
    """A job's state in the reader's language."""
    return t(STATE_WORDS.get(str(state), str(state)))

KIND_COMMAND = "command"
KIND_SUBAGENT = "subagent"
KIND_MESSAGE = "message"
KIND_TO_SUBAGENT = "to_subagent"

# Of a finished command's unread output, how much the note carries; and how
# many finished jobs a chat keeps -- the spec's defaults, read once.
NOTE_TAIL_CHARS = int(harness_spec.loop()["jobs"]["note_tail_chars"]["default"])
FINISHED_KEPT = int(harness_spec.loop()["jobs"]["finished_kept"]["default"])

# What a stopped command's note says where an exit code would be. The CLI says
# the same (a `null` exit is a signal, and TERM is the one `job_stop` sends).
STOPPED_BY_SIGNAL = "stopped (SIGTERM)"
NO_REPORT = "(the subagent returned no report)"

# How a subagent ended, in the words of its note's first line, by `reason`.
ENDED: dict[str, str] = {
    "stop": "finished",
    "stopped": "was stopped",
    "steps": "stopped at its step limit",
    "budget": "stopped at its budget",
    "error": "failed",
}


def marks() -> dict[str, str]:
    """`loop.json` `jobs.marks`: the first line of every note, by kind."""
    return dict(harness_spec.loop()["jobs"]["marks"])


def duration(seconds: float | int) -> str:
    """A duration as both projects write it: `5s`, `1m 05s`, `2h 03m`.

    Seconds alone under a minute; minutes with two-digit seconds under an
    hour; hours with two-digit minutes from there, the seconds dropped -- at
    that length they say nothing.
    """
    total = max(0, int(round(float(seconds))))
    if total < 60:
        return f"{total}s"
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _tail(unread: str) -> str:
    """The end of a command's unread output, an ellipsis before a cut."""
    text = str(unread or "")
    if len(text) > NOTE_TAIL_CHARS:
        text = "…" + text[-NOTE_TAIL_CHARS:]
    return text


def subagent_tail(agent: str, steps: int, tools: int, reason: str, error: str = "") -> str:
    """The tail line under a subagent's report: steps, tool calls, and how it
    ended when not of its own accord. `task`'s result carries the same line."""
    if reason == "stop":
        ended = ""
    elif reason == "error":
        ended = " · ended: error" + (f" — {error}" if error else "")
    else:
        ended = f" · ended: {reason}"
    return (
        f"[{agent} subagent · {steps} step{'' if steps == 1 else 's'} · "
        f"{tools} tool call{'' if tools == 1 else 's'}{ended}]"
    )


def note(case: dict[str, Any]) -> dict[str, str]:
    """The message a session is given about one job, from the job's record.

    `case` is the shape `conformance/jobs.json` describes: `kind` command,
    subagent, message or to_subagent, and the fields each needs. The result is
    `{"text": ...}` -- the mark on the first line, then the note.
    """
    kind = str(case.get("kind") or "")
    mark = marks()[kind]
    job_id = str(case.get("id") or "")

    if kind == KIND_COMMAND:
        command = str(case.get("command") or "").split("\n")[0][:120]
        exit_code = case.get("exit")
        how = STOPPED_BY_SIGNAL if exit_code is None else f"exited with {int(exit_code)}"
        line = f"{job_id} ({command}) {how} after {duration(case.get('seconds') or 0)}."
        tail = _tail(str(case.get("unread") or ""))
        if tail.strip():
            body = f"{line}\nOutput you had not read:\n{tail.rstrip()}"
        else:
            body = f"{line} It printed nothing you had not read."
        return {"text": f"{mark}\n{body}"}

    if kind == KIND_SUBAGENT:
        reason = str(case.get("reason") or "stop")
        how = ENDED.get(reason, f"ended: {reason}")
        head = (
            f"{job_id} ({case.get('agent')}: {case.get('description')}) {how} after "
            f"{duration(case.get('seconds') or 0)}."
        )
        report = str(case.get("report") or "").strip() or NO_REPORT
        tail = subagent_tail(
            str(case.get("agent") or ""),
            int(case.get("steps") or 0),
            int(case.get("tools") or 0),
            reason,
            str(case.get("error") or "").strip(),
        )
        text = f"{mark}\n{head}\n{report}\n\n{tail}"
        worktree = str(case.get("worktree") or "").strip()
        if worktree:
            text += f"\n[worktree] {worktree}"
        return {"text": text}

    if kind == KIND_MESSAGE:
        head = f"{job_id} ({case.get('agent')}: {case.get('description')}):"
        return {"text": f"{mark}\n{head}\n{str(case.get('text') or '').strip()}"}

    if kind == KIND_TO_SUBAGENT:
        return {"text": f"{mark}\n{str(case.get('text') or '').strip()}"}

    raise ValueError(f"unknown job note kind {kind!r}")


# --- The registry: chat_jobs rows ---------------------------------------------
def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite drops the offset: a row read back is naive and means UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def next_job_id(db, chat_id: str) -> str:
    """`job<n>`: one sequence per chat, continuing from what the chat has."""
    from sqlalchemy import func, select

    from lembas.db.models import ChatJob

    count = int(
        db.scalar(select(func.count()).select_from(ChatJob).where(ChatJob.chat_id == chat_id))
        or 0
    )
    return f"job{count + 1}"


def create(
    db,
    chat_id: str,
    *,
    child_chat_id: str,
    description: str,
    agent: str,
    model: str,
    message_id: str,
    background: bool,
    model_id: str = "",
    connection_id: str = "",
):
    """A new job row for a helper just started. Committed."""
    from lembas.db.models import JOB_RUNNING, JOB_SUBAGENT, ChatJob

    job = ChatJob(
        chat_id=chat_id,
        job_id=next_job_id(db, chat_id),
        kind=JOB_SUBAGENT,
        child_chat_id=child_chat_id,
        description=description[:200],
        agent=agent[:60],
        model=model[:300],
        state=JOB_RUNNING,
        started=_now(),
        data_json={
            "background": bool(background),
            "message_id": message_id,
            "model_id": model_id,
            "connection_id": connection_id,
            "runs": 1,
            "read_rows": 0,
        },
    )
    db.add(job)
    db.commit()
    return job


def by_id(db, chat_id: str, job_id: str):
    """The job this chat calls `job_id`, or None."""
    from sqlalchemy import select

    from lembas.db.models import ChatJob

    return db.scalars(
        select(ChatJob).where(ChatJob.chat_id == chat_id, ChatJob.job_id == job_id)
    ).first()


def for_child(db, child_chat_id: str):
    """The job a helper's chat runs, or None (a synchronous helper's chat has
    a row too, so `report` from either finds its parent)."""
    from sqlalchemy import select

    from lembas.db.models import ChatJob

    return db.scalars(select(ChatJob).where(ChatJob.child_chat_id == child_chat_id)).first()


def for_chat(db, chat_id: str) -> list:
    """This chat's jobs: running ones first, then the finished ones it still
    keeps (`finished_kept`), newest first."""
    from sqlalchemy import select

    from lembas.db.models import JOB_RUNNING, ChatJob

    rows = list(
        db.scalars(
            select(ChatJob).where(ChatJob.chat_id == chat_id).order_by(ChatJob.started.desc())
        )
    )
    running = [j for j in rows if j.state == JOB_RUNNING]
    finished = [j for j in rows if j.state != JOB_RUNNING][:FINISHED_KEPT]
    return running + finished


def running_for(db, chat_id: str) -> list:
    from sqlalchemy import select

    from lembas.db.models import JOB_RUNNING, ChatJob

    return list(
        db.scalars(
            select(ChatJob)
            .where(ChatJob.chat_id == chat_id, ChatJob.state == JOB_RUNNING)
            .order_by(ChatJob.started)
        )
    )


def seconds_run(job) -> int:
    started = _as_utc(job.started) or _now()
    ended = _as_utc(job.ended) or _now()
    return max(0, int((ended - started).total_seconds()))


def _rows(db, child_chat_id: str) -> list:
    from sqlalchemy import select

    from lembas.db.models import Message

    return list(
        db.scalars(
            select(Message)
            .where(Message.chat_id == child_chat_id)
            .order_by(Message.created_at, Message.id)
        )
    )


def _tool_line(event: dict[str, Any]) -> str:
    """One line per tool call, as `job_output` shows a subagent's progress."""
    from lembas.services import tool_blocks, tool_labels

    title = tool_blocks.block_for(event).title or str(event.get("query") or "")
    failed = " (failed)" if str(event.get("status") or "") == "error" else ""
    return f"→ {tool_labels.label_for(event)} {title}".rstrip() + failed


def progress_lines(rows: list) -> list[str]:
    """A helper's rows as the lines the model reads: a tool line per call,
    then what it wrote; a message from the main agent as such."""
    from lembas.db.models import ROLE_ASSISTANT, ROLE_USER

    lines: list[str] = []
    for row in rows:
        if row.role == ROLE_USER:
            if row.machine and lines:
                lines.append("(a message from the main agent was taken in)")
            continue
        if row.role != ROLE_ASSISTANT:
            continue
        for event in row.tool_calls_json or []:
            if isinstance(event, dict) and (line := _tool_line(event)):
                lines.append(line)
        text = (row.content or "").strip()
        if text:
            lines.append(text)
        if row.error:
            lines.append(f"(error: {row.error})")
    return lines


def output(db, job, *, since_rows: int = 0) -> tuple[str, int]:
    """New output of a helper since `since_rows` of its chat's rows were read:
    the text, and how many rows there are now. The last row of a running
    helper is its reply being written and is left for the next read."""
    from itertools import takewhile

    rows = _rows(db, job.child_chat_id or "")
    # An unfinished reply has no content on the row yet; it is read when it is.
    # Rows are taken in order up to the first unfinished one, so nothing is
    # skipped over and counted as read.
    complete = list(takewhile(lambda row: row.complete, rows[since_rows:]))
    return "\n".join(progress_lines(complete)), since_rows + len(complete)


def unread_chars(db, job) -> int:
    text, _ = output(db, job, since_rows=int((job.data_json or {}).get("read_rows") or 0))
    return len(text)


def counts(db, child_chat_id: str) -> tuple[int, int]:
    """(steps, tool calls) of a helper so far: a step per round of each reply."""
    from lembas.db.models import ROLE_ASSISTANT

    steps = tools = 0
    for row in _rows(db, child_chat_id):
        if row.role != ROLE_ASSISTANT:
            continue
        events = [e for e in (row.tool_calls_json or []) if isinstance(e, dict)]
        tools += len([e for e in events if e.get("kind") != "agent" or e.get("name") != "budget"])
        steps += max(1, len(row.steps_json or []) + (1 if (row.content or "").strip() else 0))
    return steps, tools


def transcript(db, job) -> list[dict[str, Any]]:
    """The helper's conversation in the shape `_lembas/job/transcript` gives:
    `{role: "user", text}` and `{role: "assistant", text, tools: [{name,
    title, isError}]}` -- one shape for a device's subagent and this server's."""
    from lembas.db.models import ROLE_ASSISTANT, ROLE_USER
    from lembas.services import tool_blocks

    turns: list[dict[str, Any]] = []
    for row in _rows(db, job.child_chat_id or ""):
        if row.role == ROLE_USER:
            turns.append({"role": "user", "text": row.content or "", "machine": bool(row.machine)})
        elif row.role == ROLE_ASSISTANT:
            tools = []
            for event in row.tool_calls_json or []:
                if not isinstance(event, dict):
                    continue
                tools.append(
                    {
                        "name": str(event.get("name") or "tool"),
                        "title": tool_blocks.block_for(event).title
                        or str(event.get("query") or ""),
                        "isError": event.get("status") == "error",
                    }
                )
            turns.append(
                {
                    "role": "assistant",
                    "text": row.content or "",
                    "tools": tools,
                    "error": row.error or "",
                    "running": not row.complete,
                }
            )
    return turns


def info(db, job) -> dict[str, Any]:
    """`JobInfo`, as `acp.md` shapes it -- the same dict for a helper here and
    a device's job, so the strip and the panel draw either."""
    data = job.data_json or {}
    if "steps" in data:
        # Closed rows keep their counts: a synchronous helper's chat is gone
        # once its answer was handed over, and would count as nothing.
        steps, tools = int(data.get("steps") or 0), int(data.get("tools") or 0)
    else:
        steps, tools = counts(db, job.child_chat_id) if job.child_chat_id else (0, 0)
    started = _as_utc(job.started)
    ended = _as_utc(job.ended)
    return {
        "id": job.job_id,
        "kind": job.kind,
        "state": job.state,
        "description": job.description,
        "agent": job.agent,
        "model": job.model,
        "started": started.isoformat() if started else "",
        "ended": ended.isoformat() if ended else None,
        "seconds": seconds_run(job),
        "unread": unread_chars(db, job) if job.child_chat_id else 0,
        "steps": steps,
        "tools": tools,
        "background": bool(data.get("background", True)),
        "exit": None,
        "command": "",
    }


def device_info(raw: Any) -> dict[str, Any]:
    """A device's `JobInfo` as the templates read one: the spec's fields, each
    bounded -- it came off somebody else's machine."""
    job = raw if isinstance(raw, dict) else {}
    started = str(job.get("started") or "")
    seconds = 0
    try:
        if started:
            begun = datetime.fromisoformat(started.replace("Z", "+00:00"))
            end_raw = str(job.get("ended") or "")
            ended = (
                datetime.fromisoformat(end_raw.replace("Z", "+00:00")) if end_raw else _now()
            )
            seconds = max(0, int((ended - begun).total_seconds()))
    except ValueError:
        seconds = 0
    if not seconds and isinstance(job.get("seconds"), int | float):
        seconds = int(job["seconds"])
    return {
        "id": str(job.get("id") or "")[:32],
        "kind": str(job.get("kind") or KIND_COMMAND)[:16],
        "state": str(job.get("state") or "running")[:16],
        "description": str(job.get("description") or job.get("command") or "")[:200],
        "command": str(job.get("command") or "")[:500],
        "agent": str(job.get("agent") or "")[:60],
        "model": str(job.get("model") or "")[:300],
        "started": started[:40],
        "ended": str(job.get("ended") or "")[:40] or None,
        "seconds": seconds,
        "unread": int(job.get("unread") or 0) if isinstance(job.get("unread"), int) else 0,
        "steps": int(job.get("steps") or 0) if isinstance(job.get("steps"), int) else 0,
        "tools": int(job.get("tools") or 0) if isinstance(job.get("tools"), int) else 0,
        "exit": job.get("exit") if isinstance(job.get("exit"), int) else None,
        "background": True,
    }


def listing(db, jobs: list) -> str:
    """What `job_list` tells the model: one line per job."""
    if not jobs:
        return "No background jobs in this conversation."
    lines = []
    for job in jobs:
        facts = info(db, job)
        what = facts["description"]
        if facts["agent"]:
            what = f"{facts['agent']}: {what}"
        unread = f", {facts['unread']} chars unread" if facts["unread"] else ""
        lines.append(
            f"{facts['id']} · subagent ({what}) · {facts['state']} · "
            f"{duration(facts['seconds'])}{unread}"
        )
    return "\n".join(lines)


__all__ = [
    "FINISHED_KEPT",
    "KIND_COMMAND",
    "KIND_MESSAGE",
    "KIND_SUBAGENT",
    "KIND_TO_SUBAGENT",
    "ENDED",
    "NOTE_TAIL_CHARS",
    "by_id",
    "counts",
    "create",
    "device_info",
    "duration",
    "for_chat",
    "for_child",
    "info",
    "listing",
    "marks",
    "next_job_id",
    "note",
    "output",
    "progress_lines",
    "running_for",
    "seconds_run",
    "state_word",
    "subagent_tail",
    "transcript",
    "unread_chars",
]
