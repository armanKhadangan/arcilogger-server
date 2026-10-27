"""Every error code and notice of Arama, Turkish first, English beside it (ADR-0015, contract 2.20).

:data:`MESSAGES` holds the error codes of the search endpoints and of saved searches with their
HTTP status (:data:`STATUS`); :data:`NOTICES` the notices a search answer carries. Placeholders
in braces have the same names in both languages (a test checks it) and are filled from the
machine-readable ``params`` of the error: ``{reason}`` and ``{format}`` hold a code that is
translated here (:data:`REASONS`, :data:`FORMATS`), every other value is shown as it is.

The query parser raises :class:`SearchQueryError` for anything it refuses, with the character
span of the offending part; :func:`query_error` turns it into the API's error and
:func:`error_item` into an entry of ``POST /search/parse``'s ``errors``. No text here claims
legal compliance; a value the user typed is shown back only to that user, shortened.
"""

from __future__ import annotations

import string
from collections.abc import Mapping
from datetime import date
from typing import Any, Final

from arcilogger_api.errors import ApiError

#: code → (Turkish, English).
MESSAGES: Final[Mapping[str, tuple[str, str]]] = {
    "search_syntax": (
        "Sorgu okunamadı: {reason} ({position}. karakter).",
        "The query cannot be read: {reason} (character {position}).",
    ),
    "search_unknown_field": (
        "“{field}” diye bir alan yok. Alan listesi için “?” düğmesine basın; metin olarak "
        "aramak için tırnak içine alın.",
        "There is no field “{field}”. Press “?” for the list of fields; put it in quotes to "
        "search for it as text.",
    ),
    "search_value_invalid": (
        "“{field}” alanı için “{value}” geçerli değil (beklenen: {format}).",
        "“{value}” is not valid for “{field}” (expected: {format}).",
    ),
    "search_term_too_short": (
        "Serbest metin en az 3 karakter olmalı.",
        "Free text must be at least 3 characters long.",
    ),
    "search_too_complex": (
        "Sorgu çok uzun: en fazla 1.000 karakter, 16 koşul, koşul başına 20 değer, değer başına "
        "256 karakter ve 5 serbest metin terimi olabilir.",
        "The query is too long: at most 1,000 characters, 16 conditions, 20 values per "
        "condition, 256 characters per value and 5 free-text terms.",
    ),
    "search_or_unsupported": (
        "VEYA yalnızca aynı alanın değerleri arasında virgülle yazılır (ör. "
        "action:deny,blocked); parantez kullanılamaz.",
        "OR works only between values of one field, written with commas (e.g. "
        "action:deny,blocked); parentheses are not supported.",
    ),
    "search_fields_incompatible": (
        "Bu alanlar hiçbir kayıt türünde birlikte bulunmuyor: {fields}.",
        "No record type has all of these fields: {fields}.",
    ),
    "search_vlan_unknown": (
        "{vlan} numaralı bir VLAN tanımlı değil (Cihazlar ve Kişiler → VLAN'lar).",
        "No VLAN {vlan} is defined (Devices and persons → VLANs).",
    ),
    "search_department_unknown": (
        "“{department}” adında bir departman yok.",
        "There is no department named “{department}”.",
    ),
    "search_range_invalid": (
        "Zaman aralığı geçersiz: başlangıç bitişten önce olmalı ve ikisinde de saat dilimi "
        "bulunmalı.",
        "Invalid time range: the start must be before the end, and both need a time zone.",
    ),
    "search_range_too_long": (
        "Arama aralığı en fazla {days} gün olabilir.",
        "The search range may span at most {days} days.",
    ),
    "search_expired": (
        "Bu aramanın süresi doldu; arama yeniden çalıştırılıyor.",
        "This search has expired; it is being run again.",
    ),
    "search_cursor_invalid": (
        "Sayfa konumu bu aramaya ait değil; aramayı yeniden başlatın.",
        "The page position does not belong to this search; start the search again.",
    ),
    "search_timeout": (
        "Arama {seconds} saniyede en kısa zaman dilimini (5 dakika) bile tarayamadı. Seçici bir "
        "alan ekleyin (ör. srcip:, domain:) ya da biraz sonra yeniden deneyin.",
        "The search could not scan even the shortest time slice (5 minutes) within {seconds} s. "
        "Add a selective field (e.g. srcip:, domain:) or try again a little later.",
    ),
    "search_busy": (
        "Şu anda çok sayıda arama çalışıyor; birkaç saniye sonra yeniden deneyin.",
        "Too many searches are running; try again in a few seconds.",
    ),
    "search_rate_limited": (
        "Kısa sürede çok fazla arama yaptınız; {seconds} saniye sonra yeniden deneyin.",
        "Too many searches in a short time; try again in {seconds} s.",
    ),
    "search_event_not_found": (
        "Kayıt veritabanında bulunamadı.",
        "The record was not found in the database.",
    ),
    "search_event_timeout": (
        "Kayıt {seconds} saniye içinde okunamadı; veritabanı şu anda yoğun. Biraz sonra yeniden "
        "deneyin.",
        "The record could not be read within {seconds} s; the database is busy. Try again a "
        "little later.",
    ),
    "search_export_running": (
        "Önceki dışa aktarmanız hâlâ hazırlanıyor.",
        "Your previous export is still being prepared.",
    ),
    "search_export_rate_limited": (
        "Saatte en fazla {n} dışa aktarma yapılabilir; {minutes} dakika sonra yeniden deneyin.",
        "At most {n} exports per hour; try again in {minutes} min.",
    ),
    "saved_search_not_found": ("Kayıtlı arama bulunamadı.", "Saved search not found."),
    "saved_search_name_taken": (
        "Bu adla kayıtlı bir aramanız zaten var.",
        "You already have a saved search with this name.",
    ),
    "saved_search_limit": (
        "En fazla {n} kayıtlı arama saklanabilir.",
        "At most {n} saved searches can be kept.",
    ),
}

#: code → HTTP status of the error.
STATUS: Final[Mapping[str, int]] = {
    "search_syntax": 422,
    "search_unknown_field": 422,
    "search_value_invalid": 422,
    "search_term_too_short": 422,
    "search_too_complex": 422,
    "search_or_unsupported": 422,
    "search_fields_incompatible": 422,
    "search_vlan_unknown": 422,
    "search_department_unknown": 422,
    "search_range_invalid": 422,
    "search_range_too_long": 422,
    "search_expired": 409,
    "search_cursor_invalid": 422,
    "search_timeout": 504,
    "search_busy": 429,
    "search_rate_limited": 429,
    "search_event_not_found": 404,
    "search_event_timeout": 504,
    "search_export_running": 409,
    "search_export_rate_limited": 429,
    "saved_search_not_found": 404,
    "saved_search_name_taken": 409,
    "saved_search_limit": 409,
}

#: Notices of a search answer (``Notice {code, message_tr, message_en, params}``).
NOTICES: Final[Mapping[str, tuple[str, str]]] = {
    "vault_only_before": (
        "{date} öncesine ait kayıtlar veritabanında yok; o günlerin ham kayıtları varsa mühürlü "
        "arşivdedir (Kanıt Paketi ile alınabilir).",
        "Records before {date} are not in the database; raw lines of those days, if any, are in "
        "the sealed vault (available through an evidence package).",
    ),
    "counts_incomplete": (
        "Yükseltmeden önce yüklenen günlerin özet sayıları hâlâ hazırlanıyor; istatistikler "
        "doğrudan sayıldı (daha yavaş).",
        "Summary counts for days loaded before the upgrade are still being prepared; the "
        "statistics were counted directly (slower).",
    ),
    "text_case": (
        "Serbest metinde büyük/küçük harf farkı yalnızca İngilizce harflerde yok sayılır; İ/ı, "
        "Ş/ş, Ğ/ğ, Ü/ü, Ö/ö, Ç/ç yazıldığı gibi aranır.",
        "Free text ignores case for English letters only; İ/ı, Ş/ş, Ğ/ğ, Ü/ü, Ö/ö, Ç/ç are "
        "matched as typed.",
    ),
    "text_truncated": (
        "Serbest metin her kaydın ilk 2.000 karakterinde aranır; satırın tamamı ham kayıttadır.",
        "Free text is matched in the first 2,000 characters of each record; the whole line is "
        "in the raw record.",
    ),
    "query_not_selective": (
        "Bu sorgu indeks kullanamıyor ve zaman dilimleri halinde taranıyor. Daha hızlı sonuç "
        "için srcip:, domain:, mac: veya qname: gibi bir alan ekleyin.",
        "This query cannot use an index and is scanned in time slices. Add a field such as "
        "srcip:, domain:, mac: or qname: for faster results.",
    ),
    "department_by_vlan": (
        "Departman, istemci adresinin VLAN'ına tanımlı varsayılan departmandır; kişi ataması "
        "değildir.",
        "The department is the default department of the client address's VLAN, not the "
        "person's assignment.",
    ),
}

#: ``{reason}`` of ``search_syntax``: what could not be read.
REASONS: Final[Mapping[str, tuple[str, str]]] = {
    "unclosed_quote": ("kapanmamış tırnak işareti", "a quotation mark is not closed"),
    "bad_escape": (
        'tırnak içinde \\ yalnızca \\" ya da \\\\ olarak yazılır',
        'inside quotes, \\ is written only as \\" or \\\\',
    ),
    "unexpected_quote": ("beklenmeyen tırnak işareti", "an unexpected quotation mark"),
    "empty_value": ("alanın değeri boş", "a field without a value"),
    "missing_clause": (
        "NOT ya da - işaretinden sonra koşul yok",
        "no condition follows NOT or -",
    ),
    "missing_field": ("“:” işaretinden önce alan adı yok", "no field name before “:”"),
    "control_character": ("sorguda bir denetim karakteri var", "a control character"),
    "star_alone": (
        "* (alan dolu) başka bir değerle birlikte yazılamaz",
        "* (the field has a value) cannot be listed with other values",
    ),
}

#: ``{format}`` of ``search_value_invalid``: what the field accepts (by value form).
FORMATS: Final[Mapping[str, tuple[str, str]]] = {
    "ip": (
        "IP adresi ya da ağ, ör. 172.20.41.5, 172.20.41.0/24, 172.20.41.*",
        "an IP address or network, e.g. 172.20.41.5, 172.20.41.0/24, 172.20.41.*",
    ),
    "int": (
        "sayı, aralık ya da karşılaştırma, ör. 443, 1000-2000, >1024",
        "a number, range or comparison, e.g. 443, 1000-2000, >1024",
    ),
    "port": (
        "0-65535 arası port, aralık ya da karşılaştırma, ör. 443, 1000-2000, >1024",
        "a port 0-65535, a range or a comparison, e.g. 443, 1000-2000, >1024",
    ),
    "proto": (
        "tcp, udp, icmp ya da 0-255 arası protokol numarası",
        "tcp, udp, icmp or a protocol number 0-255",
    ),
    "mac": (
        "MAC adresi ya da üretici öneki, ör. 00:00:5e:00:53:21, 00:00:5e:*",
        "a MAC address or vendor prefix, e.g. 00:00:5e:00:53:21, 00:00:5e:*",
    ),
    "domain": (
        "alan adı, ör. example.com (alt alan adlarıyla) ya da *.example.com",
        "a domain, e.g. example.com (with its subdomains) or *.example.com",
    ),
    "name": ("tam DNS adı, ör. www.example.com", "an exact DNS name, e.g. www.example.com"),
    "token": ("tek bir değer, ör. deny", "a single value, e.g. deny"),
    "text": (
        "metin; başında ya da sonunda * olabilir, ör. admin*",
        "text, with * at its start or end if needed, e.g. admin*",
    ),
    "url": ("URL'nin bir parçası", "a part of a URL"),
    "source": (
        "kaynak adı; sonunda * olabilir, ör. fortigate, core-*",
        "a source name, with * at its end if needed, e.g. fortigate, core-*",
    ),
    "kind": (
        "kayıt türü (alan listesindeki türlerden biri), ör. web",
        "a record type (one of the types in the field list), e.g. web",
    ),
    "severity": (
        "emerg, alert, crit, err, warning, notice, info, debug ya da 0-7, ör. <=err",
        "emerg, alert, crit, err, warning, notice, info, debug or 0-7, e.g. <=err",
    ),
    "error": (
        "ayrıştırma hatası adı, öneki ya da *, ör. fortigate.*",
        "a parse error name, a prefix or *, e.g. fortigate.*",
    ),
    "policy": (
        "politika numarası ya da adı",
        "a policy number or name",
    ),
    "vlan": ("1-4094 arası VLAN numarası", "a VLAN number 1-4094"),
    "department": ("departman adı ya da numarası", "a department name or number"),
    "freetext": ("en az 3 karakterlik metin", "text of at least 3 characters"),
}

#: Sentences appended to ``search_unknown_field`` for a word that looks like something else.
HINTS: Final[Mapping[str, tuple[str, str]]] = {
    "mac": (
        "MAC adresi aramak için: {value}",
        "To search for a MAC address: {value}",
    ),
    "quote": (
        "Adres ya da URL gibi “:” içeren bir değeri tırnak içinde arayın: {value}",
        "Search for a value with “:”, such as an address or a URL, in quotes: {value}",
    ),
}

#: A value echoed in a message is cut to this many characters.
SHOWN_VALUE_MAX: Final = 64


class SearchQueryError(ValueError):
    """The query cannot be used: ``code`` of :data:`MESSAGES`, the 0-based half-open character
    span ``[start, end)`` of the offending part and the ``params`` of its text."""

    def __init__(self, code: str, start: int, end: int, **params: Any) -> None:
        if code not in MESSAGES:  # pragma: no cover - a programming error
            raise KeyError(code)
        super().__init__(f"{code} at {start}-{end}")
        self.code = code
        self.start = start
        self.end = max(end, start)
        self.params = params


def placeholders(text: str) -> set[str]:
    """The ``{names}`` of a message."""
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def _shown(value: Any) -> str:
    text = str(value)
    return text if len(text) <= SHOWN_VALUE_MAX else text[: SHOWN_VALUE_MAX - 1] + "…"


def _values(params: Mapping[str, Any], language: int) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, value in params.items():
        if name == "reason":
            values[name] = REASONS[value][language]
        elif name == "format":
            values[name] = FORMATS[value][language]
        elif name in ("value", "field", "department"):
            values[name] = _shown(value)
        elif name == "date":  # ``params`` keep the ISO day; the texts write it as the UI does
            values[name] = date.fromisoformat(value).strftime("%d.%m.%Y")
        else:
            values[name] = value
    return values


def texts(
    code: str, params: Mapping[str, Any] | None = None, *, catalogue: str = "errors"
) -> tuple[str, str]:
    """The Turkish and English text of an error code (``catalogue="notices"``: a notice)."""
    table = NOTICES if catalogue == "notices" else MESSAGES
    message_tr, message_en = table[code]
    params = params or {}
    hint = params.get("hint")
    text_tr = message_tr.format(**_values(params, 0))
    text_en = message_en.format(**_values(params, 1))
    if hint is not None:
        value = _shown(params.get("suggestion", ""))
        text_tr = f"{text_tr} {HINTS[hint][0].format(value=value)}"
        text_en = f"{text_en} {HINTS[hint][1].format(value=value)}"
    return text_tr, text_en


def search_error(
    code: str,
    params: Mapping[str, Any] | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    details: Any = None,
) -> ApiError:
    """The API error of ``code`` with its texts and status."""
    message_tr, message_en = texts(code, params)
    return ApiError(STATUS[code], code, message_tr, message_en, headers=headers, details=details)


def query_error(exc: SearchQueryError) -> ApiError:
    """A refused query as a 422 error; ``details`` carries the span and the params."""
    return search_error(
        exc.code,
        exc.params,
        details={"start": exc.start, "end": exc.end, "params": exc.params},
    )


def error_item(exc: SearchQueryError) -> dict[str, Any]:
    """A refused query as an entry of ``ParseResult.errors``."""
    message_tr, message_en = texts(exc.code, exc.params)
    return {
        "code": exc.code,
        "message_tr": message_tr,
        "message_en": message_en,
        "start": exc.start,
        "end": exc.end,
    }


def notice(code: str, **params: Any) -> dict[str, Any]:
    """A ``Notice`` of a search answer."""
    message_tr, message_en = texts(code, params, catalogue="notices")
    return {"code": code, "message_tr": message_tr, "message_en": message_en, "params": params}
