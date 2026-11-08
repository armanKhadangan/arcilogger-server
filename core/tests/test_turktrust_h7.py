"""TÜRKTRUST's H7 root and a real TÜRKTRUST time stamp (0.3.1).

The files in ``data/turktrust`` are real: ``h7-root.crt`` is the root from TÜRKTRUST's page
"Kök Sertifikaları Kurulumu ve İptal Listeleri", ``stamp-2026-11-08.tsr`` the answer
``https://zd.turktrust.com.tr/`` gave a test account for ``stamp-2026-11-08.txt``, and
``wrong-password.tsr`` its answer to a wrong password. H7 writes its ECDSA signature algorithm
identifiers with an explicit NULL parameter, which the ``cryptography`` package refuses
(:mod:`arcilogger_core.tsa.certparse`); ARCILOGGER 0.3.0 therefore could not read any
TÜRKTRUST stamp. No test calls the TÜRKTRUST server.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core.admin.roots import inspect_root, installed_roots, root_file_name
from arcilogger_core.config import TURKTRUST_ROOT_FINGERPRINTS, TsaConfig
from arcilogger_core.evidence.standalone import verify as standalone
from arcilogger_core.tsa import certparse
from arcilogger_core.tsa.asn1 import parse_container
from arcilogger_core.tsa.base import TsaRejectedError
from arcilogger_core.tsa.verify import (
    confirmed_legal_grade,
    fingerprint,
    inspect_token,
    load_roots_dir,
    verify_token,
)

DATA = Path(__file__).parent / "data" / "turktrust"
ROOT = DATA / "h7-root.crt"
STAMP = DATA / "stamp-2026-11-08.tsr"
STAMPED = DATA / "stamp-2026-11-08.txt"
WRONG_PASSWORD = DATA / "wrong-password.tsr"
#: SHA-256 of the root file on TÜRKTRUST's page.
H7 = "9af399d1773a43c6f5c81831443ec5d6d2ce6a9c9e61dcad232dc1005443d413"
#: A moment the TSA certificate (2025-06-13 to 2030-03-03) is valid at.
NOW = datetime(2026, 11, 8, 13, 0, tzinfo=UTC)


def _h7() -> x509.Certificate:
    return certparse.load_der(ROOT.read_bytes())


# -- reading the root -----------------------------------------------------------------------------


def test_cryptography_alone_refuses_the_h7_root() -> None:
    """The reason for certparse: if a later ``cryptography`` reads H7 itself, this test fails
    and the workaround can go."""
    with pytest.raises(ValueError, match="ParseError"):
        x509.load_der_x509_certificate(ROOT.read_bytes())


def test_the_h7_root_keeps_its_published_fingerprint() -> None:
    root = _h7()
    assert hashlib.sha256(ROOT.read_bytes()).hexdigest() == H7
    assert certparse.fingerprint(root) == fingerprint(root) == H7
    assert certparse.original_der(root) == ROOT.read_bytes()
    assert "Hizmet Sağlayıcısı H7" in root.subject.rfc4514_string()


def test_only_the_null_parameters_are_left_out() -> None:
    original = asn1_x509.Certificate.load(ROOT.read_bytes()).native
    read = asn1_x509.Certificate.load(_h7().public_bytes(Encoding.DER)).native
    for certificate in (original, read):
        certificate["tbs_certificate"]["signature"].pop("parameters", None)
        certificate["signature_algorithm"].pop("parameters", None)
    assert read == original
    assert len(ROOT.read_bytes()) - len(_h7().public_bytes(Encoding.DER)) == 4


def test_the_root_signs_itself_over_its_original_encoding() -> None:
    root = _h7()
    assert certparse.is_self_signed(root) is True


def test_a_pem_written_from_the_root_is_the_published_file() -> None:
    pem = certparse.to_pem(_h7())
    (again,) = certparse.load_certificates(pem)
    assert certparse.original_der(again) == ROOT.read_bytes()
    assert fingerprint(again) == H7


def test_a_certificate_without_the_quirk_is_read_as_it_is() -> None:
    token, _ = parse_container(STAMP.read_bytes())
    ders = [c.chosen.dump() for c in token["content"]["certificates"] if c.name == "certificate"]
    tsa = next(der for der in ders if der != ROOT.read_bytes())
    certificate = certparse.load_der(tsa)
    assert certificate.public_bytes(Encoding.DER) == tsa
    assert certparse.fingerprint(certificate) == hashlib.sha256(tsa).hexdigest()
    assert certparse.is_self_signed(certificate) is False


@pytest.mark.parametrize("cut", [10, 300, 735])
def test_any_other_fault_is_still_refused(cut: int) -> None:
    with pytest.raises(ValueError, match="parsing asn1"):
        certparse.load_der(ROOT.read_bytes()[:cut])


def test_a_pem_without_a_complete_certificate_is_refused() -> None:
    with pytest.raises(ValueError, match="PEM"):
        certparse.load_certificates(b"-----BEGIN CERTIFICATE-----\nAAAA\n")


# -- the real stamp -------------------------------------------------------------------------------


def test_the_real_stamp_is_read() -> None:
    info = inspect_token(STAMP.read_bytes())
    assert info.gen_time == datetime(2026, 11, 8, 12, 35, 40, tzinfo=UTC)
    assert info.tsa_name is not None
    assert "Zaman Damgası Hizmetleri H7" in info.tsa_name
    assert info.message_imprint_hex == hashlib.sha256(STAMPED.read_bytes()).hexdigest()


def test_the_real_stamp_verifies_and_reaches_the_h7_root() -> None:
    """The EKU of TÜRKTRUST's TSA certificate is critical (T3), so the default check passes."""
    result = verify_token(STAMP.read_bytes(), roots=[_h7()], file_path=STAMPED, now=NOW)
    assert result.ok, result.errors
    assert not result.warnings
    assert result.details["root_sha256"] == H7


def test_the_real_stamp_does_not_verify_without_the_root() -> None:
    result = verify_token(STAMP.read_bytes(), roots=[], file_path=STAMPED, now=NOW)
    assert not result.ok
    assert result.details.get("root_sha256") == ""


def test_the_real_stamp_is_legal_grade_with_the_built_in_root() -> None:
    config = TsaConfig.model_validate(
        {"provider": "turktrust", "url": "https://zd.turktrust.com.tr/"}
    )
    assert config.legal_root_fingerprints == (H7,)
    result = verify_token(STAMP.read_bytes(), roots=[_h7()], file_path=STAMPED, now=NOW)
    assert confirmed_legal_grade(config.legal_grade, result, config.legal_root_fingerprints)


def test_the_standalone_verifier_agrees() -> None:
    """ADR-0006: the verifier in evidence packages reaches the same verdict, on its own."""
    roots = standalone.load_certificates(ROOT.read_bytes())
    result = standalone.verify_timestamp(STAMP.read_bytes(), STAMPED.read_bytes(), roots, now=NOW)
    assert not result.errors, result.errors
    assert roots[0].fingerprint().lower() == H7


def test_a_wrong_password_answer_is_read_as_a_rejection() -> None:
    """T2: HTTP 200 and a rejection without any text; failInfo badAlg, badRequest and bit 1."""
    with pytest.raises(TsaRejectedError) as raised:
        parse_container(WRONG_PASSWORD.read_bytes())
    assert raised.value.status == "rejection"
    assert raised.value.texts == ()
    assert raised.value.fail_info == ("bad_alg", "bad_request", "bit 1")


# -- the built-in root ----------------------------------------------------------------------------


def test_the_built_in_root_is_h7() -> None:
    assert TURKTRUST_ROOT_FINGERPRINTS == (H7,)


@pytest.mark.parametrize(
    ("listed", "expected"),
    [
        ([], (H7,)),
        ([H7.upper()], (H7,)),
        (["AA" * 32], ("aa" * 32, H7)),
    ],
)
def test_turktrust_trusts_the_built_in_root(listed: list[str], expected: tuple[str, ...]) -> None:
    config = TsaConfig.model_validate(
        {
            "provider": "turktrust",
            "url": "https://zd.turktrust.com.tr/",
            "legal_root_fingerprints": listed,
        }
    )
    assert config.legal_root_fingerprints == expected


@pytest.mark.parametrize("provider", ["zamane", "rfc3161", "mock"])
def test_other_providers_do_not(provider: str) -> None:
    assert TsaConfig.model_validate({"provider": provider}).legal_root_fingerprints == ()


# -- installing the root --------------------------------------------------------------------------


def test_the_browser_installs_the_h7_root_as_published(tmp_path: Path) -> None:
    inspection = inspect_root(ROOT.read_bytes(), now=NOW)
    assert inspection.ok, inspection.reason
    assert inspection.certificate is not None
    assert inspection.certificate.sha256 == H7
    assert inspection.certificate.self_signed is True
    assert inspection.pem is not None
    (tmp_path / root_file_name(H7)).write_bytes(inspection.pem)
    assert [root.sha256 for root in installed_roots(tmp_path)] == [H7]
    assert [fingerprint(root) for root in load_roots_dir(tmp_path)] == [H7]


def test_the_installer_file_is_found_in_the_roots_folder(tmp_path: Path) -> None:
    (tmp_path / "TURKTRUST_Elektronik_Sertifika_Hizmet_Saglayicisi_h7.crt").write_bytes(
        ROOT.read_bytes()
    )
    (root,) = installed_roots(tmp_path)
    assert (root.sha256, root.source) == (H7, "installer")
    result = verify_token(
        STAMP.read_bytes(), roots=load_roots_dir(tmp_path), file_path=STAMPED, now=NOW
    )
    assert result.ok, result.errors
