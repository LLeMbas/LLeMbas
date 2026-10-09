#!/usr/bin/env python3
"""Vendor the harness spec LLeMbas shares with the LLeMbas CLI, pinned and hash-checked.

LLeMbas and the LLeMbas CLI are two projects with one
harness design: the same tools, prompt texts, model quirks and block kinds,
written down as data in the CLI's `harness/` directory. LLeMbas never runs the
CLI; it carries a copy of that data, committed under
`src/lembas/harness_spec/`, exactly as it carries its browser libraries.

The lock (`harness.lock.json`) names the CLI tag and the SHA-256 of every
file. Nothing is fetched at runtime.

    python scripts/fetch_harness.py               # verify the vendored copy against the lock
    python scripts/fetch_harness.py --update v1.0.0    # fetch that tag's harness/, re-pin
    python scripts/fetch_harness.py --check       # is there a newer spec than the pinned one?

`--update` is how a new spec version arrives: review the diff, run the tests
(`tests/test_harness_spec.py` runs every conformance case), and say in the
CHANGELOG which spec version this release carries.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_DIR = ROOT / "src" / "lembas" / "harness_spec"
LOCKFILE = Path(__file__).resolve().parent / "harness.lock.json"

FORGE = "https://git.houmeres.sk"
REPO = "LLeMbas/LLeMbas-CLI"
# Stable releases only: a `-beta.N` tag is not a spec LLeMbas pins.
RELEASE_TAG = re.compile(r"^v\d+\.\d+\.\d+$")


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "lembas-fetch-harness"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - a fixed https forge
        return response.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _remote_files(ref: str) -> list[str]:
    tree = json.loads(
        _get(f"{FORGE}/api/v1/repos/{REPO}/git/trees/{ref}?recursive=true&per_page=10000")
    )
    if tree.get("truncated"):
        raise SystemExit(
            "fetch_harness: the forge truncated the tree listing; refusing a partial copy"
        )
    paths = [
        e["path"]
        for e in tree.get("tree", [])
        if e.get("type") == "blob" and e["path"].startswith("harness/")
    ]
    if not paths:
        raise SystemExit(f"fetch_harness: {REPO} at {ref} has no harness/ directory")
    return sorted(p[len("harness/") :] for p in paths)


def _vendored() -> dict[str, str]:
    if not SPEC_DIR.is_dir():
        return {}
    return {
        str(p.relative_to(SPEC_DIR)): _sha256(p.read_bytes())
        for p in sorted(SPEC_DIR.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def verify() -> int:
    if not LOCKFILE.exists():
        print("fetch_harness: no lock; run with --update <tag>", file=sys.stderr)
        return 1
    lock = json.loads(LOCKFILE.read_text())
    have = _vendored()
    bad = [p for p, h in lock["files"].items() if have.get(p) != h]
    extra = sorted(set(have) - set(lock["files"]))
    for p in bad:
        print(f"  {'missing' if p not in have else 'changed'}: {p}")
    for p in extra:
        print(f"  not in the lock: {p}")
    if bad or extra:
        print(
            f"fetch_harness: the vendored spec does not match {lock['ref']} — never edit it "
            "by hand; change the spec in the CLI, then --update",
            file=sys.stderr,
        )
        return 1
    print(
        f"harness spec {lock['version']} from {REPO} {lock['ref']}: "
        f"{len(have)} files, all as pinned"
    )
    return 0


def update(ref: str) -> int:
    if not RELEASE_TAG.match(ref):
        raise SystemExit(f"fetch_harness: {ref!r} is not a release tag (vX.Y.Z)")
    files = _remote_files(ref)
    fetched = {p: _get(f"{FORGE}/{REPO}/raw/tag/{ref}/harness/{p}") for p in files}
    tmp = SPEC_DIR.with_name(SPEC_DIR.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    for p, data in fetched.items():
        out = tmp / p
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    shutil.rmtree(SPEC_DIR, ignore_errors=True)
    tmp.rename(SPEC_DIR)
    version = fetched["VERSION"].decode().strip()
    lock = {
        "source": f"{FORGE}/{REPO}",
        "ref": ref,
        "version": version,
        "files": {p: _sha256(d) for p, d in fetched.items()},
    }
    LOCKFILE.write_text(json.dumps(lock, indent=2) + "\n")
    print(f"vendored harness spec {version} from {REPO} {ref}: {len(fetched)} files")
    return 0


def _semver(tag: str) -> tuple[int, ...]:
    return tuple(int(x) for x in tag.lstrip("v").split("."))


def check() -> int:
    lock = json.loads(LOCKFILE.read_text())
    tags = json.loads(_get(f"{FORGE}/api/v1/repos/{REPO}/tags?limit=50"))
    stable = sorted((t["name"] for t in tags if RELEASE_TAG.match(t["name"])), key=_semver)
    if not stable:
        print("fetch_harness: no release tags found", file=sys.stderr)
        return 1
    newest = stable[-1]
    try:
        version = _get(f"{FORGE}/{REPO}/raw/tag/{newest}/harness/VERSION").decode().strip()
    except urllib.error.HTTPError:
        version = "?"
    if version == lock["version"]:
        print(
            f"harness spec {lock['version']} is current "
            f"(pinned {lock['ref']}; newest CLI release {newest})"
        )
        return 0
    print(
        f"a newer harness spec exists: {version} in CLI release {newest} "
        f"(pinned: {lock['version']} from {lock['ref']})"
    )
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--update", metavar="TAG", help="fetch that CLI tag's harness/ and re-pin"
    )
    group.add_argument(
        "--check", action="store_true", help="say whether a newer spec has been released"
    )
    args = parser.parse_args()
    if args.update:
        return update(args.update)
    if args.check:
        return check()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
