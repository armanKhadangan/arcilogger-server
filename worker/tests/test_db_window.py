"""``db.window`` (Wave 5 contract G3): the cutoff — the window, the oldest unsealed day less the
raw-day margin (a day before ``seal.first_day`` counts as sealed; a sealed day whose manifest is
gone does not), the oldest day of an active archive reload —, the expiry of reloads after
``db.reload_keep_days``, the ``R20-db-window`` alert of a failed run and of a reload without
progress (its texts, and an e-mail that names it), the audit entries of a step that dropped
something and of the upgrade's change of ``db.retention_days``, and the job's place in the worker
(daily after 04:00).

The database tests run on the template's storage: plain PostgreSQL (rows of other months in the
default partition, daily partitions made by the job) or TimescaleDB in the Linux CI job (chunks
of a UTC day). Both drop the rows of days well before the cutoff and keep every row from the
cutoff's local midnight on; the rows of the day just before the cutoff are not asserted (a
TimescaleDB chunk holding the cutoff is kept whole)."""

from __future__ import annotations

import html
import importlib.util
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select, text, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import arcilogger_db
from arcilogger_core.config import Config, SealConfig
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import day_window, site_day
from arcilogger_db.audit import verify_audit_chain
from arcilogger_db.engine import engine_from_url, session_factory
from arcilogger_db.migrate import upgrade
from arcilogger_db.models import Alert, ArchiveReload, AuditLog, JobRun, MtDhcp, Setting
from arcilogger_db.partitions import DAYS_PER_RUN
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_db.testing import TestServer, pause_compression_jobs
from arcilogger_worker import db_window
from arcilogger_worker.alerts.mail import MailItem, render_html, render_text, subject
from arcilogger_worker.alerts.texts import (
    DESCRIBERS,
    GENERIC,
    RULES,
    UNKNOWN_RULE,
    RuleText,
    describe,
    runbook,
)
from arcilogger_worker.db_window import (
    CHANGE_KEY,
    PROGRESSED_REASON,
    RESOLVED_REASON,
    STUCK_KEY,
    WINDOW_KEY,
    WINDOW_RULE,
    Cutoff,
    job_db_window,
    oldest_unsealed_day,
)
from arcilogger_worker.service import JobContext, Worker, default_jobs
from arcilogger_worker.testing import tr

#: A site day of the past: the template's monthly partition is the real current month, so every
#: row of these tests lands in the default partition until the job makes its days.
TODAY = date(2026, 6, 15)
NOW = tr(TODAY, 5)
WINDOW = int(DEFAULT_SETTINGS["db.retention_days"])
KEEP = int(DEFAULT_SETTINGS["db.reload_keep_days"])
URL = "https://arcilogger.example.com"


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def _config(layout: Layout, first_day: date | None = None) -> Config:
    return Config(
        data_dir=layout.root, secrets_dir=layout.root, seal=SealConfig(first_day=first_day)
    )


def _seal_state(layout: Layout, day: date, status: str) -> None:
    path = layout.seal_state(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"day": day.isoformat(), "status": status}), encoding="utf-8")


def _manifest(layout: Layout, day: date) -> None:
    """A manifest of a day that had no source (enough for the job: it names no file)."""
    path = layout.manifest(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"day": day.isoformat(), "files": []}), encoding="utf-8")


def _sealed(layout: Layout, first: date, last: date, *, except_: tuple[date, ...] = ()) -> None:
    """``state/seal`` and the manifests of the sealed days ``first`` … ``last``; the days of
    ``except_`` failed."""
    day = first
    while day <= last:
        _seal_state(layout, day, "failed" if day in except_ else "sealed")
        if day not in except_:
            _manifest(layout, day)
        day += timedelta(days=1)


def _noon(day: date) -> datetime:
    return day_window(day)[0].astimezone(UTC) + timedelta(hours=12)


def _rows(engine: Engine, days: list[date], *, times: dict[date, datetime] | None = None) -> None:
    """One row of each raw day at its noon, or at the device time ``times`` gives it."""
    with engine.begin() as connection:
        connection.execute(
            insert(MtDhcp),
            [
                {
                    "time": (times or {}).get(day, _noon(day)),
                    "time_received": _noon(day),
                    "source": "core-r1",
                    "sender": "172.20.10.1",
                    "raw_day": day,
                    "raw_line": 1,
                    "raw_offset": 0,
                    "server": "dhcp60",
                    "vlan": 60,
                    "action": "assigned",
                    "ip": "172.20.60.183",
                    "mac": "00:00:5e:00:53:46",
                }
                for day in days
            ],
        )


def _raw_days_left(engine: Engine) -> list[date]:
    with engine.connect() as connection:
        return list(connection.execute(select(MtDhcp.raw_day).order_by(MtDhcp.time)).scalars())


def _before_midnight(day: date, minutes: int) -> datetime:
    """``minutes`` before the site midnight that starts ``day``."""
    return day_window(day)[0].astimezone(UTC) - timedelta(minutes=minutes)


def _days_left(engine: Engine) -> list[date]:
    with engine.connect() as connection:
        times = connection.execute(select(MtDhcp.time).order_by(MtDhcp.time)).scalars()
        return [site_day(moment) for moment in times]


def _reload(session: Session, first: date, last: date, status: str, **values: Any) -> int:
    reload = ArchiveReload(
        requested_by="operator", reason="search", first_day=first, last_day=last, status=status
    )
    for name, value in values.items():
        setattr(reload, name, value)
    session.add(reload)
    session.commit()
    return reload.id


def _context(engine: Engine, layout: Layout, now: datetime, config: Config) -> JobContext:
    return JobContext(engine, session_factory(engine), now, layout, config)


def _storage(engine: Engine) -> str:
    """``partitions`` (plain PostgreSQL) or ``hypertables`` (TimescaleDB)."""
    with engine.connect() as connection:
        storage = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
    return str(storage["storage"])


def _audit(session: Session, action: str) -> list[AuditLog]:
    session.expire_all()
    return list(
        session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
    )


def _window_worker(engine: Engine, layout: Layout, clock: Clock) -> Worker:
    """A worker with the one job, so its runs are recorded (``job_runs``) as in service."""
    job = next(job for job in default_jobs(60) if job.name == "db.window")
    return Worker(engine=engine, layout=layout, jobs=[job], clock=clock, config=_config(layout))


@pytest.fixture
def engine(db_engine: Engine) -> Engine:
    """The template's database; on TimescaleDB no compression policy runs during the test."""
    if _storage(db_engine) == "hypertables":
        pause_compression_jobs(db_engine)
    return db_engine


@pytest.fixture
def empty_engine(pg_server: TestServer) -> Iterator[Engine]:
    """A database of its own, migrated by the test (plain PostgreSQL)."""
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        pg_server.drop_database(name)


# -- the job's place --------------------------------------------------------------------------


def test_the_job_runs_daily_after_four_and_is_never_gated() -> None:
    (job,) = [job for job in default_jobs(60) if job.name == "db.window"]
    assert job.func is job_db_window
    assert (job.daily_after, job.every, job.module) == (time(4, 0), None, None)
    names = [job.name for job in default_jobs(60)]
    assert names.index("db.window") > names.index("partitions.ensure")


# -- the cutoff -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("unsealed", "reload", "day", "limited_by"),
    [
        (TODAY, None, date(2026, 5, 16), "window"),
        # An unsealed day keeps the two days before it: its rows reach into them.
        (date(2026, 5, 18), None, date(2026, 5, 16), "window"),  # a tie: the window
        (date(2026, 5, 17), None, date(2026, 5, 15), "unsealed"),
        (date(2026, 5, 2), None, date(2026, 4, 30), "unsealed"),
        # A reload's days are device-time days: kept from their first midnight on.
        (TODAY, date(2026, 4, 10), date(2026, 4, 10), "reload"),
        (date(2026, 3, 1), date(2026, 4, 10), date(2026, 2, 27), "unsealed"),
        (TODAY, date(2026, 6, 1), date(2026, 5, 16), "window"),  # a reload inside the window
        (None, date(2026, 4, 10), None, "nothing_sealed"),
    ],
)
def test_the_cutoff_is_the_earliest_limit(
    unsealed: date | None, reload: date | None, day: date | None, limited_by: str
) -> None:
    cutoff = Cutoff(window=TODAY - timedelta(days=30), unsealed=unsealed, reload=reload)
    assert (cutoff.day, cutoff.limited_by) == (day, limited_by)


def test_a_sealed_day_whose_manifest_is_unreadable_holds_the_cutoff() -> None:
    cutoff = Cutoff(
        window=TODAY - timedelta(days=30), unsealed=TODAY, reload=None, unreadable=date(2026, 5, 5)
    )
    assert (cutoff.day, cutoff.limited_by) == (date(2026, 5, 3), "manifest")


def test_the_oldest_unsealed_day_comes_from_the_sealing_records(layout: Layout) -> None:
    config = _config(layout)
    assert oldest_unsealed_day(config, NOW) is None  # nothing sealed: nothing may go
    _sealed(layout, date(2026, 4, 1), TODAY - timedelta(days=1))
    assert oldest_unsealed_day(config, NOW) == TODAY  # every closed day sealed: today
    _seal_state(layout, date(2026, 5, 20), "skipped")  # an empty day counts as sealed
    _seal_state(layout, date(2026, 5, 21), "deleted")
    assert oldest_unsealed_day(config, NOW) == TODAY
    _seal_state(layout, date(2026, 5, 3), "failed")
    layout.seal_state(date(2026, 5, 9)).unlink()  # never attempted
    assert oldest_unsealed_day(config, NOW) == date(2026, 5, 3)
    # A day before seal.first_day counts as sealed.
    assert oldest_unsealed_day(_config(layout, date(2026, 5, 5)), NOW) == date(2026, 5, 9)
    assert oldest_unsealed_day(_config(layout, date(2026, 5, 10)), NOW) == TODAY


def test_before_the_first_seal_only_seal_first_day_lets_days_go(layout: Layout) -> None:
    """No day is sealed yet: the days from ``seal.first_day`` wait for their seal, the days
    before it count as sealed (they are never sealed)."""
    assert oldest_unsealed_day(_config(layout, date(2026, 5, 1)), NOW) == date(2026, 5, 1)
    assert oldest_unsealed_day(_config(layout, TODAY + timedelta(days=3)), NOW) == TODAY


# -- the job on the database ------------------------------------------------------------------


def test_an_unsealed_day_is_never_dropped(engine: Engine, layout: Layout) -> None:
    """Not even in part: its rows before its midnight (a line received just after midnight with
    a device time before it, a clock 25 hours behind) lie in the two days' partitions before it,
    which stay as well (the boundary is asserted, on TimescaleDB too: the cutoff's midnight)."""
    unsealed = date(2026, 5, 1)
    _sealed(layout, date(2026, 3, 1), TODAY - timedelta(days=1), except_=(unsealed,))
    _rows(engine, [date(2026, 4, 10), date(2026, 4, 25), unsealed, date(2026, 5, 3), TODAY])
    with engine.begin() as connection:  # two more rows of the unsealed raw day, before it
        for minutes in (10, 24 * 60 + 50):  # 30.04. 23:50 and 29.04. 23:10 site time
            connection.execute(
                insert(MtDhcp).values(
                    time=_before_midnight(unsealed, minutes),
                    time_received=_noon(unsealed),
                    source="core-r1",
                    sender="172.20.10.1",
                    raw_day=unsealed,
                    raw_line=2,
                    raw_offset=0,
                    server="dhcp60",
                    vlan=60,
                    action="assigned",
                    ip="172.20.60.184",
                    mac="00:00:5e:00:53:47",
                )
            )
    details = job_db_window(_context(engine, layout, NOW, _config(layout)))
    assert details["cutoff"] == details["applied"] == "2026-04-29"
    assert details["limited_by"] == "unsealed"
    assert details["unsealed_from"] == "2026-05-01"
    assert details["window_from"] == (TODAY - timedelta(days=WINDOW)).isoformat()
    assert details["done"] is True
    assert _raw_days_left(engine) == [unsealed, unsealed, unsealed, date(2026, 5, 3), TODAY]
    # Once the day is sealed, the window applies.
    _sealed(layout, unsealed, unsealed)
    details = job_db_window(_context(engine, layout, NOW, _config(layout)))
    assert (details["cutoff"], details["limited_by"]) == ("2026-05-16", "window")
    assert _days_left(engine) == [TODAY]


def test_nothing_is_dropped_before_the_first_seal(engine: Engine, layout: Layout) -> None:
    _rows(engine, [date(2026, 1, 5), TODAY])
    details = job_db_window(_context(engine, layout, NOW, _config(layout)))
    assert (details["cutoff"], details["limited_by"]) == (None, "nothing_sealed")
    assert details["dropped"] == details["purged"] == 0
    assert _days_left(engine) == [date(2026, 1, 5), TODAY]


def test_a_day_before_seal_first_day_counts_as_sealed(engine: Engine, layout: Layout) -> None:
    """Days never sealed before a ``seal.first_day`` that moved the chain start forward go with
    the window; without it they hold the cutoff."""
    first_day = date(2026, 4, 20)
    _sealed(layout, date(2026, 3, 1), date(2026, 3, 31))
    _sealed(layout, first_day, TODAY - timedelta(days=1), except_=(date(2026, 5, 10),))
    _rows(engine, [date(2026, 3, 15), date(2026, 4, 5), date(2026, 5, 10), TODAY])
    details = job_db_window(_context(engine, layout, NOW, _config(layout)))
    assert (details["cutoff"], details["limited_by"]) == ("2026-03-30", "unsealed")
    assert _days_left(engine) == [date(2026, 4, 5), date(2026, 5, 10), TODAY]
    details = job_db_window(_context(engine, layout, NOW, _config(layout, first_day)))
    assert (details["cutoff"], details["limited_by"]) == ("2026-05-08", "unsealed")
    assert _days_left(engine) == [date(2026, 5, 10), TODAY]


def test_a_sealed_day_without_its_manifest_is_never_dropped(engine: Engine, layout: Layout) -> None:
    """``state/seal`` says sealed, but the manifest (or a file it names) is gone: the day could
    not come back from the vault, so it holds the cutoff like an unsealed day."""
    _sealed(layout, date(2026, 3, 1), TODAY - timedelta(days=1))
    broken, gone = date(2026, 4, 20), date(2026, 4, 25)
    layout.manifest(broken).write_text("{not json", encoding="utf-8")
    manifest = {
        "files": [
            {
                "source": "core-r1",
                "path": "2026/04/25/core-r1.log.gz",
                "bytes_uncompressed": 10,
                "lines": 1,
            }
        ]
    }
    layout.manifest(gone).write_text(json.dumps(manifest), encoding="utf-8")  # file not there
    _rows(engine, [date(2026, 4, 10), broken, gone, TODAY])
    details = job_db_window(_context(engine, layout, NOW, _config(layout)))
    assert (details["cutoff"], details["limited_by"]) == ("2026-04-18", "manifest")
    assert details["unreadable_manifest"] == broken.isoformat()
    assert _days_left(engine) == [broken, gone, TODAY]
    _manifest(layout, broken)  # repaired from the replica: the next one holds it
    details = job_db_window(_context(engine, layout, NOW, _config(layout)))
    assert (details["cutoff"], details["limited_by"]) == ("2026-04-23", "manifest")
    assert _days_left(engine) == [gone, TODAY]


@pytest.mark.parametrize("status", ["queued", "loading", "ready"])
def test_an_active_reload_is_never_dropped(
    engine: Engine, db_session: Session, layout: Layout, status: str
) -> None:
    _sealed(layout, date(2026, 1, 1), TODAY - timedelta(days=1))
    values: dict[str, Any] = {}
    if status == "ready":
        values = {"ready_at": NOW - timedelta(days=1), "expires_at": NOW + timedelta(days=6)}
    if status != "ready":  # waiting for two days: named, still held
        values = {"requested_at": NOW - timedelta(days=2)}
    active = _reload(db_session, date(2026, 4, 10), date(2026, 4, 12), status, **values)
    _reload(db_session, date(2026, 2, 1), date(2026, 2, 2), "failed", error_code="raw_unreadable")
    _rows(engine, [date(2026, 2, 1), date(2026, 4, 5), date(2026, 4, 11), date(2026, 5, 20)])
    # The reload's first day from its midnight on (the boundary, on TimescaleDB too).
    _rows(
        engine,
        [date(2026, 4, 10)],
        times={date(2026, 4, 10): _before_midnight(date(2026, 4, 10), -5)},
    )
    details = job_db_window(_context(engine, layout, NOW, _config(layout)))
    assert (details["cutoff"], details["limited_by"]) == ("2026-04-10", "reload")
    assert details["reload_from"] == "2026-04-10"
    assert details["reloads_waiting"] == ([] if status == "ready" else [active])
    assert _days_left(engine) == [date(2026, 4, 10), date(2026, 4, 11), date(2026, 5, 20)]
    db_session.expire_all()
    statuses = db_session.scalars(select(ArchiveReload.status).order_by(ArchiveReload.id)).all()
    assert statuses == [status, "failed"]


def test_a_reload_expires_after_the_keep_days_and_its_days_go(
    engine: Engine, db_session: Session, layout: Layout
) -> None:
    """``expires_at`` = ``ready_at`` + ``db.reload_keep_days`` (written by ArciIngest): the run
    before it keeps the days, the first run after it marks the reload ``expired`` and drops
    them."""
    _sealed(layout, date(2026, 1, 1), TODAY + timedelta(days=KEEP))
    ready_at = NOW - timedelta(hours=1)
    expires_at = ready_at + timedelta(days=KEEP)
    reload_id = _reload(
        db_session,
        date(2026, 4, 10),
        date(2026, 4, 12),
        "ready",
        ready_at=ready_at,
        expires_at=expires_at,
    )
    _rows(engine, [date(2026, 4, 11), TODAY])
    before = expires_at - timedelta(minutes=1)
    details = job_db_window(_context(engine, layout, before, _config(layout)))
    assert (details["expired"], details["limited_by"]) == (0, "reload")
    assert _days_left(engine) == [date(2026, 4, 11), TODAY]
    details = job_db_window(_context(engine, layout, expires_at, _config(layout)))
    assert (details["expired"], details["limited_by"]) == (1, "window")
    assert details["reload_from"] is None
    assert _days_left(engine) == [TODAY]
    db_session.expire_all()
    reload = db_session.get(ArchiveReload, reload_id)
    assert reload is not None
    assert (reload.status, reload.ready_at, reload.expires_at) == ("expired", ready_at, expires_at)


# -- a failed run -----------------------------------------------------------------------------


def test_a_failed_run_drops_nothing_and_raises_r20_db_window(
    engine: Engine, db_session: Session, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run fails (and is retried by the worker), the transaction is rolled back — the
    expired reload stays ``ready`` —, one medium alert with the key ``R20-db-window:none`` is
    opened and kept up to date by the next failure; the next successful run resolves it."""
    _sealed(layout, date(2026, 1, 1), TODAY - timedelta(days=1))
    reload_id = _reload(
        db_session,
        date(2026, 4, 10),
        date(2026, 4, 12),
        "ready",
        ready_at=NOW - timedelta(days=KEEP + 1),
        expires_at=NOW - timedelta(days=1),
    )
    _rows(engine, [date(2026, 4, 11), TODAY])

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("could not obtain lock on relation mt_dhcp")

    monkeypatch.setattr(db_window, "maintain_window", broken)
    clock = Clock(NOW)
    job = next(job for job in default_jobs(60) if job.name == "db.window")
    worker = Worker(engine=engine, layout=layout, jobs=[job], clock=clock, config=_config(layout))
    assert worker.tick() == [("db.window", "failed")]
    clock.now = worker.states["db.window"].retry_at or NOW
    assert worker.tick() == [("db.window", "failed")]
    db_session.expire_all()
    (alert,) = db_session.scalars(select(Alert)).all()
    assert (alert.rule, alert.severity, alert.state) == ("R20-db-window", "medium", "open")
    assert alert.dedup_key == WINDOW_KEY == "R20-db-window:none"
    assert alert.details["job"] == "db.window"
    assert alert.details["error"] == "RuntimeError"
    assert "mt_dhcp" not in json.dumps(alert.details)  # no error text from the database
    assert alert.title.startswith("Veritabanı penceresi işi (db.window)")
    assert alert.details["title_en"].startswith("Database window job (db.window) failed")
    assert _days_left(engine) == [date(2026, 4, 11), TODAY]
    reload = db_session.get(ArchiveReload, reload_id)
    assert reload is not None
    assert reload.status == "ready"  # rolled back with the drop
    failed = db_session.scalars(select(JobRun).where(JobRun.job == "db.window")).all()
    assert [run.status for run in failed] == ["failed", "failed"]

    monkeypatch.undo()
    clock.now = worker.states["db.window"].retry_at or NOW
    assert worker.tick() == [("db.window", "ok")]
    db_session.expire_all()
    db_session.refresh(alert)
    assert alert.state == "resolved"
    assert alert.details["auto_resolved"]["reason"] == RESOLVED_REASON
    assert _days_left(engine) == [TODAY]
    run = db_session.scalars(
        select(JobRun).where(JobRun.job == "db.window").order_by(JobRun.id.desc())
    ).first()
    assert run is not None
    assert run.details["resolved"] == alert.id
    assert run.details["expired"] == 1


def test_a_failure_after_the_steps_fails_the_run_and_keeps_the_drop(
    engine: Engine, db_session: Session, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report of the reloads without progress fails after the steps: the run fails (the
    worker retries it), the drop and its audit entry stay, and no ``R20-db-window`` alert says
    that nothing was dropped (module text)."""
    _sealed(layout, date(2026, 1, 1), TODAY - timedelta(days=1))
    _rows(engine, [date(2026, 4, 10), TODAY])

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the alerts cannot be written")

    monkeypatch.setattr(db_window, "report_stuck_reloads", broken)
    job = next(job for job in default_jobs(60) if job.name == "db.window")
    clock = Clock(NOW)
    worker = Worker(engine=engine, layout=layout, jobs=[job], clock=clock, config=_config(layout))
    assert worker.tick() == [("db.window", "failed")]
    db_session.expire_all()
    assert _days_left(engine) == [TODAY]
    assert len(_audit(db_session, "db.window")) == 1
    assert db_session.scalars(select(Alert)).all() == []
    monkeypatch.undo()
    clock.now = worker.states["db.window"].retry_at or NOW
    assert worker.tick() == [("db.window", "ok")]
    db_session.expire_all()
    assert len(_audit(db_session, "db.window")) == 1  # nothing left to drop


# -- a reload without progress ------------------------------------------------------------------


def _run_window(worker: Worker, db_session: Session) -> dict[str, Any]:
    """One successful run of the job; its details."""
    assert worker.tick() == [("db.window", "ok")]
    db_session.expire_all()
    last = db_session.scalars(
        select(JobRun).where(JobRun.job == "db.window").order_by(JobRun.id.desc())
    ).first()
    assert last is not None
    return last.details


def _window_alerts(db_session: Session) -> dict[str, Alert]:
    db_session.expire_all()
    return {
        str(alert.dedup_key): alert
        for alert in db_session.scalars(select(Alert).where(Alert.rule == WINDOW_RULE))
    }


def _set_reload(engine: Engine, reload_id: int, **values: Any) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(ArchiveReload).where(ArchiveReload.id == reload_id).values(**values)
        )


def test_a_reload_without_progress_raises_r20_db_window_and_keeps_its_days(
    engine: Engine, db_session: Session, layout: Layout
) -> None:
    """A ``loading`` reload whose status and ``bytes_done`` are the ones the previous successful
    run recorded opens one medium alert, naming it and its days; the reload queued behind it is
    not named (manager answer 2 on #121); the cutoff keeps waiting at its first day; the alert
    resolves once the reload moves on or ends."""
    _sealed(layout, date(2026, 1, 1), TODAY + timedelta(days=5))
    loading = _reload(
        db_session, date(2026, 4, 10), date(2026, 4, 12), "loading", bytes_total=900, bytes_done=100
    )
    queued = _reload(db_session, date(2026, 3, 1), date(2026, 3, 2), "queued", bytes_total=50)
    _rows(engine, [date(2026, 2, 20), date(2026, 3, 1), date(2026, 4, 11), TODAY])
    clock = Clock(NOW)
    worker = _window_worker(engine, layout, clock)

    details = _run_window(worker, db_session)  # the first run: nothing to compare with
    assert details["reload_progress"] == {
        str(queued): {"status": "queued", "bytes_done": 0},
        str(loading): {"status": "loading", "bytes_done": 100},
    }
    assert details["reloads_stuck"] == []
    assert _window_alerts(db_session) == {}

    # A day later neither moved: the loading one is named, the queued one waits behind it.
    clock.now = NOW + timedelta(days=1)
    details = _run_window(worker, db_session)
    assert details["reloads_stuck"] == [loading]
    assert (details["cutoff"], details["limited_by"]) == ("2026-03-01", "reload")
    (alert,) = _window_alerts(db_session).values()
    assert alert.dedup_key == f"{STUCK_KEY}{loading}" == f"R20-db-window:reload-{loading}"
    assert (alert.rule, alert.severity, alert.state) == ("R20-db-window", "medium", "open")
    assert {key: alert.details[key] for key in ("reload_id", "first_day", "last_day")} == {
        "reload_id": loading,
        "first_day": "2026-04-10",
        "last_day": "2026-04-12",
    }
    assert (alert.details["status"], alert.details["bytes_done"]) == ("loading", 100)
    assert "operator" not in json.dumps(alert.details) + alert.title  # never who asked
    assert alert.title.startswith(f"Arşivden yeniden yükleme #{loading} (10.04.2026 - 12.04.2026)")
    assert alert.details["title_en"].startswith(
        f"Archive reload #{loading} (2026-04-10 - 2026-04-12) has made no progress"
    )
    assert _days_left(engine) == [date(2026, 3, 1), date(2026, 4, 11), TODAY]

    # The next day still nothing moved: the same one alert, the cutoff still waits.
    clock.now = NOW + timedelta(days=2)
    details = _run_window(worker, db_session)
    assert details["reloads_stuck"] == [loading]
    assert set(_window_alerts(db_session)) == {f"{STUCK_KEY}{loading}"}
    assert details["cutoff"] == "2026-03-01"
    assert _days_left(engine) == [date(2026, 3, 1), date(2026, 4, 11), TODAY]

    # The loading one is ready, the queued one starts loading: the alert resolves.
    clock.now = NOW + timedelta(days=3)
    _set_reload(
        engine,
        loading,
        status="ready",
        bytes_done=900,
        ready_at=clock.now,
        expires_at=clock.now + timedelta(days=KEEP),
    )
    _set_reload(engine, queued, status="loading")
    details = _run_window(worker, db_session)
    assert details["reloads_stuck"] == []
    assert details["reload_progress"] == {str(queued): {"status": "loading", "bytes_done": 0}}
    (alert,) = _window_alerts(db_session).values()
    assert alert.state == "resolved"
    assert alert.details["auto_resolved"]["reason"] == PROGRESSED_REASON
    assert details["cutoff"] == "2026-03-01"  # the reload, now loading, still holds it
    assert _days_left(engine) == [date(2026, 3, 1), date(2026, 4, 11), TODAY]


def test_a_reload_queued_behind_a_progressing_load_raises_no_alert(
    engine: Engine, db_session: Session, layout: Layout
) -> None:
    """A queue that moves is no incident (manager answer 2 on #121): a ``queued`` reload
    unchanged since the previous run raises nothing while the ``loading`` one before it makes
    progress, nor in the run that sees that one end, nor while one made since that run loads;
    the cutoff keeps waiting at its first day."""
    _sealed(layout, date(2026, 1, 1), TODAY + timedelta(days=5))
    loading = _reload(
        db_session, date(2026, 4, 10), date(2026, 4, 12), "loading", bytes_total=900, bytes_done=100
    )
    queued = _reload(db_session, date(2026, 3, 1), date(2026, 3, 2), "queued", bytes_total=50)
    clock = Clock(NOW)
    worker = _window_worker(engine, layout, clock)
    assert _run_window(worker, db_session)["reloads_stuck"] == []

    clock.now = NOW + timedelta(days=1)
    _set_reload(engine, loading, bytes_done=400)
    details = _run_window(worker, db_session)
    assert details["reload_progress"] == {
        str(queued): {"status": "queued", "bytes_done": 0},
        str(loading): {"status": "loading", "bytes_done": 400},
    }
    assert details["reloads_stuck"] == []
    assert (details["cutoff"], details["limited_by"]) == ("2026-03-01", "reload")
    assert _window_alerts(db_session) == {}

    # The loading one is ready: the queue moved, the queued one has not waited a run yet.
    clock.now = NOW + timedelta(days=2)
    _set_reload(
        engine,
        loading,
        status="ready",
        bytes_done=900,
        ready_at=clock.now,
        expires_at=clock.now + timedelta(days=KEEP),
    )
    details = _run_window(worker, db_session)
    assert details["reload_progress"] == {str(queued): {"status": "queued", "bytes_done": 0}}
    assert details["reloads_stuck"] == []
    assert _window_alerts(db_session) == {}

    # A run later nothing loads and the queued one has not moved: now it is named.
    clock.now = NOW + timedelta(days=3)
    details = _run_window(worker, db_session)
    assert details["reloads_stuck"] == [queued]
    assert set(_window_alerts(db_session)) == {f"{STUCK_KEY}{queued}"}
    assert details["cutoff"] == "2026-03-01"

    # A reload made since that run is loading already: something loads, the queue moves, the
    # alert resolves (and the new one has no previous run to stand still since).
    clock.now = NOW + timedelta(days=4)
    newer = _reload(
        db_session, date(2026, 4, 20), date(2026, 4, 20), "loading", bytes_total=80, bytes_done=5
    )
    details = _run_window(worker, db_session)
    assert details["reload_progress"] == {
        str(queued): {"status": "queued", "bytes_done": 0},
        str(newer): {"status": "loading", "bytes_done": 5},
    }
    assert details["reloads_stuck"] == []
    (alert,) = _window_alerts(db_session).values()
    assert (alert.state, alert.details["auto_resolved"]["reason"]) == (
        "resolved",
        PROGRESSED_REASON,
    )


def test_queued_reloads_with_nothing_loading_raise_r20_db_window(
    engine: Engine, db_session: Session, layout: Layout
) -> None:
    """``queued`` reloads unchanged since the previous run while nothing is ``loading`` are each
    named; once one of them starts loading the alert of the other resolves (it waits behind a
    queue that moves), and when the loading one then stands still only that one is named."""
    _sealed(layout, date(2026, 1, 1), TODAY + timedelta(days=5))
    first = _reload(db_session, date(2026, 3, 1), date(2026, 3, 2), "queued", bytes_total=50)
    second = _reload(db_session, date(2026, 4, 10), date(2026, 4, 10), "queued", bytes_total=70)
    clock = Clock(NOW)
    worker = _window_worker(engine, layout, clock)
    assert _run_window(worker, db_session)["reloads_stuck"] == []

    clock.now = NOW + timedelta(days=1)
    details = _run_window(worker, db_session)
    assert details["reloads_stuck"] == sorted([first, second])
    found = _window_alerts(db_session)
    assert set(found) == {f"{STUCK_KEY}{first}", f"{STUCK_KEY}{second}"}
    assert {alert.state for alert in found.values()} == {"open"}
    assert found[f"{STUCK_KEY}{second}"].details["status"] == "queued"
    assert (details["cutoff"], details["limited_by"]) == ("2026-03-01", "reload")

    # The second starts loading: it moved on, and the first now waits behind a moving queue.
    clock.now = NOW + timedelta(days=2)
    _set_reload(engine, second, status="loading", bytes_done=10)
    details = _run_window(worker, db_session)
    assert details["reloads_stuck"] == []
    found = _window_alerts(db_session)
    assert {alert.state for alert in found.values()} == {"resolved"}
    assert {alert.details["auto_resolved"]["reason"] for alert in found.values()} == {
        PROGRESSED_REASON
    }
    assert details["cutoff"] == "2026-03-01"

    # The loading one stands still: it is named, the queued one behind it is not.
    clock.now = NOW + timedelta(days=3)
    details = _run_window(worker, db_session)
    assert details["reloads_stuck"] == [second]
    db_session.expire_all()
    open_keys = db_session.scalars(
        select(Alert.dedup_key).where(Alert.rule == WINDOW_RULE, Alert.state == "open")
    ).all()
    assert open_keys == [f"{STUCK_KEY}{second}"]  # a new alert: the one before was resolved
    assert details["cutoff"] == "2026-03-01"


def test_r20_db_window_has_its_texts_and_describer() -> None:
    assert WINDOW_RULE == WINDOW_KEY.partition(":")[0] == "R20-db-window"
    assert RULES[WINDOW_RULE] == RuleText(
        "Veritabanı penceresi işi sorunu",
        "Database window job problem",
        "logs\\worker.log ve state\\worker-status.json dosyalarına bakın; ilerlemeyen bir "
        "yeniden yüklemede ArciIngest'i denetleyin: Get-Service ArciIngest.",
        "Look at logs\\worker.log and state\\worker-status.json; for a reload without progress "
        "check ArciIngest: Get-Service ArciIngest.",
    )
    assert RULES[WINDOW_RULE] != UNKNOWN_RULE
    steps_tr, steps_en = runbook(WINDOW_RULE)
    assert steps_tr.startswith("1. Ayrıntılarda hangisi olduğunu görün")
    assert steps_en.startswith("1. See in the details which it is")
    assert steps_tr.count("\n") == steps_en.count("\n") == 3
    assert WINDOW_RULE in DESCRIBERS
    assert describe(WINDOW_RULE, {"day": "2026-06-15", "job": "db.window"}) == (
        "Veritabanı penceresi işi 15.06.2026 günü çalışamadı; pencereden eski günler silinmedi.",
        "The database window job failed on 15.06.2026; days older than the window were not "
        "dropped.",
    )
    stuck = {"reload_id": 12, "first_day": "2026-03-01", "last_day": "2026-03-02"}
    assert describe(WINDOW_RULE, stuck) == (
        "Arşivden yeniden yükleme #12 (01.03.2026 - 02.03.2026) önceki çalıştırmadan beri "
        "ilerlemedi; o günden itibaren hiçbir kayıt silinmiyor.",
        "Archive reload #12 (01.03.2026 - 02.03.2026) has made no progress since the previous "
        "run; nothing from that day on is dropped.",
    )
    one_day = {**stuck, "last_day": "2026-03-01"}
    assert describe(WINDOW_RULE, one_day)[1].startswith("Archive reload #12 (01.03.2026) has")
    # Only the whitelisted shapes are told.
    assert describe(WINDOW_RULE, {**stuck, "reload_id": "12"}) == GENERIC
    assert describe(WINDOW_RULE, {**stuck, "first_day": "dün"}) == GENERIC
    assert describe(WINDOW_RULE, {"day": "yesterday"}) == GENERIC
    assert describe(WINDOW_RULE, {}) == GENERIC


@pytest.mark.parametrize("kind", ["failed", "stuck"])
def test_the_r20_db_window_mail_names_the_rule(kind: str) -> None:
    """The e-mail gives the rule's own name and action, never the generic ``UNKNOWN_RULE``, and
    nothing of the details beyond the whitelisted facts."""
    details: dict[str, Any] = {
        "day": "2026-06-15",
        "job": "db.window",
        "error": "OperationalError",
        "title_en": "Database window job (db.window) failed on 2026-06-15",
    }
    key = WINDOW_KEY
    if kind == "stuck":
        details = {
            "reload_id": 12,
            "first_day": "2026-03-01",
            "last_day": "2026-03-02",
            "status": "queued",
            "bytes_done": 0,
            "bytes_total": 4096,
            "job": "db.window",
            "title_en": "Archive reload #12 (2026-03-01 - 2026-03-02) has made no progress",
        }
        key = f"{STUCK_KEY}12"
    item = MailItem(1, "new", WINDOW_RULE, "medium", "open", NOW, None, details)
    text = render_text([item], URL)
    page = render_html([item], URL)
    assert subject([item]) == "[ARCILOGGER] Orta: Veritabanı penceresi işi sorunu"
    facts = describe(WINDOW_RULE, details)
    assert facts != GENERIC
    for mail, shown in ((text, str), (page, html.escape)):
        assert "R20-db-window — Veritabanı penceresi işi sorunu" in mail
        assert "R20-db-window — Database window job problem" in mail
        assert f"R20-db-window — {UNKNOWN_RULE.name_tr}" not in mail
        assert f"R20-db-window — {UNKNOWN_RULE.name_en}" not in mail
        assert shown(RULES[WINDOW_RULE].action_tr) in mail
        assert shown(RULES[WINDOW_RULE].action_en) in mail
        assert shown(facts[0]) in mail
        assert shown(facts[1]) in mail
        for hidden in ("OperationalError", "4096", details["title_en"], key):
            assert hidden not in mail


# -- the audit ------------------------------------------------------------------------------------


def test_a_step_that_drops_writes_one_audit_entry(
    engine: Engine, db_session: Session, layout: Layout
) -> None:
    """``db.window`` (actor ``system``) in the step's transaction: the cutoff and what went —
    on plain PostgreSQL the older row of the default partition, on TimescaleDB its chunk. A run
    that drops nothing writes none."""
    _rows(engine, [date(2026, 4, 10), TODAY])
    details = job_db_window(_context(engine, layout, NOW, _config(layout)))
    assert details["limited_by"] == "nothing_sealed"
    assert (details["audit_ids"], _audit(db_session, "db.window")) == ([], [])

    _sealed(layout, date(2026, 1, 1), TODAY - timedelta(days=1))
    details = job_db_window(_context(engine, layout, NOW, _config(layout)))
    assert details["applied"] == "2026-05-16"
    (entry,) = _audit(db_session, "db.window")
    assert details["audit_ids"] == [entry.id]
    assert (entry.actor_id, entry.actor_username, entry.target) == (None, "system", "database")
    storage = _storage(engine)
    common = {
        "cutoff": "2026-05-16",
        "target_cutoff": "2026-05-16",
        "limited_by": "window",
        "storage": storage,
        "counts_deleted": 0,
        "clock_samples_deleted": 0,
        "reloads_expired": 0,
    }
    if storage == "partitions":  # the row of 10 April was in the default partition
        assert entry.details == {
            **common,
            "dropped": [],
            "dropped_count": 0,
            "default_rows_deleted": 1,
        }
    else:  # its UTC day's chunk, in a bounded step (W5-G-7): the step ends DAYS_PER_RUN days
        # after the chunk's start, at the next local midnight (UTC 10 April + 16 days, 27 April
        # 00:00 site time); the steps after it drop nothing and write no entry.
        (chunk,) = entry.details["dropped"]
        assert chunk.startswith("_timescaledb_internal.")
        assert DAYS_PER_RUN == 16
        assert entry.details == {
            **common,
            "cutoff": "2026-04-27",
            "dropped": [chunk],
            "dropped_count": 1,
            "default_rows_deleted": 0,
        }
    assert _days_left(engine) == [TODAY]
    # Nothing left to drop: no further entry.
    details = job_db_window(_context(engine, layout, NOW + timedelta(hours=1), _config(layout)))
    assert details["audit_ids"] == []
    assert len(_audit(db_session, "db.window")) == 1
    assert verify_audit_chain(db_session).ok


def test_a_step_whose_entry_fails_drops_nothing(
    engine: Engine, db_session: Session, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry and the drop share the step's transaction: a step whose entry cannot be
    written drops nothing."""
    _sealed(layout, date(2026, 1, 1), TODAY - timedelta(days=1))
    _rows(engine, [date(2026, 4, 10), TODAY])

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the audit log cannot be written")

    monkeypatch.setattr(db_window, "append_audit", broken)
    with pytest.raises(RuntimeError):
        job_db_window(_context(engine, layout, NOW, _config(layout)))
    assert _audit(db_session, "db.window") == []
    assert _days_left(engine) == [date(2026, 4, 10), TODAY]


def _frozen_change_key() -> str:
    """``CHANGE_KEY`` of revision ``0021_db_window`` (migrations never import the worker)."""
    path = Path(arcilogger_db.__file__).parent / "migrations" / "versions" / "v0021_db_window.py"
    spec = importlib.util.spec_from_file_location("v0021_db_window", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.CHANGE_KEY)


def test_the_frozen_change_key_is_the_jobs() -> None:
    assert _frozen_change_key() == CHANGE_KEY == "db.retention_days_migration"


def test_the_upgrades_change_of_the_window_is_audited_once(
    empty_engine: Engine, layout: Layout
) -> None:
    """Revision ``0021_db_window`` changed an administrator's 400 to 30 and left its status row:
    the first run writes one ``settings.change`` entry (actor ``system``, old and new value)
    before anything is dropped; later runs write none."""
    upgrade(empty_engine, revision="0020_fg_traffic_transip_time", timescale="off")
    with empty_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE settings SET value = '400', updated_by = 'tolga' "
                "WHERE key = 'db.retention_days'"
            )
        )
    upgrade(empty_engine, timescale="off")
    sessions = session_factory(empty_engine)
    with sessions() as session:
        row = session.get(Setting, CHANGE_KEY)
        assert row is not None
        assert row.value["audited"] is False
        changed_at = row.updated_at
        assert _audit(session, "settings.change") == []
    _sealed(layout, date(2026, 1, 1), TODAY - timedelta(days=1))
    _rows(empty_engine, [date(2026, 4, 10), TODAY])
    details = job_db_window(_context(empty_engine, layout, NOW, _config(layout)))
    with sessions() as session:
        (entry,) = _audit(session, "settings.change")
        assert details["window_change_audited"] == entry.id
        assert (entry.actor_id, entry.actor_username) == (None, "system")
        assert entry.target == "setting:db.retention_days"
        assert entry.details == {
            "old": 400,
            "new": 30,
            "was_set_by": "tolga",
            "migration": "0021_db_window",
            "changed_at": changed_at.astimezone(UTC).isoformat(timespec="microseconds"),
        }
        (dropped,) = _audit(session, "db.window")
        assert entry.id < dropped.id  # the change is on record before the first drop
        row = session.get(Setting, CHANGE_KEY)
        assert row is not None
        assert row.value["audited"] is True
    details = job_db_window(_context(empty_engine, layout, NOW, _config(layout)))
    assert "window_change_audited" not in details
    with sessions() as session:
        assert len(_audit(session, "settings.change")) == 1
        assert verify_audit_chain(session).ok
