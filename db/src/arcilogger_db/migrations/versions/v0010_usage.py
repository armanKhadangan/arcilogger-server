"""Usage analytics rollups (area U, spec 10, ADR-0011).

Revision ID: 0010_usage
Revises: 0009_alert_delivery
Create Date: 2026-10-20

* ``persons.usage_excluded``: the administrator can leave a person out of every person-level
  usage view and report (owner decision 2026-10-20; company and department totals still count
  the traffic).
* Per site day and subject rollups, rebuilt by the worker (``arcilogger_worker.usage``):
  ``usage_days`` (bookkeeping and the fingerprint of what a day was computed from),
  ``usage_daily``, ``usage_device_daily``, ``usage_domain_daily``, ``usage_app_daily``,
  ``usage_hourly``, ``usage_blocked``, ``usage_urls``; ``domain_categories`` (FortiGuard category
  per registrable domain); ``usage_rebuilds`` (rebuild requests from the web interface).

Plain tables (no hypertables or partitions): they hold aggregates per subject and day, not log
lines — ADR-0011 has the sizing — and retention deletes whole days by the leading ``day`` key.
Downgrade drops the tables and the column (derived data: a rebuild recreates the rollups).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_usage"
down_revision: str | Sequence[str] | None = "0009_alert_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)


def _metric(name: str, *, big: bool = False) -> sa.Column[int]:
    kind = sa.BigInteger() if big else sa.Integer()
    return sa.Column(name, kind, server_default="0", nullable=False)


def _metrics() -> list[sa.Column[int]]:
    return [
        _metric("online_seconds"),
        _metric("active_seconds"),
        _metric("download_bytes", big=True),
        _metric("upload_bytes", big=True),
        _metric("sessions"),
    ]


def upgrade() -> None:
    op.add_column(
        "persons",
        sa.Column("usage_excluded", sa.Boolean(), server_default="false", nullable=False),
    )

    op.create_table(
        "usage_days",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("computed_at", TZ, nullable=False),
        sa.Column("computed_until", TZ, nullable=True),
        sa.Column("inputs_hash", sa.Text(), nullable=False),
        sa.Column("attribution_hash", sa.Text(), nullable=False),
        sa.Column(
            "ips",
            postgresql.ARRAY(postgresql.INET()),
            server_default=sa.text("'{}'::inet[]"),
            nullable=False,
        ),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('partial', 'final')", name=op.f("ck_usage_days_status")),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_usage_days")),
        comment="Usage rollups per site day: status, inputs fingerprint, progress.",
    )

    op.create_table(
        "usage_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("off_hours", sa.Boolean(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=True),
        sa.Column("mac", postgresql.MACADDR(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("vlan", sa.Integer(), nullable=True),
        *_metrics(),
        _metric("blocked"),
        sa.Column("first_activity", TZ, nullable=True),
        sa.Column("last_activity", TZ, nullable=True),
        sa.Column(
            "timeline",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('person', 'device', 'ip')", name=op.f("ck_usage_daily_kind")),
        sa.PrimaryKeyConstraint("day", "subject", "off_hours", name="pk_usage_daily"),
        comment="Usage per subject and day: online/active time, bytes, sessions, timeline.",
    )
    op.create_index("ix_usage_daily_subject_day", "usage_daily", ["subject", "day"])

    op.create_table(
        "usage_device_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("mac", postgresql.MACADDR(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("off_hours", sa.Boolean(), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("vlan", sa.Integer(), nullable=True),
        *_metrics(),
        _metric("blocked"),
        sa.Column("first_activity", TZ, nullable=True),
        sa.Column("last_activity", TZ, nullable=True),
        sa.Column(
            "top_domains",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("day", "mac", "subject", "off_hours", name="pk_usage_device_daily"),
        comment="Usage per device (MAC), subject and day (per-device breakdown).",
    )
    op.create_index("ix_usage_device_daily_subject_day", "usage_device_daily", ["subject", "day"])

    op.create_table(
        "usage_domain_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("off_hours", sa.Boolean(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("seconds", postgresql.DOUBLE_PRECISION(), server_default="0", nullable=False),
        _metric("raw_seconds"),
        _metric("download_bytes", big=True),
        _metric("upload_bytes", big=True),
        _metric("sessions"),
        sa.Column("background", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "hosts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('domain', 'service', 'ip')", name=op.f("ck_usage_domain_daily_kind")
        ),
        sa.PrimaryKeyConstraint(
            "day", "subject", "domain", "off_hours", name="pk_usage_domain_daily"
        ),
        comment="Usage per subject, domain and day: estimated time, raw time, bytes, hosts.",
    )
    op.create_index("ix_usage_domain_daily_domain_day", "usage_domain_daily", ["domain", "day"])
    op.create_index("ix_usage_domain_daily_subject_day", "usage_domain_daily", ["subject", "day"])

    op.create_table(
        "usage_app_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("app", sa.Text(), nullable=False),
        sa.Column("off_hours", sa.Boolean(), nullable=False),
        sa.Column("appcat", sa.Text(), nullable=True),
        sa.Column("seconds", postgresql.DOUBLE_PRECISION(), server_default="0", nullable=False),
        _metric("bytes", big=True),
        _metric("sessions"),
        sa.PrimaryKeyConstraint("day", "subject", "app", "off_hours", name="pk_usage_app_daily"),
        comment="Usage per subject, application and day.",
    )

    op.create_table(
        "usage_hourly",
        sa.Column("hour", TZ, nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("off_hours", sa.Boolean(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        _metric("online_seconds"),
        _metric("active_seconds"),
        _metric("download_bytes", big=True),
        _metric("upload_bytes", big=True),
        sa.Column("top_domain", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("hour", "subject", "off_hours", name="pk_usage_hourly"),
        comment="Usage per subject and hour (online/active time, bytes, top domain).",
    )
    op.create_index("ix_usage_hourly_day", "usage_hourly", ["day"])
    op.create_index("ix_usage_hourly_subject_hour", "usage_hourly", ["subject", "hour"])

    op.create_table(
        "usage_blocked",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("time", TZ, nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("off_hours", sa.Boolean(), nullable=False),
        sa.Column("mac", postgresql.MACADDR(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("app", sa.Text(), nullable=True),
        sa.CheckConstraint("source IN ('web', 'dns', 'app')", name=op.f("ck_usage_blocked_source")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_blocked")),
        comment="Blocked attempts per subject (web, DNS, application control).",
    )
    op.create_index("ix_usage_blocked_day_time", "usage_blocked", ["day", "time"])
    op.create_index("ix_usage_blocked_subject_day", "usage_blocked", ["subject", "day"])

    op.create_table(
        "usage_urls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("off_hours", sa.Boolean(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("first_seen", TZ, nullable=False),
        sa.Column("last_seen", TZ, nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("blocked", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_urls")),
        comment="URLs per subject and day from the web filter log (newest kept).",
    )
    op.create_index("ix_usage_urls_subject_day", "usage_urls", ["subject", "day"])
    op.create_index("ix_usage_urls_day", "usage_urls", ["day"])

    op.create_table(
        "domain_categories",
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("cat", sa.Integer(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("hits", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("last_day", sa.Date(), nullable=False),
        sa.Column("updated_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source IN ('web', 'dns')", name=op.f("ck_domain_categories_source")),
        sa.PrimaryKeyConstraint("domain", name=op.f("pk_domain_categories")),
        comment="Category per registrable domain (fg_web.catdesc, else fg_dns.catdesc).",
    )

    op.create_table(
        "usage_rebuilds",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("from_day", sa.Date(), nullable=False),
        sa.Column("to_day", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("total_days", sa.Integer(), nullable=False),
        sa.Column("done_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("created_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", TZ, nullable=True),
        sa.Column("finished_at", TZ, nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name=op.f("ck_usage_rebuilds_status"),
        ),
        sa.CheckConstraint("to_day >= from_day", name=op.f("ck_usage_rebuilds_range")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_rebuilds")),
        comment="Usage rebuild requests (POST /usage/rebuild), processed by the worker.",
    )
    op.create_index("ix_usage_rebuilds_status_id", "usage_rebuilds", ["status", "id"])


def downgrade() -> None:
    op.drop_index("ix_usage_rebuilds_status_id", table_name="usage_rebuilds")
    op.drop_table("usage_rebuilds")
    op.drop_table("domain_categories")
    op.drop_index("ix_usage_urls_day", table_name="usage_urls")
    op.drop_index("ix_usage_urls_subject_day", table_name="usage_urls")
    op.drop_table("usage_urls")
    op.drop_index("ix_usage_blocked_subject_day", table_name="usage_blocked")
    op.drop_index("ix_usage_blocked_day_time", table_name="usage_blocked")
    op.drop_table("usage_blocked")
    op.drop_index("ix_usage_hourly_subject_hour", table_name="usage_hourly")
    op.drop_index("ix_usage_hourly_day", table_name="usage_hourly")
    op.drop_table("usage_hourly")
    op.drop_table("usage_app_daily")
    op.drop_index("ix_usage_domain_daily_subject_day", table_name="usage_domain_daily")
    op.drop_index("ix_usage_domain_daily_domain_day", table_name="usage_domain_daily")
    op.drop_table("usage_domain_daily")
    op.drop_index("ix_usage_device_daily_subject_day", table_name="usage_device_daily")
    op.drop_table("usage_device_daily")
    op.drop_index("ix_usage_daily_subject_day", table_name="usage_daily")
    op.drop_table("usage_daily")
    op.drop_table("usage_days")
    op.drop_column("persons", "usage_excluded")
