"""Source clocks by receive time: the per-minute clock samples of every source.

Revision ID: 0019_source_clock_samples
Revises: 0018_nat_egresses
Create Date: 2026-11-01

ADR-0016 D28, issue #73:

* ``source_clock_samples``: per source, received minute (UTC) and device clock offset
  (``seconds``: ``time - time_received`` in whole seconds rounded away from zero, ``0`` under a
  minute), the number of
  ``messages`` rows. The ingest loader adds a batch's samples in the transaction that loads its
  rows. The ``source-clock`` alert and a lookup's clock check read the minutes a source's lines
  were *received*: a clock set hours back writes rows whose device times lie hours before their
  arrival, which a device-time sample of ``messages`` reaches only after the offset itself. The
  primary key ``(source, minute, seconds)`` is the upsert's key and the readers' range (one
  source, a range of minutes).

No partition and no hypertable is created: a source on time has one row a minute (about 1,440 a
day, half a million a year), the readers ask one source's minutes of one or two hours.

The upgrade counts the rows received in the last :data:`BACKFILL` (``arcilogger_db.clocksamples.
rebuild`` in SQL; the services are stopped during an upgrade), so the alert's first check after it
reads the last hour as before and does not resolve an open ``source-clock`` for want of samples.
Older lines have no samples: a lookup of such a moment measures its sources by device time only,
as before (``clocksamples.rebuild`` counts a range again from its rows, but no command calls it
yet: only the tests do). The backfill takes about 0.2 s on 26 hours of ``messages`` at
1 M lines a day (docs/DATA-MODEL.md).

The downgrade drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_source_clock_samples"
down_revision: str | Sequence[str] | None = "0018_nat_egresses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)
#: The received range counted by the upgrade (the alert reads one hour, a lookup an hour on each
#: side of its moment).
BACKFILL = "2 hours"
#: ``clocksamples.offset_of`` in SQL; ``1 day`` is ingest's ``DEFAULT_MAX_CLOCK_SKEW`` (a row
#: further off carries its receive time and is not counted).
OFFSET = (
    "CASE WHEN abs(extract(epoch FROM time - time_received)) < 60 THEN 0 "
    "ELSE sign(extract(epoch FROM time - time_received)) "
    "* ceil(abs(extract(epoch FROM time - time_received))) END"
)


def upgrade() -> None:
    op.create_table(
        "source_clock_samples",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("minute", TZ, nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column("n", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("n > 0", name=op.f("ck_source_clock_samples_n")),
        sa.PrimaryKeyConstraint(
            "source", "minute", "seconds", name=op.f("pk_source_clock_samples")
        ),
        comment="Device clock offsets per source and received minute (whole seconds).",
    )
    op.execute(
        "INSERT INTO source_clock_samples (source, minute, seconds, n) "  # noqa: S608 - constants
        f"SELECT source, date_trunc('minute', time_received AT TIME ZONE 'UTC') AT TIME ZONE "
        f"'UTC', {OFFSET}, count(*) FROM messages "
        f"WHERE time_received >= now() - interval '{BACKFILL}' "
        f"AND time >= now() - interval '{BACKFILL}' - interval '1 day' "
        "AND time <= now() + interval '1 day' AND time <> time_received "
        "GROUP BY 1, 2, 3"
    )


def downgrade() -> None:
    op.drop_table("source_clock_samples")
