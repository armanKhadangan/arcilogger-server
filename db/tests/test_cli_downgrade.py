"""``arcilogger-db schema downgrade`` (issue #122): the schema step of ``Install-Arcilogger.ps1
-Rollback`` back to a version from before ``0021_db_window``. Alembic's downgrade under the
migrations' lock held for the session, the daily partitions merged back into monthly ones with
every row, the one ``settings.change`` entry (actor ``system``) for ``db.retention_days`` 30 →
400 in the transaction that moves alembic's version, nothing for an administrator's value, a
second run after a failure that goes on and audits once, the upgrade's own change written first
when the window job had not audited it yet, and the exit codes 0, 1, 2 and 3; and (issue #164)
the rollback to a program from ``0021`` on: down to ``0021`` only, ``leases.moves`` dropped and
nothing else. Plain PostgreSQL (``[database] timescale = "off"``): on TimescaleDB there is
nothing to merge."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore
from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db import cli
from arcilogger_db.audit import verify_audit_chain
from arcilogger_db.cli import main
from arcilogger_db.downgrade import CHANGE_KEY
from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import MIGRATION_LOCK_KEY, current_revisions, head_revisions, upgrade
from arcilogger_db.models import TIME_SERIES_TABLES, AuditLog
from arcilogger_db.partitions import (
    add_months,
    ensure_days,
    list_partitions,
    month_bounds,
    month_of,
    partition_name,
)
from arcilogger_db.testing import TestServer

BEFORE = "0020_fg_traffic_transip_time"
#: The revision whose downgrade merges the partitions: the version a run that fails in or after
#: its steps leaves (they commit one by one, a later revision's downgrade with the first).
REVISION = "0021_db_window"
#: The head, where every downgrade here starts.
HEAD = "0023_usage_period"
CONFIG = """
data_dir = "{data}"

[database]
host = "{host}"
port = {port}
name = "{name}"
user = "{user}"
connect_timeout_seconds = 5
timescale = "off"
"""

Hook = Callable[[Any, Any, str], None]


@dataclass
class Site:
    config: Path
    engine: Engine

    def run(self, *args: str) -> int:
        return main(["schema", "downgrade", *args, "--config", str(self.config)])


@pytest.fixture
def site(pg_server: TestServer, tmp_path: Path) -> Iterator[Site]:
    """An empty database, its configuration (plain PostgreSQL) and an engine of the test's own."""
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    url = pg_server.admin_url
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        CONFIG.format(
            data=(tmp_path / "data").as_posix(),
            host=url.host or "127.0.0.1",
            port=url.port or 5432,
            name=name,
            user=url.username,
        ),
        encoding="utf-8",
    )
    if url.password:
        SecretStore.create_key(tmp_path / "secret.key")
        SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json").set(
            DATABASE_PASSWORD, str(url.password)
        )
    engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
    try:
        yield Site(path, engine)
    finally:
        engine.dispose()
        pg_server.drop_database(name)


def _hook(monkeypatch: pytest.MonkeyPatch, hook: Hook) -> None:
    """``hook(conn, cursor, statement)`` before every statement of the command's engine."""
    original = cli._engine

    def engine_with_hook(config: Any) -> Engine:
        engine = original(config)

        def before(conn: Any, cursor: Any, statement: str, *_args: Any) -> None:
            hook(conn, cursor, statement)

        event.listen(engine, "before_cursor_execute", before)
        return engine

    monkeypatch.setattr(cli, "_engine", engine_with_hook)


def _upgraded(site: Site, capsys: pytest.CaptureFixture[str]) -> list[tuple[Any, ...]]:
    """``arcilogger-db upgrade`` (daily partitions from 8 days back to 7 ahead), the next
    month's first day too (a UTC day across a month's end in every table: two months at least),
    ``mt_dhcp`` rows in the first and the last daily partition, across that month's end and in
    the default partition, and an administrator's audited change; the rows."""
    assert main(["upgrade", "--config", str(site.config)]) == 0
    assert head_revisions() == (HEAD,)
    following = add_months(month_of(site_day(utc_now())), 1)
    with site.engine.begin() as connection:
        ensure_days(connection, following, following)
    days = _daily(site.engine, "mt_dhcp")
    across = month_bounds(following)[0]
    at = [day_window(day)[0] + timedelta(hours=12) for day in (days[0], days[-1])]
    at += [across - timedelta(microseconds=1), across, at[0] - timedelta(days=1)]
    with site.engine.begin() as connection:
        for number, when in enumerate(at, 1):
            connection.execute(
                text(
                    "INSERT INTO mt_dhcp (time, time_received, source, sender, raw_day, "
                    "raw_line, raw_offset, server, action, ip, mac) VALUES (:at, :at, 'core-r1', "
                    "'172.20.10.1', :day, :line, 0, 'dhcp60', 'assigned', '172.20.60.183', "
                    "'00:00:5e:00:53:46')"
                ),
                {"at": when, "day": site_day(when), "line": number},
            )
    change = ["settings", "set", "lookup.timeout_seconds", "6", "--by", "tolga"]
    assert main([*change, "--config", str(site.config)]) == 0
    capsys.readouterr()
    return _rows(site.engine)


def _rows(engine: Engine) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text("SELECT * FROM mt_dhcp ORDER BY id"))]


def _daily(engine: Engine, table: str) -> list[Any]:
    with engine.connect() as connection:
        return [p.day for p in list_partitions(connection, table) if p.day is not None]


def _months(engine: Engine, table: str) -> list[Any]:
    with engine.connect() as connection:
        return [p.month for p in list_partitions(connection, table) if p.month is not None]


def _revision(engine: Engine) -> tuple[str, ...]:
    with engine.connect() as connection:
        return current_revisions(connection)


def _window(engine: Engine) -> tuple[Any, str]:
    with engine.connect() as connection:
        value, author = connection.execute(
            text("SELECT value, updated_by FROM settings WHERE key = 'db.retention_days'")
        ).one()
    return value, author


def _entries(engine: Engine) -> list[AuditLog]:
    with Session(engine) as session:
        return list(session.scalars(select(AuditLog).order_by(AuditLog.id)))


def _chain_ok(engine: Engine) -> int:
    with Session(engine) as session:
        verification = verify_audit_chain(session)
    assert verification.ok, verification.errors
    return verification.checked


def _json_out(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1, captured.out  # one JSON object, nothing else on stdout
    document: dict[str, Any] = json.loads(lines[0])
    return document


def test_the_downgrade_merges_the_daily_partitions_and_audits_the_window_once(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    """Down to ``0020``: every daily partition merged into monthly ones with every row, version
    ``0020``, ``db.retention_days`` 400 again and one ``settings.change`` entry (actor
    ``system``, 30 → 400) written by this version's writer on the downgraded schema, the chain
    valid; run again at the target: exit 0, nothing done, no entry."""
    rows = _upgraded(site, capsys)
    months = sorted(
        {month_of(site_day(p.start)) for p in _partitions(site.engine) if p.day is not None}
    )
    assert len(months) >= 2
    before = _entries(site.engine)
    assert site.run("--to", BEFORE, "--json") == 0
    document = _json_out(capsys)
    (entry,) = _entries(site.engine)[len(before) :]
    assert document == {
        "from": HEAD,
        "to": BEFORE,
        "downgraded": True,
        "audit_id": entry.id,
        "retention_days": {"before": 30, "after": 400},
        "upgrade_audit_id": None,
    }
    assert _revision(site.engine) == (BEFORE,)
    for table in TIME_SERIES_TABLES:
        assert _daily(site.engine, table) == [], table
        assert set(months) <= set(_months(site.engine, table)), table
    assert _rows(site.engine) == rows
    with site.engine.connect() as connection:
        where = connection.execute(
            text("SELECT CAST(CAST(tableoid AS regclass) AS text) FROM mt_dhcp ORDER BY id")
        ).scalars()
        assert set(where) <= {partition_name("mt_dhcp", m) for m in months} | {"mt_dhcp_default"}
    assert _window(site.engine) == (400, ":seed")
    assert (entry.action, entry.actor_username, entry.actor_id) == (
        "settings.change",
        "system",
        None,
    )
    assert entry.target == "setting:db.retention_days"
    assert entry.details["old"] == 30
    assert entry.details["new"] == 400
    assert entry.details["was_set_by"] == ":seed"
    assert entry.details["downgrade"] == {"from": HEAD, "to": BEFORE}
    assert entry.details["via"] == "arcilogger-db"
    assert _chain_ok(site.engine) == len(before) + 1

    assert site.run("--to", BEFORE, "--json") == 0  # at the target already: nothing to do
    assert _json_out(capsys) == {
        "from": BEFORE,
        "to": BEFORE,
        "downgraded": False,
        "audit_id": None,
        "retention_days": None,
        "upgrade_audit_id": None,
    }
    assert site.run("--to", BEFORE) == 0
    assert f"Şema zaten {BEFORE} sürümünde" in capsys.readouterr().out
    assert len(_entries(site.engine)) == len(before) + 1
    assert _revision(site.engine) == (BEFORE,)


def _leases(engine: Engine) -> list[Any]:
    """Every lease, each column but ``moves`` (the one ``0022_lease_moves`` adds)."""
    with engine.connect() as connection:
        query = text("SELECT to_jsonb(lease) - 'moves' FROM leases AS lease ORDER BY id")
        return list(connection.execute(query).scalars())


def _lease_columns(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'leases' AND table_schema = current_schema()"
                )
            ).scalars()
        )


def test_the_downgrade_to_0021_drops_only_the_lease_moves(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    """Issue #164: a rollback to a program whose revisions end at ``0021_db_window`` takes the
    schema from the head down to ``0021`` only, through ``0022_lease_moves``' downgrade, which
    drops ``leases.moves`` and nothing else (lossless for 5651 data, the installer's list): the
    daily partitions, every log row, every lease with its other columns (the ``"vd"`` keys of
    its evidence too) and ``db.retention_days`` 30 stay, no audit entry is written. Run again:
    nothing to do. This version's upgrade (after a failed swap) brings the column back."""
    rows = _upgraded(site, capsys)
    daily = {table: _daily(site.engine, table) for table in TIME_SERIES_TABLES}
    evidence = [
        {"table": "fg_dhcp", "row_id": 7, "time": "2026-11-03T09:00:00+00:00", "raw": None}
        | {"summary": "DHCP Ack", "vd": "root"}
    ]
    moves = {"truncated": False, "groups": [{"ip": "172.20.60.184", "times": [1]}]}
    with site.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO leases (ip, mac, start, confidence, source_table, evidence, moves) "
                "VALUES ('172.20.60.183', '00:00:5e:00:53:46', now() - interval '1 day', "
                "'high', 'fg_dhcp', CAST(:evidence AS jsonb), CAST(:moves AS jsonb))"
            ),
            {"evidence": json.dumps(evidence), "moves": json.dumps(moves)},
        )
    leases = _leases(site.engine)
    assert "moves" in _lease_columns(site.engine)
    before = _entries(site.engine)
    assert site.run("--to", REVISION, "--json") == 0
    assert _json_out(capsys) == {
        "from": HEAD,
        "to": REVISION,
        "downgraded": True,
        "audit_id": None,
        "retention_days": {"before": 30, "after": 30},
        "upgrade_audit_id": None,
    }
    assert _revision(site.engine) == (REVISION,)
    assert "moves" not in _lease_columns(site.engine)
    assert _leases(site.engine) == leases
    with site.engine.connect() as connection:
        assert connection.execute(text("SELECT evidence FROM leases")).scalars().all() == [evidence]
    assert _rows(site.engine) == rows
    for table in TIME_SERIES_TABLES:
        assert _daily(site.engine, table) == daily[table], table
    assert _window(site.engine) == (30, ":seed")
    assert len(_entries(site.engine)) == len(before)
    assert _chain_ok(site.engine) == len(before)

    assert site.run("--to", REVISION) == 0  # at the target already: nothing to do
    assert f"Şema zaten {REVISION} sürümünde" in capsys.readouterr().out
    assert main(["upgrade", "--config", str(site.config)]) == 0
    capsys.readouterr()
    assert _revision(site.engine) == (HEAD,)
    assert _leases(site.engine) == leases
    with site.engine.connect() as connection:
        assert connection.execute(text("SELECT moves FROM leases")).scalars().all() == [None]


def _partitions(engine: Engine) -> list[Any]:
    with engine.connect() as connection:
        return list_partitions(connection, "mt_dhcp")


def test_the_text_output_names_the_versions_and_the_entry(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    _upgraded(site, capsys)
    assert site.run("--to", BEFORE) == 0
    out = capsys.readouterr().out
    entry = _entries(site.engine)[-1]
    assert f"Şema geri alındı [schema downgraded]: {HEAD} → {BEFORE}" in out
    assert f"db.retention_days: 30 → 400 (denetim kaydı [audit entry] #{entry.id}" in out


@pytest.mark.parametrize("value", [45, 30])
def test_an_administrators_window_is_kept_and_not_audited(
    site: Site, capsys: pytest.CaptureFixture[str], value: int
) -> None:
    """``0021``'s downgrade puts back only a 30 of the seed or of the revision: an
    administrator's value (also a 30 of their own) stays, and nothing is audited."""
    _upgraded(site, capsys)
    change = ["settings", "set", "db.retention_days", str(value), "--by", "tolga"]
    assert main([*change, "--config", str(site.config)]) == 0
    capsys.readouterr()
    before = len(_entries(site.engine))
    assert site.run("--to", BEFORE, "--json") == 0
    assert _json_out(capsys) == {
        "from": HEAD,
        "to": BEFORE,
        "downgraded": True,
        "audit_id": None,
        "retention_days": {"before": value, "after": value},
        "upgrade_audit_id": None,
    }
    assert _window(site.engine) == (value, "tolga")
    assert len(_entries(site.engine)) == before
    assert _chain_ok(site.engine) == before


def _upgraded_from_400(site: Site, capsys: pytest.CaptureFixture[str]) -> None:
    """A database of a version from before ``0021`` (``db.retention_days`` 400 of its seed)
    upgraded by this one: ``0021`` changed 400 to 30 and left its status row unaudited (the
    window job ``db.window`` has not run since)."""
    upgrade(site.engine, revision=BEFORE, timescale="off")
    with site.engine.begin() as connection:
        connection.execute(text("DELETE FROM settings WHERE key = 'db.reload_keep_days'"))
        connection.execute(
            text(
                "UPDATE settings SET value = CAST('400' AS jsonb), updated_by = :author "
                "WHERE key = 'db.retention_days'"
            ),
            {"author": ":seed"},
        )
    assert main(["upgrade", "--config", str(site.config)]) == 0
    capsys.readouterr()
    assert _window(site.engine) == (30, ":seed")


def _change_row(engine: Engine) -> Any:
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT value FROM settings WHERE key = :key"), {"key": CHANGE_KEY}
        ).scalar_one_or_none()


def test_an_upgrade_change_not_audited_yet_is_written_before_the_downgrades(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rolled back before the window job's first run: ``0021``'s downgrade drops the status row
    of the upgrade's 400 → 30, so the command writes that change's entry first (as the job
    would have), then its own 30 → 400, in the same transaction; the chain stays valid."""
    _upgraded_from_400(site, capsys)
    assert _change_row(site.engine)["audited"] is False
    before = len(_entries(site.engine))
    assert site.run("--to", BEFORE, "--json") == 0
    document = _json_out(capsys)
    upgrade_entry, entry = _entries(site.engine)[before:]
    assert document["upgrade_audit_id"] == upgrade_entry.id
    assert document["audit_id"] == entry.id
    assert upgrade_entry.id < entry.id
    for item in (upgrade_entry, entry):
        assert (item.action, item.actor_username, item.target) == (
            "settings.change",
            "system",
            "setting:db.retention_days",
        )
    assert (upgrade_entry.details["old"], upgrade_entry.details["new"]) == (400, 30)
    assert upgrade_entry.details["was_set_by"] == ":seed"
    assert upgrade_entry.details["migration"] == REVISION
    assert upgrade_entry.details["changed_at"]
    assert upgrade_entry.details["audited_at_downgrade"] == {"from": HEAD, "to": BEFORE}
    assert (entry.details["old"], entry.details["new"]) == (30, 400)
    assert _window(site.engine) == (400, ":seed")
    assert _change_row(site.engine) is None
    assert _chain_ok(site.engine) == before + 2


def test_an_upgrade_change_the_window_job_audited_is_not_written_again(
    site: Site, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window job had audited the upgrade's change (``audited: true``): only the
    downgrade's entry. A failed downgrade writes neither, and the run after it writes the
    pending one once."""
    _upgraded_from_400(site, capsys)
    raised = _interrupt_at(monkeypatch, "INSERT INTO audit_log")
    before = len(_entries(site.engine))
    assert site.run("--to", BEFORE) == 1
    assert raised
    capsys.readouterr()
    assert len(_entries(site.engine)) == before
    assert _change_row(site.engine)["audited"] is False, "the status row is still there"
    monkeypatch.undo()
    with site.engine.begin() as connection:  # as db.window marks it
        connection.execute(
            text(
                "UPDATE settings SET value = jsonb_set(value, '{audited}', 'true') WHERE key = :key"
            ),
            {"key": CHANGE_KEY},
        )
    assert site.run("--to", BEFORE, "--json") == 0
    document = _json_out(capsys)
    (entry,) = _entries(site.engine)[before:]
    assert document["upgrade_audit_id"] is None
    assert document["audit_id"] == entry.id
    assert (entry.details["old"], entry.details["new"]) == (30, 400)
    assert _chain_ok(site.engine) == before + 1


def _interrupt_at(monkeypatch: pytest.MonkeyPatch, prefix: str) -> list[str]:
    """Make the command's statement starting with ``prefix`` raise (once)."""
    raised: list[str] = []

    def hook(conn: Any, cursor: Any, statement: str) -> None:
        if not raised and statement.startswith(prefix):
            raised.append(statement)
            raise RuntimeError("interrupted")

    _hook(monkeypatch, hook)
    return raised


def test_a_failed_downgrade_exits_1_audits_nothing_and_the_next_run_goes_on(
    site: Site, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merge step that fails (``mt_dhcp``'s second month): exit 1 with the TR/EN text naming
    the backup restore, nothing on stdout, the steps before it committed, the version, the
    window and the audit log as they were; the second run goes on and audits once."""
    rows = _upgraded(site, capsys)
    months = sorted(
        {month_of(site_day(p.start)) for p in _partitions(site.engine) if p.day is not None}
    )
    second = partition_name("mt_dhcp", months[1])
    raised = _interrupt_at(monkeypatch, f'ALTER TABLE "mt_dhcp" ATTACH PARTITION "{second}"')
    before = _entries(site.engine)
    assert site.run("--to", BEFORE, "--json") == 1
    captured = capsys.readouterr()
    assert raised
    assert captured.out == ""
    assert f"Şema {BEFORE} sürümüne geri alınamadı" in captured.err
    assert f"[the schema was not downgraded to {BEFORE}]: interrupted" in captured.err
    assert "docs/YEDEKLEME.md bölüm 5" in captured.err
    assert "docs/BACKUP.md section 5" in captured.err
    assert _revision(site.engine) == (REVISION,)
    assert _window(site.engine) == (30, ":seed")
    assert len(_entries(site.engine)) == len(before)
    assert _daily(site.engine, "fg_app") == []  # merged before mt_dhcp (name order)
    assert _daily(site.engine, "mt_dhcp") != []
    assert _rows(site.engine) == rows

    monkeypatch.undo()
    assert site.run("--to", BEFORE, "--json") == 0  # the second run
    document = _json_out(capsys)
    (entry,) = _entries(site.engine)[len(before) :]
    assert document["audit_id"] == entry.id
    assert document["retention_days"] == {"before": 30, "after": 400}
    assert (document["from"], document["to"]) == (REVISION, BEFORE)
    assert (entry.details["old"], entry.details["new"]) == (30, 400)
    assert _revision(site.engine) == (BEFORE,)
    for table in TIME_SERIES_TABLES:
        assert _daily(site.engine, table) == [], table
    assert _rows(site.engine) == rows
    assert _chain_ok(site.engine) == len(before) + 1


def test_a_failed_audit_entry_keeps_the_version_and_the_window(
    site: Site, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry, the window's value and alembic's version commit together: an entry that
    cannot be written leaves the version at ``0021`` (every month merged, committed step by
    step) and the window at 30; the next run moves the version and writes the entry once."""
    _upgraded(site, capsys)
    raised = _interrupt_at(monkeypatch, "INSERT INTO audit_log")
    before = len(_entries(site.engine))
    assert site.run("--to", BEFORE) == 1
    assert raised
    assert "interrupted" in capsys.readouterr().err
    assert _revision(site.engine) == (REVISION,)
    assert _window(site.engine) == (30, ":seed")
    assert len(_entries(site.engine)) == before
    for table in TIME_SERIES_TABLES:
        assert _daily(site.engine, table) == [], table

    monkeypatch.undo()
    assert site.run("--to", BEFORE, "--json") == 0
    assert _json_out(capsys)["retention_days"] == {"before": 30, "after": 400}
    assert _revision(site.engine) == (BEFORE,)
    assert _window(site.engine) == (400, ":seed")
    assert _chain_ok(site.engine) == before + 1


def test_the_migrations_lock_is_held_for_the_session(
    site: Site, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``0021``'s steps commit one by one: the migrations' lock (the one ``arcilogger-db
    upgrade`` takes) stays held across those commits, and a lock wait is bounded."""
    _upgraded(site, capsys)
    seen: list[tuple[bool, str]] = []

    def hook(conn: Any, cursor: Any, statement: str) -> None:
        if "ATTACH PARTITION" not in statement:
            return
        with site.engine.begin() as other:  # another session, as an upgrade would be
            free = other.execute(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
            ).scalar_one()
        (timeout,) = cursor.connection.execute("SHOW lock_timeout").fetchone()
        seen.append((free, timeout))

    _hook(monkeypatch, hook)
    assert site.run("--to", BEFORE) == 0
    assert len(seen) >= 2 * len(TIME_SERIES_TABLES)  # steps after the first one's commit
    assert set(seen) == {(False, "30s")}
    with site.engine.begin() as other:  # released at the end
        assert other.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
        ).scalar_one()


@pytest.mark.parametrize("target", ["0099_unknown", "0020", "base", "heads", "-1", ""])
def test_a_revision_this_version_does_not_ship_exits_2_before_connecting(
    site: Site, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    content = site.config.read_text(encoding="utf-8")  # never reached: the check comes first
    site.config.write_text(content.replace('name = "', 'name = "arci_missing_'), encoding="utf-8")
    assert site.run("--to", target, "--json") == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "bilinmeyen şema sürümü [unknown schema revision]" in captured.err


def test_a_revision_above_the_database_exits_2_and_changes_nothing(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    upgrade(site.engine, revision=BEFORE, timescale="off")
    assert site.run("--to", REVISION, "--json") == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"{REVISION} veritabanının şema sürümünden ({BEFORE}) önce değil" in captured.err
    assert _revision(site.engine) == (BEFORE,)


def test_a_database_without_a_schema_is_refused(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    assert site.run("--to", BEFORE) == 1
    err = capsys.readouterr().err
    assert "[the database is not at one schema revision]: -" in err
    assert "docs/BACKUP.md section 5" in err
    assert _revision(site.engine) == ()


def test_an_unreachable_database_exits_3(site: Site, capsys: pytest.CaptureFixture[str]) -> None:
    content = site.config.read_text(encoding="utf-8")
    site.config.write_text(content.replace('name = "', 'name = "arci_missing_'), encoding="utf-8")
    assert site.run("--to", BEFORE) == 3
    assert "Veritabanına bağlanılamadı" in capsys.readouterr().err
