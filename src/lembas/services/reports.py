"""Reports: filing a finished piece of work, and finding it again.

A report is written and read; it is never answered. That is the whole shape of
the thing, and it is why this store is deliberately thinner than
`services/library/`: there is no sharing, because a report is a record of what
somebody's own model did on their behalf, and no revisions, because a report
describes a moment rather than a document being worked on.

`sharing.visible_to` is therefore absent on purpose rather than forgotten. If
reports ever become shareable, `RESOURCE_TYPES` is where that starts, and every
listing here has to go through the helper -- six independently written
permission checks is how one of them ends up written slightly differently.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import CHUNK_REPORT, DEFAULT_GROUP, SOURCE_MANUAL, SOURCES, Report, User
from lembas.services import sharing
from lembas.services.library import retrieval

log = logging.getLogger(__name__)

INDEX = "reports_fts"

MAX_TITLE_CHARS = 300
MAX_SUMMARY_CHARS = 500
MAX_BODY_CHARS = 60_000
SNIPPET_CHARS = 400


def visible(user: User | None, group: str | None = None):
    """Every report this person owns or has been shared.

    Takes no session because it builds a query rather than running one, and
    takes `None` to mean nobody so an unauthenticated caller gets an empty
    result instead of an exception.

    It said "a later move to shared reports is a change of one line here", and
    it was: `sharing.visible_to` is that line. Every listing, search and detail
    page went through this already, which is what made the move safe.

    `group` narrows to one data group, and is what a model's tools pass.
    """
    return select(Report).where(sharing.visible_to(Report, user, group))


def get(
    db: DBSession, report_id: str, user: User | None, group: str | None = None
) -> Report | None:
    report = db.get(Report, report_id)
    if report is None or not sharing.can_read(db, report, user, group):
        return None
    return report


def owned(db: DBSession, report_id: str, user: User | None) -> Report | None:
    """The same, but only when they own it.

    Sharing grants **reading**, so deleting and marking-as-read are the owner's
    alone. Two functions rather than a flag, because a route that wants one and
    calls the other is a bug you can see in the name.
    """
    report = db.get(Report, report_id)
    if report is None or not sharing.can_write(report, user):
        return None
    return report


def recent(
    db: DBSession, user: User | None, *, limit: int = 20, group: str | None = None
) -> list[Report]:
    return list(
        db.scalars(visible(user, group).order_by(Report.created_at.desc()).limit(limit))
    )


def search(
    db: DBSession,
    user: User | None,
    needle: str,
    *,
    limit: int = 20,
    vector: list[float] | None = None,
    group: str | None = None,
) -> list[Report]:
    """Reports matching `needle`, best match first.

    Ids come back from FTS and the rows are re-ordered by hit position, exactly
    as the library stores do -- the index knows about ranking and the ORM query
    knows about ownership, and neither is asked to do the other's job.

    `vector` is the query already embedded, or None. It comes from the caller
    rather than being worked out here because this is synchronous and embedding
    is an HTTP request -- see `services/library/retrieval.py`. None means the
    keyword search exactly as it always was.
    """
    hits = retrieval.search(db, INDEX, needle, kind=CHUNK_REPORT, vector=vector, limit=limit * 4)
    if not hits:
        return []
    order = {hit.id: position for position, hit in enumerate(hits)}
    rows = list(db.scalars(visible(user, group).where(Report.id.in_(list(order)))))
    rows.sort(key=lambda report: order.get(report.id, len(order)))
    return rows[:limit]


def unread_count(db: DBSession, user: User | None) -> int:
    if user is None:
        return 0
    return int(
        db.scalar(
            select(func.count()).select_from(Report).where(
                Report.owner_id == user.id, Report.unread.is_(True)
            )
        )
        or 0
    )


def unannounced(db: DBSession, user: User | None) -> list[Report]:
    """Reports that have arrived and have not been announced yet.

    Separate from `unread_count`, which drives the dot: the dot may be shown for
    as long as something is unread, while an announcement fires once. Reading
    them apart is what stops the poll interrupting somebody every ten seconds
    with the same report until they open it.

    Ordered oldest first, so several arriving between two ticks are announced in
    the order they were filed.
    """
    if user is None:
        return []
    return list(
        db.scalars(
            select(Report)
            .where(
                Report.owner_id == user.id,
                Report.unread.is_(True),
                Report.unread_notified.is_(False),
            )
            .order_by(Report.created_at)
        )
    )


def _first_line(body: str) -> str:
    """A summary for a model that did not write one.

    Markdown headings are stripped rather than shown: a list of reports all
    beginning "# " reads as a bug, and the heading is nearly always the title
    again.
    """
    for line in (body or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:MAX_SUMMARY_CHARS]
    return ""


def create(
    db: DBSession,
    *,
    owner: User,
    title: str,
    body: str,
    summary: str = "",
    source: str = SOURCE_MANUAL,
    source_id: str = "",
    schedule_id: str = "",
    model_id: str = "",
    error: str = "",
    unread: bool = True,
    group: str = DEFAULT_GROUP,
) -> Report:
    """File a report.

    Trimming happens here rather than at the column so an over-long write from
    a tool is filed short with everything else intact, instead of failing the
    turn -- the rule `memories` already follows.

    `unread` defaults to True because every caller that matters is something
    that happened without the reader present. A report somebody typed themselves
    passes False.
    """
    report = Report(
        owner_id=owner.id,
        data_group_id=group or DEFAULT_GROUP,
        title=(title.strip() or "Untitled report")[:MAX_TITLE_CHARS],
        summary=(summary.strip() or _first_line(body))[:MAX_SUMMARY_CHARS],
        body=(body or "").strip()[:MAX_BODY_CHARS],
        source=source if source in SOURCES else SOURCE_MANUAL,
        source_id=source_id or "",
        schedule_id=schedule_id or "",
        model_id=model_id or "",
        error=error or "",
        unread=unread,
    )
    db.add(report)
    db.commit()

    # Here rather than at the scheduled-run site, because a report is filed from
    # two places -- a schedule delivering one, and a model calling `report_write`
    # in a chat nobody stayed on -- and both are arrivals somebody would want to
    # know about. `unread` is what says it is news; a report filed with it off
    # was made by the person reading this screen.
    if report.unread:
        from lembas.services import push as push_service

        push_service.announce_later(
            report.owner_id,
            title=report.title or "Report filed",
            body=report.summary or "A report is waiting for you.",
            url=f"/library/reports/{report.id}",
            kind="report",
        )
    return report


def mark_read(db: DBSession, report: Report) -> Report:
    if report.unread:
        report.unread = False
        db.commit()
    return report


def delete(db: DBSession, report: Report) -> None:
    # Shares carry no foreign key to their resource, so nothing cascades and
    # this has to be said. A grant left behind names a report that has gone --
    # harmless now and a grant to whoever next holds that id later.
    sharing.forget_resource(db, report)
    db.delete(report)
    db.commit()


def snippet(report: Report) -> str:
    text = (report.summary or report.body or "").strip()
    if len(text) <= SNIPPET_CHARS:
        return text
    return text[:SNIPPET_CHARS].rstrip() + "…"
