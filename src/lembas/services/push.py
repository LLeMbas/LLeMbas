"""Web Push: a notification that arrives with nothing of ours running.

Everything else here is polled. `/api/chats/unread` runs in an open page, which
is enough for "a reply landed while you were on another chat" and is nothing at
all for the case this exists for -- a schedule firing at 07:00 on a laptop whose
browser is shut. There is no way to close that gap from inside a page, because
there is no page.

## The trade, stated plainly

A push goes to the **browser vendor's** push service: Google's for Chrome,
Mozilla's for Firefox, Apple's for Safari. The endpoint is chosen by the
browser and there is no version of this feature that avoids it. That sits
against "a self-hosted tool must not report page views to a third party", and
the answer is not that the tension is imaginary:

- The payload is encrypted end to end (RFC 8291) with a key derived from a
  secret only the browser and this server hold, so the push service carries
  bytes it cannot read.
- What it *does* learn is that this server sent something to that subscription,
  and when. On a personal instance that is a timing channel over your own
  activity, and it is real.
- So it is **opt-in per device**, off until somebody presses the button, and
  the rest of the notification system works without it.

Nothing else in LLeMbas contacts an outside service on its own.

## Hand-rolled, and why

`pywebpush` would do this in three lines and bring `http-ece` and `py-vapid`
with it. The encryption below is one ECDH, two HKDFs and one AES-GCM seal, all
from `cryptography`, which is already a dependency because API keys are
Fernet-encrypted. That is the same call the MCP client makes: a hand-written
client, so what actually goes on the wire is in this repository.

## The two specifications

**RFC 8291** is the payload: `aes128gcm`, one record, the salt and the server's
ephemeral public key carried in the body's own header block.

**RFC 8292** is the authorisation: a JWT signed with a P-256 key whose public
half identifies this server. The keypair is generated once and kept in the
settings table with the private half Fernet-encrypted. It must be **stable** --
the public key is baked into every subscription a browser has made, so
regenerating it silently invalidates all of them.

## Failure is a subscription being dropped, not an error

A push service answers 404 or 410 for a subscription that no longer exists --
the browser was uninstalled, the site's data cleared, the permission revoked.
That is the normal end of a subscription's life and not a fault, so those two
delete the row. Everything else is logged and left, because a push service
having a bad hour is not a reason to lose somebody's registration.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric import utils as asym_utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import PushSubscription, User
from lembas.services import fetch as fetch_service
from lembas.services import settings_store
from lembas.services.crypto import decrypt, encrypt
from lembas.services.fetch import FetchError

log = logging.getLogger(__name__)

# How long a push service should hold an undelivered message. Four hours: long
# enough that a laptop opened after lunch still gets the morning's report, short
# enough that nothing arrives claiming to be news when it is a day old.
TTL_SECONDS = 4 * 3600

# The JWT's life. Twelve hours is the maximum RFC 8292 allows, and a short one
# buys nothing here -- it is minted per request.
JWT_SECONDS = 12 * 3600

# Bigger than any payload we send; the record size field must still be present
# and must exceed the ciphertext.
RECORD_SIZE = 4096

# What a payload may carry. A push service will refuse a large body outright,
# and there is nothing here worth more than a title and a line.
MAX_PAYLOAD_BYTES = 3000

SETTING_PRIVATE = "push_private_key"
SETTING_PUBLIC = "push_public_key"


# --- base64url, without padding, everywhere ------------------------------------
def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


# --- The server's identity -----------------------------------------------------
@dataclass(frozen=True)
class Keys:
    private: ec.EllipticCurvePrivateKey
    public_b64: str


def keys(db: DBSession) -> Keys:
    """This instance's VAPID keypair, generated once and kept.

    Generated on first use rather than by a setup step, because a feature that
    needs somebody to run a command before it works is one that is off on every
    instance that did not read the release notes. The private half is Fernet-
    encrypted at rest, like every other secret here.

    **Never regenerated.** The public key is inside every subscription a browser
    holds, so a new one silently invalidates all of them -- notifications simply
    stop, with nothing anywhere saying why.
    """
    stored = settings_store.get(db, SETTING_PRIVATE)
    if stored:
        private = serialization.load_pem_private_key(decrypt(str(stored)).encode(), password=None)
        return Keys(private=private, public_b64=str(settings_store.get(db, SETTING_PUBLIC) or ""))

    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_b64 = b64(_raw_public(private.public_key()))
    settings_store.update(db, {SETTING_PRIVATE: encrypt(pem), SETTING_PUBLIC: public_b64})
    log.info("generated a VAPID keypair for web push")
    return Keys(private=private, public_b64=public_b64)


def public_key(db: DBSession) -> str:
    """What a browser needs in order to subscribe."""
    return keys(db).public_b64


def _raw_public(key: ec.EllipticCurvePublicKey) -> bytes:
    """The uncompressed 65-byte point, which is the only form either spec uses."""
    return key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )


# --- RFC 8292: proving who is asking -------------------------------------------
def _jwt(private: ec.EllipticCurvePrivateKey, audience: str, subject: str) -> str:
    header = b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64(
        json.dumps(
            {"aud": audience, "exp": int(time.time()) + JWT_SECONDS, "sub": subject},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{claims}".encode()

    der = private.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    # JWS wants the raw pair, not DER. `cryptography` only signs to DER, so it
    # is decoded and re-emitted fixed-width -- a leading zero dropped here is a
    # signature every push service rejects, and the error it gives is 401.
    r, s = asym_utils.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{header}.{claims}.{b64(raw)}"


def _audience(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


# --- RFC 8291: the payload ------------------------------------------------------
def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """Extract-then-expand, written out because the two halves take different
    salts here and `HKDF` in one call cannot express that."""
    prk = hmac.new(salt, ikm, sha256).digest()
    return HKDFExpand(algorithm=hashes.SHA256(), length=length, info=info).derive(prk)


def encrypt_payload(payload: bytes, *, p256dh: str, auth: str) -> bytes:
    """One `aes128gcm` record, ready to be the body of the POST.

    The layout is the specification's, and the order matters to a parser that
    has never seen our code:

        salt (16) | record size (4) | key id length (1) | server key (65) | ct
    """
    client_public = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), unb64(p256dh)
    )
    auth_secret = unb64(auth)

    server_private = ec.generate_private_key(ec.SECP256R1())
    server_public = _raw_public(server_private.public_key())
    shared = server_private.exchange(ec.ECDH(), client_public)

    # The first HKDF is salted with the subscription's own auth secret and its
    # info binds both public keys, which is what stops a captured record being
    # replayed at a different subscriber.
    key_info = b"WebPush: info\x00" + unb64(p256dh) + server_public
    ikm = _hkdf(auth_secret, shared, key_info, 32)

    salt = os.urandom(16)
    content_key = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)

    # 0x02 is the padding delimiter for the last (here, only) record. 0x01 would
    # say another follows, and a receiver would wait for it.
    ciphertext = AESGCM(content_key).encrypt(nonce, payload + b"\x02", None)

    return (
        salt
        + RECORD_SIZE.to_bytes(4, "big")
        + len(server_public).to_bytes(1, "big")
        + server_public
        + ciphertext
    )


# --- Sending --------------------------------------------------------------------
def subject_for(db: DBSession) -> str:
    """The `sub` claim: who to contact about this server's pushes.

    A URL is as acceptable as a mailto and needs nothing configured, so the
    instance's own base URL is used when there is one. Push services require the
    claim to be present; none of them checks that it resolves.
    """
    configured = str(settings_store.get(db, "public_url") or "").strip()
    return configured or "https://lembas.invalid"


async def send_one(db: DBSession, subscription: PushSubscription, payload: dict[str, Any]) -> bool:
    """Deliver to one registration. True if it was accepted.

    Never raises: this runs from arrival paths that must not fail because a push
    service is having a bad hour.
    """
    body = json.dumps(payload, separators=(",", ":")).encode()
    if len(body) > MAX_PAYLOAD_BYTES:  # pragma: no cover - titles are bounded already
        body = json.dumps({"title": payload.get("title", "LLeMbas")}).encode()

    try:
        encrypted = encrypt_payload(
            body, p256dh=subscription.p256dh, auth=subscription.auth_secret
        )
        token = _jwt(keys(db).private, _audience(subscription.endpoint), subject_for(db))
    except Exception:  # a malformed stored key must not kill a reply
        log.exception("could not build a push for %s", subscription.id)
        return False

    headers = {
        "TTL": str(TTL_SECONDS),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        # "high" would let a phone wake for it; this is news, not an alarm.
        "Urgency": "normal",
        "Authorization": f"vapid t={token}, k={keys(db).public_b64}",
    }

    # Again, on a stored value. The subscribe route checks it too, but the row
    # outlives that check: a name that pointed at a push service when it was
    # registered can point inside the network later, and this is the side that
    # actually opens the socket.
    try:
        fetch_service.check_url(subscription.endpoint)
    except FetchError as exc:
        log.warning(
            "refusing to push to %s: %s", _audience(subscription.endpoint), exc.message
        )
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(subscription.endpoint, content=encrypted, headers=headers)
    except httpx.RequestError as exc:
        log.warning("push to %s failed: %s", _audience(subscription.endpoint), exc)
        return False

    if response.status_code in (404, 410):
        # The normal end of a subscription's life: uninstalled, cleared, or the
        # permission revoked. Deleting it is the correct response and not an
        # error -- keeping it would mean retrying forever against a dead address.
        log.info("push subscription %s is gone; removing it", subscription.id)
        db.delete(subscription)
        db.commit()
        return False
    if response.status_code >= 400:
        subscription.last_error = f"{response.status_code}: {response.text[:200]}"
        db.commit()
        log.warning(
            "push to %s refused: %s %s",
            _audience(subscription.endpoint),
            response.status_code,
            response.text[:200],
        )
        return False

    if subscription.last_error:
        subscription.last_error = ""
        db.commit()
    return True


def subscriptions_for(db: DBSession, user: User | None) -> list[PushSubscription]:
    if user is None:
        return []
    return list(
        db.scalars(select(PushSubscription).where(PushSubscription.user_id == user.id))
    )


async def announce(
    db: DBSession, user: User | None, *, title: str, body: str, url: str = "", kind: str = ""
) -> int:
    """Tell every device this person has registered. Returns how many took it.

    Called at the moment something arrives rather than from the poll, because
    the whole point is the case where no page is open to poll. A device with a
    page open gets this *and* the in-page toast -- the service worker resolves
    that by not showing a notification when one of its own windows is focused,
    which is the only place that can be known.
    """
    subscriptions = subscriptions_for(db, user)
    if not subscriptions:
        return 0

    payload = {"title": title, "body": body, "url": url, "kind": kind}
    delivered = 0
    for subscription in list(subscriptions):
        if await send_one(db, subscription, payload):
            delivered += 1
    return delivered


# Fire-and-forget tasks, held so the event loop does not collect one mid-flight.
# asyncio keeps only a weak reference to a task nobody awaits, and a push that
# vanishes halfway is the kind of intermittent nobody reproduces.
_TASKS: set[Any] = set()


def announce_later(user_id: str, *, title: str, body: str, url: str = "", kind: str = "") -> None:
    """Announce from a path that must not wait for it, and must not fail with it.

    Called where something *arrives* -- a reply finishing with nobody watching,
    a report being filed, a run posting into Messages -- rather than from the
    poll. That is the whole point: the poll needs an open page, and the case
    worth a notification is the one where there is none.

    Each of those sites runs exactly once per arrival, which is what makes this
    fire once with no "already notified" flag of its own. `unread_notified` is
    the *page's* record of having toasted; borrowing it here would mean whichever
    channel got there first silenced the other.

    Its own session, opened inside the task: the caller's is usually about to be
    committed and closed, and holding one open across a POST to somebody else's
    server is how a request comes to wait on a push service having a bad day.
    """
    import asyncio

    from lembas.db.session import session_scope

    # The loop is checked *before* the coroutine is built, not by catching what
    # `create_task` raises without one. A coroutine made and then dropped is a
    # "never awaited" RuntimeWarning from wherever it was created -- which here
    # is every synchronous caller in the suite and every CLI command that files
    # a report. The warning would be the only symptom, and it would be
    # attributed to the caller rather than to this.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return

    async def run() -> None:
        try:
            with session_scope() as db:
                user = db.get(User, user_id)
                if user is None:
                    return
                await announce(db, user, title=title, body=body, url=url, kind=kind)
        except Exception:  # nothing upstream can act on this
            log.exception("could not announce to %s", user_id)

    task = asyncio.create_task(run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


__all__ = [
    "announce",
    "announce_later",
    "encrypt_payload",
    "keys",
    "public_key",
    "send_one",
    "subscriptions_for",
]
