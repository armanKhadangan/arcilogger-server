"""Raw-line extracts: the few lines a 5651 lookup is about, without unpacking gigabytes.

An extract file ``extract/<day>/<source>.txt`` holds, in line-number order::

    <line number>\\t<raw spool line, byte for byte, without its \\n>\\n

Raw spool lines never contain a line feed (the collector escapes every control byte as
``#ooo``, see :mod:`arcilogger_core.rawlog`), so one extract line is exactly one raw line, and the
standalone verifier can compare each one with the same line of the sealed file in the package.
Nothing is decoded or re-encoded: non-UTF-8 bytes stay as they arrived.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterable
from pathlib import Path

from arcilogger_core.evidence.model import EvidenceError


def read_lines(sealed: Path, numbers: Iterable[int]) -> dict[int, bytes]:
    """Lines ``numbers`` (1-based) of a sealed ``.log.gz`` file, without their ``\\n``.

    The file is streamed and reading stops after the highest wanted line.
    """
    wanted = set(numbers)
    if not wanted:
        return {}
    last = max(wanted)
    found: dict[int, bytes] = {}
    with gzip.open(sealed, "rb") as handle:
        for number, line in enumerate(handle, start=1):
            if number in wanted:
                found[number] = line[:-1] if line.endswith(b"\n") else line
            if number >= last:
                break
    missing = sorted(wanted - set(found))
    if missing:
        raise EvidenceError(
            f"{sealed.name}: line(s) {', '.join(map(str, missing[:10]))} do not exist "
            f"(the file has fewer lines)"
        )
    return found


def format_extract(lines: dict[int, bytes]) -> bytes:
    """The extract file content: ``<number>\\t<raw line>\\n`` in line-number order."""
    return b"".join(b"%d\t%s\n" % (number, lines[number]) for number in sorted(lines))
