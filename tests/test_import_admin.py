"""An administrator's 1.7 `lembas-export` file into 2.0: map, name, drop.

The files are 1.7.3's own fixtures (`fixtures/export/`, copied from the 1.7
line), so this reads what 1.7 really writes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from lembas.db.models import Connection, CustomTool, DataGroup, McpServer, Model
from lembas.services import crypto, export_format, import_admin, settings_store

FIXTURES = Path(__file__).parent / "fixtures" / "export"
PASSPHRASE = "fixture passphrase, not a real one"


def _doc(name: str) -> dict:
    return import_admin.load((FIXTURES / name).read_bytes())


def test_the_encrypted_file_maps_onto_2_0(db):
    report = import_admin.run(db, _doc("admin-encrypted.json"), passphrase=PASSPHRASE)
    cloud = db.scalar(select(Connection).where(Connection.provider == "example-cloud"))
    local = db.scalar(select(Connection).where(Connection.provider == "local-llama"))
    assert cloud.provider_type == "openai_compatible" and cloud.kind == "openai"
    assert crypto.decrypt(cloud.api_key_encrypted)  # re-encrypted under this instance's key
    assert local.one_model_at_a_time and local.unload_method == "GET"
    work = db.scalar(select(DataGroup).where(DataGroup.name == "Work"))
    assert local.data_group_id == work.id
    large = db.scalar(select(Model).where(Model.model_id == "example-large"))
    # Set by hand in 1.x: the override; an empty dict leaves detection to decide.
    assert large.capabilities_override_json == {"vision": True, "tools": True, "reasoning": True}
    small = db.scalar(select(Model).where(Model.model_id == "small-local-8b"))
    assert small.capabilities_override_json == {} and small.context_length == 32768
    audio = settings_store.audio(db)
    assert audio["stt_enabled"] and crypto.decrypt(audio["stt_api_key_encrypted"])
    mcp = db.scalar(select(McpServer).where(McpServer.slug == "docs"))
    assert mcp.headers_json["Authorization"] and mcp.headers_json["X-Client"] == "lembas"
    assert db.scalar(select(CustomTool).where(CustomTool.slug == "weather")) is not None
    skipped = {(e.kind, e.name): e.why_english() for e in report.skipped}
    # Named and dropped: allow_signup is gone in 2.0; the 1.x default is ours.
    assert "allow_signup" in skipped[("settings", "General")]
    assert ("data_group", "Default") in skipped
    taken = {(e.kind, e.name) for e in report.taken}
    assert ("connection", "Example Cloud") in taken
    assert ("model", "example-cloud/example-large") in taken
    # The model's user group is not here, and the report says so.
    assert any(e.name == "example-cloud/example-large" and "Team" in e.why_english()
               for e in report.taken)


def test_a_second_run_takes_nothing_twice(db):
    import_admin.run(db, _doc("admin-encrypted.json"), passphrase=PASSPHRASE)
    report = import_admin.run(db, _doc("admin-encrypted.json"), passphrase=PASSPHRASE)
    assert not [e for e in report.taken if e.kind in ("connection", "model", "custom_tool",
                                                      "mcp_server", "image_workflow")]
    assert len(db.scalars(select(Connection)).all()) == 2


def test_the_passphrase_is_checked_before_anything_is_written(db):
    with pytest.raises(export_format.Refused):
        import_admin.run(db, _doc("admin-encrypted.json"), passphrase="wrong")
    with pytest.raises(export_format.Refused):
        import_admin.run(db, _doc("admin-encrypted.json"))
    assert db.scalars(select(Connection)).all() == []


def test_the_plain_file_lists_the_keys_to_type_again(db):
    report = import_admin.run(db, _doc("admin-plain.json"))
    assert report.secrets
    assert all("no-passphrase" in e.why_english() for e in report.secrets)
    cloud = db.scalar(select(Connection).where(Connection.provider == "example-cloud"))
    assert cloud is not None and cloud.api_key_encrypted == ""


def test_kinds_and_hosts_become_provider_types(db):
    document = {
        "format": "lembas-export", "format_version": 1, "kind": "admin", "app_version": "1.7.3",
        "encryption": None, "secrets": [],
        "items": [
            {"type": "connection", "ref": "a", "name": "Claude",
             "data": {"name": "Claude", "kind": "anthropic", "provider": "claude",
                      "base_url": "https://api.anthropic.com"}},
            {"type": "connection", "ref": "b", "name": "Router",
             "data": {"name": "Router", "kind": "openai", "provider": "router",
                      "base_url": "https://openrouter.ai/api/v1"}},
            {"type": "model", "ref": "m", "name": "orphan",
             "data": {"connection": "nowhere", "model_id": "x"}},
            {"type": "canvas", "ref": "c", "name": "A canvas", "data": {}},
            {"type": "settings", "ref": "credits", "name": "Credits",
             "data": {"group": "credits", "values": {"x": 1}}},
        ],
    }
    report = import_admin.run(db, document)
    types = {c.provider: c.provider_type for c in db.scalars(select(Connection))}
    assert types == {"claude": "anthropic", "router": "openrouter"}
    skipped = {(e.kind, e.name): e.why_english() for e in report.skipped}
    assert skipped[("model", "orphan")] == "its connection was not imported"
    assert ("canvas", "A canvas") in skipped
    assert skipped[("settings", "Credits")] == "2.0 has no credits"


def test_a_memory_file_is_refused_here():
    with pytest.raises(export_format.Refused):
        import_admin.load((FIXTURES / "memory.json").read_bytes())
    with pytest.raises(export_format.Refused):
        import_admin.load(json.dumps({"format": "lembas-export", "format_version": 2,
                                      "kind": "admin", "items": []}).encode())


def test_the_admin_page_imports_a_file(client, registered):
    raw = (FIXTURES / "admin-encrypted.json").read_bytes()
    response = client.post(
        "/admin/import/file",
        files={"file": ("admin.json", raw, "application/json")},
        data={"passphrase": PASSPHRASE},
    )
    assert response.status_code == 200
    assert "Example Cloud" in response.text
    wrong = client.post(
        "/admin/import/file",
        files={"file": ("admin.json", raw, "application/json")},
        data={"passphrase": "nope"},
    )
    assert wrong.status_code == 200 and "Wrong passphrase" in wrong.text


def test_the_command_line_imports_a_file(db, monkeypatch):
    from typer.testing import CliRunner

    from lembas.cli import app as cli_app

    monkeypatch.setenv("EXPORT_PASS", PASSPHRASE)
    result = CliRunner().invoke(cli_app, ["import", "admin", str(FIXTURES / "admin-encrypted.json"),
                                          "--passphrase-env", "EXPORT_PASS"])
    assert result.exit_code == 0, result.output
    assert "connection: Example Cloud" in result.output
