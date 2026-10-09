"""Extra headers on a connection: read on every request, written by no form.

`Connection.extra_headers_json` has been sent with every request to an endpoint
since it was added and there was nowhere to set it, so its one documented use --
OpenRouter reads `HTTP-Referer` and `X-Title` and attributes usage with them --
was unreachable. Nothing advertised it, so nothing was untrue; it was simply a
column that could only ever be empty.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lembas.db.models import Connection


def _connection(client: TestClient, db):
    client.post(
        "/admin/connections",
        data={"name": "OpenRouter", "base_url": "http://127.0.0.1:1", "api_key": ""},
        follow_redirects=False,
    )
    from sqlalchemy import select

    return db.scalars(select(Connection)).first().id


def _save(client: TestClient, connection_id: str, headers: str):
    return client.post(
        f"/admin/connections/{connection_id}",
        data={
            "name": "OpenRouter",
            "base_url": "http://127.0.0.1:1",
            "api_key": "",
            "enabled": "on",
            "unload_url": "",
            "unload_method": "POST",
            "extra_headers": headers,
        },
        follow_redirects=False,
    )


def test_headers_are_stored_as_a_dict(client: TestClient, db, registered):
    """Asserted on the row, not on the form: a field that renders and is never
    read looks exactly like one that works."""
    cid = _connection(client, db)
    _save(client, cid, "HTTP-Referer: https://example.org\nX-Title: LLeMbas")

    db.expire_all()
    stored = db.get(Connection, cid).extra_headers_json
    assert stored == {
        "HTTP-Referer": "https://example.org",
        "X-Title": "LLeMbas",
    }


def test_they_reach_the_endpoint(client: TestClient, db, registered):
    """The whole point. `openai_client` passes them to httpx verbatim."""
    cid = _connection(client, db)
    _save(client, cid, "X-Title: LLeMbas")
    db.expire_all()

    from lembas.services.llm.openai_client import Endpoint

    endpoint = Endpoint.from_connection(db.get(Connection, cid))
    assert endpoint.extra_headers["X-Title"] == "LLeMbas"


def test_clearing_the_box_clears_them(client: TestClient, db, registered):
    cid = _connection(client, db)
    _save(client, cid, "X-Title: LLeMbas")
    _save(client, cid, "")
    db.expire_all()
    assert db.get(Connection, cid).extra_headers_json == {}


def test_a_name_cannot_smuggle_in_a_second_header(client: TestClient, db, registered):
    """One field must write one header. A colon or a newline in a *name* is how
    one becomes two, and a header nobody can see the effect of is worse than one
    that is visibly missing -- so a bad line is dropped, never repaired."""
    cid = _connection(client, db)
    _save(client, cid, "Bad Name: x\nX-Ok: y\n: nothing\nAlso-Bad\n")
    db.expire_all()
    assert db.get(Connection, cid).extra_headers_json == {"X-Ok": "y"}
