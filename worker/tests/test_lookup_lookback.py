"""The look-backs of a 5651 lookup and its days (Wave 5 contract §5, G7; W5-G deviations).

A lookup reads no log row before the first of its days (:func:`.days.reads_from`): a look-back
that reaches further (the 100-day cap of the missed-move check, round 7; a FortiGate lease whose
logged lease time is longer than one reload holds, through its newest record and D27) reads from
that day on, and the answer says so (``lookback_clamped_to_window``). Before that day the
missed-move check reads the moves the lease job recorded with the lease (issue #127), and a
stored FortiGate ref keeps the VDOM of its row: both are in ``leases``, which the window job
never drops. A logged lease that one
reload holds is inside the days, so the 409 and the reload cover it. D27's read past the moment
ends with the last of the days (:func:`.days.reads_until`) without that note: a method rule
(docs/CORRELATION.md) that cuts nearly every FortiGate lookup (FortiOS's 7-day lease) and a
MikroTik lookup late in the day (#126). Either way the answer is the same with the rows outside
the lookup's days and without them (the window job dropped them, only the lookup's days were
reloaded): it never changes silently.

The site is Selin Kaya's PC (``172.20.42.57`` / ``00:00:5e:00:53:57``, VLAN 42), its leases built
with ``rebuild_day`` as the nightly job does.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import day_window
from arcilogger_db.models import (
    FgDhcp,
    FgEvent,
    FgTraffic,
    Lease,
    Message,
    MtArp,
    MtDhcp,
    Source,
    Vlan,
)
from arcilogger_db.settings import get_settings, set_setting
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import LookupOutcome, LookupRequest, LookupSettings, run_lookup
from arcilogger_worker.lookup.attribution import MISSED_MOVE_WINDOW
from arcilogger_worker.lookup.days import RELOAD_DAYS, lookup_days, reads_from, reads_until
from arcilogger_worker.lookup.refs import REF_VD, fg_dhcp_move, moves_json
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)
AT = tr(DAY, 11, 23, 41)
NOW = tr(DAY + timedelta(days=9), 9)
PC, PC_MAC = "172.20.42.57", "00:00:5e:00:53:57"
#: The first day of the lookup's days with the defaults: as far back as one reload holds.
FIRST = DAY - timedelta(days=12)
#: The last day of the lookup's days with the defaults.
LAST = DAY + timedelta(days=1)
CLAMPED = "lookback_clamped_to_window"
#: The post-lease warning of 2.7 (D8, D33) and the two-origins warning (D27).
UNCONVERTED, OVERLAP = "fortigate_dhcp_not_converted_since_lease", "dhcp_sources_overlap"
#: The log tables the window job drops (the ``leases`` it never drops).
LOG_TABLES = (Message, FgTraffic, FgDhcp, FgEvent, MtDhcp, MtArp)
#: An address in no configured network (the example site configures no 172.20.44.0/24): the
#: lease build records a move of the PC's MAC to it and does not apply it (D33).
ELSEWHERE = "172.20.44.58"
#: What a recorded move keeps of its ``fg_dhcp`` row (:func:`.refs.fg_dhcp_move`).
MOVE_ROW = (
    FgDhcp.time,
    FgDhcp.ip,
    FgDhcp.interface,
    FgDhcp.extra["vd"].astext.label("vd"),
    FgDhcp.source,
    FgDhcp.logid,
    FgDhcp.dhcp_msg,
)


def settings_for(session: Session) -> LookupSettings:
    return LookupSettings.from_settings(get_settings(session))


def asked(at: datetime = AT) -> LookupRequest:
    return LookupRequest(at=at, internal_ip=PC)


def internal(session: Session, at: datetime = AT) -> LookupOutcome:
    outcome = run_lookup(session, asked(at), settings_for(session), now=NOW)
    session.rollback()
    return outcome


def build(session: Session, evidence: Evidence, *days: date, at: datetime = AT) -> None:
    """The PC's session at the moment (the day is in the database), then the leases of
    ``days``."""
    evidence.traffic(at, PC, 51001, "203.0.113.10", 443, duration=10)
    session.commit()
    for day in days:
        rebuild_day(session, day, horizon=NOW)
        session.commit()


def drop_before_the_days(session: Session) -> None:
    """The database as a reload of the lookup's days leaves it: no log row before them."""
    start = reads_from(asked(), settings_for(session))
    assert start == day_window(lookup_days(asked(), settings_for(session))[0])[0]
    for model in LOG_TABLES:
        session.execute(delete(model).where(model.time < start))
    session.commit()


def drop_after_the_days(session: Session, at: datetime = AT) -> None:
    """The database as a reload of the lookup's days leaves it when the days after them are
    older than the window too: no log row after them."""
    end = reads_until(asked(at), settings_for(session))
    assert end < day_window(lookup_days(asked(at), settings_for(session))[1])[1]
    assert end + timedelta(microseconds=1) == day_window(LAST)[1]
    for model in LOG_TABLES:
        session.execute(delete(model).where(model.time > end))
    session.commit()


def answer(outcome: LookupOutcome) -> dict[str, Any]:
    """The stored outcome without the database's oldest day (a drop moves it by design)."""
    data: dict[str, Any] = json.loads(json.dumps(outcome.to_dict()))
    data["data_window"].pop("db_first_day")
    return data


def keys(outcome: LookupOutcome) -> list[str]:
    return [e.key for e in outcome.explanations]


def clamped_day(outcome: LookupOutcome) -> Any:
    return next(e for e in outcome.explanations if e.key == CLAMPED).params["day"]


def clamped_days(outcome: LookupOutcome) -> dict[str, Any]:
    return dict(next(e for e in outcome.explanations if e.key == CLAMPED).params)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def same_without_the_rows_outside_the_days(
    session: Session, before: LookupOutcome, at: datetime = AT
) -> None:
    """The answer without the rows before and after the lookup's days (they can be dropped
    while only the lookup's days are reloaded) is ``before``."""
    drop_before_the_days(session)
    drop_after_the_days(session, at)
    after = answer(internal(session, at))
    expected = answer(before)
    # Here the newest day of the database goes too (in the field it is today's).
    for data in (after, expected):
        data["data_window"].pop("db_last_day")
    assert after == expected


def record_moves(session: Session, lease: Lease) -> None:
    """Store ``lease.moves`` as the lease job records it (issue #127): every ACK (a logid mapped
    to ``assigned``) of the lease's MAC for another address inside the lease (on its interface
    for a FortiGate lease), one group per address, interface, VDOM, sender, logid and message
    (:func:`.refs.fg_dhcp_move`, the job's own constructor). Only for a lease a test opens by
    hand, which the job never followed; the tests move the MAC to :data:`ELSEWHERE` only, in no
    configured network, so the job would have recorded each of those ACKs, not applied it
    (D33), and so few that its bound drops none.
    :func:`test_a_recorded_move_of_a_fortigate_lease_needs_its_interface_and_vdom` keeps it
    equal to what a job run writes."""
    assigned = sorted(
        logid
        for logid, meaning in settings_for(session).fortigate_logids.items()
        if meaning == "assigned"
    )
    query = select(*MOVE_ROW).where(
        FgDhcp.mac == lease.mac,
        FgDhcp.ip != lease.ip,
        FgDhcp.time > lease.start,
        FgDhcp.logid.in_(assigned),
    )
    if lease.end is not None:
        query = query.where(FgDhcp.time < lease.end)
    if lease.source_table == "fg_dhcp":
        query = query.where(FgDhcp.interface == lease.server)
    rows = session.execute(query).all()
    assert {str(row.ip) for row in rows} <= {ELSEWHERE}
    moves = moves_json(fg_dhcp_move(row) for row in rows)
    session.execute(update(Lease).where(Lease.id == lease.id).values(moves=moves))


def kept_open(session: Session, evidence: Evidence) -> Lease:
    """The PC's MikroTik lease of 120 days ago, open: traffic kept it alive (its last evidence
    an hour before the moment), so its missed-move check reads 100 days back."""
    ack = AT - timedelta(days=120)
    evidence.dhcp(ack, "assigned", PC, PC_MAC, server="dhcp42")
    build(session, evidence, ack.date())
    lease = session.scalars(select(Lease)).one()
    lease.start, lease.end, lease.end_reason = ack.astimezone(UTC), None, "open"
    lease.last_evidence = AT - timedelta(hours=1)
    session.flush()
    return lease


def test_the_defaults_reach_back_as_far_as_one_reload_holds(db_session: Session) -> None:
    first, last = lookup_days(asked(), settings_for(db_session))
    assert (first, last) == (FIRST, LAST)
    assert (last - first).days + 1 == RELOAD_DAYS


@pytest.mark.parametrize(
    ("days", "recorded", "lowered"),
    [(20, True, True), (20, False, False), (10, True, True), (10, False, True)],
    ids=[
        "move-before-the-days",
        "move-before-the-days-not-recorded",
        "move-inside-the-days",
        "move-inside-the-days-not-recorded",
    ],
)
def test_the_missed_move_check_reads_the_recorded_moves_before_the_first_day(
    db_session: Session, evidence: Evidence, days: int, recorded: bool, lowered: bool
) -> None:
    """Round 7 and issue #127: a MikroTik lease that traffic keeps open has its last DHCP
    record 120 days back; its missed-move check reads 100 days: the rows from the first day of
    the lookup's days on, and the moves the lease job recorded with the lease before it. A
    converted ACK of the MAC for an address in no configured network (recorded by the build,
    not applied) lowers the chain inside the days, and before them when it was recorded; a
    lease without recorded moves (written before the upgrade) reads only the days there (the
    chain stays ``high``). The window reaches before the first day either way, so every answer
    names the day, and the answer is the same once the rows before the days are dropped."""
    ack = AT - timedelta(days=120)
    evidence.dhcp(ack, "assigned", PC, PC_MAC, server="dhcp42")
    build(db_session, evidence, ack.date())
    lease = db_session.scalars(select(Lease)).one()
    start = ack.astimezone(UTC)
    lease.start, lease.end, lease.end_reason = start, None, "open"
    lease.last_evidence = AT - timedelta(hours=1)  # the traffic of yesterday and today
    evidence.fortigate_dhcp(AT - timedelta(days=days), ELSEWHERE, PC_MAC)
    if recorded:
        record_moves(db_session, lease)
    else:
        db_session.execute(update(Lease).values(moves=None))
    db_session.commit()

    before = internal(db_session)
    chain = before.attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.source_table == "mt_dhcp"
    assert clamped_day(before) == FIRST.isoformat()
    if lowered:
        assert (chain.confidence, chain.lease.confidence) == ("low", "low")
        assert dict(next(e for e in chain.explanations if e.key == UNCONVERTED).params) == {
            "last": iso(start),
            "ip": PC,
        }
    else:
        assert (chain.confidence, chain.lease.confidence) == ("high", "high")
    text = next(e for e in before.explanations if e.key == CLAMPED)
    assert f"{FIRST:%d.%m.%Y}" in text.text_tr
    assert f"{FIRST:%d.%m.%Y}" in text.text_en

    drop_before_the_days(db_session)
    assert answer(internal(db_session)) == answer(before)


#: Today's settings and networks (``key``, value) against which a recorded move counts no more.
TODAY = {
    "as-recorded": None,
    "sender-untrusted": None,
    "logid-unmapped": (
        "dhcp.fortigate_logids",
        {"0100026003": "assigned", "0100026002": "deassigned"},
    ),
    "logid-deassigned": (
        "dhcp.fortigate_logids",
        {"0100026003": "assigned", "0100026001": "deassigned", "0100026002": "deassigned"},
    ),
    "message-not-listed": ("dhcp.fortigate_dhcp_msgs", {"0100026001": ["DHCPACK"]}),
    "interface-excluded": ("dhcp.fortigate_interfaces_excluded", ["vlan44"]),
    "network-entered": None,
}


@pytest.mark.parametrize("case", list(TODAY))
def test_a_recorded_move_is_tested_again_with_todays_settings(
    db_session: Session, evidence: Evidence, case: str
) -> None:
    """Issue #127: the lease job records a kept ACK under the settings of the build; the lookup
    tests it again with today's, as it tests a row: a sender untrusted since (D24), a logid no
    longer mapped to ``assigned`` (unmapped, or mapped to ``deassigned``), a ``dhcp_msg`` no
    longer listed (D25), an interface excluded since (D28), or the address's network entered
    since, another one than the lease's (D33's key), and the move counts no more. As recorded,
    it lowers the chain."""
    lease = kept_open(db_session, evidence)
    sender = "unknown-192.0.2.7" if case == "sender-untrusted" else "fortigate"
    moved = AT - timedelta(days=20)
    evidence.fortigate_dhcp(moved, ELSEWHERE, PC_MAC, interface="vlan44", source=sender)
    record_moves(db_session, lease)
    db_session.commit()
    stored = db_session.scalar(select(Lease.moves))
    assert stored is not None
    assert stored["groups"] != []
    if case == "sender-untrusted":
        db_session.add(Source(name=sender, type="fortigate", address="192.0.2.7", unknown=True))
    elif case == "network-entered":
        db_session.add(Vlan(subnet="172.20.44.0/24", vlan_id=44, name_tr="Depo"))
    elif (setting := TODAY[case]) is not None:
        set_setting(db_session, setting[0], setting[1], updated_by="tolga")
    db_session.commit()
    chain = internal(db_session).attribution
    assert chain is not None
    assert chain.lease is not None
    if case == "as-recorded":
        assert (chain.confidence, chain.lease.confidence) == ("low", "low")
        assert UNCONVERTED in [e.key for e in chain.explanations]
    else:
        assert (chain.confidence, chain.lease.confidence) == ("high", "high")
        assert UNCONVERTED not in [e.key for e in chain.explanations]


@pytest.mark.parametrize("recorded", [True, False], ids=["recorded", "not-recorded"])
@pytest.mark.parametrize(
    ("moved", "lowered"),
    [("window-start", False), ("after-the-window-start", True), ("first-day", True)],
)
def test_the_recorded_moves_reach_up_to_the_first_day(
    db_session: Session, evidence: Evidence, moved: str, lowered: bool, recorded: bool
) -> None:
    """Issue #127: the rows are read after the start of the lookup's first day, the recorded
    moves up to it and including it, from the start of the window on (excluded, as for a
    row): a move exactly at the first day's midnight lowers the chain when it was recorded, and
    is read by neither part without it; a move exactly 100 days before the moment is outside
    the window, one a microsecond later is inside it."""
    first = day_window(FIRST)[0]
    assert first == reads_from(asked(), settings_for(db_session))
    time = {
        "window-start": AT - MISSED_MOVE_WINDOW,
        "after-the-window-start": AT - MISSED_MOVE_WINDOW + timedelta(microseconds=1),
        "first-day": first,
    }[moved]
    lease = kept_open(db_session, evidence)
    evidence.fortigate_dhcp(time, ELSEWHERE, PC_MAC)
    if recorded:
        record_moves(db_session, lease)
    db_session.commit()
    chain = internal(db_session).attribution
    assert chain is not None
    assert chain.lease is not None
    if lowered and recorded:
        assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    else:
        assert (chain.confidence, chain.lease.confidence) == ("high", "high")


@pytest.mark.parametrize(
    ("interface", "vd", "stored_vd", "lowered"),
    [
        ("vlan42", "root", True, True),
        ("vlan43", "root", True, False),
        ("vlan42", "misafir", True, False),
        ("vlan42", "misafir", False, True),
    ],
    ids=["same", "interface", "vdom", "vdom-of-a-ref-without-one"],
)
def test_a_recorded_move_of_a_fortigate_lease_needs_its_interface_and_vdom(
    db_session: Session,
    evidence: Evidence,
    interface: str,
    vd: str,
    stored_vd: bool,
    lowered: bool,
) -> None:
    """D33's key for an ``fg_dhcp`` lease (2.7, round 7) over the recorded moves (issue #127):
    a FortiGate lease logged for 30 days, its ACK 14 days back (before the lookup's days, so
    its stored ref is its record), and the PC's MAC on an address in no configured network a
    day later. The lease job follows the open lease and records that ACK on the lease's
    interface only, and stores the VDOM of the lease's ref; :func:`record_moves` writes the
    same moves. Only a move on the lease's interface and in its VDOM lowers the chain; a ref
    stored before the upgrade has no VDOM, so any VDOM counts, as a row's did before."""
    ack = AT - timedelta(days=14)
    evidence.fortigate_dhcp(ack, PC, PC_MAC, lease=2_592_000, vd="root")
    evidence.fortigate_dhcp(AT - timedelta(days=13), ELSEWHERE, PC_MAC, interface=interface, vd=vd)
    build(db_session, evidence, ack.date())  # the look-ahead follows the lease up to now
    lease = db_session.scalars(select(Lease).where(Lease.ip == PC)).one()
    assert [item.get(REF_VD) for item in lease.evidence if item["table"] == "fg_dhcp"] == ["root"]
    recorded = db_session.scalar(select(Lease.moves).where(Lease.id == lease.id))
    assert recorded is not None
    assert (recorded["groups"] != []) is (interface == "vlan42")
    # The tests' copy of the job's filter writes what the job wrote.
    record_moves(db_session, lease)
    assert db_session.scalar(select(Lease.moves).where(Lease.id == lease.id)) == recorded
    if not stored_vd:  # the refs as a lease written before the upgrade holds them
        lease.evidence = [{k: v for k, v in item.items() if k != REF_VD} for item in lease.evidence]
    db_session.commit()
    before = internal(db_session)
    chain = before.attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.source_table, chain.lease.last_dhcp_evidence) == ("fg_dhcp", ack)
    if lowered:
        assert (chain.confidence, chain.lease.confidence) == ("low", "low")
        assert dict(next(e for e in chain.explanations if e.key == UNCONVERTED).params) == {
            "last": iso(ack),
            "ip": PC,
        }
    else:
        assert (chain.confidence, chain.lease.confidence) == ("high", "high")
    assert clamped_day(before) == FIRST.isoformat()

    drop_before_the_days(db_session)
    assert answer(internal(db_session)) == answer(before)


def test_the_lookup_reads_the_moves_the_lease_job_recorded(
    db_session: Session, evidence: Evidence
) -> None:
    """Issue #127 end to end, nothing stored by hand: a FortiGate lease logged for 30 days, its
    ACK 14 days back (before the lookup's days), and an ACK of the PC's MAC for an address in no
    configured network on the lease's interface and in its VDOM a day later. The lease job's
    look-ahead follows the open lease and records that ACK with it (D33 does not apply it); the
    lookup reads it there, before the first day: the chain is ``low``, named from the lease's
    stored record, the look-back is noted all the same, and the answer is the same once the
    rows before the days are dropped."""
    ack = AT - timedelta(days=14)
    evidence.fortigate_dhcp(ack, PC, PC_MAC, lease=2_592_000, vd="root")
    moved = evidence.fortigate_dhcp(
        AT - timedelta(days=13), ELSEWHERE, PC_MAC, interface="vlan42", vd="root"
    )
    build(db_session, evidence, ack.date())
    row = db_session.execute(select(*MOVE_ROW).where(FgDhcp.id == moved)).one()
    stored = db_session.scalar(select(Lease.moves).where(Lease.ip == PC))
    assert stored == moves_json([fg_dhcp_move(row)])
    assert stored["groups"] != []

    before = internal(db_session)
    chain = before.attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.source_table, chain.lease.last_dhcp_evidence) == ("fg_dhcp", ack)
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert dict(next(e for e in chain.explanations if e.key == UNCONVERTED).params) == {
        "last": iso(ack),
        "ip": PC,
    }
    assert clamped_day(before) == FIRST.isoformat()

    drop_before_the_days(db_session)
    assert answer(internal(db_session)) == answer(before)


def test_a_logged_lease_longer_than_a_reload_reads_from_the_first_day_and_says_so(
    db_session: Session, evidence: Evidence
) -> None:
    """D6 and D27: a FortiGate lease logged for 30 days, its ACK 14 days before the moment
    (before the lookup's days; the renewal at half the lease is not due yet), and a MikroTik
    DHCP server that served VLAN 42 25 days before it. The lease's newest record and the
    two-origin check would reach 30 days back; they read from the first day of the lookup's
    days (the lease's stored record decides), so neither DHCP server is read and the chain stays
    ``high`` (with the rows before the days: ``low``, two origins), the answer names the day,
    and it is the same without the rows before the days."""
    ack = AT - timedelta(days=14)
    evidence.fortigate_dhcp(ack, PC, PC_MAC, lease=2_592_000)
    other = AT - timedelta(days=25)
    evidence.dhcp(other, "assigned", "172.20.42.88", "00:00:5e:00:53:88", server="dhcp42")
    build(db_session, evidence, other.date(), ack.date())
    before = internal(db_session)
    chain = before.attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.source_table, chain.lease.lease_seconds) == ("fg_dhcp", 2_592_000)
    assert chain.lease.last_dhcp_evidence == ack
    assert (chain.confidence, chain.lease.confidence) == ("high", "high")
    assert OVERLAP not in [e.key for e in chain.explanations]
    # The look-back cut names the lookup's days (D27's forward cut, here too, adds nothing).
    assert clamped_days(before) == {"day": FIRST.isoformat(), "last": LAST.isoformat()}
    assert keys(before).count(CLAMPED) == 1

    drop_before_the_days(db_session)
    assert answer(internal(db_session)) == answer(before)


def test_a_logged_lease_one_reload_holds_is_read_inside_the_days(
    db_session: Session, evidence: Evidence
) -> None:
    """D6: a FortiGate lease logged for 10 days, its ACK 9 days before the moment (before the
    eight days the configured rules alone reach): the lookup's days hold it, so the 409 and the
    reload cover it, its record is read inside them (the missing renewal at half the lease is
    named from it, D6), and no look-back is clamped. Only the two-origin check's forward read
    (``T`` + 10 days, D27) ends at the last of the days, a method rule: no note (#126)."""
    ack = AT - timedelta(days=9)
    evidence.fortigate_dhcp(ack, PC, PC_MAC, lease=864_000)
    build(db_session, evidence, ack.date())
    first, _ = lookup_days(asked(), settings_for(db_session))
    assert first <= ack.date()
    without = LookupSettings(use_logged_lease=False)
    assert lookup_days(asked(), without)[0] > ack.date()  # the rules alone: eight days

    before = internal(db_session)
    chain = before.attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.lease_seconds, chain.lease.last_dhcp_evidence) == (864_000, ack)
    renewal = next(e for e in chain.explanations if e.key == "lease_renewal_not_seen")
    assert dict(renewal.params) == {"last": iso(ack), "lease": 864_000}
    assert CLAMPED not in keys(before)

    drop_before_the_days(db_session)
    assert answer(internal(db_session)) == answer(before)


def test_the_two_origin_check_reads_up_to_the_last_day_without_a_note(
    db_session: Session, evidence: Evidence
) -> None:
    """#126: D27 reads ``[T - W, T + W]``, W the alive window of the chain's lease: a FortiGate
    lease logged for 7 days (FortiOS's default lease, docs/CORRELATION.md section 1) reaches 7
    days past the moment, beyond the last of the lookup's days, while its look-back stays inside
    them. A MikroTik DHCP server that served VLAN 42 5 days after the moment is not read (the
    chain stays ``high``; with the rows after the days it would be ``low``, two origins). The
    cut is a method rule, D27 reads up to the end of the lookup's last day, so the answer carries
    no ``lookback_clamped_to_window``, and it is the same without the rows outside the days."""
    ack = AT - timedelta(days=2)
    evidence.fortigate_dhcp(ack, PC, PC_MAC, lease=604_800)
    build(db_session, evidence, ack.date())
    served = AT + timedelta(days=5)
    evidence.dhcp(served, "assigned", "172.20.42.88", "00:00:5e:00:53:88", server="dhcp42")
    db_session.commit()
    assert reads_until(asked(), settings_for(db_session)) < served  # the forward cut
    before = internal(db_session)
    chain = before.attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.source_table, chain.lease.lease_seconds) == ("fg_dhcp", 604_800)
    assert (chain.confidence, chain.lease.confidence) == ("high", "high")
    assert OVERLAP not in [e.key for e in chain.explanations]
    assert CLAMPED not in keys(before)

    same_without_the_rows_outside_the_days(db_session, before)


def test_a_mikrotik_lookup_late_in_the_day_reads_up_to_the_last_day_without_a_note(
    db_session: Session, evidence: Evidence
) -> None:
    """#126: a MikroTik lease's alive window with the defaults (``lease.default_seconds``, 1 day,
    x ``lease.timeout_percent``, 110) reaches past the end of the next day for a moment after
    21:36. A FortiGate DHCP ACK in VLAN 42 26 h 10 min after a moment at 22:00 is inside that
    window and after the last of the lookup's days: not read, no note, the same answer without
    the rows outside the days."""
    moment = tr(DAY, 22)
    ack = moment - timedelta(hours=1)
    evidence.dhcp(ack, "assigned", PC, PC_MAC, server="dhcp42")
    build(db_session, evidence, ack.date(), at=moment)
    served = moment + timedelta(hours=26, minutes=10)
    evidence.fortigate_dhcp(served, "172.20.42.130", "00:00:5e:00:53:30")
    db_session.commit()
    assert lookup_days(asked(moment), settings_for(db_session)) == (FIRST, LAST)
    assert reads_until(asked(moment), settings_for(db_session)) < served
    alive = settings_for(db_session).lease_timeout_seconds("dhcp42")
    assert served < moment + timedelta(seconds=alive)
    before = internal(db_session, moment)
    chain = before.attribution
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.source_table == "mt_dhcp"
    assert (chain.confidence, chain.lease.confidence) == ("high", "high")
    assert OVERLAP not in [e.key for e in chain.explanations]
    assert CLAMPED not in keys(before)

    same_without_the_rows_outside_the_days(db_session, before, moment)


@pytest.mark.parametrize("vd", [True, False], ids=["ref-with-vdom", "ref-without-vdom"])
def test_a_newest_record_before_the_days_is_the_stored_one(
    db_session: Session, evidence: Evidence, vd: bool
) -> None:
    """2.7 and D33: the lease's newest record lies before the lookup's days, so the lookup takes
    the lease's stored record, and is not read from its row. The lease job stores the VDOM of
    the row with the ref (issue #127), so an ACK of the PC's MAC for another address in VLAN 42,
    logged in another VDOM inside the days, is skipped as it is when the record is the row: the
    chain stays ``high``. A ref stored before the upgrade has no VDOM: such an ACK lowers the
    chain (any VDOM counts), as before."""
    ack = AT - timedelta(days=14)
    evidence.fortigate_dhcp(ack, PC, PC_MAC, lease=2_592_000, vd="root")
    evidence.fortigate_dhcp(AT - timedelta(days=3), "172.20.42.130", PC_MAC, vd="misafir")
    build(db_session, evidence, ack.date())
    lease = db_session.scalars(select(Lease).where(Lease.ip == PC)).one()
    # The ref as the lease job stores it (the VDOM of its row; the ACK in the other VDOM did not
    # end the lease), and as a lease written before the upgrade holds it (none).
    assert lease.end is None
    assert [item.get(REF_VD) for item in lease.evidence if item["table"] == "fg_dhcp"] == ["root"]
    if not vd:
        lease.evidence = [{k: v for k, v in item.items() if k != REF_VD} for item in lease.evidence]
    db_session.commit()
    before = internal(db_session)
    chain = before.attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.source_table, chain.lease.last_dhcp_evidence) == ("fg_dhcp", ack)
    if vd:
        assert (chain.confidence, chain.lease.confidence) == ("high", "high")
        assert UNCONVERTED not in [e.key for e in chain.explanations]
    else:
        assert (chain.confidence, chain.lease.confidence) == ("low", "low")
        assert dict(next(e for e in chain.explanations if e.key == UNCONVERTED).params) == {
            "last": iso(ack),
            "ip": PC,
        }
    # The lease's newest record is read from its stored refs: the look-back was cut.
    assert clamped_day(before) == FIRST.isoformat()
    # The VDOM is the lease's, never the answer's.
    assert f'"{REF_VD}"' not in json.dumps([ref.to_dict() for ref in chain.lease.evidence])

    drop_before_the_days(db_session)
    assert answer(internal(db_session)) == answer(before)


def test_a_newest_record_before_the_days_alone_says_so(
    db_session: Session, evidence: Evidence
) -> None:
    """The lease's newest record alone reaches before the lookup's days: a FortiGate lease
    logged for one day, its ACK 20 days before the moment, kept open by traffic (as the
    missed-move case above). D27 reads one day and a tenth back (inside the days), the lease
    has no RELEASE, and the missing renewal at half the lease (D6) makes the chain ``low``
    before the post-lease checks, which then look at nothing. So only the newest record's read
    is cut at the first day: the stored record decides, and the answer names the days in the
    explanation's TR/EN text (looking back before the first day)."""
    ack = AT - timedelta(days=20)
    evidence.fortigate_dhcp(ack, PC, PC_MAC, lease=86_400)
    build(db_session, evidence, ack.date())
    lease = db_session.scalars(select(Lease)).one()
    lease.end, lease.end_reason = None, "open"
    lease.last_evidence = AT - timedelta(hours=1)  # the traffic of yesterday and today
    db_session.commit()
    settings = settings_for(db_session)
    alive = timedelta(seconds=settings.lease_timeout_seconds(lease.server, 86_400))
    assert AT - alive > reads_from(asked(), settings)  # D27 reads inside the days

    before = internal(db_session)
    chain = before.attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.source_table, chain.lease.last_dhcp_evidence) == ("fg_dhcp", ack)
    assert (chain.confidence, chain.lease.confidence) == ("low", "low")
    assert "lease_renewal_not_seen" in [e.key for e in chain.explanations]
    assert UNCONVERTED not in [e.key for e in chain.explanations]
    assert clamped_days(before) == {"day": FIRST.isoformat(), "last": LAST.isoformat()}
    text = next(e for e in before.explanations if e.key == CLAMPED)
    assert "bu sorgunun okuduğu ilk günden daha geriye bakardı" in text.text_tr
    assert f"{FIRST:%d.%m.%Y} ile {LAST:%d.%m.%Y} arasındaki" in text.text_tr
    assert "daha eski kayıtlar denetime girmedi" in text.text_tr
    assert "would look back before the first day this lookup reads" in text.text_en
    assert "older records were not part of the check" in text.text_en

    drop_before_the_days(db_session)
    assert answer(internal(db_session)) == answer(before)
