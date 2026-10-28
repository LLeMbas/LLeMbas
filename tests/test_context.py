"""Context hygiene (harness spec 3.0.0, `context.md`): ids, read headers, stubs,
the cap, `recall`, diffs and the compaction table -- insert-time only.

The conformance area `context` runs here against `services/context.py`, the
cases a copy of the CLI's `harness/conformance/context.json` until the vendored
spec carries it (`scripts/fetch_harness.py --update`), after which the vendored
one is read and the copy can go. Then the server's own half: results stored
per chat in `tool_results`, gone with the chat, and a stub that never points
at a result from an earlier reply, which a chat does not send again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from lembas.db.models import Chat, ToolResult, User
from lembas.security.passwords import hash_password
from lembas.services import compaction, harness_spec
from lembas.services import context as context_service
from lembas.services.tools.files import read_body

_VENDORED = harness_spec.conformance_dir() / "context.json"
_COPY = Path(__file__).parent / "fixtures" / "harness" / "context.json"
SPEC = json.loads((_VENDORED if _VENDORED.exists() else _COPY).read_text(encoding="utf-8"))


def run_case(case: dict) -> list[str]:
    """The CLI's runner (`tests/conformance.ts` `context`), step for step.

    `messages` is the request being built (what a stub may point at); `history`
    the results since the last compaction, which its table is drawn from.
    `reply` starts a request that carries none of them, as a chat's next reply
    does."""
    messages: list[dict] = []
    history: list[str] = []
    store = context_service.MemoryStore()
    cap = {**context_service.spec()["cap"], **(case.get("limits") or {})}
    rc = context_service.ResultContext(
        store, lambda: context_service.present_ids(messages), cap
    )
    out: list[str] = []
    for step in case["steps"]:
        if step["op"] == "reply":
            messages.clear()
            out.append("")
            continue
        if step["op"] == "compact":
            out.append(rc.compaction_table([r for r in store.rows if r.id in history]))
            messages.clear()
            history.clear()
            continue
        if step["op"] == "read":
            body, frm, to, of = read_body(step["content"], step.get("offset"), step.get("limit"))
            text = rc.insert(
                {"kind": "read", "path": step["path"], "key": step["path"],
                 "sha": context_service.sha256(step["content"]), "from": frm, "to": to,
                 "of": of, "body": body},
                step["id"],
            )
        elif step["op"] in ("edit", "write"):
            sha = context_service.sha256(step["after"])
            entry = (
                {"path": step["path"], "sha": sha,
                 "created": context_service.line_count(step["after"])}
                if "before" not in step
                else {"path": step["path"], "sha": sha,
                      "hunks": context_service.hunks(step["before"], step["after"])}
            )
            text = rc.insert({"kind": "edit", "files": [entry]}, step["id"])
        elif step["op"] == "recall":
            text = rc.insert(
                {"kind": "recall", "target": step["target"], "offset": step.get("offset"),
                 "limit": step.get("limit")},
                step["id"],
            )
        else:
            text = rc.insert(
                {"kind": "output", "tool": step["tool"], "args": step["args"],
                 "text": step["text"]},
                step["id"],
            )
        messages.append({"role": "tool", "content": text})
        history.append(step["id"])
        out.append(text)
    return out


@pytest.mark.parametrize("case", SPEC["cases"], ids=[str(n) for n in range(len(SPEC["cases"]))])
def test_conformance(case):
    assert run_case(case) == case["expect"]


def test_the_spec_has_its_cases():
    assert len(SPEC["cases"]) >= 12


def test_ids_are_hex_and_never_reused():
    rc = context_service.ResultContext(context_service.MemoryStore())
    ids = [rc.insert({"kind": "output", "tool": "bash", "text": str(n)}).split("]")[0]
           for n in range(17)]
    assert ids[0] == "[#0001" and ids[9] == "[#000a" and ids[16] == "[#0011"
    assert len(set(ids)) == 17


def test_a_stub_points_only_at_a_result_still_in_the_request():
    """A chat does not send an earlier reply's tool results again, so a read in
    the next reply is a full read, never a stub naming one that is gone."""
    store = context_service.MemoryStore()
    first: list[dict] = []
    rc = context_service.ResultContext(store, lambda: context_service.present_ids(first))
    read = {"kind": "read", "path": "a", "key": "/a", "sha": "0" * 64, "from": 1, "to": 1,
            "of": 1, "body": "1: x"}
    first.append({"role": "tool", "content": rc.insert(read)})
    assert "Unchanged since #0001" in rc.insert(read)
    second: list[dict] = []
    rc.present = lambda: context_service.present_ids(second)
    assert rc.insert(read) == "[#0003 a lines 1-1 of 1 sha 00000000]\n1: x"


def test_hunks_mark_a_last_line_without_a_newline():
    assert context_service.hunks("a\nb", "a\nc") == (
        "@@ -1,2 +1,2 @@\n a\n-b\n\\ No newline at end of file\n+c\n\\ No newline at end of file"
    )
    assert context_service.hunks("a\nb\n", "a\nb") == (
        "@@ -1,2 +1,2 @@\n a\n-b\n+b\n\\ No newline at end of file"
    )
    far = "\n".join(str(n) for n in range(1, 21)) + "\n"
    changed = far.replace("2\n", "two\n", 1).replace("19\n", "nineteen\n")
    assert context_service.hunks(far, changed).count("@@ ") == 2


def _chat(db) -> Chat:
    user = User(name="sam", email="sam@shire.test", password_hash=hash_password("x" * 12))
    db.add(user)
    db.flush()
    chat = Chat(user_id=user.id)
    db.add(chat)
    db.commit()
    return chat


def test_results_are_stored_with_the_chat_and_go_with_it(db):
    from lembas.services import chat as chat_service

    chat = _chat(db)
    rc = context_service.ResultContext(context_service.DbStore(chat.id, "m1"))
    long = "\n".join(str(n) for n in range(1, 1001))
    text = rc.insert({"kind": "output", "tool": "bash", "args": {}, "text": long})
    assert "[700 lines elided, full output #0001]" in text
    again = context_service.ResultContext(context_service.DbStore(chat.id))
    assert again.insert({"kind": "recall", "target": "0001", "offset": 500, "limit": 2}) == (
        "[#0002 recall #0001 lines 500-501 of 1000]\n500\n501"
    )
    assert again.insert({"kind": "recall", "target": "#ffff"}) == (
        "[#0003] No stored result #ffff in this session."
    )
    rows = db.scalars(select(ToolResult).where(ToolResult.chat_id == chat.id)).all()
    assert [r.rid for r in rows] == ["#0001", "#0002", "#0003"] and rows[0].message_id == "m1"
    chat_service.delete_chats(db, [db.get(Chat, chat.id)])
    db.commit()
    assert not db.scalars(select(ToolResult)).all()


def test_compaction_carries_the_reads_table(db):
    """`compaction.apply` adds the table of the reads in the turns it
    summarises (newest of each path, newest last) after the summary."""
    from lembas.db.models import ROLE_ASSISTANT, Message

    chat = _chat(db)
    upto = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="done", complete=True)
    db.add(upto)
    db.commit()
    rc = context_service.ResultContext(context_service.DbStore(chat.id, upto.id))
    for path, text in (("a.md", "alpha\n"), ("b.md", "beta\n"), ("a.md", "alpha2\n")):
        body, frm, to, of = read_body(text)
        rc.insert({"kind": "read", "path": path, "key": "/" + path,
                   "sha": context_service.sha256(text), "from": frm, "to": to, "of": of,
                   "body": body})
    chat = db.get(Chat, chat.id)
    compaction.apply(chat, summary="What happened.", upto=upto, db=db)
    db.commit()
    lines = chat.compact_summary.split("\n")
    assert lines[0] == "What happened."
    assert "## Files read before this summary" in chat.compact_summary
    assert lines[-2:] == [
        f"| b.md | 1-1 of 1 | {context_service.sha256('beta' + chr(10))[:8]} | #0002 |",
        f"| a.md | 1-1 of 1 | {context_service.sha256('alpha2' + chr(10))[:8]} | #0003 |",
    ]
    # A second compaction lists only what was read since the first.
    later = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="more", complete=True)
    db.add(later)
    db.commit()
    compaction.apply(chat, summary="Later.", upto=later, db=db)
    assert chat.compact_summary == "Later."


def test_compaction_counts_only_the_reads_of_turns_still_there(db):
    """A read made in a reply that was regenerated or deleted is not among the
    turns being summarised (`tool_results.message_id` has no foreign key), so
    it is not in the table; `recall` still shows it."""
    from lembas.db.models import ROLE_ASSISTANT, Message

    chat = _chat(db)
    gone = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="gone", complete=True)
    again = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="again", complete=True)
    db.add_all([gone, again])
    db.commit()

    def read(message_id, path, text):
        body, frm, to, of = read_body(text)
        context_service.ResultContext(context_service.DbStore(chat.id, message_id)).insert(
            {"kind": "read", "path": path, "key": "/" + path,
             "sha": context_service.sha256(text), "from": frm, "to": to, "of": of,
             "body": body}
        )

    read(gone.id, "deleted.md", "x\n")
    read(again.id, "first-attempt.md", "y\n")
    # `again` is produced a second time: the first attempt's reads are detached.
    context_service.detach(db, chat.id, again.id)
    db.commit()
    read(again.id, "kept.md", "z\n")
    db.delete(gone)
    db.commit()

    chat = db.get(Chat, chat.id)
    compaction.apply(chat, summary="S.", upto=db.get(Message, again.id), db=db)
    assert "| kept.md |" in chat.compact_summary
    assert "deleted.md" not in chat.compact_summary
    assert "first-attempt.md" not in chat.compact_summary
    rc = context_service.ResultContext(context_service.DbStore(chat.id))
    assert rc.insert({"kind": "recall", "target": "#0002"}).startswith("[#0004 recall #0002")

