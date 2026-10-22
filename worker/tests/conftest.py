"""Worker test fixtures (helpers: :mod:`arcilogger_worker.testing`).

``smtp_server``: a real SMTP server on 127.0.0.1 for the alert e-mail tests
(:mod:`arcilogger_worker.alerts.smtptesting`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
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
