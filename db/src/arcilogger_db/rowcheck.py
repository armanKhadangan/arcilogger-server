"""Validate row values against the column types of a table before loading them.

The ingest service loads rows in batches with ``COPY``; one bad value fails the whole batch.
:func:`check_row` finds such values first. It may be **stricter** than PostgreSQL (a valid but
unusual spelling becomes a parse failure), never looser — a database test casts every accepted
sample to make sure (wrong Python type, integer out of range, NUL in a
string, naive datetime, invalid INET/MACADDR text, unknown column …), so a parser bug becomes a
``parse_failures`` entry instead of a stuck ingest. Parser tests use it on every emitted row.

Python value types per column type (the contract between parsers and the loader):

=============================  ================================================================
Column type                    Python value
=============================  ================================================================
SMALLINT / INTEGER / BIGINT    ``int`` (not ``bool``) within the column's range
DOUBLE PRECISION              finite ``float`` or ``int`` (not ``bool``; no NaN/infinity)
TEXT                           ``str`` without NUL characters
BOOLEAN                        ``bool``
TIMESTAMP WITH TIME ZONE       aware ``datetime``
DATE                           ``date`` (not ``datetime``)
INET                           ``str`` such as ``"172.20.10.37"`` (or an ``ipaddress`` object);
                               no IPv6 zone id (``fe80::1%eth0``), PostgreSQL rejects it
CIDR                           ``str`` such as ``"172.20.10.0/24"``
MACADDR                        ``str`` in a layout PostgreSQL documents for ``macaddr``:
                               ``02:00:5e:00:53:46``, ``02-00-5e-00-53-46``, ``02005e:005346``,
                               ``02005e-005346``, ``0200.5e00.5346``, ``0200-5e00-5346``,
                               ``02005e005346`` (any case)
JSONB                          JSON-serialisable ``dict``/``list``/scalar; no NaN, no NUL
ARRAY(x)                       ``list`` of values valid for ``x``
UUID                           ``uuid.UUID`` (the licence tables)
BYTEA                          ``bytes`` (the licence tables)
=============================  ================================================================
"""

from __future__ import annotations

import ipaddress
import json
import math
import re
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from functools import cache
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    Float,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, CIDR, INET, JSONB, MACADDR, TIMESTAMP
from sqlalchemy.types import TypeEngine

from arcilogger_db.models import Base

#: Most specific first: SmallInteger and BigInteger are subclasses of Integer.
_RANGES: Final = {
    SmallInteger: (-(2**15), 2**15 - 1),
    BigInteger: (-(2**63), 2**63 - 1),
    Integer: (-(2**31), 2**31 - 1),
}
#: The input layouts of PostgreSQL's ``macaddr`` type (documentation, "Network Address Types").
_MACADDR = re.compile(
    r"^(?:"
    r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"
    r"|[0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5}"
    r"|[0-9A-Fa-f]{6}[:-][0-9A-Fa-f]{6}"
    r"|[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}"
    r"|[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}"
    r"|[0-9A-Fa-f]{12}"
    r")$"
)


@cache
def table_columns(table: str) -> Mapping[str, Column[Any]]:
    """Columns of ``table`` by name (empty when the table does not exist)."""
    found = Base.metadata.tables.get(table)
    return {} if found is None else {column.name: column for column in found.columns}


def required_columns(table: str) -> frozenset[str]:
    """NOT NULL columns without a server default or identity (must always be supplied)."""
    return frozenset(
        name
        for name, column in table_columns(table).items()
        if not column.nullable and column.server_default is None and column.identity is None
    )


def _integer_range(kind: TypeEngine[Any]) -> tuple[int, int]:
    for base, bounds in _RANGES.items():
        if isinstance(kind, base):
            return bounds
    return _RANGES[Integer]


def check_value(kind: TypeEngine[Any], value: Any) -> str | None:
    """Why ``value`` cannot be stored in a column of type ``kind`` (``None`` = fine)."""
    if isinstance(kind, ARRAY):
        if not isinstance(value, list | tuple):
            return f"expected a list, got {type(value).__name__}"
        for index, item in enumerate(value):
            if item is not None and (problem := check_value(kind.item_type, item)):
                return f"[{index}] {problem}"
        return None
    if isinstance(kind, JSONB):
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            return f"not JSON-serialisable: {exc}"
        return "JSON contains a NUL character (\\u0000)" if _json_has_nul(value) else None
    if isinstance(kind, Boolean):
        return None if isinstance(value, bool) else f"expected bool, got {type(value).__name__}"
    if isinstance(kind, SmallInteger | Integer | BigInteger):
        if isinstance(value, bool) or not isinstance(value, int):
            return f"expected int, got {type(value).__name__}"
        low, high = _integer_range(kind)
        return None if low <= value <= high else f"{value} out of range [{low}, {high}]"
    if isinstance(kind, Float):
        if isinstance(value, bool) or not isinstance(value, int | float):
            return f"expected float, got {type(value).__name__}"
        return None if math.isfinite(value) else f"{value} is not a finite number"
    if isinstance(kind, TIMESTAMP):
        if not isinstance(value, datetime):
            return f"expected datetime, got {type(value).__name__}"
        return None if value.utcoffset() is not None else "naive datetime (no time zone)"
    if isinstance(kind, Date):
        if isinstance(value, datetime) or not isinstance(value, date):
            return f"expected date, got {type(value).__name__}"
        return None
    if isinstance(kind, CIDR):
        if "%" in str(value):
            return "invalid network: IPv6 zone ids are not allowed"
        try:
            ipaddress.ip_network(str(value), strict=True)
        except ValueError as exc:
            return f"invalid network: {exc}"
        return None
    if isinstance(kind, INET):
        if "%" in str(value):
            return "invalid IP address: IPv6 zone ids (%…) are not allowed"
        try:
            ipaddress.ip_interface(str(value))
        except ValueError as exc:
            return f"invalid IP address: {exc}"
        return None
    if isinstance(kind, MACADDR):
        if not isinstance(value, str) or not _MACADDR.match(value):
            return f"invalid MAC address: {value!r}"
        return None
    if isinstance(kind, Text):
        if not isinstance(value, str):
            return f"expected str, got {type(value).__name__}"
        return "NUL character in text" if "\x00" in value else None
    if isinstance(kind, Uuid):
        return (
            None if isinstance(value, uuid.UUID) else f"expected UUID, got {type(value).__name__}"
        )
    if isinstance(kind, LargeBinary):
        return None if isinstance(value, bytes) else f"expected bytes, got {type(value).__name__}"
    return f"unsupported column type {kind!r}"  # pragma: no cover - a new type needs a rule


def check_row(table: str, values: Mapping[str, Any], *, complete: bool = False) -> list[str]:
    """Problems with ``values`` for ``table``; empty list when the row can be loaded.

    ``complete=True`` also requires every NOT NULL column without a default.
    """
    columns = table_columns(table)
    if not columns:
        return [f"unknown table {table!r}"]
    problems: list[str] = []
    for name, value in values.items():
        column = columns.get(name)
        if column is None:
            problems.append(f"{table}.{name}: unknown column")
        elif value is None:
            if not column.nullable:
                problems.append(f"{table}.{name}: NULL not allowed")
        elif problem := check_value(column.type, value):
            problems.append(f"{table}.{name}: {problem}")
    if complete:
        problems.extend(
            f"{table}.{name}: missing required column"
            for name in sorted(required_columns(table) - values.keys())
        )
    return problems


def _json_has_nul(value: Any) -> bool:
    """A real NUL character in any string or key (PostgreSQL ``jsonb`` refuses ``\\u0000``).

    The text ``\\u0000`` (backslash, u, four zeros) is ordinary text and allowed; searching the
    encoded JSON for that substring would wrongly refuse it.
    """
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_json_has_nul(key) or _json_has_nul(item) for key, item in value.items())
    if isinstance(value, list | tuple):
        return any(_json_has_nul(item) for item in value)
    return False
