"""The look-backs of a 5651 lookup and its days (Wave 5 contract §5, G7; W5-G deviations).

A lookup reads no log row before the first of its days (:func:`.days.reads_from`): a look-back
that reaches further (the 100-day cap of the missed-move check, round 7; a FortiGate lease whose
logged lease time is longer than one reload holds, through its newest record and D27) reads from
that day on, and the answer says so (``lookback_clamped_to_window``). A logged lease that one
reload holds is inside the days, so the 409 and the reload cover it. Either way the answer is the
same with the rows before the lookup's days and without them (the window job dropped them, only
the lookup's days were reloaded): it never changes silently.

The site is Selin Kaya's PC (``172.20.42.57`` / ``00:00:5e:00:53:57``, VLAN 42), its leases built
with ``rebuild_day`` as the nightly job does.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import day_window
from arcilogger_db.models import FgDhcp, FgEvent, FgTraffic, Lease, Message, MtArp, MtDhcp
from arcilogger_db.settings import get_settings
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import LookupOutcome, LookupRequest, LookupSettings, run_lookup
from arcilogger_worker.lookup.days import RELOAD_DAYS, lookup_days, reads_from, reads_until
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


def settings_for(session: Session) -> LookupSettings:
    return LookupSettings.from_settings(get_settings(session))


def asked() -> LookupRequest:
    return LookupRequest(at=AT, internal_ip=PC)


def internal(session: Session) -> LookupOutcome:
    outcome = run_lookup(session, asked(), settings_for(session), now=NOW)
    session.rollback()
    return outcome


def build(session: Session, evidence: Evidence, *days: date) -> None:
    """The PC's session at the moment (the day is in the database), then the leases of
    ``days``."""
    evidence.traffic(AT, PC, 51001, "203.0.113.10", 443, duration=10)
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


def drop_after_the_days(session: Session) -> None:
    """The database as a reload of the lookup's days leaves it when the days after them are
    older than the window too: no log row after them."""
    end = reads_until(asked(), settings_for(session))
    assert end < day_window(lookup_days(asked(), settings_for(session))[1])[1]
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


def test_the_defaults_reach_back_as_far_as_one_reload_holds(db_session: Session) -> None:
    first, last = lookup_days(asked(), settings_for(db_session))
    assert (first, last) == (FIRST, LAST)
    assert (last - first).days + 1 == RELOAD_DAYS


@pytest.mark.parametrize(
    ("days", "lowered"),
    [(20, False), (10, True)],
    ids=["move-before-the-days", "move-inside-the-days"],
)
def test_the_missed_move_check_reads_from_the_first_day_and_says_so(
    db_session: Session, evidence: Evidence, days: int, lowered: bool
) -> None:
    """Round 7: a MikroTik lease that traffic keeps open has its last DHCP record 120 days back;
    its missed-move check would read 100 days, and reads from the first day of the lookup's
    days instead. A converted ACK of the MAC for another address before that day is not read
    (the chain stays ``high``); one inside it lowers the chain. Both answers name the day."""
    ack = AT - timedelta(days=120)
    evidence.dhcp(ack, "assigned", PC, PC_MAC, server="dhcp42")
    build(db_session, evidence, ack.date())
    lease = db_session.scalars(select(Lease)).one()
    start = ack.astimezone(UTC)
    lease.start, lease.end, lease.end_reason = start, None, "open"
    lease.last_evidence = AT - timedelta(hours=1)  # the traffic of yesterday and today
    evidence.fortigate_dhcp(AT - timedelta(days=days), "172.20.42.120", PC_MAC)
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
    assert clamped_day(before) == FIRST.isoformat()

    drop_before_the_days(db_session)
    assert answer(internal(db_session)) == answer(before)


def test_a_logged_lease_one_reload_holds_is_read_inside_the_days(
    db_session: Session, evidence: Evidence
) -> None:
    """D6: a FortiGate lease logged for 10 days, its ACK 9 days before the moment (before the
    eight days the configured rules alone reach): the lookup's days hold it, so the 409 and the
    reload cover it, its record is read inside them (the missing renewal at half the lease is
    named from it, D6), and no look-back is clamped. Only the two-origin check's forward read
    (``T`` + 10 days, D27) ends at the last of the days, and the answer names both days."""
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
    assert clamped_days(before) == {"day": FIRST.isoformat(), "last": LAST.isoformat()}

    drop_before_the_days(db_session)
    assert answer(internal(db_session)) == answer(before)


def test_the_two_origin_check_reads_up_to_the_last_day_and_says_so(
    db_session: Session, evidence: Evidence
) -> None:
    """D27 reads ``[T - W, T + W]``, W the alive window of the chain's lease: a FortiGate lease
    logged for 10 days reaches 10 days past the moment, beyond the last of the lookup's days. A
    MikroTik DHCP server that served VLAN 42 5 days after the moment is not read (the chain
    stays ``high``; with the rows after the days it would be ``low``, two origins), the answer
    names both days, and it is the same without the rows after the days (they can be dropped
    while the lookup's days are reloaded)."""
    ack = AT - timedelta(days=2)
    evidence.fortigate_dhcp(ack, PC, PC_MAC, lease=864_000)
    build(db_session, evidence, ack.date())
    evidence.dhcp(
        AT + timedelta(days=5), "assigned", "172.20.42.88", "00:00:5e:00:53:88", server="dhcp42"
    )
    db_session.commit()
    before = internal(db_session)
    chain = before.attribution
    assert chain is not None
    assert chain.lease is not None
    assert (chain.lease.source_table, chain.lease.lease_seconds) == ("fg_dhcp", 864_000)
    assert (chain.confidence, chain.lease.confidence) == ("high", "high")
    assert OVERLAP not in [e.key for e in chain.explanations]
    assert clamped_days(before) == {"day": FIRST.isoformat(), "last": LAST.isoformat()}
    text = next(e for e in before.explanations if e.key == CLAMPED)
    assert f"{LAST:%d.%m.%Y}" in text.text_tr
    assert f"{LAST:%d.%m.%Y}" in text.text_en

    drop_after_the_days(db_session)
    after = answer(internal(db_session))
    expected = answer(before)
    # Here the newest day of the database goes too (in the field it is today's).
    for data in (after, expected):
        data["data_window"].pop("db_last_day")
    assert after == expected


def test_a_newest_record_before_the_days_is_the_stored_one(
    db_session: Session, evidence: Evidence
) -> None:
    """2.7 and D33: the lease's newest record lies before the lookup's days, so the lookup takes
    the lease's stored record, which has no VDOM, and is not read from its row: an ACK of the
    PC's MAC for another address in VLAN 42 that the build missed, logged in another VDOM inside
    the days, lowers the chain (with the row's VDOM it would be skipped, and only until the
    window job dropped the row)."""
    ack = AT - timedelta(days=14)
    evidence.fortigate_dhcp(ack, PC, PC_MAC, lease=2_592_000, vd="root")
    build(db_session, evidence, ack.date())
    evidence.fortigate_dhcp(AT - timedelta(days=3), "172.20.42.130", PC_MAC, vd="misafir")
    db_session.commit()
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
