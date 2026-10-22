"""Bulk loading with ``COPY`` — the only place outside the engine that touches the driver.

The ingest service (B2) loads parsed rows with :func:`copy_rows` instead of driver APIs, so the
PostgreSQL driver stays an implementation detail of ``arcilogger_db``: psycopg 3 is LGPL-3.0
(ADR-0005), and if the owner decides against it only this module and :mod:`arcilogger_db.engine`
change (fallback: pg8000, BSD).

Rows use the Python value types of :mod:`arcilogger_db.rowcheck` (run ``check_row`` first: one
bad value fails the whole ``COPY``). Columns missing from a row are loaded as NULL, so pass
every NOT NULL column without a default; identity columns are omitted and generated. Driver
errors are raised as SQLAlchemy exceptions (``DataError``, ``IntegrityError`` …), so callers
never import the driver; the transaction is then aborted and must be rolled back.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError

from arcilogger_db.rowcheck import table_columns


def copy_rows(
    connection: Connection,
    table: str,
    rows: Iterable[Mapping[str, Any]],
    *,
    columns: Sequence[str] | None = None,
) -> int:
    """``COPY table (columns) FROM STDIN`` inside the connection's transaction; rows written.

    ``columns`` defaults to the keys of the first row. Unknown tables or columns raise
    ``ValueError`` before anything is sent.
    """
    known = table_columns(table)
    if not known:
        raise ValueError(f"unknown table {table!r}")
    iterator = iter(rows)
    first = next(iterator, None)
    if first is None:
        return 0
    names = list(columns if columns is not None else first.keys())
    unknown = [name for name in names if name not in known]
    if unknown:
        raise ValueError(f"{table}: unknown column(s) {', '.join(unknown)}")
    jsonb = [isinstance(known[name].type, JSONB) for name in names]
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(", ").join(sql.Identifier(name) for name in names)
    )
    raw = connection.connection.driver_connection
    if raw is None:  # pragma: no cover - a pooled connection always has one
        raise RuntimeError("no DBAPI connection")
    count = 0
    try:
        with raw.cursor() as cursor, cursor.copy(statement) as copy:
            for row in _chain(first, iterator):
                values = [row.get(name) for name in names]
                copy.write_row(
                    [
                        Jsonb(value) if is_json and value is not None else value
                        for value, is_json in zip(values, jsonb, strict=True)
                    ]
                )
                count += 1
    except psycopg.Error as exc:
        raise DBAPIError.instance(
            f"COPY {table}", None, exc, psycopg.Error, hide_parameters=True
        ) from exc
    return count


def _chain(
    first: Mapping[str, Any], rest: Iterable[Mapping[str, Any]]
) -> Iterable[Mapping[str, Any]]:
    yield first
    yield from rest
