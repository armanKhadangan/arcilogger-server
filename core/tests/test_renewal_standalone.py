"""The standalone verifier's long-term status of each day's stamp (contract 2.1.4, D5, W5-A-5).

``check_renewals`` gives every packaged day token one finding: ``renewal-not-due`` (info),
``renewal-covered`` (ok), ``renewal-due``, ``renewal-late`` or ``renewal-uncovered`` (warn),
computed from the package alone with the default window of 180 days. None of them fails a
package (D6). The isolated runs verify at the real time: their days are stamped in the past
(October 2026) with certificates whose expiry is chosen around today.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from arcilogger_core.canonical import canonical_json
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.renewal import (
    RecordEntry,
    RenewalFact,
    TokenFact,
    compute_statuses,
    protects,
)
from arcilogger_core.seal import sha256_file
from arcilogger_core.timeutil import SITE_TZ, utc_now
from arcilogger_core.tsa.client import token_path_for
from arcilogger_core.tsa.verify import _valid_at as core_valid_at
from arcilogger_core.tsa.verify import inspect_token

from .renewal_world import CNA, DAY, RenewalWorld, day_token_path, make_world
from .test_evidence import findings, in_process, isolated
from .test_legal_grade import SIMULATED_POLICY, ProductionLikeProvider
from .test_renewal import _revoking
from .test_revocation import _store

CONTRACT = Path(__file__).resolve().parents[2] / "design" / "wave-5-contract.md"
FAR = CNA + timedelta(days=1825)
#: The second sealed day of a world: its certificate's expiry is what each case chooses.
OTHER = DAY + timedelta(days=1)
LEVELS = {
    "renewal-not-due": "info",
    "renewal-covered": "ok",
    "renewal-due": "warn",
    "renewal-late": "warn",
    "renewal-uncovered": "warn",
}


@pytest.fixture
def rw(tmp_path: Path) -> RenewalWorld:
    return make_world(tmp_path)


def _roots(rw: RenewalWorld, folder: Path) -> Path:
    """The mock root and the legal CA's root as the auditor's ``--kokler`` folder."""
    folder.mkdir()
    for number, root in enumerate(rw.roots):
        (folder / f"root-{number}.pem").write_bytes(root.public_bytes(serialization.Encoding.PEM))
    return folder


def _day_findings(report: sv.Report | dict[str, Any], day: date) -> list[tuple[str, ...]]:
    """The renewal findings of ``day`` as (level, code, tr, en), in process or from JSON."""
    if isinstance(report, sv.Report):
        listed = [finding.as_json() for finding in report.findings]
    else:
        listed = report["findings"]
    return [
        (f["level"], f["code"], f["tr"], f["en"])
        for f in listed
        if f["code"] in LEVELS and f["where"] == day.isoformat()
    ]


def _renewal(rw: RenewalWorld, name: str, at: datetime, day: date, *, legal: bool = True) -> str:
    rw.set_now(at)
    provider = rw.legal_provider(FAR) if legal else rw.mock_provider(FAR)
    rw.write_renewal(name, [rw.day_entry(day)], provider)
    return f"renewals/{name}.json"


def test_verifier_version_is_9() -> None:
    """Addendum H4 (W5-A-8): W5-H merged first and set "5", so W5-A set "6"; #137 changed the
    output (each renewal token judged by its own record's EKU rule, W5-A-17) and took "7";
    #156 item 1 changes it again (a deletion record's token judged by the manifest that lists
    it, W5-A-19) and took "8"; #136 adds ``xz-unsupported`` (a Python without lzma) and takes
    "9"."""
    assert sv.VERIFIER_VERSION == "9"


def test_texts_are_the_contract_texts() -> None:
    """The levels of 2.1.4 and the TR/EN texts of 2.6, verbatim."""
    contract = CONTRACT.read_text(encoding="utf-8")
    for status, (level, code, tr, en) in sv.RENEWAL_FINDINGS.items():
        assert code == f"renewal-{status.replace('_', '-')}"
        assert f"| {level} | `{code}` | — |" in contract
        assert LEVELS[code] == level
        assert f"| `{code}` | {tr} | {en} |" in contract
    assert sorted(sv.RENEWAL_FINDINGS) == sorted(["not_due", "covered", "due", "late", "uncovered"])
    # no text claims what the product must not
    texts = " ".join(f"{tr} {en}" for _, _, tr, en in sv.RENEWAL_FINDINGS.values())
    assert not re.search(r"uyumlu|garanti|compliant", texts, re.IGNORECASE)


# -- each status, in process at a chosen time ------------------------------------------------------


@dataclass(frozen=True)
class Case:
    status: str
    now: timedelta  # relative to CNA
    renewed: timedelta | None = None  # when a legal renewal of the day was stamped


CASES = [
    Case("not_due", -timedelta(days=181)),
    Case("due", -timedelta(days=180)),
    Case("uncovered", timedelta(days=1)),
    Case("covered", -timedelta(days=90), renewed=-timedelta(days=100)),
    Case("late", timedelta(days=20), renewed=timedelta(days=10)),
]


@pytest.mark.parametrize("case", CASES, ids=[case.status for case in CASES])
def test_each_status_in_a_package(rw: RenewalWorld, tmp_path: Path, case: Case) -> None:
    record = None
    if case.renewed is not None:
        record = _renewal(rw, f"{(CNA + case.renewed).date()}-1", CNA + case.renewed, DAY)
    roots = _roots(rw, tmp_path / "kokler")
    report = in_process(rw.layout.vault, roots_dir=roots, now=CNA + case.now)
    assert report.passed, report.codes()
    code = f"renewal-{case.status.replace('_', '-')}"
    ((level, found, tr, en),) = _day_findings(report, DAY)
    assert (level, found) == (LEVELS[code], code)
    template_tr, template_en = sv.RENEWAL_FINDINGS[case.status][2:]
    if record is not None and case.status == "covered":
        assert (tr, en) == (template_tr.format(renewal=record), template_en.format(renewal=record))
    else:
        moment = CNA + case.renewed if case.renewed is not None else CNA
        assert tr == template_tr.format(date=sv._format_time(moment))
        assert en == template_en.format(date=moment.isoformat())
    # the core's status of the same token at the same time agrees (rule 5)
    assert rw.of(rw.status(CNA + case.now), day_token_path()).status == case.status
    if record is not None:  # the record itself is still checked and described
        assert {f.code for f in report.findings} >= {"renewal-ok", "renewal-covers"}


def test_a_three_deep_chain_covers_the_day(rw: RenewalWorld, tmp_path: Path) -> None:
    """R1 covers the day; R2 renews R1's token and R3 R2's, each before it comes due: the day
    stays covered by R1 while the chain holds, and is due once a link is missing."""
    r1_expires = CNA + timedelta(days=400)
    rw.set_now(CNA - timedelta(days=100))
    rw.write_renewal("2031-06-23-1", [rw.day_entry()], rw.legal_provider(r1_expires))
    r1_entry = _entry_of(rw, "renewals/2031-06-23-1.json")
    rw.set_now(r1_expires - timedelta(days=100))
    r2_expires = r1_expires + timedelta(days=400)
    rw.write_renewal("2032-09-27-1", [r1_entry], rw.legal_provider(r2_expires))
    r2_entry = _entry_of(rw, "renewals/2032-09-27-1.json")
    rw.set_now(r2_expires - timedelta(days=100))
    rw.write_renewal("2033-10-31-1", [r2_entry], rw.legal_provider(FAR + timedelta(days=3650)))
    roots = _roots(rw, tmp_path / "kokler")
    now = r2_expires - timedelta(days=50)
    report = in_process(rw.layout.vault, roots_dir=roots, now=now)
    assert report.passed, report.codes()
    ((_, code, tr, _),) = _day_findings(report, DAY)
    assert (code, tr) == (
        "renewal-covered",
        sv.RENEWAL_FINDINGS["covered"][2].format(renewal="renewals/2031-06-23-1.json"),
    )
    assert rw.of(rw.status(now), day_token_path()).covered_by == "renewals/2031-06-23-1.json"
    # without R3, R2's own certificate is due at that time: R2 does not count, so neither
    # does R1, and the day (its certificate expired) is uncovered
    for name in ("2033-10-31-1.json", "2033-10-31-1.json.zd"):
        path = rw.layout.renewals_dir / name
        path.unlink()
    report = in_process(rw.layout.vault, roots_dir=roots, now=now)
    ((_, code, _, _),) = _day_findings(report, DAY)
    assert code == "renewal-uncovered"
    assert rw.of(rw.status(now), day_token_path()).status == "uncovered"


def _entry_of(rw: RenewalWorld, record: str) -> RecordEntry:
    """The entry a newer record lists for the renewal ``record`` (its own token)."""
    path = rw.layout.vault / record
    token = path.with_name(path.name + ".zd")
    return RecordEntry(
        "renewal",
        f"{record}.zd",
        sha256_file(token),
        inspect_token(token.read_bytes()).tsa_cert_not_after or FAR,
        record_sha256=sha256_file(path),
        protects=tuple(protects(json.loads(path.read_bytes()))),
    )


# -- what never counts -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    ["mock", "claim_on_mock_root", "real_policy_on_mock_root", "test_policy", "test_reason"],
)
def test_a_mock_renewal_record_in_a_package_never_counts(
    rw: RenewalWorld, tmp_path: Path, kind: str
) -> None:
    """D5: a record of the mock provider (``reason: "test"``), a record that claims legal grade
    but carries a test stamp (2.999 policy and test root, or either one alone) and a legal stamp
    of a record written as a test: the day stays due."""
    at = CNA - timedelta(days=100)
    rw.set_now(at)
    if kind == "mock":
        rw.write_renewal("2031-06-23-1", [rw.day_entry()], rw.mock_provider(FAR))
    elif kind in ("claim_on_mock_root", "real_policy_on_mock_root"):
        rw.mock_signer(FAR)
        if kind == "real_policy_on_mock_root":
            rw.tsa.policy = SIMULATED_POLICY
        rw.write_renewal("2031-06-23-1", [rw.day_entry()], ProductionLikeProvider(rw.tsa))
    elif kind == "test_policy":  # the legal CA's certificate, a policy under 2.999
        rw.legal_tsa.policy = sv.TEST_POLICY_PREFIX + "5651.1"
        rw.write_renewal("2031-06-23-1", [rw.day_entry()], rw.legal_provider(FAR))
    else:  # build_record ties the reason to the claim; a hand edit does not
        path = rw.write_renewal("2031-06-23-1", [rw.day_entry()], rw.legal_provider(FAR))
        record = json.loads(path.read_bytes())
        record["reason"] = "test"
        token_path_for(path).unlink()
        path.write_bytes(canonical_json(record))
        rw.legal_provider(FAR).stamp(path)
    roots = _roots(rw, tmp_path / "kokler")
    report = in_process(rw.layout.vault, roots_dir=roots, now=at + timedelta(days=10))
    ((level, code, _, _),) = _day_findings(report, DAY)
    assert (level, code) == ("warn", "renewal-due")
    assert "renewal-ok" in {f.code for f in report.findings}  # the stamp itself verifies


def test_a_revoked_renewal_never_counts(rw: RenewalWorld, tmp_path: Path) -> None:
    """C15: the lists next to the vault copy show the renewal's TSA certificate revoked before
    its stamp: the renewal does not count and the day stays due."""
    at = CNA - timedelta(days=100)
    rw.set_now(at)
    provider = rw.legal_provider(FAR, not_before=at - timedelta(days=30))
    assert rw.legal_tsa.signer is not None
    rw.write_renewal("2031-06-23-1", [rw.day_entry()], provider)
    copy = tmp_path / "kopya"
    shutil.copytree(rw.layout.vault, copy)
    crl = x509.load_der_x509_crl(_revoking(rw, rw.legal_tsa.signer[0], at - timedelta(days=5)))
    (copy / "sil").mkdir()
    (copy / "sil" / "legal.crl").write_bytes(crl.public_bytes(serialization.Encoding.PEM))
    roots = _roots(rw, tmp_path / "kokler")
    report = in_process(copy, roots_dir=roots, now=at + timedelta(days=10))
    assert "crl-loaded" in {f.code for f in report.findings}
    ((_, code, _, _),) = _day_findings(report, DAY)
    assert code == "renewal-due"
    (copy / "sil" / "legal.crl").unlink()
    report = in_process(copy, roots_dir=roots, now=at + timedelta(days=10))
    ((_, code, _, _),) = _day_findings(report, DAY)
    assert code == "renewal-covered"


def _listing(
    rw: RenewalWorld, certificate: x509.Certificate, at: datetime, hold: bool
) -> x509.CertificateRevocationList:
    """A list of the legal CA naming ``certificate`` at ``at``: revoked, or on hold."""
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
    return builder.sign(rw.legal.key, hashes.SHA256())


@pytest.mark.parametrize(
    ("offset", "hold", "revocation"),
    [(5, False, "crl-revoked-later"), (-5, True, "crl-hold")],
    ids=["revoked_after_the_stamp", "on_hold_before_the_stamp"],
)
def test_a_warning_of_the_lists_does_not_stop_a_renewal_counting(
    rw: RenewalWorld, tmp_path: Path, offset: int, hold: bool, revocation: str
) -> None:
    """C15: only a revocation at or before the stamp makes a renewal not count. A revocation
    after it, or a hold, is a warning of the lists: the renewal covers the day in both
    verifiers."""
    at = CNA - timedelta(days=100)
    rw.set_now(at)
    provider = rw.legal_provider(FAR, not_before=at - timedelta(days=30))
    assert rw.legal_tsa.signer is not None
    record = rw.write_renewal("2031-06-23-1", [rw.day_entry()], provider)
    crl = _listing(rw, rw.legal_tsa.signer[0], at + timedelta(days=offset), hold)
    copy = tmp_path / "kopya"
    shutil.copytree(rw.layout.vault, copy)
    (copy / "sil").mkdir()
    (copy / "sil" / "legal.crl").write_bytes(crl.public_bytes(serialization.Encoding.PEM))
    now = at + timedelta(days=10)
    # the lists give the renewal's certificate a warning, never a failure
    roots = [
        sv.parse_certificate(root.public_bytes(serialization.Encoding.DER)) for root in rw.roots
    ]
    token = record.with_name(record.name + ".zd").read_bytes()
    result = sv.verify_timestamp(token, record.read_bytes(), roots, now=now)
    assert result.ok
    assert result.gen_time is not None
    der = crl.public_bytes(serialization.Encoding.DER)
    found = sv.check_revocation(result.chain, [sv.parse_crl(der)], result.gen_time)
    assert [(level, code) for level, code, *_ in found] == [("warn", revocation)]
    report = in_process(copy, roots_dir=_roots(rw, tmp_path / "kokler"), now=now)
    ((level, code, _, _),) = _day_findings(report, DAY)
    assert (level, code) == ("ok", "renewal-covered")
    _store(rw.layout, [der], rw.legal.root)
    day = rw.of(rw.status(now), day_token_path())
    assert (day.status, day.covered_by) == ("covered", f"renewals/{record.name}")


# -- isolated, at the real time --------------------------------------------------------------------


def _seal_other(rw: RenewalWorld, cert_not_after: datetime) -> None:
    rw.seal(OTHER, cert_not_after)


@pytest.mark.parametrize("status", ["not_due", "due", "covered", "late", "uncovered"])
def test_each_status_isolated(rw: RenewalWorld, tmp_path: Path, status: str) -> None:
    """``python -I -S verify.py`` on a vault copy, at the real time; in process the same."""
    today = utc_now()
    past = datetime.combine(date(2026, 10, 20), time(12, 0), tzinfo=SITE_TZ).astimezone(UTC)
    assert today > past + timedelta(days=10), "the test assumes its October 2026 dates are past"
    expires = {
        "not_due": today + timedelta(days=1825),
        "due": today + timedelta(days=100),
        "covered": today + timedelta(days=100),
        "late": past - timedelta(days=2),
        "uncovered": past - timedelta(days=2),
    }[status]
    _seal_other(rw, expires)
    if status in ("covered", "late"):
        _renewal(rw, "2026-10-20-1", past, OTHER)
    code, report = isolated(rw.layout.vault, "--kokler", str(_roots(rw, tmp_path / "kokler")))
    assert code == 0  # nothing here fails a package (D6)
    ((level, found, _, _),) = _day_findings(report, OTHER)
    assert found == f"renewal-{status.replace('_', '-')}"
    assert level == LEVELS[found]
    ((_, first, _, _),) = _day_findings(report, DAY)
    assert first == _code(rw.of(rw.status(utc_now()), day_token_path()).status)
    path = f"{OTHER:%Y/%m/%d}/manifest-{OTHER.isoformat()}.json.zd"
    assert rw.of(rw.status(utc_now()), path).status == status


def _code(status: str) -> str:
    """The standalone code of a core status (DAY's certificate expires at the fixed CNA, so
    its status at the real time is the core's, whatever today is)."""
    return f"renewal-{status.replace('_', '-')}"


def test_a_package_without_renewals_reports_not_due(rw: RenewalWorld, tmp_path: Path) -> None:
    """A vault copy with no renewal at all still gets section 5, one finding per day: the
    core's status at the real time (``renewal-not-due`` until 180 days before CNA)."""
    code, report = isolated(rw.layout.vault, "--kokler", str(_roots(rw, tmp_path / "kokler")))
    assert code == 0
    status = rw.of(rw.status(utc_now()), day_token_path()).status
    assert [f["code"] for f in report["findings"] if f["code"].startswith("renewal-")] == [
        _code(status)
    ]
    (finding,) = findings(report, _code(status))
    assert finding["where"] == DAY.isoformat()
    assert finding["en"] == sv.RENEWAL_FINDINGS[status][3].format(date=CNA.isoformat(), renewal="")


# -- the rules are the core's ----------------------------------------------------------------------


def _hash(number: int) -> str:
    return f"{number:064x}"


NOW = datetime(2031, 6, 23, tzinfo=UTC)
WINDOW = timedelta(days=180)


def _facts(
    renewals: list[tuple[str, int, timedelta, timedelta, bool, set[int]]],
) -> tuple[list[RenewalFact], list[sv.Renewal]]:
    """(record, own hash, own expiry from NOW, stamp time from NOW, eligible, listed hashes)."""
    core, standalone = [], []
    for record, own, expires, stamped, eligible, listed in renewals:
        token = TokenFact(f"{record}.zd", "renewal", _hash(own), NOW + expires)
        lists = frozenset(_hash(n) for n in listed)
        core.append(RenewalFact(record, token, NOW + stamped, eligible, lists))
        standalone.append(
            sv.Renewal(record, _hash(own), NOW + expires, NOW + stamped, eligible, lists)
        )
    return core, standalone


RULE_CASES: dict[str, list[tuple[str, int, timedelta, timedelta, bool, set[int]]]] = {
    "none": [],
    "covered": [("renewals/a.json", 10, 3 * WINDOW, -timedelta(days=5), True, {1, 2})],
    "late": [("renewals/a.json", 10, 3 * WINDOW, timedelta(days=0), True, {1, 2, 3})],
    "ineligible": [("renewals/a.json", 10, 3 * WINDOW, -timedelta(days=5), False, {1, 2})],
    "renewal_due": [("renewals/a.json", 10, WINDOW / 2, -timedelta(days=5), True, {1, 2})],
    "chain": [
        ("renewals/a.json", 10, WINDOW / 2, -timedelta(days=50), True, {1, 2}),
        ("renewals/b.json", 11, 3 * WINDOW, -timedelta(days=5), True, {10}),
    ],
    "cycle": [
        ("renewals/a.json", 10, WINDOW / 2, -timedelta(days=50), True, {1, 11}),
        ("renewals/b.json", 11, WINDOW / 2, -timedelta(days=5), True, {10}),
    ],
    "earliest_decides": [
        ("renewals/b.json", 11, 3 * WINDOW, -timedelta(days=9), True, {2}),
        ("renewals/a.json", 10, 3 * WINDOW, -timedelta(days=9), True, {2}),
        ("renewals/c.json", 12, 3 * WINDOW, -timedelta(days=20), True, {3}),
    ],
}


@pytest.mark.parametrize("name", sorted(RULE_CASES))
def test_the_rules_are_the_core_rules(name: str) -> None:
    """The stdlib copy in verify.py gives the core's status and renewal for the same facts."""
    tokens = [
        TokenFact("1", "day", _hash(1), NOW + 2 * WINDOW),  # not due
        TokenFact("2", "day", _hash(2), NOW + timedelta(days=10)),  # due unless covered
        TokenFact("3", "day", _hash(3), NOW - timedelta(days=1)),  # expired
    ]
    core_renewals, standalone = _facts(RULE_CASES[name])
    core = compute_statuses(tokens, core_renewals, now=NOW, renew_before=WINDOW)
    rules = sv.RenewalRules(standalone, now=NOW)
    for token, expected in zip(tokens, core.tokens, strict=True):
        status, renewal = rules.status(token.token_sha256, token.cert_not_after)
        assert (status, renewal.record if renewal else None) == (
            expected.status,
            expected.covered_by,
        ), token.path


def test_the_expiry_boundaries_are_pinned(rw: RenewalWorld) -> None:
    """Owner answer Q16: the renewal rules take §2.1.2 literally (stamped *before* notAfter;
    expired from notAfter on), while certificate validity includes notAfter itself. Both are
    pinned here; changing either one is a decision, not a fix."""
    rw.mock_signer(CNA)
    assert rw.tsa.signer is not None
    certificate = rw.tsa.signer[0]
    assert certificate.not_valid_after_utc == CNA
    assert core_valid_at(certificate, CNA)
    assert not core_valid_at(certificate, CNA + timedelta(seconds=1))
    standalone = sv.parse_certificate(certificate.public_bytes(serialization.Encoding.DER))
    assert standalone.not_after == CNA
    assert standalone.valid_at(CNA)
    assert not standalone.valid_at(CNA + timedelta(seconds=1))
    second = timedelta(seconds=1)
    token = TokenFact("1", "day", _hash(1), NOW)
    later = NOW + timedelta(days=1)
    for offset, status in ((timedelta(0), "late"), (-second, "covered")):
        core_renewals, standalone_renewals = _facts(
            [("renewals/a.json", 10, 3 * WINDOW, offset, True, {1})]
        )
        core = compute_statuses([token], core_renewals, now=later, renew_before=WINDOW)
        rules = sv.RenewalRules(standalone_renewals, now=later)
        assert core.tokens[0].status == rules.status(_hash(1), NOW)[0] == status
    for now, status in ((NOW, "uncovered"), (NOW - second, "due")):
        core = compute_statuses([token], [], now=now, renew_before=WINDOW)
        rules = sv.RenewalRules([], now=now)
        assert core.tokens[0].status == rules.status(_hash(1), NOW)[0] == status
