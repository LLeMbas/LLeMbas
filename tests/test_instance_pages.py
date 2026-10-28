"""Search over files, the index card, the palette, invites, the first-run wizard,
the logo, Python in the browser, and `@` over the tree."""

from __future__ import annotations

from sqlalchemy import select

from lembas.db.models import Invite, User
from lembas.services import fs, settings_store

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00"
    b"\x00\x00\x00IEND\xaeB`\x82"
)


def _me(db):
    return db.scalars(select(User).order_by(User.created_at)).first()


# --- Search and the index ------------------------------------------------------------


def test_search_has_a_files_tab(client, db, registered):
    page = client.get("/search?q=anything").text
    assert 'href="/search?in=files' in page


def test_a_files_search_never_fails_without_an_index(client, db, registered):
    assert client.get("/search?in=files&q=ring").status_code == 200


def test_the_extraction_page_has_the_index_card_and_no_piece_size(client, db, registered):
    page = client.get("/admin/extraction").text
    assert 'data-testid="index-card"' in page
    assert 'name="chunk_chars"' not in page and 'name="chunk_overlap"' not in page
    assert "chunk_chars" not in settings_store.defaults(settings_store.EXTRACTION)


def test_the_index_can_be_rebuilt(client, db, registered, monkeypatch):
    from lembas.services import index

    called = []
    monkeypatch.setattr(index, "rebuild", lambda: called.append(True))
    response = client.post("/admin/extraction/index/rebuild", follow_redirects=False)
    assert response.status_code == 303 and called


# --- The palette ---------------------------------------------------------------------


def test_the_palette_offers_chats_models_and_settings(client, db, registered, make_chat):
    from lembas.db.models import Chat

    chat = db.get(Chat, make_chat())
    chat.title = "Council of Elrond"
    db.commit()
    items = client.get("/api/palette?q=council").json()["items"]
    assert any(i["kind"] == "chat" and i["url"] == f"/chat/{chat.id}" for i in items)
    settings = client.get("/api/palette?q=appearance").json()["items"]
    assert any(i["url"] == "/settings?tab=appearance" for i in settings)


def test_the_palette_offers_admin_pages_to_admins_only(client, db, registered):
    items = client.get("/api/palette?q=invites").json()["items"]
    assert any(i["url"] == "/admin/invites" for i in items)
    user = _me(db)
    user.role = "user"
    db.commit()
    items = client.get("/api/palette?q=invites").json()["items"]
    assert not any(i["url"].startswith("/admin") for i in items)


# --- Invites and the first run ----------------------------------------------------------


def test_an_invite_link_is_shown_once_and_signs_up_once(client, db, registered):
    page = client.post("/admin/invites", data={"email": "", "role": "member", "days": "7"})
    assert page.status_code == 200
    assert 'data-testid="invite-link"' in page.text
    link = page.text.split('/invite/', 1)[1].split('"', 1)[0]
    # Not shown again.
    assert link not in client.get("/admin/invites").text
    client.post("/auth/logout")
    form = client.get(f"/invite/{link}")
    assert 'data-testid="invite-accept"' in form.text
    joined = client.post(
        f"/invite/{link}",
        data={"name": "Sam", "email": "sam@shire.test", "password": "po-tay-toes!"},
        follow_redirects=False,
    )
    assert joined.status_code == 303
    client.post("/auth/logout")
    again = client.post(
        f"/invite/{link}",
        data={"name": "Gollum", "email": "smeagol@shire.test", "password": "my-precious!"},
        follow_redirects=False,
    )
    assert again.status_code == 400
    assert db.scalar(select(User).where(User.email == "smeagol@shire.test")) is None


def test_an_invite_can_be_revoked(client, db, registered):
    client.post("/admin/invites", data={"role": "member", "days": "1"})
    invite = db.scalars(select(Invite)).first()
    client.post(f"/admin/invites/{invite.id}/revoke")
    db.expire_all()
    assert db.scalars(select(Invite)).first() is None


def test_a_member_cannot_make_invites(client, db, registered):
    user = _me(db)
    user.role = "user"
    user.org_role = "member"
    db.commit()
    assert client.get("/admin/invites").status_code == 403


def test_the_first_account_lands_on_the_wizard(client):
    response = client.post(
        "/auth/register",
        data={"name": "Gandalf", "email": "g@grey.test", "password": "you-shall-not-pass"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/setup"
    page = client.get("/setup")
    assert 'data-testid="setup-form"' in page.text
    done = client.post(
        "/setup",
        data={"organisation": "The Fellowship", "instance_name": "Rivendell"},
        follow_redirects=False,
    )
    assert done.headers["location"].startswith("/admin/connections")


# --- The logo, Web Push, Python ----------------------------------------------------------


def test_a_logo_replaces_the_mark(client, db, registered):
    response = client.post(
        "/admin/general/logo", files={"logo": ("l.png", PNG, "image/png")}, follow_redirects=False
    )
    assert response.status_code == 303
    served = client.get("/brand/logo")
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert "brand-logo" in client.get("/chat").text
    client.post("/admin/general/logo/delete")
    assert client.get("/brand/logo").status_code == 404


def test_an_svg_or_a_fake_image_is_refused_as_a_logo(client, db, registered):
    for name, data in (("l.svg", b"<svg onload='x'/>"), ("l.png", b"not a png")):
        response = client.post(
            "/admin/general/logo", files={"logo": (name, data, "image/png")}, follow_redirects=False
        )
        assert "error=" in response.headers["location"]


def test_general_has_the_push_note_and_push_is_off(client, db, registered):
    page = client.get("/admin/general").text
    assert 'data-testid="push-card"' in page and "relay" in page
    assert not settings_store.web_push_enabled(db)


def test_python_is_never_offered_without_pyodide(client, db, registered, make_chat):
    from lembas.api import pyodide
    from lembas.db.models import Chat

    settings_store.update(db, {"pyodide_enabled": True})
    chat = db.get(Chat, make_chat())
    assert not pyodide.available()
    assert client.post(f"/chat/{chat.id}/page/announce",
                       json={"page_id": "abcdef123456"}).status_code == 409
    assert "data-python-runner" not in client.get(f"/chat/{chat.id}").text


def test_the_page_protocol_is_the_chats_owners_only(client, db, registered, make_chat,
                                                    monkeypatch):
    from lembas.api import pyodide
    from lembas.db.models import Chat
    from lembas.services import organisations, pages

    monkeypatch.setattr(pyodide, "available", lambda: True)
    settings_store.update(db, {"pyodide_enabled": True})
    other = organisations.create_account(
        db, name="Merry", email="merry@shire.test", password="brandybuck!!"
    )
    db.commit()
    theirs = Chat(user_id=other.id, model_id="")
    db.add(theirs)
    db.commit()
    assert client.post(f"/chat/{theirs.id}/page/announce",
                       json={"page_id": "abcdef123456"}).status_code == 404
    mine = db.get(Chat, make_chat())
    assert client.post(f"/chat/{mine.id}/page/announce",
                       json={"page_id": "abcdef123456"}).status_code == 204
    assert pages.attached(mine.id)
    client.post(f"/chat/{mine.id}/page/withdraw", json={"page_id": "abcdef123456"})
    assert not pages.attached(mine.id)


# --- `@` over the tree ------------------------------------------------------------------


def test_at_offers_files_the_chat_can_read(client, db, registered, make_chat):
    user = _me(db)
    home = fs.ensure_home(db, user)
    fs.write(fs.view(db, user), f"{home.path}/roadmap.md", b"# Road")
    db.commit()
    chat_id = make_chat()
    page = client.get(f"/api/files/mention-picker?q=road&chat_id={chat_id}").text
    assert 'data-mention-tree' in page and f"{home.path}/roadmap.md" in page
