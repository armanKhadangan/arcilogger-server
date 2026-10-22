"""The test-database support itself: skip/fail rules, unique names, isolation, cleanup."""

from __future__ import annotations

import subprocess
import sys

import pytest
from sqlalchemy import Engine, text

from arcilogger_db import testing
from arcilogger_db.testing import TestDatabaseError, TestServer

PLUGIN_TEST = """
def test_needs_db(db_session):
    assert db_session is not None
"""


def test_environment_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(testing.ENV_DSN, raising=False)
    assert testing.admin_dsn_from_env() is None
    monkeypatch.setenv(testing.ENV_DSN, "  ")
    assert testing.admin_dsn_from_env() is None
    monkeypatch.setenv(testing.ENV_DSN, "postgresql://u:p@h/postgres")
    assert testing.admin_dsn_from_env() == "postgresql://u:p@h/postgres"
    for value, expected in (("1", True), ("true", True), ("", False), ("0", False)):
        monkeypatch.setenv(testing.ENV_REQUIRE, value)
        assert testing.database_required() is expected
    monkeypatch.setenv(testing.ENV_TIMESCALE, "off")
    assert testing.timescale_mode_from_env() == "off"
    monkeypatch.setenv(testing.ENV_TIMESCALE, "maybe")
    with pytest.raises(TestDatabaseError):
        testing.timescale_mode_from_env()


def test_database_tests_skip_without_dsn(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(testing.ENV_DSN, raising=False)
    monkeypatch.delenv(testing.ENV_REQUIRE, raising=False)
    pytester.makepyfile(test_db=PLUGIN_TEST)
    result = pytester.runpytest("-p", "arcilogger_db.pytest_plugin", "-rs")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*ARCILOGGER_TEST_PG*"])


def test_database_tests_fail_without_dsn_when_required(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(testing.ENV_DSN, raising=False)
    monkeypatch.setenv(testing.ENV_REQUIRE, "1")
    pytester.makepyfile(test_db=PLUGIN_TEST)
    result = pytester.runpytest("-p", "arcilogger_db.pytest_plugin")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*ARCILOGGER_REQUIRE_DB=1 but ARCILOGGER_TEST_PG is not set*"])


def test_unreachable_server_fails_loudly(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(testing.ENV_DSN, "postgresql://nobody:x@127.0.0.1:1/postgres")
    monkeypatch.delenv(testing.ENV_REQUIRE, raising=False)
    pytester.makepyfile(test_db=PLUGIN_TEST)
    result = pytester.runpytest("-p", "arcilogger_db.pytest_plugin")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*server is not usable*"])


def test_names_are_unique_and_recognisable() -> None:
    names = {TestServer.new_name("t") for _ in range(50)}
    assert len(names) == 50
    assert all(testing._NAME.match(name) for name in names)
    assert all(len(name) <= 63 for name in names)


def test_each_test_gets_its_own_database(db_engine: Engine, pg_template: str) -> None:
    with db_engine.begin() as connection:
        name = connection.execute(text("SELECT current_database()")).scalar_one()
        connection.execute(text("INSERT INTO departments (name) VALUES ('Sadece bu test')"))
    assert name != pg_template
    assert name.startswith("arci_t_")


def test_previous_test_left_nothing_behind(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        count = connection.execute(
            text("SELECT count(*) FROM departments WHERE name = 'Sadece bu test'")
        ).scalar_one()
    assert count == 0


@pytest.mark.skipif(sys.platform == "win32", reason="stale detection is POSIX-only")
def test_stale_databases_of_dead_processes_are_dropped(pg_server: TestServer) -> None:
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    stale = f"arci_t_{testing._HOST_TAG}_{dead.pid}_1_abcdef"
    alive = pg_server.new_name("t")
    pg_server.create_database(stale)
    pg_server.create_database(alive)
    try:
        dropped = pg_server.drop_stale()
        assert stale in dropped
        assert alive not in dropped
    finally:
        pg_server.drop_database(stale)
        pg_server.drop_database(alive)
