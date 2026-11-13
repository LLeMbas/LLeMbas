"""Reports: what a schedule's run leaves for its owner (harness spec 3.8.0,
`reports.md`).

Two halves. The **rules** are pure -- notify-when and its instruction, where
`report_write` is offered, what a run leaves from its calls and its final
answer -- and are what `conformance/reports.json` drives
(`tests/test_harness_spec.py`). The **store** files, lists, reads and deletes
the rows, and continues a report as a conversation that stays with it.

A report is its owner's and nobody else's: every read here is by owner id,
and somebody else's report is as absent as one that does not exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    KIND_REPORT,
    ROLE_ASSISTANT,
    ROLE_USER,
    Chat,
    Message,
    Report,
    Schedule,
    ScheduleRun,
    User,
)
from lembas.db.models.report import IMPORTANCE_HIGH, IMPORTANCE_NORMAL
from lembas.services import harness_spec, html_pages

ALWAYS, WHEN, NEVER = "always", "when", "never"
# What `when` was called before spec 3.8.0; read as `when` wherever met.
_LEGACY = {"matters": WHEN}


def spec() -> dict[str, Any]:
    return harness_spec.loop()["scheduled"]["notify"]


# --- Notify when ---------------------------------------------------------------------
def notify_value(value: str | None) -> str:
    """A schedule's notify-when as one of the spec's values: the default
    (`when`) for one that says nothing or something unknown."""
    value = _LEGACY.get(str(value or ""), str(value or ""))
    return value if value in spec()["values"] else spec()["default"]


def condition(text: str | None) -> str:
    """`when`'s condition as it is put into the instruction: the person's
    words trimmed, on one line, without a full stop at the end -- the spec's
    default where they wrote none."""
    said = " ".join(str(text or "").split()).rstrip(".").strip()
    return said or spec()["default_condition"]


def instruction(value: str | None, when: str | None = "") -> str:
    """The sentence a run's prompt carries about reporting ("" for `never`)."""
    value = notify_value(value)
    text = spec()["texts"].get(value, "")
    return text.replace("{{condition}}", condition(when)) if text else ""


def tool_state(*, session: str, notify: str | None = None, turn: str = "run",
               subagent: bool = False) -> str:
    """`offered`, `unavailable` or `absent` for `report_write` (reports.md §2):
    in the list only in a session a schedule opened for its run, when its
    notify-when is not `never`, and never for a subagent; a turn there that is
    the person's own finds it in the list and answering its `unavailable`."""
    if session != "scheduled" or subagent or notify_value(notify) == NEVER:
        return "absent"
    return "offered" if turn == "run" else "unavailable"


def result_text(calls: int) -> str:
    """What the tool answers: the first call files, a later one replaces."""
    return spec()["result"]["filed" if calls <= 1 else "replaced"]


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _without_sentinel(answer: str) -> str:
    """The answer without the retired `[SILENT]` on its first or last line."""
    sentinel = str(harness_spec.loop()["scheduled"].get("sentinel") or "[SILENT]").upper()
    lines = (answer or "").strip().splitlines()

    def marks(line: str) -> bool:
        return line.strip().rstrip(".").strip().upper() == sentinel

    while lines and (marks(lines[0]) or not lines[0].strip()):
        lines.pop(0)
    while lines and (marks(lines[-1]) or not lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


def note_of(answer: str) -> str:
    """The run's line in its schedule's history: the final answer's first line
    that is not blank, cut; the spec's `no_reply` where there is none."""
    # A page in the answer reads as its one line, `[page: title]`.
    for line in html_pages.project(_without_sentinel(answer)).splitlines():
        if line.strip():
            return _cut(line.strip(), int(spec()["note_max"]))
    return harness_spec.loop()["scheduled"]["texts"]["no_reply"]


# A report is something a person reads. Past this its body is cut -- whoever
# wrote it: a model here, a linked computer's, or an answer filed as one.
BODY_MAX = 256 * 1024
CUT_NOTE = "\n\n[This report was cut here: it was longer than 256 KiB.]"


def bounded(body: str) -> str:
    """A report's body, cut at `BODY_MAX` bytes with a line saying so."""
    raw = body.encode("utf-8")
    if len(raw) <= BODY_MAX:
        return body
    return raw[:BODY_MAX].decode("utf-8", "ignore").rstrip() + CUT_NOTE


def clean_call(call: dict[str, Any]) -> dict[str, str] | None:
    """One `report_write` call as a report, or None when it lacks a title or
    a body."""
    title = " ".join(str(call.get("title") or "").split())
    body = bounded(str(call.get("body") or "").strip())
    if not title or not body:
        return None
    importance = str(call.get("importance") or IMPORTANCE_NORMAL)
    return {
        "title": title[:300],
        "body": body,
        "summary": " ".join(str(call.get("summary") or "").split())[:500],
        "importance": importance if importance == IMPORTANCE_HIGH else IMPORTANCE_NORMAL,
    }


def outcome(
    *, notify: str | None, title: str, calls: list[dict[str, Any]], answer: str
) -> tuple[dict[str, str] | None, str]:
    """What a run leaves (reports.md §4): `(report or None, note)`.

    The last report written, whatever the notify-when; under `always` with
    none written, the final answer filed as the report (unless it is empty);
    otherwise none. The note is the answer's first line either way."""
    written = [found for found in (clean_call(c) for c in calls or []) if found]
    said = _without_sentinel(answer)
    note = note_of(answer)
    if written:
        return written[-1], note
    if notify_value(notify) == ALWAYS and said:
        first = " ".join(html_pages.project(said).split("\n\n")[0].split())
        return (
            {
                "title": " ".join((title or "").split())[:300] or "Scheduled run",
                "body": bounded(said),
                "summary": _cut(first, int(spec()["summary_max"])),
                "importance": IMPORTANCE_NORMAL,
            },
            note,
        )
    return None, note


# --- The store -------------------------------------------------------------------------
def file(
    db: DBSession,
    *,
    owner_id: str,
    report: dict[str, str],
    schedule: Schedule | None = None,
    run: ScheduleRun | None = None,
    model_id: str = "",
) -> Report:
    """File one report. A run has one: filing again for the same run replaces
    what it filed before (and it is unread again). The caller commits."""
    row = None
    if run is not None and run.report_id:
        row = db.get(Report, run.report_id)
        if row is not None and row.owner_id != owner_id:
            row = None
    if row is None:
        row = Report(owner_id=owner_id)
        db.add(row)
    row.title = report["title"]
    row.summary = report.get("summary", "")
    row.body = report["body"]
    row.importance = report.get("importance") or IMPORTANCE_NORMAL
    row.schedule_id = schedule.id if schedule is not None else ""
    row.schedule_title = (schedule.title or "") if schedule is not None else ""
    row.run_id = run.id if run is not None else ""
    row.model_id = model_id or ""
    row.unread = True
    row.notified = False
    row.page_told = False
    row.html_pages = _pages_offered(db, run)
    db.flush()
    if run is not None:
        run.report_id = row.id
    return row


def _pages_offered(db: DBSession, run: ScheduleRun | None) -> bool:
    """Whether the run that wrote a report was offered pages: the instance's
    switch and its model's flag, in a run on this server (pages.md §4). A run
    on a linked computer is an agent chat and never is."""
    chat = db.get(Chat, run.chat_id) if run is not None and run.chat_id else None
    if chat is None:
        return False
    from lembas.services import chat as chat_service

    return html_pages.on(db, chat, chat_service.model_for(db, chat))


def owned(db: DBSession, user: User, report_id: str) -> Report | None:
    row = db.get(Report, report_id) if report_id else None
    return row if row is not None and row.owner_id == user.id else None


def listing(
    db: DBSession, user: User, *, unread: bool | None = None, schedule_id: str = "",
    limit: int = 100,
) -> list[Report]:
    """A person's reports: unread first, then newest first."""
    query = select(Report).where(Report.owner_id == user.id)
    if unread is not None:
        query = query.where(Report.unread.is_(unread))
    if schedule_id:
        query = query.where(Report.schedule_id == schedule_id)
    query = query.order_by(Report.unread.desc(), Report.created_at.desc(), Report.id)
    return list(db.scalars(query.limit(max(1, min(int(limit or 100), 500)))))


def unread_count(db: DBSession, user_id: str) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(Report).where(
                Report.owner_id == user_id, Report.unread.is_(True)
            )
        )
        or 0
    )


def mark_read(db: DBSession, row: Report) -> None:
    if row.unread:
        row.unread = False
        db.commit()


def delete(db: DBSession, row: Report) -> None:
    """Delete a report, and the conversation it continued in with it: that
    chat exists only as the report's."""
    chat = db.get(Chat, row.chat_id) if row.chat_id else None
    if chat is not None and chat.kind == KIND_REPORT and chat.user_id == row.owner_id:
        from lembas.services import chat as chat_service

        chat_service.delete_chats(db, [chat])
    for run in db.scalars(select(ScheduleRun).where(ScheduleRun.report_id == row.id)):
        run.report_id = ""
    db.delete(row)
    db.commit()


def schedules_named(db: DBSession, user: User) -> list[tuple[str, str]]:
    """(schedule id, title) for every schedule the person's reports name, for
    the list's filter -- the title as filed where the schedule is gone."""
    rows = db.execute(
        select(Report.schedule_id, func.max(Report.schedule_title))
        .where(Report.owner_id == user.id, Report.schedule_id != "")
        .group_by(Report.schedule_id)
    ).all()
    live = {
        s.id: s.title
        for s in db.scalars(select(Schedule).where(Schedule.user_id == user.id))
    }
    return sorted(
        ((sid, live.get(sid) or title or "") for sid, title in rows), key=lambda r: r[1].lower()
    )


def schedule_exists(db: DBSession, user: User, schedule_id: str) -> bool:
    row = db.get(Schedule, schedule_id) if schedule_id else None
    return row is not None and row.user_id == user.id


def reply_chat(db: DBSession, user: User, row: Report) -> Chat:
    """The conversation a report continues in, made once: a chat of its own
    kind seeded with the report as the first thing said. It is not a chat of
    the lists -- not in the sidebar, the unread dots, search or the apps'
    chats -- and is reached from its report."""
    from lembas.services import chat as chat_service

    existing = db.get(Chat, row.chat_id) if row.chat_id else None
    if existing is not None and existing.user_id == user.id:
        return existing
    start = chat_service.default_model(db, user)
    model_id, connection_id = (start[0], start[1]) if start else ("", None)
    run = db.get(ScheduleRun, row.run_id) if row.run_id else None
    if run is not None and run.model_id:
        from lembas.db.models import Model

        known = db.scalar(select(Model).where(Model.model_id == run.model_id))
        if known is not None and any(
            m.model_id == known.model_id for m in chat_service.available_models(db, user)
        ):
            model_id, connection_id = known.model_id, known.connection_id
    chat = Chat(
        user_id=user.id,
        kind=KIND_REPORT,
        title=row.title[:300],
        title_generated=True,
        model_id=model_id or "",
        connection_id=connection_id,
    )
    db.add(chat)
    db.flush()
    # The report is the first thing *said*. Before it goes one line of ours,
    # as a machine's turn: a history that opens with an assistant message is
    # refused by every chat template that insists on user-first alternation.
    source = f' filed by the scheduled task "{row.schedule_title}"' if row.schedule_title else ""
    now = datetime.now(UTC)
    db.add(Message(
        chat_id=chat.id, role=ROLE_USER, machine=True, complete=True, created_at=now,
        content=f"[report] This conversation continues a report{source}. The report follows.",
    ))
    opening = f"# {row.title}\n\n{row.body}" if row.title else row.body
    db.add(Message(chat_id=chat.id, role=ROLE_ASSISTANT, content=opening, complete=True,
                   html_pages=bool(row.html_pages),
                   model_id=row.model_id or "", created_at=now + timedelta(milliseconds=1)))
    row.chat_id = chat.id
    db.commit()
    return chat


def of_chat(db: DBSession, chat_id: str) -> Report | None:
    """The report a chat continues, if it is one's."""
    return db.scalar(select(Report).where(Report.chat_id == chat_id)) if chat_id else None


__all__ = [
    "ALWAYS",
    "NEVER",
    "WHEN",
    "clean_call",
    "condition",
    "delete",
    "file",
    "instruction",
    "listing",
    "mark_read",
    "note_of",
    "notify_value",
    "of_chat",
    "outcome",
    "owned",
    "reply_chat",
    "result_text",
    "schedule_exists",
    "schedules_named",
    "tool_state",
    "unread_count",
]
