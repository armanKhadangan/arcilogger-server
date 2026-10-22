"""Optional vendor lookup from an IEEE OUI file the administrator places on the server.

ARCILOGGER does **not** bundle or download the IEEE registry (it changes often and the server
has no internet access in normal operation). Without a file the device vendor stays empty.
docs/CORRELATION.md explains where to get the public listing and how to configure the path
(setting ``devices.oui_file``).

Accepted formats (detected per line, so a mixed or slightly different file still works):

* IEEE CSV (``Registry,Assignment,Organization Name,…``): the assignment is 6 (MA-L), 7 (MA-M)
  or 9 (MA-S) hex digits;
* IEEE text listing: ``00-00-0C   (hex)\t\tCisco Systems, Inc``;
* a simple two-column file: ``00:00:0C<TAB or , or ;>Vendor`` (optionally ``/bits`` after the
  prefix, as in Wireshark's ``manuf``).

*Verify on site* with the file actually downloaded: unrecognised lines are counted and skipped.
Lookups use the longest matching prefix. Randomised (locally administered) addresses have no
vendor by definition. A path that cannot be read (a folder, no permission) is logged once and
treated like no file: the vendor is optional and must never stop the lease jobs.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

_HEX_LINE: Final = re.compile(
    r"^\s*(?P<prefix>[0-9A-Fa-f]{2}(?:[-:][0-9A-Fa-f]{2}){2})\s+\(hex\)\s+(?P<vendor>.+?)\s*$"
)
_SIMPLE_LINE: Final = re.compile(
    r"^\s*(?P<prefix>[0-9A-Fa-f]{2}(?:[-:.]?[0-9A-Fa-f]{1,2}){2,5})(?:/(?P<bits>\d{1,2}))?"
    r"\s*[\t,;]\s*(?P<vendor>.+?)\s*$"
)
_CSV_HEADER: Final = re.compile(r"^\s*Registry\s*,\s*Assignment\s*,", re.IGNORECASE)
_NON_HEX: Final = re.compile(r"[^0-9a-f]")


def _digits(value: str) -> str:
    return _NON_HEX.sub("", value.lower())


@dataclass
class OuiTable:
    """Prefix (lower-case hex digits) → vendor."""

    prefixes: dict[str, str] = field(default_factory=dict)
    skipped_lines: int = 0

    @property
    def lengths(self) -> list[int]:
        return sorted({len(p) for p in self.prefixes}, reverse=True)

    def add(self, prefix: str, vendor: str) -> None:
        digits = _digits(prefix)
        name = vendor.strip()
        if 6 <= len(digits) <= 12 and name:
            self.prefixes.setdefault(digits, name)

    def lookup(self, mac: str) -> str | None:
        digits = _digits(mac)
        if len(digits) != 12:
            return None
        for length in self.lengths:
            vendor = self.prefixes.get(digits[:length])
            if vendor is not None:
                return vendor
        return None

    def __len__(self) -> int:
        return len(self.prefixes)

    @classmethod
    def parse(cls, text: str) -> OuiTable:
        table = cls()
        lines = text.splitlines()
        if lines and _CSV_HEADER.match(lines[0]):
            reader = csv.reader(io.StringIO(text))
            next(reader, None)
            for row in reader:
                if len(row) >= 3 and re.fullmatch(r"[0-9A-Fa-f]{6,9}", row[1].strip()):
                    table.add(row[1], row[2])
                elif any(cell.strip() for cell in row):
                    table.skipped_lines += 1
            return table
        for line in lines:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if "(base 16)" in line:
                continue  # the IEEE text listing repeats each entry in base 16
            match = _HEX_LINE.match(line) or _SIMPLE_LINE.match(line)
            if match is None:
                if not line[:1].isspace():
                    table.skipped_lines += 1
                continue
            prefix = _digits(match.group("prefix"))
            bits = match.groupdict().get("bits")
            if bits is not None:
                prefix = prefix[: int(bits) // 4]
            table.add(prefix, match.group("vendor").split("\t")[-1])
        return table

    @classmethod
    def load(cls, path: Path) -> OuiTable:
        return cls.parse(path.read_bytes().decode("utf-8", errors="replace"))


log = logging.getLogger(__name__)
_CACHE: dict[Path, tuple[int, int, OuiTable]] = {}
#: Paths already reported as unreadable (with the error), so the warning is logged once.
_REPORTED: dict[Path, str] = {}


def load_oui(path: str | Path | None) -> OuiTable | None:
    """The table for ``path`` (re-read only when the file changed); ``None`` without a
    readable file."""
    if path is None or not str(path).strip():
        return None
    file = Path(str(path).strip())
    try:
        stat = file.stat()
        cached = _CACHE.get(file)
        if cached is not None and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            return cached[2]
        table = OuiTable.load(file)
    except OSError as exc:
        error = type(exc).__name__
        if _REPORTED.get(file) != error:
            log.warning("OUI file %s cannot be read (%s); vendors stay empty", file, error)
            _REPORTED[file] = error
        return None
    _REPORTED.pop(file, None)
    _CACHE[file] = (stat.st_mtime_ns, stat.st_size, table)
    return table
