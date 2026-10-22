"""Monthly range partitions of the time-series tables on plain PostgreSQL (no TimescaleDB).

Without TimescaleDB the baseline creates every time-series table as ``PARTITION BY RANGE
(time)`` with a ``<table>_default`` partition (ADR-0005: at 1-5 million messages a day and 400
days in the database, unpartitioned heaps with DELETE-based retention would not be workable on
the 4-core Windows server). This module

* creates the monthly partitions ``<table>_pYYYYMM`` — boundaries at local midnight
  (Europe/Istanbul) of the 1st of each month — for the whole database retention window
  (setting ``db.retention_days``, :func:`months_back_for`; at least :data:`MONTHS_BACK`) up to
  :data:`MONTHS_AHEAD` months ahead (:func:`ensure_partitions`). The ingest service re-loads
  every sealed day of that same window from the vault (e.g. after a database loss), so such a
  day always finds its month's partition instead of filling the default partition — which
  every later ``CREATE TABLE … PARTITION OF`` would have to scan under an exclusive lock;
* moves rows that landed in the default partition before their month existed into the new
  partition (the default partition otherwise only holds outliers, e.g. a device clock in 2001);
* drops whole months for retention (:func:`drop_before`) — no DELETE of hundreds of millions of
  rows, no vacuum storm.

Callers: ``arcilogger-db upgrade`` and ``arcilogger-db partitions`` (both run
:func:`ensure_partitions`), and the worker once a day. With TimescaleDB (hypertables) every
function finds no partitioned table and does nothing.

SQL: partition DDL cannot take bind parameters, so statements are built as text. Identifiers are
generated here from :data:`TIME_SERIES_TABLES` and checked by ``_quoted``; bounds are computed
datetimes (``_literal``) — nothing comes from user input (hence the ``noqa: S608`` markers).

Locking: each table is handled under ``LOCK TABLE <parent> IN ACCESS EXCLUSIVE MODE`` (parent
before partition, the same order as an insert, so no deadlock with the ingest service); ingest
waits for the few milliseconds a new, empty partition takes. Only the rare catch-up of rows
from the default partition holds the lock longer. A transaction-level advisory lock serialises
concurrent maintenance runs; the caller provides the transaction (``engine.begin()``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Final

from alembic.runtime.environment import NameFilterParentNames, NameFilterType
from sqlalchemy import Connection, text

from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db.models import TIME_SERIES_TABLES
from arcilogger_db.settings import DEFAULT_SETTINGS

#: Months created ahead of the current one (the worker runs daily; this is the safety margin).
MONTHS_AHEAD: Final = 3
#: Fewest months created before the current one (a backlog ingested right after installation);
#: by default the window follows ``db.retention_days`` (:func:`months_back_for`).
MONTHS_BACK: Final = 1
#: pg_advisory_xact_lock key ("ARCIPART" as a signed 64-bit integer).
PARTITION_LOCK_KEY: Final = int.from_bytes(b"ARCIPART", "big") - 2**63

_MONTHLY = re.compile(r"^(?P<table>[a-z_]+)_p(?P<year>\d{4})(?P<month>\d{2})$")
_DEFAULT = re.compile(r"^(?P<table>[a-z_]+)_default$")


# -- names and bounds -----------------------------------------------------------------------------


def month_of(day: date) -> date:
    """First day of ``day``'s month."""
    return day.replace(day=1)


def add_months(month: date, count: int) -> date:
    index = month.year * 12 + month.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def month_bounds(month: date) -> tuple[datetime, datetime]:
    """``[start, end)`` of a month: local midnight of its 1st day and of the next month's."""
    return day_window(month_of(month))[0], day_window(add_months(month_of(month), 1))[0]


def months_back_for(today: date, retention_days: int) -> int:
    """Months from the oldest day the database keeps (``today - retention_days``) to ``today``'s
    month — 13 for 400 days — and never fewer than :data:`MONTHS_BACK`."""
    oldest = month_of(today - timedelta(days=max(0, retention_days)))
    current = month_of(today)
    return max(MONTHS_BACK, (current.year - oldest.year) * 12 + current.month - oldest.month)


def retention_days(connection: Connection) -> int:
    """Setting ``db.retention_days`` (the shipped default when unset or not a number)."""
    default = int(DEFAULT_SETTINGS["db.retention_days"])
    value = connection.execute(
        text("SELECT value FROM settings WHERE key = 'db.retention_days'")
    ).scalar_one_or_none()
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def partition_name(table: str, month: date) -> str:
    return f"{table}_p{month:%Y%m}"


def default_partition_name(table: str) -> str:
    return f"{table}_default"


def is_partition_table(name: str) -> bool:
    """Whether ``name`` is a partition created here (Alembic compares only the parents)."""
    match = _MONTHLY.match(name) or _DEFAULT.match(name)
    return match is not None and match.group("table") in TIME_SERIES_TABLES


def include_name(
    name: str | None, type_: NameFilterType, parent_names: NameFilterParentNames
) -> bool:
    """Alembic ``include_name`` hook: partitions are maintenance objects, not model tables."""
    return not (type_ == "table" and name is not None and is_partition_table(name))


def _literal(value: datetime) -> str:
    """Partition bounds are DDL: no bind parameters. Values are computed here, never input."""
    return f"'{value.isoformat()}'"


def _quoted(name: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):  # pragma: no cover - names are generated
        raise ValueError(f"unexpected identifier {name!r}")
    return f'"{name}"'


# -- inspection -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Partition:
    table: str
    name: str
    #: First day of the month; ``None`` for the default partition or a foreign partition.
    month: date | None
    is_default: bool


def partitioned_tables(connection: Connection) -> list[str]:
    """Time-series tables that are natively partitioned (empty with TimescaleDB)."""
    rows = connection.execute(
        text(
            "SELECT c.relname FROM pg_partitioned_table p "
            "JOIN pg_class c ON c.oid = p.partrelid "
            "WHERE c.relnamespace = CAST(current_schema() AS regnamespace) "
            "AND c.relname = ANY(:tables)"
        ),
        {"tables": list(TIME_SERIES_TABLES)},
    ).scalars()
    found = set(rows)
    return [table for table in TIME_SERIES_TABLES if table in found]


def list_partitions(connection: Connection, table: str) -> list[Partition]:
    """Partitions of ``table`` (monthly ones in month order, then the default partition)."""
    rows = connection.execute(
        text(
            "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid WHERE i.inhparent = CAST(:table AS regclass)"
        ),
        {"table": table},
    ).all()
    found: list[Partition] = []
    for name, bound in rows:
        match = _MONTHLY.match(name)
        month = None
        if match and match.group("table") == table:
            month = date(int(match.group("year")), int(match.group("month")), 1)
        found.append(Partition(table, name, month, bound == "DEFAULT"))
    return sorted(found, key=lambda p: (p.is_default, p.month or date.max, p.name))


# -- maintenance ----------------------------------------------------------------------------------


@dataclass
class PartitionReport:
    tables: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    #: Rows moved out of a default partition, by new partition.
    moved: dict[str, int] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    #: Rows older than the cutoff deleted from default partitions.
    purged: int = 0


def ensure_partitions(
    connection: Connection,
    *,
    today: date | None = None,
    months_ahead: int = MONTHS_AHEAD,
    months_back: int | None = None,
) -> PartitionReport:
    """Create the missing monthly partitions around ``today`` (site day); idempotent.

    ``months_back`` defaults to the database retention window (:func:`months_back_for`).
    """
    report = PartitionReport(tables=partitioned_tables(connection))
    if not report.tables:
        return report
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
    day = today or site_day(utc_now())
    current = month_of(day)
    if months_back is None:
        months_back = months_back_for(day, retention_days(connection))
    months = [add_months(current, n) for n in range(-months_back, months_ahead + 1)]
    for table in report.tables:
        existing = list_partitions(connection, table)
        have = {p.month for p in existing if p.month is not None}
        default = next((p.name for p in existing if p.is_default), None)
        for month in months:
            if month in have:
                continue
            name = partition_name(table, month)
            moved = _create_partition(connection, table, name, month, default)
            report.created.append(name)
            if moved:
                report.moved[name] = moved
    return report


def _create_partition(
    connection: Connection, table: str, name: str, month: date, default: str | None
) -> int:
    start, end = month_bounds(month)
    bounds = f"FROM ({_literal(start)}) TO ({_literal(end)})"
    in_range = f"time >= {_literal(start)} AND time < {_literal(end)}"
    connection.execute(text(f"LOCK TABLE {_quoted(table)} IN ACCESS EXCLUSIVE MODE"))
    stray = 0
    if default is not None:
        stray = int(
            connection.execute(
                text(f"SELECT count(*) FROM {_quoted(default)} WHERE {in_range}")  # noqa: S608
            ).scalar_one()
        )
    if stray == 0:
        connection.execute(
            text(f"CREATE TABLE {_quoted(name)} PARTITION OF {_quoted(table)} FOR VALUES {bounds}")
        )
        return 0
    # Rows of this month arrived before the partition existed: move them, then attach (PostgreSQL
    # refuses to create a partition whose range still has rows in the default partition).
    if default is None:  # pragma: no cover - stray rows imply a default partition
        raise RuntimeError(f"{table}: rows to move but no default partition")
    connection.execute(
        text(
            f"CREATE TABLE {_quoted(name)} (LIKE {_quoted(table)} INCLUDING DEFAULTS "
            "INCLUDING CONSTRAINTS INCLUDING STORAGE)"
        )
    )
    connection.execute(
        text(
            f"INSERT INTO {_quoted(name)} SELECT * FROM {_quoted(default)} "  # noqa: S608
            f"WHERE {in_range}"
        )
    )
    connection.execute(text(f"DELETE FROM {_quoted(default)} WHERE {in_range}"))  # noqa: S608
    connection.execute(
        text(f"ALTER TABLE {_quoted(table)} ATTACH PARTITION {_quoted(name)} FOR VALUES {bounds}")
    )
    return stray


def drop_before(connection: Connection, cutoff: date) -> PartitionReport:
    """Retention: drop every monthly partition that ends on or before ``cutoff`` (a site day)
    and delete older rows from the default partitions.

    The month containing ``cutoff`` is kept whole, so the database holds up to one month more
    than the retention setting; the retention job may delete the remainder of that month.
    """
    report = PartitionReport(tables=partitioned_tables(connection))
    if not report.tables:
        return report
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
    limit = day_window(cutoff)[0]
    for table in report.tables:
        for partition in list_partitions(connection, table):
            if partition.is_default:
                deleted = connection.execute(
                    text(
                        f"DELETE FROM {_quoted(partition.name)} "  # noqa: S608
                        f"WHERE time < {_literal(limit)}"
                    )
                )
                report.purged += int(deleted.rowcount or 0)
            elif partition.month is not None and month_bounds(partition.month)[1] <= limit:
                connection.execute(text(f"LOCK TABLE {_quoted(table)} IN ACCESS EXCLUSIVE MODE"))
                connection.execute(text(f"DROP TABLE {_quoted(partition.name)}"))
                report.dropped.append(partition.name)
    return report


@dataclass(frozen=True)
class PartitionStatus:
    tables: int
    #: Earliest last month covered by any table (``None`` without native partitions).
    covered_until: date | None
    #: Tables whose default partition holds rows (times outside every monthly partition).
    default_rows: dict[str, bool]


def partition_status(connection: Connection) -> PartitionStatus:
    """Summary for ``arcilogger-db check``."""
    tables = partitioned_tables(connection)
    last: list[date] = []
    default_rows: dict[str, bool] = {}
    for table in tables:
        partitions = list_partitions(connection, table)
        months = [p.month for p in partitions if p.month is not None]
        last.append(max(months) if months else date.min)
        for p in partitions:
            if p.is_default:
                default_rows[table] = bool(
                    connection.execute(
                        text(f"SELECT EXISTS (SELECT 1 FROM {_quoted(p.name)})")  # noqa: S608
                    ).scalar_one()
                )
    return PartitionStatus(
        tables=len(tables), covered_until=min(last) if last else None, default_rows=default_rows
    )
