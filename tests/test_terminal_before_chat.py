"""The terminal before the first message: on an agent connection, a shell opens
as soon as the computer and the directory are chosen -- on the page's door and
on the app's -- and the first message that makes the chat there takes it over,
scrollback and all.

A fake device answers over the real link: `_lembas/terminal/open` in the
directory chosen, output as `_lembas/terminal/output`.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from lembas.db.models import User
from lembas.security import api_tokens
from lembas.services import device_link
from lembas.services.agent import terminal as terminal_service
from tests.test_device_link import _device, _wait_linked
from tests.test_device_protocol2 import ALL, _hello

pytestmark = pytest.mark.slow


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    yield
    asyncio.run(terminal_service.shutdown())
    terminal_service._SESSIONS.clear()
    device_link.reset()


def _app_bearer(db) -> dict[str, str]:
    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="LLeMbas Desktop", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def _open_on_device(ws, terminal_id: str = "t1") -> dict:
    """The device is asked for a terminal: answered, and its first output sent."""
    asked = ws.receive_json()
    while asked.get("method") != "_lembas/terminal/open":
        asked = ws.receive_json()
    ws.send_text(json.dumps(
        {"jsonrpc": "2.0", "id": asked["id"], "result": {"terminalId": terminal_id}}
    ))
    return asked["params"]


def _output(ws, text: bytes, terminal_id: str = "t1") -> None:
    ws.send_text(json.dumps({
        "jsonrpc": "2.0", "method": "_lembas/terminal/output",
        "params": {"terminalId": terminal_id, "data": base64.b64encode(text).decode()},
    }))


def _ready(term) -> dict:
    frame = json.loads(term.receive_text())
    assert frame["t"] == "ready", frame
    return frame


def test_the_app_opens_a_terminal_before_the_chat_and_the_chat_keeps_it(client, db, registered):
    token_id, raw = _device(db)
    headers = _app_bearer(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, [*ALL, "jobs", "show"])
        _wait_linked(token_id)

        devices = client.get("/api/v2/devices", headers=headers).json()["devices"]
        assert [d["terminal"] for d in devices if d["id"] == token_id] == [True]

        url = f"/api/v2/devices/{token_id}/terminal/ws?directory=/srv/work/app&cols=100&rows=30"
        with client.websocket_connect(url, headers=headers) as term:
            asked = _open_on_device(ws)
            # In the directory chosen, at the size the panel has.
            assert asked == {"cwd": "/srv/work/app", "cols": 100, "rows": 30}
            ready = _ready(term)
            assert ready["dir"] == "/srv/work/app"
            _output(ws, b"dev@device-1:/srv/work/app$ ")
            assert term.receive_bytes() == b"dev@device-1:/srv/work/app$ "

            # The first message makes the chat there: the shell is the chat's.
            made = client.post("/api/v2/chats", headers=headers, json={
                "text": "Build it", "kind": "agent", "device": token_id,
                "directory": "/srv/work/app", "mode": "edit",
            })
            assert made.status_code == 201, made.text
            chat_id = made.json()["chat"]["id"]
            session = terminal_service.get(chat_id)
            assert session is not None and session.terminal_id == "t1"
            assert terminal_service.get(
                terminal_service.draft_key(session.owner_id, token_id, "/srv/work/app")
            ) is None

        # The chat's panel attaches to the same shell -- no second open on the
        # device -- and gets what it said so far.
        chat_url = f"/api/v2/chats/{chat_id}/terminal/ws"
        with client.websocket_connect(chat_url, headers=headers) as term:
            assert _ready(term)["dir"] == "/srv/work/app"
            assert term.receive_bytes() == b"dev@device-1:/srv/work/app$ "
        assert terminal_service.count() == 1


def test_the_page_opens_one_before_the_chat_too(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, [*ALL])
        _wait_linked(token_id)
        url = f"/api/devices/{token_id}/terminal/ws?directory=/srv/work/app"
        with client.websocket_connect(url, headers={"origin": "http://testserver"}) as term:
            assert _open_on_device(ws)["cwd"] == "/srv/work/app"
            assert _ready(term)["label"] == "device-1"

        # The composer's first message takes it over, as the app's does.
        client.post("/api/chats/start", data={
            "content": "Build it", "kind": "agent", "ssh_profile_id": f"device:{token_id}",
            "project_dir": "/srv/work/app", "agent_mode": "edit",
        })
        from lembas.db.models import Chat

        chat = db.scalar(select(Chat).order_by(Chat.created_at.desc()))
        assert chat.device_token_id == token_id
        assert terminal_service.get(chat.id) is not None


def test_another_directory_is_another_shell_and_the_old_one_goes(client, db, registered):
    token_id, raw = _device(db)
    headers = _app_bearer(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, [*ALL])
        _wait_linked(token_id)
        base = f"/api/v2/devices/{token_id}/terminal/ws?directory="
        with client.websocket_connect(base + "/srv/work/a", headers=headers) as term:
            _open_on_device(ws, "t1")
            _ready(term)
        # The screen moved to another directory: a new shell, and the one
        # nobody watches any more is closed rather than held until idle.
        with client.websocket_connect(base + "/srv/work/b", headers=headers) as term:
            asked = _open_on_device(ws, "t2")
            assert asked["cwd"] == "/srv/work/b"
            _ready(term)
            assert terminal_service.count() == 1


@pytest.mark.parametrize(
    ("directory", "headers_of", "said"),
    [
        ("", "app", "Choose a directory"),
        ("/srv/work/app", "none", None),
        ("/srv/work/app", "models", None),
    ],
)
def test_refusals(client, db, registered, directory, headers_of, said):
    token_id, raw = _device(db)
    headers = {
        "app": _app_bearer(db),
        "none": {},
        "models": {"Authorization": f"Bearer {raw}"},  # a device token: not the app's door
    }[headers_of]
    url = f"/api/v2/devices/{token_id}/terminal/ws?directory={directory}"
    with client.websocket_connect(url, headers=headers) as term:
        if said is None:
            with pytest.raises(WebSocketDisconnect) as closed:
                term.receive_text()
            assert closed.value.code == 4401
        else:
            frame = json.loads(term.receive_text())
            assert frame["t"] == "error" and said in frame["message"]


def test_someone_elses_device_is_refused(client, db, registered):
    from lembas.db.models import TOKEN_DEVICE
    from tests.conftest import join

    join(client, {"name": "Sam", "email": "sam@shire.test", "password": "po-tay-toes-boil"})
    sam = db.scalar(select(User).where(User.email == "sam@shire.test"))
    theirs, _ = api_tokens.create(
        db, sam, name="LLeMbas CLI on sams-box", scopes=["models", "link"],
        kind=TOKEN_DEVICE, device_host="sams-box",
    )
    url = f"/api/v2/devices/{theirs.id}/terminal/ws?directory=/srv/work"
    with client.websocket_connect(url, headers=_app_bearer(db)) as term:
        frame = json.loads(term.receive_text())
    assert frame == {"t": "error", "message": "That device is not yours."}
