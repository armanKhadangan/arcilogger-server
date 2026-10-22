"""Roles and permissions (spec 13), sessions (idle/absolute expiry, MFA) and CSRF."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.security import (
    ALL_PERMISSIONS,
    BACKGROUND_HEADER,
    CSRF_COOKIE,
    CSRF_HEADER,
    PERMISSIONS,
    ROLE_LABELS,
    ROLE_PERMISSIONS,
    SESSION_COOKIE,
    CurrentUser,
    CurrentUserDep,
    PendingMfaUserDep,
    hash_token,
    permissions_for,
    require,
)
from arcilogger_api.testing import LoggedIn
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import ROLES, User, UserSession
from arcilogger_db.settings import set_setting

Login = Callable[..., LoggedIn]

# The spec 13 table, transcribed column by column (✔ cells), independently of security.py.
SPEC_TABLE: dict[str, dict[str, set[str]]] = {
    "Panel": {
        "dashboard.view": {"admin", "auditor", "viewer"},
        "dashboard.usage": {"admin", "auditor", "hr", "viewer"},  # hr: "✔ (usage only)"
    },
    "Arama": {"search.read": {"admin", "auditor"}, "search.export": {"admin"}},  # auditor: read
    "5651 Sorgu": {"lookup.run": {"admin", "auditor"}},
    "Kişi raporları": {"usage.person": {"admin", "hr"}},
    "Anonim raporlar": {"usage.anonymous": {"admin", "auditor", "hr", "viewer"}},
    "Mühür/Kanıt": {  # auditor: "✔ (verify/download)"
        "seal.read": {"admin", "auditor"},
        "seal.verify": {"admin", "auditor"},
        "seal.manage": {"admin"},
        "evidence.read": {"admin", "auditor"},
        "evidence.download": {"admin", "auditor"},
        "evidence.create": {"admin"},
    },
    "Ayarlar": {"settings.manage": {"admin"}},
    "Kullanıcılar": {"users.manage": {"admin"}},
    "Denetim": {"audit.read": {"admin", "auditor"}},
}
NOT_IN_SPEC_TABLE_ADMIN_ONLY = {
    "sources.read",
    "sources.manage",
    "devices.read",
    "devices.manage",
    "alerts.read",
    "alerts.manage",
    "reports.manage",
    "compliance.read",
    "system.read",
}


def _expected_matrix() -> dict[str, set[str]]:
    expected: dict[str, set[str]] = {role: set() for role in ROLES}
    for column in SPEC_TABLE.values():
        for permission, roles in column.items():
            for role in roles:
                expected[role].add(permission)
    expected["admin"] |= NOT_IN_SPEC_TABLE_ADMIN_ONLY
    return expected


def test_permission_matrix_matches_the_spec_table() -> None:
    expected = _expected_matrix()
    assert {role: set(perms) for role, perms in ROLE_PERMISSIONS.items()} == expected
    listed = {p for column in SPEC_TABLE.values() for p in column} | NOT_IN_SPEC_TABLE_ADMIN_ONLY
    assert listed == ALL_PERMISSIONS == set(PERMISSIONS)
    assert ROLE_PERMISSIONS["admin"] == ALL_PERMISSIONS
    assert set(ROLE_LABELS) == set(ROLES) == set(ROLE_PERMISSIONS)
    assert ROLE_LABELS["hr"] == ("İK Raporları", "HR reports")
    assert permissions_for("nobody") == frozenset()


def test_person_level_data_only_for_admin_and_hr() -> None:
    """Spec 21: never show person-level data to roles without permission."""
    assert {r for r in ROLES if "usage.person" in ROLE_PERMISSIONS[r]} == {"admin", "hr"}
    assert "evidence.create" not in ROLE_PERMISSIONS["auditor"]
    assert "search.export" not in ROLE_PERMISSIONS["auditor"]


def test_require_rejects_unknown_permissions() -> None:
    with pytest.raises(ValueError, match="unknown permission"):
        require("lookup.run", "lookup.everything")


# -- endpoints used only by these tests ----------------------------------------------------------


@pytest.fixture
def probe(app: FastAPI) -> FastAPI:
    router = APIRouter(prefix="/api/v1/_probe")

    @router.get("/me")
    def me(user: CurrentUserDep) -> dict[str, object]:
        return {"username": user.username, "role": user.role}

    @router.post("/write")
    def write(user: CurrentUserDep) -> dict[str, str]:
        return {"ok": user.username}

    @router.post("/totp")
    def totp(user: PendingMfaUserDep) -> dict[str, bool]:
        return {"mfa_passed": user.mfa_passed}

    for permission in sorted(ALL_PERMISSIONS):
        router.add_api_route(f"/perm/{permission}", _guarded(permission), methods=["GET"])
    app.include_router(router)
    return app


def _guarded(permission: str) -> Callable[..., dict[str, str]]:
    def endpoint(user: CurrentUser = Depends(require(permission))) -> dict[str, str]:  # noqa: B008
        return {"ok": user.username}

    return endpoint


@pytest.mark.parametrize("role", ROLES)
def test_every_role_gets_exactly_its_permissions(
    probe: FastAPI, client: TestClient, login_as: Login, role: str
) -> None:
    login_as(client, role)
    allowed = _expected_matrix()[role]
    for permission in sorted(ALL_PERMISSIONS):
        response = client.get(f"/api/v1/_probe/perm/{permission}")
        if permission in allowed:
            assert response.status_code == 200, (role, permission)
        else:
            assert response.status_code == 403, (role, permission)
            assert response.json() == {
                "error": {
                    "code": "forbidden",
                    "message_tr": "Bu işlem için yetkiniz yok.",
                    "message_en": "You do not have permission for this action.",
                }
            }


def _error_code(response: object) -> str:
    code: str = response.json()["error"]["code"]  # type: ignore[attr-defined]
    return code


def test_no_cookie_unknown_and_revoked_sessions_are_401(
    probe: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    response = client.get("/api/v1/_probe/me")
    assert (response.status_code, _error_code(response)) == (401, "not_authenticated")
    assert set(response.json()["error"]) == {"code", "message_tr", "message_en"}
    client.cookies.set(SESSION_COOKIE, "not-a-real-token")
    response = client.get("/api/v1/_probe/me")
    assert (response.status_code, _error_code(response)) == (401, "session_invalid")

    session = login_as(client, "viewer")
    assert client.get("/api/v1/_probe/me").json() == {
        "username": session.username,
        "role": "viewer",
    }
    with app_session_factory() as db:
        db.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(session.session_token))
            .values(revoked_at=utc_now())
        )
        db.commit()
    response = client.get("/api/v1/_probe/me")
    assert (response.status_code, _error_code(response)) == (401, "session_revoked")


def _age_session(
    factory: sessionmaker[Session], token: str, *, idle: timedelta, age: timedelta
) -> None:
    now = utc_now()
    with factory() as db:
        db.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(token))
            .values(last_seen_at=now - idle, created_at=now - age)
        )
        db.commit()


def test_idle_timeout_from_settings(
    probe: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    session = login_as(client, "admin")
    _age_session(
        app_session_factory, session.session_token, idle=timedelta(minutes=14), age=timedelta(0)
    )
    assert client.get("/api/v1/_probe/me").status_code == 200  # 14 min < 15 min
    with app_session_factory() as db:  # the request touched last_seen_at
        seen = db.scalars(
            select(UserSession.last_seen_at).where(
                UserSession.token_hash == hash_token(session.session_token)
            )
        ).one()
    assert utc_now() - seen < timedelta(minutes=1)
    _age_session(
        app_session_factory, session.session_token, idle=timedelta(minutes=16), age=timedelta(0)
    )
    response = client.get("/api/v1/_probe/me")
    assert (response.status_code, _error_code(response)) == (401, "session_idle")


def _last_seen(factory: sessionmaker[Session], token: str) -> timedelta:
    with factory() as db:
        seen = db.scalars(
            select(UserSession.last_seen_at).where(UserSession.token_hash == hash_token(token))
        ).one()
    return utc_now() - seen


def test_background_requests_do_not_keep_a_session_alive(
    probe: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    """A dashboard refreshing every 30 s must not defeat the 15-minute idle timeout."""
    session = login_as(client, "viewer")
    _age_session(
        app_session_factory, session.session_token, idle=timedelta(minutes=10), age=timedelta(0)
    )
    background = {BACKGROUND_HEADER: "1"}
    for _ in range(3):
        assert client.get("/api/v1/_probe/me", headers=background).status_code == 200
    assert _last_seen(app_session_factory, session.session_token) >= timedelta(minutes=10)
    _age_session(
        app_session_factory, session.session_token, idle=timedelta(minutes=16), age=timedelta(0)
    )
    response = client.get("/api/v1/_probe/me", headers=background)
    assert (response.status_code, _error_code(response)) == (401, "session_idle")
    # A user action (no header, or any other value) counts as activity.
    second = login_as(client, "viewer")
    _age_session(
        app_session_factory, second.session_token, idle=timedelta(minutes=10), age=timedelta(0)
    )
    assert client.get("/api/v1/_probe/me", headers={BACKGROUND_HEADER: "0"}).status_code == 200
    assert _last_seen(app_session_factory, second.session_token) < timedelta(minutes=1)


def test_idle_setting_change_applies_immediately(
    probe: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    session = login_as(client, "admin")
    _age_session(
        app_session_factory, session.session_token, idle=timedelta(minutes=6), age=timedelta(0)
    )
    assert client.get("/api/v1/_probe/me").status_code == 200
    with app_session_factory() as db:
        set_setting(db, "auth.idle_minutes", 5, updated_by="admin")
        db.commit()
    _age_session(
        app_session_factory, session.session_token, idle=timedelta(minutes=6), age=timedelta(0)
    )
    assert _error_code(client.get("/api/v1/_probe/me")) == "session_idle"


def test_absolute_lifetime(
    probe: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    session = login_as(client, "auditor")
    _age_session(
        app_session_factory, session.session_token, idle=timedelta(0), age=timedelta(hours=11)
    )
    assert client.get("/api/v1/_probe/me").status_code == 200
    _age_session(
        app_session_factory,
        session.session_token,
        idle=timedelta(0),
        age=timedelta(hours=12, seconds=1),
    )
    response = client.get("/api/v1/_probe/me")
    assert (response.status_code, _error_code(response)) == (401, "session_expired")
    # expires_absolute is enforced as well, even when the setting is raised later
    second = login_as(client, "auditor")
    with app_session_factory() as db:
        set_setting(db, "auth.absolute_hours", 48, updated_by="admin")
        db.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(second.session_token))
            .values(expires_absolute=utc_now() - timedelta(seconds=1))
        )
        db.commit()
    assert _error_code(client.get("/api/v1/_probe/me")) == "session_expired"


def test_mfa_pending_only_reaches_totp_endpoints(
    probe: FastAPI, client: TestClient, login_as: Login
) -> None:
    login_as(client, "admin", mfa_passed=False)
    response = client.get("/api/v1/_probe/me")
    assert (response.status_code, _error_code(response)) == (401, "mfa_required")
    assert client.get("/api/v1/_probe/perm/audit.read").status_code == 401
    assert client.post("/api/v1/_probe/totp").json() == {"mfa_passed": False}


def test_inactive_user_and_forced_password_change(
    probe: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    session = login_as(client, "admin", must_change_password=True)
    assert client.get("/api/v1/_probe/me").status_code == 200  # e.g. /auth/me still works
    response = client.get("/api/v1/_probe/perm/audit.read")
    assert (response.status_code, _error_code(response)) == (403, "password_change_required")
    with app_session_factory() as db:
        db.execute(update(User).where(User.id == session.user_id).values(active=False))
        db.commit()
    response = client.get("/api/v1/_probe/me")
    assert (response.status_code, _error_code(response)) == (401, "user_inactive")


# -- CSRF -----------------------------------------------------------------------------------------


def test_csrf_double_submit_and_session_binding(
    probe: FastAPI, client: TestClient, login_as: Login
) -> None:
    session = login_as(client, "admin")
    assert client.post("/api/v1/_probe/write").json() == {"ok": session.username}

    del client.headers[CSRF_HEADER]
    response = client.post("/api/v1/_probe/write")
    assert (response.status_code, _error_code(response)) == (403, "csrf_failed")

    client.headers[CSRF_HEADER] = "something-else"
    assert _error_code(client.post("/api/v1/_probe/write")) == "csrf_failed"

    # Cookie and header agree but are not this session's token (e.g. a planted cookie).
    client.cookies.set(CSRF_COOKIE, "planted")
    client.headers[CSRF_HEADER] = "planted"
    response = client.post("/api/v1/_probe/write")
    assert (response.status_code, _error_code(response)) == (403, "csrf_failed")

    # Safe methods need no token.
    del client.headers[CSRF_HEADER]
    assert client.get("/api/v1/_probe/me").status_code == 200


def test_csrf_is_checked_before_authentication(probe: FastAPI, client: TestClient) -> None:
    assert _error_code(client.post("/api/v1/_probe/write")) == "csrf_failed"
    client.cookies.set(CSRF_COOKIE, "abc")
    client.headers[CSRF_HEADER] = "abc"
    response = client.post("/api/v1/_probe/write")
    assert (response.status_code, _error_code(response)) == (401, "not_authenticated")


def test_csrf_cookie_is_handed_out_once(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    header = response.headers["set-cookie"]
    assert header.startswith(f"{CSRF_COOKIE}=")
    assert "SameSite=Strict" in header
    assert "Path=/" in header
    assert "HttpOnly" not in header  # the web app must read it
    assert "Secure" not in header  # this test config has secure_cookies = false
    assert len(client.cookies[CSRF_COOKIE]) >= 40
    again = client.get("/api/v1/health")
    assert "set-cookie" not in again.headers


def test_secure_flag_by_default() -> None:
    from arcilogger_api.app import create_app

    with TestClient(create_app()) as anonymous:
        assert "Secure" in anonymous.get("/api/v1/health").headers["set-cookie"]
