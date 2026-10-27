"""The export file, format 1: what it holds, and what it never holds in clear.

The next major version starts on a new database; this file is how an
administrator's configuration and each person's memories get there. Most of
what is asserted here is about secrets -- a key must never be readable in the
file without the passphrase -- and about the promise an importer relies on:
every item has a stable type and a name it can report.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from lembas import __version__
from lembas.db.models import (
    DEFAULT_GROUP,
    Connection,
    CustomTool,
    DataGroup,
    Group,
    ImageWorkflow,
    McpServer,
    Memory,
    Model,
    User,
)
from lembas.security.passwords import hash_password
from lembas.services import crypto, settings_store
from lembas.services import export as export_service

KEY = "sk-test-0123456789abcdef"
PASSPHRASE = "correct horse battery staple"


@pytest.fixture
def configured(db):
    """An instance with one of everything an administrator configures."""
    from lembas.services import data_groups

    data_groups.ensure_default(db)
    work = DataGroup(name="Work", description="Kept apart")
    team = Group(name="Team")
    db.add_all([work, team])
    db.commit()
    connection = Connection(
        name="Upstream",
        base_url="https://api.example.invalid/v1",
        api_key_encrypted=crypto.encrypt(KEY),
        extra_headers_json={"HTTP-Referer": "https://example.invalid", "X-Api-Key": "hdr-secret"},
        data_group_id=work.id,
    )
    db.add(connection)
    db.commit()
    model = Model(
        connection_id=connection.id,
        model_id="big-model",
        display_name="Big",
        kind="chat",
        context_length=131072,
        pricing_json={"unit": "money", "input": 1.5, "output": 6.0},
        params_json={"temperature": 0.3},
        reasoning_efforts=["low", "high"],
        public=False,
    )
    model.groups = [team]
    db.add(model)
    db.add(ImageWorkflow(slug="portrait", name="Portrait", workflow_json={"3": {"inputs": {}}}))
    db.add(
        CustomTool(
            slug="weather", name="Weather", url_template="https://weather.example.invalid/{city}",
            secret_encrypted=crypto.encrypt("tool-secret"), secret_placement="bearer",
        )
    )
    db.add(
        McpServer(
            slug="docs", name="Docs", url="https://mcp.example.invalid/",
            headers_json={"Authorization": "Bearer mcp-secret", "X-Client": "lembas"},
        )
    )
    db.commit()
    settings_store.update(
        db,
        {"stt_enabled": True, "stt_base_url": "https://stt.example.invalid",
         "stt_api_key_encrypted": crypto.encrypt("stt-secret")},
        key=settings_store.AUDIO,
    )
    settings_store.update(
        db,
        {"instance_name": "Test", "push_private_key": crypto.encrypt("PEM"),
         "push_public_key": "pub", "suggestions_seeded": True},
    )
    return {"connection": connection, "model": model, "work": work}


def _of(document, type_):
    return [item for item in document["items"] if item["type"] == type_]


def _one(document, type_):
    found = _of(document, type_)
    assert len(found) == 1, found
    return found[0]


# --- The header and the items ------------------------------------------------------------
def test_the_header_says_what_and_where_from(db, configured):
    document = export_service.admin(db)
    assert document["format"] == "lembas-export"
    assert document["format_version"] == 1
    assert document["kind"] == "admin"
    assert document["app_version"] == __version__
    assert document["created_at"].endswith("Z")
    assert export_service.load(export_service.dumps(document)) == document


def test_every_item_has_a_stable_type_and_a_name(db, configured):
    document = export_service.admin(db)
    assert document["items"]
    for item in document["items"]:
        assert item["type"] in export_service.TYPES, item
        assert isinstance(item["name"], str) and item["name"], item
        assert isinstance(item["ref"], str) and item["ref"], item
        assert isinstance(item["data"], dict), item
    counted = {t: len(_of(document, t)) for t in document["counts"]}
    assert counted == document["counts"]


def test_connections_models_and_services_are_all_there(db, configured):
    document = export_service.admin(db)
    connection = _one(document, "connection")
    model = _one(document, "model")
    assert connection["data"]["base_url"] == "https://api.example.invalid/v1"
    assert connection["data"]["data_group"] == configured["work"].id
    assert connection["data"]["extra_headers"]["HTTP-Referer"] == "https://example.invalid"
    assert model["data"]["connection"] == connection["ref"]
    assert model["name"] == f"{connection['data']['provider']}/big-model"
    assert model["data"]["pricing"] == {"unit": "money", "input": 1.5, "output": 6.0}
    assert model["data"]["kind"] == "chat"
    assert model["data"]["context_length"] == 131072
    assert model["data"]["reasoning_efforts"] == ["low", "high"]
    assert model["data"]["groups"] == ["Team"]
    assert {g["data"]["name"] for g in _of(document, "data_group")} >= {"Work"}
    assert _one(document, "image_workflow")["data"]["slug"] == "portrait"
    assert _one(document, "custom_tool")["data"]["secret_placement"] == "bearer"
    assert _one(document, "mcp_server")["data"]["headers"]["X-Client"] == "lembas"
    groups = {item["data"]["group"]: item for item in _of(document, "settings")}
    assert groups["audio"]["data"]["values"]["stt_base_url"] == "https://stt.example.invalid"
    assert "stt_api_key_encrypted" not in groups["audio"]["data"]["values"]


def test_instance_state_is_not_exported(db, configured):
    """The web-push keypair belongs to this instance, not to a service."""
    general = {i["data"]["group"]: i for i in _of(export_service.admin(db), "settings")}["general"]
    values = general["data"]["values"]
    assert values["instance_name"] == "Test"
    for key in ("push_private_key", "push_public_key", "suggestions_seeded"):
        assert key not in values


def test_a_personal_data_group_is_not_an_admins_to_export(db, configured):
    owner = db.scalar(select(User))
    if owner is None:
        owner = User(name="Sam", email="sam@shire.test", password_hash=hash_password("x"))
        db.add(owner)
        db.commit()
    db.add(DataGroup(name="Mine", owner_id=owner.id))
    db.commit()
    names = {g["data"]["name"] for g in _of(export_service.admin(db), "data_group")}
    assert "Mine" not in names


# --- Secrets -----------------------------------------------------------------------------
SECRETS = (KEY, "hdr-secret", "tool-secret", "mcp-secret", "stt-secret")


def test_without_a_passphrase_no_secret_is_in_the_file(db, configured):
    document = export_service.admin(db)
    text = export_service.dumps(document)
    for secret in SECRETS:
        assert secret not in text, secret
    assert document["encryption"] is None
    connection = _one(document, "connection")["data"]
    assert connection["api_key"] == {"$omitted": "no-passphrase"}
    assert connection["extra_headers"]["X-Api-Key"] == {"$omitted": "no-passphrase"}
    # Every one named, so an importer can say what it did not get.
    fields = {(s["type"], s["field"]) for s in document["secrets"]}
    assert fields == {
        ("connection", "api_key"),
        ("connection", "extra_headers.X-Api-Key"),
        ("custom_tool", "secret"),
        ("mcp_server", "headers.Authorization"),
        ("settings", "stt_api_key"),
    }
    assert {s["state"] for s in document["secrets"]} == {"omitted"}


def test_with_a_passphrase_each_secret_opens_and_none_is_in_clear(db, configured):
    document = export_service.admin(db, passphrase=PASSPHRASE)
    text = export_service.dumps(document)
    for secret in SECRETS:
        assert secret not in text, secret
    header = document["encryption"]
    assert header["kdf"] == "scrypt" and header["n"] >= 2**14 and header["salt"]
    loaded = export_service.load(text)
    connection = _one(loaded, "connection")["data"]
    assert export_service.open_secret(loaded, PASSPHRASE, connection["api_key"]) == KEY
    mcp = _one(loaded, "mcp_server")["data"]
    assert (
        export_service.open_secret(loaded, PASSPHRASE, mcp["headers"]["Authorization"])
        == "Bearer mcp-secret"
    )
    assert {s["state"] for s in loaded["secrets"]} == {"encrypted"}


def test_a_wrong_passphrase_is_known_before_anything_is_opened(db, configured):
    document = export_service.admin(db, passphrase=PASSPHRASE)
    assert export_service.check_passphrase(document, PASSPHRASE)
    assert not export_service.check_passphrase(document, "wrong")
    marker = _one(document, "connection")["data"]["api_key"]
    with pytest.raises(export_service.ExportError):
        export_service.open_secret(document, "wrong", marker)


def test_every_file_has_its_own_salt(db, configured):
    one = export_service.admin(db, passphrase=PASSPHRASE)["encryption"]["salt"]
    two = export_service.admin(db, passphrase=PASSPHRASE)["encryption"]["salt"]
    assert one != two


def test_a_key_this_instance_cannot_read_is_named_not_invented(db, configured):
    configured["connection"].api_key_encrypted = "not-a-fernet-token"
    db.commit()
    document = export_service.admin(db, passphrase=PASSPHRASE)
    assert _one(document, "connection")["data"]["api_key"] == {"$omitted": "unreadable"}
    entry = next(s for s in document["secrets"] if s["field"] == "api_key")
    assert entry["reason"] == "unreadable" and entry["name"] == "Upstream"


def test_no_key_is_no_secret(db, configured):
    configured["connection"].api_key_encrypted = ""
    db.commit()
    document = export_service.admin(db)
    assert _one(document, "connection")["data"]["api_key"] is None
    assert not [s for s in document["secrets"] if s["field"] == "api_key"]


# --- Memories ----------------------------------------------------------------------------
@pytest.fixture
def remembered(db):
    from lembas.services import data_groups

    data_groups.ensure_default(db)
    work = DataGroup(name="Work")
    owner = User(name="Frodo", email="f@shire.test", password_hash=hash_password("x"))
    other = User(name="Sam", email="s@shire.test", password_hash=hash_password("x"))
    db.add_all([work, owner, other])
    db.commit()
    db.add_all([
        Memory(owner_id=owner.id, content="Prefers metric units.", data_group_id=DEFAULT_GROUP),
        Memory(owner_id=owner.id, content="Works on the ring project.", data_group_id=work.id),
        Memory(owner_id=owner.id, content="Written before groups.", data_group_id=None),
        Memory(owner_id=other.id, content="Likes gardening.", data_group_id=DEFAULT_GROUP),
    ])
    db.commit()
    return owner, work


def test_a_persons_memories_with_their_data_groups(db, remembered):
    owner, work = remembered
    document = export_service.memory(db, owner)
    assert document["kind"] == "memory" and document["owner"]["email"] == "f@shire.test"
    by_content = {i["data"]["content"]: i for i in document["items"]}
    assert set(by_content) == {
        "Prefers metric units.", "Works on the ring project.", "Written before groups.",
    }
    assert by_content["Works on the ring project."]["data"]["data_group"] == "Work"
    assert by_content["Written before groups."]["data"]["data_group_ref"] == DEFAULT_GROUP
    assert all(i["type"] == "memory" and i["name"] for i in document["items"])
    assert "encryption" not in document


def test_one_data_group_only(db, remembered):
    owner, work = remembered
    document = export_service.memory(db, owner, group=work.id)
    assert [i["data"]["content"] for i in document["items"]] == ["Works on the ring project."]
    assert document["data_group"] == "Work"
    default = export_service.memory(db, owner, group=DEFAULT_GROUP)
    assert len(default["items"]) == 2  # NULL is the default group


# --- Reading a file ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({"format": "something-else", "format_version": 1, "items": []}),
        json.dumps({"format": "lembas-export", "items": []}),
        json.dumps({"format": "lembas-export", "format_version": 99, "items": []}),
        json.dumps({"format": "lembas-export", "format_version": 1}),
    ],
)
def test_what_is_not_an_export_is_refused_with_a_reason(text):
    with pytest.raises(export_service.ExportError):
        export_service.load(text)


def test_an_unknown_item_type_is_kept_for_the_importer_to_name():
    text = json.dumps({
        "format": "lembas-export", "format_version": 1, "kind": "admin",
        "items": [{"type": "from-the-future", "ref": "x", "name": "Something", "data": {}}],
    })
    assert export_service.load(text)["items"][0]["type"] == "from-the-future"
