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

The column changes type and name in place (``ALTER COLUMN ... TYPE ... USING``, then ``RENAME``):
PostgreSQL rewrites each table once, with its indexes, and leaves no dead rows behind; the primary
keys keep their names and column order.

The downgrade turns ``period`` back into ``off_hours`` (``'off'`` → ``true``, else ``false``).
A day with ``'break'`` rows would then hold two rows for one key, so such days are first deleted
from every rollup table and from ``usage_days``. The older program rebuilds by itself only the
last ``usage.auto_rebuild_days`` days (default 7) of them; an older day stays empty until a
rebuild is requested for it ("Yeniden hesapla"), and a day outside the database window needs its
logs reloaded from the archive first. `docs/KULLANIM.md` §2.5 says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "0023_usage_period"
down_revision: str | Sequence[str] | None = "0022_lease_moves"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The seven rollup tables; ``off_hours`` is part of the primary key of the first five.
TABLES: Final = (
    "usage_daily",
    "usage_device_daily",
    "usage_domain_daily",
    "usage_app_daily",
    "usage_hourly",
    "usage_blocked",
    "usage_urls",
)
PARTS: Final = "period IN ('work', 'break', 'off')"


def upgrade() -> None:
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN off_hours TYPE text "
            "USING CASE WHEN off_hours THEN 'off' ELSE 'work' END"
        )
        op.execute(f"ALTER TABLE {table} RENAME COLUMN off_hours TO period")
        op.create_check_constraint(op.f(f"ck_{table}_period"), table, PARTS)


def downgrade() -> None:
    days = "SELECT DISTINCT day FROM usage_daily WHERE period = 'break'"
    for table in TABLES:
        if table != "usage_daily":
            op.execute(f"DELETE FROM {table} WHERE day IN ({days})")  # noqa: S608 - fixed names
    op.execute(f"DELETE FROM usage_days WHERE day IN ({days})")  # noqa: S608 - fixed names
    op.execute(f"DELETE FROM usage_daily WHERE day IN ({days})")  # noqa: S608 - fixed names
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_period"), table, type_="check")
        op.execute(f"ALTER TABLE {table} RENAME COLUMN period TO off_hours")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN off_hours TYPE boolean USING off_hours = 'off'"
        )
