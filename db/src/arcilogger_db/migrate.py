"""Programmatic Alembic: migrate the database without an ``alembic.ini``.

The migration scripts ship inside the package (``arcilogger_db/migrations``), so the installed
wheel is enough on the server. :func:`upgrade` migrates to the newest revision, seeds **and**
creates the monthly partitions (plain PostgreSQL only, :mod:`arcilogger_db.partitions`), in one
transaction, under a PostgreSQL advisory lock (two installers cannot migrate at once).

TimescaleDB mode (``[database] timescale`` in the configuration) is passed to the migration
through ``Config.attributes["timescale"]``: ``auto`` (default), ``require`` or ``off``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

from arcilogger_db.partitions import MONTHS_AHEAD, PartitionReport, ensure_partitions
from arcilogger_db.seed import SeedReport, seed

SCRIPT_LOCATION: Final = "arcilogger_db:migrations"
#: pg_advisory_xact_lock key serialising migrations ("ARCIMIGR" as a 64-bit integer).
MIGRATION_LOCK_KEY: Final = int.from_bytes(b"ARCIMIGR", "big") - 2**63

TimescaleMode = Literal["auto", "require", "off"]


def alembic_config(
    connection: Connection | None = None, *, timescale: TimescaleMode = "auto"
) -> AlembicConfig:
    """Alembic configuration pointing at the packaged migrations."""
    config = AlembicConfig()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    config.attributes["connection"] = connection
    config.attributes["timescale"] = timescale
    return config


def head_revisions() -> tuple[str, ...]:
    """Newest revision(s) shipped with this version."""
    script = ScriptDirectory.from_config(alembic_config())
    return tuple(sorted(script.get_heads()))


def current_revisions(connection: Connection) -> tuple[str, ...]:
    """Revision(s) the database is at (empty for a database never migrated)."""
    return tuple(sorted(MigrationContext.configure(connection).get_current_heads()))


@dataclass(frozen=True)
class MigrationStatus:
    current: tuple[str, ...]
    heads: tuple[str, ...]

    @property
    def up_to_date(self) -> bool:
        return self.current == self.heads

    @property
    def empty(self) -> bool:
        return not self.current


def migration_status(engine: Engine) -> MigrationStatus:
    with engine.connect() as connection:
        return MigrationStatus(current_revisions(connection), head_revisions())


@dataclass(frozen=True)
class UpgradeResult:
    before: tuple[str, ...]
    after: tuple[str, ...]
    seed: SeedReport
    partitions: PartitionReport


def upgrade(
    engine: Engine,
    *,
    revision: str = "heads",
    timescale: TimescaleMode = "auto",
    run_seed: bool = True,
    months_ahead: int = MONTHS_AHEAD,
    months_back: int | None = None,
) -> UpgradeResult:
    """Migrate to ``revision`` (default: newest), seed and create partitions, in one transaction
    (``months_back`` defaults to the ``db.retention_days`` window, see :mod:`.partitions`)."""
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        before = current_revisions(connection)
        command.upgrade(alembic_config(connection, timescale=timescale), revision)
        after = current_revisions(connection)
        report = SeedReport()
        if run_seed:
            with Session(bind=connection) as session:
                report = seed(session)
        partitions = PartitionReport()
        if after:
            partitions = ensure_partitions(
                connection, months_ahead=months_ahead, months_back=months_back
            )
    return UpgradeResult(before=before, after=after, seed=report, partitions=partitions)
