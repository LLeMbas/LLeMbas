"""Web Push: the encryption, the identity, and where an arrival is announced.

The encryption is the part worth testing hardest, and for an unusual reason:
there is no way to tell from here whether it is right. A push service accepts
the POST and forwards the bytes without understanding them, so a wrong key
derivation is a notification that never appears on a device, with a 201 in the
log and nothing anywhere to read. So the record is decrypted back with an
independent implementation of the specification's other half — if the two agree
on a key derived through an ECDH neither of them chose, the derivation is right.
"""

from __future__ import annotations

import hmac
import json
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import PushSubscription, User
from lembas.services import push as push_service


@pytest.fixture(autouse=True)
def push_switched_on():
    """Web Push is off by default (it goes through the browser vendor's relay);
    everything in this file is about it being on. The default itself is tested
    at the bottom."""
    from lembas.db.session import session_scope
    from lembas.services import settings_store

    with session_scope() as session:
        settings_store.update(session, {"web_push_enabled": True})
    yield


@pytest.fixture(autouse=True)
def resolvable_push_service(monkeypatch):
    """`push.example` is not a real host, and `check_url` resolves.

    The endpoint a browser hands back is now checked by the SSRF guard on both
    sides -- at subscribe, and again before the POST, because the row outlives
    the first check. That guard does DNS, so every fixture endpoint in this file
    would be refused for not existing rather than for being anywhere bad.

    Stood in with something that keeps the part under test: the *shape* of the
    refusal. A literal private or loopback address is still refused, so the
    tests that assert the guard is wired in are asserting the real thing.
    """
    import ipaddress
    from urllib.parse import urlsplit

    from lembas.services import fetch as real_fetch

    def stand_in(url: str, *, allow_private: bool = False) -> str:
        host = (urlsplit(url).hostname or "").strip("[]")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if host.endswith(".example"):
                return url
            return real_fetch.check_url(url, allow_private=allow_private)
        if not allow_private and (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_unspecified
        ):
            raise real_fetch.FetchError("That address is not reachable from here.")
        return url

    for module in ("lembas.api.push", "lembas.services.push"):
        monkeypatch.setattr(f"{module}.fetch_service.check_url", stand_in)
    return stand_in


# --- A browser, standing in for one --------------------------------------------
class Browser:
    """The client half of RFC 8291, written out rather than imported.

    This is the point: `services/push.py` and this class share no code, so a
    payload that survives the round trip proves the derivation rather than
    proving that one function is the inverse of itself.
    """

    def __init__(self) -> None:
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"  # 16 bytes, as a browser generates

    @property
    def p256dh(self) -> str:
        return push_service.b64(
            self.private.public_key().public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
        )

    @property
    def auth_b64(self) -> str:
        return push_service.b64(self.auth)

    def open(self, record: bytes) -> bytes:
        salt, record = record[:16], record[16:]
        _size, record = record[:4], record[4:]
        id_len, record = record[0], record[1:]
        server_public, ciphertext = record[:id_len], record[id_len:]

        shared = self.private.exchange(
            ec.ECDH(),
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_public),
        )
        key_info = (
            b"WebPush: info\x00"
            + self.private.public_key().public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
            + server_public
        )
        ikm = _hkdf(self.auth, shared, key_info, 32)
        key = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)

        plain = AESGCM(key).decrypt(nonce, ciphertext, None)
        assert plain[-1] == 0x02, "the last record must carry the 0x02 delimiter"
        return plain[:-1]


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, sha256).digest()
    return HKDFExpand(algorithm=hashes.SHA256(), length=length, info=info).derive(prk)


# --- The payload ---------------------------------------------------------------
def test_a_record_decrypts_back_to_what_went_in():
    browser = Browser()
    payload = json.dumps({"title": "Nightly build", "body": "It went green."}).encode()

    record = push_service.encrypt_payload(
        payload, p256dh=browser.p256dh, auth=browser.auth_b64
    )

    assert browser.open(record) == payload


def test_two_records_of_the_same_thing_differ():
    """A fresh ephemeral key and a fresh salt per record. Reusing either would
    make identical notifications identical on the wire, which is a pattern the
    push service can read without decrypting anything."""
    browser = Browser()
    payload = b"same"

    first = push_service.encrypt_payload(payload, p256dh=browser.p256dh, auth=browser.auth_b64)
    second = push_service.encrypt_payload(payload, p256dh=browser.p256dh, auth=browser.auth_b64)

    assert first != second
    assert browser.open(first) == browser.open(second) == payload


def test_another_browsers_key_cannot_open_it():
    """The auth secret is bound into the first derivation, so a record is for
    one subscription and not merely for one public key."""
    intended, other = Browser(), Browser()
    record = push_service.encrypt_payload(
        b"private", p256dh=intended.p256dh, auth=intended.auth_b64
    )

    with pytest.raises(Exception):  # noqa: B017 - any failure is the right one
        other.open(record)


def test_the_header_block_is_laid_out_as_the_specification_says():
    """salt | record size | key id length | key | ciphertext. A parser that has
    never seen our code reads it by position, so the positions are the
    contract."""
    browser = Browser()
    record = push_service.encrypt_payload(b"x", p256dh=browser.p256dh, auth=browser.auth_b64)

    assert len(record) > 16 + 4 + 1 + 65
    assert int.from_bytes(record[16:20], "big") == push_service.RECORD_SIZE
    assert record[20] == 65  # an uncompressed P-256 point, always


# --- The identity ---------------------------------------------------------------
def test_the_keypair_is_generated_once_and_kept(db, registered):
    """The public key is inside every subscription a browser holds, so a second
    one silently invalidates all of them — notifications stop, with nothing
    anywhere saying why."""
    first = push_service.public_key(db)
    second = push_service.public_key(db)

    assert first and first == second
    assert len(push_service.unb64(first)) == 65


def test_the_private_half_is_encrypted_at_rest(db, registered):
    from lembas.services import settings_store

    push_service.keys(db)
    stored = str(settings_store.get(db, push_service.SETTING_PRIVATE))

    assert stored
    assert "BEGIN PRIVATE KEY" not in stored


def test_the_token_is_signed_for_the_push_service_it_is_going_to(db, registered):
    """`aud` is the origin of the endpoint. A token minted for one push service
    and sent to another is refused with a 401, which looks like a broken key."""
    token = push_service._jwt(
        push_service.keys(db).private, "https://fcm.googleapis.com", "https://example.test"
    )
    header, claims, signature = token.split(".")

    assert json.loads(push_service.unb64(header))["alg"] == "ES256"
    assert json.loads(push_service.unb64(claims))["aud"] == "https://fcm.googleapis.com"
    # Raw r||s, not DER: a DER signature is the same length most of the time,
    # which is why this is asserted rather than assumed.
    assert len(push_service.unb64(signature)) == 64


# --- Registering ----------------------------------------------------------------
def _subscribe(client: TestClient, endpoint: str = "https://push.example/abc"):
    return client.post(
        "/api/push/subscribe",
        json={"endpoint": endpoint, "keys": {"p256dh": "BAA" + "A" * 84, "auth": "AAAAAAAA"}},
    )


def test_a_browser_registers_and_can_be_forgotten(client: TestClient, db, registered):
    assert _subscribe(client).status_code == 204
    row = db.scalars(select(PushSubscription)).one()
    assert row.endpoint == "https://push.example/abc"

    response = client.post("/api/push/unsubscribe", json={"endpoint": row.endpoint})
    assert response.status_code == 204
    assert db.scalars(select(PushSubscription)).all() == []


def test_registering_twice_is_one_row(client: TestClient, db, registered):
    """A browser that re-subscribes hands back the same endpoint, and two rows
    would be two notifications for one arrival."""
    _subscribe(client)
    _subscribe(client)

    assert len(db.scalars(select(PushSubscription)).all()) == 1


def test_an_endpoint_that_is_not_https_is_refused(client: TestClient, db, registered):
    response = client.post(
        "/api/push/subscribe",
        json={"endpoint": "http://push.example/abc", "keys": {"p256dh": "x", "auth": "y"}},
    )
    assert response.status_code == 400


def test_forgetting_somebody_elses_registration_does_nothing(client: TestClient, db, registered):
    from lembas.security.passwords import hash_password

    _subscribe(client)
    other = User(email="other@example.test", name="O", password_hash=hash_password("x" * 12))
    db.add(other)
    db.commit()
    row = db.scalars(select(PushSubscription)).one()
    row.user_id = other.id
    db.commit()

    client.post("/api/push/unsubscribe", json={"endpoint": row.endpoint})

    assert db.scalars(select(PushSubscription)).all() != []


# --- Where an arrival is announced ----------------------------------------------
async def test_announcing_with_no_registered_browser_is_a_no_op(db, registered):
    owner = db.scalars(select(User)).first()
    assert await push_service.announce(db, owner, title="t", body="b") == 0


async def test_a_gone_subscription_is_deleted_rather_than_retried(
    db, registered, mock_http
):
    """404 and 410 are the normal end of a subscription's life — uninstalled,
    cleared, permission revoked. Keeping it would mean retrying for ever
    against an address that will never answer."""
    import httpx

    db.add(
        PushSubscription(
            user_id=db.scalars(select(User)).first().id,
            endpoint="https://push.example/gone",
            p256dh=Browser().p256dh,
            auth_secret=push_service.b64(b"0123456789abcdef"),
        )
    )
    db.commit()
    row = db.scalars(select(PushSubscription)).one()

    mock_http(lambda request: httpx.Response(410))

    assert await push_service.send_one(db, row, {"title": "x"}) is False
    assert db.scalars(select(PushSubscription)).all() == []


async def test_a_refusal_that_is_not_gone_keeps_the_subscription(db, registered, mock_http):
    """A push service having a bad hour is not a reason to lose somebody's
    registration."""
    import httpx

    db.add(
        PushSubscription(
            user_id=db.scalars(select(User)).first().id,
            endpoint="https://push.example/busy",
            p256dh=Browser().p256dh,
            auth_secret=push_service.b64(b"0123456789abcdef"),
        )
    )
    db.commit()
    row = db.scalars(select(PushSubscription)).one()

    mock_http(lambda request: httpx.Response(503, text="try later"))

    assert await push_service.send_one(db, row, {"title": "x"}) is False
    kept = db.scalars(select(PushSubscription)).one()
    assert "503" in kept.last_error


async def test_a_send_carries_the_two_headers_a_push_service_requires(
    db, registered, mock_http
):
    import httpx

    seen: dict = {}

    db.add(
        PushSubscription(
            user_id=db.scalars(select(User)).first().id,
            endpoint="https://push.example/ok",
            p256dh=Browser().p256dh,
            auth_secret=push_service.b64(b"0123456789abcdef"),
        )
    )
    db.commit()
    row = db.scalars(select(PushSubscription)).one()

    def handler(request):
        seen.update(dict(request.headers))
        return httpx.Response(201)

    mock_http(handler)

    assert await push_service.send_one(db, row, {"title": "x"}) is True
    assert seen["content-encoding"] == "aes128gcm"
    assert seen["authorization"].startswith("vapid t=")
    assert ", k=" in seen["authorization"]


# --- The poll announces everything, not only chats -------------------------------
def test_a_push_endpoint_pointing_inside_the_network_is_refused(client, db, registered):
    """The endpoint is a URL the browser hands us and the server later POSTs to,
    so it goes through the SSRF guard like every other outbound client --
    `https://` alone says nothing about *where*. Delivery is triggered by the
    caller themselves: send a message, close the tab, and `_persist` announces
    it because nobody is following."""
    refused = client.post(
        "/api/push/subscribe",
        json={
            "endpoint": "https://192.168.1.5:8443/admin/shutdown",
            "keys": {"p256dh": "BAA" + "A" * 84, "auth": "AAAAAAAA"},
        },
    )
    assert refused.status_code == 400
    assert db.scalar(select(PushSubscription)) is None


async def test_a_stored_endpoint_is_checked_again_before_the_post(db, registered):
    """A row outlives the check made when it was written: a name that pointed at
    a push service can point inside the network later."""
    owner = db.scalar(select(User))
    row = PushSubscription(
        user_id=owner.id,
        endpoint="https://127.0.0.1:9/hijacked",
        p256dh=push_service.b64(
            ec.generate_private_key(ec.SECP256R1())
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint,
            )
        ),
        auth_secret=push_service.b64(b"0123456789abcdef"),
    )
    db.add(row)
    db.commit()

    sent = await push_service.send_one(db, row, {"title": "x", "body": "y", "url": "/"})

    assert sent is False
    # Refused rather than dropped: the row is somebody's registration, and being
    # unreachable today is not the same as being gone.
    assert db.scalar(select(PushSubscription)) is not None


# --- Off by default, and nothing of the content ------------------------------------


def test_web_push_is_off_until_an_administrator_turns_it_on(client, db, registered):
    from lembas.services import settings_store

    settings_store.update(db, {"web_push_enabled": False})
    assert client.get("/api/push/key").json() == {"key": ""}
    response = client.post(
        "/api/push/subscribe",
        json={"endpoint": "https://push.example.test/x", "keys": {"p256dh": "a", "auth": "b"}},
    )
    assert response.status_code == 409
    assert settings_store.defaults(settings_store.GENERAL)["web_push_enabled"] is False


async def test_a_push_carries_no_content(db, registered, monkeypatch):
    owner = db.scalars(select(User)).first()
    db.add(PushSubscription(user_id=owner.id, endpoint="https://push.example.test/e",
                            p256dh="x", auth_secret="y"))
    db.commit()
    sent = []

    async def capture(_db, _subscription, payload):
        sent.append(payload)
        return True

    monkeypatch.setattr(push_service, "send_one", capture)
    await push_service.announce(
        db, owner, title="Secret plans", body="The ring is in the cellar", url="/chat/x",
        kind="reply", extra={"chat_id": "x", "preview": "the ring"},
    )
    assert sent and "Secret" not in json.dumps(sent) and "ring" not in json.dumps(sent)
    assert sent[0]["chat_id"] == "x" and sent[0]["url"] == "/chat/x"

