"""Revision ``0023_usage_period`` (contract W5 §6 K3): the boolean ``off_hours`` of the seven usage
rollup tables becomes ``period`` (``work``, ``break``, ``off``) in the same place of their primary
keys; ``true`` → ``off`` and ``false`` → ``work``, every other column as it was. The downgrade
turns it back after deleting the days that have break rows (the older program rebuilds them), and
the upgrade runs again."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, current_revisions, upgrade
from arcilogger_db.testing import TestServer

BEFORE = "0022_lease_moves"
REVISION = "0023_usage_period"
DAY, OTHER = date(2026, 10, 14), date(2026, 10, 15)
PERSON = "person:1"
#: Table → its primary key's columns at this revision.
KEYS = {
    "usage_daily": ["day", "subject", "period"],
    "usage_device_daily": ["day", "mac", "subject", "period"],
    "usage_domain_daily": ["day", "subject", "domain", "period"],
    "usage_app_daily": ["day", "subject", "app", "period"],
    "usage_hourly": ["hour", "subject", "period"],
    "usage_blocked": ["id"],
    "usage_urls": ["id"],
}


@pytest.fixture
def empty_engine(pg_server: TestServer) -> Iterator[Engine]:
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        pg_server.drop_database(name)


def _rows(engine: Engine, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(sql), params)]


def _old_rollups(engine: Engine) -> None:
    """A day as a ``0022_lease_moves`` program wrote it: a work-hours and an off-hours row in each
    table (and a second day with one row, which no downgrade touches)."""
    with engine.begin() as connection:
        for day in (DAY, OTHER):
            connection.execute(
                text(
                    "INSERT INTO usage_days (day, status, computed_at, inputs_hash, "
                    "attribution_hash) VALUES (:day, 'final', now(), :hash, :hash)"
                ),
                {"day": day, "hash": "0" * 64},
            )
        for off, active in ((False, 240), (True, 120)):
            values = {"day": DAY, "subject": PERSON, "off": off, "active": active}
            connection.execute(
                text(
                    "INSERT INTO usage_daily (day, subject, off_hours, kind, person_id, "
                    "online_seconds, active_seconds) VALUES (:day, :subject, :off, 'person', 1, "
                    ":active, :active)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO usage_device_daily (day, mac, subject, off_hours, "
                    "online_seconds) VALUES (:day, '00:00:5e:00:53:01', :subject, :off, :active)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO usage_domain_daily (day, subject, domain, off_hours, kind, "
                    "seconds) VALUES (:day, :subject, 'portal.example', :off, 'domain', :active)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO usage_app_daily (day, subject, app, off_hours, seconds) "
                    "VALUES (:day, :subject, 'HTTPS.BROWSER', :off, :active)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO usage_hourly (hour, day, subject, off_hours, active_seconds) "
                    "VALUES (:hour, :day, :subject, :off, :active)"
                ),
                {**values, "hour": f"{DAY.isoformat()}T{'16' if off else '06'}:00:00+00:00"},
            )
            connection.execute(
                text(
                    "INSERT INTO usage_blocked (day, time, subject, off_hours, source, action) "
                    "VALUES (:day, now(), :subject, :off, 'web', 'blocked')"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO usage_urls (day, subject, off_hours, url, hits, first_seen, "
                    "last_seen) VALUES (:day, :subject, :off, '/izle', 1, now(), now())"
                ),
                values,
            )
        connection.execute(
            text(
                "INSERT INTO usage_daily (day, subject, off_hours, kind, active_seconds) "
                "VALUES (:day, :subject, false, 'person', 60)"
            ),
            {"day": OTHER, "subject": PERSON},
        )


def _primary_key(engine: Engine, table: str) -> list[str]:
    columns: list[str] = inspect(engine).get_pk_constraint(table)["constrained_columns"]
    return columns


def _check_upgraded(engine: Engine) -> None:
    for table, key in KEYS.items():
        columns = {c["name"]: c for c in inspect(engine).get_columns(table)}
        assert "off_hours" not in columns, table
        assert str(columns["period"]["type"]) == "TEXT"
        assert columns["period"]["nullable"] is False
        assert _primary_key(engine, table) == key, table
    for table in KEYS:
        sql = f"SELECT period, count(*) FROM {table} WHERE day = :day GROUP BY 1"  # noqa: S608 - fixed names
        rows = _rows(engine, sql, day=DAY)
        assert sorted(rows) == [("off", 1), ("work", 1)], table
    assert _rows(
        engine,
        "SELECT period, active_seconds FROM usage_daily WHERE day = :day ORDER BY 1",
        day=DAY,
    ) == [("off", 120), ("work", 240)]


def test_up_down_and_up_again(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    _old_rollups(empty_engine)
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    _check_upgraded(empty_engine)
    # The check refuses a part that is none of the three.
    with pytest.raises(IntegrityError), empty_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO usage_daily (day, subject, period, kind) "
                "VALUES (:day, 'person:2', 'lunch', 'person')"
            ),
            {"day": DAY},
        )
    # A 0023 program split a minute into a break on the first day.
    with empty_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO usage_daily (day, subject, period, kind, active_seconds) "
                "VALUES (:day, :subject, 'break', 'person', 60)"
            ),
            {"day": DAY, "subject": PERSON},
        )
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
    with empty_engine.connect() as connection:
        assert current_revisions(connection) == (BEFORE,)
    for table, key in KEYS.items():
        columns = {c["name"] for c in inspect(empty_engine).get_columns(table)}
        assert "period" not in columns, table
        assert "off_hours" in columns, table
        assert _primary_key(empty_engine, table) == [
            "off_hours" if name == "period" else name for name in key
        ], table
        # The day with a break is gone from every table (the older program rebuilds it) ...
        assert _rows(empty_engine, f"SELECT count(*) FROM {table} WHERE day = :day", day=DAY) == [  # noqa: S608 - fixed names
            (0,)
        ], table
    assert _rows(empty_engine, "SELECT day FROM usage_days ORDER BY day") == [(OTHER,)]
    # ... the other day stays as it was.
    assert _rows(
        empty_engine, "SELECT day, off_hours, active_seconds FROM usage_daily ORDER BY day"
    ) == [(OTHER, False, 60)]
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    assert _rows(empty_engine, "SELECT day, period FROM usage_daily ORDER BY day") == [
        (OTHER, "work")
    ]


def test_a_downgrade_without_breaks_keeps_every_row(empty_engine: Engine) -> None:
    """No break rows: the downgrade only turns ``period`` back, nothing is deleted."""
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    _old_rollups(empty_engine)
    upgrade(empty_engine, revision=REVISION, timescale="off")
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
    assert _rows(
        empty_engine,
        "SELECT day, off_hours, active_seconds FROM usage_daily ORDER BY day, off_hours",
    ) == [(DAY, False, 240), (DAY, True, 120), (OTHER, False, 60)]
    assert _rows(empty_engine, "SELECT count(*) FROM usage_days") == [(2,)]
