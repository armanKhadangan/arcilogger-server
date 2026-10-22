"""Alembic environment for ARCILOGGER (programmatic only; see :mod:`arcilogger_db.migrate`).

There is no ``alembic.ini``: the caller passes an open connection in
``config.attributes["connection"]`` and the TimescaleDB mode in ``config.attributes["timescale"]``.
"""

from __future__ import annotations

from alembic import context

from arcilogger_db.models import Base
from arcilogger_db.partitions import include_name

config = context.config
connection = config.attributes.get("connection")
if connection is None:  # pragma: no cover - misuse
    raise RuntimeError("run migrations through arcilogger_db.migrate (arcilogger-db upgrade)")


context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    compare_type=True,
    include_name=include_name,
    transaction_per_migration=False,
)

with context.begin_transaction():
    context.run_migrations()
