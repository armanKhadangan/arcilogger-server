"""Re-stamping on a real data directory (Wave 5 §2.1.2, D3-D5): which tokens are renewable, the
status of each from the tokens themselves, which renewals count, and what one run writes.

Every test starts from :func:`renewal_world.make_world`: one sealed day whose TSA certificate
expires at ``CNA``, a mock root for the days and a made-up legal CA for the renewals. The clock
is moved relative to ``CNA``; no real time-stamp server is called.
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core import renewal as renewal_module
from arcilogger_core.canonical import canonical_json
from arcilogger_core.immutability import is_locked, lock, unlock
from arcilogger_core.renewal import (
    NOTES,
    RENEWAL_SCHEMA,
    RenewalBusyError,
    RenewalLegalRootsError,
    RenewalLockError,
    RenewalNotNeeded,
    RenewalRun,
    RenewalStampInvalidError,
    build_record,
    one_run,
    recover,
    renew,
    renewal_status,
    system_block,
    tsa_block,
)
from arcilogger_core.seal import sha256_file
from arcilogger_core.timeutil import site_day
from arcilogger_core.tsa.base import (
    TimestampProvider,
    TokenInfo,
    TsaConfigurationError,
    TsaError,
    TsaNoCreditError,
    TsaRejectedError,
    TsaResponseError,
    TsaUnavailableError,
)
from arcilogger_core.tsa.client import token_path_for
from arcilogger_core.tsa.mock import MockFailure, MockTsaProvider
from arcilogger_core.tsa.verify import inspect_token, verify_token

from .renewal_world import (
    CNA,
    DAY,
    REPORT_NAME,
    RenewalWorld,
    day_token_path,
    make_world,
)
from .test_evidence import tree_state
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


def test_a_test_record_never_counts_even_with_a_legal_stamp(rw: RenewalWorld) -> None:
    """D5 by the reason alone: a record that says ``reason: "test"`` does not count even when
    its claim is legal grade, its root is listed and its token verifies; nor is its own token
    renewed (W5-A-7)."""
    rw.set_now(CNA - timedelta(days=100))
    provider = rw.legal_provider(FAR)
    record = build_record(
        [rw.day_entry()],
        created_at=rw.clock.now,
        system=system_block(rw.world.config),
        tsa=tsa_block(provider, rw.world.config),
        reason="due",
    )
    record["reason"] = "test"  # build_record ties the reason to the claim; a hand edit does not
    path = rw.layout.renewals_dir / "2031-06-23-1.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(canonical_json(record))
    provider.stamp(path)
    for now, status in ((CNA - timedelta(days=90), "due"), (CNA + timedelta(days=1), "uncovered")):
        report = rw.status(now)
        day = rw.of(report, DAY_TOKEN)
        assert (day.status, day.covered_by) == (status, None)
        assert [token.path for token in report.tokens] == [DAY_TOKEN]


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


def test_a_changed_file_is_never_renewed(rw: RenewalWorld) -> None:
    """A token whose imprint is not the file it stamps as it is today (a changed manifest,
    deletion record, report or renewal record) has no status and is not renewed: no renewal
    lists the hash of a changed file, which the builder would then ship beside it."""
    deletion, _ = rw.add_deletion()
    pdf, _ = rw.add_report()
    renewal = _renew_by_hand(rw, CNA - timedelta(days=100))
    record = rw.layout.vault / renewal
    manifest = rw.layout.manifest(DAY)
    unlock(manifest)
    for path in (manifest, deletion, pdf, record):
        path.write_bytes(path.read_bytes() + b" ")
    rw.set_now(CNA - timedelta(days=90))
    report = rw.status()
    assert report.mismatched == (
        DAY_TOKEN,
        "deletions/2026-10-20-imha.json.zd",
        f"exports/reports/{REPORT_NAME}.zd",
        f"{renewal}.zd",
    )
    assert (report.tokens, report.unreadable) == ((), ())
    before = _renewal_files(rw)
    with pytest.raises(RenewalNotNeeded):
        _renew(rw, rw.legal_provider(FAR))
    assert _renewal_files(rw) == before


def test_an_imprint_is_compared_with_its_own_algorithm(
    rw: RenewalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SHA-384 stamp matches its file by SHA-384, while the record lists the file's SHA-256;
    an imprint algorithm the reader does not know leaves the token without a status."""
    pdf = rw.layout.exports / "reports" / REPORT_NAME
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.7\n%Ornek A.S. stored report\n%%EOF\n")
    rw.mock_signer(CNA)
    token = MockTsaProvider(rw.tsa, digest="sha384").stamp(pdf).token_path
    assert inspect_token(token.read_bytes()).digest_alg == "sha384"
    rw.set_now(CNA - timedelta(days=100))
    run = _renew(rw, rw.legal_provider(FAR))
    (entry,) = [e for e in _record(rw, run)["tokens"] if e["kind"] == "report"]
    assert entry["record_sha256"] == sha256_file(pdf)

    real = inspect_token

    def unknown(data: bytes) -> TokenInfo:
        info = real(data)
        return replace(info, digest_alg="md5") if data == token.read_bytes() else info

    monkeypatch.setattr("arcilogger_core.renewal.inspect_token", unknown)
    report = rw.status()
    assert report.unreadable == (f"exports/reports/{REPORT_NAME}.zd",)
    assert report.mismatched == ()


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


def test_a_changed_revocation_list_leaves_a_note(rw: RenewalWorld) -> None:
    """C14: a stored list that is not its index entry's file is left out, and the report names
    it rather than dropping it silently."""
    stamped = CNA - timedelta(days=100)
    record = _renew_by_hand(rw, stamped)
    assert rw.legal_tsa.signer is not None
    _store(rw.layout, [_revoking(rw, rw.legal_tsa.signer[0], stamped)], rw.legal.root)
    (stored,) = [path for path in rw.layout.crl_dir.iterdir() if path.name != "index.json"]
    stored.write_bytes(stored.read_bytes() + b"\0")
    report = rw.status(CNA - timedelta(days=90))
    assert rw.of(report, DAY_TOKEN).covered_by == record
    (note,) = report.notes
    assert note.code == "crl_mismatched"
    assert stored.name in note.message_tr
    assert note.message_en.startswith("Some stored revocation lists do not match")


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


def test_files_that_cannot_be_read_are_named(
    rw: RenewalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token, a stamped file or a record the run may not read has no status: never a
    crash, never a guess."""
    record = _renew_by_hand(rw, CNA - timedelta(days=100))
    rw.add_report()
    blocked = {
        rw.layout.token(DAY),
        rw.layout.vault / record,
        rw.layout.exports / "reports" / REPORT_NAME,
    }
    real_read_bytes, real_open = Path.read_bytes, Path.open

    def refuse(path: Path) -> None:
        if path in blocked:
            raise PermissionError(13, "Permission denied", str(path))

    def read_bytes(self: Path) -> bytes:
        refuse(self)
        return real_read_bytes(self)

    def open_(self: Path, *args: Any, **kwargs: Any) -> Any:
        refuse(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "open", open_)
    report = rw.status(CNA - timedelta(days=90))
    assert report.unreadable == (DAY_TOKEN, f"exports/reports/{REPORT_NAME}.zd", record)
    assert report.tokens == ()


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
    assert not renewal_module.state_path(rw.layout).exists()
    assert not renewal_module.state_dir(rw.layout).exists()
    assert inspect_token(rw.layout.token(DAY).read_bytes()).tsa_cert_not_after == CNA


# -- renew(): one stamped, locked record per run (D4, D5, W5-A-3) ----------------------------------


def _renew(rw: RenewalWorld, provider: TimestampProvider) -> RenewalRun:
    return renew(rw.layout, rw.world.config, provider, rw.roots, rw.clock.now)


def _record(rw: RenewalWorld, run: RenewalRun) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((rw.layout.vault / run.record_path).read_bytes())
    return data


def _renewal_files(rw: RenewalWorld) -> list[str]:
    folder = rw.layout.renewals_dir
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []


def test_at_most_one_record_per_run(rw: RenewalWorld) -> None:
    """Due tokens of every kind go into one record; a second run with nothing due writes no
    file; something newly due on the same date gets the next number."""
    deletion, deletion_token = rw.add_deletion()
    rw.add_report()
    # an earlier renewal whose own certificate comes due before the day's
    rw.set_now(CNA - timedelta(days=300))
    older = rw.write_renewal(
        "2030-12-05-1", [rw.day_entry()], rw.legal_provider(CNA + timedelta(days=10))
    )
    rw.set_now(CNA - timedelta(days=100))
    run = _renew(rw, rw.legal_provider(FAR))
    assert (run.record_path, run.token_path, run.reason) == (
        "renewals/2031-06-23-1.json",
        "renewals/2031-06-23-1.json.zd",
        "due",
    )
    record = _record(rw, run)
    assert [entry["kind"] for entry in record["tokens"]] == ["day", "deletion", "report", "renewal"]
    assert list(run.renewed) == [entry["path"] for entry in record["tokens"]]
    assert run.renewed == (
        DAY_TOKEN,
        "deletions/2026-10-20-imha.json.zd",
        f"exports/reports/{REPORT_NAME}.zd",
        "renewals/2030-12-05-1.json.zd",
    )
    assert record["tokens"][1] == {
        "kind": "deletion",
        "path": "deletions/2026-10-20-imha.json.zd",
        "token_sha256": sha256_file(deletion_token),
        "record_sha256": sha256_file(deletion),
        "cert_not_after": "2031-10-01T00:00:00Z",
    }
    assert {token.status for token in run.report_before.tokens} == {"due"}
    after = {token.path: token for token in run.report_after.tokens}
    # the older renewal counts again now that it is covered, and it is the earlier stamp
    assert {path: after[path].covered_by for path in run.renewed} == {
        **dict.fromkeys(run.renewed, run.record_path),
        DAY_TOKEN: "renewals/2030-12-05-1.json",
    }
    assert after[run.token_path].status == "not_due"
    assert _renewal_files(rw) == [
        "2030-12-05-1.json",
        "2030-12-05-1.json.zd",
        "2031-06-23-1.json",
        "2031-06-23-1.json.zd",
    ]
    assert older.exists()

    with pytest.raises(RenewalNotNeeded) as refused:
        _renew(rw, rw.legal_provider(FAR))
    assert refused.value.report.totals["due"] == 0
    assert (refused.value.message_tr, refused.value.message_en) == (
        "Yeniden damgalanması gereken damga yok.",
        "No time stamp is due for re-stamping.",
    )
    assert len(_renewal_files(rw)) == 4

    rw.add_report(CNA, name="ARCILOGGER-5651-rapor-8.pdf")
    second = _renew(rw, rw.legal_provider(FAR))
    assert second.record_path == "renewals/2031-06-23-2.json"
    assert second.renewed == ("exports/reports/ARCILOGGER-5651-rapor-8.pdf.zd",)


FAILURES = [
    (MockFailure.UNREACHABLE, TsaUnavailableError),
    (MockFailure.TIMEOUT, TsaUnavailableError),
    (MockFailure.REJECTION, TsaRejectedError),
    (MockFailure.NO_CREDIT, TsaNoCreditError),
    (MockFailure.WRONG_IMPRINT, TsaResponseError),
    (MockFailure.BAD_SIGNATURE, RenewalStampInvalidError),
]


@pytest.mark.parametrize(("failure", "error"), FAILURES, ids=[f.value for f, _ in FAILURES])
def test_a_failed_stamp_leaves_no_record(
    rw: RenewalWorld, failure: MockFailure, error: type[TsaError]
) -> None:
    rw.set_now(CNA - timedelta(days=100))
    before = rw.status()
    provider = rw.legal_provider(FAR)
    rw.legal_tsa.failure = failure
    with pytest.raises(error) as raised:
        _renew(rw, provider)
    assert raised.value.retryable
    assert _renewal_files(rw) == []
    assert rw.status() == before
    if isinstance(raised.value, RenewalStampInvalidError):
        assert raised.value.errors
        assert raised.value.message_tr.startswith("Yenileme kaydının zaman damgası")
        assert raised.value.message_en.startswith("The renewal record's time stamp")
    rw.legal_tsa.failure = MockFailure.NONE
    assert _renew(rw, provider).record_path == "renewals/2031-06-23-1.json"


def test_any_failure_of_the_provider_leaves_no_part(
    rw: RenewalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not only a TsaError: whatever the provider raises after writing its token, both parts
    are removed, so a vault copy never holds a file the standalone verifier does not know."""
    rw.set_now(CNA - timedelta(days=100))
    provider = rw.legal_provider(FAR)
    real = provider.stamp

    def stamp_then_fail(path: Path) -> Any:
        real(path)
        assert token_path_for(path).exists()
        raise ValueError("a provider bug")

    monkeypatch.setattr(provider, "stamp", stamp_then_fail)
    with pytest.raises(ValueError, match="a provider bug"):
        _renew(rw, provider)
    assert _renewal_files(rw) == []


def test_a_status_after_the_run_that_cannot_be_read_keeps_the_renewal(
    rw: RenewalWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """W5-A-15: once the record is written, a file error while reading the status after the
    run does not undo the run: ``report_after`` is the status before it, with a warning."""
    rw.set_now(CNA - timedelta(days=100))

    def unreadable(*args: Any, **kwargs: Any) -> Any:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(renewal_module, "renewal_status", unreadable)
    run = _renew(rw, rw.legal_provider(FAR))
    assert (run.record_path, run.reason, run.renewed) == (
        "renewals/2031-06-23-1.json",
        "due",
        (DAY_TOKEN,),
    )
    assert run.report_after == run.report_before
    assert rw.of(run.report_after, DAY_TOKEN).status == "due"
    assert "the status after renewals/2031-06-23-1.json cannot be read" in caplog.text
    assert "Input/output error" in caplog.text
    assert _renewal_files(rw) == ["2031-06-23-1.json", "2031-06-23-1.json.zd"]
    assert all(is_locked(rw.layout.vault / path) for path in (run.record_path, run.token_path))
    monkeypatch.undo()
    assert rw.of(rw.status(), DAY_TOKEN).covered_by == run.record_path


def test_a_lock_that_cannot_be_taken_is_not_called_busy(
    rw: RenewalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a lock another run holds is ``renewal_busy``; any other failure of the lock file is
    ``renewal_lock_failed``, with TR/EN texts."""

    def no_locks(handle: int) -> None:
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr("arcilogger_core.renewal._lock", no_locks)
    with pytest.raises(RenewalLockError) as failed, one_run(rw.layout):
        pass
    assert not isinstance(failed.value, RenewalBusyError)
    assert failed.value.code == "renewal_lock_failed"
    assert failed.value.message_tr.startswith("state/renewal/renewal.lock açılamadı")
    assert failed.value.message_en.startswith("state/renewal/renewal.lock cannot be opened")
    monkeypatch.undo()

    real_open = os.open
    lock_file = rw.layout.state / "renewal" / "renewal.lock"

    def refuse(path: Any, *args: Any, **kwargs: Any) -> int:
        if Path(path) == lock_file:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("arcilogger_core.renewal.os.open", refuse)
    with pytest.raises(RenewalLockError, match="Permission denied"):
        _renew(rw, rw.legal_provider(FAR))


def _unverifiable(rw: RenewalWorld, case: str) -> TimestampProvider:
    """A legal provider whose stamp does not verify: its certificate revoked before now (in the
    stored lists, C15), or expired before the stamp."""
    now = rw.clock.now
    if case == "expired":
        return rw.legal_provider(now - timedelta(days=2), not_before=now - timedelta(days=30))
    provider = rw.legal_provider(FAR, not_before=now - timedelta(days=30))
    assert rw.legal_tsa.signer is not None
    _store(
        rw.layout, [_revoking(rw, rw.legal_tsa.signer[0], now - timedelta(days=5))], rw.legal.root
    )
    return provider


@pytest.mark.parametrize("case", ["revoked", "expired"])
def test_a_stamp_that_does_not_verify_leaves_no_record(rw: RenewalWorld, case: str) -> None:
    """The new token is verified with the stored revocation lists and at its genTime before
    anything is renamed: a revoked or expired certificate leaves no record."""
    rw.set_now(CNA - timedelta(days=100))
    provider = _unverifiable(rw, case)
    before = rw.status()
    with pytest.raises(RenewalStampInvalidError) as raised:
        _renew(rw, provider)
    expected = "revoked at or before the stamp" if case == "revoked" else "not valid at genTime"
    assert any(expected in error for error in raised.value.errors), raised.value.errors
    assert _renewal_files(rw) == []
    assert rw.status() == before


def _counted(provider: TimestampProvider, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """The files ``provider`` is asked to stamp from now on."""
    calls: list[Path] = []
    real = provider.stamp

    def stamp(path: Path) -> Any:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(provider, "stamp", stamp)
    return calls


@pytest.mark.parametrize("case", ["nothing listed", "listed root not pinned"])
def test_a_legal_claim_without_a_listed_root_takes_no_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """W5-A-13: a provider that claims legal grade while no pinned root is listed in
    ``tsa.legal_root_fingerprints`` would write renewals that never count (D5), each one a
    spent credit (D4): the run refuses before the provider is called, as a configuration error
    (exit 2, cause ``legal-roots``), and leaves nothing behind."""
    rw = make_world(tmp_path, legal_listed=case != "nothing listed")
    roots = rw.roots if case == "nothing listed" else [rw.tsa.root_certificate]
    rw.set_now(CNA - timedelta(days=100))
    provider = rw.legal_provider(FAR)
    calls = _counted(provider, monkeypatch)
    before = renewal_status(rw.layout, rw.world.config, roots, rw.clock.now)
    with pytest.raises(RenewalLegalRootsError) as refused:
        renew(rw.layout, rw.world.config, provider, roots, rw.clock.now)
    assert isinstance(refused.value, TsaConfigurationError)
    assert not refused.value.retryable
    assert refused.value.cause == "legal-roots"
    assert "tsa.legal_root_fingerprints" in refused.value.message_tr
    assert "tsa.legal_root_fingerprints" in refused.value.message_en
    assert refused.value.message_tr.startswith("Sağlayıcı yasal geçerli damga beyan ediyor")
    assert refused.value.message_en.startswith("The provider claims legal grade")
    assert calls == []
    assert _renewal_files(rw) == []
    assert renewal_status(rw.layout, rw.world.config, roots, rw.clock.now) == before


def test_without_a_listed_root_only_a_stamp_that_would_be_taken_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing due is still ``RenewalNotNeeded`` (no credit is spent), and a provider that
    claims no legal grade still writes its ``reason: "test"`` record (D5)."""
    rw = make_world(tmp_path, legal_listed=False)
    with pytest.raises(RenewalNotNeeded):
        _renew(rw, rw.legal_provider(FAR))
    rw.set_now(CNA - timedelta(days=100))
    provider = rw.legal_provider(FAR, claim=False)
    calls = _counted(provider, monkeypatch)
    run = _renew(rw, provider)
    assert (run.reason, len(calls)) == ("test", 1)
    assert _renewal_files(rw) == ["2031-06-23-1.json", "2031-06-23-1.json.zd"]


def test_the_token_is_renamed_before_the_record(
    rw: RenewalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W5-A-3: a run stopped between the two renames leaves the final token beside the part,
    never a record without its token; the next run completes it."""
    rw.set_now(CNA - timedelta(days=100))
    real = renewal_module._replace
    calls: list[Path] = []

    def stop_at_second(source: Path, target: Path) -> None:
        calls.append(target)
        if len(calls) == 2:
            raise OSError("stopped between the renames")
        real(source, target)

    monkeypatch.setattr(renewal_module, "_replace", stop_at_second)
    with pytest.raises(OSError, match="between the renames"):
        _renew(rw, rw.legal_provider(FAR))
    assert [target.name for target in calls] == ["2031-06-23-1.json.zd", "2031-06-23-1.json"]
    assert _renewal_files(rw) == ["2031-06-23-1.json.part", "2031-06-23-1.json.zd"]
    monkeypatch.undo()
    with pytest.raises(RenewalNotNeeded):  # completed by the next run, and it covers the day
        _renew(rw, rw.legal_provider(FAR))
    names = ["2031-06-23-1.json", "2031-06-23-1.json.zd"]
    assert _renewal_files(rw) == names
    assert all(is_locked(rw.layout.renewals_dir / name) for name in names)
    assert rw.of(rw.status(), DAY_TOKEN).covered_by == "renewals/2031-06-23-1.json"


def test_a_crash_between_stamp_and_rename_is_recovered(rw: RenewalWorld) -> None:
    """The next run promotes a part whose token verifies (stamped, or already renamed) and
    removes every other leftover: no record without its token, no file left to confuse the
    builder or the standalone verifier's vault mode."""
    rw.set_now(CNA - timedelta(days=100))
    folder = rw.layout.renewals_dir
    folder.mkdir(parents=True)
    provider = rw.legal_provider(FAR)
    record = canonical_json(
        build_record(
            [rw.day_entry()],
            created_at=rw.clock.now,
            system=system_block(rw.world.config),
            tsa=tsa_block(provider, rw.world.config),
            reason="due",
        )
    )
    # stopped between the two renames: the part and the final token
    (folder / "2031-06-20-1.json.part").write_bytes(record)
    provider.stamp(folder / "2031-06-20-1.json.part")
    (folder / "2031-06-20-1.json.part.zd").rename(folder / "2031-06-20-1.json.zd")
    # stopped after the stamp: the part and its token
    (folder / "2031-06-21-1.json.part").write_bytes(record)
    provider.stamp(folder / "2031-06-21-1.json.part")
    # a part never stamped, a token whose part does not match, a lone token, a temporary file
    (folder / "2031-06-22-1.json.part").write_bytes(record)
    (folder / "2031-06-22-2.json.part").write_bytes(record + b" ")
    (folder / "2031-06-22-2.json.part.zd").write_bytes(
        (folder / "2031-06-20-1.json.zd").read_bytes()
    )
    (folder / "2031-06-22-3.json.part.zd").write_bytes(b"token")
    (folder / "2031-06-22-4.json.zd").write_bytes(b"token")
    (folder / ".2031-06-22-5.json.part.tmp").write_bytes(record)

    assert recover(rw.layout, rw.roots, now=rw.clock.now) == [
        "2031-06-22-1.json.part",
        "2031-06-22-2.json.part.zd",
        "2031-06-22-2.json.part",
        "2031-06-22-3.json.part.zd",
        "2031-06-22-4.json.zd",
        ".2031-06-22-5.json.part.tmp",
    ]
    assert _renewal_files(rw) == [
        "2031-06-20-1.json",
        "2031-06-20-1.json.zd",
        "2031-06-21-1.json",
        "2031-06-21-1.json.zd",
    ]
    assert all(is_locked(folder / name) for name in _renewal_files(rw))
    assert recover(rw.layout, rw.roots, now=rw.clock.now) == []
    with pytest.raises(RenewalNotNeeded):  # the promoted records cover the day
        _renew(rw, provider)
    assert rw.of(rw.status(), DAY_TOKEN).covered_by == "renewals/2031-06-20-1.json"


def _stop_after_stamp(
    rw: RenewalWorld, provider: TimestampProvider, monkeypatch: pytest.MonkeyPatch, stopped: str
) -> Path:
    """A run whose process stops right after ``provider``'s stamp, before the token is checked
    (and so before any clean-up), or after the first rename too; the token it leaves."""
    names: list[str] = []

    def stop(data: bytes, *, file_path: Path, **kwargs: Any) -> Any:
        names.append(file_path.name)
        raise SystemExit("the process stopped before the check")

    with monkeypatch.context() as patched:
        patched.setattr(renewal_module, "verify_token", stop)
        patched.setattr(renewal_module, "_remove", lambda path, removed: None)  # no clean-up
        with pytest.raises(SystemExit):
            _renew(rw, provider)
    part = rw.layout.renewals_dir / names[0]
    token = token_path_for(part)
    if stopped == "between the renames":
        token = token.rename(token_path_for(part.with_name(part.name.removesuffix(".part"))))
    return token


@pytest.mark.parametrize("stopped", ["after the stamp", "between the renames"])
def test_a_revoked_stamp_of_an_interrupted_run_is_removed(
    rw: RenewalWorld,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stopped: str,
) -> None:
    """C15: a run stopped between the stamp and its revocation check leaves a part whose token
    verifies, while the stored lists show its certificate revoked at or before the stamp. The
    next run checks it with those lists, as the stamping run would have, and removes it instead
    of promoting it: a revoked new stamp leaves no record."""
    rw.set_now(CNA - timedelta(days=100))
    provider = _unverifiable(rw, "revoked")
    before = rw.status()
    token = _stop_after_stamp(rw, provider, monkeypatch, stopped)
    part = rw.layout.renewals_dir / "2031-06-23-1.json.part"
    assert _renewal_files(rw) == sorted([part.name, token.name])
    unchecked = verify_token(token.read_bytes(), roots=rw.roots, file_path=part, now=rw.clock.now)
    assert unchecked.ok  # only the revocation lists refuse it

    assert recover(rw.layout, rw.roots, now=rw.clock.now) == [token.name, part.name]
    assert _renewal_files(rw) == []
    assert rw.status() == before
    assert "revoked at or before the stamp" in caplog.text

    # the next run tidies the same leftover first, then stamps with a certificate not revoked
    token = _stop_after_stamp(rw, provider, monkeypatch, stopped)
    run = _renew(rw, rw.legal_provider(FAR))
    assert run.discarded == (token.name, part.name)
    assert run.record_path == "renewals/2031-06-23-1.json"
    assert _renewal_files(rw) == ["2031-06-23-1.json", "2031-06-23-1.json.zd"]
    assert rw.of(rw.status(), DAY_TOKEN).covered_by == run.record_path


def test_a_renewal_keeps_the_eku_rule_its_record_was_written_under(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#137 item 4 (W5-A-17): a renewal token is judged by its own record's EKU rule, not by
    the site's setting when it is read. A site that relaxed ``tsa.require_critical_eku`` (K6)
    stamps with a certificate whose EKU is not critical, the run stops after the stamp, and the
    site turns the rule strict again: the next run still completes the part (its record says
    relaxed), and the record covers the day."""
    rw = make_world(tmp_path, require_critical_eku=False)
    rw.set_now(CNA - timedelta(days=100))
    provider = rw.legal_provider(FAR, eku_critical=False)
    token = _stop_after_stamp(rw, provider, monkeypatch, "after the stamp")
    part = rw.layout.renewals_dir / "2031-06-23-1.json.part"
    assert json.loads(part.read_bytes())["tsa"]["require_critical_eku"] is False
    strict = verify_token(token.read_bytes(), roots=rw.roots, file_path=part, now=rw.clock.now)
    assert not strict.ok  # the strict rule refuses it
    config = rw.world.config
    tsa = config.tsa.model_copy(update={"require_critical_eku": True})
    rw.world.config = config.model_copy(update={"tsa": tsa})

    assert recover(rw.layout, rw.roots, now=rw.clock.now) == []
    assert _renewal_files(rw) == ["2031-06-23-1.json", "2031-06-23-1.json.zd"]
    assert rw.of(rw.status(), DAY_TOKEN).covered_by == "renewals/2031-06-23-1.json"
    with pytest.raises(RenewalNotNeeded):
        _renew(rw, rw.legal_provider(FAR))


def test_recover_never_removes_a_locked_file(rw: RenewalWorld) -> None:
    """A locked token without its record is what is left when someone removed a completed
    record: evidence of that, so it stays (rule 2; only the retention job deletes)."""
    rw.set_now(CNA - timedelta(days=100))
    run = _renew(rw, rw.legal_provider(FAR))
    record, token = rw.layout.vault / run.record_path, rw.layout.vault / run.token_path
    unlock(record)
    record.unlink()
    leftover = rw.layout.renewals_dir / "2031-06-22-1.json.part"
    leftover.write_bytes(b"{}")
    lock(leftover)
    assert recover(rw.layout, rw.roots, now=rw.clock.now) == []
    assert (token.exists(), is_locked(token), leftover.exists()) == (True, True, True)


def test_a_renewal_left_unlocked_is_locked_by_the_next_run(
    rw: RenewalWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``lock`` failing after both renames (icacls on Windows) leaves a complete pair unlocked:
    the next run locks it, as the seal locks every file of its day; a lock that fails again is
    logged and tried by the run after."""
    rw.set_now(CNA - timedelta(days=100))

    def refuse(path: Path) -> None:
        raise subprocess.CalledProcessError(5, ["icacls", str(path)])

    monkeypatch.setattr("arcilogger_core.renewal.lock", refuse)
    with pytest.raises(subprocess.CalledProcessError):
        _renew(rw, rw.legal_provider(FAR))
    names = ["2031-06-23-1.json", "2031-06-23-1.json.zd"]
    assert _renewal_files(rw) == names
    assert recover(rw.layout, rw.roots, now=rw.clock.now) == []
    assert not any(is_locked(rw.layout.renewals_dir / name) for name in names)
    monkeypatch.undo()
    with pytest.raises(RenewalNotNeeded):  # the record counts, unlocked or not
        _renew(rw, rw.legal_provider(FAR))
    assert all(is_locked(rw.layout.renewals_dir / name) for name in names)


def test_a_renewal_of_a_renewal_three_deep(rw: RenewalWorld) -> None:
    """Each renewal's certificate comes due in turn. The next record lists the older renewal's
    token and repeats every hash it protected (D2), and the day (and the report) stays covered
    by the first renewal through the chain."""
    rw.add_report()
    day_hashes = {sha256_file(rw.layout.token(DAY)), sha256_file(rw.layout.manifest(DAY))}
    report_token = rw.layout.exports / "reports" / f"{REPORT_NAME}.zd"
    report_hashes = {
        sha256_file(report_token),
        sha256_file(report_token.with_name(REPORT_NAME)),
    }
    first_expiry = CNA + timedelta(days=300)
    second_expiry = first_expiry + timedelta(days=400)
    runs: list[RenewalRun] = []
    for stamped, expires in (
        (CNA - timedelta(days=100), first_expiry),
        (first_expiry - timedelta(days=100), second_expiry),
        (second_expiry - timedelta(days=100), FAR + timedelta(days=1000)),
    ):
        rw.set_now(stamped)
        runs.append(_renew(rw, rw.legal_provider(expires)))
        report = runs[-1].report_after
        assert rw.of(report, DAY_TOKEN).covered_by == runs[0].record_path
        assert rw.of(report, f"exports/reports/{REPORT_NAME}.zd").covered_by == runs[0].record_path
    first, second, third = runs
    for older, newer in ((first, second), (second, third)):
        (entry,) = [e for e in _record(rw, newer)["tokens"] if e["path"] == older.token_path]
        assert entry["kind"] == "renewal"
        assert entry["record_sha256"] == sha256_file(rw.layout.vault / older.record_path)
        assert entry["token_sha256"] == sha256_file(rw.layout.vault / older.token_path)
        assert day_hashes | report_hashes <= set(entry["protects"])
    assert [e["kind"] for e in _record(rw, third)["tokens"]] == [
        "day",
        "report",
        "renewal",
        "renewal",
    ]
    final = third.report_after
    assert rw.of(final, first.token_path).covered_by == second.record_path
    assert rw.of(final, second.token_path).covered_by == third.record_path
    assert rw.of(final, third.token_path).status == "not_due"
    assert {token.status for token in final.tokens} == {"covered", "not_due"}
    # the day's raw text match (the builder's) finds every link of the chain
    for run in runs:
        text = (rw.layout.vault / run.record_path).read_text(encoding="utf-8")
        assert all(value in text for value in day_hashes)


def test_report_tokens_are_renewed(rw: RenewalWorld) -> None:
    pdf, token = rw.add_report()
    rw.set_now(CNA - timedelta(days=100))
    run = _renew(rw, rw.legal_provider(FAR))
    (entry,) = [e for e in _record(rw, run)["tokens"] if e["kind"] == "report"]
    assert entry == {
        "kind": "report",
        "path": f"exports/reports/{REPORT_NAME}.zd",
        "token_sha256": sha256_file(token),
        "record_sha256": sha256_file(pdf),
        "cert_not_after": "2031-10-01T00:00:00Z",
    }
    report = rw.of(run.report_after, entry["path"])
    assert (report.status, report.covered_by) == ("covered", run.record_path)


def test_a_test_provider_renewal_is_written_with_reason_test_and_never_counts(
    rw: RenewalWorld,
) -> None:
    """D5: the mock provider's record is written and stamped, with ``reason: "test"``; nothing
    it lists is covered, so the next run stamps again (contract question Q5c)."""
    rw.set_now(CNA - timedelta(days=100))
    run = _renew(rw, rw.legal_provider(FAR, claim=False))
    record = _record(rw, run)
    assert (record["schema"], record["reason"], run.reason) == (RENEWAL_SCHEMA, "test", "test")
    assert record["tsa"] == {"provider": "mock", "legal_grade": False, "digest": "sha256"}
    day = rw.of(run.report_after, DAY_TOKEN)
    assert (day.status, day.covered_by) == ("due", None)
    assert [token.path for token in run.report_after.tokens] == [DAY_TOKEN]
    assert _renew(rw, rw.legal_provider(FAR, claim=False)).record_path.endswith("-2.json")
    rw.set_now(CNA + timedelta(days=1))
    assert rw.of(rw.status(), DAY_TOKEN).status == "uncovered"


def test_renewal_files_are_locked(rw: RenewalWorld) -> None:
    rw.set_now(CNA - timedelta(days=100))
    run = _renew(rw, rw.legal_provider(FAR))
    for path in (run.record_path, run.token_path):
        assert is_locked(rw.layout.vault / path)
    assert (rw.layout.state / "renewal" / "renewal.lock").exists()
    assert not (rw.layout.state / "renewal.lock").exists()


def test_sealed_files_are_never_touched(rw: RenewalWorld) -> None:
    """Rule 2: a run adds files under ``vault/renewals`` only."""
    rw.add_deletion()
    rw.add_report()
    vault_day, deletions = rw.layout.vault_day(DAY), rw.layout.deletions
    reports = rw.layout.exports / "reports"
    before = [tree_state(folder) for folder in (vault_day, deletions, reports)]
    rw.set_now(CNA + timedelta(days=5))
    run = _renew(rw, rw.legal_provider(FAR))
    assert rw.of(run.report_after, DAY_TOKEN).status == "late"
    assert [tree_state(folder) for folder in (vault_day, deletions, reports)] == before
    assert sorted(
        path.relative_to(rw.layout.vault).as_posix()
        for path in rw.layout.vault.rglob("*")
        if path.is_file() and not (path.is_relative_to(vault_day) or path.is_relative_to(deletions))
    ) == [run.record_path, run.token_path]


def test_a_second_run_while_one_holds_the_lock_is_refused(rw: RenewalWorld) -> None:
    rw.set_now(CNA - timedelta(days=100))
    provider = rw.legal_provider(FAR)
    with one_run(rw.layout), pytest.raises(RenewalBusyError) as busy:
        _renew(rw, provider)
    assert "state/renewal/renewal.lock" in busy.value.message_tr
    assert "state/renewal/renewal.lock" in busy.value.message_en
    assert _renewal_files(rw) == []
    assert _renew(rw, provider).reason == "due"


def test_nothing_due_writes_nothing(rw: RenewalWorld) -> None:
    with pytest.raises(RenewalNotNeeded):
        _renew(rw, rw.legal_provider(FAR))
    assert _renewal_files(rw) == []
