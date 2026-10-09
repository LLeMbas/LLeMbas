"""Every code block in a reply can be copied.

The button is not in the reply's HTML -- model output goes through a sanitiser
that strips <button>, and must keep doing so -- but in a <template> on every
page, which app.js clones into each block. These pin the three halves.
"""

from __future__ import annotations

from pathlib import Path

from lembas.services.markdown import render_markdown

JS = Path(__file__).resolve().parent.parent / "src/lembas/web/static/js/app.js"


def test_every_page_carries_the_button_template(client, registered):
    page = client.get("/chat").text
    template = page[page.index('<template id="code-copy">'):]
    template = template[: template.index("</template>")]
    assert 'class="btn btn--icon btn--sm code-block__copy"' in template
    assert 'aria-label="Copy code"' in template


def test_the_button_is_translated():
    from lembas.web.i18n import translate

    assert translate("Copy code", "sk") == "Skopírovať kód"


def test_a_reply_still_cannot_carry_a_button_of_its_own():
    html = render_markdown('<button onclick="x()">Copy</button>\n\n```py\nprint(1)\n```')
    assert "<button" not in html
    assert 'class="code-block"' in html


def test_the_script_copies_the_code_and_watches_for_new_blocks():
    source = JS.read_text()
    assert "function addCodeCopy(root)" in source
    assert 'closest(".code-block__copy")' in source
    assert "new MutationObserver" in source
