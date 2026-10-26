"""Which routes a licence gates (licensing contract 1.3, 5.2, 6.4; tests B-T9, B-T10, B-T31,
A-T17b).

* Every route of the API is classified below — a paid module or 5651 Temel. A new route that is
  not in the table fails the test, so nothing is added without deciding whether a licence may
  lock it; the ``__arci_module__`` markers of the routers' dependencies must equal the table.
* With only 5651 Temel enabled, every gated route answers 403 ``module_not_licensed`` (before
  parameter validation) and no base route ever does.
* Containment: with the licence package unimportable, or its evaluation raising, the API still
  starts, every base route works, the gated ones are locked, ``GET /license`` answers 503
  ``license_unavailable``; importing the shared modules never loads the licence code.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import arcilogger_db
from arcilogger_api.auth.testing import ensure_secret_key
from arcilogger_api.licensing import MODULE_MARKER
from arcilogger_api.routers import discover
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.license import errors
from arcilogger_core.license.testing import base_only_status, override_status
from arcilogger_db import licensing as db_licensing
from arcilogger_db.license_guard import FALLBACK_MESSAGES

pytestmark = pytest.mark.license_live

Login = Callable[..., LoggedIn]
UA, UR, BI = "usage_analytics", "usage_reports", "bulk_import"
V = "/api/v1"

#: Every route (method, OpenAPI path) → the module that gates it, ``None`` for 5651 Temel.
ROUTES: dict[tuple[str, str], str | None] = {
    # Module usage_analytics: 12 routes
    ("GET", f"{V}/usage/company"): UA,
    ("GET", f"{V}/usage/subjects"): UA,
    ("GET", f"{V}/usage/subjects/{{key}}"): UA,
    ("GET", f"{V}/usage/sites"): UA,
    ("GET", f"{V}/usage/sites/{{domain}}"): UA,
    ("GET", f"{V}/usage/categories"): UA,
    ("GET", f"{V}/usage/categories/{{category}}"): UA,
    ("GET", f"{V}/usage/export.csv"): UA,
    ("GET", f"{V}/usage/export.xlsx"): UA,
    ("POST", f"{V}/usage/rebuild"): UA,
    ("GET", f"{V}/usage/rebuild/{{job_id}}"): UA,
    ("GET", f"{V}/usage/pseudonyms"): UA,
    # Module usage_reports: 9 routes
    ("GET", f"{V}/reports/schedules"): UR,
    ("POST", f"{V}/reports/schedules"): UR,
    ("PATCH", f"{V}/reports/schedules/{{schedule_id}}"): UR,
    ("DELETE", f"{V}/reports/schedules/{{schedule_id}}"): UR,
    ("POST", f"{V}/reports/schedules/{{schedule_id}}/run"): UR,
    ("GET", f"{V}/reports/runs"): UR,
    ("GET", f"{V}/reports/runs/{{run_id}}"): UR,
    ("GET", f"{V}/reports/runs/{{run_id}}/files/{{name}}"): UR,
    ("GET", f"{V}/reports/preview"): UR,
    # Module bulk_import: 2 routes
    ("POST", f"{V}/imports/{{kind}}"): BI,
    ("GET", f"{V}/imports/{{kind}}/template.csv"): BI,
    # 5651 Temel, never gated: 62 routes, the 3 of the licence and the 38 of the browser
    # administration (ADR-0014)
    ("GET", f"{V}/health"): None,
    ("GET", f"{V}/alerts"): None,
    ("GET", f"{V}/alerts/summary"): None,
    ("GET", f"{V}/alerts/{{alert_id}}"): None,
    ("POST", f"{V}/alerts/{{alert_id}}/state"): None,
    ("POST", f"{V}/alerts/{{alert_id}}/notes"): None,
    ("GET", f"{V}/audit"): None,
    ("GET", f"{V}/audit/verify"): None,
    ("POST", f"{V}/auth/login"): None,
    ("POST", f"{V}/auth/totp"): None,
    ("GET", f"{V}/auth/totp/setup"): None,
    ("POST", f"{V}/auth/totp/confirm"): None,
    ("POST", f"{V}/auth/recovery-codes"): None,
    ("POST", f"{V}/auth/password"): None,
    ("POST", f"{V}/auth/reauth"): None,
    ("POST", f"{V}/auth/logout"): None,
    ("GET", f"{V}/auth/me"): None,
    ("POST", f"{V}/auth/privacy-notice"): None,
    ("GET", f"{V}/auth/setup/status"): None,
    ("POST", f"{V}/auth/setup/admin"): None,
    ("GET", f"{V}/credits"): None,
    ("GET", f"{V}/dashboard/summary"): None,
    ("GET", f"{V}/departments"): None,
    ("POST", f"{V}/departments"): None,
    ("PATCH", f"{V}/departments/{{department_id}}"): None,
    ("GET", f"{V}/devices"): None,
    ("GET", f"{V}/devices/export.csv"): None,
    ("POST", f"{V}/devices/assign"): None,
    ("GET", f"{V}/devices/{{mac}}"): None,
    ("PATCH", f"{V}/devices/{{mac}}"): None,
    ("POST", f"{V}/evidence"): None,
    ("GET", f"{V}/evidence"): None,
    ("GET", f"{V}/evidence/{{package_id}}"): None,
    ("GET", f"{V}/evidence/{{package_id}}/download"): None,
    ("POST", f"{V}/lookup/5651"): None,
    ("GET", f"{V}/lookup/{{lookup_id}}"): None,
    ("GET", f"{V}/lookup/{{lookup_id}}/report.pdf"): None,
    ("GET", f"{V}/lookup/{{lookup_id}}/report.pdf.zd"): None,
    ("GET", f"{V}/lookups"): None,
    ("GET", f"{V}/persons"): None,
    ("POST", f"{V}/persons"): None,
    ("GET", f"{V}/persons/export.csv"): None,
    ("GET", f"{V}/persons/{{person_id}}"): None,
    ("PATCH", f"{V}/persons/{{person_id}}"): None,
    ("GET", f"{V}/raw"): None,
    ("GET", f"{V}/seals/calendar"): None,
    ("GET", f"{V}/seals/{{day}}"): None,
    ("GET", f"{V}/seals/{{day}}/manifest"): None,
    ("GET", f"{V}/seals/{{day}}/token"): None,
    ("POST", f"{V}/seals/{{day}}/verify"): None,
    ("POST", f"{V}/seals/{{day}}/seal"): None,
    ("GET", f"{V}/users"): None,
    ("POST", f"{V}/users"): None,
    ("GET", f"{V}/users/{{user_id}}"): None,
    ("PATCH", f"{V}/users/{{user_id}}"): None,
    ("POST", f"{V}/users/{{user_id}}/reset-password"): None,
    ("POST", f"{V}/users/{{user_id}}/reset-totp"): None,
    ("POST", f"{V}/users/{{user_id}}/unlock"): None,
    ("GET", f"{V}/vlans"): None,
    ("POST", f"{V}/vlans"): None,
    ("PATCH", f"{V}/vlans"): None,
    ("DELETE", f"{V}/vlans"): None,
    ("GET", f"{V}/license"): None,
    ("POST", f"{V}/license"): None,
    ("DELETE", f"{V}/license"): None,
    # Browser administration (ADR-0014): never licence-gated.
    ("GET", f"{V}/admin/config"): None,
    ("GET", f"{V}/admin/status"): None,
    ("POST", f"{V}/admin/config/general/check"): None,
    ("PUT", f"{V}/admin/config/general"): None,
    ("POST", f"{V}/admin/config/tsa/check"): None,
    ("PUT", f"{V}/admin/config/tsa"): None,
    ("POST", f"{V}/admin/config/tsa/roots"): None,
    ("POST", f"{V}/admin/config/tsa/legal-roots"): None,
    ("POST", f"{V}/admin/config/tsa/zamane"): None,
    ("POST", f"{V}/admin/uploads"): None,
    ("POST", f"{V}/admin/config/sources/check"): None,
    ("POST", f"{V}/admin/config/sources"): None,
    ("PUT", f"{V}/admin/config/sources/{{name}}"): None,
    ("DELETE", f"{V}/admin/config/sources/{{name}}"): None,
    ("POST", f"{V}/admin/config/smtp/check"): None,
    ("PUT", f"{V}/admin/config/smtp"): None,
    ("POST", f"{V}/admin/config/smtp/ca"): None,
    ("DELETE", f"{V}/admin/config/smtp/ca"): None,
    ("POST", f"{V}/admin/config/replica/check"): None,
    ("PUT", f"{V}/admin/config/replica"): None,
    ("POST", f"{V}/admin/config/retention/check"): None,
    ("PUT", f"{V}/admin/config/retention"): None,
    ("POST", f"{V}/admin/config/backup/check"): None,
    ("PUT", f"{V}/admin/config/backup"): None,
    ("POST", f"{V}/admin/actions/tsa-test"): None,
    ("POST", f"{V}/admin/actions/replica-test"): None,
    ("POST", f"{V}/admin/actions/test-email"): None,
    ("POST", f"{V}/admin/actions/lease-rebuild"): None,
    ("GET", f"{V}/admin/requests"): None,
    ("GET", f"{V}/admin/requests/{{request_id}}"): None,
    ("DELETE", f"{V}/admin/requests/{{request_id}}"): None,
    ("POST", f"{V}/admin/requests/{{request_id}}/revert"): None,
    ("GET", f"{V}/admin/config/versions"): None,
    ("GET", f"{V}/admin/diagnostics"): None,
    ("POST", f"{V}/credits/query"): None,
    # Canlı Akış (contract 2.14): 5651 Temel
    ("POST", f"{V}/live/streams"): None,
    ("GET", f"{V}/live/streams/{{stream_id}}"): None,
    ("DELETE", f"{V}/live/streams/{{stream_id}}"): None,
    ("GET", f"{V}/live/status"): None,
    ("GET", f"{V}/settings"): None,
    ("PATCH", f"{V}/settings"): None,
    ("POST", f"{V}/settings/{{key}}/reset"): None,
}
PLACEHOLDERS = {
    "{key}": "x",
    "{domain}": "example.com",
    "{category}": "news",
    "{job_id}": "1",
    "{schedule_id}": "1",
    "{run_id}": "1",
    "{name}": "rapor.pdf",
    "{kind}": "persons",
    "{alert_id}": "1",
    "{department_id}": "1",
    "{mac}": "00:00:5e:00:53:01",
    "{package_id}": "1",
    "{lookup_id}": "1",
    "{person_id}": "1",
    "{day}": "2026-10-12",
    "{user_id}": "1",
    "{request_id}": "0" * 32,
    "{stream_id}": "ornek-akis",
}
#: Called last: they end or change the session the other calls use.
LAST = [("POST", f"{V}/auth/password"), ("POST", f"{V}/auth/logout")]


def _url(path: str) -> str:
    for placeholder, value in PLACEHOLDERS.items():
        path = path.replace(placeholder, value)
    return path


def _call(client: TestClient, method: str, path: str) -> Any:
    """A well-formed request: no body (GET/DELETE) or an empty JSON object."""
    url = _url(path)
    if method in ("GET", "DELETE"):
        return client.request(method, url)
    return client.request(method, url, json={})


def _code(response: Any) -> str | None:
    if not response.content or "json" not in response.headers.get("content-type", ""):
        return None
    error = response.json().get("error") if isinstance(response.json(), dict) else None
    return error.get("code") if isinstance(error, dict) else None


def _ordered(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [row for row in rows if row not in LAST] + [row for row in LAST if row in rows]


# -- B-T9 every route is classified ---------------------------------------------------------------


def test_every_route_is_classified(app: FastAPI) -> None:
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    found = sorted((method.upper(), path) for path, ops in paths.items() for method in ops)
    assert found == sorted(ROUTES)
    gated = [key for key, module in ROUTES.items() if module is not None]
    assert len(gated) == 23
    assert len(ROUTES) - len(gated) == 107


def test_the_module_markers_equal_the_table() -> None:
    markers: dict[tuple[str, str], str | None] = {}
    for router in discover():
        for route in router.routes:
            assert isinstance(route, APIRoute)
            modules = {
                getattr(dependency.call, MODULE_MARKER)
                for dependency in route.dependant.dependencies
                if hasattr(dependency.call, MODULE_MARKER)
            }
            assert len(modules) <= 1, route.path
            path = (V + route.path).replace("{category:path}", "{category}")
            for method in route.methods or ():
                markers[(method, path)] = next(iter(modules), None)
    expected = {key: module for key, module in ROUTES.items() if key != ("GET", f"{V}/health")}
    assert markers == expected


# -- B-T10 locked routes, never-locked routes -----------------------------------------------------


@pytest.fixture
def base_only() -> Iterator[None]:
    with override_status(base_only_status()):
        yield


@pytest.fixture
def admin(client: TestClient, login_as: Login, licence: Any, api_config: Config) -> LoggedIn:
    ensure_secret_key(api_config.secrets_dir)  # TOTP enrolment needs the key
    who: LoggedIn = licence.admin(client, login_as, password="Sarı limon ağacı 1923")
    return who


def test_every_gated_route_is_refused_when_locked(
    app: FastAPI, client: TestClient, admin: LoggedIn, base_only: None
) -> None:
    for (method, path), module in ROUTES.items():
        if module is None:
            continue
        response = _call(client, method, path)
        assert response.status_code == 403, (method, path, response.text)
        body = response.json()["error"]
        assert body["code"] == "module_not_licensed", (method, path)
        assert body["details"] == {"module": module, "license_status": "trial_expired"}
        assert "{" not in body["message_tr"]
    anonymous = TestClient(app, base_url="http://testserver")
    anonymous.get(f"{V}/health")
    anonymous.headers["X-CSRF-Token"] = anonymous.cookies["arci_csrf"]
    for (method, path), module in ROUTES.items():
        if module is not None:
            assert _code(_call(anonymous, method, path)) == "not_authenticated", (method, path)


def test_no_base_route_is_ever_locked(client: TestClient, admin: LoggedIn, base_only: None) -> None:
    base = _ordered([key for key, module in ROUTES.items() if module is None])
    for method, path in base:
        response = _call(client, method, path)
        assert _code(response) != "module_not_licensed", (method, path)
        assert response.status_code != 500, (method, path, response.text)  # 501: seal now


def test_the_locked_message_names_the_module(
    client: TestClient, admin: LoggedIn, base_only: None
) -> None:
    body = client.get(f"{V}/reports/schedules").json()["error"]
    assert body["message_tr"] == (
        "Bu özellik lisansınızda yok: Zamanlanmış kullanım raporları. Ayrıntılar Ayarlar → "
        "Lisans sayfasında."
    )
    assert body["message_en"] == (
        "This feature is not part of your licence: Scheduled usage reports. See Settings → Licence."
    )


# -- B-T31 containment ----------------------------------------------------------------------------


def test_fallback_messages_equal_the_licence_texts() -> None:
    for code, texts in FALLBACK_MESSAGES.items():
        assert errors.MESSAGES[code] == texts


@pytest.fixture
def without_licence_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """The licence package cannot be imported (ImportError at every use)."""
    for name in [n for n in sys.modules if n.startswith("arcilogger_core.license")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "arcilogger_db.licensing", None)
    monkeypatch.delattr(arcilogger_db, "licensing", raising=False)


def _check_contained(app: FastAPI, client: TestClient) -> None:
    for (method, path), module in ROUTES.items():
        if module is None:
            continue
        response = _call(client, method, path)
        assert response.status_code == 403, (method, path)
        assert response.json()["error"]["details"] == {
            "module": module,
            "license_status": "unavailable",
        }
    assert _code(client.get(f"{V}/license")) == "license_unavailable"
    assert client.get(f"{V}/license").status_code == 503
    created = client.post(
        f"{V}/users", json={"username": "yeni1", "display_name": "Yeni", "role": "viewer"}
    )
    assert created.status_code == 201, created.text  # no limit while the subsystem is broken
    for method, path in _ordered([key for key, module in ROUTES.items() if module is None]):
        response = _call(client, method, path)
        assert _code(response) not in ("module_not_licensed", "internal_error"), (method, path)
        if path != f"{V}/license":
            assert response.status_code != 500, (method, path, response.text)  # 501: seal now


def test_the_api_without_the_licence_package(
    client: TestClient, admin: LoggedIn, app: FastAPI, without_licence_code: None
) -> None:
    _check_contained(app, client)


def test_the_api_when_the_evaluation_raises(
    client: TestClient, admin: LoggedIn, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(inputs: Any) -> Any:
        raise RuntimeError("a bug in evaluate")

    monkeypatch.setattr(db_licensing, "evaluate", broken)
    _check_contained(app, client)


def test_shared_modules_never_load_the_licence_code() -> None:
    """A-T17b: in a fresh interpreter."""
    check = (
        "import sys\n"
        "import arcilogger_db.cli, arcilogger_db.backup, arcilogger_db.seed\n"
        "import arcilogger_db.license_guard\n"
        "import arcilogger_worker.service, arcilogger_worker.cli\n"
        "import arcilogger_api.app, arcilogger_api.auth.admin_cli, arcilogger_api.cli\n"
        "arcilogger_api.app.create_app()\n"
        "loaded = [n for n in sys.modules if n.startswith('arcilogger_core.license')\n"
        "          or n == 'arcilogger_db.licensing']\n"
        "print(','.join(sorted(loaded)))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", check], capture_output=True, check=True, timeout=120
    )
    assert completed.stdout.decode().strip() == ""


def test_the_api_starts_without_the_licence_package() -> None:
    check = (
        "import sys\n"
        "sys.modules['arcilogger_core.license'] = None\n"
        "sys.modules['arcilogger_db.licensing'] = None\n"
        "from fastapi.testclient import TestClient\n"
        "from arcilogger_api.app import create_app\n"
        "app = create_app()\n"
        "print(TestClient(app).get('/api/v1/health').status_code)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", check], capture_output=True, check=True, timeout=120
    )
    assert completed.stdout.decode().strip() == "200"
