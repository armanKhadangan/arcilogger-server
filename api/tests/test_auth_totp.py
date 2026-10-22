"""TOTP (RFC 6238): codes, replay protection, encrypted secret, QR, recovery codes and the
second sign-in step over HTTP (enrolment, codes, recovery codes, lockout, session rotation)."""

from __future__ import annotations

import base64
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.auth import accounts, totp
from arcilogger_api.auth.testing import (
    code,
    enrol_totp,
    ensure_secret_key,
    prime_csrf,
    sync_csrf,
)
from arcilogger_api.security import SESSION_COOKIE, hash_token
from arcilogger_core.config import Config
from arcilogger_core.secretstore import SecretStore
from arcilogger_db.models import AuditLog, User, UserRecoveryCode, UserSession

PASSWORD = "Mavi kuşlar sabah uçar"
# RFC 6238 Appendix B (SHA-1): the ASCII seed "12345678901234567890"; 6-digit codes are the
# last six digits of the 8-digit reference values.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()
RFC_VECTORS = [
    (59, "287082"),  # 94287082
    (1111111109, "081804"),  # 07081804
    (1111111111, "050471"),  # 14050471
    (1234567890, "005924"),  # 89005924
    (2000000000, "279037"),  # 69279037
    (20000000000, "353130"),  # 65353130
]

MakeUser = Callable[..., User]


def _at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


# -- unit -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("seconds", "expected"), RFC_VECTORS)
def test_rfc6238_reference_values(seconds: int, expected: str) -> None:
    counter = totp.counter_at(_at(seconds))
    assert totp.code_for(RFC_SECRET, counter) == expected
    assert totp.check_totp(RFC_SECRET, expected, last_counter=None, at=_at(seconds)).counter == (
        counter
    )


def test_window_is_one_step_either_side() -> None:
    at = _at(1234567890)
    now = totp.counter_at(at)
    for steps in (-1, 0, 1):
        value = totp.code_for(RFC_SECRET, now + steps)
        assert totp.check_totp(RFC_SECRET, value, last_counter=None, at=at).counter == now + steps
    for steps in (-2, 2):
        value = totp.code_for(RFC_SECRET, now + steps)
        assert not totp.check_totp(RFC_SECRET, value, last_counter=None, at=at).ok


def test_replay_of_a_used_or_older_step_is_refused() -> None:
    at = _at(1234567890)
    now = totp.counter_at(at)
    used = totp.code_for(RFC_SECRET, now)
    check = totp.check_totp(RFC_SECRET, used, last_counter=now, at=at)
    assert (check.ok, check.replay) == (False, True)
    older = totp.code_for(RFC_SECRET, now - 1)
    assert totp.check_totp(RFC_SECRET, older, last_counter=now, at=at).replay
    newer = totp.code_for(RFC_SECRET, now + 1)
    assert totp.check_totp(RFC_SECRET, newer, last_counter=now, at=at).counter == now + 1


@pytest.mark.parametrize("value", ["", "12345", "1234567", "abcdef", "12 34 5x"])
def test_malformed_codes_are_refused(value: str) -> None:
    assert not totp.check_totp(RFC_SECRET, value, last_counter=None, at=_at(59)).ok


def test_spaces_in_a_code_are_ignored() -> None:
    assert totp.check_totp(RFC_SECRET, "287 082", last_counter=None, at=_at(59)).ok
    assert totp.looks_like_totp("287 082")
    assert not totp.looks_like_totp("abcd-efgh-jkmn-pqrs")


def test_secret_is_160_bits_and_encrypted_with_the_store_key(tmp_path: Path) -> None:
    tmp_path = tmp_path / "keys"  # the module fixture already put a key into tmp_path
    secret = totp.new_secret()
    assert len(base64.b32decode(secret)) == 20
    with pytest.raises(totp.SecretKeyUnavailableError, match="not readable"):
        totp.encrypt_secret(tmp_path, secret)
    ensure_secret_key(tmp_path)
    token = totp.encrypt_secret(tmp_path, secret)
    assert secret not in token
    assert totp.decrypt_secret(tmp_path, token) == secret
    # The same key file as the secret store (installer: ACL for the API service account).
    store = SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json")
    store.set("probe", "value")
    assert store.get("probe") == "value"
    other = tmp_path / "other"
    ensure_secret_key(other)
    with pytest.raises(totp.SecretKeyUnavailableError, match="does not match"):
        totp.decrypt_secret(other, token)


def test_provisioning_uri_and_qr_code() -> None:
    uri = totp.provisioning_uri(RFC_SECRET, "tolga")
    parsed = urlparse(uri)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("otpauth", "totp", "/ARCILOGGER:tolga")
    query = parse_qs(parsed.query)
    assert query["secret"] == [RFC_SECRET]
    assert query["issuer"] == ["ARCILOGGER"]
    svg = totp.qr_svg(uri)
    assert svg.startswith("<svg")
    assert 'xmlns="http://www.w3.org/2000/svg"' in svg
    assert "<script" not in svg.lower()
    assert "http://" not in svg.replace("http://www.w3.org", "")
    assert RFC_SECRET not in svg  # the secret is only in the modules, not as text


def test_recovery_codes_format_normalisation_and_hash() -> None:
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 == len(set(codes))
    for value in codes:
        assert len(value) == 19
        assert value.count("-") == 3
        assert totp.normalize_recovery_code(value) == value.replace("-", "")
    sample = "ab1c-d0ef-ghjk-mnpq"
    assert totp.normalize_recovery_code(" AB1C DOEF-GHJK mnpq ") == "ab1cd0efghjkmnpq"  # O → 0
    assert totp.normalize_recovery_code("abic-d0ef-ghjk-mnpq") == "ab1cd0efghjkmnpq"  # i → 1
    assert totp.normalize_recovery_code("ab1c-d0ef-ghjk") is None
    assert totp.normalize_recovery_code("ab1c-d0ef-ghjk-mnpu") is None  # u is not Crockford
    assert totp.hash_recovery_code(7, sample) == totp.hash_recovery_code(7, "AB1CD0EFGHJKMNPQ")
    assert totp.hash_recovery_code(7, sample) != totp.hash_recovery_code(8, sample)
    with pytest.raises(ValueError, match="not a recovery code"):
        totp.hash_recovery_code(7, "nope")


# -- HTTP flows -----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _secret_key(api_config: Config) -> None:
    ensure_secret_key(api_config.secrets_dir)


def _login(client: TestClient, username: str, password: str = PASSWORD) -> Response:
    prime_csrf(client)
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    if response.status_code == 200:
        sync_csrf(client)
    return response


def _post(client: TestClient, path: str, body: dict[str, Any] | None = None) -> Response:
    response = client.post(f"/api/v1/auth/{path}", json=body or {})
    if response.status_code == 200 and "arci_csrf" in response.headers.get("set-cookie", ""):
        sync_csrf(client)
    return response


def _actions(factory: sessionmaker[Session], prefix: str = "") -> list[str]:
    with factory() as session:
        rows = session.scalars(select(AuditLog).order_by(AuditLog.id))
        return [row.action for row in rows if row.action.startswith(prefix)]


def _details(factory: sessionmaker[Session], action: str) -> list[dict[str, Any]]:
    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
        )
        return [row.details for row in rows]


def _user(factory: sessionmaker[Session], user_id: int) -> User:
    with factory() as session:
        user = session.get_one(User, user_id)
        session.expunge(user)
        return user


def _enrolled(
    make_user: MakeUser,
    factory: sessionmaker[Session],
    api_config: Config,
    role: str = "admin",
) -> tuple[User, str]:
    user = make_user(role, password=PASSWORD)
    return user, enrol_totp(factory, user.id, api_config.secrets_dir)


def test_login_with_totp_rotates_the_session(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user, secret = _enrolled(make_user, app_session_factory, api_config)
    response = _login(client, user.username)
    assert response.status_code == 200
    assert response.json()["next"] == "totp"
    assert response.json()["mfa_passed"] is False
    pending_token = client.cookies[SESSION_COOKIE]
    # Pending: nothing but the TOTP endpoints, /auth/me and logout.
    assert client.get("/api/v1/users").json()["error"]["code"] == "mfa_required"
    me = client.get("/api/v1/auth/me").json()
    assert (me["mfa"]["passed"], me["permissions"], me["next"]) == (False, [], "totp")

    response = _post(client, "totp", {"code": code(secret)})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "next": "privacy_notice",
        "mfa_passed": True,
        "must_change_password": False,
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": "admin",
            "role_label_tr": "Yönetici",
            "role_label_en": "Administrator",
        },
    }
    new_token = client.cookies[SESSION_COOKIE]
    assert new_token != pending_token
    assert client.get("/api/v1/users").status_code == 200
    with app_session_factory() as session:
        old = session.get_one(UserSession, hash_token(pending_token))
        new = session.get_one(UserSession, hash_token(new_token))
        assert old.revoked_at is not None
        assert (new.mfa_passed, new.created_at) == (True, old.created_at)
        assert new.reauthenticated_at is not None
    stale = TestClient(client.app, base_url="http://testserver")
    stale.cookies.set(SESSION_COOKIE, pending_token)
    assert stale.get("/api/v1/auth/me").json()["error"]["code"] == "session_revoked"
    assert _actions(app_session_factory, "auth.") == ["auth.login", "auth.totp"]
    login, second = (
        _details(app_session_factory, "auth.login")[0],
        _details(app_session_factory, "auth.totp")[0],
    )
    assert login["next"] == "totp"
    assert second == {"method": "totp", "session": hash_token(new_token)[:12]}
    assert _user(app_session_factory, user.id).last_login is not None


def test_wrong_and_replayed_codes(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user, secret = _enrolled(make_user, app_session_factory, api_config)
    _login(client, user.username)
    wrong = str((int(code(secret)) + 1) % 1_000_000).zfill(6)
    response = _post(client, "totp", {"code": wrong})
    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "totp_invalid",
        "message_tr": "Doğrulama kodu hatalı veya süresi geçmiş.",
        "message_en": "The verification code is wrong or has expired.",
    }
    used = code(secret)
    assert _post(client, "totp", {"code": used}).status_code == 200
    # The same code again (a second sign-in within the same 30 seconds): replay.
    client.cookies.clear()
    _login(client, user.username)
    response = _post(client, "totp", {"code": used})
    assert response.json()["error"]["code"] == "totp_invalid"
    failures = _details(app_session_factory, "auth.totp_failed")
    assert [d["reason"] for d in failures] == ["invalid", "replay"]
    assert all(d["step"] == "sign_in" for d in failures)
    # The success in between reset the counter; the replay counts as a failure.
    assert _user(app_session_factory, user.id).failed_attempts == 1
    # The next step is still accepted (±1 step window) and resets the counter.
    assert _post(client, "totp", {"code": code(secret, steps=1)}).status_code == 200
    assert _user(app_session_factory, user.id).failed_attempts == 0


def test_totp_failures_lock_the_account_and_end_the_session(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user, _secret = _enrolled(make_user, app_session_factory, api_config)
    _login(client, user.username)
    for _ in range(4):
        assert _post(client, "totp", {"code": "000000"}).json()["error"]["code"] == "totp_invalid"
    response = _post(client, "totp", {"code": "000000"})
    assert (response.status_code, response.json()["error"]["code"]) == (401, "account_locked")
    assert client.get("/api/v1/auth/me").json()["error"]["code"] == "session_revoked"
    locked = _user(app_session_factory, user.id)
    assert locked.locked_until is not None
    assert locked.locked_until > datetime.now(UTC) + timedelta(minutes=14)
    assert _details(app_session_factory, "auth.lockout") == [
        {"username": user.username, "attempts": 5, "minutes": 15, "lock": 1, "step": "totp"}
    ]
    # Locked: the right password does not open a session.
    client.cookies.clear()
    assert _login(client, user.username).json()["error"]["code"] == "login_failed"


@pytest.mark.parametrize(
    "value", ["١٢٣٤٥٦", "\uff11\uff12\uff13\uff14\uff15\uff16", "۱۲۳۴۵۶", "123456\n"]
)
def test_non_ascii_digits_are_a_counted_failure(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    value: str,
) -> None:
    """Arabic-Indic or full-width digits are not a code (``\\d`` would match them)."""
    assert not totp.looks_like_totp(value)
    user, secret = _enrolled(make_user, app_session_factory, api_config)
    assert not totp.check_totp(secret, value, last_counter=None, at=datetime.now(UTC)).ok
    _login(client, user.username)
    response = _post(client, "totp", {"code": value})
    assert (response.status_code, response.json()["error"]["code"]) == (401, "totp_invalid")
    assert _user(app_session_factory, user.id).failed_attempts == 1
    assert _details(app_session_factory, "auth.totp_failed") == [
        {"method": "recovery_code", "reason": "invalid", "step": "sign_in"}
    ]


def test_non_ascii_digits_at_reauthentication_and_enrolment(
    app: FastAPI,
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user, secret = _enrolled(make_user, app_session_factory, api_config)
    _login(client, user.username)
    assert _post(client, "totp", {"code": code(secret)}).status_code == 200
    response = _post(client, "reauth", {"password": PASSWORD, "code": "١٢٣٤٥٦"})
    assert (response.status_code, response.json()["error"]["code"]) == (403, "credentials_invalid")
    assert _details(app_session_factory, "auth.reauth_failed") == [{"reason": "bad_code"}]
    newcomer = make_user("admin", password=PASSWORD)
    other = TestClient(app, base_url="http://testserver")
    _login(other, newcomer.username)
    other.get("/api/v1/auth/totp/setup")
    enrol = _post(other, "totp/confirm", {"code": "١٢٣٤٥٦"})
    assert (enrol.status_code, enrol.json()["error"]["code"]) == (400, "totp_invalid")


def test_totp_guessing_rounds_get_longer(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    """Someone with the password but not the phone: every round of 5 wrong codes locks the
    account twice as long as the one before, until a complete sign-in."""
    user, _ = _enrolled(make_user, app_session_factory, api_config)
    for _ in range(3):
        with app_session_factory() as session:  # the previous lock has run out
            session.execute(
                update(User)
                .where(User.id == user.id, User.locked_until.is_not(None))
                .values(locked_until=datetime.now(UTC) - timedelta(seconds=1))
            )
            session.commit()
        client.cookies.clear()
        assert _login(client, user.username).json()["next"] == "totp"
        for _ in range(4):
            assert _post(client, "totp", {"code": "000000"}).status_code == 401
        assert _post(client, "totp", {"code": "000000"}).json()["error"]["code"] == (
            "account_locked"
        )
    rounds = _details(app_session_factory, "auth.lockout")
    assert [(d["lock"], d["minutes"], d["attempts"]) for d in rounds] == [
        (1, 15, 5),
        (2, 30, 10),
        (3, 60, 15),
    ]
    locked_until = _user(app_session_factory, user.id).locked_until
    assert locked_until is not None
    assert locked_until - datetime.now(UTC) > timedelta(minutes=59)


def test_a_known_password_cannot_reset_the_totp_counter(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    """Failures of the TOTP step survive a new password step (no endless guessing rounds)."""
    user, _ = _enrolled(make_user, app_session_factory, api_config)
    for _ in range(2):
        client.cookies.clear()
        _login(client, user.username)
        _post(client, "totp", {"code": "000000"})
        _post(client, "totp", {"code": "111111"})
    assert _user(app_session_factory, user.id).failed_attempts == 4
    client.cookies.clear()
    _login(client, user.username)
    assert _post(client, "totp", {"code": "222222"}).json()["error"]["code"] == "account_locked"


def test_recovery_codes_are_single_use(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user = make_user("auditor", password=PASSWORD)
    # Enrolment through the API returns the codes.
    _login(client, user.username)
    setup = client.get("/api/v1/auth/totp/setup").json()
    enrolled = _post(client, "totp/confirm", {"code": code(setup["secret"])})
    assert enrolled.status_code == 200, enrolled.text
    codes = enrolled.json()["recovery_codes"]
    client.post("/api/v1/auth/logout")

    client.cookies.clear()
    _login(client, user.username)
    response = _post(client, "totp", {"code": codes[0].upper().replace("-", " ")})
    assert response.status_code == 200, response.text
    assert _details(app_session_factory, "auth.totp")[-1]["recovery_codes_left"] == 9
    me = client.get("/api/v1/auth/me").json()
    assert me["mfa"]["recovery_codes_left"] == 9
    client.post("/api/v1/auth/logout")

    client.cookies.clear()
    _login(client, user.username)
    response = _post(client, "totp", {"code": codes[0]})
    assert response.json()["error"]["code"] == "totp_invalid"
    assert _details(app_session_factory, "auth.totp_failed")[-1]["method"] == "recovery_code"
    with app_session_factory() as session:
        stored = list(session.scalars(select(UserRecoveryCode.code_hash)))
    assert len(stored) == 10
    assert not set(codes) & set(stored)  # only hashes are stored
    assert totp.hash_recovery_code(user.id, codes[1]) in stored


def test_first_enrolment_flow(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user = make_user("admin", password=PASSWORD)
    response = _login(client, user.username)
    assert response.json()["next"] == "totp_setup"
    # The TOTP step is not possible before enrolment.
    assert _post(client, "totp", {"code": "123456"}).json()["error"]["code"] == "totp_not_enabled"
    confirm_too_early = _post(client, "totp/confirm", {"code": "123456"})
    assert confirm_too_early.json()["error"]["code"] == "totp_setup_missing"

    first = client.get("/api/v1/auth/totp/setup")
    assert first.status_code == 200
    setup = first.json()
    assert setup["account"] == user.username
    assert (setup["digits"], setup["period"], setup["algorithm"]) == (6, 30, "SHA1")
    assert setup["otpauth_uri"].startswith(f"otpauth://totp/ARCILOGGER:{user.username}?")
    assert setup["qr_svg"].startswith("<svg")
    # Idempotent: the same pending secret again, audited once.
    assert client.get("/api/v1/auth/totp/setup").json()["secret"] == setup["secret"]
    assert _actions(app_session_factory, "auth.totp_setup") == ["auth.totp_setup"]
    assert setup["secret"] not in str(_details(app_session_factory, "auth.totp_setup"))
    stored = _user(app_session_factory, user.id)
    assert stored.totp_secret_encrypted is not None
    assert setup["secret"] not in stored.totp_secret_encrypted
    assert not stored.totp_enabled

    wrong = _post(client, "totp/confirm", {"code": "000000"})
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (400, "totp_invalid")
    pending_token = client.cookies[SESSION_COOKIE]
    confirmation = code(setup["secret"])
    done = _post(client, "totp/confirm", {"code": confirmation})
    assert done.status_code == 200, done.text
    assert len(done.json()["recovery_codes"]) == 10
    assert done.json()["state"]["mfa_passed"] is True
    assert client.cookies[SESSION_COOKIE] != pending_token  # rotated
    assert client.get("/api/v1/auth/me").json()["mfa"] == {
        "passed": True,
        "enabled": True,
        "required": True,
        "recovery_codes_left": 10,
    }
    assert client.get("/api/v1/auth/totp/setup").json()["error"]["code"] == ("totp_already_enabled")
    # The confirmation code cannot be used to sign in again.
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    _login(client, user.username)
    assert _post(client, "totp", {"code": confirmation}).json()["error"]["code"] == "totp_invalid"
    assert _actions(app_session_factory, "auth.totp_") == [
        "auth.totp_setup",
        "auth.totp_failed",
        "auth.totp_enrol",
        "auth.totp_failed",
    ]
    assert _details(app_session_factory, "auth.totp_failed")[0]["step"] == "enrolment"


def _age_reauthentication(factory: sessionmaker[Session], user_id: int, minutes: int = 6) -> None:
    with factory() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id)
            .values(reauthenticated_at=datetime.now(UTC) - timedelta(minutes=minutes))
        )
        session.commit()


def test_voluntary_enrolment_for_roles_without_obligation(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
) -> None:
    user = make_user("viewer", password=PASSWORD)
    # Signed in completely (no second factor needed); only the KVKK notice is still open.
    assert _login(client, user.username).json()["next"] == "privacy_notice"
    token = client.cookies[SESSION_COOKIE]
    setup = client.get("/api/v1/auth/totp/setup").json()  # sign-in just now = recent
    done = _post(client, "totp/confirm", {"code": code(setup["secret"])})
    assert done.status_code == 200
    assert client.cookies[SESSION_COOKIE] == token  # already complete: no rotation needed
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    assert _login(client, user.username).json()["next"] == "totp"  # now always asked


def test_voluntary_enrolment_needs_a_recent_reauthentication(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
) -> None:
    """An unattended or stolen full session cannot bind the thief's authenticator app."""
    user = make_user("hr", password=PASSWORD)
    _login(client, user.username)
    _age_reauthentication(app_session_factory, user.id)
    stale = client.get("/api/v1/auth/totp/setup")
    assert (stale.status_code, stale.json()["error"]["code"]) == (403, "reauth_required")
    assert _user(app_session_factory, user.id).totp_secret_encrypted is None  # nothing stored
    assert _actions(app_session_factory, "auth.totp") == []

    assert _post(client, "reauth", {"password": PASSWORD}).status_code == 200
    setup = client.get("/api/v1/auth/totp/setup").json()
    _age_reauthentication(app_session_factory, user.id)  # e.g. a long pause before confirming
    late = _post(client, "totp/confirm", {"code": code(setup["secret"])})
    assert (late.status_code, late.json()["error"]["code"]) == (403, "reauth_required")
    assert not _user(app_session_factory, user.id).totp_enabled

    assert _post(client, "reauth", {"password": PASSWORD}).status_code == 200
    # The pending secret survived; the next step's code confirms it.
    assert client.get("/api/v1/auth/totp/setup").json()["secret"] == setup["secret"]
    done = _post(client, "totp/confirm", {"code": code(setup["secret"], steps=1)})
    assert done.status_code == 200, done.text
    assert _user(app_session_factory, user.id).totp_enabled
    assert _actions(app_session_factory, "auth.totp") == ["auth.totp_setup", "auth.totp_enrol"]


def test_enrolment_needs_the_secret_key(
    client: TestClient, make_user: MakeUser, api_config: Config
) -> None:
    user = make_user("admin", password=PASSWORD)
    _login(client, user.username)
    first = client.get("/api/v1/auth/totp/setup").json()["secret"]
    key = api_config.secrets_dir / "secret.key"
    key.unlink()
    response = client.get("/api/v1/auth/totp/setup")
    assert (response.status_code, response.json()["error"]["code"]) == (503, "secret_key_missing")
    # A new key file (restored server): the unconfirmed secret is replaced, not a dead end.
    SecretStore.create_key(key)
    replaced = client.get("/api/v1/auth/totp/setup")
    assert replaced.status_code == 200
    assert replaced.json()["secret"] != first
    confirmed = _post(client, "totp/confirm", {"code": code(replaced.json()["secret"])})
    assert confirmed.status_code == 200


def test_regenerating_recovery_codes_needs_recent_authentication(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user, secret = _enrolled(make_user, app_session_factory, api_config)
    _login(client, user.username)
    _post(client, "totp", {"code": code(secret)})
    fresh = _post(client, "recovery-codes")
    assert fresh.status_code == 200
    assert len(fresh.json()["recovery_codes"]) == 10
    with app_session_factory() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id)
            .values(reauthenticated_at=datetime.now(UTC) - timedelta(minutes=6))
        )
        session.commit()
    stale = _post(client, "recovery-codes")
    assert (stale.status_code, stale.json()["error"]["code"]) == (403, "reauth_required")
    assert _actions(app_session_factory, "auth.recovery") == ["auth.recovery_codes_regenerate"]


def test_recovery_codes_need_an_enrolled_second_factor(
    client: TestClient, make_user: MakeUser
) -> None:
    user = make_user("viewer", password=PASSWORD)
    _login(client, user.username)
    response = _post(client, "recovery-codes")
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "recovery_codes_unavailable",
    )


def test_totp_step_twice_is_a_conflict(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user, secret = _enrolled(make_user, app_session_factory, api_config)
    _login(client, user.username)
    _post(client, "totp", {"code": code(secret)})
    response = _post(client, "totp", {"code": code(secret, steps=1)})
    assert (response.status_code, response.json()["error"]["code"]) == (409, "mfa_already_passed")


def test_totp_step_after_the_account_was_locked_meanwhile(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    """E.g. failed sign-ins elsewhere locked the account while this session waited for TOTP."""
    user, secret = _enrolled(make_user, app_session_factory, api_config)
    _login(client, user.username)
    with app_session_factory() as session:
        session.execute(
            update(User)
            .where(User.id == user.id)
            .values(locked_until=datetime.now(UTC) + timedelta(minutes=10))
        )
        session.commit()
    response = _post(client, "totp", {"code": code(secret)})  # even the right code
    assert (response.status_code, response.json()["error"]["code"]) == (401, "account_locked")
    assert client.get("/api/v1/auth/me").json()["error"]["code"] == "session_revoked"
    assert _details(app_session_factory, "auth.totp_failed") == [
        {"reason": "locked", "step": "sign_in"}
    ]


def test_the_same_code_in_two_concurrent_requests_is_accepted_once(
    app: FastAPI,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both requests have read the user (authentication) before either locks it."""
    user, secret = _enrolled(make_user, app_session_factory, api_config)
    clients = [TestClient(app, base_url="http://testserver") for _ in range(2)]
    for each in clients:
        _login(each, user.username)
    barrier = threading.Barrier(2, timeout=10)

    def load_after_both_authenticated(db: Session, user_id: int) -> User | None:
        barrier.wait()
        return accounts.load_user_for_update(db, user_id)

    monkeypatch.setattr(
        "arcilogger_api.routers.auth.load_user_for_update", load_after_both_authenticated
    )
    value = code(secret)
    results: list[int] = []

    def submit(each: TestClient) -> None:
        results.append(each.post("/api/v1/auth/totp", json={"code": value}).status_code)

    threads = [threading.Thread(target=submit, args=(each,)) for each in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(results) == [200, 401]
    assert [d["reason"] for d in _details(app_session_factory, "auth.totp_failed")] == ["replay"]


def test_wrong_key_file_is_a_clear_503(
    client: TestClient,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
) -> None:
    user, secret = _enrolled(make_user, app_session_factory, api_config)
    key = api_config.secrets_dir / "secret.key"
    key.unlink()
    SecretStore.create_key(key)  # e.g. a restored server with a new key file
    _login(client, user.username)
    response = _post(client, "totp", {"code": code(secret)})
    assert (response.status_code, response.json()["error"]["code"]) == (503, "secret_key_missing")
    assert secret not in response.text
