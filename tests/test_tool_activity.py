"""Rendering what a tool did.

The block is written from four places and read from stored rows written by
earlier versions, so it has to render anything shaped roughly like an event --
and everything in it is third-party text.
"""

from __future__ import annotations

import re
from pathlib import Path

from lembas.services import tool_labels
from lembas.services import tools as tools_service
from lembas.web.templating import templates


def _render(*events, live: bool = False) -> str:
    return templates.get_template("chat/_tool_activity.html").render(
        {"tool_events": list(events), "live": live}
    )


def test_a_search_still_says_it_searched_the_web():
    html = _render(
        {
            "name": "web_search",
            "kind": "search",
            "query": "mallorn",
            "status": "ok",
            "results": [
                {
                    "title": "Mallorn",
                    "url": "https://a.test/m",
                    "host": "a.test",
                    "snippet": "A tree.",
                }
            ],
        }
    )
    assert "Searched the web for “mallorn”" in html
    assert '<a class="tool-result__title" href="https://a.test/m"' in html
    assert "1 result" in html


def test_a_library_tool_no_longer_claims_to_have_searched_the_web():
    """Stored rows predate `kind`, and every one of them used to render a globe
    and "Searched the web for <the note title>"."""
    html = _render({"name": "notes_search", "query": "shopping", "status": "ok", "results": []})
    assert "Searched the web" not in html
    assert "Notes searched" in html


def test_a_custom_tool_is_named_and_its_host_shown():
    html = _render(
        {
            "name": "weather",
            "kind": "custom",
            "label": "Weather",
            "query": "city='Minas Tirith'",
            "detail": "GET api.test",
            "status": "ok",
            "results": [],
            "text": "Sunny.",
        }
    )
    assert "Weather" in html
    assert "GET api.test" in html
    assert "Sunny." in html


def test_a_tools_own_text_is_escaped_and_never_rendered_as_markdown():
    """Hard rule 6. A tool's reply is exactly as untrusted as a search result,
    and markdown is the one path allowed to emit HTML."""
    html = _render(
        {
            "name": "weather",
            "kind": "custom",
            "label": "Weather",
            "status": "ok",
            "results": [],
            "text": "<img src=x onerror=alert(1)> [click](javascript:alert(1))",
        }
    )
    assert "<img" not in html
    assert "&lt;img" in html
    # The markdown link is shown as the text it is, not turned into an anchor.
    assert "<a " not in html
    assert "[click](javascript:alert(1))" in html


def test_a_result_url_that_is_not_http_never_becomes_a_link():
    html = _render(
        {
            "name": "web_search",
            "kind": "search",
            "status": "ok",
            "results": [{"title": "Bad", "url": "javascript:alert(1)", "host": "", "snippet": ""}],
        }
    )
    assert "<a " not in html
    assert '<span class="tool-result__title">Bad</span>' in html


def test_a_result_with_no_url_at_all_does_not_explode():
    html = _render(
        {
            "name": "notes_search",
            "status": "ok",
            "results": [{"title": "A note", "id": "abc"}],
        }
    )
    assert "A note" in html


def test_a_failure_shows_its_reason():
    html = _render(
        {
            "name": "weather",
            "kind": "custom",
            "label": "Weather",
            "status": "error",
            "error": "HTTP 503",
            "results": [],
        }
    )
    assert "tool-activity--error" in html
    assert "Weather failed" in html
    assert "HTTP 503" in html


# --- What a tool is called -----------------------------------------------------
def test_a_stored_profile_name_no_longer_becomes_the_label():
    """The whole point of the inversion.

    An older agent event carries `label` set to the SSH profile's name, which
    would make the transcript say "homeserver · ls -la" and name the machine
    rather than the thing that was done. Those rows are on disk and are
    re-rendered on every page load, so the rendering has to handle them.

    A command's block is titled by the command itself, and there is no host
    line at all -- the chat says where it works.
    """
    html = _render(
        {
            "name": "shell_run",
            "kind": "agent",
            "label": "homeserver",
            "query": "ls -la",
            "detail": "homeserver:/srv/app",
            "status": "ok",
            "results": [],
        }
    )
    summary = html.split("</summary>")[0]
    assert "ls -la" in summary
    assert "homeserver" not in html


def test_a_custom_tools_own_label_still_wins():
    """The other half of the same rule. A row-backed tool's name is per row and
    cannot be tabulated, so nothing in the table shadows it."""
    html = _render({"name": "weather", "kind": "custom", "label": "Weather", "results": []})
    assert "Weather" in html


def test_every_builtin_tool_has_a_label_and_an_icon(db):
    """A property, not markup. A tool added without an entry renders its own
    function name at somebody, which is the state this replaced.

    Through `registry(db)` rather than `REGISTRY`, because the latter holds only
    the tools built at import time: the scheduling, subagent and image tools are
    all built by a function and were invisible here. Some of them had labels
    only because somebody remembered, which is the arrangement this test exists
    to replace.
    """
    names = [tool.name for tool in tools_service.registry(db).values()]
    for expected in ("task", "schedule_create", "image_generate", "plan_write"):
        assert expected in names, f"{expected} is not in the registry; this test went blind"
    missing = [name for name in names if name not in tool_labels.LABELS]
    assert not missing, f"no label for {missing}"
    missing = [name for name in names if name not in tool_labels.ICONS]
    assert not missing, f"no icon for {missing}"


def test_every_icon_named_exists_in_the_sprite():
    """A typo'd symbol id renders an empty box and says nothing. This is the
    only thing that catches it."""
    sprite = Path(tools_service.__file__).parents[2] / "web/templates/partials/icons.html"
    available = set(re.findall(r'id="i-([a-z-]+)"', sprite.read_text()))
    wanted = set(tool_labels.ICONS.values()) | set(tool_labels.KIND_ICONS.values())
    wanted.add(tool_labels.FALLBACK_ICON)
    assert wanted <= available, f"not in the sprite: {sorted(wanted - available)}"


def test_an_unknown_tool_falls_back_to_its_name():
    assert tool_labels.label_for({"name": "mcp_thing"}) == "mcp_thing"
    assert tool_labels.icon_for({"name": "mcp_thing", "kind": "mcp"}) == "server"
    assert tool_labels.icon_for({"name": "whatever"}) == tool_labels.FALLBACK_ICON


# --- Diffs -----------------------------------------------------------------------
def test_a_diff_renders_added_and_removed_lines():
    html = _render(
        {
            "name": "file_edit",
            "kind": "agent",
            "query": "src/app.py",
            "status": "ok",
            "results": [],
            "diff": "--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,2 @@\n alpha\n-beta\n+BETA",
        }
    )
    assert 'diff__line--del">-beta</span>' in html
    assert 'diff__line--add">+BETA</span>' in html
    assert 'diff__line--ctx"> alpha</span>' in html
    assert 'diff__line--meta">@@ -1,2 +1,2 @@</span>' in html


def test_a_diff_header_is_not_an_addition():
    """`+++ b/x` at the top of every diff would otherwise render green, and
    `--- a/x` red, which reads as the file being replaced by itself. (An edit shows its
    hunks open and the headers only in the whole change.)"""
    big = "--- a/x.py\n+++ b/x.py\n@@ -1 +1,30 @@\n-a\n" + "\n".join(f"+b{n}" for n in range(30))
    html = _render({"name": "file_edit", "results": [], "diff": big})
    assert 'diff__line--meta">--- a/x.py</span>' in html
    assert 'diff__line--meta">+++ b/x.py</span>' in html
    assert 'diff__line--add">+++ b/x.py' not in html


def test_a_removed_line_of_dashes_is_still_a_removal():
    """A removed line whose own text begins with `--` produces exactly three
    dashes, which is why the header test is against the a/ and b/ prefixes."""
    html = _render({"name": "file_edit", "results": [], "diff": "@@ -1 +1 @@\n--- a dashed line"})
    assert 'diff__line--del">--- a dashed line</span>' in html


def test_a_diff_line_is_escaped():
    """Hard rule 6. It is a file off somebody else's machine."""
    html = _render(
        {
            "name": "file_edit",
            "results": [],
            "diff": "@@ -1 +1 @@\n+<script>alert(1)</script>",
        }
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_an_event_with_no_diff_renders_none():
    html = _render({"name": "file_read", "results": [], "text": "hello"})
    assert "diff__line" not in html


# --- What a call was for ---------------------------------------------------------
def test_an_explanation_rides_in_the_summary_not_the_body():
    """The body is collapsed. In Auto mode nothing stops for approval, so a
    reader who has to expand each call to find out what it was for is a reader
    watching a list of commands with no account of any of them."""
    html = _render(
        {
            "name": "shell_run",
            "kind": "agent",
            "query": "pytest -q",
            "why": "Checking the change did not break anything.",
            "status": "ok",
            "results": [],
        }
    )
    summary = html.split("</summary>", 1)[0]
    assert "Checking the change did not break anything." in summary


def test_an_explanation_is_escaped_like_everything_else():
    html = _render(
        {
            "name": "shell_run",
            "kind": "agent",
            "query": "ls",
            "why": "<script>alert(1)</script>",
            "status": "ok",
            "results": [],
        }
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_an_event_without_an_explanation_renders_no_empty_line():
    html = _render(
        {"name": "shell_run", "kind": "agent", "query": "ls", "status": "ok", "results": []}
    )
    assert "tool-activity__why" not in html


# --- The ids that carry an opened block across a swap --------------------------
def test_a_block_carries_an_id_built_from_where_it_sits():
    """`steps.js` records which are open before a swap and puts them back after,
    keyed on these. The step index comes from a mark and the marks are
    append-only, so index N always means the same call."""
    html = templates.get_template("chat/_tool_activity.html").render(
        {
            "tool_events": [{"name": "file_read"}, {"name": "shell_run"}],
            "message_id": "m1",
            "step_index": 3,
            "live": True,
        }
    )

    assert 'id="tool-m1-3-0"' in html
    assert 'id="tool-m1-3-1"' in html


def test_ids_are_unique_within_one_bubble():
    """Two steps, two calls each. Without the step index every block in the
    reply would be `tool-m1-0-0` or `tool-m1-0-1`, and restoring one open block
    would open four."""
    seen = []
    for index in (0, 1):
        html = templates.get_template("chat/_tool_activity.html").render(
            {
                "tool_events": [{"name": "a"}, {"name": "b"}],
                "message_id": "m1",
                "step_index": index,
                "live": False,
            }
        )
        seen += re.findall(r'id="(tool-[^"]+)"', html)

    assert len(seen) == len(set(seen)) == 4


def test_a_caller_with_no_message_emits_no_id_at_all():
    """Rather than the same id in every bubble on the page, which is worse than
    none: `document.querySelector` would find the first one and restore the
    wrong block."""
    html = templates.get_template("chat/_tool_activity.html").render(
        {"tool_events": [{"name": "a"}], "live": False}
    )

    assert "id=" not in html


def test_the_live_and_the_stored_render_agree_about_the_id():
    """The property the whole open-state restore depends on. If these differed,
    everything a reader had expanded would shut at the moment the reply
    finished -- the one moment they are most likely to be reading it."""
    events = [{"name": "shell_run"}]
    live = templates.get_template("chat/_tool_activity.html").render(
        {"tool_events": events, "message_id": "m1", "step_index": 2, "live": True}
    )
    stored = templates.get_template("chat/_tool_activity.html").render(
        {"tool_events": events, "message_id": "m1", "step_index": 2, "live": False}
    )

    assert re.findall(r'id="([^"]+)"', live) == re.findall(r'id="([^"]+)"', stored)
