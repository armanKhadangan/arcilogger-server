"""Access rules of every evidence, sealing and credit endpoint (spec 13): each route is
classified, refused without a session, before the second factor and without CSRF, and the
role matrix holds (``docs/roles.md``: the auditor reads, verifies and downloads, but does not
create packages or seal)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response

from arcilogger_api.security import CSRF_COOKIE, CSRF_HEADER
from arcilogger_api.testing import LoggedIn
from arcilogger_db.models import ROLES

Login = Callable[..., LoggedIn]
SEAL_ROLES = frozenset({"admin", "auditor"})
ADMIN_ONLY = frozenset({"admin"})
OWNED_PREFIXES = ("/api/v1/evidence", "/api/v1/seals", "/api/v1/credits")
DAY = "2026-09-10"


@dataclass(frozen=True)
class Rule:
    roles: frozenset[str]


RULES: dict[tuple[str, str], Rule] = {
    ("POST", "/api/v1/evidence"): Rule(ADMIN_ONLY),
    ("GET", "/api/v1/evidence"): Rule(SEAL_ROLES),
    ("GET", "/api/v1/evidence/{package_id}"): Rule(SEAL_ROLES),
    ("GET", "/api/v1/evidence/{package_id}/download"): Rule(SEAL_ROLES),
    ("GET", "/api/v1/seals/calendar"): Rule(SEAL_ROLES),
    ("GET", "/api/v1/seals/{day}"): Rule(SEAL_ROLES),
    ("GET", "/api/v1/seals/{day}/manifest"): Rule(SEAL_ROLES),
    ("GET", "/api/v1/seals/{day}/token"): Rule(SEAL_ROLES),
    ("POST", "/api/v1/seals/{day}/verify"): Rule(SEAL_ROLES),
    ("POST", "/api/v1/seals/{day}/seal"): Rule(ADMIN_ONLY),
    ("GET", "/api/v1/credits"): Rule(SEAL_ROLES),
    ("POST", "/api/v1/credits/query"): Rule(ADMIN_ONLY),
}
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/v1/evidence"): {"from": DAY, "to": DAY},
}


def _routes(app: FastAPI) -> list[tuple[str, str]]:
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith(OWNED_PREFIXES)
        for method in operations
    )


def _call(client: TestClient, method: str, path: str) -> Response:
    url = path.replace("{package_id}", "1").replace("{day}", DAY)
    if method == "GET":
        return client.get(url)
    return client.request(method, url, json=BODIES.get((method, path), {}))


def _code(response: Response) -> tuple[int, str | None]:
    error = response.json().get("error", {}) if response.content else {}
    return response.status_code, error.get("code")


def test_every_route_is_classified(app: FastAPI) -> None:
    assert _routes(app) == sorted(RULES)


def test_no_session_no_second_factor_no_csrf(client: TestClient, login_as: Login) -> None:
    client.get("/api/v1/health")
    client.headers[CSRF_HEADER] = client.cookies[CSRF_COOKIE]
    for method, path in RULES:
        assert _code(_call(client, method, path)) == (401, "not_authenticated"), (method, path)
    login_as(client, "admin", mfa_passed=False)
    for method, path in RULES:
        assert _code(_call(client, method, path)) == (401, "mfa_required"), (method, path)
    login_as(client, "admin")
    del client.headers[CSRF_HEADER]
    for method, path in RULES:
        if method != "GET":
            assert _code(_call(client, method, path)) == (403, "csrf_failed"), (method, path)


@pytest.mark.parametrize("role", ROLES)
def test_role_matrix(client: TestClient, login_as: Login, role: str) -> None:
    login_as(client, role)
    for (method, path), rule in RULES.items():
        status, code = _code(_call(client, method, path))
        if role in rule.roles:
            assert code not in ("forbidden", "not_authenticated"), (role, method, path, code)
        else:
            assert (status, code) == (403, "forbidden"), (role, method, path)


def test_password_change_blocks_everything(client: TestClient, login_as: Login) -> None:
    login_as(client, "admin", must_change_password=True)
    for method, path in RULES:
        assert _code(_call(client, method, path)) == (403, "password_change_required")
