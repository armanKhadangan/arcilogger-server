"""Baseline: every MVP table, the audit-log guard, TimescaleDB hypertables or native partitions.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-12

This revision is frozen: later schema changes go into new revisions. It must work on plain
PostgreSQL 16 (Windows server, GitHub Windows runner) and on PostgreSQL 16 + TimescaleDB.

TimescaleDB (``[database] timescale`` → ``config.attributes["timescale"]``):

* ``auto`` (default): when ``timescaledb`` is in ``pg_available_extensions``, create the extension
  and turn the time-series tables into hypertables (1-day chunks), compressed after 7 days where
  the installed edition supports it. If creating the extension fails (for example the library is
  not in ``shared_preload_libraries``), the tables stay plain and the reason is recorded.
* ``require``: as ``auto`` but any failure aborts the migration.
* ``off``: no TimescaleDB.

Without TimescaleDB the time-series tables are **natively partitioned**: ``PARTITION BY RANGE
(time)`` with a ``<table>_default`` partition; the monthly partitions are created by
``arcilogger_db.partitions.ensure_partitions`` (run by ``arcilogger-db upgrade`` right after this
migration, and daily by the worker). Retention then drops whole months instead of deleting rows.

The outcome is stored in the setting ``db.timescale`` (``storage``: ``hypertables`` or
``partitions``; ``arcilogger-db check`` shows it).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Frozen copy (the models may change later; this revision must not).
TIME_SERIES_TABLES = (
    "messages",
    "fg_traffic",
    "fg_web",
    "fg_dns",
    "fg_app",
    "fg_ips",
    "fg_virus",
    "fg_event",
    "mt_dhcp",
    "mt_arp",
    "mt_fw",
    "mt_account",
    "mt_interface",
    "mt_config",
    "mt_event",
)
CHUNK_INTERVAL = "1 day"
COMPRESS_AFTER = "7 days"

AUDIT_GUARD_FUNCTION = """
CREATE FUNCTION arci_audit_log_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% rejected)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""
AUDIT_GUARD_TRIGGERS = (
    "CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
    "FOR EACH ROW EXECUTE FUNCTION arci_audit_log_immutable()",
    "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
    "FOR EACH STATEMENT EXECUTE FUNCTION arci_audit_log_immutable()",
)


def upgrade() -> None:
    bind = op.get_bind()
    _require_utf8(bind)
    # Decided before the time-series tables exist: a hypertable is created from a plain table,
    # the fallback needs PARTITION BY at creation time.
    storage = _timescale_extension(bind)
    storage_options: dict[str, Any] = (
        {} if storage["enabled"] else {"postgresql_partition_by": "RANGE (time)"}
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "time",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="open", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'low', 'medium', 'high', 'critical')",
            name=op.f("ck_alerts_severity"),
        ),
        sa.CheckConstraint(
            "state IN ('open', 'acknowledged', 'resolved', 'dismissed')",
            name=op.f("ck_alerts_state"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
        comment="Alerts raised by ingest, correlation and (later) the rule engine.",
    )
    op.create_index("ix_alerts_state_time", "alerts", ["state", "time"], unique=False)
    op.create_index("ix_alerts_time", "alerts", ["time"], unique=False)
    op.create_index(
        "uq_alerts_open_dedup_key",
        "alerts",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("state = 'open' AND dedup_key IS NOT NULL"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_username", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("prev_hash", sa.Text(), nullable=False),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.CheckConstraint("hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_audit_log_hash")),
        sa.CheckConstraint("prev_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_audit_log_prev_hash")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
        sa.UniqueConstraint("hash", name=op.f("uq_audit_log_hash")),
        sa.UniqueConstraint("prev_hash", name=op.f("uq_audit_log_prev_hash")),
        comment="Hash-chained audit log; exported daily into the sealed vault.",
    )
    op.create_index("ix_audit_log_actor_id_time", "audit_log", ["actor_id", "time"], unique=False)
    op.create_index("ix_audit_log_time", "audit_log", ["time"], unique=False)
    op.create_table(
        "departments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
        sa.UniqueConstraint("name", name=op.f("uq_departments_name")),
        comment="Departments (seeded from the user VLAN names).",
    )
    op.create_table(
        "fg_app",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("logid", sa.Text(), nullable=True),
        sa.Column("sessionid", sa.BigInteger(), nullable=True),
        sa.Column("proto", sa.SmallInteger(), nullable=True),
        sa.Column("srcip", postgresql.INET(), nullable=True),
        sa.Column("srcport", sa.Integer(), nullable=True),
        sa.Column("dstip", postgresql.INET(), nullable=True),
        sa.Column("dstport", sa.Integer(), nullable=True),
        sa.Column("app", sa.Text(), nullable=True),
        sa.Column("appid", sa.BigInteger(), nullable=True),
        sa.Column("appcat", sa.Text(), nullable=True),
        sa.Column("apprisk", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_fg_app"),
        comment="FortiGate application control logs.",
        **storage_options,
    )
    op.create_index("ix_fg_app_sessionid", "fg_app", ["sessionid"], unique=False)
    op.create_index("ix_fg_app_srcip_time", "fg_app", ["srcip", "time"], unique=False)
    op.create_table(
        "fg_dns",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("logid", sa.Text(), nullable=True),
        sa.Column("sessionid", sa.BigInteger(), nullable=True),
        sa.Column("srcip", postgresql.INET(), nullable=True),
        sa.Column("srcport", sa.Integer(), nullable=True),
        sa.Column("dstip", postgresql.INET(), nullable=True),
        sa.Column("dstport", sa.Integer(), nullable=True),
        sa.Column("qname", sa.Text(), nullable=True),
        sa.Column("qtype", sa.Text(), nullable=True),
        sa.Column("answers", postgresql.ARRAY(postgresql.INET()), nullable=True),
        sa.Column("cat", sa.Integer(), nullable=True),
        sa.Column("catdesc", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("eventtype", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_fg_dns"),
        comment="FortiGate DNS logs (queried names and answers).",
        **storage_options,
    )
    op.create_index("ix_fg_dns_qname_time", "fg_dns", ["qname", "time"], unique=False)
    op.create_index("ix_fg_dns_srcip_time", "fg_dns", ["srcip", "time"], unique=False)
    op.create_table(
        "fg_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("logid", sa.Text(), nullable=True),
        sa.Column("subtype", sa.Text(), nullable=True),
        sa.Column("level", sa.Text(), nullable=True),
        sa.Column("logdesc", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("user_name", sa.Text(), nullable=True),
        sa.Column("ui", sa.Text(), nullable=True),
        sa.Column("srcip", postgresql.INET(), nullable=True),
        sa.Column("dstip", postgresql.INET(), nullable=True),
        sa.Column("cfgpath", sa.Text(), nullable=True),
        sa.Column("cfgobj", sa.Text(), nullable=True),
        sa.Column("cfgattr", sa.Text(), nullable=True),
        sa.Column("msg", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_fg_event"),
        comment="FortiGate event logs (admin logins, config changes, performance, VPN, user).",
        **storage_options,
    )
    op.create_index("ix_fg_event_subtype_time", "fg_event", ["subtype", "time"], unique=False)
    op.create_table(
        "fg_ips",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("logid", sa.Text(), nullable=True),
        sa.Column("eventtype", sa.Text(), nullable=True),
        sa.Column("sessionid", sa.BigInteger(), nullable=True),
        sa.Column("proto", sa.SmallInteger(), nullable=True),
        sa.Column("srcip", postgresql.INET(), nullable=True),
        sa.Column("srcport", sa.Integer(), nullable=True),
        sa.Column("dstip", postgresql.INET(), nullable=True),
        sa.Column("dstport", sa.Integer(), nullable=True),
        sa.Column("service", sa.Text(), nullable=True),
        sa.Column("attack", sa.Text(), nullable=True),
        sa.Column("attackid", sa.BigInteger(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("msg", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_fg_ips"),
        comment="FortiGate intrusion prevention logs.",
        **storage_options,
    )
    op.create_index("ix_fg_ips_srcip_time", "fg_ips", ["srcip", "time"], unique=False)
    op.create_table(
        "fg_traffic",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("logid", sa.Text(), nullable=True),
        sa.Column("subtype", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("policyid", sa.BigInteger(), nullable=True),
        sa.Column("policyname", sa.Text(), nullable=True),
        sa.Column("sessionid", sa.BigInteger(), nullable=True),
        sa.Column("proto", sa.SmallInteger(), nullable=True),
        sa.Column("srcip", postgresql.INET(), nullable=True),
        sa.Column("srcport", sa.Integer(), nullable=True),
        sa.Column("srcintf", sa.Text(), nullable=True),
        sa.Column("dstip", postgresql.INET(), nullable=True),
        sa.Column("dstport", sa.Integer(), nullable=True),
        sa.Column("dstintf", sa.Text(), nullable=True),
        sa.Column("transip", postgresql.INET(), nullable=True),
        sa.Column("transport", sa.Integer(), nullable=True),
        sa.Column("trandisp", sa.Text(), nullable=True),
        sa.Column("service", sa.Text(), nullable=True),
        sa.Column("dstinetsvc", sa.Text(), nullable=True),
        sa.Column("app", sa.Text(), nullable=True),
        sa.Column("appid", sa.BigInteger(), nullable=True),
        sa.Column("appcat", sa.Text(), nullable=True),
        sa.Column("apprisk", sa.Text(), nullable=True),
        sa.Column("dstcountry", sa.Text(), nullable=True),
        sa.Column("duration", sa.BigInteger(), nullable=True),
        sa.Column("sentbyte", sa.BigInteger(), nullable=True),
        sa.Column("rcvdbyte", sa.BigInteger(), nullable=True),
        sa.Column("sentpkt", sa.BigInteger(), nullable=True),
        sa.Column("rcvdpkt", sa.BigInteger(), nullable=True),
        sa.Column("sentdelta", sa.BigInteger(), nullable=True),
        sa.Column("rcvddelta", sa.BigInteger(), nullable=True),
        sa.Column("durationdelta", sa.BigInteger(), nullable=True),
        sa.Column("is_interim", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("utmaction", sa.Text(), nullable=True),
        sa.Column("crscore", sa.Integer(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_fg_traffic"),
        comment="FortiGate traffic logs (forward/local), incl. interim updates and source NAT.",
        **storage_options,
    )
    op.create_index(
        "ix_fg_traffic_dstip_dstport_time", "fg_traffic", ["dstip", "dstport", "time"], unique=False
    )
    op.create_index("ix_fg_traffic_sessionid", "fg_traffic", ["sessionid"], unique=False)
    op.create_index("ix_fg_traffic_srcip_time", "fg_traffic", ["srcip", "time"], unique=False)
    op.create_index(
        "ix_fg_traffic_transip_transport_time",
        "fg_traffic",
        ["transip", "transport", "time"],
        unique=False,
    )
    op.create_table(
        "fg_virus",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("logid", sa.Text(), nullable=True),
        sa.Column("eventtype", sa.Text(), nullable=True),
        sa.Column("sessionid", sa.BigInteger(), nullable=True),
        sa.Column("proto", sa.SmallInteger(), nullable=True),
        sa.Column("srcip", postgresql.INET(), nullable=True),
        sa.Column("srcport", sa.Integer(), nullable=True),
        sa.Column("dstip", postgresql.INET(), nullable=True),
        sa.Column("dstport", sa.Integer(), nullable=True),
        sa.Column("service", sa.Text(), nullable=True),
        sa.Column("virus", sa.Text(), nullable=True),
        sa.Column("virusid", sa.BigInteger(), nullable=True),
        sa.Column("dtype", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("msg", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_fg_virus"),
        comment="FortiGate antivirus logs.",
        **storage_options,
    )
    op.create_index("ix_fg_virus_srcip_time", "fg_virus", ["srcip", "time"], unique=False)
    op.create_table(
        "fg_web",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("logid", sa.Text(), nullable=True),
        sa.Column("sessionid", sa.BigInteger(), nullable=True),
        sa.Column("srcip", postgresql.INET(), nullable=True),
        sa.Column("srcport", sa.Integer(), nullable=True),
        sa.Column("dstip", postgresql.INET(), nullable=True),
        sa.Column("dstport", sa.Integer(), nullable=True),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("cat", sa.Integer(), nullable=True),
        sa.Column("catdesc", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("eventtype", sa.Text(), nullable=True),
        sa.Column("profile", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_fg_web"),
        comment="FortiGate web filter logs (host name, URL, category).",
        **storage_options,
    )
    op.create_index("ix_fg_web_sessionid", "fg_web", ["sessionid"], unique=False)
    op.create_index("ix_fg_web_srcip_time", "fg_web", ["srcip", "time"], unique=False)
    op.create_table(
        "ingest_offsets",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("byte_offset", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("line_number", sa.Integer(), server_default="0", nullable=False),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("source", "day", name=op.f("pk_ingest_offsets")),
        comment="Ingest resume point per spool file (source, day).",
    )
    op.create_table(
        "job_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("job", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'ok', 'failed', 'skipped')", name=op.f("ck_job_runs_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
        comment="Runs of worker jobs (status page).",
    )
    op.create_index("ix_job_runs_job_started_at", "job_runs", ["job", "started_at"], unique=False)
    op.create_table(
        "leases",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=False),
        sa.Column("mac", postgresql.MACADDR(), nullable=False),
        sa.Column("vlan", sa.Integer(), nullable=True),
        sa.Column("server", sa.Text(), nullable=True),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("start", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("end", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), server_default="open", nullable=False),
        sa.Column("confidence", sa.Text(), nullable=False),
        sa.Column("source_table", sa.Text(), nullable=False),
        sa.Column("last_evidence", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')", name=op.f("ck_leases_confidence")
        ),
        sa.CheckConstraint(
            "end_reason IN ('dhcp', 'timeout', 'superseded', 'arp', 'open')",
            name=op.f("ck_leases_end_reason"),
        ),
        sa.CheckConstraint(
            '"end" IS NULL OR "end" >= start', name=op.f("ck_leases_end_after_start")
        ),
        sa.CheckConstraint(
            "(end_reason = 'open') = (\"end\" IS NULL)", name=op.f("ck_leases_open_iff_no_end")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leases")),
        comment="Lease timeline: which MAC held which IP when (derived, rebuildable).",
    )
    op.create_index("ix_leases_ip_start", "leases", ["ip", "start"], unique=False)
    op.create_index("ix_leases_mac_start", "leases", ["mac", "start"], unique=False)
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("severity", sa.SmallInteger(), nullable=True),
        sa.Column("facility", sa.SmallInteger(), nullable=True),
        sa.Column("program", sa.Text(), nullable=True),
        sa.Column("host", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("subtype", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), server_default="", nullable=False),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_messages"),
        comment="Every received syslog message (lightweight); raw reference to the spool line.",
        **storage_options,
    )
    op.create_index("ix_messages_source_time", "messages", ["source", "time"], unique=False)
    op.create_index("ix_messages_type_time", "messages", ["type", "time"], unique=False)
    op.create_table(
        "mt_account",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("user_name", sa.Text(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("via", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_mt_account"),
        comment="MikroTik login/logout/login failure.",
        **storage_options,
    )
    op.create_index("ix_mt_account_ip_time", "mt_account", ["ip", "time"], unique=False)
    op.create_table(
        "mt_arp",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=False),
        sa.Column("mac", postgresql.MACADDR(), nullable=False),
        sa.Column("interface", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_mt_arp"),
        comment="MikroTik hourly ARP list (proves IP-MAC pairs of static devices).",
        **storage_options,
    )
    op.create_index("ix_mt_arp_ip_time", "mt_arp", ["ip", "time"], unique=False)
    op.create_index("ix_mt_arp_mac_time", "mt_arp", ["mac", "time"], unique=False)
    op.create_table(
        "mt_config",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("user_name", sa.Text(), nullable=True),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("via", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_mt_config"),
        comment="MikroTik configuration changes.",
        **storage_options,
    )
    op.create_index("ix_mt_config_source_time", "mt_config", ["source", "time"], unique=False)
    op.create_table(
        "mt_dhcp",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("server", sa.Text(), nullable=False),
        sa.Column("vlan", sa.Integer(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=False),
        sa.Column("mac", postgresql.MACADDR(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_mt_dhcp"),
        comment="MikroTik DHCP server events: which MAC got which IP when.",
        **storage_options,
    )
    op.create_index("ix_mt_dhcp_ip_time", "mt_dhcp", ["ip", "time"], unique=False)
    op.create_index("ix_mt_dhcp_mac_time", "mt_dhcp", ["mac", "time"], unique=False)
    op.create_table(
        "mt_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("topics", sa.Text(), nullable=True),
        sa.Column("level", sa.Text(), nullable=True),
        sa.Column("identity", sa.Text(), nullable=True),
        sa.Column("interface", sa.Text(), nullable=True),
        sa.Column("mac", postgresql.MACADDR(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_mt_event"),
        comment="Other MikroTik messages (heartbeat, Wi-Fi, errors, DHCP pool empty).",
        **storage_options,
    )
    op.create_index("ix_mt_event_event_time", "mt_event", ["event", "time"], unique=False)
    op.create_index("ix_mt_event_source_time", "mt_event", ["source", "time"], unique=False)
    op.create_table(
        "mt_fw",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=True),
        sa.Column("chain", sa.Text(), nullable=True),
        sa.Column("in_if", sa.Text(), nullable=True),
        sa.Column("out_if", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("src_mac", postgresql.MACADDR(), nullable=True),
        sa.Column("proto", sa.Text(), nullable=True),
        sa.Column("src", postgresql.INET(), nullable=True),
        sa.Column("sport", sa.Integer(), nullable=True),
        sa.Column("dst", postgresql.INET(), nullable=True),
        sa.Column("dport", sa.Integer(), nullable=True),
        sa.Column("len", sa.Integer(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_mt_fw"),
        comment="MikroTik firewall logs.",
        **storage_options,
    )
    op.create_index("ix_mt_fw_dst_time", "mt_fw", ["dst", "time"], unique=False)
    op.create_index("ix_mt_fw_src_time", "mt_fw", ["src", "time"], unique=False)
    op.create_table(
        "mt_interface",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("time", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("time_received", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("sender", postgresql.INET(), nullable=False),
        sa.Column("interface", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("raw_day", sa.Date(), nullable=False),
        sa.Column("raw_line", sa.Integer(), nullable=False),
        sa.Column("raw_offset", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("time", "id", name="pk_mt_interface"),
        comment="MikroTik interface link up/down.",
        **storage_options,
    )
    op.create_index("ix_mt_interface_source_time", "mt_interface", ["source", "time"], unique=False)
    op.create_table(
        "parse_failures",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("first_seen", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("last_seen", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("example_line", sa.Integer(), nullable=False),
        sa.Column("example_offset", sa.BigInteger(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parse_failures")),
        sa.UniqueConstraint(
            "source", "day", "pattern", name=op.f("uq_parse_failures_source_day_pattern")
        ),
        comment="Unparsed lines per source/day/pattern with one example raw reference.",
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.Text(), server_default=":system", nullable=False),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_settings")),
        comment="Application settings; seeded defaults, edited by admins.",
    )
    op.create_table(
        "sources",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("address", postgresql.INET(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("expected", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("heartbeat_minutes", sa.Integer(), server_default="15", nullable=False),
        sa.Column("unknown", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("first_seen", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_seen", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('fortigate', 'mikrotik', 'generic')", name=op.f("ck_sources_type")
        ),
        sa.CheckConstraint(
            "heartbeat_minutes BETWEEN 1 AND 1440", name=op.f("ck_sources_heartbeat_minutes")
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_sources")),
        comment="Log sources (configured and discovered senders).",
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("password_changed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("totp_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("totp_last_counter", sa.BigInteger(), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_login", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('admin', 'auditor', 'hr', 'viewer')", name=op.f("ck_users_role")
        ),
        sa.CheckConstraint(
            "username ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name=op.f("ck_users_username")
        ),
        sa.CheckConstraint("failed_attempts >= 0", name=op.f("ck_users_failed_attempts")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
        comment="Users of the web interface (Argon2id password hashes, TOTP).",
    )
    op.create_table(
        "lookups",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("authority", sa.Text(), nullable=False),
        sa.Column("file_no", sa.Text(), nullable=False),
        sa.Column("request_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("query", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcome", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("mode", sa.Text(), nullable=True),
        sa.Column("result_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('exact', 'candidates', 'internal', 'none')", name=op.f("ck_lookups_mode")
        ),
        sa.CheckConstraint(
            "btrim(authority) <> '' AND btrim(file_no) <> '' AND btrim(reason) <> ''",
            name=op.f("ck_lookups_request_details"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_lookups_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lookups")),
        comment="5651 lookups: request details, query, outcome.",
    )
    op.create_index("ix_lookups_created_at", "lookups", ["created_at"], unique=False)
    op.create_index(op.f("ix_lookups_user_id"), "lookups", ["user_id"], unique=False)
    op.create_table(
        "persons",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("employee_no", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_persons_department_id_departments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_persons")),
        sa.UniqueConstraint("employee_no", name=op.f("uq_persons_employee_no")),
        comment="Persons (entered by hand or CSV).",
    )
    op.create_index(op.f("ix_persons_department_id"), "persons", ["department_id"], unique=False)
    op.create_table(
        "privacy_notice_acceptances",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column(
            "accepted_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_privacy_notice_acceptances_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_privacy_notice_acceptances")),
        sa.UniqueConstraint(
            "user_id", "version", name=op.f("uq_privacy_notice_acceptances_user_id_version")
        ),
        comment="Which user accepted which version of the privacy notice (KVKK).",
    )
    op.create_table(
        "user_recovery_codes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("used_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_recovery_codes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_recovery_codes")),
        comment="Hashed one-time recovery codes for TOTP.",
    )
    op.create_index(
        op.f("ix_user_recovery_codes_user_id"), "user_recovery_codes", ["user_id"], unique=False
    )
    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_absolute", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("mfa_passed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("csrf_token_hash", sa.Text(), nullable=False),
        sa.Column("revoked_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("reauthenticated_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "csrf_token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_user_sessions_csrf_token_hash")
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_user_sessions_token_hash")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_user_sessions")),
        comment="Login sessions (SHA-256 of the cookie token; idle and absolute expiry).",
    )
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"], unique=False)
    op.create_table(
        "vlans",
        sa.Column("subnet", postgresql.CIDR(), nullable=False),
        sa.Column("vlan_id", sa.Integer(), nullable=True),
        sa.Column("name_tr", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("dhcp_server", sa.Text(), nullable=True),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column("guest", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("vlan_id BETWEEN 1 AND 4094", name=op.f("ck_vlans_vlan_id")),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name=op.f("fk_vlans_department_id_departments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("subnet", name=op.f("pk_vlans")),
        sa.UniqueConstraint("dhcp_server", name=op.f("uq_vlans_dhcp_server")),
        sa.UniqueConstraint("vlan_id", name=op.f("uq_vlans_vlan_id")),
        comment="VLANs/subnets (spec 2.2); department default for devices in the VLAN.",
    )
    op.create_table(
        "devices",
        sa.Column("mac", postgresql.MACADDR(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("first_seen", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_seen", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_ip", postgresql.INET(), nullable=True),
        sa.Column("last_vlan", sa.Integer(), nullable=True),
        sa.Column(
            "hostnames",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("vendor", sa.Text(), nullable=True),
        sa.Column("randomized", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("person_id", sa.BigInteger(), nullable=True),
        sa.Column("infrastructure", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('pc', 'phone', 'printer', 'ip_phone', 'ap', 'server', 'camera', 'other')",
            name=op.f("ck_devices_type"),
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            name=op.f("fk_devices_person_id_persons"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("mac", name=op.f("pk_devices")),
        comment="Devices by MAC address, optionally linked to a person.",
    )
    op.create_index("ix_devices_last_ip", "devices", ["last_ip"], unique=False)
    op.create_index(op.f("ix_devices_person_id"), "devices", ["person_id"], unique=False)
    op.create_table(
        "evidence_packages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("lookup_id", sa.BigInteger(), nullable=True),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("password_protected", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("status", sa.Text(), server_default="building", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("download_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('building', 'ready', 'failed', 'expired', 'deleted')",
            name=op.f("ck_evidence_packages_status"),
        ),
        sa.ForeignKeyConstraint(
            ["lookup_id"],
            ["lookups.id"],
            name=op.f("fk_evidence_packages_lookup_id_lookups"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_evidence_packages_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_packages")),
        comment="Evidence packages (ZIP files in exports/, kept 30 days by default).",
    )
    op.create_index(
        op.f("ix_evidence_packages_lookup_id"), "evidence_packages", ["lookup_id"], unique=False
    )
    op.create_table(
        "evidence_downloads",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("package_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column(
            "time",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["evidence_packages.id"],
            name=op.f("fk_evidence_downloads_package_id_evidence_packages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_evidence_downloads_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_downloads")),
        comment="Every download of an evidence package (also audited).",
    )
    op.create_index(
        op.f("ix_evidence_downloads_package_id"), "evidence_downloads", ["package_id"], unique=False
    )
    op.execute(AUDIT_GUARD_FUNCTION)
    for statement in AUDIT_GUARD_TRIGGERS:
        op.execute(statement)
    if storage["enabled"]:
        _hypertables(bind, storage)
    else:
        _default_partitions(bind, storage)
    _record_timescale(bind, storage)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS arci_audit_log_immutable()")
    op.drop_index(op.f("ix_evidence_downloads_package_id"), table_name="evidence_downloads")
    op.drop_table("evidence_downloads")
    op.drop_index(op.f("ix_evidence_packages_lookup_id"), table_name="evidence_packages")
    op.drop_table("evidence_packages")
    op.drop_index(op.f("ix_devices_person_id"), table_name="devices")
    op.drop_index("ix_devices_last_ip", table_name="devices")
    op.drop_table("devices")
    op.drop_table("vlans")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index(op.f("ix_user_recovery_codes_user_id"), table_name="user_recovery_codes")
    op.drop_table("user_recovery_codes")
    op.drop_table("privacy_notice_acceptances")
    op.drop_index(op.f("ix_persons_department_id"), table_name="persons")
    op.drop_table("persons")
    op.drop_index(op.f("ix_lookups_user_id"), table_name="lookups")
    op.drop_index("ix_lookups_created_at", table_name="lookups")
    op.drop_table("lookups")
    op.drop_table("users")
    op.drop_table("sources")
    op.drop_table("settings")
    op.drop_table("parse_failures")
    op.drop_index("ix_mt_interface_source_time", table_name="mt_interface")
    op.drop_table("mt_interface")
    op.drop_index("ix_mt_fw_src_time", table_name="mt_fw")
    op.drop_index("ix_mt_fw_dst_time", table_name="mt_fw")
    op.drop_table("mt_fw")
    op.drop_index("ix_mt_event_source_time", table_name="mt_event")
    op.drop_index("ix_mt_event_event_time", table_name="mt_event")
    op.drop_table("mt_event")
    op.drop_index("ix_mt_dhcp_mac_time", table_name="mt_dhcp")
    op.drop_index("ix_mt_dhcp_ip_time", table_name="mt_dhcp")
    op.drop_table("mt_dhcp")
    op.drop_index("ix_mt_config_source_time", table_name="mt_config")
    op.drop_table("mt_config")
    op.drop_index("ix_mt_arp_mac_time", table_name="mt_arp")
    op.drop_index("ix_mt_arp_ip_time", table_name="mt_arp")
    op.drop_table("mt_arp")
    op.drop_index("ix_mt_account_ip_time", table_name="mt_account")
    op.drop_table("mt_account")
    op.drop_index("ix_messages_type_time", table_name="messages")
    op.drop_index("ix_messages_source_time", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_leases_mac_start", table_name="leases")
    op.drop_index("ix_leases_ip_start", table_name="leases")
    op.drop_table("leases")
    op.drop_index("ix_job_runs_job_started_at", table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_table("ingest_offsets")
    op.drop_index("ix_fg_web_srcip_time", table_name="fg_web")
    op.drop_index("ix_fg_web_sessionid", table_name="fg_web")
    op.drop_table("fg_web")
    op.drop_index("ix_fg_virus_srcip_time", table_name="fg_virus")
    op.drop_table("fg_virus")
    op.drop_index("ix_fg_traffic_transip_transport_time", table_name="fg_traffic")
    op.drop_index("ix_fg_traffic_srcip_time", table_name="fg_traffic")
    op.drop_index("ix_fg_traffic_sessionid", table_name="fg_traffic")
    op.drop_index("ix_fg_traffic_dstip_dstport_time", table_name="fg_traffic")
    op.drop_table("fg_traffic")
    op.drop_index("ix_fg_ips_srcip_time", table_name="fg_ips")
    op.drop_table("fg_ips")
    op.drop_index("ix_fg_event_subtype_time", table_name="fg_event")
    op.drop_table("fg_event")
    op.drop_index("ix_fg_dns_srcip_time", table_name="fg_dns")
    op.drop_index("ix_fg_dns_qname_time", table_name="fg_dns")
    op.drop_table("fg_dns")
    op.drop_index("ix_fg_app_srcip_time", table_name="fg_app")
    op.drop_index("ix_fg_app_sessionid", table_name="fg_app")
    op.drop_table("fg_app")
    op.drop_table("departments")
    op.drop_index("ix_audit_log_time", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_id_time", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(
        "uq_alerts_open_dedup_key",
        table_name="alerts",
        postgresql_where=sa.text("state = 'open' AND dedup_key IS NOT NULL"),
    )
    op.drop_index("ix_alerts_time", table_name="alerts")
    op.drop_index("ix_alerts_state_time", table_name="alerts")
    op.drop_table("alerts")


# -- helpers --------------------------------------------------------------------------------------


def _require_utf8(bind: sa.Connection) -> None:
    """Turkish text and arbitrary log content need UTF-8 (Windows clusters default to WIN1252)."""
    encoding = bind.execute(
        sa.text(
            "SELECT pg_encoding_to_char(encoding) FROM pg_database "
            "WHERE datname = current_database()"
        )
    ).scalar_one()
    if encoding != "UTF8":
        raise RuntimeError(
            f"database encoding is {encoding}; ARCILOGGER needs UTF8 "
            "(CREATE DATABASE arcilogger ENCODING 'UTF8' TEMPLATE template0 …)"
        )


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("-")[0].split(".") if part.isdigit())


def _short(exc: DBAPIError) -> str:
    return str(exc.orig).strip().splitlines()[0][:300] if exc.orig else str(exc)[:300]


def _timescale_extension(bind: sa.Connection) -> dict[str, Any]:
    """Create the TimescaleDB extension when wanted and possible; the status to record."""
    mode = context.config.attributes.get("timescale", "auto")
    status: dict[str, Any] = {
        "mode": mode,
        "enabled": False,
        "version": None,
        "storage": "partitions",
        "hypertables": [],
        "partitioned": [],
        "compression": False,
        "telemetry_off": False,
        "reason": "",
    }
    if mode == "off":
        status["reason"] = "disabled in the configuration ([database] timescale = off)"
        return status
    available = bind.execute(
        sa.text("SELECT default_version FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar_one_or_none()
    if available is None:
        if mode == "require":
            raise RuntimeError(
                "TimescaleDB is required ([database] timescale = require) but not installed"
            )
        status["reason"] = "timescaledb extension is not installed"
        return status
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
    except DBAPIError as exc:
        if mode == "require":
            raise
        status["reason"] = f"CREATE EXTENSION timescaledb failed: {_short(exc)}"
        return status
    version = bind.execute(
        sa.text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar_one()
    status.update(enabled=True, version=version, storage="hypertables")
    # TimescaleDB telemetry is on by default; ARCILOGGER sends nothing anywhere (spec 21).
    # The installer sets it for the database before CREATE EXTENSION and server-wide
    # (ALTER SYSTEM) right after it; KURULUM 2.1 adds it to postgresql.conf.
    try:
        with bind.begin_nested():
            bind.execute(
                sa.text(
                    "DO $$ BEGIN EXECUTE format("
                    "'ALTER DATABASE %I SET timescaledb.telemetry_level = off', "
                    "current_database()); END $$"
                )
            )
        status["telemetry_off"] = True
    except DBAPIError as exc:
        status["telemetry_reason"] = _short(exc)
    return status


def _default_partitions(bind: sa.Connection, status: dict[str, Any]) -> None:
    """Plain PostgreSQL: the default partition of every time-series table (monthly partitions
    come from ``arcilogger_db.partitions``)."""
    for table in TIME_SERIES_TABLES:
        bind.execute(sa.text(f"CREATE TABLE {table}_default PARTITION OF {table} DEFAULT"))
        status["partitioned"].append(table)


def _hypertables(bind: sa.Connection, status: dict[str, Any]) -> None:
    mode = status["mode"]
    new_api = _version(str(status["version"])) >= (2, 13)
    for table in TIME_SERIES_TABLES:
        if new_api:
            statement = (
                "SELECT create_hypertable(CAST(:table AS regclass), "
                f"by_range('time', INTERVAL '{CHUNK_INTERVAL}'), create_default_indexes => FALSE)"
            )
        else:
            statement = (
                "SELECT create_hypertable(CAST(:table AS regclass), 'time', "
                f"chunk_time_interval => INTERVAL '{CHUNK_INTERVAL}', "
                "create_default_indexes => FALSE)"
            )
        bind.execute(sa.text(statement), {"table": table})
        status["hypertables"].append(table)
    try:
        with bind.begin_nested():
            for table in TIME_SERIES_TABLES:
                bind.execute(
                    sa.text(
                        f"ALTER TABLE {table} SET (timescaledb.compress, "
                        "timescaledb.compress_segmentby = 'source', "
                        "timescaledb.compress_orderby = 'time DESC, id DESC')"
                    )
                )
                bind.execute(
                    sa.text(
                        "SELECT add_compression_policy(CAST(:table AS regclass), "
                        f"INTERVAL '{COMPRESS_AFTER}')"
                    ),
                    {"table": table},
                )
        status["compression"] = True
    except DBAPIError as exc:
        if mode == "require":
            raise
        status["compression_reason"] = _short(exc)


def _record_timescale(bind: sa.Connection, status: dict[str, Any]) -> None:
    bind.execute(
        sa.text(
            "INSERT INTO settings (key, value, updated_by) "
            "VALUES ('db.timescale', CAST(:value AS jsonb), :author) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()"
        ),
        {"value": json.dumps(status), "author": ":migration"},
    )
