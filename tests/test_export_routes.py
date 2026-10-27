"""The exports, at the doors people use: the admin page, the settings page and
the command line. What the file holds is `test_export.py`'s; here it is who may
take one, that the button is wired to a route that answers with a file, and that
no key leaves in clear on the way."""

from __future__ import annotations

import json
import re

from sqlalchemy import select
from typer.testing import CliRunner

from lembas.cli import app
from lembas.db.models import Connection
from lembas.services import crypto
from lembas.services import export as export_service

runner = CliRunner()
KEY = "sk-route-test-0123456789"
PASSPHRASE = "a long passphrase"


def _connection(db) -> None:
    db.add(Connection(name="Upstream", base_url="https://api.example.invalid/v1",
                      api_key_encrypted=crypto.encrypt(KEY)))
    db.commit()


def _form(html: str, action: str) -> str:
    match = re.search(rf'<form[^>]*action="{re.escape(action)}"[^>]*>(.*?)</form>', html, re.S)
    assert match, f"no form posting to {action}"
    return match.group(0)


# --- Admin ---------------------------------------------------------------------------------
def test_the_updates_page_offers_the_admin_export(client, registered):
    form = _form(client.get("/admin/updates").text, "/admin/updates/export")
    assert 'method="post"' in form
    assert 'name="passphrase"' in form and 'name="passphrase_confirm"' in form
    assert 'type="submit"' in form
    # A plain form: a file comes back, which htmx would swap into the page.
    assert "hx-" not in form


def test_the_admin_export_is_a_file_with_the_key_encrypted(client, registered, db):
    _connection(db)
    response = client.post(
        "/admin/updates/export",
        data={"passphrase": PASSPHRASE, "passphrase_confirm": PASSPHRASE},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    assert "lembas-admin-export-" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert KEY not in response.text
    document = export_service.load(response.text)
    marker = next(i for i in document["items"] if i["type"] == "connection")["data"]["api_key"]
    assert export_service.open_secret(document, PASSPHRASE, marker) == KEY


def test_without_a_passphrase_the_key_is_left_out(client, registered, db):
    _connection(db)
    response = client.post("/admin/updates/export", data={})
    assert response.status_code == 200 and KEY not in response.text
    document = json.loads(response.text)
    assert [s["reason"] for s in document["secrets"]] == ["no-passphrase"]


def test_two_passphrases_that_differ_export_nothing(client, registered, db):
    _connection(db)
    response = client.post(
        "/admin/updates/export",
        data={"passphrase": PASSPHRASE, "passphrase_confirm": "something else"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "export_error=" in response.headers["location"]
    page = client.get(response.headers["location"]).text
    assert "The two passphrases are not the same" in page


def test_only_an_administrator_may_export_the_configuration(client, registered, db):
    _connection(db)
    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    response = client.post("/admin/updates/export", data={}, follow_redirects=False)
    assert response.status_code == 403
    assert "lembas-export" not in response.text


def test_the_passphrase_is_not_in_the_audit_log(client, registered, db):
    from lembas.db.models import AuditEvent

    client.post(
        "/admin/updates/export",
        data={"passphrase": PASSPHRASE, "passphrase_confirm": PASSPHRASE},
    )
    for event in db.scalars(select(AuditEvent)):
        assert PASSPHRASE not in (event.summary or "")


# --- The command line ----------------------------------------------------------------------
def test_export_admin_writes_an_owner_only_file(db, tmp_path, monkeypatch):
    _connection(db)
    out = tmp_path / "admin.json"
    monkeypatch.setenv("EXPORT_PASSPHRASE", PASSPHRASE)
    result = runner.invoke(
        app, ["export", "admin", "--out", str(out), "--passphrase-env", "EXPORT_PASSPHRASE"]
    )
    assert result.exit_code == 0, result.output
    assert out.stat().st_mode & 0o777 == 0o600
    text = out.read_text()
    assert KEY not in text
    assert export_service.check_passphrase(export_service.load(text), PASSPHRASE)


def test_export_admin_without_a_passphrase_names_what_it_left_out(db, tmp_path):
    _connection(db)
    result = runner.invoke(app, ["export", "admin", "--out", str(tmp_path / "a.json")])
    assert result.exit_code == 0, result.output
    assert "left out: connection 'Upstream' api_key (no-passphrase)" in result.output


def test_a_named_but_empty_passphrase_variable_is_an_error(db, tmp_path, monkeypatch):
    monkeypatch.delenv("NOT_SET_HERE", raising=False)
    out = tmp_path / "a.json"
    result = runner.invoke(
        app, ["export", "admin", "--out", str(out), "--passphrase-env", "NOT_SET_HERE"]
    )
    assert result.exit_code == 1
    assert not out.exists()


# --- Memory --------------------------------------------------------------------------------
def _memories(db, email: str) -> None:
    from lembas.db.models import DEFAULT_GROUP, DataGroup, Memory, User
    from lembas.services import data_groups

    data_groups.ensure_default(db)
    if db.get(DataGroup, "work") is None:
        db.add(DataGroup(id="work", name="Work"))
    owner = db.scalar(select(User).where(User.email == email))
    db.add_all([
        Memory(owner_id=owner.id, content="Prefers metric units.", data_group_id=DEFAULT_GROUP),
        Memory(owner_id=owner.id, content="Works on the ring project.", data_group_id="work"),
    ])
    db.commit()


def test_the_memory_tab_offers_a_download(client, registered, db):
    _memories(db, registered["email"])
    form = _form(client.get("/settings?tab=memory").text, "/api/library/memories/export")
    assert 'method="get"' in form and 'type="submit"' in form
    # Two groups here, so the choice is offered.
    assert 'name="group"' in form and 'value="work"' in form


def test_a_person_downloads_their_own_memories(client, registered, db):
    _memories(db, registered["email"])
    response = client.get("/api/library/memories/export")
    assert response.status_code == 200
    assert "lembas-memory-export-" in response.headers["content-disposition"]
    document = export_service.load(response.text)
    assert document["kind"] == "memory"
    assert {i["data"]["data_group"] for i in document["items"]} == {"Default", "Work"}

    one = export_service.load(client.get("/api/library/memories/export?group=work").text)
    assert [i["data"]["content"] for i in one["items"]] == ["Works on the ring project."]


def test_nobody_downloads_somebody_elses_memories(client, registered, db):
    _memories(db, registered["email"])
    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    response = client.get("/api/library/memories/export")
    if response.status_code == 200:
        assert export_service.load(response.text)["items"] == []
    assert "ring project" not in response.text


def test_a_group_one_may_not_use_is_refused(client, registered, db):
    from lembas.db.models import DataGroup, User

    _memories(db, registered["email"])
    other = User(name="Sam", email="sam@shire.test", password_hash="x")
    db.add(other)
    db.commit()
    db.add(DataGroup(id="sams", name="Sam's own", owner_id=other.id))
    db.commit()
    response = client.get("/api/library/memories/export?group=sams")
    assert response.status_code == 404
    assert "Sam's own" not in response.text


def test_export_memory_from_the_command_line(client, registered, db, tmp_path):
    _memories(db, registered["email"])
    out = tmp_path / "memories.json"
    result = runner.invoke(
        app, ["export", "memory", "--user", registered["email"], "--group", "Work",
              "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    document = export_service.load(out.read_text())
    assert [i["data"]["content"] for i in document["items"]] == ["Works on the ring project."]
    assert runner.invoke(
        app, ["export", "memory", "--user", "nobody@shire.test", "--out", str(out)]
    ).exit_code == 1
