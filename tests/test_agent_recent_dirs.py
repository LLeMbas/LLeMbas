"""The directory of a new agent chat: the ones already used, the latest chosen.

The composer offers, per linked device, the directories this person's agent
chats there already worked in (`chat.recent_dirs`), most recent first, and
starts on the first of them. A device with no history starts on nothing: the
composer asks rather than filling in the device's first root, which made `/`
the directory of every first chat on a new machine.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import ApiToken, Chat, Model, User
from lembas.services import chat as chat_service
from lembas.services import device_link
from tests.test_sidebar_settings_composer import _device, _enable_agents, _model, _owner


@pytest.fixture(autouse=True)
def _no_links():
    yield
    device_link.LINKS.clear()


def _chat(db, user: User, token: ApiToken, path: str, *, ago: int, **extra) -> Chat:
    model = db.scalar(select(Model)) or _model(db)
    fields = {"kind": "agent", **extra}
    chat = Chat(
        user_id=user.id,
        title=path or "untitled",
        model_id=model.model_id,
        connection_id=model.connection_id,
        device_token_id=token.id,
        project_dir=path,
        **fields,
    )
    db.add(chat)
    db.commit()
    chat.updated_at = datetime.now(UTC) - timedelta(minutes=ago)
    db.commit()
    return chat


def _recent_attr(page: str, token: ApiToken) -> list[str]:
    option = re.search(rf'<option value="device:{token.id}"[^>]*>', page)
    assert option, "the device is not offered"
    found = re.search(r"data-recent='([^']*)'", option.group(0))
    assert found, "the option carries no data-recent"
    return json.loads(html_lib.unescape(found.group(1)))


# --- The query ------------------------------------------------------------------
def test_latest_first_and_each_directory_once(client: TestClient, db, registered):
    _model(db)
    owner = _owner(db)
    device = _device(db, "device-1", linked=False)
    _chat(db, owner, device, "/srv/old", ago=50)
    _chat(db, owner, device, "/srv/app", ago=40)
    _chat(db, owner, device, "/srv/web", ago=20)
    # Used again later: it moves up to where its latest chat puts it.
    _chat(db, owner, device, "/srv/app", ago=5)

    found = chat_service.recent_dirs(db, owner.id, [device.id])
    assert found == {device.id: ["/srv/app", "/srv/web", "/srv/old"]}


def test_per_device_and_capped(client: TestClient, db, registered):
    _model(db)
    owner = _owner(db)
    first = _device(db, "device-1", linked=False)
    second = _device(db, "device-2", linked=False)
    unused = _device(db, "device-3", linked=False)
    for n in range(chat_service.RECENT_DIRS + 3):
        _chat(db, owner, first, f"/p/{n}", ago=100 - n)
    _chat(db, owner, second, "/elsewhere", ago=1)

    found = chat_service.recent_dirs(db, owner.id, [first.id, second.id, unused.id])
    assert len(found[first.id]) == chat_service.RECENT_DIRS
    assert found[first.id][0] == f"/p/{chat_service.RECENT_DIRS + 2}"
    assert "/elsewhere" not in found[first.id]
    assert found[second.id] == ["/elsewhere"]
    # A device with nothing is there, and empty -- not missing.
    assert found[unused.id] == []
    assert chat_service.recent_dirs(db, owner.id, []) == {}


def test_only_this_persons_real_agent_chats(client: TestClient, db, registered):
    _model(db)
    owner = _owner(db)
    device = _device(db, "device-1", linked=False)
    stranger = User(email="sam@shire.test", name="Sam", password_hash="x", role="user")
    db.add(stranger)
    db.commit()
    _chat(db, owner, device, "/mine", ago=30)
    _chat(db, stranger, device, "/theirs", ago=1)
    _chat(db, owner, device, "/scratch", ago=2, temporary=True)
    _chat(db, owner, device, "", ago=3)
    # A plain chat that somehow carries a directory is not an agent's.
    _chat(db, owner, device, "/plain", ago=4, kind="chat")

    assert chat_service.recent_dirs(db, owner.id, [device.id]) == {device.id: ["/mine"]}


# --- The composer ---------------------------------------------------------------
def test_each_device_option_carries_its_recent_directories(client: TestClient, db, registered):
    _model(db)
    _enable_agents(db)
    owner = _owner(db)
    first = _device(db, "device-1", linked=True)
    second = _device(db, "device-2", linked=True)
    _chat(db, owner, first, "/srv/it's <here>", ago=10)
    _chat(db, owner, first, "/srv/app", ago=1)

    page = client.get(f"/chat?kind=agent&device={first.id}").text
    # Escaped in the attribute, and read back whole.
    assert _recent_attr(page, first) == ["/srv/app", "/srv/it's <here>"]
    assert "<here>" not in page.split("data-recent=", 1)[1].split(">", 1)[0]
    assert _recent_attr(page, second) == []
    # The device's own default directory is no longer offered as one.
    assert "data-dir=" not in page[page.index('name="ssh_profile_id"') : page.index("</select>")]


def test_a_device_with_no_history_chooses_no_directory(client: TestClient, db, registered):
    _model(db)
    _enable_agents(db)
    device = _device(db, "device-1", linked=True)

    page = client.get(f"/chat?kind=agent&device={device.id}").text
    assert _recent_attr(page, device) == []
    field = re.search(r'<input type="hidden" name="project_dir"[^>]*>', page).group(0)
    assert 'value=""' in field
    assert 'data-dir-seed=""' in field
    label = page[page.index("data-dir-label") :]
    assert label[label.index(">") + 1 : label.index("</span>")].strip() == "Choose a directory"


def test_the_server_still_takes_an_empty_directory(client: TestClient, db, registered):
    """The composer will not send without one; the server stays tolerant of a
    client that does, and falls back to the device's first root."""
    _model(db)
    _enable_agents(db)
    owner = _owner(db)
    token = _device(db, "box", linked=True)
    device_link.LINKS[token.id].hello["limits"] = {"roots": ["/home/me"]}

    from lembas.api.chats import _new_chat

    chat = _new_chat(db, owner, kind="agent", ssh_profile_id=f"device:{token.id}", project_dir="")
    assert chat.project_dir == "/home/me"
