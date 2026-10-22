"""No shipped site data: lease times per DHCP server and departments belong to the site.

Revision ID: 0013_site_data
Revises: 0012_alert_events
Create Date: 2026-10-20

ARCILOGGER no longer ships a network: the seed writes no VLANs and no departments
(:mod:`arcilogger_db.seed`), and ``lease.server_seconds`` — earlier shipped with a lease time for
one example DHCP server — now defaults to ``{}``.

* A ``lease.server_seconds`` row the seed still owns (``updated_by = ':seed'``) would follow that
  new default at the next seeding and silently drop the shorter lease time the site runs with,
  which changes how its lease timeline closes intervals. This step hands such a row to
  ``:migration``, so the seed leaves it alone and the value stays as it is until an administrator
  changes it (``arcilogger-db settings set``). No value is read or written: a new database has no
  row yet (the seed inserts ``{}`` after the migrations), and a value an administrator set is not
  touched. VLANs and departments an earlier seed wrote stay as they are.
* The comment of ``departments`` no longer says they are seeded.

The downgrade gives the row back to the seed and restores the comment.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_site_data"
down_revision: str | Sequence[str] | None = "0012_alert_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = "lease.server_seconds"
SEED, MIGRATION = ":seed", ":migration"
DEPARTMENTS_OLD = "Departments (seeded from the user VLAN names)."
DEPARTMENTS_NEW = "Departments of the site (entered by its administrator; none are shipped)."


def _hand_over(old: str, new: str) -> None:
    op.execute(
        sa.text(
            "UPDATE settings SET updated_by = :new, updated_at = now() "
            "WHERE key = :key AND updated_by = :old"
        ).bindparams(key=KEY, old=old, new=new)
    )


def upgrade() -> None:
    _hand_over(SEED, MIGRATION)
    op.create_table_comment("departments", DEPARTMENTS_NEW, existing_comment=DEPARTMENTS_OLD)


def downgrade() -> None:
    op.create_table_comment("departments", DEPARTMENTS_OLD, existing_comment=DEPARTMENTS_NEW)
    _hand_over(MIGRATION, SEED)
