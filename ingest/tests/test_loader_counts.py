"""The loader's ``counts`` step (contract 2.9, 3.3): the summary table ``search_counts`` holds
exactly the lines whose rows loaded — in the transaction of the rows, across batches, after a
``COPY`` salvage, from the vault copy — and equals a recount of the rows (``searchcounts.verify``,
``rebuild_stream``). The crash matrix over every step is in ``test_exactly_once.py``."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text

from arcilogger_core.rawlog import RawRecord
from arcilogger_core.timeutil import day_window
from arcilogger_db.searchcounts import CountKey, count_key, rebuild_stream, verify
from arcilogger_ingest import prepare
from arcilogger_ingest.loader import _count_keys
from arcilogger_ingest.parsers import LinePosition, ParsedRow, ParseOutcome, get_parser
from arcilogger_ingest.prepare import PATTERN_RAW, PATTERN_ROWCHECK, PreparedLine, StreamInfo
from arcilogger_ingest.testing import (
    DAY,
    World,
    check_exactly_once,
    dhcp_message,
    fortigate_message,
    loaded,
    offsets,
)

NEXT = DAY + timedelta(days=1)
#: ``verify`` recounts the hours up to the end of this moment: every row of the tests (device
#: times within a day of DAY and NEXT) lies in the window ``HOURS`` before it.
VERIFY_NOW = day_window(DAY + timedelta(days=3))[0]
HOURS = 24 * 6


def stored(bind: Engine | Connection) -> Counter[CountKey]:
    """Every row of ``search_counts`` as ``{key: n}``."""
    sql = "SELECT hour, source, raw_day, kind, tbl, action, CAST(net AS text), n FROM search_counts"
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            rows = connection.execute(text(sql)).all()
    else:
        rows = bind.execute(text(sql)).all()
    counts: Counter[CountKey] = Counter()
    for hour, source, raw_day, kind, tbl, action, net, n in rows:
        counts[CountKey(hour, source, raw_day, kind, tbl, action, net)] += n
    return counts


def check_counts(connection: Connection) -> None:
    """``search_counts`` equals a recount of the rows and holds every ``messages`` row once."""
    report = verify(connection, HOURS, now=VERIFY_NOW)
    messages = connection.execute(text("SELECT count(*) FROM messages")).scalar_one()
    assert report.differences == []
    assert report.pending == []
    assert report.lines == messages == sum(stored(connection).values())


def counts_agree(engine: Engine) -> None:
    """:func:`check_counts` in one snapshot."""
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        connection.begin(),
    ):
        check_counts(connection)


def run_once(world: World, engine: Engine) -> None:
    assert world.service(engine).run(once=True) == 0


def key_counts(counts: Counter[CountKey], source: str) -> Counter[tuple[str, str, Any, Any]]:
    """``(kind, tbl, action, net) -> n`` of one source, over every hour."""
    found: Counter[tuple[str, str, Any, Any]] = Counter()
    for key, n in counts.items():
        if key.source == source:
            found[(key.kind, key.tbl, key.action, key.net)] += n
    return found


def test_every_loaded_line_is_counted_once_across_batches(world: World, db_engine: Engine) -> None:
    """Real parsers, small batches shared by three sources, a damaged line, two days."""
    world.reconfigure(ingest={"batch_rows": 7})
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(40)))
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(30)))
    world.append_raw("core-r1", DAY, b"0001-01-01T00:00:00+03:00 172.20.10.1 <13>year one\n")
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(30, 33)), start=clock_time(9))
    world.append("unknown-10.9.9.9", DAY, [b"<13>who am i"] * 5, sender="10.9.9.9")
    world.append("fortigate", NEXT, (fortigate_message(i) for i in range(9)))
    world.set_today(NEXT)
    run_once(world, db_engine)
    counts_agree(db_engine)
    counts = stored(db_engine)
    assert sum(counts.values()) == 40 + 33 + 5 + 9  # the damaged line has no messages row
    with db_engine.connect() as connection:
        damaged = connection.execute(
            text("SELECT count FROM parse_failures WHERE pattern = :p"), {"p": PATTERN_RAW}
        ).scalar_one()
    assert damaged == 1
    # The format samples rotate through seven lines (forty lines: samples 0-4 six times, 5-6
    # five times); the DHCP lines alternate assigned/deassigned on 172.20.60.0/24.
    fortigate = key_counts(counts, "fortigate")
    assert fortigate[("event", "fg_event", "perf-stats", None)] == 6 + 2
    assert fortigate[("app", "fg_app", "pass", "172.20.42.0/24")] == 6 + 2
    assert fortigate[("traffic", "fg_traffic", "close", "100.64.12.0/24")] == 6 + 1
    assert fortigate[("traffic", "fg_traffic", "close", "172.20.42.0/24")] == 5 + 1
    dhcp = key_counts(counts, "core-r1")
    assert dhcp == {
        ("dhcp", "mt_dhcp", "assigned", "172.20.60.0/24"): 17,
        ("dhcp", "mt_dhcp", "deassigned", "172.20.60.0/24"): 16,
    }
    assert key_counts(counts, "unknown-10.9.9.9") == {("syslog", "", None, None): 5}
    assert {key.raw_day for key in counts if key.source == "fortigate"} == {DAY, NEXT}
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM ingest_offsets WHERE counts_from_line IS NOT NULL")
            ).scalar_one()
            == 0
        )  # a file ingest starts is counted from its first line

    # Rebuilding a file from its rows gives exactly what ingest counted.
    with db_engine.begin() as connection:
        for source, day in offsets(connection):
            rebuild_stream(connection, source, day)
    assert stored(db_engine) == counts


def test_a_dropped_typed_row_counts_without_its_table(
    world: World, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typed row the row check refuses is not loaded: its line counts with ``tbl = ''``."""
    mikrotik = get_parser("mikrotik")

    def every_third_ip_invalid(record: RawRecord, position: LinePosition) -> ParseOutcome:
        outcome = mikrotik.parse(record, position)
        if position.raw_line % 3:
            return outcome
        (row,) = outcome.rows
        bad = ParsedRow(row.table, {**row.values, "ip": "172.20.60.999"})
        return ParseOutcome(parser=outcome.parser, message=outcome.message, rows=(bad,))

    monkeypatch.setattr(prepare, "parse_line", every_third_ip_invalid)
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(12)))
    run_once(world, db_engine)
    counts_agree(db_engine)
    assert key_counts(stored(db_engine), "core-r1") == {
        ("dhcp", "mt_dhcp", "assigned", "172.20.60.0/24"): 4,  # lines 1, 5, 7, 11
        ("dhcp", "mt_dhcp", "deassigned", "172.20.60.0/24"): 4,  # lines 2, 4, 8, 10
        ("dhcp", "", None, None): 4,  # lines 3, 6, 9, 12
    }
    with db_engine.connect() as connection:
        flagged = connection.execute(
            text("SELECT count(*) FROM messages WHERE parse_error = :p"),
            {"p": PATTERN_ROWCHECK + "mt_dhcp"},
        ).scalar_one()
    assert flagged == 4


def test_a_line_copy_refuses_counts_with_its_messages_row_alone(
    world: World, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``check_row`` passes, ``COPY`` fails (a lone surrogate cannot be encoded): the batch is
    bisected; line 9 is salvaged without its typed row (``tbl = ''``), line 7 loses its
    ``messages`` row and is not counted; every other line counts with its typed row."""
    mikrotik = get_parser("mikrotik")

    def unencodable(record: RawRecord, position: LinePosition) -> ParseOutcome:
        outcome = mikrotik.parse(record, position)
        if position.raw_line == 7:
            message = {**outcome.message, "host": "CORE-\udc80"}
            return ParseOutcome(parser=outcome.parser, message=message, rows=outcome.rows)
        if position.raw_line == 9:
            (row,) = outcome.rows
            bad = ParsedRow(row.table, {**row.values, "hostname": "NB-\udc80"})
            return ParseOutcome(parser=outcome.parser, message=outcome.message, rows=(bad,))
        return outcome

    monkeypatch.setattr(prepare, "parse_line", unencodable)
    world.reconfigure(ingest={"batch_rows": 20})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(20)))
    run_once(world, db_engine)
    assert [row[2] for row in loaded(db_engine)] == [n for n in range(1, 21) if n != 7]
    counts_agree(db_engine)
    assert key_counts(stored(db_engine), "core-r1") == {
        # odd lines are "assigned" (dhcp_message(0) is line 1): 1, 3, 5, 11, … without 7 and 9
        ("dhcp", "mt_dhcp", "assigned", "172.20.60.0/24"): 8,
        ("dhcp", "mt_dhcp", "deassigned", "172.20.60.0/24"): 10,
        ("dhcp", "", None, None): 1,  # line 9
    }
    with db_engine.connect() as connection:
        failures = connection.execute(
            text("SELECT pattern, example_line FROM parse_failures ORDER BY pattern")
        ).all()
    assert [tuple(row) for row in failures] == [("copy.messages", 7), ("copy.mt_dhcp", 9)]


def test_counts_continue_from_the_vault_copy(world: World, db_engine: Engine) -> None:
    """The sealer deleted the spool file; ingest goes on from the vault and keeps counting."""
    world.reconfigure(ingest={"batch_rows": 7})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10)))
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(6)))
    run_once(world, db_engine)
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10, 95)))
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(6, 30)))
    world.seal(DAY)
    assert not world.layout.spool_file("core-r1", DAY).exists()
    world.set_today(NEXT)
    run_once(world, db_engine)
    assert check_exactly_once(db_engine, world) == []
    assert offsets(db_engine)[("core-r1", DAY)][1:] == (95, True)
    counts_agree(db_engine)
    assert sum(key_counts(stored(db_engine), "core-r1").values()) == 95
    assert sum(key_counts(stored(db_engine), "fortigate").values()) == 30


def test_ingest_counts_only_its_lines_of_a_file_loaded_before_the_upgrade(
    world: World, db_engine: Engine
) -> None:
    """A file half loaded before revision ``0016_search`` (``counts_from_line`` set, no counts):
    ingest counts the lines it loads now and leaves ``counts_from_line`` for the worker job,
    whose recount of the finished file then covers every line exactly once."""
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10)))
    run_once(world, db_engine)
    with db_engine.begin() as connection:  # as the migration leaves a file loaded before it
        connection.execute(text("DELETE FROM search_counts"))
        connection.execute(text("UPDATE ingest_offsets SET counts_from_line = line_number + 1"))
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10, 25)))
    run_once(world, db_engine)
    assert sum(stored(db_engine).values()) == 15  # lines 11-25 only
    with db_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT counts_from_line FROM ingest_offsets")).scalar_one()
            == 11
        )
    with db_engine.begin() as connection:
        result = rebuild_stream(connection, "core-r1", DAY)
        connection.execute(text("UPDATE ingest_offsets SET counts_from_line = NULL"))
    assert (result.lines, sum(stored(db_engine).values())) == (25, 25)
    counts_agree(db_engine)


def test_a_device_clock_a_day_off_counts_in_its_own_hour(world: World, db_engine: Engine) -> None:
    """``hour`` is the device time of the row (``messages.time``), not the receive time: a line
    received on DAY whose device clock is almost a day behind counts in that earlier hour (and
    ``verify`` and ``rebuild_stream`` still find it: COUNTS_TIME_MARGIN)."""
    received = datetime.combine(DAY, clock_time(8, 0), tzinfo=UTC)
    behind = (received - timedelta(hours=23)).astimezone(UTC)
    stamp = behind.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    line = (
        f"<30>{stamp} CORE-R1 dhcp,info dhcp60 assigned 172.20.60.120 for 02:00:5E:00:53:78 NB-120"
    ).encode()
    world.append("core-r1", DAY, [line], start=clock_time(11, 0))
    run_once(world, db_engine)
    counts_agree(db_engine)
    (key,) = stored(db_engine)
    assert key.hour == behind.replace(minute=0, second=0, microsecond=0)
    assert key.raw_day == DAY
    assert key.hour < day_window(DAY)[0]


def _line(
    number: int,
    when: datetime,
    type_: str,
    subtype: str | None,
    table: str | None = None,
    *,
    source: str = "fortigate",
    raw_day: date = DAY,
    **typed: Any,
) -> PreparedLine:
    reference = {"time": when, "source": source, "raw_day": raw_day, "raw_line": number}
    rows: list[tuple[str, dict[str, Any]]] = [
        ("messages", {**reference, "type": type_, "subtype": subtype, "text": ""})
    ]
    if table is not None:
        rows.append((table, {**reference, **typed}))
    return PreparedLine(
        stream=StreamInfo("fortigate", "fortigate", DAY), line=number, offset=0, rows=rows
    )


def test_grouping_lines_gives_the_keys_of_count_key_line_by_line() -> None:
    """``_count_keys`` makes one key per group of lines; the result must be ``count_key`` of every
    line: hours split to the microsecond, actions differing in case, client addresses of one /24,
    public and IPv6 clients, a table without client column, a salvaged line, lines without a
    ``messages`` row."""
    end = datetime(2026, 9, 23, 6, 59, 59, 999999, tzinfo=UTC)  # the last instant of an hour
    lines = [
        _line(1, end, "utm", "webfilter", "fg_web", action="Blocked", srcip="172.20.42.76"),
        _line(
            2,
            end + timedelta(microseconds=1),
            "utm",
            "webfilter",
            "fg_web",
            action="blocked",
            srcip="172.20.42.200",
        ),
        _line(
            3,
            end - timedelta(minutes=59),
            "utm",
            "webfilter",
            "fg_web",
            action="blocked",
            srcip="172.20.42.7",
        ),
        _line(4, end, "traffic", "forward", "fg_traffic", action="accept", srcip="203.0.113.10"),
        _line(5, end, "utm", "ssl", "fg_app", action="pass", srcip="fe80::1"),
        _line(6, end, "utm", "ssl", "fg_app", action="pass", srcip=None),
        _line(7, end, "heartbeat", None, "mt_event", event="heartbeat", ip=None),
        _line(8, end, "interface", "up", "mt_interface", interface="ether1"),
        _line(9, end, "utm", None),  # Other UTM
        _line(10, end, "dhcp", "assigned", "mt_dhcp", action="assigned", ip="172.20.60.120"),
        _line(11, end, "utm", "webfilter", "fg_web", action="passthrough", srcip="172.20.42.76"),
        _line(15, end, "utm", "webfilter", "fg_web", action="blocked", srcip="172.20.43.5"),
        # each of these differs from the line before it in one thing only
        _line(16, end, "utm", "dns"),
        _line(17, end, "utm", None, source="core-r1"),
        _line(18, end, "utm", None, raw_day=NEXT),
        _line(19, end, "unknown", None),
        _line(20, end, "syslog", None),
    ]
    interface = _line(21, end, "interface", "up", "mt_interface", interface="ether2")
    lines.append(interface)
    salvaged = _line(12, end, "dhcp", "assigned", "mt_dhcp", action="assigned", ip="172.20.60.9")
    only_typed = _line(13, end, "dhcp", "assigned", "mt_dhcp", action="assigned", ip="172.20.60.9")
    only_typed.rows.pop(0)
    nothing = PreparedLine(stream=salvaged.stream, line=14, offset=0)  # rawlog.invalid
    batch: list[tuple[PreparedLine, bool]] = [(line, True) for line in lines * 3]
    batch += [(salvaged, False), (interface, False), (only_typed, True), (nothing, True)]

    expected: Counter[CountKey] = Counter()
    for line, typed_loaded in batch:
        rows = dict(line.rows)
        if "messages" not in rows:
            continue
        table = next((name for name in rows if name != "messages"), None) if typed_loaded else None
        expected[count_key(rows["messages"], table, rows.get(table) if table else None)] += 1
    assert _count_keys(batch) == expected
    assert sum(expected.values()) == 3 * 18 + 2
    web = {key: n for key, n in expected.items() if key.tbl == "fg_web"}
    assert sorted((key.hour.hour, key.action, key.net, n) for key, n in web.items()) == [
        (6, "blocked", "172.20.42.0/24", 6),  # lines 1 and 3, three times
        (6, "blocked", "172.20.43.0/24", 3),  # line 15: line 1 but for its client's /24
        (6, "passthrough", "172.20.42.0/24", 3),  # line 11: line 1 but for its action
        (7, "blocked", "172.20.42.0/24", 3),  # line 2: the next hour by a microsecond
    ]
