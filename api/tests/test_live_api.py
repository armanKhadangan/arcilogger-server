"""Canlı Akış end to end (contract 2.14, test plan 3.4): a real Uvicorn server in a thread, a
temporary spool and short hub intervals; the stream is read as the web app reads it.

Opening refuses in plain JSON (query errors, the KVKK notice, the limits, an unreadable spool)
and audits ``live.open``; the stream sends ``hello``, the backfill and new lines with exact raw
references, filters like Arama, follows network members and new unknown senders, hands over at
midnight and survives the sealer deleting a file; rate, queue and parse-budget drops are counted
exactly; a resume replays or announces a ``gap``; the session check ends a stream on revocation,
idleness, a role or KVKK change; keep-alives leave ``last_seen_at`` alone; a stream belongs to
its user and session; the GET holds no database connection while it streams.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.live.hub import Hub, Timing
from arcilogger_api.live.schemas import LiveHello, LiveLine
from arcilogger_api.live.testing import Event, LiveServer, read_events, sign_in, spool_line
from arcilogger_api.testing import EXAMPLE_SITE_NAME, LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day
from arcilogger_db.models import AuditLog

FAST = Timing(
    poll=0.05,
    listing=0.2,
    idle_stop=60.0,
    linger=1.5,
    slow_client=1.0,
    keepalive=0.4,
    session_check=0.3,
    dropped=0.2,
)
FIREWALL = "172.31.255.1"
ROUTER = "172.20.10.1"
STREAM_HEADERS = {"X-Arci-Background": "1", "Accept": "text/event-stream"}


def traffic(srcip: str = "172.20.41.105", action: str = "accept", dstport: int = 443) -> str:
    return (
        '<189>date=2026-10-26 time=10:00:00 devname="FIREWALL-01" devid="FGT0000000000000" '
        'logid="0000000013" type="traffic" subtype="forward" level="notice" vd="root" '
        f'srcip={srcip} srcport=51544 srcintf="port2" dstip=203.0.113.10 dstport={dstport} '
        f'dstintf="wan1" policyid=12 sessionid=3100101 proto=6 action="{action}" '
        'service="HTTPS" sentbyte=1200 rcvdbyte=5400'
    )


def dhcp(ip: str = "172.20.42.57", mac: str = "00:00:5E:00:53:57") -> str:
    return f"<30>2026-10-26T10:00:00.000+03:00 CORE-R1 dhcp,info dhcp42 assigned {ip} for {mac} PLN-PC14"


@pytest.fixture
def api_config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "site_name": EXAMPLE_SITE_NAME,
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": FIREWALL},
                {"name": "core-r1", "type": "mikrotik", "address": ROUTER},
                # the access points: a block of the network-device VLAN 10
                {"name": "ap", "type": "mikrotik", "address": "172.20.10.192/26"},
            ],
        }
    )


class Spool:
    """Writes collector lines into today's (or another day's) spool files."""

    def __init__(self, layout: Layout) -> None:
        self.layout = layout

    def path(self, folder: str, day: date | None = None) -> Path:
        return self.layout.spool_file(folder, day or site_day(datetime.now(UTC)))

    def write(
        self, folder: str, *messages: str, sender: str = FIREWALL, day: date | None = None
    ) -> Path:
        path = self.path(folder, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            for message in messages:
                handle.write(spool_line(message, sender))
        return path


@pytest.fixture
def spool(layout: Layout) -> Spool:
    return Spool(layout)


@pytest.fixture
def hub(app: FastAPI) -> Hub:
    created = Hub(app.state.arci, timing=FAST)
    app.state.arci_live = created
    return created


@pytest.fixture
def server(app: FastAPI, hub: Hub) -> Iterator[LiveServer]:
    with LiveServer(app) as live:
        yield live


@pytest.fixture
def http(server: LiveServer) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=server.url, timeout=10) as client:
        yield client


Clients = Callable[..., tuple[httpx.Client, LoggedIn]]


@pytest.fixture
def clients(server: LiveServer, app_session_factory: sessionmaker[Session]) -> Iterator[Clients]:
    """``clients(role, **user)``: a new signed-in client of its own (cookies are per client)."""
    opened: list[httpx.Client] = []

    def make(
        role: str = "admin", *, notice: bool = True, **user: Any
    ) -> tuple[httpx.Client, LoggedIn]:
        client = httpx.Client(base_url=server.url, timeout=10)
        opened.append(client)
        return client, sign_in(client, app_session_factory, role, notice=notice, **user)

    yield make
    for client in opened:
        client.close()


def open_stream(client: httpx.Client, q: str = "", backfill: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"q": q}
    if backfill is not None:
        body["backfill"] = backfill
    response = client.post("/api/v1/live/streams", json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


class Reader:
    """Reads a stream in a thread, so a test can write lines and wait for events."""

    def __init__(
        self, client: httpx.Client, stream_id: str, last_event_id: str | None = None
    ) -> None:
        headers = dict(STREAM_HEADERS)
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id
        self.events: list[Event] = []
        self.keepalives = 0
        self.status: int | None = None
        self.error: dict[str, Any] | None = None
        self.headers: httpx.Headers | None = None
        self.done = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run,
            args=(client, f"/api/v1/live/streams/{stream_id}", headers),
            daemon=True,
        )
        self._thread.start()

    def _run(self, client: httpx.Client, url: str, headers: dict[str, str]) -> None:
        try:
            with client.stream("GET", url, headers=headers) as response:
                self.status, self.headers = response.status_code, response.headers
                if response.status_code != 200:
                    response.read()
                    self.error = response.json()["error"]
                    return
                for event in read_events(response):
                    with self._lock:
                        if event is None:
                            self.keepalives += 1
                        else:
                            self.events.append(event)
                    if self._stop.is_set():
                        return
        except httpx.HTTPError:
            pass
        finally:
            self.done.set()

    def of(self, kind: str) -> list[Event]:
        with self._lock:
            return [event for event in self.events if event.event == kind]

    def lines(self) -> list[dict[str, Any]]:
        return [event.data for event in self.of("line")]

    def wait(self, check: Callable[[Reader], bool], seconds: float = 5.0) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if check(self):
                return
            if self.done.is_set():
                break
            time.sleep(0.02)
        assert check(self), (
            f"waited {seconds} s: {self.events[-5:]} status={self.status} {self.error}"
        )

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def audits(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )
        for row in rows:
            session.expunge(row)
        return rows


def eventually(check: Callable[[], bool], seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    assert check()


# -- opening -------------------------------------------------------------------------------------


def test_a_stream_shows_the_backfill_and_new_lines_with_raw_references(
    clients: Clients, spool: Spool, layout: Layout, app_session_factory: sessionmaker[Session]
) -> None:
    client, who = clients("auditor")
    for number in range(3):
        spool.write("fortigate", traffic(srcip=f"172.20.41.{100 + number}"))
    opened = open_stream(client, backfill=2)
    assert opened["folders"] == [
        {"folder": "fortigate", "day": spool.path("fortigate").stem, "start_line": 3}
    ]
    assert opened["limits"] == {
        "max_lines_per_second": 100,
        "keepalive_seconds": 0,
        "buffer_lines": 5000,
        "max_minutes": 60,
    }
    reader = Reader(client, opened["stream_id"])
    try:
        reader.wait(lambda r: len(r.lines()) == 2)
        hello = LiveHello.model_validate(reader.of("hello")[0].data)
        assert (hello.stream_id, hello.epoch) == (opened["stream_id"], opened["epoch"])
        assert [line["line"] for line in reader.lines()] == [2, 3]  # the backfill
        spool.write("fortigate", traffic(srcip="172.20.41.200", action="deny"))
        reader.wait(lambda r: len(r.lines()) == 3)
        line = LiveLine.model_validate(reader.lines()[-1])
        assert (line.source, line.line, line.kind, line.type, line.parsed) == (
            "fortigate",
            4,
            "traffic",
            "traffic",
            True,
        )
        assert line.summary.srcip == "172.20.41.200"
        assert line.summary.action == "deny"
        assert line.sender == FIREWALL
        assert "srcip=172.20.41.200" in line.text
        assert reader.of("line")[-1].id == f"{opened['epoch']}:{line.seq}"
        stored = spool.path("fortigate").read_bytes()[line.offset :].split(b"\n", 1)[0]
        assert stored.endswith(traffic(srcip="172.20.41.200", action="deny").encode())
        # the raw drawer opens the line by its reference
        raw = client.get(
            "/api/v1/raw",
            params={
                "source": "fortigate",
                "day": str(line.day),
                "line": line.line,
                "offset": line.offset,
            },
        )
        assert raw.status_code == 200, raw.text
        assert raw.json()["line"] == 4
    finally:
        reader.close()
    opens = audits(app_session_factory, "live.open")
    assert len(opens) == 1
    assert opens[0].actor_id == who.user_id
    assert opens[0].details == {
        "stream_id": opened["stream_id"],
        "q": "",
        "folders": ["fortigate"],
        "backfill": 2,
    }
