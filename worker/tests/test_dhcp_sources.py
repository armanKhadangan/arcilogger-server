"""The DHCP source registry (ADR-0016, ``correlation.dhcp_sources``): one loader for both DHCP
tables, the only place outside the table registry that names the MikroTik DHCP table."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from arcilogger_db.settings import set_setting
from arcilogger_worker.correlation import dhcp_sources
from arcilogger_worker.correlation.leases import Context
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)
PACKAGE = Path(dhcp_sources.__file__).resolve().parents[1]
#: Files that define or read the DHCP tables by name (2.6.2): every other module of the lease
#: timeline and the lookup goes through them.
REGISTRY = {
    "correlation/dhcp_sources.py",
    "correlation/watermark.py",
    "lookup/refs.py",
    "lookup/trust.py",
    "lookup/window.py",
}
SPECIAL_CASE = re.compile(r"== DHCP_TABLE\b|[\"']mt_dhcp[\"']|\bMtDhcp\b")


def test_no_mikrotik_dhcp_special_case_outside_the_registry() -> None:
    found = []
    for folder in ("correlation", "lookup"):
        for path in sorted((PACKAGE / folder).glob("*.py")):
            name = f"{folder}/{path.name}"
            if name in REGISTRY:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if SPECIAL_CASE.search(line):
                    found.append(f"{name}:{number}: {line.strip()}")
    assert found == []


def test_rows_of_both_tables_in_the_timelines_order(
    db_session: Session, evidence: Evidence
) -> None:
    at = tr(DAY, 9)
    release = evidence.fortigate_dhcp(
        at, "172.20.42.57", "00:00:5e:00:53:57", logid="0100026002", dhcp_msg="Release", lease=None
    )
    mikrotik = evidence.dhcp(at, "assigned", "172.20.42.57", "00:00:5e:00:53:58", server="dhcp42")
    fortigate = evidence.fortigate_dhcp(at, "172.20.42.57", "00:00:5e:00:53:59", vd=None)
    evidence.dhcp(at, "offering", "172.20.42.60", "00:00:5e:00:53:60", server="dhcp42")
    evidence.fortigate_dhcp(at, "172.20.99.61", "00:00:5e:00:53:61", lease=0)  # no network
    db_session.commit()
    ctx = Context.load(db_session)
    rows = dhcp_sources.load_rows(ctx, tr(DAY, 0), tr(DAY, 12), count=True)
    # Releases first, then the MikroTik table, then the FortiGate table (equal times).
    assert [(row.table, row.id, row.action) for row in rows] == [
        ("fg_dhcp", release, "deassigned"),
        ("mt_dhcp", mikrotik, "assigned"),
        ("fg_dhcp", fortigate, "assigned"),
        ("fg_dhcp", rows[3].id, "assigned"),
    ]
    first, second, third, fourth = rows
    assert (first.origin, first.network, first.server, first.vlan) == (
        ("fg_dhcp", "root"), "172.20.42.0/24", "vlan42", 42,
    )  # fmt: skip
    assert (second.origin, second.network, second.lease_seconds) == (
        ("mt_dhcp", None), "172.20.42.0/24", None,
    )  # fmt: skip
    assert (third.origin, third.lease_seconds) == (("fg_dhcp", None), 86400)
    assert (fourth.network, fourth.vlan, fourth.lease_seconds) == (None, None, None)
    assert {key: ctx.stats[key] for key in sorted(ctx.stats)} == {
        "fortigate_dhcp_no_network": 1,
        "kept_fg_dhcp": 3,
        "lease_fallback": 1,
        "lease_unlimited": 1,
    }
    (event,) = dhcp_sources.events([third])
    assert (event.table, event.lease_seconds, event.origin, event.network, event.ref) == (
        "fg_dhcp", 86400, ("fg_dhcp", None), "172.20.42.0/24", third.ref,
    )  # fmt: skip
    # Without ``count`` nothing is added (the seeds and the look-ahead read rows again).
    dhcp_sources.load_rows(ctx, tr(DAY, 0), tr(DAY, 12))
    assert ctx.stats["kept_fg_dhcp"] == 3


def test_rows_by_address_and_by_mac(db_session: Session, evidence: Evidence) -> None:
    at = tr(DAY, 9)
    evidence.dhcp(at, "assigned", "172.20.42.57", "00:00:5e:00:53:57", server="dhcp42")
    evidence.dhcp(at, "assigned", "172.20.42.58", "00:00:5e:00:53:31", server="dhcp42")
    evidence.fortigate_dhcp(at, "172.20.42.59", "00:00:5e:00:53:31")
    evidence.fortigate_dhcp(at, "172.20.42.60", "00:00:5e:00:53:60")
    db_session.commit()
    ctx = Context.load(db_session)

    def found(ips: list[str], macs: list[str] | None = None) -> list[tuple[str, str]]:
        rows = dhcp_sources.load_rows(ctx, tr(DAY, 0), tr(DAY, 12), ips, macs)
        return [(row.table, row.ip) for row in rows]

    assert found(["172.20.42.57"]) == [("mt_dhcp", "172.20.42.57")]
    # The MAC part reads the FortiGate table only (a MikroTik ACK moves no client, D33).
    assert found(["172.20.42.57"], ["00:00:5e:00:53:31"]) == [
        ("mt_dhcp", "172.20.42.57"),
        ("fg_dhcp", "172.20.42.59"),
    ]
    assert found([], ["00:00:5e:00:53:31"]) == [("fg_dhcp", "172.20.42.59")]
    assert found([]) == []


def test_the_first_evidence_is_usable_evidence(db_session: Session, evidence: Evidence) -> None:
    assert dhcp_sources.first_time(db_session) is None
    evidence.fortigate_dhcp(tr(DAY, 7), "172.20.42.57", "00:00:5e:00:53:57", dhcp_msg="Nak")
    evidence.fortigate_dhcp(tr(DAY, 8), "172.20.42.57", "00:00:5e:00:53:57")
    db_session.commit()
    assert dhcp_sources.first_time(db_session) == tr(DAY, 8)
    set_setting(db_session, "dhcp.fortigate_dhcp_msgs", {}, updated_by="tolga")
    db_session.commit()
    assert dhcp_sources.first_time(db_session) == tr(DAY, 7)
    evidence.dhcp(tr(DAY, 6), "assigned", "172.20.43.57", "00:00:5e:00:53:58")
    db_session.commit()
    assert dhcp_sources.first_time(db_session) == tr(DAY, 6)
