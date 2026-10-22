"""Health endpoint; no interactive docs and no public schema."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from arcilogger_api import __version__
from arcilogger_api.app import create_app


def test_health_reports_ok_with_site_time() -> None:
    response = TestClient(create_app()).get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["product"] == "ARCILOGGER"
    assert body["version"] == __version__
    assert body["tz"] == "Europe/Istanbul"
    assert datetime.fromisoformat(body["time"]).utcoffset() == timedelta(hours=3)


@pytest.mark.parametrize(
    "path", ["/api/docs", "/api/openapi.json", "/api/redoc", "/docs", "/openapi.json", "/redoc"]
)
def test_no_swagger_page_and_no_public_schema(path: str) -> None:
    """FastAPI's Swagger page loads its scripts from a CDN (spec 0.4: nothing external), and an
    unauthenticated schema would describe every endpoint: neither is served."""
    response = TestClient(create_app()).get(path)
    assert response.status_code == 404
    assert "cdn.jsdelivr.net" not in response.text


def test_the_schema_is_still_built_in_process() -> None:
    """The web contract export and the route tests use ``create_app().openapi()``."""
    assert "/api/v1/health" in create_app().openapi()["paths"]
