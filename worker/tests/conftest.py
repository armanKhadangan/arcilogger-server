"""Worker test fixtures (helpers: :mod:`arcilogger_worker.testing`).

``smtp_server``: a real SMTP server on 127.0.0.1 for the alert e-mail tests
(:mod:`arcilogger_worker.alerts.smtptesting`); ``closed_port``: a port on 127.0.0.1 that refuses
connections for the whole test (:func:`arcilogger_core.testing.refused_port`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.testing import refused_port
from arcilogger_worker.alerts.smtptesting import FakeSmtp, SmtpTestServer
from arcilogger_worker.testing import Evidence


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path / "data")


@pytest.fixture
def evidence(db_session: Session, layout: Layout) -> Evidence:
    return Evidence(db_session, layout)


@pytest.fixture
def smtp_server() -> Iterator[FakeSmtp]:
    with SmtpTestServer() as fake:
        yield fake


@pytest.fixture
def closed_port() -> Iterator[int]:
    with refused_port() as port:
        yield port
