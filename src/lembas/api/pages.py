"""Full-page routes: the chat shell and the user's own settings."""

from __future__ import annotations

import logging
from urllib.parse import urlencode
from zoneinfo import available_timezones

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db, RequiredUser
from lembas.config import settings
from lembas.db.models import (
    KIND_AGENT,
    KIND_CHAT,
    KIND_TASK,
    KINDS,
    Chat,
    Connection,
    Folder,
    KnowledgeBase,
    Message,
    User,
)
from lembas.security import permissions
from lembas.services import audio as audio_service
from lembas.services import branding as branding_service
from lembas.services import chat as chat_service
from lembas.services import compaction as compaction_service
from lembas.services import data_groups, local_device, model_prefs, settings_store
from lembas.services import prototypes as prototypes_service
from lembas.services import reports as reports_service
from lembas.services import suggestions as suggestions_service
from lembas.services.library import documents as documents_service
from lembas.services.schedule import clock
from lembas.web import i18n
from lembas.web.templating import STATIC_DIR, render

log = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])

# Matches --bg for each theme in tokens.css. Duplicated here because the
# manifest is JSON read by the operating system before any stylesheet exists;
# there is nowhere for a CSS variable to resolve.
THEME_COLOUR = {"moria": "#0B0D10", "shire": "#F1EAD6"}


def _instance_colour(brand) -> str:
    """The background this instance paints before anything has loaded.

    The instance's default built-in theme decides. Falls back to Moria rather
    than raising -- a splash screen is not worth a 500.
    """
    theme = brand.theme(brand.default_theme_id(settings.default_theme))
    return THEME_COLOUR.get(theme.base, THEME_COLOUR["moria"])


def _credit_chip(db: DBSession, user: User) -> dict | None:
    """The balance for the chat header, or None -- shown only where
    credits are in use and the administrator lets people see their balance.

    In both units, `123.45 cr · €1.23`, each written the way the
    reader's language writes numbers; an administrator, who is never charged,
    sees "Unlimited" instead. Shown at a balance of nothing too: with credits
    on, an empty account is refused on every priced model, and the header is
    where it finds out why."""
    from lembas.services import credits as credits_service

    values = settings_store.credits(db)
    if not values.get("enabled") or not values.get("show_balance"):
        return None
    if user.is_admin:
        return {"unlimited": True}
    micro = credits_service.balance(db, user)
    return {
        "unlimited": False,
        "credits": credits_service.format_credits(micro),
        "money": credits_service.format_money(
            credits_service.micro_to_money(micro, values["credit_value"]), values["currency"]
        ),
    }


def _chat_context(db: DBSession, user: User, chat: Chat | None) -> dict:
    """Model lists and permissions every chat page needs.

    Pinned and unpinned are split here rather than in the template so the
    picker's optgroups stay a plain loop.
    """
    models = chat_service.available_models(db, user)
    current = next((m for m in models if m.model_id == chat.model_id), None) if chat else None
    # A chat that exists may only be switched to a model in its own data group:
    # the new model would be sent the whole history. The rest are named below
    # the picker rather than silently missing from it, so somebody looking for
    # one learns where it went -- and that a new chat is how to reach it.
    group = data_groups.for_chat(db, chat) if chat is not None else None
    in_group = chat_service.available_models(db, user, group) if group is not None else models
    in_group_ids = {m.id for m in in_group}
    return {
        "credit_chip": _credit_chip(db, user),
        "models": in_group,
        "models_elsewhere": [m for m in models if m.id not in in_group_ids],
        "chat_group_name": data_groups.name_of(db, group) if group is not None else "",
        "several_groups": data_groups.several(db, user),
        "current_model": current,
        # Assistant bubbles show the avatar of the model that wrote them, which
        # may not be the model the chat is set to now. Keyed by model_id, the
        # denormalised value stored on each message.
        "models_by_id": {m.model_id: m for m in models},
        # Offered in the chat settings panel so a conversation can be pointed at
        # particular bases. Empty when the reader has none, and the panel then
        # shows nothing rather than an empty control.
        "knowledge_bases": (
            list(
                db.scalars(
                    documents_service.visible_bases(db, user, group).order_by(
                        KnowledgeBase.name
                    )
                )
            )
            if permissions.has(db, user, "library.use")
            else []
        ),
        "attached_base_ids": [base.id for base in chat.knowledge_bases] if chat else [],
        # What *this* model takes, not the three every model used to be assumed
        # to take. The vocabulary is per model -- gpt-oss has no `xhigh` and
        # Bonsai has no `high`, and sending the wrong one does not degrade, it
        # raises inside the chat template and fails the reply. From the service
        # so the command, the control and the request builder cannot disagree.
        "efforts": chat_service.efforts_for(current) if current else chat_service.DEFAULT_EFFORTS,
        # What the picker shows, and what `build_request` will send. One
        # resolver so the two cannot disagree.
        "resolved_effort": chat_service.resolved_effort(chat) if chat else "",
        **_scope_context(db, user, chat),
        **_agent_context(db, user, chat),
        **audio_service.template_flags(db, user),
        # How long a temporary chat lives, for the badge, the Keep menu and the
        # toggle. Read from the setting every render: it is one cached group.
        "temporary_after": chat_service.lifetime_words(chat_service.temporary_lifetime(db)),
        "temporary_leaves": chat_service.temporary_lifetime(db) == 0,
    }


def _scope_context(db: DBSession, user: User, chat: Chat | None) -> dict:
    """What this chat may use, for the menu that narrows it.

    The families listed are the ones actually offered *right now*, so the menu
    never shows a switch for something the model, the reader's permissions or
    the instance has already ruled out -- turning that on would do nothing,
    since `resolve_tools` applies this after the gates.

    **It works before the chat exists**, and that is not a nicety. The whole
    point of narrowing is to decide what a conversation may reach, and the first
    turn is the one where it matters most: the harness puts a tool's guidance in
    front of the model the moment the tool is offered, so by the time a chat
    existed to switch anything off, the model had already been told how to keep
    notes and been given the tools to do it. Switching it off afterwards does
    not un-send that turn.

    It used to say there was no row to write to. There is not -- so the
    prospective menu writes nothing: its switches are plain checkboxes submitted
    with the first message, and `start_chat` turns them into `scope_json` on the
    row it is about to create. `scope_allow` stays empty because nothing can
    have been allowed yet.

    The stand-in `Chat` is `agent/draft.py:as_chat`'s trick again: `resolve_tools`
    reads the kind, the model and the scope off a chat and never queries or
    writes it, so a row that is constructed and never added satisfies it
    unchanged. `scope_json` is set explicitly because it is a *column* default,
    applied at flush, and this one is never flushed.
    """
    from lembas.services import tools as tools_service
    from lembas.services.library import skills as skills_service

    prospective = chat is None
    if prospective:
        model_id = ""
        chosen = chat_service.default_model(db, user)
        if chosen is not None:
            model_id = chosen[0]
        if not model_id:
            return {"scope_families": [], "scope_skills": [], "scope_allow": []}
        # An ordinary chat, deliberately, even though the kind can still be
        # switched on this screen: an agent chat's tools depend on a connection
        # that is not settled until the chat is created, so offering them here
        # would be a switch for something that may not be offered. Everything a
        # plain chat can reach is switchable, which is the part that matters.
        chat = Chat(user_id=user.id, kind=KIND_CHAT, model_id=model_id, scope_json={})

    # With the reader's own default for this model counted, so the menu
    # shows what will actually be offered.
    off = tools_service.effective_off(db, chat, user)
    skills_off = tools_service.scoped_skills_off(chat)

    # Gates rather than tool names: `notes` is one switch, not five, which is
    # the same reasoning the per-model capability checkboxes carry.
    seen: dict[str, str] = {}
    for tool in tools_service.resolve_tools(db, chat, user).defs:
        seen.setdefault(tools_service.gate_of(tool.family), tool.name)
    # Not a tool, but switched the same way and stored in the same place: a
    # prototype is something the model writes, and "not in this chat" is the
    # same decision as "no web search in this chat".
    if prototypes_service.allowed(db, user) and not chat.parent_chat_id:
        seen.setdefault(prototypes_service.GATE, "")
    # Anything already switched off is absent from the offered set, so it has to
    # be put back or there would be no way to turn it on again.
    for gate in off:
        seen.setdefault(gate, "")

    families = [
        {"gate": gate, "label": tools_service.gate_label(gate, example), "on": gate not in off}
        for gate, example in sorted(seen.items())
    ]

    skills = []
    if permissions.has(db, user, "library.use"):
        skills = [
            {
                "name": skill.name,
                "description": skill.description,
                "on": skill.name not in skills_off,
            }
            for skill in skills_service.enabled_for(db, user)
        ]
        for name in sorted(skills_off):
            if name not in {s["name"] for s in skills}:
                skills.append({"name": name, "description": "", "on": False})

    # What this chat has been told to stop asking about. Shown so the list
    # cannot grow invisibly: every entry is one click of "Always allow this" on
    # a card, and a standing permission nobody can see is one nobody can revoke.
    return {
        "scope_families": families,
        "scope_skills": skills,
        "scope_allow": list(tools_service.scoped_allow(chat)),
        # Which of the two menus to draw: switches that POST at once, or
        # switches that ride along with the first message. The template asks
        # this rather than `chat is None`, so the reason is named where the
        # difference is.
        "scope_prospective": prospective,
    }


def _data_group_settings(db: DBSession, user: User) -> dict:
    """What the Data tab on /settings shows: which group each connection reads.

    Every connection this person can reach a model on, with the instance's
    choice beside their own. Their own is only in force while they hold
    `data.manage`; without it the tab still says what applies to them, because
    "which provider can read my notes?" is a question anybody may ask.
    """
    from lembas.api import admin_data_groups

    usable = data_groups.usable(db, user)
    reachable = {m.connection_id for m in chat_service.available_models(db, user)}
    connections = [
        connection
        for connection in db.scalars(select(Connection).order_by(Connection.position))
        if connection.id in reachable
    ]
    in_force = data_groups.connection_groups(db, user)
    return {
        "data_groups": usable,
        "group_names": {group.id: group.name for group in data_groups.all_groups(db)},
        "may_manage_groups": data_groups.may_manage(db, user),
        "group_labels": admin_data_groups.labels(),
        "group_counts": {
            group.id: data_groups.counts(db, group.id, owner=user) for group in usable
        },
        "group_connections": [
            {
                "connection": connection,
                "instance": connection.data_group_id or data_groups.DEFAULT_GROUP,
                "chosen": data_groups.personal_map(user).get(connection.id, ""),
                "in_force": in_force.get(connection.id, data_groups.DEFAULT_GROUP),
            }
            for connection in connections
        ],
    }


def _agent_context(db: DBSession, user: User, chat: Chat | None) -> dict:
    """What the composer and the chat header need to know about agent chats.

    An agent chat runs on a linked LLeMbas CLI and nowhere else, so the
    picker lists the devices dialled in now, and nothing when agent chats are
    off or this person may not run commands.
    """
    from lembas.services.agent import policy as agent_policy

    agents_on = bool(settings_store.agents(db).get("enabled")) and permissions.has(
        db, user, "tools.agent"
    )

    # Linked devices: a LLeMbas CLI dialled in right now. The most its own
    # limits allow is the mode a chat starts in -- the device's owner set that
    # on purpose. The directories this person already worked in there are what
    # the composer offers, the latest chosen; with none, nothing is chosen and
    # the composer asks. Named by its host: "LLeMbas CLI on device-1" would say
    # the program twice in a select that is already narrow. In LLeMbas Desktop
    # the computer it runs on is "Local", and first (`local_device`).
    devices: list[dict] = []
    device_name = ""
    device_shown: dict = {}
    if agents_on and permissions.has(db, user, "api.use"):
        from lembas.services import device_link

        links = device_link.linked_for(user.id)
        recent = chat_service.recent_dirs(db, user.id, [link.token_id for link in links])
        hosts = device_link.token_hosts(db, user.id, [link.token_id for link in links])
        devices = [
            {"value": f"device:{link.token_id}",
             **local_device.label(
                 device_link.short_name(link), hosts.get(link.token_id), link.host
             ),
             "recent": recent.get(link.token_id, []), "mode": link.max_mode}
            for link in links
        ]
        # Local first; `sort` is stable, so the rest keep their order.
        devices.sort(key=lambda device: not device["local"])
    if chat is not None and getattr(chat, "device_token_id", None):
        from lembas.db.models import ApiToken

        token = db.get(ApiToken, chat.device_token_id)
        device_name = (token.device_host or token.name) if token is not None else "a device"
        if token is not None and token.user_id == user.id:
            device_shown = local_device.label(device_name, token.device_host)
    # What this chat's device answers, for the features protocol 2
    # added: the composer's `@` files and `/` commands, the model badge. Empty
    # while it is not linked -- each feature then stays as it was.
    capabilities: list[str] = []
    if chat is not None and getattr(chat, "device_token_id", None):
        from lembas.services import device_link

        linked = device_link.link_for(chat.device_token_id)
        if linked is not None:
            meta = (linked.initialized.get("_meta") or {}).get("lembas") or {}
            capabilities = [str(c) for c in meta.get("capabilities") or []]

    return {
        "agent_devices": devices,
        "agent_device_name": device_name,
        # The chat's device as its header shows it: "Local" in LLeMbas Desktop
        # when it is this computer. The terminal keeps the host name.
        "agent_device_shown": device_shown,
        "device_capabilities": capabilities,
        "device_reports_model": "model" in capabilities
        or bool(getattr(chat, "device_model", "")),
        "agent_modes": [
            (m, agent_policy.MODE_LABELS[m], agent_policy.MODE_HINTS[m])
            for m in agent_policy.MODES
        ],
        "terminal_enabled": _terminal_enabled(db, user, chat),
        # Any chat that exists. Deliberately not gated the way the terminal is:
        # half the canvas's sources -- notes, skills, this chat's attachments,
        # its own scratch document -- need no machine at all, so the terminal's
        # total gate would remove a working feature because one source is
        # unavailable. Absent on the new-chat screen for the reason the scope
        # menu is: there is no row yet to hang a tab on.
        "canvas_enabled": chat is not None,
        # Whether the panel and its switch exist at all. The per-chat switch is
        # not consulted: a chat that turned prototypes off still has the ones
        # it made before, and they must still open.
        "prototypes_enabled": prototypes_service.allowed(db, user),
    }


def _terminal_enabled(db: DBSession, user: User, chat: Chat | None) -> bool:
    """Whether this chat can offer a shell: a chat on a linked device whose
    device opens terminals (`remote.terminal`), for somebody who may open
    one. Every condition, not a subset: the button loads 280KB of terminal and
    opens a socket, so one that cannot work is worse than none."""
    if chat is None or not getattr(chat, "device_token_id", None):
        return False
    from lembas.services import device_link

    link = device_link.link_for(chat.device_token_id)
    values = settings_store.agents(db)
    return bool(
        link is not None
        and link.can("terminal")
        and permissions.has(db, user, "agent.terminal")
        and values.get("terminal_enabled", True)
    )


def sidebar_kind(user: User) -> str:
    """Which side of the sidebar's switch this user last chose.

    One resolver, because the page, the fragment route and the switch's own
    pressed state all have to agree about it. Anything unrecognised -- an older
    release's value, a hand-edited row -- reads as ordinary chats rather than
    showing an empty sidebar nobody can explain.
    """
    chosen = (user.settings_json or {}).get("sidebar_kind")
    return chosen if chosen in KINDS else KIND_CHAT


# What an API-only instance stops offering in the sidebar and Settings.
# `tools.agent` stays: it is what shows Devices, which is Settings.
API_ONLY_HIDDEN = frozenset({
    "chat.create", "folder.manage", "files.upload", "library.use", "library.share",
    "reports.use", "schedule.use",
})


# Which of a device section's chats it lists: the filter button beside each
# device's + on the Agents side. "active" is the default and is never stored.
AGENT_FILTERS = ("active", "all", "archived")
AGENT_FILTER_DEFAULT = "active"


def agent_filters(user: User) -> dict[str, str]:
    """Each device section's chosen filter, keyed by device token id.

    `""` is the "device missing" section, which has no token to key on. Anything
    unrecognised -- a hand-edited row, a value a later release dropped -- reads
    as the default rather than as an empty section nobody can explain, the same
    rule `sidebar_kind` follows.
    """
    stored = (user.settings_json or {}).get("agent_filter") or {}
    if not isinstance(stored, dict):
        return {}
    return {str(key): value for key, value in stored.items() if value in AGENT_FILTERS}


# Every sidebar section folds, and remembers it on the account -- one mechanism
# for all of them rather than one per kind of section. Keyed by section:
# `pinned`, `chats`, `archived`, `device:<token id>` (`device:` for "device
# missing"), and `folder:<id>`, which
# is stored on the folder itself (`Folder.collapsed`) rather than here -- the
# column already existed, and a key that dies with its folder needs no pruning.
#
# Only a section that differs from its default is stored, so the map holds what
# somebody actually changed and a default that moves later still reaches them.
SECTIONS_KEY = "sections"
SECTION_DEFAULTS = {"pinned": True, "chats": True, "archived": False}


def section_states(user: User) -> dict[str, bool]:
    """What this person folded or opened, by section key.

    An older account may hold `[ PINNED MODELS ]` alone, as `pinned_open`; that
    is read as the `pinned` key when the map has no word on it, so nobody's
    choice is lost to the change of shape. Anything that is not a bool is dropped rather than
    guessed at -- a hand-edited row reads as the default.
    """
    stored = (user.settings_json or {}).get(SECTIONS_KEY) or {}
    states = (
        {str(k): v for k, v in stored.items() if isinstance(v, bool)}
        if isinstance(stored, dict)
        else {}
    )
    legacy = (user.settings_json or {}).get("pinned_open")
    if "pinned" not in states and isinstance(legacy, bool):
        states["pinned"] = legacy
    return states


def pinned_open(user: User) -> bool:
    """Whether `[ PINNED MODELS ]` is open. Open unless somebody closed
    it: a shortcut group that starts folded away is one nobody finds."""
    return section_states(user).get("pinned", SECTION_DEFAULTS["pinned"])


def _device_sections(
    db: DBSession,
    user: User,
    chats: list[Chat],
    archived: list[Chat] | None = None,
) -> list[dict]:
    """The Agents side of the sidebar, one section per device.

    An agent chat runs on one linked LLeMbas CLI and nowhere else, so "which
    machine" is the first thing anybody looking for one asks -- and one list of
    every agent chat would answer it only in each row's tooltip. Devices linked now
    come first, each with a + that opens the new-agent-chat screen with it
    chosen; then the ones that are not linked but still have chats, marked and
    dimmed, with no + since nothing can start on them; then chats whose device
    is gone altogether. A device not linked and with no chats is not listed:
    Settings -> Devices is where every signed-in device is.

    Every agent chat, filed in a folder or not -- folders belong to the Chats
    side, so a section is the only place an agent chat is listed and leaving
    the filed ones out would hide them. And the archived ones too: this side
    has no separate Archived group, and each section shows its
    own according to its filter (`agent_filters`) -- active (the default),
    archived only, or all, active first and archived after them, dimmed. A
    device that is not linked and holds only archived chats still gets its
    section, by the same rule as one with live chats, or its archive would have
    nowhere to be found.
    """
    from lembas.db.models import TOKEN_DEVICE, ApiToken
    from lembas.services import device_link

    linked = {link.token_id: link for link in device_link.linked_for(user.id)}
    tokens = {
        token.id: token
        for token in db.scalars(
            select(ApiToken).where(ApiToken.user_id == user.id, ApiToken.kind == TOKEN_DEVICE)
        )
    }
    filters = agent_filters(user)

    def key_of(chat: Chat) -> str:
        return chat.device_token_id if chat.device_token_id in tokens else ""

    by_device: dict[str, list[Chat]] = {}
    for chat in chats:
        by_device.setdefault(key_of(chat), []).append(chat)
    archived_by_device: dict[str, list[Chat]] = {}
    for chat in archived or []:
        archived_by_device.setdefault(key_of(chat), []).append(chat)

    def named(token_id: str) -> dict:
        """The section's heading: the host, or "Local" in LLeMbas Desktop for
        the computer it runs on. Only ever one of this person's own tokens."""
        token = tokens[token_id]
        if token_id in linked:
            link = linked[token_id]
            return local_device.label(device_link.short_name(link), token.device_host, link.host)
        return local_device.label(token.device_host or token.name, token.device_host)

    def section(token_id: str, shown_as: dict | str, is_linked: bool) -> dict:
        active = by_device.get(token_id, [])
        put_away = archived_by_device.get(token_id, [])
        chosen = filters.get(token_id, AGENT_FILTER_DEFAULT)
        shown = {"active": active, "archived": put_away, "all": active + put_away}[chosen]
        if isinstance(shown_as, str):
            shown_as = {"name": shown_as, "title": shown_as, "local": False}
        return {
            "token_id": token_id,
            **shown_as,
            "linked": is_linked,
            "chats": shown,
            "filter": chosen,
            # Which empty sentence to say: "nothing yet" is false for a device
            # whose every chat has been archived.
            "has_any": bool(active or put_away),
            # The newest chat of either kind, for ordering the offline ones.
            "latest": max(
                (c.updated_at for c in (*active, *put_away) if c.updated_at is not None),
                default=None,
            ),
        }

    live = [section(token_id, named(token_id), True) for token_id in linked if token_id in tokens]
    # Local first, then by name. An offline Local leads the offline ones: the
    # linked come first because only they can start a chat.
    live.sort(key=lambda entry: (not entry["local"], entry["name"].lower()))
    with_chats = set(by_device) | set(archived_by_device)
    offline = [
        section(token_id, named(token_id), False)
        for token_id in with_chats
        if token_id and token_id not in linked
    ]
    # Most recently used first, which is the order the chats inside are in. The
    # flag first so a timestamp is only ever compared with another timestamp.
    offline.sort(
        key=lambda entry: (entry["latest"] is not None, entry["latest"] or ""), reverse=True
    )
    offline.sort(key=lambda entry: not entry["local"])
    missing = [section("", i18n.t("device missing"), False)] if "" in with_chats else []
    return live + offline + missing


def sidebar_context(db: DBSession, user: User) -> dict:
    """Folder tree plus the chats that belong to no folder.

    Public because every page carrying the chat sidebar needs it, which now
    includes the library.

    Only root folders are queried; children come through the relationship and
    render recursively in the template.

    Everything is narrowed to one `Chat.kind`.

    Folders are the Chats side's alone. The Agents side lists every agent chat
    under its device, filed or not, and shows no folders at all; the Chats side
    shows every folder, holding only its ordinary chats. Every folder, not only
    the ones with an ordinary chat in them: with the Agents side not showing
    folders, one hidden here would be hidden everywhere -- nowhere to
    rename it, delete it or file into it. A folder whose chats are all agent
    chats says "Empty", which on this side is what it is.
    """
    # With the switch absent the sidebar goes back to showing everything, rather
    # than to one side of a fork nobody can move. An administrator turning agent
    # chats off would otherwise strand whoever last left the switch on Agents in
    # a sidebar that is empty with no way out of it.
    can = permissions.resolve(db, user)
    api_only = settings_store.api_only(db)
    if api_only:
        # Every entry the sidebar and Settings gate on one of these disappears
        # with it -- New chat, New folder, pinned models, Reports, Scheduled,
        # Library and the Memory tab -- without a second condition in each.
        can = {key: allowed and key not in API_ONLY_HIDDEN for key, allowed in can.items()}
    split = can.get("tools.agent", False) and bool(
        settings_store.agents(db).get("enabled")
    ) and not api_only
    kind = sidebar_kind(user) if split else ""
    agents_side = kind == KIND_AGENT

    folders = (
        []
        if agents_side
        else list(
            db.scalars(
                select(Folder)
                .where(Folder.user_id == user.id, Folder.parent_id.is_(None))
                .order_by(Folder.position, Folder.name)
            )
        )
    )
    narrowed = select(Chat).where(
        Chat.user_id == user.id,
        Chat.folder_id.is_(None),
        Chat.archived.is_(False),
        Chat.temporary.is_(False),
        # `kind` empty means "both sides of the switch", never "no filter" --
        # see `Folder.visible_chats`. Task chats and the Messages conversation
        # have sections of their own and must never appear in this list, and
        # the case that reaches here with "" is precisely an instance with
        # agents disabled, where nobody would ever see the leak coming.
        Chat.kind.in_((kind,) if kind else KINDS),
    )
    unfiled = list(
        db.scalars(narrowed.order_by(Chat.pinned.desc(), Chat.updated_at.desc()))
    )
    # The same query with the one filter inverted, and no `pinned` in the order:
    # a pinned chat that somebody archived is one they have said two opposite
    # things about, and the more recent instruction is the one to honour.
    archived = list(
        db.scalars(
            select(Chat)
            .where(
                Chat.user_id == user.id,
                Chat.archived.is_(True),
                Chat.temporary.is_(False),
                Chat.kind.in_((kind,) if kind else KINDS),
            )
            .order_by(Chat.updated_at.desc())
        )
    )
    device_sections: list[dict] = []
    if agents_side:
        # Every live agent chat, filed in a folder or not -- see
        # `_device_sections`. The archived ones go with them, into their device's
        # section, so the side's own Archived group is empty by construction.
        every_agent_chat = list(
            db.scalars(
                select(Chat)
                .where(
                    Chat.user_id == user.id,
                    Chat.archived.is_(False),
                    Chat.temporary.is_(False),
                    Chat.kind == KIND_AGENT,
                )
                .order_by(Chat.pinned.desc(), Chat.updated_at.desc())
            )
        )
        device_sections = _device_sections(db, user, every_agent_chat, archived)
        archived = []
    return {
        "folders": folders,
        "unfiled_chats": unfiled,
        # The Agents side: one section per device rather than one list.
        "device_sections": device_sections,
        # Archived chats are NOT narrowed to unfiled ones: a chat inside a
        # folder disappears from that folder when it is archived (the folder's
        # own listing has always filtered them out), so without this it would
        # have left one list and joined none. Empty on the Agents side,
        # whose archived chats are in their device sections.
        "archived_chats": archived,
        # The shortcuts at the top of the sidebar. Here rather than in
        # `_chat_context`, where they used to be, for two reasons: they are
        # sidebar content and the fragment route that re-renders the sidebar has
        # only this, and the library and connections pages carry the sidebar
        # without ever calling `_chat_context` -- so the shortcuts simply were
        # not there on any of them. The picker lists every model in the
        # administrator's order, pinned or not; pinning is not ordering.
        "pinned_models": [m for m in chat_service.available_models(db, user) if m.pinned],
        # Whether that group is open, from the account rather than the
        # browser, so it is rendered the way it was left with no flash.
        "pinned_open": pinned_open(user),
        # And every other section's, for the same reason -- the tree is
        # re-rendered out of band (the switch, a device's filter, a shared
        # session arriving), and what somebody folded must survive each of
        # those. Folders carry theirs on the row (`Folder.collapsed`).
        "sections": section_states(user),
        # Whether the Reports entry starts with its dot showing. Only the first
        # paint: from then on `/api/chats/unread` moves it out of band, the same
        # deal a chat row's dot has. Counted rather than existence-checked
        # because the same query answers both and a count is what a title would
        # want if this ever grows one.
        "unread_reports": reports_service.unread_count(db, user),
        "sidebar_kind": kind,
        # Whether the switch is worth showing at all. A two-way switch with one
        # useful side is worse than no switch: it offers a view that is empty by
        # construction and cannot be made otherwise.
        "sidebar_split": split,
        "can": can,
        # API-only mode: the sidebar keeps Settings, Admin and sign-out
        # and loses the chat tree. The routes themselves are refused by
        # `services/instance_mode.py`; this only stops offering them.
        "api_only": api_only,
    }


@router.get("/")
async def home(user: RequiredUser):
    return RedirectResponse("/chat", status_code=status.HTTP_303_SEE_OTHER)


# --- Installing as an app -----------------------------------------------------
# All three routes below are deliberately unauthenticated. A browser fetches a
# manifest and a service worker outside any page's session, and an offline page
# has by definition no server to ask who is looking at it.


@router.get("/healthz", include_in_schema=False)
async def healthz() -> Response:
    """Is the process up and can it reach its database.

    Unauthenticated, like the three below, and for a fourth reason: a
    healthcheck that needed a session would be a healthcheck nothing could run.
    It says nothing about *what* is here -- no version, no counts -- because it
    is reachable without signing in and a health endpoint is a common place to
    leak the first fact an attacker wants.

    The query is what makes it worth having. A process that is up with a
    database it cannot open answers every page with a 500, and a check that only
    proved the socket was listening would call that healthy.
    """
    from sqlalchemy import text

    from lembas.db.session import session_scope

    try:
        with session_scope() as db:
            db.execute(text("SELECT 1"))
    except Exception:  # the answer is the status code
        return JSONResponse({"status": "error"}, status_code=503)
    return JSONResponse({"status": "ok"})


@router.get("/manifest.webmanifest", include_in_schema=False)
async def manifest(db: Db) -> Response:
    """The web app manifest.

    A route rather than a static file because the name is an instance setting,
    and an installed app showing "LLeMbas" when the instance is called something
    else would be wrong on the one screen that is hardest to correct: the
    launcher.
    """
    brand = branding_service.for_db(db)
    colour = _instance_colour(brand)
    return JSONResponse(
        {
            # Matches `start_url`. An id is only an identity key and need not be
            # navigable, but "/" named a path that serves nothing but a redirect
            # while the app started somewhere else, which reads as a mistake to
            # anyone comparing the two.
            "id": "/chat",
            "name": brand.name,
            "short_name": brand.name[:12],
            "description": "A web UI for your language models.",
            "lang": "en",
            "dir": "ltr",
            "start_url": "/chat",
            "scope": "/",
            "display": "standalone",
            # Ordered best-first: a browser takes the first it understands and
            # falls through to `display` if it understands none of them.
            "display_override": ["standalone", "minimal-ui"],
            "orientation": "any",
            "categories": ["productivity", "utilities"],
            # Opening a link belonging to this scope focuses the window that is
            # already open rather than making a second one.
            "launch_handler": {"client_mode": "navigate-existing"},
            # The launcher's long-press menu. Three destinations rather than
            # ten: a menu nobody can read at a glance is a menu nobody opens.
            "shortcuts": [
                {"name": "New chat", "url": "/chat"},
                {"name": "Reports", "url": "/library/reports"},
                {"name": "Scheduled", "url": "/settings?tab=scheduled"},
            ],
            # Both follow whatever theme this instance is set up in. They were
            # Moria's near-black regardless, so a parchment instance installed
            # to a phone flashed a dark splash screen and then opened light --
            # and `THEME_COLOUR["shire"]` sat beside them, defined and read by
            # nothing. The *instance* default and not the reader's own theme:
            # a manifest is fetched without credentials unless the link asks
            # otherwise, so there is nobody to ask.
            "background_color": colour,
            "theme_color": colour,
            # Without these, Chrome on Android offers the one-line mini-infobar
            # rather than the install dialog that carries a name, an icon and a
            # picture -- which is the difference between an install somebody
            # chooses and one they swipe away without reading. Captured from the
            # running application by `scripts/shoot.py --manifest-screenshots`,
            # because the one thing a screenshot must not be is a drawing of
            # what the application looks like.
            "screenshots": [
                {"src": "/static/img/screenshot-narrow.png", "sizes": "390x844",
                 "type": "image/png", "form_factor": "narrow",
                 "label": "A conversation on a phone"},
                {"src": "/static/img/screenshot-wide.png", "sizes": "1280x800",
                 "type": "image/png", "form_factor": "wide",
                 "label": "A conversation, with the sidebar beside it"},
            ],
            "icons": [
                {"src": "/static/img/icon-192.png", "sizes": "192x192",
                 "type": "image/png", "purpose": "any"},
                {"src": "/static/img/icon-512.png", "sizes": "512x512",
                 "type": "image/png", "purpose": "any"},
                {"src": "/static/img/icon-maskable-512.png", "sizes": "512x512",
                 "type": "image/png", "purpose": "maskable"},
            ],
        },
        media_type="application/manifest+json",
    )


@router.get("/sw.js", include_in_schema=False)
async def service_worker() -> Response:
    """The service worker, served from the root.

    A worker may only control pages at or below the path it was served from, so
    one delivered by the /static mount would have scope /static/js/ and control
    nothing. Serving it here is simpler than the Service-Worker-Allowed header
    that would be needed otherwise.

    no-store because a stale worker is a worker that keeps serving a stale
    cache: the one file in the application that must never be held onto.
    """
    return FileResponse(
        STATIC_DIR / "js" / "sw.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/offline", include_in_schema=False)
async def offline(request: Request) -> Response:
    return render(request, "offline.html", {})


@router.get("/chat")
async def chat_index(
    request: Request,
    db: Db,
    user: RequiredUser,
    model: str = "",
    temporary: bool = False,
    kind: str = "",
    folder: str = "",
    device: str = "",
):
    """A composer with no chat behind it yet.

    `?model=` preselects one, which is how the pinned shortcuts work without
    creating a row for a chat that may never be sent. `?temporary=1` is the
    same idea for the temporary flag: it lives in the URL rather than in
    JavaScript, so it survives a reload and can be bookmarked. `?kind=agent`
    is how the sidebar's Agent side opens a new chat already on that side --
    a preselection like the other two, not a decision: the kind is still
    chosen on the screen and still fixed only when the first message is sent.
    `?folder=` is the same again, and is what "New chat here" on a folder row
    posts: the chat is filed there, and `_new_chat` fills in whatever the
    folder seeds and the screen left empty. `?device=` is a device
    section's + in the sidebar: that device is chosen in the agent picker,
    which still lists only the devices linked now, so an id that is not one of
    them simply chooses nothing.
    """
    context = _chat_context(db, user, None)

    # Somebody else's folder id in the URL is ignored rather than refused. It
    # would only ever get there by hand, and an error page holding a composer
    # hostage over a bad query string helps nobody.
    starting_folder = db.get(Folder, folder) if folder else None
    if starting_folder is not None and starting_folder.user_id != user.id:
        starting_folder = None
    # A folder that fixes the kind picks the fork, unless the URL already said.
    if not kind and starting_folder is not None:
        kind = starting_folder.kind

    # Fall back to the same choice a new chat would make -- the user's default,
    # then the instance default, then first in order. Using models[0] here
    # instead would show a model the chat is not going to use, which matters:
    # the composer decides from it whether to warn that images will be dropped.
    preselected = next((m for m in context["models"] if m.model_id == model), None)
    # The folder's own model, ahead of the reader's default and behind an
    # explicit `?model=`. Same order `_new_chat` applies, so the picker shows
    # the model the chat is actually going to be created with -- which matters,
    # because the composer decides from it whether to warn about images.
    if preselected is None and starting_folder is not None and starting_folder.model_id:
        preselected = next(
            (m for m in context["models"] if m.model_id == starting_folder.model_id), None
        )
    if preselected is None:
        chosen = chat_service.default_model(db, user)
        if chosen is not None:
            preselected = next(
                (m for m in context["models"] if m.model_id == chosen[0]), None
            )
    if preselected is None and context["models"]:
        preselected = context["models"][0]

    # Every preselection lives in the URL, so every link that changes one of
    # them has to carry the rest. The temporary toggle used to link to a bare
    # `/chat?temporary=1` and the model picker to a bare `/chat?model=`, so
    # each undid the other: temporary chats could only ever be started on the
    # default model. The model goes last in the picker's URL because ui.js
    # appends the chosen id to it.
    carried = {
        "model": model if model and preselected and preselected.model_id == model else "",
        "temporary": "1" if temporary else "",
        "kind": kind if kind in KINDS and kind != KIND_CHAT else "",
        "folder": starting_folder.id if starting_folder is not None else "",
        "device": device if any(d["value"] == f"device:{device}" for d in context["agent_devices"])
        else "",
    }

    def new_chat_url(**changes: str) -> str:
        query = urlencode({k: v for k, v in {**carried, **changes}.items() if v})
        return f"/chat?{query}" if query else "/chat"

    without_model = new_chat_url(model="")
    return render(
        request,
        "chat/index.html",
        {
            "chat": None,
            "messages": [],
            "bodies": {},
            **context,
            "current_model": preselected,
            # `_chat_context` reads the efforts off the *chat's* model, and there
            # is no chat here -- so every new chat was offered the generic three
            # whatever it was about to talk to. On Bonsai (low, medium, xhigh)
            # the configured `xhigh` was not among them, and the picker fell
            # through to "off". The chat created from this screen then got
            # `xhigh` anyway, so the control said one thing and the first reply
            # did another.
            "efforts": (
                chat_service.efforts_for(preselected)
                if preselected
                else chat_service.DEFAULT_EFFORTS
            ),
            # What the effort slider starts at: what `_new_chat` will seed --
            # this person's own default for the model, else the model's.
            "new_chat_effort": model_prefs.default_effort(db, user, preselected),
            "starting_temporary": temporary,
            "temporary_toggle_url": new_chat_url(temporary="" if temporary else "1"),
            "model_navigate_url": without_model + ("&" if "?" in without_model else "?") + "model=",
            "starting_kind": kind if kind in KINDS else KIND_CHAT,
            "starting_folder": starting_folder,
            "starting_device": f"device:{carried['device']}" if carried["device"] else "",
            "suggestions": suggestions_service.visible(db),
            **sidebar_context(db, user),
        },
    )


def _candidate_parents(db: DBSession, user_id: str, folder: Folder) -> list[Folder]:
    from lembas.api.folders import candidate_parents

    return candidate_parents(db, user_id, folder)


@router.get("/folders/{folder_id}")
async def folder_settings(request: Request, db: Db, user: RequiredUser, folder_id: str):
    """What a folder hands to the chats started inside it.

    A page rather than a row that expands, following the admin convention: a
    form per row in a tree that nests eight deep would be unusable, and the
    sidebar is the one part of the application that has to stay scannable.

    Guarded by `folder.manage`, the same permission the whole folder router
    carries -- editing a folder's system prompt is managing a folder, and a page
    that renders for somebody whose save is going to 403 is a trap.
    """
    if not permissions.has(db, user, "folder.manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, i18n.t("You cannot manage folders."))

    folder = db.get(Folder, folder_id)
    if folder is None or folder.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That folder no longer exists."))

    return render(
        request,
        "folders/edit.html",
        {
            "folder": folder,
            "chat": None,
            # Imported here rather than at module scope: `api.folders` imports
            # `api.deps`, which this module is a peer of, and the pair have been
            # kept apart deliberately.
            "parents": _candidate_parents(db, user.id, folder),
            "models": chat_service.available_models(db, user),
            **_agent_context(db, user, None),
            **sidebar_context(db, user),
        },
    )


@router.get("/chat/{chat_id}")
async def chat_detail(request: Request, db: Db, user: RequiredUser, chat_id: str):
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That chat no longer exists."))

    # Opening the chat is what "read" means.
    if chat.unread:
        chat.unread = False
        chat.unread_notified = False
        db.commit()

    everything = list(
        db.scalars(
            select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at)
        )
    )
    # Summarised turns are kept and still rendered, behind a divider -- they
    # have only stopped being part of the request.
    compacted, messages = compaction_service.split(db, chat, everything)
    # Opened again -- a reload, or another tab -- so a leave this chat's page
    # reported a moment ago did not happen. See chat_service.sweep_temporary.
    if chat.temporary:
        chat_service.mark_back(chat.id)

    # Empty, and kept only so `_thread.html` and the four handlers that render a
    # bubble keep one signature between them. An assistant turn is rendered from
    # its steps now (`message_steps`, a Jinja global), which is what lets a
    # reply's prose sit either side of the tool call it surrounded rather than
    # arriving as one block at the bottom. Nothing reads this for an assistant
    # message any more; `library/note_detail.html` has its own.
    bodies: dict[str, str] = {}

    # What the chat would use if its own prompt were empty, so the settings
    # panel can show it as placeholder text rather than leaving the user to
    # guess what "inherited" means.
    #
    # This mirrors `chat_service.effective_system_prompt` and has to keep
    # mirroring it, layer for layer and in the same order -- a panel naming the
    # wrong source is worse than one naming none, because it is believed.
    inherited, inherited_from = "", ""
    folder_prompt = chat_service.folder_system_prompt(db, chat)
    current = next(
        (m for m in chat_service.available_models(db, user) if m.model_id == chat.model_id), None
    )
    if folder_prompt:
        inherited, inherited_from = folder_prompt, "folder"
    elif current is not None and (current.system_prompt or "").strip():
        inherited, inherited_from = current.system_prompt.strip(), "model"
    else:
        instance_prompt = (settings_store.get(db, "system_prompt") or "").strip()
        if instance_prompt:
            inherited, inherited_from = instance_prompt, "instance"

    # The chat's background jobs for the strip: its rows, or -- on a device
    # chat -- what the device says, when it is linked and can say. Never a
    # reason for the page not to open.
    from lembas.api.chats import chat_jobs

    jobs: list[dict] = []
    if chat.kind != KIND_TASK:
        try:
            jobs = await chat_jobs(db, chat)
        except Exception:  # the strip is a convenience
            log.debug("could not list the jobs of chat %s", chat.id, exc_info=True)

    return render(
        request,
        "chat/index.html",
        {
            "chat": chat,
            "messages": messages,
            "compacted": compacted,
            "bodies": bodies,
            "jobs": jobs,
            "inherited_prompt": inherited,
            "inherited_from": inherited_from,
            **_schedule_context(db, user, chat),
            **_chat_context(db, user, chat),
            **sidebar_context(db, user),
        },
    )


def _schedule_context(db: DBSession, user: User, chat: Chat) -> dict:
    """What the strip below a task chat needs.

    Empty for every other kind, so the three keys exist unconditionally and the
    template can ask about `schedule` without a `default(false)` -- the same
    reason `audio_service.template_flags` is passed by all four bubble
    renderers rather than by whichever one remembered.

    `schedule` being None on a task chat is a real state, not an error: removing
    a schedule keeps its chat by default, and the strip says so.
    """
    from lembas.services import schedules as schedules_service

    if chat is None or chat.kind != KIND_TASK:
        return {"schedule": None, "schedule_summary": "", "schedule_next": None}

    schedule = schedules_service.for_chat(db, chat)
    if schedule is None:
        return {"schedule": None, "schedule_summary": "", "schedule_next": None}

    zone = clock.zone_for(user)
    return {
        "schedule": schedule,
        "schedule_summary": schedules_service.describe(schedule, owner=user),
        "schedule_next": (
            clock.as_utc(schedule.next_fire_at).astimezone(zone)
            if schedule.next_fire_at
            else None
        ),
    }


def _credit_settings(db: DBSession, user: User) -> dict | None:
    """The usage card in Settings: the balance in credits and money, the plan
    and when it renews, its windows, this month's spend per model, and the last
    ledger rows with what each cost."""
    from lembas.services import credits as credits_service

    values = settings_store.credits(db)
    if not values.get("enabled") or not values.get("show_balance"):
        return None
    summary = credits_service.summary(db, user)

    def money(micro: int) -> str:
        return credits_service.format_money(
            credits_service.micro_to_money(micro, summary["credit_value"]), summary["currency"]
        )

    return {
        **summary,
        "visible": True,
        "show": credits_service.format_credits,
        "money": money,
        "ledger": credits_service.ledger(db, user.id, limit=10),
    }


def _api_settings(db: DBSession, user: User) -> dict:
    """This person's tokens and devices, and what a new token may be given."""
    from lembas.db.models import TOKEN_PERSONAL, ApiToken
    from lembas.security import api_tokens
    from lembas.security import permissions as perms
    from lembas.services import devices as devices_service

    if not perms.has(db, user, "api.use"):
        return {
            "api_allowed": False,
            "api_tokens": [],
            "api_devices": [],
            "api_scopes": [],
            "linked_now": set(),
        }
    personal = list(
        db.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == user.id, ApiToken.kind == TOKEN_PERSONAL)
            .order_by(ApiToken.created_at.desc())
        )
    )
    return {
        "api_allowed": True,
        "api_tokens": personal,
        "api_devices": devices_service.devices_of(db, user),
        "api_scopes": [
            (scope, api_tokens.scope_label(scope))
            for scope in api_tokens.grantable(db, user)
            if scope != api_tokens.SCOPE_LINK
        ],
        "api_live": api_tokens.live,
        # Which of them are dialled in right now, for the Devices tab.
        "linked_now": _linked_now(user),
    }


def _linked_now(user: User) -> set[str]:
    from lembas.services import device_link

    return {link.token_id for link in device_link.linked_for(user.id)}


@router.get("/settings")
async def settings_page(
    request: Request,
    db: Db,
    user: RequiredUser,
    error: str = "",
    saved: str = "",
    tab: str = "",
    embed: str = "",
):
    from lembas.api.audio import available_voices
    from lembas.services import personalization as personalization_service
    from lembas.services import reset as reset_service
    from lembas.services.library import memories as memories_service

    context = _chat_context(db, user, None)
    sidebar = sidebar_context(db, user)
    # Fetched here rather than by the template so a speech server that is down
    # leaves the page renderable, with the reason beside an empty list.
    voices, voice_error = await available_voices(context["audio"])

    # The settings window: on a desktop, a link to /settings opens this
    # page in a frame inside a dialog over wherever the reader was, rather than
    # navigating away from it. In the frame it is rendered without the shell --
    # no sidebar, no topbar; the dialog has its own title and close button.
    #
    # `embed=1` is what the dialog asks for. `Sec-Fetch-Dest: iframe` is what
    # keeps the frame slim after that: every form here redirects back to a plain
    # `/settings?saved=…`, and threading `embed` through some thirty redirects
    # (preferences, library, tokens, devices, schedules) would be thirty places
    # to forget it. A browser says on every request whether it is loading a
    # frame, so the redirect's GET carries the answer by itself. Nothing else
    # frames /settings -- prototypes have their own sealed route -- and a
    # browser that sends no such header is caught by ui.js, which reloads the
    # frame with `embed=1` when the page that arrived has a sidebar in it.
    settings_embed = embed == "1" or request.headers.get("sec-fetch-dest") == "iframe"

    # error/saved arrive as query parameters because the password form redirects
    # back here: a POST that re-rendered in place would re-submit on refresh.
    return render(
        request,
        "settings.html",
        {
            "chat": None,
            "settings_embed": settings_embed,
            "tab": tab,
            "error": error,
            "saved": saved,
            "voices": voices,
            "voice_error": voice_error,
            "memories": memories_service.all_for(db, user),
            "memory_limit": memories_service.MAX_MEMORY_CHARS,
            # What each Start over level would delete, named and counted, for
            # the card and its confirmations (services/reset.py).
            "reset_preview": reset_service.preview(db, user),
            # API tokens and signed-in devices, in the Security tab.
            **_api_settings(db, user),
            # Custom instructions and a personality.
            "personalization_enabled": personalization_service.enabled(db),
            "personalization": personalization_service.of(user),
            "personality_presets": [
                (key, label) for key, (label, _) in personalization_service.PRESETS.items()
            ],
            # What `lembas login` is pointed at, as this page was reached.
            "instance_url": str(request.base_url).rstrip("/"),
            # Credits, in the Account tab, when the instance uses them
            # and lets people see their own.
            "credit": _credit_settings(db, user),
            **_data_group_settings(db, user),
            # Sorted rather than left in set order, because a list of six
            # hundred zones that is not alphabetical is one nobody can use.
            "languages": i18n.LANGUAGES,
            # Their own choice, and what "follow the instance" currently means --
            # named rather than left blank, because "follow the instance" is only a
            # useful option if you can see what you would be following.
            "chosen_language": str((user.settings_json or {}).get("language") or ""),
            "instance_language": dict(i18n.LANGUAGES).get(
                i18n.instance_default(), i18n.instance_default()
            ),
            "timezones": sorted(available_timezones()),
            "timezone": clock.name_for(user),
            "server_timezone": str(clock.server_zone()),
            # Through `i18n.stamp`, not `strftime`: `%A` and `%B` are C-locale
            # English whatever the page is in, and this one is read by a person.
            "local_now": i18n.stamp(clock.now_for(user), "%H:%M on %A %-d %B"),
            # The Scheduled tab. Through the
            # sidebar's `can`, which API-only mode has already narrowed.
            "schedules": _schedules_listed(db, user) if sidebar["can"].get("schedule.use") else [],
            **_model_preferences(db, user),
            **context,
            **sidebar,
        },
    )


def _model_preferences(db: DBSession, user: User) -> dict:
    """Settings -> Models, a section per model: the tools it offers
    this person and whether each is on for them, and on a reasoning model the
    levels it takes and the one a new chat starts at. See `model_prefs`."""
    from lembas.services import model_prefs

    entries = []
    for model in chat_service.available_models(db, user):
        reasoning = model_prefs.reasoning(model)
        seeded = (model.params_json or {}).get("reasoning_effort")
        tools = model_prefs.offered(db, user, model)
        entries.append({
            "model": model,
            "tools": tools,
            "tools_off": sum(1 for tool in tools if not tool["on"]),
            "efforts": chat_service.efforts_for(model) if reasoning else (),
            "effort": model_prefs.effort_choice(db, user, model),
            # What "as the instance sets it" currently means, named.
            "instance_effort": seeded if seeded in chat_service.EFFORTS else "",
        })
    return {"model_prefs": entries}


def _schedules_listed(db: DBSession, user: User) -> list[dict]:
    from lembas.api.schedules import listed

    return listed(db, user)
