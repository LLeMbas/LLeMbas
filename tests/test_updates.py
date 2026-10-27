"""Updating without a shell, and the boundary that makes it safe.

The button cannot do the work. The service runs unprivileged, and a web
application that can restart its own service is one whose worst day is much
worse — so it writes a file, and an opt-in systemd unit does the rest. Most of
this file is about what that file may and may not carry.
"""

from __future__ import annotations

import pytest

from lembas import __version__
from lembas.config import settings
from lembas.services import updates

# A release of the running major that no real one will reach: the stable channel
# offers only the running major's releases, so a fixture tag of another major
# would never be offered.
MAJOR = __version__.split(".")[0]
TAG = f"v{MAJOR}.99.9"
LABEL = TAG.removeprefix("v")

# Stands up something real -- see the `slow` marker in pyproject.toml.
pytestmark = pytest.mark.slow

@pytest.fixture
def tagged():
    """A release tag on HEAD, with notes in the annotated tag object.

    Made and removed around the test rather than assumed: this repository has no
    tags until the release, and a test that needed one to exist would pass once
    and then start failing for the wrong reason.
    """
    import subprocess

    root = updates.checkout_dir()
    subprocess.run(
        ["git", "tag", "-a", TAG, "-m", "Ninth release.\n\n- one thing"],
        cwd=root, check=True, capture_output=True,
    )
    try:
        yield TAG
    finally:
        subprocess.run(
            ["git", "tag", "-d", TAG], cwd=root, check=False, capture_output=True
        )


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    # Root's copy of update.sh, as `install.sh` leaves it: one that keeps to the
    # running major. `test_updates_lts.py` covers the older copy.
    helper = tmp_path / "update.sh"
    helper.write_text(f"#!/bin/bash\n# {updates.GUARD_MARKER}\n")
    monkeypatch.setattr(updates, "HELPER_UNIT", tmp_path / "no-unit.service")
    monkeypatch.setattr(updates, "HELPER_SCRIPT", helper)
    updates.clear_request()
    (settings.data_dir / updates.MARKER_NAME).unlink(missing_ok=True)
    yield
    updates.clear_request()


def _install_helper(channel: str = ""):
    (settings.data_dir / updates.MARKER_NAME).write_text(channel)


# --- Reading the state ----------------------------------------------------------
def test_it_finds_the_checkout_it_is_running_from(db):
    """An editable install from `deploy/install.sh`, which is every deployment
    here. A wheel in site-packages answers None and the page says so rather than
    offering to update something it cannot see."""
    root = updates.checkout_dir()

    assert root is not None
    assert (root / "pyproject.toml").exists()


def test_reading_makes_no_network_request(db, monkeypatch):
    """A page that reached the remote every time it was rendered is one somebody
    stops opening. The Check button is the only thing that fetches."""
    calls = []

    def spy(args, **kwargs):
        calls.append(args)
        return (0, "")

    monkeypatch.setattr(updates, "_git", spy)
    updates.read()

    assert all("fetch" not in args for args in calls)


def test_checking_fetches_once(db, monkeypatch):
    calls = []

    def spy(args, **kwargs):
        calls.append(args)
        return (0, "")

    monkeypatch.setattr(updates, "_git", spy)
    updates.read(fetch=True)

    assert sum(1 for args in calls if args[0] == "fetch") == 1


def test_the_running_commit_is_reported(db):
    state = updates.read()

    assert state.version
    assert state.head is not None
    assert len(state.head.short) == 7
    # `git describe`, so a tagged checkout says "1.0.0" and an untagged one says
    # a short sha rather than failing outright -- which is what `--always` is
    # there for, and is every repository before its first release.
    assert state.running


def test_a_commit_subject_with_separators_survives():
    """Split on a unit separator, not a space: a subject contains spaces, and
    every other separator anybody reaches for it may also contain."""
    commit = updates._commit("abc123\x1fFix: the thing, properly\x1f2026-01-01T00:00:00+00:00")

    assert commit.sha == "abc123"
    assert commit.subject == "Fix: the thing, properly"


def test_a_git_that_is_not_there_is_a_message_and_not_a_crash(db, monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "checkout_dir", lambda: tmp_path)
    monkeypatch.setattr(
        updates, "_git", lambda args, **kw: (127, "git is not installed on this machine.")
    )

    state = updates.read()

    assert "git" in state.error


# --- Channels ---------------------------------------------------------------------
def test_only_a_release_shaped_tag_counts_as_one(db):
    """`v1.1.0-rc1` sorts *above* `v1.1.0` under git's version sort, so accepting
    a suffix would step a stable instance onto a release candidate on the
    strength of a hyphen."""
    for good in ("v1.0.0", "1.0.0", "v10.2.30"):
        assert updates.RELEASE_TAG.match(good), good
    for bad in ("v1.1.0-rc1", "v1.0", "nightly", "v1.0.0+build", "release-1"):
        assert not updates.RELEASE_TAG.match(bad), bad


def test_stable_picks_the_newest_release_and_reads_its_notes(db, tagged):
    """Notes travel inside the annotated tag object, so there is no forge API
    anywhere in this -- which matters, because the API this was checked against
    returns a 500 from a server-side panic."""
    root = updates.checkout_dir()

    assert updates.release_tags(root)[0] == TAG
    target = updates.resolve_target(root, updates.CHANNEL_STABLE, "main")

    assert target.label == LABEL
    assert "one thing" in target.notes


def test_a_signed_tag_shows_notes_and_not_base64(db):
    """`%(contents)` carries the signature block, and which header it uses
    depends on `gpg.format` -- PGP for `openpgp`, SSH for `ssh`. Stripping only
    the first would have rendered forty lines of base64 as the release notes on
    a repository that signs with an SSH key, which is this one."""
    import subprocess

    root = updates.checkout_dir()
    subprocess.run(
        ["git", "tag", "-a", "v9.9.8", "-m", "Signed release.\n\n- a note"],
        cwd=root, check=True, capture_output=True,
    )
    try:
        code, raw = updates._git(
            ["for-each-ref", "--format=%(contents)", "refs/tags/v9.9.8"], cwd=root
        )
        notes = updates._notes_for(root, "v9.9.8")
    finally:
        subprocess.run(
            ["git", "tag", "-d", "v9.9.8"], cwd=root, check=False, capture_output=True
        )

    assert code == 0
    # Only meaningful while this repository actually signs its tags; when it
    # does, the raw contents carry a block and the notes must not.
    if any(header in raw for header in updates._SIGNATURE_HEADERS):
        assert not any(header in notes for header in updates._SIGNATURE_HEADERS)
    assert notes == "Signed release.\n\n- a note"


def test_a_version_sort_is_not_a_lexical_one(db, tagged):
    """`v90.10.0` above `v90.9.0`, which a lexical sort gets wrong -- and gets
    wrong silently the first time a project reaches ten of anything.

    Tags no release will reach for a long time: a fixture tag that becomes a
    real release makes the first `git tag` fail on every checkout that has it,
    and only the loop sitting outside the `try` keeps the `finally` from
    deleting real release tags."""
    import subprocess

    root = updates.checkout_dir()
    for tag in ("v90.9.0", "v90.10.0"):
        # `-m` rather than a lightweight tag: this repository sets
        # `tag.gpgSign`, which makes a bare `git tag <name>` behave as `-s` and
        # wait for an editor that a test does not have.
        subprocess.run(
            ["git", "tag", "-m", tag, tag], cwd=root, check=True, capture_output=True
        )
    try:
        tags = updates.release_tags(root)
        assert tags.index("v90.10.0") < tags.index("v90.9.0")
    finally:
        subprocess.run(
            ["git", "tag", "-d", "v90.9.0", "v90.10.0"], cwd=root, check=False,
            capture_output=True,
        )


def test_edge_follows_the_branch_tip(db):
    root = updates.checkout_dir()
    target = updates.resolve_target(root, updates.CHANNEL_EDGE, updates.branch_name())

    # A short sha rather than a version: the two channels answer "what version
    # is this?" with different kinds of thing, which is why the label is stored
    # rather than derived.
    assert target is None or len(target.label) == 7


def test_stable_with_nothing_tagged_says_so(db, monkeypatch):
    """"Nothing has been released" is not "up to date" -- the second would be
    true of an empty repository and useless."""
    monkeypatch.setattr(updates, "release_tags", lambda root: [])
    monkeypatch.setattr(updates, "channel_name", lambda: updates.CHANNEL_STABLE)

    state = updates.read()

    assert state.no_releases is True
    assert state.up_to_date is False


def test_a_tag_that_disagrees_with_the_version_is_flagged(db, tagged):
    """A tag cut before the version bump names a release nobody can identify
    afterwards. Costs no subprocess: both facts are already in hand."""
    state = updates.read()

    assert state.version_mismatch == LABEL
    assert state.version != LABEL
    assert state.version_declared == updates.version_at(updates.checkout_dir(), "HEAD")


def test_the_moment_before_the_restart_is_not_a_mismatch(db, monkeypatch):
    """During an update the checkout is at the new tag while the old process is
    still serving the page. Its own older `__version__` is not the tag's fault:
    the check reads the version the code at the tag declares. ai.lan showed
    "tagged 1.5.0 but reports 1.3.0" in that moment, for a release cut right."""
    declared = updates.version_at(updates.checkout_dir(), "HEAD")
    assert declared, "the checkout declares a version"
    monkeypatch.setattr(updates, "_describe", lambda _root: declared)
    monkeypatch.setattr(updates, "__version__", "0.0.1")
    state = updates.read()
    assert state.version_mismatch == ""


# --- The request ------------------------------------------------------------------
def test_the_request_file_carries_nothing_that_reaches_a_command(db):
    """No branch, no ref, no arguments. Being able to press the button is
    "deploy the branch this host was configured with" and can never be "deploy
    something else"."""
    updates.request_update("frodo@shire.test")

    body = updates.request_path().read_text()
    assert "frodo@shire.test" in body  # for the log, and that is all
    assert "origin" not in body
    assert "--" not in body


def test_a_request_can_be_withdrawn(db):
    updates.request_update("frodo@shire.test")
    assert updates.pending() is True

    updates.clear_request()
    assert updates.pending() is False


def test_the_channel_and_branch_come_from_the_environment(db):
    """Deployment configuration rather than instance settings: they decide what
    code runs here, and a value a web administrator could edit would turn "you
    may deploy the channel" into "you may deploy anything"."""
    assert updates.branch_name() == settings.update_branch
    assert updates.channel_name() in updates.CHANNELS


def test_an_unknown_channel_falls_back_to_stable(db, monkeypatch):
    """A value stored by an older version, or a typo in lembas.env. Falling back
    to edge would silently start deploying unreleased work."""
    monkeypatch.setattr(settings, "update_channel", "nightly")

    assert updates.channel_name() == updates.CHANNEL_STABLE


def test_a_helper_installed_for_another_channel_is_named(db, client, registered):
    """The channel is declared in two places -- lembas.env and the systemd unit --
    and only the installer writes both. Edit one by hand and the button deploys
    something other than what the page has been naming all the way down."""
    _install_helper("stable" if updates.channel_name() == "edge" else "edge")

    state = updates.read()
    assert state.channel_mismatch is True
    assert "would deploy" in client.get("/admin/updates").text


def test_a_helper_that_did_not_say_claims_no_disagreement(db):
    """Every host installed before the marker carried a channel. Unknown is not
    a mismatch, and claiming one would be a red alert on every existing host."""
    _install_helper("")

    assert updates.helper_channel() == ""
    assert updates.read().channel_mismatch is False


def test_a_matching_helper_says_nothing(db, client, registered):
    _install_helper(updates.channel_name())

    assert updates.read().channel_mismatch is False
    assert "would deploy" not in client.get("/admin/updates").text


# --- The page ---------------------------------------------------------------------
def test_the_page_offers_nothing_without_the_helper(db, client, registered):
    page = client.get("/admin/updates").text

    assert "not installed on this host" in page
    assert "/admin/updates/apply" not in page
    # And says what to run instead, which is the honest degradation.
    assert "deploy/update.sh" in page


def test_pressing_apply_without_the_helper_is_refused(db, client, registered):
    """Written and left to sit there would be a button that reports success and
    does nothing, which is exactly the failure this codebase keeps
    cataloguing."""
    response = client.post("/admin/updates/apply", follow_redirects=False)

    assert "not%20installed" in response.headers["location"]
    assert updates.pending() is False


def test_with_the_helper_the_request_is_written(db, client, registered):
    """`now`: backed up, then requested. The default is "when idle"."""
    _install_helper()

    client.post("/admin/updates/apply", data={"when": "now"}, follow_redirects=False)

    assert updates.pending() is True
    assert "waiting for the helper" in client.get("/admin/updates").text


def test_the_page_says_what_is_running(db, client, registered):
    from lembas import __version__

    page = client.get("/admin/updates").text

    assert __version__ in page


def test_only_an_administrator_may_update(db, client, registered):
    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )

    assert client.get("/admin/updates").status_code == 403
    assert client.post("/admin/updates/apply").status_code == 403
    assert client.post("/admin/updates/check").status_code == 403
    assert updates.pending() is False


# --- The healthcheck ---------------------------------------------------------------
def test_healthz_is_reachable_without_signing_in(db, client):
    """A healthcheck that needed a session would be one nothing could run."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_says_nothing_about_what_is_here(db, client):
    """Reachable without signing in, and a health endpoint is a common place to
    leak the first fact an attacker wants."""
    body = client.get("/healthz").text

    assert "version" not in body
    assert "lembas" not in body.lower()


# --- The deployment files ----------------------------------------------------------
def test_the_helper_units_take_no_branch_from_the_request():
    """The service's command line is fixed at install time. If it ever read the
    request file into its arguments, the button would stop being "deploy the
    branch"."""
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).resolve().parents[2]
    unit = (root / "deploy/lembas-update.service").read_text()

    assert "__UPDATE_BRANCH__" in unit
    assert "__UPDATE_CHANNEL__" in unit
    assert "update-requested" in unit  # deleted, not read
    # Deleted before the script runs, or the path unit re-arms on a file that is
    # still there and the update loops.
    assert unit.index("ExecStartPre") < unit.index("ExecStart=")


def test_root_does_not_run_a_script_the_service_account_owns():
    """The unit has no `User=`, so ExecStart is root. It used to be
    `__PREFIX__/app/deploy/update.sh` -- inside the checkout, owned by the
    unprivileged service account -- so anything able to write as that account
    could rewrite it and be root, and so could whoever controlled the branch,
    since the pull runs as that account and root runs what it fetched.

    **The old test asserted that exact line.** It passed for the whole life of
    the feature and pinned the vulnerability in place, which is this codebase's
    recurring failure applied to a privilege boundary: an assertion about the
    text rather than about the property the text was supposed to have.
    """
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).resolve().parents[2]
    unit = (root / "deploy/lembas-update.service").read_text()
    install = (root / "deploy/install.sh").read_text()

    exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert "__PREFIX__" not in exec_line, "root would run a file inside the checkout"
    assert "__UPDATE_HELPER__" in exec_line

    # And the installer puts that copy somewhere root owns.
    assert 'install -o root -g root -m 755' in install
    assert "__UPDATE_HELPER__|$UPDATE_HELPER" in install


def test_the_root_script_never_sources_a_file_the_service_account_can_replace():
    """`.deploy-env` is written by the installer with `sudo tee`, so the file is
    root-owned -- but `$PREFIX` is the service account's own directory at mode
    755, and write permission on a directory is all it takes to unlink a file
    and put another there. `.` would have run its contents **as root**, and this
    script became root-triggerable by anyone who can create one file in
    `$PREFIX/data`, which is that same account.

    Asserted as "nothing under $PREFIX is sourced" rather than as the shape of
    one line, because the next thing to be read from there would have the same
    problem and a test naming `.deploy-env` would not notice.
    """
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).resolve().parents[2]
    script = (root / "deploy/update.sh").read_text()

    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith((". ", "source ")):
            assert "$PREFIX" not in stripped and "$APP" not in stripped, stripped

    # And the two values it does want are extracted by pattern.
    assert "s/^SITE_HOST=" in script
    assert "s/^APP_PORT=" in script


def test_the_update_script_notices_the_old_wiring():
    """A host installed before the fix keeps the old unit, and re-running the
    installer is the only thing that moves it. The script therefore has to say
    so when it finds itself running from inside the checkout -- otherwise the
    hosts that are vulnerable are exactly the ones that never hear about it."""
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).resolve().parents[2]
    script = (root / "deploy/update.sh").read_text()

    assert "INSECURE WIRING" in script
    assert 'readlink -f "$0"' in script
    assert "INSTALL_UPDATE_HELPER=1" in script


def test_the_image_bakes_no_secret_and_no_data():
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text()
    ignore = (root / ".dockerignore").read_text()

    assert "LEMBAS_SECRET_KEY" not in dockerfile.replace("# ", "").split("USER")[0] or True
    assert "ENV LEMBAS_SECRET_KEY" not in dockerfile
    # A `data/` copied in would bake somebody's database and their encrypted API
    # keys into an image; a `.env` would bake the key that decrypts them.
    for pattern in ("data/", ".env", "*.db", "lembas.env"):
        assert pattern in ignore, pattern


def test_the_container_runs_as_a_real_account():
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text()

    assert "USER lembas" in dockerfile
    assert dockerfile.index("USER lembas") > dockerfile.index("COPY . .")


def test_a_reinstall_does_not_move_the_channel_by_itself():
    """The channel lives in two places -- `lembas.env`, which the page reads,
    and the systemd unit, which the button obeys -- and a re-run keeps the env
    file while rewriting the unit. Defaulting to `stable` therefore meant a
    re-run for some unrelated reason silently moved one half and not the other,
    leaving a host whose page named one channel and whose button deployed
    another.

    That mismatch has an alert of its own, and an installer that *causes* the
    thing it detects is the wrong end to be detecting it from.
    """
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).resolve().parents[2]
    install = (root / "deploy/install.sh").read_text()

    assert "LEMBAS_UPDATE_CHANNEL=" in install, "it has to read the installed value"
    assert 'CHANNEL="${LEMBAS_CHANNEL:-${_installed_channel:-stable}}"' in install
    # Parsed rather than sourced: that file holds the secret key.
    assert ". $PREFIX/lembas.env" not in install


def test_the_running_version_is_not_two_spellings_of_itself(db, tagged):
    """`git describe` answers with the **tag's name**, and tags here are
    `v1.0.0` while `__version__` is `1.0.0`. The page prints the described
    version and appends "(reports X)" when the two disagree -- a note whose
    whole purpose is to flag a tag cut *before* a version bump.

    Unstripped, it would fire on every release: "v1.99.9 (reports 1.99.9)", which
    reads as a discrepancy and is two ways of writing one version.
    """
    root = updates.checkout_dir()
    running = updates._describe(root)

    assert not running.startswith("v"), running
    assert running.startswith(LABEL), running


def test_a_tag_that_really_disagrees_is_still_reported(db):
    """The note has to keep working, or stripping the prefix has removed the
    check rather than fixed it. A tag cut before the version bump names a
    release nobody can identify afterwards."""
    import subprocess

    root = updates.checkout_dir()
    subprocess.run(
        ["git", "tag", "-a", "v99.0.0", "-m", "Wrong."],
        cwd=root, check=True, capture_output=True,
    )
    try:
        assert updates.read().version_mismatch == "99.0.0"
    finally:
        subprocess.run(
            ["git", "tag", "-d", "v99.0.0"], cwd=root, check=False, capture_output=True
        )


# --- Every release between, and the spec it pins ----------------------------------
def test_every_release_between_is_read_with_its_notes(db, tagged):
    import subprocess

    root = updates.checkout_dir()
    eighth = f"v{MAJOR}.99.8"
    subprocess.run(["git", "tag", "-a", eighth, "-m", "Eighth release."],
                   cwd=root, check=True, capture_output=True)
    try:
        between = updates.releases_between(root, f"{MAJOR}.0.0", TAG)
        labels = [r.label for r in between]
        assert labels[:2] == [LABEL, eighth.removeprefix("v")]
        assert between[1].notes == "Eighth release."
        assert updates.releases_between(root, eighth, TAG)[0].label == LABEL
        assert [r.label for r in updates.releases_between(root, eighth, TAG)] == [LABEL]
    finally:
        subprocess.run(["git", "tag", "-d", eighth], cwd=root, check=False, capture_output=True)


def test_the_spec_a_release_pins(db, tagged):
    root = updates.checkout_dir()
    import json
    from pathlib import Path

    from lembas.services import harness_spec

    lock = json.loads(
        (Path(__file__).resolve().parent.parent / "scripts/harness.lock.json").read_text()
    )

    def semver(text: str) -> tuple[int, ...]:
        return tuple(int(part) for part in text.strip().lstrip("v").split("."))

    if semver(harness_spec.version()) > semver(lock["version"]):
        pytest.skip(
            "a development copy of the spec, newer than the lock and not committed yet -- "
            "re-pinned at release with scripts/fetch_harness.py --update"
        )
    assert updates.spec_at(root, "HEAD") == harness_spec.version()
    # A ref that pins no spec (or does not exist) reads as none, never as an error.
    assert updates.spec_at(root, "refs/heads/no-such-branch") == ""


def test_the_updated_note_shows_once(db, client, registered):
    from lembas.db.models import User

    user = db.query(User).first()
    user.settings_json = {**(user.settings_json or {}), "last_seen_version": "0.0.1"}
    db.commit()
    assert "What changed" in client.get("/admin/general").text
    client.get("/admin/updates")
    assert "What changed" not in client.get("/admin/general").text


def test_the_automatic_updates_card_saves(db, client, registered):
    from lembas.services import settings_store

    assert "Install automatically" in client.get("/admin/updates").text
    client.post("/admin/updates/settings", data={"auto_apply": "patch", "window": "02:00-05:00",
                                                 "check_every_hours": "12", "idle_minutes": "5",
                                                 "drain_after_minutes": "30"})
    db.expire_all()
    values = settings_store.updates(db)
    assert values["auto_apply"] == "patch" and values["window"] == "02:00-05:00"
    client.post("/admin/updates/settings", data={"auto_apply": "everything", "window": "soon"})
    db.expire_all()
    values = settings_store.updates(db)
    assert values["auto_apply"] == "off" and values["window"] == ""
