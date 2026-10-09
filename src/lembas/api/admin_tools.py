"""Administration for the tools an administrator defines.

List-plus-detail, like `/admin/models` and for the same reason: a tool has
fifteen fields and a page that renders fifteen fields per row is unusable. The
list is compact and searchable; the whole form lives at `/admin/tools/{id}/edit`.

Validation reports back into the form rather than raising a 422. The fields here
are a JSON schema, a URL template and a secret; getting one wrong is normal, and
losing the other fourteen because of it is not acceptable. So a rejected save
re-renders the form from what was submitted, with the reason.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from lembas.api.deps import AdminUser, Db
from lembas.db.models import (
    RESPONSE_JSON,
    RESPONSE_MODES,
    RESPONSE_RAW,
    RESPONSE_TEXT,
    SECRET_NONE,
    SECRET_PLACEMENTS,
    CustomTool,
    Group,
    McpServer,
)
from lembas.services import custom_tools
from lembas.services import prompts as prompts_service
from lembas.services import tools as tools_service
from lembas.services.crypto import UNCHANGED_SENTINEL, decrypt, keep_or_replace, mask
from lembas.services.fetch import FetchError, check_url
from lembas.services.mcp import client as mcp_client
from lembas.services.mcp import registry as mcp_registry
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(tags=["admin-tools"])

PAGE_SIZE = 40

# The slug is the function name sent to the endpoint, so it is bound by the
# charset those accept, and it is half of this tool's prompt-fragment key, so it
# is bound by that pattern too. The intersection is this.
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

FILTERS: dict[str, object] = {
    "all": lambda tool: True,
    "enabled": lambda tool: tool.enabled,
    "disabled": lambda tool: not tool.enabled,
    "restricted": lambda tool: not tool.public,
}


# The labels are functions rather than constants because they are read in the
# language of whoever is looking, which a module-level value cannot know.
def _filter_labels() -> dict[str, str]:
    return {
        "all": t("All"),
        "enabled": t("Enabled"),
        "disabled": t("Disabled"),
        "restricted": t("Restricted"),
    }


def _response_labels() -> tuple[tuple[str, str], ...]:
    return (
        (RESPONSE_TEXT, t("Text — HTML reduced to prose")),
        (RESPONSE_JSON, t("JSON — parsed, narrowed by the path below")),
        (RESPONSE_RAW, t("Raw — exactly as it arrived")),
    )


def _secret_labels() -> tuple[tuple[str, str], ...]:
    return (
        (SECRET_NONE, t("None — this endpoint needs no credential")),
        ("bearer", t("Bearer token in a header")),
        ("header", t("The header named below, verbatim")),
        ("query", t("A query parameter named below")),
    )


def _tool(db: Db, tool_id: str) -> CustomTool:
    tool = db.get(CustomTool, tool_id)
    if tool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That tool no longer exists."))
    return tool


def _ordered(db: Db) -> list[CustomTool]:
    return list(db.scalars(select(CustomTool).order_by(CustomTool.position, CustomTool.slug)))


def _back(message: str = "") -> Response:
    target = f"/admin/tools?saved={quote(message)}" if message else "/admin/tools"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


# --- Form <-> row ------------------------------------------------------------
def _headers_text(headers: dict) -> str:
    return "\n".join(f"{name}: {value}" for name, value in (headers or {}).items())


def _parse_headers(text: str) -> dict[str, str]:
    """One `Name: value` per line. Blank lines and lines with no colon are dropped."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        name, _, value = line.partition(":")
        if name.strip() and _:
            out[name.strip()] = value.strip()
    return out


def _number(raw: str, *, default: int, low: int, high: int) -> int:
    text = str(raw or "").strip()
    if not text.lstrip("-").isdigit():
        return default
    return min(max(int(text), low), high)


def _populate(tool: CustomTool, form) -> None:
    """Copy a submitted form onto a row (or a draft of one).

    Checkboxes are read by key presence: FastAPI cannot tell `x=` from an absent
    `x`, and an absent one is exactly what an unticked box sends.
    """
    tool.name = str(form.get("name") or "").strip()[:120]
    tool.description = str(form.get("description") or "").strip()
    tool.guidance = str(form.get("guidance") or "").replace("\r\n", "\n").strip()
    tool.method = str(form.get("method") or "GET").strip().upper()
    tool.url_template = str(form.get("url_template") or "").strip()[:1000]
    tool.body_template = str(form.get("body_template") or "").replace("\r\n", "\n")
    tool.headers_json = _parse_headers(str(form.get("headers") or ""))

    placement = str(form.get("secret_placement") or SECRET_NONE)
    tool.secret_placement = placement if placement in SECRET_PLACEMENTS else SECRET_NONE
    tool.secret_name = str(form.get("secret_name") or "Authorization").strip()[:120]

    mode = str(form.get("response_mode") or RESPONSE_TEXT)
    tool.response_mode = mode if mode in RESPONSE_MODES else RESPONSE_TEXT
    tool.response_path = str(form.get("response_path") or "").strip()[:300]

    tool.max_chars = _number(
        form.get("max_chars"),
        default=8000,
        low=custom_tools.MIN_CHARS,
        high=custom_tools.MAX_CHARS,
    )
    tool.timeout = _number(
        form.get("timeout"),
        default=20,
        low=custom_tools.MIN_TIMEOUT,
        high=custom_tools.MAX_TIMEOUT,
    )
    tool.position = _number(form.get("position"), default=tool.position or 0, low=0, high=999)

    tool.allow_private = "allow_private" in form
    tool.enabled = "enabled" in form
    tool.public = "public" in form


def _problem(db: Db, tool: CustomTool, form, *, existing_id: str = "") -> str:
    """Why this cannot be saved, or an empty string."""
    if not tool.name:
        return t("A tool needs a name.")

    slug = str(form.get("slug") or "").strip().lower()
    if not SLUG_PATTERN.match(slug):
        return t(
            "The identifier must be lowercase letters, digits, hyphens or "
            "underscores, start with a letter or digit, and be at most 48 "
            "characters. It is the name the model calls."
        )
    if slug in tools_service.REGISTRY:
        return t("“%(slug)s” is the name of a built-in tool. Choose another.", slug=slug)
    clash = db.scalar(select(CustomTool).where(CustomTool.slug == slug))
    if clash is not None and clash.id != existing_id:
        return t("There is already a tool called “%(slug)s”.", slug=slug)
    tool.slug = slug

    if tool.method not in custom_tools.ALLOWED_METHODS:
        return t("%(method)s is not a method this can send.", method=tool.method)

    raw = str(form.get("parameters") or "").strip() or '{"type": "object", "properties": {}}'
    try:
        parameters = json.loads(raw)
    except json.JSONDecodeError as exc:
        return t("The parameters are not valid JSON: %(error)s", error=exc)
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        return t('The parameters must be a JSON object whose "type" is "object".')
    tool.parameters_json = parameters

    # The same check the runner makes, so a template that could never be called
    # is refused here rather than at the first call.
    try:
        custom_tools.fill_url(custom_tools.spec_from(tool), {})
    except Exception as exc:  # noqa: BLE001 - any refusal is a message for the form
        return str(getattr(exc, "message", exc))

    return ""


def _detail(request: Request, db: Db, tool: CustomTool, *, is_new: bool, error: str = "", **extra):
    key = f"tool.custom_{tool.slug}" if tool.slug else ""
    return render(
        request,
        "admin/tool_detail.html",
        {
            "tool": tool,
            "is_new": is_new,
            "error": error,
            "groups": list(db.scalars(select(Group).order_by(Group.name))),
            "selected_groups": extra.pop(
                "selected_groups", {group.id for group in (tool.groups if tool.id else [])}
            ),
            "headers_text": extra.pop("headers_text", _headers_text(tool.headers_json)),
            "parameters_text": extra.pop(
                "parameters_text", json.dumps(tool.parameters_json or {}, indent=2)
            ),
            "masked": mask(decrypt(tool.secret_encrypted)) if tool.secret_encrypted else "",
            "unchanged": UNCHANGED_SENTINEL,
            "methods": custom_tools.ALLOWED_METHODS,
            "response_modes": _response_labels(),
            "secret_placements": _secret_labels(),
            "prompt_key": key,
            "prompt_overridden": key in prompts_service.stored(db),
            **extra,
        },
    )


# --- The list ----------------------------------------------------------------
@router.get("/admin/tools")
async def tools_page(
    request: Request,
    db: Db,
    user: AdminUser,
    saved: str = "",
    q: str = "",
    filter: str = "all",
    page: int = 1,
):
    everything = _ordered(db)
    predicate = FILTERS.get(filter, FILTERS["all"])
    needle = q.strip().lower()
    matching = [
        tool
        for tool in everything
        if predicate(tool)
        and (not needle or needle in tool.slug.lower() or needle in (tool.name or "").lower())
    ]

    pages = max(1, -(-len(matching) // PAGE_SIZE))
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE

    return render(
        request,
        "admin/tools.html",
        {
            "tools": matching[start : start + PAGE_SIZE],
            "total": len(everything),
            "matched": len(matching),
            "page": page,
            "pages": pages,
            "page_start": start,
            "counts": {
                key: sum(1 for tool in everything if rule(tool))
                for key, rule in FILTERS.items()
            },
            "filters": _filter_labels(),
            "active_filter": filter if filter in FILTERS else "all",
            "q": q,
            "saved": saved,
        },
    )


# Registered before /{tool_id}: FastAPI matches in registration order, so with
# the parameterised route first "new" is captured as an id and the handler 404s
# on a tool that does not exist. This has already been a bug once, in
# /admin/models.
@router.get("/admin/tools/new")
async def new_tool_page(request: Request, db: Db, user: AdminUser):
    draft = CustomTool(
        name="",
        slug="",
        method="GET",
        url_template="https://",
        parameters_json={"type": "object", "properties": {}, "required": []},
        secret_placement=SECRET_NONE,
        response_mode=RESPONSE_TEXT,
        max_chars=8000,
        timeout=20,
        enabled=True,
        public=True,
        position=0,
    )
    return _detail(request, db, draft, is_new=True)


@router.post("/admin/tools")
async def create_tool(request: Request, db: Db, user: AdminUser) -> Response:
    form = await request.form()
    draft = CustomTool(headers_json={}, parameters_json={})
    _populate(draft, form)
    draft.position = db.scalar(select(func.coalesce(func.max(CustomTool.position), -1))) + 1

    problem = _problem(db, draft, form)
    if problem:
        return _detail(
            request,
            db,
            draft,
            is_new=True,
            error=problem,
            headers_text=str(form.get("headers") or ""),
            parameters_text=str(form.get("parameters") or ""),
            selected_groups=set(form.getlist("group_ids")),
        )

    draft.secret_encrypted = keep_or_replace(str(form.get("secret") or ""), "")
    draft.groups = _chosen_groups(db, form, public=draft.public)
    db.add(draft)
    db.commit()
    log.info("%s added custom tool %s", user.email, draft.slug)
    return _back(t("Added %(name)s.", name=draft.name))


def _chosen_groups(db: Db, form, *, public: bool) -> list[Group]:
    """A public tool holds no groups, the way a public model holds none."""
    if public:
        return []
    ids = set(form.getlist("group_ids"))
    return list(db.scalars(select(Group).where(Group.id.in_(ids)))) if ids else []


@router.get("/admin/tools/{tool_id}/edit")
async def edit_tool_page(request: Request, db: Db, user: AdminUser, tool_id: str):
    return _detail(request, db, _tool(db, tool_id), is_new=False)


@router.post("/admin/tools/{tool_id}/test")
async def test_tool(request: Request, db: Db, user: AdminUser, tool_id: str):
    """Call the stored row once, with arguments the administrator typed.

    The stored row rather than the submitted form, so what is tested is what a
    chat would actually do -- the same reason `/admin/search/test` reads the
    saved provider settings.
    """
    tool = _tool(db, tool_id)
    form = await request.form()
    raw = str(form.get("arguments") or "").strip() or "{}"

    try:
        arguments = json.loads(raw)
        if not isinstance(arguments, dict):
            raise ValueError(t("Arguments must be a JSON object."))
    except (json.JSONDecodeError, ValueError) as exc:
        return render(
            request,
            "admin/_tool_test.html",
            {
                "tool": tool,
                "error": t("Those arguments are not a JSON object: %(error)s", error=exc),
            },
        )

    outcome = await custom_tools.call(custom_tools.spec_from(tool), arguments)
    tool.last_error = str(outcome.event.get("error") or "")
    tool.last_checked_at = datetime.now(UTC)
    db.commit()

    return render(
        request,
        "admin/_tool_test.html",
        {
            "tool": tool,
            "outcome": outcome,
            "error": outcome.event.get("error") or "",
            "detail": outcome.event.get("detail") or "",
        },
    )


@router.post("/admin/tools/{tool_id}/delete")
async def delete_tool(db: Db, user: AdminUser, tool_id: str) -> Response:
    tool = _tool(db, tool_id)
    name = tool.name
    db.delete(tool)
    db.commit()
    log.info("%s deleted custom tool %s", user.email, name)
    return _back(t("Deleted %(name)s.", name=name))


@router.post("/admin/tools/{tool_id}")
async def update_tool(request: Request, db: Db, user: AdminUser, tool_id: str) -> Response:
    tool = _tool(db, tool_id)
    form = await request.form()

    # Validated against a draft so that a rejected save leaves the stored row
    # untouched and the form still holds what was typed.
    draft = CustomTool(headers_json={}, parameters_json={}, position=tool.position)
    _populate(draft, form)
    problem = _problem(db, draft, form, existing_id=tool.id)
    if problem:
        draft.id = tool.id
        draft.secret_encrypted = tool.secret_encrypted
        return _detail(
            request,
            db,
            draft,
            is_new=False,
            error=problem,
            headers_text=str(form.get("headers") or ""),
            parameters_text=str(form.get("parameters") or ""),
            selected_groups=set(form.getlist("group_ids")),
        )

    _populate(tool, form)
    tool.slug = draft.slug
    tool.parameters_json = draft.parameters_json
    tool.secret_encrypted = keep_or_replace(str(form.get("secret") or ""), tool.secret_encrypted)
    tool.groups = _chosen_groups(db, form, public=tool.public)
    db.commit()

    log.info("%s updated custom tool %s", user.email, tool.slug)
    return _back(t("Saved %(name)s.", name=tool.name))


# --- MCP servers -------------------------------------------------------------
MCP_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")


def _server(db: Db, server_id: str) -> McpServer:
    server = db.get(McpServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That server no longer exists."))
    return server


def _mcp_back(message: str = "") -> Response:
    target = f"/admin/mcp?saved={quote(message)}" if message else "/admin/mcp"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def _populate_server(server: McpServer, form) -> None:
    server.name = str(form.get("name") or "").strip()[:120]
    server.url = str(form.get("url") or "").strip()[:1000]
    server.guidance = str(form.get("guidance") or "").replace("\r\n", "\n").strip()
    server.headers_json = _parse_headers(str(form.get("headers") or ""))

    placement = str(form.get("secret_placement") or SECRET_NONE)
    server.secret_placement = placement if placement in SECRET_PLACEMENTS else SECRET_NONE
    server.secret_name = str(form.get("secret_name") or "Authorization").strip()[:120]

    server.timeout = _number(
        form.get("timeout"), default=30, low=mcp_client.MIN_TIMEOUT, high=mcp_client.MAX_TIMEOUT
    )
    server.max_chars = _number(
        form.get("max_chars"), default=8000, low=mcp_client.MIN_CHARS, high=mcp_client.MAX_CHARS
    )
    server.position = _number(form.get("position"), default=server.position or 0, low=0, high=999)

    server.allow_private = "allow_private" in form
    server.enabled = "enabled" in form
    server.public = "public" in form

    # One checkbox per advertised tool, so an unticked one is absent. The
    # stored map holds only the refusals; absent means on.
    if "tool_choices" in form:
        offered = set(form.getlist("tool_names"))
        chosen = set(form.getlist("tool_names_on"))
        server.tool_overrides_json = dict.fromkeys(offered - chosen, False)


def _server_problem(db: Db, server: McpServer, form, *, existing_id: str = "") -> str:
    if not server.name:
        return t("A server needs a name.")

    slug = str(form.get("slug") or "").strip().lower()
    if not MCP_SLUG_PATTERN.match(slug):
        return t(
            "The identifier must be lowercase letters, digits, hyphens or "
            "underscores, and at most 24 characters. It prefixes every tool "
            "name this server offers."
        )
    clash = db.scalar(select(McpServer).where(McpServer.slug == slug))
    if clash is not None and clash.id != existing_id:
        return t("There is already a server called “%(slug)s”.", slug=slug)
    server.slug = slug

    try:
        check_url(server.url, allow_private=True)
    except FetchError as exc:
        return exc.message
    return ""


def _server_detail(
    request: Request, db: Db, server: McpServer, *, is_new: bool, error: str = "", **extra
):
    key = f"tool.mcp_{server.slug}" if server.slug else ""
    overrides = server.tool_overrides_json or {}
    return render(
        request,
        "admin/mcp_detail.html",
        {
            "server": server,
            "is_new": is_new,
            "error": error,
            "groups": list(db.scalars(select(Group).order_by(Group.name))),
            "selected_groups": extra.pop(
                "selected_groups", {group.id for group in (server.groups if server.id else [])}
            ),
            "headers_text": extra.pop("headers_text", _headers_text(server.headers_json)),
            "tools": [
                {**entry, "on": overrides.get(entry.get("name"), True)}
                for entry in (server.tools_json or [])
                if isinstance(entry, dict)
            ],
            "masked": mask(decrypt(server.secret_encrypted)) if server.secret_encrypted else "",
            "unchanged": UNCHANGED_SENTINEL,
            "secret_placements": _secret_labels(),
            "prompt_key": key,
            "prompt_overridden": key in prompts_service.stored(db),
            **extra,
        },
    )


@router.get("/admin/mcp")
async def mcp_page(request: Request, db: Db, user: AdminUser, saved: str = ""):
    servers = list(db.scalars(select(McpServer).order_by(McpServer.position, McpServer.slug)))
    return render(
        request,
        "admin/mcp.html",
        {
            "servers": servers,
            "counts": {server.id: len(server.tools_json or []) for server in servers},
            "saved": saved,
        },
    )


# Registered before /{server_id}, for the reason given above.
@router.get("/admin/mcp/new")
async def new_server_page(request: Request, db: Db, user: AdminUser):
    draft = McpServer(
        name="",
        slug="",
        url="https://",
        secret_placement=SECRET_NONE,
        timeout=30,
        max_chars=8000,
        enabled=True,
        public=True,
        position=0,
        tools_json=[],
        tool_overrides_json={},
    )
    return _server_detail(request, db, draft, is_new=True)


@router.post("/admin/mcp")
async def create_server(request: Request, db: Db, user: AdminUser) -> Response:
    form = await request.form()
    draft = McpServer(headers_json={}, tools_json=[], tool_overrides_json={})
    _populate_server(draft, form)
    draft.position = db.scalar(select(func.coalesce(func.max(McpServer.position), -1))) + 1

    problem = _server_problem(db, draft, form)
    if problem:
        return _server_detail(
            request,
            db,
            draft,
            is_new=True,
            error=problem,
            headers_text=str(form.get("headers") or ""),
            selected_groups=set(form.getlist("group_ids")),
        )

    draft.secret_encrypted = keep_or_replace(str(form.get("secret") or ""), "")
    draft.groups = _chosen_groups(db, form, public=draft.public)
    db.add(draft)
    db.commit()

    # Discovered immediately, the way a new connection's models are: an
    # administrator who has just typed a URL wants to know whether it answered.
    count, error = await mcp_registry.refresh(db, draft)
    log.info("%s added MCP server %s (%d tools)", user.email, draft.slug, count)
    if error:
        return _mcp_back(
            t("Added %(name)s, but it could not be reached: %(error)s",
              name=draft.name, error=error)
        )
    return _mcp_back(t("Added %(name)s — %(count)s tool(s).", name=draft.name, count=count))


@router.get("/admin/mcp/{server_id}/edit")
async def edit_server_page(request: Request, db: Db, user: AdminUser, server_id: str):
    return _server_detail(request, db, _server(db, server_id), is_new=False)


@router.post("/admin/mcp/{server_id}/test")
async def test_server(request: Request, db: Db, user: AdminUser, server_id: str):
    """Contact the server and cache what it advertises.

    Returns the row fragment, swapped in place, exactly as "Test & refresh"
    does for a connection.
    """
    server = _server(db, server_id)
    count, error = await mcp_registry.refresh(db, server)
    if error:
        message = f"{server.name}: {error}"
    elif count == 1:
        message = t("%(name)s: found 1 tool.", name=server.name)
    else:
        message = t("%(name)s: found %(count)s tools.", name=server.name, count=count)
    return render(
        request,
        "admin/_mcp_row.html",
        {
            "server": server,
            "tool_count": len(server.tools_json or []),
            "message": message,
            "message_kind": "error" if error else "success",
        },
    )


@router.post("/admin/mcp/{server_id}/delete")
async def delete_server(db: Db, user: AdminUser, server_id: str) -> Response:
    server = _server(db, server_id)
    name = server.name
    db.delete(server)
    db.commit()
    log.info("%s deleted MCP server %s", user.email, name)
    return _mcp_back(t("Deleted %(name)s.", name=name))


@router.post("/admin/mcp/{server_id}")
async def update_server(request: Request, db: Db, user: AdminUser, server_id: str) -> Response:
    server = _server(db, server_id)
    form = await request.form()

    draft = McpServer(headers_json={}, tools_json=[], position=server.position)
    _populate_server(draft, form)
    problem = _server_problem(db, draft, form, existing_id=server.id)
    if problem:
        draft.id = server.id
        draft.secret_encrypted = server.secret_encrypted
        draft.tools_json = server.tools_json
        return _server_detail(
            request,
            db,
            draft,
            is_new=False,
            error=problem,
            headers_text=str(form.get("headers") or ""),
            selected_groups=set(form.getlist("group_ids")),
        )

    _populate_server(server, form)
    server.slug = draft.slug
    server.secret_encrypted = keep_or_replace(
        str(form.get("secret") or ""), server.secret_encrypted
    )
    server.groups = _chosen_groups(db, form, public=server.public)
    db.commit()

    log.info("%s updated MCP server %s", user.email, server.slug)
    return _mcp_back(t("Saved %(name)s.", name=server.name))
