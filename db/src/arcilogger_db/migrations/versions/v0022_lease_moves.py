"""The missed moves recorded with the lease: ``leases.moves``.

Revision ID: 0022_lease_moves
Revises: 0021_db_window
Create Date: 2026-11-04

Issue #127 (Wave 5 contract, deviations #127-1 and #127-2):

* ``leases.moves`` (``JSONB``, nullable, no default, no index): the kept FortiGate ACKs of the
  lease's MAC for another address that the lease job saw and did not apply, so the missed-move
  check (round 7, ADR-0016 D33) reaches before the database window again.
  ``{"truncated": bool, "groups": [...]}``, a group ``{"ip", "interface", "vd", "source",
  "logid", "dhcp_msg", "times"}``, the times as integer epoch microseconds (UTC), sorted, once
  each (``arcilogger_worker.lookup.refs``); the job keeps at most 1,000 times in the 100 days
  before the lease's end and sets ``truncated`` when it drops one. NULL: not recorded (every
  lease before this revision, an ARP interval); ``"groups": []``: recorded, none. On
  PostgreSQL 16 a nullable column without a default changes only the catalog: instant at any
  table size, and ``leases`` is a plain table on both storages (not a time-series table), so
  TimescaleDB changes nothing.
* No schema change for #127-2: the ``fg_dhcp`` refs the lease job writes into ``leases.evidence``
  from now on carry the extra key ``"vd"`` (the VDOM of the ref's row).

No backfill: recording a move needs D33 and the trust rules with the settings and the networks
of the build, which a revision cannot run (revisions never import application code), and only
the window's rows are in the database anyway. Every existing lease keeps ``moves`` NULL; the
check reads the ``fg_dhcp`` rows of the lookup's days for it as before (W5-G-17), and the lease
job records the moves it sees from its next run on.

The downgrade drops the column, nothing else (no ``DELETE``, no rewrite of ``evidence``): derived
data only, no 5651 data. Raw logs, log rows and every other lease column stay as they are. The
``"vd"`` keys stay in ``leases.evidence``; a ``0021_db_window`` program ignores them
(``EvidenceRef.from_dict`` reads its own keys only) and leaves them out of a lease it rewrites.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_lease_moves"
down_revision: str | Sequence[str] | None = "0021_db_window"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "leases", sa.Column("moves", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("leases", "moves")
