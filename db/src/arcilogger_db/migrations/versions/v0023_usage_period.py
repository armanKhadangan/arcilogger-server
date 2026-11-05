"""The part of the day of a usage row: ``period`` instead of ``off_hours``.

Revision ID: 0023_usage_period
Revises: 0022_lease_moves
Create Date: 2026-11-05

Wave 5 contract §6 (Addendum B), W5-K, decision K3: the work hours may have breaks ("mola"), so a
minute is in the work hours, in a break or off hours. In the seven usage rollup tables the boolean
``off_hours`` becomes ``period text NOT NULL`` with ``CHECK (period IN ('work', 'break', 'off'))``,
in the same place of the primary key where it is part of one (``usage_daily``,
``usage_device_daily``, ``usage_domain_daily``, ``usage_app_daily``, ``usage_hourly``).

Existing rows: ``true`` → ``'off'``, ``false`` → ``'work'``. Nothing was a break before this
revision, so every stored number stays what it was; a day is split by break only when the worker
rebuilds it (a changed ``usage.work_hours`` rebuilds the last ``usage.auto_rebuild_days`` days,
as far as their logs are still in the database window). The tables are derived data, rewritten
per day by the worker; no raw log and no 5651 data is touched.

The downgrade turns ``period`` back into ``off_hours`` (``'off'`` → ``true``, else ``false``).
A day with ``'break'`` rows would then hold two rows for one key, so such days are first deleted
from every rollup table and from ``usage_days``: the older program rebuilds them from the logs
the database still holds, like a day it never computed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op

revision: str = "0023_usage_period"
down_revision: str | Sequence[str] | None = "0022_lease_moves"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Table → its primary key's columns with ``off_hours`` (``None``: no key of its own columns).
TABLES: Final[dict[str, tuple[str, ...] | None]] = {
    "usage_daily": ("day", "subject", "off_hours"),
    "usage_device_daily": ("day", "mac", "subject", "off_hours"),
    "usage_domain_daily": ("day", "subject", "domain", "off_hours"),
    "usage_app_daily": ("day", "subject", "app", "off_hours"),
    "usage_hourly": ("hour", "subject", "off_hours"),
    "usage_blocked": None,
    "usage_urls": None,
}
PARTS: Final = "period IN ('work', 'break', 'off')"


def _swap(columns: tuple[str, ...], old: str, new: str) -> list[str]:
    return [new if column == old else column for column in columns]


def upgrade() -> None:
    for table, key in TABLES.items():
        op.add_column(table, sa.Column("period", sa.Text(), nullable=True))
        op.execute(
            f"UPDATE {table} SET period = CASE WHEN off_hours THEN 'off' ELSE 'work' END"  # noqa: S608 - fixed names
        )
        op.alter_column(table, "period", nullable=False)
        if key is not None:
            op.drop_constraint(f"pk_{table}", table, type_="primary")
            op.create_primary_key(f"pk_{table}", table, _swap(key, "off_hours", "period"))
        op.drop_column(table, "off_hours")
        op.create_check_constraint(op.f(f"ck_{table}_period"), table, PARTS)


def downgrade() -> None:
    days = "SELECT DISTINCT day FROM usage_daily WHERE period = 'break'"
    for table in TABLES:
        if table != "usage_daily":
            op.execute(f"DELETE FROM {table} WHERE day IN ({days})")  # noqa: S608 - fixed names
    op.execute(f"DELETE FROM usage_days WHERE day IN ({days})")  # noqa: S608 - fixed names
    op.execute(f"DELETE FROM usage_daily WHERE day IN ({days})")  # noqa: S608 - fixed names
    for table, key in TABLES.items():
        op.drop_constraint(op.f(f"ck_{table}_period"), table, type_="check")
        op.add_column(table, sa.Column("off_hours", sa.Boolean(), nullable=True))
        op.execute(f"UPDATE {table} SET off_hours = (period = 'off')")  # noqa: S608 - fixed names
        op.alter_column(table, "off_hours", nullable=False)
        if key is not None:
            op.drop_constraint(f"pk_{table}", table, type_="primary")
            op.create_primary_key(f"pk_{table}", table, list(key))
        op.drop_column(table, "period")
