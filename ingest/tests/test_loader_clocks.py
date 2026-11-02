"""The loader's clock samples (issue #73, ADR-0016 D28): ``source_clock_samples`` holds every
loaded line with a device time of its own once, by source, received minute and offset, in the
transaction of the rows, and equals ``clocksamples.rebuild`` of the rows. The crash matrix over
every step (the samples are written in the ``counts`` step) is in ``test_exactly_once.py``."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time

from sqlalchemy import Connection, Engine, text

from arcilogger_core.timeutil import day_window
from arcilogger_db.clocksamples import ClockKey, clock_key, rebuild
from arcilogger_ingest.loader import _clock_keys
from arcilogger_ingest.prepare import PreparedLine, StreamInfo
from arcilogger_ingest.testing import DAY, World, dhcp_message, fortigate_message


def stored(bind: Engine | Connection) -> Counter[ClockKey]:
    """Every row of ``source_clock_samples`` as ``{key: n}``."""
    sql = "SELECT source, minute, seconds, n FROM source_clock_samples"
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            rows = connection.execute(text(sql)).all()
    else:
        rows = bind.execute(text(sql)).all()
    return Counter({ClockKey(source, minute, seconds): n for source, minute, seconds, n in rows})


def _sampled_rows(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT count(*) FROM messages WHERE time <> time_received")
            ).scalar_one()
        )


def test_every_line_with_a_device_time_is_sampled_once(world: World, db_engine: Engine) -> None:
    """Real parsers, small batches shared by three sources: the samples hold exactly the rows
    with a device time (an unknown sender's lines carry none), and a rebuild of each source's
    received day from its rows gives the same keys."""
    world.reconfigure(ingest={"batch_rows": 7})
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(40)))
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(30)))
    world.append("unknown-10.9.9.9", DAY, [b"<13>who am i"] * 5, sender="10.9.9.9")
    assert world.service(db_engine).run(once=True) == 0
    samples = stored(db_engine)
    assert sum(samples.values()) == _sampled_rows(db_engine) > 0
    assert {key.source for key in samples} == {"fortigate", "core-r1"}
    start, end = day_window(DAY)
    with db_engine.begin() as connection:
        for source in ("fortigate", "core-r1"):
            rebuild(connection, source, start - timedelta(days=1), end + timedelta(days=1))
    assert stored(db_engine) == samples


def test_a_clock_hours_behind_is_sampled_in_the_minute_it_was_received(
    world: World, db_engine: Engine
) -> None:
    """The key is the receive time's minute and the offset in seconds: a router whose clock is
    3 hours behind has its line in the minute it arrived, not 3 hours earlier."""
    received = datetime.combine(DAY, clock_time(8, 0), tzinfo=UTC)
    behind = received - timedelta(hours=3)
    stamp = behind.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    line = (
        f"<30>{stamp} CORE-R1 dhcp,info dhcp60 assigned 172.20.60.120 for 02:00:5E:00:53:78 NB-120"
    ).encode()
    world.append("core-r1", DAY, [line], start=clock_time(11, 0))  # 08:00 UTC, site time +03:00
    assert world.service(db_engine).run(once=True) == 0
    assert stored(db_engine) == {ClockKey("core-r1", received, -10800): 1}


def _line(number: int, time: datetime, received: datetime, source: str) -> PreparedLine:
    message = {"time": time, "time_received": received, "source": source, "raw_line": number}
    return PreparedLine(
        stream=StreamInfo(source, "fortigate", DAY),
        line=number,
        offset=0,
        rows=[("messages", message), ("fg_traffic", {"time": time})],
    )


def test_grouping_lines_gives_the_keys_of_clock_key_line_by_line() -> None:
    """``_clock_keys`` makes one key per group of lines; the result must be ``clock_key`` of every
    line: minutes split to the microsecond, offsets on both sides of a minute and parts of a
    second rounded away from zero, two sources, a line stamped with its receive time, salvaged
    lines, lines without a ``messages`` row."""
    end = datetime(2026, 9, 23, 6, 59, 59, 999999, tzinfo=UTC)  # the last instant of a minute
    lines = [
        _line(1, end - timedelta(seconds=0.3), end, "fortigate"),
        _line(2, end + timedelta(microseconds=1), end + timedelta(microseconds=1), "fortigate"),
        _line(3, end - timedelta(seconds=0.3), end + timedelta(microseconds=1), "fortigate"),
        _line(4, end - timedelta(hours=3), end, "fortigate"),
        _line(5, end - timedelta(hours=3, seconds=0.6), end, "fortigate"),
        _line(6, end + timedelta(seconds=60.4), end, "fortigate"),
        _line(7, end + timedelta(seconds=59.4), end, "fortigate"),
        _line(8, end - timedelta(minutes=8), end, "core-r1"),
    ]
    salvaged = _line(9, end - timedelta(hours=3), end, "fortigate")
    only_typed = _line(10, end - timedelta(hours=3), end, "fortigate")
    only_typed.rows.pop(0)
    nothing = PreparedLine(stream=salvaged.stream, line=11, offset=0)  # rawlog.invalid
    batch: list[tuple[Sequence[PreparedLine], bool]] = [(lines * 2, True), (lines, True)]
    batch += [((salvaged,), False), ((only_typed, nothing), True)]

    expected: Counter[ClockKey] = Counter()
    for run, _typed in batch:
        for line in run:
            rows = dict(line.rows)
            if "messages" in rows and (key := clock_key(rows["messages"])) is not None:
                expected[key] += 1
    assert _clock_keys(batch) == expected
    assert sum(expected.values()) == 3 * 7 + 1  # line 2 has no device time of its own
    minute = end.replace(second=0, microsecond=0)
    assert sorted((key.minute - minute, key.seconds, n) for key, n in expected.items()) == [
        (timedelta(0), -10801, 3),  # line 5: part of a second more rounds away from zero
        (timedelta(0), -10800, 4),  # line 4, and the salvaged line 9
        (timedelta(0), -480, 3),  # line 8, its own source
        (timedelta(0), 0, 6),  # lines 1 and 7: under a minute
        (timedelta(0), 61, 3),  # line 6: 60.4 s rounds away from zero
        (timedelta(minutes=1), 0, 3),  # line 3: received a microsecond into the next minute
    ]
