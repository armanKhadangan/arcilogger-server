"""Repository-wide pytest configuration.

Registers the database fixtures (``pg_dsn``, ``db_engine``, ``db_session`` …) for the tests of
every package (see ``arcilogger_db.pytest_plugin`` and docs/MVP-CONTRACTS.md) and pytest's own
``pytester`` (used to test those fixtures).
"""

pytest_plugins = ("arcilogger_db.pytest_plugin", "pytester")
