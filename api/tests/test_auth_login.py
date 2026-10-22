"""Sign-in: password step, uniform failures, lockout, per-IP rate limit, sessions, /auth/me,
logout and the KVKK privacy notice (spec 13, 3.3, 8.5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from argon2 import PasswordHasher, Type
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.auth import accounts, passwords
from arcilogger_api.auth.ratelimit import AuthRateLimits, RateLimiter, install_limits
from arcilogger_api.auth.testing import ensure_secret_key, prime_csrf, sync_csrf
from arcilogger_api.security import CSRF_COOKIE, SESSION_COOKIE, hash_token
from arcilogger_api.testing import EXAMPLE_SITE_NAME
from arcilogger_core.config import Config
from arcilogger_db.models import AuditLog, PrivacyNoticeAcceptance, User, UserSession
from arcilogger_db.settings import set_setting

PASSWORD = "Mavi kuşlar sabah uçar"
MakeUser = Callable[..., User]


@pytest.fixture(autouse=True)
def _secret_key(api_config: Config) -> None:
    ensure_secret_key(api_config.secrets_dir)


def _login(client: TestClient, username: str, password: str = PASSWORD) -> Response:
    prime_csrf(client)
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    if response.status_code == 200:
        sync_csrf(client)
    return response


def _audit(factory: sessionmaker[Session], prefix: str = "") -> list[AuditLog]:
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


def _set_user(factory: sessionmaker[Session], user_id: int, **values: Any) -> None:
    with factory() as session:
        session.execute(update(User).where(User.id == user_id).values(**values))
        session.commit()


def test_login_without_second_factor(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    user = make_user("viewer", password=PASSWORD, display_name="Ayşe Kaya")
    response = _login(client, f"  {user.username.upper()} ")  # trimmed, case-insensitive
    assert response.status_code == 200, response.text
    # No second factor for the viewer; the KVKK notice, as for every role that sees names.
    assert response.json() == {
        "next": "privacy_notice",
        "mfa_passed": True,
        "must_change_password": False,
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": "Ayşe Kaya",
            "role": "viewer",
            "role_label_tr": "Rapor İzleyici",
            "role_label_en": "Report viewer",
        },
    }
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith(f"{SESSION_COOKIE}="))
    assert "HttpOnly" in session_cookie
    assert "SameSite=strict" in session_cookie
    csrf_cookie = next(c for c in cookies if c.startswith(f"{CSRF_COOKIE}="))
    assert "HttpOnly" not in csrf_cookie
    token = client.cookies[SESSION_COOKIE]
    with app_session_factory() as session:
        row = session.get_one(UserSession, hash_token(token))
        assert (row.mfa_passed, row.ip, row.user_agent) == (True, None, "testclient")
        assert row.reauthenticated_at is not None  # a full sign-in counts as recent
    stored = _user(app_session_factory, user.id)
    assert stored.last_login is not None
    assert stored.failed_attempts == 0
    (entry,) = _audit(app_session_factory)
    assert (entry.action, entry.actor_id, entry.actor_username, entry.target) == (
        "auth.login",
        user.id,
        user.username,
        f"user:{user.id}",
    )
    assert entry.details == {
        "session": hash_token(token)[:12],
        "next": "privacy_notice",
        "rehashed": False,
    }

    me = client.get("/api/v1/auth/me").json()
    assert me["permissions"] == ["dashboard.usage", "dashboard.view", "usage.anonymous"]
    assert me["mfa"] == {
        "passed": True,
        "enabled": False,
        "required": False,
        "recovery_codes_left": 0,
    }
    assert me["privacy_notice"] == {"version": "1", "accepted": False, "required": True}
    assert me["idle_timeout_minutes"] == 15
    assert (me["must_change_password"], me["next"]) == (False, "privacy_notice")
    assert me["user"]["role_label_tr"] == "Rapor İzleyici"
    assert me["site_name"] == EXAMPLE_SITE_NAME  # the organisation, for the web app's sidebar


def test_me_gives_the_configured_organisation_name_even_when_empty(
    app: FastAPI, client: TestClient, make_user: MakeUser, api_config: Config
) -> None:
    user = make_user("viewer", password=PASSWORD)
    assert _login(client, user.username).status_code == 200
    app.state.arci.config = Config.model_validate({**api_config.model_dump(), "site_name": ""})
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["site_name"] == ""
    renamed = {**api_config.model_dump(), "site_name": "Başka Kurum Ltd. Şti."}
    app.state.arci.config = Config.model_validate(renamed)
    assert client.get("/api/v1/auth/me").json()["site_name"] == "Başka Kurum Ltd. Şti."


def test_unknown_user_and_wrong_password_look_the_same(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = make_user("auditor", password=PASSWORD)
    disabled = make_user("hr", password=PASSWORD, active=False)
    dummy_calls: list[str] = []
    real_dummy = passwords.dummy_verify

    def spy(password: str) -> None:
        dummy_calls.append(password)
        real_dummy(password)

    monkeypatch.setattr("arcilogger_api.routers.auth.dummy_verify", spy)
    unknown = _login(client, "nobody-here", "Bilinmeyen kullanıcı parolası")
    wrong = _login(client, user.username, "Yanlış ama uzun bir parola")
    inactive = _login(client, disabled.username)  # right password, disabled account
    invalid_name = _login(client, "Ünal!", "x")  # cannot be a user name at all
    for response in (unknown, wrong, inactive, invalid_name):
        assert response.status_code == 401
        assert response.json() == {
            "error": {
                "code": "login_failed",
                "message_tr": "Giriş başarısız: kullanıcı adı veya parola hatalı ya da hesap "
                "geçici olarak kilitli.",
                "message_en": "Sign-in failed: wrong user name or password, or the account is "
                "temporarily locked.",
            }
        }
        assert SESSION_COOKIE not in response.headers.get("set-cookie", "")
    # Unknown names cost one Argon2 verification like a real account.
    assert dummy_calls == ["Bilinmeyen kullanıcı parolası", "x"]
    failures = _audit(app_session_factory, "auth.login_failed")
    assert [(f.target, f.details, f.actor_username) for f in failures] == [
        (None, {"reason": "unknown_user"}, "system"),  # the typed name is never stored
        (f"user:{user.id}", {"username": user.username, "reason": "bad_password"}, "system"),
        (f"user:{disabled.id}", {"username": disabled.username, "reason": "inactive"}, "system"),
        (None, {"reason": "unknown_user"}, "system"),
    ]
    assert "Yanlış" not in str([f.details for f in failures])  # no password in the log
    assert _user(app_session_factory, user.id).failed_attempts == 1
    assert _user(app_session_factory, disabled.id).failed_attempts == 0


def test_lockout_unlock_and_expiry(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    user = make_user("viewer", password=PASSWORD)
    for attempt in range(1, 6):
        assert _login(client, user.username, f"Yanlış parola numara {attempt}").status_code == 401
    locked = _user(app_session_factory, user.id)
    assert locked.failed_attempts == 5
    assert locked.locked_until is not None
    assert timedelta(minutes=14) < locked.locked_until - datetime.now(UTC) <= timedelta(minutes=15)
    lockouts = _audit(app_session_factory, "auth.lockout")
    assert [entry.details for entry in lockouts] == [
        {"username": user.username, "attempts": 5, "minutes": 15, "lock": 1, "step": "password"}
    ]
    # The right password is refused while locked, and does not extend the counter.
    response = _login(client, user.username)
    assert response.json()["error"]["code"] == "login_failed"
    assert _audit(app_session_factory, "auth.login_failed")[-1].details["reason"] == "locked"
    assert _user(app_session_factory, user.id).failed_attempts == 5

    # An expired lock does not restart the count: the next 5 failures lock again, twice as long.
    _set_user(app_session_factory, user.id, locked_until=datetime.now(UTC) - timedelta(seconds=1))
    for attempt in range(1, 5):
        assert _login(client, user.username, f"Yine yanlış parola {attempt}").status_code == 401
    after = _user(app_session_factory, user.id)
    assert after.failed_attempts == 9
    assert after.locked_until is not None
    assert after.locked_until < datetime.now(UTC)
    assert _login(client, user.username, "Beşinci yanlış parola").status_code == 401
    relocked = _user(app_session_factory, user.id)
    assert relocked.locked_until is not None
    assert (
        timedelta(minutes=29) < relocked.locked_until - datetime.now(UTC) <= timedelta(minutes=30)
    )
    assert _audit(app_session_factory, "auth.lockout")[-1].details == {
        "username": user.username,
        "attempts": 10,
        "minutes": 30,
        "lock": 2,
        "step": "password",
    }
    # A complete sign-in after the lock resets the count: the next lock is the short one again.
    _set_user(app_session_factory, user.id, locked_until=datetime.now(UTC) - timedelta(seconds=1))
    assert _login(client, user.username).status_code == 200
    assert _user(app_session_factory, user.id).failed_attempts == 0
    client.cookies.clear()
    for attempt in range(1, 6):
        _login(client, user.username, f"Yeni tur yanlış parola {attempt}")
    assert _audit(app_session_factory, "auth.lockout")[-1].details["minutes"] == 15


def test_lockout_settings_are_read_on_every_attempt(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    with app_session_factory() as session:
        set_setting(session, "auth.lockout_attempts", 2, updated_by="admin")
        set_setting(session, "auth.lockout_minutes", 60, updated_by="admin")
        session.commit()
    user = make_user("viewer", password=PASSWORD)
    _login(client, user.username, "Yanlış parola bir tane")
    assert _user(app_session_factory, user.id).locked_until is None
    _login(client, user.username, "Yanlış parola iki tane")
    locked_until = _user(app_session_factory, user.id).locked_until
    assert locked_until is not None
    assert locked_until - datetime.now(UTC) > timedelta(minutes=59)


def test_lock_lengths_double_up_to_a_day() -> None:
    settings = accounts.AuthSettings(
        idle_minutes=15,
        absolute_hours=12,
        lockout_attempts=5,
        lockout_minutes=15,
        password_min_length=12,
        totp_required_roles=frozenset(),
    )
    assert [accounts.lock_minutes(settings, lock) for lock in range(1, 10)] == [
        15,
        30,
        60,
        120,
        240,
        480,
        960,
        1440,
        1440,
    ]
    assert accounts.lock_minutes(settings, 0) == 15
    assert accounts.lock_minutes(settings, 10**9) == accounts.MAX_LOCK_MINUTES == 1440
    # A configured lock longer than the cap is kept, never shortened.
    long = replace(settings, lockout_minutes=2000)
    assert [accounts.lock_minutes(long, lock) for lock in (1, 2, 7)] == [2000, 2000, 2000]
    short = replace(settings, lockout_minutes=1)
    assert [accounts.lock_minutes(short, lock) for lock in (1, 2, 11, 12)] == [1, 2, 1024, 1440]


def test_failures_count_on_after_an_expired_lock() -> None:
    settings = accounts.AuthSettings(
        idle_minutes=15,
        absolute_hours=12,
        lockout_attempts=3,
        lockout_minutes=10,
        password_min_length=12,
        totp_required_roles=frozenset(),
    )
    user = User(username="u", display_name="U", role="viewer", password_hash="!")
    user.failed_attempts = 0
    at = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    locks = []
    for attempt in range(1, 10):
        if accounts.register_failure(user, settings, at):
            assert user.locked_until is not None
            locks.append((attempt, user.locked_until - at))
            at = user.locked_until  # the next attempt comes when the lock has run out
    assert locks == [
        (3, timedelta(minutes=10)),
        (6, timedelta(minutes=20)),
        (9, timedelta(minutes=40)),
    ]
    accounts.complete_sign_in(user, at)
    assert (user.failed_attempts, user.locked_until) == (0, None)


def test_must_change_password_flow(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    user = make_user("viewer", password=PASSWORD, must_change_password=True)
    response = _login(client, user.username)
    assert (response.json()["next"], response.json()["must_change_password"]) == (
        "password_change",
        True,
    )
    assert client.get("/api/v1/auth/me").json()["next"] == "password_change"
    blocked = client.post("/api/v1/auth/privacy-notice", json={"version": "1"})
    assert blocked.json()["error"]["code"] == "password_change_required"
    changed = client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "Yepyeni bir parola 2026"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["next"] == "privacy_notice"
    sync_csrf(client)
    assert client.get("/api/v1/auth/me").json()["must_change_password"] is False


def test_rehash_on_login_when_parameters_changed(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1, type=Type.ID)
    user = make_user("viewer")
    _set_user(app_session_factory, user.id, password_hash=weak.hash(PASSWORD))
    assert _login(client, user.username).status_code == 200
    stored = _user(app_session_factory, user.id).password_hash
    assert stored.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert _audit(app_session_factory, "auth.login")[0].details["rehashed"] is True


def test_rate_limit_per_address(
    app: FastAPI,
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
) -> None:
    now = [1000.0]
    install_limits(
        app.state,
        AuthRateLimits(
            limiters={
                "login": RateLimiter(3, 60.0, clock=lambda: now[0]),
                "totp": RateLimiter(3, 60.0),
                "reauth": RateLimiter(3, 60.0),
                "setup": RateLimiter(3, 60.0),
            }
        ),
    )
    user = make_user("viewer", password=PASSWORD)
    for _ in range(3):
        assert _login(client, user.username, "Yanlış parola deneme").status_code == 401
    for _ in range(3):  # also the right password: the address is limited, not the account
        refused = _login(client, user.username)
        assert refused.status_code == 429
        assert refused.json()["error"]["code"] == "rate_limited"
        assert refused.headers["retry-after"] == "60"
    assert [e.details for e in _audit(app_session_factory, "auth.rate_limited")] == [
        {"bucket": "login"}
    ]  # only the first refusal is audited
    now[0] += 61
    assert _login(client, user.username).status_code == 200


def test_rate_limiter_unit() -> None:
    now = [0.0]
    limiter = RateLimiter(2, 10.0, clock=lambda: now[0], max_keys=2)
    assert limiter.hit("a").allowed
    assert limiter.hit("a").allowed
    refused = limiter.hit("a")
    assert (refused.allowed, refused.retry_after, refused.first_refusal) == (False, 10, True)
    now[0] = 4.5
    assert limiter.hit("a").retry_after == 6
    assert not limiter.hit("a").first_refusal
    assert limiter.hit("b").allowed
    now[0] = 10.0  # "a"'s first hit has left the window: allowed again
    assert limiter.hit("a").allowed
    assert limiter.hit("a").first_refusal is False  # allowed decisions never flag a refusal
    assert limiter.hit("a").first_refusal  # refused again after an allowed request: audit again
    # Full table: expired addresses are dropped first, else the oldest one.
    now[0] = 100.0
    assert limiter.hit("c").allowed
    assert limiter.hit("d").allowed
    assert limiter.hit("e").allowed
    assert len(limiter._hits) == 2


def test_new_login_ends_the_session_the_browser_held(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    first = make_user("viewer", password=PASSWORD)
    second = make_user("hr", password=PASSWORD)
    _login(client, first.username)
    old = client.cookies[SESSION_COOKIE]
    _login(client, second.username)
    assert client.cookies[SESSION_COOKIE] != old
    with app_session_factory() as session:
        assert session.get_one(UserSession, hash_token(old)).revoked_at is not None


def test_logout(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    user = make_user("viewer", password=PASSWORD)
    _login(client, user.username)
    token = client.cookies[SESSION_COOKIE]
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 204
    assert SESSION_COOKIE not in client.cookies
    with app_session_factory() as session:
        assert session.get_one(UserSession, hash_token(token)).revoked_at is not None
    (entry,) = _audit(app_session_factory, "auth.logout")
    assert (entry.actor_id, entry.details) == (user.id, {"session": hash_token(token)[:12]})
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/v1/auth/me").json()["error"]["code"] == "session_revoked"


def test_me_reports_absolute_expiry_and_reauthentication(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    user = make_user("auditor", password=PASSWORD, totp_enabled=False)
    with app_session_factory() as session:
        set_setting(session, "auth.totp_required_roles", [], updated_by="admin")
        set_setting(session, "auth.absolute_hours", 8, updated_by="admin")
        session.commit()
    assert _login(client, user.username).json()["next"] == "privacy_notice"
    me = client.get("/api/v1/auth/me").json()
    created = datetime.fromisoformat(me["session"]["created_at"])
    expires = datetime.fromisoformat(me["session"]["expires_at"])
    assert expires - created == timedelta(hours=8)
    assert me["session"]["reauthenticated_at"] is not None
    assert me["mfa"]["required"] is False
    assert "lookup.run" in me["permissions"]


def test_privacy_notice_acceptance(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    user = make_user("hr", password=PASSWORD)
    assert _login(client, user.username).json()["next"] == "privacy_notice"
    me = client.get("/api/v1/auth/me").json()
    assert me["privacy_notice"] == {"version": "1", "accepted": False, "required": True}
    stale = client.post("/api/v1/auth/privacy-notice", json={"version": "0"})
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "notice_outdated")
    for _ in range(2):  # idempotent: one row, one audit entry
        accepted = client.post("/api/v1/auth/privacy-notice", json={"version": "1"})
        assert accepted.status_code == 200
        assert accepted.json() == {"version": "1", "accepted": True, "required": True}
    assert client.get("/api/v1/auth/me").json()["next"] == "done"
    with app_session_factory() as session:
        rows = list(session.scalars(select(PrivacyNoticeAcceptance)))
        assert [(r.user_id, r.version) for r in rows] == [(user.id, "1")]
    assert [e.details for e in _audit(app_session_factory, "privacy.")] == [{"version": "1"}]
    # A new notice version must be accepted again.
    with app_session_factory() as session:
        set_setting(session, "privacy.notice_version", "2", updated_by="admin")
        session.commit()
    assert client.get("/api/v1/auth/me").json()["next"] == "privacy_notice"


@pytest.mark.parametrize(
    "body",
    [
        {"username": "a"},
        {"username": "a", "password": ""},
        {"username": "a", "password": "x" * 1025},
        {"username": "a", "password": "x", "remember": True},
    ],
)
def test_login_input_is_validated_without_echo(client: TestClient, body: dict[str, Any]) -> None:
    prime_csrf(client)
    response = client.post("/api/v1/auth/login", json=body)
    assert response.status_code == 422
    assert "xxxx" not in response.text
