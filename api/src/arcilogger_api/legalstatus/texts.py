"""The fixed texts of the legal-validity status (Wave 5 contract 2.6), Turkish first, and the
revocation texts of the day page.

Every text is a ``(Turkish, English)`` pair; those of 2.6 are character for character as the
contract gives them. ``{…}`` are filled by :mod:`arcilogger_api.legalstatus.compute` and
:mod:`arcilogger_api.sealinfo`. The web app appends the
"ZAMAN-DAMGASI.md bölüm N" reference to a banner text (C13), so these texts carry none. No text
claims legal conformity: a reason says what is missing, never that something is in order.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from cryptography import x509
from cryptography.x509.oid import NameOID

from arcilogger_api.legalstatus.models import LegalReasonCode

#: (Turkish, English)
Text = tuple[str, str]

REASONS: Final[Mapping[LegalReasonCode, Text]] = {
    "provider_not_legal": (
        "Zaman damgaları deneme ya da test sağlayıcısından alınıyor; bu damgaların yasal "
        "geçerliliği yoktur. Ayarlar → Zaman damgası'ndan Kamu SM üretim sunucusunu seçin.",
        "Time stamps come from a trial or test provider and have no legal validity. Choose the "
        "Kamu SM production server under Settings → Time stamping.",
    ),
    "credentials_in_clear": (
        "TÜRKTRUST bağlantısı şifresiz HTTP ile yapılıyor; kullanıcı adı ve parola ağda açık "
        "metin olarak gider. Mümkünse HTTPS'e dönün.",
        "The TÜRKTRUST connection uses unencrypted HTTP; the username and password cross the "
        "network in clear text. Switch back to HTTPS if you can.",
    ),
    "legal_root_missing": (
        "Kök sertifika parmak izi tanımlı değil; hiçbir gün yasal nitelikli gösterilemez.",
        "No legal root fingerprint is set; no day can be shown as legal grade.",
    ),
    "unstamped_days": (
        "{count} kapanmış gün {hours} saatten uzun süredir damgalanmadı.",
        "{count} closed day(s) have not been time-stamped for more than {hours} hours.",
    ),
    "renewal_due": (
        "{count} damganın sertifikası {days} gün içinde sona eriyor; yeniden damgalama bekleniyor.",
        "{count} stamp certificate(s) expire within {days} days; re-stamping is due.",
    ),
    "renewal_uncovered": (
        "{count} damganın sertifikası yeniden damgalanmadan sona erdi; damgalar geçerliliğini "
        "korur, ancak uzun süreli kanıt için yeniden damgalama önerilir.",
        "{count} stamp certificate(s) expired without re-stamping; the stamps stay valid, but "
        "re-stamping is recommended for long-term evidence.",
    ),
    "certificate_revoked": (
        "Zaman damgası sertifikası iptal listesinde: {name}. Zaman damgası sağlayıcısıyla "
        "görüşün; hukuki inceleme önerilir.",
        "A time-stamp certificate is on a revocation list: {name}. Contact the provider; legal "
        "review is recommended.",
    ),
    "crl_stale": (
        "{days} günden yeni doğrulanmış bir iptal listesi (SİL) yok.",
        "No verified revocation list newer than {days} days.",
    ),
}

#: The credit card of a provider without a credit query (``GET /credits`` ``supported``).
NO_CREDIT_QUERY: Final[Text] = (
    "Bu sağlayıcı kontör sorgusu sunmuyor; bakiyeyi sağlayıcının portalından izleyin.",
    "This provider offers no credit query; watch the balance in the provider's portal.",
)

#: A day's re-stamping state on the sealing page, by verifier code.
RENEWAL_DAY: Final[Mapping[str, Text]] = {
    "renewal-covered": (
        "Bu damga, sertifikası sona ermeden yeniden damgalanmış: {renewal}.",
        "Re-stamped before its certificate expired: {renewal}.",
    ),
    "renewal-due": (
        "Damga sertifikası {date} tarihinde sona eriyor; yeniden damgalama bekleniyor.",
        "The stamp certificate expires on {date}; re-stamping is due.",
    ),
    "renewal-late": (
        "Yeniden damgalama, eski sertifikanın süresi dolduktan sonra yapılmış ({date}).",
        "Re-stamped after the old certificate had expired ({date}).",
    ),
    "renewal-uncovered": (
        "Damga sertifikasının süresi {date} tarihinde doldu ve yeniden damgalama yok; damga, "
        "alındığı an için geçerlidir.",
        "The stamp certificate expired on {date} with no re-stamping; the stamp is valid for "
        "the moment it was made.",
    ),
    "renewal-not-due": (
        "Yeniden damgalama henüz gerekmiyor (sertifika {date} tarihine kadar geçerli).",
        "Re-stamping is not due yet (certificate valid until {date}).",
    ),
}

#: The revocation result of a day's time stamp on the sealing page (``revocation``), by the
#: verifiers' code (D10). ``crl-revoked`` is the reason's text; the others are not fixed by the
#: contract (they follow the standalone verifier's wording). ``{date}`` is a site-time date.
REVOCATION: Final[Mapping[str, Text]] = {
    "crl-revoked": REASONS["certificate_revoked"],
    "crl-revoked-later": (
        "Sertifika zaman damgasından sonra iptal edilmiş ({date}): {name}; iptal nedeninin "
        "incelenmesi gerekir.",
        "The certificate was revoked after the stamp ({date}): {name}; the reason for the "
        "revocation should be reviewed.",
    ),
    "crl-hold": (
        "Sertifika SİL'de askıya alınmış görünüyor ({date}): {name}; askının damga anını "
        "kapsayıp kapsamadığı incelenmeli.",
        "The certificate is on hold on a revocation list ({date}): {name}; check whether the "
        "hold covers the stamp time.",
    ),
    "crl-none": (
        "Bu sertifikayı kapsayan saklı SİL yok; iptal durumu bilinmiyor.",
        "No stored revocation list covers this certificate; the revocation status is unknown.",
    ),
    "crl-stale": (
        "Saklı en yeni SİL zaman damgasından önce yayımlanmış; damga anındaki iptal durumu "
        "bilinmiyor.",
        "The newest stored revocation list predates the stamp; the revocation status at the "
        "stamp time is unknown.",
    ),
    "crl-ok": ("SİL'de iptal kaydı yok.", "No revocation entry on the revocation lists."),
}
#: The verifiers' level of each revocation code (D10), when a result does not carry it.
REVOCATION_LEVELS: Final[Mapping[str, str]] = {
    "crl-revoked": "fail",
    "crl-revoked-later": "warn",
    "crl-hold": "warn",
    "crl-none": "info",
    "crl-stale": "info",
    "crl-ok": "ok",
}

#: ``provider_label`` of a provider whose stamps the site claims as legal grade.
LEGAL_PROVIDER_LABELS: Final[Mapping[str, str]] = {"zamane": "Kamu SM", "turktrust": "TÜRKTRUST"}
MOCK_LABEL: Final = "Deneme (mock)"
#: Any other server: ``rfc3161``, or Kamu SM and TÜRKTRUST on a host other than production.
TEST_LABEL: Final = "Test sunucusu"


def provider_label(provider: str, *, legal_grade: bool) -> str:
    """The name of the time-stamp server in use (``legal_grade``: ``tsa.legal_grade``)."""
    if provider == "mock":
        return MOCK_LABEL
    return LEGAL_PROVIDER_LABELS.get(provider, TEST_LABEL) if legal_grade else TEST_LABEL


def certificate_name(subject: str) -> str:
    """A certificate as a reason names it: its common name, else the whole RFC 4514 text."""
    try:
        names = x509.Name.from_rfc4514_string(subject).get_attributes_for_oid(NameOID.COMMON_NAME)
    except ValueError:
        return subject
    value = names[0].value if names else ""
    return value if isinstance(value, str) and value else subject


def reason_text(code: LegalReasonCode, **values: object) -> Text:
    """The reason's message, filled."""
    tr, en = REASONS[code]
    return tr.format(**values), en.format(**values)
