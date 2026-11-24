"""The pointer list: which skills a page names, by the branching rules.

A pointer is a skill's name and one line saying when to use it, or a group's
name and how many skills are behind it. The rules (the same for the main
skill's page, where the groups are categories, and for a category's page,
where they are its subcategories):

- up to 10 skills: one list, no groups;
- a group of up to 10: every skill in it pointed to, under the group;
- a group of more than 10: a pointer to the group and up to 3 main skills;
- a group of more than 25: a pointer to the group (which has groups of its
  own) and up to 5 main skills;
- never more than 50 pointers. The ceiling wins: the largest group that is
  still listed whole collapses first; then the largest loses its main skills;
  then the smallest groups are counted instead of named.

Main skills: the pinned ones first, then the most read lately, then by name.
A page with no groups below it (a subcategory) lists its main skills up to the
ceiling and says how many more a search reaches.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

FLAT_MAX = 10
GROUP_ALL_MAX = 10
GROUP_BIG = 25
MAINS = 3
MAINS_BIG = 5
CEILING = 50
WHEN_MAX = 160
GENERAL = "general"

MODE_ALL = "all"
MODE_MAIN = "main"
MODE_NAME = "name"


@dataclass(frozen=True)
class Item:
    name: str
    when: str
    category: str = ""
    subcategory: str = ""
    pinned: bool = False
    reads: int = 0


@dataclass
class Group:
    name: str
    count: int
    mode: str
    skills: list[Item] = field(default_factory=list)
    # Whether the group's own page has groups below it (more than 25).
    divided: bool = False

    @property
    def cost(self) -> int:
        return len(self.skills) if self.mode == MODE_ALL else 1 + len(self.skills)


@dataclass
class Page:
    total: int
    flat: list[Item] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    # Groups counted and not named, and skills a flat page left to search.
    more_groups: int = 0
    more_skills: int = 0

    @property
    def pointers(self) -> int:
        return (
            len(self.flat)
            + sum(group.cost for group in self.groups)
            + (1 if self.more_groups else 0)
        )


def when(text: str) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= WHEN_MAX else line[: WHEN_MAX - 1].rstrip() + "…"


def mains(items: Iterable[Item]) -> list[Item]:
    return sorted(items, key=lambda i: (not i.pinned, -i.reads, i.name))


def build(
    items: Iterable[Item],
    group_of: Callable[[Item], str] | None,
    *,
    divisible: bool = True,
) -> Page:
    """The page for these skills. `group_of` None: a page with nothing below
    it. `divisible`: whether a group of more than 25 has groups of its own."""
    everything = sorted(items, key=lambda i: i.name)
    page = Page(total=len(everything))
    if group_of is None:
        page.flat = (
            everything if len(everything) <= CEILING else mains(everything)[: CEILING]
        )
        page.more_skills = len(everything) - len(page.flat)
        return page
    if len(everything) <= FLAT_MAX:
        page.flat = everything
        return page
    by_group: dict[str, list[Item]] = {}
    for item in everything:
        by_group.setdefault((group_of(item) or GENERAL).strip().lower() or GENERAL, []).append(item)
    for name in sorted(by_group):
        members = by_group[name]
        if len(members) <= GROUP_ALL_MAX:
            page.groups.append(Group(name, len(members), MODE_ALL, members))
        elif len(members) <= GROUP_BIG:
            page.groups.append(Group(name, len(members), MODE_MAIN, mains(members)[:MAINS]))
        else:
            page.groups.append(
                Group(name, len(members), MODE_MAIN, mains(members)[:MAINS_BIG], divisible)
            )
    _fit(page)
    return page


def _fit(page: Page) -> None:
    """The ceiling: collapse until the page has at most 50 pointers."""

    def largest(wanted: Callable[[Group], bool]) -> Group | None:
        found = [g for g in page.groups if wanted(g)]
        return max(found, key=lambda g: (g.count, g.name)) if found else None

    while page.pointers > CEILING:
        # 1. The largest group still listed whole becomes a pointer and 3 mains
        #    (only where that is fewer pointers than the list was).
        group = largest(lambda g: g.mode == MODE_ALL and g.count > MAINS + 1)
        if group is not None:
            group.mode = MODE_MAIN
            group.skills = mains(group.skills)[:MAINS]
            continue
        # 2. The largest group loses its main skills: its name and count stay.
        group = largest(lambda g: g.mode == MODE_MAIN and g.skills)
        if group is not None:
            group.mode = MODE_NAME
            group.skills = []
            continue
        group = largest(lambda g: g.mode == MODE_ALL and g.count > 1)
        if group is not None:
            group.mode = MODE_NAME
            group.skills = []
            continue
        # 3. Still too many groups: the smallest are counted, not named.
        keep = sorted(page.groups, key=lambda g: (-g.count, g.name))[: CEILING - 1]
        names = {g.name for g in keep}
        page.more_groups = len(page.groups) - len(keep)
        page.groups = [g for g in page.groups if g.name in names]
        for g in page.groups:
            if g.mode == MODE_ALL:
                g.mode, g.skills = MODE_NAME, []
        break


def main_page(items: Iterable[Item]) -> Page:
    return build(items, lambda i: i.category)


def category_page(items: Iterable[Item], category: str) -> Page:
    wanted = (category or GENERAL).strip().lower()
    members = [i for i in items if ((i.category or GENERAL).strip().lower()) == wanted]
    return build(members, lambda i: i.subcategory, divisible=False)


def subcategory_page(items: Iterable[Item], category: str, subcategory: str) -> Page:
    wanted = (category or GENERAL).strip().lower()
    sub = (subcategory or GENERAL).strip().lower()
    members = [
        i
        for i in items
        if (i.category or GENERAL).strip().lower() == wanted
        and (i.subcategory or GENERAL).strip().lower() == sub
    ]
    return build(members, None)


def render(page: Page, *, level: str = "category", parent: str = "") -> str:
    """The page as the text an agent reads. `level` names what a group is."""
    lines: list[str] = []

    def pointer(item: Item) -> str:
        return f"- {item.name}: {when(item.when)}"

    def call(name: str) -> str:
        if level == "category":
            return f'skills_category("{name}")'
        return f'skills_category("{parent}", "{name}")'

    lines += [pointer(item) for item in page.flat]
    if page.more_skills:
        lines.append(f"({page.more_skills} more here: skills_search finds them.)")
    for group in page.groups:
        if group.mode == MODE_ALL:
            lines.append(f"{group.name} ({group.count}):")
            lines += ["  " + pointer(item) for item in group.skills]
            continue
        tail = ", in groups" if group.divided else ""
        lines.append(f"{group.name} ({group.count} skills{tail}) → {call(group.name)}")
        lines += ["  " + pointer(item) for item in group.skills]
    if page.more_groups:
        what = "categories" if level == "category" else "groups"
        lines.append(f"({page.more_groups} smaller {what} not named: skills_search finds them.)")
    return "\n".join(lines)


__all__ = [
    "CEILING",
    "GENERAL",
    "Group",
    "Item",
    "Page",
    "build",
    "category_page",
    "main_page",
    "mains",
    "render",
    "subcategory_page",
    "when",
]
