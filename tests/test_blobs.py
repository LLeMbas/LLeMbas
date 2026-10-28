"""The blob store: contents by hash, in parts, in a second SQLite file."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from lembas.config import settings
from lembas.services import backup, blobs


def test_the_blob_store_is_its_own_file(db):
    blobs.put_bytes(db, b"hello")
    db.commit()
    assert settings.blobs_path.exists()
    with sqlite3.connect(settings.blobs_path) as raw:
        assert raw.execute("SELECT count(*) FROM blob_parts").fetchone()[0] == 1
        assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with sqlite3.connect(settings.db_path) as raw:
        names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "blobs" not in names and "blob_parts" not in names


def test_same_bytes_are_one_blob(db):
    first = blobs.put_bytes(db, b"the same")
    second = blobs.put_bytes(db, b"the same")
    assert first == second == hashlib.sha256(b"the same").hexdigest()
    assert db.execute(text("SELECT count(*) FROM blobs.blobs")).scalar() == 1


def test_large_content_is_cut_into_parts_and_streamed_back(db, monkeypatch):
    monkeypatch.setattr(blobs, "PART_SIZE", 10)
    data = bytes(range(256)) * 3
    digest, size = blobs.put_stream(db, (data[i : i + 7] for i in range(0, len(data), 7)))
    assert size == len(data) and digest == hashlib.sha256(data).hexdigest()
    parts = list(blobs.iter_parts(db, digest))
    assert len(parts) == -(-len(data) // 10) and all(len(p) <= 10 for p in parts)
    assert b"".join(parts) == data
    assert blobs.size(db, digest) == len(data)


def test_empty_content_is_a_blob_too(db):
    digest = blobs.put_bytes(db, b"")
    assert digest == blobs.EMPTY_HASH and blobs.read_bytes(db, digest) == b""


def test_refcounts_and_the_sweep(db):
    kept = blobs.put_bytes(db, b"kept")
    dropped = blobs.put_bytes(db, b"dropped")
    blobs.incref(db, kept)
    db.commit()
    # Fresh, unreferenced blobs survive: a write in flight is never collected.
    assert blobs.sweep(db) == 0
    later = datetime.now(UTC) + blobs.SWEEP_GRACE + timedelta(minutes=1)
    assert blobs.sweep(db, now=later) == 1
    assert blobs.exists(db, kept) and not blobs.exists(db, dropped)
    blobs.decref(db, kept)
    assert blobs.refcount(db, kept) == 0
    blobs.decref(db, kept)  # never below zero
    assert blobs.refcount(db, kept) == 0


def test_putting_an_unreferenced_blob_again_restarts_its_grace(db):
    digest = blobs.put_bytes(db, b"again")
    db.execute(
        text("UPDATE blobs.blobs SET created_at = :t"),
        {"t": datetime.now(UTC) - timedelta(days=2)},
    )
    blobs.put_bytes(db, b"again")
    assert blobs.sweep(db) == 0 and blobs.exists(db, digest)


def test_a_missing_blob_says_so(db):
    with pytest.raises(blobs.BlobMissing):
        blobs.incref(db, "0" * 64)
    with pytest.raises(blobs.BlobMissing):
        list(blobs.iter_parts(db, "0" * 64))


def test_a_backup_takes_the_blob_store_with_it(db):
    blobs.put_bytes(db, b"backed up")
    db.commit()
    archive = backup.take("blobs")
    names = {f["path"] for f in backup.manifest(archive)["files"]}
    assert {"lembas.db", "lembas-blobs.db"} <= names


