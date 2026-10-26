"""Canlı Akış through Caddy (contract 2.15, 3.6): the live tail's server-sent events reach the
browser at once and uncompressed, and a closed tab does not keep the API's stream open.

* **Static** (always): the Caddyfile excludes ``/api/v1/live/*`` from ``encode`` with a named
  matcher and has no ``flush_interval`` (``reverse_proxy`` flushes ``text/event-stream`` at
  once; ``-1`` would stop Caddy from cancelling the upstream request when the client goes away).
* **With** ``ARCILOGGER_CADDY`` (a Caddy binary; the installer workflow downloads the pinned
  release): a stub upstream sends one event a second through the real Caddyfile; the first one
  arrives within a second and the response has no ``Content-Encoding``, while other API answers
  are still compressed.
"""

from __future__ import annotations

import gzip
import http.client
import http.server
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

import pytest

REPO = Path(__file__).parents[1]
CADDYFILE = REPO / "deploy" / "windows" / "caddy" / "Caddyfile"
CADDY = os.environ.get("ARCILOGGER_CADDY")
needs_caddy = pytest.mark.skipif(
    not CADDY or not Path(CADDY).is_file(), reason="ARCILOGGER_CADDY not set"
)
#: Sent by the stub upstream for every other API path: long enough for Caddy to compress it.
PLAIN_BODY = b'{"items": [' + b", ".join(b'"%04d"' % n for n in range(400)) + b"]}"


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
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


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


def start_caddy(
    tmp_path: Path, port: int, upstream: str
) -> tuple[subprocess.Popen[bytes], ssl.SSLContext]:
    """The real Caddyfile on ``port`` in front of ``upstream``; returns it and a TLS context that
    trusts its local CA once it answers."""
    assert CADDY is not None
    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    (web / "index.html").write_text("<!doctype html><title>ARCILOGGER</title>", encoding="utf-8")
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
            stderr = process.stderr.read().decode() if process.stderr else ""
            raise AssertionError(f"Caddy stopped: {stderr}")
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


@needs_caddy
def test_events_pass_caddy_at_once_and_uncompressed(tmp_path: Path, upstream: int) -> None:
    port = free_port()
    process, context = start_caddy(tmp_path, port, f"127.0.0.1:{upstream}")
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
