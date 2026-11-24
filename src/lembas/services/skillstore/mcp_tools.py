"""The store's tools on the server's MCP endpoint (`api/mcp_server.py`).

Seventeen tools, described in a line each because every connected agent pays
for the list: nine for skills (`skills_main`, `skills_category`,
`skills_search`, `skill_outline`, `skill_read`, `skill_add`, `skill_edit`,
`skill_history`, `skills_policy`) and eight for notes (`notes_lookup`,
`note_outline`, `note_read`, `note_add`, `note_append`, `note_change`,
`note_confirm`, `note_archive`). Every one of them goes through
`service`, so the policy, the provenance and the data groups are the same
here as on every other door.

Offered by the account's permissions, checked on every call: `library.use`
and `tools.skills` for all of them, and `tools.skills.write` for the ones that
change something.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import User
from lembas.services.skillstore import catalog, policy, repo, service
from lembas.services.skillstore.policy import Who

_S = {"type": "string"}


def _tool(name: str, description: str, properties: dict, required: list[str], write=False):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "write": write,
    }


_REASON = {"type": "string", "description": "One line: why."}
_MODEL = {"type": "string", "description": "Your model's name, if you know it."}

TOOLS: tuple[dict[str, Any], ...] = (
    _tool("skills_main", "The main skill: how skills and notes are used here, and the list "
          "of skills or their categories. Read once before using or changing skills.", {}, []),
    _tool("skills_category", "The skills of one category (or of one of its groups).",
          {"category": _S, "group": _S}, ["category"]),
    _tool("skills_search", "Find skills and notes by words (and meaning, where available).",
          {"query": _S, "in": {"type": "string", "enum": ["all", "skills", "notes"]},
           "limit": {"type": "integer"}}, ["query"]),
    _tool("skill_outline", "A skill's sections with their sizes. Read this before the skill.",
          {"name": _S}, ["name"]),
    _tool("skill_read", "One section of a skill (or all of it when no section is named).",
          {"name": _S, "section": _S}, ["name"]),
    _tool("skill_add", "Add a skill. description: one line saying when to use it.",
          {"name": _S, "description": _S, "body": _S, "category": _S, "group": _S,
           "reason": _REASON, "model": _MODEL},
          ["name", "description", "body", "reason"], write=True),
    _tool("skill_edit", "Change one section of a skill (section \"new\" adds one at the end), "
          "or its description or category. May wait for approval: the answer says.",
          {"name": _S, "section": _S, "text": _S, "description": _S, "category": _S,
           "group": _S, "reason": _REASON, "model": _MODEL},
          ["name", "reason"], write=True),
    _tool("skill_history", "Who changed a skill, when, through what and why.",
          {"name": _S}, ["name"]),
    _tool("skills_policy", "What you may do here: whether your changes apply, wait or are "
          "refused, and what became of your latest proposals.", {}, []),
    _tool("notes_lookup", "Notes about a subject: their headers (kind, summary, dates, "
          "author, last confirmed). Ask before working on something.",
          {"subject": _S, "kind": _S,
           "status": {"type": "string", "enum": ["active", "archived", "all"]}}, []),
    _tool("note_outline", "A note's header and its sections with their sizes.",
          {"name": _S}, ["name"]),
    _tool("note_read", "One section of a note (or all of it when no section is named).",
          {"name": _S, "section": _S}, ["name"]),
    _tool("note_add", "Write a new note: a finding, plan, incident or other record. "
          "subjects: the words somebody would look it up by. Never a secret.",
          {"title": _S, "body": _S, "subjects": {"type": "array", "items": _S},
           "kind": {"type": "string", "description": "note, plan, finding, incident, crash, "
                    "decision, reference"},
           "summary": _S, "model": _MODEL},
          ["title", "body", "subjects"], write=True),
    _tool("note_append", "Add to a note: a dated, signed addition at its end.",
          {"name": _S, "text": _S, "model": _MODEL}, ["name", "text"], write=True),
    _tool("note_change", "Change what a note says: one section (or \"all\"), its summary, "
          "subjects or kind. Somebody else's note may wait for approval.",
          {"name": _S, "section": _S, "text": _S, "summary": _S,
           "subjects": {"type": "array", "items": _S}, "kind": _S, "reason": _REASON,
           "model": _MODEL},
          ["name", "reason"], write=True),
    _tool("note_confirm", "Say you checked a note and it is still true (sets last confirmed).",
          {"name": _S, "model": _MODEL}, ["name"], write=True),
    _tool("note_archive", "Archive a note that no longer holds.",
          {"name": _S, "reason": _REASON, "model": _MODEL}, ["name", "reason"], write=True),
)
NAMES = frozenset(tool["name"] for tool in TOOLS)


def offered(db: DBSession, user: User) -> list[dict[str, Any]]:
    """The store's tools this account may use now."""
    from lembas.security import permissions

    allowed = permissions.resolve(db, user)
    if not (allowed.get("library.use") and allowed.get("tools.skills")):
        return []
    may_write = bool(allowed.get("tools.skills.write", True))
    return [tool for tool in TOOLS if may_write or not tool["write"]]


def listed(db: DBSession, user: User) -> list[dict[str, Any]]:
    """`tools/list`'s entries for the store's tools."""
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
            "annotations": {"readOnlyHint": not tool["write"]},
        }
        for tool in offered(db, user)
    ]


# --- Who is calling -------------------------------------------------------------
def new_session(client: str) -> str:
    """An `Mcp-Session-Id` that remembers the client's own name: what a later
    call's provenance says the harness was. Self-declared, like the name."""
    name = " ".join((client or "").split())[:60]
    packed = base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{secrets.token_hex(8)}.{packed}"


def client_of(session_id: str) -> str:
    _head, sep, packed = (session_id or "").partition(".")
    if not sep or len(packed) > 120:
        return ""
    try:
        raw = base64.urlsafe_b64decode(packed + "=" * (-len(packed) % 4))
        return " ".join(raw.decode("utf-8").split())[:60]
    except (ValueError, UnicodeDecodeError):
        return ""


def caller(
    db: DBSession,
    user: User,
    *,
    token_id: str = "",
    token_name: str = "",
    session_id: str = "",
    meta: dict[str, Any] | None = None,
    chat=None,
    model: str = "",
) -> Who:
    """Who an MCP call is made by. The data group is the session's when the
    call names one of this device's sessions (`_meta.lembas.scope`), and the
    default group otherwise -- never a guess at another.

    What the server knows is the account and the token; those decide. The
    harness (the client's own name, or what its metadata says) is a label the
    caller chose, recorded and shown as that."""
    from lembas.services import data_groups

    meta = meta or {}
    harness = str(meta.get("harness") or "") or client_of(session_id) or token_name
    # The model the server knows is the scoped chat's own; anything else -- the
    # client's metadata, a tool's argument -- is the caller's claim, kept as one.
    known = (chat.model_id or "") if chat is not None else ""
    said_model = known or str(meta.get("model") or "") or model
    group = data_groups.for_chat(db, chat) if chat is not None else data_groups.DEFAULT_GROUP
    return policy.agent(
        db,
        user,
        token_id=token_id,
        token_name=token_name,
        harness=harness,
        model=said_model,
        model_said=not known,
        session=str(meta.get("scope") or "") or session_id.partition(".")[0],
        data_group=group,
    )


# --- Running one ----------------------------------------------------------------
def _said(outcome: service.Outcome) -> str:
    if outcome.status == service.APPLIED and outcome.commit:
        return f"{outcome.message} ({outcome.name}, commit {outcome.commit[:8]})"
    return f"{outcome.message} ({outcome.name})" if outcome.name else outcome.message


def _policy_text(db: DBSession, who: Who) -> str:
    said = policy.describe(db, who)
    lines = [
        f"A change you make to a skill {said['skills']}.",
        f"A note you add, add to or confirm {said['notes_add']}.",
        f"A change to a note somebody else wrote {said['notes_change']}; to your own, at once.",
        f"Your notes are written under the data group \"{who.data_group}\" and you read the "
        "notes of that group and the shared ones.",
        f"You are recorded as: {who.account_name}, token {who.token_name or '-'}, calling "
        f"yourself {who.harness}"
        + (f", model {who.model}" + (" (as you said)" if who.model_said else "")
           if who.model else "") + ".",
        f"(The levels: {said['levels']}.)",
    ]
    latest = service.own_proposals(db, who, 5)
    if latest:
        lines.append("Your latest proposals:")
        lines += [
            f"- {p.id[:8]} {p.target} {p.name}: {p.action} — {p.status}"
            + (f' ("{p.word}")' if p.word and p.status != "pending" else "")
            for p in latest
        ]
    return "\n".join(lines)


def _lookup_text(db: DBSession, who: Who, args: dict[str, Any]) -> str:
    rows = catalog.notes(
        db, who,
        subject=str(args.get("subject") or ""),
        kind=str(args.get("kind") or ""),
        status=str(args.get("status") or "active"),
    )
    if not rows:
        return "No notes on that here. (notes_lookup() with no subject lists them all.)"
    shown = rows[:30]
    text = "\n".join(service.note_header(row) for row in shown)
    if len(rows) > len(shown):
        text += f"\n({len(rows) - len(shown)} more: narrow by subject or kind.)"
    return text + "\n\nRead one with note_outline(name), then note_read(name, section)."


async def call(db: DBSession, who: Who, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """(what the tool answers, whether it is an error)."""
    def s(key: str) -> str:
        return str(args.get(key) or "")

    def opt(key: str) -> str | None:
        return None if args.get(key) is None else str(args[key])

    if s("model") and not who.model:
        who = replace(who, model=" ".join(s("model").split())[:200], model_said=True)
    try:
        catalog.ensure(db)
        service.sync_if_due(db)
        if name == "skills_main":
            catalog.count_read(db, repo.MAIN, who.account_id)
            return service.main_text(db), False
        if name == "skills_category":
            return service.category_text(db, s("category"), s("group")), False
        if name == "skills_search":
            hits = await catalog.search(
                db, who, s("query"), what=s("in") or "all", limit=int(args.get("limit") or 8)
            )
            if not hits:
                return "Nothing found.", False
            lines = []
            for hit in hits:
                line = f"{hit['kind']} {hit['name']}: {hit['when']}"
                if hit["section"]:
                    line += f"\n    in section {hit['section']}: {hit['snippet']}"
                lines.append(line)
            return "\n".join(lines), False
        if name == "skill_outline":
            return service.skill_outline(db, s("name")), False
        if name == "skill_read":
            return service.skill_read(
                db, s("name"), s("section"), reader=who.account_id
            ), False
        if name == "skill_history":
            row = catalog.skill(db, s("name"))
            if row is None:
                raise service.Refused(f'There is no skill "{s("name")}".')
            return service.history_text(service.TARGET_SKILL, row.name), False
        if name == "skills_policy":
            return _policy_text(db, who), False
        if name == "skill_add":
            return _said(service.skill_add(
                db, who, name=s("name"), description=s("description"), body=s("body"),
                category=s("category"), subcategory=s("group"), reason=s("reason"),
            )), False
        if name == "skill_edit":
            return _said(service.skill_edit(
                db, who, name=s("name"), section=s("section"), text=opt("text"),
                description=opt("description"), category=opt("category"),
                subcategory=opt("group"), reason=s("reason"),
            )), False
        if name == "notes_lookup":
            return _lookup_text(db, who, args), False
        if name == "note_outline":
            return service.note_outline(db, who, s("name")), False
        if name == "note_read":
            return service.note_read(db, who, s("name"), s("section")), False
        if name == "note_add":
            return _said(service.note_add(
                db, who, title=s("title"), body=s("body"), subjects=args.get("subjects"),
                kind=s("kind") or "note", summary=s("summary"),
            )), False
        if name == "note_append":
            return _said(service.note_append(db, who, ident=s("name"), text=s("text"))), False
        if name == "note_change":
            return _said(service.note_change(
                db, who, ident=s("name"), section=s("section"), text=opt("text"),
                summary=opt("summary"), subjects=args.get("subjects"), kind=opt("kind"),
                reason=s("reason"),
            )), False
        if name == "note_confirm":
            return _said(service.note_confirm(db, who, ident=s("name"))), False
        if name == "note_archive":
            return _said(
                service.note_archive(db, who, ident=s("name"), reason=s("reason"))
            ), False
    except service.Refused as exc:
        return str(exc), True
    except repo.StoreError as exc:
        return str(exc), True
    return f"There is no tool {name!r} here.", True


__all__ = ["NAMES", "TOOLS", "call", "caller", "client_of", "listed", "new_session", "offered"]
