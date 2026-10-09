"""Where a form begins and ends, and which form a button belongs to.

Every other test in this suite talks to a route. That is what let this ship: a
POST from `TestClient` carries exactly the fields the test names, so a page whose
fields are not in any form passes every one of them. The browser is the only
thing that disagrees, and what it disagrees about is a parse rule.

`<form>` inside `<form>` is not allowed in HTML, and the failure is silent and
inverted: the parser **drops the inner start tag**, and the inner *end* tag then
closes the outer form. So a nested form does not create a small form inside a big
one -- it truncates the big one, and everything below becomes unsubmittable.

That is what `admin/model_detail.html` once did. "Save changes"
belonged to no form and did nothing; the description, the system prompt, all
nineteen capability switches and the availability card could not be saved; and
the one button that *was* inside the surviving half posted it to the save route,
where every absent field took its `Form()` default -- clearing the description
and the system prompt and disabling the model.

The markup reads correctly at every point, which is why this is a test about
structure rather than about wording.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[1] / "src/lembas/web/templates"

# Jinja comments are not markup. The explanation of this very bug, in
# `model_detail.html`, contains the words it warns about.
COMMENT = re.compile(r"\{#.*?#\}", re.S)
TAG = re.compile(r"<form\b|</form\s*>", re.I)
SUBMIT = re.compile(r"<button\b[^>]*>", re.I)


def _markup(template: Path) -> str:
    return COMMENT.sub("", template.read_text())


def _pages() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def test_the_scan_finds_the_forms_it_is_meant_to_police():
    """A blindness guard. If the tags stop being written the way this matches,
    every assertion below passes by finding nothing -- which is exactly how the
    bug it exists for got through its own page's tests."""
    total = sum(len(TAG.findall(_markup(page))) for page in _pages())
    assert total > 40, f"only {total} form tags found across the templates"


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_no_form_is_nested_inside_another(page: Path):
    depth = 0
    for match in TAG.finditer(_markup(page)):
        if match.group(0).startswith("</"):
            depth -= 1
            assert depth >= 0, f"{page.name}: a form ends where none began"
            continue
        depth += 1
        assert depth == 1, (
            f"{page.name}: a form opens inside another at character {match.start()}. "
            "HTML drops the inner tag and the matching end tag closes the OUTER "
            "form, so everything below it stops being submittable. Declare the "
            "second form outside the first and point the button at it with "
            'form="its-id".'
        )


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_every_submit_button_can_actually_submit_something(page: Path):
    """A submit outside every form is inert, and looks exactly like a working one.

    A button may reach its form by id instead of by containment, which is how
    the fix to the bug above works -- so an `form="..."` is accepted, provided
    the form it names is declared in the same template.
    """
    markup = _markup(page)
    ids = set(re.findall(r'<form\b[^>]*\bid="([^"]+)"', markup))

    # Open **as a browser would**, which is the whole point. A `<form>` start tag
    # while a form is already open is a parse error and is *ignored*; the next
    # end tag therefore closes the one that was already open. Counting nesting
    # naively instead reports the buttons after it as still inside a form, which
    # is precisely the wrong answer -- and the reason the first version of this
    # test passed on the markup it was written for.
    open_form = False
    cursor = 0
    orphans: list[str] = []

    def check(start: int, end: int | None) -> None:
        for button in SUBMIT.finditer(markup, start, end if end is not None else len(markup)):
            tag = button.group(0)
            if 'type="submit"' not in tag:
                continue
            named = re.search(r'\bform="([^"]+)"', tag)
            if named is not None:
                assert named.group(1) in ids, (
                    f"{page.name}: a submit button names form "
                    f"{named.group(1)!r}, which this template does not declare"
                )
                continue
            if not open_form:
                orphans.append(tag[:90])

    for match in TAG.finditer(markup):
        check(cursor, match.start())
        cursor = match.end()
        if match.group(0).startswith("</"):
            open_form = False
        elif not open_form:
            open_form = True
    check(cursor, None)

    assert not orphans, (
        f"{page.name}: {len(orphans)} submit button(s) belong to no form and do "
        f"nothing when pressed: {orphans}"
    )


def test_the_detect_button_is_associated_with_the_detect_form():
    """The specific fix, pinned. Not the general rule above: this says the button
    reaches the *detection* route, which is the half the general rule cannot see.
    Submitting the page's main form instead is what cleared a model's settings."""
    markup = _markup(TEMPLATES / "admin/model_detail.html")
    form = re.search(
        r'<form\b[^>]*\bid="detect-efforts"[^>]*\baction="([^"]*)"', markup, re.S
    )
    assert form, "the detect form is gone; the button below it now saves the page"
    assert form.group(1).endswith("/detect-efforts")
    assert 'form="detect-efforts"' in markup
