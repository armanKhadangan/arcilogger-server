"""TÜRKTRUST provider (contract §2.4, §3 W5-T): RFC 3161 over HTTPS with HTTP Basic
authentication, against a local HTTPS server whose test CA is trusted only in the test
(``SSL_CERT_FILE``). No test calls the real TÜRKTRUST server."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import logging
import select
import socket
import ssl
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import IPv4Address
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from pydantic import ValidationError

from arcilogger_core import cli
from arcilogger_core.admin.contract import FALLBACK_POLICY
from arcilogger_core.config import TURKTRUST_PRODUCTION_HOST, Config, TsaConfig
from arcilogger_core.credit import check_credit
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import TURKTRUST_PASSWORD, TURKTRUST_USERNAME, SecretStore
from arcilogger_core.tsa import make_provider, url_with_port
from arcilogger_core.tsa.base import (
    TsaConfigurationError,
    TsaError,
    TsaNoCreditError,
    TsaUnavailableError,
)
from arcilogger_core.tsa.mock import MockFailure, MockTsa, MockTsaProvider
from arcilogger_core.tsa.preflight import seal_preflight
from arcilogger_core.tsa.turktrust import (
    TURKTRUST_LEGAL_GRADE,
    TurktrustHttpsProvider,
    basic_authorization,
)
from arcilogger_core.tsa.verify import confirmed_legal_grade, fingerprint, verify_token

USERNAME = "ornek-as"
#: Non-ASCII on purpose (RFC 7617 with UTF-8), and long enough to be found in any text.
PASSWORD = "Gizli-Parola-ğüşİ-7f3a9c"
NOW = datetime.now(UTC)


# -- a local HTTPS server in front of the mock TSA ------------------------------------------------


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _tls_files(folder: Path) -> tuple[Path, ssl.SSLContext]:
    """A test CA (written to ``ca.pem``) and a server certificate for ``localhost`` and
    127.0.0.1; the server context."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("Örnek TLS CA (TEST)"))
        .issuer_name(_name("Örnek TLS CA (TEST)"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=30))
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
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(IPv4Address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (folder / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (folder / "host.pem").write_bytes(host.public_bytes(serialization.Encoding.PEM))
    (folder / "host.key").write_bytes(
        host_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(folder / "host.pem", folder / "host.key")
    return folder / "ca.pem", context


@dataclass
class TsaServer:
    """The mock TSA behind HTTP(S) and HTTP Basic authentication. ``status`` other than 200 is
    answered instead of a stamp; every request's ``Authorization`` header is recorded."""

    tsa: MockTsa
    tls: ssl.SSLContext | None = None
    status: int = 200
    authorizations: list[str | None] = field(default_factory=list)

    def __post_init__(self) -> None:
        outer = self
        expected = basic_authorization(USERNAME, PASSWORD)

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                outer.authorizations.append(self.headers.get("Authorization"))
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if self.headers.get("Authorization") != expected:
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="TSA"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                answer = outer.tsa.respond(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/timestamp-reply")
                self.send_header("Content-Length", str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if self.tls is not None:
            self._server.socket = self.tls.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        scheme = "https" if self.tls is not None else "http"
        return f"{scheme}://localhost:{self._server.server_address[1]}/"

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
            def do_POST(self) -> None:
                outer.used.append(("POST", self.path))
                target = urlsplit(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                upstream = http.client.HTTPConnection(target.netloc, timeout=5)
                headers = {k: v for k, v in self.headers.items() if k.lower() != "host"}
                upstream.request("POST", target.path or "/", body=body, headers=headers)
                answer = upstream.getresponse()
                data = answer.read()
                self.send_response(answer.status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
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
def tsa(tmp_path: Path) -> MockTsa:
    return MockTsa(tmp_path / "mock-tsa")


@pytest.fixture
def secure(tsa: MockTsa, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TsaServer]:
    """The HTTPS server; its CA is trusted through ``SSL_CERT_FILE`` in this test only."""
    ca, context = _tls_files(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    server = TsaServer(tsa, tls=context)
    yield server
    server.close()


@pytest.fixture
def plain(tsa: MockTsa) -> Iterator[TsaServer]:
    server = TsaServer(tsa)
    yield server
    server.close()


def _provider(url: str, **kwargs: Any) -> TurktrustHttpsProvider:
    options: dict[str, Any] = {
        "username": USERNAME,
        "password": PASSWORD,
        "legal_grade": False,
        "timeout_seconds": 10,
    }
    options.update(kwargs)
    return TurktrustHttpsProvider(url, **options)


def _day(tmp_path: Path) -> Path:
    path = tmp_path / "2026-11-04.log.xz"
    path.write_bytes("örnek ham kayıtlar\n".encode() * 100)
    return path


def _no_password(*texts: str) -> None:
    encoded = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    for text in texts:
        assert PASSWORD not in text
        assert encoded not in text


# -- stamping -------------------------------------------------------------------------------------


@pytest.mark.parametrize("digest", ["sha256", "sha512"])
def test_a_stamp_over_https_with_basic_authentication(
    secure: TsaServer, tmp_path: Path, digest: str
) -> None:
    day = _day(tmp_path)
    result = _provider(secure.url, digest=digest).stamp(day)
    assert result.token_path == day.with_name(day.name + ".zd")
    assert result.info.digest_alg == digest
    expected = hashlib.new(digest, day.read_bytes()).hexdigest()
    assert result.info.message_imprint_hex == expected
    # RFC 7617 with UTF-8: user name and password, base64 of their UTF-8 bytes.
    pair = f"{USERNAME}:{PASSWORD}".encode()
    assert secure.authorizations == ["Basic " + base64.b64encode(pair).decode("ascii")]
    _no_password(result.raw_output, repr(_provider(secure.url)))


@pytest.mark.parametrize("status", [401, 403])
def test_refused_credentials_are_a_configuration_error(
    secure: TsaServer, tmp_path: Path, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    """401: the server refuses the password; 403: it refuses the account."""
    caplog.set_level(logging.DEBUG)
    if status == 403:
        secure.status = 403
    provider = _provider(secure.url, password="yanlış-parola" if status == 401 else PASSWORD)
    with pytest.raises(TsaConfigurationError) as raised:
        provider.stamp(_day(tmp_path))
    assert raised.value.retryable is False
    assert "credentials" in str(raised.value)
    assert f"HTTP {status}" in str(raised.value)
    _no_password(str(raised.value), repr(raised.value), caplog.text)


@pytest.mark.parametrize("status", [500, 502, 503, 408, 429])
def test_a_server_error_is_retried(
    secure: TsaServer, tmp_path: Path, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    secure.status = status
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TsaUnavailableError) as raised:
        _provider(secure.url).stamp(_day(tmp_path))
    assert raised.value.retryable is True
    assert f"HTTP {status}" in str(raised.value)
    _no_password(str(raised.value), caplog.text)


def test_a_network_error_is_retried(tmp_path: Path) -> None:
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    with pytest.raises(TsaUnavailableError) as raised:
        _provider(f"https://127.0.0.1:{port}/").stamp(_day(tmp_path))
    assert raised.value.retryable is True
    _no_password(str(raised.value))


def test_a_credit_word_rejection_is_no_credit(
    secure: TsaServer, tsa: MockTsa, tmp_path: Path
) -> None:
    """T2: TÜRKTRUST's answer for an empty balance is unknown; a rejection whose status text
    holds a credit word is "no credit"."""
    tsa.failure = MockFailure.NO_CREDIT
    with pytest.raises(TsaNoCreditError, match="credit") as raised:
        _provider(secure.url).stamp(_day(tmp_path))
    _no_password(str(raised.value))


def test_the_server_certificate_is_verified(
    secure: TsaServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the test CA in the trust store the TLS handshake fails: there is no way around
    the verification."""
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    with pytest.raises(TsaUnavailableError, match=r"CERTIFICATE_VERIFY_FAILED|certificate"):
        _provider(secure.url).stamp(_day(tmp_path))
    assert secure.authorizations == []  # nothing was sent before the certificate was checked


def test_https_through_the_proxy(secure: TsaServer, tmp_path: Path) -> None:
    proxy = Proxy()
    try:
        _provider(secure.url, https_proxy=proxy.url).stamp(_day(tmp_path))
    finally:
        proxy.close()
    assert proxy.used == [("CONNECT", f"localhost:{urlsplit(secure.url).port}")]
    assert len(secure.authorizations) == 1


def test_without_the_setting_no_proxy_is_used(
    secure: TsaServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The environment's proxy variables are ignored: network.https_proxy alone decides (D13)."""
    proxy = Proxy()
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.setenv(name, proxy.url)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    try:
        _provider(secure.url).stamp(_day(tmp_path))
    finally:
        proxy.close()
    assert proxy.used == []
    assert len(secure.authorizations) == 1


def test_plain_http_needs_the_setting(plain: TsaServer, tmp_path: Path) -> None:
    with pytest.raises(TsaConfigurationError, match="https"):
        _provider(plain.url)
    result = _provider(plain.url, allow_plain_http=True).stamp(_day(tmp_path))
    assert result.token_path.exists()
    assert plain.authorizations == [basic_authorization(USERNAME, PASSWORD)]


def test_plain_http_through_the_proxy(plain: TsaServer, tmp_path: Path) -> None:
    proxy = Proxy()
    try:
        _provider(plain.url, allow_plain_http=True, https_proxy=proxy.url).stamp(_day(tmp_path))
    finally:
        proxy.close()
    assert proxy.used == [("POST", plain.url)]


def test_a_redirect_is_not_followed(secure: TsaServer, tmp_path: Path) -> None:
    """The request and its credentials go only to the configured URL; the 3xx is a configuration
    error (W5-T-2)."""
    secure.status = 307
    with pytest.raises(TsaConfigurationError, match="HTTP 307"):
        _provider(secure.url).stamp(_day(tmp_path))
    assert len(secure.authorizations) == 1


@pytest.mark.parametrize("status", [301, 302, 308, 400, 404, 405, 415])
def test_another_refusal_is_a_configuration_error(
    secure: TsaServer, tmp_path: Path, status: int, caplog: pytest.LogCaptureFixture
) -> None:
    """W5-T-2 (#204 Q4): a 3xx, or a 4xx other than 401/403/408/429, most likely means a wrong
    URL or path (T1). It is not retried, and its text has the status, never the credentials."""
    secure.status = status
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TsaConfigurationError) as raised:
        _provider(secure.url).stamp(_day(tmp_path))
    assert raised.value.retryable is False
    assert f"HTTP {status}" in str(raised.value)
    assert "[tsa] url" in str(raised.value)
    assert "credentials" not in str(raised.value)
    _no_password(str(raised.value), repr(raised.value), caplog.text)


def test_no_credit_query() -> None:
    assert _provider("https://zd.turktrust.com.tr/").credits() is None


def test_a_colon_in_the_user_name_is_refused() -> None:
    """RFC 7617: the user name of Basic authentication cannot contain a colon."""
    with pytest.raises(TsaConfigurationError, match="':'"):
        _provider("https://zd.turktrust.com.tr/", username="ornek:as")


# -- the configuration and the legal-grade claim --------------------------------------------------


def test_turktrust_legal_grade_is_the_owners_decision() -> None:
    """Pinned: TÜRKTRUST production stamps claim legal grade by the product owner's decision of
    2026-11-02 (contract D12, owner question Q1). Changing it must be deliberate."""
    assert TURKTRUST_LEGAL_GRADE is True


@pytest.mark.parametrize(
    ("url", "legal"),
    [
        ("https://zd.turktrust.com.tr/", True),
        ("https://zd.turktrust.com.tr:443/tsa", True),
        ("https://tzd.turktrust.com.tr/", False),
        ("https://zd.turktrust.com.tr.example.test/", False),
        ("https://203.0.113.10/", False),
    ],
)
def test_only_turktrust_production_claims_legal_grade(url: str, legal: bool) -> None:
    assert TURKTRUST_PRODUCTION_HOST == "zd.turktrust.com.tr"
    assert TsaConfig(provider="turktrust", url=url, port=443).legal_grade is legal
    # The same host with another provider never claims it.
    assert TsaConfig(provider="rfc3161", url=url, port=443).legal_grade is False


def test_plain_http_is_refused_by_the_configuration_unless_allowed() -> None:
    url = "http://zd.turktrust.com.tr/"
    with pytest.raises(ValidationError, match="clear text"):
        TsaConfig(provider="turktrust", url=url, port=80)
    allowed = TsaConfig(provider="turktrust", url=url, port=80, allow_plain_http=True)
    assert allowed.legal_grade is True  # the claim follows the host, the warning is separate
    # The other providers keep their http:// URLs.
    assert TsaConfig(provider="rfc3161", url="http://203.0.113.10/").url.startswith("http://")


def test_a_turktrust_day_is_legal_grade_only_with_its_root_listed(
    tsa: MockTsa, tmp_path: Path
) -> None:
    """Rule 4 is unchanged: the claim, a verified token and a listed root."""
    day = _day(tmp_path)
    token = MockTsaProvider(tsa).stamp(day).token_path.read_bytes()
    claim = TsaConfig(provider="turktrust", url="https://zd.turktrust.com.tr/").legal_grade
    result = verify_token(token, roots=[tsa.root_certificate], file_path=day)
    assert result.ok, result.errors
    root = fingerprint(tsa.root_certificate)
    assert result.details["root_sha256"] == root
    assert confirmed_legal_grade(claim, result, [root]) is True
    assert confirmed_legal_grade(claim, result, []) is False
    assert confirmed_legal_grade(False, result, [root]) is False


# -- make_provider --------------------------------------------------------------------------------


def _config(tmp_path: Path, **tsa: Any) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "tsa": {"provider": "turktrust", "url": "https://zd.turktrust.com.tr/", **tsa},
            "network": {"https_proxy": "http://172.20.10.5:3128"},
        }
    )


def _store(tmp_path: Path) -> SecretStore:
    SecretStore.create_key(tmp_path / "secret.key")
    return SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json")


def test_make_provider_needs_both_secrets(tmp_path: Path) -> None:
    secrets = _store(tmp_path)
    config = _config(tmp_path, port=443)
    layout = Layout(tmp_path / "data")
    with pytest.raises(TsaConfigurationError, match="not stored"):
        make_provider(config, layout, secrets)
    secrets.set(TURKTRUST_USERNAME, USERNAME)
    with pytest.raises(TsaConfigurationError, match="not stored"):
        make_provider(config, layout, secrets)
    secrets.set(TURKTRUST_PASSWORD, PASSWORD)
    provider = make_provider(config, layout, secrets)
    assert isinstance(provider, TurktrustHttpsProvider)
    assert provider.name == "turktrust"
    assert provider.legal_grade is True
    assert provider.url == "https://zd.turktrust.com.tr/"
    assert provider.https_proxy == "http://172.20.10.5:3128"
    _no_password(repr(provider))


def test_make_provider_takes_the_port(tmp_path: Path) -> None:
    secrets = _store(tmp_path)
    secrets.set(TURKTRUST_USERNAME, USERNAME)
    secrets.set(TURKTRUST_PASSWORD, PASSWORD)
    provider = make_provider(_config(tmp_path, port=8443), Layout(tmp_path / "data"), secrets)
    assert isinstance(provider, TurktrustHttpsProvider)
    assert provider.url == "https://zd.turktrust.com.tr:8443/"


@pytest.mark.parametrize(
    ("url", "port", "expected"),
    [
        ("https://zd.turktrust.com.tr/", 443, "https://zd.turktrust.com.tr/"),
        ("http://zd.turktrust.com.tr/", 80, "http://zd.turktrust.com.tr/"),
        ("https://zd.turktrust.com.tr/tsa", 8443, "https://zd.turktrust.com.tr:8443/tsa"),
        ("https://zd.turktrust.com.tr:9443/", 443, "https://zd.turktrust.com.tr:9443/"),
        # tsa.port defaults to 80: an https URL without a port keeps 443.
        ("https://zd.turktrust.com.tr/", 80, "https://zd.turktrust.com.tr/"),
    ],
)
def test_url_with_port(url: str, port: int, expected: str) -> None:
    assert url_with_port(url, port) == expected


def test_errors_are_tsa_errors() -> None:
    assert issubclass(TsaConfigurationError, TsaError)
    assert issubclass(TsaUnavailableError, TsaError)


def test_the_seal_preflight_needs_the_turktrust_account(tmp_path: Path) -> None:
    """Without both secrets the Seal task could not build the provider: ``credentials``."""
    config = _config(tmp_path, port=443)
    anchors = TrustAnchors(tmp_path, FALLBACK_POLICY)
    secrets = _store(tmp_path)
    assert seal_preflight(config, anchors=anchors, secrets=secrets, stored=())[0] == "credentials"
    half = frozenset({TURKTRUST_USERNAME})
    assert seal_preflight(config, anchors=anchors, secrets=secrets, stored=half)[0] == "credentials"
    account = frozenset({TURKTRUST_USERNAME, TURKTRUST_PASSWORD})
    # The account is enough: no Zamane client or Java is asked for (only the roots remain).
    assert seal_preflight(config, anchors=anchors, secrets=secrets, stored=account) == ["roots"]


def test_the_seal_task_names_a_missing_account_credentials(tmp_path: Path) -> None:
    """#204 Q3: the Seal task's cause for a missing TÜRKTRUST account is ``credentials``, as the
    preflight says; with the account stored, a refused provider stays ``provider``."""
    secrets = _store(tmp_path)
    app = cast(cli.App, SimpleNamespace(config=_config(tmp_path, port=443), secrets=secrets))
    assert cli._provider_cause(app) == "credentials"
    secrets.set(TURKTRUST_USERNAME, USERNAME)
    assert cli._provider_cause(app) == "credentials"
    secrets.set(TURKTRUST_PASSWORD, PASSWORD)
    assert cli._provider_cause(app) == "provider"


def test_the_credit_task_records_no_credit_query(tmp_path: Path) -> None:
    """§2.4, C21: TÜRKTRUST has no credit query. The Credit task logs ``supported: false`` and
    never raises ``R20-credit``."""
    layout = Layout(tmp_path / "data")
    provider = _provider("https://zd.turktrust.com.tr/")
    assert check_credit(provider, layout, 500) is None
    (entry,) = [json.loads(line) for line in layout.credit_log.read_text("utf-8").splitlines()]
    assert entry["provider"] == "turktrust"
    assert entry["credits"] is None
    assert entry["supported"] is False
    alerts = list(layout.alerts.rglob("*")) if layout.alerts.exists() else []
    assert not any("R20-credit" in path.read_text("utf-8") for path in alerts if path.is_file())
