"""The receive-time indexes of the security rules: ``(time_received)`` on ``fg_traffic``,
``fg_dns``, ``fg_web`` and ``fg_app``.

Revision ID: 0026_rules_time_received
Revises: 0025_admin_webhook_test
Create Date: 2026-11-15

Wave 6 contract §2.1.7 and deviation W6-A-7 (manager's answer on #278). A rule finds the
addresses with a row in its slice by receive time (D8) and counts its window the same way. Its
device-time bounds are the window plus twice ``rules.max_device_skew_seconds`` wide (3 h with the
defaults), so with only ``time`` indexed (the primary key) that step read every row in those
bounds. With 10 minutes of the guard's mix (10,000 messages per second) behind the slice, the
18 rules went from 30.7 s to 21.3 s a run: R10 1.17 to 0.45 s, R09 0.86 to 0.42 s, R02 0.30 to
0.06 s, R01 0.18 to 0.05 s. Ingest's throughput gate stayed within the spread of its runs.
The other parsed tables are small or read by other indexes; they get none.

The indexes are created on the parents: on native partitions PostgreSQL builds them on every
partition, on TimescaleDB on every chunk (an upgrade runs with the services stopped).

The downgrade drops them: lossless.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "0026_rules_time_received"
down_revision: str | Sequence[str] | None = "0025_admin_webhook_test"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The tables whose rows the rules read by receive time in bulk.
TABLES: Final = ("fg_traffic", "fg_dns", "fg_web", "fg_app")


def upgrade() -> None:
    for table in TABLES:
        op.create_index(f"ix_{table}_time_received", table, ["time_received"], unique=False)


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(f"ix_{table}_time_received", table_name=table)
