"""Canlı Akış through Caddy (contract 2.15, 3.6): the live tail's server-sent events reach the
browser at once and uncompressed, and a closed tab does not keep the API's stream open.

* **Static** (always): the Caddyfile excludes ``/api/v1/live/*`` from ``encode`` with a named
  matcher and has no ``flush_interval`` (``reverse_proxy`` flushes ``text/event-stream`` at
  once; ``-1`` would stop Caddy from cancelling the upstream request when the client goes away).
* **With** ``ARCILOGGER_CADDY`` (a Caddy binary; the installer workflow downloads the pinned
  release): a stub upstream sends one event a second through the real Caddyfile; the first one
  arrives within a second and the response has no ``Content-Encoding``, while other API answers
  are still compressed.
* **With** ``ARCILOGGER_CADDY`` **and the test database** (the Windows end-to-end job): the real
  API behind the real Caddyfile — a stream is opened and read through Caddy, a line appended to
  the spool afterwards arrives within 3 s, uncompressed; when the client closes its connection,
  Caddy cancels the upstream request and the API detaches the stream within 2 s (R15).
"""

from __future__ import annotations

import gzip
import http.client
import http.server
import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from sqlalchemy import Engine

from arcilogger_api.app import create_app
from arcilogger_api.live.hub import hub_of
from arcilogger_api.live.testing import LiveServer, read_events, sign_in, spool_line
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.engine import session_factory

REPO = Path(__file__).parents[1]
CADDYFILE = REPO / "deploy" / "windows" / "caddy" / "Caddyfile"
CADDY = os.environ.get("ARCILOGGER_CADDY")
needs_caddy = pytest.mark.skipif(
    not CADDY or not Path(CADDY).is_file(), reason="ARCILOGGER_CADDY not set"
)
#: Sent by the stub upstream for every other API path: long enough for Caddy to compress it.
PLAIN_BODY = b'{"items": [' + b", ".join(b'"%04d"' % n for n in range(400)) + b"]}"
#: How Caddy's log says that the port it was given could not be bound (``listen tcp :<port>:
#: bind: …``): EADDRINUSE on Linux and WSAEADDRINUSE on Windows (another socket took it), and
#: WSAEACCES, Windows' answer for a port another socket holds exclusively or a reserved range.
PORT_TAKEN = (
    "address already in use",
    "Only one usage of each socket address",
    "An attempt was made to access a socket in a way forbidden by its access permissions",
)
#: Ports tried, each free when it is picked, before a test gives up starting Caddy.
CADDY_PORT_TRIES = 5
#: Ports :func:`free_port` picks for TCP before giving up on one that UDP can bind too: enough to
#: step past an excluded range of several hundred ports (a pick costs microseconds).
PORT_PICKS = 1000


def _site_block(text: str) -> str:
    """The body of the site block (everything after the global options block)."""
    start = text.index("{$ARCILOGGER_SITE_ADDRESSES")
    return text[start:]


def test_the_live_tail_is_not_compressed_and_not_held_back() -> None:
    text = CADDYFILE.read_text(encoding="utf-8")
    site = _site_block(text)
    assert re.search(r"(?m)^\s*@compress not path /api/v1/live/\*$", site)
    assert re.search(r"(?m)^\s*encode @compress zstd gzip$", site)
    # exactly one encode directive, and it carries the matcher
    assert len(re.findall(r"(?m)^\s*encode\b", site)) == 1
    assert not re.search(r"(?m)^\s*flush_interval\b", text)  # comments may name it
    proxy = re.search(r"handle /api/\* \{\s*reverse_proxy (\S+)\s*\}", site)
    assert proxy is not None, "the API block stays a plain reverse_proxy"


class _Upstream(http.server.BaseHTTPRequestHandler):
    """``/api/v1/live/…``: three events, one a second; anything else: a JSON body."""

    def do_GET(self) -> None:
        if self.path.startswith("/api/v1/live/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            for number in range(3):
                self.wfile.write(b'event: line\ndata: {"seq": %d}\n\n' % number)
                self.wfile.flush()
                time.sleep(1)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(PLAIN_BODY)))
        self.end_headers()
        self.wfile.write(PLAIN_BODY)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def upstream() -> Iterator[int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def free_port() -> int:
    """A port free for TCP **and** UDP: Caddy serves HTTP/3 (QUIC, UDP) on the HTTPS port too.
    Windows can exclude whole UDP ranges (Hyper-V/WinNAT ``excludedportrange``), where only the
    UDP bind is refused (WSAEACCES), and it hands out TCP ports one after another: a port picked
    for TCP alone, and each retry's next one, then fall into the same excluded range."""
    for _ in range(PORT_PICKS):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port: int = sock.getsockname()[1]
        if udp_bindable(port):
            return port
    raise AssertionError(f"no port free for TCP and UDP in {PORT_PICKS} picks")


def udp_bindable(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def caddy_env(tmp_path: Path, extra: dict[str, str]) -> dict[str, str]:
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "APPDATA": str(tmp_path / "appdata"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "ARCILOGGER_LOG_DIR": (tmp_path / "logs").as_posix(),
    }
    env.update(extra)
    return env


class PortTakenError(Exception):
    """Caddy stopped because the port it was given could not be bound."""


def caddy_errors(log: Path, start: int) -> list[str]:
    """The ``error`` messages of Caddy's log file from byte ``start`` on. The Caddyfile sends
    Caddy's own log to a file in ``ARCILOGGER_LOG_DIR``, so a failed start leaves stderr empty."""
    if not log.is_file():
        return []
    with log.open("rb") as handle:
        handle.seek(start)
        lines = handle.read().decode("utf-8", errors="replace").splitlines()
    errors = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("level") == "error":
            errors.append(str(entry.get("msg")))
    return errors


def port_taken(errors: list[str], port: int) -> bool:
    """Whether Caddy stopped because ``port`` could not be bound, for TCP or for HTTP/3's UDP."""
    return any(
        f"listen {network} :{port}: bind: {reason}" in error
        for error in errors
        for network in ("tcp", "udp")
        for reason in PORT_TAKEN
    )


def start_caddy(
    tmp_path: Path, upstream: str
) -> tuple[subprocess.Popen[bytes], int, ssl.SSLContext]:
    """The real Caddyfile on a free port in front of ``upstream``; returns it, the port and a TLS
    context that trusts its local CA once it answers.

    The port is free when it is picked, but only Caddy binds it, later and in its own process, so
    another process can take it in between. Only when Caddy's log says so is another port tried.
    """
    failures: list[str] = []
    for _ in range(CADDY_PORT_TRIES):
        port = free_port()
        try:
            process, context = run_caddy(tmp_path, port, upstream)
        except PortTakenError as taken:
            failures.append(str(taken))
            continue
        return process, port, context
    raise AssertionError(f"Caddy's port was taken {CADDY_PORT_TRIES} times: {failures}")


def run_caddy(
    tmp_path: Path, port: int, upstream: str
) -> tuple[subprocess.Popen[bytes], ssl.SSLContext]:
    """The real Caddyfile on ``port`` in front of ``upstream``; returns it and a TLS context that
    trusts its local CA once it answers, or raises :class:`PortTakenError`."""
    assert CADDY is not None
    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    (web / "index.html").write_text("<!doctype html><title>ARCILOGGER</title>", encoding="utf-8")
    log = tmp_path / "logs" / "caddy.log"
    start = log.stat().st_size if log.is_file() else 0
    process = subprocess.Popen(
        [CADDY, "run", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
        env=caddy_env(
            tmp_path,
            {
                "ARCILOGGER_SITE_ADDRESSES": f"https://localhost:{port}",
                "ARCILOGGER_WEB_ROOT": web.as_posix(),
                "ARCILOGGER_API_UPSTREAM": upstream,
            },
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    root = tmp_path / "data" / "caddy" / "pki" / "authorities" / "local" / "root.crt"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if root.is_file():
            context = ssl.create_default_context(cafile=str(root))
            try:
                with (
                    socket.create_connection(("127.0.0.1", port), timeout=2) as raw,
                    context.wrap_socket(raw, server_hostname="localhost"),
                ):
                    return process, context
            except OSError:
                pass
        if process.poll() is not None:
            _, stderr = process.communicate()
            errors = caddy_errors(log, start)
            if port_taken(errors, port):
                raise PortTakenError("; ".join(errors))
            raise AssertionError(f"Caddy stopped: {stderr.decode()} {errors}")
        time.sleep(0.3)
    process.kill()
    raise AssertionError("Caddy did not answer within 30 s")


def stop_caddy(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def test_a_port_udp_cannot_bind_is_not_given_to_caddy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caddy's HTTP/3 listener binds the HTTPS port on UDP too: a port whose UDP bind is refused
    (a Windows excluded range) is passed over for the next one."""
    asked: list[int] = []

    def refuse_the_first(port: int) -> bool:
        asked.append(port)
        return len(asked) > 1

    monkeypatch.setitem(globals(), "udp_bindable", refuse_the_first)
    port = free_port()
    assert len(asked) == 2
    assert port == asked[1]


def test_a_port_a_udp_socket_holds_is_not_udp_bindable() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as held:
        held.bind(("127.0.0.1", 0))
        port: int = held.getsockname()[1]
        assert not udp_bindable(port)
    assert udp_bindable(free_port())


@needs_caddy
def test_events_pass_caddy_at_once_and_uncompressed(tmp_path: Path, upstream: int) -> None:
    process, port, context = start_caddy(tmp_path, f"127.0.0.1:{upstream}")
    try:
        connection = http.client.HTTPSConnection("localhost", port, context=context, timeout=10)
        started = time.monotonic()
        connection.request(
            "GET",
            "/api/v1/live/streams/ornek",
            headers={"Accept": "text/event-stream", "Accept-Encoding": "zstd, gzip"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Encoding") is None
        assert response.getheader("Content-Type", "").startswith("text/event-stream")
        first = response.readline()
        assert time.monotonic() - started < 1.0, "the first event was held back"
        assert first == b"event: line\n"
        assert response.readline() == b'data: {"seq": 0}\n'
        connection.close()
        # everything else of the API is still compressed
        connection = http.client.HTTPSConnection("localhost", port, context=context, timeout=10)
        connection.request("GET", "/api/v1/sources", headers={"Accept-Encoding": "gzip"})
        response = connection.getresponse()
        assert response.getheader("Content-Encoding") == "gzip"
        assert gzip.decompress(response.read()) == PLAIN_BODY
        connection.close()
    finally:
        stop_caddy(process)


@needs_caddy
def test_a_spool_line_reaches_the_live_tail_through_caddy(
    tmp_path: Path, db_engine: Engine
) -> None:
    config = Config.model_validate(
        {
            "site_name": "Test",
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": [{"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"}],
        }
    )
    layout = Layout(config.data_dir)
    # The hub follows the spool file of its own site day: with its own clock, a site midnight
    # (21:00 UTC) between choosing this file and opening the stream, or before the append,
    # leaves the line in a file it no longer reads. It reads the test's moment instead.
    moment = utc_now()
    spool = layout.spool_file("fortigate", site_day(moment))
    spool.parent.mkdir(parents=True)
    spool.write_bytes(spool_line('logid="0000000013" type="traffic" srcip=172.20.41.105'))
    app = create_app(config, db_engine, audit_flush_seconds=None)
    hub_of(app).now = lambda: moment
    with LiveServer(app) as api:
        process, port, context = start_caddy(tmp_path, f"127.0.0.1:{api.port}")
        try:
            with httpx.Client(
                base_url=f"https://localhost:{port}", verify=context, timeout=10
            ) as client:
                sign_in(client, session_factory(db_engine))
                opened = client.post("/api/v1/live/streams", json={"q": "", "backfill": 0})
                assert opened.status_code == 201, opened.text
                url = f"/api/v1/live/streams/{opened.json()['stream_id']}"
                headers = {"X-Arci-Background": "1", "Accept-Encoding": "zstd, gzip"}
                with client.stream("GET", url, headers=headers) as response:
                    assert response.status_code == 200
                    assert "content-encoding" not in response.headers
                    events = read_events(response)
                    hello = next(event for event in events if event is not None)
                    assert hello.event == "hello"
                    written = time.monotonic()
                    with spool.open("ab") as handle:
                        handle.write(
                            spool_line('logid="0000000013" type="traffic" srcip=172.20.41.7')
                        )
                    line = next(event for event in events if event is not None)
                    assert time.monotonic() - written <= 3.0
                    assert (line.event, line.data["line"], line.data["source"]) == (
                        "line",
                        2,
                        "fortigate",
                    )
                    assert "srcip=172.20.41.7" in line.data["text"]
                # the tab goes away: Caddy cancels the upstream request and the API lets the
                # stream go at once (it lingers, paused, until it is closed)
                stream = app.state.arci_live.streams[opened.json()["stream_id"]]
                deadline = time.monotonic() + 2.0
                while stream.connection is not None and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert stream.connection is None, "the API still streams to a closed tab"
                assert stream.closed is None
                assert client.delete(url).status_code == 204
        finally:
            stop_caddy(process)
