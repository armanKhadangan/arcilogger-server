"""THE end-to-end MVP test: from syslog on the wire to a verified evidence package.

One run, with the real components and nothing mocked but the time-stamp authority (the mock
TSA, never legally valid) and the clocks where the code takes one:

1. two site days of load-generator traffic (``tools/loadgen``, synthetic) for the example source
   layout (``fortigate``, ``core-r1``, ``switch-1`` on loopback senders): day 1 written as spool
   files exactly as the collector would, day 2 sent **live** to the collector running
   in-process on free ports (FortiGate over TCP with octet counting, the MikroTiks over UDP);
2. the ingest service until it has caught up (no parse failure allowed) and the worker's lease
   rebuild;
3. the first administrator through the setup flow (one-time token as ``arcilogger-api
   setup-token`` issues it), sign-in with real TOTP codes (RFC 6238, computed with ``pyotp`` as an
   authenticator app would), the KVKK notice;
4. NAT settings for the double-NAT case (no public address configured: candidates) and the
   exact case (``nat.wan_transip``); 5651 lookups of a session from the generator's ground truth;
   the attribution chain must name the expected MAC, device and person;
5. the official PDF report; sealing of the closed days with the core sealer and the chain
   verification; an evidence package of the lookup through the API (recent re-authentication),
   downloaded and verified by the standalone verifier in a separate ``python -I -S`` process —
   and a tampered copy must fail;
6. the audit chain, in the database and in the sealed audit file of the day, holds every step.

Runs in CI on Linux and Windows whenever the test database is available
(``ARCILOGGER_REQUIRE_DB=1``); target well under 90 s.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import importlib.util
import json
import shutil
import socket
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from arcilogger_api.app import create_app
from arcilogger_api.audit import flush_audit_spool, verify_audit_chain, verify_audit_lines
from arcilogger_api.auth.firstrun import issue_setup_token
from arcilogger_api.auth.testing import ensure_secret_key, prime_csrf, sync_csrf
from arcilogger_api.evidence_jobs import EvidenceBuilder
from arcilogger_collector.service import Collector
from arcilogger_core.chain import verify_day, verify_range
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ, site_day, utc_now
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_db.engine import session_factory
from arcilogger_db.models import (
    AuditLog,
    Department,
    Device,
    LookupReport,
    Message,
    ParseFailure,
    Person,
)
from arcilogger_db.settings import set_setting
from arcilogger_ingest.service import IngestService
from arcilogger_worker.correlation.leases import rebuild_days

ROOT = Path(__file__).resolve().parents[1]
LOADGEN = ROOT / "tools" / "loadgen" / "arcilogger_loadgen.py"
TRANSIP = "100.64.12.9"
#: The ISP router's public address in the double-NAT case (documentation range, RFC 5737).
PUBLIC_IP = "203.0.113.200"
USERNAME = "tolga"
PASSWORD = "Mavi kalem 7491, masa lambası!"  # gitleaks:allow (throwaway test account)
#: Longest wait for the collector to write one message sent to it (loopback: milliseconds).
RECEIVE_SECONDS = 10.0
REQUIRED_ACTIONS = {
    "setup.admin_create",
    "auth.login",
    "auth.totp_enrol",
    "auth.totp",
    "privacy.accept",
    "lookup.run",
    "lookup.report",
    "evidence.request",
    "evidence.create",
    "evidence.download",
}


@cache
def loadgen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_loadgen", LOADGEN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


def loopback_alias() -> bool:
    """Linux and Windows route all of 127.0.0.0/8 to the loopback interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind(("127.0.0.2", 0))
        except OSError:  # pragma: no cover - macOS without an alias
            return False
    return True


def site_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=SITE_TZ).astimezone(UTC)


def make_config(root: Path, first_day: date, aliases: bool) -> Config:
    addresses = ("127.0.0.1", "127.0.0.2", "127.0.0.3") if aliases else ("127.0.0.1",) * 3
    return Config.model_validate(
        {
            "site_name": "Örnek A.Ş. (uçtan uca test)",
            "data_dir": str(root / "data"),
            "secrets_dir": str(root / "secrets"),
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": addresses[0]},
                {"name": "core-r1", "type": "mikrotik", "address": addresses[1]},
                {"name": "switch-1", "type": "mikrotik", "address": addresses[2]},
            ]
            if aliases
            else [{"name": "fortigate", "type": "fortigate", "address": addresses[0]}],
            "collector": {
                "bind": "127.0.0.1",
                "udp_port": 0,
                "tcp_port": 0,
                "fsync_interval_seconds": 0.02,
            },
            "seal": {"quiet_seconds": 0, "gzip_level": 6, "first_day": first_day.isoformat()},
            "tsa": {"provider": "mock"},
            "api": {"secure_cookies": False},
            "ingest": {"batch_rows": 5000, "batch_seconds": 0.05, "poll_seconds": 0.02},
        }
    )


def send_live(config: Config, messages: list[Any], addresses: dict[str, str]) -> int:
    """Day 2: every message through the running collector, received at its simulated time."""
    lg = loadgen()
    now = [site_midnight(messages[0].at.astimezone(SITE_TZ).date())]

    async def run() -> int:
        collector = Collector(config, clock=lambda: now[0])
        await collector.start()
        udp, tcp = collector.udp_address, collector.tcp_address
        assert udp is not None
        assert tcp is not None
        sources = [
            lg.Source(name, "fortigate" if name == "fortigate" else "mikrotik", address, name)
            for name, address in addresses.items()
        ]
        sender = lg.Sender(
            "127.0.0.1",
            udp_port=udp[1],
            tcp_port=tcp[1],
            protocol="split",  # FortiGate "reliable" (TCP, octet counting), MikroTik UDP
            sources=sources,
            bind=addresses,
        )
        sent = 0
        loop = asyncio.get_running_loop()
        try:
            with sender:
                for message in messages:
                    now[0] = message.received
                    sender.send(message.source, message.wire)
                    sent += 1
                    # Wait until the collector wrote it: every line gets its own receive time.
                    # A lost or refused message fails the test instead of hanging the CI job.
                    deadline = loop.time() + RECEIVE_SECONDS
                    while sum(s.messages for s in collector.stats.sources.values()) < sent:
                        if loop.time() > deadline:
                            raise AssertionError(
                                f"message {sent} of {len(messages)} ({message.source}, "
                                f"{message.kind}) did not reach the collector within "
                                f"{RECEIVE_SECONDS} s: {collector.stats.sources}"
                            )
                        await asyncio.sleep(0)
        finally:
            await collector.stop()
        return sent

    return asyncio.run(run())


def sealer(config: Config, tsa: MockTsa, at: datetime) -> Sealer:
    return Sealer(
        config,
        provider=MockTsaProvider(tsa),
        roots=[tsa.root_certificate],
        clock=lambda: at,
        clock_status=lambda: None,
    )


def lookup_body(today: date, **query: Any) -> dict[str, Any]:
    return {
        "request": {
            "authority": "İstanbul Cumhuriyet Başsavcılığı",
            "file_no": "2026/5651-E2E",
            "request_date": today.isoformat(),
            "reason": "Uçtan uca test: genel IP ve port ile kullanıcı tespiti.",
        },
        "query": query,
    }


def totp_code(secret: str, steps: int = 0) -> str:
    """What an authenticator app shows ``steps`` periods from now (independent of ARCILOGGER)."""
    return pyotp.TOTP(secret).at(datetime.now(UTC) + timedelta(seconds=30 * steps))


def test_mvp_end_to_end(tmp_path: Path, db_engine: Engine) -> None:
    lg = loadgen()
    today = site_day(utc_now())
    day1, day2 = today - timedelta(days=2), today - timedelta(days=1)
    aliases = loopback_alias()
    config = make_config(tmp_path, day1, aliases)
    layout = Layout(config.data_dir)
    ensure_secret_key(config.secrets_dir)
    addresses = {source.name: source.address for source in config.sources}
    names = ("fortigate", "core-r1", "switch-1")
    sources = tuple(
        lg.Source(
            name,
            "fortigate" if name == "fortigate" else "mikrotik",
            addresses.get(name, "127.0.0.1"),
            {"fortigate": "FIREWALL-01", "core-r1": "CORE-R1", "switch-1": "SWITCH-1"}[name],
        )
        for name in names
    )
    settings = lg.Settings(
        seed=5651, users=6, intensity=10.0, weekend_presence=1.0, guests_per_day=1, sources=sources
    )
    first, second = list(lg.Simulation(settings).run(day1, day2))
    spool_addresses = {source.name: source.address for source in sources}

    # -- 1. day 1 as spool files, day 2 live through the collector ------------------------------
    written = lg.write_spool(config.data_dir, [first], addresses=spool_addresses)
    assert sum(written.values()) == len(first.messages)
    live = [m for m in second.messages if aliases or m.source == "fortigate"]
    if not aliases:  # pragma: no cover - only where 127.0.0.2 is not a loopback address
        # One sender address: the MikroTik lines of day 2 go to the spool like day 1's.
        rest = lg.DayOutput(day2, [m for m in second.messages if m.source != "fortigate"])
        lg.write_spool(config.data_dir, [rest], addresses=spool_addresses, append=True)
    assert send_live(config, live, dict(spool_addresses)) == len(live)
    total_lines = len(first.messages) + len(second.messages)
    for source in names:
        assert layout.spool_file(source, day2).exists()
        if source == "fortigate":
            stored = layout.spool_file(source, day2).read_bytes().splitlines(keepends=True)
            expected = [m for m in live if m.source == source and site_day(m.received) == day2]
            assert stored[: len(expected)] == [
                format_line(m.received.astimezone(SITE_TZ), spool_addresses[source], m.wire)
                for m in expected
            ]

    # -- 2. ingest until caught up, then the lease timeline ------------------------------------
    assert IngestService(config, db_engine).run(once=True) == 0
    sessions = session_factory(db_engine)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == total_lines
        assert session.scalars(select(ParseFailure)).all() == []
        report = rebuild_days(session, day1, day2, now=utc_now())
        assert report.inserted > 0

    # The ground truth: a PC session of day 2, in work hours, long enough to be unambiguous.
    truth = next(
        s
        for s in second.sessions
        if s.kind == "pc"
        and not s.blocked
        and s.end - s.start >= timedelta(seconds=20)
        and 9 <= s.start.astimezone(SITE_TZ).hour < 17
    )
    user = next(u for u in lg.build_users(settings) if u.index == truth.user)
    with sessions() as session:
        department = session.scalars(
            select(Department).where(Department.name == user.department)
        ).one()
        person = Person(name=user.name, department_id=department.id, title="Uzman")
        session.add(person)
        session.flush()
        device = session.get_one(Device, truth.mac)
        device.person_id = person.id
        device.name = f"{user.name} bilgisayarı"
        device.type = "pc"
        person_id = person.id
        # The test machine's disk is not the server's 1 TB data disk (spec 14): smaller reserve.
        set_setting(session, "evidence.min_free_percent", 1, updated_by=USERNAME)
        set_setting(session, "evidence.min_free_gb", 1, updated_by=USERNAME)
        session.commit()

    # -- 3. first administrator, sign-in with TOTP, KVKK notice ---------------------------------
    app = create_app(config, db_engine)
    with sessions() as session:
        token, _ = issue_setup_token(session, at=utc_now())
        session.commit()
    with TestClient(app, base_url="http://testserver") as client:
        prime_csrf(client)
        assert client.get("/api/v1/auth/setup/status").json()["needs_setup"] is True
        created = client.post(
            "/api/v1/auth/setup/admin",
            json={
                "token": token,
                "username": USERNAME,
                "display_name": "Tolga Yönetici",
                "password": PASSWORD,
            },
        )
        assert created.status_code == 201, created.text
        login = client.post("/api/v1/auth/login", json={"username": USERNAME, "password": PASSWORD})
        assert login.status_code == 200, login.text
        sync_csrf(client)
        enrolment = client.get("/api/v1/auth/totp/setup")
        assert enrolment.status_code == 200, enrolment.text
        secret = enrolment.json()["secret"]
        confirmed = client.post("/api/v1/auth/totp/confirm", json={"code": totp_code(secret)})
        assert confirmed.status_code == 200, confirmed.text
        sync_csrf(client)
        # Sign out and in again: password, then the next code of the authenticator.
        assert client.post("/api/v1/auth/logout").status_code == 204
        prime_csrf(client)
        again = client.post("/api/v1/auth/login", json={"username": USERNAME, "password": PASSWORD})
        assert again.status_code == 200, again.text
        sync_csrf(client)
        signed = client.post("/api/v1/auth/totp", json={"code": totp_code(secret, 1)})
        assert signed.status_code == 200, signed.text
        sync_csrf(client)
        me = client.get("/api/v1/auth/me").json()
        assert me["mfa"]["passed"] is True
        version = me["privacy_notice"]["version"]
        accepted = client.post("/api/v1/auth/privacy-notice", json={"version": version})
        assert accepted.status_code == 200, accepted.text

        # -- 4. double NAT (no public address configured): candidates ---------------------------
        middle = truth.start + (truth.end - truth.start) / 2
        at = middle.astimezone(SITE_TZ).isoformat()
        response = client.post(
            "/api/v1/lookup/5651",
            json=lookup_body(
                today,
                at=at,
                public_ip=PUBLIC_IP,
                public_port=40000 + truth.transport % 20000,
                protocol="tcp",
                dest_ip=truth.dstip,
                dest_port=truth.dstport,
            ),
        )
        assert response.status_code == 201, response.text
        candidates = response.json()
        assert candidates["mode"] == "candidates"
        assert candidates["outcome"]["double_nat"] is True
        found = [
            match
            for match in candidates["outcome"]["matches"]
            if match["traffic"]["sessionid"] == truth.sessionid
        ]
        assert found, [m["traffic"]["sessionid"] for m in candidates["outcome"]["matches"]]
        assert found[0]["kind"] == "candidate"
        assert found[0]["chain"]["lease"]["mac"] == truth.mac
        assert found[0]["chain"]["person"]["person_id"] == person_id

        # -- exact: the FortiGate's WAN address is what transip shows ---------------------------
        with sessions() as session:
            set_setting(session, "nat.wan_transip", TRANSIP, updated_by=USERNAME)
            session.commit()
        response = client.post(
            "/api/v1/lookup/5651",
            json=lookup_body(
                today, at=at, public_ip=TRANSIP, public_port=truth.transport, protocol="tcp"
            ),
        )
        assert response.status_code == 201, response.text
        exact = response.json()
        assert exact["mode"] == "exact", exact["outcome"]["explanations"]
        (match,) = exact["outcome"]["matches"]
        assert match["kind"] == "exact"
        traffic = match["traffic"]
        assert (traffic["sessionid"], traffic["srcip"], traffic["srcport"]) == (
            truth.sessionid,
            truth.srcip,
            truth.srcport,
        )
        assert (traffic["transip"], traffic["transport"]) == (TRANSIP, truth.transport)
        chain = match["chain"]
        assert chain["internal_ip"] == truth.srcip
        assert chain["lease"]["mac"] == truth.mac
        assert chain["device"]["mac"] == truth.mac
        assert chain["device"]["name"] == f"{user.name} bilgisayarı"
        assert chain["person"]["person_id"] == person_id
        assert chain["person"]["name"] == user.name
        assert chain["department"] == user.department
        lookup_id = exact["id"]

        # -- 5. the official report ------------------------------------------------------------
        pdf = client.get(f"/api/v1/lookup/{lookup_id}/report.pdf")
        assert pdf.status_code == 200, pdf.text
        assert pdf.content.startswith(b"%PDF-")
        report_sha = hashlib.sha256(pdf.content).hexdigest()
        assert pdf.headers["x-arci-sha256"] == report_sha
        with sessions() as session:
            stored_report = session.scalars(
                select(LookupReport).where(LookupReport.lookup_id == lookup_id)
            ).one()
            assert stored_report.sha256 == report_sha

        # Sealing of the closed days (the nightly task, an hour after midnight) and the chain.
        # The mock TSA's clock: the real time, until the simulated night after today below.
        tsa_time: list[datetime] = []
        tsa = MockTsa(
            layout.state / "mock-tsa",
            key_type="ec",
            clock=lambda: tsa_time[0] if tsa_time else utc_now(),
        )
        outcomes = sealer(config, tsa, site_midnight(today) + timedelta(hours=1)).seal_due()
        assert [(o.day, o.status) for o in outcomes] == [(day1, "sealed"), (day2, "sealed")]
        checks = verify_range(layout, [day1, day2], [tsa.root_certificate], deep=True)
        assert [c.errors for c in checks] == [[], []]

        # The evidence package of the lookup (admin, re-authenticated by the TOTP sign-in).
        response = client.post("/api/v1/evidence", json={"lookup_id": lookup_id})
        assert response.status_code == 202, response.text
        package_id = response.json()["package"]["id"]
        builder = getattr(app.state, "arci_evidence_builder", None)
        assert isinstance(builder, EvidenceBuilder)
        assert builder.wait(timeout=120)
        detail = client.get(f"/api/v1/evidence/{package_id}").json()
        assert detail["status"] == "ready", detail["error"]
        assert set(detail["scope"]["days"]) <= {day1.isoformat(), day2.isoformat()}
        download = client.get(f"/api/v1/evidence/{package_id}/download")
        assert download.status_code == 200, download.text
        assert hashlib.sha256(download.content).hexdigest() == detail["sha256"]
        package = tmp_path / "paket.zip"
        package.write_bytes(download.content)
        flush_audit_spool(db_engine, layout)

    # The standalone verifier, as the recipient runs it: no site-packages, isolated mode.
    folder = tmp_path / "paket"
    with zipfile.ZipFile(package) as archive:
        archive.extractall(folder)
    assert (
        hashlib.sha256((folder / "verify.py").read_bytes()).hexdigest()
        == (detail["verifier_sha256"])
    )
    report_name = f"rapor/ARCILOGGER-5651-rapor-{lookup_id}.pdf"
    assert (folder / report_name).read_bytes() == pdf.content
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["report"] == report_name
    assert {e["path"]: e["sha256"] for e in index["files"]}[report_name] == report_sha
    verified = subprocess.run(
        [sys.executable, "-I", "-S", str(folder / "verify.py"), str(package)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )
    assert verified.returncode == 0, verified.stdout[-3000:] + verified.stderr[-2000:]
    tampered = Path(shutil.copytree(folder, tmp_path / "tampered"))
    sealed_log = next((tampered / "vault").rglob("fortigate.log.gz"))
    data = bytearray(sealed_log.read_bytes())
    data[len(data) // 2] ^= 0x01
    sealed_log.write_bytes(bytes(data))
    refused = subprocess.run(
        [sys.executable, "-I", "-S", str(folder / "verify.py"), str(tampered)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )
    assert refused.returncode != 0, refused.stdout[-2000:]

    # -- 6. the audit chain: database, then the sealed audit file of the day --------------------
    with sessions() as session:
        chain_check = verify_audit_chain(session)
        assert chain_check.ok, chain_check.errors
        rows = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
        actions = {row.action for row in rows}
        assert actions >= REQUIRED_ACTIONS, REQUIRED_ACTIONS - actions
        (report_entry,) = [row for row in rows if row.action == "lookup.report"]
        assert report_entry.details["sha256"] == report_sha
        (created_entry,) = [row for row in rows if row.action == "evidence.create"]
        assert created_entry.details["report"]["sha256"] == report_sha
        assert created_entry.details["sha256"] == detail["sha256"]
        last_hash = rows[-1].hash
    audit_days = sorted(
        date.fromisoformat(path.stem) for path in (layout.spool / "_audit").glob("*.jsonl")
    )
    assert audit_days
    closing = site_midnight(audit_days[-1] + timedelta(days=1)) + timedelta(minutes=30)
    # Sealing today's audit entries needs a night that has not come yet: the time-stamp
    # authority must say the day ended (the sealer checks its genTime), so its clock is moved too.
    tsa_time.append(closing + timedelta(seconds=5))
    outcomes = sealer(config, tsa, closing).seal_due()
    assert all(outcome.status == "sealed" for outcome in outcomes), outcomes
    later = closing + timedelta(minutes=1)
    sealed_lines: list[bytes] = []
    for day in audit_days:
        assert verify_day(layout, day, [tsa.root_certificate], deep=True, now=later).ok
        sealed = layout.vault_day(day) / "audit.jsonl.gz"
        sealed_lines += gzip.decompress(sealed.read_bytes()).splitlines()
        manifest = json.loads(layout.manifest(day).read_bytes())
        assert manifest["audit_log"]["entries"] > 0
    spooled = verify_audit_lines(sealed_lines)
    assert spooled.ok, spooled.errors
    assert spooled.last_hash == last_hash
    sealed_actions = {json.loads(line)["action"] for line in sealed_lines}
    assert sealed_actions >= REQUIRED_ACTIONS, REQUIRED_ACTIONS - sealed_actions
    checks = verify_range(
        layout,
        [day1 + timedelta(days=n) for n in range((audit_days[-1] - day1).days + 1)],
        [tsa.root_certificate],
        now=later,
    )
    assert all(check.ok for check in checks), [(c.day, c.errors) for c in checks]
    # Every entry of the database is in the sealed audit files, the last one last.
    with Session(db_engine) as session:
        assert session.scalar(select(func.max(AuditLog.id))) == json.loads(sealed_lines[-1])["id"]
        assert session.scalar(select(func.count()).select_from(AuditLog)) == len(sealed_lines)
