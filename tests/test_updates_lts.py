"""A long-term-support line stays on its major version.

The stable channel offers the running major's releases and nothing else: a new
major may change what lies underneath (2.0 does not carry a 1.x database over),
so moving to one is done by hand. The page, the automatic updates and the root
helper (`deploy/update.sh`) each hold that line, and a host whose helper copy
predates it is not allowed to cross it either.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from lembas import __version__
from lembas.config import settings
from lembas.services import updates, updates_auto

ROOT = Path(__file__).resolve().parent.parent
MAJOR = int(__version__.split(".")[0])


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    updates_auto.reset()
    updates.clear_request()
    monkeypatch.setattr(updates, "HELPER_UNIT", tmp_path / "lembas-update.service")
    monkeypatch.setattr(updates, "HELPER_SCRIPT", tmp_path / "update.sh")
    yield
    updates_auto.reset()
    updates.clear_request()
    (settings.data_dir / updates.MARKER_NAME).unlink(missing_ok=True)


def _helper(tmp_path, *, guarded: bool) -> None:
    """A helper installed as `install.sh` leaves it: the marker file, and root's
    copy of the script -- with the same-major line or without."""
    (settings.data_dir / updates.MARKER_NAME).write_text("stable")
    body = "#!/bin/bash\n" + (f"# {updates.GUARD_MARKER}\n" if guarded else "") + "exit 0\n"
    (tmp_path / "update.sh").write_text(body)


# --- Which releases are offered -------------------------------------------------------
def test_the_major_of_a_version():
    assert updates.major_of("1.7.2") == 1
    assert updates.major_of("v2.0.0") == 2
    assert updates.major_of("2.0.0-beta.1") == 2
    assert updates.major_of("nightly") is None


def test_only_the_running_majors_releases_are_offered():
    tags = ["v2.1.0", "v2.0.0", "v1.7.4", "v1.7.3", "v1.6.0"]
    assert updates.same_major(tags, "1.7.2") == ["v1.7.4", "v1.7.3", "v1.6.0"]
    assert updates.newer_major(tags, "1.7.2") == "v2.1.0"
    assert updates.newer_major(tags, "2.1.0") == ""
    assert updates.same_major(tags, "2.0.0") == ["v2.1.0", "v2.0.0"]


def test_every_policy_stays_on_the_major():
    """`all` once meant every release, a new major included."""
    for policy in updates_auto.POLICIES:
        assert not updates_auto.allowed("1.7.2", "2.0.0", policy), policy
    assert updates_auto.allowed("1.7.2", "1.7.3", "all")
    assert updates_auto.allowed("1.7.2", "1.8.0", "all")


@pytest.mark.slow
def test_stable_resolves_to_the_newest_release_of_the_running_major(tmp_path):
    repo = _repo(tmp_path / "repo", "1.7.2", ["v1.7.2", "v1.7.3", "v2.0.0"])
    target = updates.resolve_target(repo, updates.CHANNEL_STABLE, "main")
    assert target is not None and target.label == "1.7.3"
    assert updates.newer_major(updates.release_tags(repo)) == "v2.0.0"


# --- The root helper ------------------------------------------------------------------
def test_the_shipped_script_says_it_keeps_to_the_major():
    assert updates.GUARD_MARKER in (ROOT / "deploy/update.sh").read_text()


def test_the_helper_is_found_through_its_unit(tmp_path):
    elsewhere = tmp_path / "opt/update.sh"
    elsewhere.parent.mkdir()
    elsewhere.write_text(f"# {updates.GUARD_MARKER}\n")
    (tmp_path / "lembas-update.service").write_text(
        f"[Service]\nExecStartPre=-/usr/bin/rm -f x\nExecStart=/bin/bash {elsewhere}\n"
    )
    assert updates.helper_script() == elsewhere
    assert updates.helper_guarded() is True


def test_an_older_or_unreadable_helper_is_not_trusted(tmp_path):
    assert updates.helper_guarded() is False  # nothing there to read
    _helper(tmp_path, guarded=False)
    assert updates.helper_guarded() is False
    _helper(tmp_path, guarded=True)
    assert updates.helper_guarded() is True


# --- The page -------------------------------------------------------------------------
def _apply_buttons(html: str) -> list[dict[str, str]]:
    """The attributes of the two buttons that request an update (idle, now)."""
    from html.parser import HTMLParser

    found: list[dict[str, str]] = []

    class Finder(HTMLParser):
        def handle_starttag(self, tag, attrs):
            got = {key: (value or "") for key, value in attrs}
            if tag == "button" and got.get("name") == "when":
                found.append(got)

    Finder().feed(html)
    return found


def _state(monkeypatch, *, newer: str):
    state = updates.State(
        checkout=str(ROOT),
        head=updates.Commit(sha="a" * 40, subject="Running"),
        available=updates.Target(ref="v1.99.0", label="1.99.0", sha="b" * 40),
        helper=updates.helper_installed(),
        newer_major=newer,
    )
    monkeypatch.setattr(updates, "read", lambda fetch=False: state)


def test_the_page_names_a_newer_major_it_does_not_offer(client, registered, monkeypatch):
    _state(monkeypatch, newer=f"{MAJOR + 1}.0.0")
    page = client.get("/admin/updates").text
    assert f"LLeMbas {MAJOR + 1}.0.0, a new major version, is out" in page
    assert "done by hand" in page


def test_nothing_is_said_when_there_is_no_newer_major(client, registered, monkeypatch):
    _state(monkeypatch, newer="")
    assert "a new major version, is out" not in client.get("/admin/updates").text


def test_an_older_helper_is_named_and_held_while_a_new_major_exists(
    client, registered, monkeypatch, tmp_path
):
    _helper(tmp_path, guarded=False)
    _state(monkeypatch, newer=f"{MAJOR + 1}.0.0")
    page = client.get("/admin/updates").text
    assert "does not keep to one major version" in page
    buttons = _apply_buttons(page)
    assert len(buttons) == 2 and all("disabled" in b for b in buttons)


def test_a_current_helper_is_not_held(client, registered, monkeypatch, tmp_path):
    _helper(tmp_path, guarded=True)
    _state(monkeypatch, newer=f"{MAJOR + 1}.0.0")
    page = client.get("/admin/updates").text
    assert "does not keep to one major version" not in page
    buttons = _apply_buttons(page)
    assert len(buttons) == 2 and not any("disabled" in b for b in buttons)


def test_an_older_helper_is_not_asked_to_cross(db, monkeypatch, tmp_path):
    """The idle path and Now both end in `updates_auto.apply`, which refuses."""
    _helper(tmp_path, guarded=False)
    _state(monkeypatch, newer=f"{MAJOR + 1}.0.0")
    asked = []
    monkeypatch.setattr(updates, "request_update", lambda who="": asked.append(who) or "")
    problem = asyncio.run(updates_auto.apply("an administrator"))
    assert "new major version" in problem and asked == []


def test_an_older_helper_still_takes_a_patch_while_no_new_major_exists(
    db, monkeypatch, tmp_path
):
    _helper(tmp_path, guarded=False)
    _state(monkeypatch, newer="")
    asked = []
    monkeypatch.setattr(updates, "request_update", lambda who="": asked.append(who) or "")
    assert asyncio.run(updates_auto.apply("automatic")) == ""
    assert asked == ["automatic"]


# --- deploy/update.sh, run for real against a throwaway repository ----------------------
def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
    }
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout.strip()


def _repo(path: Path, start: str, tags: list[str]) -> Path:
    """A repository with one commit per tag, each declaring its own version,
    left checked out at the commit that declares `start`."""
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "src/lembas").mkdir(parents=True)
    (path / "deploy").mkdir()
    (path / "deploy/lembas.service").write_text("[Service]\n")
    (path / "deploy/nginx-vhost.conf").write_text("")
    for tag in tags:
        version = tag.removeprefix("v")
        (path / "src/lembas/__init__.py").write_text(f'__version__ = "{version}"\n')
        _git(path, "add", "-A")
        _git(path, "commit", "-q", "-m", version)
        _git(path, "tag", "-a", tag, "-m", version)
    _git(path, "-c", "advice.detachedHead=false", "checkout", "-q", f"v{start}")
    return path


def _run_update(tmp_path: Path, start: str, tags: list[str]) -> tuple[str, str]:
    """`deploy/update.sh` on a prefix whose checkout is at `start`, with `sudo`
    and `pip` stubbed: `pip` fails, so the script stops right after it has
    chosen and checked out its target -- nothing past that point runs."""
    upstream = _repo(tmp_path / "upstream", start, tags)
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    subprocess.run(
        ["git", "clone", "-q", "--no-checkout", str(upstream), str(prefix / "app")],
        check=True, capture_output=True,
    )
    _git(prefix / "app", "-c", "advice.detachedHead=false", "checkout", "-q", f"v{start}")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "sudo").write_text('#!/bin/bash\n[[ "$1" == -u ]] && shift 2\nexec "$@"\n')
    (stubs / "systemctl").write_text("#!/bin/bash\nexit 0\n")
    (prefix / "venv/bin").mkdir(parents=True)
    (prefix / "venv/bin/pip").write_text("#!/bin/bash\nexit 1\n")
    for stub in (stubs / "sudo", stubs / "systemctl", prefix / "venv/bin/pip"):
        stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "PREFIX": str(prefix),
        "SERVICE_USER": "nobody",
        "LEMBAS_CHANNEL": "stable",
        "LEMBAS_BRANCH": "main",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    done = subprocess.run(
        ["bash", str(ROOT / "deploy/update.sh")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    head = (prefix / "app/src/lembas/__init__.py").read_text()
    return head, done.stdout + done.stderr


@pytest.mark.slow
def test_the_script_takes_the_newest_release_of_its_own_major(tmp_path):
    head, output = _run_update(tmp_path, "1.7.2", ["v1.7.2", "v1.7.3", "v2.0.0"])
    assert '"1.7.3"' in head, output
    assert "v2.0.0 is a new major version" in output


@pytest.mark.slow
def test_the_script_stays_put_when_only_a_new_major_is_newer(tmp_path):
    head, output = _run_update(tmp_path, "1.7.3", ["v1.7.3", "v2.0.0", "v2.0.1"])
    assert '"1.7.3"' in head, output
    assert "staying put" in output or "already at" in output, output
