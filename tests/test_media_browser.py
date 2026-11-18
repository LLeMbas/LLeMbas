"""The cards of generated media in a conversation, measured in a real
Chromium at 1280x900 and at 390x844: the player, the bar under it and a
running job's row line up by construction -- the page never scrolls sideways,
nothing lies outside the window, the two buttons of a bar sit on one line
whatever the file is called, and what a thumb presses is at least 40 px.

Slow (a browser per page): run it through `heavy-guard`, by itself."""

from __future__ import annotations

import re
from datetime import timedelta
from types import SimpleNamespace

import pytest

from lembas.db.models import MediaJob, Message
from lembas.services.media import jobs, library
from tests.test_media_jobs import GLB, MP3, MP4, world  # noqa: F401
from tests.test_reports_layout import CHROMIUM, REQUIRED, _measure

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to measure in"),
]

NAMED = (
    ".media-card__bar > *, .media-job__row > *, .media-card__video, .media-card__audio, "
    ".media-card__model, .media-job__what"
)
TAPS = ".media-card__bar > .btn, .media-job__cancel"


@pytest.fixture(autouse=True)
def _a_browser_is_there():
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")


@pytest.fixture
def conversation(db, world) -> str:  # noqa: F811
    """A reply whose tools started a job that still runs, finished one, and
    made a track and a 3D model with a name that cannot wrap."""
    user, chat = world["user"], world["chat"]
    video = library.keep(db, user, kind="video", payload=MP4, chat_id=chat.id, width=832,
                         height=480, seconds=5)
    music = library.keep(db, user, kind="music", payload=MP3, chat_id=chat.id, seconds=60)
    model = library.keep(db, user, kind="model3d", payload=GLB, chat_id=chat.id)
    model["name"] = "a-three-dimensional-model-with-a-name-that-does-not-wrap-anywhere-at-all.glb"
    started = jobs.now()
    common = {
        "user_id": user.id, "chat_id": chat.id, "kind": "video", "connection_id": "c",
        "dialect": "comfyui", "model": "video_t2v", "label": "Studio · Text to video (Wan 2.1)",
        "params_json": {"prompt": "a kite over long dunes at dusk, " * 6},
        "started_at": started - timedelta(seconds=75),
        "deadline_at": started + timedelta(minutes=30),
    }
    running = MediaJob(state="running", provider_ref="r1", **common)
    done = MediaJob(state="done", provider_ref="r2", result_json=video, ended_at=started,
                    **common)
    failed = MediaJob(state="failed", provider_ref="r3", ended_at=started,
                      error="ComfyUI ran out of video memory in KSampler. " * 3, **common)
    db.add_all([running, done, failed])
    db.flush()
    db.add(Message(
        chat_id=chat.id, role="assistant", content="Started.", complete=True,
        tool_calls_json=[
            {"name": "video_generate", "kind": "image", "status": "ok",
             "media_job": {"id": row.id, "kind": "video", "state": "running"}}
            for row in (running, done, failed)
        ] + [
            {"name": "music_generate", "kind": "image", "status": "ok", "media": music},
            {"name": "model_generate", "kind": "image", "status": "ok", "media": model},
        ],
    ))
    db.commit()
    return f"/chat/{chat.id}"


class _Unfed:
    """The client, with the players' sources taken out of the page it hands
    over: the measured page is a file, where the raw route and the sealed
    frame's page are not. What is measured is the boxes the stylesheet gives
    a player, a frame and their bar -- which do not depend on the bytes."""

    def __init__(self, client) -> None:
        self._client = client

    def get(self, path, **kwargs):
        response = self._client.get(path, **kwargs)
        if not path.startswith("/chat/"):
            return response
        text = re.sub(r'src="/files/(?:raw\?|viewer/)[^"]*"', 'src="about:blank"', response.text)
        return SimpleNamespace(status_code=response.status_code, text=text)


@pytest.mark.parametrize("width", [1280, 390])
def test_the_cards_fit_and_line_up(client, conversation, tmp_path, width):
    probe = _measure(_Unfed(client), conversation, width, tmp_path, NAMED, TAPS)
    assert probe["page"] <= probe["width"], f"the chat scrolls sideways at {width}"
    boxes = probe["named"]
    whats = [b["what"] for b in boxes]
    for wanted in ("media-video", "media-audio", "media-model", "media-job-cancel",
                   "media-open", "media-download"):
        assert any(wanted in what for what in whats), f"{wanted} was not drawn: {whats}"
    assert all(b["left"] >= 0 and b["right"] <= probe["width"] + 1 for b in boxes), boxes
    # Each bar: the name, then two buttons on one line, ending where the bar ends.
    opens = [b for b in boxes if b["what"] == "media-open"]
    downloads = [b for b in boxes if b["what"] == "media-download"]
    names = [b for b in boxes if "media-card__name" in b["what"]]
    assert len(opens) == len(downloads) == len(names) == 3
    for name, first, second in zip(names, opens, downloads, strict=True):
        assert first["top"] == second["top"] and first["bottom"] == second["bottom"], (
            first, second)
        assert name["right"] <= first["left"] < first["right"] <= second["left"]
    # Every bar's buttons end at the same x: the long name was cut, not the row pushed.
    assert len({b["right"] for b in downloads}) == 1, downloads
    # A running job: state, time and Cancel in one row; Cancel at the row's end.
    cancel = next(b for b in boxes if b["what"] == "media-job-cancel")
    state = next(b for b in boxes if "media-job__state" in b["what"] and b["top"] < cancel["bottom"]
                 and b["bottom"] > cancel["top"])
    assert state["right"] <= cancel["left"]
    players = [b for b in boxes if b["what"] in ("media-video", "media-audio", "media-model")]
    assert all(b["right"] - b["left"] >= 200 and b["h"] > 20 for b in players), players
    if width < 500:
        assert all(tap["h"] >= 40 for tap in probe["taps"]), probe["taps"]
