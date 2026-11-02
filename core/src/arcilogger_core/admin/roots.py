"""Root certificates added through the browser (ADR-0014; contract 2.6 tsa and smtp).

:func:`inspect_root` checks an uploaded Kamu SM root before anything trusts it: PEM or DER, at
most 64 KiB, exactly one X.509 v3 certificate that is a CA (BasicConstraints), self-issued and
self-signed, with an RSA key of at least 2048 bits or an EC key on P-256 or P-384. An expired root
is still accepted with the warning ``root_expired`` (the days stamped while it was valid need it).
Its identity is the SHA-256 of its DER encoding: it is installed as ``kamusm-roots\\<sha256>.pem``
and that is the fingerprint ``tsa.legal_root_fingerprints`` lists.

:func:`inspect_ca_bundle` checks a company CA file for the SMTP connection (PEM, at most 256 KiB,
1-10 certificates); it is stored as ``smtp-ca\\<sha256>.pem`` (the SHA-256 of the file).

The API shows the result before a request is made (``UploadPreview``); the agent checks the copy
it claimed again. Nothing here installs a file.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core.admin.contract import UPLOAD_LIMITS

ROOT_INVALID: Final = "root_certificate_invalid"
ROOT_EXPIRED: Final = "root_expired"
MIN_RSA_BITS: Final = 2048
EC_CURVES: Final = frozenset({"secp256r1", "secp384r1"})
CA_BUNDLE_MAX: Final = 10
_PEM_MARK: Final = b"-----BEGIN"


@dataclass(frozen=True)
class CertificateInfo:
    #: SHA-256 of the DER encoding (the fingerprint).
    sha256: str
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    is_ca: bool
    self_signed: bool
    #: ``RSA 4096``, ``EC P-256`` …
    key: str

    def expired(self, now: datetime) -> bool:
        return now > self.not_after


@dataclass(frozen=True)
class RootInspection:
    """An uploaded root: ``ok`` with the certificate, or ``code``/``reason`` why not."""

    ok: bool
    certificate: CertificateInfo | None = None
    #: The certificate as PEM, the form it is installed in.
    pem: bytes | None = None
    code: str | None = None
    reason: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaBundleInspection:
    ok: bool
    sha256: str
    certificates: tuple[CertificateInfo, ...] = ()
    code: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class InstalledRoot:
    """A root in ``kamusm-roots\\``: placed by an administrator or the installer, or added
    through the browser (named ``<sha256>.pem``)."""

    sha256: str
    subject: str
    not_after: datetime
    file: str
    source: Literal["installer", "web"]


def _key_text(certificate: x509.Certificate) -> str:
    key = certificate.public_key()
    if isinstance(key, rsa.RSAPublicKey):
        return f"RSA {key.key_size}"
    if isinstance(key, ec.EllipticCurvePublicKey):
        names = {"secp256r1": "P-256", "secp384r1": "P-384", "secp521r1": "P-521"}
        return f"EC {names.get(key.curve.name, key.curve.name)}"
    return type(key).__name__


def _info(certificate: x509.Certificate) -> CertificateInfo:
    try:
        constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
        is_ca = constraints.ca
    except x509.ExtensionNotFound:
        is_ca = False
    self_signed = certificate.issuer == certificate.subject
    if self_signed:
        try:
            certificate.verify_directly_issued_by(certificate)
        except (ValueError, TypeError, InvalidSignature):
            self_signed = False
    return CertificateInfo(
        sha256=hashlib.sha256(certificate.public_bytes(Encoding.DER)).hexdigest(),
        subject=certificate.subject.rfc4514_string(),
        issuer=certificate.issuer.rfc4514_string(),
        not_before=certificate.not_valid_before_utc,
        not_after=certificate.not_valid_after_utc,
        is_ca=is_ca,
        self_signed=self_signed,
        key=_key_text(certificate),
    )


def _strong_key(certificate: x509.Certificate) -> bool:
    key = certificate.public_key()
    if isinstance(key, rsa.RSAPublicKey):
        return key.key_size >= MIN_RSA_BITS
    if isinstance(key, ec.EllipticCurvePublicKey):
        return key.curve.name in EC_CURVES
    return False


def _certificates(data: bytes) -> list[x509.Certificate]:
    if _PEM_MARK in data:
        return x509.load_pem_x509_certificates(data)
    return [x509.load_der_x509_certificate(data)]


def inspect_root(data: bytes, *, now: datetime | None = None) -> RootInspection:
    """Whether ``data`` is a root certificate the browser may install (see the module text)."""
    now = now or datetime.now(UTC)

    def refuse(reason: str) -> RootInspection:
        return RootInspection(ok=False, code=ROOT_INVALID, reason=reason)

    if len(data) > UPLOAD_LIMITS["kamusm_root"]:
        return refuse(f"larger than {UPLOAD_LIMITS['kamusm_root']} bytes")
    try:
        certificates = _certificates(data)
    except ValueError as exc:
        return refuse(f"not a PEM or DER certificate: {exc}")
    if len(certificates) != 1:
        return refuse(f"{len(certificates)} certificates, exactly one is needed")
    certificate = certificates[0]
    if certificate.version != x509.Version.v3:
        return refuse("not an X.509 v3 certificate")
    try:
        info = _info(certificate)
    except ValueError as exc:  # a malformed extension
        return refuse(f"unreadable certificate: {exc}")
    if not info.is_ca:
        return refuse("not a CA certificate (BasicConstraints CA is not true)")
    if not info.self_signed:
        return refuse("not self-issued and self-signed")
    if not _strong_key(certificate):
        return refuse(f"key {info.key}: RSA of at least {MIN_RSA_BITS} bits or EC P-256/P-384")
    warnings = (ROOT_EXPIRED,) if info.expired(now) else ()
    return RootInspection(
        ok=True,
        certificate=info,
        pem=certificate.public_bytes(Encoding.PEM),
        warnings=warnings,
    )


def inspect_ca_bundle(data: bytes) -> CaBundleInspection:
    """Whether ``data`` is a CA file for the SMTP connection (PEM, 1-10 certificates)."""
    digest = hashlib.sha256(data).hexdigest()

    def refuse(reason: str) -> CaBundleInspection:
        return CaBundleInspection(ok=False, sha256=digest, code=ROOT_INVALID, reason=reason)

    if len(data) > UPLOAD_LIMITS["smtp_ca"]:
        return refuse(f"larger than {UPLOAD_LIMITS['smtp_ca']} bytes")
    if _PEM_MARK not in data:
        return refuse("not a PEM file")
    try:
        certificates = x509.load_pem_x509_certificates(data)
        infos = tuple(_info(certificate) for certificate in certificates)
    except ValueError as exc:
        return refuse(f"not a PEM certificate file: {exc}")
    if not 1 <= len(infos) <= CA_BUNDLE_MAX:
        return refuse(f"{len(infos)} certificates, 1-{CA_BUNDLE_MAX} are allowed")
    return CaBundleInspection(ok=True, sha256=digest, certificates=infos)


def root_file_name(sha256: str) -> str:
    """The name a root added through the browser is installed under."""
    return f"{sha256}.pem"


def installed_roots(directory: Path | None) -> list[InstalledRoot]:
    """The roots in ``directory`` (``tsa.roots_dir``), as ``tsa.verify.load_roots_dir`` finds
    them; unreadable files are left out (the verifier refuses them the same way)."""
    if directory is None or not directory.is_dir():
        return []
    found: dict[str, InstalledRoot] = {}
    patterns: Sequence[str] = ("*.pem", "*.crt", "*.cer", "*.der")
    for path in sorted({p for pattern in patterns for p in directory.glob(pattern)}):
        try:
            certificates = _certificates(path.read_bytes())
        except (OSError, ValueError):
            continue
        for certificate in certificates:
            try:
                info = _info(certificate)
            except ValueError:
                continue
            source: Literal["installer", "web"] = (
                "web" if path.name == root_file_name(info.sha256) else "installer"
            )
            found.setdefault(
                info.sha256,
                InstalledRoot(info.sha256, info.subject, info.not_after, path.name, source),
            )
    return list(found.values())
