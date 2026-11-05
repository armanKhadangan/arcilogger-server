"""The search holds the partition lock from before its snapshot (Wave 5 contract §5, G8).

A search reads in one REPEATABLE READ snapshot, taken by the transaction's first statement. When
the lock were taken in that transaction, a search that waits for a step of the window job
``db.window`` would read with the snapshot of before the step: the old cutoff
(``db.window_dropped_before``), while the partitions the step dropped are gone. A site day whose
first three hours lie in the dropped UTC-day partition would then be answered without them and
with ``archived: null`` — a partial day. The search therefore takes the lock shared at session
level before its transaction (``archive.service.hold_window``), so its snapshot has the drop:

* a search that waits for a drop of part of its day answers 409 ``archive_reload_required`` (the
  day is sealed in the vault), never the rest of the day with ``archived: null``; its next page
  waits too and names the day in ``archived``;
* the wait is part of the request's budget: the scan gets what is left of it;
* a search that cannot get the lock within ``search.request_budget_ms`` answers 504
  ``search_timeout`` (audited ``outcome: timeout``);
* the lock never stays on a pooled connection: after an answer, after a failure inside the read,
  after the timeout and after a failure of the statement that takes it, the window job gets the
  lock exclusively at once;
* an export (#148, W5-G-23), which reads its windows in one snapshot as the pages do, holds the
  lock the same way and is not changed by a drop that starts while it reads: the drop waits for
  it, and the file holds the whole day. It waits for the lock as a search does (504
  ``search_timeout``, nothing audited), the wait taken from ``search.export_budget_seconds``,
  and releases it before its audit entry is written; a failure inside the read leaves no lock
  behind;
* the export checks its search's range against the database window again, in its own snapshot
  (W5-G-24): after a drop that finished between the search and the export it answers as a search
  of that range would — 409 ``archive_reload_required`` with the same details and texts, no
  ``search.export`` entry; or the rows the database holds in a file marked partial (``-kismi``,
  ``X-Arci-Export-Truncated: archived``, ``X-Arci-Export-Archived``, the "Bilgi" sheet, the
  entry's ``truncated`` and ``archived``), never a partial file that does not say so;
* a search or export that waited for the lock until its budget was gone says so (the lock-wait
  text of ``search_timeout``); a scan that ran out keeps the text of the shortest time slice.

The day is ten days before today (daily partitions); its two lines are CORE-R1 heartbeats, at
01:00 (UTC-day partition of the day before) and at 12:00 (its own UTC day), written to the spool
and the database and sealed with the mock TSA.
"""

from __future__ import annotations

import asyncio
import csv
import io
import threading
import time
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from arcilogger_api.archive import service as archive_service
from arcilogger_api.archive.models import ArchivedRange
from arcilogger_api.audit import record_audit
from arcilogger_api.lookup_testing import seal_day
from arcilogger_api.routers import search as search_router
from arcilogger_api.routers.search import state_of
from arcilogger_api.search import export as exports
from arcilogger_api.search import windows
from arcilogger_api.search.messages import MESSAGES, VARIANTS, placeholders, texts
from arcilogger_api.search.testing import sign_in
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import day_window, isoformat_site, site_day, utc_now
from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import AuditLog, Message
from arcilogger_db.partitions import (
    PARTITION_LOCK_KEY,
    day_bounds,
    drop_before,
    dropped_before,
    ensure_days,
)
from arcilogger_db.settings import DEFAULT_SETTINGS, set_setting
from arcilogger_worker.testing import Evidence, tr

from .test_search_grammar import OVERSTATEMENT

API = "/api/v1/search"
#: The searched day: ten days old, inside the daily partitions.
DAY = site_day(utc_now()) - timedelta(days=10)
#: Its line in the UTC-day partition of the day before (00:00-03:00 of the site day).
EARLY = tr(DAY, 1)
#: Its line in its own UTC-day partition.
LATE = tr(DAY, 12)
#: The cutoff of the window job's step: drops the UTC-day partition of ``DAY - 1`` and so the
#: first three hours of ``DAY``, keeps the rest of it.
CUTOFF = DAY + timedelta(days=1)
#: ``pg_locks`` names a bigint advisory key by its two 32-bit halves.
KEY_HIGH, KEY_LOW = divmod(PARTITION_LOCK_KEY % 2**64, 2**32)
WAITING = text(
    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted "
    "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()) "
    "AND CAST(classid AS bigint) = :high AND CAST(objid AS bigint) = :low AND objsubid = 1"
)
#: The export's Zaman column of the two lines (site time).
EXPORTED = [isoformat_site(LATE), isoformat_site(EARLY)]
#: The step's statement waits for a lock (the partition lock, or a table the export reads).
STEP_WAITING = text("SELECT count(*) FROM pg_locks WHERE pid = :pid AND NOT granted")


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


@pytest.fixture(autouse=True)
def _no_leftover_slots(app: FastAPI) -> Iterator[None]:
    yield
    assert state_of(app).gate.active == 0


@pytest.fixture
def outside(db_engine: Engine) -> Iterator[Engine]:
    """Connections of the window job and the checks, outside the API's pool: a session does not
    conflict with its own advisory locks, so a check on a pooled API connection would miss one
    left there."""
    engine = engine_from_url(db_engine.url, poolclass=NullPool)
    yield engine
    engine.dispose()


@pytest.fixture
def day(
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    api_config: Config,
    layout: Layout,
) -> date:
    """The two lines of :data:`DAY` in daily partitions, in the database and sealed."""
    with db_engine.begin() as connection:
        ensure_days(connection, DAY - timedelta(days=1), DAY + timedelta(days=1))
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        evidence.heartbeat(EARLY)
        evidence.heartbeat(LATE)
        session.commit()
    seal_day(api_config, layout, DAY)
    return DAY


def search(client: TestClient, *, last: date = DAY, **body: Any) -> Any:
    """A search of :data:`DAY` (to the end of ``last``)."""
    return client.post(
        API,
        json={
            "q": "",
            "from": day_window(DAY)[0].isoformat(),
            "to": day_window(last)[1].isoformat(),
            **body,
        },
    )


def export(client: TestClient, search_id: str, fmt: str = "csv") -> Any:
    return client.post(f"{API}/export", json={"search_id": search_id, "format": fmt})


def exported_times(response: Any) -> list[str]:
    """The Zaman column of an exported CSV file."""
    rows = list(
        csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter=";")
    )
    return [row[0] for row in rows[1:]]


def export_entries(factory: sessionmaker[Session]) -> list[AuditLog]:
    with factory() as session:
        rows = list(session.scalars(select(AuditLog).where(AuditLog.action == "search.export")))
        session.expunge_all()
    return rows


def in_thread(call: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    """``call`` started in a thread; its result (or exception) lands in the list."""
    answer: list[Any] = []

    def run() -> None:
        try:
            answer.append(call())
        except BaseException as exc:  # the test thread re-raises it
            answer.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, answer


def wait_until_blocked(engine: Engine, thread: threading.Thread) -> None:
    """Until a session of the test database waits for the partition lock."""
    deadline = time.monotonic() + 15
    with engine.connect() as watcher:
        while time.monotonic() < deadline:
            waiting = watcher.execute(WAITING, {"high": KEY_HIGH, "low": KEY_LOW}).scalar_one()
            watcher.rollback()
            if waiting:
                return
            assert thread.is_alive(), "the search finished without waiting for the lock"
            time.sleep(0.02)
    pytest.fail("the search never waited for the partition lock")


def exclusive(engine: Engine) -> bool:
    """The window job's lock is free at once: no session (a pooled API connection) holds it."""
    with engine.connect() as other:
        free = other.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY}
        ).scalar_one()
        other.rollback()
    return bool(free)


def soon_exclusive(engine: Engine) -> bool:
    """:func:`exclusive` within a few seconds: a connection closed by the API is ended by the
    server a moment later."""
    deadline = time.monotonic() + 5
    while not exclusive(engine):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def times(connection: Connection) -> list[Any]:
    return list(connection.execute(select(Message.time).order_by(Message.time)).scalars())


def test_a_search_waiting_for_a_drop_sees_it(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    outside: Engine,
    day: date,
) -> None:
    sign_in(client, app_session_factory)
    before = search(client)
    assert before.status_code == 200, before.text
    assert before.json()["archived"] is None
    assert len(before.json()["items"]) == 2
    assert exclusive(outside)

    with outside.connect() as step:
        # The window job's step: the lock first, then the drop, committed later.
        step.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
        thread, answer = in_thread(lambda: search(client))
        try:
            wait_until_blocked(outside, thread)
            drop_before(step, CUTOFF)
            # Part of the day went, the rest is still there: a partial day if read now.
            assert times(step) == [LATE]
            step.commit()
        finally:
            step.rollback()
    thread.join(30)
    assert not thread.is_alive()
    (response,) = answer
    assert not isinstance(response, BaseException), response
    # Never the rest of the day with ``archived: null``: the snapshot has the new cutoff.
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "archive_reload_required"
    assert (error["details"]["first_day"], error["details"]["last_day"]) == (
        DAY.isoformat(),
        DAY.isoformat(),
    )
    assert error["message_tr"] != error["message_en"]
    assert exclusive(outside)


def test_a_search_that_cannot_get_the_lock_in_time_times_out(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    outside: Engine,
    day: date,
) -> None:
    sign_in(client, app_session_factory)
    with app_session_factory() as session:
        set_setting(session, "search.request_budget_ms", 500, updated_by="test")
        session.commit()
    with outside.connect() as step:
        step.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
        began = time.monotonic()
        thread, answer = in_thread(lambda: search(client))
        thread.join(15)
        waited = time.monotonic() - began
        step.rollback()  # a search that still waits gets the lock now and ends
    thread.join(30)
    assert waited < 15, "the search waited for the lock without a bound"
    (response,) = answer
    assert not isinstance(response, BaseException), response
    assert response.status_code == 504, response.text
    error = response.json()["error"]
    assert error["code"] == "search_timeout"
    assert error["message_tr"] != error["message_en"]
    # The lock wait's text (W5-G-24), not the shortest time slice's: nothing was scanned.
    assert (error["message_tr"], error["message_en"]) == texts(
        "search_timeout", {"seconds": 0.5}, variant="lock"
    )
    assert waited > 0.4
    with app_session_factory() as session:
        audited = session.scalars(
            select(AuditLog).where(AuditLog.action == "search.run").order_by(AuditLog.id.desc())
        ).first()
    assert audited is not None
    assert audited.details["outcome"] == "timeout"
    assert exclusive(outside)
    with app_session_factory() as session:
        default = DEFAULT_SETTINGS["search.request_budget_ms"]
        set_setting(session, "search.request_budget_ms", default, updated_by="test")
        session.commit()
    # The same pooled connections serve the next search, without the lock.
    again = search(client)
    assert again.status_code == 200, again.text
    assert exclusive(outside)


def test_the_lock_is_released_when_the_read_fails(
    app: FastAPI,
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    outside: Engine,
    day: date,
) -> None:
    sign_in(client, app_session_factory)

    def failing(_db: Session, _start: datetime, _end: datetime) -> None:
        raise RuntimeError("Örnek arıza / sample failure")

    state_of(app).hook = failing
    try:
        with pytest.raises(RuntimeError, match="sample failure"):
            search(client)
    finally:
        state_of(app).hook = None
    assert exclusive(outside)
    # The next page reads under the lock too, and leaves it.
    found = search(client, limit=1)
    assert found.status_code == 200, found.text
    page = found.json()
    following = client.post(
        f"{API}/page", json={"search_id": page["search_id"], "cursor": page["next_cursor"]}
    )
    assert following.status_code == 200, following.text
    assert following.json()["archived"] is None
    assert exclusive(outside)


def test_an_aborted_read_does_not_keep_the_lock_on_its_connection(
    app: FastAPI,
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    outside: Engine,
    day: date,
) -> None:
    """A statement that fails inside the read (not a timeout) leaves its transaction aborted: the
    release cannot run in it, so the connection is closed instead of going back to the pool."""
    sign_in(client, app_session_factory)

    def failing(db: Session, _start: datetime, _end: datetime) -> None:
        db.execute(text("SELECT 1 / 0"))

    state_of(app).hook = failing
    try:
        with pytest.raises(DBAPIError, match="division by zero"):
            search(client)
    finally:
        state_of(app).hook = None
    assert soon_exclusive(outside)
    found = search(client)
    assert found.status_code == 200, found.text
    assert exclusive(outside)


#: :data:`archive_service._HOLD` failing after the lock is granted (#126): the ``CASE`` takes the
#: lock before it divides, and ``random()`` keeps the division from being folded before the lock.
_HOLD_THEN_FAIL = (
    "SELECT CASE WHEN pg_advisory_lock_shared(%s)::text IS NOT NULL "
    "THEN 1 / CAST(random() * 0 AS integer) END "
    "FROM (SELECT set_config('lock_timeout', %s, true)) AS deadline"
)


def test_a_failed_hold_does_not_keep_the_lock_on_its_connection(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    outside: Engine,
    monkeypatch: pytest.MonkeyPatch,
    day: date,
) -> None:
    """A failure of the statement that takes the lock, other than its timeout, may come after
    the lock was granted (a session lock outlives the failed statement): the connection is
    closed instead of going back to the pool holding it (#126)."""
    sign_in(client, app_session_factory)
    monkeypatch.setattr(archive_service, "_HOLD", _HOLD_THEN_FAIL)
    with pytest.raises(Exception, match="division by zero"):  # psycopg's, not wrapped
        search(client)
    monkeypatch.undo()
    assert soon_exclusive(outside)
    found = search(client)
    assert found.status_code == 200, found.text
    assert exclusive(outside)


class _LostError(Exception):
    """A lost connection (the driver's error; psycopg stays behind arcilogger_db, ADR-0005)."""


class _LockTimeoutError(Exception):
    """The driver's ``lock_not_available``."""

    sqlstate = "55P03"


class _Raw:
    """A DBAPI connection whose statement fails with ``error``."""

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.closed = False
        self.autocommit = False

    def execute(self, *_args: object) -> None:
        raise self.error


class _Connection:
    """The SQLAlchemy connection around :class:`_Raw`: ``invalidate`` closes it."""

    def __init__(self, raw: _Raw) -> None:
        self.connection = SimpleNamespace(driver_connection=raw)
        self.invalidated = 0

    def invalidate(self) -> None:
        self.invalidated += 1
        self.connection.driver_connection.closed = True


@pytest.mark.parametrize(
    "error",
    [KeyboardInterrupt(), asyncio.CancelledError(), _LostError("server closed the connection")],
    ids=["interrupt", "cancelled", "lost"],
)
def test_any_failure_but_the_timeout_invalidates_the_held_connection(error: BaseException) -> None:
    """#126: also an interrupt or a cancelled task (not an ``Exception``) may come after the lock
    was granted: the connection is invalidated and the error goes on unchanged."""
    connection = _Connection(_Raw(error))
    db = cast(Session, SimpleNamespace(connection=lambda: connection))
    with pytest.raises(type(error)) as raised:
        archive_service.hold_window(db, 1)
    assert raised.value is error
    assert connection.invalidated == 1


def test_the_lock_timeout_keeps_the_connection() -> None:
    """The lock's timeout (``lock_not_available``) was never granted: the connection stays, back
    out of autocommit, and the answer is :class:`TimeoutError`."""
    raw = _Raw(_LockTimeoutError("canceling statement due to lock timeout"))
    connection = _Connection(raw)
    db = cast(Session, SimpleNamespace(connection=lambda: connection))
    with pytest.raises(TimeoutError, match="not granted within 1 s"):
        archive_service.hold_window(db, 1)
    assert (connection.invalidated, raw.autocommit) == (0, False)


def test_a_next_page_waiting_for_a_drop_sees_it(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    outside: Engine,
    day: date,
) -> None:
    """The next page holds the lock from before its snapshot as the first page does: one that
    waits for the drop of the day's first hours names the day in ``archived`` (its line at
    01:00 is gone), never ``archived: null``."""
    sign_in(client, app_session_factory)
    first = search(client, limit=1)
    assert first.status_code == 200, first.text
    page = first.json()
    assert len(page["items"]) == 1  # the line at 12:00, newest first
    assert page["next_cursor"] is not None

    with outside.connect() as step:
        step.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
        thread, answer = in_thread(
            lambda: client.post(
                f"{API}/page", json={"search_id": page["search_id"], "cursor": page["next_cursor"]}
            )
        )
        try:
            wait_until_blocked(outside, thread)
            drop_before(step, CUTOFF)
            step.commit()
        finally:
            step.rollback()
    thread.join(30)
    assert not thread.is_alive()
    (response,) = answer
    assert not isinstance(response, BaseException), response
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["archived"] == {
        "first_day": DAY.isoformat(),
        "last_day": DAY.isoformat(),
        "keep_days": 7,
    }
    assert exclusive(outside)


def test_the_wait_for_the_lock_is_taken_from_the_budget(
    app: FastAPI,
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    day: date,
) -> None:
    """The scan of a search gets ``search.request_budget_ms`` less the wait for the lock (on the
    search's clock); a wait that used it up answers 504 ``search_timeout``."""
    sign_in(client, app_session_factory)
    budgets: list[float] = []
    run_page = windows.run_page  # the router's, before it is replaced

    def recording(*args: Any, **kwargs: Any) -> Any:
        budgets.append(args[6])
        return run_page(*args, **kwargs)

    hold = archive_service.hold_window
    now = [100.0]
    waits = iter([1.5, 4.0])

    def slow_hold(db: Session, seconds: float) -> Any:
        held = hold(db, seconds)
        now[0] += next(waits)
        return held

    monkeypatch.setattr(search_router, "run_page", recording)
    monkeypatch.setattr(search_router, "hold_window", slow_hold)
    monkeypatch.setattr(state_of(app), "clock", lambda: now[0])
    found = search(client)
    assert found.status_code == 200, found.text
    assert budgets == [DEFAULT_SETTINGS["search.request_budget_ms"] / 1000 - 1.5]
    used_up = search(client)
    assert used_up.status_code == 504, used_up.text
    error = used_up.json()["error"]
    assert error["code"] == "search_timeout"
    seconds = DEFAULT_SETTINGS["search.request_budget_ms"] / 1000
    assert (error["message_tr"], error["message_en"]) == texts(
        "search_timeout", {"seconds": int(seconds)}, variant="lock"
    )
    assert len(budgets) == 1


def test_an_export_in_progress_is_not_changed_by_a_drop(
    app: FastAPI,
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    reauthenticate: Callable[[LoggedIn], None],
    outside: Engine,
    day: date,
) -> None:
    """#148: the export reads its rows in windows of device time, newest first, in one read-only
    snapshot. It is paused before its first window that reaches the UTC-day partition of
    ``DAY - 1`` (after the window with the line at 12:00), and a step of the window job that
    drops that partition starts then: the step must wait for the export, on the partition lock
    before it locks any table (on plain PostgreSQL it would otherwise wait on ``messages`` while
    holding the lock exclusively, and ingest and new searches with it), and the file holds the
    whole day, never the line at 12:00 alone as if the day were complete."""
    reauthenticate(sign_in(client, app_session_factory))
    found = search(client)
    assert found.status_code == 200, found.text
    assert found.json()["archived"] is None
    assert len(found.json()["items"]) == 2
    early_partition = day_bounds(DAY)[0]  # the line at 01:00 lies before it
    paused, resume = threading.Event(), threading.Event()
    reached: list[datetime] = []

    def pause(_db: Session, lower: datetime, upper: datetime) -> None:
        if lower < early_partition and not paused.is_set():
            reached.append(upper)
            paused.set()
            assert resume.wait(30), "the test never resumed the export"

    state_of(app).hook = pause
    try:
        exporting, exported = in_thread(lambda: export(client, found.json()["search_id"]))
        try:
            assert paused.wait(15), "the export never reached the day before"
            assert reached[0] <= LATE  # the line at 12:00 is read already
            with outside.connect() as step:
                pid = step.execute(text("SELECT pg_backend_pid()")).scalar_one()
                step.commit()

                def run_step() -> list[Any]:
                    drop_before(step, CUTOFF)
                    after = times(step)
                    step.commit()
                    return after

                stepping, stepped = in_thread(run_step)
                # Sessions waiting for the partition lock once the step waits; ``None``: it was
                # never seen waiting (asserted after the export is resumed, so it cannot hang).
                on_the_lock: int | None = None
                deadline = time.monotonic() + 15
                with outside.connect() as watcher:
                    while stepping.is_alive() and time.monotonic() < deadline:
                        waits = watcher.execute(STEP_WAITING, {"pid": pid}).scalar_one()
                        if waits:
                            on_the_lock = watcher.execute(
                                WAITING, {"high": KEY_HIGH, "low": KEY_LOW}
                            ).scalar_one()
                            watcher.rollback()
                            break
                        watcher.rollback()
                        time.sleep(0.02)
                dropped_first = not stepping.is_alive()
                resume.set()
                exporting.join(30)
                stepping.join(30)
                assert not stepping.is_alive()
        finally:
            resume.set()
            exporting.join(30)
    finally:
        state_of(app).hook = None
    assert not exporting.is_alive()
    (response,) = exported
    assert not isinstance(response, BaseException), response
    assert response.status_code == 200, response.text
    assert "X-Arci-Export-Truncated" not in response.headers
    rows = list(
        csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter=";")
    )
    assert [row[0] for row in rows[1:]] == EXPORTED, (
        "the export's later window no longer saw the dropped partition: a partial day"
    )
    assert not dropped_first, "the step dropped the partition while the export was reading"
    assert on_the_lock is not None, "the step neither waited nor finished within 15 s"
    assert on_the_lock == 1, "the step waited on a table the export reads, not the partition lock"
    (after,) = stepped
    assert after == [LATE]  # the step ran once the export had ended
    assert exclusive(outside)


def test_an_export_that_cannot_get_the_lock_in_time_times_out(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    reauthenticate: Callable[[LoggedIn], None],
    outside: Engine,
    day: date,
) -> None:
    """The export waits for the lock at most ``search.request_budget_ms``, as a search does: 504
    ``search_timeout``, no file and so no ``search.export`` entry (the hourly limit counts
    those); its user's export, its slot and its connection are free again."""
    reauthenticate(sign_in(client, app_session_factory))
    found = search(client)
    assert found.status_code == 200, found.text
    with app_session_factory() as session:
        set_setting(session, "search.request_budget_ms", 500, updated_by="test")
        session.commit()
    with outside.connect() as step:
        step.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
        began = time.monotonic()
        thread, answer = in_thread(lambda: export(client, found.json()["search_id"]))
        thread.join(15)
        waited = time.monotonic() - began
        step.rollback()
    thread.join(30)
    assert waited < 15, "the export waited for the lock without a bound"
    (response,) = answer
    assert not isinstance(response, BaseException), response
    assert response.status_code == 504, response.text
    error = response.json()["error"]
    assert error["code"] == "search_timeout"
    assert error["message_tr"] != error["message_en"]
    assert "0.5 saniyede" in error["message_tr"]
    assert "0.5 s" in error["message_en"]
    assert (error["message_tr"], error["message_en"]) == texts(
        "search_timeout", {"seconds": 0.5}, variant="export_lock"
    )
    assert waited > 0.4
    assert export_entries(app_session_factory) == []
    assert exclusive(outside)
    again = export(client, found.json()["search_id"])
    assert again.status_code == 200, again.text
    assert len(export_entries(app_session_factory)) == 1
    assert exclusive(outside)


def test_the_export_takes_the_wait_for_the_lock_from_its_budget(
    app: FastAPI,
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    reauthenticate: Callable[[LoggedIn], None],
    monkeypatch: pytest.MonkeyPatch,
    day: date,
) -> None:
    """The export reads for ``search.export_budget_seconds`` less the wait for the lock (on the
    search's clock); a wait that used it up answers 504 ``search_timeout`` naming that budget,
    not ``search.request_budget_ms``, and no file."""
    reauthenticate(sign_in(client, app_session_factory))
    found = search(client)
    assert found.status_code == 200, found.text
    with app_session_factory() as session:
        set_setting(session, "search.export_budget_seconds", 10, updated_by="test")
        session.commit()
    budgets: list[float] = []
    read_rows = exports.read_rows

    def recording(*args: Any, **kwargs: Any) -> Any:
        budgets.append(kwargs["budget_seconds"])
        return read_rows(*args, **kwargs)

    hold = archive_service.hold_window
    now = [100.0]
    waits = iter([1.5, 12.0])

    def slow_hold(db: Session, seconds: float) -> Any:
        held = hold(db, seconds)
        now[0] += next(waits)
        return held

    monkeypatch.setattr(exports, "read_rows", recording)
    monkeypatch.setattr(search_router, "hold_window", slow_hold)
    monkeypatch.setattr(state_of(app), "clock", lambda: now[0])
    exported = export(client, found.json()["search_id"])
    assert exported.status_code == 200, exported.text
    assert budgets == [10 - 1.5]
    used_up = export(client, found.json()["search_id"])
    assert used_up.status_code == 504, used_up.text
    error = used_up.json()["error"]
    assert error["code"] == "search_timeout"
    assert "kendisine tanınan 10 saniyede" in error["message_tr"]  # its budget, not the wait
    assert "the 10 s it was allowed" in error["message_en"]
    assert (error["message_tr"], error["message_en"]) == texts(
        "search_timeout", {"seconds": 10}, variant="export_lock"
    )
    assert len(budgets) == 1
    assert len(export_entries(app_session_factory)) == 1


def test_the_export_releases_the_lock_before_its_audit_entry(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    reauthenticate: Callable[[LoggedIn], None],
    outside: Engine,
    monkeypatch: pytest.MonkeyPatch,
    day: date,
) -> None:
    """The lock is released with the read, before the ``search.export`` entry is written and
    committed (and so before the file is sent): the window job never waits for an audit entry
    or a download."""
    reauthenticate(sign_in(client, app_session_factory))
    found = search(client)
    assert found.status_code == 200, found.text
    free: list[bool] = []

    def checking(db: Session, **kwargs: Any) -> Any:
        if kwargs["action"] == "search.export":
            free.append(exclusive(outside))
        return record_audit(db, **kwargs)

    monkeypatch.setattr(search_router, "record_audit", checking)
    response = export(client, found.json()["search_id"])
    assert response.status_code == 200, response.text
    assert free == [True]
    assert exclusive(outside)


def test_a_failed_export_does_not_keep_the_lock(
    app: FastAPI,
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    reauthenticate: Callable[[LoggedIn], None],
    outside: Engine,
    day: date,
) -> None:
    """A statement that fails inside the export's read (not a timeout) leaves no lock on a
    pooled connection, no entry and no running export behind."""
    reauthenticate(sign_in(client, app_session_factory))
    found = search(client)
    assert found.status_code == 200, found.text

    def failing(db: Session, _start: datetime, _end: datetime) -> None:
        db.execute(text("SELECT 1 / 0"))

    state_of(app).hook = failing
    try:
        with pytest.raises(DBAPIError, match="division by zero"):
            export(client, found.json()["search_id"])
    finally:
        state_of(app).hook = None
    assert soon_exclusive(outside)
    assert export_entries(app_session_factory) == []
    again = export(client, found.json()["search_id"])
    assert again.status_code == 200, again.text
    assert exclusive(outside)


# -- the export checks the database window (W5-G-24) -----------------------------------------------


def step_drops(engine: Engine) -> None:
    """A step of the window job that finishes between the search and the export: the drop of the
    UTC-day partition of ``DAY - 1`` (``CUTOFF``), committed."""
    with engine.begin() as step:
        drop_before(step, CUTOFF)
        assert EARLY not in times(step)
        assert LATE in times(step)
        assert dropped_before(step) == CUTOFF


def test_an_export_after_a_drop_of_its_whole_range_answers_as_the_search(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    reauthenticate: Callable[[LoggedIn], None],
    outside: Engine,
    day: date,
) -> None:
    """The search of ``DAY`` answered before the drop (``archived: null``, both lines); after it
    every day of its range is gone (the cutoff is after ``DAY``), so the export answers 409
    ``archive_reload_required`` exactly as a search of that range does now, with no file and no
    ``search.export`` entry (the hourly limit counts files) — never the line at 12:00 alone as if
    the day were complete. Fails without the check: 200 with ``truncated: null``."""
    reauthenticate(sign_in(client, app_session_factory))
    found = search(client)
    assert found.status_code == 200, found.text
    assert found.json()["archived"] is None
    assert len(found.json()["items"]) == 2
    step_drops(outside)

    response = export(client, found.json()["search_id"])
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "archive_reload_required"
    assert (error["details"]["first_day"], error["details"]["last_day"]) == (
        DAY.isoformat(),
        DAY.isoformat(),
    )
    again = search(client)  # what a search of the range answers now
    assert again.status_code == 409, again.text
    assert error == again.json()["error"]
    assert export_entries(app_session_factory) == []
    assert exclusive(outside)


def test_an_export_after_a_drop_of_part_of_its_range_is_marked_partial(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    reauthenticate: Callable[[LoggedIn], None],
    layout: Layout,
    outside: Engine,
    day: date,
) -> None:
    """The search of ``DAY`` and ``DAY + 1`` answered before the drop with every line; after it
    ``DAY`` is gone and ``DAY + 1`` is not, so the export gives the rows the database holds, as a
    search of the range does now (with ``archived``), in a file that says so: ``-kismi``,
    ``X-Arci-Export-Truncated: archived``, ``X-Arci-Export-Archived`` and the "Bilgi" sheet
    naming the days, and the entry's ``truncated`` and ``archived`` (the search's ``archived``).
    Where the reading stopped (``rows``) stays the header's and the entry's ``truncated``, with
    ``X-Arci-Export-Archived`` and ``archived`` beside it.
    Fails without the check: no marking, ``truncated: null``."""
    following = tr(DAY + timedelta(days=1), 12)
    with app_session_factory() as session:
        Evidence(session, layout).heartbeat(following)
        session.commit()
    reauthenticate(sign_in(client, app_session_factory))
    found = search(client, last=DAY + timedelta(days=1))
    assert found.status_code == 200, found.text
    assert found.json()["archived"] is None
    assert len(found.json()["items"]) == 3
    step_drops(outside)
    again = search(client, last=DAY + timedelta(days=1))  # what a search answers now
    assert again.status_code == 200, again.text
    archived = again.json()["archived"]
    assert archived == {"first_day": DAY.isoformat(), "last_day": DAY.isoformat(), "keep_days": 7}
    shown = [isoformat_site(datetime.fromisoformat(item["time"])) for item in again.json()["items"]]
    assert shown == [isoformat_site(following), isoformat_site(LATE)]

    response = export(client, found.json()["search_id"])
    assert response.status_code == 200, response.text
    assert response.headers["X-Arci-Export-Truncated"] == "archived"
    assert response.headers["X-Arci-Export-Archived"] == DAY.isoformat()
    assert '-kismi.csv"' in response.headers["content-disposition"]
    assert exported_times(response) == shown
    (entry,) = export_entries(app_session_factory)
    assert entry.details["truncated"] == "archived"
    assert entry.details["archived"] == archived
    assert entry.details["rows"] == 2

    book = export(client, found.json()["search_id"], "xlsx")
    assert book.status_code == 200, book.text
    assert '-kismi.xlsx"' in book.headers["content-disposition"]
    info = {
        row[0].value: row[1].value
        for row in load_workbook(io.BytesIO(book.content))["Bilgi"].iter_rows()
    }
    assert info["Durum / Status"] == " / ".join(exports.TRUNCATION_TEXTS["archived"])
    assert info[exports.ARCHIVED_LABEL] == DAY.isoformat()  # one day, named once

    with app_session_factory() as session:
        set_setting(session, "search.export_max_rows", 1, updated_by="test")
        session.commit()
    cut = export(client, found.json()["search_id"])
    assert cut.status_code == 200, cut.text
    assert cut.headers["X-Arci-Export-Truncated"] == "rows"
    assert cut.headers["X-Arci-Export-Archived"] == DAY.isoformat()  # the CSV names the days too
    assert '-kismi.csv"' in cut.headers["content-disposition"]
    *_, last = export_entries(app_session_factory)
    assert (last.details["truncated"], last.details["archived"]) == ("rows", archived)
    assert exclusive(outside)


def test_the_days_gone_are_named_once_for_one_day_and_as_a_span_for_more() -> None:
    """The "Bilgi" line and ``X-Arci-Export-Archived``: one day once, else first and last."""
    one = ArchivedRange(first_day=DAY, last_day=DAY, keep_days=7)
    two = ArchivedRange(first_day=DAY, last_day=DAY + timedelta(days=1), keep_days=7)
    following = (DAY + timedelta(days=1)).isoformat()
    assert exports.archived_header(one) == DAY.isoformat()
    assert exports.archived_header(two) == f"{DAY.isoformat()}/{following}"
    info = exports.Info(query="*", start=EARLY, end=LATE, username="u", created_at=LATE)
    rows = dict(exports.info_rows(info, exports.Reading(archived=two)))
    assert rows[exports.ARCHIVED_LABEL] == f"{DAY.isoformat()} - {following}"
    assert rows["Durum / Status"] == " / ".join(exports.TRUNCATION_TEXTS["archived"])
    assert exports.ARCHIVED_LABEL not in dict(exports.info_rows(info, exports.Reading()))


# -- the lock-wait text of search_timeout (W5-G-24) ------------------------------------------------


@pytest.mark.parametrize("variant", ["lock", "export_lock"])
def test_the_lock_wait_texts_are_turkish_and_english_with_the_same_placeholders(
    variant: str,
) -> None:
    """As every text of the catalogue (``test_search_grammar``), and not the slice's."""
    message_tr, message_en = VARIANTS["search_timeout", variant]
    for message in (message_tr, message_en):
        assert message.strip() == message
        assert message.endswith(".")
        assert "5 " not in message  # not the shortest time slice (5 minutes)
    assert message_tr != message_en
    assert placeholders(message_tr) == placeholders(message_en) == {"seconds"}
    assert (message_tr, message_en) != MESSAGES["search_timeout"]
    shown = texts("search_timeout", {"seconds": 4}, variant=variant)
    for message in (*shown, message_tr, message_en):
        assert not OVERSTATEMENT.search(message), message
    assert "{" not in "".join(shown)  # every placeholder filled
    assert "4 saniyede" in shown[0]
    assert "4 s" in shown[1]
    assert shown[0].startswith("Dışa aktarma" if variant == "export_lock" else "Arama")


def test_a_scan_that_runs_out_keeps_the_text_of_the_time_slice(
    app: FastAPI,
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    day: date,
) -> None:
    """The lock was granted at once and the scan used the budget up: the text of the shortest
    time slice, not the lock wait's."""
    sign_in(client, app_session_factory)
    with app_session_factory() as session:
        set_setting(session, "search.request_budget_ms", 500, updated_by="test")
        session.commit()

    def always_slow(db: Session, _lower: datetime, _upper: datetime) -> None:
        db.execute(text("SELECT pg_sleep(5)"))

    state_of(app).hook = always_slow
    try:
        response = search(client, q="srcip:172.20.41.5")
    finally:
        state_of(app).hook = None
    assert response.status_code == 504, response.text
    error = response.json()["error"]
    assert error["code"] == "search_timeout"
    assert (error["message_tr"], error["message_en"]) == texts("search_timeout", {"seconds": 0.5})
