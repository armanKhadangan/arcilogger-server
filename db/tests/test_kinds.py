"""Kinds (contract 2.3): every parser output has a named kind, the typed tables agree with the
``messages`` rows, and the SQL predicates select exactly what :func:`kind_of` says."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import INET

from arcilogger_core.rawlog import format_line
from arcilogger_db.bulk import copy_rows
from arcilogger_db.kinds import (
    ACTION_TABLES,
    CLIENT_IP,
    KINDS,
    KNOWN_TYPES,
    MT_EVENT_KINDS,
    TABLE_KINDS,
    TYPED_TABLES,
    UTM_SUBTYPES,
    ascii_lower,
    family_of,
    kind_of,
    kind_of_row,
    mt_event_events,
    tables_of,
)
from arcilogger_db.models import TIME_SERIES_TABLES, Base, Message
from arcilogger_ingest.prepare import PreparedLine, StreamInfo, prepare_line

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures"
RECEIVED = datetime(2026, 9, 23, 9, 44, 57, 123456, tzinfo=timezone(timedelta(hours=3)))
DAY = date(2026, 9, 23)


def _lines(path: Path) -> list[bytes]:
    """The non-empty lines of a fixture (``#`` lines are comments)."""
    return [
        line
        for line in path.read_bytes().splitlines()
        if line.strip() and not line.startswith(b"#")
    ]


def _mikrotik_print_sample() -> list[bytes]:
    """The RouterOS ``/log print`` sample as wire lines (as in ingest's parser contract test)."""
    lines = []
    text = (FIXTURES / "format" / "mikrotik-log-print-sample.txt").read_text("utf-8")
    for row in text.splitlines():
        topics, _, message = row.partition("|")
        topics_text = ",".join(topics.strip().split(";"))
        lines.append(
            f"<30>2026-09-23T09:38:26.380+03:00 CORE-R1 {topics_text} {message.strip()}".encode()
        )
    return lines


#: Every format fixture with the source type it is read as.
FIXTURE_LINES: list[tuple[str, bytes]] = [
    *(("fortigate", line) for line in _lines(FIXTURES / "format" / "fortigate-sample.log")),
    *(("fortigate", line) for line in _lines(FIXTURES / "synthetic" / "fortigate-synthetic.log")),
    *(("mikrotik", line) for line in _mikrotik_print_sample()),
    *(
        ("mikrotik", line)
        for line in _lines(FIXTURES / "synthetic" / "mikrotik-wire-synthetic.log")
    ),
    ("generic", b"<14>Sep 23 09:44:57 printer-2 lpd[812]: job 17 printed"),
]


def _prepared(source_type: Any, message: bytes, number: int = 1) -> PreparedLine:
    raw = format_line(RECEIVED, "172.20.10.1", message)
    return prepare_line(StreamInfo("example", source_type, DAY), number, 0, raw.rstrip(b"\n"))


def _outcomes() -> list[tuple[bytes, dict[str, Any], list[tuple[str, dict[str, Any]]]]]:
    found = []
    for source_type, message in FIXTURE_LINES:
        prepared = _prepared(source_type, message)
        rows = dict(prepared.rows[:1])
        assert "messages" in rows, message
        found.append((message, rows["messages"], prepared.rows[1:]))
    return found


def test_every_fixture_line_has_a_named_kind() -> None:
    """A new parser type must be added to ``kinds`` (it would be searchable as ``other`` only)."""
    unnamed = [
        (message, row["type"], row["subtype"])
        for message, row, _ in _outcomes()
        if kind_of(row["type"], row["subtype"]) == "other"
    ]
    assert unnamed == []


def test_typed_rows_have_the_kind_of_their_messages_row() -> None:
    seen: set[str] = set()
    for message, row, typed in _outcomes():
        kind = kind_of(row["type"], row["subtype"])
        assert len(typed) <= 1, message
        for table, values in typed:
            assert kind_of_row(table, values.get("event")) == kind, (message, table)
            assert table in KINDS[kind].table_names, (message, table)
            seen.add(table)
    # the fixtures reach most typed tables: a broken mapping cannot hide behind an empty set
    assert {"fg_traffic", "fg_web", "fg_dns", "fg_app", "mt_dhcp", "mt_arp", "mt_fw"} <= seen
    assert {"fg_event", "fg_dhcp"} <= seen  # FortiGate DHCP lines are events (ADR-0016)


def test_kinds_name_every_typed_table_and_mt_event_event() -> None:
    assert TIME_SERIES_TABLES[1:] == TYPED_TABLES
    named = {table for kind in KINDS.values() for table in kind.table_names}
    assert named == set(TYPED_TABLES)
    assert set(TABLE_KINDS) == set(TYPED_TABLES) - {"mt_event"}
    for event, kind in MT_EVENT_KINDS.items():
        assert event in mt_event_events([kind])
        assert kind_of_row("mt_event", event) == kind
    assert kind_of_row("mt_event", "something_new") is None
    assert tables_of(["dhcp"]) == ("mt_dhcp", "mt_event")
    # FortiGate DHCP rows are of the FortiGate kind event, as their messages rows (event/system)
    assert tables_of(["event"]) == ("fg_event", "fg_dhcp")
    assert TABLE_KINDS["fg_dhcp"] == kind_of("event", "system") == "event"
    assert tables_of(["web", "traffic"]) == ("fg_traffic", "fg_web")
    assert tables_of(["syslog", "unknown", "other", "utm"]) == ()


def test_kind_of_rules() -> None:
    assert kind_of("utm", "webfilter") == "web"
    assert kind_of("utm", "ssl") == kind_of("utm", "app-ctrl") == "app"
    assert kind_of("utm", "dlp") == kind_of("utm", None) == kind_of("utm", "") == "utm"
    assert kind_of("traffic", "forward") == "traffic"
    assert kind_of("dhcp", "pool_empty") == "dhcp"
    assert kind_of("anomaly", None) == "other"  # a FortiGate type without a kind
    assert kind_of("Traffic", None) == "other"  # compared exactly, as the parsers write it
    assert set(UTM_SUBTYPES) == {"webfilter", "dns", "app-ctrl", "ssl", "ips", "virus"}
    assert "utm" in KNOWN_TYPES
    assert "other" not in KNOWN_TYPES


def test_kinds_have_turkish_and_english_labels_and_a_family() -> None:
    assert list(KINDS)[:2] == ["traffic", "web"]
    assert list(KINDS)[-1] == "other"
    for key, kind in KINDS.items():
        assert kind.key == key
        assert kind.label_tr.strip()
        assert kind.label_en.strip()
    # the MikroTik kind says so: on a FortiGate-only site DHCP records are under type:event
    assert (KINDS["dhcp"].label_tr, KINDS["dhcp"].label_en) == ("DHCP (MikroTik)",) * 2
    assert family_of(["traffic", "web"]) == "fortigate"
    assert family_of(["dhcp", "heartbeat"]) == "mikrotik"
    assert family_of(["traffic", "dhcp"]) is None
    assert family_of(["syslog"]) == "generic"
    assert family_of(["unknown"]) is None


def test_client_ip_and_action_columns_exist() -> None:
    for table in TYPED_TABLES:
        columns = Base.metadata.tables[table].c
        addresses = [c.name for c in columns if isinstance(c.type, INET) and c.name != "sender"]
        if addresses:
            assert table in CLIENT_IP, table
        if table in CLIENT_IP:
            assert isinstance(columns[CLIENT_IP[table]].type, INET), table
        assert ("action" in columns) == (table in ACTION_TABLES), table
    assert set(CLIENT_IP) == set(TYPED_TABLES) - {"mt_interface", "mt_config"}
    assert CLIENT_IP["fg_dhcp"] == "ip"
    assert "fg_dhcp" not in ACTION_TABLES  # no action column: the meaning is the worker's
    assert list(CLIENT_IP) == [t for t in TYPED_TABLES if t in CLIENT_IP]  # branch-rank order


def test_ascii_lower_folds_ascii_letters_only() -> None:
    assert ascii_lower("DENY Edit AAAA") == "deny edit aaaa"
    assert ascii_lower("İSTANBUL ŞUBE") == "İstanbul Şube"
    assert ascii_lower("ı") == "ı"


def test_table_predicates() -> None:
    columns = Base.metadata.tables["mt_event"].c
    assert KINDS["traffic"].table_predicate("fg_traffic", columns) is None
    dhcp = KINDS["dhcp"].table_predicate("mt_event", columns)
    assert dhcp is not None
    assert "event IN" in str(dhcp.compile(compile_kwargs={"literal_binds": True}))
    with pytest.raises(ValueError, match="no rows"):
        KINDS["traffic"].table_predicate("mt_event", columns)


#: ``(type, subtype)`` pairs the SQL predicates are checked on (NULL and empty subtypes too).
PAIRS = [
    ("traffic", "forward"),
    ("traffic", None),
    ("utm", "webfilter"),
    ("utm", "dns"),
    ("utm", "app-ctrl"),
    ("utm", "ssl"),
    ("utm", "ips"),
    ("utm", "virus"),
    ("utm", "dlp"),
    ("utm", None),
    ("utm", ""),
    ("event", "system"),
    ("dhcp", "assigned"),
    ("dhcp", "pool_empty"),
    ("arp", None),
    ("fw", "forward"),
    ("account", "login"),
    ("interface", "up"),
    ("config", "changed"),
    ("wifi", "connected"),
    ("heartbeat", None),
    ("log", "error"),
    ("syslog", None),
    ("unknown", None),
    ("anomaly", None),
    ("Traffic", None),
]


def test_messages_predicates_select_what_kind_of_says(db_engine: Engine) -> None:
    time = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
    rows: list[dict[str, Any]] = [
        {
            "time": time + timedelta(seconds=number),
            "time_received": time,
            "source": "example",
            "sender": "172.20.10.1",
            "raw_day": DAY,
            "raw_line": number,
            "raw_offset": number * 100,
            "type": type_,
            "subtype": subtype,
            "text": "",
            "extra": {},
        }
        for number, (type_, subtype) in enumerate(PAIRS, start=1)
    ]
    with db_engine.begin() as connection:
        copy_rows(connection, "messages", rows, columns=list(rows[0]))
    table = Message.__table__
    with db_engine.connect() as connection:
        for key, kind in KINDS.items():
            selected = set(
                connection.execute(
                    select(table.c.raw_line).where(kind.messages_predicate(table.c))
                ).scalars()
            )
            expected = {
                number
                for number, (type_, subtype) in enumerate(PAIRS, start=1)
                if kind_of(type_, subtype) == key
            }
            assert selected == expected, key
        total = connection.scalar(select(func.count()).select_from(table))
    assert total == len(PAIRS)
