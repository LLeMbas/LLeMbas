"""The index beside the store: rebuilt from the files, read for every lookup.

`reindex` reads the store's files into `store_skills` and `store_notes` (all
of them, or the paths a change touched) and forgets rows whose file is gone.
Nothing here is the truth: delete both tables and `reindex` puts them back,
apart from the read counts, which are this instance's own.

**Who sees what is decided here, for every door**: skills are everybody's;
a note is visible in the data group it was written under, or to everybody
when the note itself or its kind is shared (`visible`). The search and the
fetch by name use the same rule.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import struct
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import DEFAULT_GROUP, StoreNote, StoreRead, StoreSkill
from lembas.services.skillstore import doc, pointers, policy, repo

log = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
READ_DAYS = 30
EMBED_CHARS = 6000


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def now_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _str(value, limit: int = 300) -> str:
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    return " ".join(str(value or "").split())[:limit]


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "yes", "1")


def _stamp_change(row, rel: str) -> None:
    last = next(iter(repo.history(rel, limit=1)), None)
    if last is None:
        return
    row.commit_sha = last.sha
    row.changed_at = _when(last.at)
    row.changed_by = (last.trailers.get("Account") or last.author)[:120]
    row.changed_account_id = last.trailers.get("Account-Id", "")[:32]
    row.changed_harness = last.trailers.get("Harness", "")[:80]
    said = last.trailers.get("Model-Said", "")
    row.changed_model = (last.trailers.get("Model", "") or said)[:200]
    row.changed_model_said = bool(said and not last.trailers.get("Model"))
    row.changed_origin = last.origin[:16]


def _index_skill(db: DBSession, rel: str, text: str) -> None:
    name = rel.split("/")[1]
    header, body = doc.split(text)
    meta = doc.fields(header)
    row = db.scalar(select(StoreSkill).where(StoreSkill.name == name))
    if row is None:
        row = StoreSkill(name=name)
        db.add(row)
    digest = text_hash(text)
    row.title = _str(meta.get("name") or name)
    row.description = _str(meta.get("description"), 2000)
    row.category = doc.slug(_str(meta.get("category"), 64)) if meta.get("category") else ""
    row.subcategory = (
        doc.slug(_str(meta.get("subcategory"), 64)) if meta.get("subcategory") else ""
    )
    row.pinned = _truthy(meta.get("pinned", ""))
    row.body = body.strip("\n")
    row.size = len(row.body)
    row.sections_json = doc.outline(row.body)
    if row.text_hash != digest or not row.commit_sha:
        row.text_hash = digest
        _stamp_change(row, rel)


def note_ident(rel: str) -> str:
    return rel[len(repo.NOTES) + 1 : -len(".md")]


def _index_note(db: DBSession, rel: str, text: str) -> None:
    ident = note_ident(rel)
    header, body = doc.split(text)
    meta = doc.fields(header)
    row = db.scalar(select(StoreNote).where(StoreNote.ident == ident))
    if row is None:
        row = StoreNote(ident=ident)
        db.add(row)
    digest = text_hash(text)
    row.body = body.strip("\n")
    row.title = _str(meta.get("title")) or doc.title_of(row.body) or ident
    subjects = meta.get("subjects")
    if isinstance(subjects, str):
        subjects = [part.strip() for part in subjects.split(",")]
    row.subjects_json = [_str(s, 80).lower() for s in (subjects or []) if _str(s)][:24]
    kind = doc.slug(_str(meta.get("kind"), 40)) if meta.get("kind") else "note"
    row.kind = kind
    row.summary = _str(meta.get("summary"), 600)
    archived = _str(meta.get("status")).lower() == "archived"
    row.status = STATUS_ARCHIVED if archived else STATUS_ACTIVE
    row.data_group_id = _str(meta.get("group"), 32) or DEFAULT_GROUP
    row.shared = _truthy(meta.get("shared", ""))
    row.created = _str(meta.get("created"), 40)
    row.updated = _str(meta.get("updated"), 40)
    row.author = _str(meta.get("author"), 120)
    row.author_id = _str(meta.get("author_id"), 32)
    row.harness = _str(meta.get("harness"), 80)
    row.model = _str(meta.get("model"), 200)
    row.model_said = _truthy(meta.get("model_said", ""))
    row.confirmed = _str(meta.get("confirmed"), 40)
    row.confirmed_by = _str(meta.get("confirmed_by"), 120)
    row.size = len(row.body)
    row.sections_json = doc.outline(row.body)
    if row.text_hash != digest or not row.commit_sha:
        row.text_hash = digest
        _stamp_change(row, rel)


def reindex(db: DBSession, paths: list[str] | None = None) -> int:
    """Read the store's files into the index: all of them, or these paths.
    Returns how many files were read."""
    repo.ensure()
    present = set(repo.files())
    wanted = present if paths is None else {p for p in paths if p.endswith(".md")}
    read = 0
    for rel in sorted(wanted):
        if rel not in present:
            continue
        text = repo.read(rel)
        if text is None:
            continue
        if rel.startswith(repo.SKILLS + "/"):
            _index_skill(db, rel, text)
        else:
            _index_note(db, rel, text)
        read += 1
    skills_there = {rel.split("/")[1] for rel in present if rel.startswith(repo.SKILLS + "/")}
    notes_there = {note_ident(rel) for rel in present if rel.startswith(repo.NOTES + "/")}
    for row in db.scalars(select(StoreSkill)):
        if row.name not in skills_there:
            db.delete(row)
    for row in db.scalars(select(StoreNote)):
        if row.ident not in notes_there:
            db.delete(row)
    db.commit()
    return read


def ensure(db: DBSession) -> None:
    """The store and its index, the first time anything asks."""
    made = repo.ensure()
    if made or db.scalar(select(func.count()).select_from(StoreSkill)) == 0:
        reindex(db)


# --- Skills ---------------------------------------------------------------------
def skills(db: DBSession, *, with_main: bool = False) -> list[StoreSkill]:
    ensure(db)
    rows = list(db.scalars(select(StoreSkill).order_by(StoreSkill.name)))
    return rows if with_main else [row for row in rows if row.name != repo.MAIN]


def skill(db: DBSession, name: str) -> StoreSkill | None:
    ensure(db)
    wanted = (name or "").strip()
    row = db.scalar(select(StoreSkill).where(StoreSkill.name == wanted))
    if row is None and wanted:
        key = doc.slug(wanted)
        row = next((r for r in skills(db, with_main=True) if doc.slug(r.name) == key), None)
    return row


def count_read(db: DBSession, name: str) -> None:
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    row = db.get(StoreRead, (name, day))
    if row is None:
        db.add(StoreRead(name=name, day=day, count=1))
    else:
        row.count += 1
    db.execute(
        delete(StoreRead).where(
            StoreRead.day < (datetime.now(UTC) - timedelta(days=READ_DAYS * 3)).strftime("%Y-%m-%d")
        )
    )
    db.commit()


def reads(db: DBSession) -> dict[str, int]:
    since = (datetime.now(UTC) - timedelta(days=READ_DAYS)).strftime("%Y-%m-%d")
    rows = db.execute(
        select(StoreRead.name, func.sum(StoreRead.count))
        .where(StoreRead.day >= since)
        .group_by(StoreRead.name)
    )
    return {name: int(total or 0) for name, total in rows}


def items(db: DBSession) -> list[pointers.Item]:
    counts = reads(db)
    return [
        pointers.Item(
            name=row.name,
            when=row.description,
            category=row.category,
            subcategory=row.subcategory,
            pinned=row.pinned,
            reads=counts.get(row.name, 0),
        )
        for row in skills(db)
    ]


def categories(db: DBSession) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in skills(db):
        key = row.category or pointers.GENERAL
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items())


# --- Notes ----------------------------------------------------------------------
def visible(db: DBSession, who: policy.Who, note: StoreNote) -> bool:
    if note.shared or note.kind in policy.settings(db)["shared_kinds"]:
        return True
    return (note.data_group_id or DEFAULT_GROUP) in who.groups


def note(db: DBSession, who: policy.Who, ident: str) -> StoreNote | None:
    """A note by name -- only one this caller may see; another group's does
    not exist for it."""
    ensure(db)
    wanted = (ident or "").strip().strip("/")
    if wanted.endswith(".md"):
        wanted = wanted[:-3]
    if wanted.startswith(repo.NOTES + "/"):
        wanted = wanted[len(repo.NOTES) + 1 :]
    row = db.scalar(select(StoreNote).where(StoreNote.ident == wanted))
    return row if row is not None and visible(db, who, row) else None


def notes(
    db: DBSession,
    who: policy.Who,
    *,
    subject: str = "",
    kind: str = "",
    status: str = STATUS_ACTIVE,
    group: str = "",
) -> list[StoreNote]:
    """The notes this caller may see, narrowed; newest change first."""
    ensure(db)
    wanted_subject = " ".join((subject or "").lower().split())
    out = []
    for row in db.scalars(select(StoreNote).order_by(StoreNote.updated.desc(), StoreNote.ident)):
        if not visible(db, who, row):
            continue
        if status and status != "all" and row.status != status:
            continue
        if kind and row.kind != kind:
            continue
        if group and (row.data_group_id or DEFAULT_GROUP) != group:
            continue
        if wanted_subject and not _about(row, wanted_subject):
            continue
        out.append(row)
    return out


def _about(row: StoreNote, subject: str) -> bool:
    words = [w for w in re.split(r"[\s,;/]+", subject) if w]
    hay = " ".join([*row.subjects_json, row.title.lower(), row.ident.lower()])
    return bool(words) and all(word in hay for word in words)


def kinds(db: DBSession, who: policy.Who) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in notes(db, who):
        counts[row.kind] = counts.get(row.kind, 0) + 1
    return sorted(counts.items())


# --- Search ---------------------------------------------------------------------
def _terms(query: str) -> list[str]:
    return re.findall(r"[\w-]{2,}", (query or "").lower())[:12]


def _score(terms: list[str], name: str, short: str, body: str) -> float:
    name, short, body = name.lower(), short.lower(), body.lower()
    score = 0.0
    for term in terms:
        hit = 6.0 * (term in name) + 3.0 * (term in short) + min(body.count(term), 5) * 1.0
        if not hit:
            # Every word must be somewhere, or the row is about something else.
            return 0.0
        score += hit
    return score


def _snippet(body: str, terms: list[str]) -> tuple[str, str]:
    """(section id, a line around the first word found) in a body."""
    lowered = body.lower()
    at = min((lowered.find(t) for t in terms if t in lowered), default=-1)
    if at < 0:
        return "", ""
    line = body.count("\n", 0, at)
    section = next(
        (s.id for s in doc.sections(body) if s.start <= line < s.end), ""
    )
    start = max(0, at - 60)
    return section, " ".join(body[start : at + 140].split())


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def _embed_missing(db: DBSession, rows: list, worker, limit: int = 48) -> None:
    from lembas.services.index import pipeline

    stale = [r for r in rows if r.embedding_key != f"{worker.model_id}:{r.text_hash}"][:limit]
    for start in range(0, len(stale), max(1, worker.batch)):
        batch = stale[start : start + max(1, worker.batch)]
        texts = [
            (f"{getattr(r, 'name', '') or r.title}\n{getattr(r, 'description', '')}"
             f"{getattr(r, 'summary', '')}\n{r.body}")[:EMBED_CHARS]
            for r in batch
        ]
        vectors = await pipeline._embed(worker, texts)
        for row, vector in zip(batch, vectors, strict=False):
            row.embedding = pipeline.pack(vector)
            row.embedding_key = f"{worker.model_id}:{row.text_hash}"
    db.commit()


async def _meaning(db: DBSession, query: str, rows: list) -> dict[str, float]:
    """row id -> likeness to the query, where the instance has an embedder;
    empty for every "no" (keywords only, never an error)."""
    try:
        from lembas.services.index import pipeline

        worker = pipeline.embedder(db)
        if worker is None or not rows:
            return {}
        await _embed_missing(db, rows, worker)
        vectors = await pipeline._embed(worker, [query])
        if not vectors:
            return {}
        wanted = vectors[0]
        key = worker.model_id + ":"
        return {
            row.id: _cosine(wanted, _unpack(row.embedding))
            for row in rows
            if row.embedding and row.embedding_key.startswith(key)
        }
    except Exception as exc:
        log.info("skills store: no embeddings for this search: %s", exc)
        db.rollback()
        return {}


async def search(
    db: DBSession, who: policy.Who, query: str, *, what: str = "all", limit: int = 8
) -> list[dict[str, object]]:
    """Skills and the notes this caller may see, by words and (where the
    instance embeds) by meaning; best first."""
    terms = _terms(query)
    limit = min(max(int(limit or 8), 1), 25)
    rows: list = []
    if what in ("all", "skills"):
        rows += skills(db)
    if what in ("all", "notes"):
        rows += notes(db, who, status="all")
    if not terms:
        return []
    likeness = await _meaning(db, query, rows)
    scored = []
    for row in rows:
        is_skill = isinstance(row, StoreSkill)
        name = row.name if is_skill else f"{row.ident} {row.title} {' '.join(row.subjects_json)}"
        short = row.description if is_skill else row.summary
        words = _score(terms, name, short, row.body)
        like = likeness.get(row.id, 0.0)
        if not words and like < 0.5:
            continue
        scored.append((words + 12.0 * max(like, 0.0), row))
    scored.sort(key=lambda pair: (-pair[0], getattr(pair[1], "name", "") or pair[1].ident))
    out = []
    for _score_value, row in scored[:limit]:
        section, snippet = _snippet(row.body, terms)
        if isinstance(row, StoreSkill):
            out.append({"kind": "skill", "name": row.name, "when": row.description,
                        "section": section, "snippet": snippet})
        else:
            out.append({"kind": "note", "name": row.ident, "when": row.summary or row.title,
                        "section": section, "snippet": snippet, "note_kind": row.kind,
                        "status": row.status})
    return out


__all__ = [
    "STATUS_ACTIVE",
    "STATUS_ARCHIVED",
    "categories",
    "count_read",
    "ensure",
    "items",
    "kinds",
    "note",
    "note_ident",
    "notes",
    "now_stamp",
    "reads",
    "reindex",
    "search",
    "skill",
    "skills",
    "text_hash",
    "visible",
]
