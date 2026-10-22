"""Engine construction: URL, password from the secret store (never shown), UTC sessions."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from arcilogger_core.config import Config
from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore
from arcilogger_db.engine import (
    database_password,
    database_url,
    engine_from_url,
    make_engine,
)
from arcilogger_db.models import Device, FgDns, MtDhcp

SECRET = "çok-gizli-db-parolası"


def _config(tmp_path: Path, **database: object) -> Config:
    return Config.model_validate(
        {"data_dir": tmp_path / "data", "secrets_dir": tmp_path, "database": database}
    )


def test_url_from_configuration_masks_the_password(tmp_path: Path) -> None:
    config = _config(tmp_path, host="db.local", port=5433, name="arci", user="u", sslmode="require")
    url = database_url(config.database, SECRET)
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username) == ("db.local", 5433, "arci", "u")
    assert url.query == {"sslmode": "require"}
    assert SECRET not in repr(url)
    assert SECRET not in str(url)


def test_defaults_point_to_the_local_server(tmp_path: Path) -> None:
    config = _config(tmp_path)
    url = database_url(config.database, None)
    assert (url.host, url.port, url.database, url.username) == (
        "127.0.0.1",
        5432,
        "arcilogger",
        "arcilogger",
    )
    assert url.password is None
    assert url.query == {}
    assert config.database.timescale == "auto"


def test_password_comes_from_the_secret_store(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert database_password(config) is None  # no key file yet: not an error
    SecretStore.create_key(tmp_path / "secret.key")
    store = SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json")
    assert database_password(config) is None
    store.set(DATABASE_PASSWORD, SECRET)
    assert database_password(config) == SECRET
    engine = make_engine(config)
    try:
        assert engine.url.password == SECRET
        assert SECRET not in repr(engine)
        assert SECRET not in repr(engine.url)
    finally:
        engine.dispose()
    assert SECRET not in (tmp_path / "secrets.json").read_text(encoding="utf-8")


def test_connection_failure_does_not_reveal_the_password(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = _config(tmp_path, host="127.0.0.1", port=1, connect_timeout_seconds=1)
    engine = make_engine(config, password=SECRET)
    caplog.set_level(logging.DEBUG)
    try:
        with pytest.raises(OperationalError) as caught, engine.connect():
            pass  # pragma: no cover
    finally:
        engine.dispose()
    assert SECRET not in str(caught.value)
    assert SECRET not in caplog.text


def test_sessions_use_utc_and_return_aware_times(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        assert connection.execute(text("SHOW TimeZone")).scalar() == "UTC"
        value = connection.execute(text("SELECT TIMESTAMPTZ '2026-10-12 18:19:07+03'")).scalar()
    assert value == datetime(2026, 10, 12, 15, 19, 7, tzinfo=UTC)
    assert value.utcoffset() == timedelta(0)


def test_network_types_round_trip_as_strings(db_session: Session) -> None:
    received = datetime(2026, 10, 12, 18, 19, 7, 123456, tzinfo=timezone(timedelta(hours=3)))
    common = {
        "time": received,
        "time_received": received,
        "source": "core-r1",
        "sender": "172.20.10.1",
        "raw_day": date(2026, 10, 12),
        "raw_line": 1,
        "raw_offset": 0,
    }
    db_session.add(
        MtDhcp(
            **common,
            server="dhcp60",
            vlan=60,
            action="assigned",
            ip="172.20.60.183",
            mac="02:00:5E:00:53:46",
            hostname="Telefon-12",
        )
    )
    db_session.add(FgDns(**common, qname="example.com", answers=["93.184.215.14", "2606:2800::1"]))
    db_session.add(Device(mac="02-00-5E-00-53-46", last_ip="172.20.60.183"))
    db_session.commit()
    dhcp = db_session.scalars(select(MtDhcp)).one()
    db_session.refresh(dhcp)
    assert dhcp.ip == "172.20.60.183"
    assert dhcp.mac == "02:00:5e:00:53:46"  # PostgreSQL normalises MAC addresses
    assert dhcp.time == received
    assert dhcp.time.tzinfo is not None
    assert dhcp.extra == {}
    dns = db_session.scalars(select(FgDns)).one()
    db_session.refresh(dns)
    assert dns.answers == ["93.184.215.14", "2606:2800::1"]
    device = db_session.get(Device, "02:00:5e:00:53:46")
    assert device is not None
    assert device.hostnames == []
    assert device.tags == []


def test_engine_from_plain_postgresql_url_uses_psycopg() -> None:
    engine = engine_from_url("postgresql://u:p@127.0.0.1:5432/x")
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert "p@" not in repr(engine.url)
    finally:
        engine.dispose()
