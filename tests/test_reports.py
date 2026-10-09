"""Reports: the store, the tool, the pages, and the section's own character.

The tests here are mostly about things that fail without saying so. A report
that was never filed, a page that quietly grew a way to reply to one, an index
that exists on a fresh database and not on an upgraded one -- none of those
announce themselves.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Report, User
from lembas.services import settings_store
from tests.test_schedules_ui import _as_stranger


def _user(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _file(db, **kwargs) -> Report:
    from lembas.services import reports as reports_service

    fields = {"title": "A report", "body": "What I found.", **kwargs}
    return reports_service.create(db, owner=_user(db), **fields)


# --- The store ----------------------------------------------------------------
def test_a_report_is_filed_and_read_back(client: TestClient, db, registered):
    from lembas.services import reports as reports_service

    report = _file(db, title="Build failures", body="# Heading\n\nThree tests fail.")
    assert reports_service.get(db, report.id, _user(db)) is report


def test_a_missing_summary_falls_back_to_the_first_real_line(client: TestClient, db, registered):
    """A list of forty reports all reading '# ' is a bug somebody has to explain."""
    report = _file(db, body="# Weekly build report\n\nThree tests fail on ARM.", summary="")
    assert report.summary == "Weekly build report"


def test_a_report_belongs_to_its_owner_alone(client: TestClient, db, registered):
    """There is no sharing here, which is a decision rather than an omission --
    so a second account must not reach the first one's reports."""
    from lembas.security.passwords import hash_password
    from lembas.services import reports as reports_service

    report = _file(db)
    stranger = User(
        email="sam@shire.test", name="Sam", password_hash=hash_password("gardening-is-hard")
    )
    db.add(stranger)
    db.commit()

    assert reports_service.get(db, report.id, stranger) is None
    assert reports_service.recent(db, stranger) == []


def test_an_over_long_body_is_trimmed_rather_than_refused(client: TestClient, db, registered):
    """The rule `memories` already follows: file it short with everything else
    intact, instead of failing the turn that wrote it."""
    from lembas.services import reports as reports_service

    report = _file(db, body="x" * (reports_service.MAX_BODY_CHARS + 500))
    assert len(report.body) == reports_service.MAX_BODY_CHARS


# --- The tool -----------------------------------------------------------------
@pytest.mark.anyio
async def test_report_write_actually_runs(client: TestClient, db, registered):
    """Not that it is declared -- that it *runs*.

    `_run_scratch_write` read a field its own dataclass did not have and raised
    AttributeError for the whole life of the feature, swallowed by `run_tool`'s
    blanket except into a message that reads exactly like a model calling it
    wrongly. The test that existed asserted the family and the risk, which are
    properties of the declaration.
    """
    from lembas.services import tools as tools_service

    context = tools_service.ToolContext(owner_id=_user(db).id, chat_id="", model_id="test-model")
    outcome = await tools_service.run_tool(
        context, "report_write", '{"title": "Findings", "body": "Three tests fail."}'
    )

    assert outcome.event["status"] == "ok", outcome.content
    filed = db.scalars(select(Report)).all()
    assert [r.title for r in filed] == ["Findings"]
    assert filed[0].model_id == "test-model"


@pytest.mark.anyio
async def test_report_write_refuses_an_empty_body(client: TestClient, db, registered):
    from lembas.services import tools as tools_service

    context = tools_service.ToolContext(owner_id=_user(db).id, chat_id="")
    outcome = await tools_service.run_tool(context, "report_write", '{"title": "Nothing"}')

    assert outcome.event["status"] == "error"
    assert db.scalars(select(Report)).all() == []


@pytest.mark.anyio
async def test_report_search_finds_by_body_word(client: TestClient, db, registered):
    """FTS tables are outside the model-driven schema sync, so this is also the
    check that `reports_fts` was actually created and its triggers fire."""
    from lembas.services import tools as tools_service

    _file(db, title="Unrelated", body="Nothing about the thing.")
    _file(db, title="The one", body="A palantir was involved.")

    context = tools_service.ToolContext(owner_id=_user(db).id, chat_id="")
    outcome = await tools_service.run_tool(context, "report_search", '{"query": "palantir"}')

    assert [r["title"] for r in outcome.event["results"]] == ["The one"]


def test_the_index_is_backfilled_on_an_upgrade(client: TestClient, db, registered):
    """The upgrade path, which the fresh-database case does not exercise.

    FTS tables are outside the model-driven schema sync -- they are not
    SQLAlchemy models, so `sync_schema` cannot diff them. On an instance that
    already has reports, `ensure_fts` has to create the index *and* backfill
    what is already in the table; without the backfill every report filed before
    the upgrade is invisible to search forever, and nothing says so.
    """
    from sqlalchemy import text

    from lembas.db.migrations import ensure_fts
    from lembas.db.session import get_engine
    from lembas.services import reports as reports_service

    _file(db, title="Before the upgrade", body="A palantir was involved.")

    # Drop the index and its triggers, leaving the rows: an instance upgrading
    # into this release looks exactly like this.
    with get_engine().begin() as connection:
        for suffix in ("_ai", "_ad", "_au"):
            connection.execute(text(f"DROP TRIGGER IF EXISTS reports_fts{suffix}"))
        connection.execute(text("DROP TABLE IF EXISTS reports_fts"))

    assert "reports_fts" in ensure_fts(get_engine())
    assert [r.title for r in reports_service.search(db, _user(db), "palantir")] == [
        "Before the upgrade"
    ]


# --- The section's character --------------------------------------------------
def test_the_reports_pages_carry_no_composer(client: TestClient, db, registered):
    """Reports are read, never answered, and the way to be sure of that is for
    the machinery that would answer to be absent.

    `chat/_message.html` is the state machine: an `sse-connect` anywhere on
    these pages is a generation this section has no business starting.
    """
    report = _file(db)

    for url in ("/reports", f"/reports/{report.id}"):
        body = client.get(url).text
        assert "sse-connect" not in body, url
        assert "composer__form" not in body, url
        # Nothing on the page can send anything anywhere. Checked as "no field
        # named content" rather than by looking for a URL, because the sidebar
        # legitimately links to sections that do have composers.
        assert 'name="content"' not in body, url


def test_no_route_accepts_a_message_for_a_report(client: TestClient, registered):
    """Asserted on the resolved routes rather than on the templates, because the
    failure this guards against is somebody adding the endpoint first."""
    from lembas.main import app

    # Read from the OpenAPI schema rather than by walking `app.routes`: this
    # FastAPI keeps an included router wrapped rather than flattening it, so the
    # walk finds nothing at all and the assertion passes for the wrong reason.
    writable = {
        f"{method.upper()} {path}"
        for path, methods in app.openapi()["paths"].items()
        if path.startswith(("/reports", "/api/reports"))
        for method in methods
        if method.upper() in {"POST", "PATCH", "PUT"}
    }
    assert writable == {"POST /api/reports/{report_id}/delete"}


def test_a_report_body_cannot_smuggle_html(client: TestClient, db, registered):
    """Model output, hard rule 6. Rendered through services/markdown.py, which
    is the one path allowed to emit HTML here."""
    report = _file(
        db,
        body="<script>alert(1)</script>\n\n[click](javascript:alert(1))",
    )
    body = client.get(f"/reports/{report.id}").text

    assert "<script>alert(1)</script>" not in body
    # The link is left as inert text rather than becoming an anchor, which is
    # the property that matters: what must never appear is the href.
    assert 'href="javascript:' not in body


def test_opening_a_report_clears_its_dot(client: TestClient, db, registered):
    report = _file(db)
    assert report.unread is True

    client.get(f"/reports/{report.id}")
    db.refresh(report)
    assert report.unread is False


def test_the_unread_poll_carries_the_section_dot(client: TestClient, db, registered):
    """Sent on every tick including empty, because it has to be able to clear.
    A dot that survived reading the last report would be news nobody can dismiss.
    """
    report = _file(db)

    showing = client.get("/api/chats/unread").text
    dot = next(s for s in showing.split("<span") if 'id="unread-reports"' in s)
    assert "hidden" not in dot

    client.get(f"/reports/{report.id}")
    cleared = client.get("/api/chats/unread").text
    dot = next(s for s in cleared.split("<span") if 'id="unread-reports"' in s)
    assert "hidden" in dot


def test_reports_are_a_tab_of_the_library_not_a_sidebar_entry(
    client: TestClient, db, registered
):
    """Reports is a tab of the Library, not a sidebar entry. The sidebar keeps
    the way there -- the Library link, which carries the unread dot now -- and
    the tab is on every Library page."""
    body = client.get("/chat").text
    assert 'href="/reports"' not in body
    assert 'id="sidebar-sections"' not in body
    # The dot rides the Library link, outside the tree the switch swaps.
    assert body.index('id="unread-reports"') > body.index('class="sidebar__footer"')

    for path in ("/library/knowledge", "/library/notes", "/library/reports"):
        page = client.get(path).text
        assert 'href="/library/reports"' in page, path


def test_the_old_addresses_redirect(client: TestClient, db, registered):
    """A notification, a link in a chat or the installed app's shortcut still
    lands, query and all."""
    response = client.get("/reports?q=digest&shared=1", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/library/reports?q=digest&shared=1"

    response = client.get("/reports/abc", follow_redirects=False)
    assert response.headers["location"] == "/library/reports/abc"


def test_the_reports_tab_keeps_its_own_permission(client: TestClient, db, registered):
    """Gated on `reports.use` as before, whatever `library.use` says: without
    the library, the Library link opens on Reports and shows only that tab."""
    settings_store.update(db, {"default_permissions": {"library.use": False}})
    _as_stranger(client, db)

    page = client.get("/library/reports").text
    assert 'href="/library/knowledge"' not in page
    assert 'href="/library/reports"' in page

    settings_store.update(
        db, {"default_permissions": {"library.use": False, "reports.use": False}}
    )
    assert client.get("/library/reports", follow_redirects=False).status_code == 403
    assert client.get("/reports", follow_redirects=False).status_code == 403
