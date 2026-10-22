"""Turkish texts for the technical messages of the evidence path (spec 12.4: Turkish first).

The core verifier, the sealer and the package builder write their findings in English: they are
evidence-path code shared with the command line, the scheduled tasks and the standalone verifier,
and their messages are stored in state files, manifests and the audit log as they are. The web
interface must not show them raw, so the API translates the messages it knows — each pattern is
exactly a message of ARCILOGGER's own code, pinned by ``api/tests/test_issues.py`` — and marks
anything else clearly as a technical detail (``teknik ayrıntı``) instead of guessing.

* :func:`issue_tr` — a verification error or warning of :func:`arcilogger_core.chain.verify_day`
  (also a package build error of :mod:`arcilogger_core.evidence.builder`);
* :func:`token_issue_tr` — a message of the day page's own token check
  (:func:`arcilogger_core.tsa.verify.verify_token`, without the ``token:`` prefix);
* :func:`seal_failure` — the sentence for a failed sealing attempt from the error **code** the
  Seal task records with it (``state/seal/<day>.json`` ``last_error_code``), spec 12.4: "Mühürleme
  başarısız: Kamu SM sunucusuna ulaşılamadı. 15 dk sonra tekrar denenecek.";
* :func:`bilingual_error` — ``"<Türkçe> / <English>"`` for an error stored for the web (the
  pattern of the evidence jobs' own messages).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import datetime
from typing import Final

from arcilogger_core.config import KAMUSM_PRODUCTION_HOST, Config

_Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], str]]


def _rule(pattern: str, text: str | Callable[[re.Match[str]], str]) -> _Rule:
    compiled = re.compile(pattern)
    if callable(text):
        return compiled, text
    return compiled, lambda match: text.format(**match.groupdict())


#: Token verifier messages (``arcilogger_core.tsa.verify``), without the ``token:`` prefix.
_TOKEN_RULES: Final[list[_Rule]] = [
    _rule(
        r"^message imprint does not match the file \(file changed or wrong token\)$",
        "damgadaki özet dosyayla uyuşmuyor (dosya değişmiş ya da damga başka bir dosyanın)",
    ),
    _rule(r"^signature invalid: (?P<kind>.+)$", "damganın imzası geçersiz ({kind})"),
    _rule(r"^genTime is in the future$", "damga zamanı (genTime) gelecekte"),
    _rule(r"^signed attributes missing$", "damgada imzalı öznitelikler yok"),
    _rule(
        r"^message-digest attribute does not match TSTInfo$",
        "damganın message-digest özniteliği TSTInfo ile uyuşmuyor",
    ),
    _rule(r"^TSA certificate has no extended key usage$", "TSA sertifikasında EKU yok"),
    _rule(
        r"^TSA certificate extended key usage is not critical \(RFC 3161 2\.3\)$",
        "TSA sertifikasının genişletilmiş anahtar kullanımı (EKU) kritik işaretli değil "
        "(RFC 3161 2.3)",
    ),
    _rule(
        r"^TSA certificate extended key usage is not (?P<what>.+)$",
        "TSA sertifikasının genişletilmiş anahtar kullanımı (EKU) uygun değil",
    ),
    _rule(
        r"^TSA certificate key usage does not allow signing.*$",
        "TSA sertifikasının anahtar kullanımı imzalamaya izin vermiyor",
    ),
    _rule(
        r"^certificate chain does not reach a pinned root certificate$",
        "sertifika zinciri güvenilen (sabitlenmiş) bir kök sertifikaya ulaşmıyor",
    ),
    _rule(
        r"^certificate (?P<subject>.+) not valid at genTime$",
        "{subject} sertifikası damga zamanında geçerli değil",
    ),
    _rule(r"^certificate chain too long$", "sertifika zinciri çok uzun"),
    _rule(
        r"^issuer (?P<name>.+) has no basic constraints$",
        "{name} yayıncısında temel kısıtlamalar (basicConstraints) yok",
    ),
    _rule(r"^issuer (?P<name>.+) is not a CA$", "{name} yayıncısı bir sertifika otoritesi değil"),
    _rule(
        r"^issuer (?P<name>.+) has no keyCertSign key usage$",
        "{name} yayıncısının sertifika imzalama (keyCertSign) yetkisi yok",
    ),
    _rule(
        r"^the chain exceeds the pathLenConstraint of (?P<name>.+)$",
        "zincir, {name} sertifikasının yol uzunluğu sınırını (pathLenConstraint) aşıyor",
    ),
    _rule(
        r"^ESSCertID issuerSerial does not match the TSA certificate$",
        "ESSCertID issuerSerial alanı TSA sertifikasıyla uyuşmuyor",
    ),
    _rule(
        r"^ESSCertID(?P<v>v2)? does not match the TSA certificate$",
        "ESSCertID{v} TSA sertifikasıyla uyuşmuyor",
    ),
    _rule(
        r"^ESSCertIDv2 uses an unsupported digest (?P<alg>.+)$",
        "ESSCertIDv2 desteklenmeyen bir özet algoritması kullanıyor ({alg})",
    ),
    _rule(
        r"^no signing-certificate attribute \(ESSCertID/ESSCertIDv2\)$",
        "damgada imzalayan sertifika özniteliği (ESSCertID/ESSCertIDv2) yok",
    ),
    _rule(
        r"^unsupported imprint algorithm (?P<alg>.+)$",
        "damgadaki özetin algoritması desteklenmiyor ({alg})",
    ),
    _rule(
        r"^unsupported digest algorithm (?P<alg>.+)$",
        "imzanın özet algoritması desteklenmiyor ({alg})",
    ),
    _rule(
        r"^weak digest algorithm (?P<alg>\S+) for (?P<what>.+) "
        r"\(SHA-256 or stronger is required\)$",
        "zayıf özet algoritması ({alg}); SHA-256 veya daha güçlüsü gerekir",
    ),
    _rule(
        r"^no file or digest given to compare with the imprint$",
        "damgadaki özetle karşılaştırılacak dosya yok",
    ),
    _rule(r"^signed attribute (?P<oid>\S+) appears twice$", "imzalı öznitelik {oid} iki kez var"),
    _rule(
        r"^content-type attribute is not id-ct-TSTInfo$",
        "damganın content-type özniteliği id-ct-TSTInfo değil",
    ),
]

#: ``arcilogger_core.chain.verify_day`` / ``verify_range`` and ``arcilogger_api.sealcheck``.
_RULES: Final[list[_Rule]] = [
    _rule(r"^manifest missing$", "manifest yok"),
    _rule(
        r"^manifest is not canonical JSON \(modified\?\)$",
        "manifest kanonik JSON değil (değiştirilmiş olabilir)",
    ),
    _rule(r"^manifest is not JSON$", "manifest JSON değil"),
    _rule(r"^manifest is for (?P<day>.+)$", "manifest başka bir günün ({day})"),
    _rule(r"^time-stamp token missing$", "zaman damgası dosyası yok"),
    _rule(r"^manifest unreadable$", "manifest okunamıyor"),
    _rule(r"^(?P<path>.+): missing$", "{path}: dosya yok"),
    _rule(
        r"^(?P<path>.+): SHA-256 mismatch \(file changed\)$",
        "{path}: SHA-256 özeti tutmuyor (dosya değişmiş)",
    ),
    _rule(
        r"^(?P<path>.+): content differs from the manifest$",
        "{path}: açılmış içerik manifestle uyuşmuyor",
    ),
    _rule(r"^(?P<path>.+): not read-only$", "{path}: salt okunur değil"),
    _rule(
        r"^chain broken: no link to the previous day$",
        "zincir kopuk: önceki güne bağlantı yok",
    ),
    _rule(r"^chain broken: day (?P<day>\S+) is missing$", "zincir kopuk: {day} günü yok"),
    _rule(
        r"^chain broken: day (?P<day>\S+) was changed$",
        "zincir kopuk: {day} günü sonradan değiştirilmiş",
    ),
    _rule(
        r"^chain broken: link does not point to the day before$",
        "zincir kopuk: bağlantı bir önceki günü göstermiyor",
    ),
    _rule(r"^day missing from the chain$", "gün zincirde yok"),
    _rule(
        r"^chain broken: the skipped days do not end the day before$",
        "zincir kopuk: atlanan boş günler bir önceki günde bitmiyor",
    ),
    _rule(
        r"^(?P<n>\d+) empty day\(s\) (?P<first>\S+) … (?P<last>\S+) were not stamped one by one "
        r"\(no log; listed in this manifest\)$",
        "{n} boş gün ({first} … {last}) tek tek damgalanmadı (hiç log yok; bu manifestte "
        "listeleniyor)",
    ),
    _rule(r"^gaps: (?P<why>.+)$", "manifestin boş gün listesi geçersiz (teknik ayrıntı: {why})"),
    _rule(
        r"^legal-grade claim, but the stamp is from the test TSA \(mock root/policy\)$",
        "manifest Kamu SM üretim sunucusu damgası diyor, ama damga test (mock) zaman damgası "
        "biriminden (test kökü/politikası)",
    ),
    _rule(
        r"^legal-grade claim not confirmed: the root is not in tsa\.legal_root_fingerprints$",
        "manifestteki Kamu SM üretim sunucusu beyanı teyit edilmedi: kök sertifika "
        "tsa.legal_root_fingerprints listesinde değil",
    ),
    _rule(
        r"^unreadable token: (?P<why>.+)$",
        "zaman damgası dosyası okunamıyor (teknik ayrıntı: {why})",
    ),
    _rule(r"^no trusted root certificates.*$", "güvenilen kök sertifika yok (tsa.roots_dir)"),
    _rule(
        r"^time limit of (?P<seconds>\d+) s exceeded; the check was stopped$",
        "{seconds} sn süre sınırı aşıldı; doğrulama durduruldu",
    ),
    # evidence package builder (arcilogger_core.evidence.builder)
    _rule(
        r"^(?P<path>.+): size differs from the manifest \(changed\?\)$",
        "{path}: dosya boyutu manifesttekinden farklı (değişmiş olabilir)",
    ),
    _rule(
        r"^(?P<path>.+): differs from the hash sealed in the manifest$",
        "{path}: manifestte mühürlenen özetten farklı (değişmiş)",
    ),
    _rule(
        r"^(?P<path>.+): listed in the (?P<day>\S+) manifest but not in the vault$",
        "{path}: {day} manifestinde listelenmiş, ama kasada yok",
    ),
    _rule(
        r"^(?P<path>.+): sealed record missing in the vault$", "{path}: mühürlü kayıt kasada yok"
    ),
    _rule(r"^(?P<path>.+): time stamp missing in the vault$", "{path}: zaman damgası kasada yok"),
    _rule(
        r"^(?P<day>\S+): not sealed \(no manifest or time stamp in the vault\)$",
        "{day}: mühürlü değil (kasada manifest veya zaman damgası yok)",
    ),
    _rule(r"^(?P<day>\S+): the manifest is for another day$", "{day}: manifest başka bir günün"),
    _rule(
        r"^(?P<day>\S+): the chain link to (?P<before>\S+) does not match the vault.*$",
        "{day}: {before} gününe zincir bağlantısı kasayla uyuşmuyor (önceki gün, bu gün "
        "mühürlendikten sonra değiştirilmiş) — paket oluşturulmadı",
    ),
    _rule(
        r"^(?P<day>\S+): the time stamp does not verify: (?P<why>.*)$",
        lambda m: f"{m['day']}: zaman damgası doğrulanamadı ({_token_tr(m['why'])})",
    ),
    _rule(
        r"^(?P<day>\S+): the manifest claims a Kamu SM production stamp, but the token is from "
        r"the test \(mock\) TSA — package not built$",
        "{day}: manifest Kamu SM üretim sunucusu damgası diyor, ama damga test (mock) zaman "
        "damgası biriminden — paket oluşturulmadı",
    ),
    _rule(
        r"^none of the requested days has a seal of its own \(no log at all\)$",
        "istenen günlerin hiçbirinin kendi mührü yok (hiç log yok)",
    ),
    _rule(
        r"^(?P<day>\S+): the manifest's list of empty days: (?P<why>.+)$",
        "{day}: manifestin boş gün listesi geçersiz (teknik ayrıntı: {why})",
    ),
]


def _translate(rules: list[_Rule], text: str) -> str | None:
    for pattern, render in rules:
        match = pattern.match(text)
        if match is not None:
            return render(match)
    return None


def _token_tr(text: str) -> str:
    return "; ".join(
        _translate(_TOKEN_RULES, part) or f"teknik ayrıntı: {part}" for part in text.split("; ")
    )


def token_issue_tr(text: str) -> str:
    """Turkish form of one message of the day page's token check: the token verifier's own
    messages (no ``token:`` prefix) and the API's (``no trusted root certificates``,
    ``unreadable token: …``)."""
    return _translate(_TOKEN_RULES, text) or issue_tr(text)


def token_issues_tr(texts: list[str]) -> list[str]:
    return [token_issue_tr(text) for text in texts]


def issue_tr(text: str) -> str:
    """Turkish form of one verification (or package build) message; unknown messages are kept
    as a marked technical detail."""
    if text.startswith("token: "):
        return f"zaman damgası doğrulanamadı: {_token_tr(text.removeprefix('token: '))}"
    return _translate(_RULES, text) or f"teknik ayrıntı: {text}"


def issues_tr(texts: list[str]) -> list[str]:
    return [issue_tr(text) for text in texts]


def bilingual_error(text: str, unknown_tr: str = "Teknik hata") -> str:
    """``"<Türkçe> / <English>"`` for an error message shown in the web interface
    (``unknown_tr``: the Turkish sentence for a message without a translation)."""
    turkish = issue_tr(text)
    if turkish.startswith("teknik ayrıntı: "):
        return f"{unknown_tr} (ayrıntı İngilizce) / {text}"
    return f"{turkish} / {text}"


# -- sealing failures -----------------------------------------------------------------------------

#: ``last_error_code`` (written by the Seal task) → (Turkish, English) cause. ``{server}`` is the
#: time-stamp server as the user knows it (:func:`_server`: Kamu SM only for a Kamu SM host).
SEAL_FAILURES: Final[dict[str, tuple[str, str]]] = {
    "tsa_unavailable": ("{server_tr} ulaşılamadı", "the {server_en} could not be reached"),
    "tsa_no_credit": (
        "zaman damgası kontörü yetersiz ({balance_tr} kontör bakiyesini kontrol edin)",
        "not enough time-stamp credit (check the {balance_en} balance)",
    ),
    "tsa_rejected": (
        "{server_tr} isteği reddetti ya da geçersiz yanıt verdi",
        "the {server_en} refused the request or answered with an invalid response",
    ),
    "tsa_configuration": (
        "zaman damgası ayarları eksik veya hatalı (müşteri no/parola, istemci, kök sertifikalar)",
        "the time-stamp settings are missing or wrong (customer number/password, client, roots)",
    ),
    "seal_error": (
        "gün henüz mühürlenemiyor (ayrıntı gün sayfasında)",
        "the day cannot be sealed yet (details on the day page)",
    ),
    "clock_ahead": (
        "sunucu saati ileride: zaman damgasının saatine göre gün henüz bitmedi; damga atıldı, gün "
        "erken mühürlenmedi (sunucu saatini kontrol edin)",
        "the server clock is ahead: by the time stamp's own time the day has not ended; the stamp "
        "was discarded and the day was not sealed early (check the server clock)",
    ),
}
SEAL_UNKNOWN: Final = (
    "beklenmeyen bir hata oluştu (teknik ayrıntı gün sayfasında)",
    "an unexpected error occurred (technical detail on the day page)",
)


def _is_kamusm(config: Config) -> bool:
    """The configured time-stamp server is a Kamu SM one: a real provider (never the mock, whose
    shipped default URL still names a Kamu SM host) at a ``kamusm.gov.tr`` host."""
    tsa = config.tsa
    host = tsa.host.lower()
    return tsa.provider != "mock" and (host == "kamusm.gov.tr" or host.endswith(".kamusm.gov.tr"))


def _server(config: Config) -> tuple[str, str]:
    """The server as the sentence names it: Kamu SM (production or test) only when it is one."""
    if not _is_kamusm(config):
        return "zaman damgası sunucusuna", "time-stamp server"
    if config.tsa.host.lower() == KAMUSM_PRODUCTION_HOST:
        return "Kamu SM sunucusuna", "Kamu SM server"
    return "Kamu SM test sunucusuna", "Kamu SM test server"


def seal_failure_cause(code: str | None, config: Config) -> tuple[str, str]:
    """The cause of a failed attempt (``"Kamu SM sunucusuna ulaşılamadı"``), Turkish and
    English, without the retry time."""
    server_tr, server_en = _server(config)
    cause_tr, cause_en = SEAL_FAILURES.get(code or "", SEAL_UNKNOWN)
    if code == "tsa_unavailable":
        return cause_tr.format(server_tr=server_tr), cause_en.format(server_en=server_en)
    if code == "tsa_rejected":
        server_tr = server_tr.replace("sunucusuna", "sunucusu")
        return cause_tr.format(server_tr=server_tr), cause_en.format(server_en=server_en)
    if code == "tsa_no_credit":
        kamusm = _is_kamusm(config)
        return (
            cause_tr.format(balance_tr="Kamu SM" if kamusm else "zaman damgası sağlayıcısının"),
            cause_en.format(balance_en="Kamu SM" if kamusm else "time-stamp provider's"),
        )
    return cause_tr, cause_en


def last_error_tr(code: str | None, config: Config) -> str:
    """The "Son hata" line of the day page: the cause as a sentence."""
    cause = seal_failure_cause(code, config)[0]
    return f"{cause[:1].upper()}{cause[1:]}."


def seal_failure(
    code: str | None, config: Config, retry: datetime | None, now: datetime
) -> tuple[str, str]:
    """``("Mühürleme başarısız: <neden>. <n> dk sonra tekrar denenecek.", English)``."""
    cause_tr, cause_en = seal_failure_cause(code, config)
    if retry is None:
        when_tr, when_en = "", ""
    elif retry > now:
        minutes = max(1, math.ceil((retry - now).total_seconds() / 60))
        when_tr = f" {minutes} dk sonra tekrar denenecek."
        when_en = f" It will be retried in {minutes} min."
    else:
        when_tr = " Bir sonraki mühürleme çalışmasında tekrar denenecek."
        when_en = " It will be retried on the next sealing run."
    return (
        f"Mühürleme başarısız: {cause_tr}.{when_tr}",
        f"Sealing failed: {cause_en}.{when_en}",
    )
