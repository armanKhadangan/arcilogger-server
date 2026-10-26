"""Repository-wide pytest configuration.

Registers the database fixtures (``pg_dsn``, ``db_engine``, ``db_session`` …) for the tests of
every package (see ``arcilogger_db.pytest_plugin`` and docs/MVP-CONTRACTS.md) and pytest's own
``pytester`` (used to test those fixtures).

Every test must leave the licence module as it found it: the trusted key set and the server
identity provider are module state that the test helpers swap (``arcilogger_core.license.testing``).
A test that leaves either swapped makes later tests in the same worker fail far from the cause, so
the guard below restores them and fails the test that leaked.
"""

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
