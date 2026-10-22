"""The lease timeline must not depend on how it was computed: live incremental runs, a range
rebuild and single-day rebuilds in any order give the same rows (with the same ids) and the same
alerts — and those rows are the hand-computed ones.

Regression for the review finding "seed reconstruction re-opens leases that had already closed":
a lease that closed before midnight (timeout, release) is never re-opened by a later sighting of
the same pair or by another client's traffic on the same IP; a static device with gaps keeps
separate intervals; an ARP interval whose end is decided after midnight, a release just before
midnight and late evidence of the previous day are handled exactly as a continuous run would.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from arcilogger_db.models import Alert, Lease
from arcilogger_db.settings import set_setting
from arcilogger_worker.correlation.leases import rebuild_day, rebuild_days, run_incremental
from arcilogger_worker.correlation.watermark import get_watermark, set_watermark
from arcilogger_worker.lookup import attribute
from arcilogger_worker.testing import Evidence, tr

D0 = date(2026, 10, 11)
D = D0 + timedelta(days=1)
D1 = D0 + timedelta(days=2)
D2 = D0 + timedelta(days=3)

X, Y, Z, W = "172.20.43.120", "172.20.10.237", "172.20.61.100", "172.20.43.130"
Q, V, R, U = "172.20.61.101", "172.20.43.140", "172.20.43.150", "172.20.43.15"
P = "172.20.43.160"
A, B, C = "f0:1f:af:00:00:0a", "f0:1f:af:00:00:0b", "f0:1f:af:00:00:0c"
S, T = "00:11:22:33:44:55", "00:11:22:33:44:66"
G, E, F = "02:00:5e:00:53:46", "02:00:5e:00:53:21", "02:00:5e:00:53:22"
H, J, K = "f0:1f:af:00:00:11", "f0:1f:af:00:00:12", "f0:1f:af:00:00:13"
L, M = "f0:1f:af:00:00:14", "00:11:22:33:44:77"


def scenario(ev: Evidence) -> None:
    # X: a released lease, the next client, a rogue static device (conflict), A again later.
    ev.dhcp(tr(D, 9), "assigned", X, A, hostname="PC-A")
    for k in range(10):
        ev.traffic(tr(D, 10 + k, 7), X, 40000 + k)
    ev.dhcp(tr(D1, 8), "deassigned", X, A)
    ev.dhcp(tr(D1, 9), "assigned", X, B, hostname="PC-B")
    ev.arp(tr(D1, 12), X, C)
    ev.traffic(tr(D1, 13), X, 41000)
    ev.dhcp(tr(D2, 10), "assigned", X, A)
    # Y: static device, hourly ARP-LOG with a 4-hour gap every morning.
    for hour in [*range(0, 6), *range(9, 24)]:
        ev.arp(tr(D, hour, 30), Y, S, "vlan10")
        ev.arp(tr(D1, hour, 31), Y, S, "vlan10")
    # Z: guest (1 h lease) assigned before midnight, times out after it.
    ev.dhcp(tr(D, 23), "assigned", Z, G, server="dhcp61")
    ev.arp(tr(D, 23, 40), Z, G, "vlan61")
    # W: kept alive across midnight by traffic only (same MAC as on X).
    ev.dhcp(tr(D, 22), "assigned", W, A, hostname="PC-A")
    for k in range(6):
        ev.traffic(tr(D, 22, 30) + timedelta(minutes=40 * k), W, 42000 + k)
    # Q: guest lease times out, another guest, then the first one again before midnight.
    ev.dhcp(tr(D1, 10), "assigned", Q, E, server="dhcp61")
    ev.dhcp(tr(D1, 13), "assigned", Q, F, server="dhcp61")
    ev.dhcp(tr(D1, 23), "assigned", Q, E, server="dhcp61")
    # V: release lost, the lease times out; the next client's traffic until midnight.
    ev.dhcp(tr(D0, 8), "assigned", V, H)
    ev.dhcp(tr(D, 11), "assigned", V, J)
    for hour in range(12, 24):
        ev.traffic(tr(D, hour, 50), V, 43000 + hour)
    # R: released 2 minutes before midnight; the router's stale ARP entry right after it.
    ev.dhcp(tr(D, 9), "assigned", R, K)
    ev.dhcp(tr(D, 23, 58), "deassigned", R, K)
    ev.arp(tr(D1, 0, 1), R, K)  # stale: ignored
    ev.arp(tr(D1, 1), R, K)  # a real sighting an hour later
    # U: static device whose interval is decided only after midnight (65-minute gap).
    ev.arp(tr(D, 22, 56), U, T, "vlan43")
    ev.arp(tr(D1, 0, 1), U, T, "vlan43")
    ev.arp(tr(D1, 1), U, T, "vlan43")
    # P: an IP conflict across midnight — L's lease, M (static) on the same address, and L's
    # re-assignment at 23:30, a continuation that must not end M's interval.
    ev.dhcp(tr(D, 8), "assigned", P, L)
    for hour in [*range(10, 24), *range(0, 4)]:
        ev.arp(tr(D if hour >= 10 else D1, hour), P, M)
    ev.dhcp(tr(D, 23, 30), "assigned", P, L)


def _utc(moment: datetime) -> str:
    return moment.isoformat()


EXPECTED = sorted(
    (ip, mac, start, end, reason)
    for ip, mac, start, end, reason in [
        (X, A, tr(D, 9), tr(D1, 8), "dhcp"),
        (X, B, tr(D1, 9), tr(D2, 10), "superseded"),
        (X, C, tr(D1, 12), tr(D1, 13), "arp"),
        (X, A, tr(D2, 10), None, "open"),
        (Y, S, tr(D, 0, 30), tr(D, 6, 30), "arp"),
        (Y, S, tr(D, 9, 30), tr(D1, 6, 31), "arp"),
        (Y, S, tr(D1, 9, 31), tr(D2, 0, 31), "arp"),
        (Z, G, tr(D, 23), tr(D1, 0, 46), "timeout"),
        (W, A, tr(D, 22), tr(D2, 4, 14), "timeout"),
        (Q, E, tr(D1, 10), tr(D1, 11, 6), "timeout"),
        (Q, F, tr(D1, 13), tr(D1, 14, 6), "timeout"),
        (Q, E, tr(D1, 23), tr(D2, 0, 6), "timeout"),
        (V, H, tr(D0, 8), tr(D, 10, 24), "timeout"),
        (V, J, tr(D, 11), tr(D2, 2, 14), "timeout"),
        (R, K, tr(D, 9), tr(D, 23, 58), "dhcp"),
        (R, K, tr(D1, 1), tr(D1, 2), "arp"),
        (U, T, tr(D, 22, 56), tr(D1, 2), "arp"),
        (P, L, tr(D, 8), tr(D2, 1, 54), "timeout"),
        (P, M, tr(D, 10), tr(D1, 4), "arp"),
    ]
)
CONFLICTS = sorted(
    [
        (f"ip_conflict:{X}:{B},{C}:{D1.isoformat()}", "open"),
        (f"ip_conflict:{P}:{M},{L}:{D.isoformat()}", "open"),
    ]
)
END = tr(D2, 23)


def summary(session: Session) -> list[tuple[Any, ...]]:
    session.expire_all()
    rows = session.scalars(select(Lease)).all()
    return sorted((r.ip, r.mac, r.start, r.end, r.end_reason) for r in rows)


def snapshot(session: Session) -> dict[int, tuple[Any, ...]]:
    """Every column a rebuild computes, by lease id (ids must stay stable)."""
    session.expire_all()
    return {
        r.id: (
            r.ip,
            r.mac,
            r.vlan,
            r.server,
            r.hostname,
            r.start,
            r.end,
            r.end_reason,
            r.confidence,
            r.source_table,
            r.last_evidence,
            tuple((e["table"], e["row_id"]) for e in r.evidence),
        )
        for r in session.scalars(select(Lease))
    }


def alerts(session: Session) -> list[tuple[str | None, str]]:
    return sorted((a.dedup_key, a.state) for a in session.scalars(select(Alert)))


def live(session: Session, *, step: timedelta = timedelta(minutes=7)) -> None:
    """The service: the incremental job every few minutes (window cuts at odd times)."""
    now = tr(D0, 7, 3)
    while now <= END:
        run_incremental(session, now=now)
        session.commit()
        now += step


def test_live_range_and_single_day_rebuilds_agree(db_session: Session, evidence: Evidence) -> None:
    scenario(evidence)
    db_session.commit()
    live(db_session)
    assert summary(db_session) == EXPECTED
    assert alerts(db_session) == CONFLICTS
    reference = snapshot(db_session)
    watermark = get_watermark(db_session)
    assert watermark is not None

    def check(label: str) -> None:
        assert snapshot(db_session) == reference, label
        assert alerts(db_session) == CONFLICTS, label

    # In order without look-ahead, intervals open at a midnight are written open and closed
    # again by the next day; the final rows are what counts.
    report = rebuild_days(db_session, D0, D2)
    assert (report.inserted, report.deleted) == (0, 0)
    check("range")
    for order in ([D2, D1, D, D0], [D1], [D], [D1, D0, D2, D], [D0]):
        for day in order:
            # A single day looks ahead: nothing to write when the rows are right.
            single = rebuild_day(db_session, day)
            db_session.commit()
            assert (single.inserted, single.updated, single.deleted) == (0, 0, 0), day
        check(f"single days {order}")
    assert get_watermark(db_session) == watermark  # rebuilds never move the watermark


def test_rebuild_from_scratch_in_order_gives_the_same_rows(
    db_session: Session, evidence: Evidence
) -> None:
    """Without any stored row (first catch-up, or after deleting leases) the days are rebuilt in
    order: each day's seeds are the rows of the days before it. (Single days in any order rely
    on those rows existing — on an empty table a later day cannot know an interval that started
    earlier.)"""
    scenario(evidence)
    set_watermark(db_session, END)
    db_session.commit()
    rebuild_days(db_session, D0, D2)
    assert summary(db_session) == EXPECTED
    assert alerts(db_session) == CONFLICTS
    first = snapshot(db_session)
    for day in (D2, D0, D1):  # now in any order: nothing changes
        report = rebuild_day(db_session, day)
        db_session.commit()
        assert (report.inserted, report.updated, report.deleted) == (0, 0, 0), day
    assert snapshot(db_session) == first


def test_closed_leases_are_never_reopened(db_session: Session, evidence: Evidence) -> None:
    """The reviewer's cases, through the lookup: the attribution stays the right client with
    high confidence after the nightly rebuild, and no false conflict is raised."""
    scenario(evidence)
    db_session.commit()
    live(db_session)
    rebuild_days(db_session, D, D1)  # what the nightly job does on D1 after midnight
    rebuild_days(db_session, D1, D2)
    for ip, moment, mac in (
        (V, tr(D, 15), J),  # H timed out at 10:24; J's traffic must not revive H
        (Q, tr(D1, 14), F),  # E timed out at 12:12; E's re-assignment at 23:00 is new
        (Y, tr(D, 8), None),  # the static device was not seen 06:30-09:30
    ):
        chain = attribute(db_session, ip, moment)
        if mac is None:
            assert chain is None
            continue
        assert chain is not None
        assert chain.lease is not None
        assert (chain.lease.mac, chain.lease.confidence) == (mac, "high")
        assert (chain.conflict, chain.alternatives) == (False, ())
    assert alerts(db_session) == CONFLICTS


def test_a_late_release_before_midnight_closes_the_seed(
    db_session: Session, evidence: Evidence
) -> None:
    """Stored rows say the lease crossed midnight; a release at 23:00 arrives late. Rebuilding
    only the next day already closes it at 23:00 (as the rebuild of the day itself does), keeps
    its id and does not continue it."""
    evidence.dhcp(tr(D, 20), "assigned", X, A)
    evidence.traffic(tr(D1, 1), X, 50000)
    db_session.commit()
    live_until = tr(D1, 6)
    run_incremental(db_session, now=tr(D, 19))
    db_session.commit()
    now = tr(D, 19)
    while now < live_until:
        now += timedelta(minutes=10)
        run_incremental(db_session, now=now)
        db_session.commit()
    (row_id,) = db_session.scalars(select(Lease.id))
    evidence.dhcp(tr(D, 23), "deassigned", X, A)  # ingest backlog: arrives now
    db_session.commit()
    report = rebuild_day(db_session, D1)
    db_session.commit()
    assert report.updated == 1
    assert summary(db_session) == [(X, A, tr(D, 20), tr(D, 23), "dhcp")]
    assert list(db_session.scalars(select(Lease.id))) == [row_id]
    single = snapshot(db_session)
    rebuild_days(db_session, D, D1)
    assert snapshot(db_session) == single


def test_an_arp_interval_decided_after_midnight_continues(
    db_session: Session, evidence: Evidence
) -> None:
    """Last sighting 22:55: the interval ends 23:55 unless a sighting comes by 00:01. Stored as
    closed (the service saw no sighting), then a 00:00:30 sighting arrives late: rebuilding only
    the next day continues the same row instead of opening a second one."""
    evidence.arp(tr(D, 22, 55), U, T, "vlan43")
    db_session.commit()
    run_incremental(db_session, now=tr(D, 22, 50))
    for minute in range(0, 60 * 4, 10):
        run_incremental(db_session, now=tr(D, 23) + timedelta(minutes=minute))
        db_session.commit()
    assert summary(db_session) == [(U, T, tr(D, 22, 55), tr(D, 23, 55), "arp")]
    evidence.arp(tr(D1, 0, 0, 30), U, T, "vlan43")
    db_session.commit()
    rebuild_day(db_session, D1)
    db_session.commit()
    assert summary(db_session) == [(U, T, tr(D, 22, 55), tr(D1, 1, 0, 30), "arp")]
    single = snapshot(db_session)
    rebuild_days(db_session, D, D1)
    assert snapshot(db_session) == single
    # The same interval closed by a new assignment of another MAC at 23:57 (before midnight)
    # stays closed at its claim: the release check reads the raw events before midnight.
    evidence.dhcp(tr(D, 23, 57), "assigned", U, A, server="dhcp43")
    db_session.commit()
    rebuild_days(db_session, D, D1)
    assert summary(db_session) == sorted(
        [
            (U, T, tr(D, 22, 55), tr(D, 23, 55), "arp"),
            (U, A, tr(D, 23, 57), None, "open"),
            # T seen again while A holds the address: a new interval (and an IP conflict).
            (U, T, tr(D1, 0, 0, 30), tr(D1, 1, 0, 30), "arp"),
        ]
    )
    assert [key for key, _ in alerts(db_session)] == [f"ip_conflict:{U}:{T},{A}:{D1}"]


def test_two_open_rows_of_one_pair_are_repaired(db_session: Session, evidence: Evidence) -> None:
    """Inconsistent stored rows (two open intervals of one pair — the timeline never makes
    them): the next day's rebuild keeps the newer one and closes the older at its start."""
    evidence.dhcp(tr(D, 8), "assigned", X, A)
    db_session.commit()
    rebuild_day(db_session, D, horizon=tr(D1, 12))
    db_session.add(Lease(ip=X, mac=A, start=tr(D, 6), confidence="high", source_table="mt_dhcp"))
    db_session.commit()
    report = rebuild_day(db_session, D1, horizon=tr(D1, 12))
    db_session.commit()
    assert report.updated == 1  # the older row closed; the newer one was already right
    assert report.stats["duplicate_seed"] == 1
    assert summary(db_session) == [
        (X, A, tr(D, 6), tr(D, 8), "superseded"),
        (X, A, tr(D, 8), tr(D1, 10, 24), "timeout"),  # unchanged: 08:00 + 26 h 24 min
    ]


def test_conflict_alerts_follow_the_evidence(db_session: Session, evidence: Evidence) -> None:
    """A conflict that late evidence disproves is resolved by the rebuild (marked as such); if
    later evidence brings it back, the same alert is re-opened. Lease ids stay stable."""
    evidence.dhcp(tr(D, 8), "assigned", X, A)
    evidence.arp(tr(D, 9), X, B)
    db_session.commit()
    horizon = tr(D1, 12)
    assert rebuild_day(db_session, D, horizon=horizon).alerts == 1
    db_session.commit()
    key = f"ip_conflict:{X}:{A},{B}:{D.isoformat()}"
    assert alerts(db_session) == [(key, "open")]
    ids = sorted(db_session.scalars(select(Lease.id)))

    evidence.dhcp(tr(D, 8, 30), "deassigned", X, A)  # late: A had left before B appeared
    db_session.commit()
    report = rebuild_day(db_session, D, horizon=horizon)
    db_session.commit()
    assert (report.alerts, report.resolved_alerts) == (0, 1)
    assert alerts(db_session) == [(key, "resolved")]
    alert = db_session.scalars(select(Alert)).one()
    assert alert.details["auto_resolved"]["reason"] == "lease_rebuild"
    assert sorted(db_session.scalars(select(Lease.id))) == ids  # A's row updated in place

    # A back at 09:30 (that ends B's ARP interval), B seen again at 10:00: conflict again.
    evidence.dhcp(tr(D, 9, 30), "assigned", X, A)
    evidence.arp(tr(D, 10), X, B)
    db_session.commit()
    report = rebuild_day(db_session, D, horizon=horizon)
    db_session.commit()
    assert report.alerts == 1
    assert alerts(db_session) == [(key, "open")]
    db_session.refresh(alert)
    assert "auto_resolved" not in alert.details
    assert "reopened_at" in alert.details

    # An alert a person dismissed is never touched again (neither resolved nor re-opened).
    db_session.execute(update(Alert).values(state="dismissed"))
    db_session.commit()
    assert rebuild_day(db_session, D, horizon=horizon).alerts == 0
    assert alerts(db_session) == [(key, "dismissed")]


def test_seeds_that_no_longer_reach_midnight_are_closed(
    db_session: Session, evidence: Evidence
) -> None:
    """Stored rows computed with longer lease/ARP times (the administrator shortened them):
    rebuilding the next day closes the seeds where their evidence now ends — never later."""
    evidence.dhcp(tr(D, 20), "assigned", X, A)
    evidence.arp(tr(D, 22), Y, S, "vlan10")
    evidence.arp(tr(D, 23), Y, S, "vlan10")
    db_session.commit()
    rebuild_day(db_session, D, horizon=tr(D, 23, 30))  # both still open at the horizon
    db_session.commit()
    set_setting(db_session, "lease.default_seconds", 3600, updated_by="tolga")  # x 110 % = 66 min
    set_setting(db_session, "arp.interval_seconds", 1800, updated_by="tolga")  # grace 33 min
    db_session.commit()
    report = rebuild_day(db_session, D1, horizon=tr(D1, 12))
    db_session.commit()
    assert report.stats["seeds_closed_before_midnight"] == 2
    assert summary(db_session) == sorted(
        [
            (X, A, tr(D, 20), tr(D, 21, 6), "timeout"),
            (Y, S, tr(D, 22), tr(D, 23, 30), "arp"),
        ]
    )
