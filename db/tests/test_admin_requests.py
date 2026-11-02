"""``admin_requests`` (ADR-0014, contract 2.15): migration 0015 up and down, and the database's
own checks — the names of the administration contract, ``terminal`` and no secret value in
``params`` or ``result``."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from arcilogger_core.admin.contract import (
    ADMIN_REQUEST_KINDS,
    SECTIONS,
    STATES,
    TERMINAL_STATES,
    new_id,
)
from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, upgrade
from arcilogger_db.models import AdminRequest, User
from arcilogger_db.testing import TestServer

MIGRATION = importlib.import_module("arcilogger_db.migrations.versions.v0015_admin_requests")
#: A masked secret as the API writes it into ``params.changes`` (contract 2.14).
MASKED = {"secret": True, "old": "set", "new": "set", "changed": True}


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


def _row(**values: Any) -> AdminRequest:
    fields: dict[str, Any] = {
        "id": new_id(),
        "kind": "config.apply",
        "section": "smtp",
        "channel": "agent",
        "state": "queued",
        "requested_by": "ayse",
    }
    fields.update(values)
    return AdminRequest(**fields)


def _refused(session: Session, row: AdminRequest, constraint: str) -> None:
    savepoint = session.begin_nested()
    session.add(row)
    with pytest.raises(IntegrityError, match=constraint):
        session.flush()
    savepoint.rollback()


def test_migration_0015_up_and_down(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision="0014_license")
    assert "admin_requests" not in inspect(empty_engine).get_table_names()
    assert upgrade(empty_engine, revision="0015_admin_requests").after == ("0015_admin_requests",)
    inspector = inspect(empty_engine)
    columns = {column["name"]: column for column in inspector.get_columns("admin_requests")}
    assert set(columns) == {
        "id",
        "kind",
        "section",
        "channel",
        "state",
        "terminal",
        "requested_by_id",
        "requested_by",
        "created_at",
        "updated_at",
        "expires_at",
        "effective_at",
        "params",
        "result",
        "error_code",
        "rolled_back",
        "audit_id",
    }
    nullable = {name for name, column in columns.items() if column["nullable"]}
    assert nullable == {
        "section",
        "requested_by_id",
        "expires_at",
        "effective_at",
        "result",
        "error_code",
        "audit_id",
    }
    indexes = {index["name"]: index for index in inspector.get_indexes("admin_requests")}
    assert indexes["ix_admin_requests_channel_state_created_at"]["column_names"] == [
        "channel",
        "state",
        "created_at",
    ]
    assert indexes["ix_admin_requests_kind_created_at"]["column_sorting"] == {
        "created_at": ("desc",)
    }
    assert "NOT terminal" in str(
        indexes["ix_admin_requests_section"]["dialect_options"]["postgresql_where"]
    )
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0014_license")
    assert "admin_requests" not in inspect(empty_engine).get_table_names()
    assert upgrade(empty_engine, revision="0015_admin_requests").after == (
        "0015_admin_requests",
    )  # and up again


def test_the_migration_names_what_the_contract_names() -> None:
    """Migrations are frozen in time; the contract is not: a new kind or state needs a new
    revision (this test fails until there is one)."""
    assert MIGRATION.KINDS == ADMIN_REQUEST_KINDS
    assert MIGRATION.SECTIONS == SECTIONS
    assert MIGRATION.STATES == STATES
    assert set(MIGRATION.TERMINAL) == TERMINAL_STATES


def test_requests_of_both_channels_are_stored(db_session: Session) -> None:
    user = User(username="ayse", display_name="Ayşe", role="admin", password_hash="!")
    db_session.add(user)
    db_session.flush()
    agent = _row(
        requested_by_id=user.id,
        params={"changes": {"smtp.host": {"old": None, "new": "smtp.ornek.example"}}},
    )
    worker = _row(
        kind="worker.test_email",
        section=None,
        channel="worker",
        params={"to": "bt@ornek.example"},
    )
    db_session.add_all([agent, worker])
    db_session.commit()
    stored = db_session.get(AdminRequest, agent.id)
    assert stored is not None
    assert (stored.terminal, stored.rolled_back, stored.result) == (False, False, None)
    assert stored.created_at == stored.updated_at
    # The history keeps the name when the user row goes.
    db_session.delete(user)
    db_session.commit()
    db_session.expire_all()
    assert db_session.scalars(select(AdminRequest.requested_by_id)).all() == [None, None]
    assert {row.requested_by for row in db_session.scalars(select(AdminRequest))} == {"ayse"}


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ({"id": "6F1C1A2B3C4D5E6F708192A3B4C5D6E7"}, "ck_admin_requests_id"),
        ({"id": "6f1c1a2b"}, "ck_admin_requests_id"),
        ({"kind": "config.delete"}, "ck_admin_requests_kind"),
        ({"channel": "api"}, "ck_admin_requests_channel"),
        ({"kind": "worker.test_email"}, "ck_admin_requests_channel_kind"),
        ({"kind": "action.seal", "section": None, "channel": "worker"}, "channel_kind"),
        ({"section": "database"}, "ck_admin_requests_section"),
        ({"state": "done"}, "ck_admin_requests_state"),
        ({"state": "active"}, "ck_admin_requests_terminal"),
        ({"state": "pending", "terminal": True}, "ck_admin_requests_terminal"),
        ({"requested_by": " "}, "ck_admin_requests_requested_by"),
    ],
)
def test_the_database_keeps_rows_to_the_contract(
    db_session: Session, values: dict[str, Any], constraint: str
) -> None:
    _refused(db_session, _row(**values), constraint)


def test_every_state_is_stored_with_its_terminal_flag(db_session: Session) -> None:
    for state in STATES:
        db_session.add(_row(state=state, terminal=state in TERMINAL_STATES))
    db_session.commit()
    assert db_session.scalar(text("SELECT count(*) FROM admin_requests")) == len(STATES)


@pytest.mark.parametrize(
    "secret",
    [
        {"password": "hunter-2"},
        {"changes": {"smtp.password": {"old": "set", "new": "N3w-secret"}}},
        {"changes": {"kamusm.customer": "1234567"}},
        {"steps": [{"probe": {"Token": "gAAAAABfake"}}]},
        {"a": {"b": [{"customer_number": 7}]}},
        {"smtp_PASSWORD": None},
    ],
)
def test_no_secret_value_enters_params_or_result(
    db_session: Session, secret: dict[str, Any]
) -> None:
    """Contract 2.15: no ``params`` or ``result`` written by the API or the worker holds a key
    named like a secret — the database refuses one, whoever writes it."""
    _refused(db_session, _row(params=secret), "ck_admin_requests_params_masked")
    _refused(
        db_session,
        _row(state="succeeded", terminal=True, kind="action.credit", section=None, result=secret),
        "ck_admin_requests_result_masked",
    )


def test_masked_secrets_and_names_of_secrets_are_allowed(db_session: Session) -> None:
    row = _row(
        params={
            "changes": {"smtp.password": MASKED, "smtp.host": {"old": "a", "new": "b"}},
            "secrets": [{"name": "smtp.password", "action": "set"}],
            "note": "password, customer and token are words a value may hold",
        },
        result={"error_text": 'server said "password": refused'},
    )
    db_session.add(row)
    db_session.commit()
    db_session.expire_all()
    stored = db_session.get(AdminRequest, row.id)
    assert stored is not None
    assert stored.params["changes"]["smtp.password"] == MASKED
