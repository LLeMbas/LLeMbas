"""Which release of each LLeMbas app this server recommends (`GET /api/v2/apps`).

The apps are released together with the server, so the server knows which of
them goes with it: `app_releases.json`, beside this package, moved at every
release. An app asks its server rather than the public releases alone, so a
phone signed in to an older server is not pushed onto an app that server's
`/api/v2` does not speak yet.

**Builds.** An app released in more than one build (Android: the standard
APK, one with the notification listener, one for F-Droid) may have the others
named beside its version, each with its address and SHA-256 (`_builds`); the
app's updater reads `notifications` or `fdroid` on its entry when it is that
build, and nothing is there otherwise.

**Channels.** A stable server (a version without a suffix) recommends only a
stable app, and never a beta, whatever the file says. A beta server may also
recommend the app's beta, and recommends whichever of the two is newer. Every
version is parsed strictly (`X.Y.Z` or `X.Y.Z-beta.N`); anything else is
ignored rather than guessed at -- the same rule `services/updates.py` keeps.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

from lembas import __version__

APPS = ("android", "desktop")
STABLE, BETA = "stable", "beta"
# Where each app's releases are published, by tag.
PAGES = {
    "android": "https://github.com/LLeMbas/LLeMbas-Android/releases/tag/{tag}",
    "desktop": "https://github.com/LLeMbas/LLeMbas-Desktop/releases/tag/{tag}",
}
PINS = Path(__file__).resolve().parent.parent / "app_releases.json"
VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?$")


def parse(text: object) -> tuple[int, int, int, float] | None:
    """A sortable key for `X.Y.Z` / `X.Y.Z-beta.N` (a release above its
    betas), or None for anything else."""
    match = VERSION.match(str(text or "").strip())
    if match is None:
        return None
    a, b, c, beta = match.groups()
    return int(a), int(b), int(c), float(beta) if beta is not None else float("inf")


def channel_of(version: str) -> str:
    """`beta` for a server whose version carries a suffix, else `stable`."""
    return STABLE if VERSION.match(version or "") and "-" not in version else BETA


@cache
def pins() -> dict[str, Any]:
    return json.loads(PINS.read_text(encoding="utf-8"))


def offer(app: str, *, server_version: str = __version__,
          pinned: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The release of `app` to recommend: `{version, tag, channel, page}` --
    and, where the file names them for that version, `notifications` and
    `fdroid` (`_builds`) -- or None. A stable server never sees a beta."""
    said = (pinned if pinned is not None else pins()).get(app) or {}
    channels = (STABLE, BETA) if channel_of(server_version) == BETA else (STABLE,)
    best: tuple[tuple, str, str] | None = None
    for channel in channels:
        version = str(said.get(channel) or "").strip().removeprefix("v")
        key = parse(version)
        if key is None or (channel == STABLE and key[3] != float("inf")):
            continue  # a beta written in the stable slot is never offered as stable
        if channel == BETA and key[3] == float("inf"):
            channel = STABLE
        if best is None or key > best[0]:
            best = (key, version, channel)
    if best is None:
        return None
    _, version, channel = best
    tag = f"v{version}"
    out = {"version": version, "tag": tag, "channel": channel,
           "page": PAGES[app].format(tag=tag)}
    out.update(_builds(said, version))
    return out


# The other builds of one release that an app's own updater may want instead
# of the standard one: Android's with the notification listener
# (`notifications`) and its F-Droid flavour (`fdroid`).
BUILDS = ("notifications", "fdroid")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _builds(said: dict[str, Any], version: str) -> dict[str, dict[str, str]]:
    """The extra builds the file names for exactly this version, each
    `{url, sha256}` -- an https address and a SHA-256, or it is left out: an
    updater that installs what it is handed is given nothing half-said.

    Written under the app as `"builds": {"<version>": {"notifications":
    {"url", "sha256"}, "fdroid": {…}}}` and optional throughout: an app that
    finds no entry keeps to its standard build."""
    listed = (said.get("builds") or {}).get(version) or {}
    found: dict[str, dict[str, str]] = {}
    for name in BUILDS:
        entry = listed.get(name) if isinstance(listed, dict) else None
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        digest = str(entry.get("sha256") or "").strip().lower()
        if url.startswith("https://") and _SHA256.match(digest):
            found[name] = {"url": url, "sha256": digest}
    return found


def recommended(*, server_version: str = __version__,
                pinned: dict[str, Any] | None = None) -> dict[str, Any]:
    """`GET /api/v2/apps`: the server's own version and channel, and each app's
    recommended release (null when none)."""
    return {
        "server": {"version": server_version, "channel": channel_of(server_version)},
        "apps": {app: offer(app, server_version=server_version, pinned=pinned) for app in APPS},
    }


__all__ = ["APPS", "channel_of", "offer", "parse", "pins", "recommended"]
