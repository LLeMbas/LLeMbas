"""What a desktop app needs from the server: one approval for its two tokens, an
app that is not listed as a device, and the person's files as JSON."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import TOKEN_DEVICE, ApiToken, DeviceCode, User
from lembas.security import api_tokens
from lembas.services import fs

GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@pytest.fixture(autouse=True)
def _fresh_rates():
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()
    yield


def _me(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _login(client, db, **fields) -> dict:
    code = client.post("/api/device/code", json={"client_id": "lembas-desktop",
                                                 "device_name": "LLeMbas Desktop",
                                                 "device_host": "workbench", **fields}).json()
    page = client.get(f"/device?code={code['user_code']}").text
    client.post("/device", data={"code": code["user_code"], "decision": "approve"})
    db.expire_all()
    db.scalar(select(DeviceCode)).last_polled_at = None
    db.commit()
    token = client.post("/api/device/token",
                        json={"grant_type": GRANT, "device_code": code["device_code"]}).json()
    return {"page": page, "token": token, "code": code}


def _bearer(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


# --- One approval, two tokens ------------------------------------------------------


def test_discovery_says_a_login_may_ask_for_the_agent_too(client):
    found = client.get("/.well-known/lembas.json").json()
    assert found["login"]["device"]["agent_scope"] is True


def test_one_approval_hands_over_the_app_and_the_agent_token(client, db, registered):
    got = _login(client, db, scope="app models", agent_scope="models memory link")
    # Both sets are shown before anything is approved.
    assert 'data-testid="agent-scopes"' in got["page"]
    token = got["token"]
    assert set(token["scope"].split()) == {"app", "models"}
    assert set(token["agent_scope"].split()) == {"models", "memory", "link"}
    assert token["agent_token"].startswith("lmb_") and token["agent_token"] != token["access_token"]
    rows = list(db.scalars(select(ApiToken).where(ApiToken.client == "lembas-desktop")))
    assert len(rows) == 2 and all(r.kind == TOKEN_DEVICE for r in rows)
    # Two programs, two tokens: each works on its own door.
    assert client.get("/api/v2/me", headers=_bearer(token["access_token"])).status_code == 200
    assert client.get("/api/v2/me", headers=_bearer(token["agent_token"])).status_code in (401, 403)
    # And the code is spent: a second poll gets neither.
    again = client.post("/api/device/token",
                        json={"grant_type": GRANT, "device_code": got["code"]["device_code"]})
    assert again.json()["error"] == "invalid_grant"


def test_a_login_that_asks_for_one_token_gets_one_as_before(client, db, registered):
    token = _login(client, db, scope="models memory link")["token"]
    assert "agent_token" not in token and "agent_scope" not in token
    assert db.scalar(select(ApiToken.id).where(ApiToken.client == "lembas-desktop")) is not None
    assert len(list(db.scalars(select(ApiToken)))) == 1


def test_the_agent_token_never_carries_the_app_scope(client, db, registered):
    token = _login(client, db, scope="app", agent_scope="app link bogus")["token"]
    assert token["agent_scope"] == "link"


# --- An app is not a device --------------------------------------------------------


def test_the_app_token_is_listed_as_an_app_not_a_device(client, db, registered):
    from lembas.services import devices as devices_service

    token = _login(client, db, scope="app models", agent_scope="models memory link")["token"]
    user = _me(db)
    devices = devices_service.devices_of(db, user)
    apps = devices_service.apps_of(db, user)
    assert [set(d.scopes_json) for d in devices] == [{"models", "memory", "link"}]
    assert [set(a.scopes_json) for a in apps] == [{"app", "models"}]
    page = client.get("/settings?tab=devices").text
    assert 'data-testid="signed-in-apps"' in page
    # The app's own list of devices to start an agent chat on never names itself.
    listed = client.get("/api/v2/devices", headers=_bearer(token["access_token"])).json()
    assert all(d["id"] != apps[0].id for d in listed["devices"])


# --- Files ---------------------------------------------------------------------------


def _app(db) -> dict[str, str]:
    _, raw = api_tokens.create(db, _me(db), name="LLeMbas Desktop", scopes=["app"])
    return _bearer(raw)


def test_the_files_listing_is_the_persons_own(client, db, registered):
    user = _me(db)
    home = fs.ensure_home(db, user)
    fs.write(fs.view(db, user), f"{home.path}/lembas.md", b"# Lembas\n")
    fs.mkdir(fs.view(db, user), f"{home.path}/recipes")
    db.commit()
    got = client.get("/api/v2/files", params={"path": home.path}, headers=_app(db))
    assert got.status_code == 200
    body = got.json()
    assert body["path"] == home.path
    kinds = [(e["name"], e["kind"]) for e in body["entries"]]
    assert ("recipes", "dir") in kinds and kinds[-1] == ("lembas.md", "file")  # dirs first
    entry = body["entries"][-1]
    assert set(entry) >= {"name", "path", "kind", "size", "mime"} and entry["size"] == 9


def test_someone_elses_home_is_refused(client, db, registered):
    from lembas.services import organisations

    other = organisations.create_account(db, name="Merry", email="merry@shire.test",
                                         password="brandybuck!!")
    db.commit()
    theirs = fs.ensure_home(db, other)
    fs.write(fs.view(db, other), f"{theirs.path}/secret.md", b"pipeweed")
    db.commit()
    headers = _app(db)
    assert client.get("/api/v2/files", params={"path": theirs.path},
                      headers=headers).status_code == 404
    assert client.get("/api/v2/files/content", params={"path": f"{theirs.path}/secret.md"},
                      headers=headers).status_code == 404


def test_content_is_text_html_as_source_and_binaries_withheld(client, db, registered):
    user = _me(db)
    home = fs.ensure_home(db, user).path
    v = fs.view(db, user)
    fs.write(v, f"{home}/page.html", b"<script>alert(1)</script>")
    fs.write(v, f"{home}/blob.bin", b"\x00\x01\x02")
    fs.write(v, f"{home}/big.txt", b"a" * (1024 * 1024 + 1))
    db.commit()
    headers = _app(db)

    def get(name):
        return client.get("/api/v2/files/content", params={"path": f"{home}/{name}"},
                          headers=headers)

    html = get("page.html").json()
    assert html["text"] == "<script>alert(1)</script>" and "withheld" not in html
    assert html["name"] == "page.html" and html["size"] == 25
    blob = get("blob.bin").json()
    assert blob["text"] == "" and blob["withheld"]["reason"] == "not_text"
    big = get("big.txt").json()
    assert big["text"] == "" and big["withheld"]["reason"] == "too_large"
    assert get("nothing.txt").status_code == 404


def test_the_files_door_needs_the_app_scope(client, db, registered):
    _, raw = api_tokens.create(db, _me(db), name="CLI", scopes=["models"])
    assert client.get("/api/v2/files", headers=_bearer(raw)).status_code in (401, 403)
    assert client.get("/api/v2/files").status_code == 401
