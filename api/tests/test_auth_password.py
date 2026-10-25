"""Password change (policy, session rotation, other sessions revoked) and re-authentication with
``require_recent_auth`` (spec 13: sensitive actions)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from argon2 import PasswordHasher, Type
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.auth.passwords import verify_password
from arcilogger_api.auth.testing import code, enrol_totp, ensure_secret_key, prime_csrf, sync_csrf
from arcilogger_api.security import (
    SESSION_COOKIE,
    CurrentUser,
    hash_token,
    require_recent_auth,
)
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_db.models import AuditLog, User, UserSession
from arcilogger_db.settings import set_setting

PASSWORD = "Mavi kuşlar sabah uçar"
NEW_PASSWORD = "Sarı limon ağacı 1923"
MakeUser = Callable[..., User]
Login = Callable[..., LoggedIn]


@pytest.fixture(autouse=True)
def _secret_key(api_config: Config) -> None:
    ensure_secret_key(api_config.secrets_dir)


def _login(client: TestClient, username: str, password: str = PASSWORD) -> Response:
    prime_csrf(client)
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    if response.status_code == 200:
        sync_csrf(client)
    return response


def _change(client: TestClient, current: str, new: str) -> Response:
    response = client.post(
        "/api/v1/auth/password", json={"current_password": current, "new_password": new}
    )
    if response.status_code == 200:
        sync_csrf(client)
    return response


def _audit(factory: sessionmaker[Session], prefix: str) -> list[AuditLog]:
    with factory() as session:
        rows = list(session.scalars(select(AuditLog).order_by(AuditLog.id)))
        for row in rows:
            session.expunge(row)
    return [row for row in rows if row.action.startswith(prefix)]


def _user(factory: sessionmaker[Session], user_id: int) -> User:
    with factory() as session:
        user = session.get_one(User, user_id)
        session.expunge(user)
        return user


def _age_reauth(factory: sessionmaker[Session], user_id: int, minutes: int) -> None:
    with factory() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id)
            .values(reauthenticated_at=datetime.now(UTC) - timedelta(minutes=minutes))
        )
        session.commit()


def test_password_change_rotates_and_revokes_sessions(
    app: FastAPI,
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
) -> None:
    user = make_user("viewer", password=PASSWORD, must_change_password=True)
    other = TestClient(app, base_url="http://testserver")
    _login(other, user.username)  # e.g. a second browser
    _login(client, user.username)
    before = client.cookies[SESSION_COOKIE]
    response = _change(client, PASSWORD, NEW_PASSWORD)
    assert response.status_code == 200, response.text
    assert response.json()["must_change_password"] is False
    after = client.cookies[SESSION_COOKIE]
    assert after != before
    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").json()["error"]["code"] == "session_revoked"
    with app_session_factory() as session:
        assert session.get_one(UserSession, hash_token(before)).revoked_at is not None
        new_row = session.get_one(UserSession, hash_token(after))
        assert new_row.mfa_passed
        assert new_row.reauthenticated_at is None
    stored = _user(app_session_factory, user.id)
    assert verify_password(stored.password_hash, NEW_PASSWORD).ok
    assert not stored.must_change_password
    assert stored.password_changed_at is not None
    (entry,) = _audit(app_session_factory, "auth.password_change")
    assert entry.details == {
        "forced": True,
        "other_sessions_revoked": 1,
        "session": hash_token(after)[:12],
    }
    assert PASSWORD not in str(entry.details)
    assert NEW_PASSWORD not in str(entry.details)
    client.cookies.clear()
    assert _login(client, user.username).status_code == 401
    assert _login(client, user.username, NEW_PASSWORD).status_code == 200


@pytest.mark.parametrize(
    ("new", "rule"),
    [
        (PASSWORD, "reused"),
        ("kısa parola", "too_short"),
        ("qwerty123456", "common"),
        ("arcilogger-sifre", "personal"),
    ],
)
def test_new_password_must_pass_the_policy(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    new: str,
    rule: str,
) -> None:
    user = make_user("viewer", password=PASSWORD)
    _login(client, user.username)
    response = _change(client, PASSWORD, new)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "password_rejected"
    assert error["details"][0]["rule"] == rule
    assert error["message_tr"] == error["details"][0]["message_tr"]
    assert new not in response.text
    assert verify_password(_user(app_session_factory, user.id).password_hash, PASSWORD).ok
    assert _audit(app_session_factory, "auth.password_change") == []


def test_wrong_current_password_counts_towards_lockout(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    user = make_user("viewer", password=PASSWORD)
    _login(client, user.username)
    for _ in range(4):
        response = _change(client, "Yanlış mevcut parola", NEW_PASSWORD)
        assert (response.status_code, response.json()["error"]["code"]) == (
            403,
            "credentials_invalid",
        )
    assert client.get("/api/v1/auth/me").status_code == 200  # still signed in
    response = _change(client, "Yanlış mevcut parola", NEW_PASSWORD)
    assert (response.status_code, response.json()["error"]["code"]) == (401, "account_locked")
    assert client.get("/api/v1/auth/me").json()["error"]["code"] == "session_revoked"
    assert len(_audit(app_session_factory, "auth.password_change_failed")) == 5
    assert [e.details["step"] for e in _audit(app_session_factory, "auth.lockout")] == [
        "password_change"
    ]


def test_password_change_is_refused_while_locked(
    app: FastAPI,
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
) -> None:
    # The lock ends only the session that locked it: another session of the account must not
    # go on guessing, so it is refused even with the right password, and that does not count.
    user = make_user("viewer", password=PASSWORD)
    other = TestClient(app, base_url="http://testserver")
    _login(other, user.username)  # e.g. a second browser
    _login(client, user.username)
    for _ in range(5):
        _change(client, "Yanlış mevcut parola", NEW_PASSWORD)
    assert client.get("/api/v1/auth/me").json()["error"]["code"] == "session_revoked"
    response = _change(other, PASSWORD, NEW_PASSWORD)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "credentials_invalid")
    assert other.get("/api/v1/auth/me").status_code == 200  # refused, not ended
    stored = _user(app_session_factory, user.id)
    assert verify_password(stored.password_hash, PASSWORD).ok
    assert stored.failed_attempts == 5  # not counted
    failed = _audit(app_session_factory, "auth.password_change_failed")
    assert [e.details["reason"] for e in failed] == ["bad_password"] * 5 + ["locked"]
    changes = _audit(app_session_factory, "auth.password_change")
    assert {e.action for e in changes} == {"auth.password_change_failed"}


# -- re-authentication ----------------------------------------------------------------------------


@pytest.fixture
def sensitive(app: FastAPI) -> FastAPI:
    router = APIRouter(prefix="/api/v1/_sensitive")

    @router.get("/download")
    def download(
        user: Annotated[CurrentUser, Depends(require_recent_auth("evidence.download"))],
    ) -> dict[str, str]:
        return {"ok": user.username}

    @router.get("/strict")
    def strict(
        user: Annotated[CurrentUser, Depends(require_recent_auth(minutes=1))],
    ) -> dict[str, str]:
        return {"ok": user.username}

    app.include_router(router)
    return app


def test_require_recent_auth(
    sensitive: FastAPI,
    client: TestClient,
    make_user: MakeUser,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "auditor")  # a session row without reauthenticated_at
    response = client.get("/api/v1/_sensitive/download")
    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "reauth_required",
        "message_tr": "Bu işlem için parolanızı yeniden girmeniz gerekiyor.",
        "message_en": "Please confirm your password to continue with this action.",
    }
    # A full sign-in counts as recent for five minutes.
    with app_session_factory() as session:
        set_setting(session, "auth.totp_required_roles", [], updated_by="admin")
        session.commit()
    user = make_user("auditor", password=PASSWORD)
    client.cookies.clear()
    _login(client, user.username)
    assert client.get("/api/v1/_sensitive/download").json() == {"ok": user.username}
    _age_reauth(app_session_factory, user.id, 6)
    assert client.get("/api/v1/_sensitive/download").json()["error"]["code"] == "reauth_required"
    reauth = client.post("/api/v1/auth/reauth", json={"password": PASSWORD})
    assert reauth.status_code == 200, reauth.text
    assert reauth.json()["valid_minutes"] == 5
    assert client.get("/api/v1/_sensitive/download").status_code == 200
    _age_reauth(app_session_factory, user.id, 2)
    assert client.get("/api/v1/_sensitive/download").status_code == 200
    assert client.get("/api/v1/_sensitive/strict").json()["error"]["code"] == "reauth_required"
    # Permissions are still checked: a viewer is forbidden even right after signing in.
    viewer = make_user("viewer", password=PASSWORD)
    client.cookies.clear()
    _login(client, viewer.username)
    assert client.get("/api/v1/_sensitive/download").json()["error"]["code"] == "forbidden"
    (entry,) = _audit(app_session_factory, "auth.reauth")
    assert (entry.actor_id, set(entry.details)) == (user.id, {"session"})
    with pytest.raises(ValueError, match="at least 1"):
        require_recent_auth(minutes=0)


def test_reauth_with_totp(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user = make_user("admin", password=PASSWORD)
    secret = enrol_totp(app_session_factory, user.id, api_config.secrets_dir)
    _login(client, user.username)
    # One code for the login and the replay: computing it twice could cross a 30 s step.
    used = code(secret)
    assert client.post("/api/v1/auth/totp", json={"code": used}).status_code == 200
    sync_csrf(client)
    missing = client.post("/api/v1/auth/reauth", json={"password": PASSWORD})
    assert (missing.status_code, missing.json()["error"]["code"]) == (403, "credentials_invalid")
    replayed = client.post("/api/v1/auth/reauth", json={"password": PASSWORD, "code": used})
    assert replayed.json()["error"]["code"] == "credentials_invalid"
    good = client.post(
        "/api/v1/auth/reauth", json={"password": PASSWORD, "code": code(secret, steps=1)}
    )
    assert good.status_code == 200, good.text
    reasons = [e.details["reason"] for e in _audit(app_session_factory, "auth.reauth_failed")]
    assert reasons == ["bad_code", "replay"]
    assert _user(app_session_factory, user.id).failed_attempts == 0


def test_reauth_failures_lock_and_end_the_session(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    user = make_user("viewer", password=PASSWORD)
    _login(client, user.username)
    for _ in range(4):
        response = client.post("/api/v1/auth/reauth", json={"password": "Yanlış parola 1234"})
        assert response.json()["error"]["code"] == "credentials_invalid"
    response = client.post("/api/v1/auth/reauth", json={"password": "Yanlış parola 1234"})
    assert (response.status_code, response.json()["error"]["code"]) == (401, "account_locked")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_reauth_is_refused_while_locked(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    user = make_user("viewer", password=PASSWORD)
    _login(client, user.username)
    with app_session_factory() as session:
        session.execute(
            update(User)
            .where(User.id == user.id)
            .values(locked_until=datetime.now(UTC) + timedelta(minutes=5))
        )
        session.commit()
    response = client.post("/api/v1/auth/reauth", json={"password": PASSWORD})
    assert response.json()["error"]["code"] == "credentials_invalid"
    assert _audit(app_session_factory, "auth.reauth_failed")[0].details == {"reason": "locked"}
    assert _user(app_session_factory, user.id).failed_attempts == 0  # not counted


def test_reauth_rehashes_old_parameters(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    user = make_user("viewer", password=PASSWORD)
    _login(client, user.username)
    weak = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1, type=Type.ID)
    with app_session_factory() as session:
        session.execute(
            update(User).where(User.id == user.id).values(password_hash=weak.hash(PASSWORD))
        )
        session.commit()
    assert client.post("/api/v1/auth/reauth", json={"password": PASSWORD}).status_code == 200
    stored = _user(app_session_factory, user.id).password_hash
    assert stored.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
