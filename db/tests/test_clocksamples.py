"""``source_clock_samples`` (issue #73, ADR-0016 D28): the key of a line, the additive upsert, the
rebuild from ``messages``, the two readers and the median as ``percentile_cont`` gives it."""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from arcilogger_db.clocksamples import (
    NEAR_SECONDS,
    ClockKey,
    clock_key,
    median,
    minute_of,
    newest,
    offset_of,
    rebuild,
    received_between,
    upsert_samples,
)
from arcilogger_db.models import Message, SourceClockSample

AT = datetime(2026, 10, 20, 9, 30, 15, tzinfo=UTC)
SECOND = timedelta(seconds=1)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(0), 0),
        (-0.4 * SECOND, 0),
        (59.6 * SECOND, 0),  # under a minute: no limit is that small
        (-59.9 * SECOND, 0),
        (60 * SECOND, 60),
        (-60.2 * SECOND, -61),  # away from zero, as a lookup prints an offset
        (120 * SECOND, 120),  # not beyond a limit of 120 s ...
        (120.0001 * SECOND, 121),  # ... and beyond it, as the unrounded offset is
        (-120.4 * SECOND, -121),
        (-timedelta(hours=3), -10800),
        (timedelta(hours=3, seconds=0.5), 10801),
    ],
)
def test_the_offset_is_whole_seconds_away_from_zero_and_zero_under_a_minute(
    offset: timedelta, expected: int
) -> None:
    assert offset_of(AT + offset, AT) == expected
    assert NEAR_SECONDS == 60


def test_the_key_is_the_received_minute_and_a_line_without_device_time_has_none() -> None:
    message = {"source": "fortigate", "time": AT - timedelta(hours=3), "time_received": AT}
    assert clock_key(message) == ClockKey("fortigate", AT.replace(second=0), -10800)
    assert clock_key({**message, "time": AT}) is None  # the receive time stands in for it
    local = AT.astimezone(datetime.now().astimezone().tzinfo)
    assert minute_of(local) == AT.replace(second=0)  # UTC, whatever the zone given


@pytest.mark.parametrize(
    "values",
    [[5], [1, 2], [0, 0, -10800], [-10800, -10800, 0, 0], [3, 1, 2, 10, -4, 7], [120, 121]],
)
def test_the_median_is_percentile_cont(values: list[int]) -> None:
    pairs: dict[int, int] = {}
    for value in values:
        pairs[value] = pairs.get(value, 0) + 1
    assert median(list(pairs.items())) == (statistics.median(values), len(values))
    assert median([]) == (None, 0)
    assert median([(30, 0)]) == (None, 0)


def _message(session: Session, line: int, time: datetime, received: datetime, source: str) -> None:
    session.add(
        Message(
            time=time,
            time_received=received,
            source=source,
            sender="172.31.255.1",
            raw_day=date(2026, 10, 20),
            raw_line=line,
            raw_offset=0,
            type="traffic",
            text="",
        )
    )


def test_the_rebuild_counts_what_the_loader_counts(db_session: Session) -> None:
    """Every row with a device time once, by received minute and offset, the rows stamped with
    the receive time left out; a second rebuild gives the same keys (it replaces, not adds)."""
    start = AT.replace(minute=0, second=0)
    lines = []
    for line in range(1, 41):
        received = start + timedelta(seconds=37 * line)
        offset = timedelta(milliseconds=-300) if line < 20 else -timedelta(hours=3)
        if line % 10 == 0:
            offset = timedelta(minutes=-12)  # a few late lines
        time = received if line == 7 else received + offset
        _message(db_session, line, time, received, "fortigate")
        lines.append({"source": "fortigate", "time": time, "time_received": received})
    _message(db_session, 99, AT, AT, "core-r1")
    db_session.commit()
    expected: dict[ClockKey, int] = {}
    for message in lines:
        if (key := clock_key(message)) is not None:
            expected[key] = expected.get(key, 0) + 1
    connection = db_session.connection()
    end = start + timedelta(hours=1)
    assert rebuild(connection, "fortigate", start, end) == len(expected)
    assert rebuild(connection, "fortigate", start, end) == len(expected)
    rows = db_session.execute(
        select(
            SourceClockSample.source,
            SourceClockSample.minute,
            SourceClockSample.seconds,
            SourceClockSample.n,
        )
    ).all()
    assert {ClockKey(s, m, o): n for s, m, o, n in rows} == expected
    assert sum(expected.values()) == 39  # line 7 has no device time


def test_the_readers(db_session: Session) -> None:
    """``received_between`` sums a range's minutes; ``newest`` takes whole minutes, newest first,
    until it holds the rows asked for."""
    connection = db_session.connection()
    base = AT.replace(second=0)
    upsert_samples(
        connection,
        {
            ClockKey("fortigate", base, 0): 100,
            ClockKey("fortigate", base + timedelta(minutes=1), 0): 50,
            ClockKey("fortigate", base + timedelta(minutes=2), -10800): 60,
            ClockKey("fortigate", base + timedelta(minutes=2), -10799): 20,
            ClockKey("fortigate", base + timedelta(minutes=3), -10800): 150,
            ClockKey("core-r1", base + timedelta(minutes=3), 300): 9,
        },
    )
    upsert_samples(connection, {ClockKey("fortigate", base, 0): 1})  # adds
    span = base + timedelta(minutes=3)
    assert sorted(received_between(connection, "fortigate", base, span)) == [
        (-10800, 210),
        (-10799, 20),
        (0, 151),
    ]
    assert sorted(received_between(connection, "fortigate", base, base)) == [(0, 101)]
    # 150 rows in the newest minute, then the whole minute before it.
    assert sorted(newest(connection, "fortigate", base, span, 200)) == [
        (-10800, 210),
        (-10799, 20),
    ]
    assert sorted(newest(connection, "fortigate", base, span, 150)) == [(-10800, 150)]
    assert sorted(newest(connection, "fortigate", base, span, 10_000)) == [
        (-10800, 210),
        (-10799, 20),
        (0, 151),
    ]
    assert newest(connection, "fortigate", span + timedelta(minutes=1), span, 200) == []
    total = db_session.scalar(select(func.sum(SourceClockSample.n)))
    assert total == 390
    assert db_session.execute(text("SELECT count(*) FROM source_clock_samples")).scalar() == 6


def test_the_rebuild_bound_is_ingests_clock_skew() -> None:
    """A row is used only within ingest's skew of its receive time (the rebuild reads that far)."""
    from arcilogger_db.clocksamples import MAX_CLOCK_SKEW
    from arcilogger_ingest.parsers.syslog import DEFAULT_MAX_CLOCK_SKEW

    assert MAX_CLOCK_SKEW == DEFAULT_MAX_CLOCK_SKEW
