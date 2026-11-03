"""Evidence packages carry the renewals of their days and of their report (Wave 5 D1-D3, §3).

The builder is not changed for renewals (D1): it ships every ``vault/renewals/*.json`` whose
raw text holds a packaged day's manifest or token hash, or the packaged report's token hash.
A renewal of a renewal repeats the hashes the older one protected (``protects``, D2), so a
package of one day picks up the whole chain. The stamps here are made in October 2026, before
the real date: the builder's self-check verifies the finished package at the real time.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization

from arcilogger_core.evidence.builder import build_package
from arcilogger_core.evidence.model import PackageInfo
from arcilogger_core.renewal import RecordEntry, renew
from arcilogger_core.seal import sha256_file
from arcilogger_core.timeutil import utc_now

from .renewal_cases import entry_of
from .renewal_world import DAY, REPORT_NAME, RenewalWorld, make_world
from .test_evidence import findings, in_process, isolated, unlock_tree, unpack

#: The day of the package; its stamp's certificate expires in March 2027.
OTHER = DAY + timedelta(days=1)
OTHER_EXPIRES = datetime(2027, 3, 1, tzinfo=UTC)
#: The chain: R1 renews the day's token, R2 R1's, R3 R2's; each before the certificate it
#: renews expires, and each certificate but R3's within 180 days of December 2026.
R1 = ("2026-10-15-1", datetime(2026, 10, 15, 9, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
R2 = ("2026-10-16-1", datetime(2026, 10, 16, 9, tzinfo=UTC), datetime(2027, 2, 1, tzinfo=UTC))
R3 = ("2026-10-17-1", datetime(2026, 10, 17, 9, tzinfo=UTC), datetime(2036, 1, 1, tzinfo=UTC))
#: When the whole chain is needed for the day to be covered.
JUDGED = datetime(2026, 12, 1, tzinfo=UTC)


@pytest.fixture
def rw(tmp_path: Path) -> RenewalWorld:
    world = make_world(tmp_path / "site")
    assert utc_now() > R3[1] + timedelta(days=1), (
        "the test assumes its October 2026 stamps are past"
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
    folder = unpack(info, folder)
    records: list[str] = json.loads((folder / "paket.json").read_bytes())["records"]
    return records


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
