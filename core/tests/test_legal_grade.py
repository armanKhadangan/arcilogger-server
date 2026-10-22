"""Which roots are trusted, and when a day counts as "legal grade".

* The mock TSA's test root (its private keys lie next to it in ``state/mock-tsa``) is trusted
  only while ``tsa.provider`` is ``mock``; a leftover ``state/mock-tsa`` is never trusted in
  production.
* "Legal grade" comes from the verified chain: the manifest's claim (Kamu SM production server)
  **and** a chain to a root listed in ``tsa.legal_root_fingerprints``.
* An evidence package bundles only the roots its tokens chain to.

The "Kamu SM" CA here is simulated (a self-made root and TSA certificate with a made-up policy
OID); nothing about the real Kamu SM certificates is assumed.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from arcilogger_core.canonical import canonical_json
from arcilogger_core.chain import verify_range
from arcilogger_core.config import Config
from arcilogger_core.evidence import EvidenceError, build_package
from arcilogger_core.evidence.archive import ArchivePackage
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.immutability import unlock
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer
from arcilogger_core.tsa import trusted_roots
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_core.tsa.verify import fingerprint, verify_token

#: A made-up policy OID (not 2.999, not a real Kamu SM policy).
SIMULATED_POLICY = "1.3.6.1.4.1.55555.1.1"


@dataclass
class Simulated:
    tsa: MockTsa
    root: x509.Certificate

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.root)


def _name(text: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "TR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Simulated Kamu SM (test)"),
            x509.NameAttribute(NameOID.COMMON_NAME, text),
        ]
    )


def simulated_kamusm(folder: Path) -> Simulated:
    """A MockTsa that signs with a certificate of a non-test CA and a non-test policy."""
    start = datetime.now(UTC) - timedelta(days=400)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root = (
        x509.CertificateBuilder()
        .subject_name(_name("Simulated Root"))
        .issuer_name(_name("Simulated Root"))
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .sign(root_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name("Simulated TSA"))
        .issuer_name(root.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(start)
        .not_valid_after(start + timedelta(days=3000))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), True)
        .sign(root_key, hashes.SHA256())
    )
    tsa = MockTsa(folder, key_type="ec")
    tsa.signer = (certificate, key)
    tsa.policy = SIMULATED_POLICY
    return Simulated(tsa, root)


class ProductionLikeProvider(MockTsaProvider):
    """Reports itself like the Zamane provider against the production server."""

    name = "zamane"
    legal_grade = True


def _seal(world: Any, days: list[date], provider: MockTsaProvider, roots: list[Any]) -> None:
    for day in days:
        world.busy_day(day)
    world.set_now(days[-1] + timedelta(days=1))
    sealer = Sealer(
        world.config, provider=provider, roots=roots, clock=world.clock, clock_status=lambda: "t"
    )
    assert [o.status for o in sealer.seal_due()] == ["sealed"] * len(days)


def _roots_dir(tmp_path: Path, *certificates: x509.Certificate) -> Path:
    folder = tmp_path / "kamusm-roots"
    folder.mkdir(exist_ok=True)
    for number, certificate in enumerate(certificates):
        pem = certificate.public_bytes(serialization.Encoding.PEM)
        (folder / f"root-{number}.pem").write_bytes(pem)
    return folder


# -- which roots are trusted ---------------------------------------------------------------------


def test_a_leftover_mock_tsa_is_never_trusted_in_production(world: Any, tmp_path: Path) -> None:
    kamu = simulated_kamusm(tmp_path / "kamu")
    roots_dir = _roots_dir(tmp_path, kamu.root)
    assert (world.layout.state / "mock-tsa" / "root.key").exists()  # the trial's leftover
    world.reconfigure(tsa={"provider": "zamane", "roots_dir": str(roots_dir)})
    assert [fingerprint(r) for r in trusted_roots(world.config, world.layout)] == [kamu.fingerprint]
    world.reconfigure(tsa={"provider": "rfc3161", "roots_dir": str(roots_dir)})
    assert len(trusted_roots(world.config, world.layout)) == 1
    world.reconfigure(tsa={"provider": "mock", "roots_dir": str(roots_dir)})
    trusted = {fingerprint(r) for r in trusted_roots(world.config, world.layout)}
    assert trusted == {kamu.fingerprint, fingerprint(world.tsa.root_certificate)}


def test_fingerprints_are_validated_and_normalized(tmp_path: Path) -> None:
    digest = hashlib.sha256(b"x").hexdigest().upper()
    colons = ":".join(digest[i : i + 2] for i in range(0, 64, 2))
    config = Config.model_validate(
        {
            "data_dir": str(tmp_path),
            "secrets_dir": str(tmp_path),
            "tsa": {"legal_root_fingerprints": [colons]},
        }
    )
    assert config.tsa.legal_root_fingerprints == (digest.lower(),)
    with pytest.raises(ValueError, match="SHA-256 fingerprint"):
        Config.model_validate(
            {"data_dir": "x", "secrets_dir": "x", "tsa": {"legal_root_fingerprints": ["abc"]}}
        )


# -- legal grade from the verified chain ---------------------------------------------------------


def test_legal_grade_needs_the_claim_and_a_listed_root(
    world: Any, days: list[date], tmp_path: Path
) -> None:
    kamu = simulated_kamusm(tmp_path / "kamu")
    world.reconfigure(tsa={"provider": "zamane", "legal_root_fingerprints": [kamu.fingerprint]})
    _seal(world, days[:2], ProductionLikeProvider(kamu.tsa), [kamu.root])
    state = json.loads(world.layout.seal_state(days[0]).read_text(encoding="utf-8"))
    assert state["legal_grade_claimed"] is True
    assert state["legal_grade"] is True
    assert state["root_sha256"] == kamu.fingerprint
    listed = verify_range(world.layout, days[:2], [kamu.root], legal_roots=[kamu.fingerprint])
    assert [(c.ok, c.legal_grade) for c in listed] == [(True, True), (True, True)]
    unlisted = verify_range(world.layout, days[:2], [kamu.root])  # no fingerprints configured
    assert [(c.ok, c.legal_grade) for c in unlisted] == [(True, False), (True, False)]
    other = hashlib.sha256(b"another root").hexdigest()
    checks = verify_range(world.layout, days[:2], [kamu.root], legal_roots=[other])
    assert not checks[0].legal_grade
    assert any("not confirmed" in warning for warning in checks[0].warnings)

    # The package: legal grade, and only the root the tokens chain to.
    info = build_package(
        world.layout,
        days[:2],
        roots=[kamu.root, world.tsa.root_certificate],  # e.g. the API's extra test root
        legal_root_fingerprints=[kamu.fingerprint],
        out_dir=tmp_path / "out",
    )
    assert (info.legal_grade, info.legal_grade_claimed) == (True, True)
    with zipfile.ZipFile(info.path) as archive:
        index = json.loads(archive.read("paket.json"))
        roots = [name for name in archive.namelist() if name.startswith("sertifikalar/kokler/")]
    assert [root["sha256"] for root in index["roots"]] == [kamu.fingerprint]
    assert len(roots) == 1
    assert [(d["legal_grade"], d["legal_grade_claimed"]) for d in index["days"]] == [
        (True, True)
    ] * 2
    assert all(d["root_sha256"] == kamu.fingerprint for d in index["days"])

    # Without the fingerprint: claimed, not confirmed; the guide says what to compare.
    plain = build_package(world.layout, days[:2], roots=[kamu.root], out_dir=tmp_path / "out2")
    assert (plain.legal_grade, plain.legal_grade_claimed) == (False, True)
    assert plain.as_json()["legal_grade_claimed"] is True
    with zipfile.ZipFile(plain.path) as archive:
        index = json.loads(archive.read("paket.json"))
        guide = archive.read("DOGRULAMA.txt").decode("utf-8-sig")
    assert [(d["legal_grade"], d["legal_grade_claimed"]) for d in index["days"]] == [
        (False, True)
    ] * 2
    assert "Kamu SM kökü olarak" in guide
    assert "TEST DAMGASI" not in guide


def _forge(world: Any, day: date, *, policy: str) -> None:
    """Rewrite a sealed day (raw data and manifest hashes, keeping the legal-grade claim) and
    stamp it again with the leftover mock TSA key, as someone who can read state/mock-tsa could."""
    vault = world.layout.vault_day(day)
    for path in vault.iterdir():
        unlock(path)
    manifest_path = world.layout.manifest(day)
    manifest = json.loads(manifest_path.read_bytes())
    manifest["site"] = "rewritten"
    manifest_path.write_bytes(canonical_json(manifest))
    world.layout.token(day).unlink()
    world.tsa.policy = policy
    MockTsaProvider(world.tsa).stamp(manifest_path)
    world.tsa.policy = sv.TEST_POLICY_PREFIX + "5651.1"


@pytest.mark.parametrize("policy", ["2.999.5651.1", SIMULATED_POLICY])
def test_a_day_restamped_with_the_mock_key_does_not_verify(
    world: Any, days: list[date], tmp_path: Path, policy: str
) -> None:
    kamu = simulated_kamusm(tmp_path / "kamu")
    roots_dir = _roots_dir(tmp_path, kamu.root)
    world.reconfigure(tsa={"provider": "zamane", "roots_dir": str(roots_dir)})
    _seal(world, days[:2], ProductionLikeProvider(kamu.tsa), [kamu.root])
    _forge(world, days[1], policy=policy)
    production = trusted_roots(world.config, world.layout)
    checks = verify_range(world.layout, days[:2], production)
    assert any("does not reach a pinned root" in e for e in checks[1].errors), checks[1].errors
    # Even where the test root is trusted (provider = mock), the claim gives the forgery away.
    with_test_root = [*production, world.tsa.root_certificate]
    checks = verify_range(world.layout, days[:2], with_test_root)
    assert any("stamp is from the test TSA" in e for e in checks[1].errors), checks[1].errors
    with pytest.raises(EvidenceError, match="test \\(mock\\) TSA"):
        build_package(world.layout, days[:2], roots=with_test_root, out_dir=tmp_path / "out")


def test_the_standalone_verifier_refuses_a_legal_claim_on_a_test_root(
    world: Any, days: list[date], tmp_path: Path
) -> None:
    """Defence in depth: a package (whose roots it trusts) with a claimed legal-grade day that
    chains to ARCILOGGER's test root fails, whatever policy OID the token carries."""
    _seal(world, days[:1], MockTsaProvider(world.tsa), [world.tsa.root_certificate])
    info = build_package(
        world.layout, days[:1], roots=[world.tsa.root_certificate], out_dir=tmp_path / "out"
    )
    folder = tmp_path / "pkg"
    with zipfile.ZipFile(info.path) as archive:
        archive.extractall(folder)
    name = f"vault/{days[0]:%Y/%m/%d}/manifest-{days[0].isoformat()}.json"
    manifest = json.loads((folder / name).read_bytes())
    manifest["tsa"]["legal_grade"] = True
    (folder / name).write_bytes(canonical_json(manifest))
    world.tsa.policy = SIMULATED_POLICY
    token = MockTsaProvider(world.tsa).stamp(folder / name).token_path
    assert token == folder / f"{name}.zd"
    index = json.loads((folder / "paket.json").read_bytes())
    for entry in index["files"]:
        data = (folder / entry["path"]).read_bytes()
        entry.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    for day in index["days"]:
        day["serial"] = None  # paket.json is not signed; a forger rewrites it as well
    (folder / "paket.json").write_bytes(canonical_json(index))
    with sv.open_package(folder) as package:
        report = sv.verify_package(package)
    assert "legal-claim-mismatch" in report.codes("fail")


def test_the_mock_counts_credits_only_where_it_may_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API service may only read state/mock-tsa: a stamp still works (finding: report stamps
    always failed under the installed rights)."""
    tsa = MockTsa(tmp_path / "mock-tsa", key_type="ec")
    credits = tsa.credits
    document = tmp_path / "rapor.pdf"
    document.write_bytes(b"%PDF-1.7 report")

    def read_only(*_: Any, **__: Any) -> None:
        raise PermissionError(13, "Permission denied", str(tmp_path / "mock-tsa" / ".credits.tmp"))

    monkeypatch.setattr("arcilogger_core.tsa.mock.atomic_write_text", read_only)
    stamp = MockTsaProvider(tsa).stamp(document)

    check = verify_token(
        stamp.token_path.read_bytes(), roots=[tsa.root_certificate], file_path=document
    )
    assert check.ok, check.errors
    assert tsa.credits == credits  # not counted down, not failed
    with pytest.raises(PermissionError):  # an explicit write in a test still fails loudly
        tsa.credits = 5
    assert Layout(tmp_path).state.name == "state"


def test_a_report_stamped_with_sha512_keeps_its_root_in_the_package(
    world: Any, days: list[date], tmp_path: Path
) -> None:
    """Roots used by the report's token count too, whatever digest it was made with."""
    kamu = simulated_kamusm(tmp_path / "kamu")
    _seal(world, days[:1], MockTsaProvider(world.tsa), [world.tsa.root_certificate])
    report = tmp_path / "rapor.pdf"
    report.write_bytes(b"%PDF-1.7 report")
    token = MockTsaProvider(kamu.tsa, digest="sha512").stamp(report).token_path.read_bytes()
    info = build_package(
        world.layout,
        days[:1],
        roots=[world.tsa.root_certificate, kamu.root],
        report_pdf=report.read_bytes(),
        report_token=token,
        out_dir=tmp_path / "out",
    )
    with zipfile.ZipFile(info.path) as archive:
        index = json.loads(archive.read("paket.json"))
    assert sorted(root["sha256"] for root in index["roots"]) == sorted(
        [fingerprint(world.tsa.root_certificate), kamu.fingerprint]
    )
    with ArchivePackage(info.path, None) as package:
        assert sv.verify_package(package, deep=False).passed
