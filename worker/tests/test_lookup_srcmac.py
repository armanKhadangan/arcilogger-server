"""srcmac in the 5651 lookup (ADR-0016 D10; Wave 3 contract 2.9, 3.4 "srcmac"), against
PostgreSQL.

The srcmac field of a FortiGate session record names a MAC only on an interface listed in
``lookup.srcmac_interfaces`` (off by default), from a trusted sender, for a MAC that is no
infrastructure MAC, is not shared by several IPv4 addresses within an hour of the moment and
holds no lease of another address there. It then gives a ``low`` chain without a lease
(``mac_source="srcmac"``, ``srcmac_only``), agrees with a lease or contradicts it (the ``low``
conflict of D10), and never reaches the leases, the devices, conflicts or usage. The example:
Selin Kaya (Planlama) on ``Planlama-PC-14``, ``172.20.42.57`` / ``00:00:5e:00:53:57`` in
VLAN 42, a static address now (no lease covers the moment).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from arcilogger_db.egress import validate_egresses
from arcilogger_db.models import Department, Device, FgTraffic, Lease, Person, Setting, Source
from arcilogger_db.settings import get_settings, set_setting
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import (
    AttributionChain,
    LookupOutcome,
    LookupRequest,
    LookupSettings,
    engine,
    run_lookup,
    trust,
)
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)
AT = tr(DAY, 11, 23, 41)
NOW = tr(DAY + timedelta(days=9), 9)
ENTERED = tr(DAY - timedelta(days=30), 9)
PC, PC_MAC, HOST = "172.20.42.57", "00:00:5e:00:53:57", "PLN-PC14"
#: Another client, a router on VLAN 42 and the FortiGate's NAT address.
OTHER = "00:00:5e:00:53:77"
ROUTER_IP, ROUTER_MAC = "172.20.42.10", "00:00:5e:00:53:0a"
WAN = "100.64.12.9"
#: No exits: the public address goes to the implicit double-NAT exit (candidates).
PUBLIC = "198.51.100.20"
#: An unconfigured sender (D24).
STRANGER = "unknown-192.0.2.7"


def build(session: Session, first: date, last: date) -> None:
    session.commit()
    day = first
    while day <= last:
        rebuild_day(session, day, horizon=NOW)
        session.commit()
        day += timedelta(days=1)


def listed(session: Session, interfaces: list[str], *, max_ips: int | None = None) -> None:
    set_setting(session, "lookup.srcmac_interfaces", interfaces, updated_by="tolga")
    if max_ips is not None:
        set_setting(session, "lookup.srcmac_max_ips", max_ips, updated_by="tolga")
    session.commit()


def lookup(session: Session, **request: Any) -> LookupOutcome:
    request.setdefault("at", AT)
    settings = LookupSettings.from_settings(get_settings(session))
    outcome = run_lookup(session, LookupRequest(**request), settings, now=NOW)
    session.rollback()
    assert LookupOutcome.from_dict(json.loads(json.dumps(outcome.to_dict()))) == outcome
    return outcome


def internal(session: Session, ip: str = PC) -> LookupOutcome:
    return lookup(session, internal_ip=ip)


def candidates(session: Session) -> LookupOutcome:
    return lookup(session, public_ip=PUBLIC)


def wan_exit(session: Session) -> None:
    """Exit ``wan`` with the FortiGate's NAT address, entered before the evidence."""
    value = validate_egresses({"wan": {"mode": "isp_nat", "transips": [WAN]}})
    set_setting(session, "nat.egresses", value, updated_by="tolga")
    session.execute(update(Setting).where(Setting.key == "nat.egresses").values(updated_at=ENTERED))
    session.commit()


def exact(session: Session) -> LookupOutcome:
    """The exact request: the exit's address with the session's port."""
    return lookup(session, public_ip=WAN, public_port=51001, protocol="tcp")


def keys(items: LookupOutcome | AttributionChain) -> list[str]:
    return [e.key for e in items.explanations]


def note(items: LookupOutcome | AttributionChain, key: str) -> dict[str, Any]:
    return dict(next(e for e in items.explanations if e.key == key).params)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def stored(outcome: LookupOutcome) -> str:
    """The outcome as the API stores and returns it (JSON, texts included)."""
    return json.dumps(outcome.to_dict(), ensure_ascii=False).lower()


def session_of(
    evidence: Evidence,
    ip: str = PC,
    mac: str | None = PC_MAC,
    *,
    at: datetime = AT,
    port: int = 51001,
    srcintf: str = "vlan42",
    duration: int = 10,
) -> int:
    """A session of ``ip`` through the FortiGate's NAT address logged at ``at`` (lasting
    ``duration`` seconds), with ``mac`` as its srcmac on ``srcintf``."""
    return evidence.traffic(
        at, ip, port, transip=WAN, srcintf=srcintf, dstintf="wan1", srcmac=mac, duration=duration
    )


def forged(session: Session, row: int) -> None:
    """Make the row ``row`` a line of an unconfigured sender (``trust.untrusted_sources``)."""
    if session.get(Source, STRANGER) is None:
        session.add(Source(name=STRANGER, type="fortigate", address="192.0.2.7", unknown=True))
    session.execute(update(FgTraffic).where(FgTraffic.id == row).values(source=STRANGER))
    session.commit()


@pytest.fixture
def selin(db_session: Session) -> int:
    """The PC's device (known from earlier leases, its address static now) and Selin Kaya of
    Planlama, its owner."""
    planning = db_session.scalars(select(Department).where(Department.name == "Planlama")).one()
    person = Person(name="Selin Kaya", department_id=planning.id, title="Planlama Uzmanı")
    db_session.add(person)
    db_session.flush()
    db_session.add(
        Device(
            mac=PC_MAC,
            name="Planlama-PC-14",
            hostnames=[HOST],
            vendor="Örnek A.Ş.",
            person_id=person.id,
        )
    )
    db_session.commit()
    return person.id


def assert_srcmac_chain(chain: AttributionChain | None, row: int) -> AttributionChain:
    """The srcmac chain of the PC: no lease, ``low`` throughout, the device and the person by
    the MAC, resting on the session record ``row``."""
    assert chain is not None
    assert (chain.internal_ip, chain.lease, chain.mac_source, chain.confidence) == (
        PC,
        None,
        "srcmac",
        "low",
    )
    assert note(chain, "srcmac_only") == {"interface": "vlan42"}
    device = chain.device
    assert device is not None
    assert (device.mac, device.name, device.hostnames, device.vendor) == (
        PC_MAC,
        "Planlama-PC-14",
        (HOST,),
        "Örnek A.Ş.",
    )
    assert device.confidence == "low"
    assert [(ref.table, ref.row_id) for ref in device.evidence] == [("fg_traffic", row)]
    assert chain.person is not None
    assert chain.person.name == "Selin Kaya"
    assert (chain.department, chain.vlan) == ("Planlama", 42)
    return chain


# -- off by default, and only on listed interfaces ------------------------------------------------


def test_off_by_default(db_session: Session, evidence: Evidence, selin: int) -> None:
    """``lookup.srcmac_interfaces`` is empty by default: the srcmac of the session gives no
    chain, and the match carries no srcmac."""
    session_of(evidence)
    build(db_session, DAY, DAY)
    outcome = internal(db_session)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert "no_lease" in keys(outcome)
    assert "srcmac_only" not in keys(outcome)
    found = candidates(db_session)
    (match,) = found.matches
    assert (match.chain, match.traffic.srcmac) == (None, None)
    assert "chain_missing" in keys(found)


def test_a_listed_interface_gives_the_srcmac_chain(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """With VLAN 42's interface listed: the srcmac chain, ``low``, ``mac_source="srcmac"``,
    ``srcmac_only``, the device and the person; the internal outcome keeps ``no_lease``, the
    match has a chain (no ``chain_missing``) and carries the srcmac."""
    row = session_of(evidence)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = internal(db_session)
    assert outcome.mode == "internal"
    assert "no_lease" in keys(outcome)
    assert_srcmac_chain(outcome.attribution, row)
    found = candidates(db_session)
    (match,) = found.matches
    assert match.traffic.srcmac == PC_MAC
    assert_srcmac_chain(match.chain, row)
    assert "chain_missing" not in keys(found)


def test_an_exact_match_gets_the_same_srcmac_chain(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """The exact form (an ``isp_nat`` exit with the FortiGate's NAT address, the request with
    its address, port 51001 and tcp): the same srcmac chain as the candidate's."""
    row = session_of(evidence)
    wan_exit(db_session)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = exact(db_session)
    assert outcome.mode == "exact"
    (match,) = outcome.matches
    assert (match.kind, match.traffic.srcmac) == ("exact", PC_MAC)
    assert_srcmac_chain(match.chain, row)
    assert "chain_missing" not in keys(outcome)


def test_an_unlisted_interface_is_never_used_and_never_stored(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """A srcmac on an interface that is not listed never names a MAC, and with
    ``lookup.srcmac_interfaces = []`` no outcome (as stored) holds any srcmac value, although
    every row carries one: the PC's session in VLAN 42 and another client's in VLAN 43."""
    session_of(evidence)
    session_of(evidence, "172.20.43.20", OTHER, port=51002, srcintf="vlan43")
    wan_exit(db_session)
    build(db_session, DAY, DAY)
    outcomes = [
        internal(db_session),
        internal(db_session, "172.20.43.20"),
        lookup(db_session, public_ip=WAN),
        exact(db_session),
        lookup(db_session, mac=PC_MAC),
    ]
    for outcome in outcomes:
        text = stored(outcome)
        assert OTHER not in text
        assert all(m.traffic.srcmac is None for m in outcome.matches)
        assert outcome.attribution is None
        assert all(m.chain is None for m in outcome.matches)
    # Only the PC's own MAC was asked for, and it is in the request alone.
    assert all(PC_MAC not in stored(outcome) for outcome in outcomes[:4])
    listed(db_session, ["vlan42"])
    unlisted = internal(db_session, "172.20.43.20")
    assert (unlisted.mode, unlisted.attribution) == ("none", None)
    assert OTHER not in stored(unlisted)
    found = lookup(db_session, public_ip=WAN)
    by_ip = {m.traffic.srcip: m for m in found.matches}
    assert by_ip["172.20.43.20"].traffic.srcmac is None
    assert by_ip["172.20.43.20"].chain is None
    assert by_ip[PC].traffic.srcmac == PC_MAC
    assert OTHER not in stored(found)


# -- the guards -----------------------------------------------------------------------------------


def test_one_mac_on_two_ipv4_addresses_is_shared(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """Two IPv4 addresses with one srcmac within an hour of the moment (``srcmac_max_ips`` 1):
    ``srcmac_shared`` and no chain."""
    session_of(evidence)
    session_of(evidence, "172.20.42.58", at=AT + timedelta(minutes=30), port=51002)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = internal(db_session)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert note(outcome, "srcmac_shared") == {"mac": PC_MAC, "at": iso(AT), "count": 2}
    found = candidates(db_session)
    (match,) = found.matches
    assert match.chain is None
    assert note(found, "srcmac_shared") == {"mac": PC_MAC, "at": iso(AT), "count": 2}


def test_a_dual_stack_client_is_not_shared(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """One IPv4 and two IPv6 addresses (a temporary one among them) with one srcmac: only IPv4
    addresses count, so the MAC is not shared."""
    row = session_of(evidence)
    session_of(evidence, "2001:db8:42::57", at=AT + timedelta(minutes=5), port=51002)
    session_of(evidence, "2001:db8:42::5:a1b2", at=AT + timedelta(minutes=6), port=51003)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = internal(db_session)
    assert "srcmac_shared" not in keys(outcome)
    assert_srcmac_chain(outcome.attribution, row)


def test_a_row_of_an_unconfigured_sender_is_never_used_nor_counted(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """D24: the srcmac of a row from an ``unknown-<ip>`` sender never names a MAC, and such a
    row of the PC's MAC on another address does not make the MAC shared."""
    forged(db_session, session_of(evidence))
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    alone = internal(db_session)
    assert (alone.mode, alone.attribution) == ("none", None)
    (match,) = candidates(db_session).matches
    assert (match.chain, match.traffic.srcmac) == (None, None)
    assert match.untrusted_sources == (STRANGER,)
    row = session_of(evidence, at=AT + timedelta(seconds=20), port=51002)
    forged(db_session, session_of(evidence, "172.20.42.58", at=AT + timedelta(minutes=10),
                                  port=51003))  # fmt: skip
    outcome = internal(db_session)
    assert "srcmac_shared" not in keys(outcome)
    assert_srcmac_chain(outcome.attribution, row)


def test_a_mac_with_a_lease_of_another_address_is_not_used(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """The router's MAC holds a lease of .10 that covers the window: its srcmac on the PC's
    session is a next hop's, not the PC's (``srcmac_other_lease``)."""
    evidence.fortigate_dhcp(AT - timedelta(hours=2), ROUTER_IP, ROUTER_MAC)
    session_of(evidence, mac=ROUTER_MAC)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = internal(db_session)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert note(outcome, "srcmac_other_lease") == {
        "mac": ROUTER_MAC,
        "at": iso(AT),
        "other_ip": ROUTER_IP,
    }


@pytest.mark.parametrize(
    ("released", "refused"),
    [
        pytest.param(timedelta(hours=1), True, id="at-the-window-start"),
        pytest.param(timedelta(hours=1, seconds=1), False, id="a-second-before"),
    ],
)
def test_a_lease_of_another_address_ending_at_the_window_start(
    db_session: Session, evidence: Evidence, selin: int, released: timedelta, refused: bool
) -> None:
    """The boundary of the other-lease check: the router's lease of .10, released exactly at
    T - 1 h, still touches the window and refuses its srcmac; released a second earlier, it does
    not (the read of the leases and the rule on them agree)."""
    evidence.fortigate_dhcp(AT - timedelta(hours=3), ROUTER_IP, ROUTER_MAC)
    evidence.fortigate_dhcp(AT - released, ROUTER_IP, ROUTER_MAC, logid="0100026002",
                            dhcp_msg="Release")  # fmt: skip
    session_of(evidence, mac=ROUTER_MAC)
    build(db_session, DAY, DAY)
    ends = db_session.scalars(select(Lease.end).where(Lease.mac == ROUTER_MAC)).all()
    assert ends == [AT - released]
    listed(db_session, ["vlan42"])
    outcome = internal(db_session)
    if refused:
        assert outcome.attribution is None
        assert note(outcome, "srcmac_other_lease")["other_ip"] == ROUTER_IP
    else:
        assert "srcmac_other_lease" not in keys(outcome)
        chain = outcome.attribution
        assert chain is not None
        assert (chain.mac_source, chain.device and chain.device.mac) == ("srcmac", ROUTER_MAC)


@pytest.mark.parametrize("marked", ["setting", "device"])
def test_an_infrastructure_mac_is_not_used(
    db_session: Session, evidence: Evidence, selin: int, marked: str
) -> None:
    """A MAC of ``devices.infrastructure_macs``, or of a device flagged as infrastructure, gives
    ``srcmac_infrastructure`` and no chain."""
    if marked == "setting":
        set_setting(db_session, "devices.infrastructure_macs", [ROUTER_MAC], updated_by="tolga")
    else:
        db_session.add(Device(mac=ROUTER_MAC, name="CORE-SW1", infrastructure=True))
    session_of(evidence, mac=ROUTER_MAC)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = internal(db_session)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert note(outcome, "srcmac_infrastructure") == {"mac": ROUTER_MAC}


def test_one_guard_query_serves_every_match(
    db_session: Session, evidence: Evidence, selin: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The router guard counts every srcmac of a candidate lookup in one
    ``trust.srcmac_ip_counts`` call, limited to the listed interfaces and the MACs seen."""
    for n in range(3):
        session_of(evidence, f"172.20.42.{60 + n}", f"00:00:5e:00:53:{0x60 + n:02x}",
                   port=51001 + n)  # fmt: skip
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    calls: list[tuple[Any, ...]] = []
    counted = trust.srcmac_ip_counts

    def counting(*args: Any, **kwargs: Any) -> dict[tuple[str, str], int]:
        calls.append(args)
        return counted(*args, **kwargs)

    monkeypatch.setattr(engine, "srcmac_ip_counts", counting)
    outcome = candidates(db_session)
    assert len(outcome.matches) == 3
    assert all(m.chain is not None and m.chain.mac_source == "srcmac" for m in outcome.matches)
    ((_session, low, high, interfaces, _untrusted, macs),) = calls
    assert (low, high) == (AT - timedelta(hours=1), AT + timedelta(hours=1))
    assert tuple(interfaces) == ("vlan42",)
    assert sorted(macs) == [f"00:00:5e:00:53:{0x60 + n:02x}" for n in range(3)]


def test_a_shared_mac_logged_after_the_guard_window_is_still_counted(
    db_session: Session, evidence: Evidence, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The router's MAC on two addresses: .57's three-hour session spans the moment but is
    logged once, at its end two hours after it; .58's session is at the moment. The one count
    covers the hour around the moment, and the row outside it adds its own address (.57 is not
    among the hour's), so the row used is counted: ``srcmac_shared`` and no srcmac chain, also
    in the internal lookup of .57."""
    session_of(evidence, mac=ROUTER_MAC, at=AT + timedelta(hours=2), duration=3 * 3600)
    session_of(evidence, "172.20.42.58", ROUTER_MAC, port=51002)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    calls: list[tuple[datetime, datetime]] = []
    counted = trust.srcmac_ip_counts

    def counting(*args: Any, **kwargs: Any) -> dict[tuple[str, str], int]:
        calls.append((args[1], args[2]))
        return counted(*args, **kwargs)

    monkeypatch.setattr(engine, "srcmac_ip_counts", counting)
    found = candidates(db_session)
    assert sorted(m.traffic.srcip for m in found.matches) == [PC, "172.20.42.58"]
    assert all(m.chain is None for m in found.matches)
    assert note(found, "srcmac_shared") == {"mac": ROUTER_MAC, "at": iso(AT), "count": 2}
    assert calls == [(AT - timedelta(hours=1), AT + timedelta(hours=1))]
    outcome = internal(db_session)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert note(outcome, "srcmac_shared") == {"mac": ROUTER_MAC, "at": iso(AT), "count": 2}


def test_another_long_session_does_not_widen_the_guard_of_a_mac(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """One candidate's long session reaches no other MAC's guard: .58's 21-hour session spans
    the moment and is logged 20 hours after it, while the PC's MAC shows on .99 ten hours after
    the moment. The PC's MAC is counted within the hour around the moment only (one address),
    so .57's lease of another MAC stays the ``low`` conflict of D10."""
    evidence.fortigate_dhcp(tr(DAY, 8), PC, OTHER, hostname="MUH-NB03")
    row = session_of(evidence)
    session_of(evidence, "172.20.42.99", PC_MAC, at=AT + timedelta(hours=10), port=51003)
    session_of(evidence, "172.20.42.58", "00:00:5e:00:53:58", at=AT + timedelta(hours=20),
               port=51002, duration=21 * 3600)  # fmt: skip
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    found = candidates(db_session)
    assert sorted(m.traffic.srcip for m in found.matches) == [PC, "172.20.42.58"]
    assert "srcmac_shared" not in keys(found)
    (match,) = (m for m in found.matches if m.traffic.srcip == PC)
    chain = match.chain
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.mac, chain.mac_source, chain.confidence) == (OTHER, "lease", "low")
    assert note(chain, "srcmac_mismatch") == {"srcmac": PC_MAC, "mac": OTHER}
    (alternative,) = chain.alternatives
    assert_srcmac_chain(alternative, row)


def test_an_address_the_hour_shows_already_is_counted_once(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """.57's own session at the moment and its three-hour session logged two hours after it,
    both with the PC's MAC: the row outside the hour adds no second address, so the MAC is
    used (one address)."""
    session_of(evidence, port=51002)
    session_of(evidence, at=AT + timedelta(hours=2), duration=3 * 3600)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = internal(db_session)
    assert "srcmac_shared" not in keys(outcome)
    chain = outcome.attribution
    assert chain is not None
    assert (chain.mac_source, chain.device and chain.device.mac) == ("srcmac", PC_MAC)


@pytest.mark.parametrize(("begins", "refused"), [(timedelta(minutes=30), True),
                                                 (timedelta(minutes=90), False)])  # fmt: skip
def test_the_other_lease_check_of_a_row_after_the_window_reads_the_hour(
    db_session: Session, evidence: Evidence, begins: timedelta, refused: bool
) -> None:
    """.57's session row is logged two hours after the moment; the other-lease check still
    reads the hour around the moment (2.9): the router's lease of .10 beginning 30 minutes
    after the moment refuses its srcmac, one beginning 90 minutes after it does not."""
    evidence.fortigate_dhcp(AT + begins, ROUTER_IP, ROUTER_MAC)
    session_of(evidence, mac=ROUTER_MAC, at=AT + timedelta(hours=2), duration=3 * 3600)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = internal(db_session)
    if refused:
        assert (outcome.mode, outcome.attribution) == ("none", None)
        assert note(outcome, "srcmac_other_lease") == {
            "mac": ROUTER_MAC,
            "at": iso(AT),
            "other_ip": ROUTER_IP,
        }
    else:
        assert "srcmac_other_lease" not in keys(outcome)
        chain = outcome.attribution
        assert chain is not None
        assert (chain.mac_source, chain.device and chain.device.mac) == ("srcmac", ROUTER_MAC)


# -- beside a lease (D10) -------------------------------------------------------------------------


def test_a_lease_with_the_same_srcmac_agrees(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """The lease's MAC is the session's srcmac: ``srcmac_agrees``, the lease chain as it was."""
    evidence.fortigate_dhcp(tr(DAY, 8), PC, PC_MAC, hostname=HOST)
    session_of(evidence)
    build(db_session, DAY, DAY)
    (before,) = candidates(db_session).matches
    listed(db_session, ["vlan42"])
    (match,) = candidates(db_session).matches
    chain = match.chain
    assert chain is not None
    assert before.chain is not None
    assert (chain.mac_source, chain.confidence, chain.alternatives) == (
        "lease",
        before.chain.confidence,
        (),
    )
    assert note(chain, "srcmac_agrees") == {"mac": PC_MAC}
    assert keys(chain) == [*keys(before.chain), "srcmac_agrees"]


def test_a_lease_with_another_srcmac_is_a_low_conflict(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """The srcmac names another MAC than the lease (a static device on a pool address): the
    lease chain first, the srcmac chain as its alternative, both ``low`` with
    ``srcmac_mismatch``."""
    evidence.fortigate_dhcp(tr(DAY, 8), PC, OTHER, hostname="MUH-NB03")
    row = session_of(evidence)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    (match,) = candidates(db_session).matches
    chain = match.chain
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.mac, chain.mac_source, chain.confidence) == (OTHER, "lease", "low")
    assert note(chain, "srcmac_mismatch") == {"srcmac": PC_MAC, "mac": OTHER}
    (alternative,) = chain.alternatives
    assert_srcmac_chain(alternative, row)
    assert note(alternative, "srcmac_mismatch") == {"srcmac": PC_MAC, "mac": OTHER}
    assert AttributionChain.from_dict(json.loads(json.dumps(chain.to_dict()))) == chain


def test_an_exact_match_beside_another_lease_is_the_low_conflict(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """D10 in the exact form: the lease of another MAC and the session's srcmac give the lease
    chain first and the srcmac chain as its alternative, both ``low`` with
    ``srcmac_mismatch``."""
    evidence.fortigate_dhcp(tr(DAY, 8), PC, OTHER, hostname="MUH-NB03")
    row = session_of(evidence)
    wan_exit(db_session)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = exact(db_session)
    assert outcome.mode == "exact"
    (match,) = outcome.matches
    chain = match.chain
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.mac, chain.mac_source, chain.confidence) == (OTHER, "lease", "low")
    assert note(chain, "srcmac_mismatch") == {"srcmac": PC_MAC, "mac": OTHER}
    (alternative,) = chain.alternatives
    assert_srcmac_chain(alternative, row)
    assert note(alternative, "srcmac_mismatch") == {"srcmac": PC_MAC, "mac": OTHER}


# -- internal lookups without a lease -------------------------------------------------------------


def test_four_srcmacs_of_one_address_name_the_count_and_three(
    db_session: Session, evidence: Evidence
) -> None:
    """Four different srcmacs from .57 within the tolerance: ``srcmac_ambiguous`` with the count
    and the first three, no chain."""
    macs = [f"00:00:5e:00:53:{0x91 + n:02x}" for n in range(4)]
    for n, mac in enumerate(macs):
        session_of(evidence, mac=mac, at=AT + timedelta(seconds=5 * n), port=51001 + n)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = internal(db_session)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert "no_lease" in keys(outcome)
    assert note(outcome, "srcmac_ambiguous") == {
        "ip": PC,
        "seconds": 60,
        "count": 4,
        "macs": ", ".join(macs[:3]),
    }


def test_a_mac_only_lookup_finds_the_address_by_its_srcmac(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """A MAC-only lookup without a lease: the rows whose srcmac is the MAC within the
    tolerance give its one address and the srcmac chain (the outcome keeps
    ``no_lease_for_mac``); two addresses give ``mac_multiple_ips`` and no chain."""
    row = session_of(evidence)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = lookup(db_session, mac=PC_MAC)
    assert outcome.mode == "internal"
    assert "no_lease_for_mac" in keys(outcome)
    assert_srcmac_chain(outcome.attribution, row)
    session_of(evidence, "172.20.42.58", at=AT + timedelta(seconds=30), port=51002)
    listed(db_session, ["vlan42"], max_ips=2)
    two = lookup(db_session, mac=PC_MAC)
    assert (two.mode, two.attribution) == ("none", None)
    assert note(two, "mac_multiple_ips") == {"mac": PC_MAC, "ips": f"{PC}, 172.20.42.58"}


def test_a_srcmac_logged_after_fifty_rows_of_another_is_still_seen(
    db_session: Session, evidence: Evidence
) -> None:
    """The rows read for an address are the nearest row of each srcmac, at most 50: fifty
    sessions of .57 with one srcmac inside the tolerance and a long session with a second
    srcmac logged at its end, two hours later, give ``srcmac_ambiguous``, not the first MAC's
    chain (the 50 oldest rows of the window hold only the first MAC)."""
    first, second = "00:00:5e:00:53:91", "00:00:5e:00:53:92"
    for n in range(50):
        session_of(evidence, mac=first, at=AT - timedelta(seconds=55 - n), port=51001 + n)
    session_of(evidence, mac=second, at=AT + timedelta(hours=2), port=52001, duration=3 * 3600)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])
    outcome = internal(db_session)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert note(outcome, "srcmac_ambiguous") == {
        "ip": PC,
        "seconds": 60,
        "count": 2,
        "macs": f"{first}, {second}",
    }


def test_an_address_after_fifty_rows_of_the_mac_is_still_seen(
    db_session: Session, evidence: Evidence
) -> None:
    """A MAC-only lookup reads the nearest row of each address, at most 50: fifty rows of the
    MAC from .57 before the moment and one from .58 after it give ``mac_multiple_ips``, not a
    srcmac chain of .57."""
    for n in range(50):
        session_of(evidence, at=AT - timedelta(seconds=55 - n), port=51001 + n)
    session_of(evidence, "172.20.42.58", at=AT + timedelta(seconds=30), port=52001)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"], max_ips=2)
    outcome = lookup(db_session, mac=PC_MAC)
    assert (outcome.mode, outcome.attribution) == ("none", None)
    assert note(outcome, "mac_multiple_ips") == {"mac": PC_MAC, "ips": f"{PC}, 172.20.42.58"}


# -- srcmac never reaches the leases or the devices -----------------------------------------------


def test_leases_and_devices_are_unchanged_after_lookups(
    db_session: Session, evidence: Evidence, selin: int
) -> None:
    """Lookups with srcmac on read only: no lease, no device (no upsert of the srcmac chain's
    MAC, of a conflict's or of a refused one)."""
    evidence.fortigate_dhcp(tr(DAY, 8), "172.20.42.58", OTHER)
    evidence.fortigate_dhcp(AT - timedelta(hours=2), ROUTER_IP, ROUTER_MAC)
    session_of(evidence)
    session_of(evidence, "172.20.42.58", "00:00:5e:00:53:a8", port=51002)
    session_of(evidence, "172.20.42.59", ROUTER_MAC, port=51003)
    session_of(evidence, "172.20.42.60", "00:00:5e:00:53:a9", port=51004)
    build(db_session, DAY, DAY)
    listed(db_session, ["vlan42"])

    def snapshot() -> tuple[list[Any], list[Any]]:
        leases = [tuple(r) for r in db_session.execute(select(Lease.__table__).order_by("id"))]
        devices = [tuple(r) for r in db_session.execute(select(Device.__table__).order_by("mac"))]
        return leases, devices

    before = snapshot()
    found = candidates(db_session)
    assert {m.traffic.srcip for m in found.matches if m.chain is not None} == {
        PC,
        "172.20.42.58",
        "172.20.42.60",
    }
    internal(db_session)
    internal(db_session, "172.20.42.60")
    lookup(db_session, mac="00:00:5e:00:53:a9")
    assert snapshot() == before
    assert db_session.get(Device, "00:00:5e:00:53:a9") is None
