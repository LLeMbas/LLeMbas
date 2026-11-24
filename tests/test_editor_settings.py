"""The two models an editor works with: autocomplete, and main.

Through `/v1/lembas/editor` (an editor's door), `/api/v2/editor` (the apps'),
a person's settings page and the administrator's model page -- one store
behind all four (`services/editor.py`).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from lembas.db.migrations import sync_schema
from lembas.db.models import Connection, Model, User
from lembas.db.session import get_engine
from lembas.security import api_tokens
from lembas.services import editor, settings_store
from lembas.services.crypto import encrypt
from tests.conftest import join

V1 = "/v1/lembas/editor"
V2 = "/api/v2/editor"


@pytest.fixture(autouse=True)
def _fresh():
    api_tokens.reset_rates()
    yield


def _me(db) -> User:
    return db.scalar(select(User).where(User.email == "frodo@shire.test"))


def _models(db, *names, **fields) -> dict[str, Model]:
    connection = db.scalar(select(Connection).where(Connection.name == "local"))
    if connection is None:
        connection = Connection(name="local", base_url="http://upstream.test/v1",
                                api_key_encrypted=encrypt(""))
        db.add(connection)
        db.commit()
    rows = {}
    for position, name in enumerate(names):
        rows[name] = Model(connection_id=connection.id, model_id=name, position=position,
                           **fields)
        db.add(rows[name])
    db.commit()
    return rows


def _auth(db, *scopes, user=None) -> dict[str, str]:
    _, raw = api_tokens.create(db, user or _me(db), name="editor",
                               scopes=list(scopes or ("models",)))
    return {"Authorization": f"Bearer {raw}"}


EMPTY = {"autocomplete": {"model": None, "source": None, "allowed": None},
         "main": {"model": None}}


# --- /v1/lembas/editor -------------------------------------------------------------
def test_nothing_chosen_is_nulls_and_every_model_allowed(client, db, registered):
    _models(db, "small", "large")
    assert client.get(V1, headers=_auth(db)).json() == EMPTY


def test_the_door_is_a_token_with_the_models_scope(client, db, registered):
    _models(db, "small")
    assert client.get(V1).status_code == 401
    assert client.get(V1, headers=_auth(db, "memory")).status_code == 403
    assert client.put(V1, headers=_auth(db, "memory"), json={}).status_code == 403


def test_a_person_sets_both_and_reads_them_back(client, db, registered):
    _models(db, "small", "large")
    auth = _auth(db)
    answer = client.put(V1, headers=auth, json={"autocomplete_model": "local/small",
                                                "main_model": "local/large"})
    assert answer.status_code == 200, answer.text
    wanted = {"autocomplete": {"model": "local/small", "source": "person", "allowed": None},
              "main": {"model": "local/large"}}
    assert answer.json() == wanted
    assert client.get(V1, headers=auth).json() == wanted
    # In the account, as refs: it follows the person to every editor.
    stored = _me(db).settings_json
    assert stored["autocomplete_model"] == "local/small"
    assert stored["editor_main_model"] == "local/large"


def test_a_field_left_out_is_kept_and_null_clears(client, db, registered):
    _models(db, "small", "large")
    auth = _auth(db)
    client.put(V1, headers=auth, json={"autocomplete_model": "small", "main_model": "large"})
    # The older, bare id is taken and stored as the ref.
    assert client.get(V1, headers=auth).json()["autocomplete"]["model"] == "local/small"
    after = client.put(V1, headers=auth, json={"main_model": None}).json()
    assert after["autocomplete"]["model"] == "local/small" and after["main"]["model"] is None
    after = client.put(V1, headers=auth, json={"autocomplete_model": None}).json()
    assert after == EMPTY


def test_the_instance_default_answers_until_the_person_chooses(client, db, registered):
    rows = _models(db, "small", "large")
    editor.set_instance_default(db, rows["small"])
    auth = _auth(db)
    assert client.get(V1, headers=auth).json()["autocomplete"] == {
        "model": "local/small", "source": "instance", "allowed": None}
    client.put(V1, headers=auth, json={"autocomplete_model": "local/large"})
    assert client.get(V1, headers=auth).json()["autocomplete"] == {
        "model": "local/large", "source": "person", "allowed": None}
    # null: back to the instance's.
    back = client.put(V1, headers=auth, json={"autocomplete_model": None}).json()
    assert back["autocomplete"]["source"] == "instance"


def test_marked_models_are_the_only_ones_allowed(client, db, registered):
    rows = _models(db, "small", "medium", "large")
    rows["small"].autocomplete = rows["medium"].autocomplete = True
    db.commit()
    auth = _auth(db)
    assert client.get(V1, headers=auth).json()["autocomplete"]["allowed"] == [
        "local/small", "local/medium"]
    refused = client.put(V1, headers=auth, json={"autocomplete_model": "local/large",
                                                 "main_model": "local/large"})
    assert refused.status_code == 403
    assert "not allowed for autocomplete" in refused.json()["error"]["message"]
    # Refused whole: the main model in the same request was not written either.
    assert client.get(V1, headers=auth).json()["main"]["model"] is None
    # The main model is any chat model, marked or not.
    assert client.put(V1, headers=auth, json={"main_model": "local/large"}).status_code == 200


def test_a_choice_that_stopped_being_allowed_is_no_longer_a_choice(client, db, registered):
    rows = _models(db, "small", "large")
    auth = _auth(db)
    client.put(V1, headers=auth, json={"autocomplete_model": "local/large"})
    rows["small"].autocomplete = True
    db.commit()
    assert client.get(V1, headers=auth).json()["autocomplete"] == {
        "model": None, "source": None, "allowed": ["local/small"]}


def test_a_model_that_is_gone_is_not_a_choice(client, db, registered):
    rows = _models(db, "small", "large")
    auth = _auth(db)
    client.put(V1, headers=auth, json={"autocomplete_model": "local/small",
                                       "main_model": "local/small"})
    db.delete(rows["small"])
    db.commit()
    assert client.get(V1, headers=auth).json() == EMPTY


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"autocomplete_model": "local/nothing"}, 404),
        ({"main_model": "local/nothing"}, 404),
        ({"main_model": "local/vectors"}, 400),
        ({"autocomplete_model": 7}, 400),
    ],
)
def test_what_cannot_be_chosen_is_refused(client, db, registered, body, status):
    _models(db, "small")
    _models(db, "vectors", kind="embedding")
    answer = client.put(V1, headers=_auth(db), json=body)
    assert answer.status_code == status and answer.json()["error"]["message"]


def test_another_persons_private_model_cannot_be_chosen(client, db, registered):
    from lembas.db.models import Group

    rows = _models(db, "small", "private")
    rows["private"].public = False
    rows["private"].groups = [Group(name="Others")]
    rows["private"].autocomplete = True
    db.commit()
    settings_store.update(db, {"default_permissions": {"api.use": True}})
    join(client, {"name": "Sam", "email": "sam@shire.test", "password": "second-breakfast-now"})
    sam = db.scalar(select(User).where(User.email == "sam@shire.test"))
    auth = _auth(db, user=sam)
    # Marked models exist, and Sam can see none of them: none is allowed, and
    # the one that exists is not named to him.
    assert client.get(V1, headers=auth).json()["autocomplete"]["allowed"] == []
    assert client.put(V1, headers=auth,
                      json={"autocomplete_model": "local/private"}).status_code == 404


# --- /api/v2/editor ------------------------------------------------------------------
def test_the_apps_door_gives_the_same_answer(client, db, registered):
    rows = _models(db, "small", "large")
    editor.set_instance_default(db, rows["small"])
    app = _auth(db, "app")
    assert client.get(V2, headers=app).json() == client.get(V1, headers=_auth(db)).json()
    set_ = client.put(V2, headers=app, json={"autocomplete_model": "local/large",
                                             "main_model": "local/small"})
    assert set_.status_code == 200
    assert set_.json() == {
        "autocomplete": {"model": "local/large", "source": "person", "allowed": None},
        "main": {"model": "local/small"}}
    assert client.get(V1, headers=_auth(db)).json() == set_.json()


def test_the_apps_door_wants_the_app_scope_and_refuses_in_detail(client, db, registered):
    rows = _models(db, "small", "large")
    rows["small"].autocomplete = True
    db.commit()
    assert client.get(V2, headers=_auth(db, "models")).status_code == 403
    app = _auth(db, "app")
    refused = client.put(V2, headers=app, json={"autocomplete_model": "local/large"})
    assert refused.status_code == 403 and "autocomplete" in refused.json()["detail"]
    assert client.put(V2, headers=app, json={"main_model": "x/y"}).status_code == 404


# --- The administrator's page ------------------------------------------------------------
def test_the_model_page_has_the_mark_and_saves_it(client, db, registered):
    rows = _models(db, "small", "large")
    page = client.get(f"/admin/models/{rows['small'].id}/capabilities")
    assert page.status_code == 200
    assert 'data-testid="model-autocomplete"' in page.text
    assert 'name="autocomplete"' in page.text and "Allowed for autocomplete" in page.text
    saved = client.post(f"/admin/models/{rows['small'].id}",
                        data={"tab": "capabilities", "autocomplete": "true"},
                        follow_redirects=False)
    assert saved.status_code == 303
    db.expire_all()
    assert rows["small"].autocomplete is True
    # Unticked and saved: no longer marked.
    client.post(f"/admin/models/{rows['small'].id}", data={"tab": "capabilities"})
    db.expire_all()
    assert rows["small"].autocomplete is False


def test_saving_another_tab_leaves_the_mark_alone(client, db, registered):
    rows = _models(db, "small", autocomplete=True)
    client.post(f"/admin/models/{rows['small'].id}", data={"tab": "limits"})
    db.expire_all()
    assert rows["small"].autocomplete is True


def test_an_embedding_model_has_no_autocomplete_card(client, db, registered):
    rows = _models(db, "vectors", kind="embedding")
    page = client.get(f"/admin/models/{rows['vectors'].id}/capabilities")
    if page.status_code == 200:
        assert 'data-testid="model-autocomplete"' not in page.text
    made = client.post(f"/admin/models/{rows['vectors'].id}/autocomplete-default",
                       follow_redirects=False)
    assert made.status_code == 303
    assert editor.instance_default(db) == ""


def test_an_administrator_makes_and_clears_the_instance_default(client, db, registered):
    rows = _models(db, "small", "large")
    url = f"/admin/models/{rows['small'].id}/autocomplete-default"
    made = client.post(url, follow_redirects=False)
    assert made.status_code == 303 and "/capabilities" in made.headers["location"]
    assert editor.instance_default(db) == "local/small"
    db.expire_all()
    # Nothing was marked, so nothing is: every model stays allowed.
    assert rows["small"].autocomplete is False
    page = client.get(f"/admin/models/{rows['small'].id}/capabilities").text
    assert "autocomplete default" in page and 'name="clear"' in page
    other = client.get(f"/admin/models/{rows['large'].id}/capabilities").text
    assert "The default is local/small" in other
    client.post(url, data={"clear": "1"})
    assert editor.instance_default(db) == ""


def test_where_models_are_marked_the_new_default_is_marked_too(client, db, registered):
    rows = _models(db, "small", "large")
    rows["small"].autocomplete = True
    db.commit()
    client.post(f"/admin/models/{rows['large'].id}/autocomplete-default")
    db.expire_all()
    assert rows["large"].autocomplete is True
    assert editor.autocomplete(db, _me(db)).source == "instance"


def test_only_an_administrator_sets_the_default(client, db, registered):
    rows = _models(db, "small")
    join(client, {"name": "Sam", "email": "sam@shire.test", "password": "second-breakfast-now"})
    refused = client.post(f"/admin/models/{rows['small'].id}/autocomplete-default",
                          follow_redirects=False)
    assert refused.status_code in (302, 303, 403, 404)
    assert editor.instance_default(db) == ""


# --- A person's settings page --------------------------------------------------------------
def test_the_settings_page_has_the_two_entries(client, db, registered):
    rows = _models(db, "small", "large")
    editor.set_instance_default(db, rows["small"])
    page = client.get("/settings?tab=models").text
    assert 'id="editor-models"' in page
    assert 'name="autocomplete_model"' in page and 'name="main_model"' in page
    assert "Use the instance default (small)" in page
    assert '<option value="local/large"' in page


def test_the_settings_page_saves_and_shows_the_choice(client, db, registered):
    _models(db, "small", "large")
    saved = client.post("/api/preferences/editor-models",
                        data={"autocomplete_model": "local/small", "main_model": "local/large"},
                        follow_redirects=False)
    assert saved.status_code == 303 and "saved=" in saved.headers["location"]
    assert editor.view(db, _me(db)) == {
        "autocomplete": {"model": "local/small", "source": "person", "allowed": None},
        "main": {"model": "local/large"}}
    page = client.get("/settings?tab=models").text
    assert '<option value="local/small" selected' in page
    # Empty: back to the instance's, and no choice of a main model.
    client.post("/api/preferences/editor-models",
                data={"autocomplete_model": "", "main_model": ""})
    db.expire_all()
    assert editor.view(db, _me(db)) == EMPTY


def test_the_settings_page_offers_only_allowed_models_and_refuses_the_rest(
    client, db, registered
):
    rows = _models(db, "small", "large")
    rows["small"].autocomplete = True
    db.commit()
    page = client.get("/settings?tab=models").text
    autocomplete = page.split('name="autocomplete_model"', 1)[1].split("</select>", 1)[0]
    assert "local/small" in autocomplete and "local/large" not in autocomplete
    assert "An administrator chose which models" in page
    refused = client.post("/api/preferences/editor-models",
                          data={"autocomplete_model": "local/large"}, follow_redirects=False)
    assert "error=" in refused.headers["location"]
    assert editor.view(db, _me(db))["autocomplete"]["model"] is None


def test_two_connections_with_one_model_id_are_told_apart(client, db, registered):
    _models(db, "qwen")
    other = Connection(name="Cloud", base_url="http://cloud.test/v1",
                       api_key_encrypted=encrypt(""))
    db.add(other)
    db.commit()
    db.add(Model(connection_id=other.id, model_id="qwen"))
    db.commit()
    choices = editor.choices(db, _me(db))
    assert dict(choices["main"]) == {"local/qwen": "qwen (local)", "cloud/qwen": "qwen (Cloud)"}


# --- The schema ---------------------------------------------------------------------------
def test_the_mark_arrives_unset_on_an_old_database(db, registered):
    """`models.autocomplete` added to a table that has rows: every one False,
    which is "nothing marked" -- every model allowed, as before."""
    _models(db, "small", "large")
    db.close()
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE models DROP COLUMN autocomplete"))
    assert any("autocomplete" in step for step in sync_schema(engine))
    with engine.connect() as connection:
        values = connection.execute(text("SELECT autocomplete FROM models")).scalars().all()
    assert len(values) == 2 and all(value in (0, False) for value in values)
