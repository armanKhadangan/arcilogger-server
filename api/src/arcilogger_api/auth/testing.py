"""Helpers for tests that go through the real sign-in flow (API tests, end-to-end setups).

``arcilogger_api.testing`` creates sessions directly in the database; the helpers here prepare
what the authentication endpoints need instead: the secret store key (TOTP secrets are
encrypted with it), an enrolled TOTP secret, current codes and the CSRF cookie/header pair.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.auth import totp
from arcilogger_api.security import CSRF_COOKIE, CSRF_HEADER
from arcilogger_core.secretstore import SecretStore
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import User


def ensure_secret_key(secrets_dir: Path) -> Path:
    """Create ``secret.key`` in ``secrets_dir`` unless it exists (as the installer does)."""
    path = totp.secret_key_path(secrets_dir)
    if not path.exists():
        SecretStore.create_key(path)
    return path


def enrol_totp(
    factory: sessionmaker[Session], user_id: int, secrets_dir: Path, secret: str | None = None
) -> str:
    """Give ``user_id`` an enabled TOTP secret (no recovery codes); returns the secret."""
    ensure_secret_key(secrets_dir)
    value = secret or totp.new_secret()
    with factory() as session:
        user = session.get_one(User, user_id)
        user.totp_secret_encrypted = totp.encrypt_secret(secrets_dir, value)
        user.totp_enabled = True
        user.totp_last_counter = None
        session.commit()
    return value


def code(secret: str, *, steps: int = 0, at: datetime | None = None) -> str:
    """The TOTP code ``steps`` time steps after ``at`` (default now); ±1 step is accepted."""
    return totp.code_for(secret, totp.counter_at(at or utc_now()) + steps)


def prime_csrf(client: TestClient) -> str:
    """Fetch an ``arci_csrf`` cookie (any GET hands one out) and send it as the header."""
    token = client.cookies.get(CSRF_COOKIE)
    if not token:
        client.get("/api/v1/health")
        token = client.cookies[CSRF_COOKIE]
    client.headers[CSRF_HEADER] = token
    return token


def sync_csrf(client: TestClient) -> None:
    """After a session rotation the server set a new CSRF cookie: send it as the header."""
    client.headers[CSRF_HEADER] = client.cookies[CSRF_COOKIE]
