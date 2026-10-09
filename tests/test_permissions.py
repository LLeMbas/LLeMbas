"""Groups, permissions and model access control."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Group, Model, User
from lembas.security import permissions
from lembas.services import chat as chat_service
from lembas.services import settings_store
from lembas.services.crypto import encrypt


@pytest.fixture
def admin(client: TestClient, registered) -> None:
    """The registered fixture already makes an administrator."""
    return None


@pytest.fixture
def plain_user(client: TestClient, db, registered) -> User:
    """A second, non-admin account. Leaves the client signed in as them."""
    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    return db.scalar(select(User).where(User.email == "sam@shire.test"))


def _connection(db) -> Connection:
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    return connection


def _model(db, model_id: str, **kwargs) -> Model:
    model = Model(connection_id=_connection(db).id, model_id=model_id, **kwargs)
    db.add(model)
    db.commit()
    return model


# --- Permission resolution ---------------------------------------------------
def test_admins_get_everything(db, registered):
    admin_user = db.scalar(select(User).where(User.role == "admin"))
    assert all(permissions.resolve(db, admin_user).values())


def test_signed_out_gets_nothing(db):
    assert not any(permissions.resolve(db, None).values())


def test_plain_user_gets_the_baseline(db, plain_user):
    resolved = permissions.resolve(db, plain_user)
    assert resolved["chat.create"] is True
    # Off in the baseline by default.
    assert resolved["chat.params"] is False


def test_a_group_widens_permissions(db, plain_user):
    group = Group(name="Power users", permissions_json={"chat.params": True})
    group.users = [plain_user]
    db.add(group)
    db.commit()
    assert permissions.resolve(db, plain_user)["chat.params"] is True


def test_groups_union_rather_than_override(db, plain_user):
    """A second group can only ever add. Absent means 'no opinion', not 'deny'."""
    db.add_all(
        [
            Group(name="A", permissions_json={"chat.params": True}, users=[plain_user]),
            Group(name="B", permissions_json={}, users=[plain_user]),
        ]
    )
    db.commit()
    assert permissions.resolve(db, plain_user)["chat.params"] is True


def test_baseline_can_be_narrowed_instance_wide(db, plain_user):
    settings_store.update(db, {"default_permissions": {"chat.create": False}})
    assert permissions.resolve(db, plain_user)["chat.create"] is False


def test_a_group_can_grant_back_what_the_baseline_removed(db, plain_user):
    settings_store.update(db, {"default_permissions": {"chat.create": False}})
    db.add(Group(name="Writers", permissions_json={"chat.create": True}, users=[plain_user]))
    db.commit()
    assert permissions.resolve(db, plain_user)["chat.create"] is True


# --- Enforcement through the API ---------------------------------------------
def test_creating_a_chat_is_refused_without_permission(client: TestClient, db, plain_user):
    settings_store.update(db, {"default_permissions": {"chat.create": False}})
    assert client.post("/api/chats/start", data={"content": "hi"}).status_code == 403
    assert db.scalar(select(Chat)) is None


def test_folder_routes_are_refused_without_permission(client: TestClient, db, plain_user):
    settings_store.update(db, {"default_permissions": {"folder.manage": False}})
    assert client.post("/api/folders", data={"name": "Nope"}).status_code == 403


def test_changing_sampling_is_refused_without_permission(
    client: TestClient, db, plain_user, make_chat
):
    _model(db, "test-model")
    chat_id = make_chat(email="sam@shire.test")
    response = client.patch(f"/api/chats/{chat_id}", data={"temperature": "0.9"})
    assert response.status_code == 403


def test_sampling_is_allowed_once_a_group_grants_it(client: TestClient, db, plain_user, make_chat):
    _model(db, "test-model")
    db.add(Group(name="Tuners", permissions_json={"chat.params": True}, users=[plain_user]))
    db.commit()

    chat_id = make_chat(email="sam@shire.test")
    assert client.patch(f"/api/chats/{chat_id}", data={"temperature": "0.9"}).status_code == 204

    chat = db.get(Chat, chat_id)
    db.refresh(chat)
    assert chat.params_json["temperature"] == 0.9


@pytest.mark.parametrize(
    ("field", "value"),
    [("temperature", "5"), ("top_p", "-1"), ("max_tokens", "0"), ("temperature", "abc")],
)
def test_out_of_range_parameters_are_dropped_not_clamped(
    client: TestClient, db, registered, field, value
, make_chat):
    """Silently changing what someone typed is worse than ignoring it."""
    _model(db, "test-model")
    chat_id = make_chat()
    client.patch(f"/api/chats/{chat_id}", data={field: value})

    chat = db.get(Chat, chat_id)
    db.refresh(chat)
    assert field not in (chat.params_json or {})


def test_an_empty_parameter_clears_it(client: TestClient, db, registered, make_chat):
    _model(db, "test-model")
    chat_id = make_chat()
    client.patch(f"/api/chats/{chat_id}", data={"temperature": "0.7"})
    client.patch(f"/api/chats/{chat_id}", data={"temperature": ""})

    chat = db.get(Chat, chat_id)
    db.refresh(chat)
    assert chat.params_json["temperature"] is None


# --- Model access ------------------------------------------------------------
def test_public_models_are_visible_to_everyone(db, plain_user):
    _model(db, "open-model", public=True)
    assert [m.model_id for m in permissions.models_visible_to(db, plain_user)] == ["open-model"]


def test_restricted_models_are_hidden_without_a_group(db, plain_user):
    _model(db, "secret-model", public=False)
    assert permissions.models_visible_to(db, plain_user) == []


def test_a_group_grants_access_to_a_restricted_model(db, plain_user):
    model = _model(db, "secret-model", public=False)
    group = Group(name="Insiders", users=[plain_user], models=[model])
    db.add(group)
    db.commit()
    assert [m.model_id for m in permissions.models_visible_to(db, plain_user)] == ["secret-model"]


def test_admins_see_restricted_models(db, registered):
    _model(db, "secret-model", public=False)
    admin_user = db.scalar(select(User).where(User.role == "admin"))
    assert [m.model_id for m in permissions.models_visible_to(db, admin_user)] == ["secret-model"]


def test_disabled_models_are_hidden_from_everyone(db, registered):
    _model(db, "off-model", enabled=False)
    admin_user = db.scalar(select(User).where(User.role == "admin"))
    assert permissions.models_visible_to(db, admin_user) == []


def test_switching_to_an_inaccessible_model_is_refused(
    client: TestClient, db, plain_user, make_chat
):
    """The picker is not the security boundary; a crafted request must fail."""
    _model(db, "open-model", public=True)
    _model(db, "secret-model", public=False)

    chat_id = make_chat(email="sam@shire.test")
    response = client.patch(f"/api/chats/{chat_id}", data={"model_id": "secret-model"})
    assert response.status_code == 403

    chat = db.get(Chat, chat_id)
    db.refresh(chat)
    assert chat.model_id == "open-model"


def test_model_select_permission_is_required_to_switch(
    client: TestClient, db, plain_user, make_chat
):
    _model(db, "a-model", public=True)
    _model(db, "b-model", public=True)
    settings_store.update(db, {"default_permissions": {"chat.model_select": False}})

    chat_id = make_chat(email="sam@shire.test")
    assert client.patch(f"/api/chats/{chat_id}", data={"model_id": "b-model"}).status_code == 403


# --- Ordering and defaults ---------------------------------------------------
def test_pinning_does_not_reorder_the_picker(db, registered):
    """Pinning is a sidebar shortcut. A picker whose order silently differs from
    the admin screen is just confusing."""
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="ordinary", position=0),
            Model(connection_id=connection.id, model_id="favourite", position=9, pinned=True),
        ]
    )
    db.commit()
    admin_user = db.scalar(select(User).where(User.role == "admin"))
    assert [m.model_id for m in chat_service.available_models(db, admin_user)] == [
        "ordinary",
        "favourite",
    ]


def test_position_decides_order_among_unpinned(db, registered):
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="second", position=1),
            Model(connection_id=connection.id, model_id="first", position=0),
        ]
    )
    db.commit()
    admin_user = db.scalar(select(User).where(User.role == "admin"))
    assert [m.model_id for m in chat_service.available_models(db, admin_user)] == [
        "first",
        "second",
    ]


def test_instance_default_model_is_used_for_new_chats(client: TestClient, db, registered):
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="first", position=0),
            Model(connection_id=connection.id, model_id="chosen", position=5),
        ]
    )
    db.commit()
    settings_store.update(db, {"default_model": "chosen"})

    client.post("/api/chats/start", data={"content": "hi"})
    assert db.scalar(select(Chat)).model_id == "chosen"


def test_a_users_own_default_beats_the_instance_default(client: TestClient, db, plain_user):
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="instance-pick", position=0),
            Model(connection_id=connection.id, model_id="my-pick", position=5),
        ]
    )
    db.commit()
    settings_store.update(db, {"default_model": "instance-pick"})
    client.post("/api/preferences/default-model", data={"model_id": "my-pick"})

    client.post("/api/chats/start", data={"content": "hi"})
    assert db.scalar(select(Chat)).model_id == "my-pick"


def test_an_unreachable_default_falls_through(db, plain_user):
    """A default the user has lost access to must not produce a dead chat."""
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="allowed", position=0, public=True),
            Model(connection_id=connection.id, model_id="gone", position=1, public=False),
        ]
    )
    db.commit()
    settings_store.update(db, {"default_model": "gone"})
    assert chat_service.default_model(db, plain_user)[0] == "allowed"


def test_choosing_an_inaccessible_default_is_refused(client: TestClient, db, plain_user):
    _model(db, "secret-model", public=False)
    response = client.post(
        "/api/preferences/default-model", data={"model_id": "secret-model"}, follow_redirects=False
    )
    assert "error=" in response.headers["location"]
    db.refresh(plain_user)
    assert "default_model" not in (plain_user.settings_json or {})


# --- Admin guards ------------------------------------------------------------
def test_ordinary_users_cannot_reach_user_administration(client: TestClient, db, plain_user):
    for path in ("/admin/users", "/admin/groups", "/admin/models"):
        assert client.get(path, follow_redirects=False).status_code == 403, path


def test_the_last_administrator_cannot_be_demoted(client: TestClient, db, registered):
    admin_user = db.scalar(select(User).where(User.role == "admin"))
    response = client.post(
        f"/admin/users/{admin_user.id}",
        data={"name": admin_user.name, "role": "user", "active": "true"},
        follow_redirects=False,
    )
    assert "only%20administrator" in response.headers["location"]

    db.refresh(admin_user)
    assert admin_user.role == "admin"


def test_you_cannot_delete_your_own_account(client: TestClient, db, registered):
    admin_user = db.scalar(select(User).where(User.role == "admin"))
    response = client.post(
        f"/admin/users/{admin_user.id}/delete", follow_redirects=False
    )
    assert "your%20own%20account" in response.headers["location"]
    assert db.get(User, admin_user.id) is not None


def test_deactivating_a_user_revokes_their_sessions(client: TestClient, db, registered):
    """Otherwise the change only lands when their cookie happens to expire."""
    other = TestClient(client.app)
    other.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    assert other.get("/chat", follow_redirects=False).status_code == 200

    target = db.scalar(select(User).where(User.email == "sam@shire.test"))
    client.post(
        f"/admin/users/{target.id}",
        data={"name": target.name, "role": "user"},  # `active` absent means off
        follow_redirects=False,
    )

    assert other.get("/chat", follow_redirects=False).status_code == 303


def test_a_pinned_model_appears_once_in_the_picker(client: TestClient, db, registered, make_chat):
    """Listing a model twice would let two rows claim to be selected."""
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="favourite", pinned=True, position=0),
            Model(connection_id=connection.id, model_id="ordinary", position=1),
        ]
    )
    db.commit()

    chat_id = make_chat()
    page = client.get(f"/chat/{chat_id}").text
    assert page.count('data-picker-value="favourite"') == 1
    assert page.count('data-picker-value="ordinary"') == 1


# --- System prompt layering --------------------------------------------------
def test_system_prompt_falls_back_to_the_instance(db, user_id):
    connection = _connection(db)
    db.add(Model(connection_id=connection.id, model_id="m"))
    db.commit()
    settings_store.update(db, {"system_prompt": "You are terse."})

    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()
    assert chat_service.effective_system_prompt(db, chat) == "You are terse."


def test_a_models_prompt_beats_the_instance(db, user_id):
    connection = _connection(db)
    db.add(Model(connection_id=connection.id, model_id="m", system_prompt="You are a poet."))
    db.commit()
    settings_store.update(db, {"system_prompt": "You are terse."})

    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()
    assert chat_service.effective_system_prompt(db, chat) == "You are a poet."


def test_a_chats_prompt_beats_everything(db, user_id):
    connection = _connection(db)
    db.add(Model(connection_id=connection.id, model_id="m", system_prompt="You are a poet."))
    db.commit()
    settings_store.update(db, {"system_prompt": "You are terse."})

    chat = Chat(
        user_id=user_id, model_id="m", connection_id=connection.id,
        system_prompt="You are a dwarf.",
    )
    db.add(chat)
    db.commit()
    assert chat_service.effective_system_prompt(db, chat) == "You are a dwarf."


def test_layers_replace_rather_than_stack(db, user_id):
    """Concatenating them reads well in a settings screen and badly in practice:
    two layers that disagree give the model contradictory instructions."""
    connection = _connection(db)
    db.add(Model(connection_id=connection.id, model_id="m", system_prompt="MODEL"))
    db.commit()
    settings_store.update(db, {"system_prompt": "INSTANCE"})

    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()
    assert "INSTANCE" not in chat_service.effective_system_prompt(db, chat)


def test_no_prompt_anywhere_sends_no_authored_prompt(db, user_id):
    """A system message still goes out -- the harness carries the date -- but
    nothing an administrator or the user wrote is in it, and there is no seam
    line introducing a prompt that does not exist."""
    connection = _connection(db)
    db.add(Model(connection_id=connection.id, model_id="m"))
    db.commit()

    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()

    messages = chat_service.build_request(db, chat)["messages"]
    assert [m for m in messages if m["role"] != "system"] == []
    assert chat_service.effective_system_prompt(db, chat) == ""
    assert "---" not in messages[0]["content"]


# --- Admin bulk actions ------------------------------------------------------
def test_bulk_disable_works(client: TestClient, db, registered):
    """Regression: /admin/models/{model_id} was registered first, so "bulk" was
    parsed as a model id and every bulk action 404'd."""
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="a"),
            Model(connection_id=connection.id, model_id="b"),
        ]
    )
    db.commit()
    ids = [m.id for m in db.scalars(select(Model))]

    response = client.post(
        "/admin/models/bulk", data={"action": "disable", "model_ids": ids},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert all(not m.enabled for m in db.scalars(select(Model)))


def test_bulk_restrict_then_publish(client: TestClient, db, registered):
    connection = _connection(db)
    db.add(Model(connection_id=connection.id, model_id="a"))
    db.commit()
    model = db.scalar(select(Model))

    client.post("/admin/models/bulk", data={"action": "private", "model_ids": [model.id]})
    db.refresh(model)
    assert model.public is False

    client.post("/admin/models/bulk", data={"action": "public", "model_ids": [model.id]})
    db.refresh(model)
    assert model.public is True


def test_bulk_with_nothing_selected_is_harmless(client: TestClient, db, registered):
    assert client.post(
        "/admin/models/bulk", data={"action": "disable"}, follow_redirects=False
    ).status_code == 303


# --- The model admin list ----------------------------------------------------
def _many_models(db, count: int) -> None:
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id=f"model-{i:03d}", position=i)
            for i in range(count)
        ]
    )
    db.commit()


def test_the_list_paginates_rather_than_rendering_everything(
    client: TestClient, db, registered
):
    """A hundred models must not become a hundred forms on one page."""
    from lembas.api.admin_models import PAGE_SIZE

    _many_models(db, PAGE_SIZE + 15)
    page = client.get("/admin/models").text

    assert page.count('name="model_ids"') == PAGE_SIZE
    assert f"of {PAGE_SIZE + 15}" in page
    assert "Page 1 of 2" in page


def test_the_second_page_shows_the_remainder(client: TestClient, db, registered):
    from lembas.api.admin_models import PAGE_SIZE

    _many_models(db, PAGE_SIZE + 15)
    page = client.get("/admin/models?page=2").text
    assert page.count('name="model_ids"') == 15


def test_an_out_of_range_page_is_clamped(client: TestClient, db, registered):
    _many_models(db, 5)
    assert "Page 1 of 1" not in client.get("/admin/models?page=99").text
    assert client.get("/admin/models?page=99").status_code == 200


def test_the_list_does_not_render_edit_forms(client: TestClient, db, registered):
    """The whole point of the split: rows link to a page, they are not forms."""
    _many_models(db, 3)
    page = client.get("/admin/models").text
    assert 'name="system_prompt"' not in page
    assert 'name="display_name"' not in page
    assert page.count("/edit") >= 3


def test_search_narrows_the_list(client: TestClient, db, registered):
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="llama-large", position=0),
            Model(connection_id=connection.id, model_id="qwen-small", position=1),
        ]
    )
    db.commit()

    page = client.get("/admin/models?q=qwen").text
    assert "qwen-small" in page
    assert "llama-large" not in page


def test_search_matches_the_display_name_too(client: TestClient, db, registered):
    connection = _connection(db)
    db.add(
        Model(connection_id=connection.id, model_id="abc-123", display_name="Friendly Name")
    )
    db.commit()
    assert "abc-123" in client.get("/admin/models?q=friendly").text


def test_filter_tabs_narrow_the_list(client: TestClient, db, registered):
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="on-model", position=0),
            Model(connection_id=connection.id, model_id="off-model", position=1, enabled=False),
        ]
    )
    db.commit()

    disabled = client.get("/admin/models?filter=disabled").text
    assert "off-model" in disabled
    assert ">on-model" not in disabled


def test_filtering_by_connection(client: TestClient, db, registered):
    first = _connection(db)
    second = _connection(db)
    db.add_all(
        [
            Model(connection_id=first.id, model_id="from-first", position=0),
            Model(connection_id=second.id, model_id="from-second", position=1),
        ]
    )
    db.commit()

    page = client.get(f"/admin/models?connection={second.id}").text
    assert "from-second" in page
    assert "from-first" not in page


# --- The model detail page ---------------------------------------------------
def test_the_detail_page_carries_the_full_form(client: TestClient, db, registered):
    _many_models(db, 3)
    db.add(Group(name="Insiders"))
    db.commit()
    model = db.scalar(select(Model).where(Model.model_id == "model-001"))

    page = client.get(f"/admin/models/{model.id}/edit").text
    for field in ('name="display_name"', 'name="system_prompt"', 'name="capability"',
                  'name="group_ids"', 'name="position"'):
        assert field in page, field


def test_the_detail_page_links_to_its_neighbours(client: TestClient, db, registered):
    _many_models(db, 3)
    first, middle, last = db.scalars(select(Model).order_by(Model.position)).all()

    page = client.get(f"/admin/models/{middle.id}/edit").text
    assert f"/admin/models/{first.id}/edit" in page
    assert f"/admin/models/{last.id}/edit" in page
    assert "2 of 3" in page


def test_an_unknown_model_detail_is_404(client: TestClient, db, registered):
    assert client.get("/admin/models/nope/edit").status_code == 404


def test_saving_from_the_detail_page_returns_to_it(client: TestClient, db, registered):
    _many_models(db, 2)
    model = db.scalar(select(Model).where(Model.model_id == "model-000"))

    response = client.post(
        f"/admin/models/{model.id}",
        data={"display_name": "Renamed", "enabled": "true", "public": "true"},
        follow_redirects=False,
    )
    assert response.headers["location"].startswith(f"/admin/models/{model.id}/edit")

    db.refresh(model)
    assert model.display_name == "Renamed"


def test_typing_a_position_moves_the_model(client: TestClient, db, registered):
    """Up/down is unusable for moving a model 60 places."""
    _many_models(db, 5)
    last = db.scalar(select(Model).where(Model.model_id == "model-004"))

    client.post(
        f"/admin/models/{last.id}",
        data={"display_name": "", "enabled": "true", "public": "true", "position": "1"},
    )
    order = [m.model_id for m in db.scalars(select(Model).order_by(Model.position))]
    assert order[0] == "model-004"


def test_a_nonsense_position_is_ignored(client: TestClient, db, registered):
    _many_models(db, 3)
    model = db.scalar(select(Model).where(Model.model_id == "model-000"))

    client.post(
        f"/admin/models/{model.id}",
        data={"display_name": "", "enabled": "true", "public": "true", "position": "abc"},
    )
    db.refresh(model)
    assert model.position == 0


def test_moving_returns_to_the_filtered_view(client: TestClient, db, registered):
    _many_models(db, 3)
    model = db.scalar(select(Model).where(Model.model_id == "model-001"))

    response = client.post(
        f"/admin/models/{model.id}/move",
        data={"direction": "up", "back": "/admin/models?filter=enabled&page=2"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/admin/models?filter=enabled&page=2"


def test_the_new_chat_composer_preselects_the_default_model(
    client: TestClient, db, registered
):
    """It must match what /start would actually pick. Showing a different model
    also mis-reports whether images will be sent."""
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="first-in-order", position=0),
            Model(connection_id=connection.id, model_id="the-default", position=7),
        ]
    )
    db.commit()
    settings_store.update(db, {"default_model": "the-default"})

    page = client.get("/chat").text
    assert 'data-picker-value="the-default"' in page
    # And the hidden field the composer submits carries it too.
    assert 'name="model_id" value="the-default"' in page


def test_the_composer_respects_an_explicit_model_query(client: TestClient, db, registered):
    connection = _connection(db)
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="default-one", position=0),
            Model(connection_id=connection.id, model_id="asked-for", position=1),
        ]
    )
    db.commit()
    settings_store.update(db, {"default_model": "default-one"})

    page = client.get("/chat?model=asked-for").text
    assert 'name="model_id" value="asked-for"' in page


def test_the_vision_warning_follows_the_preselected_model(client: TestClient, db, registered):
    connection = _connection(db)
    db.add(
        Model(
            connection_id=connection.id, model_id="blind-model", position=0,
            capabilities_json={"vision": False},
        )
    )
    db.commit()
    assert "has no vision" in client.get("/chat").text
