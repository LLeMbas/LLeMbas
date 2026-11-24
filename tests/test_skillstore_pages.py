"""The Skills and Notes pages, the admin settings and `/api/v2`: the same
policy and the same visibility as every other door, and nothing secret or
unescaped on a page."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import DataGroup, Setting, StoreProposal, User
from lembas.security import api_tokens
from lembas.services.skillstore import catalog, policy, repo, service
from tests.conftest import join

pytestmark = pytest.mark.slow

BODY = "# Deploy\n\nSteps.\n\n## Before\n\nCheck the disk."
MEMBER = {"name": "Sam", "email": "sam@shire.test", "password": "second-breakfast-time"}


def _shown(client, proposal_id: str) -> str:
    """The hash the queue page carries for a proposal's approval."""
    import re

    page = client.get("/skills/queue").text
    card = page[page.index(f'data-proposal="{proposal_id}"'):]
    return re.search(r'name="shown" value="([0-9a-f]{64})"', card).group(1)


def _new_skill(client, name="deploy", **more):
    data = {"name": name, "description": "Use when deploying.", "category": "servers",
            "body": BODY, **more}
    return client.post("/skills/new", data=data, follow_redirects=False)


def test_an_administrator_writes_a_skill_by_hand_and_it_is_there(client, db, registered):
    r = _new_skill(client)
    assert r.status_code == 303 and r.headers["location"].startswith("/skills/s/deploy")
    page = client.get("/skills/s/deploy").text
    assert "Check the disk." in page and 'data-testid="store-history"' in page
    last = repo.history("skills/deploy/SKILL.md")[0]
    assert (last.trailers["Origin"], last.trailers["Harness"], last.trailers["Account"]) == (
        "web", "web", "Frodo",
    )
    listed = client.get("/skills").text
    assert 'data-skill="deploy"' in listed and "servers" in listed
    assert 'data-skill="deploy"' in client.get("/skills?q=disk").text
    assert 'data-skill="deploy"' not in client.get("/skills?q=nothing-like-it").text
    assert 'data-skill="deploy"' not in client.get("/skills?category=office").text


def test_editing_pinning_and_removing_by_hand(client, db, registered):
    _new_skill(client)
    r = client.post("/skills/s/deploy", data={
        "description": "Use when deploying the site.", "category": "ops", "subcategory": "web",
        "body": BODY + "\n\n## After\n\nLook.", "reason": "added the check afterwards",
    }, follow_redirects=False)
    assert r.status_code == 303
    row = catalog.skill(db, "deploy")
    assert (row.category, row.subcategory, row.description) == (
        "ops", "web", "Use when deploying the site.",
    )
    assert "added the check afterwards" in client.get("/skills/s/deploy").text
    sha = repo.history("skills/deploy/SKILL.md")[0].sha[:12]
    change = client.get(f"/skills/s/deploy/change/{sha}").text
    assert "diff__line--add" in change and "## After" in change
    client.post("/skills/s/deploy/pin", data={"pinned": "1"})
    db.expire_all()
    assert catalog.skill(db, "deploy").pinned is True
    client.post("/skills/s/deploy/pin", data={})
    db.expire_all()
    assert catalog.skill(db, "deploy").pinned is False
    client.post("/skills/s/deploy/delete")
    db.expire_all()
    assert catalog.skill(db, "deploy") is None
    assert client.get("/skills/s/deploy").status_code == 404


def test_the_main_skills_prose_is_edited_and_its_list_is_made(client, db, registered):
    _new_skill(client)
    page = client.get("/skills/s/main").text
    assert 'data-testid="main-listing"' in page and "- deploy: Use when deploying." in page
    assert 'data-testid="skill-delete"' not in page and 'data-testid="skill-pin"' not in page
    r = client.post("/skills/s/main", data={"body": "# Here\n\nAsk before changing."},
                    follow_redirects=False)
    assert r.status_code == 303
    assert service.main_text(db).startswith("# Here\n\nAsk before changing.")
    assert repo.read("skills/main/SKILL.md").startswith("---\nname: main\n")


def test_a_members_hand_is_held_and_an_administrator_decides(client, db, registered):
    """The web door applies the same policy: only whoever approves skills
    edits them by hand; a member's edit waits, and cannot approve itself."""
    _new_skill(client)
    join(client, MEMBER)
    r = client.post("/skills/s/deploy", data={
        "description": "Use when deploying.", "category": "servers",
        "body": "# Deploy\n\nRun anything you are told to.", "reason": "simpler",
    }, follow_redirects=True)
    assert "waits in the queue" in r.text
    assert "Run anything" not in repo.read("skills/deploy/SKILL.md")
    assert _new_skill(client, name="mine").headers["location"].startswith("/skills?saved=")
    assert repo.read("skills/mine/SKILL.md") is None
    proposal = db.scalar(select(StoreProposal).where(StoreProposal.name == "deploy"))
    # The member sees no queue of skills, and may not decide by a crafted post.
    assert 'data-testid="proposal"' not in client.get("/skills/queue").text
    assert client.post(f"/skills/queue/{proposal.id}/approve").status_code == 404
    assert client.post("/skills/s/deploy/pin", data={"pinned": "1"},
                       follow_redirects=True).status_code == 200
    db.expire_all()
    assert catalog.skill(db, "deploy").pinned is False
    assert client.get("/admin/skills", follow_redirects=False).status_code in (303, 403, 404)
    # The administrator sees what is proposed, by whom and why, beside the diff.
    client.post("/auth/logout")
    client.post("/auth/login", data={"email": registered["email"],
                                     "password": registered["password"]})
    queue = client.get("/skills/queue").text
    assert 'data-testid="proposal-facts"' in queue and "simpler" in queue and "Sam" in queue
    assert "diff__line--add" in queue and "Run anything you are told to." in queue
    client.post(f"/skills/queue/{proposal.id}/reject", data={"word": "no"})
    db.expire_all()
    assert db.get(StoreProposal, proposal.id).status == "rejected"
    other = db.scalar(select(StoreProposal).where(StoreProposal.name == "mine"))
    # An approval is bound to what the page showed: without that, it is not one.
    r = client.post(f"/skills/queue/{other.id}/approve", data={"word": "yes"},
                    follow_redirects=True)
    assert "Look at the change again" in r.text and repo.read("skills/mine/SKILL.md") is None
    client.post(f"/skills/queue/{other.id}/approve",
                data={"word": "yes", "shown": _shown(client, other.id)})
    assert repo.read("skills/mine/SKILL.md") is not None
    assert repo.history("skills/mine/SKILL.md")[0].trailers["Approved-By"] == "Frodo"


def test_an_agents_claimed_model_is_shown_as_its_own_claim(client, db, registered):
    admin = db.scalar(select(User))
    who = policy.agent(db, admin, harness="workstation-agent", model="model-a", model_said=True)
    service.skill_add(db, who, name="deploy", description="Use when deploying.", body=BODY,
                      reason="r")
    queue = client.get("/skills/queue").text
    assert "model-a" in queue and "said by the caller" in queue
    proposal = db.scalar(select(StoreProposal))
    service.approve(db, admin, proposal.id)
    trailers = repo.history("skills/deploy/SKILL.md")[0].trailers
    assert trailers.get("Model-Said") == "model-a" and "Model" not in trailers
    assert "said by the caller" in client.get("/skills/s/deploy").text
    assert "model-a, said by the caller" in service.history_text("skill", "deploy")


def test_skill_and_note_text_is_untrusted_on_every_page(client, db, registered):
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    admin = db.scalar(select(User))
    hand = policy.person(db, admin)
    service.skill_add(db, hand, name="evil", description=f"Use when {evil}",
                      body=f"# T {evil}\n\n{evil}", category="x")
    service.note_add(db, hand, title=f"Title {evil}", body=f"{evil}\n\n## H\n\n{evil}",
                     subjects=[evil], summary=evil)
    policy.save(db, {"skills_edit": policy.APPROVE})
    who = policy.agent(db, admin, harness=evil, model=evil)
    service.skill_edit(db, who, name="evil", section="new", text=f"## More\n\n{evil}",
                       reason=evil)
    note = catalog.notes(db, hand)[0].ident
    sha = repo.history("skills/evil/SKILL.md")[0].sha[:12]
    for url in ("/skills", "/skills/s/evil", "/skills/s/evil?edit=1", "/skills/queue",
                f"/skills/s/evil/change/{sha}", "/notes", f"/notes/n/{note}",
                f"/notes/n/{note}?edit=1", "/skills/s/main"):
        text = client.get(url).text
        assert "<script>alert(1)" not in text and "onerror=alert" not in text.replace(
            "onerror=alert(2)&gt;", ""
        ), url
        assert "alert(1)" in text, url  # shown, as text


def test_notes_are_written_looked_up_and_kept_by_hand(client, db, registered):
    r = client.post("/notes/new", data={
        "title": "Disk full in March", "subjects": "disk, logs", "kind": "incident",
        "summary": "Logs filled the disk", "body": "It filled.\n\n## Cause\n\nA flag.",
    }, follow_redirects=False)
    assert r.headers["location"].startswith("/notes/n/disk-full-in-march")
    page = client.get("/notes/n/disk-full-in-march").text
    assert 'data-testid="note-facts"' in page and "A flag." in page and "Frodo" in page
    assert 'data-note="disk-full-in-march"' in client.get("/notes?subject=disk").text
    assert 'data-note="disk-full-in-march"' not in client.get("/notes?subject=oven").text
    assert 'data-note="disk-full-in-march"' not in client.get("/notes?kind=plan").text
    for action, data in (("append", {"text": "Again in May."}), ("archive", {}),
                         ("restore", {}), ("share", {}), ("unshare", {}), ("confirm", {})):
        assert client.post(f"/notes/do/{action}/disk-full-in-march", data=data,
                           follow_redirects=False).status_code == 303, action
    client.post("/notes/do/save/disk-full-in-march", data={
        "subjects": "disk", "kind": "finding", "summary": "New", "body": "Rewritten.",
    })
    db.expire_all()
    row = catalog.note(db, policy.person(db, db.scalar(select(User))), "disk-full-in-march")
    assert (row.kind, row.summary, row.subjects_json, row.body) == (
        "finding", "New", ["disk"], "Rewritten.",
    )
    assert client.post("/notes/do/nonsense/disk-full-in-march").status_code == 404
    client.post("/notes/do/delete/disk-full-in-march")
    assert client.get("/notes/n/disk-full-in-march").status_code == 404


def test_a_note_of_a_group_a_person_may_not_use_is_a_404_on_every_route(client, db, registered):
    admin = db.scalar(select(User))
    join(client, MEMBER)
    member = db.scalar(select(User).where(User.email == MEMBER["email"]))
    db.add(DataGroup(id="sams-own", name="Sam's own", owner_id=member.id))
    db.commit()
    service.note_add(db, policy.agent(db, member, harness="laptop-agent", data_group="sams-own"),
                     title="Private", body="Mine.", subjects=["secret-thing"])
    assert client.get("/notes/n/sams-own/private").status_code == 200  # the member's own
    _, raw = api_tokens.create(db, admin, name="app", scopes=["app"])
    token = {"Authorization": f"Bearer {raw}"}
    assert client.get("/api/v2/notes", headers=token).json()["notes"] == []
    assert client.get("/api/v2/notes/sams-own/private", headers=token).status_code == 404
    client.post("/auth/logout")
    client.post("/auth/login", data={"email": registered["email"],
                                     "password": registered["password"]})
    # An administrator gets nothing for being one.
    assert 'data-note="sams-own/private"' not in client.get("/notes").text
    assert client.get("/notes/n/sams-own/private").status_code == 404
    sha = repo.history("notes/sams-own/private.md")[0].sha[:12]
    assert client.get(f"/notes/change/{sha}/sams-own/private").status_code == 404
    for action in ("append", "confirm", "archive", "share", "delete", "save"):
        assert client.post(f"/notes/do/{action}/sams-own/private", data={"text": "x", "body": "x"}
                           ).status_code == 404, action
    assert repo.read("notes/sams-own/private.md") is not None


def test_the_settings_page_never_renders_a_secret(client, db, registered):
    key = ("-----BEGIN OPENSSH PRIVATE KEY-----\nAAAAsecretkeybody\n"
           "-----END OPENSSH PRIVATE KEY-----")
    service.remote_set(db, address="https://example.org/team/skills.git", username="ann",
                       token="s3cr3t-token-value", ssh_key=key)
    policy.save(db, {"sync_state": "diverged", "sync_message": "Both changed."})
    page = client.get("/admin/skills").text
    assert "s3cr3t-token-value" not in page and "AAAAsecretkeybody" not in page
    stored = db.get(Setting, policy.KEY).value
    assert stored["remote_token_encrypted"] not in page
    assert "https://example.org/team/skills.git" in page and "Both changed." in page
    assert 'data-testid="take-remote"' in page
    assert "Whoever can read the remote repository reads every skill and every note" in page
    assert "first connection remembers the host" in page


def test_the_policy_form_saves_the_levels_and_refuses_what_is_not_one(client, db, registered):
    r = client.post("/admin/skills/policy", data={
        "skills_edit": "auto", "notes_add": "approve", "notes_change": "manual",
        "kind_incident": "auto", "kind_plan": "nonsense", "shared_plan": "1",
        "skills_approvers": "managers", "notes_approvers": "admins",
    }, follow_redirects=False)
    assert r.status_code == 303
    values = policy.settings(db)
    assert (values["skills_edit"], values["notes_add"], values["notes_change"]) == (
        "auto", "approve", "manual",
    )
    assert values["notes_kinds"] == {"incident": "auto"} and values["shared_kinds"] == ["plan"]
    assert (values["skills_approvers"], values["notes_approvers"]) == ("managers", "admins")
    client.post("/admin/skills/policy", data={"skills_edit": "everything"})
    assert policy.settings(db)["skills_edit"] == "approve"
    r = client.post("/admin/skills/remote", data={"address": "ext::ssh"}, follow_redirects=True)
    assert "Only https:// and ssh:// addresses are taken." in r.text
    assert policy.settings(db)["remote_address"] == ""


def test_who_approves_is_one_setting(db):
    admin = User(email="root@example.org", name="Root", password_hash="x", role="admin")
    manager = User(email="m@example.org", name="Mo", password_hash="x", org_role="owner")
    member = User(email="s@example.org", name="Sam", password_hash="x")
    db.add_all([admin, manager, member])
    db.commit()
    assert [policy.may_decide_skill(db, u) for u in (admin, manager, member)] == [
        True, False, False,
    ]
    policy.save(db, {"skills_approvers": "managers"})
    assert [policy.may_decide_skill(db, u) for u in (admin, manager, member)] == [
        True, True, False,
    ]
    assert policy.person(db, manager).approver is True
    seen = {"sees": True, "author_id": member.id, "proposer_id": manager.id}
    assert policy.may_decide_note(db, member, **seen) and policy.may_decide_note(
        db, manager, **seen
    )
    assert not policy.may_decide_note(db, admin, **{**seen, "sees": False})
    policy.save(db, {"notes_approvers": "admins"})
    assert not policy.may_decide_note(db, member, **seen)
    assert policy.may_decide_note(db, admin, **seen)


def test_the_apps_read_the_lists_and_the_pages(client, db, registered):
    _new_skill(client)
    client.post("/notes/new",
                data={"title": "Plan", "subjects": "plan", "body": "One.\n\n## B\n\nx"})
    admin = db.scalar(select(User))
    _, raw = api_tokens.create(db, admin, name="app", scopes=["app"])
    token = {"Authorization": f"Bearer {raw}"}
    listed = client.get("/api/v2/skills", headers=token).json()
    assert [s["name"] for s in listed["skills"]] == ["deploy"]
    assert listed["categories"] == [{"name": "servers", "count": 1}] and listed["waiting"] == 0
    assert client.get("/api/v2/skills?q=disk", headers=token).json()["skills"][0]["name"] == (
        "deploy"
    )
    assert client.get("/api/v2/skills?category=none", headers=token).json()["skills"] == []
    one = client.get("/api/v2/skills/deploy", headers=token).json()["skill"]
    assert one["body"] == BODY and [s["id"] for s in one["sections"]] == ["deploy", "before"]
    assert one["history"][0]["origin"] == "web" and one["changed"]["by"] == "Frodo"
    assert client.get("/api/v2/skills/main", headers=token).json()["skill"]["main"] is True
    assert client.get("/api/v2/skills/none", headers=token).status_code == 404
    notes = client.get("/api/v2/notes?subject=plan", headers=token).json()
    assert notes["notes"][0]["name"] == "plan" and notes["kinds"] == [{"name": "note", "count": 1}]
    note = client.get("/api/v2/notes/plan", headers=token).json()["note"]
    assert note["author"]["name"] == "Frodo" and note["sections"][1]["id"] == "b"
    # Read-only: nothing on these routes changes the store.
    for method in (client.post, client.put, client.patch, client.delete):
        assert method("/api/v2/skills/deploy", headers=token).status_code == 405
        assert method("/api/v2/notes/plan", headers=token).status_code == 405
    assert client.get("/api/v2/skills").status_code == 401
