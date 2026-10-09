"""A grid that reflows can still overflow a phone, and twice it has.

`repeat(auto-fit, minmax(13rem, 1fr))` puts two 208px cards side by side on a
390px screen: `auto-fit` decides how many columns fit, and a track whose minimum
is a fixed length never gives that minimum up. The tree's standing rule is to
write `minmax(min(100%, 13rem), 1fr)` instead, so the *track* yields rather than
the viewport.

Half of the rule is not the rule. `.suggestions` had `width: 100%` and got the
`min(100%, …)` track, and still rendered 455px wide inside a 366px column -- it
is a grid item, so it carries `min-width: auto`, which for a grid item means a
min-content floor, and a floor beats `width`. Worse, the floor is measured while
the percentage is indefinite, so the track falls back to a card's max-content:
adding `min(100%, …)` on its own moved the overflow from 428px to 455px.

So both halves are asserted here, on every auto-fit grid in the stylesheets. A
stylesheet cannot see whether a given grid is a flex item on some page today or
becomes one next week, and `min-width: 0` costs nothing where it is not needed.

Found on a phone, on the one screen the screenshot harness had never actually
rendered -- `scripts/shoot.py` builds its client without a lifespan, so the
suggestion cards were absent from every shot ever taken of the new-chat screen.
That is fixed there; this file is the cheap half that runs in the suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import lembas

CSS = Path(lembas.__file__).parent / "web/static/css"

# `[^{}]*` cannot cross a brace, so an `@media` prelude never matches and the
# rules nested inside it do.
RULE = re.compile(r"([^{}]*)\{([^{}]*)\}")
COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _auto_grids() -> list[tuple[str, str, dict[str, str]]]:
    found = []
    for path in sorted(CSS.glob("*.css")):
        text = COMMENT.sub("", path.read_text(encoding="utf-8"))
        for prelude, body in RULE.findall(text):
            if "auto-fit" not in body and "auto-fill" not in body:
                continue
            declarations = {
                part.partition(":")[0].strip(): part.partition(":")[2].strip()
                for part in body.split(";")
                if ":" in part
            }
            found.append((path.name, prelude.strip(), declarations))
    return found


def test_the_stylesheets_still_have_auto_fit_grids_to_check():
    # Otherwise the two tests below pass by finding nothing, which is how a
    # coverage test quietly stops covering anything.
    assert len(_auto_grids()) >= 4


def test_every_auto_fit_track_can_give_up_its_minimum():
    offenders = [
        f"{name} {selector}"
        for name, selector, declarations in _auto_grids()
        for value in [declarations.get("grid-template-columns", "")]
        if "minmax(" in value and "min(100%" not in value
    ]
    assert not offenders, (
        "an auto-fit track with a fixed minimum overflows a phone; write "
        f"minmax(min(100%, X), 1fr): {offenders}"
    )


def test_every_auto_fit_grid_drops_its_automatic_minimum_size():
    offenders = [
        f"{name} {selector}"
        for name, selector, declarations in _auto_grids()
        if declarations.get("min-width") != "0"
    ]
    assert not offenders, (
        "a grid item's `min-width: auto` is a min-content floor and beats "
        f"`width`, so `min(100%, …)` alone does not save it: {offenders}"
    )
