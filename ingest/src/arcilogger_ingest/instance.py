"""One ingest service per database: a PostgreSQL session-level advisory lock.

Two instances would never load a line twice — the compare-and-set on ``ingest_offsets``
(:mod:`arcilogger_ingest.loader`) refuses the second one's batch — but they would keep refusing
each other's batches (wasted work, a warning per batch) and overwrite each other's status file.
A second instance is an easy mistake on Windows: ``arcilogger-ingest run`` typed in a console
while the ``ArciIngest`` service is running. So the service holds an advisory lock for as long as
it runs, on a connection of its own:

* the lock belongs to the database, so test databases do not see each other's lock;
* it lives as long as the database session: when the service dies (kill, crash, power cut)
  PostgreSQL drops it with the connection — there is no stale lock file to clean up;
* the connection is in autocommit mode, so it is never left "idle in transaction";
* on release the lock is unlocked explicitly (``pg_advisory_unlock``) and the connection closed,
  not returned to the pool (a pooled connection would keep holding the lock). Closing alone
  frees the lock only once the server backend has exited, which can take a moment on a busy
  server: a ``run --once`` started right after the service stopped would then exit 4.

The lock connection dies with every other connection when the database restarts. The service
checks it before every batch and takes the lock again — or finds that another instance got it
meanwhile and steps back (:mod:`arcilogger_ingest.service`). The compare-and-set stays as the
safety net for the moments in between.
"""

from __future__ import annotations

import contextlib
from typing import Final

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError

#: ``pg_try_advisory_lock`` key ("ARCIINGS" as a signed 64-bit integer, like the other keys).
INGEST_LOCK_KEY: Final = int.from_bytes(b"ARCIINGS", "big") - 2**63


class InstanceLock:
    """The single-instance lock of the ingest service on ``engine``'s database."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None

    @property
    def held(self) -> bool:
        """The lock was taken and its connection has not failed since (last known state)."""
        return self._connection is not None

    def acquire(self) -> bool:
        """Hold the lock (``True``), or ``False`` while another instance holds it.

        A lock already held is checked with one round trip, because its connection may have
        died with a database restart. Raises :class:`~sqlalchemy.exc.DBAPIError` when the
        database is unavailable; the lock is then not held.
        """
        if self._connection is not None:
            try:
                self._connection.execute(text("SELECT 1"))
            except DBAPIError:
                self.release()
                raise
            return True
        connection = self._engine.connect()
        try:
            connection.execution_options(isolation_level="AUTOCOMMIT")
            got = connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": INGEST_LOCK_KEY}
            ).scalar_one()
        except BaseException:
            _discard(connection)
            raise
        if not got:
            connection.close()  # holds nothing, may go back to the pool
            return False
        self._connection = connection
        return True

    def release(self) -> None:
        """Give the lock up at once and close its connection (idempotent)."""
        connection, self._connection = self._connection, None
        if connection is not None:
            with contextlib.suppress(Exception):  # a dead connection holds nothing any more
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": INGEST_LOCK_KEY}
                )
            _discard(connection)


def _discard(connection: Connection) -> None:
    """Close the database session itself; PostgreSQL drops its advisory locks with it."""
    with contextlib.suppress(Exception):
        connection.invalidate()
    with contextlib.suppress(Exception):
        connection.close()
