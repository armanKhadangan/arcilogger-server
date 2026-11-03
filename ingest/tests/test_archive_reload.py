"""Archive reload in the ingest service (Wave 5 contract G4): a sealed day older than the database
window comes back from the vault, exactly once.

* A day whose rows the window dropped comes back identically: the same rows with the same
  raw-line references, the same ``search_counts``, clock samples and parse failures — also the
  rows of the next raw day's file that lie in it (a line received after midnight with a device
  time before it): a request's days load the raw days two days either side again.
* A reload of a day that is still loaded (a reload run twice) gives the same rows and counts.
* Status ``queued`` → ``loading`` → ``ready`` with ``bytes_done`` = ``bytes_total`` and
  ``expires_at`` = ``ready_at`` + ``db.reload_keep_days``; one request loads at a time.
* ``failed`` with an ``error_code`` when a day is not sealed in the vault or a sealed file cannot
  be read; a sealed file that does not match its manifest fails the request before anything of
  it is deleted.
* A request starts loading only when the window job does not hold the partition lock.

On TimescaleDB the compression policies are paused (``DAY`` is a fixed past day: a policy would
compress chunks under a reload or a drop at a moment no test chose).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from arcilogger_core.immutability import unlock
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import SITE_TZ, day_window
from arcilogger_db.models import TIME_SERIES_TABLES, Base
from arcilogger_db.partitions import PARTITION_LOCK_KEY, drop_before, partitioned_tables
from arcilogger_db.searchcounts import verify
from arcilogger_db.settings import set_setting
from arcilogger_db.testing import pause_compression_jobs
from arcilogger_ingest import service as service_module
from arcilogger_ingest.service import ARCHIVE_NOT_IN_VAULT, ARCHIVE_UNREADABLE, IngestService
from arcilogger_ingest.testing import DAY, World, dhcp_message, fortigate_message, offsets

#: The reloads run on this day: :data:`DAY` is 40 days old, outside the 30-day window.
TODAY = DAY + timedelta(days=40)
#: A spool line damaged on disk (no valid sender): a parse failure (``rawlog.invalid``), no row.
DAMAGED = b"2026-09-23T09:44:57.123456+03:00 \xff\xfe <13>damaged sender\n"


@pytest.fixture(autouse=True)
def _no_compression(db_engine: Engine) -> None:
    """On TimescaleDB no compression policy runs under the test (module text)."""
    with db_engine.connect() as connection:
        storage = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
    if storage["storage"] == "hypertables":
        pause_compression_jobs(db_engine)


def run_once(world: World, engine: Engine, **kwargs: Any) -> IngestService:
    service = world.service(engine, **kwargs)
    assert service.run(once=True) == 0
    return service


def load_day(world: World, engine: Engine) -> None:
    """``DAY`` written, sealed and loaded from the vault while it is in the window."""
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(40)))
    world.append_raw("core-r1", DAY, DAMAGED)
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(30)))
    world.append("unknown-192.0.2.99", DAY, [b"<13>who am i"] * 5, sender="192.0.2.99")
    world.seal(DAY)
    world.set_today(DAY + timedelta(days=1))
    run_once(world, engine)
    world.set_today(TODAY)


def snapshot(engine: Engine) -> dict[str, list[Any]]:
    """Everything ingest wrote for the files (row ids aside, which a new load renumbers)."""
    found: dict[str, list[Any]] = {}
    with engine.connect() as connection:
        for name in TIME_SERIES_TABLES:
            table = Base.metadata.tables[name]
            columns = [column for column in table.c if column.name != "id"]
            order = (table.c.source, table.c.raw_day, table.c.raw_line)
            found[name] = [
                tuple(row) for row in connection.execute(select(*columns).order_by(*order))
            ]
        for name, sql in {
            "search_counts": "SELECT hour, source, raw_day, kind, tbl, action, net, n "
            "FROM search_counts ORDER BY hour, source, raw_day, kind, tbl, action, net",
            "source_clock_samples": "SELECT source, minute, seconds, n "
            "FROM source_clock_samples ORDER BY source, minute, seconds",
            "parse_failures": "SELECT source, day, pattern, count, example_line, example_offset, "
            "error FROM parse_failures ORDER BY source, day, pattern",
        }.items():
            found[name] = [tuple(row) for row in connection.execute(text(sql))]
    return found


def counts_agree(engine: Engine) -> None:
    """``search_counts`` equals its recount from the rows."""
    with engine.connect() as connection:
        report = verify(connection, 24 * 7, now=day_window(DAY + timedelta(days=3))[0])
    assert report.differences == []


def queue(engine: Engine, first_day: date, last_day: date | None = None) -> int:
    """A request as the API queues it (after its audit entry)."""
    with engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO archive_reloads (requested_by, reason, first_day, last_day) "
                    "VALUES ('ornek.denetci', 'search', :first, :last) RETURNING id"
                ),
                {"first": first_day, "last": last_day or first_day},
            ).scalar_one()
        )


def reload_row(engine: Engine, reload_id: int) -> dict[str, Any]:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM archive_reloads WHERE id = :id"), {"id": reload_id}
        ).mappings()
        return dict(row.one())


def sealed_bytes(world: World, day: date) -> int:
    manifest = json.loads(world.layout.manifest(day).read_bytes())
    return sum(int(entry["bytes_uncompressed"]) for entry in manifest["files"])


def drop(engine: Engine, cutoff: date) -> None:
    """What the window job does to the days before ``cutoff``."""
    with engine.begin() as connection:
        drop_before(connection, cutoff)


def test_a_dropped_day_comes_back_identically(world: World, db_engine: Engine) -> None:
    load_day(world, db_engine)
    before = snapshot(db_engine)
    assert len(before["messages"]) == 40 + 30 + 5
    assert before["mt_dhcp"]
    assert before["fg_traffic"]
    assert before["search_counts"]
    assert before["source_clock_samples"]
    assert [row[2] for row in before["parse_failures"]] == ["rawlog.invalid"]
    finished = offsets(db_engine)

    drop(db_engine, DAY + timedelta(days=2))  # the partition (or chunk) of DAY goes
    dropped = snapshot(db_engine)
    assert dropped["messages"] == dropped["search_counts"] == []
    assert dropped["source_clock_samples"] == []
    assert offsets(db_engine) == finished  # the files stay finished: ingest does not reload them

    reload_id = queue(db_engine, DAY)
    assert reload_row(db_engine, reload_id)["status"] == "queued"
    run_once(world, db_engine)
    assert snapshot(db_engine) == before
    counts_agree(db_engine)
    assert offsets(db_engine) == finished
    row = reload_row(db_engine, reload_id)
    assert row["status"] == "ready"
    assert row["error_code"] is None
    assert row["bytes_total"] == row["bytes_done"] == sealed_bytes(world, DAY)
    assert row["ready_at"] == world.now
    assert row["expires_at"] == world.now + timedelta(days=7)


def test_a_reload_run_twice_gives_the_same_rows_and_counts(world: World, db_engine: Engine) -> None:
    load_day(world, db_engine)
    before = snapshot(db_engine)
    first = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert snapshot(db_engine) == before  # over rows that are still there: replaced, not added
    second = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert snapshot(db_engine) == before
    counts_agree(db_engine)
    assert [reload_row(db_engine, i)["status"] for i in (first, second)] == ["ready", "ready"]


def test_live_data_still_loads_beside_a_reload(world: World, db_engine: Engine) -> None:
    """Today's spool lines load in the same run as the reload (newest day first) and are left
    alone by it."""
    load_day(world, db_engine)
    before = snapshot(db_engine)
    drop(db_engine, DAY + timedelta(days=2))
    world.append("fortigate", TODAY, (fortigate_message(i) for i in range(12)))
    queue(db_engine, DAY)
    run_once(world, db_engine)
    after = snapshot(db_engine)
    today = [row for row in after["messages"] if row[-3] == TODAY]
    assert len(today) == 12
    assert [row for row in after["messages"] if row[-3] == DAY] == before["messages"]


def test_one_request_loads_at_a_time(world: World, db_engine: Engine) -> None:
    load_day(world, db_engine)
    before = snapshot(db_engine)
    drop(db_engine, DAY + timedelta(days=2))
    ids = [queue(db_engine, DAY), queue(db_engine, DAY)]
    seen: list[tuple[str, ...]] = []

    def statuses(step: str) -> None:
        if step != "commit":
            return
        with db_engine.connect() as connection:
            rows = connection.execute(
                text("SELECT status FROM archive_reloads ORDER BY id")
            ).scalars()
            seen.append(tuple(rows))

    run_once(world, db_engine, step_hook=statuses)
    assert ("loading", "queued") in seen
    assert ("ready", "loading") in seen
    assert all(state.count("loading") <= 1 for state in seen)
    assert ("queued", "loading") not in seen
    assert [reload_row(db_engine, i)["status"] for i in ids] == ["ready", "ready"]
    assert snapshot(db_engine) == before


def test_progress_is_the_committed_offset(
    world: World, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``bytes_done`` grows with the committed offsets while the request loads."""
    load_day(world, db_engine)
    world.reconfigure(ingest={"batch_rows": 10})
    drop(db_engine, DAY + timedelta(days=2))
    reload_id = queue(db_engine, DAY)
    monkeypatch.setattr(service_module, "ARCHIVE_PROGRESS_SECONDS", 0.0)  # after every batch
    seen: list[int] = []

    def progress(step: str) -> None:
        if step == "offsets":
            seen.append(int(reload_row(db_engine, reload_id)["bytes_done"]))

    run_once(world, db_engine, step_hook=progress)
    total = sealed_bytes(world, DAY)
    assert seen == sorted(seen)
    assert any(0 < done < total for done in seen)
    assert reload_row(db_engine, reload_id)["bytes_done"] == total


def test_expiry_follows_db_reload_keep_days(world: World, db_engine: Engine) -> None:
    load_day(world, db_engine)
    with Session(db_engine) as session:
        set_setting(session, "db.reload_keep_days", 3, updated_by="ornek.yonetici")
        session.commit()
    reload_id = queue(db_engine, DAY)
    run_once(world, db_engine)
    row = reload_row(db_engine, reload_id)
    assert row["expires_at"] - row["ready_at"] == timedelta(days=3)


def test_a_day_not_sealed_in_the_vault_fails_the_request_untouched(
    world: World, db_engine: Engine
) -> None:
    load_day(world, db_engine)
    before, finished = snapshot(db_engine), offsets(db_engine)
    reload_id = queue(db_engine, DAY - timedelta(days=1), DAY)  # the day before was never sealed
    run_once(world, db_engine)
    row = reload_row(db_engine, reload_id)
    assert (row["status"], row["error_code"]) == ("failed", ARCHIVE_NOT_IN_VAULT)
    assert row["ready_at"] is None
    assert snapshot(db_engine) == before
    assert offsets(db_engine) == finished


def test_an_unreadable_sealed_file_fails_the_request(world: World, db_engine: Engine) -> None:
    load_day(world, db_engine)
    vault_file = world.layout.vault_day(DAY) / "fortigate.log.gz"
    good = vault_file.read_bytes()
    unlock(vault_file)
    vault_file.write_bytes(good[: len(good) // 2])
    before = snapshot(db_engine)
    reload_id = queue(db_engine, DAY)
    later = queue(db_engine, DAY)
    run_once(world, db_engine, archive_error_seconds=0.0)
    row = reload_row(db_engine, reload_id)
    assert (row["status"], row["error_code"]) == ("failed", ARCHIVE_UNREADABLE)
    assert row["bytes_done"] < row["bytes_total"]
    assert reload_row(db_engine, later)["status"] == "failed"  # the next one meets it as well
    # Checked against the manifest before its rows were deleted: the fortigate rows are still
    # there (the core-r1 file, reset before it, came back whole).
    assert snapshot(db_engine) == before

    vault_file.write_bytes(good)  # restored from the replica: asked again, it loads
    again = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert reload_row(db_engine, again)["status"] == "ready"
    assert len(snapshot(db_engine)["messages"]) == 40 + 30 + 5
    counts_agree(db_engine)


def test_a_file_changed_after_the_claim_stops_the_resets(
    world: World, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim checked every file whole; one gone or changed before its own reset (a size that
    no longer matches) is not reset: the files reset before it load back, the request stays
    ``loading`` until they have, then fails — its rows are left as they were."""
    load_day(world, db_engine)
    before = snapshot(db_engine)
    real = IngestService._sealed_file_intact

    def changed(self: IngestService, source: str, day: date, *, digest: bool = True) -> bool:
        if source == "fortigate" and not digest:  # changed after the claim's check
            return False
        return real(self, source, day, digest=digest)

    monkeypatch.setattr(IngestService, "_sealed_file_intact", changed)
    reload_id = queue(db_engine, DAY)
    seen: list[str] = []

    def status(step: str) -> None:
        if step == "commit":
            seen.append(str(reload_row(db_engine, reload_id)["status"]))

    run_once(world, db_engine, step_hook=status)
    row = reload_row(db_engine, reload_id)
    assert (row["status"], row["error_code"]) == ("failed", ARCHIVE_UNREADABLE)
    assert "loading" in seen  # while the core-r1 file loaded back
    assert snapshot(db_engine) == before
    counts_agree(db_engine)


class SimulatedCrashError(Exception):
    """Stands for the process dying at that point."""


def test_a_reload_interrupted_while_loading_is_resumed_exactly_once(
    world: World, db_engine: Engine
) -> None:
    """The service dies in the middle of a reload: the request stays ``loading``, the next start
    takes it up again (every file reset once more) and the rows come out the same."""
    load_day(world, db_engine)
    world.reconfigure(ingest={"batch_rows": 10})
    before = snapshot(db_engine)
    drop(db_engine, DAY + timedelta(days=2))
    reload_id = queue(db_engine, DAY)
    commits = 0

    def crash(step: str) -> None:
        nonlocal commits
        if step == "commit":
            commits += 1
            if commits == 3:
                raise SimulatedCrashError

    with pytest.raises(SimulatedCrashError):
        world.service(db_engine, step_hook=crash).run(once=True)
    row = reload_row(db_engine, reload_id)
    assert row["status"] == "loading"
    assert 0 < len(snapshot(db_engine)["messages"]) < len(before["messages"])

    run_once(world, db_engine)
    assert reload_row(db_engine, reload_id)["status"] == "ready"
    assert snapshot(db_engine) == before
    counts_agree(db_engine)


def test_a_reload_brings_back_the_rows_other_raw_days_have_in_its_days(
    world: World, db_engine: Engine
) -> None:
    """A line received just after midnight with a device time before it (a session's end, a
    clock behind) is in the next raw day's file but in the day's partition: the window's drop
    takes it with the day, and the reload of the day loads the raw days two days either side
    again (``RAW_DAY_MARGIN``), so it comes back too."""
    late = dhcp_message(1).replace(b"2026-09-23T09:38:26.380", b"2026-09-23T23:59:30.000")
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10)))
    world.seal(DAY)
    world.append("core-r1", DAY + timedelta(days=1), [late], start=time(0, 0, 10))
    world.seal(DAY + timedelta(days=1))
    world.set_today(DAY + timedelta(days=2))
    run_once(world, db_engine)
    world.set_today(TODAY)
    before = snapshot(db_engine)
    start, end = day_window(DAY)
    in_day = [
        row
        for row in before["messages"]
        if start <= row[0] < end and row[-3] == DAY + timedelta(days=1)
    ]
    assert len(in_day) == 1  # the late line: raw day DAY + 1, device time on DAY

    drop(db_engine, DAY + timedelta(days=3))
    assert snapshot(db_engine)["messages"] == []
    reload_id = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert snapshot(db_engine) == before
    counts_agree(db_engine)
    row = reload_row(db_engine, reload_id)
    assert row["status"] == "ready"
    assert row["bytes_total"] == sealed_bytes(world, DAY) + sealed_bytes(
        world, DAY + timedelta(days=1)
    )


def test_a_request_waits_while_the_window_job_holds_the_partitions(
    world: World, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transaction that starts loading takes the partition maintenance lock: while the
    window job holds it (reading the reloads, dropping), the request stays ``queued``, so the
    job never drops what a reload has started to load."""
    load_day(world, db_engine)
    before = snapshot(db_engine)
    drop(db_engine, DAY + timedelta(days=2))
    reload_id = queue(db_engine, DAY)
    monkeypatch.setattr(service_module, "PARTITION_LOCK_TIMEOUT", "200ms")
    with db_engine.connect() as job, job.begin():
        job.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
        run_once(world, db_engine)
        assert reload_row(db_engine, reload_id)["status"] == "queued"
    run_once(world, db_engine)
    assert reload_row(db_engine, reload_id)["status"] == "ready"
    assert snapshot(db_engine) == before


def test_a_row_outside_its_days_window_is_replaced_not_doubled(
    world: World, db_engine: Engine
) -> None:
    """A spool line whose receive time is of another day (damaged, restored by hand) gives a row
    outside the file's ``stream_window``: in the default partition on plain PostgreSQL, where
    the reset finds it, so a reload run twice leaves it once. (On TimescaleDB it is not looked
    for: every chunk would have to be read; a known limit.)"""
    stray = format_line(datetime(2001, 1, 1, 9, tzinfo=SITE_TZ), "172.20.10.1", dhcp_message(3))
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(4)))
    world.append_raw("core-r1", DAY, stray)
    world.seal(DAY)
    world.set_today(DAY + timedelta(days=1))
    run_once(world, db_engine)
    world.set_today(TODAY)
    with db_engine.connect() as connection:
        if not partitioned_tables(connection):
            pytest.skip("TimescaleDB hypertables: a row outside its window is not looked for")
    before = snapshot(db_engine)
    assert [row for row in before["messages"] if row[0].year == 2001]
    for _ in range(2):
        queue(db_engine, DAY)
        run_once(world, db_engine)
    assert snapshot(db_engine) == before
