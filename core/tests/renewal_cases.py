"""Renewal cases shared by the core verifier and the standalone verifier (Wave 5 rule 5, §2.1.4).

Each case builds a data directory around one sealed day (:data:`~.renewal_world.DAY`, its TSA
certificate expiring at :data:`~.renewal_world.CNA`): renewal records stamped at chosen times,
perhaps revocation lists of the legal CA, and the time ``now`` both verifiers judge it at. The
case names the code and level both must give the day. It is run through :func:`core_verdict`
(``chain.verify_day(..., renewals=True)``, with the stored lists in ``state/crl``) and
:func:`standalone_verdict` (``verify_package`` on a copy of the vault, the lists in ``sil/``),
called as they are: the table never patches either. ``test_renewal_verifiers.py`` asserts that
both give the case's code, level and texts.

The core decides "legal grade" with ``tsa.legal_root_fingerprints`` (rule 4), which a package
does not carry (contract W5-A-5): every case lists the legal CA's root there, so a case whose
root the site has not listed (it counts in the standalone verifier only) is not in the table.

The deletion-record cases (:data:`DELETION_CASES`, #156 item 1) are shared by the evidence
builder and the standalone verifier, and run in ``test_renewal_evidence.py``: the core judges no
deletion record's token once a manifest seals it (its renewal status reads only the token's
hashes and certificate).
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from arcilogger_core.canonical import canonical_json
from arcilogger_core.chain import verify_day
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.renewal import RecordEntry, protects, renew, renewal_status
from arcilogger_core.seal import sha256_file
from arcilogger_core.tsa.client import token_path_for
from arcilogger_core.tsa.verify import inspect_token

from .renewal_world import CNA, DAY, RenewalWorld, day_token_path
from .test_evidence import in_process
from .test_legal_grade import SIMULATED_POLICY, ProductionLikeProvider
from .test_revocation import _store

FAR = CNA + timedelta(days=1825)
#: The second sealed day of a world, for a renewal that lists another token.
OTHER = DAY + timedelta(days=1)
#: The level of each code (§2.1.4).
LEVELS = {
    "renewal-not-due": "info",
    "renewal-covered": "ok",
    "renewal-due": "warn",
    "renewal-late": "warn",
    "renewal-uncovered": "warn",
}


@dataclass(frozen=True)
class Built:
    """What a case built: the time to verify at, the legal CA's revocation lists (DER) and
    whether the auditor's roots (and the core's pinned roots) include the legal CA's root."""

    now: datetime
    lists: tuple[bytes, ...] = ()
    legal_root_pinned: bool = True


@dataclass(frozen=True)
class RenewalCase:
    name: str
    build: Callable[[RenewalWorld], Built]
    code: str
    level: str
    #: The site relaxed the EKU rule before sealing the day (``tsa.require_critical_eku =
    #: false``, K6): the world is made so (``make_world``), and the day's manifest says so.
    relaxed: bool = False


@dataclass(frozen=True)
class Verdict:
    """The day's finding: its code, level and TR/EN texts."""

    code: str
    level: str
    message_tr: str
    message_en: str


# -- building blocks -------------------------------------------------------------------------------


def renew_day(
    rw: RenewalWorld,
    name: str,
    at: datetime,
    *,
    expires: datetime = FAR,
    not_before: datetime | None = None,
    eku_critical: bool = True,
    relaxed: bool | None = None,
) -> str:
    """A legal renewal of the day stamped at ``at`` by a new certificate of the legal CA valid
    until ``expires`` (its EKU critical unless ``eku_critical`` is false); its record says the
    site's EKU rule, or the one ``relaxed`` names. Its record path."""
    rw.set_now(at)
    provider = rw.legal_provider(expires, not_before=not_before, eku_critical=eku_critical)
    rw.write_renewal(name, [rw.day_entry()], provider, relaxed=relaxed)
    return f"renewals/{name}.json"


def entry_of(rw: RenewalWorld, record: str) -> RecordEntry:
    """The entry a newer record lists for the renewal ``record`` (its own token, D2)."""
    path = rw.layout.vault / record
    token = token_path_for(path)
    return RecordEntry(
        "renewal",
        f"{record}.zd",
        sha256_file(token),
        inspect_token(token.read_bytes()).tsa_cert_not_after or FAR,
        record_sha256=sha256_file(path),
        protects=tuple(protects(json.loads(path.read_bytes()))),
    )


#: The chain of three: R1 covers the day, R2 renews R1's token and R3 R2's, each 100 days before
#: the certificate it renews expires.
R1_EXPIRES = CNA + timedelta(days=400)
R2_EXPIRES = R1_EXPIRES + timedelta(days=400)
CHAIN_NOW = R2_EXPIRES - timedelta(days=50)


def three_deep(rw: RenewalWorld, *, links: int = 3) -> list[str]:
    """The first ``links`` records of the chain of three; their paths."""
    records = [renew_day(rw, "2031-06-23-1", CNA - timedelta(days=100), expires=R1_EXPIRES)]
    if links > 1:
        rw.set_now(R1_EXPIRES - timedelta(days=100))
        provider = rw.legal_provider(R2_EXPIRES)
        rw.write_renewal("2032-09-27-1", [entry_of(rw, records[0])], provider)
        records.append("renewals/2032-09-27-1.json")
    if links > 2:
        rw.set_now(R2_EXPIRES - timedelta(days=100))
        provider = rw.legal_provider(FAR + timedelta(days=3650))
        rw.write_renewal("2033-10-31-1", [entry_of(rw, records[1])], provider)
        records.append("renewals/2033-10-31-1.json")
    return records


def crl_of(
    rw: RenewalWorld, certificate: x509.Certificate, at: datetime, *, hold: bool = False
) -> bytes:
    """A list of the legal CA naming ``certificate`` at ``at``: revoked, or on hold; DER."""
    entry = x509.RevokedCertificateBuilder().serial_number(certificate.serial_number)
    entry = entry.revocation_date(at)
    if hold:
        entry = entry.add_extension(x509.CRLReason(x509.ReasonFlags.certificate_hold), False)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(rw.legal.root.subject)
        .last_update(at + timedelta(days=1))
        .next_update(at + timedelta(days=8))
        .add_revoked_certificate(entry.build())
    )
    return builder.sign(rw.legal.key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


def listed_renewal(rw: RenewalWorld, offset: timedelta, *, hold: bool = False) -> Built:
    """A renewal 100 days before expiry whose certificate a list names ``offset`` from its
    stamp; judged ten days later."""
    at = CNA - timedelta(days=100)
    renew_day(rw, "2031-06-23-1", at, not_before=at - timedelta(days=30))
    assert rw.legal_tsa.signer is not None
    listed = crl_of(rw, rw.legal_tsa.signer[0], at + offset, hold=hold)
    return Built(at + timedelta(days=10), (listed,))


# -- the cases -------------------------------------------------------------------------------------


def _not_due(rw: RenewalWorld) -> Built:
    return Built(CNA - timedelta(days=181))


def _due_at_the_window(rw: RenewalWorld) -> Built:
    return Built(CNA - timedelta(days=180))


def _due(rw: RenewalWorld) -> Built:
    return Built(CNA - timedelta(days=90))


def _uncovered(rw: RenewalWorld) -> Built:
    return Built(CNA + timedelta(days=1))


def _uncovered_at_not_after(rw: RenewalWorld) -> Built:
    return Built(CNA)


def _covered(rw: RenewalWorld) -> Built:
    renew_day(rw, "2031-06-23-1", CNA - timedelta(days=100))
    return Built(CNA - timedelta(days=90))


def _covered_after_expiry(rw: RenewalWorld) -> Built:
    renew_day(rw, "2031-06-23-1", CNA - timedelta(days=100))
    return Built(CNA + timedelta(days=30))


def _covered_by_renew(rw: RenewalWorld) -> Built:
    at = CNA - timedelta(days=100)
    rw.set_now(at)
    renew(rw.layout, rw.world.config, rw.legal_provider(FAR), rw.roots, at)
    return Built(at + timedelta(days=10))


def _late(rw: RenewalWorld) -> Built:
    renew_day(rw, "2031-10-11-1", CNA + timedelta(days=10))
    return Built(CNA + timedelta(days=20))


def _late_at_not_after(rw: RenewalWorld) -> Built:
    renew_day(rw, "2031-10-01-1", CNA)
    return Built(CNA + timedelta(days=1))


def _earliest_decides(rw: RenewalWorld) -> Built:
    renew_day(rw, "2031-10-11-1", CNA + timedelta(days=10))
    renew_day(rw, "2031-06-23-1", CNA - timedelta(days=100))
    return Built(CNA + timedelta(days=20))


def _own_certificate_due(rw: RenewalWorld) -> Built:
    renew_day(rw, "2031-06-23-1", CNA - timedelta(days=100), expires=CNA + timedelta(days=50))
    return Built(CNA - timedelta(days=90))


def _stamped_at_its_own_not_after(rw: RenewalWorld) -> Built:
    at = CNA - timedelta(days=100)
    renew_day(rw, "2031-06-23-1", at, expires=at, not_before=at - timedelta(days=30))
    return Built(CNA - timedelta(days=90))


def _three_deep(rw: RenewalWorld) -> Built:
    three_deep(rw)
    return Built(CHAIN_NOW)


def _chain_broken(rw: RenewalWorld) -> Built:
    three_deep(rw, links=2)
    return Built(CHAIN_NOW)


def _eku_not_critical(rw: RenewalWorld, *, relaxed: bool | None = None) -> Built:
    """The renewal's certificate marks its EKU non-critical; whether it counts is the rule its
    own record was written under (the site's, or the one ``relaxed`` names), not the day's."""
    record = renew_day(
        rw, "2031-06-23-1", CNA - timedelta(days=100), eku_critical=False, relaxed=relaxed
    )
    tsa = json.loads((rw.layout.vault / record).read_bytes())["tsa"]
    if relaxed is not None:
        assert ("require_critical_eku" in tsa) is relaxed
    return Built(CNA - timedelta(days=90))


def _eku_relaxed_record(rw: RenewalWorld) -> Built:
    """A day sealed strict, then a renewal whose record says the site relaxed the rule."""
    return _eku_not_critical(rw, relaxed=True)


def _eku_strict_record(rw: RenewalWorld) -> Built:
    """A day sealed relaxed, then a renewal whose record says the rule is strict again."""
    return _eku_not_critical(rw, relaxed=False)


def _eku_strict_record_beside_a_relaxed_day(rw: RenewalWorld) -> Built:
    """The day sealed strict and the next one relaxed, in one package; the renewal of the first
    day says the rule is strict: a relaxed manifest elsewhere does not relax it."""
    config = rw.world.config
    rw.world.config = config.model_copy(
        update={"tsa": config.tsa.model_copy(update={"require_critical_eku": False})}
    )
    rw.seal(OTHER)
    rw.world.config = config
    manifest = json.loads(rw.layout.manifest(OTHER).read_bytes())
    assert manifest["tsa"]["require_critical_eku"] is False
    return _eku_not_critical(rw, relaxed=False)


def _mock(rw: RenewalWorld) -> Built:
    at = CNA - timedelta(days=100)
    rw.set_now(at)
    rw.write_renewal("2031-06-23-1", [rw.day_entry()], rw.mock_provider(FAR))
    return Built(at + timedelta(days=10))


def _claim_on_mock_root(rw: RenewalWorld, *, policy: str | None = None) -> Built:
    at = CNA - timedelta(days=100)
    rw.set_now(at)
    rw.mock_signer(FAR)
    if policy is not None:
        rw.tsa.policy = policy
    rw.write_renewal("2031-06-23-1", [rw.day_entry()], ProductionLikeProvider(rw.tsa))
    return Built(at + timedelta(days=10))


def _real_policy_on_mock_root(rw: RenewalWorld) -> Built:
    return _claim_on_mock_root(rw, policy=SIMULATED_POLICY)


def _test_policy_on_legal_root(rw: RenewalWorld) -> Built:
    rw.legal_tsa.policy = sv.TEST_POLICY_PREFIX + "5651.1"
    renew_day(rw, "2031-06-23-1", CNA - timedelta(days=100))
    return Built(CNA - timedelta(days=90))


def _legal_stamp_of_a_test_record(rw: RenewalWorld) -> Built:
    record = renew_day(rw, "2031-06-23-1", CNA - timedelta(days=100))
    path = rw.layout.vault / record
    edited = json.loads(path.read_bytes())
    edited["reason"] = "test"
    token_path_for(path).unlink()
    path.write_bytes(canonical_json(edited))
    rw.legal_provider(FAR).stamp(path)
    return Built(CNA - timedelta(days=90))


def _changed_after_its_stamp(rw: RenewalWorld) -> Built:
    record = renew_day(rw, "2031-06-23-1", CNA - timedelta(days=100))
    path = rw.layout.vault / record
    edited = json.loads(path.read_bytes())
    edited["created_at"] = edited["created_at"].replace("2031", "2030", 1)
    path.write_bytes(canonical_json(edited))
    return Built(CNA - timedelta(days=90))


#: How :func:`tampered_renewal` edits a record: each one a reader that keeps the last of two keys
#: (``json.loads``), or guesses the encoding of bytes, reads as relaxed.
TAMPERINGS = ("a key twice", "UTF-16")


def tampered_renewal(
    rw: RenewalWorld,
    how: str,
    name: str = "2031-06-23-1",
    at: datetime = CNA - timedelta(days=100),
    *,
    expires: datetime = FAR,
) -> str:
    """A renewal of the day with a non-critical EKU (as :func:`renew_day`) whose record, edited
    and then stamped again, says relaxed only to a lax reader: ``tsa.require_critical_eku``
    written twice, true first and false last (``a key twice``), or the relaxed record in UTF-16
    (``UTF-16``). The standalone verifier's ``load_json`` cannot read either, so each reader
    must judge its token strict (#156 item 3). Its record path."""
    record = renew_day(rw, name, at, expires=expires, eku_critical=False, relaxed=True)
    path = rw.layout.vault / record
    raw = path.read_bytes()
    if how == "a key twice":
        edited = raw.replace(b'"tsa":{', b'"tsa":{"require_critical_eku":true,', 1)
    else:
        edited = raw.decode("utf-8").encode("utf-16")
    assert json.loads(edited)["tsa"]["require_critical_eku"] is False  # the lax reading
    with pytest.raises(ValueError, match=r"appears twice|can't decode"):
        sv.load_json(edited)
    token_path_for(path).unlink()
    path.write_bytes(edited)
    rw.legal_provider(expires, eku_critical=False).stamp(path)
    return record


def _tampered_twice(rw: RenewalWorld) -> Built:
    tampered_renewal(rw, "a key twice")
    return Built(CNA - timedelta(days=90))


def _tampered_utf16(rw: RenewalWorld) -> Built:
    tampered_renewal(rw, "UTF-16")
    return Built(CNA - timedelta(days=90))


def _root_not_pinned(rw: RenewalWorld) -> Built:
    renew_day(rw, "2031-06-23-1", CNA - timedelta(days=100))
    return Built(CNA - timedelta(days=90), legal_root_pinned=False)


def _lists_another_token(rw: RenewalWorld) -> Built:
    rw.seal(OTHER)
    at = CNA - timedelta(days=100)
    rw.set_now(at)
    rw.write_renewal("2031-06-23-1", [rw.day_entry(OTHER)], rw.legal_provider(FAR))
    return Built(at + timedelta(days=10))


def _revoked_before(rw: RenewalWorld) -> Built:
    return listed_renewal(rw, -timedelta(days=5))


def _revoked_at(rw: RenewalWorld) -> Built:
    return listed_renewal(rw, timedelta(0))


def _revoked_after(rw: RenewalWorld) -> Built:
    return listed_renewal(rw, timedelta(days=5))


def _on_hold(rw: RenewalWorld) -> Built:
    return listed_renewal(rw, -timedelta(days=5), hold=True)


CASES: tuple[RenewalCase, ...] = (
    # the clock alone
    RenewalCase("more than 180 days left", _not_due, "renewal-not-due", "info"),
    RenewalCase("exactly 180 days left", _due_at_the_window, "renewal-due", "warn"),
    RenewalCase("90 days left, no renewal", _due, "renewal-due", "warn"),
    RenewalCase("expired, no renewal", _uncovered, "renewal-uncovered", "warn"),
    RenewalCase(
        "at the certificate's notAfter itself, no renewal (Q16)",
        _uncovered_at_not_after,
        "renewal-uncovered",
        "warn",
    ),
    # a renewal that counts
    RenewalCase("renewed before expiry", _covered, "renewal-covered", "ok"),
    RenewalCase(
        "renewed before expiry, judged after it", _covered_after_expiry, "renewal-covered", "ok"
    ),
    RenewalCase("renewed by renew()", _covered_by_renew, "renewal-covered", "ok"),
    RenewalCase("renewed after expiry", _late, "renewal-late", "warn"),
    RenewalCase(
        "renewed at the certificate's notAfter itself (Q16)",
        _late_at_not_after,
        "renewal-late",
        "warn",
    ),
    RenewalCase(
        "a late and an in-time renewal: the earliest decides",
        _earliest_decides,
        "renewal-covered",
        "ok",
    ),
    # the chain of renewals
    RenewalCase("a chain three deep", _three_deep, "renewal-covered", "ok"),
    RenewalCase(
        "a chain broken: the second renewal's own certificate is due, no third renewal",
        _chain_broken,
        "renewal-uncovered",
        "warn",
    ),
    RenewalCase(
        "the renewal's own certificate is due", _own_certificate_due, "renewal-due", "warn"
    ),
    RenewalCase(
        "the renewal was stamped at its own certificate's notAfter (Q16)",
        _stamped_at_its_own_not_after,
        "renewal-due",
        "warn",
    ),
    # the EKU rule (K6): each renewal's own record judges its token, as a day's manifest does
    # the day's (#137 item 4, W5-A-17)
    RenewalCase(
        "a renewal with a non-critical EKU, its record and the day relaxed",
        _eku_not_critical,
        "renewal-covered",
        "ok",
        relaxed=True,
    ),
    RenewalCase(
        "a renewal with a non-critical EKU, its record and the day strict",
        _eku_not_critical,
        "renewal-due",
        "warn",
    ),
    RenewalCase(
        "a renewal with a non-critical EKU, its record relaxed, the day sealed strict",
        _eku_relaxed_record,
        "renewal-covered",
        "ok",
    ),
    RenewalCase(
        "a renewal with a non-critical EKU, its record strict, the day sealed relaxed",
        _eku_strict_record,
        "renewal-due",
        "warn",
        relaxed=True,
    ),
    RenewalCase(
        "a renewal with a non-critical EKU, its record strict, beside a relaxed day",
        _eku_strict_record_beside_a_relaxed_day,
        "renewal-due",
        "warn",
    ),
    # what never counts (D5)
    RenewalCase("a mock renewal (reason test)", _mock, "renewal-due", "warn"),
    RenewalCase("a production claim on the mock root", _claim_on_mock_root, "renewal-due", "warn"),
    RenewalCase(
        "a production claim and policy on the mock root",
        _real_policy_on_mock_root,
        "renewal-due",
        "warn",
    ),
    RenewalCase(
        "a test policy on the legal root", _test_policy_on_legal_root, "renewal-due", "warn"
    ),
    RenewalCase(
        "a legal stamp of a record written as a test",
        _legal_stamp_of_a_test_record,
        "renewal-due",
        "warn",
    ),
    RenewalCase(
        "a record changed after its stamp", _changed_after_its_stamp, "renewal-due", "warn"
    ),
    RenewalCase(
        "a record with require_critical_eku twice, the last false",
        _tampered_twice,
        "renewal-due",
        "warn",
    ),
    RenewalCase("a relaxed record in UTF-16", _tampered_utf16, "renewal-due", "warn"),
    RenewalCase("a root that is not pinned", _root_not_pinned, "renewal-due", "warn"),
    RenewalCase("a renewal of another token only", _lists_another_token, "renewal-due", "warn"),
    # revocation of the renewal's certificate (C15): only at or before the stamp
    RenewalCase("revoked before the stamp", _revoked_before, "renewal-due", "warn"),
    RenewalCase("revoked at the stamp time", _revoked_at, "renewal-due", "warn"),
    RenewalCase("revoked after the stamp", _revoked_after, "renewal-covered", "ok"),
    RenewalCase("on hold before the stamp", _on_hold, "renewal-covered", "ok"),
)


# -- the two verifiers -----------------------------------------------------------------------------


def _roots(rw: RenewalWorld, built: Built) -> list[x509.Certificate]:
    return rw.roots if built.legal_root_pinned else [rw.tsa.root_certificate]


def core_verdict(rw: RenewalWorld, built: Built) -> Verdict:
    """The core verifier's ``renewal`` of the day: ``verify_day(..., renewals=True)`` with the
    lists stored as ``crl fetch`` stores them, the legal root listed (rule 4) and the
    standalone verifier's window (the §2.5 default, W5-A-5) passed as the site's."""
    if built.lists:
        _store(rw.layout, list(built.lists), rw.legal.root)
    check = verify_day(
        rw.layout,
        DAY,
        _roots(rw, built),
        legal_roots=[rw.legal.fingerprint],
        now=built.now,
        renewals=True,
        renew_before_days=sv.RENEW_BEFORE.days,
    )
    assert check.ok, check.errors
    assert check.renewal is not None
    renewal = check.renewal
    return Verdict(renewal.code, renewal.level, renewal.message_tr, renewal.message_en)


def standalone_verdict(rw: RenewalWorld, built: Built, folder: Path) -> Verdict:
    """The standalone verifier's finding for the day (:func:`standalone_report`)."""
    report = standalone_report(rw, built, folder)
    (finding,) = [f for f in report.findings if f.code in LEVELS and f.where == DAY.isoformat()]
    return Verdict(finding.code, finding.level, finding.tr, finding.en)


def standalone_report(rw: RenewalWorld, built: Built, folder: Path) -> sv.Report:
    """The standalone verifier's report on a copy of the vault (``sil/`` holds the lists,
    ``--kokler`` the roots), in this process at ``now``."""
    copy = folder / "kopya"
    shutil.copytree(rw.layout.vault, copy)
    if built.lists:
        (copy / "sil").mkdir()
        for number, der in enumerate(built.lists):
            pem = x509.load_der_x509_crl(der).public_bytes(serialization.Encoding.PEM)
            (copy / "sil" / f"legal-{number}.crl").write_bytes(pem)
    roots = folder / "kokler"
    roots.mkdir()
    for number, root in enumerate(_roots(rw, built)):
        (roots / f"root-{number}.pem").write_bytes(root.public_bytes(serialization.Encoding.PEM))
    return in_process(copy, roots_dir=roots, now=built.now)


def core_status(rw: RenewalWorld, built: Built) -> str:
    """The status ``renewal_status`` gives the day's token (``state/renewal/renewal.json``)."""
    report = renewal_status(rw.layout, rw.world.config, _roots(rw, built), built.now)
    return rw.of(report, day_token_path()).status


# -- deletion records (#156 item 1, W5-A-19) -------------------------------------------------------

#: The third sealed day of a deletion case.
THIRD = DAY + timedelta(days=2)
DELETION = "2026-10-14-imha.json"


@dataclass(frozen=True)
class DeletionCase:
    """A deletion record whose token's certificate marks its EKU non-critical, in a package of
    ``days`` (the days :attr:`build` seals and returns). Its token is judged by the EKU rule of
    the manifests that list the record, or by the package's when none of the packaged ones
    does (reached only through the chain anchor). ``findings``: the ``token-eku`` findings of
    the standalone verifier on the package, as (level, code, day); the builder bundles the
    legal CA's root for the token exactly when they are warnings, and refuses the package
    (its self-check) when one fails."""

    name: str
    build: Callable[[RenewalWorld], tuple[date, ...]]
    findings: frozenset[tuple[str, str, str]]

    @property
    def relaxed(self) -> bool:
        return all(level == "warn" for level, _, _ in self.findings)


def deletion_record(rw: RenewalWorld, days: list[dict[str, str]]) -> Path:
    """``deletions/<DELETION>`` listing ``days``, stamped now under the legal CA's root (which
    no sealed day uses) by a certificate whose EKU is not critical."""
    record = rw.layout.deletions / DELETION
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_bytes(
        canonical_json({"schema": "arcilogger.deletion/1", "days": days, "site": "Örnek A.Ş."})
    )
    rw.legal_provider(FAR, eku_critical=False).stamp(record)
    return record


def delete_day(rw: RenewalWorld) -> None:
    """:data:`DAY` deleted as the retention job deletes it: a deletion record that keeps its
    hashes, the chain anchor naming that record, and the day's folder gone. No manifest lists
    the record yet (no day was sealed after it)."""
    layout = rw.layout
    hashes = {
        "manifest_sha256": sha256_file(layout.manifest(DAY)),
        "token_sha256": sha256_file(layout.token(DAY)),
    }
    deletion_record(rw, [{"day": DAY.isoformat(), **hashes}])
    anchor = {"day": DAY.isoformat(), **hashes, "record": DELETION}
    layout.chain_anchor.write_bytes(canonical_json(anchor))
    folder = layout.vault_day(DAY)
    for path in folder.iterdir():
        if is_locked(path):
            unlock(path)
    shutil.rmtree(folder)


def _listed_strict_beside_a_relaxed_day(rw: RenewalWorld) -> tuple[date, ...]:
    deletion_record(rw, [])
    rw.seal(OTHER, relaxed=False)
    rw.seal(THIRD, relaxed=True)
    return (OTHER, THIRD)


def _listed_relaxed_beside_a_strict_day(rw: RenewalWorld) -> tuple[date, ...]:
    deletion_record(rw, [])
    rw.seal(OTHER, relaxed=True)
    return (DAY, OTHER)


def _listed_strict(rw: RenewalWorld) -> tuple[date, ...]:
    deletion_record(rw, [])
    rw.seal(OTHER, relaxed=False)
    return (DAY, OTHER)


def _behind_the_anchor(rw: RenewalWorld, *, relaxed: bool) -> tuple[date, ...]:
    rw.seal(OTHER, relaxed=relaxed)
    delete_day(rw)
    return (OTHER,)


def _behind_the_anchor_relaxed(rw: RenewalWorld) -> tuple[date, ...]:
    return _behind_the_anchor(rw, relaxed=True)


def _behind_the_anchor_strict(rw: RenewalWorld) -> tuple[date, ...]:
    return _behind_the_anchor(rw, relaxed=False)


def _behind_the_anchor_and_listed_strict(rw: RenewalWorld) -> tuple[date, ...]:
    _behind_the_anchor(rw, relaxed=True)
    rw.seal(THIRD, relaxed=False)
    return (OTHER, THIRD)


def _eku(*found: tuple[str, date]) -> frozenset[tuple[str, str, str]]:
    codes = {"fail": "token-eku", "warn": "token-eku-not-critical"}
    return frozenset((level, codes[level], day.isoformat()) for level, day in found)


DELETION_CASES: tuple[DeletionCase, ...] = (
    DeletionCase(
        "listed by a strict manifest, beside a relaxed day",
        _listed_strict_beside_a_relaxed_day,
        _eku(("fail", OTHER)),
    ),
    DeletionCase(
        "listed by a relaxed manifest, beside a strict day",
        _listed_relaxed_beside_a_strict_day,
        _eku(("warn", OTHER)),
    ),
    DeletionCase("listed by a strict manifest", _listed_strict, _eku(("fail", OTHER))),
    DeletionCase(
        "only behind the chain anchor, the package relaxed",
        _behind_the_anchor_relaxed,
        _eku(("warn", OTHER)),
    ),
    DeletionCase(
        "only behind the chain anchor, the package strict",
        _behind_the_anchor_strict,
        _eku(("fail", OTHER)),
    ),
    DeletionCase(
        "behind the chain anchor and listed by a strict manifest, the package relaxed",
        _behind_the_anchor_and_listed_strict,
        _eku(("fail", OTHER), ("fail", THIRD)),
    ),
)
