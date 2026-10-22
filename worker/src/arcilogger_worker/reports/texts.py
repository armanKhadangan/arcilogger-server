"""Words of the usage reports (Turkish first, English second) and the value formats they share.

Durations and volumes use :mod:`arcilogger_core.fmt` (the twin of the web formatters, tested
against the same vectors): a report and the screen never show the same value differently.
Every duration is an **estimate** (spec 10.3): each report prints :data:`METHOD_NOTE_TR` /
:data:`METHOD_NOTE_EN`, the text of the "tahmini" icon of the web interface.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any, Final

from arcilogger_core.fmt import format_bytes, format_date, format_duration, format_number
from arcilogger_core.timeutil import to_site

PRODUCT: Final = "ARCILOGGER"
METHOD_NOTE_TR: Final = (
    "Tahmini değer: bağlantı kayıtlarından hesaplanır; arka planda açık sekmeler de sayılabilir."
)
METHOD_NOTE_EN: Final = (
    "Estimated value: computed from connection logs; tabs left open in the background may "
    "count too."
)
#: Report kind → (Turkish, English) name.
KIND_NAMES: Final[Mapping[str, tuple[str, str]]] = {
    "end_of_day": ("Gün sonu raporu", "End-of-day report"),
    "full_day": ("Tam gün raporu", "Full-day report"),
    "weekly": ("Haftalık rapor", "Weekly report"),
    "monthly": ("Aylık rapor", "Monthly report"),
    "custom": ("Özel dönem raporu", "Custom period report"),
}
#: Report kind → file name part (ASCII).
KIND_SLUGS: Final[Mapping[str, str]] = {
    "end_of_day": "gun-sonu",
    "full_day": "tam-gun",
    "weekly": "haftalik",
    "monthly": "aylik",
    "custom": "ozel",
}
#: Seal status of the previous day → (Turkish, English).
SEAL_LABELS: Final[Mapping[str, tuple[str, str]]] = {
    "sealed": ("Mühürlendi", "Sealed"),
    "pending": ("Henüz mühürlenmedi", "Not sealed yet"),
    "failed": ("Mühürleme başarısız (yeniden denenecek)", "Sealing failed (will be retried)"),
    "skipped": ("Log yok (boş gün)", "No logs (empty day)"),
    "no_data": ("Veri yok", "No data"),
}
#: The group of the subjects without a department, named as the usage engine names it in the
#: department rows (``arcilogger_worker.usage.query.UNKNOWN_DEPARTMENT``): one name per report.
NO_DEPARTMENT: Final = ("Bilinmiyor", "Unknown")
UNKNOWN_CATEGORY: Final = "Bilinmiyor"
SOURCE_LABELS: Final[Mapping[str, tuple[str, str]]] = {
    "web": ("Web filtresi", "Web filter"),
    "dns": ("DNS filtresi", "DNS filter"),
    "app": ("Uygulama denetimi", "Application control"),
}


def kind_name(kind: str, lang: int = 0) -> str:
    return KIND_NAMES.get(kind, (kind, kind))[lang]


def moment(value: Any) -> datetime | None:
    """An ISO-8601 text (or a datetime) as an aware datetime; ``None`` for anything else."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def when(value: Any) -> str:
    """``19.10.2026 18:30`` (site time) or ``—``."""
    found = moment(value)
    return "—" if found is None else to_site(found).strftime("%d.%m.%Y %H:%M")


def clock(value: Any) -> str:
    """``08:12`` (site time) or ``—``."""
    found = moment(value)
    return "—" if found is None else to_site(found).strftime("%H:%M")


def several_days(start: Any, end: Any) -> bool:
    """Whether the period ``[start, end)`` touches more than one site day."""
    first, last = moment(start), moment(end)
    if first is None or last is None or last <= first:
        return False
    return to_site(first).date() != to_site(last - timedelta(microseconds=1)).date()


def activity(value: Any, *, dated: bool) -> str:
    """A first or last activity in a table: ``08:12``, with ``dated`` ``12.10.2026 08:12`` (a
    report over several days)."""
    return when(value) if dated else clock(value)


def activity_span(first: Any, last: Any) -> str:
    """``12.10.2026 08:05 - 17:58``; ``12.10.2026 08:05 - 16.10.2026 17:58`` across days."""
    start, end = moment(first), moment(last)
    if start is not None and end is not None and to_site(start).date() == to_site(end).date():
        return f"{when(first)} - {clock(last)}"
    return f"{when(first)} - {when(last)}"


def period_text(start: Any, end: Any) -> str:
    """``19.10.2026`` for one whole day, ``12.10.2026 - 18.10.2026`` for whole days, otherwise
    ``20.10.2026 00:00 - 18:30`` / ``… - 21.10.2026 06:00`` (the end is exclusive)."""
    first, last = moment(start), moment(end)
    if first is None or last is None:
        return "—"
    first, last = to_site(first), to_site(last)
    midnight = first.time() == last.time() == datetime.min.time()
    if midnight and last > first:
        final = last - timedelta(days=1)
        if final.date() == first.date():
            return format_date(first)
        return f"{format_date(first)} - {format_date(final)}"
    if first.date() == last.date():
        return f"{first:%d.%m.%Y %H:%M} - {last:%H:%M}"
    return f"{first:%d.%m.%Y %H:%M} - {last:%d.%m.%Y %H:%M}"


def duration(seconds: Any, lang: int = 0) -> str:
    """``3 sa 12 dk`` (estimate) or ``—``."""
    if not isinstance(seconds, int | float) or isinstance(seconds, bool) or seconds < 0:
        return "—"
    return format_duration(seconds, "en" if lang else "tr")


def volume(num_bytes: Any, lang: int = 0) -> str:
    """``38,2 GB`` or ``—``."""
    if not isinstance(num_bytes, int) or isinstance(num_bytes, bool) or num_bytes < 0:
        return "—"
    return format_bytes(num_bytes, "en" if lang else "tr")


def number(value: Any, lang: int = 0) -> str:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "—"
    return format_number(value, 0, "en" if lang else "tr")


def percent(value: Any, lang: int = 0, *, signed: bool = True) -> str:
    """``+%180`` / ``-%62`` in Turkish, ``+180%`` in English (``signed``: with ``+``)."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "—"
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    digits = format_number(abs(value), 0, "en" if lang else "tr")
    return f"{sign}{digits}%" if lang else f"{sign}%{digits}"


def share(value: Any, lang: int = 0) -> str:
    """``%15`` — a share 0..1 as a whole percentage."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "—"
    return percent(value * 100, lang, signed=False)


def day_text(value: Any) -> str:
    """``18.10.2026`` from ``2026-10-18`` (or ``—``)."""
    try:
        return format_date(date.fromisoformat(str(value)))
    except ValueError:
        return "—"


def department(name: Any, lang: int = 0) -> str:
    return name if isinstance(name, str) and name else NO_DEPARTMENT[lang]


def seal_label(status: str, lang: int = 0) -> str:
    return SEAL_LABELS.get(status, (status, status))[lang]


def site_name(report: Mapping[str, Any]) -> str:
    """The organisation name of a report (``[site] site_name``), or ``""`` when none is set:
    every rendering then leaves it out — no empty brackets, no dangling separator."""
    value = report.get("site_name")
    return value.strip() if isinstance(value, str) else ""


def joined(*parts: str, separator: str = " · ") -> str:
    """The non-empty ``parts`` with ``separator`` between them."""
    return separator.join(part for part in parts if part)
