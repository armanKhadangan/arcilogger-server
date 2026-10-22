"""Usage jobs: re-attribution after (re)assignments, VLAN changes, settings changes and late
logs, the final rebuild of a partial day, rebuild requests, retention and
``arcilogger-worker usage rebuild``."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, delete, func, make_url, select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore
from arcilogger_db.assignments import Planner, apply, load_history, lock_devices
from arcilogger_db.engine import session_factory
from arcilogger_db.models import (
    Department,
    DomainCategory,
    JobRun,
    UsageDaily,
    UsageDay,
    UsageRebuild,
    Vlan,
)
from arcilogger_db.settings import set_setting
from arcilogger_worker.cli import EXIT_CONFIG, EXIT_OK, main
from arcilogger_worker.testing import Evidence, tr
from arcilogger_worker.usage.config import UsageSettings
from arcilogger_worker.usage.domains import DomainNamer
from arcilogger_worker.usage.engine import DayResult, compute_day
from arcilogger_worker.usage.jobs import (
    apply_retention,
    finalize_yesterday,
    process_requests,
    reattribute,
    rebuild_day,
    refresh_today,
    stale_reason,
)
from arcilogger_worker.usage.query import Directory as UsageDirectory
from arcilogger_worker.usage.subjects import build_timelines
from arcilogger_worker.usage.testing import (
    BARE,
    DAY,
    NEXT,
    PC2,
    PC2_MAC,
    GoldenSite,
    build_golden_site,
)

#: Three days after the golden day: inside the default re-attribution window (7 days).
NOW = tr(DAY + timedelta(days=3), 9)


@pytest.fixture
def site(db_session: Session, layout: Layout) -> GoldenSite:
    golden = build_golden_site(db_session, layout)
    rebuild_day(db_session, DAY, now=NOW)
    rebuild_day(db_session, NEXT, now=NOW)
    return golden


def _reason(session: Session) -> str | None:
    settings = UsageSettings.load(session)
    namer = DomainNamer(settings.psl_file, settings.psl_private_suffixes)
    return stale_reason(session, DAY, now=NOW, settings=settings, namer=namer)


def _subjects(session: Session) -> dict[str, int]:
    rows = session.execute(
        select(UsageDaily.subject, func.sum(UsageDaily.active_seconds))
        .where(UsageDaily.day == DAY)
        .group_by(UsageDaily.subject)
    )
    return {subject: int(active) for subject, active in rows}


def test_a_new_assignment_moves_the_usage_to_the_person(
    db_engine: Engine, db_session: Session, site: GoldenSite
) -> None:
    assert _reason(db_session) is None
    assert _subjects(db_session)[f"device:{PC2_MAC}"] == 300
    # PC-02 turns out to be Can's since 14:00 on the golden day (entered three days later).
    owners = lock_devices(db_session, [PC2_MAC])
    planner = Planner(load_history(db_session, [PC2_MAC]), now=NOW, owners=owners)
    planner.assign(PC2_MAC, site.can, tr(DAY, 14))
    apply(db_session, planner.changes, assigned_by="test")
    db_session.commit()
    assert _reason(db_session) == "attribution"
    report = reattribute(session_factory(db_engine), now=NOW)
    assert report.reasons == {DAY.isoformat(): "attribution", NEXT.isoformat(): "attribution"}
    db_session.expire_all()
    subjects = _subjects(db_session)
    assert f"device:{PC2_MAC}" not in subjects
    # Can: 120 s of PC-03 plus PC-02's 240 s (15:02-15:05) and 60 s (23:59).
    assert subjects[f"person:{site.can}"] == 420
    assert _reason(db_session) is None
    assert reattribute(session_factory(db_engine), now=NOW).days == []


def _vlans_of(session: Session, subject: str) -> set[int | None]:
    """The VLAN numbers of ``subject``'s usage rows of the golden day."""
    rows = session.scalars(
        select(UsageDaily.vlan).where(UsageDaily.day == DAY, UsageDaily.subject == subject)
    )
    return set(rows)


def test_vlan_changes_follow_like_a_department_change(
    db_engine: Engine, db_session: Session, site: GoldenSite
) -> None:
    """The VLANs page (``/api/v1/vlans``) edits ``vlans`` rows. A VLAN's name and department are
    joined when the usage is read, so they need no rebuild; a VLAN renumbered, moved, entered
    or deleted changes the VLAN number of an address without a lease (the table's), and the
    re-attribution job rebuilds the recent days by itself."""
    bare = f"ip:{BARE}"

    def vlan_of(subject: str) -> set[int | None]:
        return _vlans_of(db_session, subject)

    assert vlan_of(bare) == {41}
    finance = db_session.get_one(Vlan, "172.20.41.0/24")
    finance.name_tr = "Finans Ofisi"
    finance.department_id = None
    db_session.commit()
    assert _reason(db_session) is None
    assert UsageDirectory(db_session).vlan_departments()[41] == "Finans Ofisi"
    finance.department_id = db_session.scalars(
        select(Department.id).where(Department.name == "İdari İşler")
    ).one()
    finance.vlan_id = 38
    db_session.commit()
    assert _reason(db_session) == "attribution"
    reattribute(session_factory(db_engine), now=NOW)
    db_session.expire_all()
    assert vlan_of(bare) == {38}
    assert UsageDirectory(db_session).vlan_departments()[38] == "İdari İşler"
    assert _reason(db_session) is None
    db_session.delete(db_session.get_one(Vlan, "172.20.41.0/24"))
    db_session.commit()
    assert _reason(db_session) == "attribution"
    reattribute(session_factory(db_engine), now=NOW)
    db_session.expire_all()
    assert vlan_of(bare) == {None}


def test_a_deleted_vlan_keeps_the_vlan_numbers_of_leases(
    db_engine: Engine, db_session: Session, site: GoldenSite
) -> None:
    """A leased address keeps the VLAN number its lease was built with (the DHCP server's, else
    the subnet's at that time) when its VLAN is deleted; only its hours without a lease (PC-02
    before its 07:00 lease) take the table's, none now. Those change the fingerprint, so the recent
    days are rebuilt, and the leased usage stays in VLAN 43. The VLAN's department goes because
    the usage pages no longer find it when they read the number (the VLANs page's delete
    confirmation says so)."""
    device = f"device:{PC2_MAC}"

    def pieces() -> list[tuple[str, int | None]]:
        timeline = build_timelines(db_session, [PC2], tr(DAY, 0), tr(NEXT, 0))[PC2]
        return [(subject.key, subject.vlan) for subject in timeline.subjects]

    assert pieces() == [(f"ip:{PC2}", 43), (device, 43)]
    assert _vlans_of(db_session, device) == {43}
    assert UsageDirectory(db_session).vlan_departments()[43] == "İdari İşler"
    db_session.delete(db_session.get_one(Vlan, "172.20.43.0/24"))
    db_session.commit()
    assert pieces() == [(f"ip:{PC2}", None), (device, 43)]
    assert _reason(db_session) == "attribution"
    reattribute(session_factory(db_engine), now=NOW)
    db_session.expire_all()
    assert _vlans_of(db_session, device) == {43}
    assert 34 not in UsageDirectory(db_session).vlan_departments()
    assert _reason(db_session) is None


def test_changed_settings_and_late_logs_rebuild_the_recent_days(
    db_engine: Engine, db_session: Session, layout: Layout, site: GoldenSite
) -> None:
    set_setting(db_session, "usage.active_bytes_threshold", 1000, updated_by="test")
    db_session.commit()
    assert _reason(db_session) == "inputs"
    reattribute(session_factory(db_engine), now=NOW)
    db_session.expire_all()
    # PC-02's 15:00 and 15:01 (2000 bytes each) are active now: 240 + 120 + 60.
    assert _subjects(db_session)[f"device:{PC2_MAC}"] == 420
    assert _reason(db_session) is None
    # A log of the 14th loaded late (ingest_offsets of that raw day grow).
    Evidence(db_session, layout).traffic(
        tr(DAY, 16, 1), PC2, 51999, "203.0.113.99", sessionid=2999, duration=60,
        rcvdbyte=9000, sentbyte=0, action="close",
    )  # fmt: skip
    db_session.commit()
    assert _reason(db_session) == "inputs"
    reattribute(session_factory(db_engine), now=NOW)
    db_session.expire_all()
    assert _subjects(db_session)[f"device:{PC2_MAC}"] == 480


def test_logs_loaded_while_a_day_is_computed_rebuild_it_again(
    db_engine: Engine,
    db_session: Session,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    site: GoldenSite,
) -> None:
    """The ingest commits a log of the day while its rollup is computed (catching up after an
    outage): the stored fingerprint must not count that log yet, so the next run rebuilds the
    day instead of keeping it without the log."""

    def compute_while_loading(session: Session, day: date, **kwargs: Any) -> DayResult:
        result = compute_day(session, day, **kwargs)
        with session_factory(db_engine)() as ingest:  # the ingest service: its own connection
            Evidence(ingest, layout).traffic(
                tr(DAY, 16, 1), PC2, 51999, "203.0.113.99", sessionid=2999, duration=60,
                rcvdbyte=9000, sentbyte=0, action="close",
            )  # fmt: skip
            ingest.commit()
        return result

    monkeypatch.setattr("arcilogger_worker.usage.jobs.compute_day", compute_while_loading)
    rebuild_day(db_session, DAY, now=NOW)
    monkeypatch.undo()
    assert _subjects(db_session)[f"device:{PC2_MAC}"] == 300  # computed without the log
    assert _reason(db_session) == "inputs"
    report = reattribute(session_factory(db_engine), now=NOW)
    assert report.reasons == {DAY.isoformat(): "inputs"}
    db_session.expire_all()
    # The late log's 9000 bytes make 16:00 an active minute.
    assert _subjects(db_session)[f"device:{PC2_MAC}"] == 360
    assert _reason(db_session) is None


def test_missing_and_partial_days_are_completed(
    db_engine: Engine, db_session: Session, site: GoldenSite
) -> None:
    db_session.execute(delete(UsageDay).where(UsageDay.day == DAY))
    db_session.commit()
    assert _reason(db_session) == "missing"
    rebuild_day(db_session, DAY, now=tr(DAY, 20))
    assert db_session.get_one(UsageDay, DAY).status == "partial"
    assert _reason(db_session) == "partial"
    report = reattribute(session_factory(db_engine), now=NOW)
    assert report.reasons[DAY.isoformat()] == "partial"
    db_session.expire_all()
    assert db_session.get_one(UsageDay, DAY).status == "final"


def test_the_time_budget_leaves_days_for_the_next_run(
    db_engine: Engine, db_session: Session, site: GoldenSite
) -> None:
    set_setting(db_session, "usage.active_bytes_threshold", 1000, updated_by="test")
    db_session.commit()
    report = reattribute(session_factory(db_engine), now=NOW, budget=timedelta(0))
    assert report.more
    assert report.skipped == [DAY.isoformat(), NEXT.isoformat()]
    assert report.details()["more"] is True


def test_today_and_yesterday_jobs(db_engine: Engine, db_session: Session, site: GoldenSite) -> None:
    sessions = session_factory(db_engine)
    today = refresh_today(sessions, now=tr(DAY, 12))
    assert (today["day"], today["status"]) == (DAY.isoformat(), "partial")
    yesterday = finalize_yesterday(sessions, now=tr(NEXT, 0, 45))
    assert (yesterday["day"], yesterday["status"]) == (DAY.isoformat(), "final")


def test_requests_report_failures_on_the_request(
    db_engine: Engine, db_session: Session, monkeypatch: pytest.MonkeyPatch, site: GoldenSite
) -> None:
    db_session.add(
        UsageRebuild(from_day=DAY, to_day=NEXT, total_days=2, requested_by="tolga", status="queued")
    )
    db_session.commit()

    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr("arcilogger_worker.usage.jobs.rebuild_day", broken)
    process_requests(session_factory(db_engine), now=NOW)
    db_session.expire_all()
    request = db_session.scalars(select(UsageRebuild)).one()
    assert request.status == "failed"
    assert request.error == f"{DAY}: RuntimeError: disk full"


def test_retention_deletes_old_rollups(
    db_engine: Engine, db_session: Session, site: GoldenSite
) -> None:
    set_setting(db_session, "usage.retention_days", 30, updated_by="test")
    db_session.commit()
    kept = apply_retention(session_factory(db_engine), now=NOW)
    assert kept["deleted"]["usage_daily"] == 0
    result = apply_retention(session_factory(db_engine), now=tr(NEXT + timedelta(days=30), 9))
    assert result["deleted"]["usage_daily"] == 7  # the 14th; the 15th is still inside
    assert result["deleted"]["usage_days"] == 1
    db_session.expire_all()
    assert db_session.scalars(select(DomainCategory)).all() == []


def _write_config(tmp_path: Path, dsn: str) -> Path:
    """``arcilogger.toml`` for the test database (as worker/tests/test_cli.py writes it)."""
    url = make_url(dsn)
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n\n'
        "[database]\n"
        f'host = "{url.host}"\nport = {url.port or 5432}\n'
        f'name = "{url.database}"\nuser = "{url.username}"\nconnect_timeout_seconds = 2\n',
        encoding="utf-8",
    )
    if url.password:
        SecretStore.create_key(tmp_path / "secret.key")
        SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json").set(
            DATABASE_PASSWORD, str(url.password)
        )
    return path


def test_cli_rebuild_records_a_job_run(
    pg_dsn: str,
    tmp_path: Path,
    db_session: Session,
    site: GoldenSite,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _write_config(tmp_path, pg_dsn)
    code = main(
        ["usage", "rebuild", "--from", DAY.isoformat(), "--to", NEXT.isoformat(),
         "--config", str(config)]
    )  # fmt: skip
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert f"{DAY}: final" in out
    run = db_session.scalars(select(JobRun).where(JobRun.job == "usage.rebuild")).one()
    assert (run.status, run.details["days"]) == ("ok", 2)
    assert main(["usage", "rebuild", "--from", "2026-10-15", "--to", "2026-10-14"]) == EXIT_CONFIG
    assert main(["usage", "rebuild", "--from", "2999-01-01"]) == EXIT_CONFIG
