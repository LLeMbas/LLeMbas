"""A device's directories for the apps, the model last used in one, and the
two types of schedule (harness spec 3.9.0, `acp.md`).

The device is a `Link` answered here, as in tests/test_schedule_device.py;
the pure rule (`directories.start`) is held to the spec's own cases in
tests/test_harness_spec.py (`last_model`, kind `client`).
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from lembas.db.models import KIND_AGENT, Chat, Message, Schedule
from lembas.security import api_tokens
from lembas.services import device_link, directories, settings_store
from lembas.services import generation as generation_service
from lembas.services import schedules as schedules_service
from lembas.services.schedule import device
from tests import test_schedule_runs as base
from tests.test_interactive_only import PLAN_URL
from tests.test_interactive_only import _connection as _typed_connection
from tests.test_schedule_device import _agents_on, _link, _on_device, _token
from tests.test_schedule_runs import T0, _owner, _runs, _sweep

script = base.script
three = base.three

FOLDER = "/home/dev/waybread"


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    device.TURNS.clear()
    yield
    device_link.reset()
    device.TURNS.clear()


def _remembering(db, token, models: dict[str, str], *, capability: bool = True, **kwargs):
    """A linked device that remembers `models` (path -> ref) and lists two
    folders under `/home/dev`."""
    capabilities = ["scheduled", "turns", *(["directory_model"] if capability else [])]

    def answer(method, params):
        if method != "_lembas/directories":
            return {}
        path = (params or {}).get("path") or ""

        def entry(where: str, accepted: bool = True) -> dict:
            out = {"path": where, "name": where.rsplit("/", 1)[-1], "accepted": accepted}
            if capability and accepted and where in models:
                out["model"] = models[where]
            if not accepted:
                out["reason"] = "Not trusted on this device."
            return out

        if not path:
            return {"path": "", "places": [entry("/home/dev"), entry(FOLDER)]}
        here = {**entry(path), "parent": path.rsplit("/", 1)[0] if path != "/home/dev" else ""}
        if (params or {}).get("list") is False and capability:
            return here
        return {**here, "entries": [entry(f"{path}/docs"), entry(f"{path}/vendor", False)]}

    return _link(db, token, capabilities=tuple(capabilities), answer=answer, **kwargs)


def _app(db) -> dict[str, str]:
    _, raw = api_tokens.create(db, _owner(db), name="App", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


# --- The listing, for the page and the apps ---------------------------------------------------
def test_the_apps_listing_says_where_a_session_may_open_and_the_model(client, db, three):
    _agents_on(db)
    token, _ = _token(db)
    _, asked = _remembering(db, token, {FOLDER: "hosted/hosted", f"{FOLDER}/docs": "gone/gone"})
    auth = _app(db)

    places = client.get(f"/api/v2/devices/{token.id}/directories", headers=auth).json()
    assert (places["path"], places["code"], places["roots"]) == ("", "", ["/home/dev"])
    assert [e["path"] for e in places["entries"]] == ["/home/dev", FOLDER]
    hosted = {"id": "hosted", "ref": "hosted/hosted", "name": "Hosted"}
    assert places["entries"][1]["model"] == hosted
    assert places["remembers"] is True

    here = client.get(
        f"/api/v2/devices/{token.id}/directories", params={"path": FOLDER}, headers=auth
    ).json()
    assert (here["path"], here["name"], here["parent"]) == (FOLDER, "waybread", "/home/dev")
    assert here["accepted"] is True and here["model"] == hosted
    docs, vendor = here["entries"]
    # A model the person no longer has is not offered as a start.
    assert docs["model"] is None and docs["accepted"] is True
    assert (vendor["accepted"], vendor["reason"]) == (False, "Not trusted on this device.")
    assert set(here) == {"device", "path", "name", "parent", "roots", "accepted", "reason",
                         "model", "entries", "remembers", "code", "error"}
    # The listing asks for the listing: `list` is for the one-directory ask.
    assert asked[-1] == ("_lembas/directories", {"path": FOLDER})


def test_no_listing_is_an_answer_with_a_code(client, db, three):
    _agents_on(db)
    token, _ = _token(db)
    auth = _app(db)
    url = f"/api/v2/devices/{token.id}/directories"

    away = client.get(url, headers=auth)
    assert away.status_code == 200
    assert (away.json()["code"], away.json()["entries"]) == ("device_not_linked", [])
    assert "not linked" in away.json()["error"]

    def refuses(method, params):
        raise device_link.DeviceError("Method not found: _lembas/directories")

    _link(db, token, answer=refuses)
    old = client.get(url, headers=auth).json()
    assert old["code"] == "cli_too_old" and "too old" in old["error"]
    assert old["remembers"] is False

    def slow(method, params):
        raise TimeoutError

    _link(db, token, answer=slow)
    assert client.get(url, headers=auth).json()["code"] == "no_answer"

    def says(method, params):
        raise device_link.DeviceError("That path is outside this device's roots.")

    _link(db, token, answer=says)
    refused = client.get(url, params={"path": "/etc"}, headers=auth).json()
    assert (refused["code"], refused["error"]) == (
        "refused", "That path is outside this device's roots."
    )


def test_the_listing_is_behind_the_pages_door(client, db, three):
    token, _ = _token(db)
    _remembering(db, token, {})
    auth = _app(db)
    url = f"/api/v2/devices/{token.id}/directories"
    # Agent chats are off for the instance: no device is anybody's to browse.
    assert client.get(url, headers=auth).status_code == 404
    _agents_on(db)
    assert client.get(url, headers=auth).status_code == 200
    assert client.get("/api/v2/devices/nope/directories", headers=auth).status_code == 404
    assert client.get(url).status_code == 401
    assert client.get("/api/devices/nope/browse").status_code == 404


def test_the_pages_dialog_says_the_model_too(client, db, three):
    _agents_on(db)
    token, _ = _token(db)
    _remembering(db, token, {FOLDER: "hosted/hosted", f"{FOLDER}/docs": "local/local-big"})
    page = client.get(f"/api/devices/{token.id}/browse", params={"path": FOLDER}).text
    assert 'data-dir-model="hosted"' in page and "Last used here: Hosted." in page
    assert "data-entry-model>Big<" in page
    plain = client.get(f"/api/devices/{token.id}/browse", params={"path": "/home/dev"}).text
    assert "data-dir-model" not in plain
    one = client.get(f"/api/devices/{token.id}/directory-model", params={"path": FOLDER})
    assert one.json() == {"model": {"id": "hosted", "ref": "hosted/hosted", "name": "Hosted"}}
    none = client.get(f"/api/devices/{token.id}/directory-model", params={"path": "/home/dev"})
    assert none.json() == {"model": None}


# --- One directory's model: cheap, short, and never an error -----------------------------------
@pytest.mark.anyio
async def test_the_one_directory_ask_lists_nothing_and_survives_everything(db, three):
    token, _ = _token(db)
    assert await directories.remembered(token.id, FOLDER) == "", "away: not asked"

    _, asked = _remembering(db, token, {FOLDER: "hosted/hosted"})
    assert await directories.remembered(token.id, FOLDER) == "hosted/hosted"
    assert asked == [("_lembas/directories", {"path": FOLDER, "list": False})]
    assert await directories.remembered(token.id, "/home/dev") == ""

    # An older device is not asked at all: it would list the whole folder.
    _, asked = _remembering(db, token, {FOLDER: "hosted/hosted"}, capability=False)
    assert await directories.remembered(token.id, FOLDER) == "" and asked == []

    async def never(method, params=None, *, timeout=60):
        await asyncio.sleep(timeout + 5)

    link, _ = _remembering(db, token, {})
    link.request = never
    started = asyncio.get_running_loop().time()

    async def bounded(method, params=None, *, timeout=60):
        raise TimeoutError

    link.request = bounded
    assert await directories.remembered(token.id, FOLDER, timeout=0.05) == ""
    assert asyncio.get_running_loop().time() - started < 2

    def breaks(method, params):
        raise device_link.DeviceError("no")

    _link(db, token, capabilities=("directory_model",), answer=breaks)
    assert await directories.remembered(token.id, FOLDER) == ""


# --- A new agent chat ---------------------------------------------------------------------------
@pytest.fixture
def started(db, three, monkeypatch):
    """A device that remembers a model for FOLDER; the reply itself is not run."""
    _agents_on(db)
    settings_store.update(db, {"default_model": "local-big"})
    token, _ = _token(db)
    _, asked = _remembering(db, token, {FOLDER: "hosted/hosted"})
    monkeypatch.setattr(generation_service, "ensure", lambda *a, **k: None)
    return token, asked


def _newest(db) -> Chat:
    db.expire_all()
    return db.scalars(select(Chat).order_by(Chat.created_at.desc())).first()


def test_the_page_starts_an_agent_chat_on_the_directorys_model(client, db, started):
    token, _ = started
    form = {"content": "Run the tests.", "kind": "agent", "model_id": "local-big",
            "ssh_profile_id": f"device:{token.id}", "project_dir": FOLDER}
    assert client.post("/api/chats/start", data=form).status_code == 204
    chat = _newest(db)
    assert (chat.kind, chat.model_id, chat.project_dir) == (KIND_AGENT, "hosted", FOLDER)
    # Kept nowhere but as the chat's own model.
    assert db.scalars(select(Message).where(Message.chat_id == chat.id)).first() is not None

    # The person chose one on this screen: theirs, whatever the directory says.
    client.post("/api/chats/start", data={**form, "model_chosen": "1"})
    assert _newest(db).model_id == "local-big"
    # Another folder, where nothing is remembered: as before.
    client.post("/api/chats/start", data={**form, "project_dir": "/home/dev"})
    assert _newest(db).model_id == "local-big"
    # An ordinary chat never asks a device.
    client.post("/api/chats/start", data={"content": "Hello.", "model_id": "local-big"})
    assert _newest(db).model_id == "local-big"


def test_the_composer_marks_a_chosen_model_and_carries_the_default(client, db, started):
    page = client.get("/chat?kind=agent").text
    assert 'name="model_chosen"' not in page
    assert 'data-start-model data-default-model="local-big"' in page
    chosen = client.get("/chat?kind=agent&model=hosted").text
    assert 'name="model_chosen" value="1"' in chosen


def test_the_app_starts_the_same_chat_and_reads_the_same_start(client, db, started):
    token, asked = started
    auth = _app(db)
    body = {"text": "Run the tests.", "kind": "agent", "device": token.id, "directory": FOLDER}
    made = client.post("/api/v2/chats", json=body, headers=auth)
    assert made.status_code == 201, made.text
    assert made.json()["chat"]["model"] == "hosted"
    named = client.post("/api/v2/chats", json={**body, "model": "local-small"}, headers=auth)
    assert named.json()["chat"]["model"] == "local-small"
    elsewhere = client.post(
        "/api/v2/chats", json={**body, "directory": "/home/dev"}, headers=auth
    )
    assert elsewhere.json()["chat"]["model"] == "local-big"

    # What the draft shows before anything is sent: the same answer.
    query = {"kind": "agent", "device": token.id, "directory": FOLDER}
    draft = client.get("/api/v2/models", params=query, headers=auth).json()
    assert draft["start"] == {"model": "hosted", "ref": "hosted/hosted", "from": "directory"}
    other = client.get(
        "/api/v2/models", params={**query, "directory": "/home/dev"}, headers=auth
    ).json()
    assert other["start"] == {"model": "local-big", "ref": "local/local-big",
                              "from": "default"}
    # No directory named: nothing to say, and the device is not asked.
    before = len(asked)
    bare = client.get(
        "/api/v2/models", params={"kind": "agent", "device": token.id}, headers=auth
    ).json()
    assert "start" not in bare and len(asked) == before


def test_a_device_that_is_away_or_older_starts_as_before(client, db, three, monkeypatch):
    _agents_on(db)
    settings_store.update(db, {"default_model": "local-big"})
    monkeypatch.setattr(generation_service, "ensure", lambda *a, **k: None)
    token, _ = _token(db)
    _, asked = _remembering(db, token, {FOLDER: "hosted/hosted"}, capability=False)
    auth = _app(db)
    body = {"text": "Run the tests.", "kind": "agent", "device": token.id, "directory": FOLDER}
    made = client.post("/api/v2/chats", json=body, headers=auth)
    assert made.json()["chat"]["model"] == "local-big" and asked == []


# --- An Agentic schedule's run ----------------------------------------------------------------
@pytest.mark.anyio
async def test_a_run_follows_the_directorys_model_and_says_where_it_ran(db, three, script):
    _agents_on(db)
    settings_store.update(db, {"default_model": "local-big"})
    token, _ = _token(db)
    _, asked = _remembering(db, token, {FOLDER: "hosted/hosted"})
    schedule = _on_device(db, token)
    assert schedule.model_id == ""

    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "ok", run.error
    assert (run.model_id, run.model_reason) == ("hosted", "directory")
    assert db.get(Chat, run.chat_id).model_id == "hosted"
    # Asked as the run started, the cheap way.
    assert ("_lembas/directories", {"path": FOLDER, "list": False}) in asked
    from lembas.services.schedule import runs

    said = runs.run_dict(run)
    assert said["ran_on"] == {"device": token.id, "device_name": "device-1", "cwd": FOLDER,
                              "mode": "edit"}
    assert said["model"]["reason"] == "directory"


@pytest.mark.anyio
async def test_a_named_model_is_the_runs_and_nothing_remembered_is_the_default(
    db, three, script
):
    _agents_on(db)
    settings_store.update(db, {"default_model": "local-big"})
    token, _ = _token(db)
    _, asked = _remembering(db, token, {FOLDER: "hosted/hosted"})
    named = _on_device(db, token)
    named.model_id = "local-small"
    db.commit()
    await _sweep(script, T0)
    (run,) = _runs(db, named)
    assert (run.model_id, run.model_reason) == ("local-small", "pinned")
    assert asked == [], "a schedule that names a model does not ask the directory"

    schedules_service.delete(db, named)
    elsewhere = schedules_service.create(
        db, owner=_owner(db), title="Elsewhere", instruction="Look.",
        rule={"start": T0.isoformat(), "every": {"hours": 1}},
        settings={"runs_on": {"device": token.id, "cwd": "/home/dev"}},
    )
    elsewhere.next_fire_at = T0
    db.commit()
    await _sweep(script, T0)
    (run,) = _runs(db, elsewhere)
    assert (run.model_id, run.model_reason) == ("local-big", "default")


@pytest.mark.anyio
async def test_a_run_never_takes_a_directorys_interactive_only_model(db, three, script):
    """A person may well chat in that folder on a plan; a schedule may not."""
    _agents_on(db)
    settings_store.update(db, {"default_model": "local-big"})
    plan = _typed_connection(db, "Plan", "alibaba_plan", PLAN_URL, "sk-sp-x")
    base._model(db, plan, "mallorn-large", 5)
    token, _ = _token(db)
    _remembering(db, token, {FOLDER: "plan/mallorn-large"})
    schedule = _on_device(db, token)
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "ok", run.error
    assert (run.model_id, run.model_reason) == ("local-big", "default")

    # And a default that is itself on a plan fails the run, saying why.
    settings_store.update(db, {"default_model": "mallorn-large"})
    schedule.next_fire_at = T0
    db.commit()
    await _sweep(script, T0)
    last = _runs(db, schedule)[-1]
    assert last.status == "failed" and "interactive use only" in last.error


@pytest.mark.anyio
async def test_a_continued_conversation_keeps_its_sessions_model(db, three, script):
    _agents_on(db)
    settings_store.update(db, {"default_model": "local-big"})
    token, _ = _token(db)
    remembered = {FOLDER: "hosted/hosted"}
    _remembering(db, token, remembered)
    schedule = _on_device(db, token)
    schedule.mode = "continue"
    db.commit()
    await _sweep(script, T0)
    first = _runs(db, schedule)[-1]
    assert first.model_id == "hosted"

    # The person now works there on another model: the conversation the
    # schedule continues is not moved by it.
    remembered[FOLDER] = "local/local-small"
    from datetime import timedelta

    await _sweep(script, T0 + timedelta(hours=1))
    second = _runs(db, schedule)[-1]
    assert second.id != first.id and second.status == "ok", second.error
    assert second.chat_id == first.chat_id
    assert db.get(Chat, second.chat_id).model_id == "hosted"
    assert (second.model_id, second.model_reason) == ("hosted", "session")


# --- The two types, in the form -----------------------------------------------------------------
def _form(**fields) -> dict:
    return {"title": "Nightly tests", "instruction": "Run the tests.", "target": "chat",
            "repeat": "calendar", "weekdays": "0", "times": "02:00", "count": "0", **fields}


def test_the_form_asks_the_type_first_and_reads_it(client, db, three):
    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    page = client.get("/scheduled/new").text
    assert 'name="type" value="chat" checked' in page
    # No computer accepts scheduled work: the type is there, and not offered.
    assert 'data-testid="agentic-unavailable"' in page
    agentic = page[page.index('value="agentic"'):][:200]
    assert "disabled" in agentic

    _agents_on(db)
    token, _ = _token(db)
    _remembering(db, token, {})
    page = client.get("/scheduled/new").text
    assert 'data-testid="agentic-unavailable"' not in page
    assert "s usual model</option>" in page and 'name="device_model_id"' in page
    assert "data-dir-browse" in page and "data-dir-device" in page

    # Agentic: its own model field is the one that is read.
    made = client.post("/api/schedules", follow_redirects=False, data=_form(
        type="agentic", device=token.id, device_dir=FOLDER, device_mode="edit",
        model_id="local-big", device_model_id="",
    ))
    assert made.status_code == 303, made.text
    schedule = db.scalars(select(Schedule)).one()
    assert (schedule.device_token_id, schedule.device_dir, schedule.model_id) == (
        token.id, FOLDER, ""
    )
    shown = client.get(f"/scheduled/{schedule.id}").text
    assert 'data-testid="schedule-type">Agentic<' in shown
    assert "s usual model" in shown and "device-1" in shown
    edit = client.get(f"/scheduled/{schedule.id}/edit").text
    assert 'name="type" value="agentic" checked' in edit

    # Chat: the computer's fields are still in the form and are not read.
    changed = client.post(f"/api/schedules/{schedule.id}", follow_redirects=False, data=_form(
        type="chat", device=token.id, device_dir=FOLDER, model_id="hosted",
        device_model_id="local-big", watch_command="git rev-parse HEAD",
    ))
    assert changed.status_code == 303, changed.text
    db.expire_all()
    schedule = db.get(Schedule, schedule.id)
    assert (schedule.device_token_id, schedule.model_id, schedule.watch_kind) == (
        None, "hosted", ""
    )
    assert 'data-testid="schedule-type">Chat<' in client.get(f"/scheduled/{schedule.id}").text


def test_agentic_without_a_computer_is_refused_not_made_a_chat_schedule(client, db, three):
    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    refused = client.post("/api/schedules", follow_redirects=False,
                          data=_form(type="agentic", device="", device_dir=FOLDER))
    # Back to the form with the reason, as every refusal of this form is.
    assert refused.headers["location"].startswith("/scheduled/new?error=")
    assert "which%20computer" in refused.headers["location"]
    assert db.scalars(select(Schedule)).first() is None


def test_the_task_page_says_where_and_on_what_each_run_ran(client, db, three):
    from lembas.db.models import ScheduleRun

    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    schedule = base._make(db, model_id="hosted")
    db.add_all([
        ScheduleRun(schedule_id=schedule.id, user_id=schedule.user_id, status="ok", due_at=T0,
                    model_id="hosted", model_label="Hosted", model_reason="directory",
                    ran_on_json={"device": "t1", "device_name": "workstation",
                                 "cwd": "/home/user/project", "mode": "edit"}),
        ScheduleRun(schedule_id=schedule.id, user_id=schedule.user_id, status="ok", due_at=T0,
                    model_id="hosted", model_label="Hosted", model_reason="pinned"),
    ])
    db.commit()
    shown = client.get(f"/scheduled/{schedule.id}").text
    assert "Agentic · workstation · <code>/home/user/project</code>" in shown
    assert "the folder&#39;s usual model" in shown
    assert shown.count("· Chat") == 1


# --- After the review of 2.0.0-beta.13..HEAD ---------------------------------------------------
@pytest.mark.anyio
async def test_one_timeout_covers_the_sending_too(db, three):
    """A socket that has stopped taking bytes must not hold a chat's first
    message past the two seconds it was given."""
    token, _ = _token(db)

    async def stalled(_text):
        await asyncio.sleep(30)

    async def nothing(*_a, **_k):
        return None

    link = device_link.Link(token_id=token.id, user_id=token.user_id, name=token.name,
                            send_text=stalled, close=nothing, hello={})
    link.initialized = {"_meta": {"lembas": {"capabilities": ["directory_model"]}}}
    device_link.LINKS[token.id] = link
    started = asyncio.get_running_loop().time()
    assert await directories.remembered(token.id, FOLDER, timeout=0.1) == ""
    assert asyncio.get_running_loop().time() - started < 2
    with pytest.raises(directories.NoAnswer):
        await directories.asked(token.id, FOLDER, timeout=0.1)
    assert link._waiting == {}


@pytest.mark.anyio
async def test_a_device_that_does_not_answer_is_waited_for_not_answered_for(db, three, script):
    """"Nothing is remembered there" is an answer; silence is not. A run does
    not fall to the default -- which may be a model it cannot use, failing it
    for good -- because the device was slow."""
    _agents_on(db)
    settings_store.update(db, {"default_model": "local-big"})
    token, _ = _token(db)
    state = {"silent": True}

    def answer(method, params):
        if state["silent"]:
            raise TimeoutError
        return {"path": FOLDER, "name": "waybread", "accepted": True, "parent": "/home/dev",
                "model": "hosted/hosted"}

    _link(db, token, capabilities=("scheduled", "turns", "directory_model"), answer=answer)
    schedule = _on_device(db, token)
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert (run.status, run.waiting_for) == ("waiting", "device")
    assert script.turns == [] and not run.model_id

    state["silent"] = False
    from datetime import timedelta

    await _sweep(script, T0 + timedelta(minutes=3))
    (run,) = _runs(db, schedule)
    assert run.status == "ok", run.error
    assert (run.model_id, run.model_reason) == ("hosted", "directory")


@pytest.mark.anyio
async def test_a_named_model_runs_on_the_schedules_own_connection_and_keeps_its_effort(
    db, three, script
):
    _agents_on(db)
    token, _ = _token(db)
    _remembering(db, token, {})
    # The same id on two connections; the schedule names the second.
    second = base._connection(db, "elsewhere")
    twin = base._model(db, second, "hosted", 9, display_name="Hosted elsewhere")
    schedule = _on_device(db, token)
    schedule.model_id, schedule.connection_id, schedule.effort = "hosted", second.id, "max"
    db.commit()
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "ok", run.error
    chat = db.get(Chat, run.chat_id)
    assert (chat.model_id, chat.connection_id, run.connection_id) == ("hosted", second.id,
                                                                    twin.connection_id)
    # The effort is the device's to judge: not dropped for being a level
    # this server does not know of the model.
    assert chat.params_json.get("reasoning_effort") == "max"


def test_twins_are_told_apart_from_the_folder_to_the_chat(client, db, started):
    """Two connections serving one model id: the device remembers
    `provider/model`, and the chat starts on that connection."""
    token, _ = started
    second = base._connection(db, "elsewhere")
    twin = base._model(db, second, "hosted", 9, display_name="Hosted")
    link = device_link.link_for(token.id)
    inner = link.request

    async def request(method, params=None, *, timeout=60):
        got = await inner(method, params, timeout=timeout)
        if isinstance(got, dict) and got.get("model"):
            got = {**got, "model": "elsewhere/hosted"}
        return got

    link.request = request
    auth = _app(db)
    query = {"kind": "agent", "device": token.id, "directory": FOLDER}
    draft = client.get("/api/v2/models", params=query, headers=auth).json()
    assert draft["start"] == {"model": "hosted", "ref": "elsewhere/hosted", "from": "directory"}
    refs = [(m["ref"], m["connection"]) for m in draft["models"] if m["id"] == "hosted"]
    assert refs == [("hosted/hosted", "hosted"), ("elsewhere/hosted", "elsewhere")]

    body = {"text": "Run the tests.", "kind": "agent", "device": token.id, "directory": FOLDER}
    client.post("/api/v2/chats", json=body, headers=auth)
    assert _newest(db).connection_id == twin.connection_id
    # A ref sent as `model` names the row; a bare id is the first, as before.
    client.post("/api/v2/chats", json={**body, "model": "hosted/hosted"}, headers=auth)
    assert _newest(db).connection_id != twin.connection_id
    # The page: the form's own start, and a picker that says which is which.
    form = {"content": "Run the tests.", "kind": "agent", "model_id": "local-big",
            "ssh_profile_id": f"device:{token.id}", "project_dir": FOLDER}
    client.post("/api/chats/start", data=form)
    assert _newest(db).connection_id == twin.connection_id
    page = client.get("/chat?kind=agent").text
    assert 'data-picker-value="elsewhere/hosted"' in page
    assert 'data-picker-value="hosted/hosted"' in page
    assert "Hosted · elsewhere" in page and 'data-picker-value="local-big"' in page
    chosen = client.get("/chat?kind=agent&model=elsewhere/hosted").text
    assert 'name="model_id" value="elsewhere/hosted"' in chosen
    one = client.get(f"/api/devices/{token.id}/directory-model", params={"path": FOLDER})
    assert one.json()["model"]["ref"] == "elsewhere/hosted"


def test_a_listing_is_cut_to_size(client, db, three):
    _agents_on(db)
    token, _ = _token(db)

    def answer(method, params):
        return {"path": "/home/dev", "name": "dev", "accepted": True, "parent": "",
                "entries": [{"path": f"/home/dev/{n}", "name": "x" * 5000, "accepted": False,
                             "reason": "r" * 5000} for n in range(2000)]}

    _link(db, token, answer=answer)
    got = client.get(f"/api/v2/devices/{token.id}/directories", params={"path": "/home/dev"},
                     headers=_app(db)).json()
    assert len(got["entries"]) == directories.MAX_ENTRIES
    assert len(got["entries"][0]["name"]) == directories.MAX_NAME
    assert len(got["entries"][0]["reason"]) == directories.MAX_REASON


@pytest.mark.anyio
async def test_a_runs_failure_carries_the_devices_reason(db, three, script):
    """`acp.md` *When the device is away, or refuses*: a scheduled prompt for
    a session open in a terminal is refused with `data.reason: "terminal"`."""
    from lembas.services import harness_spec
    from lembas.services.schedule import runs

    _agents_on(db)
    token, _ = _token(db)
    _remembering(db, token, {})
    schedule = _on_device(db, token)
    sentence = harness_spec.loop()["scheduled"]["texts"]["terminal"]
    script.replies.append({"content": "", "error": f"device-1 refused: {sentence}"})
    script.device_reason = "terminal"
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "failed" and sentence in run.error
    assert runs.run_dict(run)["error"] == {
        "class": "device", "text": f"device-1 refused: {sentence}", "reason": "terminal"
    }
    # A link's error keeps what the device said it was.
    link = device_link.link_for(token.id)
    future = asyncio.get_running_loop().create_future()
    link._waiting[7] = future
    await link.receive('{"jsonrpc":"2.0","id":7,"error":{"code":-32602,"message":"no",'
                       '"data":{"reason":"terminal"}}}')
    assert future.exception().reason == "terminal"
