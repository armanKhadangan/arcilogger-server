"""Files uploaded through the browser: Kamu SM roots, SMTP CA files and Zamane clients
(ADR-0014; contract 2.6 tsa and smtp, Day 0 note D0-16)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from arcilogger_core.admin import roots, zamane
from arcilogger_core.admin.roots import inspect_ca_bundle, inspect_root, installed_roots
from arcilogger_core.admin.zamane import client_bytes, inspect_client
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa import zamane_releases
from arcilogger_core.tsa.zamane_releases import ZamaneRelease

NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)


def certificate(
    key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey | None = None,
    *,
    ca: bool | None = True,
    days: int = 3650,
    start: datetime = NOW - timedelta(days=1),
    name: str = "Örnek Test Kök",
) -> x509.Certificate:
    key = key or ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=days))
    )
    if ca is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=None), True)
    return builder.sign(key, hashes.SHA256())


def pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(Encoding.PEM)


# -- roots -----------------------------------------------------------------------------------------


def test_a_root_in_pem_or_der_has_its_der_fingerprint() -> None:
    cert = certificate()
    der = cert.public_bytes(Encoding.DER)
    for data in (pem(cert), der):
        found = inspect_root(data, now=NOW)
        assert found.ok, found.reason
        assert found.certificate is not None
        assert found.certificate.sha256 == hashlib.sha256(der).hexdigest()
        assert found.certificate.key == "EC P-256"
        assert found.certificate.subject == "CN=Örnek Test Kök"
        assert found.pem == pem(cert)
        assert found.warnings == ()


def test_an_rsa_root_needs_2048_bits() -> None:
    strong = inspect_root(pem(certificate(rsa.generate_private_key(65537, 2048))), now=NOW)
    assert strong.ok
    assert strong.certificate is not None
    assert strong.certificate.key == "RSA 2048"
    weak_key = rsa.generate_private_key(65537, 1024)  # noqa: S505 - the key being refused
    weak = inspect_root(pem(certificate(weak_key)), now=NOW)
    assert (weak.ok, weak.code) == (False, "root_certificate_invalid")
    assert weak.reason is not None
    assert "RSA 1024" in weak.reason


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda: pem(certificate(ca=False)), "not a CA"),
        (lambda: pem(certificate(ca=None)), "not a CA"),
        (lambda: pem(certificate(ec.generate_private_key(ec.SECP521R1()))), "key EC P-521"),
        (lambda: pem(certificate()) + pem(certificate()), "2 certificates"),
        (lambda: b"-----BEGIN CERTIFICATE-----\nbm90\n-----END CERTIFICATE-----\n", "not a PEM"),
        (lambda: b"\x30\x03\x02\x01\x00", "not a PEM or DER"),
        (lambda: b"x" * (64 * 1024 + 1), "larger than"),
    ],
)
def test_what_is_not_a_root(make: object, reason: str) -> None:
    found = inspect_root(make(), now=NOW)  # type: ignore[operator]
    assert (found.ok, found.code) == (False, "root_certificate_invalid")
    assert found.reason is not None
    assert reason in found.reason
    assert found.certificate is None


def test_a_root_issued_by_another_key_is_refused() -> None:
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Örnek Test Kök")])
    key, other = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    forged = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)  # claims to be self-issued, signed by another key
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(NOW - timedelta(days=1))
        .not_valid_after(NOW + timedelta(days=10))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(other, hashes.SHA256())
    )
    found = inspect_root(pem(forged), now=NOW)
    assert not found.ok
    assert found.reason == "not self-issued and self-signed"


def test_an_expired_root_is_accepted_with_a_warning() -> None:
    old = certificate(start=NOW - timedelta(days=4000), days=3650)
    found = inspect_root(pem(old), now=NOW)
    assert found.ok
    assert found.warnings == (roots.ROOT_EXPIRED,)


def test_installed_roots_tell_the_browsers_from_the_installers(tmp_path: Path) -> None:
    web, installer = certificate(name="Web"), certificate(name="Kurulum")
    web_sha = hashlib.sha256(web.public_bytes(Encoding.DER)).hexdigest()
    (tmp_path / roots.root_file_name(web_sha)).write_bytes(pem(web))
    (tmp_path / "kok.crt").write_bytes(installer.public_bytes(Encoding.DER))
    (tmp_path / "bozuk.pem").write_bytes(b"not a certificate")
    found = {item.subject: item for item in installed_roots(tmp_path)}
    assert set(found) == {"CN=Web", "CN=Kurulum"}
    assert found["CN=Web"].source == "web"
    assert found["CN=Kurulum"].source == "installer"
    assert found["CN=Kurulum"].file == "kok.crt"
    assert installed_roots(None) == []
    assert installed_roots(tmp_path / "missing") == []


# -- SMTP CA files ---------------------------------------------------------------------------------


def test_an_smtp_ca_file_holds_one_to_ten_certificates() -> None:
    bundle = b"".join(pem(certificate(name=f"CA {n}")) for n in range(3))
    found = inspect_ca_bundle(bundle)
    assert found.ok
    assert found.sha256 == hashlib.sha256(bundle).hexdigest()
    assert [c.subject for c in found.certificates] == ["CN=CA 0", "CN=CA 1", "CN=CA 2"]
    eleven = b"".join(pem(certificate()) for _ in range(11))
    assert inspect_ca_bundle(eleven).reason == "11 certificates, 1-10 are allowed"
    der = certificate().public_bytes(Encoding.DER)
    assert inspect_ca_bundle(der).reason == "not a PEM file"
    assert inspect_ca_bundle(b"-----BEGIN" + b"x" * (256 * 1024)).code == "root_certificate_invalid"


# -- Zamane clients --------------------------------------------------------------------------------


def jar(content: bytes = b"\xca\xfe\xba\xbe") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        archive.writestr("ornek/Client.class", content)
    return buffer.getvalue()


def archive(*members: tuple[str, bytes], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as handle:
        for name, data in members:
            handle.writestr(name, data)
    return buffer.getvalue()


def written(tmp_path: Path, data: bytes, name: str = "upload.bin") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def test_a_bare_jar_is_its_own_client(tmp_path: Path) -> None:
    data = jar()
    found = inspect_client(written(tmp_path, data))
    assert found.ok
    assert (found.kind, found.jar_name, found.release) == ("jar", None, None)
    assert found.sha256 == found.jar_sha256 == hashlib.sha256(data).hexdigest()
    assert not found.known
    assert client_bytes(written(tmp_path, data)) == data


def test_a_distribution_zip_gives_its_one_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = jar(b"release")
    data = archive(("BENIOKU.txt", b"x"), ("lib/tss-client-console-3.1.33.jar", client))
    by_archive = {hashlib.sha256(client).hexdigest(): ZamaneRelease("3.1.33")}
    monkeypatch.setattr(zamane_releases, "KNOWN_RELEASES", by_archive)
    found = inspect_client(written(tmp_path, data))
    assert found.ok
    assert (found.kind, found.jar_name, found.release) == (
        "zip",
        "tss-client-console-3.1.33.jar",
        "3.1.33",
    )
    assert found.sha256 == hashlib.sha256(data).hexdigest()
    assert found.jar_sha256 == hashlib.sha256(client).hexdigest()
    assert client_bytes(written(tmp_path, data)) == client
    # A release may also be known by the SHA-256 of the archive Kamu SM distributes.
    release = ZamaneRelease("3.1.34", archive_sha256=hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(zamane_releases, "KNOWN_RELEASES", {"f" * 64: release})
    assert inspect_client(written(tmp_path, data)).release == "3.1.34"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"not a zip at all", "File is not a zip file"),
        (archive(("README.txt", b"x")), "0 tss-client-console-*.jar files"),
        (
            archive(("a/tss-client-console-1.jar", jar()), ("b/tss-client-console-2.jar", jar())),
            "2 tss-client-console-*.jar files",
        ),
        (archive(("../tss-client-console-1.jar", jar())), "absolute path or '..'"),
        (archive(("/abs/tss-client-console-1.jar", jar())), "absolute path or '..'"),
        (archive(("C:/x/tss-client-console-1.jar", jar())), "absolute path or '..'"),
        (archive(("tss-client-console-1.jar", b"no manifest")), "the client is not a jar"),
        (archive(("tss-client-console-1.jar", archive(("x", b"y")))), "has no META-INF"),
        (archive(*[(f"f{n}.txt", b"x") for n in range(101)]), "101 entries"),
        (
            archive(("tss-client-console-1.jar", b"\x00" * (2 * 1024 * 1024))),
            "compressed more than 20 times",
        ),
    ],
)
def test_what_is_not_a_client(tmp_path: Path, data: bytes, reason: str) -> None:
    found = inspect_client(written(tmp_path, data))
    assert (found.ok, found.code) == (False, zamane.ARCHIVE_INVALID)
    assert found.reason is not None
    assert reason in found.reason
    assert found.sha256 == hashlib.sha256(data).hexdigest()  # computed before parsing
    with pytest.raises(ValueError):  # noqa: PT011 - the reason varies
        client_bytes(written(tmp_path, data))


def test_a_client_larger_than_the_upload_limit_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "big.jar"
    with path.open("wb") as handle:
        handle.truncate(25 * 1024 * 1024 + 1)
    found = inspect_client(path)
    assert not found.ok
    assert found.reason == f"larger than {25 * 1024 * 1024} bytes"
