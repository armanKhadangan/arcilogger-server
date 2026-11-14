"""A local HTTPS webhook receiver for tests (Wave 6 contract §2.4, tests T-H1 and T-H2).

:class:`WebhookReceiver` serves ``https://localhost:<port>/`` with a certificate of its own test
CA (``ca_file``), records every request (path, headers, body) and answers each with the next
status of :attr:`WebhookReceiver.statuses` (the last one repeats) and :attr:`reply` as the body.
Used by the core, worker and API tests of the webhooks; every value is fictional.
"""

from __future__ import annotations

import ssl
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


@dataclass(frozen=True)
class Received:
    path: str
    headers: dict[str, str]
    body: bytes


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def tls_files(folder: Path) -> tuple[Path, ssl.SSLContext]:
    """A test CA (written to ``webhook-ca.pem``) and a server certificate for ``localhost`` and
    127.0.0.1; the server context."""
    now = datetime.now(UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("Örnek Webhook CA (TEST)"))
        .issuer_name(_name("Örnek Webhook CA (TEST)"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    host_key = ec.generate_private_key(ec.SECP256R1())
    host = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca.subject)
        .public_key(host_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(IPv4Address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "webhook-ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (folder / "webhook-host.pem").write_bytes(host.public_bytes(serialization.Encoding.PEM))
    (folder / "webhook-host.key").write_bytes(
        host_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(folder / "webhook-host.pem", folder / "webhook-host.key")
    return folder / "webhook-ca.pem", context


@dataclass
class WebhookReceiver:
    """An HTTPS endpoint that records what it is sent (see the module docstring)."""

    folder: Path
    statuses: list[int] = field(default_factory=lambda: [200])
    reply: bytes = b""
    received: list[Received] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.ca_file, context = tls_files(self.folder)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                outer.received.append(Received(self.path, dict(self.headers.items()), body))
                status = outer.statuses[min(len(outer.received), len(outer.statuses)) - 1]
                self.send_response(status)
                self.send_header("Content-Length", str(len(outer.reply)))
                self.end_headers()
                self.wfile.write(outer.reply)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"https://localhost:{self._server.server_address[1]}/arcilogger?kanal=guvenlik"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> WebhookReceiver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
