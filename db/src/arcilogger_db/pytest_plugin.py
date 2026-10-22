"""pytest fixtures for database tests (registered by the repository's root ``conftest.py``).

Fixtures:

``pg_server`` (session)
    :class:`~arcilogger_db.testing.TestServer` for ``ARCILOGGER_TEST_PG``; skips (or fails with
    ``ARCILOGGER_REQUIRE_DB=1``) when the variable is not set.
``pg_template`` (session)
    Name of the migrated + seeded template database of this test session, with the fictional
    example site (:func:`arcilogger_db.testing.seed_example_network`).
``pg_dsn`` (function)
    DSN (``postgresql://…``) of a **fresh database for this test**, copied from the template.
``db_engine`` (function)
    SQLAlchemy engine on that database (disposed afterwards).
``db_session_factory`` / ``db_session`` (function)
    Session factory and one open session (closed afterwards; uncommitted work is rolled back).

Tests may commit freely: every test has its own database.

**Licence default** (licensing contract 5.5): an autouse fixture makes
:func:`arcilogger_db.licensing.load_status` answer ``all_modules_status()`` (state ``trial``,
every module on, no limits) for every test, unless the test carries the marker
``license_live`` (real evaluation). Subprocesses cannot see it; they run the real ``trial`` of a
fresh installation. The template database has no licence installation row and no
``license.trial_start`` audit entry (the seed of ``arcilogger-db upgrade`` writes both): every
test starts with an empty audit log, and a licence test's trial starts at its own clock.

This module is a test helper: it is not part of the wheel (``db/pyproject.toml``).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from arcilogger_db.engine import engine_from_url, session_factory
from arcilogger_db.testing import (
    ENV_DSN,
    ENV_REQUIRE,
    SKIP_REASON,
    TestDatabaseError,
    TestServer,
    admin_dsn_from_env,
    database_required,
    timescale_mode_from_env,
)


@pytest.fixture(scope="session")
def pg_server() -> Iterator[TestServer]:
    dsn = admin_dsn_from_env()
    if dsn is None:
        if database_required():
            pytest.fail(f"{ENV_REQUIRE}=1 but {ENV_DSN} is not set", pytrace=False)
        pytest.skip(SKIP_REASON)
    server = TestServer(dsn)
    try:
        server.check()
    except TestDatabaseError as exc:
        pytest.fail(f"{ENV_DSN} is set but the server is not usable: {exc}", pytrace=False)
    server.drop_stale()
    yield server
    server.dispose()


@pytest.fixture(autouse=True)
def _license_default(request: pytest.FixtureRequest) -> Iterator[None]:
    """Every module on (``all_modules_status``) unless the test is marked ``license_live``."""
    if request.node.get_closest_marker("license_live") is not None:
        yield
        return
    try:
        from arcilogger_core.license.testing import all_modules_status, override_status
    except ImportError:  # a test that blocks the licence package (containment tests)
        yield
        return
    with override_status(all_modules_status()):
        yield


#: Removes what the seed's licence step wrote into the template (tables are append-only).
_TEMPLATE_LICENSE_CLEANUP = (
    "ALTER TABLE audit_log DISABLE TRIGGER USER",
    "ALTER TABLE license_installation DISABLE TRIGGER USER",
    "DELETE FROM audit_log WHERE action = 'license.trial_start'",
    "DELETE FROM license_installation",
    "ALTER TABLE license_installation ENABLE TRIGGER USER",
    "ALTER TABLE audit_log ENABLE TRIGGER USER",
    "SELECT setval(pg_get_serial_sequence('audit_log', 'id'), "
    "coalesce((SELECT max(id) FROM audit_log), 1), (SELECT count(*) > 0 FROM audit_log))",
)


def _without_licence_installation(server: TestServer, name: str) -> None:
    """The template as it was before the licence step of the seed (see the module text)."""
    with server._admin.connect() as connection:
        connection.execute(text(f'ALTER DATABASE "{name}" WITH ALLOW_CONNECTIONS true'))
    engine = engine_from_url(server.url_for(name), application="tests-migrate", poolclass=NullPool)
    try:
        with engine.begin() as connection:
            for statement in _TEMPLATE_LICENSE_CLEANUP:
                connection.execute(text(statement))
    finally:
        engine.dispose()
        with server._admin.connect() as connection:
            connection.execute(text(f'ALTER DATABASE "{name}" WITH ALLOW_CONNECTIONS false'))
        server._terminate(name)


@pytest.fixture(scope="session")
def pg_template(pg_server: TestServer) -> Iterator[str]:
    name = pg_server.create_template(timescale=timescale_mode_from_env())
    try:
        _without_licence_installation(pg_server, name)
    except BaseException:
        pg_server.drop_database(name)
        raise
    yield name
    pg_server.drop_database(name)


@pytest.fixture
def pg_dsn(pg_server: TestServer, pg_template: str) -> Iterator[str]:
    with pg_server.database(pg_template) as name:
        yield pg_server.dsn_for(name)


@pytest.fixture
def db_engine(pg_dsn: str) -> Iterator[Engine]:
    engine = engine_from_url(pg_dsn, pool_size=5, max_overflow=10)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return session_factory(db_engine)


@pytest.fixture
def db_session(db_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with db_session_factory() as session:
        yield session
