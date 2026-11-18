"""Which connection's model a thing is on, when two connections serve one id.

A chat, a message, a schedule and a run each store their connection; `/api/v2`
used to send the bare model id, and an app that looked the id up showed the
first connection that had it. They now carry `model_ref` (`provider/model`)
built from what the row itself stored -- and a schedule takes a ref, so the
second connection's model can be chosen at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Message, Model, Schedule, ScheduleRun, User
from lembas.security import api_tokens
from lembas.services import chat as chat_service
from lembas.services import schedules
from lembas.services.crypto import encrypt
from lembas.services.schedule import runs

RULE = {"at": {"times": ["07:30"]}}


@pytest.fixture(autouse=True)
def _fresh_rates():
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()
    yield


@pytest.fixture
def twins(db, registered):
    """`local` and `cloud`, both serving `shared`; the first found is `local`."""
    made = []
    for position, name in enumerate(("local", "cloud")):
        connection = Connection(
            name=name, base_url=f"http://{name}.test/v1", api_key_encrypted=encrypt("k")
        )
        db.add(connection)
        db.commit()
        db.add(Model(connection_id=connection.id, model_id="shared", position=position,
                     display_name=f"Shared on {name}", capabilities_json={"tools": True}))
        made.append(connection)
    db.commit()
    user = db.scalars(select(User).order_by(User.created_at)).first()
    _, raw = api_tokens.create(db, user, name="LLeMbas Desktop", scopes=["app"])
    return user, made[0], made[1], {"Authorization": f"Bearer {raw}"}


def test_a_chat_on_the_second_connection_names_the_second(client, db, twins):
    user, _local, cloud, headers = twins
    chat = Chat(user_id=user.id, model_id="shared", connection_id=cloud.id, title="There")
    db.add(chat)
    db.flush()
    db.add(Message(chat_id=chat.id, role="user", content="Hello."))
    db.add(Message(chat_id=chat.id, role="assistant", content="Hi.", model_id="shared",
                   connection_id=cloud.id, complete=True))
    db.commit()

    one = client.get(f"/api/v2/chats/{chat.id}", headers=headers)
    assert one.status_code == 200, one.text
    body = one.json()
    assert (body["chat"]["model"], body["chat"]["model_ref"]) == ("shared", "cloud/shared")
    listed = client.get("/api/v2/chats", headers=headers).json()["chats"]
    assert [c["model_ref"] for c in listed if c["id"] == chat.id] == ["cloud/shared"]
    messages = client.get(f"/api/v2/chats/{chat.id}/messages", headers=headers).json()["messages"]
    reply = next(m for m in messages if m["role"] == "assistant")
    assert (reply["model"], reply["model_ref"]) == ("shared", "cloud/shared")
    assert next(m for m in messages if m["role"] == "user")["model_ref"] is None
    # The ref is one `GET /models` lists, so an app finds the row by it.
    refs = {m["ref"] for m in client.get("/api/v2/models", headers=headers).json()["models"]}
    assert {"local/shared", "cloud/shared"} <= refs

    # Changing the model by its ref moves the chat to that connection.
    moved = client.patch(
        f"/api/v2/chats/{chat.id}", headers=headers, json={"model": "local/shared"}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["chat"]["model_ref"] == "local/shared"


def test_a_ref_is_null_when_the_connection_is_unknown_or_gone(db, twins):
    user, _local, cloud, _headers = twins
    chat = Chat(user_id=user.id, model_id="shared", connection_id=None)
    there = Chat(user_id=user.id, model_id="shared", connection_id=cloud.id)
    none = Chat(user_id=user.id, model_id="", connection_id=cloud.id)
    db.add_all([chat, there, none])
    db.flush()
    # A message keeps a plain id: its connection may have been deleted since.
    gone = Message(chat_id=there.id, role="assistant", content="x", model_id="shared",
                   connection_id="0" * 32)
    db.add(gone)
    db.commit()
    # Never guessed from the id: the first connection serving it is not named.
    assert [chat_service.ref_of(c) for c in (chat, gone, there, none)] == [
        None, None, "cloud/shared", None,
    ]
    assert chat_service.ref_of(Chat(model_id="shared", connection_id=cloud.id)) is None


def test_a_schedule_takes_a_ref_and_says_which_connection(client, db, twins):
    user, local, cloud, headers = twins
    body = {"title": "News", "instruction": "Summarise the news.", "rule": RULE,
            "model": "cloud/shared"}
    made = client.post("/api/v2/schedules", json=body, headers=headers)
    assert made.status_code == 201, made.text
    schedule = made.json()["schedule"]
    assert (schedule["model"], schedule["model_ref"]) == ("shared", "cloud/shared")
    row = db.get(Schedule, schedule["id"])
    assert row.connection_id == cloud.id
    # The bare id still works, and is the first that scheduled work may use.
    assert schedules.model_choice(db, user, "shared") == ("shared", local.id)
    assert schedules.model_choice(db, user, "local/shared") == ("shared", local.id)
    with pytest.raises(schedules.ScheduleError):
        schedules.model_choice(db, user, "nowhere/shared")
    plain = client.post("/api/v2/schedules", json={**body, "model": ""}, headers=headers)
    assert plain.json()["schedule"]["model_ref"] is None

    run = ScheduleRun(schedule_id=row.id, user_id=user.id, status="ok", model_id="shared",
                      connection_id=cloud.id, model_label="Shared on cloud")
    db.add(run)
    db.commit()
    assert runs.run_dict(run)["model"]["ref"] == "cloud/shared"


def test_the_schedule_form_offers_each_connections_model_by_its_ref(client, db, twins):
    user, _local, cloud, _headers = twins
    page = client.get("/scheduled/new")
    assert page.status_code == 200
    assert 'value="local/shared"' in page.text and 'value="cloud/shared"' in page.text
    # Two of one id: each is told apart by its connection's name.
    assert "Shared on cloud (cloud)" in page.text
    saved = Schedule(user_id=user.id, title="News", instruction="x", rule_json=RULE,
                     model_id="shared", connection_id=cloud.id)
    db.add(saved)
    db.commit()
    edit = client.get(f"/scheduled/{saved.id}/edit")
    assert edit.status_code == 200
    assert 'value="cloud/shared" selected' in edit.text
    assert 'value="local/shared" selected' not in edit.text
    # Saving the form as it stands keeps the second connection.
    posted = client.post(
        f"/api/schedules/{saved.id}",
        data={"title": "News", "instruction": "x", "model_id": "cloud/shared",
              "rule": __import__("json").dumps(RULE)},
        follow_redirects=False,
    )
    assert posted.status_code in (200, 204, 303), posted.text
    db.expire_all()
    assert db.get(Schedule, saved.id).connection_id == cloud.id
