"""The 30-day database window: ``db.retention_days`` 30, daily partitions, ``archive_reloads``.

Revision ID: 0021_db_window
Revises: 0020_fg_traffic_transip_time
Create Date: 2026-11-02

Wave 5 contract §5, owner decision "son 30 olsun" (G1, G2, G4; issue #110):

* ``db.retention_days`` becomes the database window, default 30 (was 400), at least 7. A stored
  400 (the untouched old default) becomes 30, a stored value below 7 becomes 7, every other
  value is kept. A row of the seed stays the seed's (it follows the shipped default from then
  on); an administrator's changed row is written with ``updated_by = ':migration'``, and the log
  names the old and the new value. No revision writes audit entries (the audit chain is
  application code). The new key ``db.reload_keep_days`` (7) is inserted by the seed, as every
  new key.
* ``archive_reloads``: the requests to bring sealed days older than the window back from the
  vault ("arşivden yeniden yükle", G4), loaded by ArciIngest one at a time (the partial unique
  index allows one ``loading`` row).
* Plain PostgreSQL: the time-series tables get **daily** partitions from the first day of the
  month after the migration (``arcilogger_db.partitions``, run by ``arcilogger-db upgrade`` right
  after this revision). The monthly partitions ``<table>_pYYYYMM`` created ahead by earlier
  versions for the months after the current one are dropped when they are empty; one that holds
  rows (a device clock in the future) is kept, never split, and the daily partitions start after
  it. Monthly partitions of the current and earlier months stay until the window job drops them
  (they end on or before its cutoff). Nothing changes on TimescaleDB (1-day chunks already).

The downgrade (developers and the migration tests; ``Install-Arcilogger.ps1 -Rollback`` runs
none): the daily partitions are merged back into monthly ones (rows and the default partition's
rows of those months moved, then attached), since the previous version knows monthly partitions
only; ``archive_reloads``, ``db.reload_keep_days`` and the window job's status row
``db.window_dropped_before`` are dropped; a ``db.retention_days`` of 30
written by the seed or by this revision goes back to 400 (lossy: a value raised to 7 stays 7,
which the previous version accepts).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_db_window"
down_revision: str | Sequence[str] | None = "0020_fg_traffic_transip_time"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

TZ = postgresql.TIMESTAMP(timezone=True)
TABLE = "archive_reloads"
KEY = "db.retention_days"
KEEP_KEY = "db.reload_keep_days"
#: The status row of the window job's applied cutoff (``arcilogger_db.partitions.DROPPED_KEY``).
DROPPED_KEY = "db.window_dropped_before"
SEED = ":seed"
MIGRATION = ":migration"
AUTHORS = [SEED, MIGRATION]
#: G1: the old shipped default, the new one and the new lower bound.
OLD_DEFAULT = 400
NEW_DEFAULT = 30
MINIMUM = 7
#: Frozen copies of ``models.ARCHIVE_RELOAD_REASONS`` and ``ARCHIVE_RELOAD_STATUSES``.
REASONS = ("lookup", "search")
STATUSES = ("queued", "loading", "ready", "failed", "expired")
#: Frozen copy of ``models.TIME_SERIES_TABLES`` and of the site time zone
#: (``arcilogger_core.timeutil``): migrations never import application code.
TIME_SERIES_TABLES = (
    "messages",
    "fg_traffic",
    "fg_web",
    "fg_dns",
    "fg_app",
    "fg_ips",
    "fg_virus",
    "fg_event",
    "mt_dhcp",
    "mt_arp",
    "mt_fw",
    "mt_account",
    "mt_interface",
    "mt_config",
    "mt_event",
    "fg_dhcp",
)
SITE_TZ = ZoneInfo("Europe/Istanbul")
MONTHLY = re.compile(r"^(?P<table>[a-z_]+)_p(?P<year>\d{4})(?P<month>\d{2})$")
DAILY = re.compile(r"^(?P<table>[a-z_]+)_p(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})$")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _next_month(day: date) -> date:
    return date(day.year + day.month // 12, day.month % 12 + 1, 1)


def _midnight(day: date) -> str:
    """Local midnight of a site day as a DDL literal (computed here, never input)."""
    return f"'{datetime.combine(day, time(0, 0), tzinfo=SITE_TZ).isoformat()}'"


def _partitions(bind: sa.Connection) -> list[tuple[str, str]]:
    """``(parent, partition)`` of every natively partitioned time-series table."""
    rows = bind.execute(
        sa.text(
            "SELECT p.relname, c.relname FROM pg_partitioned_table t "
            "JOIN pg_class p ON p.oid = t.partrelid "
            "JOIN pg_inherits i ON i.inhparent = p.oid JOIN pg_class c ON c.oid = i.inhrelid "
            "WHERE p.relnamespace = CAST(current_schema() AS regnamespace) "
            "AND p.relname = ANY(:tables) ORDER BY p.relname, c.relname"
        ),
        {"tables": list(TIME_SERIES_TABLES)},
    ).all()
    return [(parent, name) for parent, name in rows]


# -- upgrade --------------------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    _window_setting(bind)
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("requested_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("lookup_id", sa.BigInteger(), nullable=True),
        sa.Column("first_day", sa.Date(), nullable=False),
        sa.Column("last_day", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("bytes_total", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("bytes_done", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("ready_at", TZ, nullable=True),
        sa.Column("expires_at", TZ, nullable=True),
        sa.CheckConstraint(_in("reason", REASONS), name=op.f("ck_archive_reloads_reason")),
        sa.CheckConstraint(_in("status", STATUSES), name=op.f("ck_archive_reloads_status")),
        sa.CheckConstraint("last_day >= first_day", name=op.f("ck_archive_reloads_range")),
        sa.CheckConstraint(
            "btrim(requested_by) <> ''", name=op.f("ck_archive_reloads_requested_by")
        ),
        sa.CheckConstraint(
            "lookup_id IS NULL OR reason = 'lookup'", name=op.f("ck_archive_reloads_lookup")
        ),
        sa.CheckConstraint(
            "bytes_total >= 0 AND bytes_done >= 0", name=op.f("ck_archive_reloads_bytes")
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (error_code IS NOT NULL)",
            name=op.f("ck_archive_reloads_error_code"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('ready', 'expired') "
            "OR (ready_at IS NOT NULL AND expires_at IS NOT NULL)",
            name=op.f("ck_archive_reloads_ready"),
        ),
        sa.ForeignKeyConstraint(
            ["lookup_id"],
            ["lookups.id"],
            name=op.f("fk_archive_reloads_lookup_id_lookups"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_archive_reloads")),
        comment="Archive reloads: sealed days loaded back into the database on request.",
    )
    op.create_index("ix_archive_reloads_status_id", TABLE, ["status", "id"], unique=False)
    op.create_index(
        "uq_archive_reloads_loading",
        TABLE,
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'loading'"),
    )
    _drop_future_months(bind)


def _window_setting(bind: sa.Connection) -> None:
    """G1: 400 → 30, below 7 → 7, every other value kept (module docstring)."""
    row = bind.execute(
        sa.text("SELECT value, updated_by FROM settings WHERE key = :key"), {"key": KEY}
    ).one_or_none()
    if row is None:
        return  # a new database: the seed inserts the new default
    value, author = row
    if not isinstance(value, int) or isinstance(value, bool):
        return  # not a number: the readers fall back to the default, nothing to convert
    if value == OLD_DEFAULT:
        new = NEW_DEFAULT
    elif value < MINIMUM:
        new = MINIMUM
    else:
        return
    by = SEED if author == SEED else MIGRATION
    bind.execute(
        sa.text(
            "UPDATE settings SET value = CAST(:value AS jsonb), updated_by = :by, "
            "updated_at = now() WHERE key = :key"
        ),
        {"value": str(new), "by": by, "key": KEY},
    )
    log.info("%s: %s -> %s (was set by %s)", KEY, value, new, author)


def _drop_future_months(bind: sa.Connection) -> None:
    """The empty monthly partitions of the months after the current one (G2)."""
    today = datetime.now(UTC).astimezone(SITE_TZ).date()
    first = _next_month(today.replace(day=1))
    for parent, name in _partitions(bind):
        match = MONTHLY.match(name)
        if match is None or match.group("table") != parent:
            continue
        if date(int(match.group("year")), int(match.group("month")), 1) < first:
            continue
        # The lock first: no row can arrive between the check and the drop.
        bind.execute(sa.text(f'LOCK TABLE "{parent}" IN ACCESS EXCLUSIVE MODE'))
        exists = sa.text(f'SELECT EXISTS (SELECT 1 FROM "{name}")')  # noqa: S608
        if bind.execute(exists).scalar_one():
            log.info("%s: holds rows, kept as a monthly partition", name)
            continue
        bind.execute(sa.text(f'DROP TABLE "{name}"'))


# -- downgrade ------------------------------------------------------------------------------------


def downgrade() -> None:
    bind = op.get_bind()
    _merge_daily_partitions(bind)
    op.drop_index("uq_archive_reloads_loading", table_name=TABLE)
    op.drop_index("ix_archive_reloads_status_id", table_name=TABLE)
    op.drop_table(TABLE)
    bind.execute(
        sa.text("DELETE FROM settings WHERE key = ANY(:keys)"), {"keys": [KEEP_KEY, DROPPED_KEY]}
    )
    bind.execute(
        sa.text(
            "UPDATE settings SET value = CAST(:old AS jsonb), updated_at = now() "
            "WHERE key = :key AND value = CAST(:new AS jsonb) AND updated_by = ANY(:authors)"
        ),
        {"old": str(OLD_DEFAULT), "new": str(NEW_DEFAULT), "key": KEY, "authors": AUTHORS},
    )


def _merge_daily_partitions(bind: sa.Connection) -> None:
    """Every month's daily partitions become one monthly partition, as the previous version
    creates them (the default partition's rows of that month moved into it too)."""
    months: dict[tuple[str, date], list[str]] = {}
    partitions = _partitions(bind)
    defaults = {parent for parent, name in partitions if name == f"{parent}_default"}
    for parent, name in partitions:
        match = DAILY.match(name)
        if match is not None and match.group("table") == parent:
            month = date(int(match.group("year")), int(match.group("month")), 1)
            months.setdefault((parent, month), []).append(name)
    for (parent, month), names in sorted(months.items()):
        monthly = f"{parent}_p{month:%Y%m}"
        values = f"FROM ({_midnight(month)}) TO ({_midnight(_next_month(month))})"
        in_range = f"time >= {_midnight(month)} AND time < {_midnight(_next_month(month))}"
        bind.execute(sa.text(f'LOCK TABLE "{parent}" IN ACCESS EXCLUSIVE MODE'))
        bind.execute(
            sa.text(
                f'CREATE TABLE "{monthly}" (LIKE "{parent}" INCLUDING DEFAULTS '
                "INCLUDING CONSTRAINTS INCLUDING STORAGE)"
            )
        )
        for name in names:
            bind.execute(sa.text(f'INSERT INTO "{monthly}" SELECT * FROM "{name}"'))  # noqa: S608
            bind.execute(sa.text(f'DROP TABLE "{name}"'))
        if parent in defaults:
            default = f"{parent}_default"
            bind.execute(
                sa.text(
                    f'INSERT INTO "{monthly}" SELECT * FROM "{default}" '  # noqa: S608
                    f"WHERE {in_range}"
                )
            )
            bind.execute(sa.text(f'DELETE FROM "{default}" WHERE {in_range}'))  # noqa: S608
        bind.execute(
            sa.text(f'ALTER TABLE "{parent}" ATTACH PARTITION "{monthly}" FOR VALUES {values}')
        )
