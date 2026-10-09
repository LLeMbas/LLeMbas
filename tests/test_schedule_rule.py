"""The recurrence rule, on its own.

`rule.py` is pure and total: no session, no wall clock, nothing that raises. So
it is tested exhaustively here, before anything calls it — which is the whole
reason it was built first. Everything downstream of it fails quietly. A schedule
that never fires looks exactly like a working one on every screen it appears on,
and one that fires an hour out looks like nothing at all until the report is
late.

The DST cases are the ones worth reading. They are not hypotheticals: each of
them happens twice a year, on a machine nobody is watching.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from lembas.services.schedule import rule as rule_service

# A zone with an interesting spring and autumn, and one without.
PRAGUE = ZoneInfo("Europe/Prague")
UTC_ZONE = UTC


def at(text: str) -> datetime:
    """A UTC instant from an ISO string, for readable expectations."""
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def local(text: str, zone=PRAGUE) -> datetime:
    """A wall-clock stamp in a zone, as an instant."""
    return datetime.fromisoformat(text).replace(tzinfo=zone).astimezone(UTC)


# --- validate: total, clamping, never raising ---------------------------------
def test_validate_never_raises_on_anything():
    """The compile step hands this whatever a model wrote. Model output that
    becomes a *timer* is the sharpest case of hard rule 6 in the codebase."""
    for junk in (
        None, 0, "", "every monday", [], {}, {"every": "often"},
        {"at": "3pm"}, {"count": "lots"}, {"start": "not a date"},
        {"every": {"minutes": -5}}, {"at": {"weekdays": [9, "x", None]}},
        {"until": "2020-01-01T00:00:00Z", "start": "2026-01-01T00:00:00Z"},
        {"at": {"times": ["25:99", "nope", ""]}},
    ):
        assert isinstance(rule_service.validate(junk), dict)


def test_prose_and_cron_normalise_to_nothing():
    """`{}` is the honest answer, and the caller's cue to show the manual form
    rather than write a schedule that can never fire."""
    assert rule_service.validate("0 3 * * 1") == {}
    assert rule_service.validate({"cron": "0 3 * * 1"}) == {}
    assert rule_service.validate({"every": {"seconds": 5}}) == {}


def test_anything_validate_accepts_can_actually_fire():
    """The flagship invariant. A rule that normalises to something non-empty but
    has no next occurrence is a schedule indistinguishable from a working one on
    the list page — which is this feature's worst silent failure."""
    now = at("2026-08-05T12:00:00")
    candidates = [
        {"start": "2026-08-05T14:30:00Z"},
        {"start": "2026-08-05T14:30:00Z", "every": {"minutes": 10}},
        {"at": {"times": ["15:00"]}},
        {"at": {"weekdays": [0], "times": ["15:00"]}},
        {"at": {"days": [1, 15], "times": ["09:00"]}},
        {"at": {"months": [1, 7], "days": [1], "times": ["00:00"]}},
        {"at": {"weekdays": [0], "times": ["15:00"]}, "count": 5},
        {"start": "2026-08-05T00:00:00Z", "at": {"weekdays": [0], "times": ["15:00"]},
         "every": {"weeks": 2}},
    ]
    for raw in candidates:
        clean = rule_service.validate(raw)
        assert clean, raw
        assert rule_service.next_after(clean, now, zone=PRAGUE) is not None, clean


def test_an_interval_below_a_minute_is_raised_not_honoured():
    """The ticker's granularity is coarser than a second, so honouring it is
    impossible and pretending to would run silently late for ever."""
    clean = rule_service.validate({"start": "2026-08-05T00:00:00Z", "every": {"minutes": 0}})
    assert "every" not in clean

    clean = rule_service.validate(
        {"start": "2026-08-05T00:00:00Z", "every": {"minutes": 1}}
    )
    assert clean["every"] == {"minutes": 1}


def test_a_calendar_with_no_time_gets_midnight():
    """Otherwise "every Monday" means nothing at all, and the whole `at` block
    would have to be discarded."""
    clean = rule_service.validate({"at": {"weekdays": [0]}})
    assert clean["at"]["times"] == ["00:00"]


def test_a_window_that_closes_before_it_opens_is_refused():
    assert rule_service.validate(
        {"start": "2026-08-05T00:00:00Z", "until": "2026-08-01T00:00:00Z",
         "every": {"hours": 1}}
    ) == {}


# --- The four combinations ------------------------------------------------------
def test_a_one_shot_fires_once_and_then_never():
    clean = rule_service.validate({"start": "2026-08-05T14:30:00Z"})
    assert rule_service.next_after(clean, at("2026-08-05T12:00:00"), zone=PRAGUE) == at(
        "2026-08-05T14:30:00"
    )
    # Once it has run, `fired` is what stops it being offered again — its moment
    # is in the past, so nothing else would.
    assert rule_service.next_after(
        clean, at("2026-08-05T12:00:00"), zone=PRAGUE, fired=1
    ) is None
    assert rule_service.next_after(clean, at("2026-08-05T15:00:00"), zone=PRAGUE) is None


def test_a_timer_steps_from_its_start():
    clean = rule_service.validate(
        {"start": "2026-08-05T12:00:00Z", "every": {"minutes": 10}}
    )
    assert rule_service.next_after(clean, at("2026-08-05T11:00:00"), zone=PRAGUE) == at(
        "2026-08-05T12:00:00"
    )
    assert rule_service.next_after(clean, at("2026-08-05T12:00:00"), zone=PRAGUE) == at(
        "2026-08-05T12:10:00"
    )
    assert rule_service.next_after(clean, at("2026-08-05T12:05:00"), zone=PRAGUE) == at(
        "2026-08-05T12:10:00"
    )


def test_a_timer_idle_for_a_year_costs_one_division():
    """Computed rather than stepped. A loop here would spin a hundred thousand
    times inside the ticker for a schedule nobody touched."""
    clean = rule_service.validate(
        {"start": "2020-01-01T00:00:00Z", "every": {"minutes": 1}}
    )
    assert rule_service.next_after(clean, at("2026-08-05T12:00:30"), zone=PRAGUE) == at(
        "2026-08-05T12:01:00"
    )


def test_every_monday_at_three_means_local_three():
    clean = rule_service.validate({"at": {"weekdays": [0], "times": ["15:00"]}})
    # 5 August 2026 is a Wednesday; the next Monday is the 10th.
    found = rule_service.next_after(clean, at("2026-08-05T12:00:00"), zone=PRAGUE)
    assert found == local("2026-08-10T15:00:00")
    assert found.astimezone(PRAGUE).strftime("%A %H:%M") == "Monday 15:00"


def test_several_times_a_day_are_all_taken_in_order():
    clean = rule_service.validate({"at": {"times": ["09:00", "17:00"]}})
    first = rule_service.next_after(clean, local("2026-08-05T08:00:00"), zone=PRAGUE)
    assert first == local("2026-08-05T09:00:00")
    second = rule_service.next_after(clean, first, zone=PRAGUE)
    assert second == local("2026-08-05T17:00:00")
    third = rule_service.next_after(clean, second, zone=PRAGUE)
    assert third == local("2026-08-06T09:00:00")


def test_a_stride_over_a_calendar_keeps_every_nth():
    """"Every other Monday" — and anchored on `start`, so it names the same two
    Mondays whenever it is asked rather than depending on when you looked."""
    clean = rule_service.validate(
        {
            "start": "2026-08-10T00:00:00Z",
            "at": {"weekdays": [0], "times": ["15:00"]},
            "every": {"weeks": 2},
        }
    )
    first = rule_service.next_after(clean, at("2026-08-05T00:00:00"), zone=PRAGUE)
    assert first == local("2026-08-10T15:00:00")
    second = rule_service.next_after(clean, first, zone=PRAGUE)
    assert second == local("2026-08-24T15:00:00")  # not the 17th

    later = rule_service.next_after(clean, local("2026-08-20T00:00:00"), zone=PRAGUE)
    assert later == local("2026-08-24T15:00:00")


def test_a_day_of_month_that_does_not_exist_every_month_still_finds_one():
    """31 is a real answer in January and no answer in February. The search must
    walk on rather than concluding the rule is dead."""
    clean = rule_service.validate({"at": {"days": [31], "times": ["09:00"]}})
    found = rule_service.next_after(clean, local("2026-02-01T00:00:00"), zone=PRAGUE)
    assert found == local("2026-03-31T09:00:00")


def test_an_impossible_calendar_answers_never_rather_than_spinning():
    """31 February matches nothing. Bounded by the search horizon, so the ticker
    cannot be hung by one bad row."""
    clean = rule_service.validate(
        {"at": {"months": [2], "days": [31], "times": ["09:00"]}}
    )
    assert rule_service.next_after(clean, at("2026-08-05T00:00:00"), zone=PRAGUE) is None


# --- Daylight saving ------------------------------------------------------------
def test_a_wall_clock_time_survives_spring_forward():
    """29 March 2026, Prague: 02:00 becomes 03:00 and 02:30 does not exist.

    A daily 02:30 report vanishing once a year, on a schedule nobody is
    watching, is exactly the failure this whole module is arranged around. It
    fires at the first instant that does exist instead.
    """
    clean = rule_service.validate({"at": {"times": ["02:30"]}})
    found = rule_service.next_after(clean, local("2026-03-29T00:00:00"), zone=PRAGUE)

    assert found is not None
    assert found.astimezone(PRAGUE).date().isoformat() == "2026-03-29"
    # Exactly the first minute that exists, not "somewhere after". Left to
    # zoneinfo's own resolution this reads 02:30+01:00 — an hour later as an
    # instant, and a wall-clock time that did not happen.
    assert found.astimezone(PRAGUE).strftime("%H:%M") == "03:00"


def test_a_wall_clock_time_fires_once_across_fall_back():
    """25 October 2026, Prague: 02:00–03:00 happens twice. Once, not twice."""
    clean = rule_service.validate({"at": {"times": ["02:30"]}})
    first = rule_service.next_after(clean, local("2026-10-24T12:00:00"), zone=PRAGUE)
    assert first.astimezone(PRAGUE).date().isoformat() == "2026-10-25"

    # The next occurrence is the following day, not the repeat of the same hour.
    second = rule_service.next_after(clean, first, zone=PRAGUE)
    assert second.astimezone(PRAGUE).date().isoformat() == "2026-10-26"
    # 25 real hours between them, because the clock went back in between. Taking
    # the second 02:30 instead would make this one hour.
    assert second - first == timedelta(hours=25)


def test_a_daily_calendar_holds_its_wall_clock_across_a_boundary():
    """The half that people mean by "every day at 9": 09:00 stays 09:00, and the
    real interval between two firings is 23 or 25 hours."""
    clean = rule_service.validate({"at": {"times": ["09:00"]}})
    before = rule_service.next_after(clean, local("2026-03-28T00:00:00"), zone=PRAGUE)
    after = rule_service.next_after(clean, before, zone=PRAGUE)

    assert before.astimezone(PRAGUE).hour == 9
    assert after.astimezone(PRAGUE).hour == 9
    assert after - before == timedelta(hours=23)


def test_a_timer_holds_its_interval_across_a_boundary():
    """The other half, and the opposite behaviour on purpose: six hours is six
    hours, so a 23-hour day must not shift or double it."""
    clean = rule_service.validate(
        {"start": "2026-03-28T00:00:00Z", "every": {"hours": 6}}
    )
    cursor = local("2026-03-28T12:00:00")
    steps = []
    for _ in range(6):
        cursor = rule_service.next_after(clean, cursor, zone=PRAGUE)
        steps.append(cursor)

    assert all(b - a == timedelta(hours=6) for a, b in zip(steps, steps[1:], strict=False))


# --- Exhaustion -----------------------------------------------------------------
def test_a_count_is_spent_and_then_it_is_over():
    """"Five times" meaning "for ever" is the failure. Exhaustion returns None,
    which is the caller's cue to disable rather than to loop."""
    clean = rule_service.validate(
        {"start": "2026-08-05T12:00:00Z", "every": {"minutes": 10}, "count": 3}
    )
    now = at("2026-08-05T11:00:00")
    assert rule_service.next_after(clean, now, zone=PRAGUE, fired=2) is not None
    assert rule_service.next_after(clean, now, zone=PRAGUE, fired=3) is None
    assert rule_service.next_after(clean, now, zone=PRAGUE, fired=99) is None


def test_an_until_closes_the_window():
    clean = rule_service.validate(
        {
            "start": "2026-08-05T12:00:00Z",
            "every": {"days": 1},
            "until": "2026-08-08T00:00:00Z",
        }
    )
    assert rule_service.next_after(clean, at("2026-08-06T00:00:00"), zone=PRAGUE) == at(
        "2026-08-06T12:00:00"
    )
    assert rule_service.next_after(clean, at("2026-08-08T00:00:00"), zone=PRAGUE) is None


# --- Catching up ----------------------------------------------------------------
def test_a_missed_run_collapses_to_one():
    """A host off for a week comes back owing one report, not a hundred and
    sixty-eight. This is the whole reason `advance` is not just `next_after`."""
    clean = rule_service.validate(
        {"start": "2026-08-01T00:00:00Z", "every": {"hours": 1}}
    )
    due, following = rule_service.advance(
        clean,
        after=at("2026-08-01T00:00:00"),
        now=at("2026-08-08T00:30:00"),
        zone=PRAGUE,
    )
    assert due is True
    # The next one is measured from now, not from the slot that was missed.
    assert following == at("2026-08-08T01:00:00")


def test_a_schedule_not_yet_due_is_left_alone():
    clean = rule_service.validate(
        {"start": "2026-08-05T12:00:00Z", "every": {"hours": 1}}
    )
    due, following = rule_service.advance(
        clean,
        after=at("2026-08-05T12:00:00"),
        now=at("2026-08-05T12:30:00"),
        zone=PRAGUE,
    )
    assert due is False
    assert following == at("2026-08-05T13:00:00")


def test_catching_up_the_last_of_a_count_leaves_nothing_behind():
    """The firing being caught up counts, so `count` is spent by what actually
    ran rather than by what was scheduled."""
    clean = rule_service.validate(
        {"start": "2026-08-01T00:00:00Z", "every": {"hours": 1}, "count": 3}
    )
    due, following = rule_service.advance(
        clean, after=at("2026-08-01T02:00:00"), now=at("2026-08-08T00:00:00"),
        zone=PRAGUE, fired=2,
    )
    assert due is True
    assert following is None


def test_a_missed_one_shot_still_fires():
    """A reminder is not less wanted for being late."""
    clean = rule_service.validate({"start": "2026-08-01T09:00:00Z"})
    due, following = rule_service.advance(
        clean, after=at("2026-07-31T00:00:00"), now=at("2026-08-08T00:00:00"), zone=PRAGUE
    )
    assert due is True
    assert following is None


# --- Saying it back --------------------------------------------------------------
def test_describe_says_what_the_rule_actually_does():
    """A row reading "Every Monday at 3PM" over a rule that fires daily is the
    same class of failure as three places disagreeing about a tool's name — and
    this is the reader's only view of something that happens while they are not
    looking."""
    cases = [
        ({"at": {"weekdays": [0], "times": ["15:00"]}}, "Every Monday at 15:00"),
        ({"at": {"times": ["09:00"]}}, "Every day at 09:00"),
        ({"at": {"times": ["09:00", "17:00"]}}, "Every day at 09:00 and 17:00"),
        ({"start": "2026-08-05T12:00:00Z", "every": {"minutes": 10}}, "Every 10 minutes"),
        ({"start": "2026-08-05T12:00:00Z", "every": {"hours": 6}}, "Every 6 hours"),
        (
            {"start": "2026-08-05T12:00:00Z", "every": {"minutes": 10}, "count": 5},
            "Every 10 minutes, 5 times",
        ),
    ]
    for raw, expected in cases:
        assert rule_service.describe(rule_service.validate(raw), zone=PRAGUE) == expected


def test_describe_reads_as_english_for_what_a_model_actually_writes():
    """Every case below is a rule a 4B model produced from a plain request, and
    the first two used to read "Every the 1st at 09:00" and "Every of January".

    Worth pinning as *wording*, which is not a thing tests usually assert. This
    string is the whole of what the reader sees before approving a schedule and
    the whole of what the model is told about its own chat — so a phrase nobody
    can parse is a review step nobody performs.
    """
    cases = [
        # "on the 1st of every month, write up what changed"
        ({"at": {"days": [1], "times": ["09:00"]}}, "On the 1st of each month at 09:00"),
        (
            {"at": {"days": [1, 15], "times": ["09:00"]}},
            "On the 1st and 15th of each month at 09:00",
        ),
        # "every weekday at 8am give me a briefing" — five names in a row is the
        # commonest thing this produces otherwise.
        ({"at": {"weekdays": [0, 1, 2, 3, 4], "times": ["08:00"]}}, "Every weekday at 08:00"),
        (
            {"at": {"weekdays": [5, 6], "times": ["10:00"]}},
            "Every Saturday and Sunday at 10:00",
        ),
        # Naming all seven is no constraint at all, and saying so is how "every
        # day" comes out of a rule that enumerated them.
        (
            {"at": {"weekdays": [0, 1, 2, 3, 4, 5, 6], "times": ["09:00"]}},
            "Every day at 09:00",
        ),
        ({"at": {"months": [1, 7], "times": ["09:00"]}}, "Every day in January and July at 09:00"),
        (
            {"at": {"months": [1], "days": [1], "times": ["00:00"]}},
            "On the 1st of January at 00:00",
        ),
        # Both set is an AND, and rare. Said plainly rather than smoothed into
        # something that reads like an OR.
        (
            {"at": {"weekdays": [0], "days": [1], "times": ["09:00"]}},
            "On the 1st of each month, if it is a Monday at 09:00",
        ),
    ]
    for raw, expected in cases:
        assert rule_service.describe(rule_service.validate(raw), zone=PRAGUE) == expected


def test_describe_survives_an_empty_rule():
    assert rule_service.describe({}, zone=PRAGUE) == "Never"
    assert rule_service.describe(rule_service.validate("nonsense"), zone=PRAGUE) == "Never"


def test_describe_names_the_one_shot_moment_in_the_readers_zone():
    clean = rule_service.validate({"start": "2026-08-05T13:00:00Z"})
    assert "15:00" in rule_service.describe(clean, zone=PRAGUE)
    assert "13:00" in rule_service.describe(clean, zone=UTC_ZONE)
