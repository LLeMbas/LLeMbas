"""The pointer list's branching rules: every threshold, and the ceiling."""

from __future__ import annotations

from lembas.services.skillstore import pointers as p


def _items(spec: dict[str, int], subs: int = 0) -> list[p.Item]:
    out = []
    for category, count in spec.items():
        for n in range(count):
            out.append(
                p.Item(
                    name=f"{category or 'x'}-{n:03d}",
                    when=f"Use when {category} {n}.",
                    category=category,
                    subcategory=f"s{n % subs}" if subs else "",
                    reads=n,
                )
            )
    return out


def test_up_to_ten_skills_are_one_list():
    page = p.main_page(_items({"a": 4, "b": 6}))
    assert [i.name for i in page.flat] == sorted(i.name for i in page.flat)
    assert len(page.flat) == 10 and page.groups == [] and page.pointers == 10


def test_eleven_skills_are_listed_by_category():
    page = p.main_page(_items({"a": 5, "b": 6}))
    assert page.flat == []
    assert [(g.name, g.mode, len(g.skills)) for g in page.groups] == [
        ("a", p.MODE_ALL, 5),
        ("b", p.MODE_ALL, 6),
    ]


def test_a_category_of_ten_is_whole_and_of_eleven_is_a_pointer_and_three():
    page = p.main_page(_items({"a": 10, "b": 11}))
    a, b = page.groups
    assert (a.mode, len(a.skills)) == (p.MODE_ALL, 10)
    assert (b.mode, len(b.skills), b.divided) == (p.MODE_MAIN, 3, False)
    assert page.pointers == 10 + 1 + 3


def test_a_category_of_twenty_five_keeps_three_and_of_twenty_six_gets_five_and_groups():
    page = p.main_page(_items({"a": 25, "b": 26}))
    a, b = page.groups
    assert (a.mode, len(a.skills), a.divided) == (p.MODE_MAIN, 3, False)
    assert (b.mode, len(b.skills), b.divided) == (p.MODE_MAIN, 5, True)


def test_main_skills_are_the_pinned_then_the_most_read():
    items = _items({"a": 12})
    items[0] = p.Item(name="a-000", when="w", category="a", pinned=True, reads=0)
    page = p.main_page(items)
    assert [i.name for i in page.groups[0].skills] == ["a-000", "a-011", "a-010"]


def test_skills_without_a_category_are_general():
    page = p.main_page(_items({"": 6, "b": 6}))
    assert [g.name for g in page.groups] == ["b", p.GENERAL]


def test_the_ceiling_wins_and_the_largest_category_collapses_first():
    # Six categories of ten: 60 pointers by the rules, more than 50.
    spec = {"a": 10, "b": 10, "c": 10, "d": 10, "e": 10, "f": 9}
    page = p.main_page(_items(spec))
    assert page.pointers <= p.CEILING
    modes = {g.name: g.mode for g in page.groups}
    # The largest (and, among equals, the last by name) went first; the smallest stays whole.
    assert modes["f"] == p.MODE_ALL
    assert modes["e"] == p.MODE_MAIN and len(page.groups[4].skills) == 3
    collapsed = [name for name, mode in modes.items() if mode == p.MODE_MAIN]
    assert collapsed == ["d", "e"], collapsed
    assert page.pointers == 9 + 3 * 10 + 2 * 4


def test_exactly_fifty_pointers_stay_as_they_are():
    page = p.main_page(_items({"a": 10, "b": 10, "c": 10, "d": 10, "e": 10}))
    assert page.pointers == 50
    assert all(g.mode == p.MODE_ALL for g in page.groups)


def test_many_categories_lose_their_main_skills_and_then_their_names():
    page = p.main_page(_items({f"c{n:02d}": 12 for n in range(20)}))
    assert page.pointers <= p.CEILING
    assert any(g.mode == p.MODE_NAME for g in page.groups)
    page = p.main_page(_items({f"c{n:02d}": 2 for n in range(70)}))
    assert page.pointers == p.CEILING
    assert len(page.groups) == 49 and page.more_groups == 21
    assert all(g.mode == p.MODE_NAME for g in page.groups)


def test_a_categorys_page_is_built_by_the_same_rules():
    items = _items({"a": 30}, subs=2) + _items({"b": 3})
    page = p.category_page(items, "a")
    assert page.total == 30
    assert [(g.name, g.count, g.mode, len(g.skills)) for g in page.groups] == [
        ("s0", 15, p.MODE_MAIN, 3),
        ("s1", 15, p.MODE_MAIN, 3),
    ]
    small = p.category_page(items, "b")
    assert len(small.flat) == 3 and small.groups == []
    sub = p.subcategory_page(items, "a", "s1")
    assert sub.total == 15 and len(sub.flat) == 15


def test_a_page_with_nothing_below_it_stops_at_the_ceiling():
    page = p.subcategory_page(_items({"a": 70}), "a", "")
    assert len(page.flat) == p.CEILING and page.more_skills == 20
    assert page.flat[0].name == "a-069"  # the most read first


def test_a_pointer_is_a_name_and_one_bounded_line():
    long = p.Item(name="n", when="word " * 100, category="")
    text = p.render(p.main_page([long]))
    assert text.startswith("- n: word") and "\n" not in text
    assert len(text) <= len("- n: ") + p.WHEN_MAX


def test_rendering_names_the_call_that_opens_a_group():
    text = p.render(p.main_page(_items({"a": 12, "b": 2})))
    assert 'a (12 skills) → skills_category("a")' in text
    assert "b (2):" in text
    page = p.category_page(_items({"a": 30}, subs=2), "a")
    assert 'skills_category("a", "s0")' in p.render(page, level="subcategory", parent="a")
