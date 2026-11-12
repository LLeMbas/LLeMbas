"""`GET /api/v2/apps`: which Android and Desktop release this server recommends.
A beta server may name a beta app; a stable server never does."""

from __future__ import annotations

from sqlalchemy import select

from lembas import __version__
from lembas.db.models import User
from lembas.security import api_tokens
from lembas.services import app_releases

PINS = {"android": {"stable": "1.0.0", "beta": "1.1.0-beta.2"},
        "desktop": {"stable": "1.2.0", "beta": "1.2.0-beta.4"}}


def _version(server: str, app: str, pins=PINS):
    said = app_releases.offer(app, server_version=server, pinned=pins)
    return (said["version"], said["channel"]) if said else None


def test_a_stable_server_never_offers_a_beta():
    assert _version("2.0.0", "android") == ("1.0.0", "stable")
    only_beta = {"android": {"stable": None, "beta": "1.1.0-beta.2"}}
    assert _version("2.0.0", "android", only_beta) is None
    # A beta written where the stable goes is still a beta.
    assert _version("2.0.0", "android", {"android": {"stable": "1.1.0-beta.2"}}) is None


def test_a_beta_server_offers_the_newer_of_the_two():
    assert _version("2.1.0-beta.3", "android") == ("1.1.0-beta.2", "beta")
    # The stable 1.2.0 is above every 1.2.0 beta.
    assert _version("2.1.0-beta.3", "desktop") == ("1.2.0", "stable")


def test_nonsense_is_never_offered():
    for bad in ("latest", "1.0", "1.0.0-rc.1", "", None):
        pins = {"android": {"stable": bad, "beta": bad}}
        assert _version("2.1.0-beta.3", "android", pins) is None
    assert _version("2.1.0-beta.3", "ios") is None


def test_the_shipped_pins_parse_and_fit_this_server():
    said = app_releases.recommended()
    assert said["server"] == {"version": __version__,
                              "channel": app_releases.channel_of(__version__)}
    for app in app_releases.APPS:
        for channel in (app_releases.STABLE, app_releases.BETA):
            version = app_releases.pins()[app].get(channel)
            assert version is None or app_releases.parse(version), (app, channel)
        # Any other build named is named whole, for a version that parses.
        for version, builds in (app_releases.pins()[app].get("builds") or {}).items():
            assert app_releases.parse(version), (app, version)
            assert set(builds) <= set(app_releases.BUILDS), (app, version)
            assert app_releases._builds(app_releases.pins()[app], version).keys() == builds.keys()
        offer = said["apps"][app]
        if offer is not None and said["server"]["channel"] == "stable":
            assert offer["channel"] == "stable"


def test_the_other_builds_of_the_offered_release_ride_along():
    """Android ships three APKs of one release: the standard one, one with the
    notification listener and one for F-Droid. Its updater reads its own
    build's entry beside the release; an entry is handed over only whole (an
    https address and a SHA-256) and only for the version that is offered."""
    good = {"url": "https://example.org/app-notifications.apk", "sha256": "a" * 64}
    pins = {"android": {
        "stable": None, "beta": "1.0.0-beta.8",
        "builds": {
            "1.0.0-beta.8": {
                "notifications": good,
                "fdroid": {"url": "https://example.org/app-fdroid.apk", "sha256": "B" * 64},
                "sideload": good,  # not a build the apps know
            },
            "1.0.0-beta.7": {"notifications": {"url": "https://example.org/old.apk",
                                                "sha256": "c" * 64}},
        },
    }}
    said = app_releases.offer("android", server_version="2.0.0-beta.11", pinned=pins)
    assert said["version"] == "1.0.0-beta.8"
    assert said["notifications"] == good
    assert said["fdroid"] == {"url": "https://example.org/app-fdroid.apk", "sha256": "b" * 64}
    assert "sideload" not in said

    for bad in ({"url": "http://example.org/x.apk", "sha256": "a" * 64},
                {"url": "https://example.org/x.apk", "sha256": "abc"},
                {"url": "https://example.org/x.apk"}, "x", None):
        pins["android"]["builds"]["1.0.0-beta.8"] = {"notifications": bad}
        said = app_releases.offer("android", server_version="2.0.0-beta.11", pinned=pins)
        assert "notifications" not in said, bad
    # None named: the answer is what it always was.
    plain = app_releases.offer("android", server_version="2.1.0-beta.3", pinned=PINS)
    assert set(plain) == {"version", "tag", "channel", "page"}


def test_the_route_answers_an_apps_token(client, db, registered):
    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="LLeMbas for Android", scopes=["app"])
    answer = client.get("/api/v2/apps", headers={"Authorization": f"Bearer {raw}"})
    assert answer.status_code == 200
    assert answer.json() == app_releases.recommended()
    assert client.get("/api/v2/apps").status_code == 401
