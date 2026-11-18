"""Copying in the tree, emptying the trash, and reading part of a blob."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import DEFAULT_ORG, Chat, Node, NodeVersion, SessionGrant, User
from lembas.security.passwords import hash_password
from lembas.services import blobs, fs


@pytest.fixture
def alice(db) -> User:
    user = User(name="Alice", email="alice@shire.test", password_hash=hash_password("x" * 12))
    db.add(user)
    db.commit()
    fs.ensure_home(db, user)
    db.commit()
    return user


@pytest.fixture
def bob(db) -> User:
    user = User(name="Bob", email="bob@shire.test", password_hash=hash_password("x" * 12))
    db.add(user)
    db.commit()
    fs.ensure_home(db, user)
    db.commit()
    return user


def _paths(db, under: str) -> list[str]:
    return sorted(
        p for p in db.scalars(
            select(Node.path).where(Node.org_id == DEFAULT_ORG, Node.trashed_at.is_(None))
        ) if p.startswith(under)
    )


def test_a_file_is_copied_without_its_bytes_or_its_history(db, alice):
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/a.md", b"one")
    source = fs.write(v, "/home/alice/a.md", b"two")
    fs.mkdir(v, "/home/alice/out")
    copy = fs.cp(v, "/home/alice/a.md", "/home/alice/out")
    db.commit()
    assert copy.path == "/home/alice/out/a.md" and copy.id != source.id
    assert fs.read(v, copy.path) == b"two"
    assert copy.blob_hash == source.blob_hash and copy.mime == "text/markdown"
    assert copy.content_kind == "markdown" and copy.owner_id == alice.id
    # One version, made now; the source keeps its two.
    assert len(fs.versions(v, copy.path)) == 1
    assert len(fs.versions(v, source.path)) == 2
    # The same blob, counted for both nodes and both newest versions.
    assert blobs.refcount(db, source.blob_hash) == 4
    assert blobs.recount(db) == 0


def test_a_taken_name_is_numbered_never_overwritten(db, alice):
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/notes.md", b"mine")
    first = fs.cp(v, "/home/alice/notes.md", "/home/alice")
    second = fs.cp(v, "/home/alice/notes.md", "/home/alice")
    assert [first.name, second.name] == ["notes (2).md", "notes (3).md"]
    assert fs.read(v, "/home/alice/notes.md") == b"mine"
    fs.write(v, "/home/alice/Makefile", b"all:")
    assert fs.cp(v, "/home/alice/Makefile", "/home/alice").name == "Makefile (2)"
    fs.write(v, "/home/alice/.env", b"A=1")
    assert fs.cp(v, "/home/alice/.env", "/home/alice").name == ".env (2)"
    named = fs.cp(v, "/home/alice/notes.md", "/home/alice", "other.md")
    assert named.path == "/home/alice/other.md"
    with pytest.raises(fs.FsError):
        fs.cp(v, "/home/alice/notes.md", "/home/alice", "a/b")


def test_a_folder_is_copied_with_everything_below_it(db, alice):
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/d/one.md", b"1")
    fs.write(v, "/home/alice/d/sub/two.md", b"2")
    fs.mkdir(v, "/home/alice/d/empty")
    copy = fs.cp(v, "/home/alice/d", "/home/alice")
    db.commit()
    assert copy.kind == "dir" and copy.path == "/home/alice/d (2)"
    assert _paths(db, "/home/alice/d (2)") == [
        "/home/alice/d (2)", "/home/alice/d (2)/empty", "/home/alice/d (2)/one.md",
        "/home/alice/d (2)/sub", "/home/alice/d (2)/sub/two.md",
    ]
    assert fs.read(v, "/home/alice/d (2)/sub/two.md") == b"2"
    sub = fs.lookup(db, DEFAULT_ORG, "/home/alice/d (2)/sub")
    assert fs.lookup(db, DEFAULT_ORG, "/home/alice/d (2)/sub/two.md").parent_id == sub.id
    assert blobs.recount(db) == 0


def test_a_folder_is_not_copied_into_itself(db, alice):
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/d/sub/one.md", b"1")
    for inside in ("/home/alice/d", "/home/alice/d/sub"):
        with pytest.raises(fs.FsError, match="Access denied"):
            fs.cp(v, "/home/alice/d", inside)
    # A file beside itself is only a copy.
    assert fs.cp(v, "/home/alice/d/sub/one.md", "/home/alice/d/sub").name == "one (2).md"


def test_copying_needs_reading_there_and_writing_here(db, alice, bob):
    mine, theirs = fs.view(db, alice), fs.view(db, bob)
    fs.write(mine, "/home/alice/secret.md", b"s")
    fs.write(mine, "/home/alice/shared/open.md", b"o")
    db.commit()
    # What Bob cannot see is not there to copy...
    with pytest.raises(fs.FsError):
        fs.cp(theirs, "/home/alice/secret.md", "/home/bob")
    # ...and what he can only read he cannot copy into.
    fs.share(db, fs.lookup(db, DEFAULT_ORG, "/home/alice/shared"), "user", bob.id, "ro", by=alice)
    db.commit()
    theirs.refresh()
    fs.write(theirs, "/home/bob/b.md", b"b")
    with pytest.raises(fs.FsError, match="Read-only"):
        fs.cp(theirs, "/home/bob/b.md", "/home/alice/shared")
    copy = fs.cp(theirs, "/home/alice/shared/open.md", "/home/bob")
    assert copy.owner_id == bob.id and fs.read(theirs, "/home/bob/open.md") == b"o"
    with pytest.raises(fs.FsError, match="No such file"):
        fs.cp(theirs, "/home/bob/b.md", "/home/bob/nowhere")
    with pytest.raises(fs.FsError, match="Not a directory"):
        fs.cp(theirs, "/home/bob/b.md", "/home/bob/open.md")


def test_a_chats_copy_is_a_version_of_that_chat_and_skips_what_it_cannot_see(db, alice):
    fs.write(fs.view(db, alice), "/home/alice/d/seen.md", b"s")
    fs.write(fs.view(db, alice), "/home/alice/d/hidden/h.md", b"h")
    chat = Chat(user_id=alice.id, grants_customised=True)
    db.add(chat)
    db.flush()
    db.add(SessionGrant(chat_id=chat.id, path="/home/alice", level="stage"))
    db.add(SessionGrant(chat_id=chat.id, path="/home/alice/d/hidden", level="none"))
    db.commit()
    v = fs.view(db, alice, chat, message_id="m7")
    copy = fs.cp(v, "/home/alice/d", "/home/alice", "e")
    db.commit()
    assert _paths(db, "/home/alice/e") == ["/home/alice/e", "/home/alice/e/seen.md"]
    version = db.scalar(
        select(NodeVersion).where(
            NodeVersion.node_id == fs.lookup(db, DEFAULT_ORG, "/home/alice/e/seen.md").id
        )
    )
    assert (version.chat_id, version.message_id) == (chat.id, "m7")
    assert copy.path == "/home/alice/e"


def test_emptying_the_trash_lets_go_of_the_contents(db, alice):
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/d/one.md", b"only here")
    fs.write(v, "/home/alice/d/one.md", b"only here, again")
    fs.write(v, "/home/alice/keep.md", b"kept")
    fs.write(v, "/home/alice/d/early.md", b"went first")
    fs.rm(v, "/home/alice/d/early.md")
    db.commit()
    gone = fs.rm(v, "/home/alice/d")
    lone = fs.rm(v, fs.write(v, "/home/alice/lone.md", b"lone").path)
    db.commit()
    digest = db.scalar(select(NodeVersion.blob_hash).where(NodeVersion.node_id == lone.id))
    # One thing, by its id: the rest of the trash stays.
    assert fs.purge(v, lone.id) == 1
    db.commit()
    assert blobs.refcount(db, digest) == 0
    assert gone.id in {n.id for n in fs.trash(v)}
    # Everything: the folder, what went with it, and what went before it.
    assert fs.purge(v) == 3
    db.commit()
    assert fs.trash(v) == []
    assert db.scalar(select(Node).where(Node.trashed_at.is_not(None))) is None
    assert fs.read(v, "/home/alice/keep.md") == b"kept"
    assert blobs.recount(db) == 0
    with pytest.raises(fs.FsError):
        fs.purge(v, gone.id)


def test_nobody_empties_somebody_elses_trash(db, alice, bob):
    mine, theirs = fs.view(db, alice), fs.view(db, bob)
    node = fs.rm(mine, fs.write(mine, "/home/alice/a.md", b"a").path)
    db.commit()
    assert fs.purge(theirs) == 0
    with pytest.raises(fs.FsError):
        fs.purge(theirs, node.id)
    assert [n.id for n in fs.trash(mine)] == [node.id]


def test_a_range_of_a_blob_is_read_from_the_part_it_is_in(db, monkeypatch):
    monkeypatch.setattr(blobs, "PART_SIZE", 10)
    data = bytes(range(35))
    digest, size = blobs.put_stream(db, [data])
    assert size == 35
    for start, end in ((0, 34), (0, 0), (9, 10), (10, 19), (3, 27), (30, 34), (34, 34), (5, 99)):
        assert b"".join(blobs.iter_range(db, digest, start, end)) == data[start:end + 1]
    assert list(blobs.iter_range(db, digest, 5, 4)) == []
    assert blobs.head(db, digest, 4) == data[:4]
    assert blobs.head(db, "0" * 64, 4) == b""
