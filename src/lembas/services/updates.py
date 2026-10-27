"""What is running here, what is released, and how to get from one to the other.

## Why this exists

Updating meant a shell on the box. That is fine for whoever built it and wrong
for everybody else: an instance somebody installed from `deploy/install.sh` and
handed over has an administrator who can configure every part of it and cannot
tell whether it is three releases behind.

## Two channels, because a branch tip is not a release

- **stable** — the newest version tag. "Running 1.0.0, 1.1.0 available."
- **edge** — the branch tip, whatever was pushed five minutes ago.

That distinction is the whole reason this is not just a commit comparison. A
commit sha tells nobody anything, and following `main` means deploying work that
may be half finished — the right default for whoever is building it and the wrong
one for whoever is running it. Both channels are the same machinery asking one
different question: *which ref am I comparing against*.

## A channel never crosses a major version

The stable channel offers only releases of the **major version that is
running**: a 1.x instance is offered 1.x releases and nothing else, however new
a 2.0 is. A new major may change the data underneath -- 2.0 does not carry a 1.x
database over -- so moving to one is a decision somebody makes by hand, never a
button press that happened to land after a release. The page says when a newer
major exists, so the decision is never missed either.

`deploy/update.sh` applies the same rule, because it is the helper and not this
module that chooses what is checked out. Root runs its *own copy* of that
script, installed by `install.sh`, so a host whose copy predates the rule would
still take the newest tag. `helper_guarded` reads that copy and the page and
`updates_auto.apply` refuse to request an update while it could cross.

## Git plumbing, never a forge API

Tags are read with `git`, over the transport the checkout already has. Not the
Gitea (or GitHub, or GitLab) API, for three reasons in the order they bite:

- **It needs a token on the deployment host.** A credential that can reach the
  repository, sitting on a box, to answer a read-only question about version
  numbers. That is a bad trade.
- **It is forge-specific.** A fork on GitHub, or a bare repository on a NAS,
  would get nothing.
- **It breaks.** Checked against the Gitea this is developed on: `tea whoami`
  works and `tea releases list` returns a 500 from a server-side panic about
  token scopes. An update page resting on that endpoint would have shipped
  broken.

Release notes still travel: an **annotated** tag carries its message in the tag
object, and `git for-each-ref` reads it with no API anywhere.

## The button cannot do the work, and that is the design

The service runs as an unprivileged account. It cannot restart itself, it cannot
write outside its own prefix, and it should not be able to: a web application
that can restart its own service is one whose worst day is much worse.

So the button **writes a file**, and an opt-in systemd `.path` unit watching that
file runs `deploy/update.sh` as root. Three things follow, all deliberate:

- **The request file carries nothing.** No ref, no channel, no arguments. The
  helper runs the update script with the channel and branch *it* was configured
  with, so an administrator of the web UI cannot choose what gets deployed —
  only *that* the configured channel gets deployed.
- **It is opt-in.** Without the helper the page says so and shows the command,
  which is the honest degradation and the same shape the search extras
  already have.
- **Nothing here runs a shell.** Every subprocess is a fixed argv with
  `shell=False`, and the only variables in any of them are the branch and the
  channel, which come from the environment rather than from a request.

## On "nothing executes on this machine"

That rule is about *agent chats* — a model's commands, on a host somebody chose.
This is an administrator pressing a button to run `git`, with no model anywhere
near it and no input from a request in the argv. Worth stating rather than
leaving somebody to notice the tension.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from lembas import __version__
from lembas.config import settings

log = logging.getLogger(__name__)

# Long enough for a fetch over a slow link, short enough that a page does not
# appear to hang. A fetch that takes longer than this is a network problem, and
# saying so is more use than waiting.
GIT_TIMEOUT = 30.0

CHANNEL_STABLE = "stable"
CHANNEL_EDGE = "edge"
CHANNELS = (CHANNEL_STABLE, CHANNEL_EDGE)

# What counts as a release tag on the stable channel. `v1.2.3` or `1.2.3`, and
# **nothing with a suffix**: `v1.1.0-rc1` sorts above `v1.1.0` under git's
# version sort, so accepting it would step a stable instance onto a release
# candidate on the strength of a hyphen. A prerelease is something somebody opts
# into by name, not something a channel drifts onto.
RELEASE_TAG = re.compile(r"^v?\d+\.\d+\.\d+$")

# The file the button writes. Inside the data directory, which is the one place
# the service account can write, and named so that finding it in a backup says
# what it was.
REQUEST_NAME = "update-requested"

# Written by `install.sh` when the helper is installed. A marker rather than
# asking systemd, because `systemctl is-enabled` means a subprocess on every
# page render to answer a question that changes once.
#
# It carries the channel the helper was installed with, because that is declared
# in **two** places -- here for the helper, and `lembas.env` for this process --
# and `install.sh` writing both together is the only thing keeping them in step.
# Edit one by hand and the page says `stable` while the button deploys `edge`,
# with nothing anywhere disagreeing. Reading it back is what lets the page say
# so. Empty on every host installed before this, which reads as "unknown" rather
# than as a mismatch.
#
# Display only. The service account can write this file, so a compromised
# process could lie about what the helper will do -- but not change it, because
# the helper's own channel is baked into its unit under /etc, which this account
# cannot touch. Lying about the channel is a much smaller thing than choosing it.
MARKER_NAME = ".update-helper"

# Where `install.sh` puts root's copy of `deploy/update.sh`, and the unit that
# names it. Read, never run: only to answer whether that copy keeps to one major
# version (`helper_guarded`). The unit's `ExecStart` wins over the default, so a
# helper installed somewhere else is still found.
HELPER_UNIT = Path("/etc/systemd/system/lembas-update.service")
HELPER_SCRIPT = Path("/usr/local/lib/lembas/update.sh")
# The line an `update.sh` that keeps to the running major carries. A marker
# rather than parsing the script: what it does is the script's business, and
# whether it says it does is one substring.
GUARD_MARKER = "lembas-update: same-major"

# `%H` sha, `%s` subject, `%cI` date, split on a unit separator rather than a
# space: a commit subject contains spaces, and every other separator anybody
# reaches for is one a subject may also contain.
FORMAT = "--format=%H\x1f%s\x1f%cI"


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    when: str = ""

    @property
    def short(self) -> str:
        return self.sha[:7]


@dataclass(frozen=True)
class Target:
    """A thing this instance could be at: a release, or a branch tip."""

    ref: str = ""
    # What to call it to a person. A tag name on stable, a short sha on edge --
    # which is why this is stored rather than derived: the two channels answer
    # "what version is this?" with different kinds of thing.
    label: str = ""
    sha: str = ""
    subject: str = ""
    notes: str = ""

    @property
    def short(self) -> str:
        return self.sha[:7]


@dataclass(frozen=True)
class Release:
    """One release between what runs and what is offered, with its notes."""

    tag: str
    label: str
    notes: str = ""


@dataclass(frozen=True)
class State:
    """Everything the page shows, resolved in one go."""

    version: str = __version__
    channel: str = CHANNEL_STABLE
    branch: str = "main"
    checkout: str = ""
    # What is running: `git describe`, so "1.0.0" at a tag and "1.0.0-7-gd4f56d"
    # seven commits past one. A bare sha only when there is no tag anywhere.
    running: str = ""
    head: Commit | None = None
    available: Target | None = None
    behind: list[Commit] = field(default_factory=list)
    dirty: bool = False
    # `git describe` said we are exactly at a tag whose name disagrees with
    # `__version__`. Cheap to check -- no subprocess, both are already in hand --
    # and it is the one thing about a release that is worth catching: a tag cut
    # before the version bump names a release nobody can identify afterwards.
    version_mismatch: str = ""
    # What the code at that tag declares, which the note names beside the tag.
    version_declared: str = ""
    helper: bool = False
    # What the helper will actually deploy, when it said. Empty means it did not
    # -- an older install -- and the page then says nothing rather than claiming
    # agreement it cannot check.
    helper_channel: str = ""
    requested: bool = False
    error: str = ""
    # Every release after the running one up to the offered one, newest first,
    # each with its notes: an update that skips three releases shows
    # all three, not just the last.
    between: list[Release] = field(default_factory=list)
    # The harness spec the offered release carries, from its own lock file.
    target_spec: str = ""
    # The newest release of a *later* major version, which is never offered
    # (see "A channel never crosses a major version" above), or "".
    newer_major: str = ""

    @property
    def channel_mismatch(self) -> bool:
        """The page and the helper disagree about what would be deployed.

        Only answerable when the helper said which channel it has. Somebody
        edited one of the two places and not the other, and without this the
        button would quietly deploy something other than what the page named.
        """
        return bool(self.helper_channel) and self.helper_channel != self.channel

    @property
    def is_git(self) -> bool:
        return bool(self.checkout)

    @property
    def up_to_date(self) -> bool:
        return bool(self.head and self.available and self.head.sha == self.available.sha)

    @property
    def no_releases(self) -> bool:
        """Stable, and nothing tagged yet. Worth its own answer: the page has to
        say "nothing has been released" rather than "up to date", which would be
        true of an empty repository and useless."""
        return self.channel == CHANNEL_STABLE and self.available is None


def checkout_dir() -> Path | None:
    """The git checkout this is running from, or None.

    `src/lembas/services/updates.py` upwards three times is the repository root
    for an editable install, which is what every deployment here is. A wheel in
    site-packages has no `.git` above it and answers None, which the page reads
    as "this was not installed from a checkout" rather than as an error -- and is
    what a container correctly reports, since `.dockerignore` excludes `.git`.
    """
    root = Path(__file__).resolve().parents[3]
    return root if (root / ".git").exists() else None


def _git(args: list[str], *, cwd: Path, timeout: float = GIT_TIMEOUT) -> tuple[int, str]:
    """One git command, with no shell anywhere near it.

    `shell=False` and a fixed argv, so nothing here can be made to run something
    else by what it is given. Returns the code and the output together because
    every caller wants both and none of them wants an exception: git failing is
    a thing to report on the page, not a 500.
    """
    try:
        done = subprocess.run(  # fixed argv, no shell, admin-only
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return 127, "git is not installed on this machine."
    except subprocess.TimeoutExpired:
        return 124, f"git took longer than {int(timeout)}s."
    except Exception as exc:  # a broken git is a message, not a crash
        log.debug("git %s failed", args, exc_info=True)
        return 1, str(exc)
    return done.returncode, (done.stdout or done.stderr or "").strip()


def _commit(line: str) -> Commit | None:
    parts = line.split("\x1f")
    if len(parts) < 2 or not parts[0]:
        return None
    return Commit(sha=parts[0], subject=parts[1], when=parts[2] if len(parts) > 2 else "")


# --- Configuration ---------------------------------------------------------------
def channel_name() -> str:
    wanted = (settings.update_channel or CHANNEL_STABLE).strip().lower()
    return wanted if wanted in CHANNELS else CHANNEL_STABLE


def branch_name() -> str:
    return settings.update_branch or "main"


def helper_installed() -> bool:
    return (settings.data_dir / MARKER_NAME).exists()


def helper_channel() -> str:
    """The channel the helper was installed with, or "" if it did not say."""
    try:
        return (settings.data_dir / MARKER_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def helper_script() -> Path:
    """Root's copy of the update script: the path the helper unit runs, or the
    one `install.sh` uses when the unit cannot be read."""
    try:
        for line in HELPER_UNIT.read_text(encoding="utf-8").splitlines():
            if line.startswith("ExecStart="):
                return Path(line.split()[-1])
    except (OSError, IndexError):
        pass
    return HELPER_SCRIPT


def helper_guarded() -> bool:
    """Whether the installed helper keeps to the running major version.

    False when its copy carries no marker -- one installed by an older release,
    which takes the newest tag whatever its major -- and False when it cannot be
    read, because "not known to be safe" is the answer that keeps a 1.x host on
    1.x.
    """
    try:
        return GUARD_MARKER in helper_script().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def request_path() -> Path:
    return settings.data_dir / REQUEST_NAME


def pending() -> bool:
    return request_path().exists()


# --- Resolving what is available ---------------------------------------------------
def release_tags(root: Path) -> list[str]:
    """Every release tag, newest first.

    `--sort=-v:refname` is git's own version sort, so `v1.10.0` comes above
    `v1.9.0` -- which a lexical sort gets wrong, and gets wrong silently the
    first time a project reaches ten of anything.
    """
    code, output = _git(["tag", "--list", "--sort=-v:refname"], cwd=root)
    if code != 0:
        return []
    return [line.strip() for line in output.splitlines() if RELEASE_TAG.match(line.strip())]


def major_of(version: str) -> int | None:
    """The major version of `1.7.2`, `v1.7.2` or `2.0.0-beta.1`, or None."""
    match = re.match(r"^v?(\d+)\.", (version or "").strip())
    return int(match.group(1)) if match else None


def same_major(tags: list[str], current: str = __version__) -> list[str]:
    """The tags of `current`'s major version, in their order. A version whose
    major cannot be read keeps them all: there is no line to hold it to."""
    major = major_of(current)
    if major is None:
        return list(tags)
    return [tag for tag in tags if major_of(tag) == major]


def newer_major(tags: list[str], current: str = __version__) -> str:
    """The newest release tag of a later major than `current`'s, or ""."""
    major = major_of(current)
    if major is None:
        return ""
    for tag in tags:
        found = major_of(tag)
        if found is not None and found > major:
            return tag
    return ""


# Where a signature block starts in `%(contents)`. **Both**, because which one
# appears depends on `gpg.format` -- `openpgp` writes the first and `ssh` the
# second, and a release tag signed either way would otherwise render forty lines
# of base64 as its release notes. `%(contents:subject)` and `%(contents:body)`
# would avoid this, but they also throw away every blank line in a body written
# as a list, which is what release notes are.
_SIGNATURE_HEADERS = ("-----BEGIN PGP SIGNATURE-----", "-----BEGIN SSH SIGNATURE-----")


def _notes_for(root: Path, tag: str) -> str:
    """An annotated tag's message: the release notes, travelling inside git.

    Empty for a lightweight tag, which is the honest answer -- there is nothing
    attached to one.
    """
    code, output = _git(
        ["for-each-ref", "--format=%(contents)", f"refs/tags/{tag}"], cwd=root
    )
    if code != 0:
        return ""
    for header in _SIGNATURE_HEADERS:
        output = output.split(header)[0]
    return output.strip()


def resolve_target(root: Path, channel: str, branch: str) -> Target | None:
    """What this channel says the instance should be at.

    On stable, the newest release **of the running major version**: a newer
    major is never offered here (`State.newer_major` names it instead).

    Reads **local refs only**. On stable that is the tags the last fetch brought
    down; on edge it is `origin/<branch>`, a file under `.git/refs/remotes/`.
    Neither touches the network -- `read(fetch=True)` is what refreshes them, and
    keeping that split is why opening the page costs nothing.
    """
    if channel == CHANNEL_STABLE:
        # Only the running major's releases: a new major is installed by hand.
        tags = same_major(release_tags(root))
        if not tags:
            return None
        tag = tags[0]
        code, line = _git(["log", "-1", FORMAT, f"refs/tags/{tag}"], cwd=root)
        commit = _commit(line) if code == 0 else None
        if commit is None:
            return None
        return Target(
            ref=tag,
            label=tag.removeprefix("v"),
            sha=commit.sha,
            subject=commit.subject,
            notes=_notes_for(root, tag),
        )

    code, line = _git(["log", "-1", FORMAT, f"origin/{branch}"], cwd=root)
    commit = _commit(line) if code == 0 else None
    if commit is None:
        return None
    return Target(
        ref=f"origin/{branch}", label=commit.sha[:7], sha=commit.sha, subject=commit.subject
    )


def releases_between(root: Path, current: str, target: str) -> list[Release]:
    """Release tags newer than `current` up to and including `target`, newest
    first, each with its notes. Local refs only, like `resolve_target`."""

    def key(tag: str) -> tuple[int, ...]:
        return tuple(int(x) for x in tag.removeprefix("v").split("."))

    try:
        have, want = key(current), key(target)
    except ValueError:
        return []
    out = []
    for tag in release_tags(root):
        try:
            version = key(tag)
        except ValueError:
            continue
        if have < version <= want:
            out.append(Release(tag=tag, label=tag.removeprefix("v"), notes=_notes_for(root, tag)))
    return out


def spec_at(root: Path, ref: str) -> str:
    """The harness spec version a release pins (scripts/harness.lock.json at that
    ref), or "" when it pins none -- an older release."""
    import json

    code, output = _git(["show", f"{ref}:scripts/harness.lock.json"], cwd=root)
    if code != 0:
        return ""
    try:
        return str(json.loads(output).get("version") or "")
    except ValueError:
        return ""


_VERSION_LINE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def version_at(root: Path, ref: str) -> str:
    """The version the code at `ref` declares (`src/lembas/__init__.py`), or ""
    when it cannot be read."""
    code, output = _git(["show", f"{ref}:src/lembas/__init__.py"], cwd=root)
    if code != 0:
        return ""
    match = _VERSION_LINE.search(output)
    return match.group(1) if match else ""


def _describe(root: Path) -> str:
    """What is running, in the most human terms git can manage.

    `1.0.0` exactly at a tag, `1.0.0-7-gd4f56d` seven commits past one, and a
    bare short sha when nothing has ever been tagged. That last case is why
    `--always` is there: without it this fails outright on a repository with no
    tags, which is every repository before its first release.

    The leading `v` comes off, because git answers with the **tag's name** and
    tags here are `v1.0.0` while `__version__` is `1.0.0`. Without this the page
    read "v1.0.0 (reports 1.0.0)" -- a note whose whole purpose is to flag a tag
    cut before a version bump, firing on two spellings of the same version.
    `resolve_target` has always stripped it for
    the same reason, and this docstring already promised the stripped form.
    """
    code, output = _git(["describe", "--tags", "--always", "--dirty="], cwd=root)
    return output.removeprefix("v") if code == 0 else ""


def read(*, fetch: bool = False) -> State:
    """What is running and what is available.

    `fetch` is the network half and is off by default: this is called to render a
    page, and a page that made a network request on every load would be one
    somebody stops opening. The Check button passes it.
    """
    channel, branch = channel_name(), branch_name()
    root = checkout_dir()
    base = {
        "channel": channel,
        "branch": branch,
        "helper": helper_installed(),
        "helper_channel": helper_channel(),
        "requested": pending(),
    }
    if root is None:
        return State(**base)

    code, head_line = _git(["log", "-1", FORMAT], cwd=root)
    if code != 0:
        return State(**base, checkout=str(root), error=head_line)

    error = ""
    if fetch:
        # `--tags` and `--force`: without the first, stable never learns about a
        # release; without the second, a tag that was moved (which happens to a
        # release that was cut wrong) is refused rather than updated, and the
        # instance sits on the old one with no sign of why.
        code, output = _git(
            ["fetch", "--quiet", "--tags", "--force", "origin", branch], cwd=root
        )
        if code != 0:
            error = f"Could not reach the remote: {output}"

    head = _commit(head_line)
    target = resolve_target(root, channel, branch)
    running = _describe(root)

    behind: list[Commit] = []
    if target is not None and head is not None and target.sha != head.sha:
        code, output = _git(["log", FORMAT, f"HEAD..{target.sha}"], cwd=root)
        if code == 0 and output:
            behind = [c for line in output.splitlines() if (c := _commit(line))]

    code, status = _git(["status", "--porcelain"], cwd=root)
    dirty = bool(status.strip()) if code == 0 else False

    # Exactly at a tag whose name disagrees with the version the code at that
    # tag declares. Against the checkout, not this process: during an update
    # the checkout is at the new tag a moment before the service restarts, and
    # a page drawn in that moment by the old process compared the new tag with
    # its own old `__version__` and blamed a release that was cut correctly
    # (1.5.0, reported as "tagged 1.5.0 but reports 1.3.0"). `__version__` is
    # only the fallback when the file cannot be read at HEAD.
    mismatch = declared = ""
    if RELEASE_TAG.match(running):
        declared = version_at(root, "HEAD") or __version__
        if running != declared:
            mismatch = running

    between: list[Release] = []
    target_spec = ""
    offered = target is not None and head is not None and target.sha != head.sha
    if offered and channel == CHANNEL_STABLE:
        between = releases_between(root, __version__, target.ref)
        target_spec = spec_at(root, target.ref)

    newer = newer_major(release_tags(root)) if channel == CHANNEL_STABLE else ""

    return State(
        **base,
        newer_major=newer.removeprefix("v"),
        between=between,
        target_spec=target_spec,
        checkout=str(root),
        running=running,
        head=head,
        available=target,
        behind=behind,
        dirty=dirty,
        version_mismatch=mismatch,
        version_declared=declared if mismatch else "",
        error=error,
    )


# --- Asking for it -----------------------------------------------------------------
def request_update(who: str = "") -> str:
    """Ask the helper to update. Returns a message fit to show.

    The file is written with the requester's email in it **for the log**, and
    that is all it carries. Nothing in it reaches the helper's command line: the
    helper runs the update script with its own configured channel and branch, so
    being able to press this button is not being able to choose what is deployed.
    """
    path = request_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"requested by {who or 'an administrator'}\n", encoding="utf-8")
    except OSError as exc:
        log.warning("could not write the update request: %s", exc)
        return f"Could not write {path}: {exc}"
    log.info("update requested by %s", who or "an administrator")
    return ""


def clear_request() -> None:
    """Forget a request. For the page's Cancel, and for tests."""
    request_path().unlink(missing_ok=True)


def manual_command() -> str:
    """What to run by hand when the helper is not installed."""
    root = checkout_dir()
    return f"sudo {root}/deploy/update.sh" if root else "sudo ./deploy/update.sh"


__all__ = [
    "CHANNELS",
    "CHANNEL_EDGE",
    "CHANNEL_STABLE",
    "GIT_TIMEOUT",
    "GUARD_MARKER",
    "MARKER_NAME",
    "RELEASE_TAG",
    "REQUEST_NAME",
    "Commit",
    "Release",
    "State",
    "Target",
    "branch_name",
    "channel_name",
    "checkout_dir",
    "clear_request",
    "helper_channel",
    "helper_guarded",
    "helper_script",
    "helper_installed",
    "major_of",
    "manual_command",
    "newer_major",
    "pending",
    "read",
    "release_tags",
    "releases_between",
    "spec_at",
    "request_path",
    "request_update",
    "resolve_target",
    "same_major",
]
