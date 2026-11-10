"""The `/api/v2` routes the apps need to catch up with the web (the parity
audit's server half): pinning, folders and moving a chat, search, memory, the
library's writes, language and theme, and a chat's switches. Each goes through
the core its page twin uses, so what holds on the web holds here."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import Chat, User
from lembas.security import api_tokens


@pytest.fixture(autouse=True)
def _fresh_rates():
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()
    yield


def _bearer(db, email="frodo@shire.test", scopes=("app",)) -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == email))
    _, raw = api_tokens.create(db, user, name="LLeMbas for Android", scopes=list(scopes))
    return {"Authorization": f"Bearer {raw}"}


# --- Pin ---------------------------------------------------------------------------------------
def test_a_chat_is_pinned_and_unpinned_through_patch(client, db, registered, make_chat):
    chat_id = make_chat()
    headers = _bearer(db)
    pinned = client.patch(f"/api/v2/chats/{chat_id}", headers=headers, json={"pinned": True})
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["chat"]["pinned"] is True
    # Absent leaves it alone.
    renamed = client.patch(f"/api/v2/chats/{chat_id}", headers=headers, json={"title": "Kept"})
    assert renamed.json()["chat"]["pinned"] is True
    listed = client.get("/api/v2/chats", headers=headers).json()["chats"]
    assert [c["pinned"] for c in listed if c["id"] == chat_id] == [True]
    client.patch(f"/api/v2/chats/{chat_id}", headers=headers, json={"pinned": False})
    db.expire_all()
    assert db.get(Chat, chat_id).pinned is False


def test_the_web_pin_moves_the_row_into_pinned_chats(client, db, registered, make_chat):
    first, second = make_chat(), make_chat()
    page = client.get("/chat").text
    assert 'data-testid="pinned-chats"' not in page
    assert 'data-testid="chat-pin"' in page
    response = client.patch(f"/api/chats/{second}", data={"pinned": "1"})
    # The sidebar comes back to swap in, as for archiving.
    assert response.status_code == 200 and 'id="sidebar-tree"' in response.text
    pinned_part, _, rest = response.text.partition('id="section-chats"')
    assert 'data-testid="pinned-chats"' in pinned_part and second in pinned_part
    assert second not in rest and first in rest
    client.patch(f"/api/chats/{second}", data={"pinned": "0"})
    db.expire_all()
    assert db.get(Chat, second).pinned is False
