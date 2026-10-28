"""FortiGate DHCP lease evidence in the lease jobs (ADR-0016), against PostgreSQL.

A FortiGate-only site builds leases and devices from ``fg_dhcp`` rows; every refused row is
counted and never opens, closes or names anything; settings decide what an old row means, so a
rebuild reinterprets it; seeds across midnight keep their FortiGate evidence, their lease time
and their MAC's move to another address (D33); the source registry feeds the watermark and the
lookup's data window; an exit value that cannot be parsed never stops the lease jobs.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from arcilogger_db.models import Device, FgTraffic, Lease, Source
from arcilogger_db.settings import set_setting
from arcilogger_worker.correlation.leases import (
    first_evidence_time,
    rebuild_day,
    rebuild_days,
    run_incremental,
)
from arcilogger_worker.correlation.watermark import feeding_sources, set_watermark
from arcilogger_worker.lookup.types import EvidenceRef
from arcilogger_worker.lookup.window import data_window
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)
NEXT = DAY + timedelta(days=1)
PC = "172.20.42.57"
MAC = "00:00:5e:00:53:57"
OTHER = "00:00:5e:00:53:58"
THIRD = "00:00:5e:00:53:59"


def leases(session: Session) -> list[tuple[Any, ...]]:
    session.expire_all()
    rows = session.scalars(select(Lease).order_by(Lease.start, Lease.ip, Lease.mac)).all()
    return [
        (r.ip, r.mac, r.start, r.end, r.end_reason, r.source_table, r.lease_seconds) for r in rows
    ]


def snapshot(session: Session) -> dict[int, tuple[Any, ...]]:
    session.expire_all()
    return {
        r.id: (
            r.ip, r.mac, r.vlan, r.server, r.hostname, r.start, r.end, r.end_reason,
            r.confidence, r.source_table, r.last_evidence, r.lease_seconds,
            tuple((e["table"], e["row_id"]) for e in r.evidence),
        )
        for r in session.scalars(select(Lease))
    }  # fmt: skip


def test_a_fortigate_only_site_builds_leases_and_devices(
    db_session: Session, evidence: Evidence
) -> None:
    ack = evidence.fortigate_dhcp(tr(DAY, 8), PC, MAC, hostname="PLN-PC14")
    renewal = evidence.fortigate_dhcp(tr(DAY, 20), PC, MAC, hostname="PLN-PC14", lease=43200)
    evidence.traffic(tr(DAY, 21), PC, 50001)
    evidence.fortigate_dhcp(
        tr(DAY, 9), "172.20.60.20", "02:00:5e:00:53:20", interface="vlan60", hostname="TEL-20"
    )
    release = evidence.fortigate_dhcp(
        tr(DAY, 17), "172.20.60.20", "02:00:5e:00:53:20", logid="0100026002",
        dhcp_msg="Release", lease=None, interface="vlan60",
    )  # fmt: skip
    db_session.commit()
    assert first_evidence_time(db_session) == tr(DAY, 8)

    report = rebuild_day(db_session, DAY, horizon=tr(NEXT, 12))
    db_session.commit()

    assert (report.inserted, report.events["dhcp_assigned"], report.events["dhcp_deassigned"]) == (
        2, 3, 1,
    )  # fmt: skip
    assert report.stats["kept_fg_dhcp"] == 4
    assert report.stats["refused_fg_dhcp"] == 0
    rows = {r.ip: r for r in db_session.scalars(select(Lease))}
    pc = rows[PC]
    # The renewal's lease (43200) ends it 12 h after the renewal, not after the traffic (D26).
    assert (pc.start, pc.end, pc.end_reason) == (tr(DAY, 8), tr(NEXT, 8), "timeout")
    assert (pc.source_table, pc.confidence, pc.server, pc.vlan) == ("fg_dhcp", "high", "vlan42", 42)
    assert (pc.lease_seconds, pc.last_evidence, pc.hostname) == (43200, tr(DAY, 20), "PLN-PC14")
    refs = [EvidenceRef.from_dict(item) for item in pc.evidence]
    assert [(r.table, r.row_id) for r in refs if r.table == "fg_dhcp"] == [
        ("fg_dhcp", ack),
        ("fg_dhcp", renewal),
    ]
    assert refs[0].summary == (
        f"vlan42 assigned {PC} for {MAC} PLN-PC14 (FortiGate DHCP, logid 0100026001, lease 86400)"
    )
    phone = rows["172.20.60.20"]
    assert (phone.end, phone.end_reason, phone.vlan) == (tr(DAY, 17), "dhcp", 60)
    assert [EvidenceRef.from_dict(e).row_id for e in phone.evidence][-1] == release
    devices = {d.mac: d for d in db_session.scalars(select(Device))}
    assert devices[MAC].hostnames == ["PLN-PC14"]
    assert (devices[MAC].last_ip, devices[MAC].last_vlan) == (PC, 42)
    assert devices["02:00:5e:00:53:20"].hostnames == ["TEL-20"]


def test_the_catch_up_starts_at_the_first_fortigate_evidence(
    db_session: Session, evidence: Evidence
) -> None:
    # A refused row before it is no evidence, and no catch-up starts there.
    evidence.fortigate_dhcp(tr(DAY, 6), PC, MAC, dhcp_msg="Nak")
    evidence.fortigate_dhcp(tr(DAY, 8), PC, MAC)
    db_session.commit()
    assert first_evidence_time(db_session) == tr(DAY, 8)
    report = run_incremental(db_session, now=tr(NEXT + timedelta(days=1), 12))
    db_session.commit()
    assert report.mode == "catch_up"
    assert report.days[0] == DAY.isoformat()
    assert leases(db_session) == [(PC, MAC, tr(DAY, 8), tr(NEXT, 8), "timeout", "fg_dhcp", 86400)]


def test_an_unmapped_logid_is_counted_and_used_once_mapped(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.fortigate_dhcp(tr(DAY, 8), PC, MAC, logid="0100026099", dhcp_msg="Ack")
    db_session.commit()
    report = rebuild_day(db_session, DAY, horizon=tr(NEXT, 12))
    db_session.commit()
    assert report.stats["fortigate_dhcp_unmapped"] == 1
    assert report.stats["refused_fg_dhcp"] == 1
    assert leases(db_session) == []
    assert db_session.scalars(select(Device)).all() == []
    set_setting(
        db_session,
        "dhcp.fortigate_logids",
        {"0100026001": "assigned", "0100026002": "deassigned", "0100026099": "assigned"},
        updated_by="tolga",
    )
    db_session.commit()
    report = rebuild_day(db_session, DAY, horizon=tr(NEXT, 12))
    db_session.commit()
    assert report.stats["kept_fg_dhcp"] == 1
    assert leases(db_session) == [(PC, MAC, tr(DAY, 8), tr(NEXT, 8), "timeout", "fg_dhcp", 86400)]


def test_ipv6_and_unusable_lease_times(db_session: Session, evidence: Evidence) -> None:
    evidence.fortigate_dhcp(tr(DAY, 8), "2001:db8::57", MAC)
    evidence.fortigate_dhcp(tr(DAY, 8), "172.20.42.60", "00:00:5e:00:53:60", lease=0)
    evidence.fortigate_dhcp(tr(DAY, 8), "172.20.42.61", "00:00:5e:00:53:61", lease=10**7)
    evidence.fortigate_dhcp(tr(DAY, 8), "172.20.42.62", "00:00:5e:00:53:62", lease=None)
    db_session.commit()
    report = rebuild_day(db_session, DAY, horizon=tr(NEXT + timedelta(days=1), 12))
    db_session.commit()
    stats = report.stats
    assert (stats["ipv6_ignored"], stats["excluded_fg_dhcp"], stats["refused_fg_dhcp"]) == (1, 1, 0)
    assert (stats["lease_fallback"], stats["lease_unlimited"]) == (2, 1)  # NULL is no fallback
    # The configured time x lease.timeout_percent: 86400 x 110 % = 26 h 24 min.
    assert leases(db_session) == [
        (ip, mac, tr(DAY, 8), tr(NEXT, 10, 24), "timeout", "fg_dhcp", None)
        for ip, mac in (
            ("172.20.42.60", "00:00:5e:00:53:60"),
            ("172.20.42.61", "00:00:5e:00:53:61"),
            ("172.20.42.62", "00:00:5e:00:53:62"),
        )
    ]


def test_the_logged_lease_can_be_switched_off(db_session: Session, evidence: Evidence) -> None:
    evidence.fortigate_dhcp(tr(DAY, 8), PC, MAC, lease=3600)
    set_setting(db_session, "dhcp.fortigate_use_logged_lease", False, updated_by="tolga")
    set_setting(db_session, "lease.server_seconds", {"vlan42": 7200}, updated_by="tolga")
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=tr(NEXT, 12))
    db_session.commit()
    # The interface's configured 2 h x 110 %, not the logged hour.
    assert leases(db_session) == [
        (PC, MAC, tr(DAY, 8), tr(DAY, 10, 12), "timeout", "fg_dhcp", None)
    ]


def test_the_vlan_comes_from_the_subnet_whatever_the_interface_name(
    db_session: Session, evidence: Evidence
) -> None:
    """dhcp.server_vlan_regex (``^dhcp(\\d+)$``) names MikroTik servers only (D7)."""
    evidence.fortigate_dhcp(tr(DAY, 8), "172.20.41.30", MAC, interface="dhcp60")
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=tr(DAY, 12))
    db_session.commit()
    (row,) = db_session.scalars(select(Lease))
    assert (row.server, row.vlan) == ("dhcp60", 41)


def test_refused_rows_are_counted_and_never_evidence(
    db_session: Session, evidence: Evidence
) -> None:
    db_session.add_all(
        [
            Source(name="fortigate", type="fortigate", address="172.31.255.1"),
            Source(name="core-r1", type="mikrotik", address="172.20.10.1"),
            Source(name="unknown-192.0.2.7", type="fortigate", address="192.0.2.7", unknown=True),
        ]
    )
    set_setting(db_session, "dhcp.fortigate_interfaces_excluded", ["ipsec1"], updated_by="tolga")
    fg = evidence.fortigate_dhcp
    at = tr(DAY, 8)
    fg(at, "172.20.42.70", "00:00:5e:00:53:70", source="unknown-192.0.2.7")
    fg(at, "172.20.42.71", "00:00:5e:00:53:71", source="core-r1")  # a MikroTik source
    fg(at, "172.20.42.72", "00:00:5e:00:53:72", dhcp_msg="Nak")
    fg(at, "172.20.42.73", "00:00:5e:00:53:73", dhcp_msg="")
    fg(at, "172.20.42.74", "00:00:00:00:00:00")
    fg(at, "172.20.42.75", "01:00:5e:00:00:fb")
    fg(at + timedelta(hours=1), "172.20.42.76", "00:00:5e:00:53:76", device_time=False)
    fg(at, "172.20.42.77", "00:00:5e:00:53:77", interface="ipsec1")
    fg(at, "2001:db8::78", "00:00:5e:00:53:78")
    used = fg(at, "172.20.42.79", "00:00:5e:00:53:79", dhcp_msg="ack")  # case-insensitive
    # Open leases of other MACs on two of those addresses: a refused row closes nothing.
    fg(at - timedelta(hours=1), "172.20.42.72", OTHER)
    evidence.dhcp(at - timedelta(hours=1), "assigned", "172.20.42.70", THIRD, server="dhcp42")
    # An unconfigured sender's traffic never keeps a MikroTik lease alive.
    evidence.dhcp(tr(DAY, 1), "assigned", "172.20.43.80", "00:00:5e:00:53:80")
    for hour in range(2, 23):
        evidence.traffic(tr(DAY, hour), "172.20.43.80", 50000 + hour)
    db_session.commit()
    _move_traffic_to(db_session, "unknown-192.0.2.7")

    report = rebuild_day(db_session, DAY, horizon=tr(NEXT + timedelta(days=1), 12))
    db_session.commit()

    stats = report.stats
    assert {key: stats[key] for key in stats if key.startswith(("fortigate_dhcp_", "ipv6"))} == {
        "fortigate_dhcp_untrusted_source": 2,
        "fortigate_dhcp_unexpected_msg": 2,
        "fortigate_dhcp_bad_mac": 2,
        "fortigate_dhcp_receive_time": 1,
        "fortigate_dhcp_excluded": 1,
        "ipv6_ignored": 1,
    }
    assert (stats["refused_fg_dhcp"], stats["excluded_fg_dhcp"], stats["kept_fg_dhcp"]) == (
        7, 2, 2,
    )  # fmt: skip
    rows = {(r.ip, r.mac): r for r in db_session.scalars(select(Lease))}
    assert set(rows) == {
        ("172.20.42.79", "00:00:5e:00:53:79"),
        ("172.20.42.72", OTHER),
        ("172.20.42.70", THIRD),
        ("172.20.43.80", "00:00:5e:00:53:80"),
    }
    assert [EvidenceRef.from_dict(e).row_id for e in rows[("172.20.42.79", "00:00:5e:00:53:79")]
            .evidence] == [used]  # fmt: skip
    for key in (("172.20.42.72", OTHER), ("172.20.42.70", THIRD)):
        assert rows[key].end_reason == "timeout"  # never superseded by a refused row
    # 01:00 + 26 h 24 min: the untrusted traffic did not extend it.
    assert rows[("172.20.43.80", "00:00:5e:00:53:80")].end == tr(NEXT, 3, 24)
    assert {d.mac for d in db_session.scalars(select(Device))} == {
        "00:00:5e:00:53:79",
        OTHER,
        THIRD,
        "00:00:5e:00:53:80",
    }


def _move_traffic_to(session: Session, source: str) -> None:
    """Files the helper's session rows under ``source`` (an unconfigured sender's)."""
    session.execute(update(FgTraffic).values(source=source))
    session.commit()


def test_a_sender_configured_later_vouches_for_its_earlier_lines(
    db_session: Session, evidence: Evidence
) -> None:
    """D24: ACKs of an unconfigured sender are refused; once a FortiGate source with that
    address is configured and the day rebuilt, they are lease evidence. A removed source's
    address vouches for nothing."""
    db_session.add_all(
        [
            Source(name="unknown-172.20.10.1", type="fortigate", address="172.20.10.1",
                   unknown=True),
            Source(name="fw-old", type="fortigate", address="172.20.10.1", enabled=False,
                   expected=False),
        ]
    )  # fmt: skip
    evidence.fortigate_dhcp(tr(DAY, 8), PC, MAC, source="unknown-172.20.10.1")
    db_session.commit()
    report = rebuild_day(db_session, DAY, horizon=tr(NEXT, 12))
    db_session.commit()
    assert report.stats["fortigate_dhcp_untrusted_source"] == 1
    assert leases(db_session) == []
    db_session.add(Source(name="fortigate-2", type="fortigate", address="172.20.10.0/30"))
    db_session.commit()
    report = rebuild_day(db_session, DAY, horizon=tr(NEXT, 12))
    db_session.commit()
    assert report.stats["kept_fg_dhcp"] == 1
    assert leases(db_session) == [(PC, MAC, tr(DAY, 8), tr(NEXT, 8), "timeout", "fg_dhcp", 86400)]


def test_a_seed_across_midnight_keeps_its_fortigate_evidence(
    db_session: Session, evidence: Evidence
) -> None:
    first = evidence.fortigate_dhcp(tr(DAY, 20), PC, MAC, hostname="PLN-PC14")
    renewal = evidence.fortigate_dhcp(tr(DAY, 23), PC, MAC, lease=43200)
    evidence.traffic(tr(NEXT, 2), PC, 50001)  # a printer's traffic: no extension (D26)
    later = evidence.fortigate_dhcp(tr(NEXT, 6), PC, MAC, lease=43200)
    end = tr(NEXT + timedelta(days=2), 12)
    set_watermark(db_session, end)
    db_session.commit()
    rebuild_days(db_session, DAY, NEXT + timedelta(days=1))
    expected = [(PC, MAC, tr(DAY, 20), tr(NEXT, 18), "timeout", "fg_dhcp", 43200)]
    assert leases(db_session) == expected
    (row,) = db_session.scalars(select(Lease))
    assert [EvidenceRef.from_dict(e).row_id for e in row.evidence if e["table"] == "fg_dhcp"] == [
        first,
        renewal,
        later,
    ]
    reference = snapshot(db_session)
    for day in (NEXT, DAY, NEXT):
        report = rebuild_day(db_session, day)
        db_session.commit()
        assert (report.inserted, report.updated, report.deleted) == (0, 0, 0), day
    assert snapshot(db_session) == reference


def test_a_seed_whose_mac_moves_before_midnight_is_closed(
    db_session: Session, evidence: Evidence
) -> None:
    """D33 at a midnight: the stored row says the lease of .57 crosses midnight; the MAC's ACK
    for .58 at 23:50 (late) ends it there, in the next day's rebuild as in a live run."""
    evidence.fortigate_dhcp(tr(DAY, 20), PC, MAC)
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=tr(DAY, 23, 55))
    db_session.commit()
    (row_id,) = db_session.scalars(select(Lease.id))
    evidence.fortigate_dhcp(tr(DAY, 23, 50), "172.20.42.58", MAC)  # ingest backlog
    db_session.commit()
    report = rebuild_day(db_session, NEXT, horizon=tr(NEXT, 12))
    db_session.commit()
    assert report.stats["seeds_closed_before_midnight"] == 1
    assert leases(db_session)[0] == (
        PC, MAC, tr(DAY, 20), tr(DAY, 23, 50), "superseded", "fg_dhcp", 86400,
    )  # fmt: skip
    assert list(db_session.scalars(select(Lease.id).where(Lease.ip == PC))) == [row_id]
    # The day's own rebuild gives the same rows (and adds the lease of .58).
    single = snapshot(db_session)
    set_watermark(db_session, tr(NEXT, 12))
    db_session.commit()
    rebuild_days(db_session, DAY, NEXT)
    after = snapshot(db_session)
    assert after[row_id] == single[row_id]
    assert [r[:5] for r in leases(db_session)] == [
        (PC, MAC, tr(DAY, 20), tr(DAY, 23, 50), "superseded"),
        ("172.20.42.58", MAC, tr(DAY, 23, 50), None, "open"),
    ]


def test_a_rebuilt_fortigate_seed_is_high_again(db_session: Session, evidence: Evidence) -> None:
    """Round 6: a stored ``low`` of a FortiGate lease can only be the rollback step's; a day
    rebuild re-derives the seed from its rows and writes it ``high``. The incremental job
    keeps a stored confidence."""
    evidence.fortigate_dhcp(tr(DAY, 20), PC, MAC)
    evidence.fortigate_dhcp(tr(NEXT, 7), PC, MAC)
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=tr(NEXT, 6))
    set_watermark(db_session, tr(NEXT, 6))
    db_session.commit()
    db_session.query(Lease).update({"confidence": "low"})
    db_session.commit()
    run_incremental(db_session, now=tr(NEXT, 8))
    db_session.commit()
    (row,) = db_session.scalars(select(Lease))
    assert (row.confidence, row.last_evidence) == ("low", tr(NEXT, 7))
    rebuild_day(db_session, NEXT)
    db_session.commit()
    db_session.refresh(row)
    assert row.confidence == "high"


def test_an_unreadable_exit_value_never_stops_the_lease_jobs(
    db_session: Session, evidence: Evidence
) -> None:
    set_setting(db_session, "nat.egresses", "not an object", updated_by="tolga")  # unvalidated
    evidence.fortigate_dhcp(tr(DAY, 8), PC, MAC)
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=tr(DAY, 12))
    db_session.commit()
    set_watermark(db_session, tr(DAY, 12))
    db_session.commit()
    run_incremental(db_session, now=tr(DAY, 13))
    db_session.commit()
    assert [r[:5] for r in leases(db_session)] == [(PC, MAC, tr(DAY, 8), None, "open")]


def test_fortigate_dhcp_rows_feed_the_watermark_and_the_data_window(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.fortigate_dhcp(tr(DAY, 8), PC, MAC, source="fortigate-2")
    db_session.commit()
    assert "fortigate-2" in feeding_sources(db_session, tr(DAY, 9))
    window, notes = data_window(db_session, tr(DAY, 9), now=tr(DAY, 10))
    assert window.in_database
    assert notes == []
    # A day with FortiGate DHCP rows only (no messages row: the helper writes none for them).
    other = data_window(db_session, tr(NEXT, 9), now=tr(NEXT, 10))[0]
    assert not other.in_database


@pytest.mark.parametrize("extends", [False, True])
def test_traffic_extends_a_fortigate_lease_only_when_set(
    db_session: Session, evidence: Evidence, extends: bool
) -> None:
    evidence.fortigate_dhcp(tr(DAY, 8), PC, MAC, lease=3600)
    for minute in range(10, 600, 10):
        evidence.traffic(tr(DAY, 8) + timedelta(minutes=minute), PC, 50000 + minute)
    set_setting(db_session, "dhcp.fortigate_traffic_extends", extends, updated_by="tolga")
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=tr(NEXT, 12))
    db_session.commit()
    end = tr(DAY, 18, 50) if extends else tr(DAY, 9)  # the last traffic 17:50 + 1 h, or 1 h
    assert [r[:5] for r in leases(db_session)] == [(PC, MAC, tr(DAY, 8), end, "timeout")]
