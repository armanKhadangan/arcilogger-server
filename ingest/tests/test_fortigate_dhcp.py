"""FortiGate DHCP server lines (``fg_dhcp``) and ``srcmac`` (ADR-0016; contract 2.3, 3.1).

The synthetic lines after the ADR-0016 comment of ``fortigate-synthetic.log`` are built from the
FortiOS 7.6 field list and one community sample; every value is a placeholder (sahada doğrula).
Golden values were read off those lines by hand. Refusing a row (a ``Nak``, a zero MAC, a
receive time) is the worker's job: here every such line still parses to ``fg_dhcp``.

Two tests are **pin tests** only: they keep the shipped defaults, the fixtures and the documented
field list together, and cannot detect a wrong assumption about a real FortiGate. The one test
that can is :func:`test_real_fortigate_dhcp_sample`, skipped until a masked device capture
exists; at run time the R20-lease alerts and the "not verified on site" state carry it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.rawlog import RawRecord
from arcilogger_db.rowcheck import check_row
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_ingest.parsers import LinePosition, ParseOutcome
from arcilogger_ingest.parsers.fortigate import (
    DHCP_KEYS,
    FG_DHCP,
    SPECS,
    FortiGateParser,
    eventtime_utc,
    local_time,
)
from arcilogger_ingest.parsers.kv import tokenize
from arcilogger_ingest.parsers.values import to_mac
from arcilogger_ingest.testing import (
    DAY,
    World,
    fortigate_dhcp_message,
    fortigate_srcmac_message,
    write_realistic_spool,
)

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures"
#: Awaited: the masked first device capture (tests/fixtures/format/README.md).
REAL_SAMPLE = FIXTURES / "format" / "fortigate-dhcp-sample.log"
_ALL = (FIXTURES / "synthetic" / "fortigate-synthetic.log").read_bytes().splitlines()
_COMMENT = next(i for i, line in enumerate(_ALL) if line.startswith(b"# ADR-0016"))
#: The 13 lines of contract 2.3, in their order (appended after the comment, never inserted).
LINES = _ALL[_COMMENT + 1 :]
(
    ACK,
    RENEWAL,
    RELEASE,
    ACK_LOWER_NO_HOST,
    ACK_EMPTY_HOST,
    ACK_VENDOR,
    STAT,
    DHCPV6,
    TRAFFIC_SRCMAC,
    TRAFFIC_EMPTY_SRCMAC,
    NAK,
    ZERO_MAC,
    TWO_DAYS_OFF,
) = LINES
FORMAT_SAMPLE_CLOSE = (FIXTURES / "format" / "fortigate-sample.log").read_bytes().splitlines()[6]

RECEIVED = datetime(2026, 9, 23, 6, 44, 58, 500000, tzinfo=UTC)
PARSER = FortiGateParser()
COMMON_EXTRA = {"devid": "FGT0000000000000", "vd": "root"}
#: The raw-reference columns ``Parser.parse`` adds (tested in test_parser_contract.py).
FRAMEWORK = {"time", "time_received", "source", "sender", "raw_day", "raw_line", "raw_offset"}
#: FortiOS 7.6 log reference, 26001 ``LOG_ID_DHCP_ACK`` (contract F1), written out by hand.
FIELDS_26001 = frozenset(
    {
        "date", "devid", "dhcp_msg", "eventtime", "hostname", "interface", "ip", "lease",
        "level", "logdesc", "logid", "mac", "msg", "subtype", "time", "type", "tz", "vd",
    }
)  # fmt: skip
#: 26002 ``LOG_ID_DHCP_RELEASE``: the fields of 26001 without ``lease`` (F3).
FIELDS_26002 = FIELDS_26001 - {"lease"}


def parse(message: bytes, received: datetime = RECEIVED) -> ParseOutcome:
    record = RawRecord(received=received, sender="172.31.255.1", message=message)
    position = LinePosition("fortigate", "fortigate", date(2026, 9, 23), 1, 0)
    outcome = PARSER.parse(record, position)
    for row in outcome.all_rows():
        assert check_row(row.table, row.values, complete=True) == [], row
    return outcome


def typed(outcome: ParseOutcome) -> tuple[str, dict[str, Any]]:
    """The one typed row without the framework columns."""
    (row,) = outcome.rows
    return row.table, {k: v for k, v in row.values.items() if k not in FRAMEWORK}


def dhcp(**values: Any) -> dict[str, Any]:
    """Every ``fg_dhcp`` column (``None``) with the values every synthetic ACK shares."""
    base: dict[str, Any] = dict.fromkeys(column for column, _, _ in FG_DHCP)
    base |= {
        "logid": "0100026001",
        "subtype": "system",
        "level": "information",
        "logdesc": "DHCP Ack log",
        "dhcp_msg": "Ack",
        "lease": 86400,
        "msg": "DHCP server sends a DHCPACK",
        "device_time": True,
        "extra": COMMON_EXTRA,
    }
    unknown = set(values) - set(base)
    assert not unknown, unknown
    return base | values


def utc(day: int, hour: int, minute: int, second: int) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=UTC)


GOLDEN: list[tuple[bytes, datetime, dict[str, Any]]] = [
    (
        ACK,
        utc(23, 6, 46, 18),
        dhcp(interface="vlan42", ip="172.20.42.57", mac="00:00:5e:00:53:57", hostname="PLN-PC14"),
    ),
    (
        RENEWAL,  # the same pair 12 h later
        utc(23, 18, 46, 18),
        dhcp(interface="vlan42", ip="172.20.42.57", mac="00:00:5e:00:53:57", hostname="PLN-PC14"),
    ),
    (
        RELEASE,
        utc(23, 18, 46, 40),
        dhcp(
            logid="0100026002",
            logdesc="DHCP Release log",
            dhcp_msg="Release",
            interface="vlan42",
            ip="172.20.42.57",
            mac="00:00:5e:00:53:57",
            hostname="PLN-PC14",
            lease=None,  # 26002 has no lease field
            msg="sentetik: DHCP release",
        ),
    ),
    (
        ACK_LOWER_NO_HOST,
        utc(23, 6, 46, 20),
        dhcp(interface="vlan41", ip="172.20.41.141", mac="00:00:5e:00:53:41"),
    ),
    (
        ACK_EMPTY_HOST,
        utc(23, 6, 46, 21),
        dhcp(
            interface="vlan43",
            ip="172.20.43.150",
            mac="00:00:5e:00:53:4a",
            extra={**COMMON_EXTRA, "hostname": ""},  # empty: NULL, kept as sent
        ),
    ),
    (
        ACK_VENDOR,
        utc(23, 6, 46, 22),
        dhcp(
            interface="vlan60",
            ip="172.20.60.61",
            mac="00:00:5e:00:53:61",
            hostname="TEL-61",
            extra={**COMMON_EXTRA, "vendor": "sentetik"},  # FortiOS 8.0's field
        ),
    ),
    (
        NAK,
        utc(23, 6, 46, 27),
        dhcp(
            dhcp_msg="Nak",
            interface="vlan42",
            ip="172.20.42.58",
            mac="00:00:5e:00:53:58",
            msg="sentetik: DHCP NAK",
        ),
    ),
    (
        ZERO_MAC,
        utc(23, 6, 46, 28),
        dhcp(interface="vlan42", ip="172.20.42.59", mac="00:00:00:00:00:00"),
    ),
    (
        TWO_DAYS_OFF,
        RECEIVED,  # the device time lies two days before the receive time
        dhcp(
            interface="vlan42",
            ip="172.20.42.62",
            mac="00:00:5e:00:53:62",
            hostname="PLN-PC16",
            device_time=False,
            extra={
                **COMMON_EXTRA,
                "eventtime": "1789973189000000000",
                "date": "2026-09-21",
                "time": "09:46:29",
                "tz": "+0300",
            },
        ),
    ),
]


def test_the_synthetic_lines_are_the_thirteen_of_the_contract() -> None:
    assert len(LINES) == 13
    assert all(line.startswith(b"<") for line in LINES)


@pytest.mark.parametrize(
    ("raw", "time", "expected"), GOLDEN, ids=[f"line{LINES.index(g[0]) + 1}" for g in GOLDEN]
)
def test_each_dhcp_line_gives_its_row(raw: bytes, time: datetime, expected: dict[str, Any]) -> None:
    outcome = parse(raw)
    assert typed(outcome) == ("fg_dhcp", expected)
    message = outcome.message
    assert (message["type"], message["subtype"]) == ("event", "system")
    assert message["severity"] == 6  # <190> = local7.information
    assert message["host"] == "FIREWALL-01"
    assert message["time"] == outcome.rows[0].values["time"] == time
    assert outcome.error == (None if expected["device_time"] else "fortigate.time")


def test_the_two_days_off_line_keeps_the_device_text() -> None:
    outcome = parse(TWO_DAYS_OFF)
    # eventtime is rejected first, then date/time/tz: the last rejected text is kept.
    assert outcome.detail == "implausible device time: 2026-09-21 09:46:29 +0300"
    assert outcome.message["extra"] == {"device_time": "2026-09-21 09:46:29 +0300"}


@pytest.mark.parametrize(
    "mac", ["00:00:5E:00:53:57", "00:00:5e:00:53:57", "00-00-5E-00-53-57", "00-00-5e-00-53-57"]
)
def test_the_mac_is_lower_case_with_colons(mac: str) -> None:
    line = ACK.replace(b'mac="00:00:5E:00:53:57"', f'mac="{mac}"'.encode())
    table, values = typed(parse(line))
    assert (table, values["mac"]) == ("fg_dhcp", "00:00:5e:00:53:57")
    assert "mac" not in values["extra"]


def test_an_unquoted_mac_and_a_quoted_ip_give_the_same_row() -> None:
    line = ACK.replace(b'mac="00:00:5E:00:53:57"', b"mac=00:00:5E:00:53:57").replace(
        b"ip=172.20.42.57", b'ip="172.20.42.57"'
    )
    assert typed(parse(line)) == typed(parse(ACK))


def test_a_lease_that_is_no_number_is_null_and_a_value_failure() -> None:
    outcome = parse(ACK.replace(b"lease=86400", b'lease="abc"'))
    table, values = typed(outcome)
    assert (table, values["lease"], values["extra"]["lease"]) == ("fg_dhcp", None, "abc")
    assert (outcome.error, outcome.detail) == ("fortigate.value", "invalid value: lease")
    assert values["device_time"] is True


def test_an_empty_lease_and_a_lease_of_zero() -> None:
    empty = typed(parse(ACK.replace(b"lease=86400", b"lease=")))[1]
    assert (empty["lease"], empty["extra"]["lease"]) == (None, "")
    unlimited = typed(parse(ACK.replace(b"lease=86400", b"lease=0")))[1]
    assert unlimited["lease"] == 0  # FortiOS "unlimited": the worker decides (D6)


@pytest.mark.parametrize(
    ("old", "new", "validity"),
    [
        (b"ip=172.20.42.57", b'ip="n/a"', "ip=invalid, mac=valid"),
        (b"ip=172.20.42.57", b"ip=", "ip=empty, mac=valid"),
        (b'mac="00:00:5E:00:53:57"', b'mac=""', "ip=valid, mac=empty"),
        (b'mac="00:00:5E:00:53:57"', b'mac="00:00-5E:00:53:57"', "ip=valid, mac=invalid"),
        (b'mac="00:00:5E:00:53:57"', b'mac="00:00:5E:00:53:57:01"', "ip=valid, mac=invalid"),
        (b"ip=172.20.42.57", b"ip=172.20.42.057", "ip=invalid, mac=valid"),
    ],
)
def test_a_dhcp_line_without_a_usable_ip_or_mac_is_an_event_and_a_failure(
    old: bytes, new: bytes, validity: str
) -> None:
    line = ACK.replace(old, new)
    assert line != ACK
    outcome = parse(line)
    table, values = typed(outcome)
    assert table == "fg_event"
    assert (outcome.error, outcome.detail) == (
        "fortigate.dhcp",
        f"DHCP event without a usable ip/mac: {validity}",
    )
    # Nothing is lost: the DHCP fields stay in extra as sent, and the detail names no value.
    sent = tokenize(line.decode().split(">", 1)[1]).fields
    for key in ("ip", "mac", "dhcp_msg", "interface", "hostname", "lease"):
        assert values["extra"][key] == sent[key], key
    for key in ("ip", "mac"):
        assert not sent[key] or sent[key] not in (outcome.detail or ""), key
    assert values["logid"] == "0100026001"
    assert values["msg"] == "DHCP server sends a DHCPACK"


def test_two_of_the_three_keys_are_an_ordinary_event() -> None:
    for key in DHCP_KEYS:
        line = b" ".join(
            part for part in ACK.split(b" ") if not part.startswith(key.encode() + b"=")
        )
        outcome = parse(line)
        assert typed(outcome)[0] == "fg_event", key
        assert outcome.error is None, key


@pytest.mark.parametrize(
    ("name", "line"),
    [
        ("26003 statistics", STAT),
        ("26011 DHCPv6", DHCPV6),
        (
            "26007-like: mac without ip",
            b'<190>date=2026-09-23 time=09:46:30 devname="FIREWALL-01" devid="FGT0000000000000" '
            b'eventtime=1790145990000000000 tz="+0300" logid="0100026007" type="event" '
            b'subtype="system" level="information" vd="root" logdesc="sentetik: blocked MAC" '
            b'interface="vlan42" mac="00:00:5E:00:53:63" msg="sentetik: DHCP blocked MAC"',
        ),
        (
            "26008-like DDNS: ip and dhcp_msg without mac",
            b'<190>date=2026-09-23 time=09:46:31 devname="FIREWALL-01" devid="FGT0000000000000" '
            b'eventtime=1790145991000000000 tz="+0300" logid="0100026008" type="event" '
            b'subtype="system" level="information" vd="root" logdesc="sentetik: DDNS" '
            b'interface="vlan42" dhcp_msg="DDNS" ip=172.20.42.57 msg="sentetik: DDNS update"',
        ),
    ],
)
def test_other_dhcp_messages_stay_events_without_a_failure(name: str, line: bytes) -> None:
    outcome = parse(line)
    table, values = typed(outcome)
    assert table == "fg_event", name
    assert outcome.error is None, name
    assert values["logid"].startswith("01000260"), name
    sent = tokenize(line.decode().split(">", 1)[1]).fields
    for key in ("interface", "ip", "mac", "dhcp_msg", "duid", "used"):
        if key in sent:
            assert values["extra"][key] == sent[key], (name, key)


def test_the_dhcpv6_shape_keeps_its_fields_in_extra() -> None:
    values = typed(parse(DHCPV6))[1]
    assert values["extra"] == {
        **COMMON_EXTRA,
        "interface": "vlan42",
        "dhcp_msg": "Reply",
        "duid": "00:01:00:01:2c:00:00:00:00:00:5e:00:53:57",
        "iaid": "1",
        "ip": "2001:db8::57",
        "lease": "86400",
    }


def test_an_ipv6_address_with_a_mac_is_an_fg_dhcp_row() -> None:
    """INET takes it; the worker ignores IPv6 leases (contract 2.6)."""
    line = ACK.replace(b"ip=172.20.42.57", b"ip=2001:DB8:0:0::57")
    table, values = typed(parse(line))
    assert (table, values["ip"], values["mac"]) == ("fg_dhcp", "2001:db8::57", "00:00:5e:00:53:57")


def _without(line: bytes, *keys: str) -> bytes:
    return b" ".join(
        part
        for part in line.split(b" ")
        if not any(part.startswith(f"{k}=".encode()) for k in keys)
    )


@pytest.mark.parametrize(
    ("name", "line", "device_time", "time"),
    [
        ("eventtime and date/time/tz", ACK, True, utc(23, 6, 46, 18)),
        ("eventtime only", _without(ACK, "date", "time", "tz"), True, utc(23, 6, 46, 18)),
        ("date/time/tz only", _without(ACK, "eventtime"), True, utc(23, 6, 46, 18)),
        ("two days off", TWO_DAYS_OFF, False, RECEIVED),
        ("no time field", _without(ACK, "eventtime", "date", "time", "tz"), False, RECEIVED),
        ("date/time without tz", _without(ACK, "eventtime", "tz"), False, RECEIVED),
    ],
)
def test_device_time_says_whose_time_it_is(
    name: str, line: bytes, device_time: bool, time: datetime
) -> None:
    outcome = parse(line)
    table, values = typed(outcome)
    assert (table, values["device_time"]) == ("fg_dhcp", device_time), name
    assert outcome.message["time"] == time, name


def test_device_time_follows_the_parsers_clock_skew() -> None:
    """The same rule as every line: a wider ``max_clock_skew`` accepts the two-day-old time."""
    record = RawRecord(received=RECEIVED, sender="172.31.255.1", message=TWO_DAYS_OFF)
    position = LinePosition("fortigate", "fortigate", date(2026, 9, 23), 1, 0)
    wide = FortiGateParser(max_clock_skew=timedelta(days=3)).parse(record, position)
    assert wide.rows[0].values["device_time"] is True
    assert wide.message["time"] == utc(21, 6, 46, 29)


@pytest.mark.parametrize("raw", LINES, ids=[f"line{i + 1}" for i in range(len(LINES))])
def test_exactly_one_typed_row_and_nothing_is_lost(raw: bytes) -> None:
    outcome = parse(raw)
    assert [row.table for row in outcome.all_rows()].count("messages") == 1
    table, values = typed(outcome)
    fields = tokenize(raw.decode().split(">", 1)[1]).fields
    represented = {"type", "devname"}
    if outcome.message["time"] != RECEIVED:
        represented |= {"eventtime", "date", "time", "tz"}
    mapped = {key: (column, kind) for column, key, kind in SPECS[table]}
    for key, value in fields.items():
        if key in values["extra"]:
            assert values["extra"][key] == value, key
        elif key in mapped:
            column, kind = mapped[key]
            expected = to_mac(value) if kind == "mac" else value
            assert str(values[column]) == expected or values[column] == expected, key
        else:
            assert key in represented, key


# -- srcmac (fg_traffic) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "mac", ["00:00:5E:00:53:57", "00:00:5e:00:53:57", "00-00-5E-00-53-57", "00-00-5e-00-53-57"]
)
def test_srcmac_in_every_form_is_the_normalised_column(mac: str) -> None:
    line = TRAFFIC_SRCMAC.replace(b' srcmac="00:00:5e:00:53:57"', f' srcmac="{mac}"'.encode())
    outcome = parse(line)
    table, values = typed(outcome)
    assert (table, values["srcmac"], outcome.error) == ("fg_traffic", "00:00:5e:00:53:57", None)
    assert "srcmac" not in values["extra"]
    # mastersrcmac and srcmacvendor are not columns: kept as sent
    assert values["extra"]["mastersrcmac"] == "00:00:5e:00:53:57"
    assert values["extra"]["srcmacvendor"] == "sentetik"


def test_a_srcmac_that_is_no_mac_stays_in_extra() -> None:
    outcome = parse(TRAFFIC_SRCMAC.replace(b' srcmac="00:00:5e:00:53:57"', b' srcmac="n/a"'))
    table, values = typed(outcome)
    assert (table, values["srcmac"], values["extra"]["srcmac"]) == ("fg_traffic", None, "n/a")
    assert (outcome.error, outcome.detail) == ("fortigate.value", "invalid value: srcmac")


def test_an_empty_srcmac_is_null_without_a_failure() -> None:
    outcome = parse(TRAFFIC_EMPTY_SRCMAC)
    table, values = typed(outcome)
    assert (table, values["srcmac"], values["extra"]["srcmac"]) == ("fg_traffic", None, "")
    assert outcome.error is None


def test_every_traffic_row_has_the_same_columns_with_or_without_srcmac() -> None:
    """``copy_rows`` takes its column list from the first row of a batch (the template)."""
    rows = [parse(line).rows[0] for line in (TRAFFIC_SRCMAC, TRAFFIC_EMPTY_SRCMAC)]
    rows.append(parse(FORMAT_SAMPLE_CLOSE, datetime(2026, 9, 23, 6, 45, tzinfo=UTC)).rows[0])
    assert b"srcmac=" not in FORMAT_SAMPLE_CLOSE
    assert {row.table for row in rows} == {"fg_traffic"}
    assert len({frozenset(row.values) for row in rows}) == 1
    assert rows[2].values["srcmac"] is None


# -- pin tests and the format-sample guard ---------------------------------------------------


def test_fortigate_dhcp_logid_defaults_match_the_fixtures() -> None:
    """Pin test: keeps the shipped defaults and the synthetic lines together. It cannot detect a
    wrong assumption about a real FortiGate (the lines were built from the same reference); the
    default logids and ``dhcp_msg`` values change only after a real capture (DEVICE-SETUP.md
    section 2.4, KURULUM.md section 16), together with these lines."""
    logids = DEFAULT_SETTINGS["dhcp.fortigate_logids"]
    assert logids == {"0100026001": "assigned", "0100026002": "deassigned"}
    assert DEFAULT_SETTINGS["dhcp.fortigate_dhcp_msgs"] == {"0100026001": ["Ack"]}
    rows = [typed(parse(line))[1] for line in (ACK, RENEWAL, RELEASE)]
    assert {row["logid"] for row in rows} == set(logids)
    assert rows[0]["dhcp_msg"] in DEFAULT_SETTINGS["dhcp.fortigate_dhcp_msgs"][rows[0]["logid"]]


def test_routing_shape_is_the_documented_field_list() -> None:
    """Pin test: the routing keys are fields that 26001 and 26002 both carry (FortiOS 7.6 log
    reference, written out above); it cannot tell whether a real line carries them."""
    assert set(DHCP_KEYS) == {"ip", "mac", "dhcp_msg"}
    assert set(DHCP_KEYS) <= FIELDS_26001 & FIELDS_26002
    assert {key for _, key, _ in FG_DHCP} <= FIELDS_26001
    # Every other documented key is the envelope (time, type, devname) or stays in extra.
    assert FIELDS_26001 - {key for _, key, _ in FG_DHCP} == {
        "date", "devid", "eventtime", "time", "type", "tz", "vd",
    }  # fmt: skip


def _device_time(line: bytes) -> datetime:
    fields = tokenize(line.decode().split(">", 1)[-1]).fields
    found = eventtime_utc(fields.get("eventtime", "")) or local_time(
        fields.get("date", ""), fields.get("time", ""), fields.get("tz")
    )
    assert found is not None, "a device line carries its time"
    return found


@pytest.mark.skipif(
    not REAL_SAMPLE.exists(), reason="sahada doğrula: gerçek FortiGate DHCP satırı henüz yok"
)
def test_real_fortigate_dhcp_sample(record_property: Any) -> None:
    """The one assumption test (contract 3.1): the masked device capture routes as assumed."""
    lines = [
        line for line in REAL_SAMPLE.read_bytes().splitlines() if line and not line.startswith(b"#")
    ]
    assert lines
    logids: dict[str, str] = DEFAULT_SETTINGS["dhcp.fortigate_logids"]
    allowed: dict[str, list[str]] = DEFAULT_SETTINGS["dhcp.fortigate_dhcp_msgs"]
    release_values: set[str] = set()
    for line in lines:
        outcome = parse(line, received=_device_time(line) + timedelta(seconds=1))
        table, values = typed(outcome)
        assert table == "fg_dhcp", outcome.detail
        assert values["device_time"] is True
        assert values["logid"] in logids
        if values["logid"] in allowed:
            assert values["dhcp_msg"].casefold() in {m.casefold() for m in allowed[values["logid"]]}
        if logids[values["logid"]] == "assigned":
            assert isinstance(values["lease"], int)
            assert values["lease"] == 0 or 300 <= values["lease"] <= 8_640_000  # F8; 0 unlimited
        else:
            release_values.add(values["dhcp_msg"])
    record_property("release_dhcp_msg", sorted(release_values))


# -- the fortigate-only benchmark mix (bench_throughput.py --mix, docs/INGEST.md) -------------


def test_the_benchmark_mix_lines_are_srcmac_traffic_and_dhcp_rows() -> None:
    for index in range(30):
        traffic = typed(parse(fortigate_srcmac_message(index)))
        assert traffic[0] == "fg_traffic"
        assert traffic[1]["srcmac"] == f"00:00:5e:00:53:{index % 250:02x}"
        outcome = parse(fortigate_dhcp_message(index))
        assert outcome.error is None
        table, values = typed(outcome)
        assert (table, values["device_time"]) == ("fg_dhcp", True)
        release = index % 10 == 9
        assert values["logid"] == ("0100026002" if release else "0100026001")
        assert values["lease"] == (None if release else 86400)
        assert values["ip"] == f"172.20.42.{100 + index % 150}"


def test_the_fortigate_only_mix_writes_seventy_thirty_into_the_fortigate_file(
    tmp_path: Path,
) -> None:
    world = World.create(tmp_path)
    size = write_realistic_spool(world, DAY, 200, mix="fortigate-only")
    (path,) = sorted(path for path in world.layout.spool.glob("*/*") if path.is_file())
    assert path == world.layout.spool_file("fortigate", DAY)
    lines = path.read_bytes().splitlines(keepends=True)
    assert (len(lines), sum(map(len, lines))) == (200, size)
    assert sum(b'type="traffic"' in line and b" srcmac=" in line for line in lines) == 140
    assert sum(b' dhcp_msg="' in line for line in lines) == 60
    with pytest.raises(ValueError, match="unknown mix"):
        write_realistic_spool(world, DAY, 1, mix="mikrotik-only")
