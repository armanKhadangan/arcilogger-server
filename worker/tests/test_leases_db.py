"""Lease jobs against PostgreSQL: day rebuild, midnight crossing, idempotency, incremental mode,
conflict alerts, devices and the raw references in ``leases.evidence``."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site
from arcilogger_db.models import Alert, Device, Lease, Person, Source
from arcilogger_db.settings import set_setting
from arcilogger_worker.correlation.leases import (
    Context,
    rebuild_day,
    rebuild_days,
    run_incremental,
)
from arcilogger_worker.correlation.watermark import (
    COLLECTOR_STATUS_FRESH,
    SILENT_AFTER,
    collector_received,
    feeding_sources,
    get_watermark,
    ingest_progress,
    lease_horizon,
    set_watermark,
)
from arcilogger_worker.lookup import attribute
from arcilogger_worker.lookup.types import EvidenceRef
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)
NEXT = DAY + timedelta(days=1)
LATER = tr(DAY + timedelta(days=5), 0)
PC = "172.20.43.120"
A = "f0:1f:af:00:00:01"
B = "f0:1f:af:00:00:02"
PHONE = "02:00:5e:00:53:46"


def leases(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(select(Lease).order_by(Lease.start, Lease.ip, Lease.mac)).all()
    keep = (
        "ip",
        "mac",
        "vlan",
        "server",
        "hostname",
        "start",
        "end",
        "end_reason",
        "confidence",
        "source_table",
        "last_evidence",
        "evidence",
    )
    return [{key: getattr(row, key) for key in keep} for row in rows]


def test_rebuild_builds_dhcp_arp_and_traffic_intervals(
    db_session: Session, evidence: Evidence
) -> None:
    opened = evidence.dhcp(tr(DAY, 8), "assigned", PC, A, hostname="PC-07")
    evidence.traffic(tr(DAY, 9), PC, 50001)
    last_traffic = evidence.traffic(tr(DAY, 16), PC, 50002)
    closed = evidence.dhcp(tr(DAY, 17), "deassigned", PC, A, hostname="PC-07")
    for hour in (9, 10, 11):
        evidence.arp(tr(DAY, hour), "172.20.10.237", "00:1b:a9:00:00:10", "vlan10")
    evidence.dhcp(
        tr(DAY, 10), "assigned", "172.20.60.183", PHONE, server="dhcp60", hostname="Telefon-12"
    )
    db_session.commit()

    report = rebuild_day(db_session, DAY, horizon=tr(NEXT, 0))
    db_session.commit()

    assert (report.inserted, report.updated, report.deleted) == (3, 0, 0)
    assert report.events["dhcp_assigned"] == 2
    assert report.events["arp"] == 3
    rows = {row["ip"]: row for row in leases(db_session)}
    pc = rows[PC]
    assert (pc["start"], pc["end"], pc["end_reason"], pc["confidence"]) == (
        tr(DAY, 8),
        tr(DAY, 17),
        "dhcp",
        "high",
    )
    assert (pc["vlan"], pc["server"], pc["hostname"]) == (43, "dhcp43", "PC-07")
    refs = [EvidenceRef.from_dict(item) for item in pc["evidence"]]
    assert [(r.table, r.row_id) for r in refs] == [
        ("mt_dhcp", opened),
        ("fg_traffic", last_traffic),  # the latest traffic row is looked up by (srcip, time)
        ("mt_dhcp", closed),
    ]
    printer = rows["172.20.10.237"]
    assert (printer["start"], printer["end"], printer["end_reason"]) == (
        tr(DAY, 9),
        tr(DAY, 12),
        "arp",
    )
    assert (printer["confidence"], printer["source_table"], printer["vlan"]) == (
        "medium",
        "mt_arp",
        10,  # from the subnet
    )
    phone = rows["172.20.60.183"]
    assert phone["vlan"] == 60  # from the DHCP server name (dhcp.server_vlan_regex)
    assert phone["end"] is None
    assert phone["end_reason"] == "open"


def test_evidence_points_at_the_exact_raw_lines(db_session: Session, evidence: Evidence) -> None:
    evidence.traffic(tr(DAY, 7, 59), "172.20.43.99", 40000)  # other lines before
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A, hostname="PC-07")
    evidence.dhcp(tr(DAY, 8, 1), "assigned", "172.20.43.121", B)
    evidence.dhcp(tr(DAY, 17), "deassigned", PC, A, hostname="PC-07")
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=tr(NEXT, 0))
    db_session.commit()

    lease = db_session.scalars(select(Lease).where(Lease.ip == PC)).one()
    opening, closing = (EvidenceRef.from_dict(item) for item in lease.evidence)
    assert opening.raw is not None
    assert closing.raw is not None
    assert (opening.raw.source, opening.raw.day, opening.raw.line) == ("core-r1", DAY, 1)
    assert (closing.raw.line, closing.raw.offset) == (3, closing.raw.offset)
    first = evidence.raw_line("core-r1", DAY, opening.raw.line, opening.raw.offset or 0)
    last = evidence.raw_line("core-r1", DAY, closing.raw.line, closing.raw.offset or 0)
    assert first.endswith(b"dhcp43 assigned 172.20.43.120 for F0:1F:AF:00:00:01 PC-07\n")
    assert last.endswith(b"dhcp43 deassigned 172.20.43.120 for F0:1F:AF:00:00:01 PC-07\n")
    assert opening.summary == "dhcp43 assigned 172.20.43.120 for f0:1f:af:00:00:01 PC-07"


def test_interval_crossing_midnight_is_one_row_whatever_the_rebuild_order(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.dhcp(tr(DAY, 22), "assigned", PC, A)
    evidence.traffic(tr(DAY, 23, 30), PC, 50001)
    evidence.traffic(tr(NEXT, 1), PC, 50002)
    evidence.dhcp(tr(NEXT, 3), "deassigned", PC, A)
    evidence.dhcp(tr(NEXT, 9), "assigned", PC, B)
    db_session.commit()
    expected = [
        (tr(DAY, 22), tr(NEXT, 3), "dhcp", A),
        (tr(NEXT, 9), None, "open", B),
    ]

    def summary() -> list[tuple[Any, ...]]:
        return [(r["start"], r["end"], r["end_reason"], r["mac"]) for r in leases(db_session)]

    # Day first: the look-ahead follows the interval into the next day.
    first = rebuild_day(db_session, DAY, horizon=tr(NEXT, 12))
    db_session.commit()
    assert first.lookahead_chunks == 1
    assert summary() == [expected[0]]
    rebuild_day(db_session, NEXT, horizon=tr(NEXT, 12))
    db_session.commit()
    assert summary() == expected
    snapshot = leases(db_session)

    # Next day first, then the day: same result; a range rebuild too.
    db_session.execute(delete(Lease))
    db_session.commit()
    rebuild_day(db_session, NEXT, horizon=tr(NEXT, 12))
    rebuild_day(db_session, DAY, horizon=tr(NEXT, 12))
    db_session.commit()
    assert leases(db_session) == snapshot
    report = rebuild_days(db_session, DAY, NEXT, now=tr(NEXT, 12, 2))
    assert report.days == ["2026-10-12", "2026-10-13"]
    assert leases(db_session) == snapshot


def test_rebuild_is_idempotent_and_reconstructs_seeds(
    db_session: Session, evidence: Evidence
) -> None:
    """A lease from two days earlier, kept alive by renewal-silent ARP, closed today."""
    before = DAY - timedelta(days=2)
    evidence.dhcp(tr(before, 8), "assigned", PC, A, hostname="PC-07")
    moment = tr(before, 9)
    while moment < tr(DAY, 15):
        evidence.arp(moment, PC, A)
        moment += timedelta(hours=1)
    evidence.dhcp(tr(DAY, 15, 30), "deassigned", PC, A)
    evidence.arp(tr(DAY, 9, 30), PC, B)  # an IP conflict
    db_session.commit()
    horizon = tr(DAY + timedelta(days=2), 0)
    rebuild_days(db_session, before, DAY, now=horizon)
    first = leases(db_session)
    alerts = db_session.scalars(select(Alert)).all()
    assert len(first) == 2
    dhcp = next(r for r in first if r["mac"] == A)
    assert (dhcp["start"], dhcp["end"], dhcp["end_reason"]) == (
        tr(before, 8),
        tr(DAY, 15, 30),
        "dhcp",
    )
    assert [a.rule for a in alerts] == ["ip_conflict"]
    assert alerts[0].dedup_key == f"ip_conflict:{PC}:{A},{B}:2026-10-12"
    assert alerts[0].details["macs"] == [A, B]

    ids = sorted(db_session.scalars(select(Lease.id)))
    for _ in range(2):
        report = rebuild_day(db_session, DAY, horizon=horizon)
        db_session.commit()
        assert leases(db_session) == first
        # Nothing to write: the seed's tail and today's ARP interval were already right, and the
        # rows keep their ids (stored lookups and alert details point at them).
        assert (report.inserted, report.updated, report.deleted) == (0, 0, 0)
        assert sorted(db_session.scalars(select(Lease.id))) == ids
        assert report.alerts == 0  # never the same conflict twice

    # Dismissed by an administrator: rebuilding does not raise it again either.
    db_session.execute(update(Alert).values(state="dismissed"))
    db_session.commit()
    assert rebuild_day(db_session, DAY, horizon=horizon).alerts == 0


def test_infrastructure_macs_raise_no_conflict(db_session: Session, evidence: Evidence) -> None:
    router = "00:00:5e:00:53:32"
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    evidence.arp(tr(DAY, 9), PC, router)
    set_setting(db_session, "devices.infrastructure_macs", [router.upper()], updated_by="tolga")
    db_session.commit()
    assert rebuild_day(db_session, DAY, horizon=tr(NEXT, 0)).alerts == 0
    assert db_session.scalar(select(Device.infrastructure).where(Device.mac == router)) is True


def test_guest_lease_uses_the_server_lease_time(db_session: Session, evidence: Evidence) -> None:
    evidence.dhcp(tr(DAY, 10), "assigned", "172.20.61.105", PHONE, server="dhcp61")
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=tr(NEXT, 0))
    (row,) = leases(db_session)
    assert (row["end"], row["end_reason"], row["vlan"]) == (tr(DAY, 11, 6), "timeout", 61)


def test_incremental_catches_up_then_follows_the_watermark(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    db_session.commit()
    now = tr(NEXT, 11)
    first = run_incremental(db_session, now=now)
    db_session.commit()
    assert first.mode == "catch_up"
    assert first.days == ["2026-10-12", "2026-10-13"]
    assert get_watermark(db_session) == now - timedelta(seconds=120)
    (row,) = leases(db_session)
    assert (row["end"], row["end_reason"]) == (tr(NEXT, 10, 24), "timeout")  # 08:00 + 26:24

    evidence.dhcp(tr(NEXT, 11, 1), "assigned", PC, B)
    evidence.traffic(tr(NEXT, 11, 5), PC, 50001)
    evidence.dhcp(tr(NEXT, 11, 9), "deassigned", PC, B)  # still younger than the lag
    db_session.commit()
    second = run_incremental(db_session, now=tr(NEXT, 11, 10))
    db_session.commit()
    assert second.mode == "incremental"
    assert (second.inserted, second.updated) == (1, 0)
    row = leases(db_session)[-1]
    assert (row["mac"], row["end"], row["last_evidence"]) == (B, None, tr(NEXT, 11, 5))

    third = run_incremental(db_session, now=tr(NEXT, 11, 20))
    db_session.commit()
    assert (third.inserted, third.updated) == (0, 1)
    row = leases(db_session)[-1]
    assert (row["end"], row["end_reason"]) == (tr(NEXT, 11, 9), "dhcp")
    assert run_incremental(db_session, now=tr(NEXT, 11, 20)).inserted == 0  # nothing new

    # A full rebuild of the day gives the same rows as the incremental runs.
    incremental = leases(db_session)
    rebuild_day(db_session, NEXT)
    db_session.commit()
    assert leases(db_session) == incremental


def test_incremental_without_any_evidence_only_sets_the_watermark(db_session: Session) -> None:
    report = run_incremental(db_session, now=tr(DAY, 12))
    db_session.commit()
    assert report.mode == "catch_up"
    assert report.days == []
    assert get_watermark(db_session) == tr(DAY, 11, 58)


def test_rebuild_never_goes_past_the_watermark(db_session: Session, evidence: Evidence) -> None:
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    evidence.dhcp(tr(DAY, 9), "deassigned", PC, A)
    set_watermark(db_session, tr(DAY, 8, 30))
    db_session.commit()
    report = rebuild_day(db_session, DAY)
    assert report.window_end == tr(DAY, 8, 30)
    (row,) = leases(db_session)
    assert row["end"] is None  # the deassign is after the watermark: the incremental job's part


def test_devices_are_upserted_without_touching_admin_fields(
    db_session: Session, evidence: Evidence, tmp_path: Path
) -> None:
    oui = tmp_path / "oui.csv"
    oui.write_text(
        "Registry,Assignment,Organization Name,Organization Address\n"
        'MA-L,F01FAF,"Example Devices, Inc.",Somewhere\n',
        encoding="utf-8",
    )
    set_setting(db_session, "devices.oui_file", str(oui), updated_by="tolga")
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A, hostname="PC-07")
    evidence.dhcp(
        tr(DAY, 9), "assigned", "172.20.60.183", PHONE, server="dhcp60", hostname="Telefon-12"
    )
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=tr(NEXT, 0))
    db_session.commit()
    pc = db_session.get(Device, A)
    phone = db_session.get(Device, PHONE)
    assert pc is not None
    assert phone is not None
    assert (pc.vendor, pc.randomized, pc.hostnames, pc.last_ip, pc.last_vlan) == (
        "Example Devices, Inc.",
        False,
        ["PC-07"],
        PC,
        43,
    )
    assert (phone.randomized, phone.vendor) == (True, None)  # 02:… is locally administered

    person = Person(name="Deniz Aydın")
    db_session.add(person)
    db_session.flush()
    pc.name, pc.type, pc.person_id, pc.notes = "Deniz PC", "pc", person.id, "masa 3"
    pc.tags, pc.infrastructure, pc.vendor = ["muhasebe"], True, "Elle girildi"
    db_session.commit()

    # A newer day (new IP and host name), then the older day again: nothing goes backwards.
    evidence.dhcp(tr(NEXT, 8), "assigned", "172.20.43.150", A, hostname="PC-07-YENI")
    db_session.commit()
    rebuild_day(db_session, NEXT, horizon=tr(NEXT, 12))
    rebuild_day(db_session, DAY, horizon=tr(NEXT, 12))
    db_session.commit()
    db_session.expire_all()
    pc = db_session.get(Device, A)
    assert pc is not None
    assert (pc.first_seen, pc.last_seen, pc.last_ip) == (tr(DAY, 8), tr(NEXT, 8), "172.20.43.150")
    assert pc.hostnames == ["PC-07", "PC-07-YENI"]
    assert (pc.name, pc.type, pc.person_id, pc.notes, pc.tags, pc.infrastructure, pc.vendor) == (
        "Deniz PC",
        "pc",
        person.id,
        "masa 3",
        ["muhasebe"],
        True,
        "Elle girildi",
    )


def test_future_day_only_deletes(db_session: Session) -> None:
    db_session.add(
        Lease(
            ip=PC,
            mac=A,
            start=tr(DAY + timedelta(days=9), 8),
            confidence="high",
            source_table="mt_dhcp",
        )
    )
    db_session.commit()
    report = rebuild_day(db_session, DAY + timedelta(days=9), horizon=tr(DAY, 0))
    assert (report.deleted, report.inserted) == (1, 0)


def test_report_details_are_json_safe(db_session: Session, evidence: Evidence) -> None:
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    db_session.commit()
    details = rebuild_day(db_session, DAY, horizon=tr(NEXT, 0)).details()
    assert details["mode"] == "rebuild"
    assert details["days"] == ["2026-10-12"]
    assert details["events"] == {"dhcp_assigned": 1}
    assert isinstance(details["window_end"], str)
    with pytest.raises(ValueError, match="before"):
        rebuild_days(db_session, NEXT, DAY)


def _row_count(session: Session) -> int:
    return len(session.scalars(select(Lease.id)).all())


def test_bad_vlan_regex_falls_back_to_the_subnet(db_session: Session, evidence: Evidence) -> None:
    set_setting(db_session, "dhcp.server_vlan_regex", "(", updated_by="tolga")
    evidence.dhcp(tr(DAY, 8), "assigned", "172.20.60.183", PHONE, server="dhcp60")
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=tr(NEXT, 0))
    assert _row_count(db_session) == 1
    assert leases(db_session)[0]["vlan"] == 60


@pytest.mark.parametrize(
    ("grace", "interval", "applies"),
    [
        ("60", 3600, 300),  # not an integer
        (True, 3600, 300),
        (-1, 3600, 300),  # negative
        (3601, 3600, 300),  # longer than the interval
        (300, 120, 120),  # the shipped grace, the interval lowered: the default, capped
    ],
)
def test_a_bad_arp_grace_never_stops_the_lease_job(
    db_session: Session,
    evidence: Evidence,
    caplog: pytest.LogCaptureFixture,
    grace: Any,
    interval: int,
    applies: int,
) -> None:
    """``arp.ignore_after_release_seconds`` written around the validator (#40 item 6) is read as
    the lookup reads it: the shipped default, at most the ARP interval, with a warning that names
    the key only, and the day rebuild and the incremental job run to their end."""
    set_setting(db_session, "arp.ignore_after_release_seconds", grace, updated_by="tolga")
    set_setting(db_session, "arp.interval_seconds", interval, updated_by="tolga")
    released = tr(DAY, 9)
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    evidence.dhcp(released, "deassigned", PC, A)
    evidence.arp(released + timedelta(seconds=applies), PC, A)  # still a stale entry
    evidence.arp(released + timedelta(seconds=applies + 1), PC, A)  # a sighting again
    db_session.commit()

    with caplog.at_level(logging.WARNING, logger="arcilogger_worker.leases"):
        assert Context.load(db_session).rules.release_grace == applies
        report = rebuild_day(db_session, DAY, horizon=tr(NEXT, 0))
        db_session.commit()
        assert run_incremental(db_session, now=tr(NEXT, 11)).mode == "catch_up"
        db_session.commit()

    assert report.stats["ignored_arp_after_release"] == 1
    arp = [row for row in leases(db_session) if row["source_table"] == "mt_arp"]
    assert [row["start"] for row in arp] == [released + timedelta(seconds=applies + 1)]
    # One warning per run, the key and the rule only (never the stored value).
    assert {(record.levelname, record.getMessage()) for record in caplog.records} == {
        (
            "WARNING",
            "arp.ignore_after_release_seconds is not an integer between 0 and "
            f"arp.interval_seconds; the default {applies} s applies",
        )
    }


def test_a_good_arp_grace_is_used_as_stored(
    db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    set_setting(db_session, "arp.ignore_after_release_seconds", 0, updated_by="tolga")
    with caplog.at_level(logging.WARNING, logger="arcilogger_worker.leases"):
        assert Context.load(db_session).rules.release_grace == 0
    assert not caplog.records


def test_rebuilding_one_old_day_follows_long_intervals_to_their_end(
    db_session: Session, evidence: Evidence
) -> None:
    """A printer seen every hour for days and a PC kept alive by traffic: rebuilding only the
    first day (look-ahead) gives the same rows as rebuilding every day in order."""
    first = DAY - timedelta(days=1)
    printer, printer_mac = "172.20.10.237", "00:1b:a9:00:00:10"
    evidence.dhcp(tr(first, 8), "assigned", PC, A)
    moment = tr(first, 8)
    while moment <= tr(DAY + timedelta(days=2), 10):
        evidence.arp(moment, printer, printer_mac, "vlan10")
        if moment <= tr(NEXT, 18):
            evidence.traffic(moment + timedelta(minutes=30), PC, 50000 + moment.hour)
        moment += timedelta(hours=1)
    db_session.commit()
    horizon = tr(DAY + timedelta(days=4), 0)
    set_watermark(db_session, horizon)
    db_session.commit()

    rebuild_days(db_session, first, DAY + timedelta(days=3))
    in_order = leases(db_session)
    assert [(r["ip"], r["start"], r["end"], r["end_reason"]) for r in in_order] == [
        (printer, tr(first, 8), tr(DAY + timedelta(days=2), 11), "arp"),
        (PC, tr(first, 8), tr(NEXT, 18, 30) + timedelta(hours=26, minutes=24), "timeout"),
    ]

    db_session.execute(delete(Lease))
    db_session.commit()
    report = rebuild_day(db_session, first)
    db_session.commit()
    assert report.lookahead_chunks >= 3
    assert leases(db_session) == in_order


def test_late_evidence_is_picked_up_by_the_rebuild_of_today(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    db_session.commit()
    run_incremental(db_session, now=tr(DAY, 12))
    db_session.commit()
    # Ingest was behind: a 10:00 release arrives after the incremental job passed 10:00.
    evidence.dhcp(tr(DAY, 10), "deassigned", PC, A)
    db_session.commit()
    run_incremental(db_session, now=tr(DAY, 12, 5))
    db_session.commit()
    assert leases(db_session)[0]["end"] is None  # the incremental job cannot see it …
    rebuild_days(db_session, DAY, DAY)  # … the hourly re-check / nightly rebuild does
    assert (leases(db_session)[0]["end"], leases(db_session)[0]["end_reason"]) == (
        tr(DAY, 10),
        "dhcp",
    )


# -- the watermark never passes evidence that is not loaded yet ----------------------------------


def _loaded_until(evidence: Evidence, last: datetime, *, every: int = 5, hours: int = 2) -> None:
    """Ingest loaded the CORE-R1 lines up to ``last`` (a line every ``every`` minutes)."""
    moment = last - timedelta(hours=hours)
    while moment <= last:
        evidence.heartbeat(moment)
        moment += timedelta(minutes=every)


def test_an_ingest_outage_holds_the_watermark(db_session: Session, evidence: Evidence) -> None:
    """Ingest stopped at 09:00 while the worker kept running: the watermark waits at what was
    loaded, so the release and the new assignment loaded later are applied in order."""
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    _loaded_until(evidence, tr(DAY, 9))
    db_session.commit()
    run_incremental(db_session, now=tr(DAY, 9, 30))
    db_session.commit()
    assert get_watermark(db_session) == tr(DAY, 8, 58)  # 09:00 loaded, minus the 120 s lag
    assert run_incremental(db_session, now=tr(DAY, 9, 31)).window_end == tr(DAY, 8, 58)
    db_session.commit()

    # Ingest catches up: the 09:10 release and reassignment arrive after 09:30.
    evidence.dhcp(tr(DAY, 9, 10), "deassigned", PC, A)
    evidence.dhcp(tr(DAY, 9, 10, 1), "assigned", PC, B)
    _loaded_until(evidence, tr(DAY, 9, 35), hours=0, every=1)
    db_session.commit()
    report = run_incremental(db_session, now=tr(DAY, 9, 36))
    db_session.commit()
    assert report.mode == "incremental"
    assert get_watermark(db_session) == tr(DAY, 9, 33)
    chain = attribute(db_session, PC, tr(DAY, 9, 11))
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.mac == B  # not the released A, not "no lease"
    first = leases(db_session)[0]
    assert (first["mac"], first["end"], first["end_reason"]) == (A, tr(DAY, 9, 10), "dhcp")


def test_a_device_clock_behind_moves_the_horizon_back(
    db_session: Session, evidence: Evidence
) -> None:
    """The newest loaded line carries a device time 180 s behind the server clock: the
    horizon follows the device time, so lines with that clock are never skipped."""
    now = tr(DAY, 12)
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    _loaded_until(evidence, now - timedelta(seconds=180))
    db_session.commit()
    assert ingest_progress(db_session, now) == now - timedelta(seconds=180)
    run_incremental(db_session, now=now)
    db_session.commit()
    watermark = get_watermark(db_session)
    assert watermark is not None
    assert watermark <= now - timedelta(seconds=180 + 120)


def test_an_ingest_outage_longer_than_a_day_still_holds_the_watermark(
    db_session: Session, evidence: Evidence
) -> None:
    """Ingest stays down for 25 hours while the worker runs: no source has a line in the last
    day, but the sources that fed the timeline before the watermark still hold it."""
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    _loaded_until(evidence, tr(DAY, 9))
    db_session.commit()
    run_incremental(db_session, now=tr(DAY, 9, 30))
    db_session.commit()
    assert get_watermark(db_session) == tr(DAY, 8, 58)
    later = tr(DAY, 9) + timedelta(hours=25)
    assert ingest_progress(db_session, later, watermark=tr(DAY, 8, 58)) == tr(DAY, 9)
    run_incremental(db_session, now=later)
    db_session.commit()
    assert get_watermark(db_session) == tr(DAY, 8, 58)
    # Ingest comes back and loads the 09:10 release and reassignment (and what followed).
    evidence.dhcp(tr(DAY, 9, 10), "deassigned", PC, A)
    evidence.dhcp(tr(DAY, 9, 10, 1), "assigned", PC, B)
    _loaded_until(evidence, later, hours=26, every=5)
    db_session.commit()
    run_incremental(db_session, now=later + timedelta(minutes=1))
    db_session.commit()
    chain = attribute(db_session, PC, tr(DAY, 9, 11))
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.mac == B


def test_a_device_clock_far_behind_is_not_mistaken_for_silence(
    db_session: Session, evidence: Evidence
) -> None:
    """CORE-R1's clock runs 40 minutes behind (more than SILENT_AFTER) while it sends every
    minute: silence is judged by receive time, so its device time still bounds the horizon."""
    now = tr(DAY, 12)
    db_session.add(Source(name="fortigate", type="fortigate", address="172.31.255.1"))
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    evidence.heartbeat(now - timedelta(seconds=30), source="fortigate")
    behind = timedelta(minutes=40)
    moment = now - timedelta(hours=1)
    while moment <= now - timedelta(seconds=20):
        evidence.heartbeat(moment - behind, received=moment)
        moment += timedelta(minutes=1)
    db_session.commit()
    newest = now - timedelta(seconds=60) - behind
    assert ingest_progress(db_session, now) == newest
    assert lease_horizon(db_session, now) == newest - timedelta(seconds=120)


def _collector_status(
    layout: Layout, now: datetime, received: dict[str, datetime], *, age: timedelta = timedelta(0)
) -> None:
    layout.collector_status.parent.mkdir(parents=True, exist_ok=True)
    status = {
        "updated_at": isoformat_site(now - age, "seconds"),
        "sources": {
            name: {"messages": 1, "bytes": 1, "last_received": isoformat_site(moment)}
            for name, moment in received.items()
        },
    }
    layout.collector_status.write_text(json.dumps(status), encoding="utf-8")


def test_the_collector_status_tells_a_backlog_from_silence(
    db_session: Session, evidence: Evidence, layout: Layout
) -> None:
    """After an ingest outage CORE-R1 is loaded again, the FortiGate (far more lines) is still
    50 minutes behind. By the loaded lines alone it looks silent; the collector's status shows
    it is still sending, so it holds the horizon at what is loaded."""
    now = tr(DAY, 12)
    db_session.add(Source(name="fortigate", type="fortigate", address="172.31.255.1"))
    evidence.dhcp(tr(DAY, 8), "assigned", PC, A)
    evidence.heartbeat(now - timedelta(minutes=50), source="fortigate")
    _loaded_until(evidence, now - timedelta(seconds=30), hours=1, every=1)
    db_session.commit()
    fortigate_loaded = now - timedelta(minutes=50)
    router_loaded = now - timedelta(seconds=30)
    # Without the collector's view the FortiGate counts as silent.
    assert ingest_progress(db_session, now) == router_loaded
    _collector_status(layout, now, {"fortigate": now, "core-r1": router_loaded})
    received = collector_received(layout, now)
    assert received is not None
    assert received["fortigate"] == now
    assert ingest_progress(db_session, now, received=received) == fortigate_loaded
    run_incremental(db_session, now=now, layout=layout)
    db_session.commit()
    assert get_watermark(db_session) == fortigate_loaded - timedelta(seconds=120)

    # A source the collector has not heard from for longer than SILENT_AFTER, with nothing
    # left to load, is silent: it does not hold the others back.
    quiet = {"fortigate": fortigate_loaded, "core-r1": router_loaded}
    assert ingest_progress(db_session, now, received=quiet) == router_loaded
    # Silent now, but received later than what is loaded: its backlog still holds.
    backlog = {"fortigate": now - SILENT_AFTER - timedelta(minutes=1), "core-r1": router_loaded}
    assert ingest_progress(db_session, now, received=backlog) == fortigate_loaded
    # Every source silent and fully loaded: nothing to wait for.
    assert ingest_progress(db_session, now + timedelta(hours=1), received=quiet) is None
    assert lease_horizon(db_session, now + timedelta(hours=1), received=quiet) == now + timedelta(
        minutes=58
    )


def test_a_stale_or_broken_collector_status_is_not_used(layout: Layout) -> None:
    now = tr(DAY, 12)
    assert collector_received(None, now) is None
    assert collector_received(layout, now) is None  # no file
    _collector_status(layout, now, {"fortigate": now}, age=COLLECTOR_STATUS_FRESH * 2)
    assert collector_received(layout, now) is None  # the collector is down
    layout.collector_status.write_text("{broken", encoding="utf-8")
    assert collector_received(layout, now) is None
    layout.collector_status.write_text(
        json.dumps(
            {
                "updated_at": isoformat_site(now, "seconds"),
                "sources": {"a": {"last_received": None}, "b": "x", "c": {}},
            }
        ),
        encoding="utf-8",
    )
    assert collector_received(layout, now) == {}


def test_a_silent_source_does_not_hold_the_others_back(
    db_session: Session, evidence: Evidence
) -> None:
    """The FortiGate went silent at 08:00 (device off; its own "log source silent" alert):
    after SILENT_AFTER it no longer stops the timeline of the DHCP evidence."""
    db_session.add(Source(name="fortigate", type="fortigate", address="172.31.255.1"))
    evidence.traffic(tr(DAY, 7, 59), PC, 50001)
    evidence.heartbeat(tr(DAY, 8), source="fortigate")
    evidence.dhcp(tr(DAY, 7), "assigned", PC, A)
    _loaded_until(evidence, tr(DAY, 8, 20), hours=0, every=1)
    db_session.commit()
    assert feeding_sources(db_session, tr(DAY, 8, 21)) == {"core-r1", "fortigate"}
    # 20 minutes of silence: maybe only not loaded yet — it holds the others.
    assert ingest_progress(db_session, tr(DAY, 8, 21)) == tr(DAY, 8)
    _loaded_until(evidence, tr(DAY, 9), hours=0, every=1)
    db_session.commit()
    # An hour of silence while CORE-R1 is loaded up to 09:00: silent, ignored.
    assert ingest_progress(db_session, tr(DAY, 9, 1)) == tr(DAY, 9)
    assert lease_horizon(db_session, tr(DAY, 9, 1)) == tr(DAY, 8, 58)
    # Lines dated in the future (a device clock ahead) do not count as progress.
    evidence.heartbeat(tr(DAY, 23))
    db_session.commit()
    assert ingest_progress(db_session, tr(DAY, 9, 1)) == tr(DAY, 9)
