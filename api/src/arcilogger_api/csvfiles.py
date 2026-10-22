"""CSV files of "Cihazlar ve Kişiler": exports, import templates and import parsing (spec 9.2).

**Written** (exports, templates): UTF-8 **with BOM**, separator ``;`` and CRLF line ends — what
Turkish Excel opens without an import dialog. Every cell is neutralised against formula
injection: a cell starting with ``=``, ``+``, ``-``, ``@``, TAB or CR gets a leading ``'``
(device host names come from DHCP, i.e. from whoever plugs a device in).

**Read** (imports): UTF-8, BOM optional, at most :data:`MAX_BYTES` and :data:`MAX_ROWS` data rows;
the separator is ``;`` or ``,``, detected from the header line; header names are trimmed and
case-insensitive. Other encodings are refused with a hint instead of being guessed (Excel's
"CSV UTF-8" type writes UTF-8). The neutralising ``'`` of an export is removed again, so an
exported file imports unchanged. Line numbers are those of the file (the header is line 1; a
quoted value with line breaks spans several lines, a row is numbered by its first line).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from fastapi import Response

from arcilogger_api.errors import ApiError
from arcilogger_api.textfold import fold
from arcilogger_core.timeutil import isoformat_site

MAX_BYTES: Final = 5 * 1024 * 1024
MAX_ROWS: Final = 10_000
BOM: Final = "﻿"
SEPARATOR: Final = ";"
#: First characters spreadsheet programs read as the start of a formula.
FORMULA_START: Final = ("=", "+", "-", "@", "\t", "\r")
#: Longest cell accepted (the longest field, notes, allows 2,000 characters).
MAX_CELL: Final = 10_000
#: Accepted booleans, folded (:func:`arcilogger_api.textfold.fold`: ``HAYIR`` → ``hayir``).
TRUE_WORDS: Final = frozenset({"true", "1", "evet", "e", "yes", "y", "aktif", "active"})
FALSE_WORDS: Final = frozenset({"false", "0", "hayir", "h", "no", "n", "pasif", "inactive"})

MESSAGES: Final = {
    "csv_too_large": (
        "Dosya çok büyük (en fazla 5 MB).",
        "The file is too large (5 MB at most).",
    ),
    "csv_too_many_rows": (
        "Dosyada {rows} satır var; en fazla 10.000 satır içe aktarılabilir. Dosyayı bölün.",
        "The file has {rows} rows; at most 10,000 rows can be imported. Split the file.",
    ),
    "csv_encoding": (
        'Dosya UTF-8 kodlamasında değil. Excel\'de "CSV UTF-8 (virgülle ayrılmış)" türüyle '
        "kaydedin.",
        'The file is not UTF-8. Save it in Excel as "CSV UTF-8 (comma delimited)".',
    ),
    "csv_empty": (
        "Dosyada başlık satırı ve en az bir veri satırı olmalıdır.",
        "The file needs a header line and at least one data row.",
    ),
    "csv_malformed": (
        "Dosya CSV olarak okunamadı (satır {line}): {detail}.",
        "The file cannot be read as CSV (line {line}): {detail}.",
    ),
    "csv_header": (
        "Başlık satırı geçersiz: {detail}. Beklenen sütunlar: {columns}.",
        "The header line is invalid: {detail}. Expected columns: {columns}.",
    ),
}


def csv_error(status: int, code: str, **params: object) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(status, code, text_tr.format(**params), text_en.format(**params))


# -- writing --------------------------------------------------------------------------------------


def neutralise(value: str) -> str:
    """A cell a spreadsheet will not run as a formula (``'`` before ``= + - @`` TAB CR)."""
    return "'" + value if value.startswith(FORMULA_START) else value


def cell(value: object) -> str:
    """Text of one exported cell: times in site time with offset, booleans as evet/hayır."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "evet" if value else "hayır"
    if isinstance(value, datetime):
        return isoformat_site(value, timespec="seconds")
    if isinstance(value, list | tuple):
        return ", ".join(str(item) for item in value)
    return str(value)


def write_csv(header: Sequence[str], rows: Iterable[Sequence[object]]) -> bytes:
    """UTF-8 with BOM, ``;``, CRLF, every cell quoted where needed and neutralised."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=SEPARATOR, lineterminator="\r\n")
    writer.writerow([neutralise(name) for name in header])
    for row in rows:
        writer.writerow([neutralise(cell(value)) for value in row])
    return (BOM + buffer.getvalue()).encode("utf-8")


def csv_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# -- reading --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CsvRow:
    #: File line on which the row starts (the header is line 1).
    line: int
    #: Column name (lower case) → trimmed cell; columns missing in a short row are absent.
    values: dict[str, str]

    def get(self, column: str) -> str:
        return self.values.get(column, "")


@dataclass(frozen=True)
class CsvTable:
    #: Lower-case column names in file order.
    columns: tuple[str, ...]
    rows: tuple[CsvRow, ...]


def _restore(value: str) -> str:
    """Undo :func:`neutralise` (``'=…`` → ``=…``), so an exported file imports unchanged."""
    if len(value) > 1 and value[0] == "'" and value[1] in "=+-@\t\r":
        return value[1:]
    return value


def detect_separator(header_line: str) -> str:
    """``;`` unless the header line has more ``,`` than ``;`` outside quotes."""
    semicolons = commas = 0
    quoted = False
    for char in header_line:
        if char == '"':
            quoted = not quoted
        elif not quoted and char == ";":
            semicolons += 1
        elif not quoted and char == ",":
            commas += 1
    return "," if commas > semicolons else ";"


def read_csv(
    data: bytes,
    *,
    required: Collection[str],
    known: Collection[str],
    ignored: Collection[str] = (),
    export_keys: Collection[str] = (),
) -> CsvTable:
    """Parse an upload; raises :class:`ApiError` for problems of the whole file.

    ``required`` columns must be present, ``known`` ones may be, ``ignored`` ones (columns of the
    matching export) are accepted and dropped, ``export_keys`` (an export's column that
    identifies the row, e.g. ``id``) are accepted and kept but not named among the expected
    columns, as a template has none; any other column is refused, so a misspelt header is never
    silently ignored.
    """
    if len(data) > MAX_BYTES:
        raise csv_error(413, "csv_too_large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise csv_error(422, "csv_encoding") from None
    text = text.removeprefix(BOM)
    if "\x00" in text:
        raise csv_error(422, "csv_encoding")
    first_line = text.split("\n", 1)[0]
    if not text.strip():
        raise csv_error(422, "csv_empty")
    expected = ", ".join([*required, *(c for c in known if c not in required)])
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=detect_separator(first_line))
    try:
        header = next(reader)
    except (csv.Error, StopIteration) as exc:
        raise csv_error(422, "csv_malformed", line=1, detail=str(exc) or "empty") from None
    columns = tuple(name.strip().removeprefix(BOM).strip().lower() for name in header)
    duplicates = sorted({name for name in columns if columns.count(name) > 1 and name})
    if duplicates:
        raise csv_error(
            422, "csv_header", detail=f"repeated column {', '.join(duplicates)}", columns=expected
        )
    missing = [name for name in required if name not in columns]
    if missing:
        raise csv_error(
            422, "csv_header", detail=f"missing column {', '.join(missing)}", columns=expected
        )
    allowed = set(required) | set(known) | set(ignored) | set(export_keys)
    unknown = [name for name in columns if name and name not in allowed]
    if unknown:
        raise csv_error(
            422, "csv_header", detail=f"unknown column {', '.join(unknown)}", columns=expected
        )
    keep = [(index, name) for index, name in enumerate(columns) if name and name not in ignored]
    rows: list[CsvRow] = []
    line = reader.line_num + 1
    try:
        for record in reader:
            start, line = line, reader.line_num + 1
            if not any(value.strip() for value in record):
                continue  # empty lines (Excel leaves some at the end)
            if len(rows) >= MAX_ROWS:
                total = MAX_ROWS + 1 + sum(1 for rest in reader if any(v.strip() for v in rest))
                raise csv_error(422, "csv_too_many_rows", rows=total)
            values = {
                name: _restore(record[index].strip())[:MAX_CELL]
                for index, name in keep
                if index < len(record)
            }
            rows.append(CsvRow(line=start, values=values))
    except csv.Error as exc:
        raise csv_error(422, "csv_malformed", line=line, detail=str(exc)) from None
    if not rows:
        raise csv_error(422, "csv_empty")
    return CsvTable(columns=tuple(name for _, name in keep), rows=tuple(rows))


def parse_bool(value: str) -> bool | None:
    """``evet``/``hayır``, ``true``/``false``, ``1``/``0`` … → bool; ``None`` when unknown."""
    word = fold(value.strip())
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    return None
