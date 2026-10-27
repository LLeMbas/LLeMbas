"""The file tree's access resolver and the view the model gets of it.

Two halves. The harness spec's `fs_access` conformance cases (filesystem.md, 3.0
draft) run against the real tree and the real resolver: the fixture is a copy
of the CLI's `harness/conformance/fs_access.json` until the vendored spec
carries it (`scripts/fetch_harness.py --update`), after which the vendored one
is read and the copy can go. Then the server's own rules that the spec leaves
to it: owners, shares, organisations, data group presets and floors, and the
instance admin who gets nothing for being one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from lembas.db.models import (
    DEFAULT_ORG,
    GRANTEE_GROUP,
    GRANTEE_ORG,
    GRANTEE_USER,
    KIND_DIR,
    KIND_FILE,
    KIND_MOUNT,
    ORG_OWNER,
    ROLE_ADMIN,
    RULE_FLOOR,
    RULE_PRESET,
    Chat,
    DataGroup,
    DataGroupRule,
    Group,
    SessionGrant,
    User,
)
from lembas.security import access
from lembas.security.passwords import hash_password
from lembas.services import fs, harness_spec, organisations

_VENDORED = harness_spec.conformance_dir() / "fs_access.json"
_COPY = Path(__file__).parent / "fixtures" / "harness" / "fs_access.json"
SPEC = json.loads((_VENDORED if _VENDORED.exists() else _COPY).read_text(encoding="utf-8"))


def _user(db, email="frodo@shire.test", **kwargs) -> User:
    user = User(name=email.split("@")[0], email=email, password_hash=hash_password("x" * 12))
    for key, value in kwargs.items():
        setattr(user, key, value)
    db.add(user)
    db.commit()
    return user


def _build(db, case: dict) -> fs.View:
    """The case's tree, organisation-owned, seen by an organisation owner (so
    the base layer is `commit` everywhere and only the session decides)."""
    user = _user(db, org_role=ORG_OWNER)
    mounts = {access.normalise(m) for m in case.get("mounts") or []}
    for entry in case["tree"]:
        path = access.normalise(entry)
        if fs.lookup(db, DEFAULT_ORG, path) is not None:
            continue
        kind = KIND_MOUNT if path in mounts else (KIND_DIR if entry.endswith("/") else KIND_FILE)
        node = fs._make_node(db, DEFAULT_ORG, path, kind, owner_id=None, by=None)
        if kind == KIND_FILE:
            fs._set_content(
                db, node, fs.blobs.put_bytes(db, b"text\n"), 5, by=None, chat_id=None,
                message_id=None,
            )
    group = DataGroup(name="case")
    db.add(group)
    db.flush()
    for path, level in (case.get("floor") or {}).items():
        db.add(DataGroupRule(data_group_id=group.id, path=path, level=level, mode=RULE_FLOOR))
    chat = Chat(
        user_id=user.id,
        data_group_id=group.id,
        grants_customised=True,
        start_dir=case.get("cwd") or "",
    )
    db.add(chat)
    db.flush()
    for path, level in (case.get("grants") or {}).items():
        db.add(SessionGrant(chat_id=chat.id, path=path, level=level))
    db.commit()
    return fs.view(db, user, chat)


def _run(v: fs.View, op: str, path: str) -> dict:
    try:
        if op == "level":
            resolved = v.path(path)
            return {"level": v.level(resolved), "passage": v.resolver.passage(resolved)}
        if op == "list":
            return {"entries": [entry.line() for entry in fs.list_dir(v, path)]}
        if op == "read":
            node = fs.stat(v, path)
            if node is None or node.kind != KIND_FILE:
                fs.list_dir(v, path)
            else:
                fs.read(v, path)
        elif op == "write":
            fs.write(v, path, b"new\n")
        elif op == "mkdir":
            fs.mkdir(v, path)
        elif op == "rm":
            fs.rm(v, path)
        elif op == "commit":
            fs.check_commit(v, path)
        else:  # pragma: no cover - a new op in the spec
            raise AssertionError(f"unknown op {op}")
    except fs.FsError as problem:
        return {"error": problem.message}
    return {"ok": True}


@pytest.mark.parametrize(
    "case", SPEC["cases"], ids=[f"{c['op']}-{c['path']}-{n}" for n, c in enumerate(SPEC["cases"])]
)
def test_conformance(db, case):
    v = _build(db, case)
    assert _run(v, case["op"], case["path"]) == case["expect"]


def test_the_spec_has_all_its_cases():
    assert len(SPEC["cases"]) >= 41


# --- Plan §6, worked through ---------------------------------------------------------
WORKED = {
    "tree": ["/AGENTS.md", "/Dir1/", "/Dir1/FILE1.txt", "/Dir1/Dir3/", "/Dir2/"],
    "grants": {"/AGENTS.md": "ro", "/Dir1/Dir3": "commit"},
}


def test_worked_example(db):
    v = _build(db, WORKED)
    assert access.at_least(v.level("/AGENTS.md"), "ro")
    assert v.level("/Dir1/Dir3") == "commit"
    assert v.level("/Dir1/Dir3/deeper/x.md") == "commit"
    assert v.level("/Dir1") == "none" and v.resolver.passage("/Dir1")
    assert v.level("/Dir1/FILE1.txt") == "none"
    assert v.level("/Dir2") == "none" and not v.resolver.passage("/Dir2")
    for op, path in (("list", "/Dir1"), ("read", "/Dir1/FILE1.txt"), ("write", "/Dir1/FILE1.txt")):
        assert "Access denied" in _run(v, op, path)["error"]
    # `/` is a passage too: listing it is refused rather than showing AGENTS.md
    # alone, so an unticked parent never lists (filesystem.md).
    assert "Access denied" in _run(v, "list", "/")["error"]
    assert _run(v, "read", "/AGENTS.md") == {"ok": True}
    assert _run(v, "write", "/Dir1/Dir3/new.md") == {"ok": True}
    assert _run(v, "list", "/Dir1/Dir3") == {"entries": ["new.md"]}


def test_the_access_function(db):
    v = _build(db, WORKED)
    assert access.access(db, v.user, v.chat, "/Dir1/Dir3/../FILE1.txt") == "none"
    assert access.access(db, v.user, v.chat, "/Dir1/Dir3") == "commit"
    # Outside a chat there is no session layer: the person's own right.
    assert access.access(db, v.user, None, "/Dir1/FILE1.txt") == "commit"


# --- Base: owners, shares, the organisation -------------------------------------------
@pytest.fixture
def people(db):
    alice = _user(db, "alice@shire.test")
    bob = _user(db, "bob@shire.test")
    owner = _user(db, "owner@shire.test", org_role=ORG_OWNER)
    for person in (alice, bob, owner):
        fs.ensure_home(db, person)
    db.commit()
    return alice, bob, owner


def test_homes_are_private(db, people):
    alice, bob, owner = people
    home = fs.home_of(db, alice).path
    assert home == "/home/alice"
    assert access.access(db, alice, None, home + "/notes.md") == "commit"
    assert access.access(db, bob, None, home) == "none"
    # Running the organisation does not open anybody's home.
    assert access.access(db, owner, None, home) == "none"
    assert access.access(db, owner, None, "/home") == "commit"


def test_an_instance_admin_gets_nothing_for_being_one(db, people):
    alice, _, _ = people
    admin = _user(db, "admin@shire.test", role=ROLE_ADMIN)
    assert access.access(db, admin, None, fs.home_of(db, alice).path) == "none"
    assert access.access(db, admin, None, "/home") == "none"


def test_skills_are_readable_by_the_organisation(db, people):
    alice, _, owner = people
    assert access.access(db, alice, None, "/skills/x/SKILL.md") == "ro"
    assert access.access(db, owner, None, "/skills/x/SKILL.md") == "commit"


def test_a_member_sees_their_home_by_its_full_path(db, people):
    alice, bob, _ = people
    v = fs.view(db, alice)
    assert v.resolver.passage("/") and v.resolver.passage("/home")
    with pytest.raises(fs.FsError, match="Access denied: /home is not open"):
        fs.list_dir(v, "/home")
    fs.write(v, "/home/alice/a.md", b"mine")
    assert [e.line() for e in fs.list_dir(v, "/home/alice")] == ["skills/", "a.md"]
    # Bob's home reads exactly like a name nobody took.
    for path in ("/home/bob/x.md", "/home/nobody/x.md"):
        with pytest.raises(fs.FsError) as hidden:
            fs.read(v, path)
        assert hidden.value.message == f"Access denied: {path} is not open to this session."


def test_shares_open_a_directory_at_their_level(db, people):
    alice, bob, _ = people
    va = fs.view(db, alice)
    fs.write(va, "/home/alice/Shared/plan.md", b"plan")
    shared = fs.lookup(db, DEFAULT_ORG, "/home/alice/Shared")
    fs.share(db, shared, GRANTEE_USER, bob.id, "ro", by=alice)
    db.commit()
    vb = fs.view(db, bob)
    assert fs.read(vb, "/home/alice/Shared/plan.md") == b"plan"
    with pytest.raises(fs.FsError, match="Read-only"):
        fs.write(vb, "/home/alice/Shared/plan.md", b"mine now")
    # A group share at a higher level on the same node: the highest wins.
    team = Group(name="Fellowship")
    team.users.append(bob)
    db.add(team)
    db.flush()
    fs.share(db, shared, GRANTEE_GROUP, team.id, "stage", by=alice)
    db.commit()
    vb = fs.view(db, bob)
    fs.write(vb, "/home/alice/Shared/plan.md", b"ours")
    assert fs.read(fs.view(db, alice), "/home/alice/Shared/plan.md") == b"ours"
    # The rest of Alice's home stays hers.
    assert access.access(db, bob, None, "/home/alice/a.md") == "none"


def test_an_organisation_share_reaches_every_member_but_no_other_org(db, people):
    alice, bob, _ = people
    va = fs.view(db, alice)
    fs.mkdir(va, "/home/alice/Public")
    fs.share(db, fs.lookup(db, DEFAULT_ORG, "/home/alice/Public"), GRANTEE_ORG, DEFAULT_ORG,
             "ro", by=alice)
    other = organisations.create(db, "Mordor")
    stranger = _user(db, "orc@mordor.test", org_id=other.id)
    db.commit()
    assert access.access(db, bob, None, "/home/alice/Public") == "ro"
    assert access.access(db, stranger, None, "/home/alice/Public") == "none"
    # Another organisation's tree is another tree: the same path is not this node.
    assert fs.lookup(db, other.id, "/home/alice/Public") is None


# --- Session: presets, customised grants, floors -------------------------------------
def test_a_group_with_no_preset_gives_a_chat_everything_its_person_has(db, people):
    alice, _, _ = people
    chat = Chat(user_id=alice.id)
    db.add(chat)
    db.commit()
    assert access.access(db, alice, chat, "/home/alice/x") == "commit"
    assert access.access(db, alice, chat, "/home/bob/x") == "none"


def test_a_preset_and_a_floor(db, people):
    alice, _, _ = people
    group = DataGroup(name="API")
    db.add(group)
    db.flush()
    db.add_all(
        [
            DataGroupRule(data_group_id=group.id, path="/home/alice", level="commit"),
            DataGroupRule(
                data_group_id=group.id, path="/home/alice/Private", level="none", mode=RULE_FLOOR
            ),
            DataGroupRule(
                data_group_id=group.id, path="/home/alice/Ref", level="ro", mode=RULE_FLOOR
            ),
        ]
    )
    chat = Chat(user_id=alice.id, data_group_id=group.id)
    db.add(chat)
    db.commit()
    assert access.access(db, alice, chat, "/home/alice/a.md") == "commit"
    assert access.access(db, alice, chat, "/home/alice/Ref/r.md") == "ro"
    assert access.access(db, alice, chat, "/home/alice/Private/p.md") == "none"
    assert access.access(db, alice, chat, "/skills/s") == "none"  # not in the preset

    # Customised: the chat's own rows, still under the floor.
    chat.grants_customised = True
    db.add_all(
        [
            SessionGrant(chat_id=chat.id, path="/", level="commit"),
            SessionGrant(chat_id=chat.id, path="/home/alice/Private", level="commit"),
        ]
    )
    db.commit()
    assert access.access(db, alice, chat, "/skills/s") == "ro"  # the share, not the grant
    assert access.access(db, alice, chat, "/home/alice/Private/p.md") == "none"
    assert access.access(db, alice, chat, "/home/alice/Ref/r.md") == "ro"
    assert {r.mode for r in db.scalars(select(DataGroupRule))} == {RULE_PRESET, RULE_FLOOR}


def test_a_boundary_hides_everything_outside_it(db, people):
    alice, _, _ = people
    chat = Chat(user_id=alice.id, boundary="/home/alice/Project", start_dir="/elsewhere")
    db.add(chat)
    db.commit()
    v = fs.view(db, alice, chat)
    assert v.cwd == "/home/alice/Project"  # a start outside the boundary reads as it
    fs.write(v, "notes.md", b"x")
    assert fs.lookup(db, DEFAULT_ORG, "/home/alice/Project/notes.md") is not None
    assert v.level("/home/alice/other.md") == "none"
    assert v.resolver.passage("/home/alice")
