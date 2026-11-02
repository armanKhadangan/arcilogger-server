"""Revocation lists in evidence packages (Wave 5 W5-B): the builder ships the stored lists a
package's days need (D9) and every list with an entry for a certificate of their chains (C8),
the note when it has none, and the renewal of a packaged report's time stamp (D3). The vault
helpers are those of ``test_evidence.py``."""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from arcilogger_core.canonical import canonical_json
from arcilogger_core.chain import verify_day
from arcilogger_core.crl import INDEX_NAME, INDEX_SCHEMA, parse_crl
from arcilogger_core.evidence import EvidenceError, build_package
from arcilogger_core.evidence import builder as builder_module
from arcilogger_core.evidence.builder import NO_CRL_NOTE
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_core.tsa.verify import fingerprint, inspect_token, token_chain

from .revocation_cases import PrivateKey
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
from .test_revocation import LegalCa, _certificate, _key_of, _with_certificate


def _crl(
    tsa: MockTsa,
    revoked: list[tuple[int, datetime]],
    this_update: datetime,
    *,
    reason: x509.ReasonFlags | None = None,
    delta_of: int | None = None,
    issuer: tuple[x509.Certificate, PrivateKey] | None = None,
    key: PrivateKey | None = None,
) -> bytes:
    """A DER list of ``issuer`` (default: the mock TSA's root), issued at ``this_update`` and
    signed with ``key`` (default: the issuer's own); ``reason`` for every entry, ``delta_of``:
    a delta list of that base list."""
    certificate, own_key = issuer or (tsa.root_certificate, _key_of(tsa, "root.key"))
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(certificate.subject)
        .last_update(this_update)
        .next_update(this_update + timedelta(days=7))
    )
    if delta_of is not None:
        builder = builder.add_extension(x509.DeltaCRLIndicator(delta_of), critical=True)
    for serial, when in revoked:
        entry = x509.RevokedCertificateBuilder().serial_number(serial).revocation_date(when)
        if reason is not None:
            entry = entry.add_extension(x509.CRLReason(reason), critical=False)
        builder = builder.add_revoked_certificate(entry.build())
    return builder.sign(key or own_key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


def _store_crl(
    folder: Path,
    tsa: MockTsa,
    this_update: datetime,
    data: bytes = b"",
    issuer: x509.Certificate | None = None,
) -> str:
    """Store a list (``data``, or one of ``tsa``'s root revoking another certificate) as the
    CRL job names it (D8, C7), under ``issuer`` (default: ``tsa``'s root); returns the file
    name."""
    data = data or _crl(tsa, [(12345, this_update - timedelta(days=1))], this_update)
    digest = hashlib.sha256(data).hexdigest()
    issuer_sha256 = fingerprint(issuer or tsa.root_certificate)
    name = f"{issuer_sha256[:12]}-{this_update:%Y%m%dT%H%M%SZ}-{digest[:8]}.crl"
    (folder / name).write_bytes(data)
    return name


def _index(folder: Path, issuers: Sequence[x509.Certificate]) -> None:
    """``index.json`` as ``crl fetch`` writes it (§2.2) for the lists of ``folder`` it would
    have stored: those one of ``issuers`` signed, delta lists left out (C7)."""
    entries = []
    for path in sorted(folder.glob("*.crl")):
        crl = parse_crl(path.read_bytes())
        signer = next((issuer for issuer in issuers if crl.signed_by(issuer)), None)
        if signer is None or crl.delta:
            continue
        assert path.name.startswith(fingerprint(signer)[:12])
        entries.append(
            {
                "url": "http://crl.example.com/test.crl",
                "file": path.name,
                "issuer": crl.issuer,
                "issuer_sha256": fingerprint(signer),
                "this_update": crl.this_update.isoformat(),
                "next_update": crl.next_update.isoformat() if crl.next_update else None,
                "sha256": crl.sha256,
                "fetched_at": crl.this_update.isoformat(),
            }
        )
    index = {"schema": INDEX_SCHEMA, "lists": entries, "failures": []}
    (folder / INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")


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


def test_a_list_with_an_entry_for_a_chain_certificate_is_shipped_too(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """C8: besides the D9 subset, every stored list with an entry for a certificate of a
    packaged day's chain goes in, so the package sees the hold ``verify_day`` sees. Any entry
    counts, removeFromCRL too. An older list about other certificates stays out, and so do a
    list under the root's name signed by another key and a delta list (C7)."""
    vault = seal_vault(tmp_path, "ec", DAYS[:1])
    tsa = vault.tsa
    stamp = inspect_token(vault.layout.token(DAYS[0]).read_bytes()).gen_time.replace(microsecond=0)
    serial = tsa.tsa_certificate.serial_number
    crls = vault.layout.crl_dir
    crls.mkdir(parents=True)
    hold = _crl(
        tsa,
        [(serial, stamp + timedelta(days=1))],
        stamp + timedelta(days=2),
        reason=x509.ReasonFlags.certificate_hold,
    )
    # it would fail the day if it counted: revoked before the stamp
    forged = _crl(
        tsa,
        [(serial, stamp - timedelta(days=1))],
        stamp + timedelta(days=2, hours=12),
        key=ec.generate_private_key(ec.SECP256R1()),
    )
    delta = _crl(
        tsa,
        [(serial, stamp + timedelta(days=3))],
        stamp + timedelta(days=4),
        reason=x509.ReasonFlags.certificate_hold,
        delta_of=1,
    )
    lifted = _crl(
        tsa,
        [(serial, stamp + timedelta(days=9))],
        stamp + timedelta(days=10),
        reason=x509.ReasonFlags.remove_from_crl,
    )
    names = {
        "older": _store_crl(crls, tsa, stamp - timedelta(days=5)),
        "first_after_the_day": _store_crl(crls, tsa, stamp + timedelta(hours=1)),
        "holds_the_tsa_certificate": _store_crl(crls, tsa, stamp + timedelta(days=2), hold),
        "signed_by_another_key": _store_crl(crls, tsa, stamp + timedelta(days=2, hours=12), forged),
        "other_certificates": _store_crl(crls, tsa, stamp + timedelta(days=3)),
        "delta": _store_crl(crls, tsa, stamp + timedelta(days=4), delta),
        "lifts_the_hold": _store_crl(crls, tsa, stamp + timedelta(days=10), lifted),
        # the newest list has no entry for the TSA certificate
        "newest": _store_crl(crls, tsa, stamp + timedelta(days=20)),
    }

    info = vault.build(crl_dir=crls, out_dir=tmp_path / "out")
    folder = unpack(info, tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    shipped = ("first_after_the_day", "holds_the_tsa_certificate", "lifts_the_hold", "newest")
    assert index["crls"] == sorted(f"sil/{names[key]}" for key in shipped)
    assert f"CRL file {names['delta']} is a delta list" in caplog.text
    code, report = isolated(folder)
    assert code == 0, codes(report)
    assert [(f["level"], f["where"]) for f in findings(report, "crl-hold")] == [
        ("warn", DAYS[0].isoformat())
    ]
    # verify_day reads the indexed lists (load_stored) and sees the same hold.
    _index(crls, [tsa.root_certificate])
    check = verify_day(vault.layout, DAYS[0], vault.roots)
    assert check.ok, check.errors
    assert any("on hold" in warning for warning in check.warnings), check.warnings
    unlock_tree(tmp_path)


def test_a_list_with_an_entry_for_an_intermediate_is_shipped_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C8 along a three-level chain (root, intermediate CA, TSA certificate): the root's list
    with an entry for the intermediate and the intermediate's list with an entry for the TSA
    certificate go in. A list under the root's name signed by another key does not."""
    vault = seal_vault(tmp_path, "ec", DAYS[:1])
    # The mock root allows no intermediate (pathLenConstraint 0): the second day is stamped
    # under a root of its own, which the vault's roots are then.
    root_key = ec.generate_private_key(ec.SECP256R1())
    root = (_certificate("Örnek Root (TEST)", root_key, None), root_key)
    monkeypatch.setattr(vault.tsa, "root_certificate", root[0])
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _certificate("Örnek TSA CA (TEST)", ca_key, root)
    tsa_key = ec.generate_private_key(ec.SECP256R1())
    tsa_certificate = _certificate("Örnek TSA (TEST)", tsa_key, (ca, ca_key), ca=False)
    vault.tsa.signer = (tsa_certificate, tsa_key)
    respond = vault.tsa.respond
    # test: the token carries the intermediate, as a provider's does
    monkeypatch.setattr(
        vault.tsa, "respond", lambda request: _with_certificate(respond(request), ca)
    )
    vault.write_day(DAYS[1])
    sealer = vault.sealer(datetime.combine(DAYS[2], time(0, 30), tzinfo=SITE_TZ))
    assert [outcome.status for outcome in sealer.seal_due()] == ["sealed"]
    token = vault.layout.token(DAYS[1]).read_bytes()
    assert token_chain(token, vault.roots) == [tsa_certificate, ca, root[0]]
    stamp = inspect_token(token).gen_time.replace(microsecond=0)
    crls = vault.layout.crl_dir
    crls.mkdir(parents=True)

    def of_ca(this_update: datetime, data: bytes = b"") -> str:
        data = data or _crl(
            vault.tsa, [(12345, this_update - timedelta(days=1))], this_update, issuer=(ca, ca_key)
        )
        return _store_crl(crls, vault.tsa, this_update, data, issuer=ca)

    def of_root(this_update: datetime, data: bytes = b"") -> str:
        data = data or _crl(
            vault.tsa, [(12345, this_update - timedelta(days=1))], this_update, issuer=root
        )
        return _store_crl(crls, vault.tsa, this_update, data)

    holds_ca = _crl(
        vault.tsa,
        [(ca.serial_number, stamp + timedelta(days=1))],
        stamp + timedelta(days=2),
        reason=x509.ReasonFlags.certificate_hold,
        issuer=root,
    )
    forged = _crl(
        vault.tsa,
        [(ca.serial_number, stamp - timedelta(days=1))],
        stamp + timedelta(days=2, hours=12),
        issuer=root,
        key=ec.generate_private_key(ec.SECP256R1()),
    )
    holds_tsa = _crl(
        vault.tsa,
        [(tsa_certificate.serial_number, stamp + timedelta(days=3))],
        stamp + timedelta(days=4),
        reason=x509.ReasonFlags.certificate_hold,
        issuer=(ca, ca_key),
    )
    names = {
        "root_first_after_the_day": of_root(stamp + timedelta(hours=1)),
        "root_holds_the_intermediate": of_root(stamp + timedelta(days=2), holds_ca),
        "root_signed_by_another_key": of_root(stamp + timedelta(days=2, hours=12), forged),
        "root_other_certificates": of_root(stamp + timedelta(days=3)),
        "root_newest": of_root(stamp + timedelta(days=20)),
        "ca_first_after_the_day": of_ca(stamp + timedelta(hours=2)),
        "ca_holds_the_tsa_certificate": of_ca(stamp + timedelta(days=4), holds_tsa),
        "ca_other_certificates": of_ca(stamp + timedelta(days=5)),
        "ca_newest": of_ca(stamp + timedelta(days=21)),
    }

    info = vault.build([DAYS[1]], crl_dir=crls, out_dir=tmp_path / "out")
    folder = unpack(info, tmp_path / "pkg")
    index = json.loads((folder / "paket.json").read_bytes())
    shipped = (
        "root_first_after_the_day",
        "root_holds_the_intermediate",
        "root_newest",
        "ca_first_after_the_day",
        "ca_holds_the_tsa_certificate",
        "ca_newest",
    )
    assert index["crls"] == sorted(f"sil/{names[key]}" for key in shipped)
    code, report = isolated(folder)
    assert code == 0, codes(report)
    holds = findings(report, "crl-hold")
    assert [(f["level"], f["where"]) for f in holds] == [("warn", DAYS[1].isoformat())] * 2
    # one for the TSA certificate, one for the intermediate
    assert sorted("Örnek TSA CA (TEST)" in f["en"] for f in holds) == [False, True], holds
    _index(crls, [root[0], ca])
    check = verify_day(vault.layout, DAYS[1], vault.roots)
    assert check.ok, check.errors
    # Each certificate's finding, as the standalone verifier lists them.
    on_hold = [warning for warning in check.warnings if "on hold" in warning]
    assert sorted("Örnek TSA CA (TEST)" in warning for warning in on_hold) == [False, True]
    unlock_tree(tmp_path)


def test_a_day_revoked_before_its_stamp_is_not_legal_grade_in_the_package(
    world: Any, days: list[date], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C15: the builder checks each day against the lists of its CRL folder, so a day whose TSA
    certificate was revoked at or before the stamp is not legal grade in ``paket.json`` and the
    package row. The standalone verifier then refuses the package (its self-check stops the
    build); without that check, what the builder wrote says not legal grade."""
    ca = LegalCa(tmp_path / "legal")
    moment = ca.seal(world, days[0])

    def build(out: str) -> Any:
        return build_package(
            world.layout,
            days[:1],
            roots=[ca.root],
            legal_root_fingerprints=[ca.fingerprint],
            crl_dir=world.layout.crl_dir,
            out_dir=tmp_path / out,
        )

    before = build("before")
    assert (before.legal_grade, before.legal_grade_claimed) == (True, True)
    ca.revoke(world, moment)
    with pytest.raises(EvidenceError, match="crl-revoked"):
        build("refused")
    monkeypatch.setattr(builder_module, "_self_check", lambda part, password: None)
    info = build("written")
    assert (info.legal_grade, info.legal_grade_claimed) == (False, True)
    index = json.loads(zipfile.ZipFile(info.path).read("paket.json"))
    assert [(d["legal_grade"], d["legal_grade_claimed"]) for d in index["days"]] == [(False, True)]
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
