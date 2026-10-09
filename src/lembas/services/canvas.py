"""What is open in the canvas panel, and where its contents come from.

Six sources behind one shape. A tab key is `"<source>:<ref>"` and every source
answers the same two questions -- load this, and save that -- through one table.
A table rather than six branches for the reason `tool_labels.py` and
`sharing.RESOURCE_TYPES` are tables: six independently written permission checks
is how one of them ends up written slightly differently, and the way *that*
failure shows up is somebody editing somebody else's note.

The panel is a person's own hands. A save on an `agent:` tab therefore does not
go through `agent/policy.py`, exactly as the terminal panel and the directory
browser do not: whoever owns the credential could write the file with `scp`.
This is the first of those exceptions that *writes*, which is worth saying out
loud -- Manual mode's "everything is shown to you before it happens" is a promise
about the model, not about the interface.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from datetime import UTC

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Attachment, Chat, User
from lembas.security import permissions
from lembas.services import scratch as scratch_service
from lembas.services import sharing
from lembas.services.agent.base import Conflict, revision_of
from lembas.services.library import documents as documents_service
from lembas.services.library import notes as notes_service
from lembas.services.library import skills as skills_service
from lembas.web.i18n import t

# How many tabs a chat keeps. A model in a long reply reads forty files, and an
# unbounded strip is a strip nobody can read -- and it would live on the chat
# row forever. Past this the oldest tab that is not in front is dropped.
MAX_TABS = 12

SOURCE_NOTE = "note"
SOURCE_SKILL = "skill"
SOURCE_DOC = "doc"
SOURCE_FILE = "file"
SOURCE_SCRATCH = "scratch"


class Refused(Exception):
    """This person may not have this, or it is not there any more.

    One exception for every source, because the panel answers all of them the
    same way: a fragment saying so, in the tab, rather than an error page
    swapped into the middle of a chat.
    """


@dataclass(frozen=True)
class Doc:
    """One open file, whatever it actually is underneath."""

    key: str
    title: str
    subtitle: str = ""
    text: str = ""
    # An opaque token saying which version this was read at, round-tripped
    # through a hidden field so a save can refuse a file that moved underneath.
    revision: str = ""
    writable: bool = False
    # A filename or close enough, for choosing a lexer.
    language: str = ""
    markdown: bool = False
    truncated: bool = False
    binary: bool = False

    @property
    def editable(self) -> bool:
        """Whether the box is offered at all.

        Not the same as `writable`. Saving back the first 256KB of a larger file
        is how the rest of it is deleted, and a binary file has nothing safe to
        put in a textarea -- both open read-only however the permissions read.
        """
        return self.writable and not self.truncated and not self.binary


def path_key(project_dir: str, path: str) -> str:
    """One name for one file, so `./a.py` and `a.py` open the same tab.

    The same normalisation `agent/tools.py:_path_key` applies to the read-path
    set, and lifted here so the two cannot disagree: a tab a model opened and a
    tab a person opened have to be one tab, or the panel shows the same file
    twice and only one of them is the one being saved.
    """
    if not posixpath.isabs(path) and project_dir:
        path = posixpath.join(project_dir, path)
    return posixpath.normpath(path)


def split(key: str) -> tuple[str, str]:
    """`"agent:/srv/a:b.py"` -> `("agent", "/srv/a:b.py")`.

    `partition`, not `split`: a path may contain a colon, and a key that lost
    half its path would silently open the wrong file.
    """
    source, _, ref = (key or "").partition(":")
    return source, ref


# --- The tab strip ---------------------------------------------------------------
def tabs_of(chat: Chat) -> list[dict]:
    return list((chat.canvas_json or {}).get("tabs") or [])


def active_of(chat: Chat) -> str:
    return str((chat.canvas_json or {}).get("active") or "")


def open_tab(state: dict, tab: dict, *, activate: bool = True) -> dict:
    """Add a tab, and optionally bring it to the front. Mutates `state`.

    Mutating rather than returning a copy because the generation loop folds
    several of these into one snapshot within a round: two `file_read` calls
    that each read the state and wrote it back would leave only the second.
    That is the lost update `plan_update` documents, in a different place.

    `activate=False` is what a *model* opening a tab does, and it is the whole
    of how this feature avoids being infuriating. An agent reads forty files in
    a long reply; if each one took the panel, somebody reading the third would
    be dragged through the other thirty-seven, and anybody halfway through an
    edit would lose it. So the model fills the strip and the person decides
    what is in front. A tab they open themselves activates, because opening
    something and not being shown it is the opposite failure.
    """
    key = str(tab.get("key") or "")
    if not key:
        return state

    tabs = [t for t in (state.get("tabs") or []) if t.get("key") != key]
    tabs.append({
        "key": key,
        "title": str(tab.get("title") or key)[:120],
        "source": str(tab.get("source") or split(key)[0]),
    })

    # Evict from the front, and never the tab in front or the one just opened.
    # A model reading its way through a project must not close the file
    # somebody is looking at.
    keep = {key, str(state.get("active") or "")}
    while len(tabs) > MAX_TABS:
        victim = next((t for t in tabs if t["key"] not in keep), None)
        if victim is None:
            break
        tabs.remove(victim)

    state["tabs"] = tabs
    if activate or not state.get("active"):
        # Not activating an empty panel would leave tabs with nothing in front,
        # which reads as a panel that failed to load.
        state["active"] = key
    return state


def close_tab(state: dict, key: str) -> dict:
    tabs = [t for t in (state.get("tabs") or []) if t.get("key") != key]
    state["tabs"] = tabs
    if state.get("active") == key:
        state["active"] = tabs[-1]["key"] if tabs else ""
    return state


def merge(stored: dict | None, live: dict | None) -> dict:
    """Fold a reply's tabs into whatever the row says now.

    A union rather than an overwrite. `_persist` is the single writer, and the
    snapshot it holds was taken when the reply began -- so overwriting would
    drop a tab the person opened by hand while the reply was running.
    """
    state = {
        "tabs": list((stored or {}).get("tabs") or []),
        "active": (stored or {}).get("active") or "",
    }
    for tab in (live or {}).get("tabs") or []:
        # Never activating: what the row says is in front is what the person
        # last chose, and a reply that finishes ten minutes later must not move
        # it. The reply's own `active` is deliberately not consulted.
        open_tab(state, tab, activate=False)
    return state


# --- Which sources this chat may reach ---------------------------------------------
# --- Loading ------------------------------------------------------------------------
async def _load_note(db: DBSession, user: User, chat: Chat, ref: str) -> Doc:
    _needs_library(db, user)
    note = notes_service.get(db, ref, user)
    if note is None:
        raise Refused(t("That note is not there any more."))
    return Doc(
        key=f"{SOURCE_NOTE}:{note.id}",
        title=note.title or "Note",
        subtitle="Note",
        text=note.body or "",
        revision=_stamp(note, note.body or ""),
        writable=sharing.can_write(note, user),
        language="note.md",
        markdown=True,
    )


async def _load_skill(db: DBSession, user: User, chat: Chat, ref: str) -> Doc:
    _needs_library(db, user)
    skill = skills_service.get(db, ref, user)
    if skill is None:
        raise Refused(t("That skill is not there any more."))
    return Doc(
        key=f"{SOURCE_SKILL}:{skill.id}",
        title=skill.name or "Skill",
        subtitle="Skill",
        text=skill.body or "",
        revision=_stamp(skill, skill.body or ""),
        writable=sharing.can_write(skill, user),
        language="skill.md",
        markdown=True,
    )


async def _load_doc(db: DBSession, user: User, chat: Chat, ref: str) -> Doc:
    _needs_library(db, user)
    document = documents_service.get(db, ref, user)
    if document is None:
        raise Refused(t("That document is not there any more."))
    return Doc(
        key=f"{SOURCE_DOC}:{document.id}",
        title=document.title or document.filename or "Document",
        subtitle="Knowledge document",
        text=document.extracted_text or document.extraction_error or "",
        revision=_stamp(document, document.extracted_text or ""),
        writable=documents_service.can_write(document, user),
        language=document.filename or "",
        markdown=(document.filename or "").lower().endswith((".md", ".markdown")),
        truncated=bool(document.truncated),
    )


async def _load_file(db: DBSession, user: User, chat: Chat, ref: str) -> Doc:
    attachment = db.get(Attachment, ref)
    if attachment is None or attachment.user_id != user.id:
        raise Refused(t("That attachment is not there any more."))
    # Belonging to this conversation, so a canvas cannot browse another one's
    # files by id. `chat_id` covers one still in the composer; the message check
    # covers one that has been sent.
    if attachment.chat_id != chat.id:
        raise Refused(t("That attachment belongs to another chat."))
    return Doc(
        key=f"{SOURCE_FILE}:{attachment.id}",
        title=attachment.filename or "Attachment",
        subtitle=attachment.source_path or "Attachment",
        text=attachment.extracted_text or attachment.extraction_error or "",
        # Read-only, and not for want of a write path: `DELETE /api/files/{id}`
        # already refuses once the attachment has been sent, because it would
        # rewrite a message somebody already read. Editing is the same act with
        # a quieter failure.
        writable=False,
        language=attachment.filename or "",
        markdown=(attachment.filename or "").lower().endswith((".md", ".markdown")),
        truncated=bool(attachment.truncated),
    )


async def _load_scratch(db: DBSession, user: User, chat: Chat, ref: str) -> Doc:
    if ref != chat.id:
        raise Refused(t("That scratch document belongs to another chat."))
    doc = scratch_service.for_chat(db, chat)
    return Doc(
        key=f"{SOURCE_SCRATCH}:{chat.id}",
        title=doc.title or "Scratch",
        subtitle="This chat's scratch document",
        text=doc.body or "",
        revision=_stamp(doc, doc.body or ""),
        writable=True,
        language="scratch.md",
        markdown=True,
    )


# --- Saving --------------------------------------------------------------------------
async def _save_note(db: DBSession, user: User, chat: Chat, ref: str, text: str, revision: str):
    note = notes_service.get(db, ref, user)
    if note is None:
        raise Refused(t("That note is not there any more."))
    if not sharing.can_write(note, user):
        raise Refused(t("That note is not yours to change."))
    _check_stamp(note, note.body or "", revision)
    notes_service.update(db, note, body=text)
    return await _load_note(db, user, chat, ref)


async def _save_skill(db: DBSession, user: User, chat: Chat, ref: str, text: str, revision: str):
    skill = skills_service.get(db, ref, user)
    if skill is None:
        raise Refused(t("That skill is not there any more."))
    if not sharing.can_write(skill, user):
        raise Refused(t("That skill is not yours to change."))
    _check_stamp(skill, skill.body or "", revision)
    # Snapshots into a SkillRevision first, which is why a skill needs no
    # conflict story beyond the token: a clobber is recoverable.
    skills_service.update(db, skill, body=text, note="Edited in the canvas")
    return await _load_skill(db, user, chat, ref)


async def _save_doc(db: DBSession, user: User, chat: Chat, ref: str, text: str, revision: str):
    document = documents_service.get(db, ref, user)
    if document is None:
        raise Refused(t("That document is not there any more."))
    if not documents_service.can_write(document, user):
        raise Refused(t("That document is not yours to change."))
    _check_stamp(document, document.extracted_text or "", revision)
    documents_service.set_text(db, document, text)
    return await _load_doc(db, user, chat, ref)


async def _save_scratch(db: DBSession, user: User, chat: Chat, ref: str, text: str, revision: str):
    if ref != chat.id:
        raise Refused(t("That scratch document belongs to another chat."))
    doc = scratch_service.for_chat(db, chat)
    _check_stamp(doc, doc.body or "", revision)
    scratch_service.update(db, doc, body=text)
    return await _load_scratch(db, user, chat, ref)


# --- One table -------------------------------------------------------------------------
_SOURCES: dict[str, tuple] = {
    SOURCE_NOTE: (_load_note, _save_note),
    SOURCE_SKILL: (_load_skill, _save_skill),
    SOURCE_DOC: (_load_doc, _save_doc),
    SOURCE_FILE: (_load_file, None),
    SOURCE_SCRATCH: (_load_scratch, _save_scratch),
}


async def load(db: DBSession, user: User, chat: Chat, key: str) -> Doc:
    source, ref = split(key)
    entry = _SOURCES.get(source)
    if entry is None or not ref:
        raise Refused(t("There is nothing to open here."))
    return await entry[0](db, user, chat, ref)


async def save(
    db: DBSession, user: User, chat: Chat, key: str, text: str, revision: str = ""
) -> Doc:
    source, ref = split(key)
    entry = _SOURCES.get(source)
    if entry is None or not ref:
        raise Refused(t("There is nothing to save here."))
    saver = entry[1]
    if saver is None:
        raise Refused(t("This one can only be read."))
    return await saver(db, user, chat, ref, text, revision)


# --- Small shared pieces ------------------------------------------------------------------
def _needs_library(db: DBSession, user: User) -> None:
    if not permissions.has(db, user, "library.use"):
        raise Refused(t("You do not have access to the library."))


def _stamp(row, text: str) -> str:
    """A revision token for a database row.

    `updated_at` alone would not move for two saves inside one clock tick, so
    the length rides along -- the same pairing the file token uses, and for the
    same reason. The text is passed in rather than guessed at: a note keeps it
    in `body` and a document in `extracted_text`, and a getattr chain that
    silently found neither would hand every row the same token.
    """
    when = getattr(row, "updated_at", None)
    if when is not None and when.tzinfo is None:
        # SQLite does not store the offset, so a row loaded from disk comes back
        # naive while one still in the session's identity map keeps the tzinfo
        # it was created with -- and `.timestamp()` reads a naive value as local
        # time. Without this the same row yields two different tokens depending
        # on where it was loaded, and every save outside UTC would report a
        # conflict that is not there. The same normalisation
        # `compaction.moment` makes, for the same reason.
        when = when.replace(tzinfo=UTC)
    return revision_of(int(when.timestamp()) if when else 0, len(text or ""))


def _check_stamp(row, text: str, revision: str) -> None:
    """Refuse a save whose token no longer matches. An empty token overwrites.

    Empty is what Overwrite on the conflict card sends: somebody has been shown
    both versions and chosen. Never save silently over a change; never discard
    silently either.
    """
    if revision and _stamp(row, text) != revision:
        raise Conflict(_stamp(row, text))
