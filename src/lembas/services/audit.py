"""The audit log: who did what, when, and from where.

`record()` never raises -- the same contract as `usage.record` -- because an
audit row that could not be written must not turn a sign-in or a deleted account
into an error. It writes in its **own** session and commits it, so a caller that
then rolls back (a refused action) still leaves the attempt on record, and a
caller that never commits does not lose it either.

What is recorded, by action prefix:

- `auth.*` -- sign-in, a failed sign-in, sign-out, sign-up;
- `token.*` and `device.*` -- a token made or revoked, a device login asked for,
  approved, denied or handed its token;
- `admin.*` -- every change an administrator makes;
- `reset.*` -- a person starting over;
- `update.*` -- an update checked for or applied;
- `system.*` -- sweeps, as counts only.

Never the content of anything: a chat, a note, a prompt or a password does not
belong in a log somebody else may read. Summaries name things; they do not quote
them.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    ACTOR_ANONYMOUS,
    ACTOR_SYSTEM,
    ACTOR_TOKEN,
    ACTOR_USER,
    ApiToken,
    AuditEvent,
    User,
)

log = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 365


def _client(request) -> tuple[str, str]:
    if request is None:
        return "", ""
    ip = request.client.host if getattr(request, "client", None) else ""
    agent = request.headers.get("user-agent", "") if hasattr(request, "headers") else ""
    return ip[:45], agent[:300]


def record(
    action: str,
    *,
    user: User | None = None,
    token: ApiToken | None = None,
    request=None,
    target_kind: str = "",
    target_id: str = "",
    summary: str = "",
    detail: dict[str, Any] | None = None,
    actor_label: str = "",
    system: bool = False,
) -> None:
    """Write one event. Best-effort, and never raises."""
    try:
        from lembas.db.session import session_scope

        ip, agent = _client(request)
        if system:
            kind = ACTOR_SYSTEM
        elif token is not None:
            kind = ACTOR_TOKEN
        elif user is not None:
            kind = ACTOR_USER
        else:
            kind = ACTOR_ANONYMOUS
        label = actor_label or (user.email if user is not None else "")
        with session_scope() as db:
            db.add(
                AuditEvent(
                    at=datetime.now(UTC),
                    actor_user_id=user.id if user is not None else None,
                    actor_token_id=token.id if token is not None else None,
                    actor_kind=kind,
                    actor_label=label[:200],
                    action=action[:64],
                    target_kind=target_kind[:32],
                    target_id=str(target_id)[:64],
                    summary=summary[:1000],
                    detail_json=dict(detail or {}),
                    ip=ip,
                    user_agent=agent,
                )
            )
            db.commit()
    except Exception:  # the log must never break what it records
        log.warning("could not write audit event %s", action, exc_info=True)


def _filtered(statement, *, action: str, actor: str, since, until):
    if action:
        statement = statement.where(
            (AuditEvent.action == action) | AuditEvent.action.startswith(action.rstrip(".") + ".")
        )
    if actor:
        statement = statement.where(
            (AuditEvent.actor_user_id == actor) | AuditEvent.actor_label.contains(actor)
        )
    if since is not None:
        statement = statement.where(AuditEvent.at >= since)
    if until is not None:
        statement = statement.where(AuditEvent.at < until)
    return statement


def query(
    db: DBSession,
    *,
    action: str = "",
    actor: str = "",
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[AuditEvent]:
    """Newest first. `action` matches a prefix ("auth" finds every auth.*);
    `actor` matches the label or the user id."""
    statement = _filtered(select(AuditEvent), action=action, actor=actor, since=since, until=until)
    statement = statement.order_by(AuditEvent.at.desc()).limit(max(1, min(limit, 5000)))
    return list(db.scalars(statement.offset(max(0, offset))))


def count(
    db: DBSession,
    *,
    action: str = "",
    actor: str = "",
    since: datetime | None = None,
    until: datetime | None = None,
) -> int:
    """How many events the same filters match, for the pager."""
    from sqlalchemy import func

    statement = _filtered(
        select(func.count()).select_from(AuditEvent),
        action=action,
        actor=actor,
        since=since,
        until=until,
    )
    return int(db.scalar(statement) or 0)


FIELDS = (
    "at",
    "action",
    "actor_kind",
    "actor_label",
    "actor_user_id",
    "actor_token_id",
    "target_kind",
    "target_id",
    "summary",
    "ip",
    "user_agent",
)


def as_dict(event: AuditEvent) -> dict[str, Any]:
    out = {name: getattr(event, name) for name in FIELDS}
    at = event.at if event.at.tzinfo else event.at.replace(tzinfo=UTC)
    out["at"] = at.isoformat()
    out["id"] = event.id
    out["detail"] = event.detail_json or {}
    return out


def to_csv(events: Iterable[AuditEvent]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(FIELDS)
    for event in events:
        row = as_dict(event)
        writer.writerow([_cell(row[name]) for name in FIELDS])
    return buffer.getvalue()


def _cell(value: Any) -> str:
    """A spreadsheet reads a leading `=`, `+`, `-` or `@` as a formula, and an
    audit export is exactly the file somebody opens in one. Quoted out."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def retention_days(db: DBSession) -> int:
    from lembas.services import settings_store

    try:
        days = int(settings_store.get(db, "audit_retention_days"))
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION_DAYS
    return max(0, days)


def sweep(db: DBSession, *, now: datetime | None = None) -> int:
    """Drop events older than the retention setting; 0 keeps them for ever."""
    days = retention_days(db)
    if not days:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    removed = db.execute(delete(AuditEvent).where(AuditEvent.at < cutoff)).rowcount or 0
    if removed:
        db.commit()
        log.info("dropped %d audit event(s) older than %d days", removed, days)
    return int(removed)


class AdminAuditMiddleware:
    """Records every change made in the administration area.

    One place rather than a call in each of the twenty `api/admin_*.py` modules,
    which is the shape that drifts: an admin route added next year would be
    unaudited unless somebody remembered. Any state-changing request under
    `/admin/` that succeeded (below 400) is an `admin.change` event naming the
    method and the path -- what changed is the path's own business, and the
    form's values are not copied, because some of them are keys.

    Pure ASGI rather than `BaseHTTPMiddleware`, so a streamed response passes
    through untouched.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") not in ("POST", "PUT", "PATCH", "DELETE")
            or not str(scope.get("path", "")).startswith("/admin/")
        ):
            await self.app(scope, receive, send)
            return
        status_box: dict[str, int] = {}

        async def watch(message) -> None:
            if message.get("type") == "http.response.start":
                status_box["status"] = int(message.get("status", 0))
            await send(message)

        await self.app(scope, receive, watch)
        if status_box.get("status", 500) >= 400:
            return
        try:
            from starlette.requests import Request

            from lembas.db.session import session_scope
            from lembas.security.sessions import COOKIE_NAME, resolve_session

            request = Request(scope)
            path = request.url.path
            # The token and audit pages write their own, more specific event.
            if path.startswith("/admin/tokens/"):
                return
            with session_scope() as db:
                user = resolve_session(db, request.cookies.get(COOKIE_NAME))
                if user is not None:
                    db.expunge(user)
            record(
                "admin.change",
                user=user,
                request=request,
                target_kind="path",
                target_id=path[:64],
                summary=f"{request.method} {path}",
            )
        except Exception:  # the log must never break what it records
            log.warning("could not audit an admin change", exc_info=True)


__all__ = [
    "AdminAuditMiddleware",
    "DEFAULT_RETENTION_DAYS",
    "FIELDS",
    "as_dict",
    "query",
    "record",
    "retention_days",
    "sweep",
    "to_csv",
]
