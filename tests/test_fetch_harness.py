"""`scripts/fetch_harness.py`: which tags it pins, and how it orders them.

Nothing here reaches the network -- `_get` and `_remote_files` are replaced.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fetch_harness():
    spec = importlib.util.spec_from_file_location(
        "fetch_harness", ROOT / "scripts" / "fetch_harness.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("tag", ["v1.5.0", "v2.0.0-beta.1", "v10.20.30-beta.12"])
def test_a_release_tag_is_accepted(fetch_harness, tag):
    assert fetch_harness.RELEASE_TAG.match(tag)


@pytest.mark.parametrize(
    "tag", ["1.5.0", "v1.5", "v2.0.0-beta", "v2.0.0-rc.1", "v2.0.0-beta.1x", "main"]
)
def test_anything_else_is_refused(fetch_harness, tag):
    assert not fetch_harness.RELEASE_TAG.match(tag)


def test_update_refuses_a_non_release_tag_before_fetching(fetch_harness, monkeypatch):
    def never(*_a, **_k):
        raise AssertionError("fetched")

    monkeypatch.setattr(fetch_harness, "_remote_files", never)
    with pytest.raises(SystemExit, match=r"vX\.Y\.Z-beta\.N"):
        fetch_harness.update("v2.0.0-rc.1")


def test_update_takes_a_beta_tag(fetch_harness, monkeypatch):
    class Fetched(Exception):
        pass

    def remote_files(ref):
        raise Fetched(ref)

    monkeypatch.setattr(fetch_harness, "_remote_files", remote_files)
    with pytest.raises(Fetched, match="v2.0.0-beta.3"):
        fetch_harness.update("v2.0.0-beta.3")


def test_tags_sort_in_semver_order(fetch_harness):
    tags = ["v2.0.0", "v2.0.0-beta.10", "v1.9.0", "v2.0.0-beta.2", "v2.1.0-beta.1"]
    assert sorted(tags, key=fetch_harness._semver) == [
        "v1.9.0",
        "v2.0.0-beta.2",
        "v2.0.0-beta.10",
        "v2.0.0",
        "v2.1.0-beta.1",
    ]


def _check(fetch_harness, monkeypatch, tmp_path, pinned, tags):
    lock = tmp_path / "harness.lock.json"
    lock.write_text(json.dumps({"ref": pinned, "version": "1.0.0", "files": {}}))
    monkeypatch.setattr(fetch_harness, "LOCKFILE", lock)
    asked = []

    def get(url):
        if url.endswith("/tags?limit=50"):
            return json.dumps([{"name": t} for t in tags]).encode()
        asked.append(url)
        return b"9.9.9"

    monkeypatch.setattr(fetch_harness, "_get", get)
    fetch_harness.check()
    return asked[0].split("/raw/tag/")[1].split("/")[0]


def test_check_offers_a_stable_pin_no_beta(fetch_harness, monkeypatch, tmp_path):
    tags = ["v1.5.0", "v1.6.0", "v2.0.0-beta.1"]
    assert _check(fetch_harness, monkeypatch, tmp_path, "v1.5.0", tags) == "v1.6.0"


def test_check_follows_betas_from_a_beta_pin(fetch_harness, monkeypatch, tmp_path):
    tags = ["v1.6.0", "v2.0.0-beta.2", "v2.0.0-beta.10", "main"]
    assert _check(fetch_harness, monkeypatch, tmp_path, "v2.0.0-beta.2", tags) == (
        "v2.0.0-beta.10"
    )
