"""Late evidence (review finding: an ingest backlog older than yesterday was never turned into
leases, so a 5651 lookup answered "no lease" for a day that is in the database).

Detection compares ``ingest_offsets`` line counts per raw day with the counts each rebuild of a
complete day started from (setting ``worker.leases_ingest_seen``)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_db.models import JobRun, Lease
from arcilogger_db.settings import get_settings
from arcilogger_worker.correlation.late import SEEN_KEY, find_late_days, get_seen, ingest_lines
from arcilogger_worker.correlation.leases import rebuild_days, run_incremental, run_late_evidence
from arcilogger_worker.correlation.watermark import set_watermark
from arcilogger_worker.lookup import LookupRequest, LookupSettings, run_lookup
from arcilogger_worker.service import Worker, default_jobs
from arcilogger_worker.testing import Evidence, tr

FRIDAY = date(2026, 10, 9)
SATURDAY = FRIDAY + timedelta(days=1)
MONDAY = FRIDAY + timedelta(days=3)
PC, A = "172.20.43.120", "f0:1f:af:00:00:0a"
OTHER, OTHER_MAC = "172.20.43.200", "f0:1f:af:00:00:ff"


def d(day: int) -> date:
    return date(2026, 10, day)


def test_find_late_days() -> None:
    today = d(12)
    seen = {d(9): 10, d(10): 0, d(11): 5}
    current = {d(8): 3, d(9): 10, d(10): 4, d(11): 5, d(12): 99}
    # d(10) grew; d(8) was never recorded and is older than yesterday; today never counts.
    assert find_late_days(current, seen, today, earliest=d(1)) == [d(8), d(10)]
    # Yesterday without a record: the nightly rebuild has not run yet — not late.
    assert find_late_days({d(11): 7}, {}, today, earliest=d(1)) == []
    # Fewer lines than recorded (re-ingest from scratch) is not "late"; beyond retention ignored.
    assert find_late_days({d(9): 2, d(1): 50}, {d(9): 10}, today, earliest=d(2)) == []


def test_backlog_of_an_older_day_is_rebuilt(db_session: Session, evidence: Evidence) -> None:
    evidence.dhcp(tr(FRIDAY, 7), "assigned", OTHER, OTHER_MAC)
    set_watermark(db_session, tr(MONDAY, 8))
    db_session.commit()
    # The nightly rebuilds of the weekend recorded what they saw: nothing on Saturday.
    rebuild_days(db_session, FRIDAY, MONDAY)
    seen = get_seen(db_session)
    assert seen is not None
    assert seen[FRIDAY] == 1
    assert seen[SATURDAY] == 0
    assert MONDAY not in seen  # today is never complete
    assert run_late_evidence(db_session).late_days == []

    # Monday 08:00: the ingest service catches up with Saturday.
    evidence.dhcp(tr(SATURDAY, 10), "assigned", PC, A, hostname="PC-A")
    evidence.traffic(tr(SATURDAY, 10, 30), PC, 40000)
    evidence.dhcp(tr(SATURDAY, 18), "deassigned", PC, A)
    db_session.commit()
    before = run_lookup(
        db_session,
        LookupRequest(at=tr(SATURDAY, 12), internal_ip=PC),
        LookupSettings.from_settings(get_settings(db_session)),
        now=tr(MONDAY, 8, 5),
    )
    assert before.mode == "none"  # the problem: data in the database, no lease

    report = run_late_evidence(db_session, now=tr(MONDAY, 8, 5))
    db_session.commit()
    assert report.late_days == [SATURDAY.isoformat()]
    assert report.days == [d(day).isoformat() for day in (9, 10, 11, 12)]  # Friday … Monday
    lease = db_session.scalars(select(Lease).where(Lease.ip == PC)).one()
    assert (lease.start, lease.end, lease.end_reason) == (
        tr(SATURDAY, 10),
        tr(SATURDAY, 18),
        "dhcp",
    )
    after = run_lookup(
        db_session,
        LookupRequest(at=tr(SATURDAY, 12), internal_ip=PC),
        LookupSettings.from_settings(get_settings(db_session)),
        now=tr(MONDAY, 8, 15),
    )
    assert after.mode == "internal"
    assert after.attribution is not None
    assert after.attribution.lease is not None
    assert after.attribution.lease.mac == A
    seen = get_seen(db_session)
    assert seen is not None
    assert seen[SATURDAY] == ingest_lines(db_session)[SATURDAY] == 3
    assert run_late_evidence(db_session).late_days == []  # done


def test_first_run_only_records(db_session: Session, evidence: Evidence) -> None:
    """A database whose leases were built before this check existed: record, do not rebuild."""
    evidence.dhcp(tr(FRIDAY, 7), "assigned", OTHER, OTHER_MAC)
    set_watermark(db_session, tr(MONDAY, 8))
    db_session.commit()
    assert get_seen(db_session) is None
    report = run_late_evidence(db_session)
    db_session.commit()
    assert (report.stats["initialised"], report.days) == (1, [])
    assert get_seen(db_session) == {FRIDAY: 1}
    assert db_session.scalars(select(Lease)).all() == []


def test_without_watermark_the_catch_up_does_the_work(
    db_session: Session, evidence: Evidence
) -> None:
    """Before the incremental job ran there is nothing to compare; its catch-up rebuilds every
    day and records the complete ones."""
    evidence.dhcp(tr(FRIDAY, 7), "assigned", OTHER, OTHER_MAC)
    db_session.commit()
    report = run_late_evidence(db_session)
    assert (report.late_days, report.days, get_seen(db_session)) == ([], [], None)
    run_incremental(db_session, now=tr(SATURDAY, 12))
    db_session.commit()
    assert get_seen(db_session) == {FRIDAY: 1}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_the_service_picks_up_a_weekend_backlog(
    db_engine: Engine, db_session: Session, evidence: Evidence, layout: Layout
) -> None:
    """The reviewer's scenario end to end: the worker runs through the weekend while the ingest
    service is down; on Monday the backlog arrives and is correlated without manual repair."""
    evidence.dhcp(tr(FRIDAY, 7), "assigned", OTHER, OTHER_MAC)
    db_session.commit()
    clock = Clock(tr(FRIDAY, 7, 10))
    worker = Worker(engine=db_engine, layout=layout, jobs=default_jobs(60), clock=clock)
    step = timedelta(minutes=20)
    while clock.now < tr(MONDAY, 8):
        worker.tick()
        clock.now += step
    evidence.dhcp(tr(SATURDAY, 10), "assigned", PC, A, hostname="PC-A")
    evidence.traffic(tr(SATURDAY, 10, 30), PC, 40000)
    evidence.dhcp(tr(SATURDAY, 18), "deassigned", PC, A)
    db_session.commit()
    while clock.now < tr(MONDAY, 8, 30):
        worker.tick()
        clock.now += step
    db_session.expire_all()
    leases = db_session.scalars(select(Lease).where(Lease.ip == PC)).all()
    assert [(row.start, row.end_reason) for row in leases] == [(tr(SATURDAY, 10), "dhcp")]
    runs = db_session.scalars(
        select(JobRun).where(JobRun.job == "leases.late_evidence").order_by(JobRun.id)
    ).all()
    assert any(run.details.get("late_days") == [SATURDAY.isoformat()] for run in runs)
    assert all(run.status == "ok" for run in runs)
    outcome = run_lookup(
        db_session,
        LookupRequest(at=tr(SATURDAY, 12), internal_ip=PC),
        LookupSettings.from_settings(get_settings(db_session)),
        now=clock.now,
    )
    assert outcome.mode == "internal"
    assert SEEN_KEY in get_settings(db_session)


def test_late_fortigate_dhcp_lines_rebuild_their_day(
    db_session: Session, evidence: Evidence
) -> None:
    """ADR-0016: FortiGate DHCP lines that ingest loads late are counted per raw day like every
    other line, so their day is rebuilt and they become leases."""
    evidence.fortigate_dhcp(tr(FRIDAY, 7), "172.20.42.90", "00:00:5e:00:53:90")
    set_watermark(db_session, tr(MONDAY, 8))
    db_session.commit()
    rebuild_days(db_session, FRIDAY, MONDAY)
    assert run_late_evidence(db_session).late_days == []
    evidence.fortigate_dhcp(tr(SATURDAY, 10), "172.20.42.57", "00:00:5e:00:53:57",
                            hostname="PLN-PC14")  # fmt: skip
    evidence.fortigate_dhcp(tr(SATURDAY, 18), "172.20.42.57", "00:00:5e:00:53:57",
                            logid="0100026002", dhcp_msg="Release", lease=None)  # fmt: skip
    db_session.commit()
    report = run_late_evidence(db_session, now=tr(MONDAY, 8, 5))
    db_session.commit()
    assert report.late_days == [SATURDAY.isoformat()]
    lease = db_session.scalars(select(Lease).where(Lease.ip == "172.20.42.57")).one()
    assert (lease.start, lease.end, lease.end_reason, lease.source_table) == (
        tr(SATURDAY, 10), tr(SATURDAY, 18), "dhcp", "fg_dhcp",
    )  # fmt: skip
