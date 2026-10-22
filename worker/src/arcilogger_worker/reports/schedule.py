"""When a usage report runs and which period it covers (Europe/Istanbul site time, ADR-0012).

A schedule has a ``kind``, a local time ``HH:MM`` and, for ``weekly`` a ``weekday`` (1 = Monday
… 7 = Sunday, ISO) and for ``monthly`` a ``monthday`` (1-28, so every month has it):

==============  =======================  =====================================================
kind            runs                      covers
==============  =======================  =====================================================
``end_of_day``  every day at ``time``     today 00:00 → the run time ("Gün sonu", spec 10.5)
``full_day``    every day at ``time``     yesterday 00:00 → today 00:00 ("Tam gün")
``weekly``      ``weekday`` at ``time``   the previous ISO week, Monday 00:00 → Monday 00:00
``monthly``     ``monthday`` at ``time``  the previous calendar month
==============  =======================  =====================================================

An ``end_of_day`` schedule at 00:00 is refused: its period would be 00:00 → 00:00, nothing.

Times are computed on the site's calendar with :data:`~arcilogger_core.timeutil.SITE_TZ`
(``zoneinfo`` Europe/Istanbul): Türkiye keeps UTC+3 all year since September 2016, so a schedule
at 07:00 runs at 04:00 UTC on every day of the year — the tests check the days on which Europe
changes its clocks. Should the country bring daylight saving time back, the IANA database (the
``tzdata`` package) changes and the local time stays right.

**Missed runs** (the worker was stopped): a due schedule runs **once**, for the most recent
occurrence that passed (:func:`latest_occurrence`), never once per missed occurrence; with the
setting ``reports.catch_up`` false an occurrence more than :data:`ON_TIME` late is skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from typing import Final

from arcilogger_core.timeutil import SITE_TZ, to_site

#: The kinds a schedule can have (the run of a schedule with a period given by hand is
#: ``custom``).
SCHEDULE_KINDS: Final = ("end_of_day", "full_day", "weekly", "monthly")
RUN_KINDS: Final = (*SCHEDULE_KINDS, "custom")
TIME_PATTERN: Final = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
#: A run started at most this long after its occurrence is on time; later it is a catch-up
#: after downtime (skipped when ``reports.catch_up`` is false).
ON_TIME: Final = timedelta(minutes=30)


class TimingError(ValueError):
    """A schedule's timing is not valid; ``field``: the one to correct."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class Timing:
    """What decides when a schedule runs (:class:`TimingError` when it cannot run)."""

    kind: str
    time: str
    weekday: int | None = None
    monthday: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in SCHEDULE_KINDS:
            raise TimingError("kind", f"unknown report kind {self.kind!r}")
        try:
            clock = parse_time(self.time)
        except ValueError as exc:
            raise TimingError("time", str(exc)) from None
        if self.kind == "end_of_day" and clock == dtime(0, 0):
            raise TimingError(
                "time",
                "an end_of_day report at 00:00 would cover nothing; choose 00:01 … 23:59 "
                "(or full_day for the whole previous day)",
            )
        if (self.kind == "weekly") != (self.weekday is not None):
            raise TimingError(
                "weekday", "weekday is required for weekly schedules and only for them"
            )
        if (self.kind == "monthly") != (self.monthday is not None):
            raise TimingError(
                "monthday", "monthday is required for monthly schedules and only for them"
            )
        if self.weekday is not None and not 1 <= self.weekday <= 7:
            raise TimingError("weekday", "weekday must be 1 (Monday) … 7 (Sunday)")
        if self.monthday is not None and not 1 <= self.monthday <= 28:
            raise TimingError("monthday", "monthday must be 1 … 28")

    @property
    def clock(self) -> dtime:
        return parse_time(self.time)


def parse_time(value: str) -> dtime:
    """``"18:30"`` → 18:30; ``ValueError`` for anything but ``HH:MM`` (00:00-23:59)."""
    match = TIME_PATTERN.match(value)
    if match is None:
        raise ValueError(f"time must be HH:MM (00:00-23:59), not {value!r}")
    return dtime(int(match.group(1)), int(match.group(2)))


def _at(day: date, clock: dtime) -> datetime:
    return datetime.combine(day, clock, tzinfo=SITE_TZ)


def _runs_on(timing: Timing, day: date) -> bool:
    if timing.kind == "weekly":
        return day.isoweekday() == timing.weekday
    if timing.kind == "monthly":
        return day.day == timing.monthday
    return True


def _days_back(timing: Timing) -> int:
    """How far back the latest occurrence can be (one cycle)."""
    return {"weekly": 7, "monthly": 31}.get(timing.kind, 1)


def latest_occurrence(timing: Timing, now: datetime) -> datetime:
    """The newest occurrence at or before ``now`` (site time, aware)."""
    local = to_site(now)
    day = local.date()
    for back in range(_days_back(timing) + 1):
        candidate = day - timedelta(days=back)
        if _runs_on(timing, candidate):
            moment = _at(candidate, timing.clock)
            if moment <= local:
                return moment
    raise AssertionError("every cycle has an occurrence")  # pragma: no cover


def next_occurrence(timing: Timing, after: datetime) -> datetime:
    """The first occurrence strictly after ``after`` (site time, aware)."""
    local = to_site(after)
    day = local.date()
    for ahead in range(_days_back(timing) + 2):
        candidate = day + timedelta(days=ahead)
        if _runs_on(timing, candidate):
            moment = _at(candidate, timing.clock)
            if moment > local:
                return moment
    raise AssertionError("every cycle has an occurrence")  # pragma: no cover


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _previous_month_start(day: date) -> date:
    first = _month_start(day)
    return _month_start(first - timedelta(days=1))


def period_for(kind: str, at: datetime) -> tuple[datetime, datetime]:
    """The period a run of ``kind`` at ``at`` covers: ``[start, end)``, site time.

    ``end_of_day`` ends at ``at`` itself (today until now); the other kinds cover whole days
    before the day of ``at``.
    """
    local = to_site(at)
    today = local.date()
    if kind == "end_of_day":
        return _at(today, dtime(0, 0)), local
    if kind == "full_day":
        return _at(today - timedelta(days=1), dtime(0, 0)), _at(today, dtime(0, 0))
    if kind == "weekly":
        monday = today - timedelta(days=today.isoweekday() - 1)
        return _at(monday - timedelta(days=7), dtime(0, 0)), _at(monday, dtime(0, 0))
    if kind == "monthly":
        return _at(_previous_month_start(today), dtime(0, 0)), _at(_month_start(today), dtime(0, 0))
    raise ValueError(f"no default period for report kind {kind!r}")


@dataclass(frozen=True)
class Due:
    """A schedule's occurrence to run now (``catch_up``: it passed more than :data:`ON_TIME`
    ago, the worker was not running)."""

    occurrence: datetime
    catch_up: bool
    #: Occurrences that passed without a run (only the latest one runs).
    missed: int


def due_run(timing: Timing, next_run_at: datetime, now: datetime) -> Due | None:
    """What to run for a schedule whose ``next_run_at`` has passed (``None``: not yet due)."""
    if next_run_at > now:
        return None
    occurrence = latest_occurrence(timing, now)
    if occurrence < next_run_at:
        # next_run_at was set after the newest occurrence (e.g. the time was edited): wait.
        return None
    missed = 0
    moment = to_site(next_run_at)
    while moment < occurrence:
        missed += 1
        moment = next_occurrence(timing, moment)
    return Due(occurrence=occurrence, catch_up=now - occurrence > ON_TIME, missed=missed)
