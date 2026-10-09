"""The built-in palettes stay readable, measured from tokens.css itself.

Contrast checked by eye ships wrong: `--ink-faint` once sat under 4.5:1 in both
themes before anyone noticed. These are the WCAG 2.1 ratios for the pairs that
carry text, read from the same file the browser reads, so a palette change
that makes hint text unreadable fails here rather than on somebody's dim
laptop.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TOKENS = Path(__file__).resolve().parent.parent / "src/lembas/web/static/css/tokens.css"

SELECTORS = {
    "moria": ':root,\n:root[data-theme="moria"] {',
    "shire": ':root[data-theme="shire"],\n:root[data-base="shire"] {',
}

# (foreground, background, minimum). Body text gets AAA; everything that is
# still text gets AA; the edge the blocky look is drawn in gets the 3:1 WCAG
# asks of a component boundary.
PAIRS = [
    ("ink", "bg", 7.0),
    ("ink", "surface", 7.0),
    ("ink", "bubble-user", 7.0),
    ("ink-muted", "bg", 4.5),
    ("ink-muted", "surface", 4.5),
    ("ink-muted", "surface-raised", 4.5),
    ("ink-faint", "bg", 4.5),
    ("ink-faint", "surface", 4.5),
    ("accent-ink", "accent", 4.5),
    ("accent", "bg", 4.5),
    ("accent", "surface", 4.5),
    ("leaf", "surface", 4.5),
    ("danger", "surface", 4.5),
    ("success", "surface", 4.5),
    ("warning", "surface", 4.5),
    ("border-strong", "surface", 3.0),
]


def palette(theme: str) -> dict[str, str]:
    css = TOKENS.read_text()
    start = css.index(SELECTORS[theme])
    end = css.index("\n}\n", start)
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})\s*;", css[start:end]))


def luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    h = hex_colour.lstrip("#")
    r, g, b = (channel(int(h[i : i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", ["moria", "shire"])
@pytest.mark.parametrize("fg, bg, floor", PAIRS, ids=[f"{f}-on-{b}" for f, b, _ in PAIRS])
def test_text_is_readable(theme, fg, bg, floor):
    colours = palette(theme)
    measured = ratio(colours[fg], colours[bg])
    assert measured >= floor, f"{theme}: --{fg} on --{bg} is {measured:.2f}:1, needs {floor}:1"


def test_the_parser_found_both_palettes():
    """A guard on the guard: a selector reworded in tokens.css must fail here,
    not quietly measure nothing."""
    for theme in SELECTORS:
        assert len(palette(theme)) > 30
