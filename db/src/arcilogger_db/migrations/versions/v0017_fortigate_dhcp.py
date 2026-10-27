"""FortiGate DHCP: the table ``fg_dhcp``, ``fg_traffic.srcmac``, ``leases.lease_seconds``.

Revision ID: 0017_fortigate_dhcp
Revises: 0016_search
Create Date: 2026-10-27

ADR-0016 (FortiGate DHCP as lease evidence; Wave 3 contract 2.2.1):

* ``fg_dhcp``: the FortiGate DHCP server's ACK/RELEASE lines (``type="event"`` with ``ip``,
  ``mac`` and ``dhcp_msg``), one typed row per line like every other time-series table; the
  worker decides what a row means from its ``logid``. ``device_time`` says whether ``time`` is
  the FortiGate's event time or the receive time. Indexes ``(ip, time)`` and ``(mac, time)``.
  The storage is the one the baseline chose: when ``messages`` is a hypertable, ``fg_dhcp``
  becomes one too (the baseline's 1-day chunks; compression ``segmentby source``, ``orderby
  time DESC, id DESC`` with the 7-day policy where the baseline could compress, inside a
  savepoint like the baseline's); otherwise it is ``PARTITION BY RANGE (time)`` with
  ``fg_dhcp_default``, and its monthly partitions come from ``arcilogger_db.partitions``
  (``TIME_SERIES_MODELS``, run by ``arcilogger-db upgrade`` right after the migration).
  ``fg_dhcp`` is appended to the ``db.timescale`` status (``hypertables`` or ``partitioned``),
  the only settings row this revision writes.
* ``fg_traffic.srcmac`` (``MACADDR``, nullable, no default, no index): the session record's
  source MAC (D10). On TimescaleDB the column is added with compressed chunks in place: shown on
  TimescaleDB 2.30.2 with PostgreSQL 16 (the CI image's version; 2026-10-27,
  ``db/tests/test_migration_0017.py``): the statement is accepted, the compressed rows read
  ``srcmac`` as NULL and new rows carry it (V13). The Linux TimescaleDB CI job runs the same
  test.
* ``leases.lease_seconds`` (the lease time a FortiGate ACK logged, 300-8,640,000 s, D6) with
  ``ck_leases_lease_seconds``; ``ck_leases_source_table`` (``mt_dhcp``, ``mt_arp``,
  ``fg_dhcp``); ``ix_leases_source_table_end`` (open and recently ended leases per table).

FortiGate DHCP lines loaded before this revision stay ``fg_event`` rows; they are not converted
(D8), and every existing row keeps its values (``srcmac`` and ``lease_seconds`` are NULL).

The downgrade deletes the ``fg_dhcp`` leases first (derived rows the previous code cannot read),
then drops everything above and removes ``fg_dhcp`` from the ``db.timescale`` status. The rows of
``fg_dhcp`` are lost; their raw lines stay in the spool and the vault.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

revision: str = "0017_fortigate_dhcp"
down_revision: str | Sequence[str] | None = "0016_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)
TABLE = "fg_dhcp"
#: Frozen copies of the baseline's chunk interval and compression age (``v0001_baseline``).
CHUNK_INTERVAL = "1 day"
COMPRESS_AFTER = "7 days"
#: Frozen copies of ``models.LEASE_SOURCE_TABLES`` and ``models.LEASE_SECONDS_RANGE``.
SOURCE_TABLES = ("mt_dhcp", "mt_arp", "fg_dhcp")
LEASE_SECONDS = "lease_seconds IS NULL OR lease_seconds BETWEEN 300 AND 8640000"


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    bind = op.get_bind()
    hypertables = _messages_is_hypertable(bind)
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", TZ, nullable=False),
        sa.Column("time_received", TZ, nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("logid", sa.Text(), nullable=True),
        sa.Column("subtype", sa.Text(), nullable=True),
        sa.Column("level", sa.Text(), nullable=True),
        sa.Column("logdesc", sa.Text(), nullable=True),
        sa.Column("dhcp_msg", sa.Text(), nullable=True),
        sa.Column("interface", sa.Text(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=False),
        sa.Column("mac", postgresql.MACADDR(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("lease", sa.BigInteger(), nullable=True),
        sa.Column("msg", sa.Text(), nullable=True),
        sa.Column("device_time", sa.Boolean(), nullable=False),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_fg_dhcp"),
        comment="FortiGate DHCP server events (ACK/RELEASE by logid): which MAC got which IP when.",
        **({} if hypertables else {"postgresql_partition_by": "RANGE (time)"}),
    )
    op.create_index("ix_fg_dhcp_ip_time", TABLE, ["ip", "time"], unique=False)
    op.create_index("ix_fg_dhcp_mac_time", TABLE, ["mac", "time"], unique=False)
    status = _status(bind)
    if hypertables:
        _hypertable(bind, status)
    else:
        bind.execute(sa.text(f"CREATE TABLE {TABLE}_default PARTITION OF {TABLE} DEFAULT"))
        _list(status, "partitioned")
    _record(bind, status)

    op.add_column("fg_traffic", sa.Column("srcmac", postgresql.MACADDR(), nullable=True))
    op.add_column("leases", sa.Column("lease_seconds", sa.Integer(), nullable=True))
    op.create_check_constraint(op.f("ck_leases_lease_seconds"), "leases", LEASE_SECONDS)
    op.create_check_constraint(
        op.f("ck_leases_source_table"), "leases", _in("source_table", SOURCE_TABLES)
    )
    op.create_index(
        "ix_leases_source_table_end", "leases", ["source_table", "end"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("DELETE FROM leases WHERE source_table = 'fg_dhcp'")
    op.drop_index("ix_leases_source_table_end", table_name="leases")
    op.drop_constraint(op.f("ck_leases_source_table"), "leases", type_="check")
    op.drop_constraint(op.f("ck_leases_lease_seconds"), "leases", type_="check")
    op.drop_column("leases", "lease_seconds")
    op.drop_column("fg_traffic", "srcmac")
    op.drop_index("ix_fg_dhcp_mac_time", table_name=TABLE)
    op.drop_index("ix_fg_dhcp_ip_time", table_name=TABLE)
    op.drop_table(TABLE)  # its partitions, or its chunks and compression policy, with it
    status = _status(bind)
    for key in ("hypertables", "partitioned"):
        if TABLE in status.get(key, ()):
            status[key] = [table for table in status[key] if table != TABLE]
    _record(bind, status)


# -- storage --------------------------------------------------------------------------------------


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("-")[0].split(".") if part.isdigit())


def _short(exc: DBAPIError) -> str:
    return str(exc.orig).strip().splitlines()[0][:300] if exc.orig else str(exc)[:300]


def _messages_is_hypertable(bind: sa.Connection) -> bool:
    """The baseline's choice: TimescaleDB when ``messages`` is a hypertable."""
    extension = bind.execute(
        sa.text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar_one_or_none()
    if extension is None:
        return False
    found = bind.execute(
        sa.text(
            "SELECT 1 FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema = current_schema() AND hypertable_name = 'messages'"
        )
    ).scalar_one_or_none()
    return found is not None


def _hypertable(bind: sa.Connection, status: dict[str, Any]) -> None:
    """``fg_dhcp`` as the baseline's ``_hypertables`` makes a time-series table."""
    version = bind.execute(
        sa.text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar_one()
    if _version(str(version)) >= (2, 13):
        statement = (
            "SELECT create_hypertable(CAST(:table AS regclass), "
            f"by_range('time', INTERVAL '{CHUNK_INTERVAL}'), create_default_indexes => FALSE)"
        )
    else:
        statement = (
            "SELECT create_hypertable(CAST(:table AS regclass), 'time', "
            f"chunk_time_interval => INTERVAL '{CHUNK_INTERVAL}', "
            "create_default_indexes => FALSE)"
        )
    bind.execute(sa.text(statement), {"table": TABLE})
    _list(status, "hypertables")
    if not status.get("compression"):
        return  # the baseline compressed nothing (an edition without compression)
    try:
        with bind.begin_nested():
            bind.execute(
                sa.text(
                    f"ALTER TABLE {TABLE} SET (timescaledb.compress, "
                    "timescaledb.compress_segmentby = 'source', "
                    "timescaledb.compress_orderby = 'time DESC, id DESC')"
                )
            )
            bind.execute(
                sa.text(
                    "SELECT add_compression_policy(CAST(:table AS regclass), "
                    f"INTERVAL '{COMPRESS_AFTER}')"
                ),
                {"table": TABLE},
            )
    except DBAPIError as exc:
        if context.config.attributes.get("timescale", "auto") == "require":
            raise
        status["compression_reason"] = f"{TABLE}: {_short(exc)}"


def _status(bind: sa.Connection) -> dict[str, Any]:
    """The ``db.timescale`` status the baseline recorded (empty when there is none)."""
    value = bind.execute(
        sa.text("SELECT value FROM settings WHERE key = 'db.timescale'")
    ).scalar_one_or_none()
    return dict(value) if isinstance(value, dict) else {}


def _list(status: dict[str, Any], key: str) -> None:
    tables = [table for table in status.get(key, []) if table != TABLE]
    status[key] = [*tables, TABLE]


def _record(bind: sa.Connection, status: dict[str, Any]) -> None:
    """Update the ``db.timescale`` status row (none is created: the baseline writes it)."""
    if not status:
        return
    bind.execute(
        sa.text(
            "UPDATE settings SET value = CAST(:value AS jsonb), updated_by = :author, "
            "updated_at = now() WHERE key = 'db.timescale'"
        ),
        {"value": json.dumps(status), "author": ":migration"},
    )
