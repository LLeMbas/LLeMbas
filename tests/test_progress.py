"""Progress (harness spec 3.4.0, `progress.md`): one way to say what a reply is
doing, the same in every client.

The texts and the line are the spec's conformance cases (test_harness_spec.py,
area `progress`). Here: the tracker keeps `since` for the same phase, the
reply's own loop sets the phases a person waits through, a card open is the
person's turn, a finished reply says how it ended, a device's `phase` event
and its tree are taken as the spec shapes them, and the two streams -- the
apps' JSON and the page's HTML -- carry it beside the status, escaped.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from lembas.services import device_link, interaction, progress
from lembas.services import generation as generation_service
from tests.test_api_v2 import _bearer, _events, _models, _upstream


def test_the_same_phase_for_the_same_thing_keeps_since():
    tracker = progress.Tracker()
    tracker.set("writing")
    since = tracker.current["since"]
    tracker.set("writing", rate=12.5)
    assert tracker.current == {"phase": "writing", "since": since, "rate": 12.5}
    tracker.set("running_tool", tool="bash", what="ls")
    first = tracker.current["since"]
    tracker.current["since"] = first - 5000  # as if it had run five seconds
    tracker.set("running_tool", tool="bash", what="ls")
    assert tracker.current["since"] == first - 5000
    tracker.set("running_tool", tool="bash", what="pwd")
    assert tracker.current["since"] >= first


def test_unknown_fields_are_left_out_never_null():
    tracker = progress.Tracker()
    tracker.set("running_tool", tool="read", what=None, colour="red")
    assert set(tracker.current) == {"phase", "since", "tool"}


def test_an_open_card_is_the_persons_turn_and_a_finished_reply_says_how_it_ended():
    generation = generation_service.Generation(chat_id="c", message_id="m")
    generation.tracker.set("running_tool", tool="bash")
    item = interaction.Item(index=0, kind=interaction.KIND_APPROVAL, key="0", title="Run",
                            detail="rm x", tool_name="bash")
    # The card as the stream reads it (`interaction.build` wants a running loop).
    generation.waiting = [SimpleNamespace(items=(item,))]
    assert progress.current(generation)["phase"] == "waiting_for_person"
    assert progress.current(generation)["what"] == "approval"
    assert progress.current(generation)["tool"] == "bash"
    generation.waiting = []
    generation.error = "The server said no.\nTraceback…"
    generation_service._final_progress(generation)
    generation.done = True
    assert progress.current(generation)["phase"] == "failed"
    assert progress.current(generation)["reason"] == "The server said no."
    finished = generation_service.Generation(chat_id="c", message_id="m")
    finished.tracker.set("writing")
    generation_service._final_progress(finished)
    finished.done = True
    assert progress.current(finished) is None
    capped = generation_service.Generation(chat_id="c", message_id="m")
    capped.wrap_limit = "round limit"
    generation_service._final_progress(capped)
    capped.done = True
    assert progress.current(capped) == {**capped.tracker.current, "reason": "steps"}


def test_a_foreground_task_is_waiting_for_subagents_and_a_tool_names_its_call():
    generation = generation_service.Generation(chat_id="c", message_id="m")
    calls = [{"name": "task"}, {"name": "task"}]
    generation_service._running_tool(generation, calls, [{"prompt": "a"}, {"prompt": "b"}], {})
    assert generation.tracker.current["phase"] == "waiting_for_subagents"
    assert generation.tracker.current["running"] == 2
    generation_service._running_tool(
        generation, [{"name": "bash"}], [{"command": "make test\nmore"}], {}
    )
    assert generation.tracker.current["what"] == "make test"


def test_the_prompt_read_comes_from_a_servers_progress_frames():
    frame = {"prompt_progress": {"total": 3000, "cache": 1000, "processed": 999}}
    assert generation_service.prompt_read(frame) == {"done": 1999, "total": 3000}
    assert generation_service.prompt_read({"choices": []}) is None


def test_a_reply_goes_through_reading_thinking_writing(monkeypatch):
    seen: list[str] = []
    original = progress.Tracker.set

    def record(self, phase, **data):
        original(self, phase, **data)
        if phase and (not seen or seen[-1] != phase):
            seen.append(phase)

    monkeypatch.setattr(progress.Tracker, "set", record)
    generation = generation_service.Generation(chat_id="c", message_id="m")
    generation.tracker.set("reading_prompt")
    generation.tracker.set("thinking")
    generation_service._writing(generation)
    assert seen == ["reading_prompt", "thinking", "writing"]


def test_the_apps_stream_carries_progress_beside_the_status(client, db, registered, mock_http):
    _models(db)
    mock_http(_upstream())
    headers = _bearer(db)
    body = client.post("/api/v2/chats", headers=headers, json={"text": "Hi"}).json()
    url = f"/api/v2/chats/{body['chat']['id']}/messages/{body['reply']['id']}/stream"
    statuses = [data for name, data in _events(client.get(url, headers=headers).text)
                if name == "status"]
    assert statuses and all(set(s) == {"text", "progress"} for s in statuses)
    phases = [s["progress"]["phase"] for s in statuses if s["progress"]]
    assert set(phases) <= set(progress.phases())


def test_the_pages_frame_is_escaped_and_drawn_in_the_readers_words():
    value = {"phase": "running_tool", "since": 1000, "tool": "bash",
             "what": "<script>alert(1)</script>"}
    html = progress.html(value, now=6000)
    assert "<script>" not in html
    assert "Running bash: &lt;script&gt;alert(1)&lt;/script&gt; · 5s" in html
    assert json.loads(
        html.split('data-progress="')[1].split('"')[0].replace("&quot;", '"')
        .replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    ) == value


def test_a_pacer_sends_a_new_phase_at_once_and_holds_a_data_change_back():
    pacer = progress.Pacer()
    first = {"phase": "writing", "since": 1}
    assert pacer.due(first)
    assert not pacer.due({"phase": "writing", "since": 1, "rate": 3.0})
    assert pacer.due({"phase": "running_tool", "since": 2, "tool": "read"})


def test_a_devices_phase_event_is_the_replys_and_a_subagents_goes_to_the_tree():
    generation = generation_service.Generation(chat_id="nochat", message_id="m")
    device_link.apply_event(generation, {"type": "phase", "progress": {
        "phase": "running_tool", "since": 5, "tool": "bash", "what": "ls", "evil": {"x": 1}}})
    assert generation.tracker.current == {
        "phase": "running_tool", "since": 5, "tool": "bash", "what": "ls"}
    # Read off the events no more once the device says its phase itself.
    device_link.apply_event(generation, {"type": "reasoning", "text": "hm"})
    assert generation.tracker.current["phase"] == "running_tool"
    device_link.apply_event(generation, {"type": "phase", "progress": {"phase": "dancing"}})
    assert generation.tracker.current is None
    device_link.apply_event(generation, {"type": "team", "tree": {
        "min": 1, "max": 4, "size": 2, "open_slots": 0,
        "agents": [{"id": "lead", "name": "lead", "parent": None, "state": "working"},
                   {"id": "s1", "name": "parser", "parent": "lead", "state": "working",
                    "position": "x" * 900, "context": {"used": 10, "window": 100}}]}})
    assert len(generation.team_tree["agents"][1]["position"]) == 200
    device_link.apply_event(generation, {"type": "phase", "agent": "s1",
                                         "progress": {"phase": "thinking", "since": 9}})
    assert generation.team_tree["agents"][1]["progress"] == {"phase": "thinking", "since": 9}


def test_a_device_without_the_capability_has_its_phase_read_off_its_events():
    generation = generation_service.Generation(chat_id="nochat", message_id="m")
    device_link.apply_event(generation, {"type": "reasoning", "text": "hm"})
    assert generation.tracker.current["phase"] == "thinking"
    device_link.apply_event(generation, {"type": "tool_start", "id": "1", "name": "bash",
                                         "args": {"command": "make"}})
    assert generation.tracker.current == {
        **generation.tracker.current, "phase": "running_tool", "tool": "bash", "what": "make"}
