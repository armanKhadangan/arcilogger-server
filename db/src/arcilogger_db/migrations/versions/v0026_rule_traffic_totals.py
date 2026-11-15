"""R13's and R14's running traffic totals (#280).

Revision ID: 0026_rule_traffic_totals
Revises: 0025_admin_webhook_test
Create Date: 2026-11-15

R13 (a site day's bytes) and R14 (a night's) kept a period's totals by reading the whole period
on every 60-s run. The totals are now kept from one slice to the next (``rules.people``):

* ``rule_traffic_state``: per rule and period start, what the totals were counted with
  (``inputs``) and up to which receive time (``filled_until``, moved in the same transaction as
  ``rule_state.watermark``);
* ``rule_traffic_totals``: the period's logs and bytes per address, minute of device time and
  minute of receive time;
* ``rule_traffic_evidence``: an address's oldest counted logs, once there are a full list's
  worth (references only, as ``alert_evidence``).

All three hold derived data. The downgrade drops them; nothing else changes, and the next run
after an upgrade rebuilds the current period from ``fg_traffic`` with one read.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_rule_traffic_totals"
down_revision: str | Sequence[str] | None = "0025_admin_webhook_test"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "rule_traffic_state",
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("period_start", TZ, nullable=False),
        sa.Column("inputs", sa.Text(), nullable=False),
        sa.Column("filled_until", TZ, nullable=False),
        sa.PrimaryKeyConstraint("rule", "period_start", name=op.f("pk_rule_traffic_state")),
        comment="Running traffic totals of R13 and R14: their state.",
    )
    op.create_table(
        "rule_traffic_totals",
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("period_start", TZ, nullable=False),
        sa.Column("srcip", postgresql.INET(), nullable=False),
        sa.Column("minute", TZ, nullable=False),
        sa.Column("received", TZ, nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("first_received", TZ, nullable=True),
        sa.Column("last_received", TZ, nullable=True),
        sa.ForeignKeyConstraint(
            ["rule", "period_start"],
            ["rule_traffic_state.rule", "rule_traffic_state.period_start"],
            name=op.f("fk_rule_traffic_totals_rule_rule_traffic_state"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "rule",
            "period_start",
            "srcip",
            "minute",
            "received",
            name=op.f("pk_rule_traffic_totals"),
        ),
        comment="Running traffic totals of R13 and R14 per address and minute.",
    )
    op.create_table(
        "rule_traffic_evidence",
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("period_start", TZ, nullable=False),
        sa.Column("srcip", postgresql.INET(), nullable=False),
        sa.Column("refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["rule", "period_start"],
            ["rule_traffic_state.rule", "rule_traffic_state.period_start"],
            name=op.f("fk_rule_traffic_evidence_rule_rule_traffic_state"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "rule", "period_start", "srcip", name=op.f("pk_rule_traffic_evidence")
        ),
        comment="Running traffic totals of R13 and R14: kept evidence references.",
    )


def downgrade() -> None:
    op.drop_table("rule_traffic_evidence")
    op.drop_table("rule_traffic_totals")
    op.drop_table("rule_traffic_state")
