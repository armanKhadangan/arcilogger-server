"""Revocation cases shared by the core verifier and the standalone verifier (Wave 5 rule 5, D10).

Each case is a set of revocation lists around one time stamp, written as offsets from the stamp's
genTime, and the code and level both verifiers must give for the TSA certificate. The lists are
signed with the key of the certificate's issuer unless a case says otherwise. A case is run
through :func:`core_verdict` (``verify_token(crls=)``) and :func:`standalone_verdict` (the
standalone verifier's ``check_revocation``, read as it is, never changed);
``test_revocation.py`` asserts that both give the case's code and level.

Interim (C6, contract W5-B-9): the core already ranks a revocation at or before the stamp time
above an earlier hold and orders two entries of one date without failing; the standalone verifier
follows when W5-A changes it (merge order W5-B, then W5-A). Until then a case where they differ
carries ``standalone_before_c6``: what the standalone verifier gives today (a code and level, or
``TypeError``). The test asserts exactly that, so it fails the moment W5-A changes the standalone
verifier. W5-A then removes the value from each case, and with the last one the field itself,
this paragraph and the interim branch of ``test_revocation.py``: both verifiers must agree on
every case.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from asn1crypto import crl as asn1_crl
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from arcilogger_core.crl import parse_crl
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.tsa.verify import verify_token

PrivateKey = ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey
HOUR, DAY = timedelta(hours=1), timedelta(days=1)


@dataclass(frozen=True)
class Entry:
    """A revocation entry: its date as an offset from genTime, and its CRLReason."""

    after: timedelta
    reason: x509.ReasonFlags | None = None
    #: The entry is for another certificate of the same issuer (serial + 1).
    other_serial: bool = False
    #: A CRLReason written as this raw value, one the ``cryptography`` library has no name for
    #: (7 is unused in RFC 5280, 11 is not defined); one such entry per list.
    raw_reason: int | None = None


@dataclass(frozen=True)
class CrlSpec:
    """A revocation list: its thisUpdate as an offset from genTime, its entries and its signer.

    ``signer``: ``issuer`` (the certificate's issuer, the only list that counts), ``other-key``
    (names the issuer but is signed by another key) or ``other-ca`` (another CA's own list).
    ``digest``: the signature's hash; the standalone verifier knows SHA-1 to SHA-512 only.
    """

    this_update: timedelta
    entries: tuple[Entry, ...] = ()
    signer: Literal["issuer", "other-key", "other-ca"] = "issuer"
    digest: Literal["sha256", "sha3-256"] = "sha256"


@dataclass(frozen=True)
class RevocationCase:
    name: str
    lists: tuple[CrlSpec, ...]
    code: str
    level: str
    #: Interim, for W5-A to remove (C6): the standalone verifier's ``(code, level)`` today, or
    #: the name of the exception it raises today, where it does not apply C6 yet. None: it gives
    #: ``code`` and ``level`` like the core.
    standalone_before_c6: tuple[str, str] | str | None = None


CASES: tuple[RevocationCase, ...] = (
    RevocationCase("no list", (), "crl-none", "info"),
    RevocationCase(
        "only another CA's list", (CrlSpec(DAY, signer="other-ca"),), "crl-none", "info"
    ),
    RevocationCase(
        "the issuer's name, another key",
        (CrlSpec(DAY, (Entry(-DAY),), signer="other-key"),),
        "crl-none",
        "info",
    ),
    RevocationCase("a list after the stamp, no entry", (CrlSpec(DAY),), "crl-ok", "ok"),
    RevocationCase("a list at the stamp time, no entry", (CrlSpec(timedelta(0)),), "crl-ok", "ok"),
    RevocationCase(
        "another certificate revoked",
        (CrlSpec(DAY, (Entry(-DAY, other_serial=True),)),),
        "crl-ok",
        "ok",
    ),
    RevocationCase(
        "only removeFromCRL",
        (CrlSpec(DAY, (Entry(-DAY, x509.ReasonFlags.remove_from_crl),)),),
        "crl-ok",
        "ok",
    ),
    RevocationCase("only a list before the stamp", (CrlSpec(-HOUR),), "crl-stale", "info"),
    RevocationCase(
        "the newest of two lists is before the stamp",
        (CrlSpec(-2 * DAY), CrlSpec(-HOUR)),
        "crl-stale",
        "info",
    ),
    RevocationCase(
        "an older list before the stamp, then a newer one after it",
        (CrlSpec(-HOUR), CrlSpec(DAY)),
        "crl-ok",
        "ok",
    ),
    RevocationCase(
        "a newer list after the stamp, then an older one before it",
        (CrlSpec(DAY), CrlSpec(-HOUR)),
        "crl-ok",
        "ok",
    ),
    RevocationCase(
        "a list signed with SHA3-256, which the standalone verifier does not know",
        (CrlSpec(DAY, (Entry(-DAY),), digest="sha3-256"),),
        "crl-none",
        "info",
    ),
    RevocationCase(
        "revoked before the stamp",
        (CrlSpec(DAY, (Entry(-DAY, x509.ReasonFlags.key_compromise),)),),
        "crl-revoked",
        "fail",
    ),
    RevocationCase(
        "revoked at the stamp time", (CrlSpec(DAY, (Entry(timedelta(0)),)),), "crl-revoked", "fail"
    ),
    RevocationCase(
        "revoked before the stamp, on a list from before it",
        (CrlSpec(-HOUR, (Entry(-2 * HOUR),)),),
        "crl-revoked",
        "fail",
    ),
    RevocationCase(
        "revoked on an older list, gone from the newer one",
        (CrlSpec(-HOUR, (Entry(-2 * HOUR),)), CrlSpec(30 * DAY)),
        "crl-revoked",
        "fail",
    ),
    RevocationCase(
        "revoked after the stamp",
        (CrlSpec(2 * DAY, (Entry(DAY, x509.ReasonFlags.superseded),)),),
        "crl-revoked-later",
        "warn",
    ),
    RevocationCase(
        "on hold",
        (CrlSpec(DAY, (Entry(-DAY, x509.ReasonFlags.certificate_hold),)),),
        "crl-hold",
        "warn",
    ),
    RevocationCase(
        "on hold, then revoked before the stamp: the revocation outranks the hold (C6)",
        (
            CrlSpec(-HOUR, (Entry(-3 * DAY, x509.ReasonFlags.certificate_hold),)),
            CrlSpec(DAY, (Entry(-2 * HOUR, x509.ReasonFlags.key_compromise),)),
        ),
        "crl-revoked",
        "fail",
        standalone_before_c6=("crl-hold", "warn"),
    ),
    RevocationCase(
        "on hold and revoked on the same date, on two lists (C6)",
        (
            CrlSpec(-HOUR, (Entry(-DAY, x509.ReasonFlags.certificate_hold),)),
            CrlSpec(DAY, (Entry(-DAY),)),
        ),
        "crl-revoked",
        "fail",
        # This case guards the order of two entries of one date (a missing reason first) and the
        # standalone TypeError (it sorts (date, None) against (date, 6)), not the C6 ranking:
        # the core gave crl-revoked here before C6 as well.
        standalone_before_c6="TypeError",
    ),
    RevocationCase(
        "on hold, then revoked at the stamp time (C6)",
        (
            CrlSpec(-HOUR, (Entry(-3 * DAY, x509.ReasonFlags.certificate_hold),)),
            CrlSpec(DAY, (Entry(timedelta(0), x509.ReasonFlags.key_compromise),)),
        ),
        "crl-revoked",
        "fail",
        standalone_before_c6=("crl-hold", "warn"),
    ),
    RevocationCase(
        # privilegeWithdrawn (9) sorts after certificateHold (6): the C6 ranking decides, not
        # the order of the entries.
        "on hold and revoked (privilegeWithdrawn) on the same date (C6)",
        (
            CrlSpec(-HOUR, (Entry(-DAY, x509.ReasonFlags.certificate_hold),)),
            CrlSpec(DAY, (Entry(-DAY, x509.ReasonFlags.privilege_withdrawn),)),
        ),
        "crl-revoked",
        "fail",
        standalone_before_c6=("crl-hold", "warn"),
    ),
    RevocationCase(
        "revoked on the same date on two lists, once without a reason, once for key compromise",
        (
            CrlSpec(-HOUR, (Entry(-DAY),)),
            CrlSpec(DAY, (Entry(-DAY, x509.ReasonFlags.key_compromise),)),
        ),
        "crl-revoked",
        "fail",
        # The same standalone TypeError, with no hold involved (W5-A: a missing reason sorts
        # first).
        standalone_before_c6="TypeError",
    ),
    RevocationCase(
        "the earliest entry decides: on hold, then revoked after the stamp",
        (
            CrlSpec(-HOUR, (Entry(-3 * DAY, x509.ReasonFlags.certificate_hold),)),
            CrlSpec(2 * DAY, (Entry(DAY, x509.ReasonFlags.key_compromise),)),
        ),
        "crl-hold",
        "warn",
    ),
    RevocationCase(
        "the earliest entry decides: revoked, later on hold",
        (
            CrlSpec(DAY, (Entry(-DAY),)),
            CrlSpec(3 * DAY, (Entry(2 * DAY, x509.ReasonFlags.certificate_hold),)),
        ),
        "crl-revoked",
        "fail",
    ),
    RevocationCase(
        "another certificate's entry has the unused CRLReason 7",
        (CrlSpec(DAY, (Entry(-DAY, other_serial=True, raw_reason=7), Entry(-DAY))),),
        "crl-revoked",
        "fail",
    ),
    RevocationCase(
        "the certificate's entry has the undefined CRLReason 11",
        (CrlSpec(DAY, (Entry(-DAY, raw_reason=11),)),),
        "crl-revoked",
        "fail",
    ),
    RevocationCase(
        "a list with a bad signature does not hide a good one",
        (CrlSpec(DAY, signer="other-key"), CrlSpec(2 * DAY, (Entry(-DAY),))),
        "crl-revoked",
        "fail",
    ),
)


#: The DER of a CRLReason entry extension with the value superseded (4), the placeholder that
#: ``raw_reason`` replaces.
_SUPERSEDED = bytes.fromhex("300a0603551d1504030a0104")
_DIGESTS: dict[str, hashes.SHA256 | hashes.SHA3_256] = {
    "sha256": hashes.SHA256(),
    "sha3-256": hashes.SHA3_256(),
}


def _key(like: PrivateKey) -> PrivateKey:
    if isinstance(like, rsa.RSAPrivateKey):
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return ec.generate_private_key(ec.SECP256R1())


def make_lists(
    specs: Sequence[CrlSpec],
    issuer: x509.Certificate,
    issuer_key: PrivateKey,
    certificate: x509.Certificate,
    moment: datetime,
) -> list[bytes]:
    """The DER lists of ``specs`` (a case's ``lists``) for ``certificate``, issued by
    ``issuer`` and stamped at ``moment``."""
    lists = []
    for spec in specs:
        name, key = issuer.subject, issuer_key
        if spec.signer == "other-key":
            key = _key(issuer_key)
        elif spec.signer == "other-ca":
            key = _key(issuer_key)
            name = x509.Name(
                [
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Örnek A.Ş. TEST"),
                    x509.NameAttribute(NameOID.COMMON_NAME, "Another CA (TEST)"),
                ]
            )
        this_update = moment + spec.this_update
        builder = (
            x509.CertificateRevocationListBuilder()
            .issuer_name(name)
            .last_update(this_update)
            .next_update(this_update + 7 * DAY)
        )
        for entry in spec.entries:
            serial = certificate.serial_number + (1 if entry.other_serial else 0)
            revoked = (
                x509.RevokedCertificateBuilder()
                .serial_number(serial)
                .revocation_date(moment + entry.after)
            )
            reason = x509.ReasonFlags.superseded if entry.raw_reason is not None else entry.reason
            if reason is not None:
                revoked = revoked.add_extension(x509.CRLReason(reason), critical=False)
            builder = builder.add_revoked_certificate(revoked.build())
        digest = _DIGESTS[spec.digest]
        der = builder.sign(key, digest).public_bytes(Encoding.DER)
        raw = [entry.raw_reason for entry in spec.entries if entry.raw_reason is not None]
        if raw:
            der = _with_raw_reason(der, raw[0], key, digest)
        lists.append(der)
    return lists


def _with_raw_reason(
    der: bytes, value: int, key: PrivateKey, digest: hashes.SHA256 | hashes.SHA3_256
) -> bytes:
    """``der`` with its one superseded CRLReason written as ``value``, signed again."""
    tbs = x509.load_der_x509_crl(der).tbs_certlist_bytes
    assert tbs.count(_SUPERSEDED) == 1
    tbs = tbs.replace(_SUPERSEDED, _SUPERSEDED[:-1] + bytes([value]))
    if isinstance(key, rsa.RSAPrivateKey):
        signature = key.sign(tbs, padding.PKCS1v15(), digest)
    else:
        signature = key.sign(tbs, ec.ECDSA(digest))
    signed = asn1_crl.CertificateList.load(der)
    return bytes(
        asn1_crl.CertificateList(
            {
                "tbs_cert_list": asn1_crl.TbsCertList.load(tbs),
                "signature_algorithm": signed["signature_algorithm"],
                "signature": signature,
            }
        ).dump()
    )


def core_verdict(
    token: bytes,
    data: bytes,
    roots: Sequence[x509.Certificate],
    lists: Sequence[bytes],
    now: datetime,
) -> tuple[str, str]:
    """``(code, level)`` of the core verifier (``details["crl"]``, ``details["crl_level"]``)."""
    result = verify_token(
        token,
        roots=roots,
        digest=hashlib.sha256(data).digest(),
        now=now,
        crls=[parse_crl(der) for der in lists],
    )
    assert result.ok, result.errors
    return result.details["crl"], result.details["crl_level"]


def standalone_verdict(
    token: bytes,
    data: bytes,
    roots: Sequence[x509.Certificate],
    lists: Sequence[bytes],
    now: datetime,
) -> list[tuple[str, str]]:
    """``(code, level)`` of each finding of the standalone verifier's ``check_revocation``."""
    result = sv.verify_timestamp(
        token,
        data,
        [sv.parse_certificate(root.public_bytes(Encoding.DER)) for root in roots],
        now=now,
    )
    assert result.ok, [error.code for error in result.errors]
    assert result.gen_time is not None
    findings = sv.check_revocation(
        result.chain, [sv.parse_crl(der) for der in lists], result.gen_time
    )
    return [(code, level) for level, code, _, _ in findings]
