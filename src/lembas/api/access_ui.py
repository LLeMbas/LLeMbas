"""The tick-tree access editor: a chat's own grants, a data group's preset and floor.

One component (`access/_ticks.html`, `js/ticks.js`) for all three, because
they are one rule (`security/access.py`): a path is granted at the level of its
nearest ticked ancestor-or-self, and **an unticked directory above a ticked
path is a passage** -- it is not listed and cannot be read, and the ticked path
below it is reached only by its full path. The editor draws a passage as such,
so what a session can see is never a surprise.

* `/chat/{id}/access` -- a chat's grants. Saving makes them the chat's own
  (`grants_customised`); *Use the data group's preset* goes back.
* `/data-groups/{id}/access?mode=preset|floor` -- an administrator, or the
  owner of a personal group. A preset is what a new chat starts from; a floor is
  a ceiling no chat in the group can go above (no floor rule: no cap).

The tree drawn is what the editor's own person can see -- the rules may name
other paths (typed in), and those are kept and shown too.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import delete, select

from lembas.api._redirect import back
from lembas.api.deps import Db, RequiredUser, is_htmx
from lembas.api.pages import sidebar_context
from lembas.db.models import (
    DEFAULT_GROUP,
    LEVEL_COMMIT,
    LEVELS,
    RULE_FLOOR,
    RULE_PRESET,
    Chat,
    DataGroup,
    DataGroupRule,
    SessionGrant,
)
from lembas.security import access
from lembas.services import file_ui, fs
from lembas.web.i18n import N_, t
from lembas.web.templating import render

router = APIRouter(tags=["access"])

LEVEL_LABELS = {
    "none": N_("No access"),
    "ro": N_("Read only"),
    "stage": N_("Read and write"),
    "commit": N_("Read, write and commit"),
}


def _chat(db, user, chat_id: str) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That chat no longer exists."))
    return chat


def _group(db, user, group_id: str) -> DataGroup:
    group = db.get(DataGroup, group_id)
    if group is None or not (user.is_admin or group.owner_id == user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That data group no longer exists."))
    return group


def chat_rules(db, chat: Chat) -> dict[str, str]:
    """The grants a chat has now: its own once customised, else its group's preset."""
    if chat.grants_customised:
        return {
            access.normalise(row.path): row.level
            for row in db.scalars(select(SessionGrant).where(SessionGrant.chat_id == chat.id))
        }
    group = chat.data_group_id or DEFAULT_GROUP
    return access.rules_of(db, group, RULE_PRESET) or {"/": LEVEL_COMMIT}


def _editor(request, db, user, *, rules, floor, action, title, lede, scope, extra=None):
    v = fs.view(db, user)
    rows = file_ui.tick_rows(v, rules)
    context = {
        "rules": rules,
        "root_rule": rules.get("/", ""),
        "rows": rows,
        "floor": floor,
        "levels": LEVELS,
        "level_labels": LEVEL_LABELS,
        "action": action,
        "title": title,
        "lede": lede,
        "scope": scope,
        "saved": request.query_params.get("saved", ""),
        "error": request.query_params.get("error", ""),
        **(extra or {}),
    }
    return context


# --- A chat --------------------------------------------------------------------------
@router.get("/chat/{chat_id}/access")
async def chat_access(request: Request, db: Db, user: RequiredUser, chat_id: str):
    chat = _chat(db, user, chat_id)
    group = chat.data_group_id or DEFAULT_GROUP
    context = _editor(
        request,
        db,
        user,
        rules=chat_rules(db, chat),
        floor=access.rules_of(db, group, RULE_FLOOR),
        action=f"/chat/{chat.id}/access",
        title=t("What this chat may reach"),
        lede=t(
            "Tick what the chat may use, and how. Everything below a ticked folder comes "
            "with it unless you tick it differently. The chat never gets more than you "
            "have yourself, or more than its data group allows."
        ),
        scope=f"chat:{chat.id}",
        extra={"chat": chat, "customised": chat.grants_customised},
    )
    return render(request, "access/page.html", {**context, **sidebar_context(db, user)})


@router.post("/chat/{chat_id}/access")
async def save_chat_access(request: Request, db: Db, user: RequiredUser, chat_id: str):
    chat = _chat(db, user, chat_id)
    form = await request.form()
    db.execute(delete(SessionGrant).where(SessionGrant.chat_id == chat.id))
    if form.get("reset"):
        chat.grants_customised = False
        db.commit()
        return back(f"/chat/{chat.id}/access", t("Back to the data group's preset."))
    rules = file_ui.parse_rules(form, levels=LEVELS)
    for path, level in rules.items():
        db.add(SessionGrant(chat_id=chat.id, path=path, level=level))
    chat.grants_customised = True
    db.commit()
    return back(f"/chat/{chat.id}/access", t("Saved."))


# --- A data group ------------------------------------------------------------------
def _mode(raw: str) -> str:
    return RULE_FLOOR if raw == RULE_FLOOR else RULE_PRESET


@router.get("/data-groups/{group_id}/access")
async def group_access(
    request: Request, db: Db, user: RequiredUser, group_id: str, mode: str = RULE_PRESET
):
    group = _group(db, user, group_id)
    mode = _mode(mode)
    rules = access.rules_of(db, group.id, mode)
    if mode == RULE_PRESET:
        title = t("What a new chat in %(name)s starts with", name=group.name)
        lede = t(
            "A new chat in this data group may reach what is ticked here, until somebody "
            "changes it for their chat. Nothing ticked means everything its person has."
        )
    else:
        title = t("The most any chat in %(name)s may reach", name=group.name)
        lede = t(
            "A ceiling: no chat in this data group goes above what is ticked here, whatever "
            "it is granted. Nothing ticked means no ceiling."
        )
    context = _editor(
        request,
        db,
        user,
        rules=rules,
        floor={},
        action=f"/data-groups/{group.id}/access?mode={mode}",
        title=title,
        lede=lede,
        scope=f"group:{group.id}:{mode}",
        extra={"group": group, "mode": mode},
    )
    return render(request, "access/page.html", {**context, **sidebar_context(db, user)})


@router.post("/data-groups/{group_id}/access")
async def save_group_access(
    request: Request, db: Db, user: RequiredUser, group_id: str, mode: str = RULE_PRESET
):
    group = _group(db, user, group_id)
    mode = _mode(mode)
    form = await request.form()
    rules = file_ui.parse_rules(form, levels=LEVELS)
    db.execute(
        delete(DataGroupRule).where(
            DataGroupRule.data_group_id == group.id, DataGroupRule.mode == mode
        )
    )
    for path, level in rules.items():
        db.add(DataGroupRule(data_group_id=group.id, path=path, level=level, mode=mode))
    db.commit()
    return back(f"/data-groups/{group.id}/access?mode={mode}", t("Saved."))


# --- Lazy branches -------------------------------------------------------------------
@router.get("/access/rows")
async def rows(
    request: Request, db: Db, user: RequiredUser, scope: str, path: str = "/", depth: int = 0
) -> Response:
    """One directory's rows, when a branch the page did not open is opened."""
    kind, _, rest = scope.partition(":")
    if kind == "chat":
        rules = chat_rules(db, _chat(db, user, rest))
    elif kind == "group":
        group_id, _, mode = rest.partition(":")
        rules = access.rules_of(db, _group(db, user, group_id).id, _mode(mode))
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        root = access.normalise(path)
    except access.BadPath as problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST) from problem
    v = fs.view(db, user)
    found = [r for r in file_ui.tick_rows(v, rules, root=root, depth=max(0, depth))
             if access.is_within(r.path, root) and r.path != root]
    return render(
        request,
        "access/_rows.html",
        {"rows": found, "levels": LEVELS, "level_labels": LEVEL_LABELS, "floor": {},
         "scope": scope, "htmx": is_htmx(request)},
    )


__all__ = ["LEVEL_LABELS", "chat_rules", "router"]
