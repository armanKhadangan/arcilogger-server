"""Licence error codes with their Turkish and English texts (licensing contract 6.5).

The API answers ``{"error": {"code", "message_tr", "message_en", "details"?}}`` from a
:class:`LicenseError`; the CLI and the issuer print the same texts. Dates in messages are
``DD.MM.YYYY`` in Turkish and ``YYYY-MM-DD`` in English: give a template parameter as a
:class:`datetime.date` and each language gets its own form.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Final

from arcilogger_core.fmt import format_date
from arcilogger_core.license._constants import LICENSOR_EMAIL

#: code -> (message_tr, message_en); ``str.format`` templates.
MESSAGES: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "license_too_large": (
            "Lisans dosyası çok büyük (en çok 64 KiB).",
            "The licence file is too large (at most 64 KiB).",
        ),
        "license_malformed": (
            "Dosya bir ARCILOGGER lisans dosyası değil ya da bozuk.",
            "The file is not an ARCILOGGER licence file or it is damaged.",
        ),
        "license_unknown_key": (
            "Lisans, bu sürümün tanımadığı bir anahtarla imzalanmış ({key_id}).",
            "The licence is signed with a key this version does not know ({key_id}).",
        ),
        "license_signature_invalid": (
            "Lisans dosyasının imzası geçersiz: dosya değiştirilmiş olabilir.",
            "The licence file's signature is invalid: the file may have been changed.",
        ),
        "license_revoked": (
            "Bu lisans iptal edilmiş ({license_id}).",
            "This licence has been revoked ({license_id}).",
        ),
        "license_server_id_unavailable": (
            "Sunucu kimliği okunamadı; lisans bu sunucuya göre denetlenemiyor.",
            "The server id cannot be read; the licence cannot be checked against this server.",
        ),
        "license_other_server": (
            "Lisans bu sunucu için düzenlenmemiş (bu sunucu: {server_id}). Yeni lisans için "
            "istek kodunu gönderin.",
            "The licence was not issued for this server (this server: {server_id}). Send the "
            "request code for a new licence.",
        ),
        "license_expired": (
            "Lisansın geçerlilik süresi {date} tarihinde doldu.",
            "The licence expired on {date}.",
        ),
        "license_version_not_covered": (
            "Bu sürüm ({version}, yayın tarihi {release_date}) lisansın bakım süresinden "
            "({until} tarihine kadar) sonra yayımlandı.",
            "This version ({version}, released {release_date}) was released after the "
            "licence's maintenance period (until {until}).",
        ),
        "license_not_installed": (
            "Kurulu bir lisans yok.",
            "No licence is installed.",
        ),
        "license_unavailable": (
            "Lisans bileşeni yüklenemedi: ücretli modüller kapalı. 5651 işlevleri çalışmaya "
            "devam ediyor.",
            "The licence component could not be loaded: paid modules are off. The 5651 "
            "functions keep working.",
        ),
        "trial_clock_inconsistent": (
            "Deneme başlangıcı sistem saatinden ileride: deneme süresi kullanılamıyor. Deneme "
            f"lisansı için {LICENSOR_EMAIL} ile iletişime geçin.",
            "The trial start lies after the current time: the trial is unavailable. Contact "
            f"{LICENSOR_EMAIL} for a demo licence.",
        ),
        "module_not_licensed": (
            "Bu özellik lisansınızda yok: {module_tr}. Ayrıntılar Ayarlar → Lisans sayfasında.",
            "This feature is not part of your licence: {module_en}. See Settings → Licence.",
        ),
        "license_limit": (
            "Kullanıcı sayısı lisans sınırında ({used}/{max}): yeni kullanıcı eklenemez ya da "
            "yeniden etkinleştirilemez. Var olan kullanıcılar çalışmaya devam eder.",
            "The number of users is at the licence limit ({used}/{max}): no user can be added "
            "or re-enabled. Existing users keep working.",
        ),
        "request_malformed": (
            "İstek kodu okunamadı.",
            "The request code cannot be read.",
        ),
        "request_checksum": (
            "İstek kodu eksik ya da hatalı kopyalanmış (denetim toplamı tutmuyor).",
            "The request code is incomplete or mistyped (checksum mismatch).",
        ),
    }
)

#: code -> HTTP status of the API answer (codes without one: a state problem or the issuer).
HTTP_STATUS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "license_too_large": 413,
        "license_malformed": 422,
        "license_unknown_key": 422,
        "license_signature_invalid": 422,
        "license_revoked": 422,
        "license_server_id_unavailable": 409,
        "license_other_server": 409,
        "license_expired": 409,
        "license_version_not_covered": 409,
        "license_not_installed": 404,
        "license_unavailable": 503,
        "module_not_licensed": 403,
        "license_limit": 409,
    }
)

#: Status for a code without an HTTP column (never answered by the API in practice).
DEFAULT_HTTP_STATUS: Final = 422


class _Template(dict[str, str]):
    """Format arguments that leave an unknown placeholder visible instead of failing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _text(value: object, lang: str) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return format_date(value) if lang == "tr" else value.isoformat()
    return str(value)


class LicenseError(Exception):
    """A licence decision with a published code (one of :data:`MESSAGES`).

    ``params`` fill the message templates (a :class:`datetime.date` is formatted per language);
    ``details`` is the JSON ``details`` object of the API error (contract 6.5); ``license_id``
    and ``key_id`` name the refused file when they could be read (refusal records), else None;
    ``reason`` is a technical explanation for logs and the issuer (never shown as the message).
    """

    def __init__(
        self,
        code: str,
        params: Mapping[str, object] | None = None,
        *,
        details: Mapping[str, Any] | None = None,
        license_id: str | None = None,
        key_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        if code not in MESSAGES:
            raise ValueError(f"unknown licence error code {code!r}")
        values = dict(params or {})
        self._values = values
        self.code: str = code
        self.params: Mapping[str, str] = MappingProxyType(
            {key: _text(value, "en") for key, value in values.items()}
        )
        self._params_tr: Mapping[str, str] = MappingProxyType(
            {key: _text(value, "tr") for key, value in values.items()}
        )
        self.details: Mapping[str, Any] = MappingProxyType(dict(details or {}))
        self.license_id = license_id
        self.key_id = key_id
        self.reason = reason
        super().__init__(f"{code}: {self.message_en}")

    def __reduce__(self) -> tuple[Any, ...]:
        keywords = {
            "details": dict(self.details),
            "license_id": self.license_id,
            "key_id": self.key_id,
            "reason": self.reason,
        }
        return (_rebuild, (self.code, self._values, keywords))

    @property
    def message_tr(self) -> str:
        return MESSAGES[self.code][0].format_map(_Template(self._params_tr))

    @property
    def message_en(self) -> str:
        return MESSAGES[self.code][1].format_map(_Template(self.params))

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, DEFAULT_HTTP_STATUS)


def _rebuild(code: str, params: Mapping[str, object], keywords: Mapping[str, Any]) -> LicenseError:
    """Unpickle a :class:`LicenseError` (``__reduce__``)."""
    return LicenseError(code, params, **keywords)
