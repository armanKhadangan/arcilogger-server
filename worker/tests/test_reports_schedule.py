"""When usage reports run and which period they cover (Europe/Istanbul, no daylight saving
time since 2016), and how missed runs after downtime are handled (once, not per occurrence)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from arcilogger_core.timeutil import SITE_TZ
from arcilogger_worker.reports.schedule import (
    ON_TIME,
    Timing,
    TimingError,
    due_run,
    latest_occurrence,
    next_occurrence,
    parse_time,
    period_for,
)


def local(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=SITE_TZ)


DAILY = Timing("full_day", "07:00")
END_OF_DAY = Timing("end_of_day", "18:30")
WEEKLY = Timing("weekly", "07:15", weekday=1)
MONTHLY = Timing("monthly", "07:30", monthday=1)


@pytest.mark.parametrize(
    ("timing", "after", "expected"),
    [
        (DAILY, local(2026, 10, 20, 6, 59), local(2026, 10, 20, 7, 0)),
        (DAILY, local(2026, 10, 20, 7, 0), local(2026, 10, 21, 7, 0)),
        (END_OF_DAY, local(2026, 10, 20, 9, 0), local(2026, 10, 20, 18, 30)),
        # 20.10.2026 is a Tuesday: the next Monday is 26.10.
        (WEEKLY, local(2026, 10, 20, 9, 0), local(2026, 10, 26, 7, 15)),
        (WEEKLY, local(2026, 10, 26, 7, 14), local(2026, 10, 26, 7, 15)),
        (MONTHLY, local(2026, 10, 20, 9, 0), local(2026, 11, 1, 7, 30)),
        (MONTHLY, local(2026, 12, 31, 23, 0), local(2027, 1, 1, 7, 30)),
        (Timing("monthly", "00:00", monthday=28), local(2027, 2, 28, 0, 0), local(2027, 3, 28)),
    ],
)
def test_next_occurrence(timing: Timing, after: datetime, expected: datetime) -> None:
    assert next_occurrence(timing, after) == expected


@pytest.mark.parametrize(
    "day",
    # Europe changes its clocks on these days (last Sundays of March and October); Türkiye
    # keeps UTC+3 all year since 2016, so a 07:00 schedule stays at 04:00 UTC.
    [(2026, 3, 28), (2026, 3, 29), (2026, 3, 30), (2026, 10, 24), (2026, 10, 25), (2027, 3, 28)],
)
def test_turkiye_has_no_daylight_saving_time(day: tuple[int, int, int]) -> None:
    year, month, number = day
    before = datetime(year, month, number, tzinfo=UTC)
    moment = next_occurrence(DAILY, before)
    assert moment.astimezone(UTC).hour == 4
    assert moment.utcoffset() == timedelta(hours=3)


def test_latest_occurrence_looks_back_one_cycle() -> None:
    assert latest_occurrence(DAILY, local(2026, 10, 20, 6, 0)) == local(2026, 10, 19, 7, 0)
    assert latest_occurrence(WEEKLY, local(2026, 10, 25, 23, 0)) == local(2026, 10, 19, 7, 15)
    assert latest_occurrence(MONTHLY, local(2026, 11, 1, 7, 0)) == local(2026, 10, 1, 7, 30)
    assert latest_occurrence(MONTHLY, local(2026, 11, 1, 7, 30)) == local(2026, 11, 1, 7, 30)


@pytest.mark.parametrize(
    ("kind", "at", "start", "end"),
    [
        # Gün sonu: today 00:00 until the run time.
        ("end_of_day", local(2026, 10, 20, 18, 30), local(2026, 10, 20), local(2026, 10, 20, 18, 30)),  # noqa: E501
        # Tam gün: the previous day.
        ("full_day", local(2026, 10, 20, 7, 0), local(2026, 10, 19), local(2026, 10, 20)),
        # Weekly on any day: the previous ISO week (Monday to Monday).
        ("weekly", local(2026, 10, 26, 7, 15), local(2026, 10, 19), local(2026, 10, 26)),
        ("weekly", local(2026, 10, 28, 7, 15), local(2026, 10, 19), local(2026, 10, 26)),
        # Monthly: the previous calendar month (also across the year).
        ("monthly", local(2026, 11, 1, 7, 30), local(2026, 10, 1), local(2026, 11, 1)),
        ("monthly", local(2027, 1, 1, 7, 30), local(2026, 12, 1), local(2027, 1, 1)),
        ("monthly", local(2027, 3, 15, 7, 30), local(2027, 2, 1), local(2027, 3, 1)),
    ],
)  # fmt: skip
def test_period_for(kind: str, at: datetime, start: datetime, end: datetime) -> None:
    assert period_for(kind, at) == (start, end)


def test_period_of_a_custom_run_is_given_by_hand() -> None:
    with pytest.raises(ValueError, match="no default period"):
        period_for("custom", local(2026, 10, 20))


def test_a_due_schedule_runs_once_for_its_newest_occurrence() -> None:
    planned = local(2026, 10, 15, 7, 0)
    # On time (the worker loop ticks every minute).
    due = due_run(DAILY, planned, planned + timedelta(minutes=1))
    assert due is not None
    assert (due.occurrence, due.catch_up, due.missed) == (planned, False, 0)
    # Five days down: one run, for 20.10 07:00, a catch-up; four occurrences were missed.
    due = due_run(DAILY, planned, local(2026, 10, 20, 9, 0))
    assert due is not None
    assert (due.occurrence, due.catch_up, due.missed) == (local(2026, 10, 20, 7, 0), True, 5)
    # Not due yet.
    assert due_run(DAILY, planned, planned - timedelta(seconds=1)) is None
    assert timedelta(minutes=30) == ON_TIME


def test_timings_are_validated() -> None:
    with pytest.raises(ValueError, match="weekday is required"):
        Timing("weekly", "07:00")
    with pytest.raises(ValueError, match="weekday is required"):
        Timing("full_day", "07:00", weekday=1)
    with pytest.raises(ValueError, match="monthday is required"):
        Timing("monthly", "07:00")
    with pytest.raises(ValueError, match="1 … 28"):
        Timing("monthly", "07:00", monthday=29)
    with pytest.raises(ValueError, match="unknown report kind"):
        Timing("yearly", "07:00")
    for bad in ("7:00", "24:00", "07:60", "07.00", " 07:00", "0700"):
        with pytest.raises(ValueError, match="HH:MM"):
            parse_time(bad)
    assert parse_time("23:59").hour == 23


def test_an_end_of_day_report_at_midnight_is_refused() -> None:
    """At 00:00 the end-of-day report would cover 00:00 → 00:00, nothing (and its run could not
    be stored: ``period_to > period_from``)."""
    with pytest.raises(TimingError, match="end_of_day") as raised:
        Timing("end_of_day", "00:00")
    assert raised.value.field == "time"
    assert period_for("end_of_day", local(2026, 10, 21, 0, 1)) == (
        local(2026, 10, 21),
        local(2026, 10, 21, 0, 1),
    )
    Timing("end_of_day", "00:01")
    Timing("full_day", "00:00")  # the previous day: a whole day
