"""Time-stamp providers and the independent verifier."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from asn1crypto import tsp
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from arcilogger_core.config import Config
from arcilogger_core.execpolicy import TrustAnchors
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import KAMUSM_CUSTOMER, KAMUSM_PASSWORD, SecretStore
from arcilogger_core.tsa import make_provider, trusted_roots
from arcilogger_core.tsa.base import (
    TsaConfigurationError,
    TsaError,
    TsaNoCreditError,
    TsaRejectedError,
    TsaResponseError,
    TsaUnavailableError,
)
from arcilogger_core.tsa.mock import MockFailure, MockTsa, MockTsaProvider, MockTsaServer
from arcilogger_core.tsa.rfc3161 import Rfc3161HttpProvider
from arcilogger_core.tsa.verify import _check_eku, inspect_token, load_roots_dir, verify_token
from arcilogger_core.tsa.zamane import ZamaneCliProvider

FAKE_JAVA = Path(__file__).parent / "fake_zamane" / "fake_java.py"


@pytest.fixture
def tsa(tmp_path: Path) -> MockTsa:
    return MockTsa(tmp_path / "mock-tsa", key_type="ec")


@pytest.fixture
def document(tmp_path: Path) -> Path:
    path = tmp_path / "manifest-2026-10-12.json"
    path.write_bytes(b'{"day":"2026-10-12","schema":"arcilogger.manifest/1"}')
    return path


def _verify(tsa: MockTsa, token: Path, file: Path) -> tuple[bool, tuple[str, ...]]:
    result = verify_token(token.read_bytes(), roots=[tsa.root_certificate], file_path=file)
    return result.ok, result.errors


# -- mock provider + verifier ---------------------------------------------------------------------


def test_stamp_and_verify(tsa: MockTsa, document: Path) -> None:
    result = MockTsaProvider(tsa).stamp(document)
    assert result.token_path == document.with_name(document.name + ".zd")
    assert result.info.container == "TimeStampResp"
    assert result.info.digest_alg == "sha256"
    assert "TEST ONLY" in result.info.tsa_name
    verified = verify_token(
        result.token_path.read_bytes(), roots=[tsa.root_certificate], file_path=document
    )
    assert verified.ok, verified.errors
    assert verified.info is not None
    assert verified.info.serial == result.info.serial
    assert "Mock Root" in verified.details["root"]


def test_rsa_tokens_verify_too(tmp_path: Path, document: Path) -> None:
    tsa = MockTsa(tmp_path / "rsa", key_type="rsa")
    token = MockTsaProvider(tsa).stamp(document).token_path
    assert _verify(tsa, token, document) == (True, ())


def test_bare_timestamp_token_is_accepted(tsa: MockTsa, document: Path) -> None:
    token = MockTsaProvider(tsa).stamp(document).token_path
    bare = tsp.TimeStampResp.load(token.read_bytes())["time_stamp_token"].dump()
    result = verify_token(bare, roots=[tsa.root_certificate], file_path=document)
    assert result.ok
    assert result.info is not None
    assert result.info.container == "TimeStampToken"


def test_changed_file_is_detected(tsa: MockTsa, document: Path) -> None:
    token = MockTsaProvider(tsa).stamp(document).token_path
    document.write_bytes(document.read_bytes() + b" ")
    ok, errors = _verify(tsa, token, document)
    assert not ok
    assert any("imprint" in error for error in errors)


def test_untrusted_root_is_rejected(tmp_path: Path, tsa: MockTsa, document: Path) -> None:
    token = MockTsaProvider(tsa).stamp(document).token_path
    other = MockTsa(tmp_path / "other", key_type="ec")
    result = verify_token(token.read_bytes(), roots=[other.root_certificate], file_path=document)
    assert not result.ok
    assert any("pinned root" in error for error in result.errors)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [(MockFailure.EXPIRED_CERT, "not valid at genTime"), (MockFailure.BAD_SIGNATURE, "signature")],
)
def test_bad_tokens_fail_verification(
    tsa: MockTsa, document: Path, failure: MockFailure, expected: str
) -> None:
    tsa.failure = failure
    token = MockTsaProvider(tsa).stamp(document).token_path
    ok, errors = _verify(tsa, token, document)
    assert not ok
    assert any(expected in error for error in errors)


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (MockFailure.WRONG_IMPRINT, TsaResponseError),
        (MockFailure.NO_CREDIT, TsaNoCreditError),
        (MockFailure.REJECTION, TsaRejectedError),
        (MockFailure.UNREACHABLE, TsaUnavailableError),
        (MockFailure.TIMEOUT, TsaUnavailableError),
    ],
)
def test_provider_errors(
    tsa: MockTsa, document: Path, failure: MockFailure, error: type[TsaError]
) -> None:
    tsa.failure = failure
    with pytest.raises(error):
        MockTsaProvider(tsa).stamp(document)
    assert not document.with_name(document.name + ".zd").exists()


def test_credit_is_counted_down(tsa: MockTsa, document: Path) -> None:
    provider = MockTsaProvider(tsa)
    tsa.credits = 2
    provider.stamp(document)
    assert provider.credits() == 1
    provider.stamp(document)
    with pytest.raises(TsaNoCreditError):
        provider.stamp(document)


def test_digest_argument_and_missing_inputs(tsa: MockTsa, document: Path) -> None:
    token = MockTsaProvider(tsa).stamp(document).token_path.read_bytes()
    assert not verify_token(token, roots=[tsa.root_certificate]).ok
    import hashlib

    digest = hashlib.sha256(document.read_bytes()).digest()
    assert verify_token(token, roots=[tsa.root_certificate], digest=digest).ok


def test_future_gen_time_is_rejected(tsa: MockTsa, document: Path) -> None:
    token = MockTsaProvider(tsa).stamp(document).token_path.read_bytes()
    past = datetime.now(UTC) - timedelta(days=1)
    result = verify_token(token, roots=[tsa.root_certificate], file_path=document, now=past)
    assert any("future" in error for error in result.errors)


def test_garbage_is_not_a_token() -> None:
    with pytest.raises(TsaResponseError):
        inspect_token(b"\x30\x03\x02\x01\x00")
    result = verify_token(b"garbage", roots=[])
    assert not result.ok


def test_eku_rules() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "x")])
    now = datetime.now(UTC)
    base = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
    )
    no_eku = base.sign(key, hashes.SHA256())
    soft = base.add_extension(
        x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=False
    ).sign(key, hashes.SHA256())
    assert _check_eku(no_eku) == (["TSA certificate has no extended key usage"], [])
    errors, warnings = _check_eku(soft)
    assert any("not critical" in error for error in errors)
    assert warnings == []
    # tsa.require_critical_eku = false (K6): the same certificate passes with a warning only
    errors, warnings = _check_eku(soft, require_critical=False)
    assert errors == []
    assert any("not critical" in warning for warning in warnings)
    assert _check_eku(no_eku, require_critical=False)[0]  # a missing purpose stays an error


@pytest.mark.skipif(shutil.which("openssl") is None, reason="openssl not installed")
def test_openssl_agrees(tsa: MockTsa, document: Path, tmp_path: Path) -> None:
    """Auditors can check our tokens with `openssl ts -verify` (independent implementation)."""
    token = MockTsaProvider(tsa).stamp(document).token_path
    root = tmp_path / "root.pem"
    root.write_bytes((tsa.state_dir / "root.pem").read_bytes())
    command = [
        "openssl",
        "ts",
        "-verify",
        "-in",
        str(token),
        "-data",
        str(document),
        "-CAfile",
        str(root),
    ]
    good = subprocess.run(command, capture_output=True, text=True, check=False)
    assert "Verification: OK" in good.stdout, good.stdout + good.stderr
    document.write_bytes(document.read_bytes() + b"x")
    bad = subprocess.run(command, capture_output=True, text=True, check=False)
    assert "Verification: OK" not in bad.stdout


def test_roots_directory_loading(tmp_path: Path, tsa: MockTsa) -> None:
    roots = tmp_path / "roots"
    roots.mkdir()
    (roots / "root.pem").write_bytes((tsa.state_dir / "root.pem").read_bytes())
    from cryptography.hazmat.primitives.serialization import Encoding

    (roots / "copy.cer").write_bytes(tsa.root_certificate.public_bytes(Encoding.DER))
    assert len(load_roots_dir(roots)) == 2


# -- generic RFC 3161 over HTTP --------------------------------------------------------------------


def test_http_provider_against_mock_server(tsa: MockTsa, document: Path) -> None:
    with MockTsaServer(tsa) as server:
        result = Rfc3161HttpProvider(server.url, timeout_seconds=5).stamp(document)
    assert _verify(tsa, result.token_path, document) == (True, ())


def test_server_drops_requests_without_the_timestamp_content_type(tsa: MockTsa) -> None:
    with MockTsaServer(tsa) as server:
        request = urllib.request.Request(
            server.url, data=b"x", method="POST", headers={"Content-Type": "text/plain"}
        )
        with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
            urllib.request.urlopen(request, timeout=5)


def test_http_timeout_and_unreachable(tsa: MockTsa, document: Path) -> None:
    with MockTsaServer(tsa, delay_seconds=2) as server:
        with pytest.raises(TsaUnavailableError):
            Rfc3161HttpProvider(server.url, timeout_seconds=0.5).stamp(document)
        url = server.url
    with pytest.raises(TsaUnavailableError):
        Rfc3161HttpProvider(url, timeout_seconds=2).stamp(document)
    with pytest.raises(ValueError, match="http"):
        Rfc3161HttpProvider("ftp://example")
    assert Rfc3161HttpProvider(url).credits() is None


# -- Zamane console client (fake java) -----------------------------------------------------------


@pytest.fixture
def zamane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ZamaneCliProvider:
    jar = tmp_path / "tss-client-console-3.1.33.jar"
    jar.write_bytes(b"PK fake")
    monkeypatch.setenv("FAKE_ZAMANE_STATE", str(tmp_path / "fake-state"))
    monkeypatch.setenv("FAKE_ZAMANE_MODE", "ok")
    return ZamaneCliProvider(
        java=[sys.executable, str(FAKE_JAVA)],
        jar=jar,
        url="http://tzd.kamusm.gov.tr",
        port=80,
        customer="123456",
        password="Gizli-Parola-1",
        legal_grade=False,
        timeout_seconds=20,
    )


def test_zamane_stamp_check_and_credit(
    zamane: ZamaneCliProvider, document: Path, tmp_path: Path
) -> None:
    result = zamane.stamp(document)
    assert result.token_path.is_file()
    assert "Gizli-Parola-1" not in result.raw_output
    assert "123456" not in result.raw_output
    assert "***" in result.raw_output
    root = MockTsa(tmp_path / "fake-state").root_certificate
    assert verify_token(result.token_path.read_bytes(), roots=[root], file_path=document).ok
    assert zamane.check(document, result.token_path)[0] is True
    assert zamane.credits() == 9634


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("no_credit", TsaNoCreditError),
        ("wrong_password", TsaConfigurationError),
        ("unreachable", TsaUnavailableError),
        ("silent_failure", TsaError),
        ("garbage_token", TsaResponseError),
    ],
)
def test_zamane_failures(
    zamane: ZamaneCliProvider,
    document: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    error: type[TsaError],
) -> None:
    monkeypatch.setenv("FAKE_ZAMANE_MODE", mode)
    with pytest.raises(error) as caught:
        zamane.stamp(document)
    assert "Gizli-Parola-1" not in str(caught.value)


def test_zamane_wrong_password_is_not_retried(
    zamane: ZamaneCliProvider, document: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ZAMANE_MODE", "wrong_password")
    with pytest.raises(TsaConfigurationError) as caught:
        zamane.stamp(document)
    assert caught.value.retryable is False


def test_zamane_unreadable_credit_fails_loudly(
    zamane: ZamaneCliProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ZAMANE_MODE", "credit_garbled")
    with pytest.raises(TsaResponseError, match="credit_regex"):
        zamane.credits()


def test_zamane_timeout(
    zamane: ZamaneCliProvider, document: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ZAMANE_MODE", "hang")
    zamane.timeout_seconds = 1
    with pytest.raises(TsaUnavailableError):
        zamane.stamp(document)


def test_zamane_missing_client_or_java(
    zamane: ZamaneCliProvider, document: Path, tmp_path: Path
) -> None:
    zamane.java = ("definitely-not-java-xyz",)
    with pytest.raises(TsaConfigurationError, match="Java"):
        zamane.stamp(document)
    zamane.jar = tmp_path / "missing.jar"
    with pytest.raises(TsaConfigurationError, match="not found"):
        zamane.stamp(document)
    with pytest.raises(TsaConfigurationError):
        ZamaneCliProvider(
            java=["java"],
            jar=zamane.jar,
            url="u",
            port=80,
            customer="c",
            password="p",
            legal_grade=False,
            digest="sha384",
        )


# -- factory -----------------------------------------------------------------------------------


def _config(tmp_path: Path, tsa_settings: Mapping[str, object]) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "tsa": dict(tsa_settings),
        }
    )


def _secrets(tmp_path: Path) -> SecretStore:
    SecretStore.create_key(tmp_path / "secret.key")
    return SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json")


def test_factory(tmp_path: Path) -> None:
    secrets = _secrets(tmp_path)
    mock_config = _config(tmp_path, {"provider": "mock"})
    layout = Layout(mock_config.data_dir)
    assert make_provider(mock_config, layout, secrets).name == "mock"
    assert len(trusted_roots(mock_config, layout)) == 1
    http = make_provider(
        _config(tmp_path, {"provider": "rfc3161", "url": "http://x", "port": 8080}), layout, secrets
    )
    assert isinstance(http, Rfc3161HttpProvider)
    assert http.url == "http://x:8080"
    # The installer's Zamane client (execpolicy: zamane\ and its record, ADR-0014).
    jar = tmp_path / "zamane" / "z.jar"
    jar.parent.mkdir()
    jar.write_bytes(b"PK\x03\x04 client")
    (jar.parent / "zamane-jar.sha256").write_text(
        f"{hashlib.sha256(jar.read_bytes()).hexdigest()}  z.jar\n", encoding="utf-8"
    )
    anchors = TrustAnchors.of(tmp_path / "arcilogger.toml")
    zamane_settings = {
        "provider": "zamane",
        "url": "http://zd.kamusm.gov.tr",
        "zamane_jar": str(jar),
    }
    with pytest.raises(TsaConfigurationError, match="customer"):
        make_provider(_config(tmp_path, zamane_settings), layout, secrets, anchors=anchors)
    secrets.set(KAMUSM_CUSTOMER, "123456")
    secrets.set(KAMUSM_PASSWORD, "pw-secret")
    provider = make_provider(_config(tmp_path, zamane_settings), layout, secrets, anchors=anchors)
    assert isinstance(provider, ZamaneCliProvider)
    assert provider.legal_grade is True
    with pytest.raises(TsaConfigurationError, match="zamane_jar"):
        make_provider(_config(tmp_path, {"provider": "zamane"}), layout, secrets)


def test_trusted_roots_needs_pinned_roots_for_real_providers(tmp_path: Path) -> None:
    config = _config(tmp_path, {"provider": "zamane", "roots_dir": str(tmp_path / "none")})
    with pytest.raises(TsaConfigurationError, match="root"):
        trusted_roots(config, Layout(config.data_dir))
