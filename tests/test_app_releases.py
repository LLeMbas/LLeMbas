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
        for channel, version in app_releases.pins()[app].items():
            assert version is None or app_releases.parse(version), (app, channel)
        offer = said["apps"][app]
        if offer is not None and said["server"]["channel"] == "stable":
            assert offer["channel"] == "stable"


def test_the_route_answers_an_apps_token(client, db, registered):
    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="LLeMbas for Android", scopes=["app"])
    answer = client.get("/api/v2/apps", headers={"Authorization": f"Bearer {raw}"})
    assert answer.status_code == 200
    assert answer.json() == app_releases.recommended()
    assert client.get("/api/v2/apps").status_code == 401
