"""User management (``users.manage``): list, create, update, password/TOTP reset, unlock, the
last-administrator and self guards, recent re-authentication and audit entries (spec 13)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.auth import accounts, passwords
from arcilogger_api.auth.passwords import verify_password
from arcilogger_api.auth.testing import enrol_totp, ensure_secret_key, prime_csrf, sync_csrf
from arcilogger_api.routers import users as users_router
from arcilogger_api.security import hash_token
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_db.models import AuditLog, User, UserRecoveryCode, UserSession
from arcilogger_db.settings import set_setting

PASSWORD = "Mavi kuşlar sabah uçar"
MakeUser = Callable[..., User]
Login = Callable[..., LoggedIn]


@pytest.fixture(autouse=True)
def _secret_key(api_config: Config) -> None:
    ensure_secret_key(api_config.secrets_dir)


@pytest.fixture
def admin(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> LoggedIn:
    """A signed-in administrator who re-authenticated just now."""
    session = login_as(client, "admin", username="yonetici", display_name="Yönetici Bir")
    _reauth(app_session_factory, session.session_token, minutes=0)
    return session


def _reauth(factory: sessionmaker[Session], token: str, *, minutes: int) -> None:
    with factory() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(token))
            .values(reauthenticated_at=datetime.now(UTC) - timedelta(minutes=minutes))
        )
        session.commit()


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


def _open_sessions(factory: sessionmaker[Session], user_id: int) -> int:
    with factory() as session:
        rows = session.scalars(
            select(UserSession).where(
                UserSession.user_id == user_id, UserSession.revoked_at.is_(None)
            )
        )
        return len(list(rows))


def _signed_in(app: FastAPI, username: str, password: str) -> TestClient:
    other = TestClient(app, base_url="http://testserver")
    prime_csrf(other)
    response = other.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    sync_csrf(other)
    return other


def test_list_and_get_users(client: TestClient, admin: LoggedIn, make_user: MakeUser) -> None:
    make_user("viewer", username="zeynep")
    make_user("hr", username="burak", active=False)
    make_user("auditor", username="deniz", locked_until=datetime.now(UTC) + timedelta(minutes=5))
    page = client.get("/api/v1/users").json()
    assert [u["username"] for u in page["items"]] == ["burak", "deniz", "yonetici", "zeynep"]
    assert page["next_cursor"] is None
    deniz = page["items"][1]
    assert (deniz["locked"], deniz["role_label_tr"], deniz["totp_enabled"]) == (
        True,
        "Denetçi",
        False,
    )
    assert "password_hash" not in deniz
    assert "totp_secret_encrypted" not in deniz
    first = client.get("/api/v1/users", params={"limit": 2}).json()
    assert [u["username"] for u in first["items"]] == ["burak", "deniz"]
    assert first["next_cursor"] == "deniz"
    second = client.get("/api/v1/users", params={"limit": 2, "cursor": "deniz"}).json()
    assert [u["username"] for u in second["items"]] == ["yonetici", "zeynep"]
    assert second["next_cursor"] is None
    active = client.get("/api/v1/users", params={"active": "false"}).json()
    assert [u["username"] for u in active["items"]] == ["burak"]
    hr = client.get("/api/v1/users", params={"role": "hr"}).json()
    assert [u["username"] for u in hr["items"]] == ["burak"]
    assert client.get("/api/v1/users", params={"role": "root"}).status_code == 422
    one = client.get(f"/api/v1/users/{admin.user_id}").json()
    assert (one["username"], one["display_name"]) == ("yonetici", "Yönetici Bir")
    assert client.get("/api/v1/users/999999").json()["error"]["code"] == "not_found"


def test_create_user_with_temporary_password(
    app: FastAPI,
    client: TestClient,
    admin: LoggedIn,
    app_session_factory: sessionmaker[Session],
) -> None:
    response = client.post(
        "/api/v1/users",
        json={
            "username": "Selin.Kaya",
            "display_name": "  Selin Kaya ",
            "email": "selin@example.org",
            "role": "hr",
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    temporary = created["temporary_password"]
    assert created["user"]["username"] == "selin.kaya"
    assert created["user"]["display_name"] == "Selin Kaya"
    assert created["user"]["must_change_password"] is True
    (entry,) = _audit(app_session_factory, "user.create")
    assert (entry.actor_id, entry.target) == (admin.user_id, f"user:{created['user']['id']}")
    assert entry.details == {"username": "selin.kaya", "role": "hr", "active": True}
    assert temporary not in str(entry.details)
    # The temporary password signs in and forces a change.
    other = _signed_in(app, "selin.kaya", temporary)
    assert other.get("/api/v1/auth/me").json()["next"] == "password_change"
    assert other.get("/api/v1/users").json()["error"]["code"] == "password_change_required"

    taken = client.post(
        "/api/v1/users", json={"username": "selin.kaya", "display_name": "X", "role": "viewer"}
    )
    assert (taken.status_code, taken.json()["error"]["code"]) == (409, "username_taken")
    invalid = client.post(
        "/api/v1/users", json={"username": "-selin", "display_name": "X", "role": "viewer"}
    )
    assert (invalid.status_code, invalid.json()["error"]["code"]) == (422, "username_invalid")
    for body in (
        {"username": "a1", "display_name": "", "role": "viewer"},
        {"username": "a1", "display_name": "X", "role": "root"},
        {"username": "a1", "display_name": "X", "role": "viewer", "email": "no-at-sign"},
        {"username": "a1", "display_name": "X", "role": "viewer", "password": "mine"},
    ):
        assert client.post("/api/v1/users", json=body).status_code == 422, body


@pytest.mark.parametrize(
    "email",
    [
        "a\u0000b@c.de",  # NUL: PostgreSQL text cannot hold it
        "a\u202eb@c.de",  # right-to-left override: disguises the address in the admin UI
        "a\u200bb@c.de",  # zero-width space
        "a\u0007b@c.de",  # bell
        "a\ud800b@c.de",  # lone surrogate (accepted by the JSON parser, not encodable)
    ],
)
def test_email_with_control_or_format_characters_is_refused(
    client: TestClient, admin: LoggedIn, make_user: MakeUser, email: str
) -> None:
    # json.dumps escapes the characters (``\\ud800``), as a browser's JSON.stringify does.
    headers = {"content-type": "application/json"}
    body = {"username": "mail.test", "display_name": "Mail Test", "role": "viewer", "email": email}
    create = client.post("/api/v1/users", content=json.dumps(body), headers=headers)
    assert (create.status_code, create.json()["error"]["code"]) == (422, "validation_error")
    assert "b@c.de" not in create.text
    other = make_user("viewer")
    patch = client.patch(
        f"/api/v1/users/{other.id}", content=json.dumps({"email": email}), headers=headers
    )
    assert (patch.status_code, patch.json()["error"]["code"]) == (422, "validation_error")
    ok = client.patch(f"/api/v1/users/{other.id}", json={"email": "ayşe@örnek.com.tr"})
    assert ok.status_code == 200  # non-ASCII letters stay allowed (internationalised addresses)


@pytest.mark.parametrize("minimum", [20, 64, 128, 300])
def test_temporary_passwords_follow_a_raised_minimum_length(
    app: FastAPI,
    client: TestClient,
    admin: LoggedIn,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    minimum: int,
) -> None:
    """``auth.password_min_length`` above the 19 characters of the default temporary password
    (and even above the maximum length) must neither hang nor produce an unusable password."""
    with app_session_factory() as session:
        set_setting(session, "auth.password_min_length", minimum, updated_by="admin")
        session.commit()
    effective = min(minimum, passwords.MIN_LENGTH_CEILING)
    created = client.post(
        "/api/v1/users", json={"username": "uzun.parola", "display_name": "Uzun", "role": "hr"}
    )
    assert created.status_code == 201, created.text
    temporary = created.json()["temporary_password"]
    assert effective <= len(temporary) <= passwords.MAX_LENGTH
    other = _signed_in(app, "uzun.parola", temporary)
    assert other.get("/api/v1/auth/me").json()["next"] == "password_change"

    existing = make_user("viewer", password=PASSWORD)
    reset = client.post(f"/api/v1/users/{existing.id}/reset-password")
    assert reset.status_code == 200, reset.text
    replaced = reset.json()["temporary_password"]
    assert effective <= len(replaced) <= passwords.MAX_LENGTH
    _signed_in(app, existing.username, replaced)


def test_temporary_password_generation_gives_up_instead_of_hanging(
    app: FastAPI,
    login_as: Login,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A policy no generated password can meet is a 500, not a request that never ends while
    it holds the user-management lock; other user changes go on afterwards."""
    client = TestClient(app, base_url="http://testserver", raise_server_exceptions=False)
    _reauth(app_session_factory, login_as(client, "admin").session_token, minutes=0)
    calls: list[str] = []

    def refuse_everything(candidate: str, context: passwords.PolicyContext) -> list[Any]:
        calls.append(candidate)
        return [passwords.REUSED]

    monkeypatch.setattr(passwords, "password_problems", refuse_everything)
    response = client.post(
        "/api/v1/users", json={"username": "kimse", "display_name": "Kimse", "role": "viewer"}
    )
    assert (response.status_code, response.json()["error"]["code"]) == (500, "internal_error")
    assert len(calls) == passwords.TEMPORARY_PASSWORD_ATTEMPTS
    other = make_user("viewer", password=PASSWORD)
    reset = client.post(f"/api/v1/users/{other.id}/reset-password")
    assert reset.status_code == 500
    monkeypatch.undo()
    assert client.patch(f"/api/v1/users/{other.id}", json={"role": "hr"}).status_code == 200
    with app_session_factory() as session:
        assert session.scalar(select(User.id).where(User.username == "kimse")) is None
        assert verify_password(session.get_one(User, other.id).password_hash, PASSWORD).ok


def test_update_role_revokes_sessions_and_is_audited(
    app: FastAPI,
    client: TestClient,
    admin: LoggedIn,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
) -> None:
    user = make_user("viewer", password=PASSWORD, email="eski@example.org")
    other = _signed_in(app, user.username, PASSWORD)
    response = client.patch(
        f"/api/v1/users/{user.id}",
        json={"role": "auditor", "display_name": "Yeni Ad", "email": None},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["role"], body["display_name"], body["email"]) == ("auditor", "Yeni Ad", None)
    assert other.get("/api/v1/auth/me").json()["error"]["code"] == "session_revoked"
    (entry,) = _audit(app_session_factory, "user.update")
    assert entry.details == {
        "changes": {
            "role": {"old": "viewer", "new": "auditor"},
            "display_name": {"old": user.display_name, "new": "Yeni Ad"},
            "email": {"old": "eski@example.org", "new": None},
        },
        "sessions_revoked": 1,
    }
    # The new role needs TOTP: enrolment at the next sign-in.
    prime_csrf(other)
    login = other.post("/api/v1/auth/login", json={"username": user.username, "password": PASSWORD})
    assert login.json()["next"] == "totp_setup"
    # No change, no entry; a name change keeps the sessions.
    unchanged = client.patch(f"/api/v1/users/{user.id}", json={"display_name": "Yeni Ad"})
    assert unchanged.status_code == 200
    assert len(_audit(app_session_factory, "user.update")) == 1
    renamed = client.patch(f"/api/v1/users/{user.id}", json={"display_name": "Üçüncü Ad"})
    assert renamed.status_code == 200
    assert "sessions_revoked" not in _audit(app_session_factory, "user.update")[-1].details
    assert client.patch(f"/api/v1/users/{user.id}", json={"role": None}).status_code == 422
    assert client.patch("/api/v1/users/999999", json={"role": "hr"}).status_code == 404


def test_deactivation_ends_sessions_and_sign_in(
    app: FastAPI,
    client: TestClient,
    admin: LoggedIn,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
) -> None:
    user = make_user("hr", password=PASSWORD)
    other = _signed_in(app, user.username, PASSWORD)
    assert client.patch(f"/api/v1/users/{user.id}", json={"active": False}).status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401
    prime_csrf(other)
    login = other.post("/api/v1/auth/login", json={"username": user.username, "password": PASSWORD})
    assert login.json()["error"]["code"] == "login_failed"
    assert client.patch(f"/api/v1/users/{user.id}", json={"active": True}).status_code == 200
    assert _open_sessions(app_session_factory, user.id) == 0


def test_self_guards(
    client: TestClient, admin: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    for change in ({"role": "viewer"}, {"active": False}):
        response = client.patch(f"/api/v1/users/{admin.user_id}", json=change)
        assert (response.status_code, response.json()["error"]["code"]) == (
            409,
            "cannot_change_self",
        )
    renamed = client.patch(f"/api/v1/users/{admin.user_id}", json={"display_name": "Kendi Adım"})
    assert renamed.status_code == 200
    for action in ("reset-password", "reset-totp"):
        response = client.post(f"/api/v1/users/{admin.user_id}/{action}")
        assert response.json()["error"]["code"] == "cannot_change_self"
    assert _user(app_session_factory, admin.user_id).role == "admin"


def test_inactive_admins_do_not_count_but_can_be_changed(
    client: TestClient, admin: LoggedIn, make_user: MakeUser
) -> None:
    dormant = make_user("admin", active=False)
    other = make_user("admin")
    assert client.patch(f"/api/v1/users/{other.id}", json={"active": False}).status_code == 200
    assert client.patch(f"/api/v1/users/{dormant.id}", json={"role": "viewer"}).status_code == 200


def test_admins_demoting_each_other_at_once_leave_one_admin(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both requests pass the permission check before either commits; the advisory lock and
    the last-admin rule make the second one fail instead of leaving no administrator."""
    clients = [TestClient(app, base_url="http://testserver") for _ in range(2)]
    sessions = [login_as(c, "admin") for c in clients]
    for signed_in in sessions:
        _reauth(app_session_factory, signed_in.session_token, minutes=0)
    barrier = threading.Barrier(2, timeout=10)

    def lock_after_both_authenticated(db: Session) -> None:
        barrier.wait()
        accounts.lock_user_management(db)

    monkeypatch.setattr(users_router, "lock_user_management", lock_after_both_authenticated)
    results: dict[int, Response] = {}

    def demote(index: int) -> None:
        target = sessions[1 - index].user_id
        results[index] = clients[index].patch(f"/api/v1/users/{target}", json={"role": "viewer"})

    threads = [threading.Thread(target=demote, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    outcomes = sorted(
        (r.status_code, r.json().get("error", {}).get("code")) for r in results.values()
    )
    assert outcomes == [(200, None), (409, "last_admin")]
    with app_session_factory() as session:
        admins = session.scalars(select(User).where(User.role == "admin", User.active)).all()
    assert len(admins) == 1
    assert len(_audit(app_session_factory, "user.update")) == 1


def test_reset_password_and_totp(
    app: FastAPI,
    client: TestClient,
    admin: LoggedIn,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user = make_user(
        "auditor",
        password=PASSWORD,
        failed_attempts=5,
        locked_until=datetime.now(UTC) + timedelta(minutes=10),
    )
    enrol_totp(app_session_factory, user.id, api_config.secrets_dir)
    with app_session_factory() as session:
        session.add(UserRecoveryCode(user_id=user.id, code_hash="0" * 64))
        session.commit()
    reset = client.post(f"/api/v1/users/{user.id}/reset-password")
    assert reset.status_code == 200, reset.text
    temporary = reset.json()["temporary_password"]
    stored = _user(app_session_factory, user.id)
    assert verify_password(stored.password_hash, temporary).ok
    assert (stored.must_change_password, stored.failed_attempts, stored.locked_until) == (
        True,
        0,
        None,
    )
    totp_reset = client.post(f"/api/v1/users/{user.id}/reset-totp")
    assert totp_reset.status_code == 200
    assert totp_reset.json()["totp_enabled"] is False
    stored = _user(app_session_factory, user.id)
    assert (stored.totp_secret_encrypted, stored.totp_last_counter) == (None, None)
    with app_session_factory() as session:
        assert session.scalars(select(UserRecoveryCode)).first() is None
    assert [e.action for e in _audit(app_session_factory, "user.")] == [
        "user.password_reset",
        "user.totp_reset",
    ]
    assert _audit(app_session_factory, "user.totp_reset")[0].details == {
        "was_enabled": True,
        "sessions_revoked": 0,
    }
    other = _signed_in(app, user.username, temporary)
    assert other.get("/api/v1/auth/me").json()["next"] == "totp_setup"
    assert client.post("/api/v1/users/999999/reset-totp").status_code == 404


def test_unlock(
    client: TestClient,
    admin: LoggedIn,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
) -> None:
    user = make_user(
        "viewer",
        password=PASSWORD,
        failed_attempts=5,
        locked_until=datetime.now(UTC) + timedelta(minutes=10),
    )
    _reauth(app_session_factory, admin.session_token, minutes=30)  # not needed for unlocking
    response = client.post(f"/api/v1/users/{user.id}/unlock")
    assert response.status_code == 200
    assert (response.json()["locked"], response.json()["failed_attempts"]) == (False, 0)
    assert _audit(app_session_factory, "user.unlock")[0].details == {"was_locked": True}
    again = client.post(f"/api/v1/users/{user.id}/unlock")
    assert _audit(app_session_factory, "user.unlock")[1].details == {"was_locked": False}
    assert again.status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/users", {"username": "neu", "display_name": "N", "role": "viewer"}),
        ("patch", "/api/v1/users/{id}", {"role": "hr"}),
        ("post", "/api/v1/users/{id}/reset-password", None),
        ("post", "/api/v1/users/{id}/reset-totp", None),
    ],
)
def test_changes_need_recent_reauthentication(
    client: TestClient,
    admin: LoggedIn,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    user = make_user("viewer")
    _reauth(app_session_factory, admin.session_token, minutes=6)
    url = path.format(id=user.id)
    response: Response = getattr(client, method)(url, **({"json": body} if body else {}))
    assert (response.status_code, response.json()["error"]["code"]) == (403, "reauth_required")
    assert _audit(app_session_factory, "user.") == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/v1/users/{id}", None),
        ("patch", "/api/v1/users/{id}", {"role": "hr"}),
        ("post", "/api/v1/users/{id}/reset-password", None),
        ("post", "/api/v1/users/{id}/reset-totp", None),
        ("post", "/api/v1/users/{id}/unlock", None),
    ],
)
def test_user_numbers_are_bounded(
    client: TestClient,
    admin: LoggedIn,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """Past PostgreSQL ``bigint`` (and 0): 422 — never a 500 that drops the connection."""
    for number in (2**63, 10**30, 0):
        url = path.format(id=number)
        response: Response = getattr(client, method)(url, **({"json": body} if body else {}))
        assert (response.status_code, response.json()["error"]["code"]) == (
            422,
            "validation_error",
        ), url
    largest = getattr(client, method)(path.format(id=2**63 - 1), **({"json": body} if body else {}))
    assert (largest.status_code, largest.json()["error"]["code"]) == (404, "not_found")
