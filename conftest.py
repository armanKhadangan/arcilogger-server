"""Repository-wide pytest configuration.

Registers the database fixtures (``pg_dsn``, ``db_engine``, ``db_session`` …) for the tests of
every package (see ``arcilogger_db.pytest_plugin`` and docs/MVP-CONTRACTS.md) and pytest's own
``pytester`` (used to test those fixtures).

Every test must leave the licence module as it found it: the trusted key set and the server
identity provider are module state that the test helpers swap (``arcilogger_core.license.testing``).
A test that leaves either swapped makes later tests in the same worker fail far from the cause, so
the guard below restores them and fails the test that leaked.

The services' command lines configure the root logger (``logging.basicConfig(..., force=True)``:
level INFO, a stderr and a log file handler) when a test runs them. Left so, every later test in
the same worker captures INFO records it never asked for, so the root logger is restored too.
"""

import logging
from collections.abc import Iterator

import pytest

from arcilogger_core.license import fingerprint, keys

pytest_plugins = ("arcilogger_db.pytest_plugin", "pytester")


@pytest.fixture(autouse=True)
def _licence_module_state_restored() -> Iterator[None]:
    trusted, provider = keys.TRUSTED_KEYS, fingerprint.IDENTITY_PROVIDER
    yield
    leaked = [
        name
        for name, before, after in (
            ("keys.TRUSTED_KEYS", trusted, keys.TRUSTED_KEYS),
            ("fingerprint.IDENTITY_PROVIDER", provider, fingerprint.IDENTITY_PROVIDER),
        )
        if after is not before
    ]
    keys.TRUSTED_KEYS = trusted  # type: ignore[misc]  # tests swap the Final set
    fingerprint.IDENTITY_PROVIDER = provider
    fingerprint.clear_cache()
    if leaked:
        pytest.fail(f"the test left {', '.join(leaked)} swapped", pytrace=False)


@pytest.fixture(autouse=True)
def _root_logger_restored() -> Iterator[None]:
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield
    root.setLevel(level)
    added = [handler for handler in root.handlers if handler not in handlers]
    # Only what basicConfig adds: pytest's own capture handlers are subclasses it manages.
    for handler in added:
        if type(handler) in (logging.StreamHandler, logging.FileHandler):
            root.removeHandler(handler)
            handler.close()
