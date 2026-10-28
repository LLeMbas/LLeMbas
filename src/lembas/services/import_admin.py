"""An administrator's `lembas-export` file in: connections, models and services.

The file is the one 1.7 writes (Admin → Updates → *Export the configuration*;
`LLeMbas.wiki/Export-format.md`), read by the shared reader
(`services/export_format.py`). 2.0 starts on an empty database, and this is how
an instance's configuration comes across -- on purpose, by an administrator,
after the update.

**The rules it keeps** (the format page's *Importing*):

1. The header first; then, when the file's keys are encrypted, the passphrase,
   tested on the header's `check` before anything is written.
2. Each item on its own. A type this release does not know, a reference to an
   item that was skipped, a value outside what 2.0 accepts, or a name already
   taken here: the item is skipped and named, with the reason. Nothing fails
   the whole import after the header.
3. Unknown fields in a known item are ignored.
4. Every key the file left out, or that did not open, is listed, so the
   administrator knows which to type in again.

**How 1.x maps onto 2.0.**

- `connection.kind` becomes a provider type: `anthropic` is Anthropic, a known
  cloud host (`api.openai.com`, `api.deepseek.com`, `openrouter.ai`, …) is its
  own type, anything else is OpenAI-compatible (`providers.from_legacy_kind`).
  The provider name is kept, because `provider/model` is how `/v1` and the CLI
  name a model; a provider name already in use here skips the connection, and
  its models with it.
- A model's `capabilities` become the administrator's override only when they
  were set by hand in 1.x -- 1.x wrote that dict only from the model's form, so
  a non-empty one was somebody's choice. An empty one leaves detection to
  decide. Prices come across when they are money (`"unit": "money"`); credit
  prices are gone with credits.
- A connection's currency is the 1.x instance's (`settings.general.currency`).
- Data groups go into the default organisation; the default group is this
  instance's own default group. User groups are matched by name, and a name
  not found here is said beside the model or tool it was on.
- Settings groups are taken key by key: what this release still has is set,
  what it does not is named. Credits are gone, so is that group.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    DEFAULT_GROUP,
    DEFAULT_ORG,
    MODEL_KINDS,
    Connection,
    CustomTool,
    DataGroup,
    Group,
    ImageWorkflow,
    McpServer,
    Model,
)
from lembas.db.models.connection import PROVIDER_PATTERN
from lembas.db.models.tool import RESPONSE_MODES, SECRET_PLACEMENTS
from lembas.services import crypto, export_format, prompts, settings_store
from lembas.services.import_report import Entry, Report
from lembas.services.llm import capabilities as capabilities_service
from lembas.services.llm import providers
from lembas.web.i18n import N_, t

log = logging.getLogger(__name__)

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
MCP_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")

# Settings groups this release takes key by key, against its own defaults.
KEYED_SETTINGS = (
    settings_store.GENERAL,
    settings_store.AUDIO,
    settings_store.SEARCH,
    settings_store.IMAGES,
    settings_store.EXTRACTION,
    settings_store.SCHEDULES,
    settings_store.AGENTS,
    settings_store.SUBAGENTS,
    settings_store.UPDATES,
)
# Stored in a settings group, but this instance's own and not configuration.
NOT_TAKEN = {settings_store.GENERAL: {"logo_blob_hash", "logo_mime", "push_private_key",
                                      "push_public_key", "suggestions_seeded"}}
GONE_SETTINGS = {"credits"}
ENCRYPTED = "_encrypted"

ORDER = ("data_group", "connection", "model", "settings", "image_workflow", "custom_tool",
         "mcp_server")


def load(raw: bytes) -> dict[str, Any]:
    return export_format.load(raw, export_format.KIND_ADMIN)


def needs_passphrase(document: dict[str, Any]) -> bool:
    return isinstance(document.get("encryption"), dict)


class _Run:
    def __init__(self, db: DBSession, document: dict[str, Any], fernet: Fernet | None) -> None:
        self.db, self.document, self.fernet = db, document, fernet
        self.report = Report()
        # ref in the file -> id here, per type; a ref absent was skipped.
        self.refs: dict[str, dict[str, str]] = {kind: {} for kind in ORDER}
        general = next(
            (
                item.get("data", {}).get("values", {})
                for item in document.get("items", [])
                if isinstance(item, dict) and item.get("type") == "settings"
                and isinstance(item.get("data"), dict)
                and item["data"].get("group") == settings_store.GENERAL
            ),
            {},
        )
        currency = str((general or {}).get("currency") or "").upper()
        self.currency = currency if re.fullmatch(r"[A-Z]{3}", currency) else ""

    # --- secrets ---------------------------------------------------------------
    def secret(self, kind: str, name: str, field: str, marker: Any) -> str:
        """The clear text of a secret in the file, "" when there is none; an
        omitted or unopenable one is listed in the report."""
        if marker in (None, ""):
            return ""
        if isinstance(marker, str):
            # A file that wrote a key in clear is not version 1 as written; the
            # value is still the administrator's, and still encrypted here.
            return marker
        why = export_format.omitted(marker)
        if why:
            self.report.secret(kind, name, N_("%(field)s was left out of the file (%(why)s)"),
                               field=field, why=why)
            return ""
        plain = export_format.open_secret(self.fernet, marker)
        if not plain:
            self.report.secret(kind, name, N_("%(field)s did not open with the passphrase"),
                               field=field)
        return plain

    def headers(self, kind: str, name: str, field: str, raw: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        if not isinstance(raw, dict):
            return out
        for key, value in raw.items():
            if isinstance(value, dict):
                plain = self.secret(kind, name, f"{field}.{key}", value)
                if plain:
                    out[str(key)] = plain
            elif value not in (None, ""):
                out[str(key)] = str(value)
        return out

    def groups(self, names: Any) -> tuple[list[Group], list[str]]:
        found, missing = [], []
        for name in names if isinstance(names, list) else []:
            group = self.db.scalar(select(Group).where(Group.name == str(name)))
            if group is None:
                missing.append(str(name))
            else:
                found.append(group)
        return found, missing

    # --- items -----------------------------------------------------------------
    def data_group(self, item: dict, data: dict) -> None:
        name = str(data.get("name") or item.get("name") or "").strip()[:120]
        if data.get("default") or item.get("ref") == DEFAULT_GROUP:
            group = self.db.get(DataGroup, DEFAULT_GROUP)
            if group is None:
                from lembas.services import data_groups

                group = data_groups.ensure_default(self.db)
            self.refs["data_group"][str(item.get("ref"))] = group.id
            self.report.skip("data_group", name or group.name,
                             N_("the default group is this instance's own"))
            return
        if not name:
            self.report.skip("data_group", str(item.get("ref")), N_("it has no name"))
            return
        existing = self.db.scalar(
            select(DataGroup).where(DataGroup.owner_id.is_(None), DataGroup.name == name)
        )
        if existing is not None:
            self.refs["data_group"][str(item.get("ref"))] = existing.id
            self.report.skip("data_group", name, N_("a data group of that name is already here"))
            return
        group = DataGroup(
            org_id=DEFAULT_ORG,
            name=name,
            description=str(data.get("description") or ""),
            position=_int(data.get("position")),
            embedding_model_id=str(data.get("embedding_model_id") or ""),
            review_model_id=str(data.get("review_model_id") or ""),
        )
        self.db.add(group)
        self.db.flush()
        self.refs["data_group"][str(item.get("ref"))] = group.id
        # The connections it names come later in the file; resolved after them.
        self._group_links = getattr(self, "_group_links", [])
        self._group_links.append((group, data))
        self.report.take("data_group", name)

    def connection(self, item: dict, data: dict) -> None:
        name = str(data.get("name") or item.get("name") or "").strip()[:120]
        base_url = str(data.get("base_url") or "").strip()[:500]
        if not name or not base_url.startswith(("http://", "https://")):
            self.report.skip("connection", name or str(item.get("ref")),
                             N_("it has no name or no http(s) address"))
            return
        provider = str(data.get("provider") or "").strip().lower()
        if provider and not PROVIDER_PATTERN.match(provider):
            provider = ""
        if provider and self.db.scalar(
            select(Connection.id).where(Connection.provider == provider)
        ):
            self.report.skip("connection", name,
                             N_("a connection with the provider name %(provider)s is already "
                                "here"), provider=provider)
            return
        kind = str(data.get("kind") or "openai")
        if kind not in ("openai", "anthropic"):
            self.report.skip("connection", name, N_("unknown kind %(kind)s"), kind=kind)
            return
        row = Connection(
            name=name,
            base_url=base_url,
            provider_type=providers.from_legacy_kind(kind, base_url),
            provider=provider or None,
            api_key_encrypted=crypto.encrypt(
                self.secret("connection", name, "api_key", data.get("api_key"))
            ),
            extra_headers_json=self.headers("connection", name, "extra_headers",
                                            data.get("extra_headers")),
            enabled=bool(data.get("enabled", True)),
            position=_int(data.get("position")),
            unload_url=str(data.get("unload_url") or "")[:500],
            unload_method=str(data.get("unload_method") or "POST").upper()[:8],
            one_model_at_a_time=bool(data.get("one_model_at_a_time")),
            data_group_id=self.refs["data_group"].get(str(data.get("data_group") or "")) or None,
        )
        if self.currency:
            row.currency = self.currency
        self.db.add(row)
        self.db.flush()
        self.refs["connection"][str(item.get("ref"))] = row.id
        self.report.take("connection", name, N_("as %(type)s"),
                         type=providers.get(row.provider_type).label)

    def model(self, item: dict, data: dict) -> None:
        name = str(item.get("name") or data.get("model_id") or "")
        connection_id = self.refs["connection"].get(str(data.get("connection") or ""))
        if connection_id is None:
            self.report.skip("model", name, N_("its connection was not imported"))
            return
        model_id = str(data.get("model_id") or "").strip()[:300]
        if not model_id:
            self.report.skip("model", name, N_("it has no model id"))
            return
        if self.db.scalar(select(Model.id).where(Model.connection_id == connection_id,
                                                 Model.model_id == model_id)):
            self.report.skip("model", name, N_("already here"))
            return
        kind = data.get("kind")
        row = Model(
            connection_id=connection_id,
            model_id=model_id,
            display_name=str(data.get("display_name") or "")[:300],
            description=str(data.get("description") or ""),
            notes=str(data.get("notes") or ""),
            kind=kind if kind in MODEL_KINDS else None,
            enabled=bool(data.get("enabled", True)),
            position=_int(data.get("position")),
            pinned=bool(data.get("pinned")),
            single_session=bool(data.get("single_session")),
            public=bool(data.get("public", True)),
            system_prompt=str(data.get("system_prompt") or ""),
            params_json=data.get("params") if isinstance(data.get("params"), dict) else {},
            context_length=max(_int(data.get("context_length")), 0),
            output_limit=max(_int(data.get("output_limit")), 0),
            reasoning_efforts=[str(e) for e in data.get("reasoning_efforts") or []
                               if isinstance(e, str)],
        )
        said: list[str] = []
        pricing = data.get("pricing") if isinstance(data.get("pricing"), dict) else {}
        if pricing and pricing.get("unit") == "money":
            row.pricing_json = dict(pricing)
        elif any(pricing.get(k) for k in ("input", "output", "cached_input", "per_request")):
            said.append(N_("its prices were credits, which 2.0 does not have"))
        found, missing = self.groups(data.get("groups"))
        if not row.public:
            row.groups = found
        self.db.add(row)
        caps = data.get("capabilities")
        if isinstance(caps, dict) and caps:
            capabilities_service.set_override(row, caps)
            said.append(N_("capabilities as set by hand"))
        self.db.flush()
        self.refs["model"][str(item.get("ref"))] = row.id
        reasons = ([(N_("user groups not here: %(names)s"), {"names": ", ".join(missing)})]
                   if missing else []) + [(r, {}) for r in said]
        if not reasons:
            self.report.take("model", name)
        for n, (reason, fields) in enumerate(reasons):
            if n == 0:
                self.report.take("model", name, reason, **fields)
            else:
                self.report.notes.append(Entry("model", name, reason, fields))

    def settings(self, item: dict, data: dict) -> None:
        group = str(data.get("group") or "")
        values = data.get("values") if isinstance(data.get("values"), dict) else {}
        name = str(item.get("name") or group)
        if group in GONE_SETTINGS:
            self.report.skip("settings", name, N_("2.0 has no credits"))
            return
        if group == settings_store.PROMPTS:
            self._prompts(name, values)
            return
        if group not in KEYED_SETTINGS:
            self.report.skip("settings", name, N_("this release has no such settings"))
            return
        known = settings_store.defaults(group)
        taken: dict[str, Any] = {}
        left: list[str] = []
        for key, value in values.items():
            if key in NOT_TAKEN.get(group, ()):
                continue
            if key + ENCRYPTED in known:
                plain = self.secret("settings", name, key, value)
                taken[key + ENCRYPTED] = crypto.encrypt(plain)
            elif key in known and not key.endswith(ENCRYPTED):
                if _same_shape(known[key], value):
                    taken[key] = value
                else:
                    left.append(key)
            else:
                left.append(key)
        if taken:
            settings_store.update(self.db, taken, key=group)
            self.report.take("settings", name, N_("%(count)s values"), count=len(taken))
        if left:
            self.report.skip("settings", name, N_("no place here for: %(keys)s"),
                             keys=", ".join(sorted(left)))

    def _prompts(self, name: str, values: dict) -> None:
        book = prompts.catalogue(self.db)
        overrides: dict[str, str] = {}
        left: list[str] = []
        for key, value in values.items():
            if "." not in key:
                continue
            target = prompts.RENAMED_KEYS.get(key, key)
            if target in book and isinstance(value, str):
                overrides[target] = value
            else:
                left.append(key)
        plain = {k: v for k, v in values.items()
                 if "." not in k and k in settings_store.defaults(settings_store.PROMPTS)}
        if plain:
            settings_store.update(self.db, plain, key=settings_store.PROMPTS)
        if overrides:
            prompts.save(self.db, overrides)
        if overrides or plain:
            self.report.take("settings", name, N_("%(count)s values"),
                             count=len(overrides) + len(plain))
        if left:
            self.report.skip("settings", name, N_("no place here for: %(keys)s"),
                             keys=", ".join(sorted(left)))

    def image_workflow(self, item: dict, data: dict) -> None:
        slug = str(data.get("slug") or "")
        name = str(data.get("name") or item.get("name") or slug)[:120]
        workflow = data.get("workflow")
        if not SLUG.match(slug) or not isinstance(workflow, dict) or not workflow:
            self.report.skip("image_workflow", name or slug, N_("not a workflow this release "
                                                                 "can use"))
            return
        if self.db.scalar(select(ImageWorkflow.id).where(ImageWorkflow.slug == slug)):
            self.report.skip("image_workflow", name, N_("already here"))
            return
        self.db.add(ImageWorkflow(slug=slug, name=name, description=str(data.get("description")
                                                                        or ""),
                                  workflow_json=workflow, enabled=bool(data.get("enabled", True)),
                                  position=_int(data.get("position"))))
        self.db.flush()
        self.report.take("image_workflow", name)

    def custom_tool(self, item: dict, data: dict) -> None:
        slug = str(data.get("slug") or "")
        name = str(data.get("name") or item.get("name") or slug)[:120]
        url = str(data.get("url_template") or "")
        if not SLUG.match(slug) or not url.startswith(("http://", "https://")):
            self.report.skip("custom_tool", name or slug, N_("not a tool this release can use"))
            return
        if self.db.scalar(select(CustomTool.id).where(CustomTool.slug == slug)):
            self.report.skip("custom_tool", name, N_("already here"))
            return
        found, missing = self.groups(data.get("groups"))
        row = CustomTool(
            slug=slug,
            name=name,
            description=str(data.get("description") or ""),
            parameters_json=data.get("parameters") if isinstance(data.get("parameters"),
                                                                 dict) else {},
            guidance=str(data.get("guidance") or ""),
            method=str(data.get("method") or "GET").upper()[:8],
            url_template=url[:1000],
            body_template=str(data.get("body_template") or ""),
            headers_json=self.headers("custom_tool", name, "headers", data.get("headers")),
            secret_encrypted=crypto.encrypt(
                self.secret("custom_tool", name, "secret", data.get("secret"))
            ),
            secret_placement=_one_of(data.get("secret_placement"), SECRET_PLACEMENTS),
            secret_name=str(data.get("secret_name") or "Authorization")[:120],
            response_mode=_one_of(data.get("response_mode"), RESPONSE_MODES),
            response_path=str(data.get("response_path") or "")[:300],
            max_chars=_int(data.get("max_chars"), 8000),
            timeout=_int(data.get("timeout"), 20),
            allow_private=bool(data.get("allow_private")),
            enabled=bool(data.get("enabled", True)),
            public=bool(data.get("public", True)),
            position=_int(data.get("position")),
        )
        if not row.public:
            row.groups = found
        self.db.add(row)
        self.db.flush()
        if missing:
            self.report.take("custom_tool", name, N_("user groups not here: %(names)s"),
                             names=", ".join(missing))
        else:
            self.report.take("custom_tool", name)

    def mcp_server(self, item: dict, data: dict) -> None:
        slug = str(data.get("slug") or "")
        name = str(data.get("name") or item.get("name") or slug)[:120]
        url = str(data.get("url") or "")
        if not MCP_SLUG.match(slug) or not url.startswith(("http://", "https://")):
            self.report.skip("mcp_server", name or slug, N_("not a server this release can "
                                                             "use"))
            return
        if self.db.scalar(select(McpServer.id).where(McpServer.slug == slug)):
            self.report.skip("mcp_server", name, N_("already here"))
            return
        found, missing = self.groups(data.get("groups"))
        row = McpServer(
            slug=slug,
            name=name,
            url=url[:1000],
            guidance=str(data.get("guidance") or ""),
            headers_json=self.headers("mcp_server", name, "headers", data.get("headers")),
            secret_encrypted=crypto.encrypt(
                self.secret("mcp_server", name, "secret", data.get("secret"))
            ),
            secret_placement=_one_of(data.get("secret_placement"), SECRET_PLACEMENTS),
            secret_name=str(data.get("secret_name") or "Authorization")[:120],
            timeout=_int(data.get("timeout"), 30),
            max_chars=_int(data.get("max_chars"), 8000),
            allow_private=bool(data.get("allow_private")),
            tool_overrides_json=data.get("tool_overrides")
            if isinstance(data.get("tool_overrides"), dict) else {},
            enabled=bool(data.get("enabled", True)),
            public=bool(data.get("public", True)),
            position=_int(data.get("position")),
        )
        if not row.public:
            row.groups = found
        self.db.add(row)
        self.db.flush()
        if missing:
            self.report.take("mcp_server", name, N_("user groups not here: %(names)s"),
                             names=", ".join(missing))
        else:
            self.report.take("mcp_server", name)

    def finish(self) -> None:
        for group, data in getattr(self, "_group_links", []):
            group.embedding_connection_id = self.refs["connection"].get(
                str(data.get("embedding_connection") or ""), ""
            )
            group.review_connection_id = self.refs["connection"].get(
                str(data.get("review_connection") or ""), ""
            )


def _int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _one_of(value: Any, allowed: tuple[str, ...]) -> str:
    return value if value in allowed else allowed[0]


def _same_shape(default: Any, value: Any) -> bool:
    """Whether a stored value can stand where this release's default does."""
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, str):
        return isinstance(value, str)
    if isinstance(default, list):
        return isinstance(value, list)
    if isinstance(default, dict):
        return isinstance(value, dict)
    return True


def run(db: DBSession, document: dict[str, Any], *, passphrase: str = "") -> Report:
    """Import a loaded admin document. Raises `export_format.Refused` only for
    a missing or wrong passphrase, before anything is written."""
    fernet: Fernet | None = None
    if needs_passphrase(document):
        if not passphrase:
            raise export_format.Refused(
                t("This file's keys are encrypted: enter the passphrase it was exported with.")
            )
        fernet = export_format.opener(document["encryption"], passphrase)
    job = _Run(db, document, fernet)
    items = [i for i in document.get("items", []) if isinstance(i, dict)]
    for item in items:
        if item.get("type") not in ORDER:
            job.report.skip(str(item.get("type") or "?"), str(item.get("name") or "?"),
                            N_("this release does not know what that is"))
    for kind in ORDER:
        for item in items:
            if item.get("type") != kind:
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            try:
                if kind == "settings":
                    # Writes through settings_store, which commits on its own.
                    job.settings(item, data)
                else:
                    with db.begin_nested():
                        getattr(job, kind)(item, data)
            except Exception as problem:  # one item never fails the whole import
                log.warning("import: %s %r skipped: %s", kind, item.get("name"), problem)
                job.report.skip(kind, str(item.get("name") or item.get("ref") or "?"),
                                N_("it could not be saved: %(error)s"),
                                error=type(problem).__name__)
    job.finish()
    # Keys the file itself says it left out.
    for entry in document.get("secrets") or []:
        if isinstance(entry, dict) and entry.get("state") == "omitted":
            already = any(s.name == entry.get("name") and entry.get("field") in s.why_english()
                          for s in job.report.secrets)
            if not already:
                job.report.secret(str(entry.get("type") or "?"), str(entry.get("name") or "?"),
                                  N_("%(field)s was left out of the file (%(why)s)"),
                                  field=str(entry.get("field") or ""),
                                  why=str(entry.get("reason") or ""))
    db.commit()
    log.info("admin import from %s: %s taken, %s skipped", document.get("app_version"),
             len(job.report.taken), len(job.report.skipped))
    return job.report


__all__ = ["load", "needs_passphrase", "run"]
