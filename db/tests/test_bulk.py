"""Bulk loading with COPY (the ingest service's loader) and the driver boundary."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from arcilogger_db.bulk import copy_rows
from arcilogger_db.models import FgDns, Message, MtDhcp
from arcilogger_db.rowcheck import check_row

NOW = datetime(2026, 10, 12, 15, 19, 7, 250000, tzinfo=UTC)
COMMON: dict[str, Any] = {
    "time": NOW,
    "time_received": NOW,
    "source": "core-r1",
    "sender": "172.20.10.1",
    "raw_day": date(2026, 10, 12),
    "raw_offset": 0,
}
ROOT = Path(__file__).resolve().parents[2]


def _dhcp(line: int, **values: Any) -> dict[str, Any]:
    return {
        **COMMON,
        "raw_line": line,
        "server": "dhcp60",
        "vlan": 60,
        "action": "assigned",
        "ip": "172.20.60.183",
        "mac": "02:00:5E:00:53:46",
        "hostname": "Deniz'in\ttelefonu\n",
        **values,
    }


def test_rows_load_with_their_python_types(db_engine: Engine) -> None:
    rows = [_dhcp(1), _dhcp(2, ip="172.20.60.251", hostname=None)]
    assert all(check_row("mt_dhcp", row, complete=True) == [] for row in rows)
    with db_engine.begin() as connection:
        assert copy_rows(connection, "mt_dhcp", rows) == 2
        assert (
            copy_rows(
                connection,
                "fg_dns",
                [{**COMMON, "raw_line": 3, "qname": "örnek.com.tr", "answers": ["1.1.1.1", "::1"]}],
            )
            == 1
        )
        assert (
            copy_rows(
                connection,
                "messages",
                [
                    {
                        **COMMON,
                        "raw_line": 1,
                        "type": "dhcp",
                        "text": "şğü",
                        "extra": {"kısa": "değer", "n": 7, "liste": [1, None]},
                    }
                ],
            )
            == 1
        )
    with Session(db_engine) as session:
        loaded = session.scalars(select(MtDhcp).order_by(MtDhcp.raw_line)).all()
        dns = session.scalars(select(FgDns)).one()
        message = session.scalars(select(Message)).one()
    assert [(r.id, r.raw_line, r.ip, r.mac, r.hostname) for r in loaded] == [
        (1, 1, "172.20.60.183", "02:00:5e:00:53:46", "Deniz'in\ttelefonu\n"),
        (2, 2, "172.20.60.251", "02:00:5e:00:53:46", None),
    ]
    assert loaded[0].time == NOW  # microseconds and UTC survive
    assert dns.answers == ["1.1.1.1", "::1"]
    assert dns.qname == "örnek.com.tr"
    assert message.extra == {"kısa": "değer", "n": 7, "liste": [1, None]}
    assert message.text == "şğü"


def test_nothing_to_load_and_unknown_names(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        assert copy_rows(connection, "mt_dhcp", []) == 0
        with pytest.raises(ValueError, match="unknown table"):
            copy_rows(connection, "no_such_table", [_dhcp(1)])
        with pytest.raises(ValueError, match="unknown column"):
            copy_rows(connection, "mt_dhcp", [{**_dhcp(1), "colour": "red"}])


def test_one_bad_value_fails_the_whole_batch(db_engine: Engine) -> None:
    """Why the ingest service runs check_row first: COPY is all or nothing."""
    bad = _dhcp(2, mac="08:002b:01:0203")
    assert check_row("mt_dhcp", bad) != []
    with pytest.raises(DataError), db_engine.begin() as connection:
        copy_rows(connection, "mt_dhcp", [_dhcp(1), bad])
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM mt_dhcp")).scalar_one() == 0


def test_only_the_database_package_touches_the_driver() -> None:
    """psycopg is LGPL-3.0 (ADR-0005): keep it replaceable behind arcilogger_db."""
    allowed = {"db/src/arcilogger_db/bulk.py"}
    pattern = re.compile(r"^\s*(?:import|from)\s+psycopg\b", re.MULTILINE)
    offenders = [
        path.relative_to(ROOT).as_posix()
        for package in ("core", "collector", "db", "ingest", "worker", "api")
        for path in (ROOT / package).rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
        and path.relative_to(ROOT).as_posix() not in allowed
    ]
    assert offenders == []
