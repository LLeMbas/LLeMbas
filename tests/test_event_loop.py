"""Work that must not be done on the event loop.

One loop serves every reply's stream, every page and every poll, so whatever
runs on it without waiting holds all of them. These are the places measured as
holding it longest on the chat's paths (`scripts/perf_measure.py --slow`), each
now in a thread: the test is that it runs where no event loop is running.
"""

from __future__ import annotations

import asyncio

from lembas.api import pages as pages_api
from lembas.services import generation as generation_service
from lembas.services.index import pipeline, worker


def _on_the_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def test_the_chat_page_is_rendered_off_the_loop(client, db, registered, make_chat, monkeypatch):
    from tests.test_thread_window import _long_chat, _model

    _model(db)
    chat_id = make_chat()
    _long_chat(db, chat_id, count=12)
    seen: list[bool] = []
    real = pages_api.render

    def render(request, name, context):
        if name == "chat/index.html":
            seen.append(_on_the_loop())
        return real(request, name, context)

    monkeypatch.setattr(pages_api, "render", render)
    page = client.get(f"/chat/{chat_id}")
    assert page.status_code == 200 and "turn number 11 here" in page.text
    assert seen == [False]


async def test_the_index_reads_cuts_and_writes_off_the_loop(db, monkeypatch):
    """With no embedding model nothing in a pass ever waited, so a whole batch
    -- every message of a long chat, after a start -- was one uninterrupted
    stretch of the loop: over two seconds in the measure script."""
    seen: dict[str, bool] = {}

    def spy(name: str, real):
        def wrapped(*args, **kwargs):
            seen[name] = _on_the_loop()
            return real(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(pipeline, "reconcile_all", spy("reconcile", pipeline.reconcile_all))
    monkeypatch.setattr(pipeline, "_read_for_index", spy("read", pipeline._read_for_index))
    monkeypatch.setattr(pipeline, "_store_chunks", spy("store", pipeline._store_chunks))
    monkeypatch.setattr(pipeline.chunks, "split", spy("split", pipeline.chunks.split))

    from lembas.db.models import Chat, Message, User
    from lembas.security.passwords import hash_password

    user = User(name="frodo", email="frodo@shire.test", password_hash=hash_password("x" * 12))
    db.add(user)
    db.flush()
    chat = Chat(user_id=user.id, model_id="m")
    db.add(chat)
    db.flush()
    db.add(Message(chat_id=chat.id, role="user", content="Where does the mallorn grow? " * 8))
    db.commit()

    done = await worker.run_once(everything=True)
    assert seen.get("reconcile") is False
    if done:
        assert seen.get("read") is False and seen.get("store") is False
        assert seen.get("split") is False


async def test_the_finished_reply_is_written_and_rendered_off_the_loop(
    db, registered, make_chat, monkeypatch
):
    from lembas.api import chats as chats_api
    from lembas.db.models import Message

    chat_id = make_chat()
    row = Message(chat_id=chat_id, role="assistant", content="", complete=False)
    db.add(row)
    db.commit()
    seen: dict[str, bool] = {}
    real_persist = generation_service._persist

    def persist(*args, **kwargs):
        seen["persist"] = _on_the_loop()
        return real_persist(*args, **kwargs)

    monkeypatch.setattr(generation_service, "_persist", persist)
    generation = generation_service.Generation(chat_id=chat_id, message_id=row.id)
    generation_service._RUNNING[row.id] = generation
    await generation_service._run(generation)  # no model: straight to its end
    assert seen == {"persist": False} and generation.done

    real_frame = chats_api._final_frame

    def frame(*args, **kwargs):
        seen["frame"] = _on_the_loop()
        return real_frame(*args, **kwargs)

    monkeypatch.setattr(chats_api, "_final_frame", frame)
    chunks = [chunk async for chunk in chats_api._follow(chat_id, row.id)]
    assert seen["frame"] is False and any("event: done" in c for c in chunks)
