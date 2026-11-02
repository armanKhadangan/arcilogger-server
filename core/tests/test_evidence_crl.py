"""Revocation lists in evidence packages (Wave 5 W5-B): the builder ships the stored lists a
package's days need (D9), the note when it has none, and the renewal of a packaged report's
time stamp (D3). The vault helpers are those of ``test_evidence.py``."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from arcilogger_core.canonical import canonical_json
from arcilogger_core.evidence import build_package
from arcilogger_core.evidence.builder import NO_CRL_NOTE
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_core.tsa.verify import fingerprint, inspect_token

from .test_evidence import (
    DAYS,
    _stamped_report,
    codes,
    findings,
    fixed_clock,
    isolated,
    seal_vault,
    tree_state,
    unlock_tree,
    unpack,
)


def _crl(tsa: MockTsa, revoked: list[tuple[int, datetime]], this_update: datetime) -> bytes:
    """A DER list of the mock TSA's root, issued at ``this_update``."""
    key = serialization.load_pem_private_key((tsa.state_dir / "root.key").read_bytes(), None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(tsa.root_certificate.subject)
        .last_update(this_update)
        .next_update(this_update + timedelta(days=7))
    )
    for serial, when in revoked:
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder().serial_number(serial).revocation_date(when).build()
        )
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


def _store_crl(folder: Path, tsa: MockTsa, this_update: datetime) -> str:
    """Store a list of ``tsa``'s root as the CRL job names it (D8); returns the file name."""
    name = f"{fingerprint(tsa.root_certificate)[:12]}-{this_update:%Y%m%dT%H%M%SZ}.crl"
    data = _crl(tsa, [(12345, this_update - timedelta(days=1))], this_update)
    (folder / name).write_bytes(data)
    return name


def test_a_package_ships_only_the_crls_its_days_need(tmp_path: Path) -> None:
    """D9: for each issuer the newest list, plus the first list issued at or after each packaged
    day's stamp time; the other stored lists stay out of the package."""
    vault = seal_vault(tmp_path, "ec", [DAYS[0]])
    vault.write_day(DAYS[1])
    vault.sealer(datetime.combine(DAYS[2], time(0, 30), tzinfo=SITE_TZ)).seal_due()
    # the third day is stamped by a second TSA under another root: a second issuer
    other = MockTsa(tmp_path / "tsa-b", key_type="ec", clock=vault.tsa_clock)
    roots = [vault.tsa.root_certificate, other.root_certificate]
    vault.write_day(DAYS[2])
    now = datetime.combine(DAYS[2] + timedelta(days=1), time(0, 30), tzinfo=SITE_TZ)
    vault.tsa_clock.now = now + timedelta(seconds=5)
    sealer = Sealer(
        vault.config,
        provider=MockTsaProvider(other),
        roots=roots,
        clock=lambda: now,
        clock_status=lambda: "test clock status",
    )
    assert [outcome.status for outcome in sealer.seal_due()] == ["sealed"]
    # stamp times: 2026-09-10 21:30:05Z, 2026-09-11 21:30:05Z (first root), 2026-09-12 21:30:05Z
    stamps = [inspect_token(vault.layout.token(day).read_bytes()).gen_time for day in DAYS]
    assert [stamp.astimezone(UTC).hour for stamp in stamps] == [21, 21, 21]

    def at(day: int, hour: int, month: int = 9) -> datetime:
        return datetime(2026, month, day, hour, tzinfo=UTC)

    crls = vault.layout.crl_dir
    crls.mkdir(parents=True)
    first = {
        "before_any_stamp": _store_crl(crls, vault.tsa, at(10, 12)),
        "first_after_day_1": _store_crl(crls, vault.tsa, at(10, 22)),
        "second_after_day_1": _store_crl(crls, vault.tsa, at(11, 6)),
        "first_after_day_2": _store_crl(crls, vault.tsa, at(11, 22)),
        "first_after_day_3": _store_crl(crls, vault.tsa, at(12, 22)),
        "in_between": _store_crl(crls, vault.tsa, at(20, 12)),
        "newest": _store_crl(crls, vault.tsa, at(1, 12, month=10)),
    }
    second = {
        "first_after_days_1_and_2": _store_crl(crls, other, at(11, 23)),
        "first_after_day_3": _store_crl(crls, other, at(12, 23)),
        "in_between": _store_crl(crls, other, at(25, 12)),
        "newest": _store_crl(crls, other, at(2, 12, month=10)),
    }
    # a list of a CA the package does not chain to, a copy of a stored list and a broken file
    stranger = MockTsa(tmp_path / "stranger", key_type="ec")
    _store_crl(crls, stranger, at(30, 12, month=10))
    shutil.copyfile(crls / first["newest"], crls / "kopya.crl")
    (crls / "bozuk.crl").write_bytes(b"not a CRL")
    (crls / "index.json").write_text("{}", encoding="utf-8")
    before = tree_state(crls)

    info = build_package(
        vault.layout,
        DAYS,
        roots=roots,
        crl_dir=crls,
        clock=fixed_clock(),
        out_dir=tmp_path / "out",
    )
    folder = unpack(info, tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    shipped = [
        first["first_after_day_1"],
        first["first_after_day_2"],
        first["first_after_day_3"],
        first["newest"],
        second["first_after_days_1_and_2"],
        second["first_after_day_3"],
        second["newest"],
    ]
    assert index["crls"] == sorted(f"sil/{name}" for name in shipped)
    assert index["crl_note"] is None
    for name in shipped:  # byte for byte, under the stored name
        assert (folder / "sil" / name).read_bytes() == (crls / name).read_bytes()
    assert tree_state(crls) == before  # the CRL folder is only read
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert {(f["code"], f["where"]) for f in findings(report, "crl-ok")} == {
        ("crl-ok", day.isoformat()) for day in DAYS
    }
    # the day of the second root alone: its own lists, and the first root's are not needed
    info = build_package(
        vault.layout,
        [DAYS[2]],
        roots=roots,
        crl_dir=crls,
        clock=fixed_clock(),
        out_dir=tmp_path / "out-3",
    )
    index = json.loads(zipfile.ZipFile(info.path).read("paket.json"))
    assert index["crls"] == sorted(
        f"sil/{name}" for name in (second["first_after_day_3"], second["newest"])
    )
    unlock_tree(tmp_path)


def test_without_a_usable_crl_the_package_carries_the_note(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    vault = seal_vault(tmp_path, "ec", DAYS[1:2])
    info = vault.build([DAYS[1]], out_dir=tmp_path / "none")
    index = json.loads(zipfile.ZipFile(info.path).read("paket.json"))
    assert (index["crls"], index["crl_note"]) == ([], NO_CRL_NOTE)
    # a folder with only another CA's list, a broken file and a list with an odd version
    # (x509.InvalidVersion, no ValueError) gives no list either
    crls = tmp_path / "crl"
    crls.mkdir()
    _store_crl(crls, MockTsa(tmp_path / "stranger", key_type="ec"), datetime.now(UTC))
    (crls / "bozuk.crl").write_bytes(b"not a CRL")
    own = _store_crl(crls, vault.tsa, datetime.now(UTC))
    der = (crls / own).read_bytes()
    at = der.index(b"\x02\x01\x01", 0, 16)  # version v2
    (crls / own).write_bytes(der[:at] + b"\x02\x01\x05" + der[at + 3 :])
    info = vault.build([DAYS[1]], crl_dir=crls, out_dir=tmp_path / "other")
    assert f"CRL file {own} is not put into the package" in caplog.text
    folder = unpack(info, tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    assert (index["crls"], index["crl_note"]) == ([], NO_CRL_NOTE)
    assert not (folder / "sil").exists()
    code, report = isolated(folder)
    assert code == 0, codes(report)
    (absent,) = findings(report, "crl-absent")
    assert NO_CRL_NOTE in absent["tr"]
    unlock_tree(tmp_path)


def test_a_renewal_of_a_packaged_report_token_is_shipped(tmp_path: Path) -> None:
    """D3: a renewal whose text holds the packaged report token's hash goes into the package;
    a renewal of something else does not, nor does the report's renewal without the report."""
    vault = seal_vault(tmp_path, "ec", DAYS[:1])
    pdf, token = _stamped_report(vault, tmp_path)
    renewals = vault.layout.renewals_dir
    renewals.mkdir()
    covering = renewals / "2028-04-01-1.json"
    covering.write_bytes(
        canonical_json(
            {
                "schema": "arcilogger.renewal/1",
                "tokens": [
                    {
                        "kind": "report",
                        "path": "exports/reports/ARCILOGGER-5651-rapor-7.pdf.zd",
                        "token_sha256": hashlib.sha256(token).hexdigest(),
                        "record_sha256": hashlib.sha256(pdf).hexdigest(),
                    }
                ],
            }
        )
    )
    MockTsaProvider(vault.tsa).stamp(covering)
    unrelated = renewals / "2028-04-01-2.json"
    unrelated.write_bytes(
        canonical_json(
            {
                "schema": "arcilogger.renewal/1",
                "tokens": [{"kind": "report", "token_sha256": "cd" * 32}],
            }
        )
    )
    MockTsaProvider(vault.tsa).stamp(unrelated)
    info = vault.build(
        report_pdf=pdf,
        report_name="ARCILOGGER-5651-rapor-7.pdf",
        report_token=token,
        out_dir=tmp_path / "out",
    )
    folder = unpack(info, tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["records"] == [
        "vault/renewals/2028-04-01-1.json",
        "vault/renewals/2028-04-01-1.json.zd",
    ]
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert "report-ok" in codes(report, "ok")
    assert len(findings(report, "renewal-covers")) == 1
    # the same days without the report (or with the report but not its token): no renewal
    plain = vault.build(out_dir=tmp_path / "plain")
    assert json.loads(zipfile.ZipFile(plain.path).read("paket.json"))["records"] == []
    unstamped = vault.build(
        report_pdf=pdf,
        report_name="ARCILOGGER-5651-rapor-7.pdf",
        report_note="Zaman damgası doğrulanamadı / the time stamp did not verify",
        out_dir=tmp_path / "unstamped",
    )
    assert json.loads(zipfile.ZipFile(unstamped.path).read("paket.json"))["records"] == []
    unlock_tree(tmp_path)
