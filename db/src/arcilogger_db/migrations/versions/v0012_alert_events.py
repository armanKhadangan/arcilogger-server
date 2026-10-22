"""Alert workflow: state changes and notes (group 2, area S; ADR-0010 amendment).

Revision ID: 0012_alert_events
Revises: 0011_reports
Create Date: 2026-10-20

``alert_events``: one row per change of an alert's state (``from_state`` → ``to_state``) and per
note (``text``, 1-2000 characters), with who (``actor``: the user name, or ``system`` for the
checks' automatic resolution and re-opening; ``actor_id``) and when (``at``). Nothing is
backfilled: alerts closed before this revision have no events (their ``resolved_at`` stays).

No index on ``messages`` is added: the dashboard's messages per minute reads the last hour
through the primary key ``(time, id)`` of every partition (docs/MVP-CONTRACTS.md section 14).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_alert_events"
down_revision: str | Sequence[str] | None = "0011_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)
STATES = "('open', 'acknowledged', 'resolved', 'dismissed')"


def upgrade() -> None:
    op.create_table(
        "alert_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("alert_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=True),
        sa.Column("to_state", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('state', 'note')", name=op.f("ck_alert_events_kind")),
        sa.CheckConstraint(
            f"(kind = 'state' AND from_state IN {STATES} AND to_state IN {STATES} "
            "AND from_state <> to_state "
            "AND (text IS NULL OR char_length(text) BETWEEN 1 AND 200)) "
            "OR (kind = 'note' AND from_state IS NULL AND to_state IS NULL "
            "AND char_length(text) BETWEEN 1 AND 2000)",
            name=op.f("ck_alert_events_shape"),
        ),
        sa.CheckConstraint("btrim(actor) <> ''", name=op.f("ck_alert_events_actor")),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.id"],
            name=op.f("fk_alert_events_alert_id_alerts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_alert_events_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_events")),
        comment="State changes and notes of alerts (who and when; system: automatic).",
    )
    op.create_index("ix_alert_events_alert_id_at", "alert_events", ["alert_id", "at"], unique=False)
    op.create_index("ix_alert_events_at", "alert_events", ["at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_alert_events_at", table_name="alert_events")
    op.drop_index("ix_alert_events_alert_id_at", table_name="alert_events")
    op.drop_table("alert_events")
