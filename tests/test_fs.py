"""The tree itself: writing and versions, renames, the trash, the layout."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import DEFAULT_ORG, ORG_OWNER, Chat, Node, NodeShare, NodeVersion, User
from lembas.security.passwords import hash_password
from lembas.services import blobs, fs, organisations


@pytest.fixture
def alice(db) -> User:
    user = User(name="Alice", email="alice@shire.test", password_hash=hash_password("x" * 12))
    db.add(user)
    db.commit()
    fs.ensure_home(db, user)
    db.commit()
    return user


def _paths(db) -> list[str]:
    return sorted(
        db.scalars(select(Node.path).where(Node.org_id == DEFAULT_ORG, Node.trashed_at.is_(None)))
    )


def test_the_layout_is_seeded(db, alice):
    assert _paths(db) == ["/home", "/home/alice", "/home/alice/skills", "/skills"]
    skills = fs.lookup(db, DEFAULT_ORG, "/skills")
    assert skills.owner_id is None
    assert db.scalar(select(NodeShare.level).where(NodeShare.node_id == skills.id)) == "ro"
    # Asking again changes nothing.
    fs.ensure_home(db, alice)
    fs.seed_org(db, DEFAULT_ORG)
    assert len(_paths(db)) == 4


def test_an_account_made_from_an_invite_gets_a_home(db):
    made = organisations.create_invite(db, DEFAULT_ORG, created_by=None)
    user = organisations.accept_invite(
        db, made.token, name="Sam", email="sam@shire.test", password="potatoes-po-ta-toes"
    )
    assert fs.home_of(db, user).path == "/home/sam"


def test_a_new_organisation_gets_its_own_tree(db):
    org = organisations.create(db, "Rivendell")
    db.commit()
    assert fs.lookup(db, org.id, "/skills") is not None
    assert fs.lookup(db, org.id, "/home") is not None


def test_homes_do_not_collide(db, alice):
    other = User(name="A", email="alice@elsewhere.test", password_hash=hash_password("x" * 12))
    db.add(other)
    db.flush()
    assert fs.ensure_home(db, other).path == "/home/alice-2"


def test_every_write_is_a_version_with_its_chat(db, alice):
    chat = Chat(user_id=alice.id)
    db.add(chat)
    db.commit()
    v = fs.view(db, alice, chat, message_id="m1")
    fs.write(v, "/home/alice/a.md", b"one")
    v.message_id = "m2"
    node = fs.write(v, "/home/alice/a.md", b"two")
    db.commit()
    assert fs.read(v, "/home/alice/a.md") == b"two"
    assert node.owner_id == alice.id and node.mime == "text/markdown" and node.size == 3
    found = fs.versions(v, "/home/alice/a.md")
    assert [x.message_id for x in found] == ["m2", "m1"]
    assert {x.chat_id for x in found} == {chat.id}
    assert blobs.read_bytes(db, found[1].blob_hash) == b"one"
    # Refcounts are what the rows say: "two" by the node and one version, "one" by a version.
    assert blobs.recount(db) == 0
    assert blobs.refcount(db, node.blob_hash) == 2


def test_writing_makes_missing_directories(db, alice):
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/a/b/c.txt", b"deep")
    assert fs.lookup(db, DEFAULT_ORG, "/home/alice/a/b").kind == "dir"
    assert fs.lookup(db, DEFAULT_ORG, "/home/alice/a/b").owner_id == alice.id


def test_a_rename_moves_the_subtree_and_only_it(db, alice):
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/50%_off/a.md", b"a")
    fs.write(v, "/home/alice/50%_off/sub/b.md", b"b")
    # Would match a careless `LIKE '/home/alice/50%_off%'`.
    fs.write(v, "/home/alice/50%_offer/c.md", b"c")
    fs.write(v, "/home/alice/50XYoff/d.md", b"d")
    fs.mv(v, "/home/alice/50%_off", "/home/alice/Sale")
    db.commit()
    paths = _paths(db)
    assert "/home/alice/Sale/a.md" in paths and "/home/alice/Sale/sub/b.md" in paths
    assert "/home/alice/50%_offer/c.md" in paths and "/home/alice/50XYoff/d.md" in paths
    assert not any(p.startswith("/home/alice/50%_off/") for p in paths)
    moved = fs.lookup(db, DEFAULT_ORG, "/home/alice/Sale")
    assert moved.name == "Sale"
    assert fs.lookup(db, DEFAULT_ORG, "/home/alice/Sale/sub").parent_id == moved.id


def test_a_move_into_another_directory(db, alice):
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/x/f.md", b"f")
    fs.mkdir(v, "/home/alice/y")
    fs.mv(v, "/home/alice/x", "/home/alice/y/x")
    assert fs.read(v, "/home/alice/y/x/f.md") == b"f"
    assert fs.lookup(db, DEFAULT_ORG, "/home/alice/y/x").parent_id == fs.lookup(
        db, DEFAULT_ORG, "/home/alice/y"
    ).id
    with pytest.raises(fs.FsError, match="Already exists"):
        fs.write(v, "/home/alice/z.md", b"z")
        fs.mv(v, "/home/alice/z.md", "/home/alice/y")
    with pytest.raises(fs.FsError):
        fs.mv(v, "/home/alice/y", "/home/alice/y/inside")


def test_the_trash_and_back(db, alice):
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/d/one.md", b"1")
    fs.write(v, "/home/alice/d/two.md", b"2")
    gone = fs.rm(v, "/home/alice/d")
    db.commit()
    assert not any(p.startswith("/home/alice/d") for p in _paths(db))
    with pytest.raises(fs.FsError, match="No such file"):
        fs.read(v, "/home/alice/d/one.md")
    assert [n.path for n in fs.trash(v)] == ["/home/alice/d"]
    # The name is free again while the old one waits in the trash...
    fs.write(v, "/home/alice/d", b"a file now")
    with pytest.raises(fs.FsError, match="Already exists"):
        fs.restore(v, gone.id)
    fs.rm(v, "/home/alice/d")
    # ...and once it is, the directory comes back with what went with it.
    fs.restore(v, gone.id)
    assert fs.read(v, "/home/alice/d/two.md") == b"2"
    # Contents of trashed files are still referenced, so not swept.
    assert blobs.recount(db) == 0


def test_another_persons_files_cannot_be_removed(db, alice):
    bob = User(name="Bob", email="bob@shire.test", password_hash=hash_password("x" * 12))
    db.add(bob)
    db.commit()
    fs.write(fs.view(db, alice), "/home/alice/a.md", b"a")
    with pytest.raises(fs.FsError, match="Access denied"):
        fs.rm(fs.view(db, bob), "/home/alice/a.md")


def test_the_organisation_runs_its_own_directories(db, alice):
    owner = User(
        name="O", email="o@shire.test", password_hash=hash_password("x" * 12), org_role=ORG_OWNER
    )
    db.add(owner)
    db.commit()
    v = fs.view(db, owner)
    fs.mkdir(v, "/Projects")
    fs.write(v, "/skills/review/SKILL.md", b"---\nname: review\n---\n")
    assert [e.line() for e in fs.list_dir(v, "/")] == ["Projects/", "home/", "skills/"]
    member = fs.view(db, alice)
    with pytest.raises(fs.FsError, match="Read-only"):
        fs.write(member, "/skills/mine/SKILL.md", b"x")
    assert fs.read(member, "/skills/review/SKILL.md").startswith(b"---")


def test_names_are_checked_and_normalised(db, alice):
    v = fs.view(db, alice)
    fs.write(v, "/home/alice/café.md", b"nfd")
    assert fs.read(v, "/home/alice/café.md") == b"nfd"
    with pytest.raises(fs.FsError):
        fs.write(v, "/home/alice/a\x00b", b"x")
    assert db.scalar(select(NodeVersion.id).limit(1)) is not None


def test_a_node_and_its_blob_commit_or_roll_back_together(db):
    from lembas.db.models import DEFAULT_ORG, Node

    digest = blobs.put_bytes(db, b"together")
    db.add(Node(org_id=DEFAULT_ORG, name="a", path="/a", kind="file", blob_hash=digest))
    db.rollback()
    assert not blobs.exists(db, digest)
