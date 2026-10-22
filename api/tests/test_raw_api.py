"""Raw-line view ``GET /raw`` (spec 9.4, 12.3 page 5): byte-exact from the spool and from a sealed
vault day, line number and offset verified, display escaping, path tricks refused, permissions,
the KVKK notice and the ``raw.view`` audit entry."""

from __future__ import annotations

import base64
import gzip
import hashlib
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.lookup_testing import DAY, Site, accept_privacy, build_site, seal_day
from arcilogger_api.routers.raw import known_source
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_db.models import AuditLog, MtDhcp, Source
from arcilogger_worker.testing import tr

Login = Callable[..., LoggedIn]


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


@pytest.fixture
def site(app_session_factory: sessionmaker[Session], layout: Layout) -> Site:
    return build_site(app_session_factory, layout)


def reader(
    client: TestClient, login_as: Login, factory: sessionmaker[Session], role: str = "auditor"
) -> LoggedIn:
    who = login_as(client, role)
    accept_privacy(factory, who.user_id)
    return who


def dhcp_ref(factory: sessionmaker[Session], row_id: int) -> tuple[str, int, int]:
    with factory() as session:
        row = session.scalars(select(MtDhcp).where(MtDhcp.id == row_id)).one()
        return row.source, row.raw_line, row.raw_offset


def spool_line(layout: Layout, source: str, offset: int) -> bytes:
    data = layout.spool_file(source, DAY).read_bytes()
    return data[offset : data.index(b"\n", offset)]


def get(client: TestClient, **params: Any) -> Any:
    return client.get("/api/v1/raw", params={"day": DAY.isoformat(), **params})


def test_raw_line_from_the_spool(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
) -> None:
    who = reader(client, login_as, app_session_factory)
    source, line, offset = dhcp_ref(app_session_factory, site.dhcp_pc)
    expected = spool_line(layout, source, offset)
    response = get(client, source=source, line=line)
    assert response.status_code == 200, response.text
    data = response.json()
    assert base64.b64decode(data["raw_base64"]) == expected
    assert (data["line"], data["offset"], data["origin"], data["length"]) == (
        line,
        offset,
        "spool",
        len(expected),
    )
    assert data["file"] == f"spool/{source}/2026-10-12.log"
    assert data["sha256"] == hashlib.sha256(expected).hexdigest()
    assert data["text"] == expected.decode()
    assert "dhcp42 assigned 172.20.42.57 for 00:00:5E:00:53:57 PLN-PC14" in data["text"]
    assert data["sender"] == "172.20.10.1"
    assert data["received"].startswith("2026-10-12T10:42:18.003")
    # the matching offset is accepted, a wrong one refused
    assert get(client, source=source, line=line, offset=offset).status_code == 200
    response = get(client, source=source, line=line, offset=offset + 1)
    assert (response.status_code, response.json()["error"]["code"]) == (
        409,
        "raw_reference_mismatch",
    )
    with app_session_factory() as session:
        views = list(session.scalars(select(AuditLog).where(AuditLog.action == "raw.view")))
    assert len(views) == 2
    assert (views[0].actor_id, views[0].target) == (who.user_id, f"raw:{source}/2026-10-12/{line}")
    assert views[0].details == {
        "source": source,
        "day": "2026-10-12",
        "line": line,
        "offset": offset,
        "origin": "spool",
        "sha256": data["sha256"],
    }
    assert "PLN-PC14" not in str(views[0].details)  # the content is never audited


def test_raw_line_from_a_sealed_day_is_byte_identical(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: Site,
) -> None:
    reader(client, login_as, app_session_factory, "admin")
    source, line, offset = dhcp_ref(app_session_factory, site.dhcp_pc)
    before = get(client, source=source, line=line).json()
    seal_day(api_config, layout, DAY)
    assert not layout.spool_file(source, DAY).exists()
    after = get(client, source=source, line=line, offset=offset)
    assert after.status_code == 200, after.text
    data = after.json()
    assert (data["origin"], data["file"]) == ("vault", f"vault/2026/10/12/{source}.log.gz")
    for key in ("raw_base64", "sha256", "offset", "text", "received", "sender"):
        assert data[key] == before[key]
    beyond = get(client, source=source, line=1000)
    assert (beyond.status_code, beyond.json()["error"]["code"]) == (404, "raw_not_found")


def test_damaged_sealed_file(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: Site,
) -> None:
    reader(client, login_as, app_session_factory)
    seal_day(api_config, layout, DAY)
    sealed = layout.vault_day(DAY) / "fortigate.log.gz"
    unlock(sealed)
    sealed.write_bytes(b"\x1f\x8b\x08\x00garbage-not-deflate" * 10)
    response = get(client, source="fortigate", line=3)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "raw_unreadable")


@pytest.mark.parametrize(
    ("source", "status", "code"),
    [
        ("../../secrets", 422, "raw_source_invalid"),
        ("..", 422, "raw_source_invalid"),
        ("fortigate/../core-r1", 422, "raw_source_invalid"),
        ("_audit", 422, "raw_source_invalid"),
        (".hidden", 422, "raw_source_invalid"),
        ("C:\\Windows", 422, "raw_source_invalid"),
        ("FortiGate", 422, "raw_source_invalid"),
        ("forti gate", 422, "raw_source_invalid"),
        ("fortigate%2f..", 422, "raw_source_invalid"),
        ("nobody-knows", 404, "raw_source_unknown"),
    ],
)
def test_path_tricks_and_unknown_sources_are_refused(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
    source: str,
    status: int,
    code: str,
) -> None:
    reader(client, login_as, app_session_factory)
    response = get(client, source=source, line=1)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    with app_session_factory() as session:
        assert session.scalars(select(AuditLog).where(AuditLog.action == "raw.view")).all() == []


@pytest.mark.parametrize(
    "params",
    [
        {"source": "fortigate", "line": 0},
        {"source": "fortigate", "line": -1},
        {"source": "fortigate", "line": 1, "offset": -5},
        {"source": "fortigate", "line": 1, "day": "12.10.2026"},
        {"source": "", "line": 1},
        {"line": 1},
    ],
)
def test_invalid_parameters(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    params: dict[str, Any],
) -> None:
    reader(client, login_as, app_session_factory)
    response = client.get("/api/v1/raw", params={"day": DAY.isoformat(), **params})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error")


def test_missing_day_and_line(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    reader(client, login_as, app_session_factory)
    other_day = (DAY - timedelta(days=3)).isoformat()
    for day, line in ((other_day, 1), (DAY.isoformat(), 99)):
        response = client.get(
            "/api/v1/raw", params={"source": "fortigate", "day": day, "line": line}
        )
        assert (response.status_code, response.json()["error"]["code"]) == (404, "raw_not_found")


@pytest.mark.parametrize(("role", "status"), [("hr", 403), ("viewer", 403)])
def test_roles_without_search_or_lookup_permission(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
    role: str,
    status: int,
) -> None:
    reader(client, login_as, app_session_factory, role)
    response = get(client, source="fortigate", line=1)
    assert (response.status_code, response.json()["error"]["code"]) == (status, "forbidden")


def test_privacy_notice_password_change_and_session(
    app: FastAPI, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    with TestClient(app, base_url="http://testserver") as client:
        assert get(client, source="fortigate", line=1).status_code == 401
        login_as(client, "auditor")
        response = get(client, source="fortigate", line=1)
        assert (response.status_code, response.json()["error"]["code"]) == (
            403,
            "privacy_notice_required",
        )
    with TestClient(app, base_url="http://testserver") as client:
        login_as(client, "admin", must_change_password=True)
        response = get(client, source="fortigate", line=1)
        assert (response.status_code, response.json()["error"]["code"]) == (
            403,
            "password_change_required",
        )


def test_escapes_and_invalid_bytes_are_shown_not_interpreted(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    with app_session_factory() as session:
        session.add(Source(name="lab-switch", type="generic", address="172.20.10.45"))
        session.commit()
    path = layout.spool_file("lab-switch", DAY)
    path.parent.mkdir(parents=True)
    message = "kötü\nsatır \u202egizli\u202c #sıfır".encode() + b"\xff\xfe end"
    path.write_bytes(format_line(tr(DAY, 9), "172.20.10.45", message))
    reader(client, login_as, app_session_factory)
    data = get(client, source="lab-switch", line=1).json()
    assert base64.b64decode(data["raw_base64"]) == path.read_bytes().rstrip(b"\n")
    text = data["text"]
    assert "kötü#012satır" in text  # the spool escape of the line feed stays visible
    assert "\\u202egizli\\u202c" in text  # bidi overrides cannot reorder the text
    assert "#043sıfır" in text
    assert text.endswith("\\xff\\xfe end")
    assert "\n" not in text
    assert "\u202e" not in text


def test_known_sources(app_session_factory: sessionmaker[Session], api_config: Config) -> None:
    config = Config.model_validate(
        {
            **api_config.model_dump(),
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"},
                # the access points: a block of the network-device VLAN 10
                {"name": "ap", "type": "mikrotik", "address": "172.20.10.192/26"},
            ],
        }
    )
    with app_session_factory() as session:
        session.add(Source(name="unknown-10.9.9.9", type="generic", unknown=True))
        session.commit()
        assert known_source(session, config, "fortigate")
        assert known_source(session, config, "ap-172.20.10.211")
        assert known_source(session, config, "unknown-10.9.9.9")
        assert not known_source(session, config, "ap-172.20.10.37")  # outside the range
        assert not known_source(session, config, "ap-banana")
        assert not known_source(session, config, "core-r9")


def test_gzip_members_and_spool_tail(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """A sealed file of two gzip members reads as one stream; the incomplete last line of a
    spool file still being written is not a line yet."""
    with app_session_factory() as session:
        session.add_all(
            [
                Source(name="multi", type="generic", address="172.20.10.21"),
                Source(name="growing", type="generic", address="172.20.10.22"),
            ]
        )
        session.commit()
    first = format_line(tr(DAY, 1), "172.20.10.21", b"one")
    second = format_line(tr(DAY, 2), "172.20.10.21", b"two")
    vault = layout.vault_day(DAY) / "multi.log.gz"
    vault.parent.mkdir(parents=True)
    vault.write_bytes(gzip.compress(first) + gzip.compress(second))
    spool = layout.spool_file("growing", DAY)
    spool.parent.mkdir(parents=True)
    spool.write_bytes(first + b"2026-10-12T02:00:00.000000+03:00 172.20.10.22 half-writ")
    reader(client, login_as, app_session_factory)
    data = get(client, source="multi", line=2).json()
    assert base64.b64decode(data["raw_base64"]) == second.rstrip(b"\n")
    assert (data["offset"], data["origin"]) == (len(first), "vault")
    assert get(client, source="growing", line=1).status_code == 200
    assert get(client, source="growing", line=2).status_code == 404


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PermissionError(13, "Access is denied"), 503, "raw_access_denied"),
        (OSError(5, "Input/output error"), 503, "raw_io_error"),
    ],
)
def test_a_spool_file_the_api_may_not_read(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: Site,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    status: int,
    code: str,
) -> None:
    """An API service without read rights on ``spool`` (for example a server installed before
    NT SERVICE\\ArciApi got ReadAndExecute there, ADR-0007 section 4). Today's lines then answer a
    clear error — never "not found", never "damaged" — and nothing is audited."""
    reader(client, login_as, app_session_factory)
    real_stat = Path.stat

    def stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.is_relative_to(layout.spool):
            raise error
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    response = get(client, source="fortigate", line=1)
    body = response.json()["error"]
    assert (response.status_code, body["code"]) == (status, code)
    if code == "raw_access_denied":
        assert "okuma izni yok" in body["message_tr"]
        assert "bozuk değildir" in body["message_tr"]
    with app_session_factory() as session:
        assert session.scalars(select(AuditLog).where(AuditLog.action == "raw.view")).all() == []
