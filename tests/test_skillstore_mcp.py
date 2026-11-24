"""The skills and notes tools, end to end against the server's own `/mcp`."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import DataGroup, StoreProposal, User
from lembas.security import api_tokens
from lembas.services.skillstore import mcp_tools, policy, repo, service

pytestmark = pytest.mark.slow

BODY = "# Deploy\n\nSteps.\n\n## Before\n\nCheck the disk.\n\n## After\n\nLook at the page."


@pytest.fixture(autouse=True)
def _rates():
    api_tokens.reset_rates()
    yield


class Device:
    """One MCP client: a token, and the session id `initialize` gave it."""

    def __init__(self, client, db, *, name="workstation-agent", user=None, scopes=("memory",)):
        self.client = client
        user = user or db.scalar(select(User))
        self.token, raw = api_tokens.create(db, user, name="dev", scopes=list(scopes))
        self.headers = {"Authorization": f"Bearer {raw}"}
        answer = self.rpc("initialize", {
            "protocolVersion": "2025-06-18", "clientInfo": {"name": name, "version": "1"},
        })
        self.instructions = answer.json()["result"]["instructions"]
        self.headers["Mcp-Session-Id"] = answer.headers["Mcp-Session-Id"]

    def rpc(self, method, params=None):
        body = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            body["params"] = params
        return self.client.post("/mcp", headers=self.headers, json=body)

    def call(self, tool, _meta=None, /, **arguments):
        params = {"name": tool, "arguments": arguments}
        if _meta:
            params["_meta"] = {"lembas": _meta}
        result = self.rpc("tools/call", params).json()
        assert "result" in result, result
        return result["result"]["content"][0]["text"], result["result"]["isError"]


def test_the_tools_are_listed_and_the_instructions_point_at_them(client, db, registered):
    device = Device(client, db)
    listed = device.rpc("tools/list").json()["result"]["tools"]
    names = {tool["name"] for tool in listed}
    assert names == {"memory"} | set(mcp_tools.NAMES) and len(mcp_tools.NAMES) == 17
    by_name = {tool["name"]: tool for tool in listed}
    assert by_name["skill_read"]["annotations"]["readOnlyHint"] is True
    assert by_name["skill_edit"]["annotations"]["readOnlyHint"] is False
    assert "reason" in by_name["skill_edit"]["inputSchema"]["required"]
    # The list is paid for by every agent: a line each.
    assert all(len(tool["description"]) <= 200 for tool in listed if tool["name"] != "memory")
    assert "skills_main()" in device.instructions and "notes_lookup(subject)" in device.instructions
    assert "memory tool" in device.instructions


def test_an_account_without_skills_gets_neither_the_tools_nor_the_pointer(
    client, db, registered, monkeypatch
):
    from lembas.security import permissions

    real = permissions.resolve

    def without(db_, user):
        return {**real(db_, user), "tools.skills": False}

    monkeypatch.setattr(permissions, "resolve", without)
    device = Device(client, db)
    names = {t["name"] for t in device.rpc("tools/list").json()["result"]["tools"]}
    assert not names & set(mcp_tools.NAMES)
    assert "skills_main" not in device.instructions
    answer = device.rpc("tools/call", {"name": "skills_main", "arguments": {}}).json()
    assert answer["error"]["code"] == -32602


def test_an_account_whose_models_may_not_write_gets_the_reading_tools_only(
    client, db, registered, monkeypatch
):
    from lembas.security import permissions

    real = permissions.resolve
    monkeypatch.setattr(
        permissions, "resolve", lambda d, u: {**real(d, u), "tools.skills.write": False}
    )
    device = Device(client, db)
    names = {t["name"] for t in device.rpc("tools/list").json()["result"]["tools"]}
    assert "skill_read" in names and "notes_lookup" in names
    assert not names & {"skill_add", "skill_edit", "note_add", "note_append", "note_change",
                        "note_confirm", "note_archive"}
    answer = device.rpc("tools/call", {"name": "note_add", "arguments": {}}).json()
    assert answer["error"]["code"] == -32602


def test_an_agent_finds_reads_and_changes_a_skill(client, db, registered):
    policy.save(db, {"skills_edit": policy.AUTO})
    device = Device(client, db)
    text, failed = device.call(
        "skill_add", name="deploy", description="Use when deploying the site.", body=BODY,
        category="servers", reason="written down after the last deploy", model="model-a",
    )
    assert not failed and text.startswith("Done.")
    main, _ = device.call("skills_main")
    assert main.startswith("# Skills and notes here") and "- deploy: Use when deploying" in main
    outline, _ = device.call("skill_outline", name="deploy")
    assert "- before (" in outline and "- after (" in outline
    section, _ = device.call("skill_read", name="deploy", section="before")
    assert section == "## Before\n\nCheck the disk."
    found, _ = device.call("skills_search", query="disk")
    assert "skill deploy: Use when deploying the site." in found and "in section before" in found
    text, failed = device.call(
        "skill_edit", name="deploy", section="before", text="Check the disk and the load.",
        reason="the load mattered last time",
    )
    assert not failed
    history, _ = device.call("skill_history", name="deploy")
    assert "skill deploy: edit" in history and "(workstation-agent)" in history
    assert "reason: the load mattered last time" in history and "[mcp]" in history
    assert "(workstation-agent, model-a)" in history  # the add said its model
    page, _ = device.call("skills_category", category="servers")
    assert page.startswith("# servers (1 skills)") and "- deploy:" in page
    text, failed = device.call("skill_read", name="nothing")
    assert failed and "no skill" in text


def test_under_approve_a_change_waits_and_the_agent_is_told(client, db, registered):
    device = Device(client, db)
    text, failed = device.call(
        "skill_add", name="deploy", description="Use when deploying.", body=BODY, reason="r"
    )
    assert not failed and "waits for a person's approval" in text
    assert repo.read("skills/deploy/SKILL.md") is None
    said, _ = device.call("skills_policy")
    assert "A change you make to a skill waits for a person's approval." in said
    assert "skill deploy: add — pending" in said and "through workstation-agent" in said
    admin = db.scalar(select(User))
    proposal = db.scalar(select(StoreProposal))
    assert (proposal.harness, proposal.account_id) == ("workstation-agent", admin.id)
    service.approve(db, admin, proposal.id, "good")
    said, _ = device.call("skills_policy")
    assert 'skill deploy: add — approved ("good")' in said
    read, failed = device.call("skill_read", name="deploy", section="after")
    assert not failed and "Look at the page." in read


def test_under_manual_an_agent_is_refused_through_this_door(client, db, registered):
    policy.save(db, {"skills_edit": policy.MANUAL, "notes_add": policy.MANUAL})
    device = Device(client, db)
    text, failed = device.call(
        "skill_add", name="deploy", description="Use when deploying.", body=BODY, reason="r"
    )
    assert failed and "only a person edits here" in text
    text, failed = device.call("note_add", title="T", body="b", subjects=["s"])
    assert failed and repo.files() == ["skills/main/SKILL.md"]
    assert db.scalar(select(StoreProposal)) is None


def test_notes_are_looked_up_read_and_written(client, db, registered):
    device = Device(client, db)
    text, failed = device.call(
        "note_add", title="Disk filled up in March",
        body="The log partition filled.\n\n## Cause\n\nA debug flag.\n\n## Fix\n\nRotate daily.",
        subjects=["disk", "logs", "server-a"], kind="incident", summary="Logs filled the disk",
    )
    assert not failed and "disk-filled-up-in-march" in text
    looked, _ = device.call("notes_lookup", subject="disk")
    assert "disk-filled-up-in-march [incident] — Logs filled the disk" in looked
    assert "confirmed" in looked and "by Frodo (workstation-agent)" in looked
    assert "A debug flag." not in looked  # headers only
    outline, _ = device.call("note_outline", name="disk-filled-up-in-march")
    assert "- cause (" in outline and "- fix (" in outline
    cause, _ = device.call("note_read", name="disk-filled-up-in-march", section="cause")
    assert cause == "## Cause\n\nA debug flag."
    text, failed = device.call("note_append", name="disk-filled-up-in-march", text="Again in May.")
    assert not failed
    text, failed = device.call(
        "note_change", name="disk-filled-up-in-march", section="fix", text="Rotate hourly.",
        reason="daily was not enough",
    )
    assert not failed and text.startswith("Done.")
    text, failed = device.call("note_confirm", name="disk-filled-up-in-march")
    assert not failed
    text, failed = device.call("note_archive", name="disk-filled-up-in-march", reason="moved")
    assert not failed
    looked, _ = device.call("notes_lookup", subject="disk")
    assert looked.startswith("No notes on that here.")
    looked, _ = device.call("notes_lookup", subject="disk", status="archived")
    assert "[incident, archived]" in looked
    whole = repo.read("notes/disk-filled-up-in-march.md")
    assert "Rotate hourly." in whole and "Again in May." in whole
    subjects = [commit.subject.split(": ")[1] for commit in
                repo.history("notes/disk-filled-up-in-march.md")]
    # (A confirmation in the second the note was written says nothing new.)
    assert [s for s in subjects if s != "confirm"] == ["archive", "change", "append", "add"]


def test_another_harness_changing_a_note_waits_for_its_author(client, db, registered):
    first = Device(client, db, name="workstation-agent")
    second = Device(client, db, name="laptop-agent")
    first.call("note_add", title="Plan", body="One.", subjects=["plan"])
    text, failed = second.call(
        "note_change", name="plan", section="all", text="Two.", reason="r"
    )
    assert not failed and "waits for a person's approval" in text
    assert "One." in repo.read("notes/plan.md")
    text, _ = second.call("note_append", name="plan", text="A thought.")
    assert text.startswith("Done.") and "by Frodo (laptop-agent)" in repo.read("notes/plan.md")


def test_a_call_reads_and_writes_the_data_group_of_the_session_it_names(
    client, db, registered, make_chat
):
    db.add(DataGroup(id="private-provider", name="Private provider"))
    db.commit()
    device = Device(client, db)
    from lembas.db.models import Chat

    chat = db.get(Chat, make_chat())
    chat.device_token_id = device.token.id
    chat.device_session_id = "sess-1"
    chat.data_group_id = "private-provider"
    db.commit()
    scope = {"scope": "sess-1"}
    text, failed = device.call("note_add", scope, title="Inside", body="b", subjects=["thing"])
    assert not failed
    assert "group: private-provider" in repo.read("notes/inside.md")
    assert "Session: sess-1" in repo.gitrun.run(
        ["log", "-1", "--format=%B"], cwd=repo.root()
    )[1]
    # Without the scope the caller is in the default group: the note is not there.
    looked, _ = device.call("notes_lookup", subject="thing")
    assert looked.startswith("No notes")
    text, failed = device.call("note_read", name="inside")
    assert failed and "no note" in text
    found, _ = device.call("skills_search", query="inside")
    assert found == "Nothing found."
    looked, _ = device.call("notes_lookup", scope, subject="thing")
    assert "inside [note]" in looked
    said, _ = device.call("skills_policy", scope)
    assert 'data group "private-provider"' in said
    # A session that is not this device's is refused, whoever's it is.
    other = Device(client, db, name="laptop-agent")
    answer = other.rpc("tools/call", {
        "name": "notes_lookup", "arguments": {}, "_meta": {"lembas": scope},
    }).json()
    assert answer["error"]["message"] == "not this device's session"


def test_a_token_without_the_scope_does_not_reach_the_store(client, db, registered):
    _, raw = api_tokens.create(db, db.scalar(select(User)), name="m", scopes=["models"])
    r = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {raw}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "skills_main", "arguments": {}}},
    )
    assert r.status_code == 403


def test_the_session_id_remembers_the_clients_name_and_nothing_else():
    session = mcp_tools.new_session("  workstation   agent  ")
    assert mcp_tools.client_of(session) == "workstation agent"
    assert mcp_tools.client_of("garbage") == "" and mcp_tools.client_of("a.!!!") == ""
