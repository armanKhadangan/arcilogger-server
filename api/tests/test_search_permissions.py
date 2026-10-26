"""Who may use Arama (contract 2.18, 3.4): every endpoint with every role, the KVKK notice, the
password change, the export's re-authentication, and no licence state ever locks it (5651
Temel, ASSUMPTIONS C-2)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.search.testing import sign_in
from arcilogger_api.testing import LoggedIn
from arcilogger_core.license.testing import base_only_status, make_license, override_status
from arcilogger_core.timeutil import utc_now

V = "/api/v1/search"
NOW = datetime.now(UTC)
RANGE = {"from": (NOW - timedelta(hours=1)).isoformat(), "to": NOW.isoformat()}

#: (method, path, body or query, needs the KVKK notice, the extra permission it needs).
ENDPOINTS: list[tuple[str, str, dict[str, Any] | None, bool, str | None]] = [
    ("POST", V, {"q": "", **RANGE}, True, None),
    ("POST", f"{V}/page", {"search_id": "x", "cursor": "y"}, True, None),
    ("POST", f"{V}/stats", {"search_id": "x"}, True, None),
    ("POST", f"{V}/parse", {"q": "srcip:172.20.41.5"}, False, None),
    ("GET", f"{V}/fields", None, False, None),
    ("GET", f"{V}/event", {"table": "fg_web", "time": NOW.isoformat(), "id": 1}, True, None),
    ("POST", f"{V}/refs", {"search_id": "x"}, True, "evidence.create"),
    ("POST", f"{V}/export", {"search_id": "x", "format": "csv"}, True, "search.export"),
    ("GET", f"{V}/saved", None, True, None),
    ("POST", f"{V}/saved", {"name": "a", "q": "", "range": {"preset": "1h"}}, True, None),
    ("PUT", f"{V}/saved/1", {"name": "a", "q": "", "range": {"preset": "1h"}}, True, None),
    ("DELETE", f"{V}/saved/1", None, True, None),
]
ROLE_EXTRA = {"admin": {"evidence.create", "search.export"}, "auditor": set()}


def call(client: TestClient, method: str, path: str, body: dict[str, Any] | None) -> Any:
    if method == "GET":
        return client.get(path, params=body)
    if method == "DELETE":
        return client.delete(path)
    return client.request(method, path, json=body)


def code(response: Any) -> str | None:
    try:
        return response.json()["error"]["code"]  # type: ignore[no-any-return]
    except (ValueError, KeyError, TypeError):
        return None


@pytest.fixture
def recent(reauthenticate: Callable[[LoggedIn], None]) -> Callable[[LoggedIn], LoggedIn]:
    def mark(who: LoggedIn) -> LoggedIn:
        reauthenticate(who)
        return who

    return mark


@pytest.mark.parametrize("role", ["admin", "auditor"])
def test_administrators_and_auditors(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    recent: Callable[[LoggedIn], LoggedIn],
    role: str,
) -> None:
    recent(sign_in(client, app_session_factory, role))
    for method, path, body, _, extra in ENDPOINTS:
        response = call(client, method, path, body)
        allowed = extra is None or extra in ROLE_EXTRA[role]
        if allowed:
            assert response.status_code != 403, (role, method, path, response.text)
            assert response.status_code < 500, (role, method, path, response.text)
        else:
            assert response.status_code == 403, (role, method, path)
            assert code(response) == "forbidden"


@pytest.mark.parametrize("role", ["hr", "viewer"])
def test_other_roles_see_nothing(
    client: TestClient, app_session_factory: sessionmaker[Session], role: str
) -> None:
    sign_in(client, app_session_factory, role)
    for method, path, body, _, _ in ENDPOINTS:
        response = call(client, method, path, body)
        assert response.status_code == 403, (role, method, path)
        assert code(response) == "forbidden"


def test_the_kvkk_notice_comes_first(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    recent: Callable[[LoggedIn], LoggedIn],
) -> None:
    recent(sign_in(client, app_session_factory, notice=False))
    for method, path, body, notice, _ in ENDPOINTS:
        response = call(client, method, path, body)
        if notice:
            assert code(response) == "privacy_notice_required", (method, path)
        else:
            assert response.status_code == 200, (method, path, response.text)


def test_a_password_change_comes_first(
    client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    sign_in(client, app_session_factory, must_change_password=True)
    for method, path, body, _, _ in ENDPOINTS:
        assert code(call(client, method, path, body)) == "password_change_required", (method, path)


def test_the_export_needs_a_recent_re_authentication(
    client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    sign_in(client, app_session_factory)
    method, path, body, _, _ = next(e for e in ENDPOINTS if e[1].endswith("/export"))
    assert code(call(client, method, path, body)) == "reauth_required"


def test_without_a_session(client: TestClient) -> None:
    client.get("/api/v1/health")
    client.headers["X-CSRF-Token"] = client.cookies["arci_csrf"]
    for method, path, body, _, _ in ENDPOINTS:
        assert code(call(client, method, path, body)) == "not_authenticated", (method, path)


def test_no_endpoint_is_licence_gated_with_only_5651_temel(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    recent: Callable[[LoggedIn], LoggedIn],
) -> None:
    recent(sign_in(client, app_session_factory))
    with override_status(base_only_status()):
        for method, path, body, _, _ in ENDPOINTS:
            response = call(client, method, path, body)
            assert code(response) != "module_not_licensed", (method, path)
            assert response.status_code < 500, (method, path, response.text)


@pytest.mark.license_live
@pytest.mark.parametrize("state", ["trial_expired", "expired", "wrong_server"])
def test_no_licence_state_locks_a_search(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    recent: Callable[[LoggedIn], LoggedIn],
    licence: Any,
    state: str,
) -> None:
    recent(sign_in(client, app_session_factory))
    licence.trial_over()
    if state != "trial_expired":
        options: dict[str, Any] = {
            "server_ids": ["AAAA-BBBB-CCCC-DDDD"]
            if state == "wrong_server"
            else [licence.server_id]
        }
        if state == "expired":
            options["valid_until"] = utc_now().date() - timedelta(days=3)
        licence.store(make_license(**options))
    assert client.get("/api/v1/license").json()["status"] == state
    for method, path, body, _, _ in ENDPOINTS:
        response = call(client, method, path, body)
        assert code(response) != "module_not_licensed", (state, method, path)
        assert response.status_code < 500, (state, method, path, response.text)
    page = client.post(V, json={"q": "", **RANGE})
    assert page.status_code == 200, page.text
