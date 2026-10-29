"""``search.counts_backfill`` (contract 2.9, 3.3): only finished files, newest first, within its
time budget, idempotent, safe next to a second worker, no work on a fresh installation."""

from __future__ import annotations

import threading
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, insert, select, text, update
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_db.models import Base, IngestOffset, JobRun
from arcilogger_db.searchcounts import CountKey, count_key, rebuild_stream, upsert_counts
from arcilogger_worker import search_jobs
from arcilogger_worker.search_jobs import BACKFILL_BUDGET, backfill_counts
from arcilogger_worker.service import (
    SEARCH_COUNTS_EVERY,
    Worker,
    default_jobs,
    job_search_counts_backfill,
)

OLDER = date(2026, 9, 22)
NEWER = date(2026, 9, 23)
TODAY = date(2026, 9, 24)


def _file_lines(source: str, day: date, count: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """``count`` FortiGate web-filter lines of ``spool/<source>/<day>.log``: their ``messages``
    and ``fg_web`` rows (every fifth blocked, clients in VLAN 41 and 42)."""
    start = datetime(day.year, day.month, day.day, 6, 0, tzinfo=UTC)  # 09:00 site time
    lines = []
    for number in range(1, count + 1):
        time = start + timedelta(minutes=number)
        reference = {
            "time": time,
            "time_received": time,
            "source": source,
            "sender": "172.31.255.1",
            "raw_day": day,
            "raw_line": number,
            "raw_offset": 100 * (number - 1),
        }
        message = {**reference, "type": "utm", "subtype": "webfilter", "text": f"line {number}"}
        web = {
            **reference,
            "srcip": f"172.20.{41 + number % 2}.{100 + number % 7}",
            "action": "blocked" if number % 5 == 0 else "passthrough",
        }
        lines.append((message, web))
    return lines


def load_file(
    connection: Connection,
    source: str,
    day: date,
    count: int,
    *,
    finished: bool,
    counts_from: int | None,
) -> Counter[CountKey]:
    """A file as an upgrade leaves it: rows of ``count`` lines, its ``ingest_offsets`` row with
    ``counts_from_line = counts_from``, and the counts ingest wrote for the lines from
    ``counts_from`` on (none before: they were loaded before the summary table). Returns the
    counts of every line, what the file's keys must be once it is counted."""
    lines = _file_lines(source, day, count)
    connection.execute(insert(Base.metadata.tables["messages"]), [m for m, _ in lines])
    connection.execute(insert(Base.metadata.tables["fg_web"]), [w for _, w in lines])
    connection.execute(
        insert(IngestOffset).values(
            source=source,
            day=day,
            byte_offset=100 * count,
            line_number=count,
            finished_at=datetime(2026, 9, 25, tzinfo=UTC) if finished else None,
            counts_from_line=counts_from,
        )
    )
    first = 1 if counts_from is None else counts_from
    upsert_counts(
        connection,
        Counter(count_key(m, "fg_web", w) for m, w in lines if m["raw_line"] >= first),
    )
    return Counter(count_key(m, "fg_web", w) for m, w in lines)


def stored(engine: Engine) -> Counter[CountKey]:
    counts: Counter[CountKey] = Counter()
    with engine.connect() as connection:
        for hour, source, raw_day, kind, tbl, action, net, n in connection.execute(
            text(
                "SELECT hour, source, raw_day, kind, tbl, action, CAST(net AS text), n "
                "FROM search_counts"
            )
        ):
            counts[CountKey(hour, source, raw_day, kind, tbl, action, net)] += n
    return counts


def marks(engine: Engine) -> dict[tuple[str, date], int | None]:
    with engine.connect() as connection:
        rows = connection.execute(
            select(IngestOffset.source, IngestOffset.day, IngestOffset.counts_from_line)
        ).all()
    return {(source, day): counted for source, day, counted in rows}


def test_a_fresh_installation_has_nothing_to_count(db_engine: Engine) -> None:
    report = backfill_counts(db_engine)
    assert (report.files, report.left, report.waiting, report.more) == ([], 0, 0, False)
    assert report.details() == {"files": [], "lines": 0, "waiting": 0}


def test_finished_files_are_counted_newest_first_and_unfinished_ones_wait(
    db_engine: Engine,
) -> None:
    with db_engine.begin() as connection:
        expected = load_file(connection, "fortigate", OLDER, 30, finished=True, counts_from=21)
        expected += load_file(connection, "fortigate", NEWER, 25, finished=True, counts_from=11)
        expected += load_file(connection, "ap-172.20.10.23", NEWER, 8, finished=True, counts_from=9)
        partial = load_file(connection, "fortigate", TODAY, 12, finished=False, counts_from=5)
        # a file ingest counted from its first line: not touched
        expected += load_file(connection, "core-r1", NEWER, 6, finished=True, counts_from=None)
    report = backfill_counts(db_engine)
    assert [(item["source"], item["day"]) for item in report.files] == [
        ("ap-172.20.10.23", NEWER.isoformat()),
        ("fortigate", NEWER.isoformat()),
        ("fortigate", OLDER.isoformat()),
    ]
    assert [(item["lines"], item["keys"] > 0) for item in report.files] == [
        (8, True),
        (25, True),
        (30, True),
    ]
    assert report.details()["lines"] == 63
    assert (report.waiting, report.more) == (1, False)
    counts = stored(db_engine)
    # Every line of the counted files once (also the ones ingest counted after the upgrade);
    # the file still being loaded keeps only what ingest counted.
    assert Counter({k: n for k, n in counts.items() if k.raw_day != TODAY}) == expected
    assert sum(n for k, n in counts.items() if k.raw_day == TODAY) == 12 - 4
    assert sum(partial.values()) == 12
    assert marks(db_engine) == {
        ("fortigate", OLDER): None,
        ("fortigate", NEWER): None,
        ("ap-172.20.10.23", NEWER): None,
        ("fortigate", TODAY): 5,
        ("core-r1", NEWER): None,
    }

    again = backfill_counts(db_engine)  # idempotent: nothing left, nothing counted twice
    assert (again.files, again.waiting) == ([], 1)
    assert stored(db_engine) == counts


def test_the_budget_ends_a_run_after_the_file_in_hand(db_engine: Engine) -> None:
    """At least one file per run, then no new one once the budget is used up."""
    with db_engine.begin() as connection:
        for day in (OLDER, NEWER, TODAY):
            load_file(connection, "fortigate", day, 5, finished=True, counts_from=6)
    first = backfill_counts(db_engine, budget=timedelta(0))
    assert [item["day"] for item in first.files] == [TODAY.isoformat()]
    assert (first.left, first.more) == (2, True)
    assert first.details()["more"] is True
    assert first.details()["left"] == 2
    second = backfill_counts(db_engine, budget=timedelta(0))
    assert [item["day"] for item in second.files] == [NEWER.isoformat()]
    rest = backfill_counts(db_engine)
    assert [item["day"] for item in rest.files] == [OLDER.isoformat()]
    assert set(marks(db_engine).values()) == {None}
    assert sum(stored(db_engine).values()) == 15
    assert BACKFILL_BUDGET.total_seconds() == 60


def test_a_used_up_budget_ends_the_run_on_a_clock_that_has_not_moved(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows' ``monotonic()`` moves in 15.6 ms ticks: a first file done within one tick reads
    no time used. A zero budget is used up all the same, so the run ends after that file."""
    with db_engine.begin() as connection:
        for day in (NEWER, TODAY):
            load_file(connection, "fortigate", day, 5, finished=True, counts_from=6)
    monkeypatch.setattr(search_jobs, "time", SimpleNamespace(monotonic=lambda: 1000.0))
    report = backfill_counts(db_engine, budget=timedelta(0))
    assert [item["day"] for item in report.files] == [TODAY.isoformat()]
    assert (report.left, report.more) == (1, True)


def test_a_file_counted_meanwhile_is_not_counted_twice(db_engine: Engine) -> None:
    """A second worker (or ``arcilogger-db search-counts rebuild``) holds the file's
    ``ingest_offsets`` row: the job waits for it, finds the file done and skips it."""
    with db_engine.begin() as connection:
        expected = load_file(connection, "fortigate", NEWER, 20, finished=True, counts_from=11)
    results: list[Any] = []
    with db_engine.connect() as other:
        transaction = other.begin()
        other.execute(
            select(IngestOffset.source)
            .where(IngestOffset.source == "fortigate", IngestOffset.day == NEWER)
            .with_for_update()
        ).scalar_one()
        job = threading.Thread(target=lambda: results.append(backfill_counts(db_engine)))
        job.start()
        _wait_for_a_lock_wait(db_engine)
        # the other one counts the file and clears the mark, as the job would
        rebuild_stream(other, "fortigate", NEWER)
        other.execute(update(IngestOffset).values(counts_from_line=None))
        transaction.commit()
        job.join(30)
    assert not job.is_alive()
    (report,) = results
    assert report.files == []
    assert stored(db_engine) == expected


def _wait_for_a_lock_wait(engine: Engine) -> None:
    """Until a session of this database waits for a lock (a new transaction per look:
    ``pg_stat_activity`` is read once per transaction)."""
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            ).scalar_one()
        if waiting:
            return
        time.sleep(0.05)
    raise AssertionError("the job did not wait for the locked file")


def test_the_job_runs_every_ten_minutes_for_every_licence(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    (job,) = [job for job in default_jobs(60) if job.name == "search.counts_backfill"]
    assert (job.every, job.module, job.func) == (
        SEARCH_COUNTS_EVERY,
        None,
        job_search_counts_backfill,
    )
    assert SEARCH_COUNTS_EVERY.total_seconds() == 600
    with db_engine.begin() as connection:
        load_file(connection, "fortigate", NEWER, 7, finished=True, counts_from=4)
    now = datetime(2026, 9, 25, 9, tzinfo=UTC)
    worker = Worker(engine=db_engine, layout=layout, jobs=[job], clock=lambda: now)
    assert worker.tick() == [("search.counts_backfill", "ok")]
    run = db_session.scalars(select(JobRun).where(JobRun.job == job.name)).one()
    assert run.details["files"] == [
        {"source": "fortigate", "day": NEWER.isoformat(), "lines": 7, "keys": 3}
    ]
    assert run.details["waiting"] == 0
    assert set(run.details) == {"files", "lines", "waiting", "duration_ms"}
    now += SEARCH_COUNTS_EVERY
    assert worker.tick() == [("search.counts_backfill", "skipped")]  # nothing left


@pytest.mark.parametrize("finished", [False, True])
def test_only_finished_files_are_counted(db_engine: Engine, finished: bool) -> None:
    with db_engine.begin() as connection:
        load_file(connection, "fortigate", NEWER, 9, finished=finished, counts_from=3)
    report = backfill_counts(db_engine)
    assert len(report.files) == (1 if finished else 0)
    assert report.waiting == (0 if finished else 1)
    assert marks(db_engine)[("fortigate", NEWER)] == (None if finished else 3)
    assert sum(stored(db_engine).values()) == (9 if finished else 7)
