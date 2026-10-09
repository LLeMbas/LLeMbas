"""Keyword search, semantic search, and the two fused.

The property this file exists to hold is the first one: **with no embedding
model configured, everything here is byte-for-byte the search that has always
been.** That is what makes this safe to add to an instance that never asked for
it, and it is the one claim a comment cannot make credible.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from lembas.db.models import CHUNK_NOTE, Chunk, Connection, Model, User
from lembas.services import settings_store
from lembas.services.crypto import encrypt
from lembas.services.library import chunks as chunk_service
from lembas.services.library import indexing, retrieval
from lembas.services.library import notes as notes_service
from lembas.services.library.fts import SearchHit


@pytest.fixture(autouse=True)
def clean():
    indexing.clear()
    yield
    indexing.clear()


@pytest.fixture
def user(db, registered) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


@pytest.fixture
def embedding_model(db):
    """A connection and a model marked for embeddings, and the setting pointing
    at it. Nothing here makes a request; the tests that need one mock it."""
    connection = Connection(
        name="Embed", base_url="http://127.0.0.1:9", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(
        Model(
            connection_id=connection.id,
            model_id="embed-1",
            capabilities_json={"embeddings": True},
        )
    )
    db.commit()
    settings_store.update(db, {"embedding_model_id": "embed-1"}, key=settings_store.EXTRACTION)
    return "embed-1"


def _vector_answer(vector):
    def handler(request: httpx.Request) -> httpx.Response:
        count = len(request.read().decode().split('"input"')[1].split("[")[1].split(","))
        return httpx.Response(
            200, json={"data": [{"embedding": vector, "index": i} for i in range(count)]}
        )

    return handler


def _store_chunk(db, note, vector, *, text="stored"):
    db.add(
        Chunk(
            owner_id=note.owner_id,
            resource_type=CHUNK_NOTE,
            resource_id=note.id,
            ordinal=0,
            text=text,
            vector=chunk_service.pack(vector),
            dims=len(vector),
            model_id="embed-1",
            source_hash=chunk_service.digest(text),
        )
    )
    db.commit()


# --- Nothing configured ---------------------------------------------------------
def test_with_no_model_nothing_is_indexed(db, user):
    """No chunk rows, no requests, no cost. Asserted rather than assumed: it is
    the whole reason this is safe to add to an existing instance."""
    assert indexing.enabled(db) is False
    notes_service.create(db, owner=user, title="Moria", body="the west gate")

    assert db.scalars(select(Chunk)).all() == []


def test_with_no_model_search_is_exactly_the_keyword_search(db, user):
    from lembas.services.library import fts

    notes_service.create(db, owner=user, title="Moria", body="the west gate opens")
    notes_service.create(db, owner=user, title="Bree", body="an inn on the road")

    through_retrieval = retrieval.search(db, "notes_fts", "gate", kind=CHUNK_NOTE, limit=10)
    direct = fts.search_ids(db, "notes_fts", "gate", limit=10)

    assert [hit.id for hit in through_retrieval] == [hit.id for hit in direct]


async def test_with_no_model_a_query_embeds_to_nothing(db, user):
    assert await retrieval.embed_query(db, "anything") is None


# --- Scoring --------------------------------------------------------------------
def test_a_record_scores_as_its_best_piece(db, user, embedding_model):
    """One paragraph that answers the question is what makes a document worth
    returning. Averaging would rank a long document about something else above a
    short one that says exactly the thing."""
    near = notes_service.create(db, owner=user, title="Near", body="x")
    far = notes_service.create(db, owner=user, title="Far", body="y")
    # Two pieces for `near`, one of them irrelevant. The good one has to win.
    _store_chunk(db, near, [1.0, 0.0])
    db.add(
        Chunk(
            owner_id=user.id,
            resource_type=CHUNK_NOTE,
            resource_id=near.id,
            ordinal=1,
            text="unrelated",
            vector=chunk_service.pack([0.0, 1.0]),
            dims=2,
            model_id="embed-1",
        )
    )
    _store_chunk(db, far, [0.7, 0.714])
    db.commit()

    hits = retrieval.semantic_ids(db, CHUNK_NOTE, [1.0, 0.0], limit=10)

    assert [hit.id for hit in hits][0] == near.id


def test_a_vector_from_another_model_is_skipped(db, user, embedding_model):
    """A change of embedding model with a rebuild still pending. Scoring across
    two spaces produces a confident wrong answer rather than a missing one."""
    note = notes_service.create(db, owner=user, title="Old", body="x")
    _store_chunk(db, note, [1.0, 0.0, 0.0])  # three wide

    assert retrieval.semantic_ids(db, CHUNK_NOTE, [1.0, 0.0], limit=10) == []


def test_a_semantic_only_match_is_found(db, user, embedding_model):
    """The point of the whole feature: a record whose words do not appear in the
    query at all."""
    note = notes_service.create(db, owner=user, title="Doors", body="mellon")
    _store_chunk(db, note, [1.0, 0.0])

    keyword = retrieval.search(db, "notes_fts", "how do I get in", kind=CHUNK_NOTE, limit=5)
    hybrid = retrieval.search(
        db, "notes_fts", "how do I get in", kind=CHUNK_NOTE, vector=[1.0, 0.0], limit=5
    )

    assert keyword == []
    assert [hit.id for hit in hybrid] == [note.id]


# --- Fusion ---------------------------------------------------------------------
def test_fusion_keeps_what_only_one_side_found():
    """Neither ranking's finds are dropped. That is the property that makes
    turning this on unable to make search worse."""
    keyword = [SearchHit(id="a", rank=-1.0), SearchHit(id="b", rank=-2.0)]
    meaning = [SearchHit(id="c", rank=0.9)]

    fused = {hit.id for hit in retrieval.fuse(keyword, meaning, limit=10)}

    assert fused == {"a", "b", "c"}


def test_agreeing_on_a_record_ranks_it_first():
    """RRF's whole behaviour: both lists having it beats either list alone."""
    keyword = [SearchHit(id="a", rank=-1.0), SearchHit(id="both", rank=-2.0)]
    meaning = [SearchHit(id="both", rank=0.9), SearchHit(id="c", rank=0.8)]

    assert retrieval.fuse(keyword, meaning, limit=10)[0].id == "both"


def test_fusing_with_nothing_is_the_other_list():
    keyword = [SearchHit(id="a", rank=-1.0), SearchHit(id="b", rank=-2.0)]

    assert [hit.id for hit in retrieval.fuse(keyword, [], limit=10)] == ["a", "b"]


def test_the_fused_score_is_larger_for_better():
    """The opposite of bm25's convention, which is worth saying out loud rather
    than leaving somebody to infer it from a number that stopped being
    negative."""
    fused = retrieval.fuse([SearchHit(id="a", rank=-1.0)], [SearchHit(id="a", rank=0.9)])

    assert fused[0].rank > 0


# --- Indexing -------------------------------------------------------------------
async def test_a_record_is_chunked_and_stored(db, user, embedding_model, mock_http):
    mock_http(_vector_answer([1.0, 0.0]))
    note = notes_service.create(db, owner=user, title="Moria", body="the west gate " * 200)

    written = await indexing.index_resource(CHUNK_NOTE, note.id)

    db.expire_all()
    rows = list(db.scalars(select(Chunk).where(Chunk.resource_id == note.id)))
    assert written == len(rows) > 1
    assert all(row.dims == 2 and row.model_id == "embed-1" for row in rows)
    assert [row.ordinal for row in rows] == list(range(len(rows)))


async def test_indexing_an_unchanged_record_is_free(db, user, embedding_model, mock_http):
    """The hash is what makes it free, and what makes "is this current?"
    answerable without embedding anything."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"embedding": [1.0, 0.0], "index": 0}]})

    mock_http(handler)
    note = notes_service.create(db, owner=user, title="Moria", body="short")
    await indexing.index_resource(CHUNK_NOTE, note.id)
    before = len(requests)

    await indexing.index_resource(CHUNK_NOTE, note.id)

    assert len(requests) == before


async def test_re_indexing_twice_is_idempotent(db, user, embedding_model, mock_http):
    mock_http(_vector_answer([1.0, 0.0]))
    note = notes_service.create(db, owner=user, title="Moria", body="short")

    await indexing.index_resource(CHUNK_NOTE, note.id, force=True)
    await indexing.index_resource(CHUNK_NOTE, note.id, force=True)

    db.expire_all()
    assert len(list(db.scalars(select(Chunk).where(Chunk.resource_id == note.id)))) == 1


async def test_a_failed_embedding_leaves_the_old_chunks(db, user, embedding_model, mock_http):
    """Deleting first and failing half way through would leave a record indexed
    by half of itself, which ranks worse than not being indexed and looks like
    nothing at all."""
    mock_http(_vector_answer([1.0, 0.0]))
    note = notes_service.create(db, owner=user, title="Moria", body="short")
    await indexing.index_resource(CHUNK_NOTE, note.id)

    mock_http(lambda request: httpx.Response(500, json={"error": "down"}))
    notes_service.update(db, note, body="something else entirely")
    await indexing.index_resource(CHUNK_NOTE, note.id)

    db.expire_all()
    assert len(list(db.scalars(select(Chunk).where(Chunk.resource_id == note.id)))) == 1


async def test_a_record_with_no_text_left_drops_its_chunks(
    db, user, embedding_model, mock_http, monkeypatch
):
    """A guard rather than a state anything reaches today: every one of the four
    stores requires a title, so `text_of` is never empty for a record that
    exists. It is driven anyway, because the alternative to a guard here is a
    record whose chunks outlive its content -- and a store whose title becomes
    optional is a change nobody would think to test this against.
    """
    mock_http(_vector_answer([1.0, 0.0]))
    note = notes_service.create(db, owner=user, title="Moria", body="short")
    await indexing.index_resource(CHUNK_NOTE, note.id)

    monkeypatch.setattr(indexing, "text_of", lambda row: "")
    await indexing.index_resource(CHUNK_NOTE, note.id)

    db.expire_all()
    assert list(db.scalars(select(Chunk).where(Chunk.resource_id == note.id))) == []


async def test_a_deleted_record_takes_its_chunks_with_it(db, user, embedding_model, mock_http):
    mock_http(_vector_answer([1.0, 0.0]))
    note = notes_service.create(db, owner=user, title="Moria", body="short")
    await indexing.index_resource(CHUNK_NOTE, note.id)
    note_id = note.id

    notes_service.delete(db, note)
    await indexing.index_resource(CHUNK_NOTE, note_id)

    db.expire_all()
    assert list(db.scalars(select(Chunk).where(Chunk.resource_id == note_id))) == []


def test_the_sweep_catches_what_had_no_loop_to_clean_up(db, user, embedding_model):
    """The backstop for a delete with no event loop running -- a CLI command, or
    a cascade from removing an account."""
    note = notes_service.create(db, owner=user, title="Moria", body="short")
    _store_chunk(db, note, [1.0, 0.0])
    note_id = note.id
    notes_service.delete(db, note)

    assert indexing.sweep_orphans(db) == 1
    assert list(db.scalars(select(Chunk).where(Chunk.resource_id == note_id))) == []


def test_the_text_of_a_record_is_the_indexed_columns(db, user):
    note = notes_service.create(db, owner=user, title="Moria", body="the west gate")

    body = indexing.text_of(note)

    assert "Moria" in body and "west gate" in body
    assert indexing.kind_of(note) == CHUNK_NOTE


# --- The picker -----------------------------------------------------------------
def test_a_chat_model_is_not_offered_as_an_embedder(db, user, embedding_model, client):
    connection = db.scalars(select(Connection)).first()
    db.add(
        Model(
            connection_id=connection.id,
            model_id="chat-1",
            capabilities_json={"tools": True},
        )
    )
    db.commit()

    page = client.get("/admin/extraction").text

    assert 'value="embed-1"' in page
    assert 'value="chat-1"' not in page


def test_a_model_that_lost_its_flag_is_named(db, user, embedding_model, client):
    """A setting that vanishes from a picker is one nobody can tell from a
    setting that was never made."""
    model = db.scalar(select(Model).where(Model.model_id == "embed-1"))
    model.capabilities_json = {}
    db.commit()

    assert "embed-1" in client.get("/admin/extraction").text


def test_a_deleted_model_means_no_embedder_rather_than_an_error(db, user, embedding_model):
    db.delete(db.scalar(select(Model).where(Model.model_id == "embed-1")))
    db.commit()

    assert indexing.embedder(db) is None
    assert indexing.enabled(db) is False
