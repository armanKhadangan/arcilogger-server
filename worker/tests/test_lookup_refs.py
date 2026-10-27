"""Evidence references of the DHCP tables (ADR-0016): the FortiGate DHCP summary, the action
and IP read from the fixed part of any DHCP summary, and a lease's own last DHCP evidence."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from arcilogger_worker.lookup.refs import (
    DHCP_TABLE,
    DHCP_TABLES,
    FG_DHCP_TABLE,
    TABLE_RANK,
    dhcp_action,
    dhcp_ip,
    dhcp_ref,
    fg_dhcp_ref,
    last_dhcp_evidence,
)
from arcilogger_worker.lookup.types import EvidenceRef

T0 = datetime(2026, 10, 12, 5, 41, 10, tzinfo=UTC)


def _row(**fields: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": 77,
        "time": T0,
        "source": "fortigate",
        "raw_day": date(2026, 10, 12),
        "raw_line": 9,
        "raw_offset": 2048,
        "interface": "vlan42",
        "ip": "172.20.42.57",
        "mac": "00:00:5e:00:53:57",
        "hostname": "PLN-PC14",
        "logid": "0100026001",
        "lease": 604800,
    }
    return SimpleNamespace(**(base | fields))


def _ref(table: str, minutes: int, summary: str = "") -> EvidenceRef:
    return EvidenceRef(table, minutes, T0 + timedelta(minutes=minutes), None, summary)


def test_the_dhcp_tables_are_defined_once_with_their_rank() -> None:
    assert DHCP_TABLES == (DHCP_TABLE, FG_DHCP_TABLE) == ("mt_dhcp", "fg_dhcp")
    assert TABLE_RANK == {"mt_dhcp": 0, "fg_dhcp": 1}


def test_the_fortigate_dhcp_summary() -> None:
    ref = fg_dhcp_ref(_row(), "assigned")
    assert (ref.table, ref.row_id, ref.time) == ("fg_dhcp", 77, T0)
    assert ref.raw is not None
    assert (ref.raw.source, ref.raw.line, ref.raw.offset) == ("fortigate", 9, 2048)
    assert ref.summary == (
        "vlan42 assigned 172.20.42.57 for 00:00:5e:00:53:57 PLN-PC14 "
        "(FortiGate DHCP, logid 0100026001, lease 604800)"
    )
    release = fg_dhcp_ref(_row(hostname=None, lease=None, logid="0100026002"), "deassigned")
    assert release.summary == (
        "vlan42 deassigned 172.20.42.57 for 00:00:5e:00:53:57 "
        "(FortiGate DHCP, logid 0100026002, lease -)"
    )
    assert fg_dhcp_ref(_row(interface=None), "assigned").summary.startswith("- assigned ")


def test_a_spaced_interface_name_keeps_the_summary_readable() -> None:
    """Sahada doğrula: FortiOS interface names are free text (up to 32 characters), so a space
    becomes ``_`` and the action and IP are still found."""
    ref = fg_dhcp_ref(_row(interface="port 1"), "deassigned")
    assert ref.summary.startswith("port_1 deassigned 172.20.42.57 for ")
    assert (dhcp_action(ref), dhcp_ip(ref)) == ("deassigned", "172.20.42.57")


@pytest.mark.parametrize(
    ("server", "action", "hostname"),
    [
        ("dhcp42", "assigned", "PLN-PC14"),
        ("dhcp42", "assigned", "deassigned for 172.20.42.99"),  # a host name is anything
        ("dhcp 42", "deassigned", None),  # a RouterOS server name with a space
        ("dhcp42", "deassigned", "assigned"),
    ],
)
def test_action_and_ip_come_from_the_fixed_part(
    server: str, action: str, hostname: str | None
) -> None:
    row = _row(server=server, action=action, hostname=hostname)
    ref = dhcp_ref(row)
    assert (dhcp_action(ref), dhcp_ip(ref)) == (action, "172.20.42.57")


def test_other_tables_have_no_dhcp_action() -> None:
    arp = _ref("mt_arp", 0, "ARP-LOG 172.20.42.57 00:00:5e:00:53:57 vlan42")
    traffic = _ref("fg_traffic", 0, "172.20.42.57:51001 -> 203.0.113.10:443 assigned 1 for x")
    for ref in (arp, traffic):
        assert dhcp_action(ref) is None
        assert dhcp_ip(ref) is None


def test_the_last_dhcp_evidence_is_the_leases_own() -> None:
    ack = _ref("fg_dhcp", 0)
    arp = _ref("mt_arp", 30)
    renewal = _ref("fg_dhcp", 720)
    traffic = _ref("fg_traffic", 800)
    refs = (ack, arp, renewal, traffic)
    assert last_dhcp_evidence(refs, None, "open") == renewal.time
    # At a moment before the renewal, the renewal logged later does not count.
    assert last_dhcp_evidence(refs, None, "open", T0 + timedelta(minutes=600)) == ack.time
    assert last_dhcp_evidence(refs, None, "open", T0 - timedelta(minutes=1)) is None
    # The line that ended a superseded lease is another device's ACK, never the lease's own.
    closing = _ref("fg_dhcp", 900)
    end = closing.time
    assert last_dhcp_evidence((*refs, closing), end, "superseded") == renewal.time
    # A lease's own RELEASE is its last DHCP evidence.
    release = _ref("mt_dhcp", 900)
    assert last_dhcp_evidence((*refs, release), end, "dhcp") == release.time
    assert last_dhcp_evidence((arp, traffic), None, "open") is None
    assert last_dhcp_evidence((), None, None) is None
