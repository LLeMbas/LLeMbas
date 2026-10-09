"""The back link on a detail page points back, and only its chevron turns.

`.crumbs__back` rotates by 180 degrees, to make a right chevron point left. Four
detail pages put it on the whole link instead of the icon, so "All connections"
and the rest rendered upside down. The class belongs on the icon only.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "src/lembas/web/templates"


def test_only_an_icon_is_ever_turned_round():
    for path in TEMPLATES.rglob("*.html"):
        for match in re.finditer(r'<(\w+)[^>]*class="[^"]*\bcrumbs__back\b', path.read_text()):
            assert match.group(1) not in {"a", "nav", "span", "div"}, (
                f"{path.relative_to(TEMPLATES)}: crumbs__back on a <{match.group(1)}>"
            )


def test_a_detail_page_reads_the_right_way_up(client, registered):
    """Rendered, not just read: one of the four, through its route."""
    page = client.get("/admin/tools/new").text
    link = page[page.index('<nav class="crumbs">'):]
    link = link[: link.index("</nav>")]
    assert 'class="crumbs__back"' not in link.split("<svg", 1)[0]
    assert "crumbs__back" in link
