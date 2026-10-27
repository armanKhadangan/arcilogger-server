"""The summary table ``search_counts`` (contract 2.7.1, 3.3): one key function, an upsert that
adds, a per-file rebuild equal to incremental counting, completeness and verification."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import (
    Connection,
    Engine,
    and_,
    case,
    func,
    insert,
    literal,
    null,
    or_,
    select,
    text,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import CIDR
from sqlalchemy.exc import IntegrityError

from arcilogger_core.timeutil import day_window
from arcilogger_db.bulk import copy_rows
from arcilogger_db.kinds import ACTION_TABLES, CLIENT_IP, KINDS
from arcilogger_db.models import Base, IngestOffset, Message, SearchCount
from arcilogger_db.searchcounts import (
    COUNTS_TIME_MARGIN,
    CountKey,
    count_key,
    counts_complete,
    hour_of,
    net_of,
    rebuild_stream,
    stream_window,
    upsert_counts,
    verify,
)
from arcilogger_ingest.parsers.syslog import DEFAULT_MAX_CLOCK_SKEW, plausible_time

DAY = date(2026, 9, 23)
#: 09:00 site time of DAY.
START = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)


@dataclass
class Line:
    message: dict[str, Any]
    table: str | None = None
    typed: dict[str, Any] = field(default_factory=dict)


def _line(
    number: int,
    type_: str,
    subtype: str | None = None,
    table: str | None = None,
    *,
    source: str = "fortigate",
    day: date = DAY,
    at: datetime | None = None,
    **typed: Any,
) -> Line:
    time = at or START + timedelta(minutes=7 * number)
    reference = {
        "time": time,
        "time_received": time,
        "source": source,
        "sender": "172.31.255.1",
        "raw_day": day,
        "raw_line": number,
        "raw_offset": 100 * number,
        "extra": {},
    }
    message = {**reference, "type": type_, "subtype": subtype, "text": f"line {number}"}
    return Line(message, table, {**reference, **typed} if table else {})


#: Lines of every shape the key distinguishes; ``raw_line`` numbers are unique per file.
LINES = [
    _line(1, "traffic", "forward", "fg_traffic", srcip="172.20.41.105", action="accept"),
    _line(2, "traffic", "forward", "fg_traffic", srcip="172.20.41.17", action="deny"),
    _line(3, "traffic", "local", "fg_traffic", srcip="203.0.113.10", action="accept"),
    _line(4, "utm", "webfilter", "fg_web", srcip="172.20.42.76", action="blocked"),
    _line(5, "utm", "webfilter", "fg_web", srcip="172.20.42.76", action="Blocked"),
    _line(6, "utm", "dns", "fg_dns", srcip="172.20.60.163", action=None),
    _line(7, "utm", "ssl", "fg_app", srcip="fe80::1", action="pass"),
    _line(8, "event", "system", "fg_event", srcip=None, action="Edit"),
    _line(9, "utm", "dlp"),  # Other UTM: no typed row
    _line(10, "traffic", "forward"),  # its typed row was dropped (row check, salvage)
    _line(11, "unknown"),
    _line(12, "utm", "webfilter", "fg_web", srcip="100.64.3.9", action="passthrough"),
    _line(
        1,
        "dhcp",
        "assigned",
        "mt_dhcp",
        source="core-r1",
        server="dhcp41",
        action="assigned",
        ip="172.20.41.142",
        mac="00:00:5e:00:53:48",
    ),
    _line(
        2,
        "dhcp",
        "pool_empty",
        "mt_event",
        source="core-r1",
        event="dhcp_pool_empty",
        ip=None,
    ),
    _line(3, "heartbeat", None, "mt_event", source="core-r1", event="heartbeat"),
    _line(4, "interface", "up", "mt_interface", source="core-r1", interface="ether1", state="up"),
    _line(5, "account", "login", "mt_account", source="core-r1", action="login", ip="172.20.10.37"),
    # a row whose device clock is 20 hours behind the receive day (within the skew bound)
    _line(13, "traffic", "forward", "fg_traffic", at=START - timedelta(hours=29), srcip="10.1.2.3"),
    # the next day's file of the same source
    _line(1, "traffic", "forward", "fg_traffic", day=DAY + timedelta(days=1), srcip="10.1.2.3"),
]


def _load(connection: Connection, lines: Iterable[Line]) -> None:
    by_table: dict[str, list[dict[str, Any]]] = {}
    for line in lines:
        by_table.setdefault("messages", []).append(line.message)
        if line.table is not None:
            by_table.setdefault(line.table, []).append(line.typed)
    for table, rows in by_table.items():
        columns = sorted({name for row in rows for name in row})
        copy_rows(connection, table, rows, columns=columns)


def _counts(lines: Iterable[Line]) -> Counter[CountKey]:
    return Counter(
        count_key(line.message, line.table, line.typed if line.table else None) for line in lines
    )


def _stored(connection: Connection, **where: Any) -> Counter[CountKey]:
    query = select(SearchCount)
    for name, value in where.items():
        query = query.where(getattr(SearchCount, name) == value)
    found: Counter[CountKey] = Counter()
    for row in connection.execute(query).mappings():
        net = None if row["net"] is None else str(row["net"])
        found[CountKey(row["hour"], row["source"], row["raw_day"], row["kind"], row["tbl"],
                       row["action"], net)] += row["n"]  # fmt: skip
    return found


# -- the key --------------------------------------------------------------------------------------


def test_count_key_rules() -> None:
    first = count_key(LINES[0].message, "fg_traffic", LINES[0].typed)
    assert first == CountKey(
        START.replace(minute=0), "fortigate", DAY, "traffic", "fg_traffic", "accept",
        "172.20.41.0/24",
    )  # fmt: skip
    assert count_key(LINES[4].message, "fg_web", LINES[4].typed).action == "blocked"
    assert count_key(LINES[7].message, "fg_event", LINES[7].typed).action == "edit"
    assert count_key(LINES[2].message, "fg_traffic", LINES[2].typed).net is None  # public
    assert count_key(LINES[6].message, "fg_app", LINES[6].typed).net is None  # IPv6
    assert count_key(LINES[11].message, "fg_web", LINES[11].typed).net == "100.64.3.0/24"
    salvaged = count_key(LINES[9].message, None, None)
    assert (salvaged.kind, salvaged.tbl, salvaged.action, salvaged.net) == (
        "traffic", "", None, None,
    )  # fmt: skip
    interface = LINES[15]
    key = count_key(interface.message, "mt_interface", interface.typed)
    assert (key.tbl, key.action, key.net) == ("mt_interface", None, None)  # no such columns
    istanbul = datetime(2026, 9, 23, 9, 59, 59, tzinfo=timezone(timedelta(hours=3)))
    assert hour_of(istanbul) == datetime(2026, 9, 23, 6, tzinfo=UTC)
    assert net_of("192.168.7.200") == "192.168.7.0/24"
    assert net_of("172.32.0.1") is None  # outside 172.16/12
    assert net_of("not an address") is None
    assert net_of(None) is None


def test_count_key_equals_a_sql_group_by(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        _load(connection, LINES)
    m = Message.__table__
    parts = []
    for name in sorted({line.table for line in LINES if line.table}):
        table = Base.metadata.tables[name]
        client = CLIENT_IP.get(name)
        parts.append(
            select(
                literal(name).label("tbl"),
                table.c.time,
                table.c.source,
                table.c.raw_day,
                table.c.raw_line,
                (table.c.action if name in ACTION_TABLES else null()).label("action"),
                (table.c[client] if client else null()).label("client"),
            )
        )
    typed = parts[0].union_all(*parts[1:]).subquery()
    kind = case(*((k.messages_predicate(m.c), key) for key, k in KINDS.items()))
    private = or_(
        *(
            typed.c.client.op("<<")(sql_cast(network, CIDR))
            for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
        )
    )
    net = case((private, func.network(func.set_masklen(typed.c.client, 24))), else_=null())
    columns = (
        func.date_trunc("hour", m.c.time),
        m.c.source,
        m.c.raw_day,
        kind,
        func.coalesce(typed.c.tbl, ""),
        func.lower(typed.c.action),
        net,
    )
    query = (
        select(*columns, func.count())
        .select_from(
            m.outerjoin(
                typed,
                and_(
                    typed.c.time == m.c.time,
                    typed.c.source == m.c.source,
                    typed.c.raw_day == m.c.raw_day,
                    typed.c.raw_line == m.c.raw_line,
                ),
            )
        )
        .group_by(*columns)
    )
    grouped: Counter[CountKey] = Counter()
    with db_engine.connect() as connection:
        for hour, source, raw_day, kind_key, tbl, action, n, count in connection.execute(query):
            net_text = None if n is None else str(n)
            grouped[CountKey(hour, source, raw_day, kind_key, tbl, action, net_text)] += count
    assert grouped == _counts(LINES)


# -- upsert --------------------------------------------------------------------------------------


def test_upsert_adds_and_finds_keys_without_action_or_net(db_engine: Engine) -> None:
    counts = _counts(LINES)
    assert any(key.action is None and key.net is None for key in counts)
    with db_engine.begin() as connection:
        assert upsert_counts(connection, counts) == len(counts)
        assert upsert_counts(connection, counts) == len(counts)
        upsert_counts(connection, {next(iter(counts)): 0})  # nothing to add
        assert _stored(connection) == Counter({key: 2 * n for key, n in counts.items()})
        rows = connection.scalar(select(func.count()).select_from(SearchCount))
    assert rows == len(counts)  # NULLS NOT DISTINCT: no second row for a key without action


def test_counts_are_positive(db_engine: Engine) -> None:
    key = count_key(LINES[0].message, "fg_traffic", LINES[0].typed)
    with pytest.raises(IntegrityError, match="ck_search_counts_n"), db_engine.begin() as conn:
        conn.execute(insert(SearchCount).values(**key._asdict(), n=0))


# -- rebuild --------------------------------------------------------------------------------------


def test_rebuild_stream_equals_incremental_counting(db_engine: Engine) -> None:
    fortigate = [line for line in LINES if line.message["source"] == "fortigate"]
    today = [line for line in fortigate if line.message["raw_day"] == DAY]
    first, second = today[:5], today[5:]
    with db_engine.begin() as connection:
        for batch in (first, second):  # as the loader does: rows and counts per batch
            _load(connection, batch)
            upsert_counts(connection, _counts(batch))
        others = [line for line in LINES if line not in today]
        _load(connection, others)
        upsert_counts(connection, _counts(others))
    with db_engine.connect() as connection:
        incremental = _stored(connection, source="fortigate", raw_day=DAY)
        everything = _stored(connection)
    assert incremental == _counts(today)
    stale = CountKey(START, "fortigate", DAY, "traffic", "fg_traffic", "accept", None)
    with db_engine.begin() as connection:
        upsert_counts(connection, {stale: 5})  # a count the rows do not support
        result = rebuild_stream(connection, "fortigate", DAY)
    assert (result.source, result.day, result.lines) == ("fortigate", DAY, len(today))
    assert result.keys == len(_counts(today))
    with db_engine.begin() as connection:
        assert _stored(connection, source="fortigate", raw_day=DAY) == incremental
        assert _stored(connection) == everything  # other files untouched
        assert rebuild_stream(connection, "core-r1", DAY).lines == 5
        assert _stored(connection) == everything


def test_rebuild_of_a_file_without_typed_rows(db_engine: Engine) -> None:
    lines = [_line(1, "syslog", source="printer"), _line(2, "syslog", source="printer")]
    with db_engine.begin() as connection:
        _load(connection, lines)
        result = rebuild_stream(connection, "printer", DAY)
        assert _stored(connection, source="printer") == _counts(lines)
        assert rebuild_stream(connection, "nobody", DAY).keys == 0
    assert (result.lines, result.keys) == (2, 1)


def test_the_window_of_a_file_covers_the_clock_skew_bound() -> None:
    """The parsers keep a device time only within ``DEFAULT_MAX_CLOCK_SKEW`` of the receive time,
    which lies in the file's site day: the furthest such rows are inside the rebuild's window."""
    start, end = stream_window(DAY)
    first, after = day_window(DAY)
    last = after - timedelta(microseconds=1)
    for received, device in (
        (first, first - DEFAULT_MAX_CLOCK_SKEW),
        (last, last + DEFAULT_MAX_CLOCK_SKEW),
    ):
        time = plausible_time(device, received)
        assert time is not None  # ingest keeps it
        assert start <= time < end
    assert DEFAULT_MAX_CLOCK_SKEW + timedelta(hours=1) <= COUNTS_TIME_MARGIN
    assert end - start == timedelta(days=1) + 2 * COUNTS_TIME_MARGIN


# -- completeness and verification ---------------------------------------------------------------


def _offset(connection: Connection, source: str, day: date, counts_from: int | None) -> None:
    connection.execute(
        insert(IngestOffset).values(
            source=source, day=day, byte_offset=0, line_number=10, counts_from_line=counts_from
        )
    )


def test_counts_complete_looks_two_days_around_the_range(db_engine: Engine) -> None:
    start, end = START, START + timedelta(hours=6)
    with db_engine.begin() as connection:
        assert counts_complete(connection, start, end)
        _offset(connection, "fortigate", DAY - timedelta(days=3), 11)
        _offset(connection, "core-r1", DAY, None)
        assert counts_complete(connection, start, end)
        _offset(connection, "fortigate", DAY + timedelta(days=2), 11)
        assert not counts_complete(connection, start, end)


def test_verify_compares_recent_hours(db_engine: Engine) -> None:
    recent = [line for line in LINES if line.message["time"] >= START]
    now = START + timedelta(hours=2, minutes=30)
    with db_engine.begin() as connection:
        _load(connection, recent)
        upsert_counts(connection, _counts(recent))
        connection.execute(
            text("UPDATE fg_web SET hostname = 'WWW.Example.COM' WHERE raw_line = 4")
        )
    with db_engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        report = verify(connection, 3, now=now)
    assert report.ok, report.differences
    assert (report.start, report.end) == (START - timedelta(hours=1), START + timedelta(hours=3))
    assert report.lines == len(recent)
    assert report.mixed_case == {"fg_dns.qname": 0, "fg_web.hostname": 1}
    key = count_key(recent[0].message, "fg_traffic", recent[0].typed)
    with db_engine.begin() as connection:
        upsert_counts(connection, {key: 1})
        report = verify(connection, 3, now=now)
        assert [(k, counted, stored) for k, counted, stored in report.differences] == [(key, 1, 2)]
        _offset(connection, "fortigate", DAY, 3)  # still waiting for its recount: left out
        report = verify(connection, 3, now=now)
    assert report.ok
    assert report.pending == [("fortigate", DAY)]
    with pytest.raises(ValueError, match="negative"):
        verify(connection, -1)
