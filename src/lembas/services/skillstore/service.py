"""The store's one door: every read and every change, for every caller.

The MCP tools, a chat's skill tools, the web pages and `/api/v2` all come
through here, so the three things that must hold on every route are decided
in one place:

- **the policy** (`policy.decide_*`): a change applies, waits in the queue, or
  is refused -- `_submit` is the only function that commits a caller's change;
- **provenance**: every commit carries who asked (`Who.trailers`), and an
  approved one also who approved;
- **visibility**: a note of another data group does not exist for a caller
  (`catalog.note`, `catalog.notes`).

A change from the **remote** is the owner's own repository speaking: it is
taken as a person's edit, never queued, and its commits carry no trailers of
ours -- so history shows them as `remote`, with git's author as the name.
"""

from __future__ import annotations

import difflib
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    DEFAULT_GROUP,
    PROPOSAL_APPROVED,
    PROPOSAL_PENDING,
    PROPOSAL_REJECTED,
    PROPOSAL_STALE,
    StoreNote,
    StoreProposal,
    StoreSkill,
    User,
)
from lembas.services import crypto
from lembas.services import skill_format as fmt
from lembas.services.skillstore import catalog, doc, gitrun, pointers, policy, repo
from lembas.services.skillstore.policy import Who

log = logging.getLogger(__name__)

TARGET_SKILL = "skill"
TARGET_NOTE = "note"

APPLIED = "applied"
QUEUED = "queued"
REFUSED = "refused"

MAX_NOTE_CHARS = 60_000
MAX_REASON = 400
PENDING_PER_ACCOUNT = 50


class Refused(Exception):
    """A change the store does not take, in words the caller reads."""


@dataclass(frozen=True)
class Outcome:
    status: str
    message: str
    name: str = ""
    commit: str = ""
    proposal_id: str = ""


# --- Reading skills -----------------------------------------------------------------
def main_prose(db: DBSession) -> str:
    row = catalog.skill(db, repo.MAIN)
    return row.body if row is not None else doc.body_of(repo.MAIN_SKILL)


def main_text(db: DBSession) -> str:
    """The main skill as an agent reads it: the prose, then the generated list."""
    page = pointers.main_page(catalog.items(db))
    listed = pointers.render(page) or "(No skills yet.)"
    return (
        f"{main_prose(db)}\n\n## The skills here ({page.total})\n\n{listed}\n\n"
        "Read one with skill_outline(name), then skill_read(name, section)."
    )


def category_text(db: DBSession, category: str, subcategory: str = "") -> str:
    found = catalog.items(db)
    category = doc.slug(category or pointers.GENERAL)
    if subcategory:
        page = pointers.subcategory_page(found, category, doc.slug(subcategory))
        title = f"{category} / {doc.slug(subcategory)}"
        listed = pointers.render(page, level="subcategory", parent=category)
    else:
        page = pointers.category_page(found, category)
        title = category
        listed = pointers.render(page, level="subcategory", parent=category)
    if not page.total:
        names = ", ".join(name for name, _n in catalog.categories(db)) or "(none)"
        raise Refused(f'There is no category "{title}". Categories: {names}')
    return f"# {title} ({page.total} skills)\n\n{listed}"


def instructions(db: DBSession, who: Who | None = None) -> str:
    """The compact pointer an MCP client's instructions carry."""
    found = catalog.items(db)
    cats = catalog.categories(db)
    lines = [
        "This server keeps the instance's shared skills (how things are done here) and its "
        "notes (what was found out), besides the memory tool.",
        "Before working on something: notes_lookup(subject) for what is already known about "
        "it. A note's header says when it was last confirmed.",
        "Before a task a skill may cover, or before changing any skill: skills_main() once -- "
        "the whole main skill, with the owner's conventions and the full list. Read a skill by "
        "skill_outline(name), then skill_read(name, section).",
    ]
    if len(cats) > 1 or len(found) > pointers.FLAT_MAX:
        lines.append(
            f"{len(found)} skills: " + ", ".join(f"{name} ({count})" for name, count in cats[:20])
            + (" …" if len(cats) > 20 else "")
            + ". skills_category(name) lists one; skills_search(query) finds by words."
        )
    else:
        lines.append(f"{len(found)} skills. skills_search(query) finds by words.")
    top = pointers.mains(found)[:6]
    if top:
        lines.append("Main skills:")
        lines += [f"- {item.name}: {pointers.when(item.when)}" for item in top]
    if who is not None:
        counts = catalog.kinds(db, who)
        if counts:
            lines.append(
                "Notes here: " + ", ".join(f"{kind} ({count})" for kind, count in counts) + "."
            )
    return "\n".join(lines)


def _need_skill(db: DBSession, name: str) -> StoreSkill:
    row = catalog.skill(db, name)
    if row is None:
        raise Refused(f'There is no skill "{name}". skills_search(query) finds skills.')
    return row


def outline_text(row, label: str) -> str:
    lines = [f"{'  ' * max(0, int(s['level']) - 1)}- {s['id']}"
             + (f" — {s['title']}" if s["title"] and doc.slug(str(s["title"])) != s["id"] else "")
             + f" ({s['size']} chars)" for s in row.sections_json]
    return f"{label}\nSections ({row.size} chars in all):\n" + ("\n".join(lines) or "(empty)")


def skill_outline(db: DBSession, name: str) -> str:
    row = _need_skill(db, name)
    return outline_text(row, f"# Skill: {row.name}\n{row.description}")


def skill_read(db: DBSession, name: str, section: str = "") -> str:
    row = _need_skill(db, name)
    if row.name == repo.MAIN and not section:
        catalog.count_read(db, row.name)
        return main_text(db)
    if not section or section == "all":
        catalog.count_read(db, row.name)
        return f"# Skill: {row.name}\n{row.description}\n\n{row.body}"
    found = doc.find_section(row.body, section)
    if found is None:
        ids = ", ".join(str(s["id"]) for s in row.sections_json)
        raise Refused(f'"{row.name}" has no section "{section}". Sections: {ids}')
    catalog.count_read(db, row.name)
    return doc.section_text(row.body, found)


# --- Changing, through the policy ----------------------------------------------------
def _reason(reason: str, who: Who) -> str:
    reason = " ".join((reason or "").split())[:MAX_REASON]
    if not reason and not who.person:
        raise Refused("A reason is needed: one line saying why this change is made.")
    return reason


def _rel(target: str, name: str) -> str:
    return repo.skill_rel(name) if target == TARGET_SKILL else repo.note_rel(name)


def _subject(target: str, name: str, action: str) -> str:
    return f"{target} {name}: {action}"


def _announce(db: DBSession, proposal: StoreProposal) -> None:
    """Tell whoever decides that something waits."""
    try:
        from lembas.services import push

        if proposal.target == TARGET_SKILL:
            ids = [
                user.id for user in db.scalars(select(User))
                if policy.may_decide_skill(db, user)
            ]
        else:
            ids = [proposal.account_id] if proposal.account_id else []
            note = db.scalar(select(StoreNote).where(StoreNote.ident == proposal.name))
            if note is not None and note.author_id and note.author_id not in ids:
                ids.append(note.author_id)
        for user_id in ids:
            push.announce_later(
                user_id, title="", body="", url="/skills/queue", kind="skills"
            )
    except Exception:
        log.debug("skills store: could not announce a waiting change", exc_info=True)


def _submit(
    db: DBSession,
    who: Who,
    *,
    target: str,
    name: str,
    action: str,
    new_text: str | None,
    reason: str,
    decision: str,
    section: str = "",
    section_text: str | None = None,
) -> Outcome:
    """Apply, queue or refuse one change. The only way a caller's change is
    committed."""
    rel = _rel(target, name)
    current = repo.read(rel)
    if new_text is not None and current == new_text:
        return Outcome(APPLIED, "Nothing changed: the text is already that.", name)
    if decision == policy.REFUSE:
        raise Refused(
            "This instance's policy does not take that change from an agent: "
            + ("only a person edits here, by hand." if who.may_write
               else "this account's models may not write skills or notes.")
        )
    if decision == policy.QUEUE:
        waiting = db.scalar(
            select(func.count()).select_from(StoreProposal).where(
                StoreProposal.status == PROPOSAL_PENDING,
                StoreProposal.account_id == who.account_id,
            )
        )
        if (waiting or 0) >= PENDING_PER_ACCOUNT:
            raise Refused("Too many of this account's changes are waiting for approval already.")
        proposal = StoreProposal(
            target=target,
            name=name,
            action=action,
            section=section[:120],
            new_text=new_text,
            base_hash=catalog.text_hash(current) if current is not None else "",
            reason=reason,
            data_group_id=who.data_group,
            account_id=who.account_id,
            account_name=who.account_name[:120],
            harness=who.harness,
            model=who.model,
            model_said=who.model_said,
            session=who.session,
            origin=who.origin,
            section_text=section_text,
        )
        db.add(proposal)
        db.commit()
        _announce(db, proposal)
        return Outcome(
            QUEUED,
            "Not applied yet: the change waits for a person's approval "
            f"(proposal {proposal.id[:8]}). Until then the text is as it was.",
            name,
            proposal_id=proposal.id,
        )
    sha = repo.commit(
        {rel: new_text},
        subject=_subject(target, name, action),
        author=who.account_name or "LLeMbas",
        trailers=who.trailers(reason),
    )
    catalog.reindex(db, [rel])
    after_change(db)
    return Outcome(APPLIED, "Done.", name, commit=sha)


# --- Skills ---------------------------------------------------------------------
def _skill_fields(category: str | None, subcategory: str | None) -> dict[str, object]:
    out: dict[str, object] = {}
    if category is not None:
        out["category"] = doc.slug(category) if category.strip() else None
    if subcategory is not None:
        out["subcategory"] = doc.slug(subcategory) if subcategory.strip() else None
    return out


def _check_skill(name: str, text: str) -> None:
    try:
        fmt.validate(text, name)
    except ValueError as exc:
        raise Refused(str(exc)) from exc


def skill_add(
    db: DBSession,
    who: Who,
    *,
    name: str,
    description: str,
    body: str,
    category: str = "",
    subcategory: str = "",
    reason: str = "",
) -> Outcome:
    try:
        name = fmt.check_name(name)
    except ValueError as exc:
        raise Refused(str(exc)) from exc
    if catalog.skill(db, name) is not None or repo.read(repo.skill_rel(name)) is not None:
        raise Refused(f'A skill "{name}" already exists. Change it with skill_edit.')
    if not " ".join((description or "").split()):
        raise Refused("A description is needed: one line saying when to use the skill.")
    text = fmt.compose(name, description, body or "")
    extra = {k: v for k, v in _skill_fields(category, subcategory).items() if v}
    if extra:
        text = doc.set_fields(text, extra)
    _check_skill(name, text)
    return _submit(
        db, who, target=TARGET_SKILL, name=name, action="add", new_text=text,
        reason=_reason(reason, who), decision=policy.decide_skill(db, who),
    )


def skill_edit(
    db: DBSession,
    who: Who,
    *,
    name: str,
    section: str = "",
    text: str | None = None,
    description: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    reason: str = "",
) -> Outcome:
    """Change one section of a skill (`section` "new": add one at the end),
    and/or its description and category."""
    row = _need_skill(db, name)
    rel = repo.skill_rel(row.name)
    current = repo.read(rel) or ""
    after = current
    section_text = None
    if text is not None:
        body = doc.body_of(current)
        if section in ("new", "append"):
            body = doc.append(body, text)
        else:
            found = doc.find_section(body, section)
            if found is None:
                ids = ", ".join(str(s["id"]) for s in row.sections_json)
                raise Refused(
                    f'"{row.name}" has no section "{section}". Sections: {ids} '
                    '(or "new" to add one at the end).'
                )
            body = doc.replace_section(body, found, text)
            section_text = text
        after = doc.with_body(current, body)
    changes = _skill_fields(category, subcategory)
    if description is not None and " ".join(description.split()):
        changes["description"] = " ".join(description.split())
    if row.name == repo.MAIN:
        changes = {}
    if changes:
        after = doc.set_fields(after, changes)
    if text is None and not changes:
        raise Refused("Nothing to change: give a section and its text, or a description.")
    _check_skill(row.name, after)
    return _submit(
        db, who, target=TARGET_SKILL, name=row.name, action="edit", new_text=after,
        reason=_reason(reason, who), decision=policy.decide_skill(db, who),
        section=section, section_text=section_text,
    )


def skill_save(db: DBSession, who: Who, *, name: str, text: str, reason: str = "") -> Outcome:
    """A skill's whole file, replaced (the page's editor, and `skill_manage`)."""
    row = _need_skill(db, name)
    if row.name == repo.MAIN:
        current = repo.read(repo.skill_rel(repo.MAIN)) or repo.MAIN_SKILL
        text = doc.with_body(current, doc.body_of(text) if doc.split(text)[0] else text)
    _check_skill(row.name, text)
    return _submit(
        db, who, target=TARGET_SKILL, name=row.name, action="edit", new_text=text,
        reason=_reason(reason, who), decision=policy.decide_skill(db, who),
    )


def skill_remove(db: DBSession, who: Who, *, name: str, reason: str = "") -> Outcome:
    row = _need_skill(db, name)
    if row.name == repo.MAIN:
        raise Refused("The main skill is not removed.")
    return _submit(
        db, who, target=TARGET_SKILL, name=row.name, action="remove", new_text=None,
        reason=_reason(reason, who), decision=policy.decide_skill(db, who),
    )


def skill_pin(db: DBSession, who: Who, *, name: str, pinned: bool) -> Outcome:
    """Pin a skill as a main one: a person's choice, by hand."""
    row = _need_skill(db, name)
    if not (who.person and who.approver):
        raise Refused("Pinning is done by an administrator, by hand.")
    current = repo.read(repo.skill_rel(row.name)) or ""
    after = doc.set_fields(current, {"pinned": True if pinned else None})
    return _submit(
        db, who, target=TARGET_SKILL, name=row.name, action="pin" if pinned else "unpin",
        new_text=after, reason="", decision=policy.APPLY,
    )


# --- Notes ----------------------------------------------------------------------
def _need_note(db: DBSession, who: Who, ident: str) -> StoreNote:
    row = catalog.note(db, who, ident)
    if row is None:
        raise Refused(f'There is no note "{ident}" here. notes_lookup(subject) finds notes.')
    return row


def _subjects(value) -> list[str]:
    if isinstance(value, str):
        value = value.replace(";", ",").split(",")
    out: list[str] = []
    for item in value or []:
        text = " ".join(str(item).lower().split())[:80]
        if text and text not in out:
            out.append(text)
    return out[:24]


def _kind(value: str) -> str:
    kind = doc.slug(value or "note")[:40]
    return kind or "note"


def note_outline(db: DBSession, who: Who, ident: str) -> str:
    row = _need_note(db, who, ident)
    return outline_text(row, note_header(row))


def note_header(row: StoreNote) -> str:
    """A note's header as an agent reads it: what it is, how fresh, by whom."""
    by = row.author or "unknown"
    if row.harness:
        said = ", said by the caller" if row.model_said else ""
        by += f" ({row.harness}" + (f", {row.model}{said}" if row.model else "") + ")"
    confirmed = (
        f"confirmed {row.confirmed[:10]}" + (f" by {row.confirmed_by}" if row.confirmed_by else "")
        if row.confirmed else "never confirmed"
    )
    return (
        f"{row.ident} [{row.kind}{', archived' if row.status == 'archived' else ''}] — "
        f"{row.summary or row.title}\n"
        f"  subjects: {', '.join(row.subjects_json) or '-'}; created {row.created[:10] or '?'}, "
        f"updated {row.updated[:10] or '?'}, {confirmed}; by {by}"
    )


def note_read(db: DBSession, who: Who, ident: str, section: str = "") -> str:
    row = _need_note(db, who, ident)
    if not section or section == "all":
        return f"{note_header(row)}\n\n{row.body}"
    found = doc.find_section(row.body, section)
    if found is None:
        ids = ", ".join(str(s["id"]) for s in row.sections_json)
        raise Refused(f'"{row.ident}" has no section "{section}". Sections: {ids}')
    return doc.section_text(row.body, found)


def _free_ident(db: DBSession, wanted: str) -> str:
    base = doc.slug(wanted)[:80] or "note"
    ident, n = base, 1
    while (
        db.scalar(select(StoreNote.id).where(StoreNote.ident == ident)) is not None
        or repo.read(repo.note_rel(ident)) is not None
        or db.scalar(
            select(StoreProposal.id).where(
                StoreProposal.target == TARGET_NOTE,
                StoreProposal.name == ident,
                StoreProposal.status == PROPOSAL_PENDING,
            )
        ) is not None
    ):
        n += 1
        ident = f"{base}-{n}"
    return ident


def note_add(
    db: DBSession,
    who: Who,
    *,
    title: str,
    body: str,
    subjects=None,
    kind: str = "note",
    summary: str = "",
    reason: str = "",
    shared: bool = False,
) -> Outcome:
    title = " ".join((title or "").split())[:200]
    body = (body or "").strip("\n")
    found = _subjects(subjects)
    if not title or not body.strip():
        raise Refused("A note needs a title and a body.")
    if not found:
        raise Refused("A note needs subjects: the words somebody would look it up by.")
    if len(body) > MAX_NOTE_CHARS:
        raise Refused(f"A note holds up to {MAX_NOTE_CHARS:,} characters; this is {len(body):,}.")
    stamp = catalog.now_stamp()
    header = {
        "title": title,
        "subjects": found,
        "kind": _kind(kind),
        "summary": " ".join((summary or "").split())[:600],
        "status": catalog.STATUS_ACTIVE,
        "group": who.data_group or DEFAULT_GROUP,
        # Only a person, by hand, shows a note to every data group.
        "shared": bool(shared and who.person),
        "created": stamp,
        "updated": stamp,
        "author": who.account_name,
        "author_id": who.account_id,
        "harness": who.harness,
        "model": who.model or None,
        "model_said": True if who.model_said else None,
        "confirmed": stamp,
        "confirmed_by": who.account_name,
    }
    text = doc.set_fields(body + "\n", header)
    ident = _free_ident(db, title)
    return _submit(
        db, who, target=TARGET_NOTE, name=ident, action="add", new_text=text,
        reason=" ".join((reason or "").split())[:MAX_REASON] or "new note",
        decision=policy.decide_note(db, who, "add"),
    )


def _note_text(row: StoreNote) -> str:
    text = repo.read(repo.note_rel(row.ident))
    if text is None:
        raise Refused(f'The note "{row.ident}" is no longer there.')
    return text


def note_append(db: DBSession, who: Who, *, ident: str, text: str, reason: str = "") -> Outcome:
    """Add to a note: a dated, signed addition at its end. Nobody's words change."""
    row = _need_note(db, who, ident)
    addition = (text or "").strip("\n")
    if not addition.strip():
        raise Refused("There is nothing to add.")
    if row.size + len(addition) > MAX_NOTE_CHARS:
        raise Refused("That would make the note longer than a note may be; write a new one.")
    current = _note_text(row)
    stamp = catalog.now_stamp()
    signed = f"## Added {stamp[:10]} by {who.account_name} ({who.harness})\n\n{addition}"
    after = doc.with_body(current, doc.append(doc.body_of(current), signed))
    after = doc.set_fields(after, {"updated": stamp})
    return _submit(
        db, who, target=TARGET_NOTE, name=row.ident, action="append", new_text=after,
        reason=" ".join((reason or "").split())[:MAX_REASON] or "added to",
        decision=policy.decide_note(db, who, "append"),
    )


def note_change(
    db: DBSession,
    who: Who,
    *,
    ident: str,
    section: str = "",
    text: str | None = None,
    summary: str | None = None,
    subjects=None,
    kind: str | None = None,
    reason: str = "",
) -> Outcome:
    """Change what a note says: one section (or the whole body when `section`
    is "all"), or its summary, subjects and kind."""
    row = _need_note(db, who, ident)
    current = _note_text(row)
    after = current
    section_text = None
    if text is not None:
        body = doc.body_of(current)
        if section in ("", "all"):
            body = text.strip("\n")
        else:
            found = doc.find_section(body, section)
            if found is None:
                ids = ", ".join(str(s["id"]) for s in row.sections_json)
                raise Refused(f'"{row.ident}" has no section "{section}". Sections: {ids}')
            body = doc.replace_section(body, found, text)
            section_text = text
        if len(body) > MAX_NOTE_CHARS:
            raise Refused("That would make the note longer than a note may be.")
        if not body.strip():
            raise Refused("A note cannot be emptied; archive it instead.")
        after = doc.with_body(current, body)
    fields: dict[str, object] = {}
    if summary is not None:
        fields["summary"] = " ".join(summary.split())[:600]
    if subjects is not None and _subjects(subjects):
        fields["subjects"] = _subjects(subjects)
    if kind is not None and kind.strip():
        fields["kind"] = _kind(kind)
    if text is None and not fields:
        raise Refused("Nothing to change: give a section and its text, a summary or subjects.")
    fields["updated"] = catalog.now_stamp()
    after = doc.set_fields(after, fields)
    return _submit(
        db, who, target=TARGET_NOTE, name=row.ident, action="change", new_text=after,
        reason=_reason(reason, who),
        decision=policy.decide_note(
            db, who, "change", kind=row.kind, author_id=row.author_id, harness=row.harness
        ),
        section=section, section_text=section_text,
    )


def note_confirm(db: DBSession, who: Who, *, ident: str, reason: str = "") -> Outcome:
    """Say a note was checked and is still true: sets "last confirmed"."""
    row = _need_note(db, who, ident)
    stamp = catalog.now_stamp()
    after = doc.set_fields(
        _note_text(row), {"confirmed": stamp, "confirmed_by": who.account_name}
    )
    return _submit(
        db, who, target=TARGET_NOTE, name=row.ident, action="confirm", new_text=after,
        reason=" ".join((reason or "").split())[:MAX_REASON] or "confirmed",
        decision=policy.decide_note(db, who, "confirm"),
    )


def note_archive(
    db: DBSession, who: Who, *, ident: str, reason: str = "", restore: bool = False
) -> Outcome:
    row = _need_note(db, who, ident)
    status = catalog.STATUS_ACTIVE if restore else catalog.STATUS_ARCHIVED
    after = doc.set_fields(
        _note_text(row), {"status": status, "updated": catalog.now_stamp()}
    )
    return _submit(
        db, who, target=TARGET_NOTE, name=row.ident, action="restore" if restore else "archive",
        new_text=after, reason=_reason(reason, who),
        decision=policy.decide_note(
            db, who, "archive", kind=row.kind, author_id=row.author_id, harness=row.harness
        ),
    )


def note_remove(db: DBSession, who: Who, *, ident: str, reason: str = "") -> Outcome:
    row = _need_note(db, who, ident)
    return _submit(
        db, who, target=TARGET_NOTE, name=row.ident, action="remove", new_text=None,
        reason=_reason(reason, who),
        decision=policy.decide_note(
            db, who, "remove", kind=row.kind, author_id=row.author_id, harness=row.harness
        ),
    )


def note_share(db: DBSession, who: Who, *, ident: str, shared: bool) -> Outcome:
    """Show a note to every data group, or stop: a person's choice, by hand,
    and only its author's or an administrator's."""
    row = _need_note(db, who, ident)
    if not who.person or not (who.is_admin or row.author_id == who.account_id):
        raise Refused("A note is shared by its author or an administrator, by hand.")
    after = doc.set_fields(_note_text(row), {"shared": bool(shared)})
    return _submit(
        db, who, target=TARGET_NOTE, name=row.ident, action="share" if shared else "unshare",
        new_text=after, reason="", decision=policy.APPLY,
    )


# --- The queue ---------------------------------------------------------------------
def may_decide(db: DBSession, user: User, proposal: StoreProposal) -> bool:
    """Whether this person decides this waiting change (`policy.may_decide_*`
    say who approves; this only gathers what they ask about)."""
    if proposal.target == TARGET_SKILL:
        return policy.may_decide_skill(db, user)
    who = policy.person(db, user)
    note = db.scalar(select(StoreNote).where(StoreNote.ident == proposal.name))
    sees = (proposal.data_group_id or DEFAULT_GROUP) in who.groups and (
        note is None or catalog.visible(db, who, note)
    )
    return policy.may_decide_note(
        db, user, sees=sees, author_id=note.author_id if note is not None else "",
        proposer_id=proposal.account_id,
    )


def pending(db: DBSession, user: User) -> list[StoreProposal]:
    rows = db.scalars(
        select(StoreProposal)
        .where(StoreProposal.status == PROPOSAL_PENDING)
        .order_by(StoreProposal.created_at)
    )
    return [row for row in rows if may_decide(db, user, row)]


def pending_count(db: DBSession, user: User) -> int:
    return len(pending(db, user))


def decided(db: DBSession, user: User, limit: int = 20) -> list[StoreProposal]:
    rows = db.scalars(
        select(StoreProposal)
        .where(StoreProposal.status != PROPOSAL_PENDING)
        .order_by(StoreProposal.decided_at.desc())
        .limit(limit * 3)
    )
    return [row for row in rows if may_decide(db, user, row)][:limit]


def _resolved_text(proposal: StoreProposal) -> tuple[str | None, bool]:
    """(the text an approval would write, whether it can be applied). A file
    that changed since: a section's change is made again on the file as it is
    now; anything else has gone stale."""
    current = repo.read(_rel(proposal.target, proposal.name))
    base = catalog.text_hash(current) if current is not None else ""
    if base == proposal.base_hash:
        return proposal.new_text, True
    if proposal.action == "add" or current is None or proposal.new_text is None:
        return proposal.new_text, False
    if proposal.section_text and proposal.section not in ("", "new", "append", "all"):
        body = doc.body_of(current)
        found = doc.find_section(body, proposal.section)
        if found is not None:
            body = doc.replace_section(body, found, proposal.section_text)
            return doc.with_body(current, body), True
    return proposal.new_text, False


def diff(old: str | None, new: str | None, name: str = "") -> str:
    lines = difflib.unified_diff(
        (old or "").splitlines(), (new or "").splitlines(),
        fromfile=f"{name} (now)", tofile=f"{name} (proposed)", lineterm="", n=3,
    )
    return "\n".join(lines)


def proposal_diff(proposal: StoreProposal) -> tuple[str, bool]:
    """(the change as a diff against the file as it is now, still applicable)."""
    text, fits = _resolved_text(proposal)
    current = repo.read(_rel(proposal.target, proposal.name))
    return diff(current, text, proposal.name), fits


def approve(db: DBSession, user: User, proposal_id: str, word: str = "") -> Outcome:
    proposal = db.get(StoreProposal, proposal_id)
    if proposal is None or proposal.status != PROPOSAL_PENDING:
        raise Refused("That change is no longer waiting.")
    if not may_decide(db, user, proposal):
        raise Refused("That change is not yours to decide.")
    text, fits = _resolved_text(proposal)
    now = datetime.now(UTC)
    proposal.decided_by, proposal.decided_name, proposal.decided_at = user.id, user.name, now
    proposal.word = " ".join((word or "").split())[:400]
    if not fits:
        proposal.status = PROPOSAL_STALE
        db.commit()
        raise Refused(
            "The text changed since this was proposed, so it no longer fits; it was set aside. "
            "The change can be proposed again."
        )
    if proposal.target == TARGET_SKILL and text is not None:
        _check_skill(proposal.name, text)
    trailers = {
        "Account": proposal.account_name,
        "Account-Id": proposal.account_id,
        "Harness": proposal.harness,
        "Model": "" if proposal.model_said else proposal.model,
        "Model-Said": proposal.model if proposal.model_said else "",
        "Session": proposal.session,
        "Origin": proposal.origin,
        "Reason": proposal.reason,
        "Approved-By": user.name,
    }
    rel = _rel(proposal.target, proposal.name)
    sha = repo.commit(
        {rel: text},
        subject=_subject(proposal.target, proposal.name, proposal.action),
        author=proposal.account_name or "LLeMbas",
        trailers=trailers,
    )
    proposal.status = PROPOSAL_APPROVED
    proposal.commit_sha = sha
    db.commit()
    catalog.reindex(db, [rel])
    after_change(db)
    return Outcome(APPLIED, "Approved and applied.", proposal.name, commit=sha)


def reject(db: DBSession, user: User, proposal_id: str, word: str = "") -> Outcome:
    proposal = db.get(StoreProposal, proposal_id)
    if proposal is None or proposal.status != PROPOSAL_PENDING:
        raise Refused("That change is no longer waiting.")
    if not may_decide(db, user, proposal):
        raise Refused("That change is not yours to decide.")
    proposal.status = PROPOSAL_REJECTED
    proposal.decided_by, proposal.decided_name = user.id, user.name
    proposal.decided_at = datetime.now(UTC)
    proposal.word = " ".join((word or "").split())[:400]
    db.commit()
    return Outcome(REFUSED, "Rejected.", proposal.name)


def own_proposals(db: DBSession, who: Who, limit: int = 10) -> list[StoreProposal]:
    """What became of this account's latest proposals (an agent asks)."""
    return list(
        db.scalars(
            select(StoreProposal)
            .where(StoreProposal.account_id == who.account_id)
            .order_by(StoreProposal.created_at.desc())
            .limit(limit)
        )
    )


# --- The older store ------------------------------------------------------------------
def import_tree(db: DBSession) -> list[str]:
    """Bring in the skills an instance kept as files in the first
    organisation's `/skills/` before this store existed: each once, by name,
    as a commit that says where it came from. The files stay where they are;
    they are no longer what a chat reads (`services/skill_files.py`), so there
    is one collection. A name the store already has is left to the store."""
    from lembas.db.models import DEFAULT_ORG, KIND_FILE, Node
    from lembas.services import blobs

    values = policy.settings(db)
    done = values.get("tree_imported")
    done = [str(n) for n in done] if isinstance(done, list) else []
    rows = db.scalars(
        select(Node).where(
            Node.org_id == DEFAULT_ORG,
            Node.kind == KIND_FILE,
            Node.trashed_at.is_(None),
            Node.path.like("/skills/%/" + repo.SKILL_FILE),
        ).order_by(Node.path)
    )
    brought: list[str] = []
    seen = list(done)
    for node in rows:
        parts = node.path.split("/")
        if len(parts) != 4 or parts[2] in seen:
            continue
        name = parts[2]
        seen.append(name)
        try:
            repo.check_rel(repo.skill_rel(name))
        except repo.StoreError:
            continue
        catalog.ensure(db)
        if repo.read(repo.skill_rel(name)) is not None or not node.blob_hash:
            continue
        text = blobs.read_bytes(db, node.blob_hash).decode("utf-8", errors="replace")
        by = db.get(User, node.updated_by or node.owner_id or "") if (
            node.updated_by or node.owner_id
        ) else None
        repo.commit(
            {repo.skill_rel(name): text},
            subject=_subject(TARGET_SKILL, name, "import"),
            author=by.name if by is not None else "LLeMbas",
            trailers={
                "Account": by.name if by is not None else "",
                "Account-Id": by.id if by is not None else "",
                "Origin": policy.ORIGIN_IMPORT,
                "Reason": "From this instance's /skills/ folder.",
            },
        )
        brought.append(name)
    if seen != done:
        policy.save(db, {"tree_imported": seen})
    if brought:
        catalog.reindex(db, [repo.skill_rel(name) for name in brought])
        after_change(db)
    return brought


# --- History --------------------------------------------------------------------
def history(target: str, name: str, limit: int = 30) -> list[repo.Commit]:
    return repo.history(_rel(target, name), limit=limit)


def history_text(target: str, name: str, limit: int = 15) -> str:
    lines = []
    for commit in history(target, name, limit):
        t = commit.trailers
        who = t.get("Account") or commit.author
        said = f"{t['Model-Said']}, said by the caller" if t.get("Model-Said") else ""
        via = ", ".join(x for x in (t.get("Harness", ""), t.get("Model", ""), said) if x)
        line = f"{commit.at[:16].replace('T', ' ')}  {commit.sha[:8]}  {commit.subject}  — {who}"
        if via:
            line += f" ({via})"
        line += f" [{commit.origin}]"
        if t.get("Approved-By"):
            line += f", approved by {t['Approved-By']}"
        if t.get("Reason"):
            line += f"\n    reason: {t['Reason']}"
        lines.append(line)
    return "\n".join(lines) or "(no history)"


def commit_diff(target: str, name: str, sha: str) -> str:
    """What one commit changed in a file, as a diff."""
    rel = _rel(target, name)
    commits = repo.history(rel, limit=200)
    at = next((i for i, c in enumerate(commits) if c.sha.startswith(sha)), None)
    if at is None:
        return ""
    new = repo.at_commit(commits[at].sha, rel)
    old = repo.at_commit(commits[at + 1].sha, rel) if at + 1 < len(commits) else None
    return "\n".join(difflib.unified_diff(
        (old or "").splitlines(), (new or "").splitlines(),
        fromfile="before", tofile="after", lineterm="", n=3,
    ))


# --- The remote ------------------------------------------------------------------
def _secret(values: dict, key: str) -> str:
    return crypto.decrypt(values[key]) if values.get(key) else ""


def _credentials(values: dict) -> gitrun.Credentials:
    return gitrun.Credentials(
        username=str(values.get("remote_username") or ""),
        token=_secret(values, "remote_token_encrypted"),
        ssh_key=_secret(values, "remote_ssh_key_encrypted"),
    )


def remote_set(
    db: DBSession,
    *,
    address: str,
    branch: str = "main",
    username: str = "",
    token: str | None = None,
    ssh_key: str | None = None,
) -> None:
    """Name the remote (or none). A secret left empty keeps the stored one;
    both are stored encrypted and never given back."""
    address = (address or "").strip()
    changes: dict[str, object] = {"remote_address": "", "sync_state": repo.SYNC_OFF,
                                  "sync_message": ""}
    if address:
        try:
            changes["remote_address"] = gitrun.check_address(address)
        except gitrun.BadAddress as exc:
            raise Refused(str(exc)) from exc
        changes["remote_branch"] = (branch or "main").strip() or "main"
        changes["remote_username"] = " ".join((username or "").split())[:120]
        changes["sync_state"] = "new"
        changes["remote_adopted"] = False
        if token:
            changes["remote_token_encrypted"] = crypto.encrypt(token.strip())
        if ssh_key and "PRIVATE KEY" in ssh_key:
            key = ssh_key.replace("\r\n", "\n").strip()
            changes["remote_ssh_key_encrypted"] = crypto.encrypt(key)
    else:
        changes["remote_token_encrypted"] = ""
        changes["remote_ssh_key_encrypted"] = ""
    policy.save(db, changes)


def _record_sync(db: DBSession, result: repo.Synced) -> None:
    if result.state == repo.SYNC_OK:
        policy.save(db, {"remote_adopted": True})
    policy.save(db, {
        "sync_state": result.state,
        "sync_message": result.message[:600],
        "sync_at": catalog.now_stamp(),
    })


def sync_now(db: DBSession, *, adopt: bool = False, force: bool = False) -> repo.Synced:
    """Sync with the remote now. A store that has diverged stays stopped
    until a person resolves it (`force`: try again after they did)."""
    values = policy.settings(db)
    address = values["remote_address"]
    if not address:
        return repo.Synced(repo.SYNC_OFF)
    if values["sync_state"] == repo.SYNC_DIVERGED and not force:
        return repo.Synced(repo.SYNC_DIVERGED, values["sync_message"])
    adopt = adopt or not values.get("remote_adopted")
    result = repo.sync(address, _credentials(values), branch=values["remote_branch"], adopt=adopt)
    _take(db, result)
    return result


def _take(db: DBSession, result: repo.Synced) -> None:
    if result.pulled is not None:
        old, new = result.pulled
        catalog.reindex(db, None if not old else repo.changed_paths(old, new))
    if result.adopted:
        catalog.reindex(db)
        # What this instance held under another text is not lost and not
        # merged: it waits in the queue, for a person to choose.
        for rel, text in result.conflicts.items():
            if rel == repo.skill_rel(repo.MAIN) and text == repo.MAIN_SKILL:
                continue
            is_skill = rel.startswith(repo.SKILLS + "/")
            current = repo.read(rel)
            db.add(StoreProposal(
                target=TARGET_SKILL if is_skill else TARGET_NOTE,
                name=rel.split("/")[1] if is_skill else catalog.note_ident(rel),
                action="edit",
                new_text=text,
                base_hash=catalog.text_hash(current or ""),
                reason="This instance's text from before the remote was connected.",
                account_name="LLeMbas",
                harness="import",
                origin=policy.ORIGIN_IMPORT,
            ))
        db.commit()
    _record_sync(db, result)


def take_remote(db: DBSession) -> repo.Synced:
    values = policy.settings(db)
    if not values["remote_address"]:
        return repo.Synced(repo.SYNC_OFF)
    result = repo.take_remote(
        values["remote_address"], _credentials(values), branch=values["remote_branch"]
    )
    if result.state == repo.SYNC_OK:
        catalog.reindex(db)
    _record_sync(db, result)
    return result


_SYNCING = threading.Lock()
_THREADS: list[threading.Thread] = []


def _background_sync() -> None:
    from lembas.db.session import session_scope

    if not _SYNCING.acquire(blocking=False):
        return
    try:
        with session_scope() as db:
            sync_now(db)
    except Exception:
        log.warning("skills store: the background sync failed", exc_info=True)
    finally:
        _SYNCING.release()


def after_change(db: DBSession) -> None:
    """Push after a change, without making the caller wait for a remote."""
    values = policy.settings(db)
    if not values["remote_address"] or values["sync_state"] == repo.SYNC_DIVERGED:
        return
    thread = threading.Thread(target=_background_sync, name="skills-sync", daemon=True)
    _THREADS[:] = [t for t in _THREADS if t.is_alive()] + [thread]
    thread.start()


def sync_if_due(db: DBSession) -> None:
    """Look at the remote when the last look is older than the setting says."""
    values = policy.settings(db)
    if not values["remote_address"] or values["sync_state"] == repo.SYNC_DIVERGED:
        return
    last = values.get("sync_at") or ""
    try:
        when = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        when = None
    if when is None or datetime.now(UTC) - when > timedelta(minutes=values["sync_minutes"]):
        after_change(db)


def wait_for_sync(timeout: float = 30.0) -> None:
    """For a test: until the background sync has finished."""
    for thread in list(_THREADS):
        thread.join(timeout)


__all__ = [
    "APPLIED",
    "QUEUED",
    "REFUSED",
    "TARGET_NOTE",
    "TARGET_SKILL",
    "Outcome",
    "Refused",
    "after_change",
    "approve",
    "category_text",
    "commit_diff",
    "decided",
    "diff",
    "history",
    "history_text",
    "import_tree",
    "instructions",
    "main_prose",
    "main_text",
    "may_decide",
    "note_add",
    "note_append",
    "note_archive",
    "note_change",
    "note_confirm",
    "note_header",
    "note_outline",
    "note_read",
    "note_remove",
    "note_share",
    "own_proposals",
    "pending",
    "pending_count",
    "proposal_diff",
    "reject",
    "remote_set",
    "skill_add",
    "skill_edit",
    "skill_outline",
    "skill_pin",
    "skill_read",
    "skill_remove",
    "skill_save",
    "sync_if_due",
    "sync_now",
    "take_remote",
    "wait_for_sync",
]
