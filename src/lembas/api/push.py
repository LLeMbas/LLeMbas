"""Registering a browser for notifications, and letting it go again.

Three routes and no cleverness. The interesting half is `services/push.py`;
this is the part a browser talks to.

Ownership is the whole authorisation, as everywhere a person's own things are
handled here: a subscription belongs to whoever was signed in when it was made,
and nothing else can reach it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select

from lembas.api.deps import Db, RequiredUser
from lembas.db.models import PushSubscription
from lembas.services import fetch as fetch_service
from lembas.services import push as push_service
from lembas.services.fetch import FetchError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/push", tags=["push"])

# What a browser hands back is its own; these are the bounds that stop a crafted
# POST writing a novel into the row.
MAX_ENDPOINT = 2000
MAX_KEY = 255


@router.get("/key")
async def application_key(db: Db, user: RequiredUser) -> dict[str, str]:
    """The public half of this instance's VAPID key.

    A browser needs it to subscribe, and it is public by construction — it is
    what every push service is shown on every send. Behind a login anyway,
    because there is no reason for it to be readable by anyone who is not about
    to use it.
    """
    return {"key": push_service.public_key(db)}


@router.post("/subscribe")
async def subscribe(request: Request, db: Db, user: RequiredUser) -> Response:
    """Store what `pushManager.subscribe` handed back.

    Idempotent on the endpoint, because a browser that re-subscribes returns the
    same one — and two rows for one browser would be two notifications for one
    arrival. Re-subscribing also **re-points it at whoever is signed in now**:
    the endpoint belongs to the browser, so on a shared machine the second
    person to turn notifications on must get them instead of the first, not as
    well.
    """
    payload = await request.json()
    endpoint = str(payload.get("endpoint") or "").strip()[:MAX_ENDPOINT]
    keys = payload.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()[:MAX_KEY]
    auth = str(keys.get("auth") or "").strip()[:MAX_KEY]

    if not endpoint.startswith("https://") or not p256dh or not auth:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    # The endpoint is a URL the browser hands us and the server later POSTs to,
    # which makes it the same shape as every other URL a request can name --
    # and it was the one outbound client in the codebase not going through the
    # SSRF guard. `https://` alone says nothing about *where*: an internal
    # address is as valid a URL as Mozilla's push service, and the caller
    # triggers delivery themselves by sending a message and closing the tab.
    #
    # Checked here **and** again before the POST: a row can predate a DNS
    # change, and this one is stored.
    try:
        fetch_service.check_url(endpoint)
    except FetchError as exc:
        log.warning("refused a push endpoint from %s: %s", user.email, exc.message)
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    existing = db.scalars(
        select(PushSubscription).where(PushSubscription.endpoint == endpoint)
    ).first()
    if existing is not None:
        existing.user_id = user.id
        existing.p256dh = p256dh
        existing.auth_secret = auth
        existing.last_error = ""
    else:
        db.add(
            PushSubscription(
                user_id=user.id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth_secret=auth,
                label=str(request.headers.get("user-agent") or "")[:200],
            )
        )
    db.commit()
    log.info("%s registered a browser for notifications", user.email)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/unsubscribe")
async def unsubscribe(request: Request, db: Db, user: RequiredUser) -> Response:
    """Forget one browser.

    Answers 204 whether or not there was anything to delete: the browser has
    already dropped its own subscription by the time it calls this, and telling
    it that the row was missing gives it nothing it could do about it.
    """
    payload = await request.json()
    endpoint = str(payload.get("endpoint") or "").strip()

    row = db.scalars(
        select(PushSubscription).where(
            PushSubscription.endpoint == endpoint, PushSubscription.user_id == user.id
        )
    ).first()
    if row is not None:
        db.delete(row)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
