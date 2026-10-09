"""Markdown rendering and sanitisation.

Model output is untrusted input: it routinely contains HTML and a model can be
talked into emitting a script tag. These are the tests that keep that boundary.
"""

from __future__ import annotations

from lembas.services.markdown import escape_text, render_markdown


def test_basic_formatting():
    html = render_markdown("Some **bold** and *italic* text.")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html


def test_script_tags_are_stripped():
    html = render_markdown("Hello <script>alert('xss')</script> world")
    assert "<script" not in html
    assert "alert" not in html or "&lt;script" in html


def test_javascript_urls_never_become_links():
    """markdown-it refuses the scheme and leaves the source as literal text, so
    the guarantee to assert is that no anchor is produced -- not that the
    substring is absent, which it legitimately is not."""
    html = render_markdown("[click me](javascript:alert(1))")
    assert "<a " not in html
    assert 'href="javascript:' not in html


def test_javascript_urls_in_raw_html_anchors_are_stripped():
    html = render_markdown('<a href="javascript:alert(1)">click</a>')
    assert 'href="javascript:' not in html


def test_event_handlers_are_stripped():
    html = render_markdown('<img src="x" onerror="alert(1)">')
    assert "onerror" not in html


def test_external_links_get_protective_rel():
    html = render_markdown("[example](https://example.com)")
    assert "noopener" in html
    assert "noreferrer" in html


def test_code_block_is_highlighted_and_not_double_wrapped():
    html = render_markdown("```python\ndef f():\n    return 1\n```")
    assert 'class="code-block"' in html
    assert "pg-k" in html  # a Pygments keyword span
    # markdown-it wraps highlight output in its own <pre><code> unless the
    # fence rule is replaced outright. This is the regression guard.
    assert "<pre><code" not in html


def test_code_block_language_label():
    assert ">python<" in render_markdown("```python\nx = 1\n```")


def test_unlabelled_code_block_still_renders():
    html = render_markdown("```\njust text\n```")
    assert 'class="code-block"' in html
    assert "just text" in html


def test_code_content_is_escaped():
    html = render_markdown("```\n<script>alert(1)</script>\n```")
    assert "<script>" not in html


def test_tables_render():
    html = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in html and "<td>1</td>" in html


def test_bare_urls_are_linkified():
    assert "<a href=" in render_markdown("see https://example.com for more")


def test_empty_input():
    assert render_markdown("") == ""


def test_escape_text_handles_structural_characters():
    assert escape_text("<b>hi</b>") == "&lt;b&gt;hi&lt;/b&gt;"
    assert escape_text("a & b") == "a &amp; b"


def test_escape_text_is_chunk_safe():
    """Streaming escapes each token as it arrives, so escaping the pieces must
    equal escaping the whole -- otherwise a stream would diverge from the final
    rendering."""
    whole = "<a>&</a> text"
    chunks = ["<a", ">&<", "/a> ", "text"]
    assert "".join(escape_text(c) for c in chunks) == escape_text(whole)
