"""Display formatting for reports, e-mails and PDFs (Turkish first, English second).

The web interface has a TypeScript twin (``web/src/lib/format.ts``). Both are tested against
the same vectors in ``tests/fixtures/format-vectors.json`` so a report and the screen never show
the same value differently.

Conventions:

* Date and time: ``23.09.2026 09:44:07`` in Europe/Istanbul, for both languages.
* Numbers: Turkish ``1.234.567`` / ``1,5``; English ``1,234,567`` / ``1.5``.
* Bytes: binary multiples (1 KB = 1024 B), as Windows shows them, one decimal at most:
  ``1,5 GB``.
* Durations: the two most significant non-zero units, truncated, e.g. ``2 sa 15 dk``,
  ``15 dk 30 sn``, ``3 gün 4 sa``.
* Rounding is half away from zero on the exact binary value — the same rule JavaScript's
  ``Number.prototype.toFixed`` applies, so both implementations agree digit for digit.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from arcilogger_core.timeutil import to_site

Lang = Literal["tr", "en"]

_SEPARATORS: dict[Lang, tuple[str, str]] = {
    # (thousands separator, decimal separator)
    "tr": (".", ","),
    "en": (",", "."),
}

_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")

_DURATION_UNITS: dict[Lang, tuple[str, str, str, str]] = {
    "tr": ("gün", "sa", "dk", "sn"),
    "en": ("d", "h", "min", "s"),
}


def format_datetime(dt: datetime) -> str:
    """``23.09.2026 09:44:07`` — an aware datetime shown in Europe/Istanbul."""
    return to_site(dt).strftime("%d.%m.%Y %H:%M:%S")


def format_date(value: date | datetime) -> str:
    """``23.09.2026``; a datetime is first converted to Europe/Istanbul."""
    if isinstance(value, datetime):
        value = to_site(value)
    return value.strftime("%d.%m.%Y")


def format_number(value: float, decimals: int = 0, lang: Lang = "tr") -> str:
    """Format a number with grouping and a fixed number of decimals."""
    if decimals < 0:
        raise ValueError("decimals must be >= 0")
    if not math.isfinite(value):
        raise ValueError(f"cannot format non-finite number: {value!r}")
    quantum = Decimal(1).scaleb(-decimals)
    rounded = Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded.is_zero():
        rounded = abs(rounded)  # never print "-0"
    text = f"{rounded:,.{decimals}f}"
    thousands, decimal_sep = _SEPARATORS[lang]
    return text.replace(",", "\0").replace(".", decimal_sep).replace("\0", thousands)


def _round1(value: float) -> Decimal:
    return Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def format_bytes(num_bytes: int, lang: Lang = "tr") -> str:
    """``1,5 GB`` — binary multiples, at most one decimal, trailing ``,0`` dropped."""
    if num_bytes < 0:
        raise ValueError("byte counts cannot be negative")
    if num_bytes < 1024:
        return f"{format_number(num_bytes, 0, lang)} B"
    exponent = 0
    while exponent < len(_BYTE_UNITS) - 1 and num_bytes >= 1024 ** (exponent + 1):
        exponent += 1
    rounded = _round1(num_bytes / 1024**exponent)
    if rounded >= 1024 and exponent < len(_BYTE_UNITS) - 1:
        exponent += 1  # 1023,96 KB is shown as 1 MB, not 1.024 KB
        rounded = _round1(num_bytes / 1024**exponent)
    decimals = 0 if rounded == rounded.to_integral_value() else 1
    return f"{format_number(float(rounded), decimals, lang)} {_BYTE_UNITS[exponent]}"


def format_duration(seconds: float, lang: Lang = "tr") -> str:
    """``2 sa 15 dk`` — the two most significant non-zero units, truncated to whole seconds."""
    if seconds < 0 or not math.isfinite(seconds):
        raise ValueError(f"invalid duration: {seconds!r}")
    total = math.floor(seconds)
    days, rest = divmod(total, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, secs = divmod(rest, 60)
    values = (days, hours, minutes, secs)
    units = _DURATION_UNITS[lang]
    first = next((i for i, amount in enumerate(values) if amount), len(values) - 1)
    text = f"{values[first]} {units[first]}"
    if first + 1 < len(values) and values[first + 1]:
        text += f" {values[first + 1]} {units[first + 1]}"
    return text
