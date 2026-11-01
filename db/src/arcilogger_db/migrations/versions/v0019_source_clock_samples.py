"""Source clocks by receive time: the per-minute clock samples of every source.

Revision ID: 0019_source_clock_samples
Revises: 0018_nat_egresses
Create Date: 2026-11-01

ADR-0016 D28, issue #73:

* ``source_clock_samples``: per source, received minute (UTC) and device clock offset
  (``seconds``: ``time - time_received`` in whole seconds, ``0`` under a minute), the number of
  ``messages`` rows. The ingest loader adds a batch's samples in the transaction that loads its
  rows. The ``source-clock`` alert and a lookup's clock check read the minutes a source's lines
  were *received*: a clock set hours back writes rows whose device times lie hours before their
  arrival, which a device-time sample of ``messages`` reaches only after the offset itself. The
  primary key ``(source, minute, seconds)`` is the upsert's key and the readers' range (one
  source, a range of minutes).

No partition and no hypertable is created: a source on time has one row a minute (about 1,440 a
day, half a million a year), the readers ask one source's minutes of one or two hours.

The table starts empty: lines loaded before this revision have no samples, so a lookup of such a
moment measures its sources by device time only, as before; the alert reads the minutes received
after the upgrade.

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


def downgrade() -> None:
    op.drop_table("source_clock_samples")
