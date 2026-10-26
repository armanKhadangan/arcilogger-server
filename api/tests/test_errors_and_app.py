"""Error format, router wiring, response headers and the ``arcilogger-api`` command."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from arcilogger_api import __version__, cli
from arcilogger_api.app import create_app
from arcilogger_api.deps import DbSession
from arcilogger_api.errors import MESSAGES, ApiError
from arcilogger_api.routers import discover

EXPECTED_ROUTERS = {
    "/auth": "auth",
    "/users": "users",
    "/audit": "audit",
    "/lookup": "lookup",
    "/lookups": "lookup",
    "/raw": "raw",
    "/evidence": "evidence",
    "/seals": "seals",
    "/credits": "seals",  # Kamu SM credit widget of the sealing page (spec 16 "/credits")
    "/devices": "devices",
    # The "Cihazlar ve Kişiler" page (spec 9.2, docs/MVP-CONTRACTS.md section 11).
    "/persons": "devices",
    "/departments": "devices",
    "/vlans": "devices",
    "/imports": "devices",
    "/sources": "sources",
    # Kullanım (spec 10, docs/MVP-CONTRACTS.md section 12).
    "/usage": "usage",
    # The "Güvenlik" page and the "Panel" (docs/MVP-CONTRACTS.md section 14).
    "/alerts": "alerts",
    "/dashboard": "dashboard",
    # Scheduled usage reports (group 2 area R, docs/MVP-CONTRACTS.md section 13).
    "/reports": "reports",
    # The licence page and upload (licensing contract 6.1-6.3).
    "/license": "license",
    # Ayarlar: the browser administration (ADR-0014, docs/MVP-CONTRACTS.md section 16).
    "/admin": "admin",
    "/admin/requests": "admin",
    "/admin/actions": "admin",
    "/settings": "settings",
    # Arama (ADR-0015, docs/MVP-CONTRACTS.md section 17).
    "/search": "search",
}


class Body(BaseModel):
    password: str
    count: int


def _app_with_failures() -> FastAPI:
    app = create_app()
    router = APIRouter(prefix="/api/v1/_fail")

    @router.get("/api-error")
    def api_error() -> None:
        raise ApiError(409, "conflict")

    @router.get("/custom")
    def custom() -> None:
        raise ApiError(400, "lookup_reason_missing", "Gerekçe gerekli.", "A reason is required.")

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internals")

    @router.get("/db-down")
    def db_down() -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    @router.get("/body")
    def body(payload: Body) -> None:  # GET with body → validation error is enough
        return None

    app.include_router(router)
    return app


@pytest.fixture
def failing() -> TestClient:
    return TestClient(_app_with_failures(), raise_server_exceptions=False)


def test_api_error_and_custom_messages(failing: TestClient) -> None:
    response = failing.get("/api/v1/_fail/api-error")
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "conflict",
            "message_tr": MESSAGES["conflict"][0],
            "message_en": MESSAGES["conflict"][1],
        }
    }
    assert failing.get("/api/v1/_fail/custom").json()["error"] == {
        "code": "lookup_reason_missing",
        "message_tr": "Gerekçe gerekli.",
        "message_en": "A reason is required.",
    }


def test_unknown_path_and_method(failing: TestClient) -> None:
    response = failing.get("/api/v1/nope")
    assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")
    failing.cookies.set("arci_csrf", "a")
    response = failing.delete("/api/v1/health", headers={"X-CSRF-Token": "a"})
    assert (response.status_code, response.json()["error"]["code"]) == (405, "method_not_allowed")


def test_unexpected_errors_reveal_nothing(failing: TestClient) -> None:
    response = failing.get("/api/v1/_fail/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret internals" not in response.text
    response = failing.get("/api/v1/_fail/db-down")
    assert (response.status_code, response.json()["error"]["code"]) == (503, "unavailable")


def test_validation_errors_never_echo_input(failing: TestClient) -> None:
    response = failing.request(
        "GET", "/api/v1/_fail/body", json={"password": "Gizli-Parola-123", "count": "many"}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["body", "count"]
    assert "Gizli-Parola-123" not in response.text
    assert "many" not in response.text


def test_feature_routers_are_discovered_and_mounted() -> None:
    routers = discover()
    assert {r.prefix: r.tags for r in routers} == {p: [t] for p, t in EXPECTED_ROUTERS.items()}
    assert len({r.prefix for r in routers}) == len(routers)
    assert "/api/v1/health" in create_app().openapi()["paths"]


def test_api_responses_are_not_cacheable() -> None:
    response = TestClient(create_app()).get("/api/v1/health")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_database_is_not_needed_for_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCILOGGER_CONFIG", "/nonexistent/arcilogger.toml")
    assert TestClient(create_app()).get("/api/v1/health").status_code == 200


def test_missing_configuration_is_503_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCILOGGER_CONFIG", "/nonexistent/arcilogger.toml")
    app = create_app()

    @app.get("/api/v1/_needs_db")
    def needs_db(db: DbSession) -> None:
        return None

    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/_needs_db")
    assert (response.status_code, response.json()["error"]["code"]) == (503, "unavailable")


def test_serve_uses_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(cli, "_run_server", lambda app, **options: seen.update(options, app=app))
    config = tmp_path / "arcilogger.toml"
    config.write_text(
        f'data_dir = "{(tmp_path / "d").as_posix()}"\n[api]\nport = 8100\n', encoding="utf-8"
    )
    assert cli.main(["serve", "--config", str(config)]) == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 8100
    assert seen["reload"] is False
    assert seen["proxy_headers"] is True
    assert seen["forwarded_allow_ips"] == "127.0.0.1"
    assert isinstance(seen["app"], FastAPI)
    assert cli.main(["--config", str(config), "serve", "--port", "9000"]) == 0
    assert seen["port"] == 9000
    assert seen["app"].state.arci.config_path == config  # reloaded, the admin folder beside it
    assert cli.main(["serve", "--config", str(tmp_path / "missing.toml")]) == 2
    # A configuration that moves the secret store away from its own folder is refused.
    moved = tmp_path / "moved.toml"
    moved.write_text(
        f'data_dir = "{(tmp_path / "d").as_posix()}"\nsecrets_dir = "{tmp_path.as_posix()}/x"\n',
        encoding="utf-8",
    )
    assert cli.main(["serve", "--config", str(moved)]) == 2
    assert "secrets_dir" in capsys.readouterr().err
    assert cli.main(["version"]) == 0
    assert f"ARCILOGGER api {__version__}" in capsys.readouterr().out
    assert cli.main([]) == 2


def test_fastapi_telemetry_is_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    app = create_app()
    native = getattr(app, "_native_telemetry", None)
    if native is not None:  # FastAPI >= 0.143
        assert native.enabled() is False
    assert TestClient(app).get("/api/v1/health").status_code == 200
