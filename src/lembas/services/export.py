"""Export: what this instance holds, as a file another installation can read.

Two kinds, one format:

* **admin** -- connections and their models, the instance's data groups, the
  service settings (speech, web search, image generation and the rest), image
  workflows, custom tools and MCP servers. For an administrator, from
  Admin -> Updates or `lembas-server export admin`.
* **memory** -- one person's memories, each with its data group. From Settings
  -> Memory, or `lembas-server export memory`.

The next major version does not carry a 1.x database over. It starts on a new
one, and this file is how an administrator and each person bring across what
they want to keep.

## The format (version 1)

One JSON document: a header -- `format`, `format_version`, `kind`,
`app_version`, `created_at`, and `encryption` and `secrets` where a kind has
secrets -- and `items`. Every item is

    {"type": "<stable type>", "ref": "<id in this file>", "name": "<for people>",
     "data": {...}}

`type` says what the item is and never changes meaning within a format version;
`name` is what an importer says when it reports what it took and what it left.
An importer that meets a type it does not know, or a field it cannot map, names
the item and drops it -- it never fails the whole file. `ref` is how one item
points at another (a model at its connection); it is the row's id here and means
nothing outside the file.

## Secrets

A secret is never written in clear. With a passphrase each one is a Fernet token
under a key derived from the passphrase by scrypt, with a random salt per file;
the salt and the scrypt parameters are in the header, beside a token of a known
text so an importer can tell a wrong passphrase before it touches anything else.
Without a passphrase a secret is left out. Either way its place holds a marker,
`{"$secret": "<token>"}` or `{"$omitted": "<why>"}`, and the header's `secrets`
names every one: which item, which field, and what became of it.

What counts as a secret: every stored key (a connection's API key, the speech,
search and image services' keys, a custom tool's or MCP server's secret), and a
header whose name says it carries a credential (`Authorization`, anything with
`key`, `token`, `secret`, `auth`, `cookie`, `password` or `session` in it).

The Wiki page *Export-format* is the long form of this, for whoever writes an
importer.
"""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas import __version__
from lembas.db.models import (
    DEFAULT_GROUP,
    Connection,
    CustomTool,
    DataGroup,
    ImageWorkflow,
    McpServer,
    Setting,
    User,
)
from lembas.services import crypto, settings_store

FORMAT = "lembas-export"
FORMAT_VERSION = 1

KIND_ADMIN = "admin"
KIND_MEMORY = "memory"
KINDS = (KIND_ADMIN, KIND_MEMORY)

# Item types. Stable: a type keeps its meaning for as long as format 1 does.
TYPE_CONNECTION = "connection"
TYPE_MODEL = "model"
TYPE_DATA_GROUP = "data_group"
TYPE_SETTINGS = "settings"
TYPE_IMAGE_WORKFLOW = "image_workflow"
TYPE_CUSTOM_TOOL = "custom_tool"
TYPE_MCP_SERVER = "mcp_server"
TYPE_MEMORY = "memory"
TYPES = (
    TYPE_CONNECTION,
    TYPE_MODEL,
    TYPE_DATA_GROUP,
    TYPE_SETTINGS,
    TYPE_IMAGE_WORKFLOW,
    TYPE_CUSTOM_TOOL,
    TYPE_MCP_SERVER,
    TYPE_MEMORY,
)

# What a secret's place holds.
SECRET_MARK = "$secret"
OMITTED_MARK = "$omitted"
# Why a secret was left out.
OMITTED_NO_PASSPHRASE = "no-passphrase"
# Stored, but this instance cannot read it: LEMBAS_SECRET_KEY changed since.
OMITTED_UNREADABLE = "unreadable"

# scrypt: 2^15 x 8 is 32 MiB and a fraction of a second -- the parameters the
# scrypt paper and the `cryptography` documentation suggest for interactive use.
# Written into every file, so an importer reads them rather than assuming them,
# and a later release can raise them without breaking the files already made.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
# What the header's `check` token holds, so a wrong passphrase is known at once.
CHECK_TEXT = "lembas-export"

# A header whose name says it carries a credential.
SENSITIVE_HEADER = re.compile(r"auth|key|token|secret|cookie|password|session", re.I)

# Settings groups, as a person names them. A stored group not listed here is
# still exported, under its key.
SETTINGS_NAMES = {
    settings_store.GENERAL: "General",
    settings_store.AUDIO: "Speech (speech-to-text and text-to-speech)",
    settings_store.SEARCH: "Web search and fetching pages",
    settings_store.PROMPTS: "Prompt overrides",
    settings_store.AGENTS: "Agent chats",
    settings_store.IMAGES: "Image generation",
    settings_store.SCHEDULES: "Schedules",
    settings_store.SUBAGENTS: "Subagents",
    settings_store.EXTRACTION: "Files and semantic search",
    settings_store.CREDITS: "Credits",
    settings_store.UPDATES: "Updates",
}

# Stored settings that are this instance's own state rather than anybody's
# configuration, and are never exported: the web-push keypair (a new
# installation makes its own, and its private half is a secret of the instance,
# not of a service) and the one-off seeding flag.
NOT_EXPORTED_SETTINGS = {
    settings_store.GENERAL: ("push_private_key", "push_public_key", "suggestions_seeded"),
}

ENCRYPTED_SUFFIX = "_encrypted"


class ExportError(ValueError):
    """A file that is not one this module can read, or a wrong passphrase."""


# --- Secrets ---------------------------------------------------------------------------
def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def derive_key(passphrase: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    """The Fernet key a passphrase makes with this salt and these parameters."""
    raw = Scrypt(salt=salt, length=KEY_BYTES, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


class _Secrets:
    """Turns stored secrets into markers, and remembers each one for the header."""

    def __init__(self, passphrase: str = "") -> None:
        self.listed: list[dict[str, str]] = []
        self.header: dict[str, Any] | None = None
        self._fernet: Fernet | None = None
        if passphrase:
            salt = os.urandom(SALT_BYTES)
            key = derive_key(passphrase, salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
            self._fernet = Fernet(key)
            self.header = {
                "scheme": "scrypt+fernet",
                "kdf": "scrypt",
                "salt": _b64(salt),
                "n": SCRYPT_N,
                "r": SCRYPT_R,
                "p": SCRYPT_P,
                "length": KEY_BYTES,
                "check": self._fernet.encrypt(CHECK_TEXT.encode()).decode("ascii"),
            }

    def _mark(self, plain: str, *, stored: bool, item: dict, field: str) -> dict[str, str] | None:
        """The marker for one secret, or None when nothing was stored."""
        if not stored:
            return None
        entry = {"type": item["type"], "ref": item["ref"], "name": item["name"], "field": field}
        if not plain:
            self.listed.append({**entry, "state": "omitted", "reason": OMITTED_UNREADABLE})
            return {OMITTED_MARK: OMITTED_UNREADABLE}
        if self._fernet is None:
            self.listed.append({**entry, "state": "omitted", "reason": OMITTED_NO_PASSPHRASE})
            return {OMITTED_MARK: OMITTED_NO_PASSPHRASE}
        self.listed.append({**entry, "state": "encrypted"})
        return {SECRET_MARK: self._fernet.encrypt(plain.encode("utf-8")).decode("ascii")}

    def stored(self, ciphertext: str, *, item: dict, field: str) -> dict[str, str] | None:
        """A secret this instance keeps Fernet-encrypted under its own key."""
        return self._mark(
            crypto.decrypt(ciphertext or ""), stored=bool(ciphertext), item=item, field=field
        )

    def headers(self, headers: dict[str, Any] | None, *, item: dict, field: str) -> dict:
        """Extra headers, with the values of credential-bearing ones as secrets."""
        out: dict[str, Any] = {}
        for name, value in (headers or {}).items():
            if SENSITIVE_HEADER.search(str(name)) and value not in ("", None):
                out[name] = self._mark(str(value), stored=True, item=item, field=f"{field}.{name}")
            else:
                out[name] = value
        return out


def open_secret(document: dict[str, Any], passphrase: str, marker: Any) -> str | None:
    """The clear text of one `{"$secret": …}` marker, or None for an omitted
    one. For importers and tests; raises ExportError on a wrong passphrase."""
    if not isinstance(marker, dict) or SECRET_MARK not in marker:
        return None
    fernet = _fernet_for(document, passphrase)
    try:
        return fernet.decrypt(str(marker[SECRET_MARK]).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ExportError("A secret in this file could not be decrypted.") from exc


def _fernet_for(document: dict[str, Any], passphrase: str) -> Fernet:
    params = document.get("encryption")
    if not isinstance(params, dict):
        raise ExportError("This file carries no encrypted secrets.")
    try:
        salt = base64.b64decode(str(params["salt"]))
        key = derive_key(
            passphrase, salt, n=int(params["n"]), r=int(params["r"]), p=int(params["p"])
        )
        fernet = Fernet(key)
        fernet.decrypt(str(params["check"]).encode("ascii"))
    except (KeyError, TypeError, ValueError, InvalidToken) as exc:
        raise ExportError("Wrong passphrase, or the file's encryption header is damaged.") from exc
    return fernet


def check_passphrase(document: dict[str, Any], passphrase: str) -> bool:
    """Whether the passphrase opens this file's secrets."""
    try:
        _fernet_for(document, passphrase)
    except ExportError:
        return False
    return True


# --- The document ----------------------------------------------------------------------
def _when(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # SQLite hands back what it was given without the offset; every
        # timestamp here was written in UTC.
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _document(kind: str, items: list[dict[str, Any]], **header: Any) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "kind": kind,
        "app_version": __version__,
        "created_at": _when(datetime.now(UTC)),
        **header,
        "counts": counts,
        "items": items,
    }


def _item(type_: str, ref: str, name: str) -> dict[str, Any]:
    return {"type": type_, "ref": ref, "name": name, "data": {}}


def _group_names(groups: Iterable[Any]) -> list[str]:
    return sorted(group.name for group in groups)


# --- Admin -----------------------------------------------------------------------------
def admin(db: DBSession, *, passphrase: str = "") -> dict[str, Any]:
    """Everything an administrator configured, with secrets protected."""
    secrets = _Secrets(passphrase)
    items: list[dict[str, Any]] = []

    groups = list(
        db.scalars(
            select(DataGroup).where(DataGroup.owner_id.is_(None)).order_by(DataGroup.position)
        )
    )
    known_groups = {group.id for group in groups}
    for group in groups:
        item = _item(TYPE_DATA_GROUP, group.id, group.name)
        item["data"] = {
            "name": group.name,
            "description": group.description,
            "position": group.position,
            "default": group.id == DEFAULT_GROUP,
            "embedding_model_id": group.embedding_model_id,
            "embedding_connection": group.embedding_connection_id or None,
            "review_model_id": group.review_model_id,
            "review_connection": group.review_connection_id or None,
        }
        items.append(item)

    by_position = select(Connection).order_by(Connection.position, Connection.name)
    for connection in db.scalars(by_position):
        item = _item(TYPE_CONNECTION, connection.id, connection.name)
        group = connection.data_group_id
        item["data"] = {
            "name": connection.name,
            "kind": connection.kind,
            "provider": connection.provider_name,
            "base_url": connection.base_url,
            "api_key": secrets.stored(connection.api_key_encrypted, item=item, field="api_key"),
            "extra_headers": secrets.headers(
                connection.extra_headers_json, item=item, field="extra_headers"
            ),
            "enabled": connection.enabled,
            "position": connection.position,
            "unload_url": connection.unload_url,
            "unload_method": connection.unload_method,
            "one_model_at_a_time": connection.one_model_at_a_time,
            # A personal data group is somebody's own, and not in this file.
            "data_group": group if group in known_groups else None,
        }
        items.append(item)
        for model in connection.models:
            entry = _item(TYPE_MODEL, model.id, f"{connection.provider_name}/{model.model_id}")
            entry["data"] = {
                "connection": connection.id,
                "model_id": model.model_id,
                "display_name": model.display_name,
                "description": model.description,
                "notes": model.notes,
                "kind": model.kind,
                "enabled": model.enabled,
                "position": model.position,
                "pinned": model.pinned,
                "single_session": model.single_session,
                "public": model.public,
                "groups": _group_names(model.groups),
                "system_prompt": model.system_prompt,
                "capabilities": dict(model.capabilities_json or {}),
                "params": dict(model.params_json or {}),
                "pricing": dict(model.pricing_json or {}),
                "context_length": model.context_length,
                "output_limit": model.output_limit,
                "reasoning_efforts": list(model.reasoning_efforts or []),
            }
            items.append(entry)

    for row in db.scalars(select(Setting).order_by(Setting.key)):
        if not isinstance(row.value, dict) or not row.value:
            continue
        item = _item(TYPE_SETTINGS, row.key, SETTINGS_NAMES.get(row.key, row.key))
        values: dict[str, Any] = {}
        skipped = NOT_EXPORTED_SETTINGS.get(row.key, ())
        for key, value in sorted(row.value.items()):
            if key in skipped:
                continue
            if key.endswith(ENCRYPTED_SUFFIX):
                name = key.removesuffix(ENCRYPTED_SUFFIX)
                values[name] = secrets.stored(str(value or ""), item=item, field=name)
            else:
                values[key] = value
        item["data"] = {"group": row.key, "values": values}
        items.append(item)

    for workflow in db.scalars(select(ImageWorkflow).order_by(ImageWorkflow.position)):
        item = _item(TYPE_IMAGE_WORKFLOW, workflow.id, workflow.name)
        item["data"] = {
            "slug": workflow.slug,
            "name": workflow.name,
            "description": workflow.description,
            "workflow": dict(workflow.workflow_json or {}),
            "enabled": workflow.enabled,
            "position": workflow.position,
        }
        items.append(item)

    for tool in db.scalars(select(CustomTool).order_by(CustomTool.position, CustomTool.slug)):
        item = _item(TYPE_CUSTOM_TOOL, tool.id, tool.name)
        item["data"] = {
            "slug": tool.slug,
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters_json or {}),
            "guidance": tool.guidance,
            "method": tool.method,
            "url_template": tool.url_template,
            "body_template": tool.body_template,
            "headers": secrets.headers(tool.headers_json, item=item, field="headers"),
            "secret": secrets.stored(tool.secret_encrypted, item=item, field="secret"),
            "secret_placement": tool.secret_placement,
            "secret_name": tool.secret_name,
            "response_mode": tool.response_mode,
            "response_path": tool.response_path,
            "max_chars": tool.max_chars,
            "timeout": tool.timeout,
            "allow_private": tool.allow_private,
            "enabled": tool.enabled,
            "public": tool.public,
            "position": tool.position,
            "groups": _group_names(tool.groups),
        }
        items.append(item)

    for server in db.scalars(select(McpServer).order_by(McpServer.position, McpServer.slug)):
        item = _item(TYPE_MCP_SERVER, server.id, server.name)
        item["data"] = {
            "slug": server.slug,
            "name": server.name,
            "url": server.url,
            "guidance": server.guidance,
            "headers": secrets.headers(server.headers_json, item=item, field="headers"),
            "secret": secrets.stored(server.secret_encrypted, item=item, field="secret"),
            "secret_placement": server.secret_placement,
            "secret_name": server.secret_name,
            "timeout": server.timeout,
            "max_chars": server.max_chars,
            "allow_private": server.allow_private,
            "tool_overrides": dict(server.tool_overrides_json or {}),
            "enabled": server.enabled,
            "public": server.public,
            "position": server.position,
            "groups": _group_names(server.groups),
        }
        items.append(item)

    return _document(
        KIND_ADMIN, items, encryption=secrets.header, secrets=secrets.listed
    )


# --- Memory ----------------------------------------------------------------------------
NAME_CHARS = 60


def memory(db: DBSession, user: User, *, group: str | None = None) -> dict[str, Any]:
    """One person's memories -- all of them, or one data group's. No secrets:
    a memory is shown to a model on every turn, and the page says never to put
    a key in one."""
    from lembas.services import data_groups
    from lembas.services.library import memories as memories_service

    names = {g.id: g.name for g in db.scalars(select(DataGroup))}
    items = []
    for record in memories_service.all_for(db, user, group):
        group_id = record.data_group_id or DEFAULT_GROUP
        text = " ".join(record.content.split())
        name = text if len(text) <= NAME_CHARS else text[: NAME_CHARS - 1].rstrip() + "…"
        item = _item(TYPE_MEMORY, record.id, name)
        item["data"] = {
            "content": record.content,
            "author": record.author,
            "data_group": names.get(group_id) or data_groups.DEFAULT_NAME,
            "data_group_ref": group_id,
            "created_at": _when(record.created_at),
            "updated_at": _when(record.updated_at),
        }
        items.append(item)
    header: dict[str, Any] = {"owner": {"name": user.name, "email": user.email}}
    if group is not None:
        header["data_group"] = names.get(group, group)
    return _document(KIND_MEMORY, items, **header)


# --- Reading one back ------------------------------------------------------------------
def dumps(document: dict[str, Any]) -> str:
    """The file's text: UTF-8 JSON, indented so a person can read what is in it."""
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def load(text: str | bytes) -> dict[str, Any]:
    """A document, checked for the header an importer relies on. Unknown item
    types and fields are kept: deciding what to drop is the importer's job, and
    it has to name what it drops."""
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise ExportError("This is not a JSON file.") from exc
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise ExportError("This is not a LLeMbas export.")
    version = document.get("format_version")
    if not isinstance(version, int) or version < 1:
        raise ExportError("This export does not say which format version it is.")
    if version > FORMAT_VERSION:
        raise ExportError(
            f"This export is format version {version}; this release reads up to {FORMAT_VERSION}."
        )
    if not isinstance(document.get("items"), list):
        raise ExportError("This export has no items.")
    return document


def filename(kind: str, when: datetime | None = None) -> str:
    """`lembas-admin-export-20261027-1530.json`: a name that says what and when."""
    when = when or datetime.now(UTC)
    return f"lembas-{kind}-export-{when.strftime('%Y%m%d-%H%M')}.json"


__all__ = [
    "CHECK_TEXT",
    "FORMAT",
    "FORMAT_VERSION",
    "KINDS",
    "KIND_ADMIN",
    "KIND_MEMORY",
    "OMITTED_MARK",
    "OMITTED_NO_PASSPHRASE",
    "OMITTED_UNREADABLE",
    "SECRET_MARK",
    "TYPES",
    "ExportError",
    "admin",
    "check_passphrase",
    "derive_key",
    "dumps",
    "filename",
    "load",
    "memory",
    "open_secret",
]
