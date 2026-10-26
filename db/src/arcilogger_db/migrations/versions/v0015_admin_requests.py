"""Browser administration: the request table (ADR-0014, contract 2.15).

Revision ID: 0015_admin_requests
Revises: 0014_license
Create Date: 2026-10-26

``admin_requests`` holds every request of the browser administration from the API's ``202`` to
its outcome: the API inserts a row in the transaction that writes ``admin.request`` to the
audit log and mirrors the agent's terminal states into it, so the history outlives the agent's
30-day cleanup; the worker takes its own requests (``channel = 'worker'``: test e-mail, lease
rebuild) from it with ``FOR UPDATE SKIP LOCKED``.

The table never holds a secret (ADR-0009 §6: it is in the nightly dumps): two check constraints
refuse a key named like a secret (``password``, ``customer``, ``token``, any case, at any depth
of ``params`` or ``result``) unless its value is a masked secret ``{"secret": true, …}``. The
other checks keep ``kind``, ``channel``, ``section`` and ``state`` to the names of
``arcilogger_core.admin.contract`` and ``terminal`` true exactly for a terminal state.

Indexes: the worker's queue and the API's open requests ``(channel, state, created_at)``, the
history per kind ``(kind, created_at DESC)``, one open configuration request per section
``(section) WHERE NOT terminal``.

The downgrade drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_admin_requests"
down_revision: str | Sequence[str] | None = "0014_license"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)
KINDS = (
    "config.apply",
    "config.revert",
    "config.cancel",
    "action.seal",
    "action.credit",
    "action.tsa_test",
    "action.replica_test",
    "worker.test_email",
    "worker.lease_rebuild",
)
SECTIONS = ("general", "tsa", "smtp", "replica", "retention", "backup", "sources")
STATES = (
    "queued",
    "waiting_for_agent",
    "expired",
    "received",
    "rejected",
    "conflict",
    "probing",
    "probe_failed",
    "pending",
    "cancelled",
    "applying",
    "applied",
    "active",
    "partially_active",
    "failed",
    "needs_attention",
    "dispatched",
    "running",
    "succeeded",
    "timed_out",
)
TERMINAL = (
    "expired",
    "rejected",
    "conflict",
    "probe_failed",
    "cancelled",
    "active",
    "partially_active",
    "failed",
    "needs_attention",
    "succeeded",
    "timed_out",
)
MASKED = (
    'NOT jsonb_path_exists({column}, \'$.** ? (@.type() == "object").keyvalue() '
    '? (@.key like_regex "password|customer|token" flag "i" && !(@.value.secret == true))\')'
)


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.create_table(
        "admin_requests",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("terminal", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("requested_by_id", sa.BigInteger(), nullable=True),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("expires_at", TZ, nullable=True),
        sa.Column("effective_at", TZ, nullable=True),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("rolled_back", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("audit_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id ~ '^[0-9a-f]{32}$'", name=op.f("ck_admin_requests_id")),
        sa.CheckConstraint(_in("kind", KINDS), name=op.f("ck_admin_requests_kind")),
        sa.CheckConstraint(
            _in("channel", ("agent", "worker")), name=op.f("ck_admin_requests_channel")
        ),
        sa.CheckConstraint(
            "(channel = 'worker') = (kind LIKE 'worker.%')",
            name=op.f("ck_admin_requests_channel_kind"),
        ),
        sa.CheckConstraint(
            f"section IS NULL OR {_in('section', SECTIONS)}",
            name=op.f("ck_admin_requests_section"),
        ),
        sa.CheckConstraint(_in("state", STATES), name=op.f("ck_admin_requests_state")),
        sa.CheckConstraint(
            f"terminal = ({_in('state', TERMINAL)})", name=op.f("ck_admin_requests_terminal")
        ),
        sa.CheckConstraint(
            "btrim(requested_by) <> ''", name=op.f("ck_admin_requests_requested_by")
        ),
        sa.CheckConstraint(
            MASKED.format(column="params"), name=op.f("ck_admin_requests_params_masked")
        ),
        sa.CheckConstraint(
            MASKED.format(column="result"), name=op.f("ck_admin_requests_result_masked")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_admin_requests_requested_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_requests")),
        comment=(
            "Requests of the browser administration (ADR-0014): state, requester, non-secret "
            "parameters and result."
        ),
    )
    op.create_index(
        "ix_admin_requests_channel_state_created_at",
        "admin_requests",
        ["channel", "state", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_admin_requests_kind_created_at",
        "admin_requests",
        ["kind", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_admin_requests_section",
        "admin_requests",
        ["section"],
        unique=False,
        postgresql_where=sa.text("NOT terminal"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_requests_section",
        table_name="admin_requests",
        postgresql_where=sa.text("NOT terminal"),
    )
    op.drop_index("ix_admin_requests_kind_created_at", table_name="admin_requests")
    op.drop_index("ix_admin_requests_channel_state_created_at", table_name="admin_requests")
    op.drop_table("admin_requests")
