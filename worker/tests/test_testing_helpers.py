"""The test helper's FortiGate session lines: the interfaces and srcmac of ADR-0016 are written
only when given, so every line written without them stays byte for byte as before."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_db.models import FgTraffic
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
