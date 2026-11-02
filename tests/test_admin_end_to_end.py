"""The browser administration end to end on Linux (ADR-0014; contract test plan 3).

One installation folder and one run, with the real components: the API (``TestClient``) on its
fixed configuration path with its 2-second reload, the administration agent (``Agent.run_once``
through ``FakeAgent``, with ApplySystem's real plan in ``FakeSystem`` and the fake connection
probe), a collector running in-process on 127.0.0.1 and reloading its configuration by itself,
the real Seal task (``arcilogger-core seal``) and the worker's ``audit.import`` and status file.
Nothing but the time-stamp authority (the mock TSA, never legally valid) and the probe is fake.

1. the site name: the API takes the new file within its 2 seconds without a new engine, the
   worker reports the file's SHA-256, the request ends ``active``;
2. a log source added in the browser (``switch-2``, 172.20.10.22): the collector takes it without
   a restart and writes its messages into the source's own spool folder; ApplySystem lets the
   sender through only after the collector reported the new file (while the collector is busy,
   the syslog rule stays as it was);
3. "Şimdi mühürle" on a failed day: the Seal task seals it with the mock TSA, the day records the
   request, the API reports ``succeeded``;
4. an e-mail server change, tested by the agent's probe with the typed password;
5. retention enabled with the typed number of days it deletes while the policy holds such a
   change for an hour: ``pending``, then cancelled, nothing changed;
6. the collector kept writing the existing source all along; the worker imports the agent's
   journal and the audit chain holds ``admin.request``, ``admin.applied``,
   ``settings.secret_change`` and ``admin.action`` and verifies.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import threading
import time as clock
from collections.abc import Callable, Coroutine, Iterator
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from arcilogger_api.app import create_app
from arcilogger_api.audit import verify_audit_chain
from arcilogger_api.testing import login, make_user
from arcilogger_collector.service import Collector
from arcilogger_core import cli
from arcilogger_core.admin.contract import SystemStatus, parse_document
from arcilogger_core.admin.testing import DEFAULT_TOML, FakeAgent, Installed, make_installation
from arcilogger_core.config import load_config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line, parse_line
from arcilogger_core.timeutil import SITE_TZ, isoformat_site, site_day, utc_now
from arcilogger_db.models import AdminRequest, AuditLog, UserSession
from arcilogger_worker.service import Worker, default_jobs

T = TypeVar("T")
V = "/api/v1"
CONFIG = f"{V}/admin/config"
FORTIGATE = b'<189>date=2026-10-24 time=09:44:57 devname="FIREWALL-01" logid="0000000013" x=1'
SWITCH = b"<30>2026-10-25T09:38:26.380+03:00 SWITCH-2 system,info link up"
#: The installation of the test: its firewall sends from 127.0.0.1 (a hand-written source the
#: browser could not add), the collector listens on 127.0.0.1 and writes its status once a
#: minute unless its configuration changes.
TOML = DEFAULT_TOML.replace(
    '[collector]\nbind = "0.0.0.0"\n',
    "[api]\nsecure_cookies = false\n\n[seal]\nquiet_seconds = 0\n\n"
    '[collector]\nbind = "127.0.0.1"\nudp_port = 0\ntcp_port = 0\n'
    "fsync_interval_seconds = 0.02\nstatus_interval_seconds = 60\n\n"
    '[[sources]]\nname = "fortigate"\ntype = "fortigate"\naddress = "127.0.0.1"\n',
)


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


class RunningCollector:
    """The collector in a thread of its own (its event loop), as the service runs it."""

    def __init__(self, config_path: Path) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.collector = Collector(
            load_config(config_path), config_path=config_path, reload_seconds=0.05
        )
        self.run(self.collector.start())

    def run(self, coroutine: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=10)

    def call(self, function: Callable[[], T]) -> T:
        async def wrapper() -> T:
            return function()

        return self.run(wrapper())

    def busy(self) -> threading.Event:
        """Keep the collector busy (its loop blocked) until the returned event is set."""
        done = threading.Event()
        self.loop.call_soon_threadsafe(done.wait, 10)
        return done

    def stop(self) -> None:
        self.run(self.collector.stop())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        self.loop.close()


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> float:
    """Seconds until ``predicate`` held (fails after ``timeout``)."""
    started = clock.monotonic()
    while not predicate():
        if clock.monotonic() - started > timeout:
            raise AssertionError("condition not met in time")
        clock.sleep(0.02)
    return clock.monotonic() - started


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reported_sha(path: Path) -> str | None:
    try:
        block = json.loads(path.read_bytes()).get("config") or {}
    except (OSError, ValueError):
        return None
    found = block.get("sha256")
    return found if isinstance(found, str) else None


def spooled(layout: Layout, source: str) -> list[bytes]:
    return [
        parse_line(line).message
        for path in sorted((layout.spool / source).glob("*.log"))
        for line in path.read_bytes().splitlines()
    ]


def syslog_addresses(installed: Installed) -> tuple[str, ...]:
    status = parse_document(SystemStatus, (installed.layout.system / "status.json").read_bytes())
    return status.firewall.syslog_addresses


def write_closed_days(installed: Installed, days: list[date]) -> None:
    for day in days:
        path = installed.data.spool_file("fortigate", day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            for hour in range(0, 24, 3):
                received = datetime.combine(day, time(hour, 0), tzinfo=SITE_TZ)
                handle.write(format_line(received, "127.0.0.1", FORTIGATE))


def failed_before(installed: Installed, day: date) -> None:
    """The day's last attempt failed and its next one is hours away (the Seal task waits)."""
    state = {
        "day": day.isoformat(),
        "status": "pending",
        "attempts": 4,
        "last_attempt_at": isoformat_site(utc_now() - timedelta(minutes=5), "seconds"),
        "last_error": "the time-stamp server did not answer",
        "last_error_code": "tsa_unavailable",
        "next_attempt_at": isoformat_site(utc_now() + timedelta(hours=3), "seconds"),
    }
    path = installed.data.seal_state(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def test_admin_end_to_end(tmp_path: Path, db_engine: Engine) -> None:
    installed = make_installation(tmp_path / "programdata", toml=TOML)
    today = site_day(utc_now())
    days = [today - timedelta(days=n) for n in (3, 2, 1)]
    write_closed_days(installed, days)
    failed_before(installed, days[0])
    agent = FakeAgent(installed, tasks=False, system=True)  # the real Seal task below
    agent.start()
    app = create_app(
        load_config(installed.config_path),
        db_engine,
        config_path=installed.config_path,
        audit_flush_seconds=None,
    )
    sessions = app.state.arci.session_factory()
    jobs = [job for job in default_jobs(60) if job.name == "audit.import"]

    def worker_round() -> None:
        """The worker as its loop runs it: the file it uses now, its jobs, its status file."""
        worker = Worker(
            engine=db_engine,
            layout=installed.data,
            jobs=jobs,
            config=load_config(installed.config_path),
            config_path=installed.config_path,
            reload_seconds=0,
        )
        worker.tick()
        worker.write_status()

    collector = RunningCollector(installed.config_path)
    try:
        with TestClient(app, base_url="http://testserver") as client:
            login(client, sessions, make_user(sessions, "admin", email="tolga@ornek.example"))

            def reauthenticate() -> None:
                with sessions() as session:
                    for row in session.scalars(select(UserSession)):
                        row.reauthenticated_at = utc_now()
                    session.commit()

            def etag(section: str) -> str:
                found: str = client.get(CONFIG).json()["etags"][section]
                return found

            def follow(response: Any) -> dict[str, Any]:
                assert response.status_code == 202, response.text
                agent.process()
                found: dict[str, Any] = client.get(response.json()["status_url"]).json()
                return found

            # The firewall's messages arrive over UDP on 127.0.0.1.
            address = collector.collector.udp_address
            assert address is not None
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.bind(("127.0.0.1", 0))
                sender.sendto(FORTIGATE + b" #1", address)
                wait_for(lambda: FORTIGATE + b" #1" in spooled(installed.data, "fortigate"))
            wait_for(lambda: reported_sha(installed.data.collector_status) is not None)
            worker_round()

            # 1. The site name.
            assert client.get(f"{V}/auth/me").json()["site_name"] == "Örnek A.Ş."
            body = {"etag": etag("general"), "site_name": "Örnek Lojistik A.Ş."}
            accepted = client.put(f"{CONFIG}/general", json=body)
            assert accepted.status_code == 202, accepted.text
            agent.process()
            # Timed on the path every request takes to its configuration, not through requests:
            # the span would also hold up to two whole requests (each commits its session and
            # crosses threads), and on a loaded Windows runner they used up the second the
            # bound leaves beyond the API's 2 seconds.
            took = wait_for(
                lambda: app.state.arci.require_config().site_name == "Örnek Lojistik A.Ş.",
                timeout=4.0,
            )
            assert took <= 3.0
            assert client.get(f"{V}/auth/me").json()["site_name"] == "Örnek Lojistik A.Ş."
            assert app.state.arci.engine is db_engine  # the engine was not rebuilt
            worker_round()
            assert reported_sha(installed.data.worker_status) == file_sha(installed.config_path)
            done = client.get(accepted.json()["status_url"]).json()
            assert done["state"] == "active", done
            steps = {step["step"]: step["state"] for step in done["steps"]}
            assert (steps["reload.api"], steps["reload.worker"]) == ("done", "done")

            # 2. A new log source; ApplySystem opens it only once the collector runs the file.
            free = collector.busy()
            source = {
                "name": "switch-2",
                "type": "mikrotik",
                "address": "172.20.10.22",
                "heartbeat_minutes": 15,
                "expected": True,
                "transport": "any",
                "enabled": True,
            }
            added = client.post(
                f"{CONFIG}/sources", json={"etag": etag("sources"), "source": source}
            )
            assert added.status_code == 202, added.text
            agent.process()
            assert "172.20.10.22" not in syslog_addresses(installed)
            waiting = client.get(added.json()["status_url"]).json()
            assert waiting["state"] == "applied", waiting
            free.set()
            new_sha = file_sha(installed.config_path)
            wait_for(lambda: reported_sha(installed.data.collector_status) == new_sha)
            assert [s.name for s in collector.collector.config.sources] == ["fortigate", "switch-2"]
            # A datagram of the new device (172.20.10.22 cannot send on this host's loopback: its
            # address is handed to the collector's receive path as the socket would).
            collector.call(
                lambda: collector.collector.handle_message("172.20.10.22", SWITCH, "udp")
            )
            wait_for(lambda: SWITCH in spooled(installed.data, "switch-2"))
            agent.process()
            assert "172.20.10.22" in syslog_addresses(installed)

            def state_of(response: Any) -> str:
                found: str = client.get(response.json()["status_url"]).json()["state"]
                return found

            wait_for(lambda: state_of(added) == "active", timeout=4.0)  # the API's own reload
            active = client.get(added.json()["status_url"]).json()
            steps = {step["step"]: step["state"] for step in active["steps"]}
            assert (steps["reload.collector"], steps["system.firewall"]) == ("done", "done")

            # 3. "Şimdi mühürle" on the failed day, sealed by the Seal task with the mock TSA.
            seal = client.post(f"{V}/seals/{days[0].isoformat()}/seal")
            assert seal.status_code == 202, seal.text
            agent.process()
            assert cli.main(["--config", str(installed.config_path), "seal"]) == 0
            agent.process()
            sealed = client.get(seal.json()["status_url"]).json()
            assert sealed["state"] == "succeeded", sealed
            assert sealed["result"]["status"] == "sealed"
            state = json.loads(installed.data.seal_state(days[0]).read_bytes())
            assert state["status"] == "sealed"
            assert state["requested"]

            # 4. The e-mail server, tried with the typed password before it is used.
            reauthenticate()
            smtp = {
                "etag": etag("smtp"),
                "enabled": True,
                "host": "smtp.ornek.example",
                "port": 587,
                "security": "starttls",
                "username": "arcilogger",
                "from_address": "arcilogger@ornek.example",
                "password": "Örnek-Posta-Parola-5",
                "test_to": "tolga@ornek.example",
            }
            mail = client.put(f"{CONFIG}/smtp", json=smtp)
            assert follow(mail)["state"] == "applied"  # the worker still runs the old file
            worker_round()
            assert state_of(mail) == "active"
            (probe,) = [call for call in agent.prober.calls if call.kind == "smtp"]
            assert probe.secrets == {"smtp.password": "Örnek-Posta-Parola-5"}
            assert installed.store.get("smtp.password") == "Örnek-Posta-Parola-5"

            # 5. Retention held for an hour by the policy, then cancelled.
            installed.write_policy(hold_hours=1)
            retention = {"etag": etag("retention"), "enabled": True, "days": 760}
            preview = client.post(f"{CONFIG}/retention/check", json=retention).json()
            count = preview["retention_preview"]["days_to_delete"]
            held = client.put(
                f"{CONFIG}/retention", json={**retention, "confirm_delete_days": count}
            )
            assert held.json()["guarded"] is True
            pending = follow(held)
            assert pending["state"] == "pending", pending
            cancel = follow(client.delete(f"{V}/admin/requests/{held.json()['request_id']}"))
            assert cancel["state"] == "succeeded", cancel
            assert client.get(held.json()["status_url"]).json()["state"] == "cancelled"
            assert load_config(installed.config_path).retention.enabled is False

            # 6. The 5651 pipeline went on; the audit chain holds every step and verifies.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.bind(("127.0.0.1", 0))
                sender.sendto(FORTIGATE + b" #2", address)
                wait_for(lambda: FORTIGATE + b" #2" in spooled(installed.data, "fortigate"))
            worker_round()
            with sessions() as session:
                actions = set(session.scalars(select(AuditLog.action)))
                assert {
                    "admin.request",
                    "admin.applied",
                    "settings.secret_change",
                    "admin.action",
                    "admin.request_cancel",
                } <= actions
                assert verify_audit_chain(session).ok
                rows = list(session.scalars(select(AdminRequest)))
                assert all(row.terminal for row in rows), [(r.kind, r.state) for r in rows]
    finally:
        collector.stop()
