"""The 5651 lookup on a FortiGate-only site (ADR-0016; Wave 3 contract 2.7, 3.4 "FortiGate-only
golden"), against PostgreSQL.

Every site is written with ``Evidence.fortigate_dhcp`` plus traffic and built day by day with
``rebuild_day`` (so seeds at midnight are exercised), never with the API's ``build_site``. The
example chain: Selin Kaya (Planlama) on ``Planlama-PC-14``, ``172.20.42.57`` /
``00:00:5e:00:53:57`` in VLAN 42. Covered here: the chain in internal, candidate and exact mode,
the lease facts for the lookup moment (the lease time and the lease's own newest DHCP record,
never the line that ended it and never a renewal after the moment), the format note (D29), a
missing renewal (D6), the DECLINE shapes of D33 that the lease build decides, a RELEASE mapped
after the build, and unconfigured senders (D24).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from arcilogger_db.egress import validate_egresses
from arcilogger_db.models import Department, Device, Lease, Person, Setting, Source, Vlan
from arcilogger_db.settings import get_settings, set_setting
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import (
    AttributionChain,
    LookupOutcome,
    LookupRequest,
    LookupSettings,
    run_lookup,
)
from arcilogger_worker.lookup.attribution import Attributor
from arcilogger_worker.lookup.types import EvidenceRef
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)
AT = tr(DAY, 11, 23, 41)
NOW = tr(DAY + timedelta(days=9), 9)
#: When the exits were entered: before the evidence (no exit change after the event).
ENTERED = tr(DAY - timedelta(days=30), 9)
PC, PC_MAC, HOST = "172.20.42.57", "00:00:5e:00:53:57", "PLN-PC14"
WAN = "100.64.12.9"
#: A declining client (D33), another client and the subnet of VLAN 42.
DECLINER, OTHER = "00:00:5e:00:53:31", "00:00:5e:00:53:77"
PLANLAMA = "172.20.42.0/24"
#: The two format notes of D29.
FORMAT_NOTES = {"fortigate_format_unverified", "fortigate_format_verified"}


def build(session: Session, first: date, last: date) -> None:
    """Build the leases of ``first`` … ``last`` one day at a time, as the nightly job does."""
    session.commit()
    day = first
    while day <= last:
        rebuild_day(session, day, horizon=NOW)
        session.commit()
        day += timedelta(days=1)


def settings_for(session: Session) -> LookupSettings:
    return LookupSettings.from_settings(get_settings(session))


def lookup(session: Session, **request: Any) -> LookupOutcome:
    request.setdefault("at", AT)
    outcome = run_lookup(session, LookupRequest(**request), settings_for(session), now=NOW)
    session.rollback()
    data = outcome.to_dict()
    assert LookupOutcome.from_dict(json.loads(json.dumps(data))) == outcome
    return outcome


def internal(session: Session, ip: str, at: datetime) -> LookupOutcome:
    return lookup(session, internal_ip=ip, at=at)


def keys(outcome: LookupOutcome) -> list[str]:
    return [e.key for e in outcome.explanations]


def chain_keys(chain: AttributionChain) -> list[str]:
    return [e.key for e in chain.explanations]


def note(chain: AttributionChain, key: str) -> dict[str, Any]:
    return dict(next(e for e in chain.explanations if e.key == key).params)


def iso(moment: datetime) -> str:
    """A moment as an explanation's parameter holds it (ISO 8601, UTC)."""
    return moment.astimezone(UTC).isoformat()


def selin(session: Session) -> int:
    """Selin Kaya of Planlama, linked to the PC's device (which the lease job made)."""
    planning = session.scalars(select(Department).where(Department.name == "Planlama")).one()
    person = Person(name="Selin Kaya", department_id=planning.id, title="Planlama Uzmanı")
    session.add(person)
    session.flush()
    device = session.get(Device, PC_MAC)
    assert device is not None
    device.person_id = person.id
    device.name = "Planlama-PC-14"
    session.commit()
    return person.id


# -- the example chain ----------------------------------------------------------------------------


@pytest.fixture
def example(db_session: Session, evidence: Evidence) -> list[int]:
    """The PC's ACKs of the day (08:00, a re-ACK at 11:00, the renewal at 20:00 with a shorter
    lease) and its session at AT; returns the ACK row ids."""
    acks = [
        evidence.fortigate_dhcp(tr(DAY, 8), PC, PC_MAC, hostname=HOST),
        evidence.fortigate_dhcp(tr(DAY, 11), PC, PC_MAC, hostname=HOST),
        evidence.fortigate_dhcp(tr(DAY, 20), PC, PC_MAC, hostname=HOST, lease=43200),
    ]
    evidence.traffic(AT, PC, 51001, "203.0.113.10", 443, transip=WAN, duration=10)
    build(db_session, DAY, DAY + timedelta(days=1))
    selin(db_session)
    return acks


def test_the_example_chain_from_fortigate_dhcp_alone(
    db_session: Session, evidence: Evidence, example: list[int]
) -> None:
    outcome = internal(db_session, PC, AT)
    assert outcome.mode == "internal"
    assert "no_lease" not in keys(outcome)
    chain = outcome.attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.device is not None
    assert (chain.lease.mac, chain.device.name, chain.department, chain.vlan) == (
        PC_MAC,
        "Planlama-PC-14",
        "Planlama",
        42,
    )
    assert chain.person is not None
    assert chain.person.name == "Selin Kaya"
    lease = chain.lease
    assert (lease.confidence, lease.source_table) == ("high", "fg_dhcp")
    # The record at the moment: the 11:00 ACK and its lease, not the 20:00 renewal's 43200 s
    # that the stored lease keeps (round 6, "the lease time shown").
    assert (lease.lease_seconds, lease.last_dhcp_evidence) == (86400, tr(DAY, 11))
    stored = db_session.scalars(select(Lease)).one()
    assert stored.lease_seconds == 43200
    dhcp = [ref for ref in lease.evidence if ref.table == "fg_dhcp"]
    assert [ref.row_id for ref in dhcp] == example
    for ref in dhcp:
        assert ref.raw is not None
        assert ref.raw.source == "fortigate"
        line = evidence.raw_line(ref.raw.source, ref.raw.day, ref.raw.line, ref.raw.offset or 0)
        assert b'logid="0100026001"' in line
        assert PC_MAC.upper().encode() in line
    # D29: the format note, and no other note on FortiGate DHCP.
    assert chain_keys(chain) == ["fortigate_format_unverified"]
    assert chain.confidence == "medium"  # the person step, as on a MikroTik site


def test_candidate_and_exact_mode_give_the_same_chain(
    db_session: Session, example: list[int]
) -> None:
    chain = internal(db_session, PC, AT).attribution
    assert chain is not None
    # No exits: the public address goes to the implicit double-NAT exit (candidates).
    candidates = lookup(db_session, public_ip="198.51.100.20")
    assert candidates.mode == "candidates"
    (match,) = candidates.matches
    assert match.chain == chain
    # Exit "wan" with the FortiGate's NAT address: the exact session.
    value = validate_egresses({"wan": {"mode": "isp_nat", "transips": [WAN]}})
    set_setting(db_session, "nat.egresses", value, updated_by="tolga")
    db_session.execute(
        update(Setting).where(Setting.key == "nat.egresses").values(updated_at=ENTERED)
    )
    db_session.commit()
    exact = lookup(db_session, public_ip=WAN, public_port=51001, protocol="tcp")
    assert exact.mode == "exact"
    (match,) = exact.matches
    assert (match.traffic.srcip, match.traffic.transport, match.egress) == (PC, 51001, "wan")
    assert match.chain == chain


def test_the_format_note_names_who_verified_it_and_when(
    db_session: Session, example: list[int]
) -> None:
    set_setting(db_session, "dhcp.fortigate_format_verified", True, updated_by="tolga")
    marked = tr(DAY + timedelta(days=2), 15, 30)
    db_session.execute(
        update(Setting)
        .where(Setting.key == "dhcp.fortigate_format_verified")
        .values(updated_at=marked)
    )
    db_session.commit()
    chain = internal(db_session, PC, AT).attribution
    assert chain is not None
    assert chain_keys(chain) == ["fortigate_format_verified"]
    verified = next(e for e in chain.explanations if e.key == "fortigate_format_verified")
    assert dict(verified.params) == {"at": iso(marked), "by": "tolga"}
    assert "14.10.2026 15:30:00" in verified.text_tr
    assert "tolga" in verified.text_en


def test_a_mikrotik_chain_carries_no_fortigate_note(
    db_session: Session, evidence: Evidence
) -> None:
    ack = evidence.dhcp(tr(DAY, 8), "assigned", PC, PC_MAC, server="dhcp42", hostname=HOST)
    build(db_session, DAY, DAY)
    chain = internal(db_session, PC, AT).attribution
    assert chain is not None
    assert chain.lease is not None
    assert not FORMAT_NOTES & set(chain_keys(chain))
    assert "lease_renewal_not_seen" not in chain_keys(chain)
    lease = chain.lease
    assert (lease.source_table, lease.lease_seconds, lease.last_dhcp_evidence) == (
        "mt_dhcp",
        None,
        tr(DAY, 8),
    )
    assert [ref.row_id for ref in lease.evidence] == [ack]


# -- a missing renewal (D6) -----------------------------------------------------------------------

T0 = tr(DAY, 8)


def test_no_renewal_and_traffic_only(db_session: Session, evidence: Evidence) -> None:
    """The ACK at 08:00 (lease 86400) is never renewed while traffic from the address goes on
    for two days: the lease ends at t0 + 24 h (after a midnight; traffic does not extend it,
    D26), and from half the lease on the chain is ``low``."""
    evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=HOST)
    for hour in range(0, 49, 2):
        evidence.traffic(T0 + timedelta(hours=hour), PC, 50000 + hour)
    build(db_session, DAY, DAY + timedelta(days=2))
    selin(db_session)

    gone = internal(db_session, PC, T0 + timedelta(days=1.5))
    assert (gone.mode, gone.attribution) == ("none", None)
    assert "no_lease" in keys(gone)

    late = internal(db_session, PC, T0 + timedelta(hours=15)).attribution
    assert late is not None
    assert late.lease is not None
    assert (late.confidence, late.lease.confidence) == ("low", "low")
    assert late.lease.mac == PC_MAC
    assert late.lease.last_dhcp_evidence == T0
    assert note(late, "lease_renewal_not_seen") == {"last": iso(T0), "lease": 86400}

    early = internal(db_session, PC, T0 + timedelta(hours=11)).attribution
    assert early is not None
    assert early.lease is not None
    assert early.lease.confidence == "high"
    assert "lease_renewal_not_seen" not in chain_keys(early)


def test_renewals_every_twelve_hours_keep_the_chain_high(
    db_session: Session, evidence: Evidence
) -> None:
    for hours in (0, 12, 24, 36):
        evidence.fortigate_dhcp(T0 + timedelta(hours=hours), PC, PC_MAC, hostname=HOST)
    for hour in range(0, 49, 2):
        evidence.traffic(T0 + timedelta(hours=hour), PC, 50000 + hour)
    build(db_session, DAY, DAY + timedelta(days=2))
    for hours in (11, 15, 30, 37):
        chain = internal(db_session, PC, T0 + timedelta(hours=hours)).attribution
        assert chain is not None, hours
        assert chain.lease is not None
        assert chain.lease.confidence == "high", hours
        assert "lease_renewal_not_seen" not in chain_keys(chain), hours
        assert chain.lease.last_dhcp_evidence == T0 + timedelta(hours=hours // 12 * 12), hours


@pytest.mark.parametrize(
    ("lease", "taken", "moment"),
    [
        (604800, timedelta(days=5), timedelta(days=4)),
        (86400, timedelta(hours=22, minutes=30), timedelta(hours=14)),
    ],
)
def test_a_lease_superseded_early_without_renewal(
    db_session: Session,
    evidence: Evidence,
    lease: int,
    taken: timedelta,
    moment: timedelta,
) -> None:
    """A phone gone without a logged RELEASE; the FortiGate gives the address to B before A's
    lease ends. B's ACK ended A's lease, but it is not A's evidence: measured from A's own last
    ACK, the moment lies past half the lease (2.7)."""
    ip = "172.20.42.142"
    evidence.fortigate_dhcp(T0, ip, DECLINER, lease=lease)
    evidence.fortigate_dhcp(T0 + taken, ip, OTHER, lease=lease)
    build(db_session, DAY, (T0 + taken).date())
    stored = db_session.scalars(select(Lease).where(Lease.mac == DECLINER)).one()
    assert (stored.end, stored.end_reason) == (T0 + taken, "superseded")
    chain = internal(db_session, ip, T0 + moment).attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.mac == DECLINER
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert chain.lease.last_dhcp_evidence == T0
    assert note(chain, "lease_renewal_not_seen") == {"last": iso(T0), "lease": lease}


def test_a_renewal_after_the_moment_is_not_its_evidence(
    db_session: Session, evidence: Evidence
) -> None:
    """A came back inside its lease at t0 + 23 h 50 min (a continuation of the same interval);
    at t0 + 14 h only a static device could have used the address (round 5)."""
    evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=HOST)
    evidence.traffic(T0 + timedelta(hours=14), PC, 50001)
    back = T0 + timedelta(hours=23, minutes=50)
    evidence.fortigate_dhcp(back, PC, PC_MAC, hostname=HOST)
    build(db_session, DAY, DAY + timedelta(days=1))
    assert db_session.scalars(select(Lease)).one().start == T0  # one interval

    before = internal(db_session, PC, T0 + timedelta(hours=14)).attribution
    assert before is not None
    assert before.lease is not None
    assert (before.confidence, before.lease.confidence) == ("low", "low")
    assert before.lease.last_dhcp_evidence == T0
    assert note(before, "lease_renewal_not_seen")["last"] == iso(T0)

    after = internal(db_session, PC, back + timedelta(minutes=5)).attribution
    assert after is not None
    assert after.lease is not None
    assert (after.confidence, after.lease.confidence) == ("high", "high")
    assert after.lease.last_dhcp_evidence == back


def test_dropped_refs_are_read_from_the_rows(db_session: Session, evidence: Evidence) -> None:
    """Lease 3600 renewed every 30 minutes for 10 hours: the stored refs keep the opening ACK and
    the last six continuations, so the record just before the moment is read from the rows."""
    for step in range(21):
        evidence.fortigate_dhcp(T0 + timedelta(minutes=30 * step), PC, PC_MAC, lease=3600)
    build(db_session, DAY, DAY)
    moment = T0 + timedelta(hours=2, minutes=10)
    record = T0 + timedelta(hours=2)
    stored = db_session.scalars(select(Lease)).one()
    assert record not in {EvidenceRef.from_dict(e).time for e in stored.evidence}
    chain = internal(db_session, PC, moment).attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.confidence, chain.lease.last_dhcp_evidence) == ("high", record)
    assert "lease_renewal_not_seen" not in chain_keys(chain)
    # The usage rollups (no lookup) read the stored refs only (round 6: the per-moment query
    # runs for lookups).
    usage = Attributor(db_session, settings_for(db_session)).chain(PC, moment)
    assert usage is not None
    assert usage.lease is not None
    assert usage.lease.last_dhcp_evidence == T0


# -- DECLINE (D33) --------------------------------------------------------------------------------


def _decline(evidence: Evidence, interface: str = "vlan42") -> None:
    """:31 declines .57 (a printer set statically to it) and takes .58 four seconds later."""
    evidence.fortigate_dhcp(tr(DAY, 8, 41, 10), PC, DECLINER, interface=interface)
    evidence.fortigate_dhcp(tr(DAY, 8, 41, 14), "172.20.42.58", DECLINER, interface=interface)
    evidence.traffic(tr(DAY, 10, 12), PC, 50001)


PRINTER_AT = tr(DAY, 10, 12)


def test_a_decline_leaves_no_high_chain_on_the_declined_address(
    db_session: Session, evidence: Evidence
) -> None:
    _decline(evidence)
    build(db_session, DAY, DAY)
    declined = internal(db_session, PC, PRINTER_AT)
    assert declined.attribution is None
    assert "no_lease" in keys(declined)
    taken = internal(db_session, "172.20.42.58", PRINTER_AT).attribution
    assert taken is not None
    assert taken.lease is not None
    assert (taken.lease.mac, taken.lease.confidence) == (DECLINER, "high")
    assert "fortigate_dhcp_no_network" not in chain_keys(taken)


def test_a_decline_on_a_network_without_a_vlan_id(db_session: Session, evidence: Evidence) -> None:
    """A FortiGate-only flat LAN on ``internal``, entered without a VLAN number (round 6)."""
    db_session.execute(update(Vlan).where(Vlan.subnet == PLANLAMA).values(vlan_id=None))
    _decline(evidence, interface="internal")
    build(db_session, DAY, DAY)
    declined = internal(db_session, PC, PRINTER_AT)
    assert declined.attribution is None  # no high or medium chain for :31
    assert "no_lease" in keys(declined)
    taken = internal(db_session, "172.20.42.58", PRINTER_AT).attribution
    assert taken is not None
    assert taken.lease is not None
    assert (taken.lease.mac, taken.vlan) == (DECLINER, None)
    assert "fortigate_dhcp_no_network" not in chain_keys(taken)


def test_a_decline_in_no_configured_network_is_low(db_session: Session, evidence: Evidence) -> None:
    """D33 cannot close a declined address outside every configured network: both chains of
    :31 are ``low`` (lease and chain) with ``fortigate_dhcp_no_network``, never higher."""
    db_session.execute(delete(Vlan).where(Vlan.subnet == PLANLAMA))
    _decline(evidence)
    build(db_session, DAY, DAY)
    for ip in (PC, "172.20.42.58"):
        chain = internal(db_session, ip, PRINTER_AT).attribution
        assert chain is not None, ip
        assert chain.lease is not None
        assert (chain.lease.mac, chain.vlan) == (DECLINER, None)
        assert (chain.confidence, chain.lease.confidence) == ("low", "low"), ip
        assert note(chain, "fortigate_dhcp_no_network") == {"ip": ip}


# -- a RELEASE mapped after the build (round 6) ---------------------------------------------------


def test_a_release_mapped_after_the_build_ends_the_lease(
    db_session: Session, evidence: Evidence
) -> None:
    """The leases were built while the RELEASE logid was unmapped (refused); once it is mapped
    and the rebuild skipped, the open lease no longer covers the moments after that RELEASE: no
    chain, as after the rebuild (a recorded deviation: no new text)."""
    set_setting(db_session, "dhcp.fortigate_logids", {"0100026001": "assigned"}, updated_by="tolga")
    evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=HOST)
    evidence.fortigate_dhcp(
        tr(DAY, 12), PC, PC_MAC, logid="0100026002", dhcp_msg="Release", lease=None
    )
    build(db_session, DAY, DAY)
    assert db_session.scalars(select(Lease)).one().end_reason == "timeout"
    mapped = {"0100026001": "assigned", "0100026002": "deassigned"}
    set_setting(db_session, "dhcp.fortigate_logids", mapped, updated_by="tolga")
    db_session.commit()

    held = internal(db_session, PC, tr(DAY, 11)).attribution
    assert held is not None
    assert held.lease is not None
    assert (held.lease.mac, held.lease.confidence) == (PC_MAC, "high")
    released = internal(db_session, PC, tr(DAY, 14))
    assert released.attribution is None
    assert "no_lease" in keys(released)
    # The rebuild gives the same answers.
    build(db_session, DAY, DAY)
    assert db_session.scalars(select(Lease)).one().end == tr(DAY, 12)
    assert internal(db_session, PC, tr(DAY, 14)).attribution is None
    rebuilt = internal(db_session, PC, tr(DAY, 11)).attribution
    assert rebuilt is not None
    assert rebuilt.lease is not None
    assert (rebuilt.lease.mac, rebuilt.lease.confidence, rebuilt.lease.last_dhcp_evidence) == (
        PC_MAC,
        "high",
        held.lease.last_dhcp_evidence,
    )


def test_a_release_and_a_renewal_in_one_second_keep_the_new_lease(
    db_session: Session, evidence: Evidence
) -> None:
    """A RELEASE and an ACK of the pair in the same second: the build ends the old lease at that
    second and opens a new one (release before assignment). The RELEASE row is written last (a
    higher id), yet the new lease's own record at a later moment is the ACK, as in the build's
    order: the chain stays ``high``, never a false "nobody"."""
    renewed = tr(DAY, 12)
    evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=HOST)
    evidence.fortigate_dhcp(renewed, PC, PC_MAC, hostname=HOST)
    evidence.fortigate_dhcp(renewed, PC, PC_MAC, logid="0100026002", dhcp_msg="Release", lease=None)
    build(db_session, DAY, DAY)
    stored = db_session.scalars(select(Lease).order_by(Lease.start)).all()
    assert [(lease.start, lease.end, lease.end_reason) for lease in stored] == [
        (T0, renewed, "dhcp"),
        (renewed, renewed + timedelta(days=1), "timeout"),
    ]

    for moment in (renewed, tr(DAY, 12, 0, 1), tr(DAY, 13)):
        chain = internal(db_session, PC, moment).attribution
        assert chain is not None, moment
        assert chain.lease is not None
        assert (chain.lease.mac, chain.lease.start, chain.lease.confidence) == (
            PC_MAC,
            renewed,
            "high",
        )
        assert chain.lease.last_dhcp_evidence == renewed
        assert chain.confidence == "high"


# -- unconfigured senders (D24) -------------------------------------------------------------------


def test_an_ack_from_an_unknown_sender_creates_no_chain_and_no_device(
    db_session: Session, evidence: Evidence
) -> None:
    db_session.add(
        Source(name="unknown-192.0.2.7", type="fortigate", address="192.0.2.7", unknown=True)
    )
    evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=HOST, source="unknown-192.0.2.7")
    evidence.traffic(AT, PC, 51001)
    build(db_session, DAY, DAY)
    assert db_session.scalars(select(Lease)).all() == []
    assert db_session.get(Device, PC_MAC) is None
    outcome = internal(db_session, PC, AT)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert "no_lease" in keys(outcome)


def test_a_sender_configured_later_is_named_on_the_chain(
    db_session: Session, evidence: Evidence
) -> None:
    """An HA member's ACKs arrived from an address nobody had configured; once a FortiGate source
    covers that address and the day is rebuilt, the chain uses them and says so."""
    sender = "unknown-172.20.10.1"
    db_session.add(Source(name=sender, type="fortigate", address="172.20.10.1", unknown=True))
    evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=HOST, source=sender)
    build(db_session, DAY, DAY)
    assert internal(db_session, PC, AT).attribution is None
    db_session.add(Source(name="fortigate-2", type="fortigate", address="172.20.10.0/30"))
    build(db_session, DAY, DAY)
    chain = internal(db_session, PC, AT).attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.evidence[0].raw is not None
    assert chain.lease.evidence[0].raw.source == sender
    assert note(chain, "source_configured_later") == {"sources": sender}
    assert chain.lease.confidence == "high"  # information only
