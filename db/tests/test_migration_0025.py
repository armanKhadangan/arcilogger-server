"""Revision ``0025_admin_webhook_test`` (Wave 6 contract §2.4.4, W6-H): ``admin_requests`` takes
the worker-channel kind ``worker.webhook_test`` ("Test gönder"). The downgrade deletes only those
rows, so the older kind check holds again; the other requests stay, and the upgrade runs
again."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, current_revisions, upgrade
from arcilogger_db.testing import TestServer

BEFORE = "0024_rules"
REVISION = "0025_admin_webhook_test"
ROW = (
    "INSERT INTO admin_requests (id, kind, channel, state, requested_by, params) "
    "VALUES (:id, :kind, 'worker', 'queued', 'ayse', CAST(:params AS jsonb))"
)
EMAIL = {"id": "1" * 32, "kind": "worker.test_email", "params": '{"to": "bt@ornek.example"}'}
WEBHOOK = {"id": "2" * 32, "kind": "worker.webhook_test", "params": '{"name": "siem"}'}


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


def _execute(engine: Engine, sql: str, **params: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text(sql), params)


def _kinds(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return list(connection.scalars(text("SELECT kind FROM admin_requests ORDER BY id")))


def test_up_down_and_up_again(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    _execute(empty_engine, ROW, **EMAIL)
    with pytest.raises(IntegrityError, match="ck_admin_requests_kind"):
        _execute(empty_engine, ROW, **WEBHOOK)
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    _execute(empty_engine, ROW, **WEBHOOK)
    assert _kinds(empty_engine) == ["worker.test_email", "worker.webhook_test"]
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
    with empty_engine.connect() as connection:
        assert current_revisions(connection) == (BEFORE,)
    assert _kinds(empty_engine) == ["worker.test_email"]
    with pytest.raises(IntegrityError, match="ck_admin_requests_kind"):
        _execute(empty_engine, ROW, **WEBHOOK)
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    _execute(empty_engine, ROW, **WEBHOOK)
    assert _kinds(empty_engine) == ["worker.test_email", "worker.webhook_test"]
