"""Revision ``0024_rules`` (Wave 6 contract §2.5): ``rule_state``, ``alert_evidence``,
``alerts.assignee_id``, the ``assign`` event kind and the ``webhook`` delivery kind. The downgrade
drops exactly these, deleting first the rows only this revision allows (``assign`` events,
``webhook`` deliveries); the alerts and their other events and deliveries stay, and the upgrade
runs again."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, current_revisions, upgrade
from arcilogger_db.testing import TestServer

BEFORE = "0023_usage_period"
REVISION = "0024_rules"
#: The evidence of a log row: its table, id, times, source and raw reference.
EVIDENCE = (
    "INSERT INTO alert_evidence (alert_id, n, tbl, row_id, time, time_received, source, "
    "raw_day, raw_line, raw_offset) VALUES (1, 1, 'fg_traffic', 41, "
    "'2026-11-12T09:41:07+03:00', '2026-11-12T09:41:08+03:00', 'fw1', '2026-11-12', 17, 4096)"
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


def _rows(engine: Engine, sql: str, **params: Any) -> list[tuple[Any, ...]]:
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(sql), params)]


def _execute(engine: Engine, sql: str, **params: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text(sql), params)


def _alerts(engine: Engine) -> None:
    """A user and an alert with a note and an e-mail delivery, as a ``0023`` program wrote them."""
    _execute(
        engine,
        "INSERT INTO users (id, username, display_name, role, password_hash) "
        "OVERRIDING SYSTEM VALUE VALUES (7, 'guvenlik', 'Güvenlik', 'admin', 'x')",
    )
    _execute(
        engine,
        "INSERT INTO alerts (id, rule, severity, title, dedup_key) OVERRIDING SYSTEM VALUE "
        "VALUES (1, 'R15', 'high', 'Log kaynağı sessiz: fw1', 'R15:fw1')",
    )
    _execute(
        engine,
        "INSERT INTO alert_events (alert_id, kind, text, actor) "
        "VALUES (1, 'note', 'bakılıyor', 'guvenlik')",
    )
    _execute(
        engine,
        "INSERT INTO alert_deliveries (alert_id, kind, recipient) "
        "VALUES (1, 'new', 'it@ornek.example')",
    )


def test_up_down_and_up_again(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    _alerts(empty_engine)
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    tables = set(inspect(empty_engine).get_table_names())
    assert {"rule_state", "alert_evidence"} <= tables
    indexes = {index["name"] for index in inspect(empty_engine).get_indexes("alerts")}
    assert "ix_alerts_assignee_state" in indexes
    # What the revision allows: an assignee, an assign event, a webhook delivery, evidence.
    _execute(empty_engine, "UPDATE alerts SET assignee_id = 7 WHERE id = 1")
    _execute(
        empty_engine,
        "INSERT INTO alert_events (alert_id, kind, text, actor, actor_id) "
        "VALUES (1, 'assign', 'guvenlik', 'guvenlik', 7), (1, 'assign', NULL, 'guvenlik', 7)",
    )
    _execute(
        empty_engine,
        "INSERT INTO alert_deliveries (alert_id, kind, recipient) VALUES (1, 'webhook', 'siem')",
    )
    _execute(empty_engine, EVIDENCE)
    _execute(
        empty_engine,
        "INSERT INTO alert_evidence (alert_id, n, tbl, time) "
        "VALUES (1, 2, 'devices', '2026-11-12T09:00:00+03:00')",
    )
    _execute(
        empty_engine,
        "INSERT INTO rule_state (rule, watermark, failures) VALUES ('R01', now(), 0)",
    )
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
    with empty_engine.connect() as connection:
        assert current_revisions(connection) == (BEFORE,)
    tables = set(inspect(empty_engine).get_table_names())
    assert not {"rule_state", "alert_evidence"} & tables
    columns = {column["name"] for column in inspect(empty_engine).get_columns("alerts")}
    assert "assignee_id" not in columns
    assert _rows(empty_engine, "SELECT id, rule FROM alerts") == [(1, "R15")]
    assert _rows(empty_engine, "SELECT kind FROM alert_deliveries") == [("new",)]
    assert _rows(empty_engine, "SELECT kind FROM alert_events") == [("note",)]
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    assert _rows(empty_engine, "SELECT assignee_id FROM alerts") == [(None,)]


def test_the_downgrade_deletes_only_the_rows_the_older_checks_refuse(
    empty_engine: Engine,
) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    _alerts(empty_engine)
    upgrade(empty_engine, revision=REVISION, timescale="off")
    _execute(
        empty_engine,
        "INSERT INTO alert_events (alert_id, kind, text, actor) "
        "VALUES (1, 'assign', 'guvenlik', 'guvenlik')",
    )
    _execute(
        empty_engine,
        "INSERT INTO alert_deliveries (alert_id, kind, recipient) VALUES (1, 'webhook', 'siem')",
    )
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
    assert _rows(empty_engine, "SELECT kind, text FROM alert_events") == [("note", "bakılıyor")]
    assert _rows(empty_engine, "SELECT kind, recipient FROM alert_deliveries") == [
        ("new", "it@ornek.example")
    ]


@pytest.mark.parametrize(
    "sql",
    [
        # An assign event names a user (at most 64 characters) or nobody, never a state.
        "INSERT INTO alert_events (alert_id, kind, from_state, to_state, actor) "
        "VALUES (1, 'assign', 'open', 'acknowledged', 'guvenlik')",
        "INSERT INTO alert_events (alert_id, kind, text, actor) "
        "VALUES (1, 'assign', repeat('x', 65), 'guvenlik')",
        "INSERT INTO alert_events (alert_id, kind, text, actor) VALUES (1, 'assign', '', 'g')",
        # Evidence of a log row needs its raw reference; only the listed tables.
        "INSERT INTO alert_evidence (alert_id, n, tbl, row_id) VALUES (1, 1, 'fg_traffic', 41)",
        "INSERT INTO alert_evidence (alert_id, n, tbl, time) VALUES (1, 1, 'users', now())",
        "INSERT INTO alert_evidence (alert_id, n, tbl, time) VALUES (1, 0, 'devices', now())",
        "INSERT INTO alert_deliveries (alert_id, kind, recipient) VALUES (1, 'sms', 'x')",
        "INSERT INTO rule_state (rule, failures) VALUES ('R01', -1)",
    ],
)
def test_the_checks_refuse_what_the_contract_does_not_allow(empty_engine: Engine, sql: str) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    _alerts(empty_engine)
    upgrade(empty_engine, revision=REVISION, timescale="off")
    with pytest.raises(IntegrityError):
        _execute(empty_engine, sql)


def test_evidence_and_assignment_follow_their_rows(empty_engine: Engine) -> None:
    """Evidence goes with its alert; an assignee whose user row goes is NULL."""
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    _alerts(empty_engine)
    upgrade(empty_engine, revision=REVISION, timescale="off")
    _execute(empty_engine, "UPDATE alerts SET assignee_id = 7")
    _execute(empty_engine, EVIDENCE)
    _execute(empty_engine, "DELETE FROM users WHERE id = 7")
    assert _rows(empty_engine, "SELECT assignee_id FROM alerts") == [(None,)]
    _execute(empty_engine, "DELETE FROM alerts")
    assert _rows(empty_engine, "SELECT count(*) FROM alert_evidence") == [(0,)]
