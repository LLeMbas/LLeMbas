"""The search index: chunking by tokens, extraction, `.lembasignore`, the
pipeline (content indexed once per hash), and the search with its access
post-filter.

No real embedder anywhere: `fake_embedder` stands one in, a bag of concepts in
sixteen dimensions, so "automobile" is near "car" without a model. The chunk
conformance cases are the CLI's `harness/conformance/chunk_tokens.json`, read
from the vendored spec once it carries them and from a copy until then.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from lembas.db import vec
from lembas.db.models import (
    INDEX_DONE,
    INDEX_SKIPPED,
    ROLE_ASSISTANT,
    ROLE_USER,
    Chat,
    IndexChunk,
    IndexText,
    Message,
    PathHash,
    SessionGrant,
    User,
)
from lembas.security.passwords import hash_password
from lembas.services import fs, harness_spec
from lembas.services import index as index_service
from lembas.services.index import chunks, extract, ignore, pipeline, query, worker
from lembas.services.llm.openai_client import LLMError

_VENDORED = harness_spec.conformance_dir() / "chunk_tokens.json"
_COPY = Path(__file__).parent / "fixtures" / "harness" / "chunk_tokens.json"
CHUNK_SPEC = json.loads((_VENDORED if _VENDORED.exists() else _COPY).read_text(encoding="utf-8"))


# --- Chunking --------------------------------------------------------------------
@pytest.mark.parametrize("case", CHUNK_SPEC["cases"], ids=lambda c: c["text"][:24])
def test_chunk_tokens_conformance(case):
    assert case["tokenizer"] == "words"
    got = chunks.split(case["text"], chunks.words, size=case["size"], overlap=case["overlap"])
    assert [piece.text for piece in got] == case["expect"]


def test_the_estimate_counts_half_a_token_a_byte_and_never_splits_a_character():
    samples = ("plain ascii text", "Dobrý deň, ľúbim čučoriedky", "日本語のテキスト", "🙂🙂 ok")
    for sample in samples:
        spans = chunks.estimate(sample)
        assert len(spans) >= chunks.count_estimate(sample)
        for start, end in spans:
            assert 0 <= start <= end <= len(sample)
    dense = "ľščťžýáíé" * 400  # two bytes a letter: one token each
    pieces = chunks.split(dense)
    assert len(pieces) > 1
    assert all(chunks.count_estimate(p.text) <= chunks.SIZE for p in pieces)
    assert "".join(p.text for p in pieces).count("ľ") >= 400  # overlap repeats, never loses


def test_pieces_know_their_lines():
    text = "\n".join(f"line {n} " + "word " * 30 for n in range(1, 200))
    pieces = chunks.split(text, chunks.words, size=64, overlap=8)
    assert len(pieces) > 3
    first = pieces[0]
    assert chunks.line_range(text, first.start, first.end)[0] == 1
    later = pieces[2]
    start, end = chunks.line_range(text, later.start, later.end)
    lines = text.splitlines()
    assert later.text.splitlines()[0].strip() in lines[start - 1]
    assert later.text.splitlines()[-1].strip() in lines[end - 1]


def test_halve_cuts_near_the_middle_at_a_break():
    left, right = chunks.halve("First paragraph here.\n\nSecond paragraph there.")
    assert (left, right) == ("First paragraph here.", "Second paragraph there.")
    assert chunks.halve("x") is None
    assert "".join(chunks.halve("abcdefgh")) == "abcdefgh"


# --- Extraction ------------------------------------------------------------------
def _docx(paragraphs: list[str]) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_extraction_goes_by_the_bytes():
    assert extract.extract(b"Ahoj, svet").text == "Ahoj, svet"
    assert extract.extract("﻿BOM text".encode()).text == "BOM text"
    assert extract.extract("utf16".encode("utf-16")).text == "utf16"
    assert extract.extract(b"\x89PNG\r\n\x1a\n\x00\x00binary").skip == "not text"
    assert extract.extract(b"\xc3\x28 not utf8").skip == "not text"
    assert extract.extract(b"   \n").skip == "empty"
    docx = extract.extract(_docx(["Mellon is the password.", "Second paragraph."]))
    assert docx.text == "Mellon is the password.\n\nSecond paragraph."
    broken = extract.extract(b"%PDF-1.7 this is not really a pdf")
    assert broken.error or broken.skip
    assert extract.extract(b"x" * (extract.MAX_BYTES + 1)).skip.startswith("larger than")


# --- .lembasignore ------------------------------------------------------------------
def test_lembasignore_is_gitignore_like():
    matcher = ignore.Matcher.build(
        [
            ("/p/.lembasignore", "*.log\nbuild/\n/secret.txt\n!keep.log\ndocs/**/draft-*\n"),
            ("/p/sub/.lembasignore", "!*.log\n"),
            ("/off/.lembasignore", "*\n"),
        ]
    )
    assert matcher.ignored("/p/a.log")
    assert not matcher.ignored("/p/keep.log")
    assert not matcher.ignored("/p/sub/again.log")  # a deeper file has the last word
    assert matcher.ignored("/p/build/x.txt") and matcher.ignored("/p/deep/build/y.md")
    assert not matcher.ignored("/p/build.txt")  # dir-only pattern
    assert matcher.ignored("/p/secret.txt") and not matcher.ignored("/p/sub/secret.txt")
    assert matcher.ignored("/p/docs/a/b/draft-1.md") and not matcher.ignored("/p/docs/final.md")
    assert matcher.ignored("/off/anything.md")
    assert matcher.ignored("/q/.git/config")
    assert not matcher.ignored("/q/readme.md")


# --- The pipeline -----------------------------------------------------------------
def _user(db, email: str) -> User:
    user = User(name=email.split("@")[0], email=email, password_hash=hash_password("x" * 12))
    db.add(user)
    db.commit()
    fs.ensure_home(db, user)
    db.commit()
    return user


@pytest.fixture
def alice(db) -> User:
    return _user(db, "alice@shire.test")


@pytest.fixture(autouse=True)
def fresh_index():
    worker.clear()
    pipeline._TOKENIZES.clear()
    yield
    worker.clear()


async def refresh() -> int:
    return await index_service.run_once(everything=True)


def _write(db, user, path: str, data: str | bytes, chat: Chat | None = None):
    view = fs.view(db, user, chat)
    node = fs.write(view, path, data.encode() if isinstance(data, str) else data)
    db.commit()
    return node


async def test_content_is_indexed_once_per_hash_and_moves_cost_nothing(db, alice):
    body = "The west gate of Moria opens to the word mellon. " * 3
    _write(db, alice, "/home/alice/a.md", body)
    _write(db, alice, "/home/alice/copy/b.md", body)
    await refresh()
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(IndexText)) == 1
    assert db.scalar(select(func.count()).select_from(PathHash)) == 2
    before = sorted(db.scalars(select(IndexChunk.id)))
    assert before

    hits = await index_service.search(db, alice, None, "mellon")
    assert sorted(h.path for h in hits) == ["/home/alice/a.md", "/home/alice/copy/b.md"]
    assert hits[0].line_start == 1 and "mellon" in hits[0].snippet

    fs.mv(fs.view(db, alice), "/home/alice/copy", "/home/alice/moved")
    db.commit()
    await refresh()
    db.expire_all()
    assert sorted(db.scalars(select(IndexChunk.id))) == before  # nothing re-cut
    hits = await index_service.search(db, alice, None, "mellon")
    assert sorted(h.path for h in hits) == ["/home/alice/a.md", "/home/alice/moved/b.md"]

    fs.rm(fs.view(db, alice), "/home/alice/a.md")
    fs.rm(fs.view(db, alice), "/home/alice/moved")
    db.commit()
    await refresh()
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(PathHash)) == 0
    assert db.scalar(select(func.count()).select_from(IndexText)) == 0  # collected
    assert db.scalar(select(func.count()).select_from(IndexChunk)) == 0
    assert await index_service.search(db, alice, None, "mellon") == []


async def test_a_rewrite_reindexes_and_the_old_text_is_gone(db, alice):
    _write(db, alice, "/home/alice/n.md", "galadriel's mirror shows things that may be")
    await refresh()
    _write(db, alice, "/home/alice/n.md", "the phial of earendil, light when all lights go out")
    await refresh()
    db.expire_all()
    assert await index_service.search(db, alice, None, "galadriel") == []
    assert [h.path for h in await index_service.search(db, alice, None, "earendil")] == [
        "/home/alice/n.md"
    ]


async def test_ignored_binary_and_oversized_files_are_left_out(db, alice, monkeypatch):
    _write(db, alice, "/home/alice/.lembasignore", "*.log\nvendor/\n")
    _write(db, alice, "/home/alice/run.log", "error at the bridge of khazad-dum")
    _write(db, alice, "/home/alice/vendor/lib.md", "khazad-dum vendored copy")
    _write(db, alice, "/home/alice/notes.md", "khazad-dum is a deep place")
    _write(db, alice, "/home/alice/pic.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00binary khazad")
    await refresh()
    hits = await index_service.search(db, alice, None, "khazad")
    assert [h.path for h in hits] == ["/home/alice/notes.md"]
    db.expire_all()
    png = db.scalar(
        select(IndexText).join(PathHash, PathHash.hash == IndexText.hash).where(
            PathHash.path == "/home/alice/pic.png"
        )
    )
    assert png.status == INDEX_SKIPPED and png.error == "not text"

    # Changing the ignore file takes a file back in, with no rewrite of the file.
    _write(db, alice, "/home/alice/.lembasignore", "vendor/\n")
    await refresh()
    hits = await index_service.search(db, alice, None, "khazad")
    assert sorted(h.path for h in hits) == ["/home/alice/notes.md", "/home/alice/run.log"]

    monkeypatch.setattr(extract, "MAX_BYTES", 10)
    await refresh()
    assert await index_service.search(db, alice, None, "khazad") == []


async def test_no_embedder_writes_no_vectors(db, alice):
    _write(db, alice, "/home/alice/a.md", "a document with words in it, long enough to index")
    await refresh()
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(IndexChunk)) == 1
    assert db.scalar(select(IndexChunk.vector)) is None
    assert db.scalar(select(IndexText.model)) == ""


# --- The access post-filter --------------------------------------------------------
async def test_a_hit_in_an_unticked_directory_never_appears(db, alice):
    for n in range(30):
        _write(db, alice, f"/home/alice/closed/c{n}.md", f"the one ring, closed copy number {n}")
    _write(db, alice, "/home/alice/open/o.md", "the one ring, the copy this session may read")
    await refresh()

    chat = Chat(user_id=alice.id, grants_customised=True)
    db.add(chat)
    db.flush()
    db.add(SessionGrant(chat_id=chat.id, path="/home/alice/open", level="ro"))
    db.commit()

    # Outside the chat, the person sees all of it.
    everything = await index_service.search(db, alice, None, "ring", limit=50)
    assert len(everything) == 31
    # In the chat: only the ticked directory, even though thirty better-or-equal
    # hits in the closed one were scored first, and even when asked for one.
    for limit in (1, 5, 50):
        hits = await index_service.search(db, alice, chat, "ring", limit=limit)
        assert [h.path for h in hits] == ["/home/alice/open/o.md"]
    # A prefix outside what the chat may read finds nothing, and says nothing.
    closed = "/home/alice/closed"
    assert await index_service.search(db, alice, chat, "ring", path_prefix=closed) == []


async def test_another_persons_files_and_chats_are_not_found(db, alice):
    bob = _user(db, "bob@shire.test")
    _write(db, bob, "/home/bob/secret.md", "bob keeps the shire's second breakfast recipe here")
    chat = Chat(user_id=bob.id, title="Breakfast")
    db.add(chat)
    db.flush()
    db.add(Message(chat_id=chat.id, role=ROLE_USER, content="second breakfast plans for bob"))
    db.commit()
    await refresh()
    assert await index_service.search(db, alice, None, "breakfast", kinds=("file", "chat")) == []
    found = await index_service.search(db, bob, None, "breakfast", kinds=("file", "chat"))
    assert {h.kind for h in found} == {"file", "chat"}


# --- Chats --------------------------------------------------------------------------
async def test_chat_messages_are_found_by_their_owner_with_a_kind_filter(db, alice):
    chat = Chat(user_id=alice.id, title="Road trip")
    temp = Chat(user_id=alice.id, title="Gone soon", temporary=True)
    db.add_all([chat, temp])
    db.flush()
    db.add_all(
        [
            Message(chat_id=chat.id, role=ROLE_USER, content="How far is it to Rivendell?"),
            Message(
                chat_id=chat.id, role=ROLE_ASSISTANT, content="Rivendell lies east of Bree."
            ),
            Message(chat_id=chat.id, role="tool", content="Rivendell tool output"),
            Message(chat_id=temp.id, role=ROLE_USER, content="Rivendell in a temporary chat"),
        ]
    )
    db.commit()
    _write(db, alice, "/home/alice/map.md", "A map showing Rivendell and the misty mountains")
    await refresh()

    chats_only = await index_service.search(db, alice, None, "rivendell", kinds=("chat",))
    assert {h.title for h in chats_only} == {"Road trip"} and len(chats_only) == 2
    assert all(h.kind == "chat" and h.node_id == chat.id and h.message_id for h in chats_only)
    files_only = await index_service.search(db, alice, None, "rivendell")
    assert [h.path for h in files_only] == ["/home/alice/map.md"]
    both = await index_service.search(db, alice, None, "rivendell", kinds=("file", "chat"))
    assert len(both) == 3

    db.delete(chat)
    db.commit()
    await refresh()
    assert await index_service.search(db, alice, None, "rivendell", kinds=("chat",)) == []


# --- Vectors, with a fake embedder ---------------------------------------------------
CONCEPTS = {
    "car": 0, "automobile": 0, "vehicle": 0, "motor": 0,
    "deploy": 1, "release": 1, "ship": 1, "rollout": 1,
    "cat": 2, "kitten": 2, "feline": 2,
    "river": 3, "anduin": 3, "water": 3,
}
DIMS = 16


def fake_vector(text: str) -> list[float]:
    out = [0.0] * DIMS
    for word in query.terms(text.lower()):
        if word in CONCEPTS:
            out[CONCEPTS[word]] += 1.0
        else:
            out[4 + int(hashlib.md5(word.encode()).hexdigest(), 16) % (DIMS - 4)] += 0.05
    length = math.sqrt(sum(v * v for v in out)) or 1.0
    return [v / length for v in out]


@pytest.fixture
def fake_embedder(monkeypatch):
    if not vec.available():
        pytest.skip("sqlite-vec is not installed")
    calls: list[list[str]] = []
    refuse_over = {"chars": 0}

    async def embed(_worker, texts):
        calls.append(list(texts))
        if refuse_over["chars"] and any(len(t) > refuse_over["chars"] for t in texts):
            raise LLMError("input is too large to process. increase the physical batch size")
        return [fake_vector(t) for t in texts]

    async def tokenizer(_worker, _body):
        return chunks.estimate

    worker_ = pipeline.Embedder(endpoint=SimpleNamespace(base_url="fake"), model_id="fake-embed")
    monkeypatch.setattr(pipeline, "embedder", lambda _db: worker_)
    monkeypatch.setattr(pipeline, "_embed", embed)
    monkeypatch.setattr(pipeline, "tokenizer_for", tokenizer)
    return SimpleNamespace(calls=calls, refuse_over=refuse_over)


async def test_meaning_finds_what_keywords_cannot(db, alice, fake_embedder):
    _write(db, alice, "/home/alice/garage.md", "My car is parked in the garage next to the bike.")
    _write(db, alice, "/home/alice/pets.md", "The kitten sleeps on the sofa all afternoon long.")
    await refresh()
    db.expire_all()
    with_vectors = select(func.count()).select_from(IndexChunk).where(
        IndexChunk.vector.is_not(None)
    )
    assert db.scalar(with_vectors) == 2
    assert set(db.scalars(select(IndexText.model))) == {"fake-embed"}

    hits = await index_service.search(db, alice, None, "automobile")
    assert hits and hits[0].path == "/home/alice/garage.md"
    hits = await index_service.search(db, alice, None, "feline")
    assert hits[0].path == "/home/alice/pets.md"


async def test_a_refused_piece_is_halved_and_nothing_is_dropped(db, alice, fake_embedder):
    paragraphs = [f"Paragraph {n} talks about the river anduin and its water." for n in range(12)]
    body = "\n\n".join(paragraphs)
    _write(db, alice, "/home/alice/long.md", body)
    fake_embedder.refuse_over["chars"] = 200
    await refresh()
    db.expire_all()
    pieces = list(db.scalars(select(IndexChunk).order_by(IndexChunk.ordinal)))
    assert len(pieces) > 1 and all(len(p.text) <= 200 for p in pieces)
    assert all(p.vector is not None for p in pieces)
    joined = "\n\n".join(p.text for p in pieces)
    for paragraph in paragraphs:
        assert paragraph in joined
    assert db.scalar(select(IndexText.status)) == INDEX_DONE


async def test_a_new_embedder_sends_hashes_back_to_the_queue(db, alice, fake_embedder, monkeypatch):
    _write(db, alice, "/home/alice/a.md", "Deploy the release on friday, said nobody wise.")
    await refresh()
    first = len(fake_embedder.calls)
    other = pipeline.Embedder(endpoint=SimpleNamespace(base_url="fake"), model_id="other-embed")
    monkeypatch.setattr(pipeline, "embedder", lambda _db: other)
    await refresh()
    db.expire_all()
    assert len(fake_embedder.calls) > first
    assert set(db.scalars(select(IndexText.model))) == {"other-embed"}


# --- Recall and latency of the bit scan, on synthetic vectors ------------------------
def _clustered(n: int, dims: int, centres: int, seed: int = 7):
    import random

    rng = random.Random(seed)
    middles = [[rng.gauss(0, 1) for _ in range(dims)] for _ in range(centres)]
    out = []
    for i in range(n):
        middle = middles[i % centres]
        vector = [m + rng.gauss(0, 0.6) for m in middle]
        length = math.sqrt(sum(v * v for v in vector))
        out.append([v / length for v in vector])
    return out, middles, rng


def _load_vectors(db, alice, vectors, dims):
    from sqlalchemy import text

    from lembas.services.index import schema

    db.add(IndexText(hash="h-synthetic", status=INDEX_DONE, model="synthetic", dims=dims))
    db.add(PathHash(org_id=alice.org_id, kind="file", node_id="n", path="/home/alice/s.md",
                    hash="h-synthetic"))
    db.flush()
    schema.ensure_vec(db, dims)
    for n, vector in enumerate(vectors):
        packed = pipeline.pack(vector)
        chunk = IndexChunk(hash="h-synthetic", ordinal=n, text=f"piece {n}", vector=packed)
        db.add(chunk)
        db.flush()
        db.execute(
            text("INSERT INTO index_vec(rowid, embedding) VALUES (:i, vec_quantize_binary(:v))"),
            {"i": chunk.id, "v": packed},
        )
    db.commit()


def _recall(db, alice, middles, rng, dims, queries=10, k=20):
    import time

    scope = query.scope_for(alice, None, None, ("file",))
    total, spent = 0.0, 0.0
    for q in range(queries):
        middle = middles[q % len(middles)]
        vector = [m + rng.gauss(0, 0.6) for m in middle]
        length = math.sqrt(sum(v * v for v in vector))
        packed = pipeline.pack([v / length for v in vector])
        exact_limit = query.EXACT_LIMIT
        query.EXACT_LIMIT = 10**9
        exact = query.meaning_ids(db, packed, "synthetic", dims, scope, k)
        query.EXACT_LIMIT = 0
        began = time.perf_counter()
        bits = query.meaning_ids(db, packed, "synthetic", dims, scope, k)
        spent += time.perf_counter() - began
        query.EXACT_LIMIT = exact_limit
        total += len(set(exact) & set(bits)) / k
    return total / queries, spent / queries


def test_bit_scan_with_rerank_recalls_the_exact_top_20(db, alice):
    if not vec.available():
        pytest.skip("sqlite-vec is not installed")
    dims = 64
    vectors, middles, rng = _clustered(3000, dims, centres=30)
    _load_vectors(db, alice, vectors, dims)
    recall, latency = _recall(db, alice, middles, rng, dims)
    assert recall >= 0.9, recall
    assert latency < 0.5, latency


@pytest.mark.slow
def test_bit_scan_recall_and_latency_at_50k(db, alice):
    if not vec.available():
        pytest.skip("sqlite-vec is not installed")
    dims = 1024
    vectors, middles, rng = _clustered(50_000, dims, centres=500)
    _load_vectors(db, alice, vectors, dims)
    recall, latency = _recall(db, alice, middles, rng, dims)
    assert recall >= 0.9, recall
    assert latency < 1.0, latency


# --- The worker ---------------------------------------------------------------------
async def test_the_worker_notices_writes_and_reports_progress(db, alice):
    import asyncio

    worker.install()
    assert index_service.start()
    try:
        _write(db, alice, "/home/alice/w.md", "the eagles are coming, the eagles are coming")
        for _ in range(100):
            await asyncio.sleep(0.05)
            if await index_service.search(db, alice, None, "eagles"):
                break
        assert [h.path for h in await index_service.search(db, alice, None, "eagles")] == [
            "/home/alice/w.md"
        ]
        state = index_service.progress()
        assert state["running"] and state["states"].get("done", 0) >= 1 and state["pending"] == 0
    finally:
        await index_service.shutdown()
