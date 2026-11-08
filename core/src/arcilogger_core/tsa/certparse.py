"""Certificates as the time-stamp providers issue them.

TÜRKTRUST's root "TÜRKTRUST Elektronik Sertifika Hizmet Sağlayıcısı H7" (2020-2030), to which
its time-stamp certificate chains and which every TÜRKTRUST token carries, writes its two ECDSA
signature algorithm identifiers with an explicit NULL parameter. RFC 5758 section 3.2 says the
parameters of ecdsa-with-SHA* MUST be absent, and the ``cryptography`` package refuses such a
certificate, and with it every token that carries it. OpenSSL and ARCILOGGER's standalone
verifier (``arcilogger_core.evidence.standalone``) accept it.

:func:`load_der` therefore retries a refused certificate once with exactly that departure
removed: the NULL parameter of the two ECDSA AlgorithmIdentifiers (``TBSCertificate.signature``
and ``Certificate.signatureAlgorithm``), nothing else. The re-encoded certificate keeps the
subject, issuer, key and extensions, so the certificates it issued verify against it, but its
bytes differ from the provider's file. ARCILOGGER therefore keeps the original encoding:
:func:`original_der` returns it, :func:`fingerprint` (the SHA-256 a site compares with the
provider's published value, and ``tsa.legal_root_fingerprints``) is computed over it,
:func:`to_pem` writes it, and :func:`is_self_signed` checks the root's own signature over its
original TBSCertificate.
"""

from __future__ import annotations

import base64
import hashlib
import re
import ssl

from asn1crypto import algos, core
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding

#: ecdsa-with-SHA1 and ecdsa-with-SHA224/256/384/512 (RFC 3279, RFC 5758): parameters absent.
ECDSA_SIGNATURES: dict[str, hashes.HashAlgorithm] = {
    "1.2.840.10045.4.1": hashes.SHA1(),  # noqa: S303 - only to read an old root's own signature
    "1.2.840.10045.4.3.1": hashes.SHA224(),
    "1.2.840.10045.4.3.2": hashes.SHA256(),
    "1.2.840.10045.4.3.3": hashes.SHA384(),
    "1.2.840.10045.4.3.4": hashes.SHA512(),
}

_PEM_BLOCK = re.compile(rb"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", re.DOTALL)

#: The provider's encoding of each certificate :func:`load_der` re-encoded, by the new DER.
_ORIGINALS: dict[bytes, bytes] = {}


def load_der(der: bytes) -> x509.Certificate:
    """A DER certificate; one whose only fault is an explicit NULL in its ECDSA signature
    algorithm identifiers is read without it (see the module text). Raises ``ValueError`` as
    ``cryptography`` does for anything else."""
    try:
        return x509.load_der_x509_certificate(der)
    except ValueError:
        normalized = _without_ecdsa_null(der)
        if normalized is None:
            raise
    certificate = x509.load_der_x509_certificate(normalized)
    _ORIGINALS[certificate.public_bytes(Encoding.DER)] = der
    return certificate


def load_certificates(data: bytes) -> list[x509.Certificate]:
    """The certificates of a PEM file (every ``CERTIFICATE`` block) or of one DER file."""
    if b"-----BEGIN CERTIFICATE-----" not in data:
        return [load_der(data)]
    blocks = _PEM_BLOCK.findall(data)
    if not blocks:
        raise ValueError("no complete PEM certificate")
    try:
        ders = [base64.b64decode(re.sub(rb"\s+", b"", block), validate=True) for block in blocks]
    except ValueError as exc:  # binascii.Error
        raise ValueError(f"bad PEM base64: {exc}") from exc
    return [load_der(der) for der in ders]


def original_der(certificate: x509.Certificate) -> bytes:
    """The certificate as its issuer encoded it (the file a site compares and installs)."""
    der = certificate.public_bytes(Encoding.DER)
    return _ORIGINALS.get(der, der)


def fingerprint(certificate: x509.Certificate) -> str:
    """SHA-256 fingerprint of a certificate: lower-case hex of its original DER encoding."""
    return hashlib.sha256(original_der(certificate)).hexdigest()


def to_pem(certificate: x509.Certificate) -> bytes:
    """The original encoding as PEM."""
    return ssl.DER_cert_to_PEM_cert(original_der(certificate)).encode("ascii")


def is_self_signed(certificate: x509.Certificate) -> bool:
    """Self-issued, and its own key verifies its signature over its original TBSCertificate."""
    if certificate.issuer != certificate.subject:
        return False
    der = original_der(certificate)
    if der == certificate.public_bytes(Encoding.DER):
        try:
            certificate.verify_directly_issued_by(certificate)
        except (ValueError, TypeError, InvalidSignature):
            return False
        return True
    parsed = asn1_x509.Certificate.load(der)
    digest = ECDSA_SIGNATURES.get(parsed["signature_algorithm"]["algorithm"].dotted)
    key = certificate.public_key()
    if digest is None or not isinstance(key, ec.EllipticCurvePublicKey):
        return False
    try:
        key.verify(
            parsed["signature_value"].native,
            parsed["tbs_certificate"].dump(),
            ec.ECDSA(digest),
        )
    except InvalidSignature:
        return False
    return True


def _without_ecdsa_null(der: bytes) -> bytes | None:
    """``der`` with the NULL parameter of its ECDSA signature algorithm identifiers removed,
    or None when it has none (or is not a certificate at all)."""
    try:
        parsed = asn1_x509.Certificate.load(der, strict=True)
        tbs = parsed["tbs_certificate"]
        inner, outer = tbs["signature"], parsed["signature_algorithm"]
        if not (_ecdsa_with_null(inner) or _ecdsa_with_null(outer)):
            return None
        tbs["signature"] = _plain(inner)
        # The TBSCertificate is encoded again from its own parts (only the changed one is new),
        # then the certificate from the new TBSCertificate and its untouched signature value.
        parsed["tbs_certificate"] = asn1_x509.TbsCertificate.load(tbs.dump())
        parsed["signature_algorithm"] = _plain(outer)
        return bytes(parsed.dump())
    except (ValueError, TypeError, KeyError):
        return None


#: An encoded ASN.1 NULL (asn1crypto keeps an unexpected parameter as ``Any``).
_NULL = core.Null().dump()


def _ecdsa_with_null(algorithm: algos.SignedDigestAlgorithm) -> bool:
    parameters = algorithm["parameters"]
    return (
        algorithm["algorithm"].dotted in ECDSA_SIGNATURES
        and not isinstance(parameters, core.Void)
        and parameters.dump() == _NULL
    )


def _plain(algorithm: algos.SignedDigestAlgorithm) -> algos.SignedDigestAlgorithm:
    if not _ecdsa_with_null(algorithm):
        return algorithm
    return algos.SignedDigestAlgorithm({"algorithm": algorithm["algorithm"].dotted})
