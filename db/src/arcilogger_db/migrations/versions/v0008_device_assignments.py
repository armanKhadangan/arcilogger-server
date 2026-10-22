"""Device-person assignment history (``device_assignments``, spec 9.2, ADR-0008).

Revision ID: 0008_device_assignments
Revises: 0007_evidence_seals
Create Date: 2026-10-19

A 5651 lookup for a past moment must name the person the device was assigned to **at that
moment**, not whoever holds it when the lookup runs. One row per assignment
``[valid_from, valid_to)`` (``valid_to`` NULL = current); ``devices.person_id`` stays as the
denormalised current owner.

**No overlap, enforced by the database.** At most one open row per MAC (partial unique index) and
no two overlapping intervals of one MAC:

* ``btree_gist`` available (a contrib module of the standard distribution, "trusted"
  since PostgreSQL 13, so the database owner may create it): exclusion constraint
  ``ex_device_assignments_no_overlap`` on ``(mac WITH =, tstzrange(valid_from, valid_to) WITH
  &&)``;
* otherwise (the extension is missing or may not be created): a ``BEFORE INSERT OR UPDATE``
  trigger that locks the device row and refuses an overlap with the same SQLSTATE ``23P01``
  (``exclusion_violation``) and constraint name, so callers see one error either way.

``config.attributes["assignment_guard"] = "trigger"`` forces the fallback (tests).

**Backfill:** every device that already has a person link gets one open row with
``backfilled = true`` and ``valid_from = coalesce(first_seen, created_at)`` — the real start of
such a link was never recorded.

Downgrade drops the table and the trigger function; the ``btree_gist`` extension is left in
place (harmless, and another object may use it).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

revision: str = "0008_device_assignments"
down_revision: str | Sequence[str] | None = "0007_evidence_seals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TZ = postgresql.TIMESTAMP(timezone=True)
EXCLUSION: Final = (
    "ALTER TABLE device_assignments ADD CONSTRAINT ex_device_assignments_no_overlap "
    "EXCLUDE USING gist (mac WITH =, tstzrange(valid_from, valid_to, '[)') WITH &&)"
)
TRIGGER_FUNCTION: Final = """
CREATE FUNCTION arci_device_assignments_no_overlap() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.valid_to IS NOT NULL AND NEW.valid_to < NEW.valid_from THEN
        RETURN NEW;  -- refused by the check constraint (it runs after this trigger)
    END IF;
    -- One writer per MAC at a time: a concurrent insert of an overlapping row waits here and
    -- then sees the committed one (READ COMMITTED takes a new snapshot per statement).
    PERFORM 1 FROM devices WHERE mac = NEW.mac FOR UPDATE;
    IF EXISTS (
        SELECT 1 FROM device_assignments
        WHERE mac = NEW.mac AND id <> NEW.id
          AND tstzrange(valid_from, valid_to, '[)')
              && tstzrange(NEW.valid_from, NEW.valid_to, '[)')
    ) THEN
        RAISE EXCEPTION 'overlapping device assignment for %', NEW.mac
            USING ERRCODE = 'exclusion_violation', CONSTRAINT = 'ex_device_assignments_no_overlap';
    END IF;
    RETURN NEW;
END;
$$
"""
TRIGGER: Final = (
    "CREATE TRIGGER device_assignments_no_overlap "
    "BEFORE INSERT OR UPDATE OF mac, valid_from, valid_to ON device_assignments "
    "FOR EACH ROW EXECUTE FUNCTION arci_device_assignments_no_overlap()"
)
#: ``assigned_by`` of the backfilled rows (``:`` never occurs in a user name).
BACKFILL_AUTHOR: Final = ":migration"
BACKFILL: Final = """
INSERT INTO device_assignments (mac, person_id, valid_from, valid_to, assigned_by, backfilled)
SELECT mac, person_id, coalesce(first_seen, created_at), NULL, :author, true
FROM devices
WHERE person_id IS NOT NULL
ORDER BY mac
"""


def upgrade() -> None:
    op.create_table(
        "device_assignments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("mac", postgresql.MACADDR(), nullable=False),
        sa.Column("person_id", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", TZ, nullable=False),
        sa.Column("valid_to", TZ, nullable=True),
        sa.Column("assigned_by", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("backfilled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", TZ, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name=op.f("ck_device_assignments_valid_interval"),
        ),
        sa.ForeignKeyConstraint(
            ["mac"],
            ["devices.mac"],
            name=op.f("fk_device_assignments_mac_devices"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["persons.id"],
            name=op.f("fk_device_assignments_person_id_persons"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_assignments")),
        comment="Device-person assignment history (who held a MAC when); never deleted.",
    )
    op.create_index(
        "uq_device_assignments_open_mac",
        "device_assignments",
        ["mac"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.create_index(
        "ix_device_assignments_mac_valid_from",
        "device_assignments",
        ["mac", "valid_from"],
        unique=False,
    )
    op.create_index(
        op.f("ix_device_assignments_person_id"),
        "device_assignments",
        ["person_id"],
        unique=False,
    )
    bind = op.get_bind()
    _overlap_guard(bind)
    bind.execute(sa.text(BACKFILL), {"author": BACKFILL_AUTHOR})


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS device_assignments_no_overlap ON device_assignments")
    op.execute("DROP FUNCTION IF EXISTS arci_device_assignments_no_overlap()")
    op.drop_index(op.f("ix_device_assignments_person_id"), table_name="device_assignments")
    op.drop_index("ix_device_assignments_mac_valid_from", table_name="device_assignments")
    op.drop_index(
        "uq_device_assignments_open_mac",
        table_name="device_assignments",
        postgresql_where=sa.text("valid_to IS NULL"),
    )
    op.drop_table("device_assignments")


def _overlap_guard(bind: sa.Connection) -> str:
    """The exclusion constraint where ``btree_gist`` can be used, else the trigger."""
    if context.config.attributes.get("assignment_guard", "auto") != "trigger":
        available = bind.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'btree_gist'")
        ).scalar_one_or_none()
        if available is not None:
            try:
                with bind.begin_nested():
                    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
                    bind.execute(sa.text(EXCLUSION))
            except DBAPIError:
                pass  # e.g. no right to create the extension: the trigger does the same job
            else:
                return "exclusion"
    bind.execute(sa.text(TRIGGER_FUNCTION))
    bind.execute(sa.text(TRIGGER))
    return "trigger"
