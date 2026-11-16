"""Revision ``0027_rule_traffic_totals`` (#280): R13's and R14's running totals. The three tables
hold derived data, so the downgrade drops them (the installer lists the revision as lossless);
the upgrade creates them empty again, and the next rule run rebuilds its period from
``fg_traffic``."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text
from sqlalchemy.pool import NullPool

from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, current_revisions, upgrade
from arcilogger_db.testing import TestServer

BEFORE = "0026_rules_time_received"
REVISION = "0027_rule_traffic_totals"
TABLES = {"rule_traffic_state", "rule_traffic_totals", "rule_traffic_evidence"}
PERIOD = "2026-11-14 21:00+00"
ROWS = (
    "INSERT INTO rule_traffic_state (rule, period_start, inputs, filled_until) "
    "VALUES ('R13', :period, '{}', :period)",
    "INSERT INTO rule_traffic_totals "
    "(rule, period_start, srcip, minute, received, rows, amount, first_received, last_received) "
    "VALUES ('R13', :period, '172.20.1.10', :period, :period, 2, 4096, :period, :period)",
    "INSERT INTO rule_traffic_evidence (rule, period_start, srcip, refs) "
    "VALUES ('R13', :period, '172.20.1.10', CAST('[]' AS jsonb))",
)
COUNTS = (
    "SELECT (SELECT count(*) FROM rule_traffic_state), (SELECT count(*) FROM rule_traffic_totals), "
    "(SELECT count(*) FROM rule_traffic_evidence)"
)


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


def _tables(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return TABLES & set(inspect(connection).get_table_names())


def _counts(engine: Engine) -> tuple[int, ...]:
    """The rows of the state, totals and evidence tables."""
    with engine.connect() as connection:
        return tuple(connection.execute(text(COUNTS)).one())


def test_up_down_and_up_again(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    assert _tables(empty_engine) == set()
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    assert _tables(empty_engine) == TABLES
    with empty_engine.begin() as connection:
        for sql in ROWS:
            connection.execute(text(sql), {"period": PERIOD})
    assert _counts(empty_engine) == (1, 1, 1)
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
    with empty_engine.connect() as connection:
        assert current_revisions(connection) == (BEFORE,)
    assert _tables(empty_engine) == set()
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    assert _counts(empty_engine) == (0, 0, 0)
