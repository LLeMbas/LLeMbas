"""Starting 2.x on a 1.x data directory: the old database is set aside, whole.

The 1.x database here is built by hand in SQL, in the shape a 1.7 instance has
for the tables that tell the two apart (accounts with a credit plan, chats,
messages, the credit ledger, the library), and populated. It is not the full
1.7 schema: what the upgrade reads of it is "tables, no `nodes`, a
`user_version` below 2000", and what it must do with it is move it untouched
-- which the tests check by reading the rows back from the moved file. Built
in SQL rather than from the models, because the models are 2.x now and a
fixture made from them would be a 2.x database with a 1.x name.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import inspect

from lembas.config import settings
from lembas.db import session as db_session

ONE_X = """
CREATE TABLE users (
    id VARCHAR(32) PRIMARY KEY, email VARCHAR(320) NOT NULL UNIQUE, name VARCHAR(120) NOT NULL,
    password_hash TEXT NOT NULL, role VARCHAR(16) NOT NULL, active BOOLEAN NOT NULL,
    settings_json JSON, plan_id VARCHAR(32), plan_renews_at DATETIME,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE TABLE chats (
    id VARCHAR(32) PRIMARY KEY, user_id VARCHAR(32) NOT NULL REFERENCES users(id),
    title VARCHAR(300), model_id VARCHAR(300), data_group_id VARCHAR(32),
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE TABLE messages (
    id VARCHAR(32) PRIMARY KEY, chat_id VARCHAR(32) NOT NULL REFERENCES chats(id),
    role VARCHAR(16) NOT NULL, content TEXT, usage_json JSON,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE TABLE credit_entries (
    id VARCHAR(32) PRIMARY KEY, user_id VARCHAR(32), kind VARCHAR(16), delta BIGINT,
    cost_micro BIGINT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
CREATE TABLE notes (
    id VARCHAR(32) PRIMARY KEY, user_id VARCHAR(32), title VARCHAR(300), body TEXT,
    created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
);
"""


def _populate(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(ONE_X)
        now = "2026-01-01 00:00:00"
        connection.execute(
            "INSERT INTO users VALUES ('u1','frodo@shire.test','Frodo','x','admin',1,'{}',"
            "'p1',NULL,?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO chats VALUES ('c1','u1','Ring','m','default',?,?)", (now, now)
        )
        for n in range(3):
            connection.execute(
                "INSERT INTO messages VALUES (?, 'c1', 'user', ?, '{}', ?, ?)",
                (f"m{n}", f"line {n}", now, now),
            )
        connection.execute(
            "INSERT INTO credit_entries VALUES ('e1','u1','usage',-5,5,?,?)", (now, now)
        )
        connection.execute("INSERT INTO notes VALUES ('n1','u1','Note','body',?,?)", (now, now))
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def instance(tmp_path: Path):
    """A data directory as 1.7 leaves it: the database, uploads, a key, backups."""
    data = tmp_path / "instance"
    data.mkdir()
    settings.data_dir = data
    db_session.reset_engine()
    _populate(data / "lembas.db")
    (data / "uploads").mkdir()
    (data / "uploads" / "doc.pdf").write_bytes(b"%PDF original")
    (data / "secret.key").write_text("key", encoding="utf-8")
    (data / "backups").mkdir()
    (data / "backups" / "lembas-1.db").write_bytes(b"backup")
    yield data
    db_session.reset_engine()


def _rows(path: Path, table: str) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


def _version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()


def test_a_1x_database_is_set_aside_and_2x_starts_empty(instance: Path):
    db_session.init_db()
    old = instance / "lembas.db.old"
    assert old.exists()
    assert _rows(old, "messages") == 3 and _rows(old, "credit_entries") == 1
    assert _version(old) == 0  # the old file is left exactly as it was

    new = instance / "lembas.db"
    assert _version(new) == db_session.SCHEMA_VERSION
    tables = set(inspect(db_session.get_engine()).get_table_names())
    assert {"nodes", "organisations", "invites", "shares"} <= tables
    assert _rows(new, "chats") == 0 and _rows(new, "users") == 0

    # What the old database needs, untouched.
    assert (instance / "uploads" / "doc.pdf").read_bytes() == b"%PDF original"
    assert (instance / "secret.key").read_text(encoding="utf-8") == "key"
    assert (instance / "backups" / "lembas-1.db").read_bytes() == b"backup"


def test_a_second_start_keeps_the_old_file(instance: Path):
    db_session.init_db()
    old = instance / "lembas.db.old"
    before = old.read_bytes()
    db_session.reset_engine()
    db_session.init_db()  # beta.1 -> beta.2: the 2.x database is not 1.x
    assert old.read_bytes() == before
    assert _version(instance / "lembas.db") == db_session.SCHEMA_VERSION


def test_a_1x_database_with_an_old_file_in_the_way_refuses_to_start(instance: Path):
    (instance / "lembas.db.old").write_bytes(b"somebody's earlier copy")
    with pytest.raises(db_session.OldDatabaseInTheWay) as refused:
        db_session.init_db()
    message = str(refused.value)
    assert "lembas.db.old" in message and str(instance / "lembas.db") in message
    # Nothing moved.
    assert (instance / "lembas.db.old").read_bytes() == b"somebody's earlier copy"
    assert _rows(instance / "lembas.db", "messages") == 3


def test_what_was_only_in_the_wal_survives_the_move(instance: Path):
    """A write still in `-wal` when the upgrade runs ends up in the moved file."""
    holder = sqlite3.connect(instance / "lembas.db")
    try:
        holder.execute("PRAGMA wal_autocheckpoint=0")
        holder.execute(
            "INSERT INTO messages VALUES ('late','c1','user','in the wal','{}',"
            "'2026-01-02 00:00:00','2026-01-02 00:00:00')"
        )
        holder.commit()
        assert (instance / "lembas.db-wal").stat().st_size > 0
        db_session.init_db()
    finally:
        holder.close()
    old = instance / "lembas.db.old"
    assert _rows(old, "messages") == 4
    assert not (instance / "lembas.db-wal").exists() or _version(instance / "lembas.db") == 2000


def test_an_empty_file_is_not_a_1x_database(tmp_path: Path):
    data = tmp_path / "fresh"
    data.mkdir()
    settings.data_dir = data
    db_session.reset_engine()
    (data / "lembas.db").touch()
    db_session.init_db()
    assert not (data / "lembas.db.old").exists()
    assert _version(data / "lembas.db") == db_session.SCHEMA_VERSION
    db_session.reset_engine()
