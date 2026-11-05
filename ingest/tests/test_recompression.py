"""#149: a TimescaleDB recompression of a partial chunk against a write into it.

Where the columnstore policy recompresses a partial chunk by rebuilding it (a table it cannot
recompress segment by segment), it holds ``ShareLock`` on the chunk and then asks for
``AccessExclusiveLock`` on its index. A transaction that read the chunk (``AccessShareLock`` on
the index) and then writes it (``RowExclusiveLock``, which the policy's ``ShareLock`` blocks)
closes a cycle, and PostgreSQL aborts the side that waited longer with ``40P01`` (seen once in
#133's loop, the policy aborted). On ``messages`` TimescaleDB 2.30 recompresses segment by
segment and gives a chunk with concurrent writes up for its next run without waiting. Ingest's
batch writes before it reads (its COPY takes ``RowExclusiveLock`` first), so it closes no cycle
either way (W5-G-28). The orders are made deterministic with two connections, ordered through
``pg_locks``. TimescaleDB with compression only (the Linux CI job runs this).
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from time import monotonic, sleep
from typing import Any

import psycopg
import pytest
from sqlalchemy import Engine, text

from arcilogger_db.engine import engine_from_url
from arcilogger_db.testing import pause_compression_jobs
from arcilogger_ingest.testing import DAY, World, check_exactly_once, dhcp_message

#: The longest wait for a step of the other connection.
WAIT = 30.0
#: ``_timescaledb_catalog.chunk.status``: compressed, and partial (rows written after it).
COMPRESSED = 1
PARTIAL = 8


def needs_compression(engine: Engine) -> None:
    with engine.connect() as connection:
        status = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
    if status["storage"] != "hypertables" or not status.get("compression"):
        if os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1":
            pytest.fail(f"TimescaleDB compression expected but the template has {status}")
        pytest.skip("TimescaleDB with compression is not installed (the Linux CI job runs this)")


def messages_policy(engine: Engine) -> int:
    """The compression policy job of ``messages`` (paused: the test runs it)."""
    pause_compression_jobs(engine)
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT job_id FROM timescaledb_information.jobs "
                    "WHERE proc_name = 'policy_compression' AND hypertable_name = 'messages'"
                )
            ).scalar_one()
        )


def partial_chunk(world: World, engine: Engine) -> str:
    """``DAY``'s ``messages`` chunk, loaded, compressed, then written to again: partial."""
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(20)))
    assert world.service(engine).run(once=True) == 0
    with engine.begin() as connection:
        (chunk,) = connection.execute(
            text(
                "SELECT DISTINCT CAST(tableoid AS regclass)::text FROM messages "
                "WHERE raw_day = :day"
            ),
            {"day": DAY},
        ).scalars()
        connection.execute(text("SELECT compress_chunk(CAST(:c AS regclass))"), {"c": chunk})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(20, 30)))
    assert world.service(engine).run(once=True) == 0
    assert status(engine, chunk) & PARTIAL
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(30, 40)))  # the next batch
    return str(chunk)


def status(engine: Engine, chunk: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(
                text(
                    "SELECT status FROM _timescaledb_catalog.chunk "
                    "WHERE relid = CAST(:c AS regclass)"
                ),
                {"c": chunk},
            ).scalar_one()
        )


def in_thread(call: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    """``call`` started in a thread; its result (or exception) lands in the list."""
    answer: list[Any] = []

    def run() -> None:
        try:
            answer.append(call())
        except BaseException as exc:  # the test thread looks at it
            answer.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, answer


def run_policy(engine: Engine, job: int, notices: list[str]) -> Callable[[], int]:
    """The policy job, run in a session of its own (``CALL run_job``, outside a transaction
    block: the policy commits between chunks); returns the job id. Its warnings, which name why
    a chunk was given up (``deadlock detected``, ``concurrent DML``), land in ``notices``."""

    def call() -> int:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            raw = connection.connection.driver_connection
            assert isinstance(raw, psycopg.Connection)
            raw.add_notice_handler(
                lambda notice: notices.append(f"{notice.message_primary} {notice.message_detail}")
            )
            connection.execute(text("CALL run_job(:job)"), {"job": job})
        return job

    return call


def wait_for_policy(engine: Engine, thread: threading.Thread) -> bool:
    """Until the session running ``run_job`` waits for a lock on a relation (``True``; the only
    session of the test's database that can wait then) or the job has ended without waiting
    (``False``)."""
    deadline = monotonic() + WAIT
    with engine.connect() as watcher:
        while monotonic() < deadline:
            waiting = watcher.execute(
                text(
                    "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a USING (pid) "
                    "WHERE NOT l.granted AND l.locktype = 'relation' "
                    "AND a.datname = current_database()"
                )
            ).scalar_one()
            watcher.rollback()
            if waiting:
                return True
            if not thread.is_alive():
                return False
            sleep(0.02)
        what = watcher.execute(
            text(
                "SELECT a.wait_event_type, a.wait_event, a.state, l.locktype, l.mode, "
                "CAST(l.relation AS regclass)::text FROM pg_stat_activity a "
                "LEFT JOIN pg_locks l ON l.pid = a.pid AND NOT l.granted "
                "WHERE a.datname = current_database() AND a.pid <> pg_backend_pid()"
            )
        ).all()
    pytest.fail(f"the policy neither waited for a relation nor ended: {what}")


#: A hypertable the policy recompresses by rebuilding the chunk: no index it could segment by.
PROBE = (
    "CREATE TABLE recompress_probe (time timestamptz NOT NULL, v int)",
    "SELECT create_hypertable('recompress_probe', 'time', chunk_time_interval => interval '1 day')",
    "ALTER TABLE recompress_probe SET (timescaledb.compress)",
    "INSERT INTO recompress_probe SELECT CAST(:day AS timestamptz) + n * interval '1 s', n "
    "FROM generate_series(1, 1000) AS n",
)


def test_a_read_then_write_transaction_deadlocks_with_a_rebuilding_recompression(
    db_engine: Engine,
) -> None:
    """The cycle of #149, on a hypertable the policy recompresses by rebuilding the chunk: a
    transaction reads the partial chunk, the policy takes ``ShareLock`` on it and waits for
    ``AccessExclusiveLock`` on its index (the reader holds ``AccessShareLock`` there), the
    transaction then writes the chunk and queues behind the policy's ``ShareLock``. The policy
    waited first, so it is the side aborted (``40P01``, as in #133's loop): the chunk stays
    partial for its next run, and the write goes on."""
    needs_compression(db_engine)
    pause_compression_jobs(db_engine)
    with db_engine.begin() as connection:
        for statement in PROBE:
            connection.execute(text(statement), {"day": DAY})
        (chunk,) = connection.execute(
            text("SELECT CAST(c AS text) FROM show_chunks('recompress_probe') AS c")
        ).scalars()
        connection.execute(text("SELECT compress_chunk(CAST(:c AS regclass))"), {"c": chunk})
        connection.execute(
            text(
                "INSERT INTO recompress_probe "
                "VALUES (CAST(:day AS timestamptz) + interval '5 h', 1)"
            ),
            {"day": DAY},
        )
        job = int(
            connection.execute(
                text("SELECT add_compression_policy('recompress_probe', interval '1 day')")
            ).scalar_one()
        )
        connection.execute(text("SELECT alter_job(:job, scheduled => false)"), {"job": job})
    assert status(db_engine, chunk) & PARTIAL
    other = engine_from_url(db_engine.url)
    try:
        with db_engine.connect() as writer:
            writer.execute(text("SELECT count(*) FROM recompress_probe")).scalar_one()
            notices: list[str] = []
            policy, answer = in_thread(run_policy(other, job, notices))
            assert wait_for_policy(db_engine, policy), f"the policy did not wait: {answer}"
            writer.execute(  # the write that closes the cycle; the policy is aborted
                text("INSERT INTO recompress_probe VALUES (CAST(:day AS timestamptz), 2)"),
                {"day": DAY},
            )
            writer.commit()
            policy.join(WAIT)
    finally:
        other.dispose()
    assert not policy.is_alive()
    assert status(db_engine, chunk) & PARTIAL, "the policy recompressed the chunk anyway"
    assert any("deadlock detected" in notice for notice in notices), notices


def test_an_ingest_batch_and_the_recompression_do_not_deadlock(
    world: World, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """Ingest's order on ``messages``: a batch's COPY takes ``RowExclusiveLock`` on the chunk
    before anything reads it. The policy, coming while the batch is still open, waits for its
    commit or gives the chunk up for its next run; it never closes a cycle with the batch,
    which is not aborted. Every line is loaded once, and the next run recompresses the chunk."""
    needs_compression(db_engine)
    job = messages_policy(db_engine)
    chunk = partial_chunk(world, db_engine)
    copied = threading.Event()
    go_on = threading.Event()

    def hold_after_copy(step: str) -> None:
        if step == "counts":  # the batch's COPY done, its transaction still open
            copied.set()
            assert go_on.wait(WAIT)

    other = engine_from_url(db_engine.url)
    caplog.set_level(logging.WARNING, logger="arcilogger.ingest")
    try:
        service = world.service(db_engine, step_hook=hold_after_copy)
        batch, loaded = in_thread(lambda: service.run(once=True))
        assert copied.wait(WAIT)
        notices: list[str] = []
        policy, answer = in_thread(run_policy(other, job, notices))
        wait_for_policy(db_engine, policy)
        go_on.set()
        batch.join(WAIT)
        policy.join(WAIT)
    finally:
        go_on.set()
        other.dispose()
    assert loaded == [0]
    assert "aborted by a" not in caplog.text
    assert not any("deadlock" in notice for notice in notices), notices
    (result,) = answer  # done after the batch's commit, or the chunk given up for the next run
    assert result == job or any("concurrent DML" in notice for notice in notices), (result, notices)
    run_policy(db_engine, job, [])()  # with no write beside it
    assert status(db_engine, chunk) & COMPRESSED
    assert not status(db_engine, chunk) & PARTIAL
    assert check_exactly_once(db_engine, world) == []
