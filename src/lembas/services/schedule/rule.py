"""When a schedule next comes due.

Pure and total. Nothing here opens a session, reads the wall clock or raises:
every function takes what it needs and answers, so the whole of this module can
be tested exhaustively before anything calls it. That is deliberate, because
everything downstream fails *quietly* -- a schedule that never fires looks
exactly like a working one on the list page, and a schedule that fires an hour
out looks like nothing at all until somebody notices the report is late.

## The shape

Plain cron cannot say "ten minutes from now, five times", so the rule is a dict
with two independent generators and a bound:

    {
      "start": "2026-08-05T14:30:00Z",     # first candidate instant, UTC
      "every": {"minutes": 10},            # a stride
      "at":    {"weekdays": [0],           # 0 = Monday
                "days":     [1, 15],       # day of the month
                "months":   [1, 4, 7, 10],
                "times":    ["15:00"]},    # wall-clock, in the owner's zone
      "count": 5,                          # total firings, 0 = unbounded
      "until": "2026-12-31T00:00:00Z"      # last instant, "" = unbounded
    }

`every` and `at` compose, and the four combinations are the whole vocabulary:

    every  at   meaning
    ----- ----  ------------------------------------------------------------
      -    -    fire once, at `start`
      x    -    a timer: start, start + every, start + 2*every, ...
      -    x    a calendar: every matching wall-clock moment after `start`
      x    x    a calendar with a stride: matching moments, every Nth kept

## Timezone, and why the two halves differ

`at.times` are **wall-clock** in the owner's zone: 15:00 stays 15:00 across a
DST change, because that is what "every Monday at 3PM" means to the person who
said it. `every` durations are **elapsed real time**: ten minutes is ten
minutes, and a six-hourly timer must not skip or double on a 23- or 25-hour day.
Those are different meanings, not an inconsistency, and conflating them is how
one of the two comes out wrong twice a year.

A wall-clock time that does not exist (the hour skipped on a spring-forward day)
fires at the first instant that does, rather than being skipped -- a daily report
vanishing once a year is precisely the silent failure this file exists to avoid.
One that occurs twice on a fall-back day fires on the first, once.

## Not expressible

Said plainly, because the gap is the point: "the last Friday of the month", "the
third Monday", "weekdays except holidays", "the Nth business day", sub-minute
intervals, sunrise-relative times, and any conditional firing ("only if the
build is red"). The first two are what people will actually ask for; the rule is
JSON, so an `nth` key inside `at` adds them later with no migration.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, tzinfo

log = logging.getLogger(__name__)

# The stride units, and how many seconds each is worth. Months are absent on
# purpose: a month is not a duration, and "every month" is `at: {days: [n]}`,
# which is what somebody means by it.
UNITS: dict[str, int] = {
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
    "weeks": 604800,
}

# Bounds. Every one of these is a clamp rather than a rejection, because the
# rule can arrive from a *model* -- the compile step's output is model output
# that becomes a timer, and `validate` is this feature's `nh3.clean`.
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 366 * 86400
MAX_COUNT = 10_000
MAX_TIMES = 24
MAX_HORIZON_DAYS = 366 * 5

# How far ahead a calendar search will walk before giving up. A rule asking for
# 31 February matches nothing, and a search with no bound would spin for ever
# inside the ticker. Days rather than iterations, so the limit is a statement
# about the schedule rather than about the loop.
SEARCH_DAYS = 366 * 4

WEEKDAYS = (0, 1, 2, 3, 4, 5, 6)


# --- Reading a rule ------------------------------------------------------------
def _int(value: object, *, low: int, high: int, default: int = 0) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def _stamp(value: object) -> datetime | None:
    """An ISO instant, or None. Naive input is read as UTC.

    `fromisoformat` handles a trailing Z from Python 3.11, but a model writes
    all sorts of things, so anything unparseable is simply absent.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _times(value: object) -> list[tuple[int, int]]:
    """Wall-clock times as (hour, minute), sorted and deduplicated.

    Accepts "15:00", "15:00:30" and "9:5", because a model writes all three, and
    a rule refused for its punctuation is a round trip spent on nothing.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    found: set[tuple[int, int]] = set()
    for item in list(value)[:MAX_TIMES]:
        if not isinstance(item, str) or ":" not in item:
            continue
        hour, _, rest = item.strip().partition(":")
        minute = rest.partition(":")[0]
        try:
            pair = (int(hour), int(minute))
        except ValueError:
            continue
        if 0 <= pair[0] <= 23 and 0 <= pair[1] <= 59:
            found.add(pair)
    return sorted(found)


def _numbers(value: object, *, low: int, high: int) -> list[int]:
    if isinstance(value, int) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    found: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            number = int(item)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if low <= number <= high:
            found.add(number)
    return sorted(found)


def _every(value: object) -> dict[str, int]:
    """A stride, clamped to something that can actually be run.

    An interval under a minute is refused rather than clamped to a minute: the
    ticker's own granularity is coarser than that, so honouring it is impossible
    and pretending to would be a schedule that silently runs late for ever.
    Clamped up, because "every 10 seconds" from a model means "often", and often
    is a minute.
    """
    if not isinstance(value, dict):
        return {}
    seconds = 0
    for unit, size in UNITS.items():
        seconds += _int(value.get(unit), low=0, high=MAX_INTERVAL_SECONDS) * size
    if seconds <= 0:
        return {}
    seconds = max(MIN_INTERVAL_SECONDS, min(seconds, MAX_INTERVAL_SECONDS))
    return {"minutes": seconds // 60}


def validate(rule: object) -> dict:
    """Normalise a rule, or return {} for one that cannot be made sense of.

    **Total on purpose.** The compile step hands this whatever a model wrote, so
    it drops what it does not recognise and clamps what it does, and never
    raises. `{}` is the honest answer for prose, for a cron string, for an empty
    object -- and the caller's job is then to show the manual form rather than
    write a schedule that never fires. A schedule that can never fire is
    indistinguishable from a working one on every screen it appears on, which is
    this feature's flagship silent failure.

    The invariant worth holding on to, and pinned in the tests: **anything this
    returns non-empty has a computable next occurrence.**
    """
    if not isinstance(rule, dict):
        return {}

    every = _every(rule.get("every"))
    raw_at = rule.get("at") if isinstance(rule.get("at"), dict) else {}
    at = {
        "weekdays": _numbers(raw_at.get("weekdays"), low=0, high=6),
        "days": _numbers(raw_at.get("days"), low=1, high=31),
        "months": _numbers(raw_at.get("months"), low=1, high=12),
        "times": [f"{hour:02d}:{minute:02d}" for hour, minute in _times(raw_at.get("times"))],
    }
    # A calendar with no time of day has no time of day. Midnight is the only
    # defensible reading and it is what every cron-like thing does, so it is
    # filled in rather than making the whole `at` block meaningless.
    if any(at[key] for key in ("weekdays", "days", "months")) and not at["times"]:
        at["times"] = ["00:00"]
    if not at["times"]:
        at = {}

    start = _stamp(rule.get("start"))
    until = _stamp(rule.get("until"))
    count = _int(rule.get("count"), low=0, high=MAX_COUNT)

    # A one-shot is `start` and nothing else, so without a start there is
    # nothing to fire and nothing to infer -- unlike a calendar, which is
    # perfectly meaningful from now onwards.
    if not every and not at and start is None:
        return {}
    # A window that closes before it opens produces nothing, which is a rule
    # that cannot fire rather than one that fires oddly.
    if start is not None and until is not None and until < start:
        return {}

    normalised: dict = {}
    if start is not None:
        normalised["start"] = start.astimezone(UTC).isoformat()
    if every:
        normalised["every"] = every
    if at:
        normalised["at"] = {key: value for key, value in at.items() if value}
        normalised["at"]["times"] = at["times"]
    if count:
        normalised["count"] = count
    if until is not None:
        normalised["until"] = until.astimezone(UTC).isoformat()
    return normalised


# --- When it next comes due -----------------------------------------------------
def _interval(rule: dict) -> timedelta:
    return timedelta(minutes=int((rule.get("every") or {}).get("minutes") or 0))


def _matches(moment: datetime, at: dict) -> bool:
    """Whether a local date satisfies the calendar constraints.

    Empty means "every", per field, which is what makes `{"times": ["09:00"]}`
    read as "daily at nine" without having to enumerate seven weekdays.
    """
    weekdays = at.get("weekdays") or []
    days = at.get("days") or []
    months = at.get("months") or []
    if weekdays and moment.weekday() not in weekdays:
        return False
    if days and moment.day not in days:
        return False
    return not (months and moment.month not in months)


def _wall(day: datetime, hour: int, minute: int, zone: tzinfo) -> datetime:
    """A wall-clock time on a given local day, as an instant.

    Two DST cases, both handled here rather than left to `zoneinfo`'s defaults:

    - **The hour that does not exist.** On a spring-forward day, 02:30 is not a
      time. Constructing it anyway yields something that does not round-trip, so
      the gap is detected by comparing and the result is pushed to the first
      instant that does exist. Skipping the day instead is how a daily report
      disappears once a year.
    - **The hour that happens twice.** `fold=0` picks the first, and the
      advance-past-the-last-fire rule upstream is what stops the second being
      taken as a separate occurrence.
    """
    naive = day.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=None)
    local = naive.replace(tzinfo=zone, fold=0)
    # A time inside the spring-forward gap does not survive the round trip.
    if local.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive:
        # Walk forward a minute at a time to the far side of the gap. Gaps are
        # an hour at most in every zone the database has ever carried, so this
        # is bounded and cheap; adding the offset difference directly would
        # assume the size of a gap this code has no business knowing.
        for extra in range(1, 181):
            candidate = (naive + timedelta(minutes=extra)).replace(tzinfo=zone, fold=0)
            round_trip = candidate.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
            if round_trip == naive + timedelta(minutes=extra):
                return candidate.astimezone(UTC)
    return local.astimezone(UTC)


def _calendar_after(rule: dict, after: datetime, *, zone: tzinfo) -> datetime | None:
    """The first calendar occurrence strictly after `after`."""
    at = rule.get("at") or {}
    times = [tuple(int(part) for part in value.split(":")) for value in at.get("times") or []]
    if not times:
        return None

    local = after.astimezone(zone)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    for _ in range(SEARCH_DAYS):
        if _matches(day, at):
            for hour, minute in times:
                moment = _wall(day, hour, minute, zone)
                if moment > after:
                    return moment
        day += timedelta(days=1)
        # Re-anchor to local midnight: adding a day across a DST boundary
        # otherwise leaves the cursor an hour either side of it, and the day
        # after a fall-back would be searched from 23:00 the previous evening.
        day = day.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    return None


def _exhausted(rule: dict, moment: datetime, fired: int) -> bool:
    count = int(rule.get("count") or 0)
    if count and fired >= count:
        return True
    until = _stamp(rule.get("until"))
    return bool(until and moment > until)


def next_after(
    rule: dict, after: datetime, *, zone: tzinfo, fired: int = 0
) -> datetime | None:
    """The next instant this rule comes due, strictly after `after`.

    `None` means never again: the count is spent, the window has closed, or the
    calendar matches nothing inside the search horizon. A caller seeing `None`
    disables the schedule -- exhaustion switches off, it does not loop.

    `fired` is how many times it has already run, and is what makes `count`
    work without the rule having to carry mutable state.
    """
    if not isinstance(rule, dict) or not rule:
        return None
    count = int(rule.get("count") or 0)
    if count and fired >= count:
        return None

    after = after.astimezone(UTC)
    start = _stamp(rule.get("start"))
    every = _interval(rule)
    at = rule.get("at") or {}

    moment: datetime | None
    if at:
        # A calendar never fires before its start, so the search begins at
        # whichever of the two is later.
        floor = max(after, start - timedelta(microseconds=1)) if start else after
        moment = _calendar_after(rule, floor, zone=zone)
        if moment is not None and every:
            # A stride over a calendar keeps every Nth match. Counted from the
            # start rather than from `after`, so "every other Monday" means the
            # same two Mondays whenever it is asked.
            stride = max(1, int(round(every.total_seconds() / 86400)) or 1)
            if stride > 1 and start is not None:
                elapsed = (moment.astimezone(zone).date() - start.astimezone(zone).date()).days
                skipped = 0
                while elapsed % stride and skipped < SEARCH_DAYS:
                    moment = _calendar_after(rule, moment, zone=zone)
                    if moment is None:
                        break
                    elapsed = (
                        moment.astimezone(zone).date() - start.astimezone(zone).date()
                    ).days
                    skipped += 1
    elif every:
        if start is None:
            return None
        if after < start:
            moment = start
        else:
            # Absolute arithmetic, deliberately: a timer measures elapsed time,
            # so it must not shift when the offset does. Computed rather than
            # stepped, so a schedule idle for a year costs one division.
            elapsed = (after - start).total_seconds()
            steps = int(elapsed // every.total_seconds()) + 1
            moment = start + every * steps
    else:
        # A one-shot. Due exactly once, and only if it has not already run --
        # `fired` is what stops it being re-offered for ever once its moment has
        # passed, since `start > after` is false from then on.
        if start is None or fired:
            return None
        moment = start if start > after else None

    if moment is None or _exhausted(rule, moment, fired):
        return None
    return moment


def advance(
    rule: dict, *, after: datetime, now: datetime, zone: tzinfo, fired: int = 0
) -> tuple[bool, datetime | None]:
    """Catch up on a schedule whose time passed while nothing was running.

    Answers two things at once: whether it is owed a firing *now*, and when it
    should next come due. The pair is one function because the second depends on
    the first -- a caller that asked separately would have to decide what
    "next" means for a schedule it has just decided to fire.

    **A missed run collapses to one.** The next occurrence returned is the first
    one strictly after `now`, not the one after the slot that was missed -- so a
    host switched off for a week comes back owing one report rather than a
    hundred and sixty-eight. That is the whole reason this is not just
    `next_after`.

    It is called from the *sweep* rather than only at startup, because a
    suspended laptop, a paused container and a long stall all reproduce the
    same situation with no restart to hang a startup hook on.
    """
    due = next_after(rule, after, zone=zone, fired=fired)
    if due is None:
        return False, None
    if due > now:
        return False, due
    # Overdue. Fire once, and resume from wherever the rule is now -- counting
    # this firing, so `count` is spent by what actually ran.
    return True, next_after(rule, now, zone=zone, fired=fired + 1)


# --- Saying it back -------------------------------------------------------------
_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _join(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        return f"{number}th"
    return f"{number}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th') }"


def _duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    for size, unit in ((10080, "week"), (1440, "day"), (60, "hour"), (1, "minute")):
        if minutes >= size and not minutes % size:
            amount = minutes // size
            return f"{amount} {unit}{'s' if amount != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def _weekday_phrase(days: list[int]) -> str:
    """Weekdays as somebody would say them, or "" for no constraint.

    Monday-to-Friday collapses because that is what a person means and what a
    model writes when they say "every weekday" -- and five names in a row is the
    commonest thing this function produces otherwise. All seven is no constraint
    at all, and saying so is how "every day" comes out of a rule that named them.
    """
    chosen = set(days or [])
    if not chosen or chosen == set(WEEKDAYS):
        return ""
    if chosen == {0, 1, 2, 3, 4}:
        return "weekday"
    return _join([_DAY_NAMES[day] for day in sorted(chosen)])


def _calendar_phrase(at: dict) -> str:
    """How often a calendar rule comes round, in words that parse.

    Worth the length. This is what the setup screen echoes back before anything
    is saved, what the list page shows beside each schedule, and what the model
    is told about its own chat -- so it is the reader's only view of a decision
    taken while they were not looking. It used to build a phrase by joining
    fragments, which read "Every the 1st at 09:00" for the single commonest
    monthly schedule there is, and "Every of January" for a month with no day.
    A row nobody can parse is one nobody checks.
    """
    weekdays = _weekday_phrase(at.get("weekdays") or [])
    days = at.get("days") or []
    months = at.get("months") or []
    month_names = _join([_MONTH_NAMES[month - 1] for month in months])

    if days:
        # A day of the month is the subject; the month, if any, qualifies it.
        where = month_names or "each month"
        lead = f"On the {_join([_ordinal(day) for day in days])} of {where}"
        # Both set is an AND and is rare. Said plainly rather than smoothed into
        # something that reads like an OR.
        return f"{lead}, if it is a {weekdays}" if weekdays else lead

    if weekdays == "weekday":
        lead = "Every weekday"
    elif weekdays:
        lead = f"Every {weekdays}"
    else:
        lead = "Every day"
    return f"{lead} in {month_names}" if month_names else lead


def describe(rule: dict, *, zone: tzinfo) -> str:
    """One line saying what this rule does, in the reader's own zone.

    Not decoration. It is what the setup screen echoes back before anything is
    saved, what the list page shows beside each schedule, and what the harness
    tells a model about its own chat. A row reading "Every Monday at 3PM" over a
    rule that fires daily is the same class of failure as three places
    disagreeing about a tool's name -- and this one is the reader's only view of
    a decision that happens while they are not looking.
    """
    rule = rule or {}
    if not rule:
        return "Never"

    at = rule.get("at") or {}
    every = _interval(rule)
    parts: list[str] = []

    if at:
        parts.append(f"{_calendar_phrase(at)} at {_join(list(at.get('times') or []))}")
        # A stride over a calendar is a qualifier rather than a rewording:
        # "Every Monday at 15:00, skipping to every 14 days" is clumsy but true,
        # and inventing "every other Monday" for it would be a phrase that stops
        # being true the moment the stride is not two.
        stride_days = int(every.total_seconds() // 86400) if every else 0
        if stride_days > 1:
            parts.append(f"but only every {stride_days} days")
    elif every:
        parts.append(f"Every {_duration(every)}")
    else:
        start = _stamp(rule.get("start"))
        local = start.astimezone(zone) if start else None
        return f"Once, on {local.strftime('%-d %B %Y at %H:%M')}" if local else "Once"

    count = int(rule.get("count") or 0)
    if count:
        parts.append(f"{count} time{'s' if count != 1 else ''}")
    until = _stamp(rule.get("until"))
    if until:
        parts.append(f"until {until.astimezone(zone).strftime('%-d %B %Y')}")

    return ", ".join(parts)
