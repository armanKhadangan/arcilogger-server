"""ARCILOGGER database package (PostgreSQL 16, TimescaleDB optional).

Most of the database is **derived** data that ingest and the worker rebuild from the raw spool
and the sealed vault; users, 5651 lookups, settings, persons and the audit log are not, and the
nightly backup protects them (:mod:`arcilogger_db.backup`, ADR-0009). The evidence path
(``arcilogger-core``) never imports this package.

Modules:

* :mod:`arcilogger_db.models` — SQLAlchemy 2 typed models of every table.
* :mod:`arcilogger_db.assignments` — device-person assignment history (who held a device when).
* :mod:`arcilogger_db.migrate` — programmatic Alembic (``upgrade``, ``current``, ``heads``).
* :mod:`arcilogger_db.engine` — engine and sessions from ``arcilogger.toml`` + secret store.
* :mod:`arcilogger_db.seed` — default settings (idempotent; no VLANs, departments or sources).
* :mod:`arcilogger_db.settings` — read and write the ``settings`` table.
* :mod:`arcilogger_db.rowcheck` — validate row values against column types before loading.
* :mod:`arcilogger_db.testing` / :mod:`arcilogger_db.pytest_plugin` — throwaway test databases
  (with the fictional example site of the tests).
* :mod:`arcilogger_db.backup` — scheduled ``pg_dump`` backups, retention and restore (ADR-0009).
* :mod:`arcilogger_db.cli` — the ``arcilogger-db`` command.
"""

__version__ = "0.1.0"
