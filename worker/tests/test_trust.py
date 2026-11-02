"""Trusted senders and usable rows (ADR-0016, ``lookup.trust``): an unconfigured sender or a
source of another type never gives FortiGate evidence, except an ``unknown-<ip>`` sender inside
the address of an enabled, configured FortiGate source; an ``fg_dhcp`` row is refused by the
first rule of seven that applies; the DHCP origins per subnet and the srcmac guard's counts."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_db.models import FgDhcp, Source
from arcilogger_worker.lookup import trust
from arcilogger_worker.lookup.types import LookupSettings
from arcilogger_worker.testing import Evidence, tr


def _source(name: str, kind: str, address: str | None, **flags: bool) -> Source:
    return Source(name=name, type=kind, address=address, **flags)


def test_untrusted_sources(db_session: Session) -> None:
    db_session.add_all(
        [
            # Configured FortiGates: trusted, and they vouch for senders inside their address.
            _source("fortigate", "fortigate", "172.31.255.1"),
            _source("fw-ha", "fortigate", "172.31.254.0/29"),
            _source("fw6", "fortigate", "2001:db8:31::/64"),
            # A removed FortiGate keeps its own trust but vouches for nothing.
            _source("fortigate-old", "fortigate", "198.51.100.9", enabled=False, expected=False),
            # A configured source of another type is no FortiGate.
            _source("core-r1", "mikrotik", "172.20.10.1"),
            _source("ap-172.20.10.23", "generic", "172.20.10.23"),
            # Unconfigured senders.
            _source("unknown-172.31.254.3", "fortigate", "172.31.254.3", unknown=True),
            _source("unknown-172.31.254.4", "fortigate", None, unknown=True),  # name only
            _source("unknown-2001-db8-31--5", "fortigate", None, unknown=True),
            _source("unknown-172.31.254.9", "fortigate", "172.31.254.9", unknown=True),
            _source("unknown-192.0.2.7", "fortigate", "192.0.2.7", unknown=True),
            _source("unknown-198.51.100.9", "fortigate", "198.51.100.9", unknown=True),
            _source("unknown-172.20.10.1", "fortigate", "172.20.10.1", unknown=True),
            _source("unknown-garbled", "generic", None, unknown=True),
        ]
    )
    db_session.flush()
    assert trust.untrusted_sources(db_session) == {
        "core-r1",
        "ap-172.20.10.23",
        "unknown-172.31.254.9",  # outside the HA network /29
        "unknown-192.0.2.7",  # nobody vouches for it
        "unknown-198.51.100.9",  # a removed source's address vouches for nothing
        "unknown-172.20.10.1",  # only a FortiGate source vouches
        "unknown-garbled",
    }


def test_without_sources_nothing_is_untrusted(db_session: Session) -> None:
    """Rows of a source without a ``sources`` row (test helpers only) count as trusted."""
    assert trust.untrusted_sources(db_session) == frozenset()


def test_a_configured_source_without_an_address_vouches_for_nothing(db_session: Session) -> None:
    db_session.add_all(
        [
            _source("fortigate", "fortigate", None),
            _source("unknown-172.31.255.1", "fortigate", "172.31.255.1", unknown=True),
        ]
    )
    db_session.flush()
    assert trust.untrusted_sources(db_session) == {"unknown-172.31.255.1"}


def test_the_refusal_rules_are_listed_in_their_order() -> None:
    assert trust.REFUSAL_RULES == (
        "fortigate_dhcp_untrusted_source",
        "fortigate_dhcp_excluded",
        "ipv6_ignored",
        "fortigate_dhcp_unmapped",
        "fortigate_dhcp_unexpected_msg",
        "fortigate_dhcp_receive_time",
        "fortigate_dhcp_bad_mac",
    )
    assert {"fortigate_dhcp_excluded", "ipv6_ignored"} == trust.CONFIGURATION


DAY = date(2026, 10, 12)
MAC = "00:00:5e:00:53:57"


def _refusals(
    session: Session, settings: LookupSettings, untrusted: frozenset[str]
) -> dict[int, str | None]:
    rows = session.execute(
        select(FgDhcp.id, trust.refusal(settings, untrusted), trust.kept(settings, untrusted))
    ).all()
    for _id, code, kept in rows:
        assert kept is (code is None)
    return {row_id: code for row_id, code, _kept in rows}


def test_each_refusal_rule_in_its_order(db_session: Session, evidence: Evidence) -> None:
    fg = evidence.fortigate_dhcp
    at = tr(DAY, 9)
    ids = {
        "ok": fg(at, "172.20.42.57", MAC),
        "ok_lower_case_msg": fg(at, "172.20.42.58", "00:00:5e:00:53:58", dhcp_msg="ack"),
        "ok_release": fg(
            at,
            "172.20.42.59",
            "00:00:5e:00:53:59",
            logid="0100026002",
            dhcp_msg="Release",
            lease=None,
        ),
        "untrusted": fg(at, "172.20.42.60", MAC, source="unknown-192.0.2.7"),
        # An untrusted sender's row is refused for that first, whatever else it is.
        "untrusted_first": fg(
            at,
            "2001:db8::57",
            MAC,
            source="unknown-192.0.2.7",
            interface="ipsec1",
            device_time=False,
        ),
        "excluded": fg(at, "172.20.42.61", MAC, interface="ipsec1", logid="0100026099"),
        "ipv6": fg(at, "2001:db8::57", MAC, logid="0100026099"),
        "unmapped": fg(at, "172.20.42.62", MAC, logid="0100026099", dhcp_msg="Nak"),
        "unexpected_msg": fg(at, "172.20.42.63", MAC, dhcp_msg="Nak", device_time=False),
        "no_msg": fg(at, "172.20.42.64", MAC, dhcp_msg=""),
        "receive_time": fg(at, "172.20.42.65", "00:00:00:00:00:00", device_time=False),
        "zero_mac": fg(at, "172.20.42.66", "00:00:00:00:00:00"),
        "broadcast": fg(at, "172.20.42.67", "ff:ff:ff:ff:ff:ff"),
        "multicast": fg(at, "172.20.42.68", "01:00:5e:00:00:fb"),
        "multicast_odd": fg(at, "172.20.42.69", "33:33:00:00:00:01"),
        "unicast_even": fg(at, "172.20.42.70", "02:00:5e:00:53:70"),
    }
    settings = LookupSettings(fortigate_interfaces_excluded=("ipsec1",))
    found = _refusals(db_session, settings, frozenset({"unknown-192.0.2.7"}))
    assert {name: found[row_id] for name, row_id in ids.items()} == {
        "ok": None,
        "ok_lower_case_msg": None,
        "ok_release": None,  # 0100026002 has no dhcp_msg entry: not restricted
        "untrusted": trust.UNTRUSTED_SOURCE,
        "untrusted_first": trust.UNTRUSTED_SOURCE,
        "excluded": trust.EXCLUDED,
        "ipv6": trust.IPV6,
        "unmapped": trust.UNMAPPED,
        "unexpected_msg": trust.UNEXPECTED_MSG,
        "no_msg": trust.UNEXPECTED_MSG,
        "receive_time": trust.RECEIVE_TIME,
        "zero_mac": trust.BAD_MAC,
        "broadcast": trust.BAD_MAC,
        "multicast": trust.BAD_MAC,
        "multicast_odd": trust.BAD_MAC,
        "unicast_even": None,  # locally administered, not multicast
    }


def test_the_rules_follow_the_settings(db_session: Session, evidence: Evidence) -> None:
    at = tr(DAY, 9)
    nak = evidence.fortigate_dhcp(at, "172.20.42.57", MAC, dhcp_msg="Nak")
    other = evidence.fortigate_dhcp(at, "172.20.42.58", MAC, logid="0100026099")
    # The shipped defaults refuse both.
    assert _refusals(db_session, LookupSettings(), frozenset()) == {
        nak: trust.UNEXPECTED_MSG,
        other: trust.UNMAPPED,
    }
    # The value is listed and the logid mapped: both are lease evidence after a rebuild.
    widened = LookupSettings(
        fortigate_logids={"0100026001": "assigned", "0100026099": "deassigned"},
        fortigate_dhcp_msgs={"0100026001": ("ACK", "NAK")},
    )
    assert _refusals(db_session, widened, frozenset()) == {nak: None, other: None}
    # An empty map switches FortiGate DHCP evidence off: every row is unmapped.
    off = LookupSettings(fortigate_logids={}, fortigate_dhcp_msgs={})
    assert _refusals(db_session, off, frozenset()) == {nak: trust.UNMAPPED, other: trust.UNMAPPED}


def test_usable_dhcp_origins(db_session: Session, evidence: Evidence) -> None:
    at = tr(DAY, 9)
    evidence.dhcp(at, "assigned", "172.20.41.10", "00:00:5e:00:53:10", server="dhcp41")
    evidence.fortigate_dhcp(at, "172.20.41.11", "00:00:5e:00:53:11", interface="vlan41")
    evidence.fortigate_dhcp(at, "172.20.42.12", "00:00:5e:00:53:12", vd="vdom-2")
    evidence.fortigate_dhcp(at, "172.20.42.13", "00:00:5e:00:53:13")
    # Not counted: a refused row, an untrusted sender, a row outside the time range, an
    # unknown MikroTik action.
    evidence.fortigate_dhcp(at, "172.20.43.14", "00:00:5e:00:53:14", dhcp_msg="Nak")
    evidence.fortigate_dhcp(at, "172.20.43.15", "00:00:5e:00:53:15", source="unknown-192.0.2.7")
    evidence.fortigate_dhcp(tr(DAY, 12), "172.20.60.16", "00:00:5e:00:53:16", interface="vlan60")
    evidence.dhcp(at, "offering", "172.20.60.17", "00:00:5e:00:53:17", server="dhcp60")
    origins = trust.usable_dhcp_origins(
        db_session,
        LookupSettings(),
        frozenset({"unknown-192.0.2.7"}),
        ["172.20.41.0/24", "172.20.42.0/24", "172.20.43.0/24", "172.20.60.0/24"],
        tr(DAY, 8),
        tr(DAY, 10),
    )
    assert origins == {
        "172.20.41.0/24": {("mt_dhcp", None), ("fg_dhcp", "root")},
        "172.20.42.0/24": {("fg_dhcp", "vdom-2"), ("fg_dhcp", "root")},
        "172.20.43.0/24": set(),
        "172.20.60.0/24": set(),
    }
    assert (
        trust.usable_dhcp_origins(
            db_session, LookupSettings(), frozenset(), [], tr(DAY, 8), tr(DAY, 10)
        )
        == {}
    )


def test_srcmac_ip_counts(db_session: Session, evidence: Evidence) -> None:
    at = tr(DAY, 9)
    shared, single = "00:00:5e:00:53:01", "00:00:5e:00:53:02"
    for port, ip in enumerate(("172.20.42.57", "172.20.42.58", "172.20.42.58"), start=50001):
        evidence.traffic(at, ip, port, srcintf="vlan42", srcmac=shared.upper())
    evidence.traffic(at, "172.20.42.59", 50010, srcintf="vlan42", srcmac=single)
    # IPv6 addresses of a dual-stack client are never counted.
    evidence.traffic(at, "2001:db8::59", 50011, srcintf="vlan42", srcmac=single, transip=None)
    evidence.traffic(at, "2001:db8::5a", 50012, srcintf="vlan42", srcmac=single, transip=None)
    evidence.traffic(at, "172.20.60.20", 50013, srcintf="vlan60", srcmac=single)
    # Not counted: no srcmac, no interface, outside the range.
    evidence.traffic(at, "172.20.42.60", 50014, srcintf="vlan42")
    evidence.traffic(at, "172.20.42.61", 50015, srcmac=single)
    evidence.traffic(tr(DAY, 12), "172.20.42.62", 50016, srcintf="vlan42", srcmac=single)
    counts = trust.srcmac_ip_counts(db_session, tr(DAY, 8), tr(DAY, 10), None, frozenset())
    assert counts == {("vlan42", shared): 2, ("vlan42", single): 1, ("vlan60", single): 1}
    assert trust.srcmac_ip_counts(
        db_session, tr(DAY, 8), tr(DAY, 10), ["vlan42"], frozenset(), macs=[single]
    ) == {("vlan42", single): 1}
    assert trust.srcmac_ip_counts(db_session, tr(DAY, 8), tr(DAY, 10), [], frozenset()) == {}
    # An untrusted sender's sessions never count (the helper's sessions come from "fortigate").
    assert (
        trust.srcmac_ip_counts(db_session, tr(DAY, 8), tr(DAY, 10), None, frozenset({"fortigate"}))
        == {}
    )
