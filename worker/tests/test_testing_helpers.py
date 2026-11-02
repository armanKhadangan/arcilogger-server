"""The test helper's FortiGate lines: the interfaces and srcmac of ADR-0016 are written only when
given, so every session line written without them stays byte for byte as before; and every
FortiGate DHCP and session line it writes re-parses, with the shipped parser, to the row it
inserted (the report's cross-check re-reads cited lines that way)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_core.rawlog import parse_line as parse_raw
from arcilogger_db.models import FgDhcp, FgTraffic
from arcilogger_ingest.parsers import PARSERS, LinePosition
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)
#: The line ``traffic()`` wrote before ADR-0016, for the same call (after the receive stamp).
PLAIN = (
    '<189>date=2026-10-12 time=11:23:41 devname="FIREWALL-01" devid="FGT0000000000000" '
    'eventtime=1791793421000000000 tz="+0300" logid="0000000013" type="traffic" '
    'subtype="forward" level="notice" vd="root" srcip=172.20.42.57 srcport=51001 '
    "dstip=203.0.113.10 dstport=443 sessionid=51001 proto=6 "
    'action="accept" trandisp="snat" transip=100.64.12.9 transport=51001 duration=10'
)


def _row(session: Session, row_id: int) -> FgTraffic:
    return session.scalars(select(FgTraffic).where(FgTraffic.id == row_id)).one()


def _line(evidence: Evidence, row_id: int) -> str:
    row = _row(evidence.session, row_id)
    raw = evidence.raw_line(row.source, row.raw_day, row.raw_line, row.raw_offset)
    return raw.decode("utf-8")


def test_a_session_line_without_interfaces_is_unchanged(
    db_session: Session, evidence: Evidence
) -> None:
    row_id = evidence.traffic(tr(DAY, 11, 23, 41), "172.20.42.57", 51001)
    assert _line(evidence, row_id).endswith(" " + PLAIN + "\n")
    row = _row(db_session, row_id)
    assert (row.srcintf, row.dstintf) == (None, None)


def test_interfaces_and_srcmac_are_written_when_given(
    db_session: Session, evidence: Evidence
) -> None:
    row_id = evidence.traffic(
        tr(DAY, 11, 23, 41),
        "172.20.42.57",
        51001,
        srcintf="vlan42",
        dstintf="wan1",
        srcmac="00:00:5e:00:53:57",
    )
    line = _line(evidence, row_id)
    assert ' srcport=51001 srcintf="vlan42" dstip=203.0.113.10 ' in line
    assert ' dstport=443 dstintf="wan1" sessionid=51001 ' in line
    assert line.endswith(' duration=10 srcmac="00:00:5e:00:53:57"\n')
    row = _row(db_session, row_id)
    assert (row.srcintf, row.dstintf) == ("vlan42", "wan1")


# -- the re-parse test (ADR-0016): what the helper writes is what the shipped parser makes ------


def _reparsed(evidence: Evidence, row: Any) -> dict[str, Any]:
    """The row the shipped FortiGate parser makes of ``row``'s raw line (as the report's
    cross-check re-reads it)."""
    raw = evidence.raw_line(row.source, row.raw_day, row.raw_line, row.raw_offset)
    outcome = PARSERS["fortigate"].parse(
        parse_raw(raw.rstrip(b"\n")),
        LinePosition(
            source=row.source,
            source_type="fortigate",
            raw_day=row.raw_day,
            raw_line=row.raw_line,
            raw_offset=row.raw_offset,
        ),
    )
    (parsed,) = outcome.rows
    assert parsed.table == row.__tablename__
    return dict(parsed.values)


def _columns(row: Any, *, skip: tuple[str, ...] = ()) -> dict[str, Any]:
    names = [c.key for c in sa_inspect(type(row)).mapper.column_attrs]
    return {name: getattr(row, name) for name in names if name not in ("id", *skip)}


def _comparable(values: dict[str, Any]) -> dict[str, Any]:
    return {key: str(value) if key in ("ip", "srcip", "dstip", "transip", "sender") else value
            for key, value in values.items()}  # fmt: skip


@pytest.mark.parametrize(
    "call",
    [
        {"hostname": "PLN-PC14"},
        {},
        {"hostname": ""},
        {"logid": "0100026002", "dhcp_msg": "Release", "lease": None, "hostname": "PLN-PC14"},
        {"dhcp_msg": "Nak", "mac": "00:00:00:00:00:00"},
        {"device_time": False},
        {"source": "unknown-172.31.254.3", "vd": None, "interface": None},
        {"lease": 0, "dhcp_msg": "", "interface": ""},
    ],
)
def test_a_fortigate_dhcp_line_reparses_to_its_row(
    db_session: Session, evidence: Evidence, call: dict[str, Any]
) -> None:
    mac = str(call.pop("mac", "00:00:5e:00:53:57"))
    row_id = evidence.fortigate_dhcp(tr(DAY, 9, 46, 18), "172.20.42.57", mac, **call)
    row = db_session.scalars(select(FgDhcp).where(FgDhcp.id == row_id)).one()
    assert _comparable(_columns(row)) == _comparable(_reparsed(evidence, row))
    assert row.device_time is call.get("device_time", True)
    assert row.time == tr(DAY, 9, 46, 18)


def test_a_fortigate_dhcp_line_needs_its_dhcp_msg(evidence: Evidence) -> None:
    with pytest.raises(ValueError, match="dhcp_msg"):
        evidence.fortigate_dhcp(tr(DAY, 9), "172.20.42.57", "00:00:5e:00:53:57", dhcp_msg=None)


def test_a_session_line_reparses_to_its_row_srcmac_included(
    db_session: Session, evidence: Evidence
) -> None:
    row_id = evidence.traffic(
        tr(DAY, 11, 23, 41),
        "172.20.42.57",
        51001,
        srcintf="vlan42",
        dstintf="wan1",
        srcmac="00:00:5E:00:53:57",
    )
    row = _row(db_session, row_id)
    assert row.srcmac == "00:00:5e:00:53:57"
    parsed = _reparsed(evidence, row)
    # The helper leaves the untyped fields out of ``extra``; every typed column agrees.
    assert _comparable(_columns(row, skip=("extra",))) == _comparable(
        {key: value for key, value in parsed.items() if key != "extra"}
    )
