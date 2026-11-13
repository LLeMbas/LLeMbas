"""`/api/v2/schedules`: scheduled work for the apps, through the same core as
the pages -- and the person's event stream."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from lembas.db.models import Connection, Model, Schedule, ScheduleRun, User
from lembas.security import api_tokens
from lembas.services import settings_store
from lembas.services.crypto import encrypt
from tests.conftest import join


@pytest.fixture(autouse=True)
def _fresh_rates():
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()
    yield


def _bearer(db, email="frodo@shire.test") -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == email))
    _, raw = api_tokens.create(db, user, name="LLeMbas for Android", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def _models(db) -> None:
    connection = Connection(
        name="hosted", base_url="http://upstream.test/v1", api_key_encrypted=encrypt("k")
    )
    db.add(connection)
    db.commit()
    db.add_all(
        [
            Model(connection_id=connection.id, model_id="qwen", display_name="Qwen",
                  position=0, reasoning_efforts=["low", "high"],
                  capabilities_json={"tools": True}),
            Model(connection_id=connection.id, model_id="gpt-oss", display_name="GPT-OSS",
                  position=1, parallel=1, capabilities_json={"tools": True}),
        ]
    )
    db.commit()
    settings_store.update(db, {"models": ["gpt-oss", "qwen"]}, key=settings_store.SCHEDULES)


BODY = {
    "title": "Morning news",
    "instruction": "Summarise the news.",
    "rule": {"at": {"nth": [{"n": 2, "weekday": 0}], "times": ["07:30"]}},
    "model": "qwen",
    "notify": "always",
    "mode": "continue",
    "effort": "high",
    "tools": ["search", "fetch"],
}


def test_create_list_get_update_pause_resume_delete(client, db, registered):
    _models(db)
    auth = _bearer(db)
    made = client.post("/api/v2/schedules", json=BODY, headers=auth)
    assert made.status_code == 201, made.text
    schedule = made.json()["schedule"]
    assert schedule["description"] == "On the second Monday of each month at 07:30"
    assert len(schedule["next_runs"]) == 5
    assert schedule["next_run"] == schedule["next_runs"][0]
    assert (schedule["notify"], schedule["mode"], schedule["model"]) == (
        "always", "continue", "qwen"
    )
    assert schedule["effort"] == "high"
    assert schedule["tools"] == ["search", "fetch"]
    assert schedule["last_run"] is None and schedule["enabled"] is True

    listed = client.get("/api/v2/schedules", headers=auth).json()["schedules"]
    assert [s["id"] for s in listed] == [schedule["id"]]
    one = client.get(f"/api/v2/schedules/{schedule['id']}", headers=auth).json()["schedule"]
    assert one["id"] == schedule["id"]

    changed = client.patch(
        f"/api/v2/schedules/{schedule['id']}",
        # `matters` is what `when` was called before spec 3.8.0: still read.
        json={"notify": "matters", "notify_when": " the  headlines changed. ",
              "title": "News", "rule": {"at": {"times": ["08:00"]}}},
        headers=auth,
    ).json()["schedule"]
    assert (changed["notify"], changed["title"], changed["description"]) == (
        "when", "News", "Every day at 08:00"
    )
    assert changed["notify_when"] == "the headlines changed."
    assert changed["notify_default"] == "something needs my attention"
    assert changed["save_file"] is False

    paused = client.post(f"/api/v2/schedules/{schedule['id']}/pause", headers=auth).json()
    assert paused["schedule"]["enabled"] is False
    assert paused["schedule"]["next_runs"] == []
    resumed = client.post(f"/api/v2/schedules/{schedule['id']}/resume", headers=auth).json()
    assert resumed["schedule"]["enabled"] is True

    gone = client.delete(f"/api/v2/schedules/{schedule['id']}", headers=auth)
    assert gone.status_code == 204
    assert client.get("/api/v2/schedules", headers=auth).json()["schedules"] == []


def test_a_rule_that_cannot_run_is_refused_in_words(client, db, registered):
    auth = _bearer(db)
    refused = client.post(
        "/api/v2/schedules", json={**BODY, "rule": {"cron": "0 9 * * *"}, "model": ""},
        headers=auth,
    )
    assert refused.status_code == 400
    assert "time anything could run at" in refused.json()["detail"]


def test_a_model_nobody_may_use_is_refused(client, db, registered):
    _models(db)
    refused = client.post(
        "/api/v2/schedules", json={**BODY, "model": "nope"}, headers=_bearer(db)
    )
    assert refused.status_code == 400


def test_preview_cleans_and_reads_back(client, db, registered):
    answer = client.post(
        "/api/v2/schedules/preview",
        json={"rule": {"at": {"nth": [{"n": -1}], "times": ["9:00"]}}},
        headers=_bearer(db),
    ).json()
    assert answer["rule"] == {"at": {"nth": [{"n": -1}], "times": ["09:00"]}}
    assert answer["description"] == "On the last day of each month at 09:00"
    assert len(answer["next_runs"]) == 5
    nothing = client.post(
        "/api/v2/schedules/preview", json={"rule": "every monday"}, headers=_bearer(db)
    ).json()
    assert nothing["rule"] == {} and nothing["next_runs"] == [] and nothing["description"] is None


def test_compile_without_a_model_says_so(client, db, registered):
    answer = client.post(
        "/api/v2/schedules/compile", json={"text": "Every Monday at 9, check the build"},
        headers=_bearer(db),
    ).json()
    assert answer["ok"] is False
    assert answer["reason"]
    assert answer["instruction"] == "Every Monday at 9, check the build"
    assert answer["next_runs"] == []


def test_runs_paged_and_one_run_with_its_text(client, db, registered):
    _models(db)
    auth = _bearer(db)
    made = client.post("/api/v2/schedules", json=BODY, headers=auth).json()["schedule"]
    for hour in range(3):
        db.add(ScheduleRun(
            schedule_id=made["id"], user_id=db.scalar(select(User.id)), status="ok",
            due_at=datetime(2026, 3, 2, hour, tzinfo=UTC), preview=f"run {hour}",
            created_at=datetime(2026, 3, 2, hour, tzinfo=UTC),
        ))
    db.commit()
    page = client.get(f"/api/v2/schedules/{made['id']}/runs?limit=2", headers=auth).json()
    assert [r["preview"] for r in page["runs"]] == ["run 2", "run 1"] and page["more"]
    rest = client.get(
        f"/api/v2/schedules/{made['id']}/runs?limit=2&before={page['runs'][-1]['id']}",
        headers=auth,
    ).json()
    assert [r["preview"] for r in rest["runs"]] == ["run 0"] and not rest["more"]
    one = client.get(
        f"/api/v2/schedules/{made['id']}/runs/{page['runs'][0]['id']}", headers=auth
    ).json()
    assert one["run"]["status"] == "ok" and one["text"] is None
    listed = client.get("/api/v2/schedules", headers=auth).json()["schedules"][0]
    assert listed["last_run"]["preview"] == "run 2"


def test_run_now_answers_with_the_run_at_once(client, db, registered):
    _models(db)
    auth = _bearer(db)
    made = client.post("/api/v2/schedules", json=BODY, headers=auth).json()["schedule"]
    started = client.post(f"/api/v2/schedules/{made['id']}/run", headers=auth)
    assert started.status_code == 202
    run = started.json()["run"]
    assert run["timing"] == "manual"
    assert run["schedule_id"] == made["id"]
    db.expire_all()
    # Not counted against the rule.
    assert db.get(Schedule, made["id"]).fired_count == 0


def test_scheduling_models_in_order_with_free_and_the_persons_choice(client, db, registered):
    _models(db)
    auth = _bearer(db)
    listed = client.get("/api/v2/schedules/models", headers=auth).json()
    assert [m["id"] for m in listed["models"]] == ["gpt-oss", "qwen"]
    assert all(m["free"] and m["chosen"] for m in listed["models"])
    assert listed["instance"] == ["gpt-oss", "qwen"] and listed["mine"] == []
    chosen = client.put(
        "/api/v2/schedules/models", json={"models": ["qwen", "made-up"]}, headers=auth
    ).json()
    assert chosen["mine"] == ["qwen"]
    assert [m["chosen"] for m in chosen["models"]] == [False, True]


def test_someone_else_s_schedule_is_not_there(client, db, registered):
    _models(db)
    settings_store.update(
        db, {"default_permissions": {"schedule.use": True, "api.use": True}}
    )
    made = client.post("/api/v2/schedules", json=BODY, headers=_bearer(db)).json()["schedule"]
    join(client, {"email": "sam@shire.test", "password": "potatoes-boil-mash"})
    other = _bearer(db, "sam@shire.test")
    assert client.get(f"/api/v2/schedules/{made['id']}", headers=other).status_code == 404
    assert client.post(f"/api/v2/schedules/{made['id']}/pause", headers=other).status_code == 404
    assert client.get("/api/v2/schedules", headers=other).json()["schedules"] == []


def test_without_the_permission_it_is_refused(client, db, registered):
    join(client, {"email": "sam@shire.test", "password": "potatoes-boil-mash"})
    settings_store.update(
        db, {"default_permissions": {"schedule.use": False, "api.use": True}}
    )
    refused = client.get("/api/v2/schedules", headers=_bearer(db, "sam@shire.test"))
    assert refused.status_code == 403


@pytest.mark.anyio
async def test_the_event_stream_sends_the_list_then_only_changes(client, db, registered,
                                                                  monkeypatch):
    from lembas.api.v2 import schedules as v2

    _models(db)
    auth = _bearer(db)
    client.post("/api/v2/schedules", json=BODY, headers=auth)
    user_id = db.scalar(select(User.id).where(User.email == "frodo@shire.test"))
    slept: list[float] = []

    async def tick(seconds):
        slept.append(seconds)

    monkeypatch.setattr(v2.asyncio, "sleep", tick)
    stream = v2._events(user_id)
    first = await anext(stream)
    assert first.startswith("event: schedules\n")
    data = json.loads(first.split("data: ", 1)[1])
    assert set(data) == {"version", "schedules"}
    assert data["version"] == v2.version_of(data["schedules"])
    assert data["schedules"][0]["title"] == "Morning news"
    # Then the reports: how many are unread, and the newest of them.
    second = await anext(stream)
    assert second.startswith("event: reports\n")
    assert json.loads(second.split("data: ", 1)[1]) == {"unread": 0, "newest": None}
    # Nothing changed: the next thing sent is a keepalive, not either again.
    third = await anext(stream)
    assert third.startswith(":")
    # A report is filed: the count and the report, once.
    from lembas.services import reports as reports_service

    filed = reports_service.file(
        db, owner_id=user_id, report={"title": "Headlines", "body": "Three things."},
        schedule=db.scalars(select(Schedule)).one(), run=None,
    )
    db.commit()
    fourth = await anext(stream)
    assert fourth.startswith("event: reports\n")
    frame = json.loads(fourth.split("data: ", 1)[1])
    assert frame["unread"] == 1 and frame["newest"]["id"] == filed.id
    assert frame["newest"]["title"] == "Headlines" and "body" not in frame["newest"]
    assert (await anext(stream)).startswith(":")
    await stream.aclose()
