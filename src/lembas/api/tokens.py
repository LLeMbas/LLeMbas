"""Tokens and devices in the browser: a person's own, and the administrator's view.

The cookie door. A person makes a personal token in Settings and sees its value
once, on the page the form returns; revokes their own tokens and devices; and an
administrator sees and can revoke every token on the instance, and reads the
audit log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import partial
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from lembas.api._redirect import back
from lembas.api.deps import AdminUser, Db, RequiredUser
from lembas.db.models import ApiToken, User
from lembas.security import api_tokens, permissions
from lembas.services import audit as audit_service
from lembas.web import i18n
from lembas.web.templating import render

router = APIRouter(tags=["tokens"])

EXPIRY_CHOICES = (0, 30, 90, 365)


_back = partial(back, "/settings")


@router.post("/api/tokens")
async def create_token(
    request: Request,
    db: Db,
    user: RequiredUser,
) -> Response:
    if not permissions.has(db, user, "api.use"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, i18n.t("You may not make API tokens."))
    form = await request.form()
    name = str(form.get("name") or "").strip()
    scopes = [str(s) for s in form.getlist("scopes")]
    try:
        days = int(str(form.get("expires_days") or "0"))
    except ValueError:
        days = 0
    expires = datetime.now(UTC) + timedelta(days=days) if days in EXPIRY_CHOICES and days else None
    token, raw = api_tokens.create(db, user, name=name, scopes=scopes, expires_at=expires)
    if not token.scopes_json:
        db.delete(token)
        db.commit()
        return _back(i18n.t("Choose at least one thing the token may do."), ok=False)
    audit_service.record(
        "token.create",
        user=user,
        request=request,
        target_kind="token",
        target_id=token.id,
        summary=f"made the token {token.name!r} ({token.prefix}…)",
        detail={"scopes": token.scopes_json, "expires": expires.isoformat() if expires else None},
    )
    # Rendered, not redirected: the value must not travel in a URL, and this is
    # the only time it exists outside the program that will hold it.
    return render(
        request,
        "token_created.html",
        {"token": token, "raw": raw, "scope_label": api_tokens.scope_label},
    )


def _own(db, user: User, token_id: str) -> ApiToken:
    token = db.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("There is no such token."))
    return token


async def _drop_link(token_id: str) -> None:
    """A revoked device token ends its live link at once, with the code
    the device reads as "stop dialling"."""
    import contextlib

    from lembas.services import device_link

    link = device_link.link_for(token_id)
    if link is not None:
        device_link.unregister(link)
        with contextlib.suppress(Exception):
            await link.close(device_link.CLOSE_REFUSED, "revoked")


@router.post("/api/tokens/{token_id}/revoke")
async def revoke_own(request: Request, db: Db, user: RequiredUser, token_id: str) -> Response:
    token = _own(db, user, token_id)
    token_id, summary = token.id, f"revoked {token.kind} token {token.name!r} ({token.prefix}…)"
    api_tokens.revoke(db, token)
    await _drop_link(token_id)
    audit_service.record(
        "token.revoke",
        user=user,
        request=request,
        target_kind="token",
        target_id=token_id,
        summary=summary,
    )
    return _back(i18n.t("Revoked. Anything using it is signed out now."))


# --- Administration -------------------------------------------------------------
# No list of every token on the instance. Each account's tokens and
# devices are on its own page (Admin → Users → {user}), revoked from there.
@router.post("/admin/tokens/{token_id}/revoke")
async def admin_revoke(request: Request, db: Db, user: AdminUser, token_id: str) -> Response:
    token = db.get(ApiToken, token_id)
    if token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("There is no such token."))
    owner = db.get(User, token.user_id)
    owner_id = token.user_id
    summary = (
        f"revoked {owner.email if owner else 'a deleted account'}'s token "
        f"{token.name!r} ({token.prefix}…)"
    )
    api_tokens.revoke(db, token)
    await _drop_link(token_id)
    audit_service.record(
        "admin.token.revoke",
        user=user,
        request=request,
        target_kind="token",
        target_id=token_id,
        summary=summary,
    )
    return RedirectResponse(
        f"/admin/users/{owner_id}?saved={quote(i18n.t('Revoked.'))}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _may_read_audit(db, user: User) -> bool:
    return user.is_admin or permissions.has(db, user, "audit.read")


def _since(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except ValueError:
        return None


PAGE_SIZES = (50, 100, 250)


def _page_window(page: int, pages: int, around: int = 2) -> list[int | None]:
    """The page numbers to offer: the first, the last, and a few either side of
    this one; None where a run is left out."""
    keep = {1, pages, *range(max(1, page - around), min(pages, page + around) + 1)}
    out: list[int | None] = []
    for n in sorted(keep):
        if out and out[-1] is not None and n - out[-1] > 1:
            out.append(None)
        out.append(n)
    return out


@router.get("/admin/audit")
async def admin_audit(
    request: Request,
    db: Db,
    user: RequiredUser,
    action: str = "",
    actor: str = "",
    since: str = "",
    until: str = "",
    page: int = 1,
    size: int = 100,
):
    if not _may_read_audit(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, i18n.t("You may not read the audit log."))
    # Numbered pages over a counted total: "Newer / Older" alone gives
    # no idea how far back the log goes, and no way to get there.
    size = size if size in PAGE_SIZES else PAGE_SIZES[1]
    filters = {
        "action": action.strip(),
        "actor": actor.strip(),
        "since": _since(since),
        "until": _since(until),
    }
    total = audit_service.count(db, **filters)
    pages = max(1, -(-total // size))
    page = min(max(1, page), pages)
    events = audit_service.query(db, **filters, limit=size, offset=(page - 1) * size)
    query = (
        f"action={quote(action)}&actor={quote(actor)}&since={quote(since)}"
        f"&until={quote(until)}&size={size}"
    )
    return render(
        request,
        "admin/audit.html",
        {
            "events": events,
            "page": page,
            "pages": pages,
            "total": total,
            "size": size,
            "sizes": PAGE_SIZES,
            "window": _page_window(page, pages),
            "action": action,
            "actor": actor,
            "since": since,
            "until": until,
            "query": query,
            "retention": audit_service.retention_days(db),
        },
    )


@router.get("/admin/audit.csv")
async def admin_audit_csv(
    db: Db,
    user: RequiredUser,
    action: str = "",
    actor: str = "",
    since: str = "",
    until: str = "",
) -> Response:
    if not _may_read_audit(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, i18n.t("You may not read the audit log."))
    events = audit_service.query(
        db,
        action=action.strip(),
        actor=actor.strip(),
        since=_since(since),
        until=_since(until),
        limit=5000,
    )
    return Response(
        audit_service.to_csv(events),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )


__all__ = ["router"]
