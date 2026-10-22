"""Official PDF reports of 5651 lookups (``lookup_reports``, spec 9.5).

Revision ID: 0006_lookup_reports
Revises: 0001_baseline
Create Date: 2026-10-13

One row per lookup: the stored PDF (path under ``exports/``, SHA-256, size, pages), the inputs it
was rendered from, and the optional time stamp of its hash (setting ``lookup.report_timestamp``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_lookup_reports"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lookup_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("lookup_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("inputs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("timestamp_status", sa.Text(), server_default="not_requested", nullable=False),
        sa.Column("timestamp", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("download_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_lookup_reports_sha256")),
        sa.CheckConstraint(
            "timestamp_status IN ('not_requested', 'stamped', 'failed')",
            name=op.f("ck_lookup_reports_timestamp_status"),
        ),
        sa.ForeignKeyConstraint(
            ["lookup_id"],
            ["lookups.id"],
            name=op.f("fk_lookup_reports_lookup_id_lookups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_lookup_reports_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lookup_reports")),
        sa.UniqueConstraint("lookup_id", name=op.f("uq_lookup_reports_lookup_id")),
        comment="Official PDF reports of 5651 lookups (file in exports/reports/, SHA-256).",
    )


def downgrade() -> None:
    op.drop_table("lookup_reports")
