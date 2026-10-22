"""Offline licensing: installation, licence files, licence events (licensing contract 4.1).

Revision ID: 0014_license
Revises: 0013_site_data
Create Date: 2026-10-20

* ``license_installation``: one row (``id = 1``) with this installation's id (request code,
  licence registry; informational, not enforced) and its trial start. Written once by
  :func:`arcilogger_db.licensing.ensure_installation` (the seed, or the first status read).
* ``license_files``: every licence file exactly as installed (``content``, at most 64 KiB) with
  an index of its fields. Only ``content``, re-verified, is ever trusted.
* ``license_events``: installs, removals, refusals, limit crossings and state changes. The
  active licence is the file of the newest ``install``/``remove`` event when it is ``install``.

All three are **append-only**: one function ``arci_license_immutable()`` and, per table, a
``BEFORE UPDATE OR DELETE … FOR EACH ROW`` and a ``BEFORE TRUNCATE … FOR EACH STATEMENT``
trigger reject every change with ``insufficient_privilege`` (the ``audit_log`` pattern of
0001). The tables are an index of what happened, not the licence: the licence is the signed
file, verified again on every evaluation, so a database owner who disables the triggers changes
no licence decision.

The downgrade drops the triggers, the tables and the function.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_license"
down_revision: str | Sequence[str] | None = "0013_site_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)
TABLES = ("license_installation", "license_files", "license_events")

GUARD_FUNCTION = """
CREATE FUNCTION arci_license_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only (% rejected)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def _guard_triggers(table: str) -> tuple[str, str]:
    return (
        f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION arci_license_immutable()",
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION arci_license_immutable()",
    )


def upgrade() -> None:
    op.create_table(
        "license_installation",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("installation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trial_started_at", TZ, nullable=False),
        sa.Column("created_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_license_installation_single")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_license_installation")),
        sa.UniqueConstraint(
            "installation_id", name=op.f("uq_license_installation_installation_id")
        ),
        comment="The installation's identity and trial start (one row, never changed).",
    )
    op.create_table(
        "license_files",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.Text(), nullable=False),
        sa.Column("content", postgresql.BYTEA(), nullable=False),
        sa.Column("license_id", sa.Text(), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        sa.Column("licensee", sa.Text(), nullable=False),
        sa.Column("issued_at", TZ, nullable=False),
        sa.Column("first_installed_at", TZ, nullable=False),
        sa.Column("first_installed_by", sa.Text(), nullable=False),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_license_files_sha256")),
        sa.CheckConstraint(
            "payload_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_license_files_payload_sha256")
        ),
        sa.CheckConstraint(
            "octet_length(content) BETWEEN 1 AND 65536", name=op.f("ck_license_files_content")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_license_files")),
        sa.UniqueConstraint("sha256", name=op.f("uq_license_files_sha256")),
        comment="Licence files as installed (exact bytes; never changed or deleted).",
    )
    op.create_index(
        op.f("ix_license_files_payload_sha256"), "license_files", ["payload_sha256"], unique=False
    )
    op.create_index(
        op.f("ix_license_files_license_id"), "license_files", ["license_id"], unique=False
    )
    op.create_table(
        "license_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("at", TZ, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("license_file_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('install', 'remove', 'refuse', 'limit', 'state')",
            name=op.f("ck_license_events_kind"),
        ),
        sa.CheckConstraint(
            "source IN ('web', 'cli', 'worker', 'seed')", name=op.f("ck_license_events_source")
        ),
        sa.CheckConstraint(
            "kind NOT IN ('install', 'remove') OR license_file_id IS NOT NULL",
            name=op.f("ck_license_events_file"),
        ),
        sa.ForeignKeyConstraint(
            ["license_file_id"],
            ["license_files.id"],
            name=op.f("fk_license_events_license_file_id_license_files"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_license_events")),
        comment=(
            "Licence history: installs, removals, refusals, limit and state changes (append-only)."
        ),
    )
    op.create_index("ix_license_events_kind_id", "license_events", ["kind", "id"], unique=False)
    op.execute(GUARD_FUNCTION)
    for table in TABLES:
        for statement in _guard_triggers(table):
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
    op.drop_index("ix_license_events_kind_id", table_name="license_events")
    op.drop_table("license_events")
    op.drop_index(op.f("ix_license_files_license_id"), table_name="license_files")
    op.drop_index(op.f("ix_license_files_payload_sha256"), table_name="license_files")
    op.drop_table("license_files")
    op.drop_table("license_installation")
    op.execute("DROP FUNCTION IF EXISTS arci_license_immutable()")
