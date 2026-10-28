"""The 5651 lookup on a FortiGate-only site (ADR-0016; Wave 3 contract 2.7, 3.4 "FortiGate-only
golden"), against PostgreSQL.

Every site is written with ``Evidence.fortigate_dhcp`` plus traffic and built day by day with
``rebuild_day`` (so seeds at midnight are exercised), never with the API's ``build_site``. The
example chain: Selin Kaya (Planlama) on ``Planlama-PC-14``, ``172.20.42.57`` /
``00:00:5e:00:53:57`` in VLAN 42. Covered here: the chain in internal, candidate and exact mode,
the lease facts for the lookup moment (the lease time and the lease's own newest DHCP record,
never the line that ended it and never a renewal after the moment), the format note (D29), a
missing renewal (D6), the DECLINE shapes of D33 (those the lease build decides and those a
build made before a network was entered missed), relayed networks, the migrations from MikroTik
DHCP (D5, D33's MikroTik variant, a move loaded before the upgrade), a rollback window (D8), two
DHCP origins (D27), the probe for FortiGate DHCP lines that never became lease evidence when no
chain could be built (D8, 2.8.3), a RELEASE mapped after the build, unconfigured senders (D24),
ACKs the build refused and today's settings accept (deviation c2) and a FortiGate clock that was
off at the moment (D28). Last, the list of every explanation the engine can emit with a test
that emits it (4.3 WP-C, "done when").
"""

from __future__ import annotations

import ast
import ipaddress
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import to_site
from arcilogger_db.egress import validate_egresses
from arcilogger_db.models import (
    Department,
    Device,
    FgDhcp,
    FgEvent,
    FgTraffic,
    Lease,
    Message,
    Person,
    SearchCount,
    Setting,
    Source,
    Vlan,
)
from arcilogger_db.searchcounts import rebuild_stream
from arcilogger_db.settings import get_settings, set_setting
from arcilogger_worker.alerts import system
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import (
    AttributionChain,
    LookupOutcome,
    LookupRequest,
    LookupSettings,
    attribution,
    engine,
    run_lookup,
    trust,
)
from arcilogger_worker.lookup.attribution import Attributor
from arcilogger_worker.lookup.texts import ENGINE_EXPLANATIONS
from arcilogger_worker.lookup.types import EXPLANATIONS, EvidenceRef
from arcilogger_worker.testing import FIREWALL, FORTIGATE_DHCP_TEXTS, Evidence, tr

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
#: The post-lease warning of 2.7 (D8, D33) and the two-origins warning (D27).
UNCONVERTED, OVERLAP = "fortigate_dhcp_not_converted_since_lease", "dhcp_sources_overlap"
#: An unconfigured sender (D24).
STRANGER = "unknown-192.0.2.7"


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


def outcome_text(outcome: LookupOutcome) -> str:
    """The outcome as stored and shown (JSON, explanation texts included)."""
    return json.dumps(outcome.to_dict(), ensure_ascii=False).lower()


def unconverted_line(
    evidence: Evidence,
    time: datetime,
    ip: str,
    mac: str | None,
    *,
    hostname: str | None = None,
    lease: int | None = 86400,
    interface: str = "vlan42",
    source: str = "fortigate",
    logid: str = "0100026001",
    dhcp_msg: str | None = None,
    without_dhcp_msg: bool = False,
) -> int:
    """A FortiGate DHCP ACK (a RELEASE with ``logid="0100026002"`` and ``lease=None``) that
    stayed an ``fg_event`` row with the full DHCP shape (D8: a line loaded before the upgrade,
    or by the previous version after a rollback): its spool line and the row that version
    stored, every DHCP field in ``extra``; returns the row id. ``dhcp_msg``: another message
    than the logid's own ("Ack" / "Release"); ``without_dhcp_msg``: a line without the field,
    which D3 keeps as an ``fg_event`` row today too; ``mac=None``: a line without the field."""
    sender = source.removeprefix("unknown-") if source.startswith("unknown-") else FIREWALL
    local = to_site(time)
    logdesc, msg = FORTIGATE_DHCP_TEXTS[logid]
    kind = dhcp_msg or ("Ack" if logid == "0100026001" else "Release")
    fields = {
        "interface": interface,
        "dhcp_msg": None if without_dhcp_msg else kind,
        "mac": None if mac is None else mac.upper(),
        "ip": ip,
        "lease": None if lease is None else str(lease),
        "hostname": hostname,
    }
    tail = "".join(
        f" {key}={value}" if key in ("ip", "lease") else f' {key}="{value}"'
        for key, value in fields.items()
        if value is not None
    )
    message = (
        f'<190>date={local:%Y-%m-%d} time={local:%H:%M:%S} devname="FIREWALL-01" '
        f'devid="FGT0000000000000" eventtime={int(time.timestamp() * 1e9)} tz="+0300" '
        f'logid="{logid}" type="event" subtype="system" level="information" vd="root" '
        f'logdesc="{logdesc}"{tail} msg="{msg}"'
    )
    common = evidence._write(source, sender, time, message)
    extra = {key: value for key, value in fields.items() if value is not None}
    row = FgEvent(
        **common,
        logid=logid,
        subtype="system",
        level="information",
        logdesc=logdesc,
        msg=msg,
        extra={"devid": "FGT0000000000000", "vd": "root", **extra},
    )
    evidence.session.add(row)
    evidence.session.flush()
    return int(row.id)


def stranger(session: Session) -> None:
    """The sources row of :data:`STRANGER` (filed as unconfigured by the collector)."""
    session.add(Source(name=STRANGER, type="fortigate", address="192.0.2.7", unknown=True))


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


def test_a_srcmac_that_contradicts_the_lease(db_session: Session, evidence: Evidence) -> None:
    """3.4 "srcmac contradicts the lease" (D10): ACK(A), then a session of the address on a
    listed interface whose srcmac is B: A's chain at ``low`` with ``srcmac_mismatch``, and B's
    srcmac chain as the alternative, also ``low``."""
    evidence.fortigate_dhcp(tr(DAY, 8), PC, PC_MAC, hostname=HOST)
    row = evidence.traffic(AT, PC, 51001, transip=WAN, srcintf="vlan42", dstintf="wan1",
                           srcmac=OTHER)  # fmt: skip
    build(db_session, DAY, DAY)
    set_setting(db_session, "lookup.srcmac_interfaces", ["vlan42"], updated_by="tolga")
    db_session.commit()
    (match,) = lookup(db_session, public_ip="198.51.100.20").matches
    assert match.traffic.srcmac == OTHER
    chain = match.chain
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.mac, chain.lease.source_table) == (PC_MAC, "fg_dhcp")
    assert (chain.mac_source, chain.confidence) == ("lease", "low")
    assert note(chain, "srcmac_mismatch") == {"srcmac": OTHER, "mac": PC_MAC}
    (alternative,) = chain.alternatives
    assert alternative.device is not None
    assert (alternative.lease, alternative.device.mac) == (None, OTHER)
    assert (alternative.mac_source, alternative.confidence) == ("srcmac", "low")
    assert [ref.row_id for ref in alternative.device.evidence] == [row]
    assert {"srcmac_mismatch", "srcmac_only"} <= set(chain_keys(alternative))


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


def test_a_refused_row_of_the_pair_is_not_its_record(
    db_session: Session, evidence: Evidence
) -> None:
    """Rows of the pair after the ACK that are no lease evidence (2.7: the per-moment query reads
    the rows ``trust.kept`` accepts): an ACK from an unconfigured sender, a line stamped with its
    receive time and an unmapped logid. None of them is the lease's record, so the moment still
    lies past half the lease after the 08:00 ACK."""
    sender = "unknown-192.0.2.7"
    db_session.add(Source(name=sender, type="fortigate", address="192.0.2.7", unknown=True))
    evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=HOST)
    evidence.fortigate_dhcp(T0 + timedelta(hours=12), PC, PC_MAC, source=sender)
    evidence.fortigate_dhcp(T0 + timedelta(hours=13), PC, PC_MAC, device_time=False)
    evidence.fortigate_dhcp(T0 + timedelta(hours=14), PC, PC_MAC, logid="0100026003")
    evidence.traffic(T0 + timedelta(hours=15), PC, 50001)
    build(db_session, DAY, DAY + timedelta(days=1))
    assert db_session.scalars(select(Lease)).one().last_evidence == T0
    chain = internal(db_session, PC, T0 + timedelta(hours=15)).attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert chain.lease.last_dhcp_evidence == T0
    assert note(chain, "lease_renewal_not_seen") == {"last": iso(T0), "lease": 86400}


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
        assert (chain.confidence, chain.lease.confidence) == ("high", "high"), hours
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
    assert (chain.confidence, chain.lease.confidence) == ("high", "high")
    assert chain.lease.last_dhcp_evidence == record
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


def test_a_decline_built_before_its_network_was_entered(
    db_session: Session, evidence: Evidence
) -> None:
    """Round 7: the leases of 10-12 were built while no network held 172.20.42.x, so D33 closed
    nothing; 'Ofis' (no VLAN id) is entered on 10-21, when those leases are final. The .57 chain
    is ``low`` with the post-lease warning (the converted 08:41:14 ACK of .58), until the leases
    are rebuilt from 10-12."""
    db_session.execute(delete(Vlan).where(Vlan.subnet == PLANLAMA))
    _decline(evidence, interface="internal")
    build(db_session, DAY, DAY)
    db_session.add(Vlan(subnet=PLANLAMA, vlan_id=None, name_tr="Ofis"))
    db_session.commit()
    outcome = internal(db_session, PC, PRINTER_AT)
    chain = outcome.attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.mac, chain.vlan) == (DECLINER, None)
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert note(chain, UNCONVERTED) == {"last": iso(tr(DAY, 8, 41, 10)), "ip": PC}
    assert "fortigate_dhcp_no_network" not in chain_keys(chain)
    # Nothing is cited: the lease keeps its own ACK only, and .58 appears nowhere.
    dhcp = [ref.time for ref in chain.lease.evidence if ref.table == "fg_dhcp"]
    assert dhcp == [tr(DAY, 8, 41, 10)]
    assert "172.20.42.58" not in outcome_text(outcome)
    assert outcome.unconverted_dhcp == ()
    build(db_session, DAY, DAY)  # the rebuild the text asks for
    rebuilt = internal(db_session, PC, PRINTER_AT)
    assert rebuilt.attribution is None
    assert "no_lease" in keys(rebuilt)


@pytest.mark.parametrize(
    ("interface", "vd", "lowered"),
    [("vlan42", "root", True), ("vlan43", "root", False), ("vlan42", "kurumsal", False)],
    ids=["same", "interface", "vdom"],
)
def test_a_missed_close_needs_the_lease_interface_and_vdom(
    db_session: Session, evidence: Evidence, interface: str, vd: str, lowered: bool
) -> None:
    """D33's key for an ``fg_dhcp`` lease (2.7, round 7): :31 moves from .57 to an address in no
    configured network, so the build closed nothing; only an ACK of the lease's own interface
    and VDOM would have closed it, so only that one is a missed close."""
    elsewhere = "172.20.44.58"
    evidence.fortigate_dhcp(tr(DAY, 8, 41, 10), PC, DECLINER)
    evidence.fortigate_dhcp(tr(DAY, 8, 41, 14), elsewhere, DECLINER, interface=interface, vd=vd)
    build(db_session, DAY, DAY)
    chain = internal(db_session, PC, PRINTER_AT).attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.mac == DECLINER
    assert OVERLAP not in chain_keys(chain)
    if lowered:
        assert (chain.confidence, chain.lease.confidence) == ("low", "low")
        assert note(chain, UNCONVERTED) == {"last": iso(tr(DAY, 8, 41, 10)), "ip": PC}
    else:
        assert (chain.confidence, chain.lease.confidence) == ("high", "high")
        assert UNCONVERTED not in chain_keys(chain)


def test_each_chain_is_checked_against_its_own_window(
    db_session: Session, evidence: Evidence
) -> None:
    """One post-lease query serves both chains of a candidate lookup, and each chain is tested
    against its own window (round 6): X's last ACK is at T - 10 h, Y's at T - 2 h. A line naming
    Y's address at T - 5 h lies inside the union of the windows but before Y's last ACK, so Y
    stays as it was; the line naming X's address at the same time lowers X only."""
    x, y = "172.20.42.81", "172.20.42.82"
    x_mac, y_mac = "00:00:5e:00:53:81", "00:00:5e:00:53:82"
    evidence.fortigate_dhcp(AT - timedelta(hours=10), x, x_mac)
    evidence.fortigate_dhcp(AT - timedelta(hours=20), y, y_mac)
    evidence.fortigate_dhcp(AT - timedelta(hours=2), y, y_mac)
    unconverted_line(evidence, AT - timedelta(hours=5), y, OTHER)
    evidence.traffic(AT, x, 51001, transip=WAN)
    evidence.traffic(AT, y, 51002, transip=WAN)
    build(db_session, DAY - timedelta(days=1), DAY)
    first = lookup(db_session, public_ip="198.51.100.20")
    chains = {m.traffic.srcip: m.chain for m in first.matches}
    for ip in (x, y):
        chain = chains[ip]
        assert chain is not None
        assert chain.lease is not None
        assert (chain.confidence, chain.lease.confidence) == ("high", "high")
        assert UNCONVERTED not in chain_keys(chain)
    unconverted_line(evidence, AT - timedelta(hours=5), x, OTHER)
    db_session.commit()
    outcome = lookup(db_session, public_ip="198.51.100.20")
    chains = {m.traffic.srcip: m.chain for m in outcome.matches}
    lowered, kept_chain = chains[x], chains[y]
    assert lowered is not None
    assert lowered.lease is not None
    assert (lowered.confidence, lowered.lease.confidence) == ("low", "low")
    assert note(lowered, UNCONVERTED) == {"last": iso(AT - timedelta(hours=10)), "ip": x}
    assert kept_chain is not None
    assert kept_chain.lease is not None
    assert (kept_chain.confidence, kept_chain.lease.confidence) == ("high", "high")
    assert UNCONVERTED not in chain_keys(kept_chain)


def test_the_moment_of_the_missed_close_itself(db_session: Session, evidence: Evidence) -> None:
    """The window of converted rows is open at the moment (2.7): a row exactly at ``at`` that
    the timeline applied ends the lease at ``at``; an ``fg_event`` line at ``at`` counts."""
    db_session.execute(delete(Vlan).where(Vlan.subnet == PLANLAMA))
    _decline(evidence)
    build(db_session, DAY, DAY)
    db_session.add(Vlan(subnet=PLANLAMA, vlan_id=None, name_tr="Ofis"))
    db_session.commit()
    moved = tr(DAY, 8, 41, 14)
    chain = internal(db_session, PC, moved).attribution
    assert chain is not None
    assert UNCONVERTED not in chain_keys(chain)
    after = internal(db_session, PC, moved + timedelta(seconds=1)).attribution
    assert after is not None
    assert UNCONVERTED in chain_keys(after)
    unconverted_line(evidence, moved, "172.20.42.59", DECLINER)
    db_session.commit()
    at_line = internal(db_session, PC, moved).attribution
    assert at_line is not None
    assert note(at_line, UNCONVERTED) == {"last": iso(tr(DAY, 8, 41, 10)), "ip": PC}


@pytest.mark.parametrize(
    ("converted", "days", "lowered", "refs"),
    [
        (False, 8, False, True),
        (False, 6, True, True),
        (False, 6, True, False),
        (True, 101, False, True),
        (True, 99, True, True),
        (True, 99, True, False),
        (True, 8, True, True),
    ],
    ids=[
        "line-beyond-the-window",
        "line-inside",
        "line-inside-without-a-record",
        "converted-beyond-the-window",
        "converted-inside",
        "converted-inside-without-a-record",
        "converted-beyond-the-line-window",
    ],
)
def test_a_mikrotik_lease_kept_open_reads_back_one_probe_window(
    db_session: Session,
    evidence: Evidence,
    converted: bool,
    days: int,
    lowered: bool,
    refs: bool,
) -> None:
    """Round 7: a MikroTik lease that traffic keeps open has its last DHCP record far back; the
    post-lease check reads no further back than the D8 probe window before the moment: for an
    ``fg_event`` line the 3.8 fallback, 604,800 s (7 days; deviation c2), for a converted row
    the 8,640,000 s (100 days) of 2.7; from the lease start when the lease has no DHCP record
    (printed as ``{last}``)."""
    moment = AT
    ack = moment - timedelta(days=120)
    evidence.dhcp(ack, "assigned", PC, PC_MAC, server="dhcp42")
    build(db_session, ack.date(), ack.date())
    lease = db_session.scalars(select(Lease)).one()
    start = (ack if refs else ack - timedelta(days=1)).astimezone(UTC)  # as the rows hold it
    lease.start, lease.end, lease.end_reason = start, None, "open"
    lease.last_evidence = moment - timedelta(hours=1)  # the traffic of yesterday and today
    if not refs:
        lease.evidence = []
    if converted:
        evidence.fortigate_dhcp(moment - timedelta(days=days), "172.20.42.120", PC_MAC)
    else:
        unconverted_line(evidence, moment - timedelta(days=days), "172.20.42.121", PC_MAC)
    db_session.commit()
    chain = internal(db_session, PC, moment).attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.source_table == "mt_dhcp"
    if lowered:
        assert (chain.confidence, chain.lease.confidence) == ("low", "low")
        assert note(chain, UNCONVERTED) == {"last": iso(start), "ip": PC}
    else:
        assert chain.lease.confidence == "high"
        assert UNCONVERTED not in chain_keys(chain)


def test_a_decline_into_a_scope_wider_than_its_network(
    db_session: Session, evidence: Evidence
) -> None:
    """Round 7: 172.20.42.0/24 is entered while the FortiGate serves 172.20.42.0/23 on
    ``internal``; :31 declines .42.57 and takes .43.10, which lies in no configured network, so
    the build closes nothing."""
    db_session.execute(delete(Vlan).where(Vlan.subnet == "172.20.43.0/24"))
    other = "172.20.43.10"
    evidence.fortigate_dhcp(tr(DAY, 8, 41, 10), PC, DECLINER, interface="internal")
    evidence.fortigate_dhcp(tr(DAY, 8, 41, 14), other, DECLINER, interface="internal")
    evidence.traffic(PRINTER_AT, PC, 50001)
    build(db_session, DAY, DAY)
    assert db_session.scalars(select(Lease).where(Lease.ip == PC)).one().end_reason == "timeout"
    declined = internal(db_session, PC, PRINTER_AT).attribution
    assert declined is not None
    assert declined.lease is not None
    assert declined.lease.mac == DECLINER
    assert (declined.confidence, declined.lease.confidence) == ("low", "low")
    assert note(declined, UNCONVERTED) == {"last": iso(tr(DAY, 8, 41, 10)), "ip": PC}
    assert "fortigate_dhcp_no_network" not in chain_keys(declined)
    taken = internal(db_session, other, PRINTER_AT).attribution
    assert taken is not None
    assert taken.lease is not None
    assert (taken.confidence, taken.lease.confidence) == ("low", "low")
    assert note(taken, "fortigate_dhcp_no_network") == {"ip": other}


def test_a_nas_on_two_relayed_networks_keeps_both_chains(
    db_session: Session, evidence: Evidence
) -> None:
    """D33's key: one interface carries two DHCP servers (F22), and the NAS holds an address in
    each, renewed alternately. Neither ACK closes the other lease, and the converted-row check
    skips the MAC's row in the other configured network (2.7)."""
    nas, finance, admin = "00:00:5e:00:53:44", "172.20.41.44", "172.20.43.44"
    for hours in (0, 12, 24, 36):
        moment = T0 + timedelta(hours=hours)
        evidence.fortigate_dhcp(moment, finance, nas, interface="internal", hostname="NAS-01")
        evidence.fortigate_dhcp(
            moment + timedelta(minutes=5), admin, nas, interface="internal", hostname="NAS-01"
        )
    build(db_session, DAY, DAY + timedelta(days=2))
    stored = db_session.scalars(select(Lease).where(Lease.mac == nas).order_by(Lease.ip)).all()
    assert [lease.ip for lease in stored] == [finance, admin]
    assert all(lease.end_reason != "superseded" for lease in stored)
    # Between the two renewals of a round, too (one address renewed, the other not yet).
    for minutes in (180, 723, 840, 1380, 1443, 1800, 2163, 2400):
        for ip in (finance, admin):
            chain = internal(db_session, ip, T0 + timedelta(minutes=minutes)).attribution
            assert chain is not None, (ip, minutes)
            assert chain.lease is not None
            assert chain.lease.mac == nas
            assert (chain.confidence, chain.lease.confidence) == ("high", "high"), (ip, minutes)
            assert UNCONVERTED not in chain_keys(chain)


# -- migrations from MikroTik DHCP (D5, D33, D8) --------------------------------------------------

MOVE_DAY = date(2026, 10, 1)
MOVED = tr(MOVE_DAY, 9, 20)
#: The address the FortiGate gives Selin's PC (its pool is .100-.199; MikroTik's was .50-.199).
NEW_PC = "172.20.42.120"


def _mikrotik_lease(evidence: Evidence) -> None:
    """Selin's PC holds .57 through MikroTik DHCP from 08:00 on 10-01."""
    evidence.dhcp(tr(MOVE_DAY, 8), "assigned", PC, PC_MAC, server="dhcp42", hostname=HOST)


def _printer(evidence: Evidence, days: int) -> None:
    """A printer set statically to .57 sends traffic every two hours (no ARP-LOG on VLAN 42)."""
    for hour in range(10, 24 * days, 2):
        evidence.traffic(tr(MOVE_DAY, 0) + timedelta(hours=hour), PC, 40000 + hour)


def test_a_migration_that_keeps_the_address(db_session: Session, evidence: Evidence) -> None:
    """D5: the FortiGate's ACK of the same pair ends the MikroTik lease, and with no FortiGate
    renewal the printer's traffic keeps nobody's lease open."""
    _mikrotik_lease(evidence)
    evidence.fortigate_dhcp(MOVED, PC, PC_MAC, hostname=HOST)
    _printer(evidence, 3)
    build(db_session, MOVE_DAY, MOVE_DAY + timedelta(days=3))
    selin(db_session)
    outcome = internal(db_session, PC, MOVED + timedelta(days=2, minutes=10))
    assert outcome.attribution is None
    assert "no_lease" in keys(outcome)


#: Lookups of .57 after the move: within D27's window of the MikroTik ACK, then past it.
AFTER_MOVE = (
    MOVED + timedelta(minutes=10),
    tr(MOVE_DAY, 15),
    tr(MOVE_DAY + timedelta(days=1), 12),
    tr(MOVE_DAY + timedelta(days=3), 12),
    tr(MOVE_DAY + timedelta(days=6), 12),
)
#: D27's window around them: the default lease times (86400 s x 110 %).
WINDOW = timedelta(seconds=95040)


def _moved(evidence: Evidence) -> None:
    """The FortiGate ACKs Selin's PC to .120 at 09:20 and renews it every 12 hours for a week,
    while the printer on .57 sends traffic."""
    _mikrotik_lease(evidence)
    for step in range(14):
        evidence.fortigate_dhcp(MOVED + timedelta(hours=12 * step), NEW_PC, PC_MAC, hostname=HOST)
    _printer(evidence, 7)


def test_a_migration_that_moves_the_address(db_session: Session, evidence: Evidence) -> None:
    """D33's MikroTik variant: the FortiGate ACK for .120 ends the MikroTik lease on .57, so the
    printer's traffic never names Selin Kaya, also once the D27 window has passed."""
    _moved(evidence)
    build(db_session, MOVE_DAY, MOVE_DAY + timedelta(days=7))
    selin(db_session)
    for moment in AFTER_MOVE:
        outcome = internal(db_session, PC, moment)
        assert outcome.attribution is None, moment
        assert "no_lease" in keys(outcome)
    chain = internal(db_session, NEW_PC, tr(MOVE_DAY, 12)).attribution
    assert chain is not None
    assert chain.person is not None
    assert chain.lease is not None
    assert (chain.person.name, chain.lease.source_table) == ("Selin Kaya", "fg_dhcp")


@pytest.mark.parametrize("entered", [True, False], ids=["entered-later", "never-entered"])
def test_a_migration_built_before_the_network_was_entered(
    db_session: Session, evidence: Evidence, entered: bool
) -> None:
    """Round 7: the leases were built while no network held 172.20.42.x, so D33's MikroTik
    variant closed nothing and the printer's traffic keeps the MikroTik lease on .57 open. Every
    .57 lookup after the move names Selin Kaya at ``low`` only: with the post-lease warning (the
    converted 09:20 ACK of her MAC for .120), or with ``dhcp_sources_overlap`` while D27's window
    still holds both origins of the entered network."""
    planning = db_session.scalars(select(Vlan).where(Vlan.subnet == PLANLAMA)).one()
    values = {
        "subnet": PLANLAMA,
        "vlan_id": 42,
        "name_tr": planning.name_tr,
        "dhcp_server": planning.dhcp_server,
        "department_id": planning.department_id,
    }
    db_session.execute(delete(Vlan).where(Vlan.subnet == PLANLAMA))
    _moved(evidence)
    build(db_session, MOVE_DAY, MOVE_DAY + timedelta(days=7))
    if entered:
        db_session.add(Vlan(**values))
    selin(db_session)
    for moment in AFTER_MOVE:
        outcome = internal(db_session, PC, moment)
        chain = outcome.attribution
        assert chain is not None, moment
        assert chain.lease is not None
        assert chain.person is not None
        assert (chain.lease.source_table, chain.person.name) == ("mt_dhcp", "Selin Kaya")
        assert chain.confidence == "low", moment
        both = entered and moment - WINDOW <= tr(MOVE_DAY, 8)
        if both:
            assert note(chain, OVERLAP) == {
                "network": "VLAN 42",
                "at": iso(moment),
                "sources": "FortiGate DHCP (VDOM root), MikroTik DHCP",
            }
        else:
            assert OVERLAP not in chain_keys(chain), moment
            assert chain.lease.confidence == "low"
            assert note(chain, UNCONVERTED) == {"last": iso(tr(MOVE_DAY, 8)), "ip": PC}
        assert NEW_PC not in outcome_text(outcome)


def test_a_mikrotik_lease_reopened_after_a_rollback(
    db_session: Session, evidence: Evidence
) -> None:
    """Round 7 (2.2.1): after a same-day rollback the previous version re-opened the MikroTik
    lease that D33's MikroTik variant had closed at the converted 09:20 ACK. The converted ACK
    of the MAC for another address of the same network lowers the re-opened chain."""
    _moved(evidence)
    build(db_session, MOVE_DAY, MOVE_DAY + timedelta(days=1))
    selin(db_session)
    reopened = tr(MOVE_DAY + timedelta(days=1), 22)
    # What the previous version stores: the lease open again, kept alive by the printer's
    # traffic, with the refs of its own MikroTik and traffic rows only.
    lease = db_session.scalars(
        select(Lease).where(Lease.ip == PC, Lease.source_table == "mt_dhcp")
    ).one()
    assert (lease.end, lease.end_reason) == (MOVED, "superseded")
    lease.end, lease.end_reason, lease.last_evidence = None, "open", reopened
    lease.evidence = [ref for ref in lease.evidence if ref["table"] != "fg_dhcp"]
    db_session.commit()
    chain = internal(db_session, PC, reopened + timedelta(hours=1)).attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.source_table, chain.lease.mac) == ("mt_dhcp", PC_MAC)
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert note(chain, UNCONVERTED) == {"last": iso(tr(MOVE_DAY, 8)), "ip": PC}


#: The variants of the move loaded before the upgrade: who the ``fg_event`` ACK names.
BEFORE_UPGRADE = {
    "her-mac": (NEW_PC, PC_MAC, "fortigate", True),
    "client-b": (PC, OTHER, "fortigate", True),
    "another-network": ("172.20.43.120", PC_MAC, "fortigate", False),
    "unknown-sender": (NEW_PC, PC_MAC, STRANGER, False),
}


@pytest.mark.parametrize("variant", sorted(BEFORE_UPGRADE))
def test_a_migration_loaded_before_the_upgrade(
    db_session: Session, evidence: Evidence, variant: str
) -> None:
    """D8, round 6: the 10-01 09:20 ACK that moved Selin's PC to .120 is an ``fg_event`` row (a
    line the previous version loaded), the printer's traffic keeps the MikroTik lease on .57
    open, and her MAC's first converted ACK (a renewal of .120) comes at 10-05 21:20. The site
    keeps the default lease settings, so D27's window at 10-04 14:00 (86400 s x 110 % each way)
    holds no DHCP row of VLAN 42 and the post-lease check decides (round 7)."""
    address, mac, source, lowered = BEFORE_UPGRADE[variant]
    if source == STRANGER:
        stranger(db_session)
    _mikrotik_lease(evidence)
    unconverted_line(evidence, MOVED, address, mac, hostname="IDR-NB07", source=source)
    renewal = address if mac == PC_MAC else NEW_PC
    evidence.fortigate_dhcp(tr(MOVE_DAY + timedelta(days=4), 21, 20), renewal, PC_MAC)
    _printer(evidence, 6)
    build(db_session, MOVE_DAY, MOVE_DAY + timedelta(days=5))
    selin(db_session)
    moment = tr(MOVE_DAY + timedelta(days=3), 14)
    outcome = internal(db_session, PC, moment)
    chain = outcome.attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.person is not None
    assert (chain.lease.source_table, chain.person.name) == ("mt_dhcp", "Selin Kaya")
    assert OVERLAP not in chain_keys(chain)
    if lowered:
        assert (chain.confidence, chain.lease.confidence) == ("low", "low")
        assert note(chain, UNCONVERTED) == {"last": iso(tr(MOVE_DAY, 8)), "ip": PC}
    else:
        assert (chain.confidence, chain.lease.confidence) == ("medium", "high")
        assert UNCONVERTED not in chain_keys(chain)
    # Cites nothing: no fg_event row among the lease's evidence or listed as unconverted, and
    # no value of the line (another client's MAC, its host name) shows.
    assert "fg_event" not in {ref.table for ref in chain.lease.evidence}
    assert outcome.unconverted_dhcp == ()
    text = outcome_text(outcome)
    assert "idr-nb07" not in text
    assert OTHER not in text


#: An ``fg_event`` line that D25 or D28 says is never lease evidence: a NAK under the ACK's
#: logid (``dhcp.fortigate_dhcp_msgs``), an ACK of an excluded interface (an IPsec server) and
#: an ACK for a multicast MAC; ``None``: the test's own client.
REFUSED_LINES = {
    "nak": ("Nak", "vlan42", None),
    "excluded": ("Ack", "ipsec1", None),
    "bad-mac": ("Ack", "vlan42", "01:00:5e:00:00:fb"),
}


def exclude_ipsec(session: Session) -> None:
    set_setting(session, "dhcp.fortigate_interfaces_excluded", ["ipsec1"], updated_by="tolga")
    session.commit()


@pytest.mark.parametrize("variant", sorted(REFUSED_LINES))
def test_a_line_after_the_lease_that_is_never_lease_evidence_lowers_nothing(
    db_session: Session, evidence: Evidence, example: list[int], variant: str
) -> None:
    """The post-lease check (2.7) applies D25 and D28's interface and MAC rules to its
    ``fg_event`` lines too: a NAK for the PC's address under 0100026001, an ACK of an excluded
    interface or an ACK for a multicast MAC after the 11:00 ACK leaves the chain as it was; the
    same line as an ACK of another client on vlan42 lowers it."""
    message, interface, mac = REFUSED_LINES[variant]
    exclude_ipsec(db_session)
    unconverted_line(evidence, tr(DAY, 11, 10), PC, mac or OTHER, interface=interface,
                     dhcp_msg=message)  # fmt: skip
    db_session.commit()
    chain = internal(db_session, PC, AT).attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.confidence, chain.lease.confidence) == ("medium", "high")
    assert UNCONVERTED not in chain_keys(chain)
    unconverted_line(evidence, tr(DAY, 11, 10), PC, OTHER)
    db_session.commit()
    lowered = internal(db_session, PC, AT).attribution
    assert lowered is not None
    assert lowered.lease is not None
    assert (lowered.confidence, lowered.lease.confidence) == ("low", "low")
    assert note(lowered, UNCONVERTED) == {"last": iso(tr(DAY, 11)), "ip": PC}


def test_a_line_after_the_lease_without_dhcp_msg_counts(
    db_session: Session, evidence: Evidence, example: list[int]
) -> None:
    """D25 refuses a listed logid's line only for a ``dhcp_msg`` it does not list: an ACK line
    of the PC's address without the field (D3 kept it as an ``fg_event`` row, the shape a line
    the timeline never saw has) after the 11:00 ACK lowers the chain, as the full line does."""
    unconverted_line(evidence, tr(DAY, 11, 10), PC, OTHER, without_dhcp_msg=True)
    db_session.commit()
    chain = internal(db_session, PC, AT).attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert note(chain, UNCONVERTED) == {"last": iso(tr(DAY, 11)), "ip": PC}
    assert OTHER not in outcome_text(internal(db_session, PC, AT))


@pytest.mark.parametrize(
    ("ip", "lowered"),
    [
        ("fe80::1%eth0", True),
        ("2001:db8::1", True),
        ("172.20.43.121/24", True),
        ("172.20.43.121", False),
    ],
    ids=["zone-id", "ipv6", "with-a-prefix", "another-configured-network"],
)
def test_an_unreadable_ip_of_the_mac_after_the_lease_counts(
    db_session: Session, evidence: Evidence, example: list[int], ip: str, lowered: bool
) -> None:
    """2.7: a line of the chain's MAC after the 11:00 ACK counts unless its ``ip`` lies in
    another configured network (D33's key); an ``ip`` that is no plain IPv4 address is
    unreadable and counts, and it never reaches PostgreSQL's ``inet`` (a zone id would end the
    lookup's transaction)."""
    unconverted_line(evidence, tr(DAY, 11, 10), ip, PC_MAC)
    db_session.commit()
    chain = internal(db_session, PC, AT).attribution
    assert chain is not None
    assert chain.lease is not None
    if lowered:
        assert (chain.confidence, chain.lease.confidence) == ("low", "low")
        assert note(chain, UNCONVERTED) == {"last": iso(tr(DAY, 11)), "ip": PC}
    else:
        assert (chain.confidence, chain.lease.confidence) == ("medium", "high")
        assert UNCONVERTED not in chain_keys(chain)
    assert ip not in outcome_text(internal(db_session, PC, AT))


# -- a rollback window (D8, 2.2.1) ----------------------------------------------------------------


@pytest.mark.parametrize("source", ["fortigate", STRANGER])
def test_a_line_loaded_in_a_rollback_window(
    db_session: Session, evidence: Evidence, source: str
) -> None:
    """:31's converted ACK at t0 (lease 604800), then an ACK of .57 to :44 that the previous
    version loaded as an ``fg_event`` row at t0 + 26 h: from then on :31's chain is ``low``,
    nothing is listed as unconverted, and :44 appears nowhere; a line of an unconfigured sender
    lowers nothing."""
    if source == STRANGER:
        stranger(db_session)
    laptop = "00:00:5e:00:53:44"
    evidence.fortigate_dhcp(T0, PC, DECLINER, lease=604800)
    unconverted_line(
        evidence, T0 + timedelta(hours=26), PC, laptop, hostname="PLN-NB44", source=source
    )
    build(db_session, DAY, DAY + timedelta(days=1))
    before = internal(db_session, PC, T0 + timedelta(hours=25)).attribution
    assert before is not None
    assert before.lease is not None
    assert (before.confidence, before.lease.confidence) == ("high", "high")
    outcome = internal(db_session, PC, T0 + timedelta(hours=27, minutes=30))
    chain = outcome.attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.mac == DECLINER
    if source == STRANGER:
        assert (chain.confidence, chain.lease.confidence) == ("high", "high")
        assert UNCONVERTED not in chain_keys(chain)
    else:
        assert (chain.confidence, chain.lease.confidence) == ("low", "low")
        assert note(chain, UNCONVERTED) == {"last": iso(T0), "ip": PC}
    assert outcome.unconverted_dhcp == ()
    text = outcome_text(outcome)
    assert laptop not in text
    assert "pln-nb44" not in text


# -- FortiGate DHCP rows that did not become lease evidence (D8 probe, 2.8.3) ---------------------

#: The probe's text, its citation label (2.8.3) and the static-client warning (2.11).
NOT_CONVERTED, MISSING = "fortigate_dhcp_not_converted", "chain_missing"
UNCONVERTED_LABEL = (
    "FortiGate DHCP kaydı (kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez)"
)
#: A pre-upgrade ACK of the PC four days before the moment, with the FortiOS default lease.
WEEK = 604800


def outcome_note(outcome: LookupOutcome, key: str) -> dict[str, Any]:
    return dict(next(e for e in outcome.explanations if e.key == key).params)


def wan_exit(session: Session) -> None:
    """Exit ``wan`` with the FortiGate's NAT address, entered before the evidence."""
    value = validate_egresses({"wan": {"mode": "isp_nat", "transips": [WAN]}})
    set_setting(session, "nat.egresses", value, updated_by="tolga")
    session.execute(update(Setting).where(Setting.key == "nat.egresses").values(updated_at=ENTERED))
    session.commit()


def assert_cited(outcome: LookupOutcome, row: int, at: datetime, evidence: Evidence) -> None:
    """The one ``unconverted_dhcp`` citation: that ``fg_event`` row with its raw line, labelled
    as 2.8.3 says, and never an attribution."""
    (ref,) = outcome.unconverted_dhcp
    assert (ref.table, ref.row_id, ref.time, ref.summary) == (
        "fg_event",
        row,
        at,
        UNCONVERTED_LABEL,
    )
    assert ref.raw is not None
    assert ref.raw.source == "fortigate"
    line = evidence.raw_line(ref.raw.source, ref.raw.day, ref.raw.line, ref.raw.offset or 0)
    assert b'logid="0100026001"' in line
    assert PC_MAC.upper().encode() in line


def test_a_static_client_gives_a_match_without_a_chain(
    db_session: Session, evidence: Evidence
) -> None:
    """A printer set statically to .200: its session is listed, its chain ends at the internal
    IP (``chain_missing``), and the probe finds no DHCP row to speak of."""
    evidence.traffic(AT, "172.20.42.200", 51001, transip=WAN)
    build(db_session, DAY, DAY)
    outcome = lookup(db_session, public_ip="198.51.100.20")
    assert outcome.mode == "candidates"
    (match,) = outcome.matches
    assert (match.traffic.srcip, match.chain) == ("172.20.42.200", None)
    assert outcome_note(outcome, MISSING) == {"count": 1}
    assert NOT_CONVERTED not in keys(outcome)
    assert outcome.unconverted_dhcp == ()


@pytest.mark.parametrize("grace", [60, 0])
def test_the_probe_runs_with_a_short_arp_interval(
    db_session: Session, evidence: Evidence, grace: int
) -> None:
    """The probe reads its window from the lease timeline's rules with the configured ARP grace
    (``arp.ignore_after_release_seconds``, at most ``arp.interval_seconds``), as the lease job
    builds them: with a 120 s ARP interval an internal lookup without a lease, a candidate
    lookup whose static client has no chain and a MAC-only lookup without a lease give their
    answer, never an error."""
    set_setting(db_session, "arp.interval_seconds", 120, updated_by="tolga")
    set_setting(db_session, "arp.ignore_after_release_seconds", grace, updated_by="tolga")
    evidence.traffic(AT, "172.20.42.200", 51001, transip=WAN)
    build(db_session, DAY, DAY)
    alone = internal(db_session, "172.20.42.200", AT)
    assert (alone.mode, alone.attribution) == ("none", None)
    assert "no_lease" in keys(alone)
    candidates = lookup(db_session, public_ip="198.51.100.20")
    assert candidates.mode == "candidates"
    (match,) = candidates.matches
    assert (match.traffic.srcip, match.chain) == ("172.20.42.200", None)
    assert outcome_note(candidates, MISSING) == {"count": 1}
    by_mac = lookup(db_session, mac=PC_MAC)
    assert (by_mac.mode, by_mac.attribution) == ("none", None)
    assert "no_lease_for_mac" in keys(by_mac)
    for outcome in (alone, candidates, by_mac):
        assert NOT_CONVERTED not in keys(outcome)
        assert outcome.unconverted_dhcp == ()


def test_a_covering_line_that_stayed_an_event_row_is_named_and_cited(
    db_session: Session, evidence: Evidence
) -> None:
    """A pre-upgrade ACK four days before the moment with ``lease=604800`` (the probe window
    covers FortiGate lease times): the internal lookup names the IP with the three possible
    reasons and cites the row in ``unconverted_dhcp``; an exact match does the same."""
    acked = AT - timedelta(days=4)
    row = unconverted_line(evidence, acked, PC, PC_MAC, hostname=HOST, lease=WEEK)
    evidence.traffic(AT, PC, 51001, "203.0.113.10", 443, transip=WAN, duration=10)
    build(db_session, DAY, DAY)
    outcome = internal(db_session, PC, AT)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert keys(outcome)[-2:] == ["no_lease", NOT_CONVERTED]
    assert outcome_note(outcome, NOT_CONVERTED) == {"ips": PC}
    text = next(e for e in outcome.explanations if e.key == NOT_CONVERTED)
    for reason in ("bu sürüme yükseltmeden önce", "fortigate.dhcp", "bir geri almadan sonra"):
        assert reason in text.text_tr
    for reason in ("before the upgrade", "fortigate.dhcp", "after a rollback"):
        assert reason in text.text_en
    assert_cited(outcome, row, acked, evidence)

    wan_exit(db_session)
    exact = lookup(db_session, public_ip=WAN, public_port=51001, protocol="tcp")
    assert exact.mode == "exact"
    (match,) = exact.matches
    assert match.chain is None
    assert outcome_note(exact, MISSING) == {"count": 1}
    assert outcome_note(exact, NOT_CONVERTED) == {"ips": PC}
    assert_cited(exact, row, acked, evidence)


def test_a_srcmac_chain_is_probed_as_a_chain_without_a_lease(
    db_session: Session, evidence: Evidence
) -> None:
    """One rule for every address without a lease chain (2.8.3, 2.9): with the PC's interface
    listed, the session's srcmac gives a srcmac chain in the internal lookup and for the exact
    and the candidate match alike, and the probe still names the covering line that stayed an
    event row, cited in internal mode and for the exact match, never for a candidate. The chain
    stays the srcmac chain at ``low``."""
    acked = AT - timedelta(days=4)
    row = unconverted_line(evidence, acked, PC, PC_MAC, hostname=HOST, lease=WEEK)
    evidence.traffic(AT, PC, 51001, transip=WAN, srcintf="vlan42", dstintf="wan1",
                     srcmac=PC_MAC)  # fmt: skip
    build(db_session, DAY, DAY)
    set_setting(db_session, "lookup.srcmac_interfaces", ["vlan42"], updated_by="tolga")
    db_session.commit()
    wan_exit(db_session)
    outcomes = {
        "internal": internal(db_session, PC, AT),
        "exact": exact_request(db_session),
        "candidates": lookup(db_session, public_ip=WAN),
    }
    for name, outcome in outcomes.items():
        chain = outcome.attribution if name == "internal" else outcome.matches[0].chain
        assert chain is not None
        assert (chain.lease, chain.mac_source, chain.confidence) == (None, "srcmac", "low")
        assert MISSING not in keys(outcome)
        assert outcome_note(outcome, NOT_CONVERTED) == {"ips": PC}
        if name == "candidates":
            assert outcome.unconverted_dhcp == ()
        else:
            assert_cited(outcome, row, acked, evidence)
    assert outcomes["exact"].mode == "exact"
    assert outcomes["candidates"].mode == "candidates"


@pytest.mark.parametrize(
    "days", [100, 6], ids=["the-golden-100-days-before", "inside-the-window-by-coverage"]
)
def test_an_ack_older_than_its_lease_is_not_named(
    db_session: Session, evidence: Evidence, days: int
) -> None:
    """3.4: a pre-upgrade ACK with lease=86400 100 days before the moment is not named; the
    window reaches 7 days back (the 3.8 fallback, deviation c2), and a 1-day lease logged 6
    days before the moment, inside it, would not have covered the moment either (the coverage
    rule)."""
    unconverted_line(evidence, AT - timedelta(days=days), PC, PC_MAC, hostname=HOST)
    build(db_session, DAY, DAY)
    outcome = internal(db_session, PC, AT)
    assert "no_lease" in keys(outcome)
    assert NOT_CONVERTED not in keys(outcome)
    assert outcome.unconverted_dhcp == ()


@pytest.mark.parametrize(
    ("lease", "before", "named"),
    [
        (0, timedelta(hours=20), True),
        (0, timedelta(hours=27), False),
        (9_000_000, timedelta(hours=27), False),
    ],
    ids=["zero-inside", "zero-past", "beyond-range"],
)
def test_an_unusable_logged_lease_covers_by_the_configured_time(
    db_session: Session, evidence: Evidence, lease: int, before: timedelta, named: bool
) -> None:
    """A logged lease outside 300-8,640,000 s does not decide the coverage: the configured time
    of the interface x ``lease.timeout_percent`` does (86400 s x 110 % = 26.4 h), as for a
    lease (D6)."""
    unconverted_line(evidence, AT - before, PC, PC_MAC, lease=lease)
    build(db_session, DAY, DAY)
    outcome = internal(db_session, PC, AT)
    assert (NOT_CONVERTED in keys(outcome)) is named
    assert len(outcome.unconverted_dhcp) == (1 if named else 0)


def test_a_later_line_of_another_client_is_never_read(
    db_session: Session, evidence: Evidence
) -> None:
    """Another client's ACK for the address an hour after the moment says nothing about it: it is
    never cited, and its MAC and host name appear nowhere in the outcome, with or without the
    PC's own covering line before the moment."""
    unconverted_line(evidence, AT + timedelta(hours=1), PC, OTHER, hostname="MUH-NB03", lease=WEEK)
    build(db_session, DAY, DAY)
    alone = internal(db_session, PC, AT)
    assert "no_lease" in keys(alone)
    assert NOT_CONVERTED not in keys(alone)
    assert alone.unconverted_dhcp == ()
    acked = AT - timedelta(days=4)
    row = unconverted_line(evidence, acked, PC, PC_MAC, hostname=HOST, lease=WEEK)
    db_session.commit()
    outcome = internal(db_session, PC, AT)
    assert outcome_note(outcome, NOT_CONVERTED) == {"ips": PC}
    assert_cited(outcome, row, acked, evidence)
    for text in (outcome_text(alone), outcome_text(outcome)):
        assert OTHER not in text
        assert "muh-nb03" not in text


def test_an_ack_and_its_release_before_the_moment_say_nothing(
    db_session: Session, evidence: Evidence
) -> None:
    """The newest row before the moment is the pre-upgrade RELEASE: the client left, nothing is
    said and nothing is cited."""
    unconverted_line(evidence, AT - timedelta(days=4), PC, PC_MAC, hostname=HOST, lease=WEEK)
    unconverted_line(evidence, AT - timedelta(days=1), PC, PC_MAC, lease=None, logid="0100026002")
    build(db_session, DAY, DAY)
    outcome = internal(db_session, PC, AT)
    assert "no_lease" in keys(outcome)
    assert NOT_CONVERTED not in keys(outcome)
    assert outcome.unconverted_dhcp == ()


def test_a_converted_release_after_the_old_ack_says_nothing(
    db_session: Session, evidence: Evidence
) -> None:
    """The pre-upgrade ACK of :31 (lease 604800) at U - 1 d, then a converted ACK of :77 for the
    same address at U + 2 h and its RELEASE at U + 3 h: at U + 5 h the newest row before the
    moment is the converted RELEASE, so no chain, no text, no citation. Before :77's ACK the old
    line is the newest row and is named."""
    u = tr(DAY, 8)
    row = unconverted_line(evidence, u - timedelta(days=1), PC, DECLINER, lease=WEEK)
    evidence.fortigate_dhcp(u + timedelta(hours=2), PC, OTHER)
    evidence.fortigate_dhcp(
        u + timedelta(hours=3), PC, OTHER, logid="0100026002", dhcp_msg="Release", lease=None
    )
    build(db_session, DAY - timedelta(days=1), DAY)
    outcome = internal(db_session, PC, u + timedelta(hours=5))
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert "no_lease" in keys(outcome)
    assert NOT_CONVERTED not in keys(outcome)
    assert outcome.unconverted_dhcp == ()
    earlier = internal(db_session, PC, u + timedelta(hours=1))
    assert outcome_note(earlier, NOT_CONVERTED) == {"ips": PC}
    assert [ref.row_id for ref in earlier.unconverted_dhcp] == [row]


def test_a_refused_converted_row_does_not_silence_the_probe(
    db_session: Session, evidence: Evidence
) -> None:
    """Round 6: ``fg_dhcp`` rows that are no lease evidence (``trust.kept`` refuses them: the
    receive time, an unmapped logid, an unconfigured sender) newer than the covering line are
    no converted rows the timeline saw."""
    stranger(db_session)
    acked = AT - timedelta(days=4)
    row = unconverted_line(evidence, acked, PC, PC_MAC, hostname=HOST, lease=WEEK)
    evidence.fortigate_dhcp(AT - timedelta(hours=3), PC, OTHER, device_time=False)
    evidence.fortigate_dhcp(AT - timedelta(hours=2), PC, OTHER, logid="0100026003")
    evidence.fortigate_dhcp(AT - timedelta(hours=1), PC, OTHER, source=STRANGER)
    build(db_session, DAY, DAY)
    outcome = internal(db_session, PC, AT)
    assert outcome_note(outcome, NOT_CONVERTED) == {"ips": PC}
    assert_cited(outcome, row, acked, evidence)


@pytest.mark.parametrize("variant", sorted(REFUSED_LINES))
def test_a_line_that_is_never_lease_evidence_is_never_named(
    db_session: Session, evidence: Evidence, variant: str
) -> None:
    """D25 and D28's interface and MAC rules hold for the probe's ``fg_event`` lines as
    ``trust.kept`` holds them for ``fg_dhcp`` rows: a covering NAK under 0100026001, a covering
    ACK of an excluded interface or a covering ACK for a multicast MAC is never named or cited;
    such a line newer than a covering ACK does not hide that ACK either."""
    message, interface, mac = REFUSED_LINES[variant]
    exclude_ipsec(db_session)
    unconverted_line(evidence, AT - timedelta(days=1), PC, mac or PC_MAC, hostname=HOST,
                     lease=WEEK, interface=interface, dhcp_msg=message)  # fmt: skip
    build(db_session, DAY, DAY)
    outcome = internal(db_session, PC, AT)
    assert "no_lease" in keys(outcome)
    assert NOT_CONVERTED not in keys(outcome)
    assert outcome.unconverted_dhcp == ()
    acked = AT - timedelta(days=4)
    row = unconverted_line(evidence, acked, PC, PC_MAC, hostname=HOST, lease=WEEK)
    db_session.commit()
    older = internal(db_session, PC, AT)
    assert outcome_note(older, NOT_CONVERTED) == {"ips": PC}
    assert_cited(older, row, acked, evidence)


def test_a_covering_line_without_dhcp_msg_is_named(db_session: Session, evidence: Evidence) -> None:
    """D8 and D25: the covering pre-upgrade ACK without a ``dhcp_msg`` field is the line ingest
    kept as an ``fg_event`` row (D3), and it is named and cited like the full line; only a
    value the logid's list lacks (a NAK) refuses it."""
    acked = AT - timedelta(days=4)
    row = unconverted_line(
        evidence, acked, PC, PC_MAC, hostname=HOST, lease=WEEK, without_dhcp_msg=True
    )
    build(db_session, DAY, DAY)
    outcome = internal(db_session, PC, AT)
    assert "no_lease" in keys(outcome)
    assert outcome_note(outcome, NOT_CONVERTED) == {"ips": PC}
    assert_cited(outcome, row, acked, evidence)


@pytest.mark.parametrize("mac", [None, "0000.5e00.5357"], ids=["missing", "unreadable"])
def test_a_covering_line_without_a_readable_mac_is_named(
    db_session: Session, evidence: Evidence, mac: str | None
) -> None:
    """D28's MAC rule refuses only a readable MAC that is zero, broadcast or multicast: the
    covering pre-upgrade ACK of the PC's address without a ``mac`` field, or with one in
    another notation, is named and cited by the IP probe, as a line without ``dhcp_msg`` is."""
    acked = AT - timedelta(days=4)
    row = unconverted_line(evidence, acked, PC, mac, hostname=HOST, lease=WEEK)
    build(db_session, DAY, DAY)
    outcome = internal(db_session, PC, AT)
    assert "no_lease" in keys(outcome)
    assert outcome_note(outcome, NOT_CONVERTED) == {"ips": PC}
    (ref,) = outcome.unconverted_dhcp
    assert (ref.table, ref.row_id, ref.time, ref.summary) == (
        "fg_event",
        row,
        acked,
        UNCONVERTED_LABEL,
    )


def test_a_line_of_an_unknown_sender_is_never_cited(
    db_session: Session, evidence: Evidence
) -> None:
    """D24: the same pre-upgrade ACK written by an ``unknown-<ip>`` sender is no evidence of
    anything; a forged line never reaches the outcome."""
    stranger(db_session)
    unconverted_line(evidence, AT - timedelta(days=4), PC, PC_MAC, hostname=HOST, lease=WEEK,
                     source=STRANGER)  # fmt: skip
    build(db_session, DAY, DAY)
    outcome = internal(db_session, PC, AT)
    assert "no_lease" in keys(outcome)
    assert NOT_CONVERTED not in keys(outcome)
    assert outcome.unconverted_dhcp == ()


#: Five clients without a chain whose addresses have covering pre-upgrade ACKs.
FIVE = [f"172.20.42.{61 + n}" for n in range(5)]


def test_candidates_are_named_but_their_lines_never_cited(
    db_session: Session, evidence: Evidence
) -> None:
    """A candidate lookup with 5 chainless candidates: the text names their IPs, nothing is
    cited (a double-NAT report would print other people's DHCP lines), and no MAC or host name
    of those lines shows."""
    for n, ip in enumerate(FIVE):
        mac = f"00:00:5e:00:53:{0x61 + n:02x}"
        unconverted_line(evidence, AT - timedelta(days=4), ip, mac, hostname=f"PLN-NB{n:02}",
                         lease=WEEK)  # fmt: skip
        evidence.traffic(AT + timedelta(seconds=n), ip, 51001 + n, transip=WAN)
    build(db_session, DAY, DAY)
    outcome = lookup(db_session, public_ip="198.51.100.20")
    assert outcome.mode == "candidates"
    assert sorted(m.traffic.srcip for m in outcome.matches) == FIVE
    assert all(m.chain is None for m in outcome.matches)
    assert outcome_note(outcome, MISSING) == {"count": 5}
    assert outcome_note(outcome, NOT_CONVERTED) == {"ips": ", ".join(FIVE)}
    assert outcome.unconverted_dhcp == ()
    text = outcome_text(outcome)
    assert "00:00:5e:00:53:6" not in text
    assert "pln-nb" not in text


def test_each_candidate_is_probed_at_its_own_moment(
    db_session: Session, evidence: Evidence
) -> None:
    """A candidate's chain and its probe use the moment of its own session: another client's
    ACK for .71 logged 10 s after .71's session ended (and before the requested moment) is no
    row before that moment, while .72's covering line is named."""
    early, late = "172.20.42.71", "172.20.42.72"
    evidence.traffic(AT - timedelta(seconds=30), early, 51001, transip=WAN, duration=10)
    unconverted_line(evidence, AT - timedelta(seconds=20), early, OTHER, lease=WEEK)
    evidence.traffic(AT, late, 51002, transip=WAN)
    unconverted_line(evidence, AT - timedelta(days=4), late, PC_MAC, lease=WEEK)
    build(db_session, DAY, DAY)
    outcome = lookup(db_session, public_ip="198.51.100.20")
    assert sorted(m.traffic.srcip for m in outcome.matches) == [early, late]
    assert outcome_note(outcome, NOT_CONVERTED) == {"ips": late}
    assert OTHER not in outcome_text(outcome)


def test_a_session_after_the_moment_is_probed_no_later_than_the_moment(
    db_session: Session, evidence: Evidence
) -> None:
    """2.8.3 "No row after T is looked for": the PC's exact session starts 30 s after the
    requested moment, and another client's ACK for .57 was logged 15 s after the moment. That
    line is never read (never named, never cited); the PC's own covering line before the
    moment is the one cited."""
    evidence.traffic(AT + timedelta(seconds=40), PC, 51001, transip=WAN, duration=10)
    unconverted_line(evidence, AT + timedelta(seconds=15), PC, OTHER, hostname="MUH-NB03",
                     lease=WEEK)  # fmt: skip
    build(db_session, DAY, DAY)
    wan_exit(db_session)
    alone = lookup(db_session, public_ip=WAN, public_port=51001, protocol="tcp")
    assert alone.mode == "exact"
    (match,) = alone.matches
    assert match.chain is None
    assert match.traffic.start == AT + timedelta(seconds=30)
    assert NOT_CONVERTED not in keys(alone)
    assert alone.unconverted_dhcp == ()
    acked = AT - timedelta(days=4)
    row = unconverted_line(evidence, acked, PC, PC_MAC, hostname=HOST, lease=WEEK)
    db_session.commit()
    exact = lookup(db_session, public_ip=WAN, public_port=51001, protocol="tcp")
    assert outcome_note(exact, NOT_CONVERTED) == {"ips": PC}
    assert_cited(exact, row, acked, evidence)
    for text in (outcome_text(alone), outcome_text(exact)):
        assert OTHER not in text
        assert "muh-nb03" not in text


def test_the_probe_reads_twenty_addresses_at_most(db_session: Session, evidence: Evidence) -> None:
    """One probe per lookup for at most 20 IPs: those of the first 20 chainless matches."""
    clients = [f"172.20.42.{100 + n}" for n in range(21)]
    for n, ip in enumerate(clients):
        unconverted_line(evidence, AT - timedelta(hours=12), ip, f"00:00:5e:00:53:{n:02x}")
        evidence.traffic(AT, ip, 51001 + n, transip=WAN)
    build(db_session, DAY, DAY)
    outcome = lookup(db_session, public_ip="198.51.100.20")
    assert len(outcome.matches) == 21
    assert outcome_note(outcome, MISSING) == {"count": 21}
    first = sorted((m.traffic.srcip for m in outcome.matches[:20]), key=ipaddress.ip_address)
    assert outcome_note(outcome, NOT_CONVERTED) == {"ips": ", ".join(first)}


def test_a_mac_only_lookup_finds_the_line_by_its_mac(
    db_session: Session, evidence: Evidence
) -> None:
    """Round 6: a MAC-only lookup whose ACK exists only as an ``fg_event`` line (the MAC in the
    FortiGate's dashed form) names the address of that line and cites it, instead of a bare
    ``no_lease_for_mac``."""
    acked = AT - timedelta(days=4)
    row = unconverted_line(evidence, acked, PC, "00-00-5e-00-53-57", hostname=HOST, lease=WEEK)
    build(db_session, DAY, DAY)
    outcome = lookup(db_session, mac=PC_MAC)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert keys(outcome)[-2:] == ["no_lease_for_mac", NOT_CONVERTED]
    assert outcome_note(outcome, NOT_CONVERTED) == {"ips": PC}
    (ref,) = outcome.unconverted_dhcp
    assert (ref.row_id, ref.time, ref.summary) == (row, acked, UNCONVERTED_LABEL)
    other = lookup(db_session, mac="00:00:5e:00:53:58")
    assert NOT_CONVERTED not in keys(other)
    assert other.unconverted_dhcp == ()


def test_a_newer_converted_row_of_the_mac_silences_the_mac_only_probe(
    db_session: Session, evidence: Evidence
) -> None:
    """The MAC-only probe keeps the newest-row rule: a kept ``fg_dhcp`` ACK of the MAC (for
    .58, its 1-day lease over two days before the moment) newer than its covering ``fg_event``
    line means the timeline saw that event: no text, no citation."""
    unconverted_line(evidence, AT - timedelta(days=4), PC, PC_MAC, hostname=HOST, lease=WEEK)
    evidence.fortigate_dhcp(AT - timedelta(days=2), "172.20.42.58", PC_MAC, hostname=HOST)
    build(db_session, DAY - timedelta(days=2), DAY)
    outcome = lookup(db_session, mac=PC_MAC)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert "no_lease_for_mac" in keys(outcome)
    assert NOT_CONVERTED not in keys(outcome)
    assert outcome.unconverted_dhcp == ()


@pytest.mark.parametrize("converted", [True, False], ids=["converted", "unconverted"])
def test_a_mac_only_probe_reads_the_address_of_its_line(
    db_session: Session, evidence: Evidence, converted: bool
) -> None:
    """The MAC-only probe keeps the address rule for the line it finds: the PC's pre-upgrade
    ACK of .57 two days before the moment, then an ACK of .57 to :77 twelve hours before it
    (a converted ``fg_dhcp`` row, or a line that stayed an ``fg_event`` row too). The IP
    lookup gives :77's answer; the MAC-only lookup of the PC names and cites nothing, because
    the newest row of .57 is :77's, and nothing of :77 shows."""
    unconverted_line(evidence, AT - timedelta(days=2), PC, PC_MAC, hostname=HOST, lease=WEEK)
    if converted:
        evidence.fortigate_dhcp(AT - timedelta(hours=12), PC, OTHER, hostname="MUH-NB03")
    else:
        newer = unconverted_line(
            evidence, AT - timedelta(hours=12), PC, OTHER, hostname="MUH-NB03", lease=WEEK
        )
    build(db_session, DAY - timedelta(days=2), DAY)
    by_ip = internal(db_session, PC, AT)
    if converted:
        chain = by_ip.attribution
        assert chain is not None
        assert chain.lease is not None
        assert chain.lease.mac == OTHER
    else:
        assert outcome_note(by_ip, NOT_CONVERTED) == {"ips": PC}
        assert [ref.row_id for ref in by_ip.unconverted_dhcp] == [newer]
    outcome = lookup(db_session, mac=PC_MAC)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert "no_lease_for_mac" in keys(outcome)
    assert NOT_CONVERTED not in keys(outcome)
    assert outcome.unconverted_dhcp == ()
    text = outcome_text(outcome)
    assert OTHER not in text
    assert "muh-nb03" not in text


def test_a_mac_only_probe_line_without_a_plain_address_still_counts(
    db_session: Session, evidence: Evidence
) -> None:
    """The address rule reads only a plain IPv4 address: the MAC's covering line whose ``ip``
    carries an IPv6 zone id has no address to read and is named and cited as found, and the
    zone id never reaches PostgreSQL's ``inet`` (it would end the lookup's transaction)."""
    acked = AT - timedelta(days=2)
    row = unconverted_line(evidence, acked, "fe80::1%eth0", PC_MAC, lease=WEEK)
    build(db_session, DAY, DAY)
    outcome = lookup(db_session, mac=PC_MAC)
    assert NOT_CONVERTED in keys(outcome)
    assert [ref.row_id for ref in outcome.unconverted_dhcp] == [row]


# -- two DHCP origins (D27) -----------------------------------------------------------------------

SHARED = "172.20.42.142"
#: The MikroTik client C before, the FortiGate client A after, and a MikroTik client of VLAN 42.
C_MAC, A_MAC, D_MAC = OTHER, DECLINER, "00:00:5e:00:53:99"


def _two_origins(evidence: Evidence) -> None:
    """C's MikroTik lease of .142 at 08:00; A's FortiGate ACK of .142 an hour later, renewed every
    12 hours for four days; one more MikroTik ACK in VLAN 42 the next morning (another client)."""
    evidence.dhcp(T0, "assigned", SHARED, C_MAC, server="dhcp42", hostname="IDR-NB12")
    for step in range(8):
        evidence.fortigate_dhcp(T0 + timedelta(hours=1 + 12 * step), SHARED, A_MAC)
    evidence.dhcp(
        tr(DAY + timedelta(days=1), 10), "assigned", "172.20.42.99", D_MAC, server="dhcp42"
    )


def test_two_dhcp_origins_lower_the_chain_while_both_are_near(
    db_session: Session, evidence: Evidence
) -> None:
    _two_origins(evidence)
    build(db_session, DAY, DAY + timedelta(days=4))
    sources = "FortiGate DHCP (VDOM root), MikroTik DHCP"
    for moment in (T0 + timedelta(hours=1, minutes=30), tr(DAY + timedelta(days=1), 12)):
        outcome = internal(db_session, SHARED, moment)
        chain = outcome.attribution
        assert chain is not None, moment
        assert chain.lease is not None
        assert (chain.lease.mac, chain.confidence) == (A_MAC, "low"), moment
        assert note(chain, OVERLAP) == {"network": "VLAN 42", "at": iso(moment), "sources": sources}
        assert UNCONVERTED not in chain_keys(chain)  # low already: the check skips it
        text = outcome_text(outcome)
        assert C_MAC not in text
        assert D_MAC not in text
        assert "idr-nb12" not in text
    # Past W (86400 s x 110 %) after the last MikroTik row only the FortiGate is left.
    alone = internal(db_session, SHARED, tr(DAY + timedelta(days=3), 14)).attribution
    assert alone is not None
    assert alone.lease is not None
    assert (alone.lease.mac, alone.confidence, alone.lease.confidence) == (A_MAC, "high", "high")
    assert OVERLAP not in chain_keys(alone)


def test_two_origins_on_a_subnet_nobody_entered(db_session: Session, evidence: Evidence) -> None:
    """Round 6 (implementation note, D27): a forgotten FortiGate DHCP server beside MikroTik DHCP
    on a subnet that is not entered. The MikroTik ACK logged last gives C's chain, which the
    other origin's row of the same address lowers (the address stands for its network)."""
    unlisted = "172.20.44.30"
    evidence.fortigate_dhcp(T0, unlisted, A_MAC, interface="vlan44")
    evidence.dhcp(T0 + timedelta(hours=1), "assigned", unlisted, C_MAC, server="dhcp44")
    build(db_session, DAY, DAY)
    moment = T0 + timedelta(hours=1, minutes=30)
    chain = internal(db_session, unlisted, moment).attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.mac, chain.lease.source_table, chain.confidence) == (
        C_MAC,
        "mt_dhcp",
        "low",
    )
    assert note(chain, OVERLAP) == {
        "network": unlisted,
        "at": iso(moment),
        "sources": "FortiGate DHCP (VDOM root), MikroTik DHCP",
    }


def test_an_ha_pair_is_one_origin(db_session: Session, evidence: Evidence) -> None:
    """Two FortiGate senders of one VDOM (an HA pair, two serial numbers): the same DHCP service,
    never two origins (D27)."""
    for step in range(4):
        source, devid = ("fortigate", "FGT0000000000000")
        if step % 2:
            source, devid = ("fortigate-2", "FGT0000000000001")
        evidence.fortigate_dhcp(
            T0 + timedelta(hours=12 * step), SHARED, A_MAC, source=source, devid=devid
        )
    build(db_session, DAY, DAY + timedelta(days=2))
    for hours in (1, 13, 25, 37):
        chain = internal(db_session, SHARED, T0 + timedelta(hours=hours)).attribution
        assert chain is not None, hours
        assert chain.lease is not None
        assert (chain.confidence, chain.lease.confidence) == ("high", "high"), hours
        assert OVERLAP not in chain_keys(chain)


def test_one_origins_query_serves_every_chain(
    db_session: Session, evidence: Evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate lookup with chains in two networks: one ``usable_dhcp_origins`` query for
    both, and only the chain of the network with two origins is lowered."""
    _two_origins(evidence)
    admin = "172.20.43.20"
    evidence.fortigate_dhcp(T0, admin, D_MAC, interface="vlan43")
    moment = T0 + timedelta(hours=1, minutes=30)
    evidence.traffic(moment, SHARED, 50001)
    evidence.traffic(moment, admin, 50002)
    build(db_session, DAY, DAY)
    calls: list[list[str]] = []
    original = trust.usable_dhcp_origins

    def counted(*args: Any, **kwargs: Any) -> dict[str, set[tuple[str, str | None]]]:
        calls.append(sorted(args[3]))
        return original(*args, **kwargs)

    monkeypatch.setattr(attribution, "usable_dhcp_origins", counted)
    outcome = lookup(db_session, public_ip="198.51.100.20", at=moment)
    assert outcome.mode == "candidates"
    chains = {m.chain.internal_ip: m.chain for m in outcome.matches if m.chain is not None}
    assert sorted(chains) == [SHARED, admin]
    assert calls == [["172.20.42.0/24", "172.20.43.0/24"]]
    assert chains[SHARED].confidence == "low"
    assert OVERLAP in chain_keys(chains[SHARED])
    assert chains[admin].confidence == "high"
    assert OVERLAP not in chain_keys(chains[admin])


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


@pytest.mark.parametrize(("grace", "opening"), [(None, tr(DAY, 12, 30)), (0, tr(DAY, 12, 2))])
def test_arp_sightings_after_a_release_mapped_later_name_the_client(
    db_session: Session, evidence: Evidence, grace: int | None, opening: datetime
) -> None:
    """The same late-mapped RELEASE with MikroTik ARP sightings of the pair after it: in the
    stale build they only touched the open FortiGate lease; the rebuild ends that lease at the
    RELEASE and opens an ARP interval at the first sighting after ``arp.ignore_after_release_
    seconds`` (by default 12:02 is a stale ARP entry and 12:30 opens it; 0 switches the rule
    off). The lookup answers as after the rebuild: the ARP chain (``medium``, ``arp_only``),
    never an unwarned "nobody"."""
    if grace is not None:
        set_setting(db_session, "arp.ignore_after_release_seconds", grace, updated_by="tolga")
    assert settings_for(db_session).arp_release_grace_seconds == (300 if grace is None else 0)
    set_setting(db_session, "dhcp.fortigate_logids", {"0100026001": "assigned"}, updated_by="tolga")
    evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=HOST)
    evidence.fortigate_dhcp(
        tr(DAY, 12), PC, PC_MAC, logid="0100026002", dhcp_msg="Release", lease=None
    )
    for hour, minute in ((12, 2), (12, 30), (13, 30), (14, 30)):
        evidence.arp(tr(DAY, hour, minute), PC, PC_MAC, interface="vlan42")
    build(db_session, DAY, DAY)
    assert [lease.source_table for lease in db_session.scalars(select(Lease))] == ["fg_dhcp"]
    mapped = {"0100026001": "assigned", "0100026002": "deassigned"}
    set_setting(db_session, "dhcp.fortigate_logids", mapped, updated_by="tolga")
    db_session.commit()

    def answers() -> list[tuple[Any, ...]]:
        found: list[tuple[Any, ...]] = []
        for moment in (tr(DAY, 12, 10), tr(DAY, 14, 45), tr(DAY, 15, 31)):
            outcome = internal(db_session, PC, moment)
            chain = outcome.attribution
            if chain is None:
                found.append((moment, None, keys(outcome)))
                continue
            assert chain.lease is not None
            found.append(
                (
                    moment,
                    chain.confidence,
                    chain.lease.mac,
                    chain.lease.source_table,
                    chain.lease.start,
                    chain.lease.confidence,
                    chain.lease.last_dhcp_evidence,
                    sorted(chain_keys(chain)),
                    keys(outcome),
                )
            )
        return found

    def cited() -> tuple[EvidenceRef, ...]:
        """The refs of the chain after the last sighting (the opening one and that one)."""
        chain = internal(db_session, PC, tr(DAY, 14, 45)).attribution
        assert chain is not None
        assert chain.lease is not None
        return chain.lease.evidence

    stale, refs = answers(), cited()
    assert [ref.time for ref in refs] == [opening, tr(DAY, 14, 30)]
    assert stale[1][1:8] == (
        "medium",
        PC_MAC,
        "mt_arp",
        opening,
        "medium",
        None,
        ["arp_only", "no_person"],
    )
    early = None if grace is None else "medium"
    assert [answer[1] for answer in stale] == [early, "medium", None]
    by_mac = lookup(db_session, mac=PC_MAC, at=tr(DAY, 14, 45)).attribution
    assert by_mac is not None
    assert by_mac.lease is not None
    assert (by_mac.internal_ip, by_mac.lease.source_table) == (PC, "mt_arp")
    # The rebuild gives the same answers.
    build(db_session, DAY, DAY)
    assert sorted(
        (lease.source_table, lease.start) for lease in db_session.scalars(select(Lease))
    ) == [("fg_dhcp", T0), ("mt_arp", opening)]
    assert (answers(), cited()) == (stale, refs)


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


# -- ACKs the build refused and today's settings accept (deviation c2) -----------------------------

#: An HA member that sent from its own address before anyone configured it (D24).
HA_MEMBER = "unknown-172.20.10.2"
#: A logid nobody had mapped when the leases were built (synthetic, as in the trust tests).
LATER_LOGID = "0100026099"


def configure_ha_member(session: Session) -> None:
    """The second FortiGate source, whose address covers :data:`HA_MEMBER` (D24)."""
    session.add(Source(name="fortigate-2", type="fortigate", address="172.20.10.0/30"))
    session.commit()


def assert_unshown(outcome: LookupOutcome) -> None:
    """Nothing of OTHER's row is cited or shown (KVKK, 2.16)."""
    text = outcome_text(outcome)
    assert OTHER not in text
    assert "idr-nb07" not in text
    assert outcome.unconverted_dhcp == ()


def test_another_clients_ack_from_a_sender_configured_after_the_build(
    db_session: Session, evidence: Evidence
) -> None:
    """Manager review of PR #30, (a): OTHER's 10:00 ACK for .57 came from the HA member nobody
    had configured, so the build refused it and left the PC's 08:00 lease open. Once a source
    covers the member, the lookup at 11:00 names Selin Kaya at ``low`` with the post-lease
    warning (OTHER's row is neither cited nor shown), until the rebuild names OTHER."""
    db_session.add(Source(name=HA_MEMBER, type="fortigate", address="172.20.10.2", unknown=True))
    evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=HOST)
    evidence.fortigate_dhcp(tr(DAY, 10), PC, OTHER, hostname="IDR-NB07", source=HA_MEMBER)
    build(db_session, DAY, DAY)
    selin(db_session)
    moment = tr(DAY, 11)
    refused = internal(db_session, PC, moment).attribution
    assert refused is not None
    assert refused.lease is not None
    # ``medium``: the person step, as on a MikroTik site.
    assert (refused.lease.mac, refused.confidence, refused.lease.confidence) == (
        PC_MAC,
        "medium",
        "high",
    )
    configure_ha_member(db_session)
    outcome = internal(db_session, PC, moment)
    chain = outcome.attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.person is not None
    assert (chain.lease.mac, chain.person.name) == (PC_MAC, "Selin Kaya")
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert note(chain, UNCONVERTED) == {"last": iso(T0), "ip": PC}
    assert OVERLAP not in chain_keys(chain)
    assert [ref.time for ref in chain.lease.evidence if ref.table == "fg_dhcp"] == [T0]
    assert_unshown(outcome)
    # Before OTHER's ACK nothing is lowered.
    early = internal(db_session, PC, tr(DAY, 9, 59)).attribution
    assert early is not None
    assert (early.confidence, chain_keys(early).count(UNCONVERTED)) == ("medium", 0)
    build(db_session, DAY, DAY)  # the rebuild
    rebuilt = internal(db_session, PC, moment).attribution
    assert rebuilt is not None
    assert rebuilt.lease is not None
    assert (rebuilt.lease.mac, rebuilt.lease.confidence) == (OTHER, "high")
    assert UNCONVERTED not in chain_keys(rebuilt)


def test_another_clients_ack_under_a_logid_mapped_after_the_build(
    db_session: Session, evidence: Evidence
) -> None:
    """Manager review of PR #30, (b): OTHER's ACK for .57 six hours after the PC's (both with
    the 7-day default lease) was logged under a logid mapped only after the build. Thirty hours
    after the PC's ACK (inside half its lease) the chain is ``low`` with the post-lease warning
    until the rebuild names OTHER."""
    evidence.fortigate_dhcp(T0, PC, PC_MAC, hostname=HOST, lease=WEEK)
    evidence.fortigate_dhcp(
        T0 + timedelta(hours=6), PC, OTHER, hostname="IDR-NB07", lease=WEEK, logid=LATER_LOGID
    )
    build(db_session, DAY, DAY + timedelta(days=1))
    selin(db_session)
    moment = T0 + timedelta(hours=30)
    unmapped = internal(db_session, PC, moment).attribution
    assert unmapped is not None
    assert (unmapped.confidence, chain_keys(unmapped).count(UNCONVERTED)) == ("medium", 0)
    mapped = {"0100026001": "assigned", "0100026002": "deassigned", LATER_LOGID: "assigned"}
    set_setting(db_session, "dhcp.fortigate_logids", mapped, updated_by="tolga")
    db_session.commit()
    outcome = internal(db_session, PC, moment)
    chain = outcome.attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.mac == PC_MAC
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert note(chain, UNCONVERTED) == {"last": iso(T0), "ip": PC}
    assert "lease_renewal_not_seen" not in chain_keys(chain)
    assert_unshown(outcome)
    build(db_session, DAY, DAY + timedelta(days=1))
    rebuilt = internal(db_session, PC, moment).attribution
    assert rebuilt is not None
    assert rebuilt.lease is not None
    assert (rebuilt.lease.mac, rebuilt.lease.confidence) == (OTHER, "high")
    assert UNCONVERTED not in chain_keys(rebuilt)


def test_a_migration_ack_from_a_sender_configured_after_the_build(
    db_session: Session, evidence: Evidence
) -> None:
    """Manager review of PR #30, (c): the FortiGate's 09:20 ACK of the PC's own pair (D5) came
    from the HA member nobody had configured, so the build kept the MikroTik lease, and the
    printer's traffic keeps it open. Once the member is configured, the lookup on day 3 is
    ``low`` with the post-lease warning, until the rebuild answers ``no_lease``."""
    db_session.add(Source(name=HA_MEMBER, type="fortigate", address="172.20.10.2", unknown=True))
    _mikrotik_lease(evidence)
    evidence.fortigate_dhcp(MOVED, PC, PC_MAC, hostname=HOST, source=HA_MEMBER)
    _printer(evidence, 3)
    build(db_session, MOVE_DAY, MOVE_DAY + timedelta(days=3))
    selin(db_session)
    moment = MOVED + timedelta(days=2, minutes=10)
    refused = internal(db_session, PC, moment).attribution
    assert refused is not None
    assert refused.lease is not None
    assert (refused.lease.source_table, refused.lease.confidence) == ("mt_dhcp", "high")
    assert UNCONVERTED not in chain_keys(refused)
    configure_ha_member(db_session)
    outcome = internal(db_session, PC, moment)
    chain = outcome.attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.source_table, chain.lease.mac) == ("mt_dhcp", PC_MAC)
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert note(chain, UNCONVERTED) == {"last": iso(tr(MOVE_DAY, 8)), "ip": PC}
    assert OVERLAP not in chain_keys(chain)
    assert_unshown(outcome)
    build(db_session, MOVE_DAY, MOVE_DAY + timedelta(days=3))
    rebuilt = internal(db_session, PC, moment)
    assert rebuilt.attribution is None
    assert "no_lease" in keys(rebuilt)


def test_a_healthy_fortigate_site_keeps_its_chain(
    db_session: Session, evidence: Evidence, example: list[int]
) -> None:
    """The example site with OTHER's ACKs of .57 that a healthy build either applied (before the
    PC's lease, after the moment) or still refuses (an unconfigured sender, an unmapped logid,
    the receive time, a NAK under the ACK's logid): the lease stays ``high``, and the chain at
    the person step's ``medium``."""
    stranger(db_session)
    evidence.fortigate_dhcp(tr(DAY, 7), PC, OTHER)
    # Inside the chain's window: after its 11:00 re-ACK, before the moment.
    evidence.fortigate_dhcp(AT - timedelta(minutes=20), PC, OTHER, source=STRANGER)
    evidence.fortigate_dhcp(AT - timedelta(minutes=15), PC, OTHER, logid="0100026003")
    evidence.fortigate_dhcp(AT - timedelta(minutes=10), PC, OTHER, device_time=False)
    evidence.fortigate_dhcp(AT - timedelta(minutes=5), PC, OTHER, dhcp_msg="Nak")
    evidence.fortigate_dhcp(AT + timedelta(minutes=1), PC, OTHER)
    evidence.fortigate_dhcp(tr(DAY, 9), "172.20.42.58", OTHER)
    build(db_session, DAY - timedelta(days=1), DAY + timedelta(days=1))
    chain = internal(db_session, PC, AT).attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.mac, chain.lease.start) == (PC_MAC, tr(DAY, 8))
    assert (chain.confidence, chain.lease.confidence) == ("medium", "high")  # the person step
    assert UNCONVERTED not in chain_keys(chain)


def test_a_healthy_mixed_site_keeps_its_chains(db_session: Session, evidence: Evidence) -> None:
    """MikroTik DHCP serves VLAN 42, the FortiGate VLAN 43 (renewals every 12 hours); the
    FortiGate gave .57 to OTHER two days before the PC's MikroTik ACK (the build ended that
    lease long before). Both chains keep their confidence."""
    staff, staff_mac = "172.20.43.57", "00:00:5e:00:53:58"
    start = tr(MOVE_DAY + timedelta(days=2), 8)
    evidence.fortigate_dhcp(tr(MOVE_DAY, 8), PC, OTHER)
    evidence.dhcp(start, "assigned", PC, PC_MAC, server="dhcp42", hostname=HOST)
    for step in range(8):
        moment = tr(MOVE_DAY, 8) + timedelta(hours=12 * step)
        evidence.fortigate_dhcp(moment, staff, staff_mac, interface="vlan43")
    evidence.traffic(start + timedelta(hours=3), PC, 50001)
    build(db_session, MOVE_DAY, MOVE_DAY + timedelta(days=3))
    moment = start + timedelta(hours=4)
    mikrotik = internal(db_session, PC, moment).attribution
    assert mikrotik is not None
    assert mikrotik.lease is not None
    assert (mikrotik.lease.source_table, mikrotik.lease.mac) == ("mt_dhcp", PC_MAC)
    assert (mikrotik.confidence, mikrotik.lease.confidence) == ("high", "high")
    assert UNCONVERTED not in chain_keys(mikrotik)
    fortigate = internal(db_session, staff, moment).attribution
    assert fortigate is not None
    assert fortigate.lease is not None
    assert (fortigate.lease.source_table, fortigate.lease.mac) == ("fg_dhcp", staff_mac)
    assert (fortigate.confidence, fortigate.lease.confidence) == ("high", "high")
    assert UNCONVERTED not in chain_keys(fortigate)


# -- the source clock at the requested moment (D28, 2.7) ------------------------------------------

#: A FortiGate clock set back by 8 minutes: its rows are stamped 8 minutes before they arrive
#: (an offset is the device time minus the receive time).
BEHIND = -timedelta(minutes=8)
CLOCK = "source_clock_offset"


def clock_lines(
    evidence: Evidence, offset: timedelta, count: int = 20, *, source: str = "fortigate"
) -> None:
    """``count`` lines of ``source`` without a typed row around the moment (device times every
    five minutes from AT - 50 min) whose device time is ``offset`` from their receive time."""
    for k in range(count):
        time = AT - timedelta(minutes=50) + timedelta(minutes=5 * k)
        evidence.heartbeat(time, source=source, received=time - offset)


def as_loaded(session: Session, offset: timedelta) -> None:
    """The configured FortiGate's typed rows as ingest loads them from a clock ``offset`` off:
    received ``-offset`` after their device time, each with its ``messages`` row, and its files'
    ``search_counts`` counted from the rows as the ingest loader counts them."""
    if session.get(Source, "fortigate") is None:
        session.add(Source(name="fortigate", type="fortigate", address=FIREWALL))
    days = set()
    for model, kind in ((FgTraffic, "traffic"), (FgDhcp, "event")):
        session.execute(
            update(model)
            .where(model.source == "fortigate")
            .values(time_received=model.time - offset)
        )
        for row in session.execute(
            select(model.time, model.time_received, model.sender, model.raw_day, model.raw_line,
                   model.raw_offset, model.subtype).where(model.source == "fortigate")
        ).all():  # fmt: skip
            session.add(
                Message(time=row.time, time_received=row.time_received, source="fortigate",
                        sender=row.sender, raw_day=row.raw_day, raw_line=row.raw_line,
                        raw_offset=row.raw_offset, type=kind, subtype=row.subtype, text="")
            )  # fmt: skip
            days.add(row.raw_day)
    session.flush()
    days |= set(session.scalars(select(Message.raw_day).where(Message.source == "fortigate")))
    for day in days:
        rebuild_stream(session.connection(), "fortigate", day)
    session.commit()


def offsets(outcome: LookupOutcome) -> dict[str, int]:
    """The sources ``source_clock_offset`` names, with their offsets in seconds."""
    named = outcome_note(outcome, CLOCK)["offsets"]
    assert isinstance(named, str)
    pairs = (item.split(": ") for item in named.split(", "))
    return {name: int(value) for name, value in pairs}


def exact_request(session: Session, **request: Any) -> LookupOutcome:
    return lookup(session, public_ip=WAN, public_port=51001, protocol="tcp", **request)


@pytest.mark.parametrize(
    ("offset", "lines", "printed"),
    [
        pytest.param(BEHIND, 20, -480, id="8-minutes-behind"),
        pytest.param(-timedelta(seconds=30), 20, None, id="30-seconds"),
        pytest.param(BEHIND, 8, None, id="9-rows"),
        pytest.param(BEHIND, 9, -480, id="10-rows"),
        pytest.param(-timedelta(seconds=120.4), 20, None, id="120.4-seconds"),
        pytest.param(-timedelta(seconds=120.5), 20, -121, id="120.5-seconds"),
        pytest.param(timedelta(seconds=120.5), 20, 121, id="120.5-seconds-ahead"),
        pytest.param(-timedelta(seconds=120.6), 20, -121, id="120.6-seconds"),
    ],
)
def test_a_cited_source_whose_clock_was_off_lowers_every_chain(
    db_session: Session, evidence: Evidence, offset: timedelta, lines: int, printed: int | None
) -> None:
    """3.4 "source clock": the FortiGate's rows around T are 8 minutes behind their receive
    time; an exact request at T names the FortiGate with about -480 s, every chain is ``low``
    (its lease step too) and the match stays ``exact``. 30 s, or fewer than 10 rows of the source
    around T (the session row and the lines without a typed row), add nothing. The offset
    compared with the limit is the whole number printed, so a printed offset always exceeds
    the limit (120.4 s prints 120: nothing); a half second rounds away from zero (120.5 s is
    beyond the limit and prints 121)."""
    evidence.fortigate_dhcp(tr(DAY, 8), PC, PC_MAC, hostname=HOST)
    evidence.traffic(AT, PC, 51001, transip=WAN)
    clock_lines(evidence, offset, lines)
    as_loaded(db_session, offset)
    build(db_session, DAY, DAY)
    wan_exit(db_session)
    outcome = exact_request(db_session)
    assert outcome.mode == "exact"
    (match,) = outcome.matches
    assert match.kind == "exact"
    chain = match.chain
    assert chain is not None
    assert chain.lease is not None
    if printed is None:
        assert CLOCK not in keys(outcome)
        assert (chain.confidence, chain.lease.confidence) == ("high", "high")
        return
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert offsets(outcome) == {"fortigate": printed}
    params = outcome_note(outcome, CLOCK)
    assert (params["at"], params["limit"]) == (iso(AT), 120)
    text = next(e for e in outcome.explanations if e.key == CLOCK)
    assert f"fortigate: {printed:+d} (sınır 120 saniye)" in text.text_tr
    assert f"fortigate: {printed:+d} (limit 120 seconds)" in text.text_en
    # The internal lookup rests on the same lease rows of the same clock.
    chain = internal(db_session, PC, AT).attribution
    assert chain is not None
    assert chain.confidence == "low"


def test_the_clock_lowers_every_alternative_and_every_match(
    db_session: Session, evidence: Evidence
) -> None:
    """Under the 8-minute clock every chain of the outcome is ``low`` with its lease step: the
    PC's chain whose session srcmac contradicts its lease (D10) and that srcmac chain, its
    alternative, in the exact request; both matches' chains of one candidate request, the
    ``ip_conflict`` of .58 (two stored DHCP leases of two clients cover the moment) with the
    lease step of its alternative, ``high`` without the clock."""
    stranger, third = "00:00:5e:00:53:66", "00:00:5e:00:53:59"
    evidence.fortigate_dhcp(tr(DAY, 8), PC, PC_MAC, hostname=HOST)
    evidence.fortigate_dhcp(tr(DAY, 8, 5), "172.20.42.58", OTHER)
    evidence.fortigate_dhcp(tr(DAY, 8, 10), "172.20.42.58", third)
    evidence.traffic(AT, PC, 51001, transip=WAN, srcintf="vlan42", dstintf="wan1",
                     srcmac=stranger)  # fmt: skip
    evidence.traffic(AT, "172.20.42.58", 51002, transip=WAN)
    clock_lines(evidence, BEHIND)
    as_loaded(db_session, BEHIND)
    build(db_session, DAY, DAY)
    # The build closes OTHER's lease at the 08:10 ACK; kept open as long, both leases cover AT.
    conflict = {
        lease.mac: lease
        for lease in db_session.scalars(select(Lease).where(Lease.ip == "172.20.42.58"))
    }
    conflict[OTHER].end, conflict[OTHER].end_reason = (
        conflict[third].end,
        conflict[third].end_reason,
    )
    db_session.commit()
    set_setting(db_session, "lookup.srcmac_interfaces", ["vlan42"], updated_by="tolga")
    wan_exit(db_session)
    exact = exact_request(db_session)
    assert offsets(exact) == {"fortigate": -480}
    (match,) = exact.matches
    chain = match.chain
    assert chain is not None
    assert chain.lease is not None
    assert note(chain, "srcmac_mismatch") == {"srcmac": stranger, "mac": PC_MAC}
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    (alternative,) = chain.alternatives
    assert alternative.device is not None
    assert (alternative.device.mac, alternative.confidence) == (stranger, "low")
    found = lookup(db_session, public_ip=WAN)
    assert found.mode == "candidates"
    assert offsets(found) == {"fortigate": -480}
    assert sorted(m.traffic.srcip for m in found.matches) == [PC, "172.20.42.58"]
    for match in found.matches:
        assert match.chain is not None
        every = [match.chain, *match.chain.alternatives]
        assert {c.confidence for c in every} == {"low"}
        assert {c.lease.confidence for c in every if c.lease is not None} == {"low"}
    conflicted = next(m.chain for m in found.matches if m.traffic.srcip == "172.20.42.58")
    assert conflicted is not None
    assert chain_keys(conflicted)[0] == "ip_conflict"
    (alternative,) = conflicted.alternatives
    assert alternative.lease is not None
    assert (alternative.lease.mac, alternative.lease.source_table) == (OTHER, "fg_dhcp")
    assert (alternative.confidence, alternative.lease.confidence) == ("low", "low")


def test_rows_stamped_with_the_receive_time_say_nothing_about_the_clock(
    db_session: Session, evidence: Evidence
) -> None:
    """Lines whose device time ingest replaced by the receive time are left out of the median:
    they neither hide an offset nor count towards the 10 rows."""
    evidence.fortigate_dhcp(tr(DAY, 8), PC, PC_MAC, hostname=HOST)
    evidence.traffic(AT, PC, 51001, transip=WAN)
    clock_lines(evidence, BEHIND, 10)
    clock_lines(evidence, timedelta(0), 15)
    as_loaded(db_session, BEHIND)
    build(db_session, DAY, DAY)
    wan_exit(db_session)
    assert offsets(exact_request(db_session)) == {"fortigate": -480}


def test_the_clock_of_a_session_search_alone_may_reach_the_tolerance(
    db_session: Session, evidence: Evidence
) -> None:
    """A MikroTik lease and FortiGate sessions 8 minutes off: the FortiGate is cited for the
    session search only, whose limit is max(120 s, the tolerance); the lease's router keeps a
    good clock."""
    evidence.dhcp(tr(DAY, 8), "assigned", PC, PC_MAC, server="dhcp42", hostname=HOST)
    evidence.traffic(AT, PC, 51001, transip=WAN)
    clock_lines(evidence, BEHIND)
    as_loaded(db_session, BEHIND)
    build(db_session, DAY, DAY)
    wan_exit(db_session)
    narrow = exact_request(db_session)
    assert offsets(narrow) == {"fortigate": -480}
    assert outcome_note(narrow, CLOCK)["limit"] == 120
    (match,) = narrow.matches
    assert match.chain is not None
    assert match.chain.confidence == "low"
    wide = exact_request(db_session, tolerance_seconds=600)
    assert CLOCK not in keys(wide)
    (match,) = wide.matches
    assert match.chain is not None
    assert match.chain.confidence == "high"


@pytest.fixture
def clock_behind(db_session: Session, evidence: Evidence) -> None:
    """The empty-result site of 3.4 "source clock" (round 5): the FortiGate's clock 8 minutes
    behind, the PC's real session (received at AT) logged at device time AT - 8 min, nothing in
    the device-time window of an exact request at AT, no lease of the PC (another client's ACK
    of the morning is in the FortiGate's file of the day)."""
    evidence.fortigate_dhcp(tr(DAY, 8), "172.20.42.58", OTHER)
    evidence.traffic(AT + BEHIND, PC, 51001, transip=WAN)
    clock_lines(evidence, BEHIND)
    as_loaded(db_session, BEHIND)
    build(db_session, DAY, DAY)
    wan_exit(db_session)
    counted = db_session.execute(
        select(SearchCount.tbl).where(SearchCount.source == "fortigate").distinct()
    )
    assert {"fg_traffic", "fg_dhcp"} <= set(counted.scalars())


def test_an_empty_exact_search_names_the_clock_of_the_searched_source(
    db_session: Session, clock_behind: None
) -> None:
    outcome = exact_request(db_session)
    assert (outcome.mode, outcome.matches, outcome.attribution) == ("none", (), None)
    assert outcome.unconverted_dhcp == ()
    assert "no_match_exact" in keys(outcome)
    assert offsets(outcome) == {"fortigate": -480}
    assert outcome_note(outcome, CLOCK)["limit"] == 120


def test_an_internal_lookup_without_a_lease_names_the_clock(
    db_session: Session, clock_behind: None
) -> None:
    outcome = internal(db_session, PC, AT)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert "no_lease" in keys(outcome)
    assert offsets(outcome) == {"fortigate": -480}
    # The lease search uses the moment, not the tolerance: the fixed limit (round 6).
    wide = lookup(db_session, internal_ip=PC, tolerance_seconds=600)
    assert outcome_note(wide, CLOCK)["limit"] == 120


def test_the_tolerance_widened_by_the_offset_finds_the_session(
    db_session: Session, clock_behind: None
) -> None:
    """The text's remedy: the session is found; its chain is still missing (no lease), and the
    lease search that found nothing keeps the fixed limit, so the offset stays named."""
    outcome = exact_request(db_session, tolerance_seconds=600)
    assert outcome.mode == "exact"
    (match,) = outcome.matches
    assert (match.kind, match.traffic.srcip, match.chain) == ("exact", PC, None)
    assert outcome_note(outcome, MISSING) == {"count": 1}
    assert offsets(outcome) == {"fortigate": -480}
    assert outcome_note(outcome, CLOCK)["limit"] == 120


#: Answers that search nothing (2.8.3 ``nobody``): the exits, the requested address and the
#: resolution.
UNSEARCHED = {
    "no-egress": (
        {"wan": {"mode": "isp_nat", "public": ["198.51.100.20"], "transips": [WAN]}},
        "203.0.113.99",
        "no_egress",
    ),
    "not-logged": (
        {"misafir": {"mode": "not_logged", "public": ["198.51.100.90"]}},
        "198.51.100.90",
        "public",
    ),
    "internal": (
        {"dmz": {"mode": "internal", "transips": ["172.20.99.1"]}},
        "172.20.99.1",
        "transip",
    ),
}


def egresses(session: Session, value: dict[str, Any]) -> None:
    set_setting(session, "nat.egresses", validate_egresses(value), updated_by="tolga")
    session.commit()


@pytest.mark.parametrize("variant", sorted(UNSEARCHED))
def test_a_no_egress_answer_measures_nothing(
    db_session: Session, clock_behind: None, variant: str
) -> None:
    """An answer that searched nothing (``no_egress``, a ``not_logged`` exit nobody saw, an
    ``internal`` exit) cites no row and read no table: no clock is measured."""
    value, address, resolution = UNSEARCHED[variant]
    egresses(db_session, value)
    outcome = lookup(db_session, public_ip=address, public_port=51001, protocol="tcp")
    assert (outcome.mode, outcome.egress and outcome.egress.resolution) == ("none", resolution)
    assert CLOCK not in keys(outcome)


def test_a_not_logged_exit_the_logs_saw_measures_the_search(
    db_session: Session, evidence: Evidence, clock_behind: None
) -> None:
    """A ``not_logged`` exit whose address the FortiGate used near the moment (``observed``,
    D31 (a)): the PC's session through it, logged 8 minutes early, is searched for and not
    found, and the empty search names the FortiGate's clock."""
    guest = "198.51.100.90"
    evidence.traffic(AT + BEHIND, PC, 51001, transip=guest)
    egresses(db_session, {"misafir": {"mode": "not_logged", "public": [guest]}})
    outcome = lookup(db_session, public_ip=guest, public_port=51001, protocol="tcp")
    assert (outcome.mode, outcome.egress and outcome.egress.resolution) == ("none", "observed")
    assert "egress_not_logged_but_seen" in keys(outcome)
    assert offsets(outcome) == {"fortigate": -480}
    assert outcome_note(outcome, CLOCK)["limit"] == 120


def test_at_most_five_sources_are_measured_the_cited_ones_first(
    db_session: Session, evidence: Evidence
) -> None:
    """Round 7 note (b): the cited sources first, then the searched ones by their rows in the
    files of the day; a sixth source is never measured. Six more FortiGates whose files hold
    session and DHCP rows (counts 60 … 10); only the clock of the one with the fewest is off: an
    empty search names nobody; once the fifth is off too, it is named."""
    evidence.traffic(AT + BEHIND, PC, 51001, transip=WAN)
    as_loaded(db_session, timedelta(seconds=-1))
    names = [f"fw-{k}" for k in range(1, 7)]
    for k, name in enumerate(names):
        db_session.add(Source(name=name, type="fortigate", address=f"192.0.2.{10 + k}"))
        for table in ("fg_traffic", "fg_dhcp"):
            db_session.add(
                SearchCount(hour=tr(DAY, 11).astimezone(UTC), source=name, raw_day=DAY,
                            kind="traffic", tbl=table, n=60 - 10 * k)
            )  # fmt: skip
        clock_lines(evidence, BEHIND if name == "fw-6" else timedelta(seconds=5), source=name)
    # A disabled source and an unconfigured sender are never chosen.
    db_session.add(Source(name="fw-old", type="fortigate", address="192.0.2.30", enabled=False))
    db_session.add(Source(name=STRANGER, type="fortigate", address="192.0.2.7", unknown=True))
    for name in ("fw-old", STRANGER):
        db_session.add(
            SearchCount(hour=tr(DAY, 11).astimezone(UTC), source=name, raw_day=DAY,
                        kind="traffic", tbl="fg_traffic", n=500)
        )  # fmt: skip
        clock_lines(evidence, BEHIND, source=name)
    db_session.commit()
    build(db_session, DAY, DAY)
    wan_exit(db_session)
    outcome = exact_request(db_session)
    assert outcome.mode == "none"
    assert CLOCK not in keys(outcome)
    db_session.execute(delete(Message).where(Message.source == "fw-5"))
    clock_lines(evidence, BEHIND, source="fw-5")  # fw-5's clock is off as well now
    db_session.commit()
    assert offsets(exact_request(db_session)) == {"fw-5": -480}
    # A found session cites the FortiGate first (its lease search found nothing): it and the
    # four searched sources with the most rows are measured, fw-5 no longer.
    found = exact_request(db_session, tolerance_seconds=600)
    assert found.mode == "exact"
    assert CLOCK not in keys(found)


def test_the_clock_is_measured_on_the_rows_nearest_the_moment(
    db_session: Session, evidence: Evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 5 note "clock sample": the rows nearest the moment on each side of it, not the
    first rows of the hour before it. The clock was right until it was set back ten minutes
    before T; with ten rows a side (2,500 in the shipped sample) only the shifted rows count."""
    monkeypatch.setattr(engine, "CLOCK_SIDE_ROWS", 10)
    evidence.fortigate_dhcp(tr(DAY, 8), PC, PC_MAC, hostname=HOST)
    evidence.traffic(AT, PC, 51001, transip=WAN)
    for k in range(20):  # T - 60 min … T - 41 min: still on time
        time = AT - timedelta(minutes=60 - k)
        evidence.heartbeat(time, source="fortigate", received=time + timedelta(seconds=1))
    for k in range(-10, 10):  # T - 10 min … T + 9 min: 8 minutes behind
        time = AT + timedelta(minutes=k, seconds=30)
        evidence.heartbeat(time, source="fortigate", received=time - BEHIND)
    as_loaded(db_session, BEHIND)
    build(db_session, DAY, DAY)
    wan_exit(db_session)
    assert offsets(exact_request(db_session)) == {"fortigate": -480}


def test_the_lookup_and_the_alert_share_the_clock_limits() -> None:
    """``source_clock_offset`` and ``R20-lease:source-clock`` use the same limit and the same
    minimum of rows (D28)."""
    assert (engine.CLOCK_LIMIT_SECONDS, engine.CLOCK_MIN_ROWS) == (
        system.CLOCK_LIMIT_SECONDS,
        system.CLOCK_MIN_ROWS,
    )


# -- every engine explanation has a test that emits it (4.3 WP-C, "done when") --------------------

#: Per key the lookup engine can emit, a test that emits it and asserts it: the test names the
#: key (its text, or a module constant holding it), itself or in a module helper it calls. The
#: keys are those of the catalogue that an engine module names.
EMITTED_BY: dict[str, tuple[str, str]] = {
    "arp_only": ("test_lookup_engine.py", "test_ip_conflict_lists_every_device"),
    "candidates_without_destination": (
        "test_lookup_engine.py",
        "test_double_nat_candidates_without_destination",
    ),
    "chain_missing": (
        "test_lookup_fortigate_only.py",
        "test_the_probe_reads_twenty_addresses_at_most",
    ),
    "destination_not_found": ("test_lookup_engine.py", "test_unknown_destination_lists_nobody"),
    "destination_search": ("test_lookup_engine.py", "test_destination_only_request"),
    "dhcp_sources_overlap": (
        "test_lookup_fortigate_only.py",
        "test_one_origins_query_serves_every_chain",
    ),
    "double_nat": ("test_lookup_engine.py", "test_double_nat_candidates_without_destination"),
    "double_nat_no_candidates": ("test_lookup_engine.py", "test_shipped_defaults_are_double_nat"),
    "egress_changed_after_event": (
        "test_lookup_egress.py",
        "test_e_k4_a_content_change_of_the_exit_used_is_named",
    ),
    "egress_interface_not_seen": (
        "test_lookup_egress.py",
        "test_e_n2_the_widening_keeps_the_interfaces",
    ),
    "egress_interface_not_seen_kept": (
        "test_lookup_egress.py",
        "test_e_k_the_interface_condition_drops_only_on_evidence",
    ),
    "egress_internal": ("test_lookup_egress.py", "test_e_q_an_internal_nat_address"),
    "egress_not_logged": ("test_lookup_egress.py", "test_e_d_e_d2_a_not_logged_line"),
    "egress_not_logged_but_seen": (
        "test_lookup_fortigate_only.py",
        "test_a_not_logged_exit_the_logs_saw_measures_the_search",
    ),
    "egress_observed_not_configured": (
        "test_lookup_egress.py",
        "test_e_e2_e_e3_an_address_the_fortigate_used_itself",
    ),
    "egress_public_dynamic": (
        "test_lookup_egress.py",
        "test_e_e_an_unclaimed_address_goes_to_the_exits_of_unknown_public_address",
    ),
    "egress_public_unknown": (
        "test_lookup_egress.py",
        "test_e_e_an_unclaimed_address_goes_to_the_exits_of_unknown_public_address",
    ),
    "egress_search": ("test_lookup_egress.py", "test_e_n_an_isp_nat_line_not_seen_is_widened"),
    "egress_transip_not_seen": (
        "test_lookup_egress.py",
        "test_e_n2_the_widening_keeps_the_interfaces",
    ),
    "egress_unconfigured": ("test_lookup_engine.py", "test_unknown_destination_lists_nobody"),
    "egress_unmapped_seen": ("test_lookup_egress.py", "test_e_q_an_internal_nat_address"),
    "exact_domain_unverified": (
        "test_lookup_engine.py",
        "test_exact_domain_only_request_without_dns_logs_keeps_the_session_unverified",
    ),
    "exact_nat": ("test_lookup_egress.py", "test_e_d_e_d2_a_not_logged_line"),
    "exact_only_other_destinations": (
        "test_lookup_engine.py",
        "test_exact_lookup_lists_only_sessions_to_the_requested_destination",
    ),
    "exact_other_destinations": (
        "test_lookup_engine.py",
        "test_exact_lookup_lists_only_sessions_to_the_requested_destination",
    ),
    "exact_unverified_destinations": (
        "test_lookup_engine.py",
        "test_exact_domain_only_request_lists_only_the_sessions_tied_to_the_name",
    ),
    "fortigate_dhcp_no_network": (
        "test_lookup_fortigate_only.py",
        "test_a_decline_in_no_configured_network_is_low",
    ),
    "fortigate_dhcp_not_converted": (
        "test_lookup_fortigate_only.py",
        "test_the_probe_reads_twenty_addresses_at_most",
    ),
    "fortigate_dhcp_not_converted_since_lease": (
        "test_lookup_fortigate_only.py",
        "test_a_line_loaded_in_a_rollback_window",
    ),
    "fortigate_format_unverified": (
        "test_lookup_fortigate_only.py",
        "test_the_example_chain_from_fortigate_dhcp_alone",
    ),
    "fortigate_format_verified": (
        "test_lookup_fortigate_only.py",
        "test_the_format_note_names_who_verified_it_and_when",
    ),
    "future_time": ("test_lookup_engine.py", "test_data_window_for_days_outside_the_database"),
    "guest_vlan": ("test_lookup_engine.py", "test_guest_network_explanation"),
    "infrastructure_device": (
        "test_lookup_srcmac.py",
        "test_a_lease_of_an_infrastructure_mac_still_names_the_device",
    ),
    "ip_conflict": (
        "test_lookup_fortigate_only.py",
        "test_the_clock_lowers_every_alternative_and_every_match",
    ),
    "lease_renewal_not_seen": ("test_lookup_fortigate_only.py", "test_no_renewal_and_traffic_only"),
    "lease_timeline_behind": ("test_lookup_engine.py", "test_lease_timeline_behind_is_reported"),
    "mac_multiple_ips": (
        "test_lookup_srcmac.py",
        "test_a_mac_only_lookup_finds_the_address_by_its_srcmac",
    ),
    "mac_not_at_ip": ("test_lookup_engine.py", "test_internal_ip_mac_and_none_modes"),
    "multiple_exact": (
        "test_lookup_engine.py",
        "test_exact_lookup_lists_only_sessions_to_the_requested_destination",
    ),
    "nat_unknown_trandisp": ("test_lookup_egress.py", "test_e_l_an_unlisted_trandisp"),
    "no_data_for_day": ("test_lookup_engine.py", "test_data_window_for_days_outside_the_database"),
    "no_lease": ("test_lookup_fortigate_only.py", "test_no_renewal_and_traffic_only"),
    "no_lease_for_mac": (
        "test_lookup_fortigate_only.py",
        "test_a_mac_only_lookup_finds_the_line_by_its_mac",
    ),
    "no_match": ("test_lookup_egress.py", "test_e_c5_a_line_that_carried_the_address_stays_exact"),
    "no_match_candidates": ("test_lookup_engine.py", "test_shipped_defaults_are_double_nat"),
    "no_match_exact": (
        "test_lookup_fortigate_only.py",
        "test_an_empty_exact_search_names_the_clock_of_the_searched_source",
    ),
    "no_person": ("test_lookup_engine.py", "test_double_nat_candidates_without_destination"),
    "not_in_database": ("test_lookup_engine.py", "test_data_window_for_days_outside_the_database"),
    "older_than_db_retention": (
        "test_lookup_engine.py",
        "test_data_window_for_days_outside_the_database",
    ),
    "only_in_vault": ("test_lookup_engine.py", "test_data_window_for_days_outside_the_database"),
    "person_department_current": (
        "test_lookup_assignments.py",
        "test_a_later_reassignment_does_not_change_the_person_at_t",
    ),
    "person_link_at_lookup_time": ("test_lookup_engine.py", "test_ip_conflict_lists_every_device"),
    "person_link_backfilled": (
        "test_lookup_assignments.py",
        "test_a_backfilled_assignment_says_its_start_is_not_known",
    ),
    "person_unassigned_at_time": (
        "test_lookup_assignments.py",
        "test_a_device_unassigned_at_t_names_nobody",
    ),
    "port_hint": (
        "test_lookup_egress.py",
        "test_e_g_the_port_hint_ranks_only_a_preserved_exits_candidates",
    ),
    "port_missing": (
        "test_lookup_egress.py",
        "test_e_e2_e_e3_an_address_the_fortigate_used_itself",
    ),
    "public_ip_no_egress": (
        "test_lookup_egress.py",
        "test_e_e_an_unclaimed_address_goes_to_the_exits_of_unknown_public_address",
    ),
    "public_ip_not_transip": ("test_lookup_egress.py", "test_e_q_an_internal_nat_address"),
    "randomized_mac": ("test_lookup_engine.py", "test_double_nat_candidates_without_destination"),
    "session_changed_exit": ("test_lookup_egress.py", "test_e_k3_a_session_that_changes_exit"),
    "session_from_unknown_source": (
        "test_lookup_egress.py",
        "test_e_p2_a_forged_session_is_marked_on_its_match",
    ),
    "source_clock_offset": (
        "test_lookup_fortigate_only.py",
        "test_a_not_logged_exit_the_logs_saw_measures_the_search",
    ),
    "source_configured_later": (
        "test_lookup_fortigate_only.py",
        "test_a_sender_configured_later_is_named_on_the_chain",
    ),
    "srcmac_agrees": ("test_lookup_srcmac.py", "test_a_lease_with_the_same_srcmac_agrees"),
    "srcmac_ambiguous": (
        "test_lookup_srcmac.py",
        "test_four_srcmacs_of_one_address_name_the_count_and_three",
    ),
    "srcmac_infrastructure": ("test_lookup_srcmac.py", "test_an_infrastructure_mac_is_not_used"),
    "srcmac_mismatch": (
        "test_lookup_fortigate_only.py",
        "test_a_srcmac_that_contradicts_the_lease",
    ),
    "srcmac_only": ("test_lookup_fortigate_only.py", "test_a_srcmac_that_contradicts_the_lease"),
    "srcmac_other_lease": (
        "test_lookup_srcmac.py",
        "test_a_mac_with_a_lease_of_another_address_is_not_used",
    ),
    "srcmac_shared": ("test_lookup_srcmac.py", "test_one_mac_on_two_ipv4_addresses_is_shared"),
    "truncated": ("test_lookup_engine.py", "test_truncation_keeps_the_best_and_says_so"),
}
#: Retired keys: only the catalogue names them (stored lookups still render).
NEVER_EMITTED = frozenset({"public_ip_not_configured", "wan_transip_not_seen"})


def engine_keys(catalogue: set[str]) -> set[str]:
    """The catalogue's keys that a module of the lookup package other than the catalogue's own
    (``texts.py``, ``types.py``) names."""
    package = Path(attribution.__file__).parent
    named: set[str] = set()
    for path in sorted(package.glob("*.py")):
        if path.name in ("texts.py", "types.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Constant) and node.value in catalogue:
                named.add(node.value)
    return named


def read_module(name: str) -> tuple[dict[str, Any], dict[str, ast.FunctionDef]]:
    """A test module's string constants and functions, by name."""
    tree = ast.parse((Path(__file__).parent / name).read_text("utf-8"))
    constants: dict[str, Any] = {}
    functions: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions[node.name] = node
        targets = node.targets if isinstance(node, ast.Assign) else []
        value = node.value if isinstance(node, ast.Assign) else None
        if isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        for target in targets:
            pairs = (
                zip(target.elts, value.elts, strict=False)
                if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple)
                else [(target, value)]
            )
            for name_node, constant in pairs:
                if isinstance(name_node, ast.Name) and isinstance(constant, ast.Constant):
                    constants[name_node.id] = constant.value
    return constants, functions


def names_key(module: str, test: str, key: str) -> bool:
    constants, functions = read_module(module)
    if test not in functions:
        return False
    function = functions[test]
    helpers = [
        functions[node.func.id]
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in functions
    ]
    return any(
        (isinstance(node, ast.Constant) and node.value == key)
        or (isinstance(node, ast.Name) and constants.get(node.id) == key)
        for body in (function, *helpers)
        for node in ast.walk(body)
    )


def test_every_engine_explanation_has_a_test_that_emits_it() -> None:
    """4.3 WP-C, done when: "every engine explanation has a test that emits it". Every key of
    the catalogue that the engine names is listed with its test, every listed test exists and
    names its key, and only the retired keys are named by no engine module."""
    catalogue = set(EXPLANATIONS) | set(ENGINE_EXPLANATIONS)
    emitted = engine_keys(catalogue)
    assert catalogue - emitted == NEVER_EMITTED
    assert sorted(emitted - set(EMITTED_BY)) == []
    assert sorted(set(EMITTED_BY) - emitted) == []
    unnamed = {key: where for key, where in EMITTED_BY.items() if not names_key(*where, key)}
    assert unnamed == {}
