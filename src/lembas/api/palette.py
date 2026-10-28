"""The command palette (Ctrl+K, `js/palette.js`): jump to a chat, a file, a model
or a setting.

One JSON answer per keystroke (debounced in the browser), each list short and
each entry only something this person may open: their own chats, files the
index finds and their access lets them read (`services/index`), the models
they may start a chat with (`permissions.models_visible_to`), and the settings
pages they have -- the administration's only for an administrator.

Titles are a chat's title, a file's name, a model's label: text people and
models chose. The script puts them in with `textContent`, never as markup.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import select

from lembas.api.deps import Db, RequiredUser
from lembas.db.models import KINDS, Chat
from lembas.security import permissions
from lembas.web.i18n import N_, t

router = APIRouter(tags=["palette"])

EACH = 6

# Settings a person can always reach, then the administration's. (label, url)
SETTINGS = (
    (N_("Account"), "/settings?tab=account"),
    (N_("Models"), "/settings?tab=models"),
    (N_("Appearance"), "/settings?tab=appearance"),
    (N_("Memory"), "/settings?tab=memory"),
    (N_("Devices"), "/settings?tab=devices"),
    (N_("Data"), "/settings?tab=data"),
    (N_("Security"), "/settings?tab=security"),
    (N_("Files"), "/files"),
    (N_("Search"), "/search"),
)
ADMIN = (
    (N_("General"), "/admin/general"),
    (N_("Connections"), "/admin/connections"),
    (N_("Models"), "/admin/models"),
    (N_("Data groups"), "/admin/data-groups"),
    (N_("Extraction"), "/admin/extraction"),
    (N_("Tools"), "/admin/tools"),
    (N_("Users"), "/admin/users"),
    (N_("Invites"), "/admin/invites"),
    (N_("Updates"), "/admin/updates"),
    (N_("Audit log"), "/admin/audit"),
)


def _match(needle: str, *texts: str) -> bool:
    folded = needle.casefold()
    return not folded or any(folded in (text or "").casefold() for text in texts)


@router.get("/api/palette")
async def palette(db: Db, user: RequiredUser, q: str = "") -> JSONResponse:
    needle = " ".join(q.split())[:200]
    out: list[dict] = []

    query = select(Chat).where(
        Chat.user_id == user.id, Chat.temporary.is_(False), Chat.kind.in_(KINDS)
    )
    if needle:
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        query = query.where(Chat.title.ilike(pattern, escape="\\"))
    for chat in db.scalars(query.order_by(Chat.updated_at.desc()).limit(EACH)):
        out.append({"kind": "chat", "group": t("Chats"), "title": chat.title,
                    "url": f"/chat/{chat.id}"})

    if needle:
        try:
            from lembas.services import index

            hits = await index.search(db, user, None, needle, kinds=("file",), limit=EACH)
        except Exception:  # the index is an addition, never the palette's failure
            hits = []
        for hit in hits:
            out.append({"kind": "file", "group": t("Files"),
                        "title": hit.title or hit.path.rsplit("/", 1)[-1], "detail": hit.path,
                        "url": f"/files/view?path={quote(hit.path)}"})

    models = [
        m for m in permissions.models_visible_to(db, user) if _match(needle, m.label, m.model_id)
    ]
    for model in models[:EACH]:
        out.append({"kind": "model", "group": t("Models"), "title": model.label,
                    "detail": model.model_id,
                    "url": f"/chat?model={quote(model.model_id)}"})

    pages = list(SETTINGS) + (list(ADMIN) if user.is_admin else [])
    for label, url in pages:
        said = t(label)
        if _match(needle, said, label):
            out.append({"kind": "setting", "group": t("Settings") if not url.startswith("/admin")
                        else t("Administration"), "title": said, "url": url})
    return JSONResponse({"items": out[: EACH * 5]})


__all__ = ["router"]
