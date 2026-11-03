"""``db.window`` (Wave 5 contract G3): the cutoff — the window, the oldest unsealed day less the
raw-day margin (a day before ``seal.first_day`` counts as sealed; a sealed day whose manifest is
gone does not), the oldest day of an active archive reload —, the expiry of reloads after
``db.reload_keep_days``, the ``R20-db-window`` alert of a failed run and the job's place in the
worker (daily after 04:00).

The database tests run on the template's storage: plain PostgreSQL (rows of other months in the
default partition, daily partitions made by the job) or TimescaleDB in the Linux CI job (chunks
of a UTC day). Both drop the rows of days well before the cutoff and keep every row from the
cutoff's local midnight on; the rows of the day just before the cutoff are not asserted (a
TimescaleDB chunk holding the cutoff is kept whole)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.orm import Session

from arcilogger_core.config import Config, SealConfig
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import day_window, site_day
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, ArchiveReload, JobRun, MtDhcp
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_db.testing import pause_compression_jobs
from arcilogger_worker import db_window
from arcilogger_worker.db_window import (
    RESOLVED_REASON,
    WINDOW_KEY,
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


@pytest.fixture
def engine(db_engine: Engine) -> Engine:
    """The template's database; on TimescaleDB no compression policy runs during the test."""
    with db_engine.connect() as connection:
        storage = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
    if storage["storage"] == "hypertables":
        pause_compression_jobs(db_engine)
    return db_engine


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
