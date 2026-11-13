"""The security rules R01-R19: rule state, alert evidence, assignment, webhook deliveries.

Revision ID: 0024_rules
Revises: 0023_usage_period
Create Date: 2026-11-12

Wave 6 contract §2.5 (W6-A):

* ``rule_state``: one row per security rule with its watermark (the receive time up to which it
  has been evaluated), the last run, the newest input row seen, the last error, the failures in a
  row, the skipped backlog and the number of alerts opened. Derived state of the worker job
  ``rules.run``; nothing is backfilled (a rule starts at the current horizon, contract D12).
* ``alert_evidence``: up to ``rules.evidence_max`` references per alert to the rows that caused
  it (table, row id, device and receive time, source and the raw reference of
  ``GET /api/v1/raw``), never their content; deleted with the alert.
* ``alerts.assignee_id`` (users, ``ON DELETE SET NULL``) with the index
  ``ix_alerts_assignee_state (assignee_id, state)``: a new nullable column without a default
  changes only the catalog.
* ``alert_events.kind`` adds ``assign`` (``from_state``/``to_state`` NULL, ``text`` the new
  assignee's user name or NULL for nobody); the ``shape`` check is extended.
* ``alert_deliveries.kind`` adds ``webhook`` (``recipient`` = the webhook's name, D10).

The downgrade drops exactly these. Rows that only this revision allows are deleted first, so the
older checks hold again: the ``assign`` events of ``alert_events`` and the ``webhook`` rows of
``alert_deliveries``. All of it is derived data or alert workflow (who an alert was given to,
which webhook announced it); raw logs, log rows, leases and the audit log (which keeps the
``alert.assign`` entries) keep every row. The alerts the rules raised stay, without their
evidence links and their assignee.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_rules"
down_revision: str | Sequence[str] | None = "0023_usage_period"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)
STATES: Final = "('open', 'acknowledged', 'resolved', 'dismissed')"
#: The parsed log tables and ``devices`` (``arcilogger_db.models.RULE_EVIDENCE_TABLES``).
EVIDENCE_TABLES: Final = (
    "('fg_traffic', 'fg_web', 'fg_dns', 'fg_app', 'fg_ips', 'fg_virus', 'fg_event', "
    "'mt_dhcp', 'mt_arp', 'mt_fw', 'mt_account', 'mt_interface', 'mt_config', 'mt_event', "
    "'fg_dhcp', 'devices')"
)
SHAPE_STATE: Final = (
    f"(kind = 'state' AND from_state IN {STATES} AND to_state IN {STATES} "
    "AND from_state <> to_state "
    "AND (text IS NULL OR char_length(text) BETWEEN 1 AND 200)) "
    "OR (kind = 'note' AND from_state IS NULL AND to_state IS NULL "
    "AND char_length(text) BETWEEN 1 AND 2000)"
)
SHAPE_ASSIGN: Final = (
    " OR (kind = 'assign' AND from_state IS NULL AND to_state IS NULL "
    "AND (text IS NULL OR char_length(text) BETWEEN 1 AND 64))"
)


def upgrade() -> None:
    op.create_table(
        "rule_state",
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("watermark", TZ, nullable=True),
        sa.Column("last_run_at", TZ, nullable=True),
        sa.Column("last_input_at", TZ, nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_until", TZ, nullable=True),
        sa.Column("alerts_opened", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", TZ, nullable=True),
        sa.CheckConstraint("failures >= 0", name=op.f("ck_rule_state_failures")),
        sa.CheckConstraint("alerts_opened >= 0", name=op.f("ck_rule_state_alerts_opened")),
        sa.PrimaryKeyConstraint("rule", name=op.f("pk_rule_state")),
        comment="Watermark, last run, last data and errors of each security rule.",
    )
    op.create_table(
        "alert_evidence",
        sa.Column("alert_id", sa.BigInteger(), nullable=False),
        sa.Column("n", sa.SmallInteger(), nullable=False),
        sa.Column("tbl", sa.Text(), nullable=False),
        sa.Column("row_id", sa.BigInteger(), nullable=True),
        sa.Column("time", TZ, nullable=True),
        sa.Column("time_received", TZ, nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("raw_day", sa.Date(), nullable=True),
        sa.Column("raw_line", sa.Integer(), nullable=True),
        sa.Column("raw_offset", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(f"tbl IN {EVIDENCE_TABLES}", name=op.f("ck_alert_evidence_tbl")),
        sa.CheckConstraint("n >= 1", name=op.f("ck_alert_evidence_n")),
        sa.CheckConstraint(
            "tbl = 'devices' OR (row_id IS NOT NULL AND time IS NOT NULL "
            "AND time_received IS NOT NULL AND source IS NOT NULL AND raw_day IS NOT NULL "
            "AND raw_line IS NOT NULL AND raw_offset IS NOT NULL)",
            name=op.f("ck_alert_evidence_raw_ref"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_alert_evidence_alert_id_alerts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("alert_id", "n", name=op.f("pk_alert_evidence")),
        comment="The log rows behind a security rule's alert (references, no content).",
    )
    op.add_column("alerts", sa.Column("assignee_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_alerts_assignee_id_users"),
        "alerts",
        "users",
        ["assignee_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_alerts_assignee_state", "alerts", ["assignee_id", "state"], unique=False)
    op.drop_constraint(op.f("ck_alert_events_kind"), "alert_events", type_="check")
    op.create_check_constraint(
        op.f("ck_alert_events_kind"), "alert_events", "kind IN ('state', 'note', 'assign')"
    )
    op.drop_constraint(op.f("ck_alert_events_shape"), "alert_events", type_="check")
    op.create_check_constraint(
        op.f("ck_alert_events_shape"), "alert_events", SHAPE_STATE + SHAPE_ASSIGN
    )
    op.drop_constraint(op.f("ck_alert_deliveries_kind"), "alert_deliveries", type_="check")
    op.create_check_constraint(
        op.f("ck_alert_deliveries_kind"),
        "alert_deliveries",
        "kind IN ('new', 'resolved', 'webhook')",
    )


def downgrade() -> None:
    op.execute("DELETE FROM alert_deliveries WHERE kind = 'webhook'")
    op.drop_constraint(op.f("ck_alert_deliveries_kind"), "alert_deliveries", type_="check")
    op.create_check_constraint(
        op.f("ck_alert_deliveries_kind"), "alert_deliveries", "kind IN ('new', 'resolved')"
    )
    op.execute("DELETE FROM alert_events WHERE kind = 'assign'")
    op.drop_constraint(op.f("ck_alert_events_shape"), "alert_events", type_="check")
    op.create_check_constraint(op.f("ck_alert_events_shape"), "alert_events", SHAPE_STATE)
    op.drop_constraint(op.f("ck_alert_events_kind"), "alert_events", type_="check")
    op.create_check_constraint(
        op.f("ck_alert_events_kind"), "alert_events", "kind IN ('state', 'note')"
    )
    op.drop_index("ix_alerts_assignee_state", table_name="alerts")
    op.drop_constraint(op.f("fk_alerts_assignee_id_users"), "alerts", type_="foreignkey")
    op.drop_column("alerts", "assignee_id")
    op.drop_table("alert_evidence")
    op.drop_table("rule_state")
