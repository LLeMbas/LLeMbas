"""Whose idea of "now" is in force.

Until schedules existed, nothing here needed a timezone: `harness.py` stamped
`datetime.now().astimezone()` and every reader was told the *server's* idea of
the date. That is harmless when the answer is prose and wrong the moment a
person says "every Monday at 3" and something has to work out when that is.

One resolver, because the model compiling a schedule, the screen echoing it back
and the ticker firing it must agree about what Monday means. A disagreement here
does not raise -- it fires at the wrong time, which is the kind of wrong nobody
can debug from the outside.

Deliberately no new column. The zone lives in `user.settings_json["timezone"]`
beside the theme, empty meaning "whatever the server is set to" -- which is the
honest default for the single-user instance this mostly runs on, and is a real
answer rather than a prompt to go and choose one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from lembas.db.models import User

log = logging.getLogger(__name__)

SETTING_KEY = "timezone"


def server_zone() -> tzinfo:
    """What the machine is set to, as a real tzinfo.

    `astimezone()` on a naive stamp attaches the system zone, which is what the
    harness has always used. Read once per call rather than cached: a host whose
    zone changes under a long-running process is rare, and a cache that gets it
    wrong is worse than the lookup.
    """
    return datetime.now().astimezone().tzinfo or UTC


def known(name: str) -> bool:
    """Whether this is a zone name Python can actually resolve.

    `available_timezones()` reads the system database and is not cheap, so it is
    only consulted for a value that is about to be stored. Everything on the
    read path goes through `zone_for`, which simply falls back.
    """
    return bool(name) and name in available_timezones()


def resolve(name: str) -> tzinfo:
    """A zone by name, falling back to the server's rather than raising.

    A stored name can stop resolving -- the tz database is a system package and
    a zone can be renamed out from under a row. Falling back means a schedule
    fires an hour out at worst; raising means it does not fire at all and the
    ticker logs an exception nobody reads.
    """
    if not name:
        return server_zone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        log.warning("unknown timezone %r, falling back to the server's", name)
        return server_zone()


def name_for(user: User | None) -> str:
    """The stored name, or "" meaning the server's. Never resolved here --
    the settings form wants the raw value so an unset zone shows as unset."""
    if user is None:
        return ""
    return str((user.settings_json or {}).get(SETTING_KEY) or "")


def zone_for(user: User | None) -> tzinfo:
    """The zone a schedule of this person's fires in, and the one the harness
    should tell them the time in."""
    return resolve(name_for(user))


def now_for(user: User | None) -> datetime:
    """Aware, in the reader's zone."""
    return datetime.now(tz=zone_for(user))


def to_utc(moment: datetime, *, zone: tzinfo) -> datetime:
    """A wall-clock stamp in `zone`, as an instant.

    Naive input is *interpreted* in `zone`; aware input is converted, so a
    caller that already knows the offset cannot have it silently reassigned.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return moment.astimezone(UTC)


def as_utc(moment: datetime) -> datetime:
    """An instant, whatever it arrived as.

    SQLite does not store the offset, so a row read back from disk is naive
    while one still in the session's identity map keeps its tzinfo, and
    comparing the two raises -- the same trap `compaction.moment` exists for.
    A naive stamp from the database is UTC by construction, because that is what
    every column here is written with.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
