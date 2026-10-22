"""No network call from the database layer of the licensing (test A-T20, the part of area B).

``load_status`` and ``install_license`` (``arcilogger_db.licensing``, licensing contract 5.4)
must complete with every Python socket connection refused. The module is skipped until the
database layer exists in this checkout. (The PostgreSQL connection of the test session goes
through libpq, not Python sockets, so refusing them does not affect it.)
"""

from __future__ import annotations

import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.license.testing import fixed_identity, make_license, trusted_test_keys
from arcilogger_core.license.trusted_time import clear_cache

licensing: Any = pytest.importorskip("arcilogger_db.licensing")

pytestmark = pytest.mark.license_live


def test_load_status_and_install_license_make_no_network_call(
    db_session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config.model_validate(
        {"data_dir": str(tmp_path / "data"), "secrets_dir": str(tmp_path), "site_name": "Örnek"}
    )
    layout = Layout(config.data_dir)
    attempts: list[str] = []

    def refused(*args: object, **kwargs: object) -> None:
        attempts.append(repr(args)[:80])
        raise AssertionError("network call attempted")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(socket, "getaddrinfo", refused)
    audits: list[str] = []

    def audit(action: str, target: str | None, details: Mapping[str, Any]) -> None:
        audits.append(action)

    clear_cache()
    with fixed_identity() as server_id, trusted_test_keys():
        status = licensing.load_status(db_session, config=config, layout=layout)
        assert status.state in ("trial", "trial_expired")
        data = make_license(server_ids=[server_id], modules=("core_5651", "usage_analytics"))
        installed = licensing.install_license(
            db_session,
            data,
            config=config,
            layout=layout,
            actor="os:test",
            source="cli",
            audit=audit,
        )
        assert installed.state == "licensed"
        assert licensing.load_status(db_session, config=config, layout=layout).state == "licensed"
    assert "license.install" in audits
    assert attempts == []
