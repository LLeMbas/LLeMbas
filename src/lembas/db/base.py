"""Declarative base and column conventions shared by every model.

There is no Alembic in this project (SQLite only, schema created at startup).
That makes adding a column to an existing deployment a manual chore, so models
carry a few forward-looking columns that are not read yet -- see the notes on
``Message.parent_id`` and ``Message.content_parts_json``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming convention so constraints have stable, predictable names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def new_id() -> str:
    """Primary keys are UUID4 hex strings: URL-safe and non-enumerable."""
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    """Mixin: opaque string primary key generated in Python."""

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)


class Timestamps:
    """Mixin: creation and modification times, both timezone-aware UTC."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
