"""Folder management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.db.models import KINDS, Folder
from lembas.services.agent import policy as agent_policy
from lembas.web.i18n import t

# Every route here manages folders, so the guard belongs on the router.
router = APIRouter(
    prefix="/api/folders",
    tags=["folders"],
    dependencies=[Depends(require_permission("folder.manage"))],
)

MAX_DEPTH = 8


def _owned_folder(db: DBSession, folder_id: str, user_id: str) -> Folder:
    folder = db.get(Folder, folder_id)
    if folder is None or folder.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That folder no longer exists."))
    return folder


def _depth_of(db: DBSession, folder: Folder | None) -> int:
    depth = 0
    seen: set[str] = set()
    while folder is not None and folder.id not in seen:
        seen.add(folder.id)
        depth += 1
        folder = db.get(Folder, folder.parent_id) if folder.parent_id else None
    return depth


def _descendants(db: DBSession, folder: Folder) -> set[str]:
    """Every folder under this one, and this one. Bounded by MAX_DEPTH."""
    found = {folder.id}
    frontier = [folder.id]
    for _ in range(MAX_DEPTH + 1):
        if not frontier:
            break
        children = list(
            db.scalars(select(Folder).where(Folder.parent_id.in_(frontier)))
        )
        frontier = [c.id for c in children if c.id not in found]
        found.update(frontier)
    return found


def _subtree_height(db: DBSession, folder: Folder) -> int:
    """How many levels this folder's own subtree occupies, itself included.

    A move has to consider it: the constraint is on the *deepest leaf* after the
    move, not on the folder being dragged.
    """
    height = 1
    frontier = [folder.id]
    for _ in range(MAX_DEPTH + 1):
        children = list(
            db.scalars(select(Folder.id).where(Folder.parent_id.in_(frontier)))
        )
        if not children:
            break
        height += 1
        frontier = children
    return height


def candidate_parents(db: DBSession, user_id: str, folder: Folder) -> list[Folder]:
    """Folders this one could be moved into.

    Everything the person owns, minus the folder itself and its own subtree --
    which is the cycle guard in `update_folder` stated as a list rather than as
    a refusal. A picker that offers a move the route will reject is a control
    that looks like it works.

    Depth is checked at the route rather than filtered here: it depends on how
    tall *this* folder's subtree is, and a select that silently omitted a folder
    for that reason would be unexplainable from the screen.
    """
    blocked = _descendants(db, folder)
    return [
        candidate
        for candidate in db.scalars(
            select(Folder).where(Folder.user_id == user_id).order_by(Folder.name)
        )
        if candidate.id not in blocked
    ]


def _refresh_sidebar() -> Response:
    """Tell the browser to reload so the tree re-renders.

    The folder tree is recursive and a change can move any part of it, so
    re-rendering the whole sidebar server-side is both simpler and less
    error-prone than trying to patch individual nodes over the wire.
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["HX-Refresh"] = "true"
    return response


def _prompted(request: Request) -> str:
    """What somebody typed into an `hx-prompt` dialog, if anything.

    htmx sends it as a header rather than a field, because the element carrying
    the attribute may not be a form control at all. `ui.js` swaps the browser's
    own prompt for the themed one and hands the answer back through the same
    header, so this reads identically either way.
    """
    return (request.headers.get("HX-Prompt") or "").strip()


@router.post("")
async def create_folder(
    request: Request,
    db: Db,
    user: RequiredUser,
    name: str = Form(""),
    parent_id: str = Form(""),
) -> Response:
    name = name.strip() or _prompted(request)
    parent = _owned_folder(db, parent_id, user.id) if parent_id else None

    # A cap on nesting, so a runaway client cannot build a tree deep enough to
    # blow the recursion limit in the template.
    if parent is not None and _depth_of(db, parent) >= MAX_DEPTH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            t("Folders cannot be nested more than %(n)s deep.", n=MAX_DEPTH),
        )

    db.add(
        Folder(
            user_id=user.id,
            name=name[:200] or t("New folder"),
            parent_id=parent.id if parent else None,
        )
    )
    db.commit()
    return _refresh_sidebar()


# The settings a folder hands to chats started inside it, and how far each may
# run. A table rather than a run of `if` blocks so the save handler and the form
# cannot come to disagree about which fields exist -- the same reasoning the
# tool label table carries.
_SEEDS = {
    "description": 500,
    "system_prompt": 20_000,
    "model_id": 300,
    "ssh_profile_id": 32,
    "project_dir": 1000,
}


@router.patch("/{folder_id}")
async def update_folder(
    request: Request,
    db: Db,
    user: RequiredUser,
    folder_id: str,
) -> Response:
    """Rename, move, collapse, or set what this folder hands to its chats.

    Reads the raw form rather than declaring `Form(None)` parameters, because
    FastAPI cannot tell an empty field from an absent one -- a submitted `x=`
    arrives as None, so "clear this prompt" and "leave it alone" would be the
    same request. Key presence is the distinction, which is the rule
    `api/chats.py:update_chat` already follows and the reason every field here
    is clearable.
    """
    folder = _owned_folder(db, folder_id, user.id)
    form = await request.form()

    # A rename can arrive from a settings form or from an `hx-prompt` button on
    # the folder row; one route serves both. A blank name is ignored rather than
    # stored, since a folder nobody can see the name of is one nobody can find.
    name = str(form.get("name") or "").strip() or _prompted(request)
    if name:
        folder.name = name[:200]

    if "parent_id" in form:
        parent_id = str(form["parent_id"]).strip()
        new_parent = _owned_folder(db, parent_id, user.id) if parent_id else None
        # Reparenting a folder into its own subtree would detach that subtree
        # from the root and make it unreachable.
        cursor = new_parent
        while cursor is not None:
            if cursor.id == folder.id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    t("A folder cannot be moved inside itself."),
                )
            cursor = db.get(Folder, cursor.parent_id) if cursor.parent_id else None
        # And the depth cap, which `create_folder` has always applied and this
        # path never did -- moving a three-deep subtree under a six-deep folder
        # builds a tree nine deep, which is what MAX_DEPTH exists to keep out of
        # the recursive sidebar template. It went unnoticed because nothing in
        # the interface could submit `parent_id` at all until now.
        subtree = _subtree_height(db, folder)
        if new_parent is not None and _depth_of(db, new_parent) + subtree > MAX_DEPTH:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                t("Folders cannot be nested more than %(n)s deep.", n=MAX_DEPTH),
            )
        folder.parent_id = new_parent.id if new_parent else None

    if "collapsed" in form:
        folder.collapsed = str(form["collapsed"]).lower() in ("1", "true", "on", "yes")

    for field, limit in _SEEDS.items():
        if field in form:
            setattr(folder, field, str(form[field]).strip()[:limit])

    # Both are vocabularies rather than free text, and both accept "" for "no
    # opinion". Anything else is dropped rather than stored: a folder seeding a
    # kind that is not a kind would hand every chat started in it a value that
    # `_new_chat` then has to ignore anyway.
    if "kind" in form:
        wanted = str(form["kind"]).strip()
        folder.kind = wanted if wanted in KINDS else ""
    if "agent_mode" in form:
        wanted = str(form["agent_mode"]).strip()
        folder.agent_mode = wanted if wanted in agent_policy.MODES else ""

    db.commit()
    # One rule for every caller: reload. A rename or a move changes the tree,
    # and a save from the settings page comes back showing what was stored --
    # which is what somebody who pressed Save wants to see anyway.
    return _refresh_sidebar()


@router.delete("/{folder_id}")
async def delete_folder(db: Db, user: RequiredUser, folder_id: str) -> Response:
    """Delete a folder. Child folders go with it; chats do not.

    Chats fall back to the unfiled list (the FK is ON DELETE SET NULL), because
    losing a conversation to a mis-clicked folder delete is unforgivable.
    """
    folder = _owned_folder(db, folder_id, user.id)
    db.delete(folder)
    db.commit()
    return _refresh_sidebar()
