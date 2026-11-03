"""Archive reload in the ingest service (Wave 5 contract G4): a sealed day older than the database
window comes back from the vault, exactly once.

* A day whose rows the window dropped comes back identically: the same rows with the same
  raw-line references, the same ``search_counts``, clock samples and parse failures — also the
  rows of the next raw day's file that lie in it (a line received after midnight with a device
  time before it): a request's days load the raw days two days either side again.
* A reload of a day that is still loaded (a reload run twice) gives the same rows and counts —
  also a row of the file far outside its days (a stray receive time, found from the sealed
  file's lines whether or not the file's counts name it) and its clock sample, on both back
  ends; the rows of the source's other raw days in the same device times stay untouched.
* Two users, two records, one load: a request whose files are loaded already (another user's
  ``ready`` reload holds them) becomes ``ready`` without a delete or an offset reset.
* Status ``queued`` → ``loading`` → ``ready`` with ``bytes_done`` = ``bytes_total`` and
  ``expires_at`` = ``ready_at`` + ``db.reload_keep_days``; one request loads at a time.
* ``failed`` with an ``error_code`` when a day is not sealed in the vault or a sealed file cannot
  be read; a sealed file that does not match its manifest fails the request before anything of
  it is deleted; one that becomes unreadable while it loads stays short of its size.
* A request starts loading only when the window job does not hold the partition lock.
* On TimescaleDB with compression, a reload resets and loads raw days whose chunks are
  compressed (the margin days of a day near the window's start are older than
  ``COMPRESS_AFTER``): the same rows come back, also from a reload run twice.

On TimescaleDB the compression policies are paused (``DAY`` is a fixed past day: a policy would
compress chunks under a reload or a drop at a moment no test chose); a test that needs
compressed chunks compresses them itself.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session

from arcilogger_core import sealedfile
from arcilogger_core.immutability import unlock
from arcilogger_core.rawlog import format_line
from arcilogger_core.sealedfile import Compression
from arcilogger_core.timeutil import SITE_TZ, day_window
from arcilogger_db.models import TIME_SERIES_TABLES, Base
from arcilogger_db.partitions import PARTITION_LOCK_KEY, drop_before, ensure_days
from arcilogger_db.searchcounts import rebuild_stream, stream_window, verify
from arcilogger_db.settings import set_setting
from arcilogger_db.testing import pause_compression_jobs
from arcilogger_ingest import service as service_module
from arcilogger_ingest.files import VaultReader, VaultReadError
from arcilogger_ingest.service import (
    ARCHIVE_NOT_IN_VAULT,
    ARCHIVE_UNREADABLE,
    IngestService,
    received_outside,
)
from arcilogger_ingest.testing import DAY, World, dhcp_message, fortigate_message, offsets

#: The reloads run on this day: :data:`DAY` is 40 days old, outside the 30-day window.
TODAY = DAY + timedelta(days=40)
#: A spool line damaged on disk (no valid sender): a parse failure (``rawlog.invalid``), no row.
DAMAGED = b"2026-09-23T09:44:57.123456+03:00 \xff\xfe <13>damaged sender\n"
#: A device clock this far behind the receive time (its time is used: within a day).
HALF_HOUR = timedelta(minutes=30)


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


def of_source(found: dict[str, list[Any]], source: str) -> dict[str, list[Any]]:
    """The time-series rows of :func:`snapshot` from ``source``'s files (tables without one
    left out)."""
    rows: dict[str, list[Any]] = {}
    for name in TIME_SERIES_TABLES:
        at = [c.name for c in Base.metadata.tables[name].c if c.name != "id"].index("source")
        mine = [row for row in found[name] if row[at] == source]
        if mine:
            rows[name] = mine
    return rows


def row_ids(engine: Engine) -> dict[str, list[tuple[Any, ...]]]:
    """Every row's id with its raw-line reference (a reset and a new load renumber them)."""
    with engine.connect() as connection:
        return {
            name: [
                tuple(row)
                for row in connection.execute(
                    text(f"SELECT id, source, raw_day, raw_line FROM {name} ORDER BY id")  # noqa: S608 - fixed names
                )
            ]
            for name in TIME_SERIES_TABLES
        }


def stored_offsets(engine: Engine) -> list[tuple[Any, ...]]:
    """``ingest_offsets`` as stored, ``updated_at`` included (a reset writes it)."""
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                text(
                    "SELECT source, day, byte_offset, line_number, finished_at, updated_at "
                    "FROM ingest_offsets ORDER BY source, day"
                )
            )
        ]


def queue(
    engine: Engine, first_day: date, last_day: date | None = None, *, by: str = "ornek.denetci"
) -> int:
    """A request as the API queues it (after its audit entry)."""
    with engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO archive_reloads (requested_by, reason, first_day, last_day) "
                    "VALUES (:by, 'search', :first, :last) RETURNING id"
                ),
                {"by": by, "first": first_day, "last": last_day or first_day},
            ).scalar_one()
        )


def skipped(world: World, day: date) -> None:
    """The sealer skipped ``day`` (no line): a request may name it, nothing to load."""
    state = world.layout.seal_state(day)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"day": day.isoformat(), "status": "skipped"}), encoding="utf-8")


def expire(engine: Engine) -> None:
    """What the window job does to a ``ready`` reload once its ``expires_at`` has passed."""
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE archive_reloads SET status = 'expired' WHERE status = 'ready'")
        )


def sealed_file(world: World, day: date, source: str) -> tuple[Path, int]:
    """The sealed file of ``(source, day)`` and its ``bytes_uncompressed`` (the manifest)."""
    manifest = json.loads(world.layout.manifest(day).read_bytes())
    entry = next(entry for entry in manifest["files"] if entry["source"] == source)
    return world.layout.vault / str(entry["path"]), int(entry["bytes_uncompressed"])


def unreadable_mid_load(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """``path`` becomes unreadable while it is loaded: at the loader's second read of it the
    file is damaged in place, and that read and every later one fail (the reset's reading of
    its receive times, :func:`received_outside`, comes before the load and is not counted). (A
    reader meets such damage where its buffer ends; a test file fits in the buffer, so the
    reader is told.)"""
    reads = 0
    resetting = False
    outside = service_module.received_outside

    def reset_read(file: Path, day: date) -> list[datetime]:
        nonlocal resetting
        resetting = True
        try:
            return outside(file, day)
        finally:
            resetting = False

    class Damaged(VaultReader):
        def read(self, max_bytes: int) -> bytes:
            nonlocal reads
            if self.path.name == path.name and not resetting:
                reads += 1
                if reads == 2:
                    unlock(path)
                    path.write_bytes(path.read_bytes()[:64])
                if reads >= 2:
                    raise VaultReadError(f"{path.name}: damaged gzip data")
            return super().read(max_bytes)

    monkeypatch.setattr(service_module, "VaultReader", Damaged)
    monkeypatch.setattr(service_module, "received_outside", reset_read)


def with_device_time(message: bytes, device: datetime) -> bytes:
    """A :func:`dhcp_message` with its device time set to ``device``."""
    stamp = device.astimezone(SITE_TZ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    return message.replace(b"2026-09-23T09:38:26.380", stamp.encode())


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


@pytest.mark.parametrize("compression", ["gzip", "xz"])
def test_a_dropped_day_comes_back_identically(
    world: World, db_engine: Engine, compression: Compression
) -> None:
    """Days sealed before W5-H (gzip) and after it (xz) come back alike."""
    world.reconfigure(seal={"compression": compression})
    load_day(world, db_engine)
    manifest = json.loads(world.layout.manifest(DAY).read_bytes())
    assert {sealedfile.format_of(entry["path"]) for entry in manifest["files"]} == {compression}
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
    """The second request starts a day earlier (a day the sealer skipped): the first one holds
    only the rows of the file from ``DAY`` on, so the file is reset over rows that are still
    there — replaced, not added."""
    load_day(world, db_engine)
    before = snapshot(db_engine)
    drop(db_engine, DAY + timedelta(days=2))
    first = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert snapshot(db_engine) == before
    loaded = row_ids(db_engine)
    skipped(world, DAY - timedelta(days=1))
    second = queue(db_engine, DAY - timedelta(days=1), DAY)
    run_once(world, db_engine)
    assert row_ids(db_engine) != loaded  # loaded again
    assert snapshot(db_engine) == before
    counts_agree(db_engine)
    assert [reload_row(db_engine, i)["status"] for i in (first, second)] == ["ready", "ready"]


def test_another_users_request_of_loaded_days_is_ready_without_a_second_load(
    world: World, db_engine: Engine
) -> None:
    """Two users, two records, one load (W5-G answer 6): the second request of the same days
    finds every file loaded (its offset finished, the first request holding its rows since)
    and becomes ``ready`` with its own record — no delete, no offset reset, no row loaded
    again."""
    load_day(world, db_engine)
    drop(db_engine, DAY + timedelta(days=2))
    first = queue(db_engine, DAY, by="ornek.denetci")
    run_once(world, db_engine)
    loaded, ids, stored = snapshot(db_engine), row_ids(db_engine), stored_offsets(db_engine)
    assert loaded["messages"]
    second = queue(db_engine, DAY, by="ornek.analist")
    writes: list[str] = []

    def seen(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if statement.lstrip().upper().startswith(("DELETE", "INSERT INTO INGEST_OFFSETS")):
            writes.append(statement)

    event.listen(db_engine, "before_cursor_execute", seen)
    try:
        run_once(world, db_engine)
    finally:
        event.remove(db_engine, "before_cursor_execute", seen)
    assert writes == []
    assert row_ids(db_engine) == ids
    assert stored_offsets(db_engine) == stored
    assert snapshot(db_engine) == loaded
    rows = [reload_row(db_engine, reload_id) for reload_id in (first, second)]
    assert [(row["requested_by"], row["status"]) for row in rows] == [
        ("ornek.denetci", "ready"),
        ("ornek.analist", "ready"),
    ]
    assert rows[1]["bytes_total"] == rows[1]["bytes_done"] == sealed_bytes(world, DAY)
    assert rows[1]["expires_at"] == world.now + timedelta(days=7)


def test_a_file_the_window_job_never_reached_counts_as_loaded(
    world: World, db_engine: Engine
) -> None:
    """The newest cutoff the window job applied lies before the file's days: its rows were never
    dropped, so the request is ``ready`` without a load. Without that record (nothing dropped
    yet, or a database restored without it) the same request loads the file again."""
    load_day(world, db_engine)
    before = snapshot(db_engine)
    drop(db_engine, DAY - timedelta(days=2))
    ids, stored = row_ids(db_engine), stored_offsets(db_engine)
    first = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert reload_row(db_engine, first)["status"] == "ready"
    assert (row_ids(db_engine), stored_offsets(db_engine)) == (ids, stored)
    with db_engine.begin() as connection:
        connection.execute(text("DELETE FROM settings WHERE key = 'db.window_dropped_before'"))
    expire(db_engine)
    second = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert reload_row(db_engine, second)["status"] == "ready"
    assert row_ids(db_engine) != ids  # loaded again
    assert snapshot(db_engine) == before


def test_a_file_reloaded_is_loaded_again_when_its_rows_may_have_gone(
    world: World, db_engine: Engine
) -> None:
    """A finished file counts as loaded only while its rows of the request's days are known to
    be there: after the first request expired and the window job dropped the day again, the next
    request loads it again."""
    load_day(world, db_engine)
    before = snapshot(db_engine)
    drop(db_engine, DAY + timedelta(days=2))
    queue(db_engine, DAY)
    run_once(world, db_engine)
    expire(db_engine)
    drop(db_engine, DAY + timedelta(days=2))
    assert snapshot(db_engine)["messages"] == []
    finished = offsets(db_engine)
    again = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert reload_row(db_engine, again)["status"] == "ready"
    assert snapshot(db_engine) == before
    assert offsets(db_engine) == finished


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
    # The second one from the (skipped) day before: the file is not the first one's to hold, so
    # it is loaded again.
    skipped(world, DAY - timedelta(days=1))
    ids = [queue(db_engine, DAY), queue(db_engine, DAY - timedelta(days=1), DAY)]
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
    drop(db_engine, DAY + timedelta(days=2))
    vault_file, _ = sealed_file(world, DAY, "fortigate")
    good = vault_file.read_bytes()
    unlock(vault_file)
    vault_file.write_bytes(good[: len(good) // 2])
    before, finished = snapshot(db_engine), offsets(db_engine)
    reload_id = queue(db_engine, DAY)
    later = queue(db_engine, DAY)
    run_once(world, db_engine, archive_error_seconds=0.0)
    row = reload_row(db_engine, reload_id)
    assert (row["status"], row["error_code"]) == ("failed", ARCHIVE_UNREADABLE)
    assert row["bytes_done"] < row["bytes_total"]
    assert reload_row(db_engine, later)["status"] == "failed"  # the next one meets it as well
    # Checked against the manifest before anything was deleted or reset: no file of the day was
    # touched.
    assert snapshot(db_engine) == before
    assert offsets(db_engine) == finished

    vault_file.write_bytes(good)  # restored from the replica: asked again, it loads
    again = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert reload_row(db_engine, again)["status"] == "ready"
    assert len(snapshot(db_engine)["messages"]) == 40 + 30 + 5
    counts_agree(db_engine)


def test_a_file_unreadable_mid_load_fails_the_request_and_stays_short(
    world: World, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W5-G answer 9: a sealed file that becomes unreadable while it loads (changed in place, a
    failing disk) fails the request with ``archive_file_unreadable``; its offset stays short of
    the manifest's ``bytes_uncompressed``, so the day never counts as loaded — the next request
    loads the file again (once it is whole again), it is not taken as loaded, although another
    user's ``ready`` reload of the day holds it."""
    load_day(world, db_engine)
    before = snapshot(db_engine)
    world.reconfigure(ingest={"batch_rows": 10})
    drop(db_engine, DAY + timedelta(days=2))
    holder = queue(db_engine, DAY, by="ornek.yonetici")
    run_once(world, db_engine)
    assert reload_row(db_engine, holder)["status"] == "ready"
    vault_file, size = sealed_file(world, DAY, "fortigate")
    good = vault_file.read_bytes()
    unreadable_mid_load(monkeypatch, vault_file)
    # From the (skipped) day before: the files are not the holder's to keep, so they are reset.
    skipped(world, DAY - timedelta(days=1))
    reload_id = queue(db_engine, DAY - timedelta(days=1), DAY)
    run_once(world, db_engine, archive_error_seconds=0.0)
    row = reload_row(db_engine, reload_id)
    assert (row["status"], row["error_code"]) == ("failed", ARCHIVE_UNREADABLE)
    assert row["ready_at"] is None
    offset, _, finished = offsets(db_engine)[("fortigate", DAY)]
    assert (0 < offset < size, finished) == (True, False)
    loaded = [row for row in snapshot(db_engine)["fg_traffic"] if row in before["fg_traffic"]]
    assert 0 < len(loaded) < len(before["fg_traffic"])
    # The holder's day alone: its other files are loaded, the short one is not.
    still = queue(db_engine, DAY)
    run_once(world, db_engine, archive_error_seconds=0.0)
    row = reload_row(db_engine, still)
    assert (row["status"], row["error_code"]) == ("failed", ARCHIVE_UNREADABLE)
    assert offsets(db_engine)[("fortigate", DAY)][2] is False

    monkeypatch.setattr(service_module, "VaultReader", VaultReader)
    vault_file.write_bytes(good)  # restored from the replica: asked again, it loads
    again = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert reload_row(db_engine, again)["status"] == "ready"
    assert snapshot(db_engine) == before
    counts_agree(db_engine)


def test_a_file_changed_after_the_claim_stops_the_resets(
    world: World, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim checked every file whole; one gone or changed before its own reset (a size that
    no longer matches) is not reset: the files reset before it load back, the request stays
    ``loading`` until they have, then fails — its rows are left as they were."""
    load_day(world, db_engine)
    loaded = snapshot(db_engine)
    drop(db_engine, DAY + timedelta(days=2))
    before, finished = snapshot(db_engine), offsets(db_engine)
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
    after = snapshot(db_engine)
    assert of_source(after, "core-r1") == of_source(loaded, "core-r1")
    assert of_source(after, "fortigate") == of_source(before, "fortigate") == {}
    assert offsets(db_engine)[("fortigate", DAY)] == finished[("fortigate", DAY)]  # not reset
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


@pytest.mark.parametrize("counts", ["loaded", "recounted", "uncounted"])
def test_a_row_outside_its_days_window_is_replaced_not_doubled(
    world: World, db_engine: Engine, counts: str
) -> None:
    """A spool line whose receive time is of another day (damaged, restored by hand) gives a row
    of the file far outside its ``stream_window`` and its two days' margin: one in 2001 (the
    default partition on plain PostgreSQL, a chunk of its own on TimescaleDB), two three days
    ago (a daily partition or chunk the window job keeps), one of them from a device clock half
    an hour behind, whose clock sample shares its key with a line of that day's own file. The
    reset finds them by ``(source, raw_day)`` around the minutes the sealed file's lines were
    received in, and takes the file's part off the shared sample, so a reload run twice leaves
    each row once and every sample as it was, on both back ends (W5-G answer 10) — also when
    the file's ``search_counts`` were counted again from its rows (``search-counts rebuild``,
    which counts the rows within ``stream_window``) or the file was loaded before the counts
    existed (``counts_from_line`` set)."""
    ahead = datetime.combine(TODAY - timedelta(days=3), time(10), tzinfo=SITE_TZ)
    behind = ahead + timedelta(hours=2)
    # Each stray's receive time and its row's time (a device time within a day of it is used).
    strays = {datetime(2001, 1, 1, 9, tzinfo=SITE_TZ): None, ahead: None, behind: HALF_HOUR}
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(4)))
    for number, (received, offset) in enumerate(strays.items()):
        message = dhcp_message(5 + number)
        if offset is not None:
            message = with_device_time(message, received - offset)
        world.append_raw("core-r1", DAY, format_line(received, "172.20.10.1", message))
    world.seal(DAY)
    # That day's own file: a line in the same received minute, the same clock offset.
    shared = behind + timedelta(seconds=1)
    world.append(
        "core-r1",
        behind.date(),
        [with_device_time(dhcp_message(9), shared - HALF_HOUR)],
        start=shared.time(),
    )
    with (
        db_engine.begin() as connection
    ):  # plain PostgreSQL: the day's partitions (TimescaleDB: none)
        ensure_days(connection, ahead.date(), ahead.date())
    world.set_today(DAY + timedelta(days=1))
    run_once(world, db_engine)
    world.set_today(TODAY)
    before = snapshot(db_engine)
    times = [
        received if offset is None else received - offset for received, offset in strays.items()
    ]
    assert sorted(row[0] for row in before["messages"] if row[0] in times) == times
    minute = behind.astimezone(UTC).replace(second=0)
    assert ("core-r1", minute, -1800, 2) in before["source_clock_samples"]
    with db_engine.connect() as connection:
        holder = connection.execute(
            text("SELECT CAST(tableoid AS regclass)::text FROM messages WHERE time = :t"),
            {"t": ahead},
        ).scalar_one()
    assert holder != "messages_default"  # a partition or chunk of its day, not the default one
    with db_engine.begin() as connection:
        if counts == "recounted":
            rebuild_stream(connection, "core-r1", DAY)
        elif counts == "uncounted":
            connection.execute(
                text(
                    "UPDATE ingest_offsets SET counts_from_line = 1 "
                    "WHERE source = 'core-r1' AND day = :day"
                ),
                {"day": DAY},
            )
            connection.execute(
                text("DELETE FROM search_counts WHERE source = 'core-r1' AND raw_day = :day"),
                {"day": DAY},
            )
    for _ in range(2):
        expire(db_engine)
        drop(db_engine, DAY + timedelta(days=2))
        kept = [row for row in snapshot(db_engine)["messages"] if row[0] in times]
        assert [row[0] for row in kept] == times[1:]  # their day is in the window
        queue(db_engine, DAY)
        run_once(world, db_engine)
        assert snapshot(db_engine) == before
    counts_agree(db_engine)


def test_the_minutes_received_outside_the_day_are_read_from_the_sealed_file(
    tmp_path: Path,
) -> None:
    """:func:`received_outside`: the minutes of the lines received on another day, also of a
    receive time not written as the collector writes it and of a last line without ``\\n``;
    lines without a usable receive time (damaged, empty) are left out; a damaged gzip file
    raises."""
    in_day = datetime.combine(DAY, time(9), tzinfo=SITE_TZ)
    lines = [
        format_line(in_day, "172.20.10.1", b"<13>in its day"),
        format_line(datetime(2001, 1, 1, 9, 0, 30, tzinfo=SITE_TZ), "172.20.10.1", b"<13>2001"),
        b"2001-02-03T04:05:06+01:00 172.20.10.1 <13>no fraction\n",
        DAMAGED,
        b"\n",
        b"not a time 172.20.10.1 <13>damaged\n",
        format_line(in_day + timedelta(days=2), "172.20.10.1", b"<13>ahead").rstrip(b"\n"),
    ]
    path = tmp_path / "core-r1.log.gz"
    path.write_bytes(gzip.compress(b"".join(lines)))
    assert received_outside(path, DAY) == [
        datetime(2001, 1, 1, 9, 0, tzinfo=SITE_TZ).astimezone(UTC),
        datetime(2001, 2, 3, 3, 5, tzinfo=UTC),
        (in_day + timedelta(days=2)).astimezone(UTC),
    ]
    path.write_bytes(gzip.compress(b"".join(lines))[:40])
    with pytest.raises(VaultReadError):
        received_outside(path, DAY)


def test_a_reload_leaves_the_raw_days_beyond_its_margin_alone(
    world: World, db_engine: Engine
) -> None:
    """The reset deletes by ``(source, raw_day)``: a line of the same source's raw day
    ``DAY + 3`` (beyond the margin a request of ``DAY`` reloads), received just after its
    midnight from a clock almost a day behind, has its row in ``stream_window`` of ``DAY`` —
    it stays as it is, with its id and its file's offset."""
    after = DAY + timedelta(days=3)
    received = datetime.combine(after, time(0, 0, 10), tzinfo=SITE_TZ)
    device = received - timedelta(hours=23, minutes=30)
    load_day(world, db_engine)
    world.append(
        "core-r1", after, [with_device_time(dhcp_message(3), device)], start=time(0, 0, 10)
    )
    world.set_today(after + timedelta(days=1))
    run_once(world, db_engine)
    world.set_today(TODAY)
    lo, hi = stream_window(DAY)
    assert lo <= device < hi
    drop(db_engine, DAY + timedelta(days=2))

    def beyond() -> tuple[dict[str, list[tuple[Any, ...]]], list[Any], Any]:
        """The rows of raw day ``after`` with their ids, its messages as stored, its offset."""
        ids = {
            name: [row for row in rows if row[2] == after]
            for name, rows in row_ids(db_engine).items()
        }
        stored = [row for row in snapshot(db_engine)["messages"] if row[-3] == after]
        return ids, stored, offsets(db_engine)[("core-r1", after)]

    kept = beyond()
    assert [row[0] for row in kept[1]] == [device]
    reload_id = queue(db_engine, DAY)
    run_once(world, db_engine)
    assert reload_row(db_engine, reload_id)["status"] == "ready"
    assert beyond() == kept


def compress_all(engine: Engine) -> list[str]:
    """Compress every chunk of the time-series hypertables (what the policy does to the chunks
    older than ``COMPRESS_AFTER``, at a moment the test chooses); the compressed chunks."""
    with engine.begin() as connection:
        for name in TIME_SERIES_TABLES:
            connection.execute(
                text(
                    "SELECT compress_chunk(c, if_not_compressed => true) "
                    "FROM show_chunks(CAST(:table AS regclass)) AS c"
                ),
                {"table": name},
            )
        return list(
            connection.execute(
                text(
                    "SELECT format('%I.%I', chunk_schema, chunk_name) "
                    "FROM timescaledb_information.chunks WHERE is_compressed ORDER BY 1"
                )
            ).scalars()
        )


def test_a_reload_resets_and_loads_its_raw_days_in_compressed_chunks(
    world: World, db_engine: Engine
) -> None:
    """TimescaleDB with compression: the margin raw days of a day near the window's start are
    older than ``COMPRESS_AFTER``, so their chunks are compressed when the reload resets them.
    The reset deletes their rows from the compressed chunks and the load writes them in again:
    the same rows with the same raw-line references, counts, clock samples and failures as
    before the drop — also a line of ``DAY + 1``'s file whose device time lies on ``DAY`` (its
    chunk was dropped) — and a reload run twice, over chunks compressed again, gives them once
    more."""
    with db_engine.connect() as connection:
        status = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
    if status["storage"] != "hypertables" or not status.get("compression"):
        if os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1":
            pytest.fail(f"TimescaleDB compression expected but the template has {status}")
        pytest.skip("TimescaleDB with compression is not installed (the Linux CI job runs this)")
    margin = [DAY + timedelta(days=1), DAY + timedelta(days=2)]
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(20)))
    world.append_raw("core-r1", DAY, DAMAGED)
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(10)))
    world.seal(DAY)
    for n, day in enumerate(margin, start=1):
        device = datetime.combine(day, time(8), tzinfo=SITE_TZ)
        lines = [with_device_time(dhcp_message(i), device) for i in range(10 * n)]
        if n == 1:
            lines.append(dhcp_message(99))  # device time DAY 09:38: within a day, on DAY
        world.append("core-r1", day, lines)
        world.seal(day)
    world.set_today(DAY + timedelta(days=3))
    run_once(world, db_engine)
    world.set_today(TODAY)
    before = snapshot(db_engine)
    assert {name: len(rows) for name, rows in before.items() if rows} == {
        "messages": 20 + 10 + 11 + 20,
        "mt_dhcp": 20 + 11 + 20,
        "fg_traffic": 5,
        "fg_event": 2,
        "fg_app": 3,
        "search_counts": 14,
        "source_clock_samples": 12,
        "parse_failures": 1,
    }
    on_margin = [row for row in before["messages"] if row[-3] in margin]
    late = [row for row in on_margin if row[0] < day_window(margin[0])[0]]
    assert len(on_margin) == 31
    assert len(late) == 1
    total = sum(sealed_bytes(world, day) for day in [DAY, *margin])

    def margin_chunks() -> set[str]:
        """The chunks holding the time-series rows of the margin raw days."""
        with db_engine.connect() as connection:
            return {
                str(chunk)
                for name in TIME_SERIES_TABLES
                for chunk in connection.execute(
                    text(
                        f"SELECT DISTINCT CAST(tableoid AS regclass)::text FROM {name} "  # noqa: S608 - fixed names
                        "WHERE raw_day = ANY(:days)"
                    ),
                    {"days": margin},
                ).scalars()
            }

    for _ in range(2):
        expire(db_engine)
        drop(db_engine, DAY + timedelta(days=2))  # the chunks of DAY go, the margin's stay
        dropped = snapshot(db_engine)
        assert [row for row in dropped["messages"] if row[-3] == DAY] == []
        kept = [row for row in dropped["messages"] if row[-3] in margin]
        assert kept == [row for row in on_margin if row not in late]  # the late line went
        compressed = compress_all(db_engine)
        assert margin_chunks() <= set(compressed)
        assert margin_chunks()
        reload_id = queue(db_engine, DAY)
        run_once(world, db_engine)
        row = reload_row(db_engine, reload_id)
        assert (row["status"], row["error_code"]) == ("ready", None)
        assert row["bytes_total"] == row["bytes_done"] == total  # every file reset and loaded
        assert snapshot(db_engine) == before
        counts_agree(db_engine)
