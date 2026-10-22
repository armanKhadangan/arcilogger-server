"""Alert e-mail delivery (area E, ADR-0010).

Revision ID: 0009_alert_delivery
Revises: 0008_device_assignments
Create Date: 2026-10-19

* ``alerts.created_at``: when the row was written (``time`` is when it happened; an alert
  imported from ``state/alerts.jsonl`` keeps the time of its line). Existing rows get the
  earlier of ``time`` and ``updated_at``, so the e-mail never announces old alerts as new.
* ``alerts.resolved_at``: when the alert became ``resolved`` (existing resolved rows: their
  ``updated_at``).
* ``alert_deliveries``: one row per alert, kind (``new`` / ``resolved``) and recipient; the
  unique key makes a crash between sending and committing repeat an e-mail at most once.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_alert_delivery"
down_revision: str | Sequence[str] | None = "0008_device_assignments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.add_column(
        "alerts", sa.Column("created_at", TZ, server_default=sa.text("now()"), nullable=False)
    )
    op.add_column("alerts", sa.Column("resolved_at", TZ, nullable=True))
    op.execute("UPDATE alerts SET created_at = LEAST(time, updated_at)")
    op.execute("UPDATE alerts SET resolved_at = updated_at WHERE state = 'resolved'")

    op.create_table(
        "alert_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("alert_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("recipient", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", TZ, nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("created_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", TZ, nullable=True),
        sa.CheckConstraint("kind IN ('new', 'resolved')", name=op.f("ck_alert_deliveries_kind")),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'failed', 'suppressed')",
            name=op.f("ck_alert_deliveries_status"),
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_alert_deliveries_alert_id_alerts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_deliveries")),
        sa.UniqueConstraint(
            "alert_id",
            "kind",
            "recipient",
            name=op.f("uq_alert_deliveries_alert_id_kind_recipient"),
        ),
        comment="E-mail deliveries of alerts per recipient (queued, sent, failed).",
    )
    op.create_index(
        "ix_alert_deliveries_status_recipient",
        "alert_deliveries",
        ["status", "recipient"],
        unique=False,
    )
    op.create_index(
        "ix_alert_deliveries_recipient_sent_at",
        "alert_deliveries",
        ["recipient", "sent_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_alert_deliveries_recipient_sent_at", table_name="alert_deliveries")
    op.drop_index("ix_alert_deliveries_status_recipient", table_name="alert_deliveries")
    op.drop_table("alert_deliveries")
    op.drop_column("alerts", "resolved_at")
    op.drop_column("alerts", "created_at")
