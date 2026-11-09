"""`at.nth` -- "the second Monday", "the last Friday", "the last day of the
month" -- and the next-runs preview every form shows before saving.

Checked exhaustively against `calendar`, which is an independent statement of
the same facts: every weekday, every count from the first to the fifth and from
the last to the fifth from last, every month of eight years (leap years and a
February that starts on each weekday among them).
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from lembas.services.schedule import rule as rule_service

PRAGUE = ZoneInfo("Europe/Prague")
YEARS = range(2024, 2032)


def _expected(year: int, month: int, n: int, weekday: int | None) -> date | None:
    """The day an nth entry names, worked out with `calendar` alone."""
    length = calendar.monthrange(year, month)[1]
    if weekday is None:
        day = length + 1 + n
        return date(year, month, day) if 1 <= day <= length else None
    days = [d for d in range(1, length + 1) if date(year, month, d).weekday() == weekday]
    index = n - 1 if n > 0 else n
    try:
        return date(year, month, days[index])
    except IndexError:
        return None


def _matching_days(year: int, month: int, entry: dict) -> list[date]:
    length = calendar.monthrange(year, month)[1]
    return [
        date(year, month, d)
        for d in range(1, length + 1)
        if rule_service.nth_matches(datetime(year, month, d), entry)
    ]


@pytest.mark.parametrize("weekday", range(7))
@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, -1, -2, -3, -4, -5])
def test_every_weekday_count_in_every_month(weekday, n):
    entry = {"n": n, "weekday": weekday}
    for year in YEARS:
        for month in range(1, 13):
            want = _expected(year, month, n, weekday)
            assert _matching_days(year, month, entry) == ([want] if want else []), (
                year, month, entry,
            )


@pytest.mark.parametrize("n", range(-31, 0))
def test_counting_back_from_the_end_of_every_month(n):
    entry = {"n": n}
    for year in YEARS:
        for month in range(1, 13):
            want = _expected(year, month, n, None)
            assert _matching_days(year, month, entry) == ([want] if want else [])


def test_the_last_day_of_february_in_a_leap_year_and_not():
    rule = rule_service.validate({"at": {"nth": [{"n": -1}], "times": ["12:00"]}})
    runs = rule_service.upcoming(
        rule, now=datetime(2027, 1, 31, 13, tzinfo=UTC), zone=UTC, count=14
    )
    februaries = [r.date() for r in runs if r.month == 2]
    assert februaries == [date(2027, 2, 28), date(2028, 2, 29)]


def test_a_fifth_weekday_skips_the_months_without_one():
    rule = rule_service.validate({"at": {"nth": [{"n": 5, "weekday": 4}], "times": ["09:00"]}})
    runs = rule_service.upcoming(rule, now=datetime(2026, 1, 1, tzinfo=UTC), zone=UTC, count=6)
    for run in runs:
        assert run.weekday() == 4 and run.day >= 29
    # Every month in between had four Fridays.
    for earlier, later in zip(runs, runs[1:], strict=False):
        gap_months = (later.year - earlier.year) * 12 + later.month - earlier.month
        for k in range(1, gap_months):
            year = earlier.year + (earlier.month - 1 + k) // 12
            month = (earlier.month - 1 + k) % 12 + 1
            assert _expected(year, month, 5, 4) is None


def test_several_entries_are_any_of_them():
    rule = rule_service.validate(
        {"at": {"nth": [{"n": 1, "weekday": 0}, {"n": -1, "weekday": 4}], "times": ["08:00"]}}
    )
    runs = rule_service.upcoming(rule, now=datetime(2026, 11, 1, tzinfo=UTC), zone=UTC, count=4)
    assert [r.date() for r in runs] == [
        date(2026, 11, 2), date(2026, 11, 27), date(2026, 12, 7), date(2026, 12, 25),
    ]


def test_months_still_narrow_an_nth_rule():
    rule = rule_service.validate(
        {"at": {"nth": [{"n": 2, "weekday": 1}], "months": [3, 9], "times": ["10:00"]}}
    )
    runs = rule_service.upcoming(rule, now=datetime(2026, 1, 1, tzinfo=UTC), zone=UTC, count=3)
    assert [r.date() for r in runs] == [date(2026, 3, 10), date(2026, 9, 8), date(2027, 3, 9)]


def test_nth_drops_weekdays_and_dates():
    """Two ways of naming the day, combined, is a rule nobody can read back."""
    rule = rule_service.validate(
        {"at": {"nth": [{"n": 2, "weekday": 0}], "weekdays": [3], "days": [1], "times": ["9:00"]}}
    )
    assert rule["at"] == {"nth": [{"n": 2, "weekday": 0}], "times": ["09:00"]}


def test_nth_without_a_time_is_midnight():
    rule = rule_service.validate({"at": {"nth": [{"n": -1}]}})
    assert rule["at"]["times"] == ["00:00"]


@pytest.mark.parametrize(
    "junk",
    [
        "second monday", 7, None, [None], [{"n": 0, "weekday": 0}], [{"n": 6, "weekday": 0}],
        [{"n": -6, "weekday": 2}], [{"n": 2, "weekday": 7}], [{"n": 2, "weekday": True}],
        [{"n": 1}], [{"n": -32}], [{"n": "x"}], [{"weekday": 3}], [{"n": 2, "weekday": "monday"}],
    ],
)
def test_nth_junk_is_dropped_never_raised(junk):
    rule = rule_service.validate({"at": {"nth": junk, "times": ["09:00"]}})
    assert "nth" not in rule.get("at", {})


def test_nth_numbers_as_strings_are_read():
    rule = rule_service.validate({"at": {"nth": [{"n": "2", "weekday": "0"}], "times": ["9:00"]}})
    assert rule["at"]["nth"] == [{"n": 2, "weekday": 0}]


def test_nth_duplicates_collapse():
    rule = rule_service.validate(
        {"at": {"nth": [{"n": -1}, {"n": -1}, {"n": "-1"}], "times": ["9:00"]}}
    )
    assert rule["at"]["nth"] == [{"n": -1}]


@pytest.mark.parametrize(
    ("nth", "words"),
    [
        ([{"n": 2, "weekday": 0}], "On the second Monday of each month at 09:00"),
        ([{"n": -1, "weekday": 4}], "On the last Friday of each month at 09:00"),
        ([{"n": -1}], "On the last day of each month at 09:00"),
        ([{"n": -2, "weekday": 6}], "On the second from last Sunday of each month at 09:00"),
        ([{"n": -3}], "On the 3rd day from the end of each month at 09:00"),
        (
            [{"n": 1, "weekday": 0}, {"n": 3, "weekday": 0}],
            "On the first Monday and third Monday of each month at 09:00",
        ),
    ],
)
def test_nth_described(nth, words):
    rule = rule_service.validate({"at": {"nth": nth, "times": ["09:00"]}})
    assert rule_service.describe(rule, zone=UTC) == words


def test_nth_in_named_months_described():
    rule = rule_service.validate(
        {"at": {"nth": [{"n": -1, "weekday": 4}], "months": [6, 12], "times": ["09:00"]}}
    )
    assert rule_service.describe(rule, zone=UTC) == (
        "On the last Friday of June and December at 09:00"
    )


def test_nth_is_wall_clock_across_dst():
    """The last Sunday of March is the spring-forward day in Europe."""
    rule = rule_service.validate({"at": {"nth": [{"n": -1, "weekday": 6}], "times": ["02:30"]}})
    run = rule_service.next_after(rule, datetime(2026, 3, 1, tzinfo=UTC), zone=PRAGUE)
    # 02:30 does not exist that day; the first instant that does is 03:00 CEST.
    assert run == datetime(2026, 3, 29, 1, 0, tzinfo=UTC)


def test_every_nth_rule_validate_accepts_can_fire():
    now = datetime(2026, 11, 9, tzinfo=UTC)
    for weekday in [None, *range(7)]:
        for n in [1, 2, 3, 4, 5, -1, -2, -5] if weekday is not None else [-1, -15, -31]:
            entry = {"n": n} if weekday is None else {"n": n, "weekday": weekday}
            rule = rule_service.validate({"at": {"nth": [entry], "times": ["09:00"]}})
            assert rule_service.next_after(rule, now, zone=PRAGUE) is not None


# --- The preview --------------------------------------------------------------------
def test_upcoming_lists_the_next_five_in_order():
    rule = rule_service.validate({"start": "2026-11-09T10:00:00Z", "every": {"hours": 6}})
    runs = rule_service.upcoming(rule, now=datetime(2026, 11, 9, 11, tzinfo=UTC), zone=UTC)
    first = datetime(2026, 11, 9, 10, tzinfo=UTC)
    assert runs == [first + timedelta(hours=6 * k) for k in range(1, 6)]


def test_upcoming_stops_at_the_count():
    rule = rule_service.validate(
        {"start": "2026-11-09T10:00:00Z", "every": {"hours": 1}, "count": 3}
    )
    runs = rule_service.upcoming(rule, now=datetime(2026, 11, 9, 9, tzinfo=UTC), zone=UTC)
    assert len(runs) == 3


def test_upcoming_counts_what_has_already_run():
    rule = rule_service.validate(
        {"start": "2026-11-09T10:00:00Z", "every": {"hours": 1}, "count": 3}
    )
    runs = rule_service.upcoming(
        rule, now=datetime(2026, 11, 9, 10, 30, tzinfo=UTC), zone=UTC, fired=1
    )
    assert len(runs) == 2


def test_upcoming_of_a_one_shot_is_one():
    rule = rule_service.validate({"start": "2026-11-10T10:00:00Z"})
    assert rule_service.upcoming(rule, now=datetime(2026, 11, 9, tzinfo=UTC), zone=UTC) == [
        datetime(2026, 11, 10, 10, tzinfo=UTC)
    ]


def test_upcoming_of_nothing_is_empty():
    assert rule_service.upcoming({}, now=datetime(2026, 11, 9, tzinfo=UTC), zone=UTC) == []


def test_period_of_a_timer_and_a_calendar():
    now = datetime(2026, 11, 9, tzinfo=UTC)
    timer = rule_service.validate({"start": "2026-11-09T00:00:00Z", "every": {"minutes": 30}})
    assert rule_service.period(timer, now=now, zone=UTC) == timedelta(minutes=30)
    daily = rule_service.validate({"at": {"times": ["09:00"]}})
    assert rule_service.period(daily, now=now, zone=UTC) == timedelta(days=1)
    once = rule_service.validate({"start": "2026-11-10T00:00:00Z"})
    assert rule_service.period(once, now=now, zone=UTC) is None
