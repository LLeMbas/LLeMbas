"""Schedules: what should happen later, and where its result goes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.models.data_group import InDataGroup
from lembas.db.types import JSONDict, JSONList

# Where a firing's result is delivered. Chosen per schedule rather than fixed by
# the screen it was made on: Reports has to stay reachable from anywhere, being
# the fallback, and a schedule somebody wants moved from its own chat to Reports
# should not have to be built again.
TARGET_CHAT = "chat"
TARGET_REPORT = "report"
TARGETS = (TARGET_CHAT, TARGET_REPORT)

# Who made it. Kept because "why is this running?" is a question with two very
# different answers, and one of them is "a model decided to".
ORIGIN_USER = "user"
ORIGIN_MODEL = "model"
ORIGINS = (ORIGIN_USER, ORIGIN_MODEL)

# Each run in a chat of its own (listed under the schedule), or every run into
# the one chat that keeps growing. Fresh is the default: a run that reads its
# own earlier turns repeats itself, and a long chat costs more every time.
MODE_FRESH = "fresh"
MODE_CONTINUE = "continue"
MODES = (MODE_FRESH, MODE_CONTINUE)

# When a run's result is worth a notification. `matters` lets the run answer
# `[SILENT]` when there is nothing to report (`services/schedule/notify.py`).
NOTIFY_ALWAYS = "always"
NOTIFY_MATTERS = "matters"
NOTIFY_NEVER = "never"
NOTIFY_POLICIES = (NOTIFY_ALWAYS, NOTIFY_MATTERS, NOTIFY_NEVER)

# A run missed by more than its catch-up window: one catch-up run, or none.
CATCH_UP_ONE = "one"
CATCH_UP_SKIP = "skip"
CATCH_UPS = (CATCH_UP_ONE, CATCH_UP_SKIP)

# A run's status (`ScheduleRun.status`).
RUN_RUNNING = "running"
RUN_WAITING = "waiting"
RUN_OK = "ok"
RUN_SILENT = "silent"
RUN_FAILED = "failed"
RUN_SKIPPED = "skipped"
RUN_MISSED = "missed"
# A monitor schedule's look found nothing changed: no model ran, nobody told.
RUN_UNCHANGED = "unchanged"
RUN_STATUSES = (
    RUN_RUNNING, RUN_WAITING, RUN_OK, RUN_SILENT, RUN_FAILED, RUN_SKIPPED, RUN_MISSED,
    RUN_UNCHANGED,
)
# What a run is waiting for, while it is `waiting`.
WAIT_MODEL = "model"
WAIT_RETRY = "retry"
WAIT_RATE_LIMIT = "rate_limit"
# When it ran against when it was due.
TIMING_ON_TIME = "on_time"
TIMING_LATE = "late"
TIMING_CATCH_UP = "catch_up"
TIMING_MANUAL = "manual"


class Schedule(UUIDPrimaryKey, Timestamps, InDataGroup, Base):
    """One standing instruction and when it comes due.

    The row carries no recurrence logic at all: `rule_json` is read by
    `services/schedule/rule.py`, which is pure and knows nothing about rows.
    What lives here is the bookkeeping the ticker needs to claim a firing
    without doing it twice.
    """

    __tablename__ = "schedules"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # What the reader actually typed, kept verbatim and for ever. The compile
    # rewrites it into `instruction`, and "what did I actually ask for" has to
    # survive that -- both so the edit form can show it and so a recompile has
    # something to work from other than its own previous output.
    request: Mapped[str] = mapped_column(Text, default="")
    # What is sent when it fires. The compiled form: standalone, since it is
    # read with no conversation around it.
    instruction: Mapped[str] = mapped_column(Text, default="")

    rule_json: Mapped[dict] = mapped_column(JSONDict, default=dict)

    target: Mapped[str] = mapped_column(String(16), default=TARGET_CHAT, nullable=False)
    # The chat this fires into. Deliberately not a ForeignKey -- `migrations.py`
    # compiles the column type only, so a REFERENCES clause would exist on a
    # fresh database and not on an upgraded one. Validated on read, and a
    # dangling value disables the schedule rather than raising every tick.
    chat_id: Mapped[str] = mapped_column(String(32), default="")
    model_id: Mapped[str] = mapped_column(String(300), default="")

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # The ticker's entire query. Nullable because "nothing more to do" is a real
    # state -- a spent count, a closed window, a calendar matching nothing --
    # and is different from "due at the epoch".
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Stamped when a firing starts and cleared when it finishes, so a run that
    # died halfway says so instead of looking like one that never happened.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    fired_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Why the last run did not work. Shown on the schedule's own page: a
    # schedule that silently stopped producing anything is indistinguishable
    # from one that was never due.
    last_error: Mapped[str] = mapped_column(Text, default="")

    origin: Mapped[str] = mapped_column(String(16), default=ORIGIN_USER, nullable=False)
    compiled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- How a run runs (2.0.0-beta.6) ------------------------------------------
    # `model_id` above is the first choice; this is its connection, when one was
    # named (two connections may serve the same id). The rest of the order is
    # the scheduling list (`services/schedule/models.py`).
    connection_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default=MODE_FRESH, nullable=False)
    notify: Mapped[str] = mapped_column(String(16), default=NOTIFY_MATTERS, nullable=False)
    catch_up: Mapped[str] = mapped_column(String(16), default=CATCH_UP_ONE, nullable=False)
    # Minutes a late run may still start in. NULL: half the period, clamped
    # 2 minutes to 2 hours (`services/schedule/runs.py:grace`).
    grace_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # A level of the model's (`chat.levels_for`), "" for the model's own start.
    effort: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    # Team size for the run, where the model offers teams; NULL is none.
    agents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Tool families switched on for a run. Empty: the safe default set
    # (`services/schedule/runs.py:SAFE_TOOLS`). Scheduling is never among them.
    tools_json: Mapped[list[str]] = mapped_column(JSONList, default=list)

    # Failures in a row, for "failed 3 times -- pause it?", and when each
    # error's notification last went out ({signature: ISO time}).
    failure_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_notes_json: Mapped[dict] = mapped_column(JSONDict, default=dict)
    # Monitor mode (`schedule/monitor.py`): what it watches ("" none, "url"),
    # the address, and what the last look that changed saw -- its hash, its
    # text (for the next diff) and when.
    watch_kind: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    watch_target: Mapped[str] = mapped_column(Text, default="", nullable=False)
    watch_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    watch_last: Mapped[str] = mapped_column(Text, default="", nullable=False)
    watch_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A command watch's timeout in seconds (None: the spec's default).
    watch_timeout: Mapped[int | None] = mapped_column(Integer)
    # Run on a linked computer (harness spec 3.5.0, `schedule/device.py`):
    # the device's token, the folder there, the mode ("" = edit) and how long
    # an approval waits, in minutes (None = 30). No token: on the server.
    device_token_id: Mapped[str | None] = mapped_column(String(32))
    device_dir: Mapped[str] = mapped_column(Text, default="", nullable=False)
    device_mode: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    approval_wait_minutes: Mapped[int | None] = mapped_column(Integer)

    def __repr__(self) -> str:
        return f"<Schedule {self.title!r} {'on' if self.enabled else 'off'}>"


class ScheduleRun(UUIDPrimaryKey, Timestamps, Base):
    """One firing of a schedule: when it was due, what happened, and where the
    result is. The history a schedule's page and the apps show.

    Kept `runs_days` (Settings → Scheduling, 90 by default) and swept by the
    ticker's housekeeping. A run that waits for a free model or for a retry is
    one row that changes status, never a row per attempt.
    """

    __tablename__ = "schedule_runs"

    schedule_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The first try: what the waits for a free model are measured from.
    first_try_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default=RUN_RUNNING, nullable=False)
    timing: Mapped[str] = mapped_column(String(16), default=TIMING_ON_TIME, nullable=False)
    # Runs that came due and did not happen before this one (a host that was
    # off, a pause the rule outlived): "missed ×N".
    missed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # While `waiting`: for what, until when the next attempt, how many so far,
    # and when the window closes (then `skipped`).
    waiting_for: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # What a run waiting for a model waits on (harness spec 3.4.0, progress
    # `waiting_for_model`): `{model, reason}` of the first model in order that
    # was not free -- reason `busy`, `slots`, `group` or `loading`.
    waiting_json: Mapped[dict] = mapped_column(JSONDict, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Of those, tries again after a reply that never reached its model.
    retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    model_id: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    connection_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_label: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    # Why this model: "pinned" (the first choice) or "switched" (it was busy).
    model_reason: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    # The first choice that was passed over, when switched.
    switched_from: Mapped[str] = mapped_column(String(300), default="", nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_micro: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="", nullable=False)

    chat_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    message_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    report_path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # The result's first lines, model output: escaped wherever it is shown.
    preview: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # `unreachable`, `rate_limit`, `timeout`, `model`, `internal`, `no_model`.
    error_class: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # What the one notify decision told, as every channel shows it
    # (`notify.notification`): the full version, the lock screen's public one
    # and the actions. Empty: nobody was told. Its body is model output.
    notification_json: Mapped[dict] = mapped_column(JSONDict, default=dict)
    # A monitor schedule's look for this run: kind, hash, bytes, changed,
    # first, error -- and `block`, the turn's text about it, kept for a retry.
    watch_json: Mapped[dict] = mapped_column(JSONDict, default=dict)

    def __repr__(self) -> str:
        return f"<ScheduleRun {self.schedule_id} {self.status}>"
