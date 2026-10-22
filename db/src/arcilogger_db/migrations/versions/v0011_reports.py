"""Scheduled usage reports (group 2, area R, ADR-0012).

Revision ID: 0011_reports
Revises: 0010_usage
Create Date: 2026-10-20

* ``report_schedules``: what a scheduled report contains and when it runs (kind, local time
  ``HH:MM``, weekday / day of month, recipients, formats, person-level, pseudonyms).
* ``report_runs``: every run (scheduled or "run now") with its period, status, the files in
  ``exports/usage-reports/<id>/`` (name, format, size, SHA-256) and one delivery entry per
  recipient; kept when its schedule is deleted (``schedule_id`` set to NULL).

The four schedules of the group 2 contract (section 4) are inserted **once**, here, and never
again by the seed — an administrator may delete them. They are disabled (no recipients yet) and
person-level with names (owner decision 2026-10-20): "Gün sonu raporu" 18:30, "Tam gün raporu"
07:00, "Haftalık rapor" Monday 07:15, "Aylık rapor" day 1 07:30.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_reports"
down_revision: str | Sequence[str] | None = "0010_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)
FORMATS = sa.text('\'{"pdf": true, "xlsx": true, "email_html": true}\'::jsonb')
#: (name, kind, time, weekday, monthday) of the shipped schedules.
DEFAULT_SCHEDULES: tuple[tuple[str, str, str, int | None, int | None], ...] = (
    ("Gün sonu raporu", "end_of_day", "18:30", None, None),
    ("Tam gün raporu", "full_day", "07:00", None, None),
    ("Haftalık rapor", "weekly", "07:15", 1, None),
    ("Aylık rapor", "monthly", "07:30", None, 1),
)


def upgrade() -> None:
    schedules = op.create_table(
        "report_schedules",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("time", sa.Text(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=True),
        sa.Column("monthday", sa.SmallInteger(), nullable=True),
        sa.Column(
            "recipients",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("formats", postgresql.JSONB(), server_default=FORMATS, nullable=False),
        sa.Column("person_level", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("pseudonymize", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("next_run_at", TZ, nullable=True),
        sa.Column("created_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.Text(), server_default=":seed", nullable=False),
        sa.CheckConstraint(
            "kind IN ('end_of_day', 'full_day', 'weekly', 'monthly')",
            name=op.f("ck_report_schedules_kind"),
        ),
        sa.CheckConstraint(
            "time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'", name=op.f("ck_report_schedules_time")
        ),
        sa.CheckConstraint("weekday BETWEEN 1 AND 7", name=op.f("ck_report_schedules_weekday")),
        sa.CheckConstraint("monthday BETWEEN 1 AND 28", name=op.f("ck_report_schedules_monthday")),
        sa.CheckConstraint(
            "((kind = 'weekly') = (weekday IS NOT NULL)) "
            "AND ((kind = 'monthly') = (monthday IS NOT NULL))",
            name=op.f("ck_report_schedules_timing"),
        ),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_report_schedules_name")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_schedules")),
        comment="Scheduled usage reports: kind, time, recipients, formats (spec 10.5).",
    )
    op.create_table(
        "report_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("schedule_id", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("period_from", TZ, nullable=False),
        sa.Column("period_to", TZ, nullable=False),
        sa.Column("person_level", sa.Boolean(), nullable=False),
        sa.Column("pseudonymize", sa.Boolean(), nullable=False),
        sa.Column("formats", postgresql.JSONB(), nullable=False),
        sa.Column(
            "recipients",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("send", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=True),
        sa.Column("due_at", TZ, nullable=True),
        sa.Column("catch_up", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("started_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("picked_at", TZ, nullable=True),
        sa.Column("render_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rendered_at", TZ, nullable=True),
        sa.Column("finished_at", TZ, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "files", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "deliveries",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "summary", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("expired_at", TZ, nullable=True),
        sa.CheckConstraint(
            "kind IN ('end_of_day', 'full_day', 'weekly', 'monthly', 'custom')",
            name=op.f("ck_report_runs_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'sent', 'partial', 'failed')",
            name=op.f("ck_report_runs_status"),
        ),
        sa.CheckConstraint(
            "triggered_by IN ('schedule', 'manual')", name=op.f("ck_report_runs_triggered_by")
        ),
        sa.CheckConstraint("period_to > period_from", name=op.f("ck_report_runs_period")),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["report_schedules.id"],
            name=op.f("fk_report_runs_schedule_id_report_schedules"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_runs")),
        comment="Runs of usage reports: period, status, files (exports/), deliveries.",
    )
    op.create_index("ix_report_runs_schedule_id_id", "report_runs", ["schedule_id", "id"])
    op.create_index("ix_report_runs_status", "report_runs", ["status"])
    op.bulk_insert(
        schedules,
        [
            {
                "name": name,
                "kind": kind,
                "enabled": False,
                "time": clock,
                "weekday": weekday,
                "monthday": monthday,
                "person_level": True,
                "pseudonymize": False,
                "updated_by": ":seed",
            }
            for name, kind, clock, weekday, monthday in DEFAULT_SCHEDULES
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_report_runs_status", table_name="report_runs")
    op.drop_index("ix_report_runs_schedule_id_id", table_name="report_runs")
    op.drop_table("report_runs")
    op.drop_table("report_schedules")
