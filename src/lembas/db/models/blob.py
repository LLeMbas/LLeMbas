"""File contents, by hash, in a second SQLite file beside the main one.

`lembas-blobs.db` is attached to every connection of the application's engine
as the schema ``blobs`` (`db/session.py`), so one transaction can write a node
and its blob. Keeping the bytes out of `lembas.db` keeps the main database
small, lets a backup treat the two differently, and lets the store be swapped
later on its own.

A blob is the whole content's SHA-256, split into parts of about 4 MB so a
read never needs the whole file in memory. `refcount` counts the rows that
name the blob (`nodes`, `node_versions`, `organisations.logo_blob_hash`); a
sweep deletes blobs at zero that are older than an hour, so a write in flight
is never collected. `services/blobs.py` is the only writer.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, LargeBinary, String, Table

from lembas.db.base import Base, utcnow

BLOB_SCHEMA = "blobs"

blobs = Table(
    "blobs",
    Base.metadata,
    Column("hash", String(64), primary_key=True),
    Column("size", BigInteger, nullable=False, default=0),
    Column("parts", Integer, nullable=False, default=0),
    Column("refcount", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False, default=utcnow),
    schema=BLOB_SCHEMA,
)
# The sweep's question: which are unreferenced.
Index("ix_blobs_refcount", blobs.c.refcount)

blob_parts = Table(
    "blob_parts",
    Base.metadata,
    Column("hash", String(64), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("data", LargeBinary, nullable=False),
    schema=BLOB_SCHEMA,
)
