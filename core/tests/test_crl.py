"""Revocation lists (Wave 5 W5-B): fetch from a local server, verify, store, select (D7-D9).

Every download goes to an HTTP(S) server and a proxy on 127.0.0.1 inside the test; no test
reaches a real CA. The CAs and their lists are made here with ``cryptography``.
"""

from __future__ import annotations

import ast
import hashlib
import http.client
import json
import os
import re
import select
import socket
import ssl
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from asn1crypto import cms
from asn1crypto import crl as asn1_crl
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from arcilogger_core import crl as crl_module
from arcilogger_core.chain import verify_day
from arcilogger_core.cli import main
from arcilogger_core.config import Config
from arcilogger_core.crl import (
    INDEX_SCHEMA,
    LOCK_NAME,
    MAX_CRL_BYTES,
    Crl,
    CrlError,
    CrlOptions,
    crl_options,
    crl_urls,
    crls_for,
    distribution_points,
    fetch,
    fetch_all,
    load_stored,
    parse_crl,
    read_index,
    token_certificates,
    trusted_issuers,
)
from arcilogger_core.evidence import builder as builder_module
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.verify import CRL_REASON_HOLD, fingerprint

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
CORE_SRC = Path(__file__).parents[1] / "src"
PrivateKey = ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey


# -- certificates and lists -----------------------------------------------------------------------


@dataclass
class Ca:
    certificate: x509.Certificate
    key: PrivateKey

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.certificate)


def _name(common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Örnek A.Ş. TEST"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def _cdp(urls: Sequence[str]) -> x509.CRLDistributionPoints:
    return x509.CRLDistributionPoints(
        [
            x509.DistributionPoint(
                full_name=[x509.UniformResourceIdentifier(url)],
                relative_name=None,
                reasons=None,
                crl_issuer=None,
            )
            for url in urls
        ]
    )


def make_ca(
    common_name: str,
    issuer: Ca | None = None,
    *,
    cdp: Sequence[str] = (),
    ca: bool = True,
    rsa_key: bool = False,
    san: str | None = None,
) -> Ca:
    key: PrivateKey = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if rsa_key
        else ec.generate_private_key(ec.SECP256R1())
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(issuer.certificate.subject if issuer else _name(common_name))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=400))
        .not_valid_after(NOW + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if cdp:
        builder = builder.add_extension(_cdp(cdp), critical=False)
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(IPv4Address(san))]), critical=False
        )
    signer = issuer.key if issuer else key
    return Ca(builder.sign(signer, hashes.SHA256()), key)


def make_crl(
    ca: Ca,
    this_update: datetime,
    revoked: Sequence[tuple[int, datetime, x509.ReasonFlags | None]] = (),
    *,
    signer: PrivateKey | None = None,
    pem: bool = False,
    digest: hashes.SHA256 | hashes.SHA3_256 | None = None,
    delta_of: int | None = None,
) -> bytes:
    """A list of ``ca``; ``delta_of``: a delta list (DeltaCRLIndicator) of that base list."""
    builder = x509.CertificateRevocationListBuilder().issuer_name(ca.certificate.subject)
    builder = builder.last_update(this_update).next_update(this_update + timedelta(days=7))
    if delta_of is not None:
        builder = builder.add_extension(x509.DeltaCRLIndicator(delta_of), critical=True)
    for serial, when, reason in revoked:
        entry = x509.RevokedCertificateBuilder().serial_number(serial).revocation_date(when)
        if reason is not None:
            entry = entry.add_extension(x509.CRLReason(reason), critical=False)
        builder = builder.add_revoked_certificate(entry.build())
    signed = builder.sign(signer or ca.key, digest or hashes.SHA256())
    return signed.public_bytes(Encoding.PEM if pem else Encoding.DER)


def stored_name(ca: Ca, this_update: str, data: bytes) -> str:
    """The name ``crl fetch`` stores ``data`` under (D8, C7); ``this_update`` as in the name."""
    return f"{ca.fingerprint[:12]}-{this_update}-{hashlib.sha256(data).hexdigest()[:8]}.crl"


def token_with(certificates: Sequence[x509.Certificate]) -> bytes:
    """A bare TimeStampToken carrying ``certificates`` (fetch only reads its certificates)."""
    signed = cms.SignedData(
        {
            "version": "v3",
            "digest_algorithms": [],
            "encap_content_info": {"content_type": "tst_info"},
            "certificates": [
                asn1_x509.Certificate.load(cert.public_bytes(Encoding.DER)) for cert in certificates
            ],
            "signer_infos": [],
        }
    )
    return cast(bytes, cms.ContentInfo({"content_type": "signed_data", "content": signed}).dump())


def seal_token(
    layout: Layout, day: date, provider: str, certificates: Sequence[x509.Certificate]
) -> None:
    """A vault day with a manifest naming ``provider`` and a token with ``certificates``."""
    manifest = layout.manifest(day)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"tsa": {"provider": provider}}), encoding="utf-8")
    layout.token(day).write_bytes(token_with(certificates))


# -- a local HTTP(S) server and proxy -------------------------------------------------------------


@dataclass
class Route:
    body: bytes = b""
    status: int = 200
    #: False: no Content-Length, the body ends when the connection closes.
    length: bool = True
    #: A Content-Length larger than the body (the client must refuse before reading).
    claimed: int | None = None
    location: str | None = None
    #: Seconds to wait before answering.
    delay: float = 0


@dataclass
class Server:
    tls: ssl.SSLContext | None = None
    routes: dict[str, Route] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.requests.append(self.path)
                route = outer.routes.get(self.path)
                if route is None:
                    self.send_error(404)
                    return
                time.sleep(route.delay)
                self.send_response(route.status)
                if route.location:
                    self.send_header("Location", route.location)
                if route.claimed is not None:
                    self.send_header("Content-Length", str(route.claimed))
                elif route.length:
                    self.send_header("Content-Length", str(len(route.body)))
                self.end_headers()
                try:
                    for start in range(0, len(route.body), 1024 * 1024):
                        self.wfile.write(route.body[start : start + 1024 * 1024])
                except OSError:  # the client stopped reading (too large)
                    pass
                self.close_connection = True

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if self.tls is not None:
            self._server.socket = self.tls.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str) -> str:
        port = self._server.server_address[1]
        return f"{'https' if self.tls else 'http'}://127.0.0.1:{port}{path}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@dataclass
class Proxy:
    """A forwarding (http) and CONNECT (https) proxy that records what it carried."""

    used: list[tuple[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.used.append(("GET", self.path))
                target = urlsplit(self.path)
                upstream = http.client.HTTPConnection(target.netloc, timeout=5)
                upstream.request("GET", target.path or "/")
                answer = upstream.getresponse()
                body = answer.read()
                self.send_response(answer.status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def do_CONNECT(self) -> None:
                outer.used.append(("CONNECT", self.path))
                host, port = self.path.rsplit(":", 1)
                upstream = socket.create_connection((host, int(port)), timeout=5)
                self.send_response(200, "Connection established")
                self.end_headers()
                ends = [self.connection, upstream]
                try:
                    while True:
                        readable, _, _ = select.select(ends, [], [], 5)
                        if not readable:
                            break
                        for end in readable:
                            data = end.recv(65536)
                            if not data:
                                return
                            (upstream if end is self.connection else self.connection).sendall(data)
                finally:
                    upstream.close()
                    self.close_connection = True

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def server() -> Iterator[Server]:
    served = Server()
    yield served
    served.close()


@pytest.fixture
def root() -> Ca:
    return make_ca("Örnek Kök SM (TEST)", rsa_key=True)


@pytest.fixture
def layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path / "data")


#: The tests' budget for one URL. It bounds the whole exchange (connect, a proxy's CONNECT, the
#: TLS handshake, headers and body), so it is the default of ``tsa.timeout_seconds``, well above
#: a loaded runner's worst case. The deadline tests set their own 1 s.
FETCH_SECONDS = 60


def options(*urls: str, **kwargs: Any) -> CrlOptions:
    return CrlOptions(urls=urls, timeout_seconds=FETCH_SECONDS, **kwargs)


def closed_port_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/gone.crl"


# -- fetch: one case per outcome -------------------------------------------------------------------


def test_good_list_is_stored_byte_for_byte_and_indexed(
    server: Server, root: Ca, layout: Layout
) -> None:
    data = make_crl(root, datetime(2026, 11, 1, 6, 30, 15, tzinfo=UTC))
    server.routes["/root.crl"] = Route(data)
    run = fetch(options(server.url("/root.crl")), layout, [root.certificate], NOW)

    assert run.exit_code == 0
    name = stored_name(root, "20261101T063015Z", data)
    stored = layout.crl_dir / name
    assert stored.read_bytes() == data
    index = json.loads((layout.crl_dir / "index.json").read_text(encoding="utf-8"))
    assert index == {
        "schema": INDEX_SCHEMA,
        "lists": [
            {
                "file": name,
                "issuer": root.certificate.subject.rfc4514_string(),
                "issuer_sha256": root.fingerprint,
                "this_update": "2026-11-01T06:30:15+00:00",
                "next_update": "2026-11-08T06:30:15+00:00",
                "sha256": hashlib.sha256(data).hexdigest(),
                "url": server.url("/root.crl"),
                "fetched_at": "2026-11-02T12:00:00+03:00",
            }
        ],
        "failures": [],
    }
    assert run.as_json() == {
        "ok": True,
        "urls": [server.url("/root.crl")],
        "lists": index["lists"],
        "failures": [],
    }
    # The lock file stays (empty); no temporary file is left.
    assert sorted(path.name for path in layout.crl_dir.iterdir()) == [LOCK_NAME, name, "index.json"]


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("bad_signature", "bad_signature"),
        ("unknown_issuer", "unknown_issuer"),
        ("not_a_crl", "not_a_crl"),
        ("delta", "delta_not_supported"),
        ("http_404", "unreachable"),
        ("closed_port", "unreachable"),
        ("too_large_claimed", "too_large"),
        ("redirect_to_ftp", "unreachable"),
    ],
)
def test_a_list_that_fails_is_recorded_and_not_stored(
    server: Server, root: Ca, layout: Layout, case: str, error: str
) -> None:
    this_update = datetime(2026, 11, 1, tzinfo=UTC)
    stranger = make_ca("Başka SM (TEST)")
    bodies = {
        "bad_signature": make_crl(root, this_update, signer=stranger.key),
        "unknown_issuer": make_crl(stranger, this_update),
        "not_a_crl": b"<html>not a list</html>",
        # C7: a delta list verifies, but neither verifier applies one.
        "delta": make_crl(root, this_update, [(9, this_update, None)], delta_of=4),
    }
    url = server.url(f"/{case}.crl")
    if case in bodies:
        server.routes[f"/{case}.crl"] = Route(bodies[case])
    elif case == "closed_port":
        url = closed_port_url()
    elif case == "too_large_claimed":
        server.routes[f"/{case}.crl"] = Route(b"x" * 10, claimed=MAX_CRL_BYTES + 1)
    elif case == "redirect_to_ftp":
        # An FTP "server" on 127.0.0.1 that only listens: the redirect must not reach it.
        ftp = socket.create_server(("127.0.0.1", 0))
        ftp.setblocking(False)
        location = f"ftp://127.0.0.1:{ftp.getsockname()[1]}/root.crl"
        server.routes[f"/{case}.crl"] = Route(status=302, location=location)

    started = time.monotonic()
    run = fetch(options(url), layout, [root.certificate], NOW)

    assert time.monotonic() - started < 4  # refused, not timed out
    if case == "redirect_to_ftp":
        with pytest.raises(BlockingIOError):
            ftp.accept()
        ftp.close()
    assert run.exit_code == 1
    assert [failure.as_json() for failure in run.failures] == [
        {"url": url, "at": "2026-11-02T12:00:00+03:00", "error": error}
    ]
    assert sorted(path.name for path in layout.crl_dir.iterdir()) == [LOCK_NAME, "index.json"]
    index = read_index(layout)
    assert index["lists"] == []
    assert index["failures"] == [failure.as_json() for failure in run.failures]


class _Reply:
    """A reply of endless body bytes that counts what the client reads."""

    headers: dict[str, str] = {}  # noqa: RUF012

    def __init__(self) -> None:
        self.read = 0

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read1(self, size: int) -> bytes:
        self.read += size
        return b"\0" * size


def test_reading_stops_at_the_limit(
    root: Ca, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size is checked while reading: the body is never read to its end first."""
    reply = _Reply()
    opener = SimpleNamespace(open=lambda request, timeout: reply)
    monkeypatch.setattr(crl_module, "_opener", lambda _: opener)
    run = fetch(options("http://192.0.2.1/x.crl", max_bytes=1000), layout, [], NOW)
    assert [failure.error for failure in run.failures] == ["too_large"]
    assert 1000 < reply.read <= 1000 + 64 * 1024


def test_reading_stops_beyond_20_mb(server: Server, root: Ca, layout: Layout) -> None:
    """No Content-Length: the body is read in chunks and dropped once past the limit."""
    server.routes["/huge.crl"] = Route(b"\0" * (MAX_CRL_BYTES + 1), length=False)
    server.routes["/limit.crl"] = Route(b"\0" * 100, length=False)
    run = fetch(
        options(server.url("/huge.crl"), server.url("/limit.crl"), max_bytes=99),
        layout,
        [root.certificate],
        NOW,
    )
    assert [failure.error for failure in run.failures] == ["too_large", "too_large"]
    run = fetch(options(server.url("/huge.crl")), layout, [root.certificate], NOW)
    assert [failure.error for failure in run.failures] == ["too_large"]
    assert MAX_CRL_BYTES == 20 * 1024 * 1024


def test_a_redirect_to_http_is_followed(server: Server, root: Ca, layout: Layout) -> None:
    server.routes["/moved.crl"] = Route(status=301, location=server.url("/root.crl"))
    server.routes["/root.crl"] = Route(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC)))
    run = fetch(options(server.url("/moved.crl")), layout, [root.certificate], NOW)
    assert run.exit_code == 0
    assert server.requests == ["/moved.crl", "/root.crl"]


def test_some_good_some_failed(server: Server, root: Ca, layout: Layout) -> None:
    server.routes["/root.crl"] = Route(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC)))
    gone = closed_port_url()
    urls = (server.url("/root.crl"), gone, "ldap://192.0.2.7/cn=x")
    run = fetch(options(*urls), layout, [root.certificate], NOW)
    assert run.urls == (server.url("/root.crl"), gone)  # ldap is never tried
    assert run.exit_code == 1
    assert [entry["url"] for entry in run.lists] == [server.url("/root.crl")]
    assert [failure.url for failure in run.failures] == [gone]


def test_no_url_known_writes_an_empty_index(root: Ca, layout: Layout) -> None:
    """#151: with no URL the run still writes the index, with no list and no failure, so the
    Sistem page sees that the task ran. It still exits 2 and fetches nothing."""
    run = fetch(options(), layout, [root.certificate], NOW)
    assert run.exit_code == 2
    assert run.as_json() == {"ok": False, "urls": [], "lists": [], "failures": []}
    assert read_index(layout) == {"schema": INDEX_SCHEMA, "lists": [], "failures": []}
    assert sorted(path.name for path in layout.crl_dir.iterdir()) == [LOCK_NAME, "index.json"]
    assert fetch(options(), layout, [root.certificate], NOW).exit_code == 2
    assert read_index(layout) == {"schema": INDEX_SCHEMA, "lists": [], "failures": []}


def test_no_url_known_keeps_the_stored_lists(server: Server, root: Ca, layout: Layout) -> None:
    """A run that knows no URL any more (the setting or the pinned roots changed) keeps every
    stored list and its entry (D8), drops the failures of the run before, and still hashes every
    stored file (C14). A lock held or an index that cannot be read stops it, as any run."""
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))
    server.routes["/root.crl"] = Route(data)
    url, gone = server.url("/root.crl"), closed_port_url()
    assert fetch(options(url, gone), layout, [root.certificate], NOW).exit_code == 1
    before = read_index(layout)
    assert [failure["url"] for failure in before["failures"]] == [gone]
    name = stored_name(root, "20261101T000000Z", data)

    run = fetch(options(), layout, [root.certificate], NOW + timedelta(days=1))
    assert (run.exit_code, run.lists, run.failures) == (2, (), ())
    assert read_index(layout) == {**before, "failures": []}
    assert (layout.crl_dir / name).read_bytes() == data

    changed = bytearray(data)
    changed[len(changed) // 2] ^= 1
    (layout.crl_dir / name).write_bytes(bytes(changed))  # test: the stored file is changed
    run = fetch(options(), layout, [root.certificate], NOW + timedelta(days=2))
    assert run.exit_code == 2
    assert [(failure.url, failure.error) for failure in run.failures] == [
        (url, "stored_file_changed")
    ]
    index = read_index(layout)
    assert (index["lists"], len(index["failures"])) == (before["lists"], 1)
    assert (layout.crl_dir / name).read_bytes() == bytes(changed)

    written = (layout.crl_dir / "index.json").read_bytes()
    with crl_module._one_run(layout.crl_dir), pytest.raises(CrlError, match="another"):
        fetch(options(), layout, [root.certificate], NOW + timedelta(days=3))
    assert (layout.crl_dir / "index.json").read_bytes() == written
    (layout.crl_dir / "index.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CrlError):
        fetch(options(), layout, [root.certificate], NOW + timedelta(days=3))
    assert (layout.crl_dir / "index.json").read_text(encoding="utf-8") == "{not json"


def test_a_wrong_index_stops_the_run_and_stays(server: Server, root: Ca, layout: Layout) -> None:
    server.routes["/root.crl"] = Route(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC)))
    index = layout.crl_dir / "index.json"
    index.parent.mkdir(parents=True)
    for text in (
        "{not json",
        '{"schema": "other/1", "lists": [], "failures": []}',
        f'{{"schema": "{INDEX_SCHEMA}", "lists": [], "failures": {{}}}}',
        f'{{"schema": "{INDEX_SCHEMA}", "lists": {{}}, "failures": []}}',
    ):
        index.write_text(text, encoding="utf-8")
        with pytest.raises(CrlError):
            fetch(options(server.url("/root.crl")), layout, [root.certificate], NOW)
        assert index.read_text(encoding="utf-8") == text
        assert sorted(path.name for path in layout.crl_dir.iterdir()) == [LOCK_NAME, "index.json"]
    assert server.requests == []


def _unreadable(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """``path`` cannot be read from now on (as a file whose ACL denies the service account)."""
    read_bytes = Path.read_bytes

    def denied(self: Path) -> bytes:
        if self == path:
            raise PermissionError(13, "Permission denied", str(self))
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", denied)


def test_a_stored_list_that_cannot_be_read_stops_the_run_and_is_recorded(
    server: Server, root: Ca, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#171: a stored list that cannot be read stops the run (exit 1). The index is written as
    it was read, with ``run_failure`` (the run's time and code only, no file name or URL), not
    with the lists of the run that stopped; the next completed run drops it."""
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))
    server.routes["/root.crl"] = Route(data)
    url, gone = server.url("/root.crl"), closed_port_url()
    assert fetch(options(url, gone), layout, [root.certificate], NOW).exit_code == 1
    before = read_index(layout)
    name = stored_name(root, "20261101T000000Z", data)
    newer = make_crl(root, datetime(2026, 11, 2, tzinfo=UTC))
    server.routes["/newer.crl"] = Route(newer)
    second = server.url("/newer.crl")
    _unreadable(monkeypatch, layout.crl_dir / name)

    # The newer list is stored first; the known one then cannot be hashed.
    later = NOW + timedelta(days=1)
    with pytest.raises(CrlError, match=re.escape(f"stored CRL {name} cannot be read")):
        fetch(options(second, url), layout, [root.certificate], later)
    recorded = {"at": "2026-11-03T12:00:00+03:00", "error": "stored_file_unreadable"}
    assert read_index(layout) == {**before, "run_failure": recorded}
    # Written as every index is (the issuer's name stays UTF-8, ``crl status`` prints it).
    assert (layout.crl_dir / "index.json").read_text(encoding="utf-8") == json.dumps(
        {**before, "run_failure": recorded}, ensure_ascii=False, indent=2
    ) + "\n"
    newer_name = stored_name(root, "20261102T000000Z", newer)
    assert (layout.crl_dir / newer_name).read_bytes() == newer  # stored, not indexed yet
    # No URL: every index entry is hashed, and the same stop is recorded with this run's time.
    with pytest.raises(CrlError, match="cannot be read"):
        fetch(options(), layout, [root.certificate], later + timedelta(hours=1))
    assert read_index(layout)["run_failure"] == {**recorded, "at": "2026-11-03T13:00:00+03:00"}
    assert read_index(layout)["lists"] == before["lists"]

    monkeypatch.undo()
    run = fetch(options(second, url), layout, [root.certificate], later + timedelta(hours=2))
    assert run.exit_code == 0
    index = read_index(layout)
    assert "run_failure" not in index
    assert [entry["file"] for entry in index["lists"]] == sorted([name, newer_name])
    assert index["failures"] == []


def test_a_stopped_run_whose_record_cannot_be_written_still_raises(
    server: Server,
    root: Ca,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The index write of ``run_failure`` fails as well: it is logged, and the run still raises
    the error of the stored list (exit 1); the index stays as it is."""
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))
    server.routes["/root.crl"] = Route(data)
    url = server.url("/root.crl")
    assert fetch(options(url), layout, [root.certificate], NOW).exit_code == 0
    written = (layout.crl_dir / "index.json").read_bytes()
    name = stored_name(root, "20261101T000000Z", data)
    _unreadable(monkeypatch, layout.crl_dir / name)

    def full_disk(path: Path, text: str) -> None:
        raise OSError(28, "No space left on device", str(path))

    monkeypatch.setattr(crl_module, "atomic_write_text", full_disk)
    with pytest.raises(CrlError, match=re.escape(f"stored CRL {name} cannot be read")):
        fetch(options(url), layout, [root.certificate], NOW)
    assert (layout.crl_dir / "index.json").read_bytes() == written
    assert "the stopped run cannot be recorded" in caplog.text
    assert "No space left on device" in caplog.text


def test_a_list_that_cannot_be_stored_stops_the_run_unrecorded(
    server: Server, root: Ca, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a stored list that cannot be read is recorded (#171): a list that cannot be written
    (a full disk) stops the run with its ``OSError``, and the index stays as it is."""
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))
    server.routes["/root.crl"] = Route(data)
    assert fetch(options(server.url("/root.crl")), layout, [root.certificate], NOW).exit_code == 0
    written = (layout.crl_dir / "index.json").read_bytes()
    server.routes["/newer.crl"] = Route(make_crl(root, datetime(2026, 11, 2, tzinfo=UTC)))

    def full_disk(path: Path, data: bytes) -> bool:
        raise OSError(28, "No space left on device", str(path))

    monkeypatch.setattr(crl_module, "_store_new", full_disk)
    with pytest.raises(OSError, match="No space left on device"):
        fetch(options(server.url("/newer.crl")), layout, [root.certificate], NOW)
    assert (layout.crl_dir / "index.json").read_bytes() == written


def test_the_index_keeps_the_failures_of_the_latest_run_only(
    server: Server, root: Ca, layout: Layout
) -> None:
    flaky, gone = server.url("/flaky.crl"), closed_port_url()
    fetch(options(flaky), layout, [root.certificate], NOW)
    assert [failure["url"] for failure in read_index(layout)["failures"]] == [flaky]
    server.routes["/flaky.crl"] = Route(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC)))
    later = NOW + timedelta(days=1)
    fetch(options(flaky, gone), layout, [root.certificate], later)
    assert read_index(layout)["failures"] == [
        {"url": gone, "at": "2026-11-03T12:00:00+03:00", "error": "unreachable"}
    ]


def _with_version(der: bytes, version: int) -> bytes:
    """``der`` with another TBSCertList version number (v2 is 1)."""
    at = der.index(b"\x02\x01\x01", 0, 16)
    return der[:at] + bytes([2, 1, version]) + der[at + 3 :]


def test_a_list_the_parser_refuses_is_recorded_and_the_others_are_stored(
    server: Server, root: Ca, layout: Layout
) -> None:
    """An odd version (``x509.InvalidVersion`` is no ValueError) is ``not_a_crl`` for its URL
    alone; the run goes on."""
    good = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))
    server.routes["/odd.crl"] = Route(_with_version(good, 5))
    server.routes["/good.crl"] = Route(good)
    urls = (server.url("/odd.crl"), server.url("/good.crl"))
    run = fetch(options(*urls), layout, [root.certificate], NOW)
    assert [(failure.url, failure.error) for failure in run.failures] == [(urls[0], "not_a_crl")]
    assert [entry["url"] for entry in run.lists] == [urls[1]]
    assert [entry["url"] for entry in read_index(layout)["lists"]] == [urls[1]]


def test_a_list_signed_with_an_algorithm_the_standalone_verifier_lacks_is_not_stored(
    server: Server, root: Ca, layout: Layout
) -> None:
    """SHA3-256: ``cryptography`` would verify it, the package's verifier cannot (rule 5)."""
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC), digest=hashes.SHA3_256())
    server.routes["/sha3.crl"] = Route(data)
    run = fetch(options(server.url("/sha3.crl")), layout, [root.certificate], NOW)
    assert [failure.error for failure in run.failures] == ["bad_signature"]
    assert not parse_crl(data).known_algorithm
    assert parse_crl(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))).known_algorithm


def test_a_url_with_credentials_is_neither_fetched_nor_shown(
    server: Server, root: Ca, layout: Layout, caplog: pytest.LogCaptureFixture
) -> None:
    server.routes["/root.crl"] = Route(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC)))
    port = urlsplit(server.url("/")).port
    secret = f"http://kullanici7:s3cret@127.0.0.1:{port}/root.crl"
    run = fetch(options(secret), layout, [root.certificate], NOW)
    shown = f"http://***@127.0.0.1:{port}/root.crl"
    assert run.as_json()["failures"] == [
        {"url": shown, "at": "2026-11-02T12:00:00+03:00", "error": "unreachable"}
    ]
    assert run.urls == (shown,)
    assert server.requests == []
    index = (layout.crl_dir / "index.json").read_text(encoding="utf-8")
    for text in (caplog.text, index, json.dumps(run.as_json())):
        assert "s3cret" not in text
        assert "kullanici7" not in text


def test_two_runs_never_overlap(server: Server, root: Ca, layout: Layout) -> None:
    """The ``CrlFetch`` task and a run by hand: the second stops while the first holds the
    lock, and the first one's lists stay in the index."""
    server.routes["/slow.crl"] = Route(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC)), delay=1)
    server.routes["/other.crl"] = Route(make_crl(root, datetime(2026, 11, 2, tzinfo=UTC)))
    runs: list[Any] = []
    first = threading.Thread(
        target=lambda: runs.append(
            fetch(options(server.url("/slow.crl")), layout, [root.certificate], NOW)
        )
    )
    first.start()
    deadline = time.monotonic() + 5
    while "/slow.crl" not in server.requests and time.monotonic() < deadline:
        time.sleep(0.01)
    with pytest.raises(CrlError, match="another 'crl fetch' is running"):
        fetch(options(server.url("/other.crl")), layout, [root.certificate], NOW)
    first.join()
    assert runs[0].exit_code == 0
    assert server.requests == ["/slow.crl"]
    fetch(options(server.url("/other.crl")), layout, [root.certificate], NOW)  # free again
    assert len(read_index(layout)["lists"]) == 2


def test_a_server_that_drips_is_stopped_at_the_timeout(root: Ca, layout: Layout) -> None:
    """``timeout_seconds`` bounds the whole download, not only each wait for a byte."""
    listener = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()

    def drip() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.recv(65536)
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
            while not stop.is_set():
                try:
                    connection.sendall(b"0")
                except OSError:
                    return
                stop.wait(0.2)

    thread = threading.Thread(target=drip, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}/drip.crl"
    started = time.monotonic()
    try:
        run = fetch(CrlOptions(urls=(url,), timeout_seconds=1), layout, [root.certificate], NOW)
    finally:
        stop.set()
        listener.close()
    assert [failure.error for failure in run.failures] == ["unreachable"]
    assert time.monotonic() - started < 3


def test_a_server_that_drips_its_headers_is_stopped_at_the_timeout(
    root: Ca, layout: Layout
) -> None:
    """The deadline covers the whole exchange: a server that sends its status line and headers
    a byte at a time (each wait short) is cut too, not only one that drips its body."""
    listener = socket.create_server(("127.0.0.1", 0))
    stop = threading.Event()

    def drip() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.recv(65536)
            for byte in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 10_000:
                if stop.is_set():
                    return
                try:
                    connection.sendall(bytes([byte]))
                except OSError:
                    return
                stop.wait(0.1)

    thread = threading.Thread(target=drip, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}/headers.crl"
    started = time.monotonic()
    try:
        run = fetch(CrlOptions(urls=(url,), timeout_seconds=1), layout, [root.certificate], NOW)
    finally:
        stop.set()
        listener.close()
    assert [failure.error for failure in run.failures] == ["unreachable"]
    assert time.monotonic() - started < 3


# -- the proxy (D13) -----------------------------------------------------------------------------


def test_http_through_the_proxy(server: Server, root: Ca, layout: Layout) -> None:
    server.routes["/root.crl"] = Route(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC)))
    proxy = Proxy()
    try:
        url = server.url("/root.crl")
        run = fetch(options(url, https_proxy=proxy.url), layout, [root.certificate], NOW)
    finally:
        proxy.close()
    assert run.exit_code == 0
    assert proxy.used == [("GET", url)]


def test_https_through_the_proxy(
    root: Ca, layout: Layout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CONNECT tunnel; the server certificate verifies with the test CA (SSL_CERT_FILE)."""
    tls_ca = make_ca("Örnek TLS CA (TEST)")
    host = make_ca("127.0.0.1", tls_ca, ca=False, san="127.0.0.1")
    (tmp_path / "ca.pem").write_bytes(tls_ca.certificate.public_bytes(Encoding.PEM))
    (tmp_path / "host.pem").write_bytes(host.certificate.public_bytes(Encoding.PEM))
    (tmp_path / "host.key").write_bytes(
        host.key.private_bytes(
            Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ca.pem"))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "host.pem", tmp_path / "host.key")
    secure = Server(tls=context)
    secure.routes["/root.crl"] = Route(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC)))
    proxy = Proxy()
    try:
        url = secure.url("/root.crl")
        run = fetch(options(url, https_proxy=proxy.url), layout, [root.certificate], NOW)
    finally:
        proxy.close()
        secure.close()
    assert run.exit_code == 0, run.as_json()
    assert proxy.used == [("CONNECT", f"127.0.0.1:{urlsplit(url).port}")]
    assert secure.requests == ["/root.crl"]


def test_without_the_setting_no_proxy_is_used(
    server: Server, root: Ca, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The environment's proxy variables are ignored: network.https_proxy alone decides."""
    proxy = Proxy()
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.setenv(name, proxy.url)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    server.routes["/root.crl"] = Route(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC)))
    try:
        run = fetch(options(server.url("/root.crl")), layout, [root.certificate], NOW)
    finally:
        proxy.close()
    assert run.exit_code == 0
    assert proxy.used == []


@pytest.mark.parametrize(
    "proxy",
    [
        "https://192.0.2.1:3128",
        "http://user:secret@192.0.2.1:3128",
        "http://:3128",
        "http://192.0.2.1:3128/path",
        "socks5://192.0.2.1:1080",
        "http://192.0.2.1:abc",
        "http://192.0.2.1:99999",
        "http://192.0.2.1",
    ],
)
def test_a_proxy_other_than_http_host_port_is_refused(proxy: str) -> None:
    with pytest.raises(CrlError, match="https_proxy"):
        CrlOptions(https_proxy=proxy)
    assert CrlOptions(https_proxy="http://192.0.2.1:3128").https_proxy == "http://192.0.2.1:3128"
    assert CrlOptions(https_proxy="http://proxy.example.com:8080/").https_proxy


# -- where the URLs come from (D7) ----------------------------------------------------------------


def test_distribution_points_of_the_roots_and_the_newest_token_of_each_provider(
    server: Server, root: Ca, layout: Layout
) -> None:
    intermediate = make_ca("Örnek Ara SM (TEST)", root, cdp=[server.url("/root.crl")])
    tsa_new = make_ca("Örnek TSA 2", intermediate, ca=False, cdp=[server.url("/ara.crl")])
    tsa_old = make_ca("Örnek TSA 1", intermediate, ca=False, cdp=[server.url("/old.crl")])
    other = make_ca("Örnek Deneme TSA", root, ca=False, cdp=["ldap://192.0.2.5/cn=list"])
    seal_token(layout, date(2026, 10, 30), "zamane", [tsa_old.certificate])
    seal_token(
        layout, date(2026, 10, 31), "zamane", [tsa_new.certificate, intermediate.certificate]
    )
    seal_token(layout, date(2026, 11, 1), "rfc3161", [other.certificate])
    # A day whose manifest cannot be read, and one without a token, are passed over.
    broken = layout.manifest(date(2026, 11, 2))
    broken.parent.mkdir(parents=True)
    broken.write_text("{", encoding="utf-8")

    seen = token_certificates(layout)
    assert seen == [other.certificate, tsa_new.certificate, intermediate.certificate]
    assert distribution_points([root.certificate, *seen]) == [
        server.url("/ara.crl"),
        server.url("/root.crl"),
    ]
    # A TSA certificate signs no list: only CA certificates that chain to a root.
    assert trusted_issuers([root.certificate], seen) == [root.certificate, intermediate.certificate]

    this_update = datetime(2026, 11, 1, tzinfo=UTC)
    server.routes["/ara.crl"] = Route(make_crl(intermediate, this_update, pem=True))
    server.routes["/root.crl"] = Route(make_crl(root, this_update))
    run = fetch(options(), layout, [root.certificate], NOW)
    assert run.exit_code == 0
    assert [entry["issuer_sha256"] for entry in run.lists] == [
        intermediate.fingerprint,
        root.fingerprint,
    ]
    assert "/old.crl" not in server.requests


def test_configured_urls_replace_the_distribution_points(
    server: Server, root: Ca, layout: Layout
) -> None:
    pinned = make_ca("Örnek Kök 2 (TEST)", cdp=[server.url("/dp.crl")])
    server.routes["/set.crl"] = Route(make_crl(pinned, datetime(2026, 11, 1, tzinfo=UTC)))
    url = server.url("/set.crl")
    run = fetch(options(url, url), layout, [pinned.certificate], NOW)
    assert run.urls == (url,)
    assert server.requests == ["/set.crl"]


def test_the_lists_of_every_intermediate_seen_in_a_token_verify(
    server: Server, root: Ca, layout: Layout
) -> None:
    """After the provider changed its intermediate CA, the old intermediate (only in older
    tokens) still signs lists: each CA certificate of any token is an issuer, each loaded
    once. The URLs still come from the newest token of each provider (D7)."""
    old = make_ca("Örnek Ara SM 1 (TEST)", root, cdp=[server.url("/root.crl")])
    new = make_ca("Örnek Ara SM 2 (TEST)", root, cdp=[server.url("/root.crl")])
    tsa_old = make_ca("Örnek TSA 1", old, ca=False, cdp=[server.url("/ara-1.crl")])
    tsa_new = make_ca("Örnek TSA 2", new, ca=False, cdp=[server.url("/ara-2.crl")])
    for day in (28, 29):
        seal_token(layout, date(2026, 10, day), "zamane", [tsa_old.certificate, old.certificate])
    seal_token(layout, date(2026, 10, 31), "zamane", [tsa_new.certificate, new.certificate])
    scanned = crl_module.scan_tokens(layout)
    assert scanned.newest == (tsa_new.certificate, new.certificate)
    assert scanned.every == (
        tsa_new.certificate,
        new.certificate,
        tsa_old.certificate,
        old.certificate,
    )
    this_update = datetime(2026, 11, 1, tzinfo=UTC)
    server.routes["/ara-1.crl"] = Route(make_crl(old, this_update))
    server.routes["/ara-2.crl"] = Route(make_crl(new, this_update))
    server.routes["/root.crl"] = Route(make_crl(root, this_update))
    run = fetch(options(), layout, [root.certificate], NOW)  # D7: the newest token's points
    assert run.exit_code == 0, run.as_json()
    assert "/ara-1.crl" not in server.requests
    urls = (server.url("/ara-1.crl"), server.url("/ara-2.crl"))
    run = fetch(options(*urls), layout, [root.certificate], NOW)
    assert run.exit_code == 0, run.as_json()
    assert [entry["issuer_sha256"] for entry in run.lists] == [old.fingerprint, new.fingerprint]


def test_an_intermediate_that_does_not_chain_to_a_root_is_not_trusted(
    server: Server, root: Ca, layout: Layout
) -> None:
    rogue_root = make_ca("Sahte Kök")
    rogue = make_ca("Örnek Ara SM (TEST)", rogue_root)  # same name as a real intermediate
    seal_token(layout, date(2026, 10, 31), "zamane", [rogue.certificate])
    assert trusted_issuers([root.certificate], [rogue.certificate]) == [root.certificate]
    server.routes["/ara.crl"] = Route(make_crl(rogue, datetime(2026, 11, 1, tzinfo=UTC)))
    run = fetch(options(server.url("/ara.crl")), layout, [root.certificate], NOW)
    assert [failure.error for failure in run.failures] == ["unknown_issuer"]


def test_a_certificate_that_does_not_chain_names_no_url(
    server: Server, root: Ca, layout: Layout
) -> None:
    """Anyone can add a certificate to a token's unsigned set: only those that chain to a
    pinned root give distribution points."""
    stray_root = make_ca("Sahte Kök")
    stray = make_ca("Örnek TSA 3", stray_root, ca=False, cdp=["http://192.0.2.9/internal.crl"])
    tsa = make_ca("Örnek TSA 2", root, ca=False, cdp=[server.url("/root.crl")])
    seal_token(layout, date(2026, 10, 31), "zamane", [tsa.certificate, stray.certificate])
    seen = token_certificates(layout)
    assert crl_urls(CrlOptions(), [root.certificate], seen) == [server.url("/root.crl")]


def test_an_issuer_without_crl_sign_is_not_a_crl_issuer(
    server: Server, root: Ca, layout: Layout, caplog: pytest.LogCaptureFixture
) -> None:
    """keyUsage without cRLSign (RFC 5280 4.2.1.3); the issuer's name, from bytes not verified
    yet, is logged without its line breaks."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = _name("Örnek Ara SM\nWARNING forged line")
    usage = x509.KeyUsage(
        digital_signature=False,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=True,
        crl_sign=False,
        encipher_only=False,
        decipher_only=False,
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(root.certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(usage, critical=True)
        .sign(root.key, hashes.SHA256())
    )
    intermediate = Ca(certificate, key)
    seal_token(layout, date(2026, 10, 31), "zamane", [certificate])
    assert trusted_issuers([root.certificate], [certificate]) == [root.certificate, certificate]
    server.routes["/ara.crl"] = Route(make_crl(intermediate, datetime(2026, 11, 1, tzinfo=UTC)))
    run = fetch(options(server.url("/ara.crl")), layout, [root.certificate], NOW)
    assert [failure.error for failure in run.failures] == ["unknown_issuer"]
    assert "\nWARNING" not in caplog.text
    assert "\\nWARNING" in caplog.text


# -- nothing is ever deleted or overwritten (D8, Q4) ----------------------------------------------


def test_a_newer_list_is_added_and_the_older_one_stays(
    server: Server, root: Ca, layout: Layout
) -> None:
    url = server.url("/root.crl")
    first = make_crl(root, datetime(2026, 10, 25, tzinfo=UTC))
    server.routes["/root.crl"] = Route(first)
    fetch(options(url), layout, [root.certificate], NOW - timedelta(days=7))
    second = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC), [(7, NOW, None)])
    server.routes["/root.crl"] = Route(second)
    fetch(options(url), layout, [root.certificate], NOW)
    # The same list again: one file, the entry says when it was last fetched.
    later = NOW + timedelta(hours=1)
    run = fetch(options(url), layout, [root.certificate], later)
    assert run.exit_code == 0

    files = sorted(path.name for path in layout.crl_dir.glob("*.crl"))
    assert files == [
        stored_name(root, "20261025T000000Z", first),
        stored_name(root, "20261101T000000Z", second),
    ]
    assert (layout.crl_dir / files[0]).read_bytes() == first
    assert (layout.crl_dir / files[1]).read_bytes() == second
    index = read_index(layout)
    assert [entry["file"] for entry in index["lists"]] == files
    assert [entry["fetched_at"] for entry in index["lists"]] == [
        "2026-10-26T12:00:00+03:00",
        "2026-11-02T13:00:00+03:00",
    ]


def test_two_lists_with_the_same_this_update_are_both_kept(
    server: Server, root: Ca, layout: Layout
) -> None:
    """C7: the stored name carries the file's hash, so a re-issue with the same issuer and
    thisUpdate is kept beside the first list; the same bytes again keep their file and entry."""
    this_update = datetime(2026, 11, 1, tzinfo=UTC)
    first = make_crl(root, this_update)
    other = make_crl(root, this_update, [(9, this_update, None)])  # the same second, re-issued
    server.routes["/root.crl"] = Route(first)
    server.routes["/reissued.crl"] = Route(other)
    fetch(options(server.url("/root.crl")), layout, [root.certificate], NOW)
    later = NOW + timedelta(hours=1)
    run = fetch(options(server.url("/reissued.crl")), layout, [root.certificate], later)

    assert run.exit_code == 0
    names = [stored_name(root, "20261101T000000Z", data) for data in (first, other)]
    assert names[0] != names[1]
    assert (layout.crl_dir / names[0]).read_bytes() == first
    assert (layout.crl_dir / names[1]).read_bytes() == other
    index = read_index(layout)
    assert sorted(entry["file"] for entry in index["lists"]) == sorted(names)
    assert {entry["file"]: entry["url"] for entry in index["lists"]} == {
        names[0]: server.url("/root.crl"),
        names[1]: server.url("/reissued.crl"),
    }
    assert [crl.revoked for crl in load_stored(layout)] in (
        [{}, {9: this_update}],
        [{9: this_update}, {}],
    )
    # Both again, and once more after the index lost them (e.g. a run stopped after storing the
    # files): the files are reused, no other file appears and none is gone.
    urls = (server.url("/root.crl"), server.url("/reissued.crl"))
    assert fetch(options(*urls), layout, [root.certificate], later).exit_code == 0
    (layout.crl_dir / "index.json").write_text(
        json.dumps({"schema": INDEX_SCHEMA, "lists": [], "failures": []}), encoding="utf-8"
    )
    run = fetch(options(*urls), layout, [root.certificate], later)
    assert [entry["file"] for entry in run.lists] == names
    assert sorted(path.name for path in layout.crl_dir.glob("*.crl")) == sorted(names)
    assert (layout.crl_dir / names[0]).read_bytes() == first
    assert (layout.crl_dir / names[1]).read_bytes() == other


def test_a_delta_list_is_neither_stored_nor_used(
    server: Server, root: Ca, layout: Layout, caplog: pytest.LogCaptureFixture
) -> None:
    """C7: a delta list (DeltaCRLIndicator) is recorded as ``delta_not_supported`` beside the
    base list, which is stored; one put into the folder and the index by hand is not used."""
    this_update = datetime(2026, 11, 1, tzinfo=UTC)
    base = make_crl(root, this_update)
    delta = make_crl(root, this_update, [(9, this_update, None)], delta_of=1)
    assert parse_crl(delta).delta
    assert not parse_crl(base).delta
    server.routes["/base.crl"] = Route(base)
    server.routes["/delta.crl"] = Route(delta)
    urls = (server.url("/base.crl"), server.url("/delta.crl"))
    run = fetch(options(*urls), layout, [root.certificate], NOW)

    assert run.exit_code == 1
    assert [entry["url"] for entry in run.lists] == [server.url("/base.crl")]
    assert read_index(layout)["failures"] == [
        {
            "url": server.url("/delta.crl"),
            "at": "2026-11-02T12:00:00+03:00",
            "error": "delta_not_supported",
        }
    ]
    assert sorted(path.name for path in layout.crl_dir.glob("*.crl")) == [
        stored_name(root, "20261101T000000Z", base)
    ]
    assert "a delta list is not stored" in caplog.text
    # By hand: the file and its entry, as fetch would have written them.
    name = stored_name(root, "20261101T000000Z", delta)
    (layout.crl_dir / name).write_bytes(delta)
    index = read_index(layout)
    index["lists"].append(
        {**index["lists"][0], "file": name, "sha256": hashlib.sha256(delta).hexdigest()}
    )
    (layout.crl_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
    assert [crl.sha256 for crl in load_stored(layout)] == [hashlib.sha256(base).hexdigest()]
    assert f"stored CRL {name} is a delta list" in caplog.text


def test_a_changed_stored_file_is_recorded_and_stays(
    server: Server, root: Ca, layout: Layout
) -> None:
    """C14, the index lost the entry: the name holds the file's hash, so other bytes under it
    were changed after storing. The file is never overwritten (D8); the URL's failure is
    ``stored_file_changed`` and the run exits 1. The URLs after it still run."""
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))
    server.routes["/root.crl"] = Route(data)
    url = server.url("/root.crl")
    fetch(options(url), layout, [root.certificate], NOW)
    name = stored_name(root, "20261101T000000Z", data)
    changed = data[:-1] + bytes([data[-1] ^ 1])
    (layout.crl_dir / name).write_bytes(changed)  # test: the stored file is changed
    (layout.crl_dir / "index.json").write_text(
        json.dumps({"schema": INDEX_SCHEMA, "lists": [], "failures": []}), encoding="utf-8"
    )
    newer = make_crl(root, datetime(2026, 11, 2, tzinfo=UTC))
    server.routes["/newer.crl"] = Route(newer)
    second = server.url("/newer.crl")
    run = fetch(options(url, second), layout, [root.certificate], NOW)
    assert run.exit_code == 1
    assert [(failure.url, failure.error) for failure in run.failures] == [
        (url, "stored_file_changed")
    ]
    assert (layout.crl_dir / name).read_bytes() == changed
    newer_name = stored_name(root, "20261102T000000Z", newer)
    assert (layout.crl_dir / newer_name).read_bytes() == newer
    index = read_index(layout)
    assert [(entry["file"], entry["url"]) for entry in index["lists"]] == [(newer_name, second)]
    assert [(f["url"], f["error"]) for f in index["failures"]] == [(url, "stored_file_changed")]


def test_a_changed_stored_file_with_its_index_entry_is_recorded_and_stays(
    server: Server, root: Ca, layout: Layout
) -> None:
    """C14, the index intact: a list revoking serial 77 is stored, then one byte of its file
    changes. The next fetch hashes the stored file of the known entry: ``stored_file_changed``,
    exit 1, the file and its entry stay; ``load_stored`` names it and ``verify_day`` warns
    instead of dropping the revocation silently."""
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC), [(77, NOW - timedelta(days=9), None)])
    server.routes["/root.crl"] = Route(data)
    url = server.url("/root.crl")
    assert fetch(options(url), layout, [root.certificate], NOW).exit_code == 0
    name = stored_name(root, "20261101T000000Z", data)
    assert [crl.revoked for crl in load_stored(layout)] == [{77: NOW - timedelta(days=9)}]
    entries = read_index(layout)["lists"]
    changed = bytearray(data)
    changed[len(changed) // 2] ^= 1
    (layout.crl_dir / name).write_bytes(bytes(changed))  # test: the stored file is changed

    run = fetch(options(url), layout, [root.certificate], NOW)
    assert run.exit_code == 1
    assert [(failure.url, failure.error) for failure in run.failures] == [
        (url, "stored_file_changed")
    ]
    assert (layout.crl_dir / name).read_bytes() == bytes(changed)
    index = read_index(layout)
    assert index["lists"] == entries
    assert [(f["url"], f["error"]) for f in index["failures"]] == [(url, "stored_file_changed")]
    stored = load_stored(layout)
    assert (list(stored), stored.mismatched) == ([], (name,))
    check = verify_day(layout, date(2026, 10, 31), [root.certificate])
    assert f"revocation list {name} does not match its index entry" in check.warnings


def test_an_older_list_changed_after_its_url_moved_on_is_recorded(
    server: Server, root: Ca, layout: Layout
) -> None:
    """C14, the usual case with daily lists: the list revoking serial 77 is stored, its URL
    serves a newer list by now, and one byte of the older file changes. The fetch checks every
    stored file, not only the ones served again: ``stored_file_changed`` with the older entry's
    URL, exit 1; the newer list is stored, the older file and its entry stay. A stored file
    that is gone is recorded the same way."""
    old = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC), [(77, NOW - timedelta(days=9), None)])
    server.routes["/root.crl"] = Route(old)
    url = server.url("/root.crl")
    assert fetch(options(url), layout, [root.certificate], NOW).exit_code == 0
    old_name = stored_name(root, "20261101T000000Z", old)
    old_entry = read_index(layout)["lists"][0]
    newer = make_crl(root, datetime(2026, 11, 2, tzinfo=UTC), [(77, NOW - timedelta(days=9), None)])
    server.routes["/root.crl"] = Route(newer)
    assert fetch(options(url), layout, [root.certificate], NOW).exit_code == 0
    changed = bytearray(old)
    changed[len(changed) // 2] ^= 1
    (layout.crl_dir / old_name).write_bytes(bytes(changed))  # test: the older file is changed
    newest = make_crl(root, datetime(2026, 11, 3, tzinfo=UTC))
    server.routes["/root.crl"] = Route(newest)

    run = fetch(options(url), layout, [root.certificate], NOW)
    assert run.exit_code == 1
    assert [(failure.url, failure.error) for failure in run.failures] == [
        (url, "stored_file_changed")
    ]
    newest_name = stored_name(root, "20261103T000000Z", newest)
    assert [entry["file"] for entry in run.lists] == [newest_name]
    assert (layout.crl_dir / old_name).read_bytes() == bytes(changed)
    index = read_index(layout)
    assert [entry["file"] for entry in index["lists"]] == sorted(
        [old_name, stored_name(root, "20261102T000000Z", newer), newest_name]
    )
    assert old_entry in index["lists"]
    assert [(f["url"], f["error"]) for f in index["failures"]] == [(url, "stored_file_changed")]
    assert load_stored(layout).mismatched == (old_name,)
    # The newest list again: the older file is still changed, the failure stays.
    run = fetch(options(url), layout, [root.certificate], NOW)
    assert [(failure.url, failure.error) for failure in run.failures] == [
        (url, "stored_file_changed")
    ]
    # Gone instead of changed: the same failure.
    (layout.crl_dir / old_name).rename(layout.crl_dir / "kept-aside.bin")  # test: the file is gone
    run = fetch(options(url), layout, [root.certificate], NOW)
    assert [(failure.url, failure.error) for failure in run.failures] == [
        (url, "stored_file_changed")
    ]
    assert not (layout.crl_dir / old_name).exists()


def test_a_known_list_whose_file_is_gone_is_stored_again(
    server: Server, root: Ca, layout: Layout
) -> None:
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))
    server.routes["/root.crl"] = Route(data)
    url = server.url("/root.crl")
    fetch(options(url), layout, [root.certificate], NOW)
    (name,) = [entry["file"] for entry in read_index(layout)["lists"]]
    (layout.crl_dir / name).rename(layout.crl_dir / "kept-aside.bin")  # test: the file is gone
    run = fetch(options(url), layout, [root.certificate], NOW)  # C20: stored again, and recorded
    assert run.exit_code == 1
    assert [(failure.url, failure.error) for failure in run.failures] == [
        (url, "stored_file_changed")
    ]
    assert read_index(layout)["failures"] == [failure.as_json() for failure in run.failures]
    assert (layout.crl_dir / name).read_bytes() == data
    assert [entry["file"] for entry in read_index(layout)["lists"]] == [name]


def test_an_index_entry_with_a_foreign_file_name_is_not_reused(
    server: Server, root: Ca, layout: Layout, tmp_path: Path
) -> None:
    """An entry whose ``file`` is not a stored name (a changed index) is never followed."""
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))
    outside = tmp_path / "outside.crl"
    outside.write_bytes(data)
    layout.crl_dir.mkdir(parents=True)
    entry = {"file": str(outside), "sha256": hashlib.sha256(data).hexdigest()}
    index = {"schema": INDEX_SCHEMA, "lists": [entry], "failures": []}
    (layout.crl_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
    server.routes["/root.crl"] = Route(data)
    run = fetch(options(server.url("/root.crl")), layout, [root.certificate], NOW)
    name = stored_name(root, "20261101T000000Z", data)
    assert [entry["file"] for entry in run.lists] == [name]
    assert (layout.crl_dir / name).read_bytes() == data


#: The one removal ``crl.py`` may make: the first step of ``fetch`` under its lock, of the
#: temporary names ``_store_new`` makes (the tempfile module's ``.`` + 8 characters + ``.tmp``)
#: that a stopped run left (#170, Q15). A change to it fails the guard below, to be looked at.
_LEFTOVER_CLEANUP = r"""
with os.scandir(layout.crl_dir) as entries:
    leftovers = [
        entry.name
        for entry in entries
        if re.fullmatch(r"\.[a-z0-9_]{8}\.tmp", entry.name)
        and entry.is_file(follow_symlinks=False)
    ]
for name in sorted(leftovers):
    try:
        (layout.crl_dir / name).unlink()
    except FileNotFoundError:
        continue
    except OSError as exc:
        log.warning("state/crl: leftover temporary file %s not removed: %s", name, exc)
        continue
    log.warning("state/crl: removed the leftover temporary file %s of a stopped run", name)
"""
#: What removes, renames or overwrites a file. Path.replace is a rename over the target;
#: dataclasses' replace makes a copy. A file opened to write may be one that is there.
_DELETE_OR_OVERWRITE = re.compile(
    r"\b(unlink|remove|rmtree|rmdir|os\.replace|\.rename|truncate|O_TRUNC|write_bytes"
    r"|write_text|shutil)\b"
    r"|(?<!\bdataclasses)\.replace\("
    r"""|\bopen\([^)]*["'][rbtx]*[wa+]"""
)


def test_the_crl_module_has_no_way_to_delete() -> None:
    """Static guard (D8, rule 2): ``crl.py`` only writes. The index goes through fsutil's
    atomic write; a list is linked from a fresh temporary file (``os.link`` never replaces a
    file), whose temporary name the tempfile module removes. The one removal is
    ``_LEFTOVER_CLEANUP``, the first step of ``fetch`` under the lock (#170, Q15): a second way
    to delete, rename or overwrite anywhere in the module fails here, and so does a change to
    that one (another pattern, another place, symbolic links followed)."""
    source = (CORE_SRC / "arcilogger_core" / "crl.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    (fetch_def,) = (
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "fetch"
    )
    (locked,) = (
        node
        for node in fetch_def.body
        if isinstance(node, ast.With)
        and [ast.unparse(item.context_expr) for item in node.items] == ["_one_run(layout.crl_dir)"]
    )
    cleanup = ast.parse(_LEFTOVER_CLEANUP).body
    assert [ast.dump(node) for node in locked.body[: len(cleanup)]] == [
        ast.dump(node) for node in cleanup
    ], "fetch's first step under the lock is the leftover cleanup, as it stands above"
    (allowed,) = (
        node.lineno
        for statement in locked.body[: len(cleanup)]
        for node in ast.walk(statement)
        if isinstance(node, ast.Attribute) and node.attr == "unlink"
    )
    found = [
        (source.count("\n", 0, match.start()) + 1, match.group())
        for match in _DELETE_OR_OVERWRITE.finditer(source)
    ]
    assert found == [(allowed, "unlink")], "crl.py must never delete, rename or overwrite a file"


@pytest.mark.parametrize(
    "line",
    [
        "path.unlink()",
        "os.unlink(path)",
        "os.remove(path)",
        "shutil.rmtree(folder)",
        "folder.rmdir()",
        "os.replace(temporary, path)",
        "path.replace(target)",
        "temporary.rename(path)",
        "os.truncate(path, 0)",
        "os.open(path, os.O_WRONLY | os.O_TRUNC)",
        "path.write_bytes(data)",
        "path.write_text(text)",
        "shutil.move(temporary, path)",
        'open(path, "wb")',
        "path.open('a')",
        'open(path, mode="r+b")',
    ],
)
def test_the_delete_guard_sees_every_way_to_delete_or_overwrite(line: str) -> None:
    assert _DELETE_OR_OVERWRITE.search(line), line


@pytest.mark.parametrize(
    "line",
    [
        "dataclasses.replace(crl, url=url)",
        "replace(parse_crl(data), path=path)",
        "os.link(temporary.name, path)",
        "atomic_write_text(path, text)",
        "os.open(folder / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)",
        "opener.open(request, timeout=options.timeout_seconds)",
        'open(path, "rb")',
        "the removed file",
    ],
)
def test_the_delete_guard_lets_reads_and_new_files_be(line: str) -> None:
    assert not _DELETE_OR_OVERWRITE.search(line), line


def _leftover(folder: Path) -> Path:
    """A temporary name as ``_store_new`` makes it (the tempfile module's own naming), kept as a
    stopped run keeps it. If tempfile names its files otherwise one day, the cleanup's pattern
    no longer matches and the tests below fail."""
    with tempfile.NamedTemporaryFile(
        dir=folder, prefix=".", suffix=".tmp", delete=False
    ) as temporary:
        temporary.write(b"part of a list")
    return Path(temporary.name)


def test_fetch_removes_the_temporary_names_a_stopped_run_left(
    server: Server, root: Ca, layout: Layout, caplog: pytest.LogCaptureFixture
) -> None:
    """#170, Q15: a run stopped between ``_store_new``'s link and the tempfile module's cleanup
    leaves the temporary name, a hard link to the stored list when the link was made. The next
    run removes that name under its lock and logs it; the list keeps its bytes and its entry."""
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))
    server.routes["/root.crl"] = Route(data)
    url = server.url("/root.crl")
    assert fetch(options(url), layout, [root.certificate], NOW).exit_code == 0
    name = stored_name(root, "20261101T000000Z", data)
    stored = layout.crl_dir / name
    before = read_index(layout)
    linked = _leftover(layout.crl_dir)
    linked.unlink()  # test: the name made again as the hard link a stopped run leaves
    os.link(stored, linked)
    partial = _leftover(layout.crl_dir)  # stopped before the link: a part of a list
    assert stored.stat().st_nlink == 2

    run = fetch(options(url), layout, [root.certificate], NOW + timedelta(days=1))
    assert run.exit_code == 0, run.as_json()
    assert sorted(path.name for path in layout.crl_dir.iterdir()) == [LOCK_NAME, name, "index.json"]
    assert stored.read_bytes() == data
    assert stored.stat().st_nlink == 1
    (entry,) = read_index(layout)["lists"]
    assert {**entry, "fetched_at": None} == {**before["lists"][0], "fetched_at": None}
    for leftover in (linked, partial):
        assert f"removed the leftover temporary file {leftover.name}" in caplog.text


def test_fetch_removes_only_the_temporary_names_of_store_new(
    server: Server, root: Ca, layout: Layout, caplog: pytest.LogCaptureFixture
) -> None:
    """The cleanup takes the pattern of ``_store_new``'s names only: a stored list, one not in
    the index, the index, the lock, a file of someone else and names that only look alike (a
    ``.crl`` among them, a folder, a symbolic link where the system allows one) all stay."""
    data = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC))
    server.routes["/root.crl"] = Route(data)
    url = server.url("/root.crl")
    assert fetch(options(url), layout, [root.certificate], NOW).exit_code == 0
    folder = layout.crl_dir
    name = stored_name(root, "20261101T000000Z", data)
    kept: dict[str, bytes] = {
        "kept-aside.crl": data,  # a list that is not in the index
        "notes.txt": b"operator's notes",
        ".abcdefgh.crl": data,  # the temporary pattern's stem, a list's type
        ".abcdefgh.tmp.crl": data,
        ".abcdefg.tmp": b"seven",  # one character short
        ".abcdefghi.tmp": b"nine",  # one character more
        "abcdefgh.tmp": b"no dot",
        ".abcd-fgh.tmp": b"not tempfile's characters",
        ".abcdefgh.tmp~": b"another type",
    }
    for kept_name, kept_bytes in kept.items():
        (folder / kept_name).write_bytes(kept_bytes)
    (folder / ".folder00.tmp").mkdir()
    (folder / ".folder00.tmp" / "inside.txt").write_bytes(b"inside")
    try:
        (folder / ".symlink0.tmp").symlink_to(folder / name)
        symlink = True
    except OSError:  # Windows without the symbolic-link privilege
        symlink = False
    leftover = _leftover(folder)
    names = sorted(path.name for path in folder.iterdir())

    run = fetch(options(url), layout, [root.certificate], NOW + timedelta(days=1))
    assert run.exit_code == 0, run.as_json()
    assert sorted(path.name for path in folder.iterdir()) == sorted(set(names) - {leftover.name})
    assert (folder / name).read_bytes() == data
    for kept_name, kept_bytes in kept.items():
        assert (folder / kept_name).read_bytes() == kept_bytes, kept_name
    assert (folder / ".folder00.tmp" / "inside.txt").read_bytes() == b"inside"
    if symlink:
        assert (folder / ".symlink0.tmp").is_symlink()
    assert [entry["file"] for entry in read_index(layout)["lists"]] == [name]
    assert caplog.text.count("removed the leftover temporary file") == 1


def test_a_leftover_temporary_name_stays_while_another_run_holds_the_lock(
    root: Ca, layout: Layout
) -> None:
    """The cleanup runs under the lock: a run that finds ``state/crl`` locked stops before it,
    and the name the other run may still be writing stays."""
    layout.crl_dir.mkdir(parents=True)
    leftover = _leftover(layout.crl_dir)
    with crl_module._one_run(layout.crl_dir), pytest.raises(CrlError, match="another"):
        fetch(options(), layout, [root.certificate], NOW)
    assert leftover.read_bytes() == b"part of a list"
    assert fetch(options(), layout, [root.certificate], NOW).exit_code == 2  # free again
    assert not leftover.exists()


_URL = re.compile(r"""https?://[^\s"'`<>)\]]+""", re.IGNORECASE)
#: Words of a revocation list's address: crl, the Turkish "sil" and "depo" (repository) as a
#: word of the host or path, or a list's file type at its end.
_LIST_WORDS = re.compile(r"crl|(^|[/._-])(sil|depo)([/._-]|$)|\.(sil|der|pem|p7c)$", re.IGNORECASE)
#: Kamu SM / TÜRKTRUST addresses the code may name: the time-stamp servers and Kamu SM's
#: product page (none of them a revocation list).
_PROVIDER_URLS = (
    "zd.kamusm.gov.tr",
    "zd.turktrust.com.tr",
    "kamusm.bilgem.tubitak.gov.tr/urunler/zaman_damgasi/",
)


def _looks_like_a_list_url(url: str) -> bool:
    # Split by hand: a template such as https://[{bind}] is no URL urlsplit accepts.
    host, _, path = re.sub(r"^https?://", "", url.rstrip(".,;:"), flags=re.IGNORECASE).partition(
        "/"
    )
    if _LIST_WORDS.search(host) or _LIST_WORDS.search(f"/{path}"):
        return True
    # A host written as a constant ({KAMUSM_TEST_HOST}) is a time-stamp server's.
    provider = None if "{" in host else re.search(r"kamusm|turktrust", host, re.IGNORECASE)
    where = f"{host}/{path}".rstrip("/")
    return provider is not None and where not in {known.rstrip("/") for known in _PROVIDER_URLS}


def test_the_k10_guard_recognises_list_urls() -> None:
    for url in (
        "http://crl.example.com/kok.crl",
        "http://depo.example.gov.tr/sil/kok.sil",
        "http://depo.example.gov.tr/kok.der",
        "http://www.example.com/SIL/kok",
        "http://pki.example.com/kok.p7c",
        "http://sertifikalar.kamusm.gov.tr/kok",
        "http://www.turktrust.com.tr/kok",
    ):
        assert _looks_like_a_list_url(url), url
    for url in (
        "https://zd.kamusm.gov.tr/",
        "https://zd.turktrust.com.tr/",
        "https://kamusm.bilgem.tubitak.gov.tr/urunler/zaman_damgasi/",
        "https://www.python.org/downloads/",
        "http://host:port",
        "https://arcilogger.internal",
        "http://{KAMUSM_PRODUCTION_HOST}",
        "https://[{bind}",
    ):
        assert not _looks_like_a_list_url(url), url


def test_k10_no_crl_url_is_written_into_the_code() -> None:
    """K10 (docs/kamusm.md): Kamu SM's CRL URLs are not confirmed, so none is built in. CRL
    URLs come only from ``tsa.crl_urls`` or from the certificates' distribution points. Every
    text file of core/src is read (the standalone verifier's scripts too); ``crl.py`` names no
    address at all but the proxy setting's form."""
    found = []
    for path in sorted(CORE_SRC.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        text = path.read_bytes().decode("utf-8", "replace")
        for match in _URL.findall(text):
            own = path.name == "crl.py" and match != "http://host:port"
            if own or _looks_like_a_list_url(match):
                found.append(f"{path.relative_to(CORE_SRC)}: {match}")
    assert found == [], (
        "A CRL URL is written into core/src. Kamu SM's CRL locations are unconfirmed (K10, "
        "'sahada doğrula'): put them in tsa.crl_urls on site instead:\n" + "\n".join(found)
    )


# -- reading the lists back, and the package selection (D9) ---------------------------------------


def _store(layout: Layout, server: Server, ca: Ca, *days: int) -> None:
    for day in days:
        server.routes[f"/{day}.crl"] = Route(make_crl(ca, datetime(2026, 10, day, tzinfo=UTC)))
    urls = [server.url(f"/{day}.crl") for day in days]
    assert fetch(options(*urls), layout, [ca.certificate], NOW).exit_code == 0


def test_crls_for_picks_the_newest_and_the_first_after_each_stamp(
    server: Server, root: Ca, layout: Layout
) -> None:
    other = make_ca("Örnek Kök 3 (TEST)")
    _store(layout, server, root, 1, 8, 15, 22, 29)
    _store(layout, server, other, 2, 30)
    stored = {crl.this_update.day: crl.path.name for crl in load_stored(layout) if crl.path}
    assert stored[29].startswith(f"{root.fingerprint[:12]}-20261029T000000Z-")

    def names(moments: list[datetime]) -> list[str]:
        return [path.name for path in crls_for(layout, root.certificate, moments)]

    assert names([]) == [stored[29]]
    stamps = [
        datetime(2026, 10, 8, tzinfo=UTC),  # a list of exactly that moment counts
        datetime(2026, 10, 9, 3, tzinfo=UTC),
        datetime(2026, 10, 10, 1, tzinfo=UTC),  # the same list as the stamp before
        datetime(2026, 11, 1, tzinfo=UTC),  # no list after it yet: only the newest
    ]
    assert names(stamps) == [stored[8], stored[15], stored[29]]
    # A moment in site time is compared in UTC; a naive one is refused.
    site = datetime(2026, 10, 15, 2, 0, tzinfo=SITE_TZ)  # 2026-10-14T23:00Z
    assert names([site]) == [stored[15], stored[29]]
    with pytest.raises(ValueError, match="naive"):
        crls_for(layout, root.certificate, [datetime(2026, 10, 9)])  # noqa: DTZ001
    assert [p.name[:12] for p in crls_for(layout, other.certificate, [])] == [
        other.fingerprint[:12]
    ]
    stranger = make_ca("Örnek Kök SM (TEST)")  # the same name, another key
    assert crls_for(layout, stranger.certificate, stamps) == []
    loaded = load_stored(layout)
    assert crls_for(layout, root.certificate, [], stored=loaded) == crls_for(
        layout, root.certificate, []
    )


def test_load_stored_reads_the_index_and_skips_what_does_not_match(
    server: Server, root: Ca, layout: Layout, caplog: pytest.LogCaptureFixture
) -> None:
    hold, revoked_at = 0x51, datetime(2026, 10, 20, tzinfo=UTC)
    server.routes["/root.crl"] = Route(
        make_crl(
            root,
            datetime(2026, 11, 1, tzinfo=UTC),
            [(hold, revoked_at, x509.ReasonFlags.certificate_hold), (0x52, revoked_at, None)],
        )
    )
    _store(layout, server, root, 3, 4, 5)
    fetch(options(server.url("/root.crl")), layout, [root.certificate], NOW)
    crls = load_stored(layout)
    assert [crl.this_update.day for crl in crls] == [3, 4, 5, 1]
    newest = crls[-1]
    assert newest.revoked == {hold: revoked_at, 0x52: revoked_at}
    assert newest.reasons == {hold: CRL_REASON_HOLD}
    assert newest.issuer_sha256 == root.fingerprint
    assert newest.url == server.url("/root.crl")
    assert newest.path is not None
    assert newest.path.parent == layout.crl_dir
    assert newest.signed_by(root.certificate)

    index_path = layout.crl_dir / "index.json"
    index = read_index(layout)
    files = [entry["file"] for entry in index["lists"]]
    (day_1,) = [entry for entry in index["lists"] if entry["file"] == newest.path.name]
    with (layout.crl_dir / files[0]).open("ab") as handle:  # changed after it was stored
        handle.write(b"\0")
    (layout.crl_dir / files[1]).rename(layout.crl_dir / "elsewhere.bin")  # test: file gone
    index["lists"].append({"file": "../../vault/secret.crl", "sha256": "0" * 64})
    index["lists"].append("not an entry")
    # The name before C7 (no hash of the file): W5-B was not released with it, none is read.
    unhashed = f"{root.fingerprint[:12]}-20261101T000000Z.crl"
    assert newest.path.name.startswith(unhashed.removesuffix(".crl") + "-")
    (layout.crl_dir / unhashed).write_bytes(newest.path.read_bytes())
    index["lists"].append({**day_1, "file": unhashed})
    index_path.write_text(json.dumps(index), encoding="utf-8")
    assert [crl.this_update.day for crl in load_stored(layout)] == [5, 1]
    assert "does not match its SHA-256" in caplog.text
    assert "without a valid file name" in caplog.text


def test_load_stored_reads_the_lists_again_only_after_a_change(
    server: Server, root: Ca, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The day checks of a retention run or a report ask once per day: the parsed lists are
    kept while the index and its files stay as they are."""
    _store(layout, server, root, 3, 4)
    parsed: list[bytes] = []
    real = crl_module.parse_crl

    def counting(data: bytes) -> Crl:
        parsed.append(data)
        return real(data)

    monkeypatch.setattr(crl_module, "parse_crl", counting)
    first = load_stored(layout)
    assert len(parsed) == 2
    assert load_stored(layout) == first
    assert len(parsed) == 2
    # A changed file is read again (and left out: it no longer matches the index).
    assert first[0].path is not None
    with first[0].path.open("ab") as handle:
        handle.write(b"\0")
    assert [crl.this_update.day for crl in load_stored(layout)] == [4]
    assert len(parsed) == 3
    # A new list: the index changed. The changed file is that fetch's failure too (C14).
    server.routes["/5.crl"] = Route(make_crl(root, datetime(2026, 10, 5, tzinfo=UTC)))
    run = fetch(options(server.url("/5.crl")), layout, [root.certificate], NOW)
    assert [(failure.url, failure.error) for failure in run.failures] == [
        (server.url("/3.crl"), "stored_file_changed")
    ]
    assert [crl.this_update.day for crl in load_stored(layout)] == [4, 5]


def test_each_stored_file_is_parsed_once_per_process(
    server: Server, root: Ca, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Performance guard: a stored list is parsed once per process (the name carries the file's
    hash; the stored lists only grow, Q4). A fetch that changes nothing but the index
    (``fetched_at``) parses only what it downloads; ``load_stored`` and the builder afterwards
    parse nothing."""
    days = list(range(1, 13))
    _store(layout, server, root, *days)
    parsed: list[bytes] = []
    real = crl_module.parse_crl

    def counting(data: bytes) -> Crl:
        parsed.append(data)
        return real(data)

    monkeypatch.setattr(crl_module, "parse_crl", counting)
    first = load_stored(layout)
    assert len(first) == len(parsed) == len(days)
    parsed.clear()
    urls = [server.url(f"/{day}.crl") for day in days]
    assert fetch(options(*urls), layout, [root.certificate], NOW + timedelta(days=1)).exit_code == 0
    assert len(parsed) == len(days)  # the downloads
    parsed.clear()
    assert load_stored(layout) == first
    assert parsed == []
    assert [crl.path for crl in builder_module._stored_crls(layout.crl_dir)] == [
        crl.path for crl in sorted(first, key=lambda crl: crl.file_name)
    ]
    assert parsed == []
    # The parsed list holds the file's bytes once: they are its DER.
    assert first[0].path is not None
    assert first[0].der == first[0].path.read_bytes()


def test_parse_crl_reads_the_entries_as_the_standalone_verifier_does(root: Ca) -> None:
    """An entry extension given twice, or a CRLReason value ``cryptography`` has no name for,
    leaves the list readable; a CRLReason that is not an ENUMERATED does not."""
    revoked_at = datetime(2026, 10, 1, tzinfo=UTC)
    der = make_crl(
        root,
        datetime(2026, 11, 1, tzinfo=UTC),
        [(7, revoked_at, x509.ReasonFlags.superseded), (8, revoked_at, None)],
    )
    listed = asn1_crl.CertificateList.load(der)

    def with_tbs(tbs: bytes) -> bytes:
        return bytes(
            asn1_crl.CertificateList(
                {
                    "tbs_cert_list": asn1_crl.TbsCertList.load(tbs),
                    "signature_algorithm": listed["signature_algorithm"],
                    "signature": listed["signature"].native,
                }
            ).dump()
        )

    tbs = listed["tbs_cert_list"].dump()
    superseded = bytes.fromhex("300a0603551d1504030a0104")
    unused = parse_crl(with_tbs(tbs.replace(superseded, superseded[:-1] + b"\x07")))
    assert (unused.revoked, unused.reasons) == ({7: revoked_at, 8: revoked_at}, {7: 7})
    doubled = asn1_crl.TbsCertList.load(tbs)
    doubled["revoked_certificates"][0]["crl_entry_extensions"].append(
        asn1_crl.CRLEntryExtension(
            {"extn_id": "crl_reason", "critical": False, "extn_value": "key_compromise"}
        )
    )
    twice = parse_crl(with_tbs(doubled.dump(force=True)))
    assert twice.reasons == {7: 1}  # the last one, as the standalone verifier reads it
    not_enumerated = superseded[:-3] + b"\x02\x01\x04"  # an INTEGER
    with pytest.raises(CrlError, match="CRLReason"):
        parse_crl(with_tbs(tbs.replace(superseded, not_enumerated)))
    with pytest.raises(CrlError):
        parse_crl(_with_version(der, 5))


def test_parse_crl_reads_der_and_pem_and_refuses_other_bytes(root: Ca) -> None:
    this_update = datetime(2026, 11, 1, tzinfo=UTC)
    der = make_crl(root, this_update)
    pem = make_crl(root, this_update, pem=True)
    assert parse_crl(der).der == parse_crl(pem).der == der
    assert parse_crl(pem).sha256 == hashlib.sha256(pem).hexdigest()
    assert parse_crl(der).next_update == this_update + timedelta(days=7)
    assert parse_crl(der).issuer == root.certificate.subject.rfc4514_string()
    for junk in (b"", b"\x30\x03\x02\x01\x01", b"-----BEGIN X509 CRL-----\nAAAA\n"):
        with pytest.raises(CrlError):
            parse_crl(junk)
    leaf = make_ca("Örnek TSA", root, ca=False)
    assert not parse_crl(der).signed_by(leaf.certificate)  # another name


def test_parse_crl_refuses_pem_data_without_exactly_one_list(
    server: Server, root: Ca, layout: Layout
) -> None:
    """The standalone verifier reads every ``X509 CRL`` block of a file: a bundle of two lists
    is refused here (``not_a_crl``), so both verifiers never see different revocations."""
    first = make_crl(root, datetime(2026, 11, 1, tzinfo=UTC), pem=True)
    second = make_crl(
        root, datetime(2026, 11, 2, tzinfo=UTC), [(77, NOW - timedelta(days=9), None)], pem=True
    )
    certificate = root.certificate.public_bytes(Encoding.PEM)
    for data in (first + second, certificate, certificate + b"\x30\x03\x02\x01\x01"):
        with pytest.raises(CrlError):
            parse_crl(data)
    # One list beside another kind of block, as the standalone verifier reads it.
    assert parse_crl(certificate + first).der == parse_crl(first).der
    server.routes["/bundle.crl"] = Route(first + second)
    run = fetch(options(server.url("/bundle.crl")), layout, [root.certificate], NOW)
    assert [failure.error for failure in run.failures] == ["not_a_crl"]


# -- the configuration adapter --------------------------------------------------------------------


def test_crl_options_reads_the_w5t_keys(tmp_path: Path) -> None:
    """Until W5-T's day-1 pull request lands, the keys are read with the contract's defaults;
    a configuration that carries them (the stub here) is read as it is."""
    config = Config.model_validate(
        {"data_dir": str(tmp_path), "secrets_dir": str(tmp_path), "tsa": {"timeout_seconds": 9}}
    )
    defaults = crl_options(config)
    assert defaults == CrlOptions(urls=(), timeout_seconds=9, https_proxy="", max_age_days=7)
    stub = SimpleNamespace(
        tsa=SimpleNamespace(
            crl_urls=["http://crl.example.com/a.crl"], crl_max_age_days=3, timeout_seconds=30
        ),
        network=SimpleNamespace(https_proxy="http://192.0.2.1:3128"),
    )
    assert crl_options(cast(Config, stub)) == CrlOptions(
        urls=("http://crl.example.com/a.crl",),
        timeout_seconds=30,
        https_proxy="http://192.0.2.1:3128",
        max_age_days=3,
    )


def test_fetch_all_uses_the_configuration(server: Server, root: Ca, layout: Layout) -> None:
    """The keys W5-T adds, on a configuration stub: the URLs and the proxy are used."""
    server.routes["/root.crl"] = Route(make_crl(root, datetime(2026, 11, 1, tzinfo=UTC)))
    proxy = Proxy()
    stub = SimpleNamespace(
        tsa=SimpleNamespace(
            crl_urls=[server.url("/root.crl")], crl_max_age_days=7, timeout_seconds=FETCH_SECONDS
        ),
        network=SimpleNamespace(https_proxy=proxy.url),
    )
    try:
        run = fetch_all(cast(Config, stub), layout, [root.certificate], NOW)
    finally:
        proxy.close()
    assert run.exit_code == 0
    assert proxy.used == [("GET", server.url("/root.crl"))]


def test_layout_folders(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    assert layout.crl_dir == tmp_path / "state" / "crl"
    assert layout.renewals_dir == tmp_path / "vault" / "renewals"


# -- the command line ------------------------------------------------------------------------------

CONFIG = """
data_dir = "{data}"
site_name = "Örnek A.Ş."

[tsa]
provider = "mock"
{roots}
"""


def _cli_config(tmp_path: Path, roots: Path | None) -> str:
    path = tmp_path / "arcilogger.toml"
    line = f'roots_dir = "{roots.as_posix()}"' if roots else ""
    path.write_text(
        CONFIG.format(data=(tmp_path / "data").as_posix(), roots=line), encoding="utf-8"
    )
    return str(path)


def test_cli_no_url_known_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = _cli_config(tmp_path, None)
    assert main(["--config", config, "crl", "fetch"]) == 2
    assert "Bilinen SİL adresi yok" in capsys.readouterr().out
    assert main(["--config", config, "crl", "fetch", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["urls"] == []
    # The run still leaves its index (#151): the CrlFetch task is seen to run.
    assert read_index(Layout(tmp_path / "data")) == {
        "schema": INDEX_SCHEMA,
        "lists": [],
        "failures": [],
    }
    # A held lock or an index that cannot be read fails the run as with addresses (answer to
    # Q9 on #158): exit 1 with the TR/EN text, the index left as it is.
    index = Layout(tmp_path / "data").crl_dir / "index.json"
    written = index.read_bytes()
    with crl_module._one_run(index.parent):
        assert main(["--config", config, "crl", "fetch"]) == 1
    assert "Hata [error]: another 'crl fetch' is running" in capsys.readouterr().err
    assert index.read_bytes() == written
    index.write_text("{not json", encoding="utf-8")
    assert main(["--config", config, "crl", "fetch"]) == 1
    error = capsys.readouterr().err
    assert error.startswith("Hata [error]: ")
    assert "cannot be read" in error
    assert index.read_text(encoding="utf-8") == "{not json"


def test_cli_fetch_and_status(
    server: Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gone = closed_port_url()
    pinned = make_ca("Örnek Kök SM (TEST)", cdp=[server.url("/root.crl"), gone])
    roots = tmp_path / "roots"
    roots.mkdir()
    (roots / "kok.pem").write_bytes(pinned.certificate.public_bytes(Encoding.PEM))
    data = make_crl(pinned, datetime(2026, 11, 1, tzinfo=UTC))
    server.routes["/root.crl"] = Route(data)
    config = _cli_config(tmp_path, roots)

    assert main(["--config", config, "crl", "fetch"]) == 1
    out = capsys.readouterr().out
    name = stored_name(pinned, "20261101T000000Z", data)
    assert f"SİL doğrulandı, saklandı [CRL verified, stored]: {server.url('/root.crl')}" in out
    assert name in out
    assert f"SİL alınamadı [CRL failed]: {gone} (unreachable)" in out

    assert main(["--config", config, "crl", "fetch", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert [failure["error"] for failure in result["failures"]] == ["unreachable"]

    assert main(["--config", config, "crl", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["files"] == 1
    assert status["bytes"] == len(data)
    assert status["size"] == f"{len(data)} B ({len(data)} B)"
    assert status["index"]["schema"] == INDEX_SCHEMA
    assert [entry["file"] for entry in status["index"]["lists"]] == [name]


def test_cli_fetch_all_good_exits_0(
    server: Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pinned = make_ca("Örnek Kök SM (TEST)", cdp=[server.url("/root.crl")])
    roots = tmp_path / "roots"
    roots.mkdir()
    (roots / "kok.pem").write_bytes(pinned.certificate.public_bytes(Encoding.PEM))
    server.routes["/root.crl"] = Route(make_crl(pinned, datetime(2026, 11, 1, tzinfo=UTC)))
    assert main(["--config", _cli_config(tmp_path, roots), "crl", "fetch", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_cli_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _cli_config(tmp_path, None)
    assert main(["--config", config, "crl", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status == {
        "index": {"schema": INDEX_SCHEMA, "lists": [], "failures": []},
        "files": 0,
        "bytes": 0,
        "size": "0 B (0 B)",
    }
    crl_dir = Layout(tmp_path / "data").crl_dir
    crl_dir.mkdir(parents=True)
    (crl_dir / "index.json").write_text("[]", encoding="utf-8")
    assert main(["--config", config, "crl", "status"]) == 1
    assert "Hata [error]" in capsys.readouterr().err
    monkeypatch.setattr("arcilogger_core.cli.crl_options", lambda _: options("http://192.0.2.1/x"))
    assert main(["--config", config, "crl", "fetch"]) == 1  # the index cannot be read
    assert "Hata [error]" in capsys.readouterr().err

    def bad_proxy(_: Config) -> CrlOptions:
        return CrlOptions(https_proxy="http://user:pw@192.0.2.1:3128")

    monkeypatch.setattr("arcilogger_core.cli.crl_options", bad_proxy)
    assert main(["--config", config, "crl", "fetch"]) == 2
    err = capsys.readouterr().err
    assert "Yapılandırma hatası [configuration error]" in err
    assert "pw" not in err
