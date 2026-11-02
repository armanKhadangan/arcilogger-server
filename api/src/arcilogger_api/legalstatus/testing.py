"""Stored revocation lists for the tests of the legal-validity status and the day page.

The lists are issued by the mock TSA's root (the issuer of its TSA certificate) and stored as
W5-B's ``crl fetch`` stores them (Wave 5 contract 2.2): the file under its stored name and its
entry in ``state/crl/index.json``, so that :func:`arcilogger_core.crl.load_stored` accepts them.
Test data only: the URL is an RFC 5737 address.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.serialization import Encoding, load_pem_private_key

from arcilogger_core.crl import INDEX_NAME, INDEX_SCHEMA, parse_crl
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site
from arcilogger_core.tsa.mock import MockTsa
from arcilogger_core.tsa.verify import fingerprint

#: Where the test lists say they were fetched from.
CRL_URL = "http://192.0.2.10/crl/root.crl"


def root_list(
    tsa: MockTsa,
    this_update: datetime,
    *,
    revoked_at: datetime | None = None,
    hold: bool = False,
) -> bytes:
    """A DER list of the mock TSA's root issued at ``this_update``: its TSA certificate revoked
    (on hold with ``hold``) at ``revoked_at``, or no entry."""
    key = load_pem_private_key((tsa.state_dir / "root.key").read_bytes(), None)
    if not isinstance(key, ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey):
        raise TypeError(f"the mock root key is a {type(key).__name__}")
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(tsa.root_certificate.subject)
        .last_update(this_update)
        .next_update(this_update + timedelta(days=7))
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
