"""Skills: named instructions the model can choose to follow.

Two fields carry the design.

`description` is what gets injected -- one line per skill, for every skill --
and is therefore the only thing the model has to go on when deciding whether a
skill is relevant. A description that does not say *when* to use the skill makes
it invisible in practice.

`body` is fetched only when the model decides to use it. That split is what
makes a hundred skills affordable: the index costs a line each, the instructions
cost nothing until wanted.

**A model may rewrite its own skills**, which is the point -- it is how it
learns a procedure once instead of being told every time. The safety story is
not a gate but a record: every write snapshots what was there first, so a change
can be read and undone. A skill written after reading a hostile web page is a
real risk, and the honest mitigation is that it is visible, attributed and
revertible rather than that it was somehow prevented.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    AUTHOR_MODEL,
    AUTHOR_USER,
    CHUNK_SKILL,
    DEFAULT_GROUP,
    Skill,
    SkillRevision,
    User,
)
from lembas.services import sharing
from lembas.services.library import retrieval
from lembas.web.i18n import N_, t

log = logging.getLogger(__name__)

INDEX = "skills_fts"

# A name the model can quote back without getting it wrong.
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")

MAX_DESCRIPTION_CHARS = 400
MAX_BODY_CHARS = 20_000

# The index goes into every request, so it has a ceiling like memory does.
MAX_INDEX_SKILLS = 60


class SkillError(Exception):
    """A rejected skill write, with a message fit for the model or the user.

    Two readers, two languages: `str(exc)` is the English, which is what a
    model is handed as a tool result -- a generation inherits the request's
    language, so translating here would put a person's language into a prompt --
    and `translated()` is the same sentence in the reader's language, for a page.
    The text is marked with `N_()` where it is raised so the catalogue finds it.
    """

    def __init__(self, text: str, **fields: object) -> None:
        self.text, self.fields = text, fields
        super().__init__(text % fields if fields else text)

    def translated(self) -> str:
        return t(self.text, **self.fields)


def slugify(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return cleaned[:60]


def visible(db: DBSession, user: User | None, group: str | None = None):
    """Skills this user may see; `group` narrows to one data group, for a model."""
    return select(Skill).where(sharing.visible_to(Skill, user, group))


def get(
    db: DBSession, skill_id: str, user: User | None, group: str | None = None
) -> Skill | None:
    skill = db.get(Skill, skill_id)
    if skill is None or not sharing.can_read(db, skill, user, group):
        return None
    return skill


def by_name(
    db: DBSession, name: str, user: User | None, group: str | None = None
) -> Skill | None:
    """Look one up the way the model refers to it.

    Scoped to what this person can **see**, which is theirs plus anything
    shared with them -- correct for `skill_view` and `skill_manage`, where a
    skill somebody shared is exactly what the model is reaching for.

    It is the wrong question for "is this name taken?"; see `owned_by_name`.
    """
    if user is None:
        return None
    return db.scalar(visible(db, user, group).where(Skill.name == slugify(name)))


def owned_by_name(db: DBSession, name: str, owner: User) -> Skill | None:
    """One of *this person's own* skills by name.

    The uniqueness check used `by_name`, which is scoped to what is visible --
    so a skill somebody shared with you took that name out of your library.
    Sharing a curated skill with a team is the intended use of `library.share`,
    and doing it silently reserved the name for everyone it reached: creating
    your own was refused with "a skill called 'weekly-report' already exists.
    Edit it instead", naming a row you cannot edit, because sharing grants
    reading only. The model's `skill_create` got the same dead end.

    The table's constraint is `(owner_id, name)`, so the question the check
    should have been asking was always this one. `documents.create_base` next
    door asks it correctly.
    """
    return db.scalar(
        select(Skill).where(Skill.owner_id == owner.id, Skill.name == slugify(name))
    )


def enabled_for(
    db: DBSession,
    user: User | None,
    *,
    exclude: Iterable[str] = (),
    group: str | None = None,
) -> list[Skill]:
    """Skills that should appear in the index, oldest first for a stable order.

    `exclude` is what one chat has switched off by name -- a narrowing of what
    the library already allows, never a widening of it.
    """
    if user is None:
        return []
    hidden = {slugify(name) for name in exclude}
    rows = db.scalars(
        visible(db, user, group)
        .where(Skill.enabled.is_(True))
        .order_by(Skill.name)
        .limit(MAX_INDEX_SKILLS + len(hidden))
    )
    return [skill for skill in rows if skill.name not in hidden][:MAX_INDEX_SKILLS]


def count_enabled(
    db: DBSession,
    user: User | None,
    *,
    exclude: Iterable[str] = (),
    group: str | None = None,
) -> int:
    """How many skills are available here at all.

    Zero is what withdraws `skills_list` and `skill_view`: listing and reading
    are meaningless with nothing to read, and a model told to "read one with
    skill_view" above a list that is not there spends a round finding out.
    `skill_manage` stays, because creating the first skill is its point.
    """
    return len(enabled_for(db, user, exclude=exclude, group=group))


def search(
    db: DBSession,
    user: User | None,
    needle: str,
    *,
    limit: int = 10,
    vector: list[float] | None = None,
    group: str | None = None,
) -> list[Skill]:
    """Skills matching `needle` that this user may see, best match first.

    `vector` is the query already embedded, or None. It comes from the caller
    rather than being worked out here because this is synchronous and embedding
    is an HTTP request -- see `services/library/retrieval.py`. None means the
    keyword search exactly as it always was.
    """
    hits = retrieval.search(db, INDEX, needle, kind=CHUNK_SKILL, vector=vector, limit=limit * 4)
    if not hits:
        return []
    order = {hit.id: position for position, hit in enumerate(hits)}
    rows = list(db.scalars(visible(db, user, group).where(Skill.id.in_(list(order)))))
    rows.sort(key=lambda skill: order.get(skill.id, len(order)))
    return rows[:limit]


def snapshot(db: DBSession, skill: Skill, *, author: str, note: str = "") -> SkillRevision:
    """Record what a skill looked like before it is changed."""
    revision = SkillRevision(
        skill_id=skill.id,
        description=skill.description,
        body=skill.body,
        author=author,
        note=note[:200],
    )
    db.add(revision)
    return revision


def create(
    db: DBSession,
    *,
    owner: User,
    name: str,
    description: str,
    body: str,
    author: str = AUTHOR_USER,
    group: str = DEFAULT_GROUP,
) -> Skill:
    slug = slugify(name)
    if not SKILL_NAME_PATTERN.match(slug):
        raise SkillError(
            N_(
                "A skill name must be two or more letters, numbers or hyphens, "
                "such as 'weekly-report'."
            )
        )
    taken = owned_by_name(db, slug, owner)
    if taken is not None:
        # A name is unique per person across every data group -- the table's
        # constraint is `(owner_id, name)` and cannot be changed. "Edit it
        # instead" would send a model in another group to a skill it cannot
        # see, so that case gets its own sentence.
        if (taken.data_group_id or DEFAULT_GROUP) != (group or DEFAULT_GROUP):
            raise SkillError(
                N_(
                    "The name %(slug)s is already used by a skill in another data "
                    "group. Choose a different name."
                ),
                slug=repr(slug),
            )
        raise SkillError(
            N_("A skill called %(slug)s already exists. Edit it instead."), slug=repr(slug)
        )
    if not description.strip():
        raise SkillError(
            N_(
                "A skill needs a description saying when to use it — it is the only "
                "thing shown until the skill is opened."
            )
        )

    skill = Skill(
        owner_id=owner.id,
        data_group_id=group or DEFAULT_GROUP,
        name=slug,
        description=description.strip()[:MAX_DESCRIPTION_CHARS],
        body=body.strip()[:MAX_BODY_CHARS],
        author=author if author in (AUTHOR_USER, AUTHOR_MODEL) else AUTHOR_USER,
    )
    db.add(skill)
    db.commit()
    log.info("skill %r created by %s", slug, author)
    return skill


def update(
    db: DBSession,
    skill: Skill,
    *,
    description: str | None = None,
    body: str | None = None,
    enabled: bool | None = None,
    author: str = AUTHOR_USER,
    note: str = "",
) -> Skill:
    """Change a skill, keeping what it was.

    The snapshot happens before the change and in the same transaction, so
    there is no window where a skill has been rewritten with no record of what
    it used to say.
    """
    changing = (description is not None and description.strip() != skill.description) or (
        body is not None and body.strip() != skill.body
    )
    if changing:
        snapshot(db, skill, author=author, note=note)

    if description is not None and description.strip():
        skill.description = description.strip()[:MAX_DESCRIPTION_CHARS]
    if body is not None:
        skill.body = body.strip()[:MAX_BODY_CHARS]
    if enabled is not None:
        skill.enabled = enabled
    if changing:
        skill.author = author if author in (AUTHOR_USER, AUTHOR_MODEL) else skill.author

    db.commit()
    return skill


def revert(db: DBSession, skill: Skill, revision: SkillRevision, *, author: str) -> Skill:
    """Put a skill back to an earlier revision.

    The revert is itself a change, so the current state is snapshotted first --
    going back is undoable too.
    """
    snapshot(db, skill, author=author, note="before revert")
    skill.description = revision.description
    skill.body = revision.body
    db.commit()
    return skill


def delete(db: DBSession, skill: Skill) -> None:
    sharing.forget_resource(db, skill)
    db.delete(skill)
    db.commit()


def index_block(
    db: DBSession,
    user: User | None,
    *,
    exclude: Iterable[str] = (),
    group: str | None = None,
) -> str:
    """The one-line-per-skill listing that goes into the prompt."""
    skills = enabled_for(db, user, exclude=exclude, group=group)
    if not skills:
        return ""
    return "\n".join(f"- {skill.name}: {skill.description}" for skill in skills)
