"""The chat's file tools over the tree (harness spec 3.0.0, `sessions: chat`).

`read`, `write`, `edit`, `list`, `glob`, `grep`, `mkdir`, `mv`, `rm`, `show`
and `python`, run as a model's call would run them: through `run_tool`, with
the chat's view of the organisation's tree, every refusal in `filesystem.md`'s
words. `edit`'s matching is the CLI's replacer cascade (`tools/replace.py`),
held to the spec's `edit` cases and the CLI's own unit cases.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from lembas.db.models import (
    DEFAULT_ORG,
    KIND_AGENT,
    KIND_FILE,
    ORG_OWNER,
    RULE_FLOOR,
    Chat,
    DataGroup,
    DataGroupRule,
    SessionGrant,
    User,
)
from lembas.security.passwords import hash_password
from lembas.services import context as context_service
from lembas.services import fs, harness_spec, pages
from lembas.services import tools as tools_service
from lembas.services.tools import files as files_tools
from lembas.services.tools.replace import ReplaceError, replace


def _setup(db, *, grants=None, floor=None, start="", kind="chat", parent=None):
    user = User(
        name="frodo", email="frodo@shire.test", password_hash=hash_password("x" * 12),
        org_role=ORG_OWNER,
    )
    db.add(user)
    db.flush()
    group = DataGroup(name="files")
    db.add(group)
    db.flush()
    for path, level in (floor or {}).items():
        db.add(DataGroupRule(data_group_id=group.id, path=path, level=level, mode=RULE_FLOOR))
    chat = Chat(
        user_id=user.id, data_group_id=group.id, grants_customised=grants is not None,
        start_dir=start, kind=kind, parent_chat_id=parent,
    )
    db.add(chat)
    db.flush()
    for path, level in (grants or {}).items():
        db.add(SessionGrant(chat_id=chat.id, path=path, level=level))
    db.commit()
    return user, chat


def _put(db, user, path: str, text: str) -> None:
    v = fs.view(db, user)
    fs.write(v, path, text.encode("utf-8"))
    db.commit()


class Session:
    """A chat's calls as the generation loop makes them: run, then inserted."""

    def __init__(self, user, chat):
        self.context = tools_service.ToolContext(
            owner_id=user.id, chat_id=chat.id, display=True, message_id=""
        )
        self.results = context_service.ResultContext(context_service.DbStore(chat.id))
        self.messages: list[dict] = []

    def call(self, name: str, **args) -> tuple[str, tools_service.ToolOutcome]:
        from lembas.services.generation import _inserted

        outcome = asyncio.run(tools_service.run_tool(self.context, name, json.dumps(args)))
        call = {"id": f"c{len(self.messages)}", "name": name}
        text = _inserted(self.results, call, dict(args), outcome, self.messages)
        self.messages.append({"role": "tool", "content": text})
        return text, outcome


def test_read_write_edit_through_the_chat(db):
    user, chat = _setup(db, start="/home")
    home = fs.ensure_home(db, user).path
    db.commit()
    s = Session(user, chat)
    text, outcome = s.call("write", path=f"{home}/a.py", content="x = 1\ny = 2\nz = 3\n")
    assert text.startswith(f"[#0001 {home}/a.py sha ") and text.endswith("] Created, 3 lines.")
    assert outcome.event["result_id"] == "#0001"
    # A file it wrote is one it has seen: it may be edited without a read.
    text, outcome = s.call("edit", path=f"{home}/a.py", old="y = 2", new="y = 20")
    assert text.split("\n", 1)[1] == "@@ -1,3 +1,3 @@\n x = 1\n-y = 2\n+y = 20\n z = 3"
    assert outcome.event["diff"].startswith(f"--- a{home}/a.py\n+++ b{home}/a.py\n@@")
    text, _ = s.call("read", path=f"{home}/a.py")
    assert text.startswith(f"[#0003 {home}/a.py lines 1-3 of 3 sha ")
    assert text.endswith("\n1: x = 1\n2: y = 20\n3: z = 3")
    text, _ = s.call("read", path=f"{home}/a.py", offset=2, limit=1)
    assert text.endswith("Unchanged since #0003, which already shows lines 1-3.")
    # Relative to the start directory, answered absolute.
    text, _ = s.call("read", path="nope.md")
    assert text == "[#0005]\nNo such file or directory: /home/nope.md"
    assert fs.read(fs.view(db, user), f"{home}/a.py") == b"x = 1\ny = 20\nz = 3\n"


def test_an_existing_file_must_be_read_first_and_unchanged_since(db):
    user, chat = _setup(db)
    home = fs.ensure_home(db, user).path
    _put(db, user, f"{home}/n.md", "one\n")
    s = Session(user, chat)
    _, outcome = s.call("write", path=f"{home}/n.md", content="two\n")
    assert outcome.event["status"] == "error"
    assert outcome.content == (
        f"{home}/n.md exists and has not been read in this session. Read it first."
    )
    _, outcome = s.call("edit", path=f"{home}/n.md", old="one", new="two")
    assert "has not been read" in outcome.content
    s.call("read", path=f"{home}/n.md")
    # Changed by somebody else since it was read.
    _put(db, user, f"{home}/n.md", "three\n")
    _, outcome = s.call("edit", path=f"{home}/n.md", old="three", new="four")
    assert outcome.content == (
        f"{home}/n.md changed since you last read it. Read it again before changing it."
    )
    s.call("read", path=f"{home}/n.md")
    text, outcome = s.call("write", path=f"{home}/n.md", content="four\n")
    assert outcome.event["status"] == "ok"
    assert "\n@@ -1,1 +1,1 @@\n-three\n+four" in text


def test_the_refusals_are_the_specs_words(db):
    user, chat = _setup(
        db, grants={"/AGENTS.md": "ro", "/Dir1/Dir3": "stage"},
    )
    for path in ("/AGENTS.md", "/Dir1/FILE1.txt", "/Dir1/Dir3/a.md", "/Dir2/b.md"):
        _put_org(db, user, path)
    s = Session(user, chat)
    assert s.call("read", path="/Dir1/FILE1.txt")[1].content == (
        "Access denied: /Dir1/FILE1.txt is not open to this session."
    )
    assert s.call("list", path="/")[1].content == "Access denied: / is not open to this session."
    # Read-only is said before "read it first": reading would not help.
    assert s.call("write", path="/AGENTS.md", content="x")[1].content == (
        "Read-only: /AGENTS.md can be read in this session but not changed."
    )
    assert s.call("mkdir", path="/Dir1/x")[1].content == (
        "Access denied: /Dir1/x is not open to this session."
    )
    assert s.call("read", path="/Dir1/Dir3/missing.md")[1].content == (
        "No such file or directory: /Dir1/Dir3/missing.md"
    )
    assert s.call("write", path="/Dir1/Dir3/new.md", content="hi\n")[1].event["status"] == "ok"
    # glob rooted at a passage is denied like a listing; at a granted one it
    # finds only what is granted.
    assert s.call("glob", pattern="**/*.md", path="/Dir1")[1].content.startswith("Access denied")
    found = s.call("glob", pattern="*.md", path="/Dir1/Dir3")[1].content.split("\n")
    assert sorted(found) == ["/Dir1/Dir3/a.md", "/Dir1/Dir3/new.md"]


def _put_org(db, user, path: str, text: str = "text\n") -> None:
    """An organisation-owned file (the owner's view, no chat)."""
    node = fs._make_node(db, DEFAULT_ORG, path, KIND_FILE, owner_id=None, by=None)
    data = text.encode()
    fs._set_content(db, node, fs.blobs.put_bytes(db, data), len(data), by=None, chat_id=None,
                    message_id=None)
    db.commit()


def test_list_glob_grep_and_the_repeated_listing_stub(db):
    user, chat = _setup(db, grants={"/": "stage", "/Ref": "ro", "/Notes/Private": "none"})
    for path, text in (
        ("/Notes/a.md", "alpha\nTODO: one\n"),
        ("/Notes/sub/b.py", "x = 1  # TODO two\n"),
        ("/Notes/Private/p.md", "TODO secret\n"),
        ("/Ref/r.md", "ref\n"),
    ):
        _put_org(db, user, path, text)
    s = Session(user, chat)
    text, _ = s.call("list", path="/")
    assert text == "[#0001]\nNotes/\n  sub/\n  a.md\nRef/ [read-only]\n  r.md"
    assert s.call("list", path="/")[0] == "[#0002] Same result as #0001."
    assert s.call("list", path="/", depth=1)[0] == "[#0003]\nNotes/\nRef/ [read-only]"
    text, _ = s.call("grep", pattern="TODO", path="/Notes")
    assert text == "[#0004]\n/Notes/a.md:2:TODO: one\n/Notes/sub/b.py:1:x = 1  # TODO two"
    assert s.call("grep", pattern="todo", path="/Notes", ignore_case=True, include="*.py")[0] == (
        "[#0005]\n/Notes/sub/b.py:1:x = 1  # TODO two"
    )
    assert "invalid regex" in s.call("grep", pattern="len(x", path="/")[0]
    assert "literal: true" in s.call("grep", pattern="len[(]y", path="/")[0]
    assert s.call("grep", pattern="len(x", literal=True, path="/")[1].content.startswith(
        "No matches."
    )
    assert s.call("glob", pattern="**/*.py")[1].content == "/Notes/sub/b.py"
    assert s.call("glob", pattern="Notes/*.md")[1].content == "/Notes/a.md"


def test_mkdir_mv_rm(db):
    user, chat = _setup(db)
    home = fs.ensure_home(db, user).path
    db.commit()
    s = Session(user, chat)
    assert s.call("mkdir", path=f"{home}/docs/drafts")[1].content == f"Made {home}/docs/drafts/."
    assert s.call("mkdir", path=f"{home}/docs")[1].content == f"{home}/docs/ already exists."
    s.call("write", path=f"{home}/docs/drafts/x.md", content="x\n")
    assert s.call("mv", **{"from": f"{home}/docs/drafts", "to": f"{home}/docs/final"})[
        1
    ].content == f"Moved {home}/docs/drafts to {home}/docs/final."
    assert fs.read(fs.view(db, user), f"{home}/docs/final/x.md") == b"x\n"
    assert s.call("rm", path=f"{home}/docs/final")[1].content.startswith(
        f"Moved the directory {home}/docs/final to the trash"
    )
    assert s.call("read", path=f"{home}/docs/final/x.md")[1].content == (
        f"No such file or directory: {home}/docs/final/x.md"
    )
    assert s.call("mv", **{"from": f"{home}/docs", "to": f"{home}/skills"})[1].content == (
        f"Already exists: {home}/skills"
    )


def test_show_names_the_version_and_never_carries_the_text(db):
    user, chat = _setup(db)
    home = fs.ensure_home(db, user).path
    _put(db, user, f"{home}/page.html", "<h1>Hi</h1>\n")
    s = Session(user, chat)
    _, outcome = s.call("show", path=f"{home}/page.html", line=1)
    assert outcome.content == f"Shown to the user: {home}/page.html at line 1."
    card = outcome.event["show"]
    assert card["kind"] == "html" and card["path"] == f"{home}/page.html"
    assert card["version_id"] and card["node_id"] and card["size"] == 12
    assert "<h1>" not in json.dumps(card)
    assert files_tools.show_kind("/a/b.ts") == ("code", "typescript")
    assert files_tools.show_kind("/a/Dockerfile") == ("code", "dockerfile")
    assert files_tools.show_kind("/a/notes.MD") == ("markdown", "")
    assert files_tools.show_kind("/a/data.csv") == ("text", "")
    # No screen (a helper's chat): the spec's sentence, word for word.
    s.context.display = False
    assert s.call("show", path=f"{home}/page.html")[1].content == (
        harness_spec.tools()["show"]["unavailable"]
    )


def test_python_needs_an_attached_page(db):
    """Never attached until a page announces itself (the web UI's runner):
    not offered, and a call answers the spec's `unavailable` text."""
    user, chat = _setup(db)
    s = Session(user, chat)
    _, outcome = s.call("python", code="print(1)")
    assert outcome.content == (
        "Python cannot run now: no open browser page is attached to this chat."
    )
    db.refresh(chat)
    offered = _offered(db, chat, user)
    assert "python" not in offered and "read" in offered and "recall" in offered


def test_python_runs_on_the_page_and_saves_through_write(db):
    user, chat = _setup(db)
    home = fs.ensure_home(db, user).path
    _put(db, user, f"{home}/in.csv", "a,b\n1,2\n")
    s = Session(user, chat)

    async def scenario():
        pages.announce(chat.id, "tab-1", {"python"})
        task = asyncio.create_task(
            tools_service.run_tool(
                s.context, "python",
                json.dumps({"code": "print(2)", "files": [f"{home}/in.csv"],
                            "save": [f"{home}/out.txt"]}),
            )
        )
        request = await pages.take(chat.id, "tab-1", timeout=2)
        assert request["files"][0]["path"] == f"{home}/in.csv"
        assert request["save"] == [f"{home}/out.txt"]
        import base64

        assert pages.answer(chat.id, "tab-1", request["call_id"], {
            "stdout": "2\n", "result": None,
            "files": [{"path": f"{home}/out.txt", "content_b64": base64.b64encode(b"ok").decode()},
                      {"path": f"{home}/sneaky.txt", "content_b64": ""}],
        })
        return await task

    try:
        outcome = asyncio.run(scenario())
    finally:
        pages.reset()
    assert outcome.content == f"2\nSaved: {home}/out.txt"
    assert fs.read(fs.view(db, user), f"{home}/out.txt") == b"ok"
    assert fs.lookup(db, DEFAULT_ORG, f"{home}/sneaky.txt") is None


def test_a_page_that_goes_away_ends_the_call(db):
    async def scenario():
        pages.announce("c1", "p1", {"python"})
        assert pages.attached("c1")
        waiting = asyncio.create_task(pages.run("c1", {"code": ""}, timeout=5))
        await asyncio.sleep(0)
        pages.withdraw("c1", "p1")
        with pytest.raises(pages.NotAttached):
            await waiting
        assert not pages.attached("c1")

    try:
        asyncio.run(scenario())
    finally:
        pages.reset()


def _offered(db, chat, user) -> set[str]:
    from lembas.db.models import Connection, Model

    connection = Connection(name="c", base_url="http://x.invalid/v1")
    db.add(connection)
    db.flush()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    chat.model_id = "m"
    chat.connection_id = connection.id
    db.commit()
    return {tool.name for tool in tools_service.resolve_tools(db, chat, user).defs}


def test_an_agent_chat_is_offered_none_of_them(db):
    user, chat = _setup(db, kind=KIND_AGENT)
    offered = _offered(db, chat, user)
    assert not offered & {"read", "write", "edit", "list", "glob", "grep", "mkdir", "mv", "rm",
                          "show", "python", "recall"}


def test_the_tools_are_the_specs(db):
    for name in ("read", "write", "edit", "list", "glob", "grep", "search", "mkdir", "mv", "rm",
                 "show", "python", "recall"):
        spec = harness_spec.tools()[name]
        assert spec["sessions"] in ("chat", "both"), name
        tool = tools_service.REGISTRY[name]
        variables = {**tools_service.SPEC_VARIABLES, **tools_service.LLEMBAS_VARS.get(name, {})}
        assert tool.description == harness_spec.fill(spec["description"], variables)
        assert tool.parameters == harness_spec.offered_parameters(name, variables)
        assert tool.risk == {"interact": "interact"}.get(spec["risk"], spec["risk"]), name


# --- edit's cascade -----------------------------------------------------------------------
EDIT = json.loads((harness_spec.conformance_dir() / "edit.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", EDIT["cases"], ids=[str(n) for n in range(len(EDIT["cases"]))])
def test_edit_conformance(case):
    try:
        new = replace(case["content"], case["old"], case["new"], case.get("all", False))
        got = {"content": new}
    except ReplaceError as exc:
        got = {"error": str(exc)}
    assert got == case["expect"]


def test_the_clis_own_replace_cases():
    """`tests/units.test.ts` and `permission-hardening.test.ts` in the CLI."""
    assert replace("a\nb\nc", "b", "B") == "a\nB\nc"
    assert "if z:" in replace("  if x:\n    y()\n", "if x:\n  y()", "if z:\n  y()")
    with pytest.raises(ReplaceError, match="multiple"):
        replace("x x", "x", "y")
    assert replace("x x", "x", "y", True) == "y y"
    # `$` in the new text is literal with `all` (the CLI's fix over upstream).
    assert replace("a x a", "a", "$$HOME $&", True) == "$$HOME $& x $$HOME $&"
    with pytest.raises(ReplaceError, match="cannot be empty"):
        replace("abc", "", "x")


def test_glob_patterns():
    rx = files_tools.glob_regex
    assert rx("**/*.ts").match("a/b/c.ts") and rx("**/*.ts").match("c.ts")
    assert not rx("src/*.ts").match("src/a/b.ts")
    assert rx("*.{ts,tsx}").match("x.tsx") and not rx("*.{ts,tsx}").match("x.js")
    assert rx("src/**/test_*.py").match("src/test_a.py")
    assert rx("[!a]*.md").match("b.md") and not rx("[!a]*.md").match("a.md")


def test_search_finds_passages_the_chat_may_read(db):
    """`search` over the index (`services/index`), after scoring filtered by the
    chat's view: `path:from-to` and the passage; a repeated one is a stub."""
    from lembas.services import index as index_service
    from lembas.services.index import pipeline, worker

    worker.clear()
    pipeline._TOKENIZES.clear()
    try:
        user, chat = _setup(db, grants={"/Open": "ro"})
        _put_org(db, user, "/Open/o.md", "the one ring, the copy this chat may read\n")
        _put_org(db, user, "/Closed/c.md", "the one ring, a copy kept from it\n")
        asyncio.run(index_service.run_once(everything=True))
        s = Session(user, chat)
        text, outcome = s.call("search", query="ring")
        assert outcome.event["hits"] == 1
        assert text == "[#0001]\n/Open/o.md:1-1\nthe one ring, the copy this chat may read"
        assert s.call("search", query="ring")[0] == "[#0002] Same result as #0001."
        assert s.call("search", query="ring", path="/Closed")[1].content == (
            "Access denied: /Closed is not open to this session."
        )
    finally:
        worker.clear()
