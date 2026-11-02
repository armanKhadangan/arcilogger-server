"""The index of the lookup's port-less probe steps: ``fg_traffic (transip, time)``.

Revision ID: 0020_fg_traffic_transip_time
Revises: 0019_source_clock_samples
Create Date: 2026-11-02

Issue #71 (follow-up of #40 item 13 and Q-C3-1):

* ``ix_fg_traffic_transip_time``: the overlap probe (``_Probe.seen_overlap``) and the D15
  "seen, but not alone" check (``_Lookup._carried``) ask their steps without a port for the
  first row of one address in a time range. On ``ix_fg_traffic_transip_transport_time`` such a
  step walked every index entry of the address in the partition (``time`` is its third column,
  PostgreSQL 16 has no skip scan): about 1.1 s per step for a busy address after its last row on
  day 28 of a monthly partition at 1 M rows a day, two such steps in one lookup (2.3 s). On this
  index the step reads the address's rows of the range only; the steps name the address as
  ``transip = ANY(inet[])`` so that ``transip`` stays in their sort and the planner cannot take
  the primary key instead (``lookup/engine.py`` ``_address_is``). Steps with a port keep the other
  index.

The index is created on the parent: on native partitions PostgreSQL builds it on every partition,
on TimescaleDB on every chunk (an upgrade runs with the services stopped; 18 s for a monthly
partition of 28 M rows on 4 vCPU at the default ``maintenance_work_mem``). Its size is about
31.5 MB per million rows, ``ix_fg_traffic_transip_transport_time``'s 40.6 MB (docs/INGEST.md).

The downgrade drops the index.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0020_fg_traffic_transip_time"
down_revision: str | Sequence[str] | None = "0019_source_clock_samples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_fg_traffic_transip_time", "fg_traffic", ["transip", "time"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_fg_traffic_transip_time", table_name="fg_traffic")
