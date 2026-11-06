"""Canlı Durum's rolling snapshot (:mod:`arcilogger_worker.activity`; Wave 5 contract §6.2 M2, the
M4 decision on #201) on the golden usage site (:mod:`arcilogger_worker.usage.testing`): it counts
as the usage engine does, an interim log's delta at its own minute, a row committed late once,
and its file holds no name and no URL."""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.layout import Layout
from arcilogger_db.models import FgTraffic, JobRun, Person
from arcilogger_db.settings import set_setting
from arcilogger_worker.activity import Runner, Snapshot
from arcilogger_worker.testing import Evidence, tr
from arcilogger_worker.usage.engine import compute_day
from arcilogger_worker.usage.testing import DAY, NEXT, PC1, build_golden_site

#: An address without a lease: its subject is ``ip:<address>``.
LONE = "172.20.43.50"


def warm(snapshot: Snapshot, session: Session, now: datetime) -> list[dict[str, Any]]:
    """Ticks at ``now`` until the warm-up has covered the snapshot; their details."""
    ticks = []
    for _ in range(2000):
        ticks.append(snapshot.tick(session, now))
        if not ticks[-1]["warming_up"]:
            return ticks
    raise AssertionError("the snapshot never covered its minutes")


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def counts(data: dict[str, Any], key: str, moment: datetime) -> tuple[Any, ...] | None:
    """The subject's download, upload and sessions started in the file's minute at
    ``moment``."""
    for entry in data["minutes"]:
        if datetime.fromisoformat(entry["minute"]) == moment.astimezone(UTC):
            found = entry["subjects"].get(key)
            return None if found is None else tuple(found[:3])
    return None


def test_over_a_whole_day_the_bytes_are_compute_days(db_session: Session, layout: Layout) -> None:
    """The snapshot over the golden day (and the half hour after it, where the session over
    midnight ends) has, per subject and hour, the bytes of the day's rollup, and per subject the
    bytes of each foreground destination (as counted; the file rounds each minute)."""
    build_golden_site(db_session, layout)
    db_session.commit()
    snapshot = Snapshot(layout.activity, minutes=24 * 60 + 31, warmup_slice=timedelta(hours=1))
    warm(snapshot, db_session, tr(NEXT, 0, 30))
    result = compute_day(db_session, DAY)
    lo, hi = result.lo.timestamp(), result.hi.timestamp()

    hourly: defaultdict[tuple[str, datetime], list[float]] = defaultdict(lambda: [0.0, 0.0])
    domains: defaultdict[tuple[str, str], float] = defaultdict(float)
    for minute, subjects in snapshot.merged().cells.items():
        if not lo <= minute < hi:
            continue
        hour = datetime.fromtimestamp(minute, UTC).replace(minute=0)
        for key, (down, up, volumes, _) in subjects.items():
            hourly[(key, hour)][0] += down
            hourly[(key, hour)][1] += up
            for (domain, _kind), volume in volumes.items():
                domains[(key, domain)] += volume
    expected_hourly: defaultdict[tuple[str, datetime], list[int]] = defaultdict(lambda: [0, 0])
    for row in result.hourly:
        expected_hourly[(row["subject"], row["hour"])][0] += row["download_bytes"]
        expected_hourly[(row["subject"], row["hour"])][1] += row["upload_bytes"]
    expected_domains: defaultdict[tuple[str, str], int] = defaultdict(int)
    for row in result.domains:
        if not row["background"]:
            expected_domains[(row["subject"], row["domain"])] += (
                row["download_bytes"] + row["upload_bytes"]
            )

    assert {key: [round(v) for v in values] for key, values in hourly.items() if any(values)} == {
        key: values for key, values in expected_hourly.items() if any(values)
    }
    assert {key: round(volume) for key, volume in domains.items()} == dict(expected_domains)
    assert sum(values[0] for values in hourly.values()) == 734500  # the golden company's download


def test_an_interim_delta_lands_at_its_own_minute(db_session: Session, layout: Layout) -> None:
    """A session's logs come one by one, as in the golden video session: the first interim log
    (09:02) puts 12 000 / 1 200 bytes over 09:00-09:02 while the session is open, the second
    (09:04) 6 000 / 600 over 09:02-09:04, the final log (09:05) only the rest over 09:04-09:05;
    the minutes already counted stay as they were."""
    evidence = Evidence(db_session, layout)
    snapshot = Snapshot(layout.activity)
    key = f"ip:{LONE}"
    warm(snapshot, db_session, tr(DAY, 9, 0, 30))
    common: dict[str, Any] = {"sessionid": 7001, "dstip": "203.0.113.10"}

    evidence.traffic(
        tr(DAY, 9, 2),
        LONE,
        50001,
        interim=True,
        duration=120,
        durationdelta=120,
        rcvdbyte=12000,
        sentbyte=1200,
        rcvddelta=12000,
        sentdelta=1200,
        **common,
    )
    db_session.commit()
    assert snapshot.tick(db_session, tr(DAY, 9, 2, 30))["open_sessions"] == 1
    data = read(layout.activity)
    assert counts(data, key, tr(DAY, 9)) == (6000, 600, 1)
    assert counts(data, key, tr(DAY, 9, 1)) == (6000, 600, 0)
    assert counts(data, key, tr(DAY, 9, 2)) is None
    assert datetime.fromisoformat(data["data_until"]) == tr(DAY, 9, 2).astimezone(UTC)

    evidence.traffic(
        tr(DAY, 9, 4),
        LONE,
        50001,
        interim=True,
        duration=240,
        durationdelta=120,
        rcvdbyte=18000,
        sentbyte=1800,
        rcvddelta=6000,
        sentdelta=600,
        **common,
    )
    db_session.commit()
    snapshot.tick(db_session, tr(DAY, 9, 4, 30))
    evidence.traffic(
        tr(DAY, 9, 5),
        LONE,
        50001,
        duration=300,
        rcvdbyte=18600,
        sentbyte=1860,
        action="close",
        **common,
    )
    db_session.commit()
    assert snapshot.tick(db_session, tr(DAY, 9, 5, 30))["open_sessions"] == 0
    data = read(layout.activity)
    assert {minute: counts(data, key, tr(DAY, 9, minute)) for minute in range(6)} == {
        0: (6000, 600, 1),
        1: (6000, 600, 0),
        2: (3000, 300, 0),
        3: (3000, 300, 0),
        4: (600, 60, 0),
        5: None,
    }


def test_a_row_committed_late_is_counted_once(
    db_session: Session, db_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    """Ingest's batches commit out of id order: a row with a lower id, committed after a tick
    has counted a higher one, is still read (the id margin), and neither is counted twice. (The
    two rows are either side of midnight, so their writers do not share a spool day.)"""
    now = tr(NEXT, 0, 1, 30)
    snapshot = Snapshot(layout.activity)
    warm(snapshot, db_session, now)
    db_session.commit()
    with db_session_factory() as early, db_session_factory() as late:
        slow = Evidence(early, layout).traffic(
            tr(DAY, 23, 59, 50), LONE, 40001, rcvdbyte=1000, sentbyte=100, duration=30
        )
        fast = Evidence(late, layout).traffic(
            tr(NEXT, 0, 0, 40), LONE, 40002, rcvdbyte=2000, sentbyte=200, duration=30
        )
        assert slow < fast
        late.commit()
        assert snapshot.tick(db_session, now)["rows"] == 1
        db_session.commit()
        early.commit()
    assert snapshot.tick(db_session, now)["rows"] == 1
    db_session.commit()
    assert snapshot.tick(db_session, now)["rows"] == 0
    data = read(layout.activity)
    assert counts(data, f"ip:{LONE}", tr(DAY, 23, 59)) == (1000, 100, 1)
    assert counts(data, f"ip:{LONE}", tr(NEXT, 0)) == (2000, 200, 1)


def test_the_file_holds_no_name_and_no_url(db_session: Session, layout: Layout) -> None:
    build_golden_site(db_session, layout)
    db_session.commit()
    warm(Snapshot(layout.activity), db_session, tr(DAY, 9, 30))
    content = layout.activity.read_text(encoding="utf-8")
    data = json.loads(content)
    assert data["minutes"], "the window has traffic"
    assert ["video.example", "domain"] in data["destinations"].values()
    names = list(db_session.scalars(select(Person.name)))
    assert names
    for forbidden in [*names, "izle", "www.", "http"]:
        assert forbidden not in content
    # Only the address a person's or a device's row shows (W5-M-2); an ``ip:`` key has none.
    for key, subject in data["subjects"].items():
        assert key.split(":")[0] in {"person", "device"}
        assert set(subject) == {"ip"}
    assert data["subjects"][next(iter(data["subjects"]))]["ip"] == PC1


def test_an_unchanged_snapshot_is_not_written_again(db_session: Session, layout: Layout) -> None:
    build_golden_site(db_session, layout)
    db_session.commit()
    snapshot = Snapshot(layout.activity)
    now = tr(DAY, 9, 30)
    warm(snapshot, db_session, now)
    layout.activity.write_text("{}", encoding="utf-8")  # changed behind its back: not looked at
    assert snapshot.tick(db_session, now + timedelta(seconds=10))["written"] is False
    assert layout.activity.read_text(encoding="utf-8") == "{}"
    # The next minute moves the snapshot's range: it is written.
    assert snapshot.tick(db_session, now + timedelta(minutes=1))["written"] is True
    assert read(layout.activity)["warming_up"] is False


def test_the_warm_up_reads_in_slices_and_then_the_new_rows(
    db_session: Session, layout: Layout
) -> None:
    """After a start, the last hour and the session overlap before it are read in slices (here
    one per tick: no budget); a row committed meanwhile, in a slice already read and at most
    ``LATE_ROWS`` old, is read by its id and counted once."""
    now = tr(DAY, 12, 0, 30)
    evidence = Evidence(db_session, layout)
    evidence.traffic(tr(DAY, 11, 10), LONE, 40001, rcvdbyte=1000, sentbyte=100, duration=30)
    db_session.commit()
    slice_ = timedelta(minutes=2)
    snapshot = Snapshot(layout.activity, warmup_slice=slice_, budget=0)
    first = snapshot.tick(db_session, now)
    assert (first["warming_up"], first["slices"]) == (True, 1)
    assert read(layout.activity)["warming_up"] is True
    while snapshot.warm_from is not None and snapshot.warm_from <= tr(DAY, 11, 58):
        snapshot.tick(db_session, now)
    assert snapshot.warm_from is not None
    evidence.traffic(tr(DAY, 11, 58), LONE, 40002, rcvdbyte=2000, sentbyte=200, duration=30)
    db_session.commit()
    ticks = warm(snapshot, db_session, now)
    assert all(details["slices"] <= 1 for details in ticks)
    assert ticks[-1]["warming_up"] is False
    snapshot.tick(db_session, now)
    data = read(layout.activity)
    assert data["warming_up"] is False
    assert counts(data, f"ip:{LONE}", tr(DAY, 11, 9)) == (1000, 100, 1)
    assert counts(data, f"ip:{LONE}", tr(DAY, 11, 57)) == (2000, 200, 1)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def runs(session: Session) -> list[JobRun]:
    session.expire_all()
    return list(session.scalars(select(JobRun).where(JobRun.job == "activity.tick")))


def test_the_thread_records_its_ticks_once_a_minute(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    """The owner's decision on #201 (07:08Z): a ``job_runs`` row at most once a minute, with
    the ticks and the sessions waiting; a failed tick is logged and recorded, and the thread
    waits longer before the next one."""
    clock = Clock(tr(DAY, 12))
    runner = Runner(db_engine, layout.activity, gate=lambda now, engine: None, clock=clock)
    for second in range(0, 60, 10):
        clock.now = tr(DAY, 12, 0, second)
        assert runner.once() == "ok"
    assert runs(db_session) == []
    clock.now = tr(DAY, 12, 1)
    runner.once()
    (row,) = runs(db_session)
    assert (row.status, row.details["ticks"], row.details["failed"]) == ("ok", 7, 0)
    assert {"seconds", "ended_waiting", "stale_sessions", "warming_up"} <= set(row.details)
    assert runner.status()["thread"] is True

    def broken(*args: object, **kwargs: object) -> dict[str, Any]:
        raise RuntimeError("the database went away")

    runner.snapshot.tick = broken  # type: ignore[method-assign]
    clock.now = tr(DAY, 12, 1, 10)
    assert runner.once() == "failed"
    clock.now = tr(DAY, 12, 2, 20)
    runner.once()
    assert (runner.failures, runs(db_session)[-1].status) == (2, "failed")
    assert runs(db_session)[-1].details["error"] == "RuntimeError"
    assert runner.last_error == "RuntimeError: the database went away"


def test_the_thread_idles_while_the_module_is_off(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    """As a job of a locked module: no tick, one ``skipped`` row per site day."""
    clock = Clock(tr(DAY, 12))
    skipped = {"_skipped": True, "license": {"module": "usage_analytics", "status": "expired"}}
    runner = Runner(db_engine, layout.activity, gate=lambda now, engine: skipped, clock=clock)
    for minute in range(3):
        clock.now = tr(DAY, 12, minute)
        assert runner.once() == "skipped"
    (row,) = runs(db_session)
    assert (row.status, row.details) == (
        "skipped",
        {"license": {"module": "usage_analytics", "status": "expired"}},
    )
    assert not layout.activity.exists()
    clock.now = tr(NEXT, 12)
    runner.once()
    assert len(runs(db_session)) == 2


def test_the_thread_stops_with_the_service(db_engine: Engine, layout: Layout) -> None:
    stop = threading.Event()
    runner = Runner(
        db_engine, layout.activity, gate=lambda now, engine: None, every=timedelta(seconds=1)
    )
    thread = threading.Thread(target=runner.run, args=(stop,))
    thread.start()
    for _ in range(200):
        if runner.runs:
            break
        threading.Event().wait(0.02)
    stop.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert runner.last_status == "ok"
    assert layout.activity.exists()


def ticks(
    snapshot: Snapshot, session: Session, start: datetime, end: datetime, step: timedelta
) -> None:
    """Tick from ``start`` to ``end`` (both included) every ``step``, as the thread would."""
    moment = start
    while moment <= end:
        snapshot.tick(session, moment)
        moment += step


def total(snapshot: Snapshot, key: str) -> tuple[int, int]:
    """The subject's download and upload over the snapshot's minutes (as counted)."""
    cells = [found[key] for found in snapshot.merged().cells.values() if key in found]
    return round(sum(cell[0] for cell in cells)), round(sum(cell[1] for cell in cells))


def test_a_warm_up_across_minutes_survives_a_session_left_without_logs(
    db_session: Session, layout: Layout
) -> None:
    """B1 of #211's review: during a warm-up longer than a minute the range moves on; a session
    whose only log (interim, its final log lost) leaves the range is over, and the ticks go on."""
    now = tr(DAY, 12, 0, 30)
    evidence = Evidence(db_session, layout)
    evidence.traffic(
        tr(DAY, 10, 33), LONE, 50001, interim=True, duration=120, durationdelta=120,
        rcvdbyte=5000, sentbyte=500, rcvddelta=5000, sentdelta=500, sessionid=8001,
    )  # fmt: skip
    db_session.commit()
    snapshot = Snapshot(layout.activity, warmup_slice=timedelta(seconds=30), budget=0)
    moment = now
    for _ in range(400):
        details = snapshot.tick(db_session, moment)
        if not details["warming_up"]:
            break
        moment += timedelta(seconds=10)
    else:
        raise AssertionError("the warm-up never ended")
    assert moment - now > timedelta(minutes=3)  # the range moved on several minutes meanwhile
    assert not snapshot.open
    assert not snapshot.ended


def test_a_session_that_logs_again_after_a_pause_is_counted_once(
    db_session: Session, layout: Layout
) -> None:
    """S2 of #211's review: interim logs at 09:02 (12 000 / 1 200) and 09:45 (+6 000 / 600) with
    a pause longer than ``usage.session_overlap_seconds`` between them, then the final log: the
    session ended at the pause and logs again; its bytes are counted once, as ``compute_day``."""
    evidence = Evidence(db_session, layout)
    snapshot = Snapshot(layout.activity)
    common: dict[str, Any] = {"sessionid": 8002, "dstip": "203.0.113.10"}
    ticks(snapshot, db_session, tr(DAY, 9, 0, 30), tr(DAY, 9, 1, 30), timedelta(minutes=1))
    evidence.traffic(
        tr(DAY, 9, 2), LONE, 50002, interim=True, duration=120, durationdelta=120,
        rcvdbyte=12000, sentbyte=1200, rcvddelta=12000, sentdelta=1200, **common,
    )  # fmt: skip
    db_session.commit()
    ticks(snapshot, db_session, tr(DAY, 9, 2, 30), tr(DAY, 9, 44, 30), timedelta(minutes=1))
    assert not snapshot.open, "no log for the overlap: the session ended"
    evidence.traffic(
        tr(DAY, 9, 45), LONE, 50002, interim=True, duration=2700, durationdelta=2580,
        rcvdbyte=18000, sentbyte=1800, rcvddelta=6000, sentdelta=600, **common,
    )  # fmt: skip
    db_session.commit()
    ticks(snapshot, db_session, tr(DAY, 9, 45, 30), tr(DAY, 9, 47, 30), timedelta(minutes=1))
    evidence.traffic(
        tr(DAY, 9, 48), LONE, 50002, duration=2880, rcvdbyte=18600, sentbyte=1860,
        action="close", **common,
    )  # fmt: skip
    db_session.commit()
    ticks(snapshot, db_session, tr(DAY, 9, 48, 30), tr(DAY, 9, 50, 30), timedelta(minutes=1))
    expected = [row for row in compute_day(db_session, DAY).daily if row["subject"] == f"ip:{LONE}"]
    assert (
        total(snapshot, f"ip:{LONE}")
        == (
            sum(row["download_bytes"] for row in expected),
            sum(row["upload_bytes"] for row in expected),
        )
        == (18600, 1860)
    )


def test_a_late_interim_log_after_the_final_one_is_counted_once(
    db_session: Session, layout: Layout
) -> None:
    """The nit of #211's review: an interim log committed after its session's final log was
    counted does not open the session again, and nothing is counted twice."""
    evidence = Evidence(db_session, layout)
    snapshot = Snapshot(layout.activity)
    common: dict[str, Any] = {"sessionid": 8003, "dstip": "203.0.113.10"}
    evidence.traffic(
        tr(DAY, 9, 2), LONE, 50003, interim=True, duration=120, durationdelta=120,
        rcvdbyte=12000, sentbyte=1200, rcvddelta=12000, sentdelta=1200, **common,
    )  # fmt: skip
    evidence.traffic(
        tr(DAY, 9, 6), LONE, 50003, duration=360, rcvdbyte=18600, sentbyte=1860,
        action="close", **common,
    )  # fmt: skip
    db_session.commit()
    ticks(snapshot, db_session, tr(DAY, 9, 6, 30), tr(DAY, 9, 7, 30), timedelta(minutes=1))
    evidence.traffic(
        tr(DAY, 9, 4), LONE, 50003, interim=True, duration=240, durationdelta=120,
        rcvdbyte=18000, sentbyte=1800, rcvddelta=6000, sentdelta=600, **common,
    )  # fmt: skip
    db_session.commit()
    ticks(snapshot, db_session, tr(DAY, 9, 8, 30), tr(DAY, 9, 9, 30), timedelta(minutes=1))
    assert not snapshot.open
    assert not snapshot.ended
    assert total(snapshot, f"ip:{LONE}") == (18600, 1860)


def test_a_failed_count_keeps_the_sessions_that_ended(
    db_session: Session, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3 of #211's review: when counting the ended sessions fails (a database error), they wait
    for the next tick instead of being lost."""
    evidence = Evidence(db_session, layout)
    snapshot = Snapshot(layout.activity)
    ticks(snapshot, db_session, tr(DAY, 9, 0, 30), tr(DAY, 9, 0, 30), timedelta(minutes=1))
    evidence.traffic(tr(DAY, 9, 1), LONE, 50004, rcvdbyte=8000, sentbyte=1000, duration=30)
    db_session.commit()
    counts = snapshot._counts

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise OperationalError("SELECT", {}, Exception("the database went away"))

    monkeypatch.setattr(snapshot, "_counts", broken)
    with pytest.raises(OperationalError):
        snapshot.tick(db_session, tr(DAY, 9, 1, 30))
    db_session.rollback()
    assert len(snapshot.ended) == 1
    monkeypatch.setattr(snapshot, "_counts", counts)
    snapshot.tick(db_session, tr(DAY, 9, 1, 40))
    assert total(snapshot, f"ip:{LONE}") == (8000, 1000)


def test_a_golden_day_tick_by_tick_is_compute_day(db_session: Session, layout: Layout) -> None:
    """S4(a) of #211's review: the golden day's traffic logs committed as they come, a tick every
    two minutes with a moving clock, with a session that pauses longer than the overlap and a
    final log committed three minutes late: per subject and hour, and per destination, the
    snapshot's bytes are ``compute_day``'s."""
    build_golden_site(db_session, layout)
    evidence = Evidence(db_session, layout)
    common: dict[str, Any] = {"sessionid": 8005, "dstip": "203.0.113.10"}
    evidence.traffic(
        tr(DAY, 9, 2), LONE, 50005, interim=True, duration=120, durationdelta=120,
        rcvdbyte=12000, sentbyte=1200, rcvddelta=12000, sentdelta=1200, **common,
    )  # fmt: skip
    evidence.traffic(
        tr(DAY, 9, 45), LONE, 50005, interim=True, duration=2700, durationdelta=2580,
        rcvdbyte=18000, sentbyte=1800, rcvddelta=6000, sentdelta=600, **common,
    )  # fmt: skip
    late = evidence.traffic(
        tr(DAY, 9, 48), LONE, 50005, duration=2880, rcvdbyte=18600, sentbyte=1860,
        action="close", **common,
    )  # fmt: skip
    db_session.commit()
    columns = ", ".join(c.name for c in FgTraffic.__table__.columns if c.name != "id")
    db_session.execute(text("CREATE TABLE held_traffic AS SELECT * FROM fg_traffic"))
    db_session.execute(text("DELETE FROM fg_traffic"))
    db_session.commit()
    copy = text(
        f"INSERT INTO fg_traffic ({columns}) SELECT {columns} FROM held_traffic "  # noqa: S608
        "WHERE time >= :lo AND time < :hi AND id <> :late ORDER BY time, id"
    )
    late_copy = text(
        f"INSERT INTO fg_traffic ({columns}) SELECT {columns} FROM held_traffic "  # noqa: S608
        "WHERE id = :late"
    )
    snapshot = Snapshot(layout.activity, minutes=24 * 60 + 31)
    step = timedelta(minutes=2)
    moment, previous = tr(DAY, 0), tr(DAY - timedelta(days=1), 22)
    while moment <= tr(NEXT, 0, 30):
        db_session.execute(copy, {"lo": previous, "hi": moment, "late": late})
        if previous <= tr(DAY, 9, 51) < moment:  # 3 minutes after its time
            db_session.execute(late_copy, {"late": late})
        db_session.commit()
        snapshot.tick(db_session, moment)
        previous, moment = moment, moment + step
    db_session.execute(text("DROP TABLE held_traffic"))
    db_session.commit()
    result = compute_day(db_session, DAY)
    lo, hi = result.lo.timestamp(), result.hi.timestamp()
    hourly: defaultdict[tuple[str, datetime], list[float]] = defaultdict(lambda: [0.0, 0.0])
    domains: defaultdict[tuple[str, str], float] = defaultdict(float)
    for minute, subjects in snapshot.merged().cells.items():
        if not lo <= minute < hi:
            continue
        hour = datetime.fromtimestamp(minute, UTC).replace(minute=0)
        for key, (down, up, volumes, _) in subjects.items():
            hourly[(key, hour)][0] += down
            hourly[(key, hour)][1] += up
            for (domain, _kind), volume in volumes.items():
                domains[(key, domain)] += volume
    expected_hourly: defaultdict[tuple[str, datetime], list[int]] = defaultdict(lambda: [0, 0])
    for row in result.hourly:
        expected_hourly[(row["subject"], row["hour"])][0] += row["download_bytes"]
        expected_hourly[(row["subject"], row["hour"])][1] += row["upload_bytes"]
    expected_domains: defaultdict[tuple[str, str], int] = defaultdict(int)
    for row in result.domains:
        if not row["background"]:
            expected_domains[(row["subject"], row["domain"])] += (
                row["download_bytes"] + row["upload_bytes"]
            )
    assert {key: [round(v) for v in values] for key, values in hourly.items() if any(values)} == {
        key: values for key, values in expected_hourly.items() if any(values)
    }
    assert {key: round(volume) for key, volume in domains.items() if round(volume)} == {
        key: volume for key, volume in expected_domains.items() if volume
    }
    assert total(snapshot, f"ip:{LONE}") == (18600, 1860)


def test_a_pause_longer_than_a_short_overlap_is_counted_once(
    db_session: Session, layout: Layout
) -> None:
    """The re-review of #211: with ``usage.session_overlap_seconds`` at 30 s, a session that
    pauses 45 s ended at the pause; its next log (55 s into the session, under the 60 s of
    ``EARLIER_SECONDS``) is looked up all the same, so its bytes are counted once."""
    set_setting(db_session, "usage.session_overlap_seconds", 30, updated_by="test")
    evidence = Evidence(db_session, layout)
    snapshot = Snapshot(layout.activity)
    common: dict[str, Any] = {"sessionid": 8006, "dstip": "203.0.113.10"}
    evidence.traffic(
        tr(DAY, 9, 2), LONE, 50006, interim=True, duration=10, durationdelta=10,
        rcvdbyte=1000, sentbyte=100, rcvddelta=1000, sentdelta=100, **common,
    )  # fmt: skip
    db_session.commit()
    ticks(snapshot, db_session, tr(DAY, 9, 2, 5), tr(DAY, 9, 2, 45), timedelta(seconds=10))
    assert not snapshot.open, "no log for the 30 s overlap: the session ended"
    evidence.traffic(
        tr(DAY, 9, 2, 45), LONE, 50006, duration=55, rcvdbyte=3000, sentbyte=300,
        action="close", **common,
    )  # fmt: skip
    db_session.commit()
    ticks(snapshot, db_session, tr(DAY, 9, 2, 55), tr(DAY, 9, 3, 15), timedelta(seconds=10))
    expected = [row for row in compute_day(db_session, DAY).daily if row["subject"] == f"ip:{LONE}"]
    assert (
        total(snapshot, f"ip:{LONE}")
        == (
            sum(row["download_bytes"] for row in expected),
            sum(row["upload_bytes"] for row in expected),
        )
        == (3000, 300)
    )
