"""In LLeMbas Desktop, the device that is this computer is "Local" and first.

Desktop's webview says which computer it runs on in its User-Agent; a browser
does not, and there nothing changes. A display label only: it never shows a
device that is not the signed-in person's own.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import TOKEN_DEVICE, User
from lembas.security import api_tokens
from lembas.security.passwords import hash_password
from lembas.services import device_link, local_device, settings_store

DESKTOP = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 LLeMbasDesktop/0.4.0 (host={})"


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    yield
    device_link.reset()


# --- The User-Agent ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("agent", "host"),
    [
        (DESKTOP.format("workstation"), "workstation"),
        (DESKTOP.format("Work-Station.example_1"), "Work-Station.example_1"),
        ("LLeMbasDesktop/0.4.0 (host=box)", "box"),
        ("Mozilla/5.0 LLeMbasDesktop/1.0.0-beta.1 (host=box) Safari/605", "box"),
        (DESKTOP.format("a" * 253), "a" * 253),
    ],
)
def test_a_desktop_user_agent_names_its_host(agent, host):
    assert local_device.parse(agent) == host


@pytest.mark.parametrize(
    "agent",
    [
        None,
        "",
        "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0",
        DESKTOP.format(""),
        DESKTOP.format("a" * 254),
        DESKTOP.format("bad host"),
        DESKTOP.format("bad/host"),
        DESKTOP.format("böx"),
        DESKTOP.format("box<script>"),
        "LLeMbasDesktop/0.4.0 (hostname=box)",
        "LLeMbasDesktop/0.4.0(host=box)",
        "LLeMbasDesktop/ (host=box)",
        "XLLeMbasDesktop/0.4.0 (host=box)",
        "LLeMbasDesktop/0.4.0 (host=box",
        # Two tokens that might disagree: neither is believed.
        "LLeMbasDesktop/0.4.0 (host=a) LLeMbasDesktop/0.4.0 (host=b)",
    ],
)
def test_anything_else_names_no_host(agent):
    assert local_device.parse(agent) is None


def test_local_host_reads_the_request_headers():
    class Request:
        headers = {"user-agent": DESKTOP.format("box")}

    assert local_device.local_host(Request()) == "box"
    assert local_device.local_host(object()) is None


# --- Scaffolding -------------------------------------------------------------------
def _link(db, user, host: str, *, roots=("/work",)):
    token, _ = api_tokens.create(
        db, user, name=host, scopes=["models", "link"], kind=TOKEN_DEVICE, device_host=host
    )

    async def nothing(*_a, **_k):
        return None

    device_link.LINKS[token.id] = device_link.Link(
        token_id=token.id, user_id=user.id, name=host, send_text=nothing, close=nothing,
        hello={"device": {"host": host}, "limits": {"roots": list(roots)}},
    )
    return token


def _agents_on(db):
    """Agent chats on, and a model: the composer offers nothing without one."""
    from lembas.db.models import Connection, Model
    from lembas.services.crypto import encrypt

    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    connection = Connection(name="Test", base_url="http://127.0.0.1:1",
                            api_key_encrypted=encrypt(""))
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()


def _me(db) -> User:
    return db.scalar(select(User).where(User.email == "frodo@shire.test"))


def _stranger(db) -> User:
    user = User(name="gollum", email="gollum@shire.test", password_hash=hash_password("x"),
                role="user")
    db.add(user)
    db.commit()
    return user


def _options(html: str) -> list[str]:
    """The composer's device select, as its options read, in order."""
    select_at = html.index('name="ssh_profile_id"')
    body = html[select_at: html.index("</select>", select_at)]
    out = []
    for chunk in body.split("<option")[1:]:
        out.append(chunk.split(">", 1)[1].split("</option>", 1)[0].strip())
    return out


def _sections(html: str) -> list[str]:
    import re

    return re.findall(r'<span class="nav-group__name">([^<]*)</span>', html)


def _agent_sidebar(client):
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})


# --- The composer and the sidebar --------------------------------------------------
def test_in_desktop_this_computer_is_local_and_first(client, db, registered):
    _agents_on(db)
    me = _me(db)
    _link(db, me, "alpha")
    _link(db, me, "Zulu")
    _agent_sidebar(client)

    html = client.get("/chat?kind=agent",
                      headers={"user-agent": DESKTOP.format("zulu")}).text

    assert _options(html) == ["Local", "alpha"]
    assert 'title="This computer (Zulu)"' in html
    assert _sections(html)[:2] == ["Local", "alpha"]


def test_in_a_browser_nothing_changes(client, db, registered):
    _agents_on(db)
    me = _me(db)
    _link(db, me, "alpha")
    _link(db, me, "zulu")
    _agent_sidebar(client)

    html = client.get("/chat?kind=agent",
                      headers={"user-agent": "Mozilla/5.0 Firefox/140.0"}).text

    assert "Local" not in _options(html)
    assert sorted(_options(html)) == ["alpha", "zulu"]
    assert _sections(html)[:2] == ["alpha", "zulu"]
    assert "This computer (" not in html


def test_desktop_on_a_computer_with_no_device_relabels_nothing(client, db, registered):
    _agents_on(db)
    _link(db, _me(db), "alpha")
    _agent_sidebar(client)

    html = client.get("/chat?kind=agent", headers={"user-agent": DESKTOP.format("other")}).text

    assert _options(html) == ["alpha"]
    assert "Local" not in _sections(html)


def test_another_persons_device_with_the_same_host_is_never_shown(client, db, registered):
    _agents_on(db)
    _link(db, _stranger(db), "zulu")
    _link(db, _me(db), "alpha")
    _agent_sidebar(client)

    html = client.get("/chat?kind=agent", headers={"user-agent": DESKTOP.format("zulu")}).text

    assert _options(html) == ["alpha"]
    assert "Local" not in _sections(html)
    assert "zulu" not in html.lower().replace("gollum", "")


def test_the_label_is_set_per_request(client, db, registered):
    """A task reused for the next request must not keep the last one's host."""
    _agents_on(db)
    _link(db, _me(db), "zulu")
    client.get("/chat?kind=agent", headers={"user-agent": DESKTOP.format("zulu")})
    html = client.get("/chat?kind=agent", headers={"user-agent": "Mozilla/5.0"}).text
    assert _options(html) == ["zulu"]


# --- A device that allows no folder ------------------------------------------------
def test_a_device_with_no_roots_says_where_to_add_one(client, db, registered):
    _agents_on(db)
    token = _link(db, _me(db), "zulu", roots=())

    async def request(method, params=None, *, timeout=60):
        return {"path": "", "places": []}

    device_link.LINKS[token.id].request = request

    desktop = client.get(f"/api/devices/{token.id}/browse",
                         headers={"user-agent": DESKTOP.format("zulu")}).text
    assert "data-no-roots" in desktop and "This computer allows no folders yet" in desktop
    assert "<code>lembas remote add &lt;dir&gt;</code>" in desktop

    browser = client.get(f"/api/devices/{token.id}/browse").text
    assert "This device allows no folders yet" in browser


def test_a_device_with_roots_but_no_places_keeps_the_old_sentence(client, db, registered):
    _agents_on(db)
    token = _link(db, _me(db), "zulu", roots=("/gone",))

    async def request(method, params=None, *, timeout=60):
        return {"path": "", "places": []}

    device_link.LINKS[token.id].request = request
    html = client.get(f"/api/devices/{token.id}/browse").text
    assert "data-no-roots" not in html
    assert "This device offers no directories" in html


def test_an_agent_chats_header_says_local_in_desktop(client, db, registered, make_chat):
    from lembas.db.models import KIND_AGENT, Chat

    _agents_on(db)
    token = _link(db, _me(db), "zulu")
    chat = db.get(Chat, make_chat())
    chat.kind = KIND_AGENT
    chat.device_token_id = token.id
    chat.project_dir = "/work"
    db.commit()

    def where(html: str) -> str:
        at = html.index('class="topbar__where-name"')
        return html[at: html.index("</span>", at)]

    desktop = where(client.get(f"/chat/{chat.id}",
                               headers={"user-agent": DESKTOP.format("ZULU")}).text)
    assert "Local" in desktop and 'title="This computer (zulu)"' in desktop
    browser = where(client.get(f"/chat/{chat.id}").text)
    assert "zulu" in browser and "Local" not in browser
