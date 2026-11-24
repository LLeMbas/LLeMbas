"""The store's git repository: local only, with a remote that fast-forwards,
with one that diverged, with hostile addresses and hostile paths. Every remote
here is a bare repository in a temporary folder; nothing touches the network."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from lembas.config import settings
from lembas.db.models import Setting, StoreProposal, User
from lembas.services import crypto
from lembas.services.skillstore import catalog, doc, gitrun, policy, repo, service

pytestmark = pytest.mark.slow

SKILL = "---\nname: {name}\ndescription: Use when {name}.\n---\n\n# {name}\n\nDo it.\n"


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Remote Person",
        "GIT_AUTHOR_EMAIL": "person@example.org",
        "GIT_COMMITTER_NAME": "Remote Person",
        "GIT_COMMITTER_EMAIL": "person@example.org",
    }
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.fixture
def local_remotes():
    settings.skills_local_remote = True
    yield
    settings.skills_local_remote = False


@pytest.fixture
def bare(tmp_path: Path, local_remotes) -> Path:
    path = tmp_path / "remote.git"
    path.mkdir()
    _git(path, "init", "--quiet", "--bare", "--initial-branch", "main")
    return path


def _clone(bare: Path, tmp_path: Path, name: str = "work") -> Path:
    work = tmp_path / name
    _git(tmp_path, "clone", "--quiet", str(bare), str(work))
    return work


def _push_file(work: Path, rel: str, text: str, message: str = "change") -> None:
    target = work / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(work, "add", "--", rel)
    _git(work, "commit", "--quiet", "-m", message)
    _git(work, "push", "--quiet", "origin", "HEAD:main")


# --- Local only -------------------------------------------------------------------
def test_the_store_works_with_no_setup_and_every_change_is_a_commit():
    assert repo.ensure() is True
    assert repo.ensure() is False
    assert repo.read("skills/main/SKILL.md").startswith("---\nname: main")
    before = repo.head()
    sha = repo.commit(
        {"skills/a/SKILL.md": SKILL.format(name="a")},
        subject="skill a: add",
        author="Ann",
        trailers={"Account": "Ann", "Harness": "test", "Reason": "because\nof this"},
    )
    assert sha and sha != before
    last = repo.history("skills/a/SKILL.md")[0]
    assert last.author == "Ann" and last.subject == "skill a: add"
    assert last.trailers == {"Account": "Ann", "Harness": "test", "Reason": "because of this"}
    # The same text again is not a change.
    assert repo.commit({"skills/a/SKILL.md": SKILL.format(name="a")}, subject="x", author="a") == ""
    assert repo.at_commit(sha, "skills/a/SKILL.md").startswith("---\nname: a")
    repo.commit({"skills/a/SKILL.md": None}, subject="skill a: remove", author="Ann")
    assert repo.read("skills/a/SKILL.md") is None
    assert len(repo.history("skills/a/SKILL.md")) == 2


def test_a_skills_other_files_and_header_fields_are_left_alone():
    repo.ensure()
    text = "---\nname: a\ndescription: Use when a.\nlicense: MIT\nmetadata:\n  x: 1\n---\n\nBody.\n"
    extra = repo.root() / "skills" / "a" / "helper.py"
    extra.parent.mkdir(parents=True)
    extra.write_text("print('kept')\n")
    repo.commit({"skills/a/SKILL.md": text}, subject="add", author="a")
    changed = doc.set_fields(repo.read("skills/a/SKILL.md"), {"category": "office"})
    repo.commit({"skills/a/SKILL.md": changed}, subject="edit", author="a")
    now = repo.read("skills/a/SKILL.md")
    assert "license: MIT\nmetadata:\n  x: 1\ncategory: office\n---" in now
    assert extra.read_text() == "print('kept')\n"


@pytest.mark.parametrize(
    "rel",
    [
        "../outside.md",
        "skills/../../x/SKILL.md",
        "skills/a/../../../etc/passwd",
        "/etc/passwd",
        ".git/config",
        "skills/.git/config",
        "notes/.hidden.md",
        "other/a.md",
        "skills",
        "notes/a\x00b.md",
    ],
)
def test_paths_that_leave_the_store_are_refused(rel):
    repo.ensure()
    with pytest.raises(repo.StoreError):
        repo.check_rel(rel)
    assert repo.read(rel) is None
    with pytest.raises(repo.StoreError):
        repo.commit({rel: "x"}, subject="x", author="x")


def test_a_link_inside_the_store_is_not_followed(tmp_path):
    repo.ensure()
    secret = tmp_path / "secret.md"
    secret.write_text("secret")
    (repo.root() / "notes" / "leak.md").symlink_to(secret)
    (repo.root() / "skills" / "out").symlink_to(tmp_path)
    assert repo.read("notes/leak.md") is None
    assert "notes/leak.md" not in repo.files()
    assert not any(rel.startswith("skills/out/") for rel in repo.files())
    with pytest.raises(repo.StoreError):
        repo.commit({"notes/leak.md": "overwritten"}, subject="x", author="x")
    assert secret.read_text() == "secret"


# --- Addresses --------------------------------------------------------------------
@pytest.mark.parametrize(
    "address",
    [
        "https://example.org/team/skills.git",
        "https://git@example.org:8443/team/skills",
        "ssh://git@example.org:2222/team/skills.git",
        "git@example.org:team/skills.git",
        "https://[2001:db8::1]/team/skills.git",
    ],
)
def test_https_and_ssh_addresses_are_taken(address):
    assert gitrun.check_address(address) == address


@pytest.mark.parametrize(
    "address",
    [
        "",
        "ext::sh -c 'touch /tmp/owned'",
        "ext::ssh example.org",
        "file:///etc",
        "/srv/git/skills.git",
        "../skills.git",
        "git://example.org/skills.git",
        "http://example.org/skills.git",
        "ftp://example.org/skills.git",
        "--upload-pack=touch /tmp/owned",
        "-oProxyCommand=touch owned",
        "ssh://-oProxyCommand=x/skills",
        "ssh://example.org/skills.git --upload-pack=x",
        "https://example.org/skills.git\n--upload-pack=x",
        "https://user:pass word@example.org/x",
        "fd::17/foo",
        "transport::address",
        "git@example.org:-oops/../x",
        "-x@example.org:team/skills.git",
    ],
)
def test_hostile_addresses_are_refused(address):
    with pytest.raises(gitrun.BadAddress):
        gitrun.check_address(address)
    assert repo.sync(address).state == repo.SYNC_ERROR


def test_a_local_path_is_an_address_only_where_the_deployment_says(tmp_path, local_remotes):
    assert gitrun.check_address(str(tmp_path)) == str(tmp_path)
    settings.skills_local_remote = False
    with pytest.raises(gitrun.BadAddress):
        gitrun.check_address(str(tmp_path))


def test_git_runs_with_no_prompt_no_hooks_and_none_of_the_accounts_configuration():
    repo.ensure()
    env = gitrun._base_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert "core.hooksPath=" + os.devnull in gitrun._FIXED
    assert "protocol.allow=never" in gitrun._FIXED
    # A hook in the repository is not run by a commit.
    marker = repo.root() / "hook-ran"
    hooks = repo.root() / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-commit", "post-commit", "commit-msg", "post-merge", "post-checkout"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        hook.chmod(0o755)
    repo.commit({"notes/a.md": "x\n"}, subject="x", author="x")
    assert not marker.exists()


def test_a_secret_is_not_an_argument_and_not_in_what_is_said():
    creds = gitrun.Credentials(username="ann", token="s3cr3t-token")
    with gitrun.Remote("https://example.org/x.git", creds) as remote:
        assert "s3cr3t-token" not in " ".join(remote.env.keys())
        assert remote.env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    said = gitrun.scrub("fatal: https://ann:s3cr3t-token@example.org/x", creds)
    assert "s3cr3t-token" not in said
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    with gitrun.Remote("git@example.org:x.git", gitrun.Credentials(ssh_key=key)) as remote:
        command = remote.env["GIT_SSH_COMMAND"]
        assert "StrictHostKeyChecking=accept-new" in command and "BatchMode=yes" in command
        path = Path(command.split("'-i' '")[1].split("'")[0])
        assert path.read_text().startswith("-----BEGIN") and (path.stat().st_mode & 0o077) == 0
    assert not path.exists()


def test_a_command_that_takes_too_long_is_stopped():
    repo.ensure()
    code, out = gitrun.run(["hash-object", "--stdin"], cwd=repo.root(), timeout=0.2, stdin=None)
    assert code in (0, 124)  # no stdin: answers at once
    code, out = gitrun.run(
        ["-c", "alias.wait=!sleep 30", "wait"], cwd=repo.root(), timeout=0.5
    )
    assert code == 124 and "longer than" in out


# --- A remote ---------------------------------------------------------------------
def test_an_empty_remote_takes_the_stores_history(bare, tmp_path):
    repo.ensure()
    repo.commit({"skills/a/SKILL.md": SKILL.format(name="a")}, subject="add", author="a")
    result = repo.sync(str(bare))
    assert (result.state, result.pushed) == (repo.SYNC_OK, True)
    assert _git(bare, "rev-parse", "main") == repo.head()
    assert repo.sync(str(bare)).state == repo.SYNC_OK


def test_a_remote_that_is_ahead_fast_forwards_and_ours_ahead_is_pushed(bare, tmp_path):
    repo.ensure()
    repo.sync(str(bare))
    work = _clone(bare, tmp_path)
    _push_file(work, "skills/b/SKILL.md", SKILL.format(name="b"), "from the remote")
    before = repo.head()
    result = repo.sync(str(bare))
    assert result.state == repo.SYNC_OK and result.pulled == (before, repo.head())
    assert repo.read("skills/b/SKILL.md") == SKILL.format(name="b")
    assert repo.changed_paths(*result.pulled) == ["skills/b/SKILL.md"]
    # A commit from the remote has no trailers of ours: history shows it as the remote's.
    last = repo.history("skills/b/SKILL.md")[0]
    assert last.origin == "remote" and last.author == "Remote Person"
    repo.commit({"notes/n.md": "ours\n"}, subject="note n: add", author="Ann")
    result = repo.sync(str(bare))
    assert (result.state, result.pushed) == (repo.SYNC_OK, True)
    assert _git(bare, "rev-parse", "main") == repo.head()


def test_a_diverged_remote_stops_and_nothing_is_merged(bare, tmp_path):
    repo.ensure()
    repo.sync(str(bare))
    work = _clone(bare, tmp_path)
    _push_file(work, "skills/b/SKILL.md", SKILL.format(name="b"))
    repo.commit({"skills/c/SKILL.md": SKILL.format(name="c")}, subject="add", author="Ann")
    ours = repo.head()
    theirs = _git(bare, "rev-parse", "main")
    result = repo.sync(str(bare))
    assert result.state == repo.SYNC_DIVERGED and "Nothing is merged" in result.message
    assert repo.head() == ours and _git(bare, "rev-parse", "main") == theirs
    assert repo.read("skills/b/SKILL.md") is None
    # Asking to adopt changes nothing about a divergence: the histories are related.
    assert repo.sync(str(bare), adopt=True).state == repo.SYNC_DIVERGED
    # By a person's word: our side is kept as a branch in the remote, and we take theirs.
    taken = repo.take_remote(str(bare))
    assert taken.state == repo.SYNC_OK and repo.head() == theirs
    assert _git(bare, "rev-parse", f"lembas/kept-{ours[:10]}") == ours
    assert repo.read("skills/c/SKILL.md") is None and repo.read("skills/b/SKILL.md")


def test_a_remote_with_skills_is_adopted_and_what_only_we_had_goes_on_top(bare, tmp_path):
    work = _clone(bare, tmp_path)
    _push_file(work, "skills/theirs/SKILL.md", SKILL.format(name="theirs"))
    _push_file(work, "skills/both/SKILL.md", SKILL.format(name="both"))
    _push_file(work, "README.md", "A repository of skills.\n")
    repo.ensure()
    repo.commit({"skills/ours/SKILL.md": SKILL.format(name="ours")}, subject="add", author="a")
    repo.commit({"skills/both/SKILL.md": "---\nname: both\n---\n\nOur text.\n"}, subject="add",
                author="a")
    # Without being asked to adopt, unrelated histories are a divergence.
    assert repo.sync(str(bare)).state == repo.SYNC_DIVERGED
    result = repo.sync(str(bare), adopt=True)
    assert result.state == repo.SYNC_OK and result.adopted
    assert repo.read("skills/theirs/SKILL.md") == SKILL.format(name="theirs")
    assert repo.read("skills/ours/SKILL.md") == SKILL.format(name="ours")
    assert repo.read("skills/both/SKILL.md") == SKILL.format(name="both")
    assert result.conflicts["skills/both/SKILL.md"].endswith("Our text.\n")
    assert (repo.root() / "README.md").read_text() == "A repository of skills.\n"
    assert _git(bare, "rev-parse", "main") == repo.head()


def _commit_tree_with(work: Path, mode: str, kind: str, sha: str, path: str) -> None:
    """Push a commit whose tree holds one entry git's porcelain would not make."""
    base = _git(work, "ls-tree", "HEAD")
    entry = f"{mode} {kind} {sha}\t{path}"
    tree = subprocess.run(
        ["git", "mktree"], cwd=work, input=(base + "\n" if base else "") + entry + "\n",
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    commit = _git(work, "commit-tree", tree, "-p", "HEAD", "-m", "hostile")
    _git(work, "push", "--quiet", "origin", f"{commit}:refs/heads/main")


def test_a_remote_that_sends_a_link_or_a_repository_inside_is_refused(bare, tmp_path):
    repo.ensure()
    repo.sync(str(bare))
    work = _clone(bare, tmp_path)
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=work, input="/etc/passwd",
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _commit_tree_with(work, "120000", "blob", blob, "passwd-link")
    before = repo.head()
    result = repo.sync(str(bare))
    assert result.state == repo.SYNC_REFUSED and "link" in result.message
    assert repo.head() == before and not (repo.root() / "passwd-link").exists()


def test_a_remote_that_sends_too_large_a_file_is_refused(bare, tmp_path, monkeypatch):
    repo.ensure()
    repo.sync(str(bare))
    work = _clone(bare, tmp_path)
    monkeypatch.setattr(gitrun, "MAX_FILE_BYTES", 200)
    _push_file(work, "notes/huge.md", "x" * 500)
    before = repo.head()
    result = repo.sync(str(bare))
    assert result.state == repo.SYNC_REFUSED and "larger" in result.message
    assert repo.head() == before


def test_a_fetch_that_grows_past_the_bound_is_stopped(bare, tmp_path, monkeypatch):
    repo.ensure()
    repo.sync(str(bare))
    work = _clone(bare, tmp_path)
    _push_file(work, "notes/big.md", os.urandom(3_000_000).hex())
    monkeypatch.setattr(gitrun, "MAX_FETCH_BYTES", 1)
    before = repo.head()
    result = repo.sync(str(bare))
    assert result.state == repo.SYNC_ERROR and repo.head() == before


def test_a_remotes_hook_and_configuration_run_nothing(bare, tmp_path):
    repo.ensure()
    repo.sync(str(bare))
    work = _clone(bare, tmp_path)
    marker = tmp_path / "ran"
    _push_file(work, ".gitattributes", "*.md filter=evil\n")
    _push_file(work, ".gitmodules", '[submodule "x"]\n\tpath = x\n\turl = ext::sh -c "touch '
               + str(marker) + '"\n')
    _push_file(work, "notes/a.md", "from the remote\n")
    result = repo.sync(str(bare))
    assert result.state == repo.SYNC_OK
    assert repo.read("notes/a.md") == "from the remote\n" and not marker.exists()


# --- The instance and its remote -------------------------------------------------
# The settings, the secrets, what a change from the remote is taken as, and a
# divergence, as the instance lives with them.
def _admin(db) -> User:
    user = User(email="root@example.org", name="Root", password_hash="x", role="admin")
    db.add(user)
    db.commit()
    return user


def test_a_remotes_secrets_are_stored_encrypted_and_an_empty_field_keeps_them(db):
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    service.remote_set(db, address="https://example.org/team/skills.git", username="ann",
                       token="s3cr3t-token", ssh_key=key)
    stored = db.get(Setting, policy.KEY).value
    assert "s3cr3t-token" not in str(stored) and "PRIVATE KEY" not in str(stored)
    assert crypto.decrypt(stored["remote_token_encrypted"]) == "s3cr3t-token"
    assert crypto.decrypt(stored["remote_ssh_key_encrypted"]) == key
    service.remote_set(db, address="https://example.org/team/other.git", username="ann")
    values = policy.settings(db)
    assert values["remote_address"].endswith("other.git")
    assert crypto.decrypt(values["remote_token_encrypted"]) == "s3cr3t-token"
    # Naming no remote forgets them.
    service.remote_set(db, address="")
    values = policy.settings(db)
    assert values["remote_address"] == "" and values["remote_token_encrypted"] == ""
    assert values["remote_ssh_key_encrypted"] == ""
    with pytest.raises(service.Refused, match="https:// and ssh://"):
        service.remote_set(db, address="ext::ssh")
    with pytest.raises(service.Refused):
        service.remote_set(db, address="file:///srv/skills.git")


def test_a_change_is_pushed_after_it_is_made(db, bare):
    admin = _admin(db)
    service.remote_set(db, address=str(bare))
    assert service.sync_now(db).state == repo.SYNC_OK
    service.skill_add(db, policy.person(db, admin), name="a", description="Use when a.",
                      body="Do it.")
    service.wait_for_sync()
    assert _git(bare, "rev-parse", "main") == repo.head()
    assert policy.settings(db)["sync_state"] == repo.SYNC_OK


def test_a_change_from_the_remote_is_a_persons_edit_shown_as_the_remotes(db, bare, tmp_path):
    """The remote is the owner's own repository: what arrives is taken at once,
    whatever the policy is for agents, and history says where it came from."""
    policy.save(db, {"skills_edit": policy.MANUAL})
    admin = _admin(db)
    service.remote_set(db, address=str(bare))
    service.sync_now(db)
    work = _clone(bare, tmp_path)
    _push_file(work, "skills/deploy/SKILL.md", SKILL.format(name="deploy"), "Add deploy by hand")
    _push_file(work, "notes/found.md",
               "---\ntitle: Found\nsubjects: [disk]\nkind: finding\ngroup: default\n---\n\n"
               "It is full.\n")
    assert service.sync_now(db).state == repo.SYNC_OK
    row = catalog.skill(db, "deploy")
    assert row is not None and (row.changed_origin, row.changed_by) == ("remote", "Remote Person")
    assert db.scalar(select(StoreProposal)) is None
    history = service.history_text(service.TARGET_SKILL, "deploy")
    assert "Add deploy by hand" in history and "Remote Person [remote]" in history
    note = catalog.note(db, policy.person(db, admin), "found")
    assert (note.kind, note.subjects_json, note.changed_origin) == ("finding", ["disk"], "remote")


def test_a_note_from_the_remote_for_a_group_nobody_has_is_seen_by_nobody(db, bare, tmp_path):
    admin = _admin(db)
    service.remote_set(db, address=str(bare))
    service.sync_now(db)
    work = _clone(bare, tmp_path)
    _push_file(work, "notes/elsewhere.md", "---\ntitle: E\ngroup: no-such-group\n---\n\nx\n")
    _push_file(work, "notes/plain.md", "No header at all.\n")
    service.sync_now(db)
    seen = {n.ident for n in catalog.notes(db, policy.person(db, admin))}
    assert seen == {"plain"}  # a note that names no group is the default group's


def test_a_divergence_stops_syncing_and_is_said_until_a_person_resolves_it(db, bare, tmp_path):
    admin = _admin(db)
    hand = policy.person(db, admin)
    service.remote_set(db, address=str(bare))
    service.sync_now(db)
    work = _clone(bare, tmp_path)
    _push_file(work, "skills/theirs/SKILL.md", SKILL.format(name="theirs"))
    # A change here before the remote's was seen: the push is refused, and that is a divergence.
    service.skill_add(db, hand, name="ours", description="Use when ours.", body="Do it.")
    service.wait_for_sync()
    values = policy.settings(db)
    assert values["sync_state"] == repo.SYNC_DIVERGED
    assert "Nothing is merged" in values["sync_message"] or "changed" in values["sync_message"]
    theirs = _git(bare, "rev-parse", "main")
    # Stopped: further changes are kept here and nothing is tried.
    service.skill_add(db, hand, name="more", description="Use when more.", body="Do it.")
    service.wait_for_sync()
    assert service.sync_now(db).state == repo.SYNC_DIVERGED
    assert _git(bare, "rev-parse", "main") == theirs and catalog.skill(db, "theirs") is None
    ours = repo.head()
    # A person takes the remote's side; this store's side is kept in the remote as a branch.
    taken = service.take_remote(db)
    assert taken.state == repo.SYNC_OK and "kept as the branch" in taken.message
    assert _git(bare, "rev-parse", f"lembas/kept-{ours[:10]}") == ours
    assert catalog.skill(db, "theirs") is not None and catalog.skill(db, "ours") is None
    assert policy.settings(db)["sync_state"] == repo.SYNC_OK


def test_connecting_a_remote_that_holds_skills_adopts_them(db, bare, tmp_path):
    admin = _admin(db)
    hand = policy.person(db, admin)
    work = _clone(bare, tmp_path)
    _push_file(work, "skills/theirs/SKILL.md", SKILL.format(name="theirs"))
    _push_file(work, "skills/both/SKILL.md", SKILL.format(name="both"))
    service.skill_add(db, hand, name="ours", description="Use when ours.", body="Do it.")
    service.skill_add(db, hand, name="both", description="Use when both, here.", body="Ours.")
    service.remote_set(db, address=str(bare))
    result = service.sync_now(db)
    assert result.state == repo.SYNC_OK and result.adopted
    assert {s.name for s in catalog.skills(db)} == {"theirs", "both", "ours"}
    assert catalog.skill(db, "both").description == "Use when both."
    # What this instance had under the same name is neither lost nor merged: it waits.
    waiting = service.pending(db, admin)
    assert [(p.name, p.origin) for p in waiting] == [("both", "import")]
    diff, fits = service.proposal_diff(waiting[0])
    assert fits and "+Ours." in diff
    assert _git(bare, "rev-parse", "main") == repo.head()
    # Adoption is for connecting: from now on unrelated history is a divergence.
    assert policy.settings(db)["remote_adopted"] is True


def test_a_backup_holds_the_store_and_a_restore_puts_it_back(db):
    import json
    import tarfile

    from lembas.db.session import init_db
    from lembas.services import backup

    init_db()  # as a start does: marks the database 2.x, which a restore checks
    admin = _admin(db)
    service.skill_add(db, policy.person(db, admin), name="a", description="Use when a.",
                      body="Do it.")
    archive = backup.take(passphrase="")
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
        manifest = json.load(tar.extractfile(backup.MANIFEST))
    assert "skills-store/skills/a/SKILL.md" in names and "skills-store/.git/HEAD" in names
    assert "skills-store/skills/a/SKILL.md" in {f["path"] for f in manifest["files"]}
    head = repo.head()
    service.skill_remove(db, policy.person(db, admin), name="a")
    db.close()
    backup.stage(archive)
    aside = backup.apply_pending()
    assert repo.head() == head and repo.read("skills/a/SKILL.md") is not None
    assert (aside / "skills-store" / ".git").is_dir()
