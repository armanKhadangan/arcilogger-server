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

import socket
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import live
from arcilogger_api.errors import MESSAGES as API_MESSAGES
from arcilogger_api.live import messages as live_messages
from arcilogger_api.live.hub import Hub, Timing
from arcilogger_api.live.schemas import LiveDropped, LiveEnd, LiveGap, LiveHello, LiveLine
from arcilogger_api.live.testing import Event, LiveServer, read_events, sign_in, spool_line
from arcilogger_api.routers import sources as sources_router
from arcilogger_api.search import messages as search_messages
from arcilogger_api.security import hash_token, session_problem
from arcilogger_api.testing import EXAMPLE_SITE_NAME, LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import day_window, site_day
from arcilogger_db.models import AuditLog, User, UserSession
from arcilogger_db.settings import set_setting

# Most streams here are opened and never read, so they linger from the moment they open; a
# short linger ran out on the Windows runner while a test was still counting or closing them.
# Lingering out is tested in test_live_hub.py on a controlled clock.
FAST = Timing(
    poll=0.05,
    listing=0.2,
    idle_stop=60.0,
    linger=60.0,
    slow_client=1.0,
    keepalive=0.4,
    session_check=0.3,
    dropped=0.2,
)
FIREWALL = "172.31.255.1"
ROUTER = "172.20.10.1"
STREAM_HEADERS = {"X-Arci-Background": "1", "Accept": "text/event-stream"}
LIVE_ENDPOINTS = (
    ("POST", "/api/v1/live/streams"),
    ("GET", "/api/v1/live/streams/ornek-akis"),
    ("DELETE", "/api/v1/live/streams/ornek-akis"),
    ("GET", "/api/v1/live/status"),
)


def traffic(srcip: str = "172.20.41.105", action: str = "accept", dstport: int = 443) -> str:
    return (
        '<189>date=2026-10-26 time=10:00:00 devname="FIREWALL-01" devid="FGT0000000000000" '
        'logid="0000000013" type="traffic" subtype="forward" level="notice" vd="root" '
        f'srcip={srcip} srcport=51544 srcintf="port2" dstip=203.0.113.10 dstport={dstport} '
        f'dstintf="wan1" policyid=12 sessionid=3100101 proto=6 action="{action}" '
        'service="HTTPS" sentbyte=1200 rcvdbyte=5400'
    )


def dhcp(ip: str = "172.20.42.57", mac: str = "00:00:5E:00:53:57") -> str:
    return (
        "<30>2026-10-26T10:00:00.000+03:00 CORE-R1 dhcp,info dhcp42 "
        f"assigned {ip} for {mac} PLN-PC14"
    )


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


#: A test's spool and hub share one wall clock (:class:`Clock`). On the real clock a site
#: midnight passing mid-test put the later lines into a new file numbered from 1, and the days,
#: line numbers and ranges a test asserts split across two files. A test here runs for seconds,
#: more on the Windows runner; the clock starts at the next site midnight when that is nearer
#: than this. The move stays well below the session idle limit (``auth.idle_minutes``, 15 by
#: default) the hub's session checks apply to the real-time sessions the tests sign in with.
NEAR_MIDNIGHT = timedelta(minutes=2)


class Clock:
    """Real time, from the next site midnight on when that is nearer than :data:`NEAR_MIDNIGHT`."""

    def __init__(self) -> None:
        real = datetime.now(UTC)
        midnight = day_window(site_day(real))[1]
        self.offset = midnight - real if midnight - real < NEAR_MIDNIGHT else timedelta(0)

    def now(self) -> datetime:
        return datetime.now(UTC) + self.offset


class Spool:
    """Writes collector lines into today's (or another day's) spool files."""

    def __init__(self, layout: Layout, clock: Clock) -> None:
        self.layout, self.clock = layout, clock

    def path(self, folder: str, day: date | None = None) -> Path:
        return self.layout.spool_file(folder, day or site_day(self.clock.now()))

    def write(
        self, folder: str, *messages: str, sender: str = FIREWALL, day: date | None = None
    ) -> Path:
        path = self.path(folder, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as handle:
            for message in messages:
                handle.write(spool_line(message, sender, self.clock.now()))
        return path


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def spool(layout: Layout, clock: Clock) -> Spool:
    return Spool(layout, clock)


@pytest.fixture
def hub(app: FastAPI, clock: Clock) -> Hub:
    created = Hub(app.state.arci, timing=FAST, now=clock.now)
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


def open_stream(client: httpx.Client, q: str = "", backfill: int | None = 0) -> dict[str, Any]:
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


def test_the_stream_filters_like_arama(clients: Clients, spool: Spool) -> None:
    client, _ = clients()
    spool.write("fortigate", traffic())  # the folders exist before the streams open
    spool.write("core-r1", dhcp(), sender=ROUTER)
    streams = {
        q: open_stream(client, q)
        for q in ("srcip:172.20.41.0/24 -action:accept", "type:dhcp mac:00:00:5e:00:53:57")
    }
    readers = {q: Reader(client, opened["stream_id"]) for q, opened in streams.items()}
    try:
        spool.write(
            "fortigate",
            traffic(action="deny"),
            traffic(),
            traffic(srcip="172.20.43.9", action="deny"),
        )
        spool.write("core-r1", dhcp(mac="00:00:5E:00:53:58"), dhcp(), sender=ROUTER)
        first, second = readers.values()
        first.wait(lambda r: len(r.lines()) == 1)
        second.wait(lambda r: len(r.lines()) == 1)
        time.sleep(0.3)
        assert [(line["source"], line["line"]) for line in first.lines()] == [("fortigate", 2)]
        assert [(line["source"], line["line"]) for line in second.lines()] == [("core-r1", 3)]
        assert second.lines()[0]["summary"]["mac"].lower() == "00:00:5e:00:53:57"
        assert second.lines()[0]["kind"] == "dhcp"
    finally:
        for reader in readers.values():
            reader.close()


def test_new_unknown_senders_and_network_members_appear(clients: Clients, spool: Spool) -> None:
    client, _ = clients()
    opened = open_stream(client, "source:unknown-*,ap")
    reader = Reader(client, opened["stream_id"])
    try:
        reader.wait(lambda r: bool(r.of("hello")))
        spool.write(
            "unknown-198.51.100.23",
            "<13>Oct 26 10:00:00 yeni-cihaz test mesaji",
            sender="198.51.100.23",
        )
        spool.write(
            "ap-172.20.10.200",
            "<30>2026-10-26T10:00:00.000+03:00 AP-2 wireless,info deneme",
            sender="172.20.10.200",
        )
        spool.write("fortigate", traffic())
        reader.wait(lambda r: len(r.lines()) == 2, seconds=5)
        assert sorted(line["source"] for line in reader.lines()) == [
            "ap-172.20.10.200",
            "unknown-198.51.100.23",
        ]
        unknown = next(line for line in reader.lines() if line["source"].startswith("unknown-"))
        # its raw drawer opens before ingest has seen the sender (R26)
        raw = client.get(
            "/api/v1/raw",
            params={"source": unknown["source"], "day": unknown["day"], "line": 1, "offset": 0},
        )
        assert raw.status_code == 200, raw.text
    finally:
        reader.close()


def test_rate_limited_lines_are_reported_as_dropped(
    clients: Clients, spool: Spool, app_session_factory: sessionmaker[Session]
) -> None:
    with app_session_factory() as session:
        set_setting(session, "live.max_lines_per_second", 10, updated_by="test")
        session.commit()
    client, _ = clients()
    spool.write("fortigate", traffic())
    opened = open_stream(client)
    reader = Reader(client, opened["stream_id"])
    try:
        reader.wait(lambda r: bool(r.of("hello")))
        spool.write("fortigate", *(traffic() for _ in range(60)))
        reader.wait(
            lambda r: sum(e.data["rate_limited"] for e in r.of("dropped")) + len(r.lines()) == 60
        )
        dropped = [LiveDropped.model_validate(event.data) for event in reader.of("dropped")]
        assert sum(item.rate_limited for item in dropped) >= 40
        assert "saniyede en fazla 10 satır gösterilir" in dropped[0].message_tr
        assert "at most 10 lines a second are shown" in dropped[0].message_en
    finally:
        reader.close()


def test_pause_and_resume_replay_what_was_missed(clients: Clients, spool: Spool, hub: Hub) -> None:
    client, _ = clients()
    spool.write("fortigate", traffic())
    opened = open_stream(client, "action:deny", backfill=0)
    reader = Reader(client, opened["stream_id"])
    reader.wait(lambda r: bool(r.of("hello")))
    spool.write("fortigate", traffic(action="deny"))
    reader.wait(lambda r: len(r.lines()) == 1)
    last_id = reader.of("line")[-1].id
    stream = hub.streams[opened["stream_id"]]
    reader.close()  # Duraklat: the connection goes, the stream lingers
    # The server has seen the pause and the hub has read the three lines: they are replayed
    # from the buffer, not sent live to a resume that came before them.
    eventually(lambda: stream.connection is None)
    seq = hub.seq
    spool.write("fortigate", traffic(action="deny"), traffic(), traffic(action="deny"))
    eventually(lambda: hub.seq >= seq + 3)
    resumed = Reader(client, opened["stream_id"], last_event_id=last_id)
    try:
        resumed.wait(lambda r: len(r.lines()) == 2)
        assert [line["line"] for line in resumed.lines()] == [3, 5]
        assert resumed.of("gap") == []
    finally:
        resumed.close()
    # a resume from another hub (the API restarted) cannot replay
    other = Reader(client, opened["stream_id"], last_event_id="baska-bir-epoch:3")
    try:
        other.wait(lambda r: bool(r.of("gap")))
        gap = LiveGap.model_validate(other.of("gap")[0].data)
        assert (gap.reason, gap.missed) == ("hub_restarted", None)
        assert "Arama'da" in gap.message_tr
    finally:
        other.close()


def test_a_resume_after_the_buffer_moved_on_announces_a_gap(
    clients: Clients, spool: Spool, app_session_factory: sessionmaker[Session], hub: Hub
) -> None:
    with app_session_factory() as session:
        set_setting(session, "live.buffer_lines", 500, updated_by="test")
        session.commit()
    client, _ = clients()
    spool.write("fortigate", traffic())
    opened = open_stream(client, "action:deny", backfill=0)
    reader = Reader(client, opened["stream_id"])
    reader.wait(lambda r: bool(r.of("hello")))
    spool.write("fortigate", traffic(action="deny"))
    reader.wait(lambda r: len(r.lines()) == 1)
    last_id = reader.of("line")[-1].id
    stream = hub.streams[opened["stream_id"]]
    reader.close()
    # Wait for the hub's state, not a sleep: the server has seen the pause (the deny lines go
    # to the stream's backlog, not to the closed connection), and the hub has read and buffered
    # all 603 lines before the resume. One read cycle of 603 parsed lines took 0.3 s on Linux
    # with coverage; a resume before it ends rightly gets the three lines live, without a gap.
    eventually(lambda: stream.connection is None)
    seq = hub.seq
    spool.write("fortigate", *(traffic(action="deny") for _ in range(3)))
    spool.write("fortigate", *(traffic() for _ in range(600)))  # pushes them out of the buffer
    eventually(lambda: hub.seq >= seq + 603)
    resumed = Reader(client, opened["stream_id"], last_event_id=last_id)
    try:
        resumed.wait(lambda r: bool(r.of("gap")))
        gap = LiveGap.model_validate(resumed.of("gap")[0].data)
        assert (gap.reason, gap.missed) == ("resume_too_late", 3)
    finally:
        resumed.close()


def test_closing_audits_the_delivered_ranges(
    clients: Clients, spool: Spool, app_session_factory: sessionmaker[Session]
) -> None:
    client, who = clients("auditor")
    spool.write("fortigate", traffic())
    opened = open_stream(client, backfill=1)
    reader = Reader(client, opened["stream_id"])
    reader.wait(lambda r: len(r.lines()) == 1)
    spool.write("fortigate", traffic(), traffic())
    reader.wait(lambda r: len(r.lines()) == 3)
    response = client.delete(f"/api/v1/live/streams/{opened['stream_id']}")
    assert response.status_code == 204
    reader.done.wait(5)
    reader.close()
    closes = audits(app_session_factory, "live.close")
    assert len(closes) == 1
    details = closes[0].details
    assert details["reason"] == "closed"
    assert details["delivered"] == 3
    assert details["ranges"] == [
        {"folder": "fortigate", "day": spool.path("fortigate").stem, "from_line": 1, "to_line": 3}
    ]
    assert closes[0].actor_id == who.user_id
    again = client.get(f"/api/v1/live/streams/{opened['stream_id']}", headers=STREAM_HEADERS)
    assert (again.status_code, again.json()["error"]["code"]) == (404, "live_stream_not_found")


def test_a_stream_belongs_to_its_user_and_session(
    clients: Clients, spool: Spool, app_session_factory: sessionmaker[Session], server: LiveServer
) -> None:
    owner_client, owner = clients()
    opened = open_stream(owner_client)
    url = f"/api/v1/live/streams/{opened['stream_id']}"
    stranger, _ = clients()
    assert stranger.get(url, headers=STREAM_HEADERS).status_code == 404
    assert stranger.delete(url).status_code == 404
    # the same user in another session (another browser)
    with httpx.Client(base_url=server.url, timeout=10) as other_session:
        with app_session_factory() as session:
            from arcilogger_api.security import create_session

            issued = create_session(session, session.get_one(User, owner.user_id), mfa_passed=True)
            session.commit()
        other_session.cookies.set("arci_session", issued.token)
        other_session.cookies.set("arci_csrf", issued.csrf_token)
        other_session.headers["X-CSRF-Token"] = issued.csrf_token
        response = other_session.get(url, headers=STREAM_HEADERS)
        assert (response.status_code, response.json()["error"]["code"]) == (
            404,
            "live_stream_not_found",
        )
    assert owner_client.delete(url).status_code == 204


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ("revoke", "session_revoked"),
        ("idle", "session_idle"),
        ("role", "permission_changed"),
        ("notice", "privacy_notice_required"),
        ("inactive", "user_inactive"),
    ],
)
def test_the_session_check_ends_the_stream(
    clients: Clients,
    spool: Spool,
    app_session_factory: sessionmaker[Session],
    change: str,
    code: str,
) -> None:
    client, who = clients()
    opened = open_stream(client)
    reader = Reader(client, opened["stream_id"])
    reader.wait(lambda r: bool(r.of("hello")))
    token = hash_token(who.session_token)
    with app_session_factory() as session:
        if change == "revoke":
            session.execute(
                update(UserSession)
                .where(UserSession.token_hash == token)
                .values(revoked_at=datetime.now(UTC))
            )
        elif change == "idle":
            back = datetime.now(UTC) - timedelta(minutes=31)
            session.execute(
                update(UserSession).where(UserSession.token_hash == token).values(last_seen_at=back)
            )
        elif change == "role":
            session.execute(update(User).where(User.id == who.user_id).values(role="hr"))
        elif change == "notice":
            set_setting(session, "privacy.notice_version", "2", updated_by="test")
        else:
            session.execute(update(User).where(User.id == who.user_id).values(active=False))
        session.commit()
    try:
        reader.wait(lambda r: bool(r.of("end")))
        end = LiveEnd.model_validate(reader.of("end")[0].data)
        assert end.code == code
        assert end.message_tr
        assert end.message_en
        reader.done.wait(5)
        assert reader.done.is_set(), "the response ends after the end event"
    finally:
        reader.close()
    eventually(
        lambda: (
            [entry.details["reason"] for entry in audits(app_session_factory, "live.close")]
            == [code]
        )
    )


def test_keepalives_never_count_as_activity(
    clients: Clients, spool: Spool, app_session_factory: sessionmaker[Session]
) -> None:
    client, who = clients()
    opened = open_stream(client)
    token = hash_token(who.session_token)
    with app_session_factory() as session:
        seen = session.scalars(
            select(UserSession.last_seen_at).where(UserSession.token_hash == token)
        ).one()
    reader = Reader(client, opened["stream_id"])
    try:
        reader.wait(lambda r: r.keepalives >= 3, seconds=5)  # and several session checks
        status = client.get("/api/v1/live/status", headers={"X-Arci-Background": "1"})
        assert status.status_code == 200
        assert status.json()["streams"] == 1
    finally:
        reader.close()
    with app_session_factory() as session:
        after = session.scalars(
            select(UserSession.last_seen_at).where(UserSession.token_hash == token)
        ).one()
    assert after == seen


def test_the_stream_holds_no_database_connection(
    app: FastAPI, clients: Clients, spool: Spool, hub: Hub
) -> None:
    hub.timing = Timing(poll=0.05, listing=0.2, keepalive=0.2, session_check=60.0)
    engine: Engine = app.state.arci.engine
    client, _ = clients()
    spool.write("fortigate", traffic())
    opened = open_stream(client)
    reader = Reader(client, opened["stream_id"])
    try:
        reader.wait(lambda r: r.keepalives >= 1)
        for _ in range(5):
            spool.write("fortigate", traffic())
            time.sleep(0.1)
            assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
        reader.wait(lambda r: len(r.lines()) == 5)
        assert reader.headers is not None
        assert reader.headers["content-type"] == "text/event-stream; charset=utf-8"
        assert reader.headers["cache-control"] == "no-store"
        assert reader.headers["x-accel-buffering"] == "no"
    finally:
        reader.close()


def test_a_client_that_stops_reading_is_ended_and_audited(
    clients: Clients,
    spool: Spool,
    server: LiveServer,
    hub: Hub,
    app_session_factory: sessionmaker[Session],
) -> None:
    """A client that keeps the connection open but never reads (its TCP window stays at zero)
    holds no write for ever: within the slow-client time the stream ends with ``slow_client``,
    ``live.close`` is written, the slot is free and the server ends the response."""
    client, who = clients()
    opened = open_stream(client)
    cookies = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
    request = (
        f"GET /api/v1/live/streams/{opened['stream_id']} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{server.port}\r\nCookie: {cookies}\r\n"
        "X-Arci-Background: 1\r\nAccept: text/event-stream\r\n\r\n"
    )
    long_line = traffic() + ' msg="' + "ornek " * 1000 + '"'
    with socket.socket() as stalled:
        stalled.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        stalled.connect(("127.0.0.1", server.port))
        stalled.sendall(request.encode())
        deadline = time.monotonic() + 15
        while not audits(app_session_factory, "live.close") and time.monotonic() < deadline:
            spool.write("fortigate", *([long_line] * 20))
            time.sleep(0.1)
        (closed,) = audits(app_session_factory, "live.close")
        assert closed.details["stream_id"] == opened["stream_id"]
        assert closed.details["reason"] == "slow_client"
        assert hub.count_streams(who.user_id) == (0, 0)
        # The response ends: once the client reads, the buffered bytes arrive, then the end.
        stalled.settimeout(10)
        while stalled.recv(1 << 16):
            pass


def test_a_server_stop_ends_the_stream_and_audits_it(
    app: FastAPI, hub: Hub, spool: Spool, app_session_factory: sessionmaker[Session]
) -> None:
    """The service stopping (contract 2.15, R16): the open stream gets ``end {server_stopping}``,
    the server is down within its graceful timeout plus the 1 s of that last write, and
    ``live.close`` records ``server_stopping``."""
    spool.write("fortigate", traffic())
    graceful = 1
    stopping = LiveServer(app, graceful_seconds=graceful)
    with stopping, httpx.Client(base_url=stopping.url, timeout=10) as client:
        sign_in(client, app_session_factory)
        opened = open_stream(client)
        reader = Reader(client, opened["stream_id"])
        reader.wait(lambda r: bool(r.of("hello")))
        began = time.monotonic()
        stopping.__exit__(None, None, None)  # what the service stop does (repeating it is harmless)
        took = time.monotonic() - began
        reader.done.wait(5)
        reader.close()
    assert not stopping.thread.is_alive()
    assert [LiveEnd.model_validate(event.data).code for event in reader.of("end")] == [
        "server_stopping"
    ]
    # graceful + the 1 s of that last write, plus room for the lifespan shutdown, the close audit
    # and the thread join on a slow runner (Windows took 2.1 s); a shielded last write without its
    # 1 s bound would hold the server until the 20 s join instead
    assert took < graceful + 1 + 2, took
    closes = audits(app_session_factory, "live.close")
    assert [entry.details["reason"] for entry in closes] == ["server_stopping"]
    assert closes[0].details["stream_id"] == opened["stream_id"]


# -- refusals ------------------------------------------------------------------------------------


def test_opening_refusals_are_plain_json(
    clients: Clients, spool: Spool, layout: Layout, app_session_factory: sessionmaker[Session]
) -> None:
    client, _ = clients()
    for q, code in (
        ("banana:1", "search_unknown_field"),
        ("vlan:99", "search_vlan_unknown"),
        ("srcip:192.0.2.4 OR srcip:192.0.2.5", "search_or_unsupported"),
        ("ab", "search_term_too_short"),
    ):
        response = client.post("/api/v1/live/streams", json={"q": q})
        assert (response.status_code, response.json()["error"]["code"]) == (422, code), q
        assert "start" in response.json()["error"]["details"]
    for body in ({"q": "x" * 1001}, {"backfill": 51}, {"backfill": -1}, {"q": "", "since": 1}):
        response = client.post("/api/v1/live/streams", json=body)
        assert (response.status_code, response.json()["error"]["code"]) == (
            422,
            "validation_error",
        ), body
    first, second = open_stream(client, backfill=None), open_stream(client)
    response = client.post("/api/v1/live/streams", json={})
    assert (response.status_code, response.json()["error"]["code"]) == (429, "live_stream_limit")
    assert "en fazla 2 canlı akış" in response.json()["error"]["message_tr"]
    assert "server total 4" in response.json()["error"]["message_en"]
    for opened in (first, second):
        assert client.delete(f"/api/v1/live/streams/{opened['stream_id']}").status_code == 204
    others = [clients()[0] for _ in range(2)]
    for other in others:
        open_stream(other)
        open_stream(other)
    response = client.post("/api/v1/live/streams", json={})
    assert (response.status_code, response.json()["error"]["code"]) == (429, "live_stream_limit")
    opens = audits(app_session_factory, "live.open")
    assert len(opens) == 6
    assert opens[0].details["backfill"] == 50  # the default: live.backfill_lines


def test_concurrent_opens_never_overshoot_the_limits(
    clients: Clients, spool: Spool, server: LiveServer, hub: Hub
) -> None:
    """Two tabs (or two automatic re-opens) at once: the limit is checked where the stream is
    registered, with no ``await`` between, so exactly the allowed number open."""
    spool.write("fortigate", traffic())
    client, who = clients()
    for _ in range(5):
        start = threading.Barrier(4)
        statuses: list[int] = []

        def post(statuses: list[int] = statuses, start: threading.Barrier = start) -> None:
            with httpx.Client(
                base_url=server.url, timeout=10, cookies=client.cookies, headers=client.headers
            ) as tab:
                start.wait()
                statuses.append(tab.post("/api/v1/live/streams", json={"q": ""}).status_code)

        tabs = [threading.Thread(target=post) for _ in range(4)]
        for tab in tabs:
            tab.start()
        for tab in tabs:
            tab.join()
        assert sorted(statuses) == [201, 201, 429, 429]
        assert hub.count_streams(who.user_id) == (2, 2)
        for stream in list(hub.streams.values()):
            assert client.delete(f"/api/v1/live/streams/{stream.id}").status_code == 204


def test_an_unreadable_spool_folder_is_503(clients: Clients, layout: Layout) -> None:
    layout.root.mkdir(parents=True, exist_ok=True)
    layout.spool.write_text("not a folder", encoding="utf-8")
    client, _ = clients()
    response = client.post("/api/v1/live/streams", json={})
    assert (response.status_code, response.json()["error"]["code"]) == (503, "live_unavailable")
    assert "Ham kayıt klasörü okunamıyor" in response.json()["error"]["message_tr"]


@pytest.mark.parametrize(
    ("role", "notice", "code"),
    [
        ("hr", True, "forbidden"),
        ("viewer", True, "forbidden"),
        ("auditor", False, "privacy_notice_required"),
    ],
)
def test_permissions_and_the_kvkk_notice(
    clients: Clients, spool: Spool, role: str, notice: bool, code: str
) -> None:
    client, _ = clients(role, notice=notice)
    response = client.post("/api/v1/live/streams", json={})
    assert (response.status_code, response.json()["error"]["code"]) == (403, code)
    if role in ("hr", "viewer"):
        for method, path in LIVE_ENDPOINTS:
            response = client.request(method, path, headers=STREAM_HEADERS)
            assert (response.status_code, response.json()["error"]["code"]) == (
                403,
                "forbidden",
            ), path


@pytest.mark.parametrize(("method", "path"), LIVE_ENDPOINTS)
def test_a_password_change_comes_first(
    clients: Clients, spool: Spool, method: str, path: str
) -> None:
    client, _ = clients(must_change_password=True)
    response = client.request(
        method, path, json={} if method == "POST" else None, headers=STREAM_HEADERS
    )
    assert (response.status_code, response.json()["error"]["code"]) == (
        403,
        "password_change_required",
    )


def test_csrf_is_required_to_open_and_close(clients: Clients, spool: Spool) -> None:
    client, _ = clients()
    spool.write("fortigate", traffic())
    opened = open_stream(client)
    token = client.headers.pop("X-CSRF-Token")
    response = client.post("/api/v1/live/streams", json={})
    assert (response.status_code, response.json()["error"]["code"]) == (403, "csrf_failed")
    url = f"/api/v1/live/streams/{opened['stream_id']}"
    response = client.delete(url)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "csrf_failed")
    client.headers["X-CSRF-Token"] = token
    assert client.delete(url).status_code == 204  # it was still open


# -- texts and the session check -----------------------------------------------------------------


def test_every_text_has_both_languages_with_the_same_placeholders() -> None:
    """The three catalogues of contract 2.20 (search, live, sources)."""
    catalogues: list[dict[str, tuple[str, str]]] = [
        dict(search_messages.MESSAGES),
        dict(search_messages.NOTICES),
        dict(live_messages.MESSAGES),
        dict(live_messages.END_MESSAGES),
        dict(live_messages.DROPPED_REASONS),
        {"dropped": live_messages.DROPPED_MESSAGE, "gap": live_messages.GAP_MESSAGE},
        dict(sources_router.MESSAGES),
    ]
    for catalogue in catalogues:
        for code, (text_tr, text_en) in catalogue.items():
            assert text_tr.strip(), code
            assert text_en.strip(), code
            assert search_messages.placeholders(text_tr) == search_messages.placeholders(text_en), (
                code
            )
    assert set(live_messages.STATUS) == set(live_messages.MESSAGES)
    for code in live_messages.SESSION_END_CODES:
        assert all(live_messages.end_texts(code)), code
    assert live_messages.end_texts("time_limit", minutes=60)[0].startswith("Canlı akış 60 dakika")


def test_session_problem_refuses_what_authentication_refuses(
    client: TestClient,
    login_as: Callable[..., LoggedIn],
    app_session_factory: sessionmaker[Session],
) -> None:
    """``session_problem`` (the stream's 30 s check) gives the code ``get_current_user`` answers
    with, for every way a session can end (contract 2.14.6: a behaviour-preserving extraction)."""
    now = datetime.now(UTC)
    changes: dict[str, Callable[[Session, LoggedIn], None]] = {
        "ok": lambda session, who: None,
        "session_revoked": lambda session, who: _session(session, who, revoked_at=now),
        "session_expired": lambda session, who: _session(
            session, who, expires_absolute=now - timedelta(seconds=1)
        ),
        "session_idle": lambda session, who: _session(
            session, who, last_seen_at=now - timedelta(minutes=31)
        ),
        "user_inactive": lambda session, who: _user(session, who, active=False),
        "mfa_required": lambda session, who: _session(session, who, mfa_passed=False),
    }
    for expected, change in changes.items():
        who = login_as(client, "auditor")
        with app_session_factory() as session:
            change(session, who)
            session.commit()
        with app_session_factory() as session:
            found = session_problem(session, hash_token(who.session_token), datetime.now(UTC))
        response = client.get("/api/v1/live/status", headers={"X-Arci-Background": "1"})
        if expected == "ok":
            assert found is None
            assert response.status_code == 200
        else:
            assert found == expected
            assert (response.status_code, response.json()["error"]["code"]) == (401, expected)
    with app_session_factory() as session:
        assert session_problem(session, "0" * 64, now) == "session_invalid"


def _session(session: Session, who: LoggedIn, **values: Any) -> None:
    session.execute(
        update(UserSession)
        .where(UserSession.token_hash == hash_token(who.session_token))
        .values(**values)
    )


def _user(session: Session, who: LoggedIn, **values: Any) -> None:
    session.execute(update(User).where(User.id == who.user_id).values(**values))


def test_the_package_documents_its_parts() -> None:
    assert live.__doc__ is not None
    assert "hub" in live.__doc__
    assert API_MESSAGES["session_idle"][0]
