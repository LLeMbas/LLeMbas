"""The store's one door: the policy levels, who sees which note, and who a
change is recorded as."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import DataGroup, StoreProposal, StoreSkill, User
from lembas.services.skillstore import catalog, doc, policy, repo, service

pytestmark = pytest.mark.slow

BODY = "# Report\n\nSteps.\n\n## Collect\n\nCollect things.\n\n## Send\n\nSend it."


def _user(db, email="ann@example.org", name="Ann", role="user") -> User:
    user = User(email=email, name=name, password_hash="x", role=role)
    db.add(user)
    db.commit()
    return user


def _agent(db, user, harness="workstation-agent", group="default", **kwargs):
    # A token of its own for each harness, unless the test says which.
    kwargs.setdefault("token_id", f"token-of-{harness}")
    kwargs.setdefault("token_name", "dev")
    return policy.agent(db, user, harness=harness, model="model-a", session="s-1",
                        data_group=group, **kwargs)


def _add(db, who, name="weekly-report", **kwargs):
    return service.skill_add(
        db, who, name=name, description="Use when writing the weekly report.", body=BODY,
        reason="needed", **kwargs,
    )


# --- The three levels, for skills -------------------------------------------------
def test_approve_is_the_default_and_an_agents_change_waits(db):
    user = _user(db)
    out = _add(db, _agent(db, user))
    assert out.status == service.QUEUED and catalog.skill(db, "weekly-report") is None
    assert repo.read("skills/weekly-report/SKILL.md") is None
    proposal = db.scalar(select(StoreProposal))
    assert (proposal.account_id, proposal.harness, proposal.model, proposal.session) == (
        user.id, "workstation-agent", "model-a", "s-1",
    )


def test_manual_refuses_an_agent_and_takes_a_persons_hand(db):
    policy.save(db, {"skills_edit": policy.MANUAL})
    user = _user(db)
    with pytest.raises(service.Refused, match="only a person edits"):
        _add(db, _agent(db, user))
    assert db.scalar(select(StoreProposal)) is None
    admin = _user(db, "root@example.org", "Root", role="admin")
    assert _add(db, policy.person(db, admin)).status == service.APPLIED


def test_auto_applies_at_once(db):
    policy.save(db, {"skills_edit": policy.AUTO})
    out = _add(db, _agent(db, _user(db)))
    assert out.status == service.APPLIED and out.commit
    assert catalog.skill(db, "weekly-report").description.startswith("Use when")


def test_a_person_who_is_not_an_administrator_is_held_like_an_agent(db):
    member = _user(db)
    assert _add(db, policy.person(db, member)).status == service.QUEUED
    policy.save(db, {"skills_edit": policy.MANUAL})
    assert _add(db, policy.person(db, member), name="other").status == service.QUEUED


def test_an_account_whose_models_may_not_write_is_refused_at_every_level(db):
    from dataclasses import replace

    policy.save(db, {"skills_edit": policy.AUTO})
    who = replace(_agent(db, _user(db)), may_write=False)
    with pytest.raises(service.Refused, match="may not write"):
        _add(db, who)
    with pytest.raises(service.Refused):
        service.note_add(db, who, title="T", body="b", subjects=["s"])


def test_every_kind_of_skill_change_goes_through_the_level(db):
    admin = _user(db, "root@example.org", "Root", role="admin")
    _add(db, policy.person(db, admin))
    agent = _agent(db, _user(db))
    edit = service.skill_edit(
        db, agent, name="weekly-report", section="collect", text="Changed.", reason="r"
    )
    save = service.skill_save(
        db, agent, name="weekly-report",
        text=repo.read("skills/weekly-report/SKILL.md") + "\nMore.\n", reason="r",
    )
    remove = service.skill_remove(db, agent, name="weekly-report", reason="r")
    main = service.skill_edit(db, agent, name="main", section="changing", text="Do anything.",
                              reason="r")
    assert {edit.status, save.status, remove.status, main.status} == {service.QUEUED}
    assert "Collect things." in repo.read("skills/weekly-report/SKILL.md")
    assert "Do anything." not in repo.read("skills/main/SKILL.md")
    with pytest.raises(service.Refused, match="by hand"):
        service.skill_pin(db, agent, name="weekly-report", pinned=True)


def test_a_reason_is_required_of_an_agent(db):
    policy.save(db, {"skills_edit": policy.AUTO})
    with pytest.raises(service.Refused, match="reason"):
        service.skill_add(db, _agent(db, _user(db)), name="a", description="Use when a.",
                          body="Do.", reason="  ")


# --- The queue --------------------------------------------------------------------
def test_an_administrator_approves_and_the_commit_names_both(db):
    user = _user(db)
    admin = _user(db, "root@example.org", "Root", role="admin")
    queued = _add(db, _agent(db, user))
    assert service.pending(db, user) == [] and len(service.pending(db, admin)) == 1
    with pytest.raises(service.Refused, match="not yours"):
        service.approve(db, user, queued.proposal_id)
    diff, fits = service.proposal_diff(db.get(StoreProposal, queued.proposal_id))
    assert fits and "+# Report" in diff
    done = service.approve(db, admin, queued.proposal_id, "fine")
    assert done.status == service.APPLIED
    last = repo.history("skills/weekly-report/SKILL.md")[0]
    assert last.author == "Ann"
    assert last.trailers == {
        "Account": "Ann", "Account-Id": user.id, "Harness": "workstation-agent",
        "Model": "model-a", "Session": "s-1", "Origin": "mcp", "Reason": "needed",
        "Approved-By": "Root",
    }
    row = catalog.skill(db, "weekly-report")
    assert (row.changed_by, row.changed_harness, row.changed_model, row.changed_origin) == (
        "Ann", "workstation-agent", "model-a", "mcp",
    )
    with pytest.raises(service.Refused, match="no longer waiting"):
        service.approve(db, admin, queued.proposal_id)


def test_a_rejection_changes_nothing_and_keeps_the_word(db):
    admin = _user(db, "root@example.org", "Root", role="admin")
    queued = _add(db, _agent(db, _user(db)))
    service.reject(db, admin, queued.proposal_id, "not like this")
    proposal = db.get(StoreProposal, queued.proposal_id)
    assert (proposal.status, proposal.word, proposal.decided_name) == (
        "rejected", "not like this", "Root",
    )
    assert repo.read("skills/weekly-report/SKILL.md") is None


def test_a_section_change_is_made_again_on_a_file_that_moved_on(db):
    admin = _user(db, "root@example.org", "Root", role="admin")
    hand = policy.person(db, admin)
    _add(db, hand)
    agent = _agent(db, _user(db))
    queued = service.skill_edit(
        db, agent, name="weekly-report", section="collect", text="Collect everything.",
        reason="r",
    )
    whole = service.skill_save(
        db, agent, name="weekly-report",
        text=repo.read("skills/weekly-report/SKILL.md").replace("Steps.", "The steps."),
        reason="r",
    )
    service.skill_edit(db, hand, name="weekly-report", section="send", text="Send it today.")
    service.approve(db, admin, queued.proposal_id)
    text = repo.read("skills/weekly-report/SKILL.md")
    assert "Collect everything." in text and "Send it today." in text
    # A whole-file proposal from before cannot be: it would undo the two changes.
    with pytest.raises(service.Refused, match="no longer fits"):
        service.approve(db, admin, whole.proposal_id)
    assert db.get(StoreProposal, whole.proposal_id).status == "stale"
    assert "Send it today." in repo.read("skills/weekly-report/SKILL.md")


def test_an_approval_checks_the_skill_again(db):
    admin = _user(db, "root@example.org", "Root", role="admin")
    queued = _add(db, _agent(db, _user(db)))
    proposal = db.get(StoreProposal, queued.proposal_id)
    proposal.new_text = "no header at all"
    db.commit()
    with pytest.raises(service.Refused, match="frontmatter"):
        service.approve(db, admin, queued.proposal_id)


# --- Reading in sections ------------------------------------------------------------
def test_a_skill_is_read_by_outline_and_section_and_reads_are_counted(db):
    admin = _user(db, "root@example.org", "Root", role="admin")
    _add(db, policy.person(db, admin))
    outline = service.skill_outline(db, "weekly-report")
    assert "- report (" in outline and "  - collect (" in outline and "chars" in outline
    read = {"reader": admin.id}
    assert service.skill_read(db, "weekly-report", "send", **read) == "## Send\n\nSend it."
    assert service.skill_read(db, "Weekly Report", **read).startswith("# Skill: weekly-report")
    with pytest.raises(service.Refused, match="Sections: report, collect, send"):
        service.skill_read(db, "weekly-report", "nowhere")
    # A read counts once per account and day, however often it is made; a
    # read nobody is known for is not counted.
    assert catalog.reads(db) == {"weekly-report": 1}
    service.skill_read(db, "weekly-report")
    other = _user(db)
    service.skill_read(db, "weekly-report", reader=other.id)
    assert catalog.reads(db) == {"weekly-report": 2}


def test_the_main_skill_is_prose_and_a_generated_list(db):
    admin = _user(db, "root@example.org", "Root", role="admin")
    hand = policy.person(db, admin)
    for n in range(12):
        _add(db, hand, name=f"office-{n:02d}", category="office")
    _add(db, hand, name="deploy", category="servers")
    service.skill_pin(db, hand, name="office-07", pinned=True)
    text = service.main_text(db)
    assert text.startswith("# Skills and notes here")
    assert 'office (12 skills) → skills_category("office")' in text
    assert text.index("- office-07:") == text.index("- office-")  # the pinned one first
    assert "servers (1):\n  - deploy: Use when" in text
    assert "- main:" not in text
    # The list is not in the file: only the prose is stored and edited.
    assert "office-07" not in repo.read("skills/main/SKILL.md")
    service.skill_edit(db, hand, name="main", section="changing", text="Ask first.")
    assert "## Changing\n\nAsk first." in service.main_text(db)
    with pytest.raises(service.Refused):
        service.skill_remove(db, hand, name="main")
    compact = service.instructions(db, _agent(db, admin))
    assert "13 skills: office (12), servers (1)" in compact and "- office-07:" in compact
    assert "skills_main()" in compact and "notes_lookup(subject)" in compact
    assert len(compact) < 1800
    assert 'office / general' not in service.category_text(db, "office")
    assert "- office-03:" not in service.category_text(db, "office")
    assert "- office-03:" in service.category_text(db, "office", "general")


# --- Notes: who sees what -----------------------------------------------------------
def _groups(db, user):
    db.add(DataGroup(id="private-provider", name="Private provider"))
    db.add(DataGroup(id="anns-own", name="Ann's own", owner_id=user.id))
    db.commit()


def _note(db, who, title="Backup plan", **kwargs):
    kwargs.setdefault("subjects", ["backup", "servers"])
    return service.note_add(db, who, title=title, body="We back up nightly.\n\n## Detail\n\nx",
                            kind=kwargs.pop("kind", "plan"), summary="How backups run", **kwargs)


def test_a_note_is_stamped_with_its_writer_and_its_data_group(db):
    user = _user(db)
    _groups(db, user)
    out = _note(db, _agent(db, user, group="private-provider"))
    # Filed under its data group's own folder (the default group's at the top).
    assert out.status == service.APPLIED and out.name == "private-provider/backup-plan"
    meta = doc.fields(doc.split(repo.read("notes/private-provider/backup-plan.md"))[0])
    assert meta["group"] == "private-provider" and meta["shared"] == "false"
    assert (meta["author"], meta["author_id"], meta["harness"], meta["model"]) == (
        "Ann", user.id, "workstation-agent", "model-a",
    )
    assert meta["subjects"] == ["backup", "servers"] and meta["kind"] == "plan"
    assert meta["created"] == meta["confirmed"] and meta["status"] == "active"
    # A second note of the same title gets a name of its own.
    again = _note(db, _agent(db, user, group="private-provider"))
    assert again.name == "private-provider/backup-plan-2"
    assert meta["author_key"] == policy.token_key("token-of-workstation-agent")


def test_a_note_is_visible_in_its_data_group_and_nowhere_else(db):
    user = _user(db)
    _groups(db, user)
    inside = _agent(db, user, group="private-provider")
    outside = _agent(db, user, group="default")
    _note(db, inside)
    NAME = "private-provider/backup-plan"
    assert [n.ident for n in catalog.notes(db, inside)] == [NAME]
    # A note of the same title written outside gets the name it would have had
    # anyway: the hidden one does not show through it.
    assert _note(db, outside).name == "backup-plan"
    service.note_remove(db, policy.person(db, user), ident="backup-plan")
    assert catalog.notes(db, outside) == [] and catalog.note(db, outside, NAME) is None
    for call in (service.note_read, service.note_outline):
        with pytest.raises(service.Refused, match="no note"):
            call(db, outside, NAME)
    with pytest.raises(service.Refused, match="no note"):
        service.note_append(db, outside, ident=NAME, text="x")
    with pytest.raises(service.Refused, match="no note"):
        service.note_change(db, outside, ident=NAME, summary="x", reason="r")
    with pytest.raises(service.Refused, match="no note"):
        service.note_confirm(db, outside, ident=f"notes/{NAME}.md")

    async def found(who):
        return [hit["name"] for hit in await catalog.search(db, who, "backup")]

    import asyncio

    assert asyncio.run(found(inside)) == [NAME]
    assert asyncio.run(found(outside)) == []
    assert "plan (1)" in service.instructions(db, inside)
    assert "Notes here" not in service.instructions(db, outside)


def test_a_person_sees_the_groups_they_may_use_and_an_administrator_no_more(db):
    user = _user(db)
    other = _user(db, "bob@example.org", "Bob")
    admin = _user(db, "root@example.org", "Root", role="admin")
    _groups(db, user)
    _note(db, _agent(db, user, group="anns-own"), title="Mine")
    _note(db, _agent(db, user, group="private-provider"), title="Instance")
    assert {n.ident for n in catalog.notes(db, policy.person(db, user))} == {
        "anns-own/mine", "private-provider/instance",
    }
    assert {n.ident for n in catalog.notes(db, policy.person(db, other))} == {
        "private-provider/instance",
    }
    assert {n.ident for n in catalog.notes(db, policy.person(db, admin))} == {
        "private-provider/instance",
    }


def test_a_note_or_a_kind_is_shared_by_policy_and_only_by_a_persons_hand(db):
    user = _user(db)
    _groups(db, user)
    inside = _agent(db, user, group="private-provider")
    outside = _agent(db, user, group="default")
    # An agent cannot share what it writes, whatever it asks for.
    _note(db, inside, shared=True)
    assert catalog.note(db, outside, "private-provider/backup-plan") is None
    with pytest.raises(service.Refused, match="by hand"):
        service.note_share(db, inside, ident="private-provider/backup-plan", shared=True)
    service.note_share(db, policy.person(db, user), ident="private-provider/backup-plan",
                       shared=True)
    assert catalog.note(db, outside, "private-provider/backup-plan") is not None
    _note(db, inside, title="Outage", kind="incident")
    assert catalog.note(db, outside, "private-provider/outage") is None
    policy.save(db, {"shared_kinds": ["incident"]})
    assert catalog.note(db, outside, "private-provider/outage") is not None


def test_lookup_is_by_subject_kind_and_status(db):
    user = _user(db)
    who = _agent(db, user)
    _note(db, who)
    _note(db, who, title="Outage in spring", kind="incident", subjects=["outage", "servers"])
    assert [n.ident for n in catalog.notes(db, who, subject="backup")] == ["backup-plan"]
    assert len(catalog.notes(db, who, subject="servers")) == 2
    assert [n.ident for n in catalog.notes(db, who, kind="incident")] == ["outage-in-spring"]
    service.note_archive(db, who, ident="backup-plan", reason="replaced")
    assert [n.ident for n in catalog.notes(db, who)] == ["outage-in-spring"]
    assert [n.ident for n in catalog.notes(db, who, status="archived")] == ["backup-plan"]
    header = service.note_header(catalog.note(db, who, "backup-plan"))
    assert "[plan, archived]" in header and "confirmed" in header
    assert "by Ann (workstation-agent by its own name" in header


# --- Notes: the levels ------------------------------------------------------------
def test_adding_and_adding_to_are_free_and_changing_anothers_waits(db):
    ann, bob = _user(db), _user(db, "bob@example.org", "Bob")
    _note(db, _agent(db, ann))
    bobs = _agent(db, bob, harness="laptop-agent")
    added = service.note_append(db, bobs, ident="backup-plan", text="Also on Sundays.")
    assert added.status == service.APPLIED
    text = repo.read("notes/backup-plan.md")
    assert "by Bob (laptop-agent)\n\nAlso on Sundays." in text and "We back up nightly." in text
    changed = service.note_change(
        db, bobs, ident="backup-plan", section="detail", text="y", reason="wrong"
    )
    archived = service.note_archive(db, bobs, ident="backup-plan", reason="old")
    removed = service.note_remove(db, bobs, ident="backup-plan", reason="old")
    assert {changed.status, archived.status, removed.status} == {service.QUEUED}
    assert "## Detail\n\nx" in repo.read("notes/backup-plan.md")
    # The author's own agent changes its own note at once; another token of
    # the same account is somebody else -- whatever it calls itself.
    own = service.note_change(db, _agent(db, ann), ident="backup-plan", section="detail",
                              text="z", reason="fixed")
    assert own.status == service.APPLIED
    other_token = service.note_change(
        db, _agent(db, ann, harness="workstation-agent", token_id="another-token"),
        ident="backup-plan", section="detail", text="q", reason="r",
    )
    assert other_token.status == service.QUEUED
    # ... and the same token is the author under any name it gives itself.
    renamed = service.note_change(
        db, _agent(db, ann, harness="renamed", token_id="token-of-workstation-agent"),
        ident="backup-plan", section="detail", text="q2", reason="r",
    )
    assert renamed.status == service.APPLIED


def test_the_note_levels_are_settable_and_per_kind(db):
    ann, bob = _user(db), _user(db, "bob@example.org", "Bob")
    _note(db, _agent(db, ann))
    _note(db, _agent(db, ann), title="Outage", kind="incident")
    bobs = _agent(db, bob, harness="laptop-agent")
    policy.save(db, {"notes_change": policy.AUTO, "notes_kinds": {"incident": policy.MANUAL}})
    assert service.note_change(db, bobs, ident="backup-plan", summary="s", reason="r").status == (
        service.APPLIED
    )
    with pytest.raises(service.Refused, match="only a person"):
        service.note_change(db, bobs, ident="outage", summary="s", reason="r")
    policy.save(db, {"notes_add": policy.APPROVE})
    assert _note(db, bobs, title="Another").status == service.QUEUED
    assert service.note_confirm(db, bobs, ident="backup-plan").status == service.QUEUED
    policy.save(db, {"notes_add": policy.MANUAL})
    with pytest.raises(service.Refused):
        service.note_append(db, bobs, ident="backup-plan", text="x")


def test_confirming_sets_last_confirmed_and_who(db):
    ann, bob = _user(db), _user(db, "bob@example.org", "Bob")
    _note(db, _agent(db, ann))
    service.note_confirm(db, _agent(db, bob, harness="laptop-agent"), ident="backup-plan")
    row = catalog.note(db, _agent(db, ann), "backup-plan")
    assert row.confirmed_by == "Bob" and row.author == "Ann"
    assert repo.history("notes/backup-plan.md")[0].subject == "note backup-plan: confirm"


def test_a_note_change_is_decided_by_its_author_or_proposer_who_can_see_it(db):
    ann, bob = _user(db), _user(db, "bob@example.org", "Bob")
    carol = _user(db, "carol@example.org", "Carol")
    _note(db, _agent(db, ann))
    queued = service.note_change(db, _agent(db, bob, harness="laptop-agent"), ident="backup-plan",
                                 summary="Better", reason="r")
    assert [p.id for p in service.pending(db, ann)] == [queued.proposal_id]
    assert [p.id for p in service.pending(db, bob)] == [queued.proposal_id]
    assert service.pending(db, carol) == []
    service.approve(db, ann, queued.proposal_id)
    assert catalog.note(db, _agent(db, ann), "backup-plan").summary == "Better"


# --- The index is rebuilt from the files ------------------------------------------
def test_the_index_is_rebuilt_from_the_files(db):
    admin = _user(db, "root@example.org", "Root", role="admin")
    _add(db, policy.person(db, admin), category="office")
    _note(db, _agent(db, admin))
    db.query(StoreSkill).delete()
    db.commit()
    catalog.reindex(db)
    row = catalog.skill(db, "weekly-report")
    assert (row.category, row.changed_by, row.changed_origin) == ("office", "Root", "web")
    assert [s["id"] for s in row.sections_json] == ["report", "collect", "send"]
    # A file removed behind the index's back is forgotten at the next rebuild.
    (repo.root() / "notes" / "backup-plan.md").unlink()
    catalog.reindex(db)
    assert catalog.notes(db, policy.person(db, admin)) == []
