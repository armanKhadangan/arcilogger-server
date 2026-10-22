"""Turkish texts for the technical messages of the evidence path (spec 12.4: Turkish first):
every message the real core code produces for a damaged vault has a translation, a failed seal
is described by its error code with the spec sentence, and unknown messages are marked as a
technical detail instead of being guessed."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from arcilogger_api.issues import bilingual_error, issue_tr, seal_failure, token_issue_tr
from arcilogger_core.chain import verify_range
from arcilogger_core.config import Config
from arcilogger_core.evidence import EvidenceError, build_package
from arcilogger_core.immutability import unlock
from arcilogger_core.seal import SealError, failure_code
from arcilogger_core.tsa.base import (
    TsaConfigurationError,
    TsaNoCreditError,
    TsaRejectedError,
    TsaResponseError,
    TsaUnavailableError,
)
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

D1, D2, D3, D4 = (date(2026, 9, day) for day in range(10, 14))
UNTRANSLATED = "teknik ayrıntı"


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[SealedVault]:
    sealed = make_vault(tmp_path / "data", tsa_now=at_site(D1 - timedelta(days=30)))
    sealed.seal_days([D1, D2, D3, D4])
    yield sealed
    sealed.unlock_all()


def test_every_message_of_the_day_check_has_a_turkish_text(vault: SealedVault) -> None:
    layout = vault.layout
    vault.tamper(layout.vault_day(D2) / "fortigate.log.gz")  # hash + not read-only
    vault.tamper(layout.manifest(D3), b" ")  # not canonical, and day 4's link no longer matches
    unlock(layout.token(D4))
    layout.token(D4).unlink()
    missing = layout.vault_day(D1) / "core-r1.log.gz"
    unlock(missing)
    missing.unlink()
    roots = [vault.tsa.root_certificate]
    checks = verify_range(layout, [D1, D2, D3, D4], roots, deep=True)
    checks += verify_range(layout, [D1, D4], roots)  # 11 and 12 missing from the range
    messages = sorted({text for check in checks for text in (*check.errors, *check.warnings)})
    assert len(messages) >= 7, messages
    for text in messages:
        assert not issue_tr(text).startswith(UNTRANSLATED), text
    assert issue_tr("2026/09/11/fortigate.log.gz: SHA-256 mismatch (file changed)") == (
        "2026/09/11/fortigate.log.gz: SHA-256 özeti tutmuyor (dosya değişmiş)"
    )
    assert issue_tr("2026/09/11/fortigate.log.gz: not read-only") == (
        "2026/09/11/fortigate.log.gz: salt okunur değil"
    )


def test_token_messages_are_translated(vault: SealedVault, tmp_path: Path) -> None:
    other = make_vault(tmp_path / "other", tsa_now=at_site(D1 - timedelta(days=30)))
    (check,) = verify_range(vault.layout, [D1], [other.tsa.root_certificate])
    assert check.errors
    for text in check.errors:
        assert issue_tr(text).startswith("zaman damgası doğrulanamadı"), text
        assert UNTRANSLATED not in issue_tr(text), text
        assert UNTRANSLATED not in token_issue_tr(text.removeprefix("token: ")), text
    assert issue_tr(
        "token: message imprint does not match the file (file changed or wrong token)"
    ) == (
        "zaman damgası doğrulanamadı: damgadaki özet dosyayla uyuşmuyor (dosya değişmiş ya da "
        "damga başka bir dosyanın)"
    )


CORE = Path(__file__).resolve().parents[2] / "core" / "src" / "arcilogger_core"

#: (fragment of the core source, a message it produces): the fragment pins the message to the
#: code, so a reworded message fails here instead of silently becoming "teknik ayrıntı".
TOKEN_MESSAGES = [
    (
        '"TSA certificate extended key usage is not critical (RFC 3161 2.3)"',
        "TSA certificate extended key usage is not critical (RFC 3161 2.3)",
    ),
    (
        '"TSA certificate extended key usage is not exactly id-kp-timeStamping"',
        "TSA certificate extended key usage is not exactly id-kp-timeStamping",
    ),
    (
        '"TSA certificate key usage does not allow signing (digitalSignature/nonRepudiation)"',
        "TSA certificate key usage does not allow signing (digitalSignature/nonRepudiation)",
    ),
    (
        '"certificate chain does not reach a pinned root certificate"',
        "certificate chain does not reach a pinned root certificate",
    ),
    ("not valid at genTime", "certificate CN=Kamu SM TSA not valid at genTime"),
    ('"certificate chain too long"', "certificate chain too long"),
    ("has no basic constraints", "issuer CN=Ara has no basic constraints"),
    ("is not a CA", "issuer CN=Ara is not a CA"),
    ("has no keyCertSign key usage", "issuer CN=Ara has no keyCertSign key usage"),
    ("the chain exceeds the pathLenConstraint of", "the chain exceeds the pathLenConstraint of X"),
    (
        '"ESSCertID issuerSerial does not match the TSA certificate"',
        "ESSCertID issuerSerial does not match the TSA certificate",
    ),
    (
        '"ESSCertIDv2 does not match the TSA certificate"',
        "ESSCertIDv2 does not match the TSA certificate",
    ),
    (
        '"ESSCertID does not match the TSA certificate"',
        "ESSCertID does not match the TSA certificate",
    ),
    ("ESSCertIDv2 uses an unsupported digest", "ESSCertIDv2 uses an unsupported digest md5"),
    (
        '"no signing-certificate attribute (ESSCertID/ESSCertIDv2)"',
        "no signing-certificate attribute (ESSCertID/ESSCertIDv2)",
    ),
    ("unsupported imprint algorithm", "unsupported imprint algorithm md2"),
    ("unsupported digest algorithm", "unsupported digest algorithm md2"),
    (
        "(SHA-256 or stronger is required)",
        "weak digest algorithm sha1 for the signature (signerInfo) (SHA-256 or stronger is "
        "required)",
    ),
    (
        '"no file or digest given to compare with the imprint"',
        "no file or digest given to compare with the imprint",
    ),
    ("appears twice", "signed attribute 1.2.840.113549.1.9.3 appears twice"),
    (
        '"content-type attribute is not id-ct-TSTInfo"',
        "content-type attribute is not id-ct-TSTInfo",
    ),
    ('"signed attributes missing"', "signed attributes missing"),
    ('"genTime is in the future"', "genTime is in the future"),
]
DAY_MESSAGES = [
    (
        '"chain broken: the skipped days do not end the day before"',
        "chain broken: the skipped days do not end the day before",
    ),
    (
        "were not stamped one by one",
        "3 empty day(s) 2026-09-01 … 2026-09-03 were not stamped one by one (no log; listed in "
        "this manifest)",
    ),
    ('f"gaps: {exc}"', "gaps: Invalid isoformat string: 'x'"),
    (
        '"legal-grade claim, but the stamp is from the test TSA (mock root/policy)"',
        "legal-grade claim, but the stamp is from the test TSA (mock root/policy)",
    ),
    (
        '"tsa.legal_root_fingerprints"',
        "legal-grade claim not confirmed: the root is not in tsa.legal_root_fingerprints",
    ),
]
BUILD_MESSAGES = [
    (
        'but the token is from the "\n',
        "2026-09-10: the manifest claims a Kamu SM production stamp, but the token is from the "
        "test (mock) TSA — package not built",
    ),
    (
        '"none of the requested days has a seal of its own (no log at all)"',
        "none of the requested days has a seal of its own (no log at all)",
    ),
    ("the manifest's list of empty days:", "2026-09-10: the manifest's list of empty days: bad"),
]


@pytest.mark.parametrize(("fragment", "message"), TOKEN_MESSAGES)
def test_every_token_check_message_has_a_turkish_text(fragment: str, message: str) -> None:
    assert fragment in (CORE / "tsa" / "verify.py").read_text(encoding="utf-8")
    turkish = token_issue_tr(message)
    assert UNTRANSLATED not in turkish, message
    assert UNTRANSLATED not in issue_tr(f"token: {message}"), message


@pytest.mark.parametrize(("fragment", "message"), DAY_MESSAGES)
def test_the_day_checks_new_messages_have_a_turkish_text(fragment: str, message: str) -> None:
    assert fragment in (CORE / "chain.py").read_text(encoding="utf-8")
    assert not issue_tr(message).startswith(UNTRANSLATED), message


@pytest.mark.parametrize(("fragment", "message"), BUILD_MESSAGES)
def test_the_builders_new_messages_have_a_turkish_text(fragment: str, message: str) -> None:
    assert fragment in (CORE / "evidence" / "builder.py").read_text(encoding="utf-8")
    assert not issue_tr(message).startswith(UNTRANSLATED), message


def test_the_day_pages_own_token_messages() -> None:
    assert token_issue_tr("no trusted root certificates") == (
        "güvenilen kök sertifika yok (tsa.roots_dir)"
    )
    assert token_issue_tr("unreadable token: bad tag") == (
        "zaman damgası dosyası okunamıyor (teknik ayrıntı: bad tag)"
    )
    assert token_issue_tr("something new") == "teknik ayrıntı: something new"


def test_package_build_errors_are_turkish_first(vault: SealedVault) -> None:
    layout = vault.layout
    vault.tamper(layout.vault_day(D2) / "fortigate.log.gz")
    with pytest.raises(EvidenceError) as raised:
        build_package(layout, [D2], roots=[vault.tsa.root_certificate])
    text = bilingual_error(str(raised.value), "Paket oluşturulamadı")
    assert not text.startswith("Paket oluşturulamadı (ayrıntı İngilizce)"), text
    turkish, english = text.split(" / ", 1)
    assert "farklı" in turkish
    assert english == str(raised.value)
    unknown = bilingual_error("something new", "Paket oluşturulamadı")
    assert unknown == "Paket oluşturulamadı (ayrıntı İngilizce) / something new"
    assert issue_tr("something new") == "teknik ayrıntı: something new"


def _config(provider: str, url: str) -> Config:
    return Config.model_validate(
        {"data_dir": "data", "secrets_dir": "secrets", "tsa": {"provider": provider, "url": url}}
    )


def test_a_failed_seal_is_described_by_its_code_with_the_spec_sentence() -> None:
    now = datetime(2026, 10, 13, 0, 20, tzinfo=UTC)
    production = _config("zamane", "http://zd.kamusm.gov.tr")
    message_tr, message_en = seal_failure(
        "tsa_unavailable", production, now + timedelta(minutes=15), now
    )
    assert message_tr == (
        "Mühürleme başarısız: Kamu SM sunucusuna ulaşılamadı. 15 dk sonra tekrar denenecek."
    )
    assert message_en == (
        "Sealing failed: the Kamu SM server could not be reached. It will be retried in 15 min."
    )
    mock = _config("mock", "http://localhost")
    text, _ = seal_failure("tsa_unavailable", mock, now - timedelta(minutes=1), now)
    assert text == (
        "Mühürleme başarısız: zaman damgası sunucusuna ulaşılamadı. Bir sonraki mühürleme "
        "çalışmasında tekrar denenecek."
    )
    assert "Kamu SM" not in text  # the mock is not Kamu SM
    for code in (
        "tsa_no_credit",
        "tsa_rejected",
        "tsa_configuration",
        "seal_error",
        "clock_ahead",
        None,
        "x",
    ):
        text, _ = seal_failure(code, production, None, now)
        assert text.startswith("Mühürleme başarısız: ")
        assert text.endswith(".")
        assert "{" not in text


@pytest.mark.parametrize(
    ("provider", "url", "server_tr"),
    [
        # The shipped default: the mock TSA, its URL still the Kamu SM test host — not Kamu SM.
        ("mock", "http://tzd.kamusm.gov.tr", "zaman damgası sunucusu"),
        ("mock", "http://zd.kamusm.gov.tr", "zaman damgası sunucusu"),
        ("zamane", "http://zd.kamusm.gov.tr", "Kamu SM sunucusu"),
        ("zamane", "http://TZD.KAMUSM.GOV.TR", "Kamu SM test sunucusu"),
        ("rfc3161", "http://tzd.kamusm.gov.tr", "Kamu SM test sunucusu"),
        ("rfc3161", "https://tsa.example.org", "zaman damgası sunucusu"),
        ("rfc3161", "https://notkamusm.gov.tr", "zaman damgası sunucusu"),
        ("zamane", "http://127.0.0.1:8318", "zaman damgası sunucusu"),
    ],
)
def test_the_server_is_named_kamu_sm_only_when_it_is_one(
    provider: str, url: str, server_tr: str
) -> None:
    """Never invent facts: the mock TSA is never called Kamu SM, even with the default URL."""
    now = datetime(2026, 10, 13, 0, 20, tzinfo=UTC)
    config = _config(provider, url)
    unreachable, _ = seal_failure("tsa_unavailable", config, None, now)
    assert unreachable == f"Mühürleme başarısız: {server_tr}na ulaşılamadı."
    refused, _ = seal_failure("tsa_rejected", config, None, now)
    assert refused.startswith(f"Mühürleme başarısız: {server_tr} isteği reddetti")
    credit, credit_en = seal_failure("tsa_no_credit", config, None, now)
    assert ("Kamu SM" in credit) is server_tr.startswith("Kamu SM")
    assert ("Kamu SM" in credit_en) is server_tr.startswith("Kamu SM")


def test_the_default_configuration_never_names_kamu_sm() -> None:
    config = Config.model_validate({"data_dir": "data", "secrets_dir": "secrets"})
    assert config.tsa.provider == "mock"
    now = datetime(2026, 10, 13, 0, 20, tzinfo=UTC)
    for code in ("tsa_unavailable", "tsa_rejected", "tsa_no_credit"):
        for text in seal_failure(code, config, now + timedelta(minutes=15), now):
            assert "Kamu SM" not in text, text


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (TsaUnavailableError("down"), "tsa_unavailable"),
        (TsaNoCreditError("no credit"), "tsa_no_credit"),
        (TsaConfigurationError("no jar"), "tsa_configuration"),
        (TsaRejectedError("rejected"), "tsa_rejected"),
        (TsaResponseError("garbage"), "tsa_rejected"),
        (SealError("not closed"), "seal_error"),
        (
            SealError("the time stamp's time is before the end of the day", critical=True),
            "clock_ahead",
        ),
        (OSError(5, "I/O"), "unknown"),
    ],
)
def test_the_seal_task_records_a_stable_error_code(error: Exception, code: str) -> None:
    assert failure_code(error) == code
