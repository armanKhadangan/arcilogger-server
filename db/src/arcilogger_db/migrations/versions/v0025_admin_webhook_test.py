"""The webhook test of the browser: the worker channel's kind ``worker.webhook_test``.

Revision ID: 0025_admin_webhook_test
Revises: 0024_rules
Create Date: 2026-11-15

Wave 6 contract §2.4.4 (W6-H; manager's answer on #266): "Test gönder" of Ayarlar → Webhook'lar
is a worker-channel request like the test e-mail (``admin_requests``, ``channel = 'worker'``).
``ck_admin_requests_kind`` (revision ``0015_admin_requests``) gets the kind ``worker.webhook_test``;
``ck_admin_requests_channel_kind`` already puts every ``worker.*`` kind on the worker channel.

The downgrade deletes the ``worker.webhook_test`` rows first, so the older check holds again: a
test notification's request is history only, its outcome stays in the audit log
(``webhook.test``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "0025_admin_webhook_test"
down_revision: str | Sequence[str] | None = "0024_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The kinds of ``0015_admin_requests``.
BEFORE: Final = (
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
KINDS: Final = (*BEFORE, "worker.webhook_test")


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_admin_requests_kind"), "admin_requests", type_="check")
    op.create_check_constraint(op.f("ck_admin_requests_kind"), "admin_requests", _in("kind", KINDS))


def downgrade() -> None:
    op.execute("DELETE FROM admin_requests WHERE kind = 'worker.webhook_test'")
    op.drop_constraint(op.f("ck_admin_requests_kind"), "admin_requests", type_="check")
    op.create_check_constraint(
        op.f("ck_admin_requests_kind"), "admin_requests", _in("kind", BEFORE)
    )
