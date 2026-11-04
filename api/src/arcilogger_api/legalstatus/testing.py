"""Stored revocation lists for the tests of the legal-validity status and the day page.

The lists are issued by the mock TSA's root (the issuer of its TSA certificate) and stored as
W5-B's ``crl fetch`` stores them (Wave 5 contract 2.2): the file under its stored name and its
entry in ``state/crl/index.json``, so that :func:`arcilogger_core.crl.load_stored` accepts them.
:func:`simulated_ca` gives a mock TSA a made-up CA's root instead of ARCILOGGER's test root, for
the stamps of a real provider (whose issuers need lists, W5-E-5). Test data only: the URL is an
RFC 5737 address.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    load_pem_private_key,
)
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from arcilogger_core.crl import INDEX_NAME, INDEX_SCHEMA, parse_crl
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site
from arcilogger_core.tsa.mock import MockTsa
from arcilogger_core.tsa.verify import fingerprint

#: Where the test lists say they were fetched from.
CRL_URL = "http://192.0.2.10/crl/root.crl"
#: A made-up CA a real provider's stamps chain to: not ARCILOGGER's test organization, and a
#: policy outside the test arc 2.999, under the documentation arc 1.3.6.1.4.1.32473 (RFC 5612).
CA_ORGANIZATION = "Örnek Simulated CA"
CA_POLICY = "1.3.6.1.4.1.32473.1.1"


def _ca_name(common_name: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, CA_ORGANIZATION),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def simulated_ca(state_dir: Path) -> None:
    """Write a mock TSA's state folder (the files :class:`MockTsa` keeps) whose root and TSA
    certificates are a made-up CA's (:data:`CA_ORGANIZATION`): a ``MockTsa(state_dir)`` then
    stamps as a real provider does, its chain outside ARCILOGGER's test root, and
    :func:`root_list` issues that CA's lists. Set the mock TSA's ``policy`` to
    :data:`CA_POLICY` for a policy outside the test arc too. ``state_dir`` must not hold a
    mock TSA yet (one that does keeps its own certificates)."""
    if (state_dir / "root.pem").exists():
        raise FileExistsError(f"{state_dir} already holds a mock TSA")
    state_dir.mkdir(parents=True, exist_ok=True)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = _ca_name("Örnek Simulated Root")
    root = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2070, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(root_key, hashes.SHA256())
    )
    files: dict[str, tuple[x509.Certificate, ec.EllipticCurvePrivateKey]] = {
        "root": (root, root_key)
    }
    for file, start, end in (
        ("tsa", datetime(2026, 1, 1, tzinfo=UTC), datetime(2031, 1, 1, tzinfo=UTC)),
        ("tsa-expired", datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 12, 1, tzinfo=UTC)),
    ):
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(_ca_name(f"Örnek Simulated TSA {file}"))
            .issuer_name(root_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(start)
            .not_valid_after(end)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True
            )
            .sign(root_key, hashes.SHA256())
        )
        files[file] = (certificate, key)
    for file, (certificate, key) in files.items():
        (state_dir / f"{file}.pem").write_bytes(certificate.public_bytes(Encoding.PEM))
        (state_dir / f"{file}.key").write_bytes(
            key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        )
    (state_dir / "credits.json").write_text(json.dumps({"credits": 10000}), encoding="utf-8")


def root_list(
    tsa: MockTsa,
    this_update: datetime,
    *,
    revoked_at: datetime | None = None,
    hold: bool = False,
    valid_days: int = 7,
) -> bytes:
    """A DER list of the mock TSA's root issued at ``this_update`` with its next update
    ``valid_days`` later: its TSA certificate revoked (on hold with ``hold``) at ``revoked_at``,
    or no entry."""
    key = load_pem_private_key((tsa.state_dir / "root.key").read_bytes(), None)
    if not isinstance(key, ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey):
        raise TypeError(f"the mock root key is a {type(key).__name__}")
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(tsa.root_certificate.subject)
        .last_update(this_update)
        .next_update(this_update + timedelta(days=valid_days))
    )
    if revoked_at is not None:
        entry = (
            x509.RevokedCertificateBuilder()
            .serial_number(tsa.tsa_certificate.serial_number)
            .revocation_date(revoked_at)
        )
        if hold:
            entry = entry.add_extension(
                x509.CRLReason(x509.ReasonFlags.certificate_hold), critical=False
            )
        builder = builder.add_revoked_certificate(entry.build())
    return builder.sign(key, hashes.SHA256()).public_bytes(Encoding.DER)


def store_lists(
    layout: Layout,
    lists: Sequence[tuple[MockTsa, bytes]],
    *,
    fetched_at: datetime,
    failures: Sequence[dict[str, str]] = (),
) -> list[str]:
    """Store each ``(tsa, list)`` (a list of that mock TSA's root) and write the index with
    them and ``failures``, as one ``crl fetch`` run leaves it; their stored names."""
    layout.crl_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, str | None]] = []
    names: list[str] = []
    for tsa, der in lists:
        crl = replace(parse_crl(der), issuer_sha256=fingerprint(tsa.root_certificate))
        (layout.crl_dir / crl.file_name).write_bytes(der)
        names.append(crl.file_name)
        entries.append(
            {
                "file": crl.file_name,
                "issuer": crl.issuer,
                "issuer_sha256": crl.issuer_sha256,
                "this_update": crl.this_update.isoformat(),
                "next_update": crl.next_update.isoformat() if crl.next_update else None,
                "sha256": crl.sha256,
                "url": CRL_URL,
                "fetched_at": isoformat_site(fetched_at, "seconds"),
            }
        )
    index = {"schema": INDEX_SCHEMA, "lists": entries, "failures": list(failures)}
    (layout.crl_dir / INDEX_NAME).write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return names
