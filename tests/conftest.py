"""Test fixtures.

Every test runs against a throwaway SQLite file in a tmp_path, never the real
data directory. The environment has to be set before lembas.config is imported,
because Settings is a cached singleton read at import time.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Must happen before any lembas import.
_TMP = tempfile.mkdtemp(prefix="lembas-tests-")
os.environ.update(
    {
        "LEMBAS_SECRET_KEY": "test-secret-key-not-for-real-use",
        "LEMBAS_DATA_DIR": _TMP,
        "LEMBAS_ALLOW_SIGNUP": "true",
        "LEMBAS_LOG_LEVEL": "warning",
    }
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from lembas.config import settings  # noqa: E402
from lembas.db.base import Base  # noqa: E402
from lembas.db.session import get_engine, get_session_factory, reset_engine  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_database(tmp_path: Path) -> Iterator[None]:
    """Point the engine at a per-test database and build the schema.

    reset_engine() is essential: the engine is a module-level singleton, so
    without it every test after the first would share the first one's file.
    """
    settings.data_dir = tmp_path
    reset_engine()
    settings.ensure_dirs()

    import lembas.db.models  # noqa: F401  (registers the tables)

    # sync_schema rather than create_all: it is what startup runs, and it also
    # builds the full-text indexes, which are not SQLAlchemy models and so are
    # invisible to create_all. Tests were otherwise running against a schema
    # production does not have.
    from lembas.db.migrations import sync_schema

    Base.metadata.create_all(bind=get_engine())
    sync_schema(get_engine())

    # The agent settings' `loopback` switch is off by default. The suite runs
    # with it on, because test fixtures point at 127.0.0.1; a test that cares
    # about the switch closes it explicitly.
    from lembas.db.session import session_scope
    from lembas.services import settings_store

    with session_scope() as db:
        settings_store.update(db, {"loopback": "on"}, key=settings_store.AGENTS)

    yield
    reset_engine()


@pytest.fixture(autouse=True)
def fresh_snapshots() -> Iterator[None]:
    """Drop the process-level snapshots between tests.

    Two of them now, and both are read once per process against a database this
    fixture throws away between tests -- so without this, the first test to
    render a page pins one instance's name and themes for every test after it,
    and the first to save an upload limit pins that too. The same shape as the
    registries below, and the reason each of them exists.
    """
    from lembas.services import branding, files

    branding.forget()
    files.forget()
    yield
    branding.forget()
    files.forget()


@pytest.fixture(autouse=True)
def fresh_generation_registry() -> Iterator[None]:
    """Empty the in-flight reply registry between tests.

    `_RUNNING` and `_TASKS` are module-level dicts, so a test that starts a
    reply and does not wait for it leaves an entry behind for the rest of the
    session -- holding a Generation, and a Task belonging to an event loop that
    has since closed. `_prune()` will not clear it either: it only drops
    generations that have finished, and it runs on every `ensure()`.

    Cheap, and it keeps a test that posts a message from meeting the leftovers
    of one that asked a question.
    """
    from lembas.services import generation as generation_service

    generation_service._RUNNING.clear()
    generation_service._TASKS.clear()
    yield
    generation_service._RUNNING.clear()
    generation_service._TASKS.clear()


@pytest.fixture(autouse=True)
def fresh_terminal_registry() -> Iterator[None]:
    """Empty the open-shell registry between tests, for the same reason.

    A leaked entry holds a shell session belonging to an event loop that has
    since closed, and the reaper task is module-level too -- one left
    running would wake up inside the next test's loop.
    """
    from lembas.services.agent import terminal as terminal_service

    def _clear() -> None:
        reaper = terminal_service._REAPER
        if reaper is not None:
            reaper.cancel()
        terminal_service._REAPER = None
        terminal_service._SESSIONS.clear()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def fresh_schedule_ticker() -> Iterator[None]:
    """Stop the schedule ticker and forget any firings, for the same reason.

    The ticker is a module-level task like the terminal reaper, and a firing is
    a task holding a chat id. One left running would wake up inside the next
    test's event loop, against the next test's database, and fire something
    nobody in that test has ever heard of.

    The wake locks go too: they are keyed on chat id, and `make_chat` recycles
    ids freely across a session.
    """
    from lembas.services import wake as wake_service
    from lembas.services.schedule import ticker as ticker_service

    def _clear() -> None:
        running = ticker_service._TICKER
        if running is not None:
            running.cancel()
        ticker_service._TICKER = None
        for task in list(ticker_service._FIRING):
            task.cancel()
        ticker_service._FIRING.clear()
        wake_service._LOCKS.clear()

    _clear()
    yield
    _clear()


@pytest.fixture
def db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from lembas.main import app

    # raise_server_exceptions=False so error-handler behaviour is exercised
    # rather than the exception propagating into the test.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def registered(client: TestClient) -> dict[str, str]:
    """Register the first account. It becomes the administrator."""
    credentials = {
        "name": "Frodo",
        "email": "frodo@shire.test",
        "password": "speak-friend-and-enter",
    }
    response = client.post("/auth/register", data=credentials, follow_redirects=False)
    assert response.status_code == 303, response.text
    return credentials


@pytest.fixture
def make_chat(db: Session):
    """Create a chat row directly, as scaffolding for other tests.

    Chats are normally created by POST /api/chats/start along with their first
    exchange -- there is deliberately no endpoint that makes an empty one. Most
    tests want a chat to act on, not that flow, so they get one straight from
    the database rather than having to subtract an opening turn from every
    assertion. The flow itself is covered in test_chat.py.
    """
    from sqlalchemy import select

    from lembas.db.models import Chat, Model, User

    def _create(email: str | None = None, model_id: str | None = None) -> str:
        user = (
            db.scalar(select(User).where(User.email == email))
            if email
            else db.scalars(select(User).order_by(User.created_at)).first()
        )
        model = (
            db.scalar(select(Model).where(Model.model_id == model_id))
            if model_id
            else db.scalars(select(Model).order_by(Model.position)).first()
        )
        chat = Chat(
            user_id=user.id,
            model_id=model.model_id if model else "",
            connection_id=model.connection_id if model else None,
        )
        db.add(chat)
        db.commit()
        return chat.id

    return _create


@pytest.fixture
def mock_http():
    """Answer every outgoing httpx request with a handler of the test's choosing.

    The services build their own AsyncClient because each needs its own timeout,
    so there is no client to inject; patching the class is what reaches them.
    Returns a callable that installs a handler and is undone on teardown.
    """
    import httpx

    original = httpx.AsyncClient

    def install(handler):
        class Patched(original):
            def __init__(self, **kwargs):
                super().__init__(transport=httpx.MockTransport(handler), **kwargs)

        httpx.AsyncClient = Patched

    yield install
    httpx.AsyncClient = original


def control_named(html: str, name: str) -> dict[str, str]:
    """The attributes of the one element carrying `name="…"`.

    Exists so a test can ask "does the control that carries the name also carry
    the verb?". Two selects in the composer once delegated their `hx-patch` to
    an empty sibling form through the `form=` attribute, which scopes values but
    routes no events -- htmx binds a trigger to the annotated element, and
    `change` reaches ancestors, never siblings. Both controls were decorative
    for a whole release, and the tests passed the entire time because they
    asserted the markup that was there rather than the property that mattered.
    """
    from html.parser import HTMLParser

    found: list[dict[str, str]] = []

    class Finder(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            got = {key: (value or "") for key, value in attrs}
            if got.get("name") == name:
                found.append(got)

    Finder().feed(html)
    assert len(found) == 1, f"expected one element named {name!r}, found {len(found)}"
    return found[0]


def script(name: str) -> str:
    """One of the shipped browser files, as text.

    There is no JavaScript test runner here -- hard rule 1 keeps Node out of the
    project -- so behaviour is driven by hand under a DOM stub and what the
    suite pins is what the source says about itself.
    """
    import lembas

    return (Path(lembas.__file__).parent / "web/static/js" / name).read_text(encoding="utf-8")


def js_code(source: str) -> str:
    """The same source with its comments blanked out.

    These files are heavily commented, and every comment names the thing it is
    explaining -- so `"list" in refresh` is true of a paragraph saying that
    writing to `list` too early was the bug. An invariant about the code must
    not be satisfiable by prose describing it. String literals are left intact,
    because the assertions are usually about an event name inside one.
    """
    out: list[str] = []
    at, end = 0, len(source)
    while at < end:
        char = source[at]
        if char in "\"'`":
            quote = char
            out.append(char)
            at += 1
            while at < end:
                if source[at] == "\\":
                    out.append(source[at : at + 2])
                    at += 2
                    continue
                out.append(source[at])
                at += 1
                if source[at - 1] == quote:
                    break
            continue
        if char == "/" and source[at : at + 2] in ("//", "/*"):
            stop = (
                source.find("\n", at)
                if source[at + 1] == "/"
                else source.find("*/", at) + 2
            )
            if stop <= at:
                stop = end
            # Blanked rather than removed, so every offset still lines up.
            out.append("".join(" " if c != "\n" else "\n" for c in source[at:stop]))
            at = stop
            continue
        out.append(char)
        at += 1
    return "".join(out)


def js_says(text: str, *pieces: str) -> bool:
    """Whether the pieces appear, in order, whatever sits between them.

    Asserting a line verbatim makes a test that fails on reindentation, which is
    noise; asserting only that two words appear somewhere makes one that never
    fails at all. This is the middle: the shape, not the spacing.
    """
    at = 0
    for piece in pieces:
        at = text.find(piece, at)
        if at == -1:
            return False
        at += len(piece)
    return True


def js_function(source: str, name: str) -> str:
    """The text of one named function, braces matched.

    Lets a test ask about the inside of a handler rather than about the file --
    "is `build()` called before anything writes to `list`" is a question about
    `refresh`, and asking it of the whole source answers yes for the wrong
    reason.
    """
    code = js_code(source)
    # The parenthesis is load-bearing: `token` is a prefix of `tokenAt`, and
    # asking about the wrong function is a test that passes for no reason.
    start = code.index(f"function {name}(")
    at = code.index("{", start)
    depth, quote = 0, ""
    while at < len(code):
        char = code[at]
        if quote:
            if char == "\\":
                at += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return code[start : at + 1]
        at += 1
    raise AssertionError(f"no closing brace for {name}()")


@pytest.fixture
def user_id(db: Session, registered: dict[str, str]) -> str:
    """The registered user's id.

    Chats have a real foreign key to users and SQLite enforces it (the
    connect-time PRAGMA in db/session.py turns that on), so tests that build a
    Chat directly need a user that actually exists.
    """
    from sqlalchemy import select

    from lembas.db.models import User

    return db.scalar(select(User).where(User.email == registered["email"])).id
