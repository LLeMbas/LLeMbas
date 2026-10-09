#!/usr/bin/env python3
"""Download the pinned browser libraries into the static vendor directory.

LLeMbas has no Node toolchain and loads nothing from a CDN at runtime -- a
self-hosted tool should keep working without internet access, and should not
report every user's page view to a third party. The few libraries it does use
are fetched once, here, and committed.

Integrity is enforced with vendor.lock.json. A mismatched hash aborts rather
than overwriting, and so does a name that is not in the lock at all: that is
the whole point of pinning.

    python scripts/fetch_vendor.py            # fetch and verify against the lock
    python scripts/fetch_vendor.py --update   # re-pin after a version bump
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = ROOT / "src" / "lembas" / "web" / "static" / "vendor"
LOCKFILE = Path(__file__).resolve().parent / "vendor.lock.json"

# Pinned deliberately. Bump the version, run with --update, review the diff.
PACKAGES = {
    "htmx.min.js": {
        "version": "2.0.10",
        "url": "https://unpkg.com/htmx.org@2.0.10/dist/htmx.min.js",
        "why": "Server-rendered interactivity: every swap in the app.",
    },
    "htmx-ext-sse.js": {
        "version": "2.2.4",
        "url": "https://unpkg.com/htmx-ext-sse@2.2.4/sse.js",
        "why": "Server-sent events, which is how streamed replies reach the page.",
    },
    "alpine.min.js": {
        "version": "3.15.12",
        "url": "https://unpkg.com/alpinejs@3.15.12/dist/cdn.min.js",
        "why": "Small client-only state: menus, theme toggle, composer autosize.",
    },
    "xterm.js": {
        "version": "5.5.0",
        "url": "https://unpkg.com/@xterm/xterm@5.5.0/lib/xterm.js",
        "why": "The terminal panel. Loaded only on a chat that has an SSH connection.",
    },
    "xterm.css": {
        "version": "5.5.0",
        "url": "https://unpkg.com/@xterm/xterm@5.5.0/css/xterm.css",
        "why": "Terminal layout. Its colours are overridden from tokens.css at runtime.",
    },
    "xterm-addon-fit.js": {
        "version": "0.10.0",
        "url": "https://unpkg.com/@xterm/addon-fit@0.10.0/lib/addon-fit.js",
        "why": "Sizes the terminal to the panel; without it a resize is 80x24 forever.",
    },
    # The interface face of the built-in themes. One variable file
    # per subset, the split Google Fonts and Fontsource use, so a page in
    # English never downloads the Central European letters.
    "jetbrains-mono-latin.woff2": {
        "version": "5.3.0",
        "url": "https://unpkg.com/@fontsource-variable/jetbrains-mono@5.3.0/files/jetbrains-mono-latin-wght-normal.woff2",
        "why": "JetBrains Mono (variable, 100-800): the chrome of Moria and Shire.",
    },
    "jetbrains-mono-latin-ext.woff2": {
        "version": "5.3.0",
        "url": "https://unpkg.com/@fontsource-variable/jetbrains-mono@5.3.0/files/jetbrains-mono-latin-ext-wght-normal.woff2",
        "why": "JetBrains Mono's Latin Extended half: Slovak and Czech letters.",
    },
    "jetbrains-mono-OFL.txt": {
        "version": "5.3.0",
        "url": "https://unpkg.com/@fontsource-variable/jetbrains-mono@5.3.0/LICENSE",
        "why": "The SIL Open Font License, which has to travel with the font files.",
    },
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "lembas-vendor-fetch"})
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite vendor.lock.json with the hashes just downloaded",
    )
    args = parser.parse_args()

    lock = json.loads(LOCKFILE.read_text()) if LOCKFILE.exists() else {}
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)

    new_lock: dict[str, dict[str, str]] = {}
    failed = False

    for filename, spec in PACKAGES.items():
        try:
            payload = fetch(spec["url"])
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  FAIL {filename}: {exc}", file=sys.stderr)
            failed = True
            continue

        digest = sha256(payload)
        expected = lock.get(filename, {}).get("sha256")

        if lock and not expected and not args.update:
            # A name added to PACKAGES but absent from the lock has nothing to
            # compare against, so the mismatch branch below never fires and the
            # file lands unpinned -- which is the one thing this script exists
            # to prevent. Adding a library is a --update, like bumping one.
            print(
                f"  FAIL {filename}: not in {LOCKFILE.name}\n"
                f"       Nothing to verify this download against. If the "
                f"library was added deliberately, re-run with --update.",
                file=sys.stderr,
            )
            failed = True
            continue

        if expected and digest != expected and not args.update:
            print(
                f"  FAIL {filename}: hash mismatch\n"
                f"       expected {expected}\n"
                f"       received {digest}\n"
                f"       Refusing to overwrite. If the version was bumped "
                f"deliberately, re-run with --update.",
                file=sys.stderr,
            )
            failed = True
            continue

        (VENDOR_DIR / filename).write_bytes(payload)
        new_lock[filename] = {
            "version": spec["version"],
            "url": spec["url"],
            "sha256": digest,
        }
        status = "ok" if expected == digest else ("pinned" if args.update else "new")
        print(f"  {status:>6}  {filename}  {len(payload):>8,} bytes  v{spec['version']}")

    if failed:
        print("\nOne or more downloads failed. Vendored files were not fully written.")
        return 1

    if args.update or not LOCKFILE.exists():
        LOCKFILE.write_text(json.dumps(new_lock, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {LOCKFILE.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
