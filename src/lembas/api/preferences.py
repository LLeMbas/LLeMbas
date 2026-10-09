"""Per-user preferences set from the browser."""

from __future__ import annotations

import contextlib
import logging
from urllib.parse import quote

from fastapi import APIRouter, Body, Form, Request, status
from fastapi.responses import RedirectResponse, Response

from lembas.api.deps import Db, RequiredUser
from lembas.config import settings
from lembas.security import hardening
from lembas.security.passwords import hash_password, validate_password, verify_password
from lembas.security.sessions import COOKIE_NAME, create_session, revoke_all_for_user
from lembas.services.schedule import clock
from lembas.web import i18n

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/preferences", tags=["preferences"])

# The built-in pair used to be spelled out here, and in four other places. It is
# one server-resolved list now, because an administrator can define a theme and a
# hard-coded pair would refuse it -- silently, since this route answers a
# rejection with `{"ok": false}` that nothing displays.
def themes() -> tuple[str, ...]:
    from lembas.services import branding

    return branding.snapshot().theme_ids


@router.post("/theme")
async def set_theme(db: Db, user: RequiredUser, theme: str = Body(..., embed=True)) -> dict:
    """Mirror the browser's theme choice onto the account.

    localStorage is the source of truth for the current tab; this is what makes
    the choice follow the user to another browser, and what lets the server
    render the right theme on first paint instead of flashing the default.
    """
    if theme not in themes():
        return {"ok": False, "detail": "Unknown theme."}

    # Replaced rather than mutated in place: SQLAlchemy only reliably detects
    # a change to a JSON column when the whole value is reassigned.
    user.settings_json = {**(user.settings_json or {}), "theme": theme}
    db.commit()
    return {"ok": True, "theme": theme}


@router.post("/timezone")
async def set_timezone(db: Db, user: RequiredUser, timezone: str = Form("")) -> Response:
    """Which zone this person's schedules fire in, and what time they are told it is.

    Empty is a real answer -- "whatever the server is set to" -- rather than an
    unset field, which is why it is stored as "" instead of being removed. An
    unrecognised name is refused rather than stored and fallen back from later:
    a schedule that quietly fires in the wrong zone is the failure this whole
    field exists to prevent, and the one place to catch it is the write.
    """
    chosen = (timezone or "").strip()
    if chosen and not clock.known(chosen):
        return RedirectResponse(
            "/settings?error=timezone", status_code=status.HTTP_303_SEE_OTHER
        )
    user.settings_json = {**(user.settings_json or {}), clock.SETTING_KEY: chosen}
    db.commit()
    return RedirectResponse("/settings?saved=timezone", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/language")
async def set_language(db: Db, user: RequiredUser, language: str = Form("")) -> Response:
    """Which language this person sees the interface in.

    Empty is a real answer -- "whatever the instance is set to" -- rather than an
    unset field, which is why it is stored as "" rather than removed. The same
    shape the timezone above uses, and for the same reason: absent and "follow the
    default" are different states, and a form cannot tell them apart otherwise.

    Unlike the theme and the layout this needs no `localStorage` tier. Those two
    exist there because a paint that starts in the wrong theme flashes; text is
    rendered on the server and cannot.
    """
    chosen = (language or "").strip().lower()
    if chosen and chosen not in i18n.LANGUAGE_IDS:
        return RedirectResponse(
            "/settings?error=language", status_code=status.HTTP_303_SEE_OTHER
        )
    user.settings_json = {**(user.settings_json or {}), "language": chosen}
    db.commit()
    return RedirectResponse("/settings?saved=language", status_code=status.HTTP_303_SEE_OTHER)


# Which CSS variables a browser is allowed to set from here, and how far. An
# open dict would let a page store anything under somebody's account and have
# it read back on every load; a width outside these bounds would hand them a
# panel they cannot see to drag back.
LAYOUT_BOUNDS = {
    "--terminal-width": (384, 2400),
    "--canvas-width": (384, 2400),
    "--prototype-width": (384, 2400),
    "--inspector-width": (280, 2400),
    "--sidebar-width": (200, 800),
}


@router.post("/layout")
async def set_layout(db: Db, user: RequiredUser, widths: dict = Body(...)) -> dict:
    """Remember how wide somebody dragged the panels.

    Same two tiers as the theme: `localStorage` is the truth for the tab that
    did the dragging, and this is what carries it to another browser. Unknown
    names are dropped rather than refused -- an older browser sending a key a
    newer release removed should not fail the request.
    """
    kept: dict[str, int] = {}
    for name, raw in (widths or {}).items():
        bounds = LAYOUT_BOUNDS.get(str(name))
        if bounds is None:
            continue
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            continue
        kept[str(name)] = min(max(value, bounds[0]), bounds[1])

    settings = {**(user.settings_json or {})}
    settings["layout"] = {**(settings.get("layout") or {}), **kept}
    user.settings_json = settings
    db.commit()
    return {"ok": True, "layout": kept}


@router.post("/sidebar-kind")
async def set_sidebar_kind(
    request: Request, db: Db, user: RequiredUser, kind: str = Form("")
) -> Response:
    """Switch the sidebar between ordinary chats and agent chats.

    Saves and re-renders in one round trip, because the two cannot be allowed to
    disagree: a switch that stored a choice and left the tree showing the other
    side would look broken, and re-rendering without storing would lose it on the
    next navigation. The tree comes back as a fragment rather than an `HX-Refresh`
    -- a full reload is what `api/folders.py` does for a structural change, and it
    would throw away the folder open/closed state on every flick of the switch,
    which is the same thing `/api/chats/unread` avoids by swapping out of band.

    An unrecognised value is refused rather than stored: `sidebar_kind` reads it
    back as "chat" anyway, so storing it would be a preference that silently
    does nothing.
    """
    from lembas.api.pages import sidebar_context
    from lembas.db.models import KINDS
    from lembas.web.templating import templates

    if kind not in KINDS:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    user.settings_json = {**(user.settings_json or {}), "sidebar_kind": kind}
    db.commit()

    return templates.TemplateResponse(
        request,
        "partials/_sidebar_tree.html",
        # `oob` brings the New chat button along out of band. It sits above the
        # scroll area rather than inside the tree, so a swap of the tree alone
        # left it saying "New chat" while agent chats were listed underneath.
        {"chat": None, "user": user, "oob": True, **sidebar_context(db, user)},
    )


@router.post("/pinned-open")
async def set_pinned_open(db: Db, user: RequiredUser, state: str = Form("1")) -> Response:
    """Remember whether `[ PINNED MODELS ]` is open or closed.

    On the account, not in `localStorage`, because the sidebar renders it: the
    server writes `open` into the `<details>`, so the group is drawn the way it
    was left with no flash -- and the same way after the Chats/Agents switch
    swaps it out of band, which re-renders it from this. Posted by app.js on the
    element's `toggle` event; nothing comes back to swap, the browser already
    shows the new state.

    Anything other than "0" is open, so a malformed request can only leave the
    shortcuts showing.
    """
    # Kept for a page loaded before an update, whose app.js still
    # posts here; it lands in the one map every section now uses.
    return _store_section(db, user, "pinned", state != "0")


def _store_section(db, user, key: str, is_open: bool) -> Response:
    from lembas.api.pages import SECTION_DEFAULTS, SECTIONS_KEY

    settings_map = {**(user.settings_json or {})}
    stored = settings_map.get(SECTIONS_KEY)
    chosen = dict(stored) if isinstance(stored, dict) else {}
    # A device's section, like every section without a default of its own,
    # starts open.
    default = SECTION_DEFAULTS.get(key, True)
    if is_open == default:
        chosen.pop(key, None)
    else:
        chosen[key] = is_open
    settings_map[SECTIONS_KEY] = chosen
    # The older `pinned_open` key is retired as soon as anything is written: left behind, it
    # would be read back as the fallback for a `pinned` that was just removed
    # for being the default, and undo it.
    settings_map.pop("pinned_open", None)
    user.settings_json = settings_map
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/section")
async def set_section_open(
    db: Db, user: RequiredUser, key: str = Form(""), state: str = Form("1")
) -> Response:
    """Remember whether one sidebar section is open or folded.

    Every section heading -- `[ PINNED MODELS ]`, `[ CHATS ]`, each root folder
    and each child folder, each device on the Agents side, Archived -- is one
    button whose press app.js applies at once and posts here. On the account
    rather than in the browser, because the server renders the tree, out of
    band too, and draws each section the way it was left: no flash, and no swap
    that quietly re-opens what somebody shut.

    Keys are checked rather than stored blind: a folder or a device has to be
    this person's own, so the map cannot be grown with keys for things that are
    not theirs -- and a key nothing renders would be a preference that does
    nothing. "0" is folded; anything else is open, so a malformed request can
    only leave a section showing. Nothing comes back to swap.
    """
    from lembas.api.pages import SECTION_DEFAULTS
    from lembas.db.models import TOKEN_DEVICE, ApiToken, Folder

    is_open = state != "0"
    kind, _, ident = key.partition(":")
    if key in SECTION_DEFAULTS:
        return _store_section(db, user, key, is_open)
    if kind == "folder" and ident:
        folder = db.get(Folder, ident)
        if folder is None or folder.user_id != user.id:
            return Response(status_code=status.HTTP_400_BAD_REQUEST)
        folder.collapsed = not is_open
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if kind == "device" and key.startswith("device:"):
        if ident:
            token = db.get(ApiToken, ident)
            if token is None or token.user_id != user.id or token.kind != TOKEN_DEVICE:
                return Response(status_code=status.HTTP_400_BAD_REQUEST)
        return _store_section(db, user, key, is_open)
    return Response(status_code=status.HTTP_400_BAD_REQUEST)


@router.post("/agent-filter")
async def set_agent_filter(
    request: Request,
    db: Db,
    user: RequiredUser,
    device: str = Form(""),
    show: str = Form(""),
) -> Response:
    """Which chats one device's section on the Agents side lists.

    Saves and re-renders the tree in one round trip, exactly as the Chats/Agents
    switch does and for its reason: a stored choice the tree did not follow
    would look broken, and a tree that changed without storing would forget on
    the next navigation.

    `device` is a device token of this person's, or "" for the "device missing"
    section; `show` is one of `AGENT_FILTERS`. Somebody else's token, or an
    unknown filter, is refused rather than stored -- a preference for a section
    that cannot be shown is one that silently does nothing. The default
    ("active") is removed rather than stored, so the map only ever holds the
    sections somebody changed.
    """
    from lembas.api.pages import AGENT_FILTER_DEFAULT, AGENT_FILTERS, sidebar_context
    from lembas.db.models import TOKEN_DEVICE, ApiToken
    from lembas.web.templating import templates

    if show not in AGENT_FILTERS:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)
    if device:
        token = db.get(ApiToken, device)
        if token is None or token.user_id != user.id or token.kind != TOKEN_DEVICE:
            return Response(status_code=status.HTTP_400_BAD_REQUEST)

    settings = {**(user.settings_json or {})}
    chosen = dict(settings.get("agent_filter") or {})
    if show == AGENT_FILTER_DEFAULT:
        chosen.pop(device, None)
    else:
        chosen[device] = show
    settings["agent_filter"] = chosen
    user.settings_json = settings
    db.commit()

    return templates.TemplateResponse(
        request,
        "partials/_sidebar_tree.html",
        {"chat": None, "user": user, "oob": False, **sidebar_context(db, user)},
    )


@router.post("/default-model")
async def set_default_model(
    db: Db, user: RequiredUser, model_id: str = Form("")
) -> Response:
    """Choose which model new chats start with.

    An empty value clears the choice and falls back to the instance default.
    Validated against what this user can actually reach, so a model they lose
    access to cannot linger as a preference that silently fails later.
    """
    from lembas.security import permissions

    model_id = model_id.strip()
    if model_id and not permissions.can_use_model(db, user, model_id):
        return RedirectResponse(
            f"/settings?tab=models&error={quote(i18n.t('That model is not available to you.'))}",
            status_code=303,
        )

    settings_map = {**(user.settings_json or {})}
    if model_id:
        settings_map["default_model"] = model_id
    else:
        settings_map.pop("default_model", None)
    user.settings_json = settings_map
    db.commit()

    return RedirectResponse(
        f"/settings?tab=models&saved={quote(i18n.t('Default model updated.'))}", status_code=303
    )


@router.post("/models")
async def set_model_preferences(
    db: Db,
    user: RequiredUser,
    model_id: str = Form(""),
    tools_on: list[str] = Form(default=[]),
    reasoning_effort: str = Form(""),
) -> Response:
    """This person's own defaults for one model: which of the tools it
    offers stay on with them, and the effort a new chat starts at.

    Narrowing only, and checked here rather than trusted from the form: the
    gates are recomputed from what the model offers this person now, so only
    those can be switched off and nothing can be switched on beyond them; an
    effort that is not one of the model's levels, or a model not marked as
    reasoning, leaves the instance's choice in force.
    """
    from lembas.services import chat as chat_service
    from lembas.services import model_prefs

    model = next(
        (m for m in chat_service.available_models(db, user) if m.model_id == model_id.strip()),
        None,
    )
    if model is None:
        return RedirectResponse(
            f"/settings?tab=models&error={quote(i18n.t('That model is not available to you.'))}",
            status_code=303,
        )
    offered = {tool["gate"] for tool in model_prefs.offered(db, user, model)}
    model_prefs.save(
        db,
        user,
        model,
        tools_off_now=offered - set(tools_on),
        offered_gates=offered,
        effort=reasoning_effort,
    )
    message = quote(i18n.t("Saved for %(model)s.", model=model.label))
    return RedirectResponse(
        f"/settings?tab=models&saved={message}#model-{model.id}", status_code=303
    )


@router.post("/audio")
async def set_audio(
    db: Db,
    user: RequiredUser,
    voice: str = Form(""),
    speed: str = Form(""),
    language: str = Form(""),
    autoplay: bool = Form(False),
) -> Response:
    """Per-reader audio choices, overriding the instance defaults.

    The voice is deliberately not checked against the discovered list. Voices
    come and go when a speech server is reconfigured, and rejecting a saved
    preference because a list fetched a moment ago did not mention it would be
    a confusing failure with no obvious fix.
    """
    chosen: dict[str, object] = {"autoplay": autoplay}
    if voice.strip():
        chosen["voice"] = voice.strip()[:120]
    if language.strip():
        chosen["language"] = language.strip()[:16]
    if speed.strip():
        # An unreadable speed leaves the default in place rather than failing:
        # nothing else on the form should be lost to a typo in one field.
        with contextlib.suppress(ValueError):
            chosen["speed"] = min(max(float(speed), 0.25), 4.0)

    # Whole-dict reassignment: an in-place edit of a JSON column is not
    # reliably detected as a change.
    user.settings_json = {**(user.settings_json or {}), "audio": chosen}
    db.commit()
    return RedirectResponse(
        f"/settings?saved={quote(i18n.t('Audio preferences updated.'))}", status_code=303
    )


@router.post("/password")
async def change_password(
    request: Request,
    db: Db,
    user: RequiredUser,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
) -> Response:
    """Change your own password.

    Every other session is revoked on success. If the reason for changing a
    password is that someone else knows it, leaving their session alive would
    defeat the point.
    """

    def back(message: str, ok: bool = False) -> Response:
        field = "saved" if ok else "error"
        return RedirectResponse(
            f"/settings?{field}={quote(message)}", status_code=status.HTTP_303_SEE_OTHER
        )

    if not verify_password(current_password, user.password_hash):
        log.info("failed password change for %s: current password wrong", user.email)
        return back(i18n.t("Your current password is not correct."))

    if new_password != confirm_password:
        return back(i18n.t("The new passwords do not match."))

    if (problem := validate_password(new_password)) is not None:
        return back(problem)

    if verify_password(new_password, user.password_hash):
        return back(i18n.t("That is already your password."))

    user.password_hash = hash_password(new_password)
    db.commit()

    revoke_all_for_user(db, user)
    token = create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=request.client.host if request.client else "",
    )
    log.info("password changed for %s; other sessions revoked", user.email)

    # revoke_all_for_user killed this session too, so hand back a fresh cookie
    # -- otherwise changing your password would sign you out of the tab you are
    # standing in.
    response = back(
        i18n.t("Password changed. Any other sessions have been signed out."), ok=True
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl,
        httponly=True,
        samesite="lax",
        secure=hardening.is_https(request),
        path="/",
    )
    from lembas.services import audit

    audit.record(
        "auth.password", user=user, request=request, summary=f"{user.email} changed their password"
    )
    return response


# --- Start over --------------------------------------------------------------
# A person empties their own data, at one of three levels; services/reset.py
# says what each one deletes and keeps. Three plain form posts rather than htmx:
# the page that comes back has to be re-read anyway, since the sidebar, the
# Memory tab and the counts all change.
@router.post("/reset/{level}")
async def start_over(
    db: Db,
    user: RequiredUser,
    level: str,
    current_password: str = Form(""),
    confirm_word: str = Form(""),
) -> Response:
    from lembas.services import reset as reset_service

    def back(message: str, ok: bool = False) -> Response:
        field = "saved" if ok else "error"
        return RedirectResponse(
            f"/settings?{field}={quote(message)}", status_code=status.HTTP_303_SEE_OTHER
        )

    if level not in reset_service.LEVELS:
        return back(i18n.t("There is no such reset."))

    if level == reset_service.LEVEL_ALL:
        # The one that cannot be undone and takes everything: the password, so a
        # browser left signed in is not enough, and a typed word, so a slip of the
        # mouse is not either.
        if not verify_password(current_password, user.password_hash):
            log.info("start-over refused for %s: wrong password", user.email)
            return back(i18n.t("Your password is not correct, so nothing was deleted."))
        word = confirm_word.strip().upper()
        if word not in {reset_service.CONFIRM_WORD, i18n.t("RESET").upper()}:
            return back(
                i18n.t("Type %(word)s to confirm a full reset. Nothing was deleted.",
                       word=i18n.t("RESET"))
            )

    try:
        counts = reset_service.reset(db, user, level)
    except reset_service.ResetRefused as refused:
        return back(i18n.t(str(refused)) + " " + i18n.t("Nothing was deleted."))

    from lembas.services import audit

    audit.record(
        f"reset.{level}",
        user=user,
        target_kind="user",
        target_id=user.id,
        summary=f"{user.email} started over ({level}): {counts.total} rows",
        detail=dict(counts.rows),
    )
    if not counts.total:
        return back(i18n.t("There was nothing to delete."), ok=True)
    return back(
        {
            reset_service.LEVEL_CHATS: i18n.t("Your chats and folders have been deleted."),
            reset_service.LEVEL_MEMORY: i18n.t(
                "Everything models remembered about you has been forgotten."
            ),
            reset_service.LEVEL_ALL: i18n.t(
                "You have started over. Your account and settings are as they were."
            ),
        }[level],
        ok=True,
    )


# --- Data groups -------------------------------------------------------------
# A person's own arrangement of which provider may read which of their data.
# Everything here needs `data.manage`: it changes what a provider can see, and an
# instance that never granted it keeps the arrangement its administrator made.
def _refuse_without_manage(db, user) -> Response | None:
    from lembas.services import data_groups

    if data_groups.may_manage(db, user):
        return None
    return RedirectResponse(
        f"/settings?error={quote(i18n.t('You may not manage your own data groups.'))}",
        status_code=303,
    )


@router.post("/data-groups")
async def set_data_groups(request: Request, db: Db, user: RequiredUser) -> Response:
    """Which group each connection reads, for this person.

    One select per connection, named `group__<connection id>`; empty means
    "follow the instance", which removes the entry rather than storing a copy of
    the administrator's choice -- a copy would stop following it the day it
    changed.
    """
    from lembas.services import data_groups

    refused = _refuse_without_manage(db, user)
    if refused is not None:
        return refused
    form = await request.form()
    chosen: dict[str, str] = {}
    for key, value in form.items():
        if not key.startswith("group__"):
            continue
        connection_id, group_id = key.removeprefix("group__"), str(value).strip()
        if group_id and data_groups.may_use(db, user, group_id):
            chosen[connection_id] = group_id
    user.settings_json = {**(user.settings_json or {}), data_groups.SETTING_KEY: chosen}
    db.commit()
    return RedirectResponse(
        f"/settings?saved={quote(i18n.t('Data groups updated.'))}", status_code=303
    )


@router.post("/data-groups/new")
async def create_personal_group(
    db: Db, user: RequiredUser, name: str = Form("")
) -> Response:
    from lembas.db.models import DataGroup

    refused = _refuse_without_manage(db, user)
    if refused is not None:
        return refused
    name = " ".join(name.split())[:120]
    if not name:
        return RedirectResponse(
            f"/settings?error={quote(i18n.t('A data group needs a name.'))}", status_code=303
        )
    db.add(DataGroup(name=name, owner_id=user.id))
    db.commit()
    return RedirectResponse(
        f"/settings?saved={quote(i18n.t('Data group created.'))}", status_code=303
    )


@router.post("/data-groups/{group_id}/delete")
async def delete_personal_group(db: Db, user: RequiredUser, group_id: str) -> Response:
    """Remove one of this person's own groups -- only once nothing is in it."""
    from lembas.services import data_groups

    refused = _refuse_without_manage(db, user)
    if refused is not None:
        return refused
    group = data_groups.get(db, group_id)
    if group is None or group.owner_id != user.id:
        return RedirectResponse(
            f"/settings?error={quote(i18n.t('No such data group.'))}", status_code=303
        )
    try:
        data_groups.delete(db, group)
    except ValueError as exc:
        return RedirectResponse(f"/settings?error={quote(str(exc))}", status_code=303)
    return RedirectResponse(
        f"/settings?saved={quote(i18n.t('Data group deleted.'))}", status_code=303
    )


# --- Personalization ---------------------------------------------------------
@router.post("/personalization")
async def set_personalization(
    db: Db,
    user: RequiredUser,
    instructions: str = Form(""),
    personality: str = Form(""),
    custom: str = Form(""),
) -> Response:
    """This person's custom instructions and personality, for every model."""
    from lembas.services import personalization

    if not personalization.enabled(db):
        return RedirectResponse(
            f"/settings?error={quote(i18n.t('Personalization is off here.'))}", status_code=303
        )
    user.settings_json = {
        **(user.settings_json or {}),
        "personalization": personalization.cleaned(instructions, personality, custom),
    }
    db.commit()
    return RedirectResponse(
        f"/settings?saved={quote(i18n.t('Personalization saved.'))}", status_code=303
    )
