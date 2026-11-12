"""What the composer's `/` and `@` menus offer, and what choosing from `@` does.

One implementation behind two doors. The page asks through the cookie routes
(`api/devices.py`, `api/files.py`), which answer with HTML; LLeMbas Desktop
asks through `/api/v2` (`api/v2/menus.py`), which answers with JSON. Both call
here, so a file one door would not offer is not offered by the other, and a
device that cannot answer is the same silence through both.

Nothing in here checks who is asking for what *route*: the doors do that (the
session cookie or the token's scope, and `files.upload`). What is checked here
is what belongs to the data: whose device, whose chat, whose attachment, which
data group, and what the chat may read in the tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Attachment, Chat, User
from lembas.services import data_groups, settings_store
from lembas.services import files as files_service
from lembas.web import i18n

# How many of each kind `@` offers: this runs per keystroke.
ATTACHMENTS_OFFERED = 5
TREE_FILES_OFFERED = 6
DEVICE_FILES_OFFERED = 50


class NotAvailable(Exception):
    """What was pointed at cannot be attached by this person, here. `what` is
    the kind of thing, for the sentence the door says."""

    def __init__(self, what: str, message: str = "") -> None:
        super().__init__(message or what)
        self.what = what
        self.message = message


# --- A linked device -----------------------------------------------------------------
def owned_device(db: DBSession, user: User, token_id: str):
    """One of this person's device tokens that may run agent chats now, or 404."""
    from lembas.db.models import TOKEN_DEVICE, ApiToken
    from lembas.services import device_link

    token = db.get(ApiToken, token_id)
    if (
        token is None
        or token.user_id != user.id
        or token.kind != TOKEN_DEVICE
        or device_link.refusal(db, user, token.id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such device.")
    return token


def device_where(db: DBSession, user: User, token_id: str, chat_id: str, cwd: str) -> dict:
    """What a device question is about: the chat's session when it has
    one -- the device knows its root -- else the chat's directory, else the one
    the new-chat screen has chosen."""
    if chat_id:
        chat = db.get(Chat, chat_id)
        if chat is None or chat.user_id != user.id or chat.device_token_id != token_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That chat no longer exists.")
        if chat.device_session_id:
            return {"sessionId": chat.device_session_id}
        cwd = chat.project_dir or cwd
    return {"cwd": cwd} if cwd.strip() else {}


@dataclass
class DeviceFiles:
    files: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
    device: str = ""


async def device_files(
    db: DBSession, user: User, token_id: str, *, q: str = "", chat_id: str = "", cwd: str = ""
) -> DeviceFiles:
    """What `@` offers from a linked device (capability `files`): the
    device's own file index, ranked as its terminal's `@` picker ranks it. A
    chosen file goes in as `@path` text -- the device reads it when it gets the
    prompt, so nothing is copied here. Empty, saying nothing, for a device that
    cannot answer; the composer still offers the rest."""
    from lembas.services import device_link

    owned_device(db, user, token_id)
    where = device_where(db, user, token_id, chat_id, cwd)
    link = device_link.link_for(token_id)
    out = DeviceFiles(device=device_link.short_name(link) if link else "")
    if link is None or not link.can("files") or not where:
        return out
    try:
        got = await link.request(
            "_lembas/files/search", {**where, "query": q[:200], "limit": 20}, timeout=5
        )
    except device_link.DeviceError as exc:
        out.error = str(exc)
        return out
    except (device_link.LinkClosed, TimeoutError):
        out.error = i18n.t("The device did not answer.")
        return out
    for entry in (got or {}).get("files") or []:
        path = str((entry or {}).get("path") or "") if isinstance(entry, dict) else ""
        if path and "\n" not in path and len(path) <= 1000:
            kind = "dir" if entry.get("kind") == "dir" else "file"
            out.files.append(
                {"path": path, "kind": kind, "name": path.rstrip("/").rsplit("/", 1)[-1] or path}
            )
    out.files = out.files[:DEVICE_FILES_OFFERED]
    return out


async def device_commands(
    db: DBSession, user: User, token_id: str, *, chat_id: str = "", cwd: str = ""
) -> tuple[list[dict[str, Any]], str]:
    """The `/` commands a linked device offers here (capability
    `commands`), and the device's name: the set its session last announced, or,
    for a composer with a directory and no session yet, the device's answer to
    `_lembas/commands`. Only the commands that make sense without its terminal
    ever come."""
    from lembas.services import device_link

    owned_device(db, user, token_id)
    where = device_where(db, user, token_id, chat_id, cwd)
    link = device_link.link_for(token_id)
    commands: list[dict[str, Any]] = []
    if link is not None and link.can("commands") and where:
        session = where.get("sessionId", "")
        if session and session in link.commands:
            commands = link.commands[session]
        else:
            try:
                got = await link.request("_lembas/commands", where, timeout=5)
                commands = device_link.clean_commands((got or {}).get("commands"))
            except (device_link.DeviceError, device_link.LinkClosed, TimeoutError):
                commands = []
            if session:
                link.commands[session] = commands
    return commands, (device_link.short_name(link) if link else "")


# --- `@` on the server ---------------------------------------------------------------
@dataclass
class Mentions:
    """What `@` offers from the server for what has been typed after it."""

    attachments: list[Attachment] = field(default_factory=list)
    tree_files: list[Any] = field(default_factory=list)
    # The URL typed, when what was typed is one: a page to read.
    website: str = ""


def tree_files(db: DBSession, user: User, chat_id: str, needle: str) -> list:
    """Files in the tree whose name has `needle` in it, that the chat can read
    -- or, on a new chat, that the person can. Only the path is offered: `@`
    puts the path in the message and the model reads the file through its own
    `read`, under the chat's access. Bounded, because this runs per keystroke."""
    from lembas.db.models import KIND_FILE, Node
    from lembas.services import fs

    chat = db.get(Chat, chat_id) if chat_id else None
    if chat is not None and (chat.user_id != user.id or chat.kind == "agent"):
        return []
    view = fs.view(db, user, chat)
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.scalars(
        select(Node)
        .where(
            Node.org_id == user.org_id,
            Node.kind == KIND_FILE,
            Node.trashed_at.is_(None),
            Node.name.ilike(f"%{escaped}%", escape="\\"),
        )
        .order_by(Node.updated_at.desc())
        .limit(40)
    )
    found = []
    for node in rows:
        if view.resolver.visible(node.path):
            found.append(node)
        if len(found) >= TREE_FILES_OFFERED:
            break
    return found


def mentions(db: DBSession, user: User, *, q: str = "", chat_id: str = "") -> Mentions:
    """What `@` offers from here: this chat's attachments, files in the tree,
    and a URL.

    A device chat's project files are not among them: the composer asks the
    linked device for those beside this (`device_files`). This is a
    keystroke-latency path and it must never wait on a machine.
    """
    needle = q.strip().lower()
    # A URL typed after `@` is a page to read, not a name to look up. The
    # fetcher, its SSRF guard and its HTML-to-text already live behind
    # `attach_url`; this only offers it.
    website = q.strip() if needle.startswith(("http://", "https://")) else ""

    found: list[Attachment] = []
    if chat_id and needle:
        rows = db.scalars(
            select(Attachment)
            .where(
                Attachment.user_id == user.id,
                Attachment.chat_id == chat_id,
                Attachment.message_id.is_not(None),
            )
            .order_by(Attachment.created_at.desc())
            .limit(20)
        )
        found = [a for a in rows if needle in a.filename.lower()][:ATTACHMENTS_OFFERED]
    return Mentions(
        attachments=found,
        tree_files=tree_files(db, user, chat_id, needle) if needle and not website else [],
        website=website,
    )


# --- Choosing from `@` ----------------------------------------------------------------
def attach_copy(
    db: DBSession, user: User, attachment_id: str, *, chat_id: str = "", model_id: str = ""
) -> Attachment:
    """Point at something already in a conversation, without uploading again.

    Copied rather than referenced, like everything else here -- an attachment
    belongs to the message it was sent with, and two messages sharing one row
    would make deleting either of them a question rather than an answer.
    """
    original = db.get(Attachment, attachment_id)
    if original is None or original.user_id != user.id:
        raise NotAvailable("attachment")
    # Another chat's attachment is that chat's data, in that chat's group.
    source = db.get(Chat, original.chat_id) if original.chat_id else None
    if source is not None and data_groups.for_chat(db, source) != data_groups.for_composer(
        db, user, chat_id, model_id
    ):
        raise NotAvailable("attachment")
    return files_service.copy_attachment(
        db, user_id=user.id, chat_id=chat_id or None, attachment=original
    )


def attach_tree_file(db: DBSession, user: User, path: str, *, chat_id: str = "") -> Attachment:
    """A file from the tree, attached as a copy of what it holds now.

    Read through the chat's own view -- or the person's, before the chat
    exists -- so what cannot be read there cannot be attached there either,
    and worded the same whether the file is missing or out of reach. An agent
    chat has no view of the server's tree: its files are the device's.
    """
    from lembas.services import fs

    chat = db.get(Chat, chat_id) if chat_id else None
    if chat is not None and (chat.user_id != user.id or chat.kind == "agent"):
        raise NotAvailable("file")
    view = fs.view(db, user, chat)
    try:
        node = fs.stat(view, path)
        if node is None or node.kind != "file":
            raise NotAvailable("file")
        if int(node.size or 0) > files_service.limits().max_upload_bytes:
            raise NotAvailable("file", i18n.t("That file is too large to attach."))
        payload = fs.read(view, path)
    except fs.FsError as exc:
        raise NotAvailable("file") from exc
    try:
        return files_service.store(
            db,
            user_id=user.id,
            chat_id=chat_id or None,
            payload=payload,
            filename=node.name,
            source_path=node.path,
        )
    except files_service.FileError as exc:
        raise NotAvailable("file", str(exc)) from exc


async def attach_url(db: DBSession, user: User, url: str, *, chat_id: str = "") -> Attachment:
    """Fetch a web page and attach its text.

    The page is reduced to text here and stored, rather than being fetched again
    when the message is sent: the same rule as PDF extraction. A reply must not
    change because a page was edited between composing and sending. Raises the
    fetcher's own `FetchError`, whose message is already the reader's.
    """
    from lembas.services.fetch import fetch

    config = settings_store.search(db)
    page = await fetch(url, allow_private=bool(config.get("allow_private_fetch")))
    return files_service.store_text(
        db,
        user_id=user.id,
        chat_id=chat_id or None,
        filename=f"{page.title[:120] or 'page'}.txt",
        text=page.text,
        truncated=page.truncated,
        source_note=page.url,
    )


__all__ = [
    "DeviceFiles",
    "Mentions",
    "NotAvailable",
    "attach_copy",
    "attach_tree_file",
    "attach_url",
    "device_commands",
    "device_files",
    "device_where",
    "mentions",
    "owned_device",
    "tree_files",
]
