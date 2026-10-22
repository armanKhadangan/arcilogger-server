"""Fixtures of the ingest tests (helpers: :mod:`arcilogger_ingest.testing`)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from arcilogger_core.immutability import is_locked, unlock
from arcilogger_ingest.testing import TcpProxy, World, proxy_for


@pytest.fixture(autouse=True)
def _unlock_after_test(tmp_path: Path) -> Iterator[None]:
    """Sealed files are made immutable; unlock them so pytest can clean its temp folders."""
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Data directory, configuration (fortigate, core-r1, network ``ap``) and clock at
    2026-09-23 18:00 Europe/Istanbul."""
    return World.create(tmp_path)


@pytest.fixture
def proxy(pg_dsn: str) -> Iterator[TcpProxy]:
    with proxy_for(pg_dsn) as running:
        yield running
