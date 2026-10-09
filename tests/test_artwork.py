"""The pixel artwork: generated, coloured by the theme, and complete.

`templates/_art.html` is written by scripts/build_artwork.py from
scripts/pixel_art.py. Three things can drift apart and each would ship looking
fine in one theme: the committed file and its generator, a role and its class
in app.css, and a role and its colour in the theme that shows the drawing.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
CSS = ROOT / "src/lembas/web/static/css"
ART = ROOT / "src/lembas/web/templates/_art.html"

sys.path.insert(0, str(SCRIPTS))
import pixel_art  # noqa: E402


def _load_builder():
    pytest.importorskip("fontTools")  # build_artwork imports it at the top
    spec = importlib.util.spec_from_file_location("build_artwork", SCRIPTS / "build_artwork.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _theme_block(theme: str) -> str:
    css = (CSS / "tokens.css").read_text()
    marker = {
        "moria": ':root,\n:root[data-theme="moria"] {',
        "shire": ':root[data-theme="shire"],\n:root[data-base="shire"] {',
    }[theme]
    start = css.index(marker)
    return css[start : css.index("\n}\n", start)]


def test_the_committed_art_is_what_the_generator_writes():
    assert ART.read_text() == _load_builder().build_art_macros(), (
        "templates/_art.html is out of date: run "
        "python scripts/build_artwork.py --only _art.html"
    )


@pytest.mark.parametrize(
    "drawing, themes",
    [("leaf", ("moria", "shire")), ("durin", ("moria",)), ("smial", ("shire",))],
)
def test_every_role_has_a_colour_in_the_theme_that_shows_it(drawing, themes):
    for theme in themes:
        block = _theme_block(theme)
        for role in pixel_art.ROLES[drawing]:
            assert f"--art-{role}:" in block, f"{theme} has no --art-{role} for the {drawing}"


def test_every_role_has_a_class_in_app_css():
    app = (CSS / "app.css").read_text()
    for roles in pixel_art.ROLES.values():
        for role in roles:
            rule = rf"\.px-{re.escape(role)}\s*{{\s*fill:\s*var\(--art-{role}\)"
            assert re.search(rule, app), role


def test_the_roles_list_matches_the_drawings():
    """ROLES is what the two tests above check against, so it must be what the
    drawings actually use -- a role drawn but not listed would go unchecked."""
    for name, draw in (("leaf", pixel_art.leaf), ("durin", pixel_art.durin),
                       ("smial", pixel_art.smial)):
        assert set(draw().values()) == set(pixel_art.ROLES[name]), name


def test_the_mark_has_no_colour_of_its_own(client, registered):
    """The vector mark it replaced carried eleven hex values in a template."""
    page = client.get("/chat").text
    mark = page[page.index('class="art art--leaf'):]
    mark = mark[: mark.index("</svg>")]
    assert "#" not in mark.replace("&#", "")
    assert 'class="px-leaf' in mark
