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


# --- Review fixes: the trash is judged by the node, not by where it lay ---------------------
def _shared_folder(db, alice, bob):
    """Alice's folder, shared with Bob to change (`stage`); one file in it
    shared with him to read only -- the narrower share wins for that file."""
    from lembas.db.models import GRANTEE_USER, LEVEL_RO, LEVEL_STAGE

    mine = fs.view(db, alice)
    fs.write(mine, "/home/alice/shared/notes.md", b"ours")
    secret = fs.write(mine, "/home/alice/shared/contract.md", b"read, do not touch")
    folder = fs.lookup(db, DEFAULT_ORG, "/home/alice/shared")
    fs.share(db, folder, GRANTEE_USER, bob.id, LEVEL_STAGE, by=alice)
    fs.share(db, secret, GRANTEE_USER, bob.id, LEVEL_RO, by=alice)
    db.commit()
    return secret


def test_a_read_only_share_still_holds_once_the_file_is_in_the_trash(db, alice, bob):
    secret = _shared_folder(db, alice, bob)
    theirs = fs.view(db, bob)
    with pytest.raises(fs.FsError):
        fs.rm(theirs, "/home/alice/shared/contract.md")  # read only while it is live
    fs.rm(fs.view(db, alice), "/home/alice/shared/contract.md")
    db.commit()
    theirs = fs.view(db, bob)
    # Not in his trash, not his to bring back, and above all not his to destroy.
    assert fs.trash(theirs) == []
    with pytest.raises(fs.FsError):
        fs.purge(theirs, secret.id)
    with pytest.raises(fs.FsError):
        fs.restore(theirs, secret.id)
    assert fs.purge(theirs) == 0
    db.commit()
    assert db.get(Node, secret.id) is not None
    assert [n.id for n in fs.trash(fs.view(db, alice))] == [secret.id]


def test_emptying_the_trash_leaves_what_other_people_put_there(db, alice, bob):
    """Bob can write in Alice's shared folder. What Alice trashed there is
    not in his trash, and his "empty" does not remove it."""
    _shared_folder(db, alice, bob)
    hers = fs.rm(fs.view(db, alice), "/home/alice/shared/notes.md")
    theirs = fs.view(db, bob)
    own = fs.rm(theirs, fs.write(theirs, "/home/bob/scratch.md", b"mine").path)
    db.commit()
    theirs = fs.view(db, bob)
    assert [n.id for n in fs.trash(theirs)] == [own.id]
    assert fs.purge(theirs) == 1
    db.commit()
    assert db.get(Node, hers.id) is not None and db.get(Node, own.id) is None


def test_what_a_grantee_trashed_is_theirs_to_restore_and_the_owners_to_destroy(db, alice, bob):
    _shared_folder(db, alice, bob)
    theirs = fs.view(db, bob)
    gone = fs.rm(theirs, "/home/alice/shared/notes.md")
    db.commit()
    theirs = fs.view(db, bob)
    # He put it there: it is in his trash and he can bring it back...
    assert [n.id for n in fs.trash(theirs)] == [gone.id]
    # ...but with `stage` he may not remove it for good, by name or by emptying.
    with pytest.raises(fs.FsError):
        fs.purge(theirs, gone.id)
    assert fs.purge(theirs) == 0
    db.commit()
    assert db.get(Node, gone.id) is not None
    # The owner of the home sees it too, and may.
    mine = fs.view(db, alice)
    assert gone.id in {n.id for n in fs.trash(mine)}
    assert fs.purge(mine, gone.id) == 1


def test_one_thing_below_that_is_not_theirs_refuses_the_whole_purge(db, alice, bob):
    """`commit` on a folder, read-only on one file inside: the folder cannot
    be removed for good by its grantee, as `rm` would not remove it."""
    from lembas.db.models import GRANTEE_USER, LEVEL_COMMIT, LEVEL_RO

    mine = fs.view(db, alice)
    fs.write(mine, "/home/alice/project/a.md", b"a")
    kept = fs.write(mine, "/home/alice/project/deep/keep.md", b"keep")
    folder = fs.lookup(db, DEFAULT_ORG, "/home/alice/project")
    fs.share(db, folder, GRANTEE_USER, bob.id, LEVEL_COMMIT, by=alice)
    fs.share(db, kept, GRANTEE_USER, bob.id, LEVEL_RO, by=alice)
    gone = fs.rm(mine, "/home/alice/project")
    db.commit()
    theirs = fs.view(db, bob)
    with pytest.raises(fs.FsError):
        fs.purge(theirs, gone.id)
    db.commit()
    assert db.get(Node, kept.id) is not None and db.get(Node, gone.id) is not None
    # Without the narrower share, `commit` on the node's own share is enough.
    from lembas.db.models import NodeShare

    db.execute(NodeShare.__table__.delete().where(NodeShare.node_id == kept.id))
    db.commit()
    assert fs.purge(fs.view(db, bob), gone.id) == 4


def test_moving_a_folder_leaves_an_older_trashed_one_of_the_same_name_where_it_was(db, alice):
    v = fs.view(db, alice)
    old_file = fs.write(v, "/home/alice/docs/old.md", b"old")
    old_dir = fs.rm(v, "/home/alice/docs")
    db.commit()
    # A new folder of the same name, with something trashed out of it.
    fs.write(v, "/home/alice/docs/new.md", b"new")
    left = fs.rm(v, fs.write(v, "/home/alice/docs/left.md", b"left").path)
    db.commit()
    fs.mv(v, "/home/alice/docs", "/home/alice/archive")
    db.commit()
    db.expire_all()
    # The older, unrelated one kept its place, and so did what was in it...
    assert db.get(Node, old_dir.id).path == "/home/alice/docs"
    assert db.get(Node, old_file.id).path == "/home/alice/docs/old.md"
    # ...what was trashed out of the moved folder followed it...
    assert db.get(Node, left.id).path == "/home/alice/archive/left.md"
    assert fs.read(v, "/home/alice/archive/new.md") == b"new"
    # ...and each is restored where it belongs.
    fs.restore(v, left.id)
    fs.restore(v, old_dir.id)
    db.commit()
    assert fs.read(v, "/home/alice/archive/left.md") == b"left"
    assert fs.read(v, "/home/alice/docs/old.md") == b"old"


def test_a_copy_whose_name_is_taken_meanwhile_takes_the_next_one(db, alice, monkeypatch):
    """Two copies of one file at one moment both find `a (2).md` free. The
    second is not an error from the database: it is called `a (3).md`."""
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/a.md", b"one")
    fs.write(v, "/home/alice/a (2).md", b"somebody got here first")
    db.commit()
    real, calls = fs._free_name, []

    def stale(view, directory, name, *, is_dir):
        calls.append(name)
        # The first look is the one made before the other copy landed.
        return "a (2).md" if len(calls) == 1 else real(view, directory, name, is_dir=is_dir)

    monkeypatch.setattr(fs, "_free_name", stale)
    copy = fs.cp(v, "/home/alice/a.md", "/home/alice")
    db.commit()
    assert copy.path == "/home/alice/a (3).md" and fs.read(v, copy.path) == b"one"
    assert fs.read(v, "/home/alice/a (2).md") == b"somebody got here first"


def test_one_copy_makes_only_so_many_nodes(db, alice, monkeypatch):
    monkeypatch.setattr(fs, "COPY_MAX", 3)
    v = fs.view(db, alice)
    for name in ("a", "b", "c"):
        fs.write(v, f"/home/alice/big/{name}.md", b"x")
    fs.write(v, "/home/alice/small/a.md", b"x")
    db.commit()
    before = len(_paths(db, "/home/alice"))
    with pytest.raises(fs.TooMany) as refused:
        fs.cp(v, "/home/alice/big", "/home/alice")
    assert "at most 3" in refused.value.message
    db.rollback()
    assert len(_paths(db, "/home/alice")) == before
    assert fs.cp(v, "/home/alice/small", "/home/alice").path == "/home/alice/small (2)"


def test_a_write_based_on_content_that_is_gone_is_refused_by_the_write_itself(db, alice):
    from lembas.db.session import session_scope

    v = fs.view(db, alice)
    opened = fs.write(v, "/home/alice/a.md", b"one").blob_hash
    db.commit()
    assert fs.write(v, "/home/alice/a.md", b"two", base=opened).blob_hash != opened
    db.commit()
    # Somebody else saves, in a session of their own, after this one looked.
    stale = fs.stat(v, "/home/alice/a.md").blob_hash
    with session_scope() as other:
        fs.write(fs.view(other, other.get(User, alice.id)), "/home/alice/a.md", b"theirs")
    with pytest.raises(fs.Changed):
        fs.write(v, "/home/alice/a.md", b"mine", base=stale)
    db.rollback()
    db.expire_all()
    assert fs.read(fs.view(db, alice), "/home/alice/a.md") == b"theirs"
