"""Evidence packages carry the renewals of their days, of their deletion records and of their
report (Wave 5 D1-D3, §3).

The builder ships every ``vault/renewals/*.json`` whose raw text holds a packaged day's manifest
or token hash, the packaged report's token hash (D1, D3) or a packaged deletion record's token
hash (contract W5-A-18).
A renewal of a renewal repeats the hashes the older one protected (``protects``, D2), so a
package of one day picks up the whole chain. The stamps here are made in October 2026, before
the real date: the builder's self-check verifies the finished package at the real time. R3's
certificate runs to 2069, so the chain covers the day at any real date the mock root (ten years
from October 2026) is still valid; the fixture says when that is no longer so.
"""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization

from arcilogger_core.evidence.builder import (
    _collect_days,
    _Records,
    build_package,
    requires_critical_eku,
)
from arcilogger_core.evidence.model import EvidenceError, PackageInfo
from arcilogger_core.renewal import RecordEntry, _parse_record, renew
from arcilogger_core.seal import sha256_file
from arcilogger_core.timeutil import utc_now

from .renewal_cases import (
    DELETION_CASES,
    LEVELS,
    TAMPERINGS,
    Built,
    DeletionCase,
    core_verdict,
    entry_of,
    renew_day,
    standalone_report,
    tampered_renewal,
)
from .renewal_world import CNA, DAY, REPORT_NAME, RenewalWorld, day_token_path, make_world
from .test_evidence import findings, in_process, isolated, unlock_tree, unpack

#: The day of the package; its stamp's certificate expires in March 2027.
OTHER = DAY + timedelta(days=1)
OTHER_EXPIRES = datetime(2027, 3, 1, tzinfo=UTC)
#: The chain: R1 renews the day's token, R2 R1's, R3 R2's; each before the certificate it
#: renews expires, and each certificate but R3's within 180 days of December 2026. R3's runs
#: to 2069 (inside the legal root), so it is not due at any real date the tests run at.
R1 = ("2026-10-15-1", datetime(2026, 10, 15, 9, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
R2 = ("2026-10-16-1", datetime(2026, 10, 16, 9, tzinfo=UTC), datetime(2027, 2, 1, tzinfo=UTC))
R3 = ("2026-10-17-1", datetime(2026, 10, 17, 9, tzinfo=UTC), datetime(2069, 1, 1, tzinfo=UTC))
#: When the whole chain is needed for the day to be covered.
JUDGED = datetime(2026, 12, 1, tzinfo=UTC)
#: The deletion record's renewal (stamped as R1) runs a month short of the record's certificate
#: (:data:`CNA`); two months before that, both are due and only the renewal of the renewal
#: (stamped as R2, to 2069) lets it cover the record's token.
D1_EXPIRES = datetime(2031, 9, 1, tzinfo=UTC)
DELETION_JUDGED = datetime(2031, 8, 1, tzinfo=UTC)


@pytest.fixture
def rw(tmp_path: Path) -> RenewalWorld:
    world = make_world(tmp_path / "site")
    assert utc_now() > R3[1] + timedelta(days=1), (
        "the test assumes its October 2026 stamps are past"
    )
    # the package is verified at the real time, and the days' tokens chain to the mock root
    expires = world.tsa.root_certificate.not_valid_after_utc
    assert utc_now() < expires, (
        f"the test assumes the mock root (until {expires:%Y-%m-%d}) is valid"
    )
    return world


def _build(rw: RenewalWorld, days: list[date], out: Path, **kwargs: Any) -> PackageInfo:
    return build_package(
        rw.layout,
        days,
        roots=rw.roots,
        legal_root_fingerprints=[rw.legal.fingerprint],
        out_dir=out,
        clock=lambda: datetime(2026, 10, 20, 9, tzinfo=UTC),
        **kwargs,
    )


def _roots(rw: RenewalWorld, folder: Path) -> Path:
    folder.mkdir()
    for number, root in enumerate(rw.roots):
        (folder / f"root-{number}.pem").write_bytes(root.public_bytes(serialization.Encoding.PEM))
    return folder


def _records(info: PackageInfo, folder: Path) -> list[str]:
    """The renewal files in the package's ZIP (unpacked to ``folder`` for the verifiers); the
    builder's ``paket.json`` lists the same."""
    with zipfile.ZipFile(info.path) as archive:
        names = sorted(n for n in archive.namelist() if n.startswith("vault/renewals/"))
    folder = unpack(info, folder)
    listed = json.loads((folder / "paket.json").read_bytes())["records"]
    assert sorted(n for n in listed if n.startswith("vault/renewals/")) == names
    return names


def _chain(rw: RenewalWorld) -> list[str]:
    """R1, R2 and R3 written by hand: R2 lists only R1's token and R3 only R2's, so the day's
    hashes reach them only through ``protects`` (D2). Their record paths."""
    paths: list[str] = []
    entries: list[RecordEntry] = [rw.day_entry(OTHER)]
    for name, at, expires in (R1, R2, R3):
        rw.set_now(at)
        rw.write_renewal(name, entries, rw.legal_provider(expires))
        paths.append(f"renewals/{name}.json")
        entries = [entry_of(rw, paths[-1])]
    return paths


def test_the_builder_picks_up_a_three_deep_chain_for_one_day(
    rw: RenewalWorld, tmp_path: Path
) -> None:
    """§3: a package of one day ships R1, R2 and R3 with their tokens, and the standalone
    verifier on it finds the day covered by R1 through the chain."""
    rw.seal(OTHER, OTHER_EXPIRES)
    paths = _chain(rw)
    day_token = sha256_file(rw.layout.token(OTHER))
    for path in paths[1:]:  # only R1 lists the day's token itself
        record = json.loads((rw.layout.vault / path).read_bytes())
        assert day_token not in {entry["token_sha256"] for entry in record["tokens"]}
        assert day_token in record["tokens"][0]["protects"]
    info = _build(rw, [OTHER], tmp_path / "out")
    folder = tmp_path / "pkg"
    assert _records(info, folder) == sorted(
        f"vault/{path}{suffix}" for path in paths for suffix in ("", ".zd")
    )
    roots = _roots(rw, tmp_path / "kokler")
    report = in_process(folder, roots_dir=roots, now=JUDGED)
    assert report.passed, report.codes()
    found = [f for f in report.findings if f.code.startswith("renewal-")]
    assert (
        sorted(f.code for f in found if f.code in ("renewal-ok", "renewal-covers"))
        == ["renewal-covers"] * 3 + ["renewal-ok"] * 3
    )
    (day,) = [f for f in found if f.where == OTHER.isoformat()]
    assert (day.level, day.code) == ("ok", "renewal-covered")
    assert day.en == f"Re-stamped before its certificate expired: {paths[0]}."
    # the core says the same of the vault at that time
    status = rw.status(JUDGED)
    assert rw.of(status, f"{OTHER:%Y/%m/%d}/manifest-{OTHER}.json.zd").covered_by == paths[0]
    # the auditor's way, at the real time: the chain still covers the day
    code, isolated_report = isolated(folder, "--kokler", str(roots))
    assert code == 0
    (covered,) = findings(isolated_report, "renewal-covered")
    assert covered["where"] == OTHER.isoformat()
    # a package of the other day carries none of them
    assert _records(_build(rw, [DAY], tmp_path / "out-day"), tmp_path / "pkg-day") == []
    unlock_tree(tmp_path)


def test_a_package_with_the_report_ships_the_report_renewal(
    rw: RenewalWorld, tmp_path: Path
) -> None:
    """§3 and D3: ``renew`` renews a stored report's token; a package with that report (and its
    token) ships the renewal, one of the same day without the report does not."""
    pdf, token = rw.add_report(cert_not_after=datetime(2027, 1, 15, tzinfo=UTC))
    at = datetime(2026, 10, 20, 9, tzinfo=UTC)
    rw.set_now(at)
    run = renew(rw.layout, rw.world.config, rw.legal_provider(R3[2]), rw.roots, at)
    report_token = f"exports/reports/{REPORT_NAME}.zd"
    assert run.renewed == (report_token,)  # the day's certificate is not due
    report = rw.status(JUDGED)
    assert rw.of(report, report_token).covered_by == run.record_path
    info = _build(
        rw,
        [DAY],
        tmp_path / "out",
        report_pdf=pdf.read_bytes(),
        report_name=REPORT_NAME,
        report_token=token.read_bytes(),
    )
    folder = tmp_path / "pkg"
    assert _records(info, folder) == [f"vault/{run.record_path}", f"vault/{run.token_path}"]
    code, verified = isolated(folder, "--kokler", str(_roots(rw, tmp_path / "kokler")))
    assert code == 0
    assert {f["code"] for f in verified["findings"]} >= {"report-ok", "renewal-ok"}
    (covers,) = findings(verified, "renewal-covers")
    assert covers["where"] == "yenileme"
    # without the report the renewal names nothing of the package
    assert _records(_build(rw, [DAY], tmp_path / "out-plain"), tmp_path / "pkg-plain") == []
    unlock_tree(tmp_path)


def test_a_package_with_a_deletion_record_ships_its_renewals(
    rw: RenewalWorld, tmp_path: Path
) -> None:
    """D3 and W5-A-18: a deletion record's token is renewed like a day's; a package that ships
    the record (the next day's manifest lists it) ships the renewals that list its token hash,
    the renewal of that renewal too (D2), with their tokens. The standalone verifier checks
    them as renewals and judges only the days (§2.1.4)."""
    deletion, deletion_token = rw.add_deletion("2026-10-13-imha.json")
    rw.seal(OTHER)  # its certificate runs to CNA here: not due at any real date
    manifest = json.loads(rw.layout.manifest(OTHER).read_bytes())
    assert [entry["path"] for entry in manifest["deletions"]] == ["deletions/2026-10-13-imha.json"]
    # the first renewal lists only the record's token, the second only the first's (whose
    # ``protects`` repeats it)
    entry = RecordEntry(
        "deletion",
        "deletions/2026-10-13-imha.json.zd",
        sha256_file(deletion_token),
        CNA,
        record_sha256=sha256_file(deletion),
    )
    rw.set_now(R1[1])
    rw.write_renewal(R1[0], [entry], rw.legal_provider(D1_EXPIRES))
    rw.set_now(R2[1])
    rw.write_renewal(R2[0], [entry_of(rw, f"renewals/{R1[0]}.json")], rw.legal_provider(R3[2]))
    paths = [f"renewals/{R1[0]}.json", f"renewals/{R2[0]}.json"]
    second = json.loads((rw.layout.vault / paths[1]).read_bytes())
    assert sha256_file(deletion_token) not in {e["token_sha256"] for e in second["tokens"]}
    assert sha256_file(deletion_token) in second["tokens"][0]["protects"]
    # the core: near the end of the record's certificate, the first covers it through the second
    status = rw.status(DELETION_JUDGED)
    assert rw.of(status, entry.path).covered_by == paths[0]
    info = _build(rw, [OTHER], tmp_path / "out")
    folder = tmp_path / "pkg"
    assert _records(info, folder) == sorted(
        f"vault/{path}{suffix}" for path in paths for suffix in ("", ".zd")
    )
    index = json.loads((folder / "paket.json").read_bytes())
    assert [n for n in index["records"] if n.startswith("vault/deletions/")] == [
        "vault/deletions/2026-10-13-imha.json",
        "vault/deletions/2026-10-13-imha.json.zd",
    ]
    code, verified = isolated(folder, "--kokler", str(_roots(rw, tmp_path / "kokler")))
    assert code == 0, verified["findings"]
    assert {"deletion-ok", "renewal-ok"} <= {f["code"] for f in verified["findings"]}
    assert [f["where"] for f in findings(verified, "renewal-ok")] == ["yenileme"] * 2
    assert [f["where"] for f in findings(verified, "renewal-covers")] == ["yenileme"] * 2
    # the day is judged by its own token, which no renewal lists
    (day,) = [
        f
        for f in verified["findings"]
        if f["code"].startswith("renewal-") and f["where"] == OTHER.isoformat()
    ]
    assert day["code"] == "renewal-not-due"
    # a package without the deletion record carries none of them
    assert _records(_build(rw, [DAY], tmp_path / "out-day"), tmp_path / "pkg-day") == []
    unlock_tree(tmp_path)


def test_a_relaxed_renewal_under_the_legal_root_is_packaged_and_judged_alike(
    rw: RenewalWorld, tmp_path: Path
) -> None:
    """W5-A-17: the builder picks the root of a renewal's token with its own record's EKU rule,
    as both verifiers judge it. A day sealed strict, renewed with a non-critical EKU under the
    legal CA's root (which no packaged day uses) by a record that says the site relaxed the
    rule: the package ships that root and builds, and the standalone verifier on it and the
    core on the vault find the day covered by the renewal, with the same texts (rule 5)."""
    manifest = json.loads(rw.layout.manifest(DAY).read_bytes())
    assert "require_critical_eku" not in manifest["tsa"]
    record = renew_day(rw, R1[0], R1[1], expires=R3[2], eku_critical=False, relaxed=True)
    assert (
        json.loads((rw.layout.vault / record).read_bytes())["tsa"]["require_critical_eku"] is False
    )
    info = _build(rw, [DAY], tmp_path / "out")
    folder = tmp_path / "pkg"
    assert _records(info, folder) == [f"vault/{record}", f"vault/{record}.zd"]
    index = json.loads((folder / "paket.json").read_bytes())
    assert rw.legal.fingerprint in {root["sha256"] for root in index["roots"]}
    # near the end of the day's certificate: covered by the renewal, in both verifiers
    built = Built(CNA - timedelta(days=90))
    report = in_process(folder, roots_dir=_roots(rw, tmp_path / "kokler"), now=built.now)
    assert report.passed, report.codes()
    (day,) = [f for f in report.findings if f.code in LEVELS and f.where == DAY.isoformat()]
    core = core_verdict(rw, built)
    assert (day.code, day.level) == (core.code, core.level) == ("renewal-covered", "ok")
    assert (day.tr, day.en) == (core.message_tr, core.message_en)
    assert rw.of(rw.status(built.now), day_token_path()).covered_by == record
    found = [(f.level, f.code, f.where) for f in report.findings if f.code.startswith("token-eku")]
    assert found == [("warn", "token-eku-not-critical", "yenileme")]
    unlock_tree(tmp_path)


def test_an_unreadable_renewal_record_stops_the_build_with_an_evidence_error(
    rw: RenewalWorld, tmp_path: Path
) -> None:
    """W5-A-17: a renewal record the builder cannot read is judged strict, as the verifiers
    judge it, even when it is nested too deep for the JSON reader (a RecursionError, not a
    ValueError). A tampered record that names the day's token hash: the build is refused with
    an EvidenceError the callers handle, not a crash."""
    record = renew_day(rw, R1[0], R1[1], expires=R3[2])
    path = rw.layout.vault / record
    path.chmod(0o644)
    digest = json.dumps(sha256_file(rw.layout.token(DAY)))
    path.write_text("[" * 100_000 + digest + "]" * 100_000, encoding="utf-8")
    with pytest.raises(RecursionError):
        json.loads(path.read_bytes())
    with pytest.raises(EvidenceError, match=r"token-imprint \(yenileme\)"):
        _build(rw, [DAY], tmp_path / "out")
    unlock_tree(tmp_path)


@pytest.mark.parametrize("how", TAMPERINGS)
def test_a_tampered_renewal_record_is_strict_in_all_three_readers(
    rw: RenewalWorld, tmp_path: Path, how: str
) -> None:
    """#156 item 3: the core and the builder read a renewal record as the standalone verifier's
    ``load_json`` does. A record that says relaxed only to ``json.loads`` (a key twice, the last
    false; UTF-16), stamped in the past with a non-critical EKU under the legal root (as in the
    test above, where the record is not tampered): the core cannot read
    it and it does not cover the day, the builder judges its token strict and so does not take
    the legal root for it, and the standalone verifier refuses its token (``token-eku``). A
    package of the day with the record is refused by the builder's self-check; one in UTF-16
    is not picked up at all (its text does not hold the day's hash in UTF-8)."""
    record = tampered_renewal(rw, how, R1[0], R1[1], expires=R3[2])
    raw = (rw.layout.vault / record).read_bytes()
    built = Built(CNA - timedelta(days=90))
    # the core
    assert _parse_record(raw) is None
    token = rw.of(rw.status(built.now), day_token_path())
    assert (token.status, token.covered_by) == ("due", None)
    assert core_verdict(rw, built).code == "renewal-due"
    # the builder
    records = _Records(rw.layout, rw.roots)
    records.add(record, "renewal")
    assert records.used_roots == set()
    if how == "a key twice":
        with pytest.raises(EvidenceError, match=r"token-eku \(yenileme\)"):
            _build(rw, [DAY], tmp_path / "out")
    else:
        assert _records(_build(rw, [DAY], tmp_path / "out"), tmp_path / "pkg") == []
    # the standalone verifier on the vault
    report = standalone_report(rw, built, tmp_path)
    found = [(f.level, f.code, f.where) for f in report.findings if f.code.startswith("token-eku")]
    assert found == [("fail", "token-eku", "yenileme")]
    (day,) = [f for f in report.findings if f.code in LEVELS and f.where == DAY.isoformat()]
    assert day.code == "renewal-due"
    unlock_tree(tmp_path)


@pytest.mark.parametrize("case", DELETION_CASES, ids=[case.name for case in DELETION_CASES])
def test_a_deletion_records_token_is_judged_by_the_manifest_that_lists_it(
    tmp_path: Path, case: DeletionCase
) -> None:
    """#156 item 1 (W5-A-19): a deletion record's token is judged by the EKU rule of the
    manifest that seals the record, not by any manifest of the package; a record reached only
    through the chain anchor by the package's rule. The standalone verifier on the vault and on
    the package gives the case's findings, and the builder takes the legal CA's root for the
    token exactly when they are warnings (rule 5): it ships a package that passes, or its
    self-check refuses one whose record token fails."""
    rw = make_world(tmp_path / "site")
    days = case.build(rw)
    # the builder
    collected, _ = _collect_days(rw.layout, days, rw.roots, None)
    records = _Records(
        rw.layout, rw.roots, require_critical_eku=requires_critical_eku(rw.layout, days)
    )
    records.collect(collected)
    assert (rw.legal.fingerprint in records.used_roots) is case.relaxed
    # the standalone verifier on the vault (the auditor's roots: the mock and the legal one)
    report = standalone_report(rw, Built(utc_now()), tmp_path / "vault-check")
    found = {(f.level, f.code, f.where) for f in report.findings if f.code.startswith("token-eku")}
    assert found == case.findings
    # and on the package
    if not case.relaxed:
        failures = [f"token-eku ({where})" for _, _, where in sorted(case.findings)]
        with pytest.raises(EvidenceError, match="token-eku") as refused:
            _build(rw, list(days), tmp_path / "out")
        assert all(failure in str(refused.value) for failure in failures), refused.value
        unlock_tree(tmp_path)
        return
    info = _build(rw, list(days), tmp_path / "out")
    folder = unpack(info, tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    assert rw.legal.fingerprint in {root["sha256"] for root in index["roots"]}
    report = in_process(folder)
    assert report.passed, report.codes()
    found = {(f.level, f.code, f.where) for f in report.findings if f.code.startswith("token-eku")}
    assert found == case.findings
    unlock_tree(tmp_path)
