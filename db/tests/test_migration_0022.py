"""Revision ``0022_lease_moves`` (issue #127, deviation #127-1): up and down on native partitions
and on TimescaleDB; the column added NULL to every existing lease without rewriting one, the
downgrade that drops it and nothing else (the leases, their evidence and its ``"vd"`` keys stay
as they were), up again; a NULL of the model stored as SQL NULL, the column loaded only when
named."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, current_revisions, upgrade
from arcilogger_db.models import Lease
from arcilogger_db.testing import TestServer

BEFORE = "0021_db_window"
REVISION = "0022_lease_moves"
#: The columns of ``leases`` at ``0021_db_window``, in their order (``lease_seconds`` from 0017).
COLUMNS = (
    "id",
    "ip",
    "mac",
    "vlan",
    "server",
    "hostname",
    "start",
    "end",
    "end_reason",
    "confidence",
    "source_table",
    "last_evidence",
    "evidence",
    "created_at",
    "updated_at",
    "lease_seconds",
)
#: Every column of a lease as the database prints it, by id.
ROWS = (
    "SELECT id, ARRAY[" + ", ".join(f'CAST("{name}" AS text)' for name in COLUMNS) + "] "
    "FROM leases ORDER BY id"
)


def _fg_ref(line: int, **vd: str | None) -> dict[str, Any]:
    """A FortiGate ref as the lease job writes it: from 0022 on with ``"vd"`` beside its keys."""
    return {
        "table": "fg_dhcp",
        "row_id": line,
        "time": f"2026-10-28T09:12:{line:02d}+00:00",
        "raw": {"source": "fortigate", "day": "2026-10-28", "line": line, "offset": 400 * line},
        "summary": "vlan42 assigned 172.20.42.58 for 00:00:5e:00:53:58 PLN-PC15 (FortiGate DHCP, "
        "logid 0100026001, lease 86400)",
        **vd,
    }


#: The refs of a FortiGate lease: a VDOM, a site without VDOMs, one written before 0022.
FG_REFS = json.dumps([_fg_ref(9, vd="root"), _fg_ref(10, vd=None), _fg_ref(11)])
#: Moves as the lease job writes them (``arcilogger_worker.lookup.refs.moves_json``).
MOVES = json.dumps(
    {
        "truncated": False,
        "groups": [
            {"ip": "172.20.44.58", "interface": "vlan42", "vd": "root", "source": "fortigate",
             "logid": "0100026001", "dhcp_msg": "Ack", "times": [1790000000000000]}
        ],
    }
)  # fmt: skip
#: Recorded, none.
NONE = {"truncated": False, "groups": []}


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


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def _rows(engine: Engine) -> list[tuple[int, list[str | None]]]:
    with engine.connect() as connection:
        return [(row[0], row[1]) for row in connection.execute(text(ROWS))]


def _file(engine: Engine) -> int:
    """The file that holds the rows of ``leases``: a rewrite of the table would change it."""
    return int(_scalar(engine, "SELECT pg_relation_filenode('leases')"))


def _old_leases(engine: Engine) -> None:
    """Leases a ``0021_db_window`` program wrote, and one ``fg_dhcp`` lease whose refs a 0022
    program wrote (as a rollback leaves it)."""
    at = (datetime.now(UTC) - timedelta(days=20)).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(
            text(
                'INSERT INTO leases (ip, mac, vlan, server, start, "end", end_reason, '
                "confidence, source_table, last_evidence, evidence) VALUES "
                "('172.20.42.57', '00:00:5e:00:53:57', 42, 'dhcp42', :start, NULL, 'open', "
                "'high', 'mt_dhcp', :start, '[]'), "
                "('172.20.10.23', '00:00:5e:00:53:43', 10, NULL, :start, :end, 'arp', "
                "'medium', 'mt_arp', :start, '[]')"
            ),
            {"start": at, "end": at + timedelta(hours=1)},
        )
        connection.execute(
            text(
                "INSERT INTO leases (ip, mac, vlan, server, start, end_reason, confidence, "
                "source_table, last_evidence, lease_seconds, evidence) VALUES ('172.20.42.58', "
                "'00:00:5e:00:53:58', 42, 'vlan42', :at, 'open', 'high', 'fg_dhcp', :at, 86400, "
                "CAST(:evidence AS jsonb))"
            ),
            {"at": at, "evidence": FG_REFS},
        )


def _moves_column(engine: Engine) -> Any:
    columns = {column["name"]: column for column in inspect(engine).get_columns("leases")}
    return columns.get("moves")


def _check_upgraded(engine: Engine, before: list[tuple[int, list[str | None]]], file: int) -> None:
    column = _moves_column(engine)
    assert column is not None
    assert str(column["type"]) == "JSONB"
    assert column["nullable"] is True
    assert column["default"] is None
    assert _scalar(engine, "SELECT count(*) FROM leases WHERE moves IS NOT NULL") == 0
    assert _rows(engine) == before  # every lease as it was
    assert _file(engine) == file  # and not one rewritten: the catalog only


def _check_downgraded(engine: Engine) -> None:
    """A 0022 program recorded a move, then the rollback: the column goes, nothing else."""
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE leases SET moves = CAST(:moves AS jsonb) WHERE source_table = 'fg_dhcp'"),
            {"moves": MOVES},
        )
    before, file = _rows(engine), _file(engine)
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
    with engine.connect() as connection:
        assert current_revisions(connection) == (BEFORE,)
    assert _moves_column(engine) is None
    assert tuple(c["name"] for c in inspect(engine).get_columns("leases")) == COLUMNS
    assert _rows(engine) == before  # the evidence with its "vd" keys too
    assert _file(engine) == file
    vds = _scalar(
        engine,
        "SELECT jsonb_agg(item -> 'vd') FROM leases, jsonb_array_elements(evidence) AS item "
        "WHERE source_table = 'fg_dhcp'",
    )
    assert vds == ["root", None, None]  # the JSON null of a site without VDOMs, and no key
    assert (
        _scalar(
            engine,
            "SELECT count(*) FROM leases, jsonb_array_elements(evidence) AS item WHERE item ? 'vd'",
        )
        == 2
    )
    assert upgrade(engine, revision=REVISION).after == (REVISION,)  # and up again
    _check_upgraded(engine, before, _file(engine))


def test_up_and_down_on_native_partitions(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    _old_leases(empty_engine)
    assert _moves_column(empty_engine) is None
    before, file = _rows(empty_engine), _file(empty_engine)
    assert len(before) == 3
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    _check_upgraded(empty_engine, before, file)
    _check_downgraded(empty_engine)


def test_up_and_down_on_timescaledb(empty_engine: Engine) -> None:
    """``leases`` is a plain table on TimescaleDB too: the same change. On the Linux CI job
    (``ARCILOGGER_EXPECT_TIMESCALE=1``) it fails instead of skipping."""
    expected = os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1"
    available = _scalar(
        empty_engine, "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'"
    )
    if available is None:
        if expected:
            pytest.fail("TimescaleDB expected but not installed")
        pytest.skip("TimescaleDB is not installed (the Linux CI job runs this)")
    upgrade(empty_engine, revision=BEFORE, timescale="require")
    status = _scalar(empty_engine, "SELECT value FROM settings WHERE key = 'db.timescale'")
    assert status["storage"] == "hypertables", status
    _old_leases(empty_engine)
    before, file = _rows(empty_engine), _file(empty_engine)
    assert upgrade(empty_engine, revision=REVISION).after == (REVISION,)
    _check_upgraded(empty_engine, before, file)
    _check_downgraded(empty_engine)


def test_a_lease_without_moves_is_stored_as_null_and_moves_load_only_when_named(
    db_engine: Engine,
) -> None:
    """``None`` is SQL NULL (not recorded), not the JSON ``null``; no group is recorded, none. A
    ``select(Lease)`` leaves the column out and raises when it is read by accident."""
    at = datetime(2026, 10, 28, 9, 12, 4, tzinfo=UTC)
    with Session(db_engine) as session:
        for ip, moves in (("172.20.42.57", None), ("172.20.42.58", NONE)):
            session.add(
                Lease(
                    ip=ip,
                    mac="00:00:5e:00:53:57",
                    start=at,
                    confidence="high",
                    source_table="fg_dhcp",
                    moves=moves,
                )
            )
        session.add(
            Lease(ip="172.20.42.59", mac="00:00:5e:00:53:59", start=at, confidence="medium",
                  source_table="mt_arp")
        )  # fmt: skip
        session.commit()
        stored = session.execute(
            text("SELECT host(ip), moves IS NULL, jsonb_typeof(moves) FROM leases ORDER BY ip")
        ).all()
        assert [tuple(row) for row in stored] == [
            ("172.20.42.57", True, None),
            ("172.20.42.58", False, "object"),
            ("172.20.42.59", True, None),
        ]
    with Session(db_engine) as session:
        lease = session.scalars(select(Lease).where(Lease.ip == "172.20.42.58")).one()
        with pytest.raises(InvalidRequestError, match="moves"):
            _ = lease.moves
        named = session.execute(select(Lease.ip, Lease.moves).order_by(Lease.ip)).all()
        assert [(str(ip), moves) for ip, moves in named] == [
            ("172.20.42.57", None),
            ("172.20.42.58", NONE),
            ("172.20.42.59", None),
        ]
