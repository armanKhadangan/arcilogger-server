"""Evidence packages built in the background, on-demand seal verifications, audit import.

Revision ID: 0007_evidence_seals
Revises: 0006_lookup_reports
Create Date: 2026-10-13

* ``evidence_packages``: status ``queued`` (the API answers at once and builds the ZIP in the
  background), the package id of ``paket.json`` (``package_name``), the SHA-256 of
  ``paket.json`` and of the package's ``verify.py``, ``legal_grade`` and ``finished_at``.
* ``seal_verifications``: "Doğrula" on the sealing page (the API may not write
  ``state/verify``, ADR-0007).
* ``audit_imports``: events of the scheduled sealing and retention jobs copied into the audit
  log exactly once (spec 8.5).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_evidence_seals"
down_revision: str | Sequence[str] | None = "0006_lookup_reports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "status IN ('building', 'ready', 'failed', 'expired', 'deleted')"
NEW_STATUSES = "status IN ('queued', 'building', 'ready', 'failed', 'expired', 'deleted')"
TZ = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_evidence_packages_status"), "evidence_packages", type_="check")
    op.create_check_constraint(
        op.f("ck_evidence_packages_status"), "evidence_packages", NEW_STATUSES
    )
    op.add_column("evidence_packages", sa.Column("package_name", sa.Text(), nullable=True))
    op.add_column("evidence_packages", sa.Column("index_sha256", sa.Text(), nullable=True))
    op.add_column("evidence_packages", sa.Column("verifier_sha256", sa.Text(), nullable=True))
    op.add_column("evidence_packages", sa.Column("legal_grade", sa.Boolean(), nullable=True))
    op.add_column("evidence_packages", sa.Column("finished_at", TZ, nullable=True))
    op.create_check_constraint(
        op.f("ck_evidence_packages_verifier_sha256"),
        "evidence_packages",
        "verifier_sha256 ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_evidence_packages_index_sha256"),
        "evidence_packages",
        "index_sha256 ~ '^[0-9a-f]{64}$'",
    )

    op.create_table(
        "seal_verifications",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("started_at", TZ, nullable=False),
        sa.Column("finished_at", TZ, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("deep", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "errors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('ok', 'failed', 'timeout', 'error')",
            name=op.f("ck_seal_verifications_outcome"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_seal_verifications_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seal_verifications")),
        comment="On-demand verifications of sealed days (API); scheduled ones: state/verify.",
    )
    op.create_index(
        "ix_seal_verifications_day_finished_at",
        "seal_verifications",
        ["day", "finished_at"],
        unique=False,
    )

    op.create_table(
        "audit_imports",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("audit_id", sa.BigInteger(), nullable=False),
        sa.Column("imported_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_audit_imports")),
        comment="Scheduled-job events imported into audit_log (dedup by event key).",
    )


def downgrade() -> None:
    op.drop_table("audit_imports")
    op.drop_index("ix_seal_verifications_day_finished_at", table_name="seal_verifications")
    op.drop_table("seal_verifications")
    op.drop_constraint(
        op.f("ck_evidence_packages_index_sha256"), "evidence_packages", type_="check"
    )
    op.drop_constraint(
        op.f("ck_evidence_packages_verifier_sha256"), "evidence_packages", type_="check"
    )
    for column in ("finished_at", "legal_grade", "verifier_sha256", "index_sha256", "package_name"):
        op.drop_column("evidence_packages", column)
    # Rows the new code left in the new state cannot satisfy the old check.
    op.execute("UPDATE evidence_packages SET status = 'failed' WHERE status = 'queued'")
    op.drop_constraint(op.f("ck_evidence_packages_status"), "evidence_packages", type_="check")
    op.create_check_constraint(
        op.f("ck_evidence_packages_status"), "evidence_packages", OLD_STATUSES
    )
