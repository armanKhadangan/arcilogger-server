"""The Canlı Akış and Kaynaklar samples (``web/e2e/samples/live.json``, ``sources.json``).

Captured from the real live tail and sources API: ``capture_api_samples.py`` calls
:func:`capture` on its main site after the other pages' samples, with the capture (the app, its
database and data directory with the raw spool, the request helpers ``json``, ``error``,
``file``) and three signed-in clients: an administrator, an auditor and an administrator who has
not accepted the KVKK notice. It returns ``{sample file name: data}``; a file whose data is empty
is not written (and ``--check`` expects none).

The capture's "today" is 13.10.2026; the module writes today's spool lines itself (the
firewall's traffic, the core router's DHCP, an unknown sender's first lines, a line that is not
in the collector's format), so both pages read what the collector would have written.

What ``live.json`` holds (contract 2.14.4): ``open`` (the administrator's and the auditor's
``POST /live/streams``), ``events`` — the server-sent events as the page reads them, each
``{event, id, data}``: a stream's ``hello``, backfill and new lines (parsed, beyond the parse
budget, not in spool format), a ``dropped`` event (the rate limit), a ``gap`` (a resume the hub
cannot serve) and an ``end`` (``time_limit``) —, ``status`` and ``errors`` (query errors, the
stream limit, the KVKK notice, an unknown stream, permissions, validation). The stream is read
through a real Uvicorn server in this process (:class:`arcilogger_api.live.testing.LiveServer`):
a ``TestClient`` cannot read an endless response.

What ``sources.json`` holds (contract 2.16, 2.17): ``overview``, ``detail`` of the firewall, the
core router and the unknown sender, ``tail`` (the core router's last lines, a source without a
file), ``dashboard`` (``GET /dashboard/summary`` with today's parse failures, for the Panel's
"Ayrıştırma hataları" card) and ``errors``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import httpx2 as httpx

from arcilogger_api.live.hub import Hub, Timing
from arcilogger_api.live.testing import LiveServer, read_events
from arcilogger_api.routers.sources import state_of
from arcilogger_api.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.models import IngestOffset, ParseFailure, Source
from arcilogger_db.settings import DEFAULT_SETTINGS

if TYPE_CHECKING:
    from capture_api_samples import Capture

#: Keys this module's files compare differently, added to the script's defaults:
#: ``{file: (data-map keys, enum keys)}`` (``capture_api_samples.MAP_KEYS``, ``ENUM_KEYS``).
#: The pages map the event names, the drop and gap reasons, the origins and the source types to
#: labels and branches.
SHAPE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "live": (frozenset(), frozenset({"event", "reason"})),
    "sources": (frozenset(), frozenset({"origin", "last_message_origin", "reason", "type"})),
}

API = "/api/v1"
FIREWALL = "172.31.255.1"
ROUTER = "172.20.10.1"
#: An unknown sender of the example world (RFC 5737 documentation address).
NEWCOMER_SENDER = "198.51.100.23"
UNKNOWN = f"unknown-{NEWCOMER_SENDER}"
#: The settings this module changes; put back to their defaults at the end.
CHANGED = ("live.max_lines_per_second",)
#: Shorter hub intervals than production, so the capture does not wait for them (the
#: keep-alive interval the samples show stays the production one).
TIMING = Timing(poll=0.1, listing=0.3)
WAIT_SECONDS = 15.0


def _traffic(n: int, action: str = "accept") -> bytes:
    return (
        '<189>date=2026-10-13 time=07:00:00 devname="FIREWALL-01" devid="FGT0000000000000" '
        'logid="0000000013" type="traffic" subtype="forward" level="notice" vd="root" '
        f'srcip=172.20.42.57 srcport={50000 + n} srcintf="port2" dstip=203.0.113.10 '
        f'dstport=443 dstintf="wan1" policyid=12 sessionid={3200000 + n} proto=6 '
        f'action="{action}" service="HTTPS" sentbyte=1200 rcvdbyte=5400'
    ).encode()


def _dhcp(n: int) -> bytes:
    return (
        f"<30>2026-10-13T07:00:{n % 60:02d}.000+03:00 CORE-R1 dhcp,info dhcp42 assigned "
        "172.20.42.57 for 00:00:5E:00:53:57 PLN-PC14"
    ).encode()


def _spool(capture: Capture, folder: str, sender: str, messages: list[bytes]) -> None:
    path = capture.layout.spool_file(folder, site_day(utc_now()))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        for message in messages:
            handle.write(format_line(utc_now(), sender, message))


def _client(server: LiveServer, test_client: Any) -> httpx.Client:
    """An HTTP client of the live server with the session of a capture client."""
    who = test_client.who
    client = httpx.Client(base_url=server.url, timeout=10)
    client.cookies.set(SESSION_COOKIE, who.session_token)
    client.cookies.set(CSRF_COOKIE, who.csrf_token)
    client.headers[CSRF_HEADER] = who.csrf_token
    return client


class Stream:
    """``GET /live/streams/{id}`` read in a thread until :meth:`until` is satisfied."""

    def __init__(
        self, client: httpx.Client, stream_id: str, last_event_id: str | None = None
    ) -> None:
        self.events: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        headers = {"X-Arci-Background": "1", "Accept": "text/event-stream"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = last_event_id
        self.thread = threading.Thread(
            target=self._run, args=(client, f"{API}/live/streams/{stream_id}", headers), daemon=True
        )
        self.thread.start()

    def _run(self, client: httpx.Client, url: str, headers: dict[str, str]) -> None:
        try:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    raise SystemExit(f"GET {url}: {response.status_code}")
                for event in read_events(response):
                    if event is not None:
                        with self.lock:
                            self.events.append(
                                {"event": event.event, "id": event.id, "data": event.data}
                            )
                    if self.stop.is_set():
                        return
        except httpx.HTTPError:
            return

    def until(
        self, check: Callable[[list[dict[str, Any]]], bool], what: str
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            with self.lock:
                if check(self.events):
                    return list(self.events)
            time.sleep(0.05)
        raise SystemExit(f"live tail: no {what} within {WAIT_SECONDS} s: {self.events[-3:]}")

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=10)


def _kinds(events: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [event for event in events if event["event"] == name]


def capture_live(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"open": {}, "events": {}, "errors": {}}
    errors = out["errors"]
    hub = Hub(capture.app.state.arci, timing=TIMING)
    capture.app.state.arci_live = hub
    _spool(capture, "fortigate", FIREWALL, [_traffic(n) for n in range(5)])
    _spool(capture, "core-r1", ROUTER, [_dhcp(n) for n in range(3)])
    with LiveServer(capture.app) as server:
        clients = [_client(server, admin), _client(server, auditor), _client(server, newcomer)]
        boss, reader_, fresh = clients
        owned: list[tuple[httpx.Client, str]] = []
        try:
            opened = capture.json(
                boss.post(f"{API}/live/streams", json={"q": "", "backfill": 3}), 201
            )
            out["open"]["admin"] = opened
            owned.append((boss, opened["stream_id"]))
            # lines written after the stream opened: replayed when the page connects
            _spool(capture, "fortigate", FIREWALL, [_traffic(10, "deny")])
            _spool(capture, UNKNOWN, NEWCOMER_SENDER, [b"<13>Oct 13 07:00:00 yeni-cihaz ilk mesaj"])
            path = capture.layout.spool_file("core-r1", site_day(utc_now()))
            with path.open("ab") as handle:
                handle.write(b"\x00\x00 torn fragment of a line\n")  # not in spool format
            time.sleep(1.0)  # the listing finds the unknown sender's new folder
            stream = Stream(boss, opened["stream_id"])
            events = stream.until(lambda e: len(_kinds(e, "line")) >= 6, "backfill and new lines")
            stream.close()
            out["events"]["stream"] = events
            # the rate limit: more lines than a second allows
            capture.setting("live.max_lines_per_second", 10)
            limited = capture.json(
                reader_.post(f"{API}/live/streams", json={"q": "source:fortigate", "backfill": 0}),
                201,
            )
            out["open"]["auditor"] = limited
            owned.append((reader_, limited["stream_id"]))
            _spool(capture, "fortigate", FIREWALL, [_traffic(100 + n) for n in range(25)])
            time.sleep(0.5)
            stream = Stream(reader_, limited["stream_id"])
            out["events"]["dropped"] = stream.until(lambda e: bool(_kinds(e, "dropped")), "dropped")
            stream.close()
            # a resume the hub cannot serve: another epoch
            stream = Stream(reader_, limited["stream_id"], last_event_id="baska-bir-api:12")
            out["events"]["gap"] = stream.until(lambda e: bool(_kinds(e, "gap")), "gap")
            stream.close()
            out["status"] = capture.json(boss.get(f"{API}/live/status"), 200)
            errors["live_stream_limit"] = capture.error(
                _limit(reader_, owned), 429, "live_stream_limit"
            )
            errors["search_unknown_field"] = capture.error(
                boss.post(f"{API}/live/streams", json={"q": "banana:1"}),
                422,
                "search_unknown_field",
            )
            errors["search_value_invalid"] = capture.error(
                boss.post(f"{API}/live/streams", json={"q": "srcip:banana"}),
                422,
                "search_value_invalid",
            )
            errors["validation_error"] = capture.error(
                boss.post(f"{API}/live/streams", json={"q": "", "backfill": 500}),
                422,
                "validation_error",
            )
            errors["privacy_notice_required"] = capture.error(
                fresh.post(f"{API}/live/streams", json={"q": ""}), 403, "privacy_notice_required"
            )
            errors["live_stream_not_found"] = capture.error(
                boss.get(f"{API}/live/streams/ornek-akis", headers={"X-Arci-Background": "1"}),
                404,
                "live_stream_not_found",
            )
            hr = _client(server, capture.client("hr", "ik.canli", "İK Canlı Akış"))
            clients.append(hr)
            errors["forbidden"] = capture.error(
                hr.post(f"{API}/live/streams", json={"q": ""}), 403, "forbidden"
            )
            # the time limit ends a connected stream
            stream = Stream(boss, opened["stream_id"], last_event_id=events[-1]["id"])
            stream.until(lambda e: bool(_kinds(e, "hello")), "hello")
            found = hub.get(opened["stream_id"])
            if found is None:
                raise SystemExit("live tail: the administrator's stream is gone")
            found.opened_at -= timedelta(minutes=found.settings.max_minutes + 1)
            out["events"]["end"] = stream.until(lambda e: bool(_kinds(e, "end")), "end")
            stream.close()
            for client, stream_id in owned:
                client.delete(f"{API}/live/streams/{stream_id}")
        finally:
            for client in clients:
                client.close()
            for key in CHANGED:
                capture.setting(key, DEFAULT_SETTINGS[key])
    capture.app.state.arci_live = None
    return out


def _limit(client: httpx.Client, owned: list[tuple[httpx.Client, str]]) -> Any:
    """Open streams until the per-user limit refuses one (the auditor has one open)."""
    for _ in range(5):
        response = client.post(f"{API}/live/streams", json={"q": ""})
        if response.status_code != 201:
            return response
        owned.append((client, response.json()["stream_id"]))
    raise SystemExit("live tail: the stream limit never refused")


def capture_sources(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"detail": {}, "tail": {}, "errors": {}}
    errors = out["errors"]
    now = utc_now()
    today = site_day(now)
    with capture.factory() as session:
        session.merge(
            Source(
                name=UNKNOWN,
                type="generic",
                address=NEWCOMER_SENDER,
                unknown=True,
                first_seen=now - timedelta(minutes=20),
                last_seen=now - timedelta(minutes=1),
            )
        )
        session.merge(IngestOffset(source="fortigate", day=today, byte_offset=2048, line_number=31))
        session.merge(IngestOffset(source="core-r1", day=today, byte_offset=0, line_number=4))
        for source, pattern, count, line in (
            ("fortigate", "fortigate.kv", 2, 3),
            ("core-r1", "rawlog.invalid", 1, 4),
        ):
            session.add(
                ParseFailure(
                    source=source,
                    day=today,
                    pattern=pattern,
                    count=count,
                    first_seen=now,
                    last_seen=now,
                    example_line=line,
                    example_offset=0,
                    error="value was not key=value" if pattern != "rawlog.invalid" else None,
                )
            )
        session.commit()
    state_of(_Request(capture.app)).overview.ttl = 0.0  # every answer reads the database
    out["overview"] = capture.json(admin.get(f"{API}/sources"), 200)
    for name in ("fortigate", "core-r1", UNKNOWN):
        out["detail"][name] = capture.json(admin.get(f"{API}/sources/{name}"), 200)
    out["tail"]["core-r1"] = capture.json(
        admin.get(f"{API}/sources/core-r1/tail", params={"limit": 5}), 200
    )
    out["tail"]["no_file_today"] = capture.json(admin.get(f"{API}/sources/switch-3/tail"), 200)
    out["dashboard"] = capture.json(admin.get(f"{API}/dashboard/summary"), 200)
    errors["source_not_found"] = capture.error(
        admin.get(f"{API}/sources/yok-boyle"), 404, "source_not_found"
    )
    errors["forbidden"] = capture.error(auditor.get(f"{API}/sources"), 403, "forbidden")
    errors["privacy_notice_required"] = capture.error(
        newcomer.get(f"{API}/sources/core-r1/tail"), 403, "privacy_notice_required"
    )
    return out


class _Request:
    """What :func:`arcilogger_api.routers.sources.state_of` reads of a request."""

    def __init__(self, app: Any) -> None:
        self.app = app


def capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    """The live tail and sources samples."""
    return {
        "live": capture_live(capture, admin, auditor, newcomer),
        "sources": capture_sources(capture, admin, auditor, newcomer),
    }
