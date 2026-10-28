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
    # The file editor (CodeMirror 6). There is no single pre-bundled build of
    # CodeMirror 6 with languages, so each package is esm.sh's own bundle of it
    # with every package the others share left as a bare import ("external"),
    # resolved by the import map in files/edit.html -- one copy of
    # @codemirror/state for all of them, which CodeMirror requires. The long
    # path segment is esm.sh's encoding of that external list; the URL is
    # fixed and the bytes are pinned like everything else here. Loaded only on
    # the editor page.
    "cm-state.mjs": {
        "version": "6.7.6",
        "url": "https://esm.sh/@codemirror/state@6.7.6/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/state.bundle.mjs",
        "why": "CodeMirror: the editor's state (one shared copy for every module below).",
    },
    "cm-view.mjs": {
        "version": "6.43.15",
        "url": "https://esm.sh/@codemirror/view@6.43.15/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/view.bundle.mjs",
        "why": "CodeMirror: the editor's DOM view.",
    },
    "cm-language.mjs": {
        "version": "6.13.1",
        "url": "https://esm.sh/@codemirror/language@6.13.1/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/language.bundle.mjs",
        "why": "CodeMirror: syntax trees and highlighting.",
    },
    "cm-commands.mjs": {
        "version": "6.11.1",
        "url": "https://esm.sh/@codemirror/commands@6.11.1/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/commands.bundle.mjs",
        "why": "CodeMirror: key bindings and editing commands.",
    },
    "cm-search.mjs": {
        "version": "6.7.2",
        "url": "https://esm.sh/@codemirror/search@6.7.2/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/search.bundle.mjs",
        "why": "CodeMirror: find and replace.",
    },
    "cm-autocomplete.mjs": {
        "version": "6.20.4",
        "url": "https://esm.sh/@codemirror/autocomplete@6.20.4/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/autocomplete.bundle.mjs",
        "why": "CodeMirror: completion, bracket closing.",
    },
    "cm-lint.mjs": {
        "version": "6.9.7",
        "url": "https://esm.sh/@codemirror/lint@6.9.7/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/lint.bundle.mjs",
        "why": "CodeMirror: diagnostics (needed by basicSetup).",
    },
    "lezer-common.mjs": {
        "version": "1.5.4",
        "url": "https://esm.sh/@lezer/common@1.5.4/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/common.bundle.mjs",
        "why": "Lezer: the parse tree the language modules share.",
    },
    "lezer-highlight.mjs": {
        "version": "1.2.5",
        "url": "https://esm.sh/@lezer/highlight@1.2.5/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/highlight.bundle.mjs",
        "why": "Lezer: highlight tags.",
    },
    "lezer-lr.mjs": {
        "version": "1.4.11",
        "url": "https://esm.sh/@lezer/lr@1.4.11/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/lr.bundle.mjs",
        "why": "Lezer: the LR parser the grammars run on.",
    },
    "cm-codemirror.mjs": {
        "version": "6.0.2",
        "url": "https://esm.sh/codemirror@6.0.2/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/codemirror.bundle.mjs",
        "why": "CodeMirror: basicSetup.",
    },
    "cm-lang-markdown.mjs": {
        "version": "6.5.2",
        "url": "https://esm.sh/@codemirror/lang-markdown@6.5.2/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/lang-markdown.bundle.mjs",
        "why": "CodeMirror: Markdown.",
    },
    "cm-lang-python.mjs": {
        "version": "6.2.1",
        "url": "https://esm.sh/@codemirror/lang-python@6.2.1/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/lang-python.bundle.mjs",
        "why": "CodeMirror: Python.",
    },
    "cm-lang-javascript.mjs": {
        "version": "6.2.5",
        "url": "https://esm.sh/@codemirror/lang-javascript@6.2.5/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/lang-javascript.bundle.mjs",
        "why": "CodeMirror: JavaScript and TypeScript.",
    },
    "cm-lang-html.mjs": {
        "version": "6.4.12",
        "url": "https://esm.sh/@codemirror/lang-html@6.4.12/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/lang-html.bundle.mjs",
        "why": "CodeMirror: HTML.",
    },
    "cm-lang-css.mjs": {
        "version": "6.3.1",
        "url": "https://esm.sh/@codemirror/lang-css@6.3.1/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/lang-css.bundle.mjs",
        "why": "CodeMirror: CSS.",
    },
    "cm-lang-json.mjs": {
        "version": "6.0.2",
        "url": "https://esm.sh/@codemirror/lang-json@6.0.2/X-ZUBjb2RlbWlycm9yL2F1dG9jb21wbGV0ZSxAY29kZW1pcnJvci9jb21tYW5kcyxAY29kZW1pcnJvci9sYW5nLWNzcyxAY29kZW1pcnJvci9sYW5nLWh0bWwsQGNvZGVtaXJyb3IvbGFuZy1qYXZhc2NyaXB0LEBjb2RlbWlycm9yL2xhbmd1YWdlLEBjb2RlbWlycm9yL2xpbnQsQGNvZGVtaXJyb3Ivc2VhcmNoLEBjb2RlbWlycm9yL3N0YXRlLEBjb2RlbWlycm9yL3ZpZXcsQGxlemVyL2NvbW1vbixAbGV6ZXIvaGlnaGxpZ2h0LEBsZXplci9scg/es2022/lang-json.bundle.mjs",
        "why": "CodeMirror: JSON.",
    },
    # Highlighted code in the file viewer. The browser build with the common
    # languages; its colours come from tokens.css (files.css), not a theme file.
    "highlight.min.js": {
        "version": "11.12.0",
        "url": "https://unpkg.com/@highlightjs/cdn-assets@11.12.0/highlight.min.js",
        "why": "Highlighted code in the file viewer and the show card.",
    },
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "lembas-vendor-fetch"})
    with urllib.request.urlopen(request, timeout=60) as response:
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
