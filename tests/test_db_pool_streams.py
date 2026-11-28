"""A request's database session is gone before its response streams.

FastAPI closes a `yield` dependency of the default ("request") scope only
after the response has been sent -- for a stream, when the stream ends. A
session that has run one query keeps its pooled connection until it is
closed, so every open event stream held one of the pool's fifteen, and a
handful of apps, tabs and streamed completions emptied it: every other
request then waited thirty seconds and failed, until a restart. The `Db`
dependency is function-scoped for that reason; the streams open their own
short sessions.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import text

from lembas.api.deps import Db
from lembas.db.session import get_engine


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/held")
    def held(db: Db) -> StreamingResponse:
        db.execute(text("SELECT 1"))  # the session now holds a connection

        def body():
            yield str(get_engine().pool.checkedout())

        return StreamingResponse(body(), media_type="text/plain")

    return app


def test_a_stream_holds_no_pooled_connection_of_its_request():
    get_engine()  # the test database's engine, as the app would use it
    with TestClient(_app()) as client:
        assert client.get("/held").text == "0"


def test_the_request_session_still_closes_without_a_stream():
    with TestClient(_app()) as client:
        client.get("/held")
    assert get_engine().pool.checkedout() == 0
