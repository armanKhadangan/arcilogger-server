"""SQLAlchemy engine and sessions built from ``arcilogger.toml`` and the secret store.

* The connection settings come from the ``[database]`` section of the configuration.
* The password comes from the encrypted secret store (name ``database.password``); it is passed
  to the driver only and never logged — SQLAlchemy masks it in ``repr(url)`` and the engine
  hides bound parameters in log messages and exceptions.
* Every connection uses the time zone UTC, so timestamps arrive as aware UTC datetimes.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import URL, Engine, create_engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config, DatabaseConfig
from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore

DRIVER = "postgresql+psycopg"
APPLICATION_NAME = "arcilogger"


def database_url(database: DatabaseConfig, password: str | None) -> URL:
    """Connection URL (``repr``/``str`` mask the password)."""
    query = {"sslmode": database.sslmode} if database.sslmode else {}
    return URL.create(
        DRIVER,
        username=database.user,
        password=password,
        host=database.host,
        port=database.port,
        database=database.name,
        query=query,
    )


def secret_store(config: Config) -> SecretStore:
    return SecretStore(config.secrets_dir / "secret.key", config.secrets_dir / "secrets.json")


def database_password(config: Config) -> str | None:
    """The database password from the secret store, or ``None`` when none is stored.

    A missing key file means no secrets were set up yet; that is not an error here (the
    connection attempt will tell whether a password is needed).
    """
    key = config.secrets_dir / "secret.key"
    if not key.exists():
        return None
    return secret_store(config).get(DATABASE_PASSWORD)


def _connect_args(application: str, timeout: int) -> dict[str, Any]:
    return {
        "application_name": f"{APPLICATION_NAME}-{application}",
        "connect_timeout": timeout,
        # Text is exchanged as UTF-8 whatever the Windows code page says.
        "client_encoding": "utf8",
        # All timestamps in UTC inside the database session (spec 0.8: store UTC).
        "options": "-c TimeZone=UTC",
    }


def _engine_defaults(kwargs: dict[str, Any]) -> dict[str, Any]:
    kwargs.setdefault("pool_pre_ping", True)
    kwargs.setdefault("hide_parameters", True)
    # INET/CIDR values are plain strings ("172.20.10.37"), as typed in the models; SQLAlchemy
    # 2.1 would otherwise return ipaddress objects.
    kwargs.setdefault("native_inet_types", False)
    return kwargs


def make_engine(
    config: Config, *, application: str = "app", password: str | None = None, **kwargs: Any
) -> Engine:
    """Engine for the configured database; ``password`` overrides the secret store."""
    database = config.database
    secret = password if password is not None else database_password(config)
    kwargs.setdefault("pool_size", database.pool_size)
    return create_engine(
        database_url(database, secret),
        connect_args=_connect_args(application, database.connect_timeout_seconds),
        **_engine_defaults(kwargs),
    )


def engine_from_url(url: str | URL, *, application: str = "test", **kwargs: Any) -> Engine:
    """Engine from a URL/DSN (tests and tools). ``postgresql://`` is mapped to psycopg 3."""
    parsed = make_url(url)
    if parsed.drivername in ("postgresql", "postgres"):
        parsed = parsed.set(drivername=DRIVER)
    return create_engine(
        parsed, connect_args=_connect_args(application, 10), **_engine_defaults(kwargs)
    )


def session_factory(engine: Engine, **info: Any) -> sessionmaker[Session]:
    """Session factory; ``info`` is copied into every session's ``Session.info``.

    ``expire_on_commit=False`` keeps loaded objects usable after the request's commit.
    """
    return sessionmaker(engine, expire_on_commit=False, info=info)
