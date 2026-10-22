"""API test fixtures: an app on a fresh test database, users and ready-made sessions.

``client`` talks to an app whose engine points at this test's own database (``db_engine`` from
``arcilogger_db.pytest_plugin``) and whose data directory is ``tmp_path / "data"``.

``make_user("hr", password="…")`` inserts a user. ``login_as(client, "auditor")`` creates a user
with that role and a valid, MFA-passed session row, and puts the session cookie, the CSRF cookie
and the ``X-CSRF-Token`` header on that client (use several clients for several users).

``licence`` (licensing tests, marked ``license_live``): real licence evaluation with the
deterministic test key and the default server id, and :class:`LicenceKit` helpers (upload,
store a licence directly, backdate the trial, read events and audit entries).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import testing as api_testing
from arcilogger_api.app import create_app
from arcilogger_api.auth.accounts import accept_privacy, privacy_version
from arcilogger_api.deps import AppState
from arcilogger_api.security import hash_token
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.license import load_license
from arcilogger_core.license.testing import fixed_identity, trusted_test_keys
from arcilogger_core.license.trusted_time import clear_cache as clear_trusted_time_cache
from arcilogger_db.models import (
    AuditLog,
    LicenseEvent,
    LicenseFile,
    LicenseInstallation,
    User,
    UserSession,
)


@pytest.fixture
def api_config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "site_name": api_testing.EXAMPLE_SITE_NAME,
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
        }
    )


@pytest.fixture
def layout(api_config: Config) -> Layout:
    return Layout(api_config.data_dir)


@pytest.fixture
def app(api_config: Config, db_engine: Engine) -> FastAPI:
    return create_app(api_config, db_engine)


@pytest.fixture
def app_session_factory(app: FastAPI) -> sessionmaker[Session]:
    """The app's own session factory (its sessions know the audit spool layout)."""
    state: AppState = app.state.arci
    return state.session_factory()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://testserver") as test_client:
        yield test_client


@pytest.fixture
def make_user(app_session_factory: sessionmaker[Session]) -> Callable[..., User]:
    def factory(role: str = "admin", **kwargs: Any) -> User:
        return api_testing.make_user(app_session_factory, role, **kwargs)

    return factory


@pytest.fixture
def login_as(
    app_session_factory: sessionmaker[Session], make_user: Callable[..., User]
) -> Callable[..., LoggedIn]:
    def factory(
        client: TestClient, role: str = "admin", *, mfa_passed: bool = True, **user: Any
    ) -> LoggedIn:
        return api_testing.login(
            client, app_session_factory, make_user(role, **user), mfa_passed=mfa_passed
        )

    return factory


# -- licensing (licensing contract 12 B) ----------------------------------------------------------


class LicenceKit:
    """Helpers of the licence tests (real evaluation, the app's own session factory)."""

    UPLOAD = "/api/v1/license"

    def __init__(self, app: FastAPI, factory: sessionmaker[Session], server_id: str) -> None:
        self.app = app
        self.factory = factory
        self.server_id = server_id

    def invalidate(self) -> None:
        """Forget the API's cached status and the trusted-time caches."""
        state: AppState = self.app.state.arci
        state.license_cache.invalidate()
        clear_trusted_time_cache()

    def recent(self, who: LoggedIn) -> None:
        """The session re-authenticated just now (``require_recent_auth``)."""
        with self.factory() as session:
            session.execute(
                update(UserSession)
                .where(UserSession.token_hash == hash_token(who.session_token))
                .values(reauthenticated_at=datetime.now(UTC))
            )
            session.commit()

    def notice(self, who: LoggedIn) -> None:
        with self.factory() as session:
            accept_privacy(session, who.user_id, privacy_version(session), ip=None)
            session.commit()

    def admin(self, client: TestClient, login_as: Callable[..., LoggedIn], **user: Any) -> LoggedIn:
        """An administrator with a recent re-authentication and the KVKK notice accepted."""
        who = login_as(client, "admin", **user)
        self.recent(who)
        self.notice(who)
        return who

    def upload(self, client: TestClient, data: bytes, name: str = "lisans.lic") -> Any:
        return client.post(self.UPLOAD, files={"file": (name, data, "application/json")})

    def store(self, data: bytes, *, actor: str = "admin") -> None:
        """Make ``data`` the active licence without the upload checks (a licence that was
        valid when it was installed, or one an older version stored)."""
        document = load_license(data)
        at = datetime.now(UTC)
        with self.factory() as session:
            row = LicenseFile(
                sha256=document.sha256,
                payload_sha256=document.payload_sha256,
                content=data,
                license_id=document.payload.license_id,
                key_id=document.key_id,
                licensee=document.payload.licensee,
                issued_at=document.payload.issued_at,
                first_installed_at=at,
                first_installed_by=actor,
            )
            session.add(row)
            session.flush()
            session.add(
                LicenseEvent(
                    at=at, kind="install", source="cli", actor=actor, license_file_id=row.id
                )
            )
            session.commit()
        self.invalidate()

    def trial_started(self, at: datetime) -> None:
        """The installation row with this trial start (before a status read creates it)."""
        with self.factory() as session:
            session.add(LicenseInstallation(id=1, installation_id=uuid4(), trial_started_at=at))
            session.commit()
        self.invalidate()

    def trial_over(self) -> None:
        self.trial_started(datetime.now(UTC) - timedelta(days=30))

    def events(self, kind: str | None = None) -> list[LicenseEvent]:
        with self.factory() as session:
            query = select(LicenseEvent).order_by(LicenseEvent.id)
            if kind is not None:
                query = query.where(LicenseEvent.kind == kind)
            rows = list(session.scalars(query))
            for row in rows:
                session.expunge(row)
            return rows

    def audits(self, action: str) -> list[AuditLog]:
        with self.factory() as session:
            rows = list(
                session.scalars(
                    select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
                )
            )
            for row in rows:
                session.expunge(row)
            return rows


@pytest.fixture
def licence(app: FastAPI, app_session_factory: sessionmaker[Session]) -> Iterator[LicenceKit]:
    """Real licence evaluation with the test key and the default server id (mark the test
    ``license_live``)."""
    clear_trusted_time_cache()
    with fixed_identity() as server_id, trusted_test_keys():
        yield LicenceKit(app, app_session_factory, server_id)
    clear_trusted_time_cache()
