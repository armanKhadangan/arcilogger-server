"""API test fixtures: an app on a fresh test database, users and ready-made sessions.

``client`` talks to an app whose engine points at this test's own database (``db_engine`` from
``arcilogger_db.pytest_plugin``) and whose data directory is ``tmp_path / "data"``.

``make_user("hr", password="…")`` inserts a user. ``login_as(client, "auditor")`` creates a user
with that role and a valid, MFA-passed session row, and puts the session cookie, the CSRF cookie
and the ``X-CSRF-Token`` header on that client (use several clients for several users).

``licence`` (licensing tests, marked ``license_live``): real licence evaluation with the
deterministic test key and the default server id, and :class:`LicenceKit` helpers (upload,
store a licence directly, backdate the trial, read events and audit entries).

``installed`` (browser administration, ADR-0014): a test that uses it gets an app started with a
real installation folder (``arcilogger_core.admin.testing.make_installation``: configuration
file, channel keys, ``policy.json``, secret store) and its fixed configuration path, which the
API reloads on every request; ``agent`` is the real administration agent of that installation
(``FakeAgent``), started, so its heartbeat is fresh. Use ``pytestmark =
pytest.mark.usefixtures("installed")`` for a whole module.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import deps
from arcilogger_api import testing as api_testing
from arcilogger_api.app import create_app
from arcilogger_api.auth.accounts import accept_privacy, privacy_version
from arcilogger_api.deps import AppState
from arcilogger_api.security import hash_token
from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin import testing as admin_testing
from arcilogger_core.admin.testing import FakeAgent, Installed
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

#: The configuration file of ``installed``: the fictional site of the administration tests.
ADMIN_TOML = admin_testing.DEFAULT_TOML.replace(
    "[collector]", "[api]\nsecure_cookies = false\n\n[collector]", 1
)


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Installed:
    """An installation folder for the browser administration (the app below serves it)."""
    monkeypatch.setattr(deps, "RELOAD_SECONDS", 0.0)  # the API looks at the file every request
    return admin_testing.make_installation(tmp_path / "programdata", toml=ADMIN_TOML)


@pytest.fixture
def agent(installed: Installed) -> FakeAgent:
    """The real agent of ``installed`` (fake clock, event log, prober, tasks), started."""
    fake = FakeAgent(installed)
    fake.start()
    return fake


def _installed(request: pytest.FixtureRequest) -> Installed | None:
    if "installed" not in request.fixturenames:
        return None
    found: Installed = request.getfixturevalue("installed")
    return found


@pytest.fixture
def reauthenticate(app_session_factory: sessionmaker[Session]) -> Callable[[LoggedIn], None]:
    """Mark a session as re-authenticated just now (``ensure_recent_auth``)."""

    def mark(who: LoggedIn) -> None:
        with app_session_factory() as session:
            session.execute(
                update(UserSession)
                .where(UserSession.token_hash == hash_token(who.session_token))
                .values(reauthenticated_at=datetime.now(UTC))
            )
            session.commit()

    return mark


def _ca_certificate(name: str = "Örnek Test Kök") -> bytes:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    start = datetime.now(UTC) - timedelta(days=1)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(Encoding.PEM)


@pytest.fixture
def ca_pem() -> Callable[..., bytes]:
    """A new self-signed CA certificate in PEM (a Kamu SM root or an SMTP CA file of a test;
    never a real one): ``ca_pem()`` or ``ca_pem("Ad")``."""
    return _ca_certificate


@pytest.fixture
def api_config(request: pytest.FixtureRequest, tmp_path: Path) -> Config:
    site = _installed(request)
    if site is not None:
        return site.config()
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
def app(request: pytest.FixtureRequest, api_config: Config, db_engine: Engine) -> FastAPI:
    site = _installed(request)
    return create_app(api_config, db_engine, config_path=site.config_path if site else None)


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

    def __init__(
        self,
        app: FastAPI,
        factory: sessionmaker[Session],
        server_id: str,
        patch: pytest.MonkeyPatch,
    ) -> None:
        self.app = app
        self.factory = factory
        self.server_id = server_id
        #: Swaps of the licence module state (``keys.TRUSTED_KEYS``, the identity provider) made
        #: through this one are undone before the fixture restores the key set and the identity;
        #: the test's own ``monkeypatch`` would undo them afterwards and leave the test key behind.
        self.patch = patch

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
    with fixed_identity() as server_id, trusted_test_keys(), pytest.MonkeyPatch.context() as patch:
        yield LicenceKit(app, app_session_factory, server_id, patch)
    clear_trusted_time_cache()
