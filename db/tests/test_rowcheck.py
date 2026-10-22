"""Row validation against column types (protects batch COPY from one bad value)."""

from __future__ import annotations

import ipaddress
import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from arcilogger_db.models import TIME_SERIES_TABLES, Base, MtDhcp
from arcilogger_db.rowcheck import check_row, required_columns, table_columns

NOW = datetime(2026, 10, 12, 15, 19, 7, tzinfo=UTC)
COMMON = {
    "time": NOW,
    "time_received": NOW,
    "source": "core-r1",
    "sender": "172.20.10.1",
    "raw_day": date(2026, 10, 12),
    "raw_line": 7,
    "raw_offset": 1234,
}
DHCP = {
    **COMMON,
    "server": "dhcp60",
    "vlan": 60,
    "action": "assigned",
    "ip": "172.20.60.183",
    "mac": "02:00:5E:00:53:46",
    "hostname": "Telefon-12",
    "extra": {"note": "x"},
}


def test_valid_row_has_no_problems() -> None:
    assert check_row("mt_dhcp", DHCP, complete=True) == []
    assert (
        check_row("fg_dns", {**COMMON, "answers": ["1.1.1.1", ipaddress.ip_address("::1")]}) == []
    )


@pytest.mark.parametrize(
    ("column", "value", "problem"),
    [
        ("vlan", "51", "expected int, got str"),
        ("vlan", True, "expected int, got bool"),
        ("vlan", 2**31, "out of range"),
        ("raw_offset", 2**63, "out of range"),
        ("ip", "172.20.60.300", "invalid IP address"),
        ("mac", "02:00:5E:00:53", "invalid MAC address"),
        ("hostname", "a\x00b", "NUL character"),
        ("hostname", 5, "expected str"),
        ("time", datetime(2026, 10, 12, 18, 0), "naive datetime"),  # noqa: DTZ001
        ("raw_day", NOW, "expected date, got datetime"),
        ("extra", {"x": float("nan")}, "not JSON-serialisable"),
        ("extra", {"x": "\x00"}, "NUL character"),
        ("extra", {"x": object()}, "not JSON-serialisable"),
        ("server", None, "NULL not allowed"),
        ("unknown_field", 1, "unknown column"),
    ],
)
def test_bad_values_are_reported(column: str, value: object, problem: str) -> None:
    problems = check_row("mt_dhcp", {**DHCP, column: value})
    assert len(problems) == 1, problems
    assert problems[0].startswith(f"mt_dhcp.{column}: ")
    assert problem in problems[0]


def test_integer_ranges_follow_the_column_type() -> None:
    assert check_row("fg_traffic", {"proto": 2**15}) == [
        "fg_traffic.proto: 32768 out of range [-32768, 32767]"
    ]
    assert check_row("fg_traffic", {"sessionid": 2**40}) == []
    assert check_row("fg_traffic", {"srcport": 2**31}) != []


def test_arrays_cidr_and_booleans() -> None:
    assert check_row("fg_dns", {"answers": "1.1.1.1"}) == [
        "fg_dns.answers: expected a list, got str"
    ]
    assert check_row("fg_dns", {"answers": ["1.1.1.1", "nope"]})[0].startswith(
        "fg_dns.answers: [1] invalid IP address"
    )
    assert check_row("vlans", {"subnet": "172.20.10.0/24"}) == []
    assert "invalid network" in check_row("vlans", {"subnet": "172.20.10.1/24"})[0]
    assert check_row("fg_traffic", {"is_interim": 1}) == [
        "fg_traffic.is_interim: expected bool, got int"
    ]
    assert check_row("usage_domain_daily", {"seconds": 12.5}) == []
    assert check_row("usage_domain_daily", {"seconds": 12}) == []
    assert check_row("usage_domain_daily", {"seconds": True}) == [
        "usage_domain_daily.seconds: expected float, got bool"
    ]
    assert check_row("usage_domain_daily", {"seconds": float("nan")}) == [
        "usage_domain_daily.seconds: nan is not a finite number"
    ]


def test_unknown_table_and_missing_required_columns() -> None:
    assert check_row("nope", {}) == ["unknown table 'nope'"]
    problems = check_row("mt_dhcp", {"ip": "172.31.254.1"}, complete=True)
    assert "mt_dhcp.mac: missing required column" in problems
    assert "mt_dhcp.raw_line: missing required column" in problems
    assert not any("extra" in p or ".id:" in p for p in problems)  # defaults / identity


def test_required_columns_of_time_series_tables() -> None:
    for table in TIME_SERIES_TABLES:
        assert {"time", "time_received", "source", "sender", "raw_day", "raw_line"} <= (
            required_columns(table)
        )
        assert "id" not in required_columns(table)
    assert table_columns("missing") == {}


def test_every_column_type_has_a_rule() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            # None is always "fine" for type purposes; a non-None sample must be judged.
            result = check_row(table.name, {column.name: object()})
            assert result, (table.name, column.name)
            assert "unsupported column type" not in result[0], (table.name, column.name)


#: Spellings PostgreSQL accepts (documented layouts) and ones it rejects (review findings).
MAC_SAMPLES = (
    "02:00:5E:00:53:46",
    "02-00-5e-00-53-46",
    "02005e:005346",
    "02005E-005346",
    "0200.5e00.5346",
    "0200-5e00-5346",
    "02005e005346",
    "0:8:0:0:2:b:0:1:0:2:0:3",
    "08:002b:01:0203",
    "08-00:2b-01:02-03",
    "02:00:5e:00:53",
    "02:00:5e:00:53:46:11",
    "gg:00:5e:00:53:46",
    " 02:00:5e:00:53:46",
    "0200.5e00.532",
)
INET_SAMPLES = (
    "172.20.60.183",
    "172.20.60.183/24",
    "::1",
    "2001:db8::1/64",
    "::ffff:172.31.254.1",
    "fe80::1%eth0",
    "fe80::1%25",
    "172.20.60.300",
    "172.20.1",
    "1.2.3.4/33",
    "",
)


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        ("0200.5e00.5346", True),
        ("02005e-005346", True),
        ("0:8:0:0:2:b:0:1:0:2:0:3", False),
        ("08:002b:01:0203", False),
        ("08-00:2b-01:02-03", False),
    ],
)
def test_mac_layouts(value: str, accepted: bool) -> None:
    assert (check_row("mt_arp", {"mac": value}) == []) is accepted


def test_ipv6_zone_ids_are_rejected() -> None:
    assert "zone ids" in check_row("mt_arp", {"ip": "fe80::1%eth0"})[0]
    assert "zone ids" in check_row("fg_dns", {"answers": ["fe80::1%25"]})[0]
    assert "zone ids" in check_row("vlans", {"subnet": "fe80::%1/64"})[0]


def _postgres_accepts(session: Session, value: str, sql_type: str) -> bool:
    with session.begin_nested() as savepoint:
        try:
            session.execute(text(f"SELECT CAST(:value AS {sql_type})"), {"value": value})
        except DBAPIError:
            savepoint.rollback()
            return False
    return True


def test_check_row_is_never_looser_than_postgresql(db_session: Session) -> None:
    """Whatever ``check_row`` accepts must load: one bad value would stall a whole COPY."""
    for column, sql_type, samples in (
        ("mac", "macaddr", MAC_SAMPLES),
        ("ip", "inet", INET_SAMPLES),
    ):
        for value in samples:
            if check_row("mt_arp", {column: value}) == []:
                assert _postgres_accepts(db_session, value, sql_type), (column, value)
    # The documented layouts are all accepted by both.
    for value in MAC_SAMPLES[:7]:
        assert check_row("mt_arp", {"mac": value}) == []
        assert _postgres_accepts(db_session, value, "macaddr")


def test_checked_row_really_loads(db_session: Session) -> None:
    assert check_row("mt_dhcp", DHCP, complete=True) == []
    db_session.add(MtDhcp(**DHCP))
    db_session.commit()


def test_json_nul_rule_matches_postgresql(db_session: Session) -> None:
    """A real NUL is refused (as by jsonb); the text backslash-u-0000 is ordinary text."""
    literal = {"0": "\\u0000", "\\u0000": ["a\\u0000b"]}
    assert check_row("mt_event", {"extra": literal}) == []
    assert _postgres_accepts(db_session, json.dumps(literal), "jsonb")
    for nul in ({"x": "\x00"}, {"\x00": 1}, {"x": ["ok", {"y": "a\x00"}]}):
        assert "NUL" in check_row("mt_event", {"extra": nul})[0]
        assert not _postgres_accepts(db_session, json.dumps(nul), "jsonb")
