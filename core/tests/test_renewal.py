"""Re-stamping on a real data directory (Wave 5 §2.1.2, D3-D5): which tokens are renewable, the
status of each from the tokens themselves, and which renewals count.

Every test starts from :func:`renewal_world.make_world`: one sealed day whose TSA certificate
expires at ``CNA``, a mock root for the days and a made-up legal CA for the renewals. The clock
is moved relative to ``CNA``; no real time-stamp server is called.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core.immutability import unlock
from arcilogger_core.renewal import NOTES, renewal_status
from arcilogger_core.timeutil import site_day
from arcilogger_core.tsa.verify import inspect_token

from .renewal_world import (
    CNA,
    DAY,
    REPORT_NAME,
    RenewalWorld,
    day_token_path,
    make_world,
)
from .test_revocation import _store

DAY_TOKEN = day_token_path()
FAR = CNA + timedelta(days=1825)


@pytest.fixture
def rw(tmp_path: Path) -> RenewalWorld:
    return make_world(tmp_path)


def _name(rw: RenewalWorld) -> str:
    return f"{site_day(rw.clock.now).isoformat()}-1"


def _renew_by_hand(rw: RenewalWorld, at: datetime, *, legal: bool = True) -> str:
    """A renewal of the day, stamped at ``at`` by a new legal-CA certificate valid until
    ``FAR``; its record path."""
    rw.set_now(at)
    provider = rw.legal_provider(FAR, claim=legal)
    name = _name(rw)
    rw.write_renewal(name, [rw.day_entry()], provider)
    return f"renewals/{name}.json"


# -- each status from real tokens ------------------------------------------------------------------


@dataclass(frozen=True)
class RealCase:
    status: str
    now: timedelta
    #: When a counting renewal is stamped, relative to CNA (None: no renewal).
    renewed: timedelta | None = None


REAL_CASES = [
    RealCase("not_due", -timedelta(days=181)),
    RealCase("due", -timedelta(days=179)),
    RealCase("uncovered", timedelta(days=1)),
    RealCase("covered", -timedelta(days=90), renewed=-timedelta(days=100)),
    RealCase("late", timedelta(days=20), renewed=timedelta(days=10)),
]


@pytest.mark.parametrize("case", REAL_CASES, ids=[case.status for case in REAL_CASES])
def test_each_status_from_real_tokens(rw: RenewalWorld, case: RealCase) -> None:
    record = _renew_by_hand(rw, CNA + case.renewed) if case.renewed is not None else None
    report = rw.status(CNA + case.now)
    day = rw.of(report, DAY_TOKEN)
    assert (day.kind, day.cert_not_after, day.status) == ("day", CNA, case.status)
    assert day.covered_by == record
    if record is None:
        assert day.renewed_at is None
        assert [token.path for token in report.tokens] == [DAY_TOKEN]
    else:
        assert case.renewed is not None
        assert day.renewed_at == CNA + case.renewed
        # the renewal's own token is renewable too, and far from due
        own = rw.of(report, f"{record}.zd")
        assert (own.kind, own.cert_not_after, own.status) == ("renewal", FAR, "not_due")
    assert report.totals[day.status] >= 1
    assert sum(report.totals.values()) == len(report.tokens)
    assert (report.unreadable, report.notes) == ((), ())


def test_the_day_token_is_the_source_of_its_certificate_date(rw: RenewalWorld) -> None:
    """``cert_not_after`` comes from the token, not from ``state/seal``."""
    state = rw.layout.seal_state(DAY)
    state.chmod(0o644)
    state.write_text("{}", encoding="utf-8")
    assert rw.of(rw.status(CNA - timedelta(days=1)), DAY_TOKEN).cert_not_after == CNA


# -- which renewals count (D5) ---------------------------------------------------------------------


def test_a_test_provider_renewal_never_counts(rw: RenewalWorld) -> None:
    """The mock provider on the legal CA (claim false: ``reason: test``) and a production claim
    on the mock root (root not listed) are both read as renewals, and neither counts."""
    rw.set_now(CNA - timedelta(days=100))
    rw.write_renewal("2031-06-23-1", [rw.day_entry()], rw.legal_provider(FAR, claim=False))
    claimed = rw.mock_provider(FAR)
    claimed.legal_grade = True
    claimed.name = "zamane"
    rw.write_renewal("2031-06-23-2", [rw.day_entry()], claimed)
    for now, status in ((CNA - timedelta(days=90), "due"), (CNA + timedelta(days=1), "uncovered")):
        report = rw.status(now)
        day = rw.of(report, DAY_TOKEN)
        assert (day.status, day.covered_by, day.renewed_at) == (status, None, None)
        # a test record's own token is not renewable (W5-A-7); the claimed one is
        assert [token.path for token in report.tokens] == [
            DAY_TOKEN,
            "renewals/2031-06-23-2.json.zd",
        ]


def test_a_renewal_counts_with_its_root_listed_only(tmp_path: Path) -> None:
    rw = make_world(tmp_path, legal_listed=False)
    _renew_by_hand(rw, CNA - timedelta(days=100))
    assert rw.of(rw.status(CNA - timedelta(days=90)), DAY_TOKEN).status == "due"


def test_a_changed_token_is_not_covered(rw: RenewalWorld) -> None:
    """A record whose entry's hash is not the token file as it is today covers nothing."""
    rw.set_now(CNA - timedelta(days=100))
    entry = rw.day_entry()
    other = entry.__class__(**{**entry.__dict__, "token_sha256": "0" * 64})
    rw.write_renewal("2031-06-23-1", [other], rw.legal_provider(FAR))
    assert rw.of(rw.status(CNA - timedelta(days=90)), DAY_TOKEN).status == "due"


def test_a_changed_record_does_not_count(rw: RenewalWorld) -> None:
    record = _renew_by_hand(rw, CNA - timedelta(days=100))
    path = rw.layout.vault / record
    path.write_bytes(path.read_bytes().replace(b'"due"', b'"due" '))
    assert rw.of(rw.status(CNA - timedelta(days=90)), DAY_TOKEN).status == "due"


def _revoking(rw: RenewalWorld, certificate: x509.Certificate, revoked_at: datetime) -> bytes:
    """A list of the legal CA revoking ``certificate`` at ``revoked_at``."""
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(rw.legal.root.subject)
        .last_update(revoked_at + timedelta(days=1))
        .next_update(revoked_at + timedelta(days=8))
        .add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(certificate.serial_number)
            .revocation_date(revoked_at)
            .build()
        )
    )
    return builder.sign(rw.legal.key, hashes.SHA256()).public_bytes(Encoding.DER)


def test_a_revoked_renewal_does_not_count(rw: RenewalWorld) -> None:
    """C15: a renewal whose certificate was revoked at or before its stamp is not legal grade,
    so it does not count; the renewal tokens are verified with the stored lists."""
    stamped = CNA - timedelta(days=100)
    record = _renew_by_hand(rw, stamped)
    now = CNA - timedelta(days=90)
    assert rw.of(rw.status(now), DAY_TOKEN).covered_by == record
    assert rw.legal_tsa.signer is not None
    _store(rw.layout, [_revoking(rw, rw.legal_tsa.signer[0], stamped)], rw.legal.root)
    day = rw.of(rw.status(now), DAY_TOKEN)
    assert (day.status, day.covered_by) == ("due", None)


def test_unreadable_revocation_lists_leave_a_note(rw: RenewalWorld) -> None:
    """Q12: the renewal tokens are then verified without lists, and the report says so."""
    record = _renew_by_hand(rw, CNA - timedelta(days=100))
    rw.layout.crl_dir.mkdir(parents=True)
    (rw.layout.crl_dir / "index.json").write_text("{", encoding="utf-8")
    report = rw.status(CNA - timedelta(days=90))
    assert rw.of(report, DAY_TOKEN).covered_by == record
    assert [note.code for note in report.notes] == ["crl_unreadable"]
    note = report.notes[0]
    assert (note.message_tr, note.message_en) == NOTES["crl_unreadable"]


# -- which tokens are renewable (D3) ---------------------------------------------------------------


def test_report_tokens_are_listed(rw: RenewalWorld) -> None:
    rw.add_report(CNA + timedelta(days=30))
    report = rw.status(CNA - timedelta(days=140))
    entry = rw.of(report, f"exports/reports/{REPORT_NAME}.zd")
    assert (entry.kind, entry.status) == ("report", "due")
    assert entry.cert_not_after == CNA + timedelta(days=30)
    assert rw.of(report, DAY_TOKEN).status == "due"


def test_deletion_tokens_are_listed(rw: RenewalWorld) -> None:
    rw.add_deletion("2026-10-20-imha.json")
    rw.add_deletion("2026-10-20-2-imha.json")
    report = rw.status(CNA - timedelta(days=1))
    assert [token.path for token in report.tokens if token.kind == "deletion"] == [
        "deletions/2026-10-20-2-imha.json.zd",
        "deletions/2026-10-20-imha.json.zd",
    ]


def test_part_files_and_chain_anchor_are_ignored(rw: RenewalWorld) -> None:
    deletions, renewals = rw.layout.deletions, rw.layout.renewals_dir
    deletions.mkdir(parents=True, exist_ok=True)
    renewals.mkdir(parents=True, exist_ok=True)
    for path in (
        deletions / "chain-anchor.json",
        deletions / "2026-10-21-imha.json.part",
        deletions / "2026-10-21-imha.json.part.zd",
        renewals / "2031-06-23-1.json.part",
        renewals / "2031-06-23-1.json.part.zd",
        renewals / ".2031-06-23-1.json.part.tmp",
    ):
        path.write_bytes(b"{}")
    report = rw.status(CNA - timedelta(days=1))
    assert [token.path for token in report.tokens] == [DAY_TOKEN]
    assert report.unreadable == ()


def test_unreadable_tokens_and_records_are_named(rw: RenewalWorld) -> None:
    """A token that cannot be parsed has no status (W5-C's reader could not read its date);
    neither has a record that is not a renewal record, nor one without its token."""
    pdf, token = rw.add_report(name="ARCILOGGER-5651-rapor-8.pdf")
    token.write_bytes(b"not a token")
    orphan, _ = rw.add_report(name="ARCILOGGER-5651-rapor-9.pdf")
    orphan.unlink()
    renewals = rw.layout.renewals_dir
    renewals.mkdir(parents=True)
    (renewals / "2031-01-01-1.json").write_text('{"schema": "other"}', encoding="utf-8")
    (renewals / "2031-01-01-2.json").write_text("{", encoding="utf-8")
    _renew_by_hand(rw, CNA - timedelta(days=100))
    (rw.layout.renewals_dir / f"{_name(rw)}.json.zd").unlink()
    report = rw.status(CNA - timedelta(days=90))
    assert report.unreadable == (
        "exports/reports/ARCILOGGER-5651-rapor-8.pdf.zd",
        "exports/reports/ARCILOGGER-5651-rapor-9.pdf.zd",
        "renewals/2031-01-01-1.json",
        "renewals/2031-01-01-2.json",
        f"renewals/{_name(rw)}.json",
    )
    assert [token.path for token in report.tokens] == [DAY_TOKEN]
    assert pdf.exists()


def test_missing_reports_folder_is_not_an_error(rw: RenewalWorld) -> None:
    assert not (rw.layout.exports / "reports").exists()
    report = rw.status(CNA - timedelta(days=1))
    assert (report.notes, report.unreadable) == ((), ())


def test_an_unreadable_reports_folder_leaves_a_note(
    rw: RenewalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``Renew`` task's account may lack the read right on ``exports\\reports`` (W5-E's
    ACL): the run goes on without the report tokens."""
    rw.add_report()
    real = os.scandir
    folder = rw.layout.exports / "reports"

    def scandir(path: Path) -> object:
        if Path(path) == folder:
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr("arcilogger_core.renewal.os.scandir", scandir)
    report = rw.status(CNA - timedelta(days=1))
    assert [note.code for note in report.notes] == ["reports_unreadable"]
    assert [token.path for token in report.tokens] == [DAY_TOKEN]


def test_a_day_without_its_token_is_not_listed(rw: RenewalWorld) -> None:
    """A manifest whose stamp is not there (yet) has nothing to renew."""
    token = rw.layout.token(DAY)
    unlock(token)
    token.rename(token.with_name("aside"))
    report = rw.status(CNA - timedelta(days=1))
    assert (report.tokens, report.unreadable) == ((), ())


def test_renewal_status_writes_nothing(rw: RenewalWorld) -> None:
    _renew_by_hand(rw, CNA - timedelta(days=100))
    rw.add_report()
    before = sorted(path for path in rw.layout.root.rglob("*") if path.is_file())
    renewal_status(rw.layout, rw.world.config, rw.roots, CNA)
    assert sorted(path for path in rw.layout.root.rglob("*") if path.is_file()) == before
    assert not (rw.layout.state / "renewal.json").exists()
    assert inspect_token(rw.layout.token(DAY).read_bytes()).tsa_cert_not_after == CNA
