"""`report_write`, `report_search` and `report_get`."""

from __future__ import annotations

from typing import Any

from lembas.db.models import (
    SOURCE_CHAT,
    User,
)
from lembas.db.session import session_scope
from lembas.services import reports as reports_service
from lembas.services.tools.core import (
    _STRING,
    FAMILY_REPORT,
    RISK_WRITE,
    ToolContext,
    ToolDef,
    ToolOutcome,
    _object,
)
from lembas.services.tools.query_vector import _query_vector


# --- Reports -----------------------------------------------------------------
async def _run_report_write(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    title = str(args.get("title") or "").strip()
    body = str(args.get("body") or "").strip()
    summary = str(args.get("summary") or "").strip()
    if not body:
        return ToolOutcome(
            "A report needs a body. Write what you found, not a note saying you found it.",
            {"name": "report_write", "status": "error", "error": "Empty body."},
        )
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        if user is None:
            return ToolOutcome(
                "That report could not be filed.",
                {"name": "report_write", "status": "error", "error": "No such owner."},
            )
        report = reports_service.create(
            db,
            owner=user,
            title=title,
            body=body,
            summary=summary,
            source=SOURCE_CHAT,
            source_id=context.chat_id or "",
            model_id=context.model_id or "",
            group=context.data_group,
        )
        return ToolOutcome(
            f"Filed report {report.id} — {report.title!r}. "
            "The reader will find it under Reports; they cannot reply to it there.",
            {
                "name": "report_write",
                "query": report.title,
                "status": "ok",
                "results": [{"title": report.title, "id": report.id}],
            },
        )


async def _run_report_search(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    query = str(args.get("query") or "").strip()
    vector = await _query_vector(query)
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        found = (
            reports_service.search(
                db, user, query, limit=8, vector=vector, group=context.data_group
            )
            if query
            else reports_service.recent(db, user, limit=8, group=context.data_group)
        )
        event = {
            "name": "report_search",
            "query": query,
            "status": "ok",
            "results": [{"title": r.title, "id": r.id} for r in found],
        }
        if not found:
            return ToolOutcome("There are no reports matching that.", event)
        lines = ["Reports:"]
        for report in found:
            when = report.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"\n[{report.id}] {when} — {report.title}\n{reports_service.snippet(report)}"
            )
        lines.append("\nUse report_get with an id to read one in full.")
        return ToolOutcome("\n".join(lines), event)


async def _run_report_get(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        report = reports_service.get(db, str(args.get("id") or ""), user, context.data_group)
        if report is None:
            return ToolOutcome(
                "There is no such report.",
                {"name": "report_get", "status": "error", "error": "Not found."},
            )
        when = report.created_at.strftime("%Y-%m-%d %H:%M")
        return ToolOutcome(
            f"{report.title}\nFiled {when}\n\n{report.body}",
            {
                "name": "report_get",
                "query": report.title,
                "status": "ok",
                "results": [{"title": report.title, "id": report.id}],
            },
        )


TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="report_write",
        family=FAMILY_REPORT,
        description=(
            "File a report: a finished piece of work, written for the person "
            "to read later. Use this when you have been asked for one, and "
            "when you finish a long piece of work whose result is worth "
            "keeping — an investigation, a summary of what you found, an "
            "account of what you changed. A report is read on its own, away "
            "from this conversation and possibly long after it, and THE "
            "READER CANNOT REPLY TO IT. So write it whole: say what you were "
            "asked, what you found and what you conclude, and do not refer "
            "to 'the above' or ask a question at the end."
        ),
        parameters=_object(
            {
                "title": {
                    **_STRING,
                    "description": (
                        "One line naming what this is about, as it will appear "
                        "in a list of dozens. 'Build failures this week', not "
                        "'Report' or 'Results'."
                    ),
                },
                "body": {
                    **_STRING,
                    "description": (
                        "The report itself, in Markdown. Headings and lists are "
                        "rendered. This is the whole of what the reader gets, so "
                        "it should stand on its own with no further context."
                    ),
                },
                "summary": {
                    **_STRING,
                    "description": (
                        "One sentence for the list page, so the report can be "
                        "triaged without opening it. Say the finding, not the "
                        "subject: 'Three tests fail on ARM only', not 'About the "
                        "test failures'. Omit it and the first line of the body "
                        "is used instead."
                    ),
                },
            },
            ["title", "body"],
        ),
        run=_run_report_write,
        risk=RISK_WRITE,
    ),
    ToolDef(
        name="report_search",
        family=FAMILY_REPORT,
        description=(
            "Search reports filed earlier, yours and the reader's. With no "
            "query, returns the most recent. Worth doing before writing a "
            "recurring report, so this week's can say what changed since last "
            "week's rather than repeating it."
        ),
        parameters=_object({"query": _STRING}, []),
        run=_run_report_search,
    ),
    ToolDef(
        name="report_get",
        family=FAMILY_REPORT,
        description="Read one report in full, by the id a search returned.",
        parameters=_object({"id": _STRING}, ["id"]),
        run=_run_report_get,
    ),
)
