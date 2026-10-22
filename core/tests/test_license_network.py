"""No network call, ever (licensing contract: offline activation; test A-T20, core and issuer).

With every Python socket connection refused, the licence code and the issuer still do all their
work: trusted time over a sealed vault, verification, evaluation, request codes, and the issuer's
``keygen``/``issue``/``verify``. (``load_status``/``install_license`` of the database layer are
checked in ``test_license_network_db.py``.)
"""

from __future__ import annotations

import contextlib
import importlib.util
import socket
import sys
from datetime import UTC, date, datetime, timedelta
from functools import cache
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any

import pytest

from arcilogger_core.license import fingerprint, keys
from arcilogger_core.license.document import load_license
from arcilogger_core.license.request import make_request_code, new_request, parse_request_code
from arcilogger_core.license.state import Installation, LicenseInputs, evaluate
from arcilogger_core.license.testing import fixed_identity, make_license, trusted_test_keys
from arcilogger_core.license.trusted_time import clear_cache, file_lower_bounds, trusted_now

pytestmark = pytest.mark.license_live

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "license-issuer" / "arcilogger_license_issuer.py"


def refuse_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attempts: list[str] = []

    def refused(*args: object, **kwargs: object) -> None:
        attempts.append(repr(args)[:80])
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)
    monkeypatch.setattr(socket.socket, "sendto", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(socket, "getaddrinfo", refused)
    return attempts


@cache
def issuer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_license_issuer", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_licence_code_works_without_network(
    world: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 10, 12)
    world.busy_day(day)
    world.set_now(day + timedelta(days=1), 0, 30)
    assert [outcome.status for outcome in world.sealer().seal_due()] == ["sealed"]
    clear_cache()
    attempts = refuse_network(monkeypatch)

    trusted = trusted_now(world.layout, world.config, release_date=date(2026, 1, 1))
    assert trusted.tsa is not None  # the mock-stamped day verified offline
    assert file_lower_bounds(world.layout)["vault"]
    with fixed_identity() as server_id, trusted_test_keys():
        assert fingerprint.current_server_id() == server_id
        stored = make_license(server_ids=[server_id], modules=("core_5651", "usage_analytics"))
        assert load_license(stored).payload.server_ids == (server_id,)
        status = evaluate(
            LicenseInputs(
                installation=Installation("3f1c2b6e-8a51-4c43-9d0f-5b7e2a61c9d4", trusted.now),
                stored=stored,
                server_id=server_id,
                server_id_error=None,
                trusted=trusted,
                lower_bounds=file_lower_bounds(world.layout),
                users_used=1,
                log_sources_used=2,
                log_sources_unconfigured=0,
                version="0.1.0",
                release_date=date(2026, 10, 20),
            )
        )
        assert status.state == "licensed"
        request = new_request(
            fingerprint.current_identity(),
            installation_id="3f1c2b6e-8a51-4c43-9d0f-5b7e2a61c9d4",
            organization="Örnek A.Ş.",
            now=datetime.now(UTC),
        )
        assert parse_request_code(make_request_code(request)) == request
    with contextlib.suppress(fingerprint.FingerprintError):  # a container without a machine id
        fingerprint.read_identity()
    clear_cache()
    assert attempts == []


def test_the_issuer_works_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    attempts = refuse_network(monkeypatch)
    main = issuer().main
    passphrase = tmp_path / "passphrase.txt"
    passphrase.write_text("a long offline passphrase\n", encoding="utf-8")
    keys_dir = tmp_path / "keys"
    assert main(["keygen", "--key-id", "arci-net1", "--out", str(keys_dir),
                 "--passphrase-file", str(passphrase)]) == 0  # fmt: skip
    with fixed_identity() as server_id:
        code = make_request_code(
            new_request(
                fingerprint.current_identity(),
                installation_id="3f1c2b6e-8a51-4c43-9d0f-5b7e2a61c9d4",
                organization="Örnek A.Ş.",
                now=datetime.now(UTC),
            )
        )
    lic = tmp_path / "net.lic"
    assert main([
        "issue", "--key", str(keys_dir / "arci-net1.private.pem"), "--request", code,
        "--licensee", "Örnek A.Ş.", "--modules", "usage_analytics", "--max-sources", "5",
        "--max-users", "5", "--maintenance-until", "2027-10-20",
        "--registry", str(tmp_path / "registry.csv"), "--out", str(lic),
        "--passphrase-file", str(passphrase),
    ]) == 0  # fmt: skip
    public = (keys_dir / "arci-net1.public.txt").read_text(encoding="utf-8").split('"')[3]
    assert main(["verify", str(lic), "--public-key", public, "--server-id", server_id]) == 0
    monkeypatch.setattr(keys, "TRUSTED_KEYS", MappingProxyType({"arci-net1": public}))
    assert main(["verify", str(lic), "--committed-keys", "--at", "2027-10-20"]) == 0
    assert load_license(lic.read_bytes()).payload.server_ids == (server_id,)
    capsys.readouterr()
    assert attempts == []
