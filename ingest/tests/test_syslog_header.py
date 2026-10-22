"""Tolerant syslog header parsing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from arcilogger_ingest.parsers.syslog import parse_header, parse_time, split_pri

TR = timezone(timedelta(hours=3))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<189>date=x", (23, 5, "date=x")),
        ("<0>x", (0, 0, "x")),
        ("<191>x", (23, 7, "x")),
        ("<192>x", (None, None, "<192>x")),
        ("<1a>x", (None, None, "<1a>x")),
        ("no pri", (None, None, "no pri")),
    ],
)
def test_split_pri(text: str, expected: tuple[object, ...]) -> None:
    assert split_pri(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09-23T09:38:26+03:00", datetime(2026, 9, 23, 9, 38, 26, tzinfo=TR)),
        ("2026-09-23T09:38:26.380+03:00", datetime(2026, 9, 23, 9, 38, 26, 380000, tzinfo=TR)),
        ("2026-09-23T06:38:26Z", datetime(2026, 9, 23, 6, 38, 26, tzinfo=UTC)),
        ("2026-09-23T06:38:26.123456789Z", datetime(2026, 9, 23, 6, 38, 26, 123456, tzinfo=UTC)),
        ("2026-09-23T09:38:26,5+0300", datetime(2026, 9, 23, 9, 38, 26, 500000, tzinfo=TR)),
        ("2026-09-23T09:38:26", None),  # no offset: never guessed
        ("garbage", None),
    ],
)
def test_parse_time(text: str, expected: datetime | None) -> None:
    assert parse_time(text) == expected


def test_mikrotik_style_iso_header() -> None:
    header = parse_header(
        "<30>2026-09-23T09:38:26.380+03:00 CORE-R1 dhcp,info dhcp60 assigned 172.20.60.163"
    )
    assert (header.facility, header.severity) == (3, 6)
    assert header.time == datetime(2026, 9, 23, 9, 38, 26, 380000, tzinfo=TR)
    assert header.host == "CORE-R1"
    assert header.program is None  # topics are not a "tag:" — the MikroTik parser reads them
    assert header.rest == "dhcp,info dhcp60 assigned 172.20.60.163"


def test_bsd_header_with_tag() -> None:
    header = parse_header("<13>Oct  2 08:01:02 switch-1 sshd[42]: Accepted key")
    assert header.time is None
    assert header.time_text == "Oct  2 08:01:02"
    assert (header.host, header.program, header.rest) == ("switch-1", "sshd", "Accepted key")


def test_rfc5424_header() -> None:
    header = parse_header("<165>1 2026-09-23T06:44:57.5Z host app 77 ID47 - the message")
    assert (header.facility, header.severity) == (20, 5)
    assert header.time == datetime(2026, 9, 23, 6, 44, 57, 500000, tzinfo=UTC)
    assert (header.host, header.program, header.rest) == ("host", "app", "the message")
    with_sd = parse_header('<165>1 - - - - - [x a="1"] msg')
    assert (with_sd.time, with_sd.host, with_sd.program, with_sd.rest) == (None, None, None, "msg")


def test_fortigate_has_no_header_after_pri() -> None:
    header = parse_header('<189>date=2026-09-23 time=09:44:57 devname="FIREWALL-01"')
    assert header.time is None
    assert header.time_text is None
    assert header.host is None
    assert header.rest.startswith("date=2026-09-23")
